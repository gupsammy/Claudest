"""
Tests for the SessionEnd handoff contract between clear-handoff.py and
_find_cleared_from_session_uuid in memory-context.py.

Contract:
  1. clear-handoff.py only writes when end_reason == "clear"
  2. clear-handoff.py only writes when both session_id and cwd are present
  3. Handoff file contains session_id, cwd, timestamp (no transcript_path)
  4. _find_cleared_from_session_uuid returns None if file missing
  5. _find_cleared_from_session_uuid returns None if cwd doesn't match
  6. _find_cleared_from_session_uuid returns None if timestamp is stale (>30s)
  7. File is deleted ONLY after validation passes (not on cwd mismatch)
  8. Corrupt/unreadable files are deleted immediately
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[2] / "plugins" / "claude-memory" / "hooks"


def _load_module(name: str, path: Path):
    """Load a hyphenated-filename module by path."""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Load clear-handoff module once
_clear_handoff = _load_module("clear_handoff", HOOKS_DIR / "clear-handoff.py")
# Load memory-context module once
_memory_context = _load_module("memory_context", HOOKS_DIR / "memory-context.py")

_find_cleared_from_session_uuid = _memory_context._find_cleared_from_session_uuid


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_handoff_main(tmp_path: Path, payload: dict) -> Path:
    """
    Run clear-handoff.main() with a fake db_path under tmp_path and the given
    payload piped through stdin. Returns the handoff_path.
    """
    fake_db = tmp_path / "conversations.db"
    handoff_path = tmp_path / "clear-handoff.json"

    fake_settings = {"db_path": str(fake_db)}

    import io
    stdin_data = json.dumps(payload)

    with patch.object(sys, "stdin", io.StringIO(stdin_data)), \
         patch.object(_clear_handoff, "load_settings", return_value=fake_settings), \
         patch.object(_clear_handoff, "get_db_path", return_value=fake_db):
        _clear_handoff.main()

    return handoff_path


# ---------------------------------------------------------------------------
# clear-handoff.py contract tests
# ---------------------------------------------------------------------------

class TestClearHandoffWriter:
    def test_writes_file_on_clear(self, tmp_path):
        """Contract 1+2+3: writes file with correct keys when end_reason=clear."""
        hp = _run_handoff_main(tmp_path, {
            "end_reason": "clear",
            "session_id": "abc-123",
            "cwd": "/some/project",
        })
        assert hp.exists(), "Handoff file should be written for end_reason=clear"
        data = json.loads(hp.read_text())
        assert data["session_id"] == "abc-123"
        assert data["cwd"] == "/some/project"
        assert "timestamp" in data
        assert "transcript_path" not in data

    def test_does_not_write_missing_session_id(self, tmp_path):
        """Contract 2: skips write when session_id is absent."""
        hp = _run_handoff_main(tmp_path, {"end_reason": "clear", "cwd": "/some/project"})
        assert not hp.exists()

    def test_does_not_write_missing_cwd(self, tmp_path):
        """Contract 2: skips write when cwd is absent."""
        hp = _run_handoff_main(tmp_path, {"end_reason": "clear", "session_id": "abc-123"})
        assert not hp.exists()

    @pytest.mark.parametrize("end_reason", ["interrupt", "crash", "CLEAR", None])
    def test_does_not_write_for_other_reasons(self, tmp_path, end_reason):
        """Contract 1: only end_reason='clear' (exact, case-sensitive) triggers write."""
        hp = _run_handoff_main(tmp_path, {
            "end_reason": end_reason,
            "session_id": "abc-123",
            "cwd": "/some/project",
        })
        assert not hp.exists(), f"Should not write for end_reason={end_reason!r}"

    def test_does_not_write_on_invalid_json(self, tmp_path):
        """Gracefully ignores malformed stdin."""
        import io
        fake_db = tmp_path / "conversations.db"
        with patch.object(sys, "stdin", io.StringIO("not-json")), \
             patch.object(_clear_handoff, "load_settings", return_value={"db_path": str(fake_db)}), \
             patch.object(_clear_handoff, "get_db_path", return_value=fake_db):
            _clear_handoff.main()
        assert not (tmp_path / "clear-handoff.json").exists()


# ---------------------------------------------------------------------------
# _find_cleared_from_session_uuid contract tests
# ---------------------------------------------------------------------------

def _write_handoff(tmp_path: Path, data: dict) -> Path:
    """Write a handoff JSON file relative to a fake db_path."""
    hp = tmp_path / "clear-handoff.json"
    hp.write_text(json.dumps(data))
    return tmp_path / "conversations.db"  # db_path; handoff is db_path.parent / "clear-handoff.json"


class TestFindClearedFromSessionUuid:
    def test_returns_none_when_file_missing(self, tmp_path):
        """Contract 4: no handoff file → None."""
        db_path = tmp_path / "conversations.db"
        result = _find_cleared_from_session_uuid(db_path, "/some/project")
        assert result is None

    def test_returns_session_id_on_valid_handoff(self, tmp_path):
        """Happy path: valid file, matching cwd, fresh timestamp → session_id returned."""
        db_path = _write_handoff(tmp_path, {
            "session_id": "sid-valid",
            "cwd": "/my/project",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        result = _find_cleared_from_session_uuid(db_path, "/my/project")
        assert result == "sid-valid"

    def test_returns_none_on_cwd_mismatch(self, tmp_path):
        """Contract 5: cwd mismatch → None."""
        db_path = _write_handoff(tmp_path, {
            "session_id": "sid-xyz",
            "cwd": "/other/project",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        result = _find_cleared_from_session_uuid(db_path, "/my/project")
        assert result is None

    def test_file_not_deleted_on_cwd_mismatch(self, tmp_path):
        """Contract 7 (key fix): file must survive a cwd mismatch so another process can claim it."""
        db_path = _write_handoff(tmp_path, {
            "session_id": "sid-xyz",
            "cwd": "/other/project",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        handoff_path = tmp_path / "clear-handoff.json"
        _find_cleared_from_session_uuid(db_path, "/my/project")
        assert handoff_path.exists(), "Handoff file should NOT be deleted on cwd mismatch"

    def test_file_deleted_after_valid_consumption(self, tmp_path):
        """Contract 7: file IS deleted after validation passes."""
        db_path = _write_handoff(tmp_path, {
            "session_id": "sid-valid",
            "cwd": "/my/project",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        handoff_path = tmp_path / "clear-handoff.json"
        _find_cleared_from_session_uuid(db_path, "/my/project")
        assert not handoff_path.exists(), "Handoff file should be deleted after valid consumption"

    def test_returns_none_on_stale_timestamp(self, tmp_path):
        """Contract 6: timestamp older than 30s → None."""
        stale = (datetime.now(timezone.utc) - timedelta(seconds=31)).isoformat()
        db_path = _write_handoff(tmp_path, {
            "session_id": "sid-stale",
            "cwd": "/my/project",
            "timestamp": stale,
        })
        result = _find_cleared_from_session_uuid(db_path, "/my/project")
        assert result is None

    def test_stale_file_is_deleted_on_rejection(self, tmp_path):
        """Stale handoff file must be deleted so it doesn't block future clears."""
        stale = (datetime.now(timezone.utc) - timedelta(seconds=31)).isoformat()
        db_path = _write_handoff(tmp_path, {
            "session_id": "sid-stale",
            "cwd": "/my/project",
            "timestamp": stale,
        })
        handoff_path = tmp_path / "clear-handoff.json"
        _find_cleared_from_session_uuid(db_path, "/my/project")
        assert not handoff_path.exists(), "Stale handoff file should be deleted on rejection"

    def test_returns_session_id_on_fresh_timestamp_boundary(self, tmp_path):
        """Timestamp exactly at boundary (29s) is still accepted."""
        fresh = (datetime.now(timezone.utc) - timedelta(seconds=29)).isoformat()
        db_path = _write_handoff(tmp_path, {
            "session_id": "sid-fresh",
            "cwd": "/my/project",
            "timestamp": fresh,
        })
        result = _find_cleared_from_session_uuid(db_path, "/my/project")
        assert result == "sid-fresh"

    def test_deletes_corrupt_file_immediately(self, tmp_path):
        """Contract 8: unreadable/corrupt JSON → file deleted, None returned."""
        handoff_path = tmp_path / "clear-handoff.json"
        handoff_path.write_text("{{not valid json{{")
        db_path = tmp_path / "conversations.db"
        result = _find_cleared_from_session_uuid(db_path, "/my/project")
        assert result is None
        assert not handoff_path.exists(), "Corrupt handoff file should be deleted immediately"
