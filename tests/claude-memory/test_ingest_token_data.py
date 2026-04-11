#!/usr/bin/env python3
"""Tests for ingest_token_data — token ingest append-only behaviour.

Gap 4: turns must be skip-if-exists on reimport, and session_metrics totals
must not double when the same session is ingested twice.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

# Add the ingest script's directory to sys.path so imports resolve
_INGEST_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "plugins" / "claude-memory" / "skills"
    / "get-token-insights" / "scripts"
)
sys.path.insert(0, str(_INGEST_DIR))

from ingest_token_data import (
    JnlFile,
    ParsedSession,
    Turn,
    ensure_schema,
    import_session,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal_jnl(tmp_path: Path) -> JnlFile:
    """A JnlFile pointing at a dummy path (file not actually read in these tests)."""
    fake_path = tmp_path / "fake-session.jsonl"
    fake_path.write_text("")
    return JnlFile(
        path=fake_path,
        project_cwd="/test/project",
        is_sidechain=False,
        parent_session_id=None,
    )


def _make_session(session_id: str, turns: list[Turn]) -> ParsedSession:
    """Build a minimal ParsedSession from an explicit turn list."""
    s = ParsedSession(session_id=session_id, project_path="/test/project")
    s.turns = turns
    return s


def _make_turn(index: int, input_tokens: int = 100, output_tokens: int = 50) -> Turn:
    return Turn(
        index=index,
        message_id=f"msg-{index}",
        timestamp=f"2026-03-01T10:0{index}:00Z",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


@pytest.fixture
def token_db():
    """In-memory DB with full token ingest schema."""
    conn = sqlite3.connect(":memory:")
    ensure_schema(conn)
    conn.commit()
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# Gap 4a — turns are append-only (skip-if-exists)
# ---------------------------------------------------------------------------

class TestTurnsAppendOnly:
    """Importing the same session twice must not duplicate turn rows."""

    def test_first_import_creates_turns(self, token_db, tmp_path):
        """First ingest writes one row per turn into the turns table."""
        jnl = _minimal_jnl(tmp_path)
        session = _make_session("sess-abc", [_make_turn(1), _make_turn(2)])

        import_session(token_db, session, jnl)
        token_db.commit()

        count = token_db.execute(
            "SELECT COUNT(*) FROM turns WHERE session_id = 'sess-abc'"
        ).fetchone()[0]
        assert count == 2, f"Expected 2 turn rows after first import, got {count}"

    def test_second_import_skips_existing_turns(self, token_db, tmp_path):
        """Second ingest of the same session must not add new turn rows."""
        jnl = _minimal_jnl(tmp_path)
        session = _make_session("sess-dedup", [_make_turn(1), _make_turn(2)])

        import_session(token_db, session, jnl)
        token_db.commit()

        # Import identical session again
        import_session(token_db, session, jnl)
        token_db.commit()

        count = token_db.execute(
            "SELECT COUNT(*) FROM turns WHERE session_id = 'sess-dedup'"
        ).fetchone()[0]
        assert count == 2, (
            f"Duplicate import must not create extra turn rows — got {count}"
        )

    def test_repeated_imports_no_duplicate_turn_index(self, token_db, tmp_path):
        """(session_id, turn_index) uniqueness must hold after multiple imports."""
        jnl = _minimal_jnl(tmp_path)
        session = _make_session("sess-unique", [_make_turn(1), _make_turn(2)])

        for _ in range(3):
            import_session(token_db, session, jnl)
            token_db.commit()

        rows = token_db.execute("""
            SELECT session_id, turn_index, COUNT(*) AS cnt
            FROM turns
            WHERE session_id = 'sess-unique'
            GROUP BY session_id, turn_index
            HAVING cnt > 1
        """).fetchall()
        assert rows == [], (
            f"(session_id, turn_index) must be unique — duplicates found: {rows}"
        )


# ---------------------------------------------------------------------------
# Gap 4b — session_metrics totals do not double on reimport
# ---------------------------------------------------------------------------

class TestSessionMetricsStableOnReimport:
    """session_metrics totals must reflect the session once, not accumulate."""

    def test_total_input_tokens_not_doubled(self, token_db, tmp_path):
        """total_input_tokens in session_metrics must be the same after two imports."""
        jnl = _minimal_jnl(tmp_path)
        # Two turns with known token counts
        session = _make_session(
            "sess-tokens",
            [_make_turn(1, input_tokens=200), _make_turn(2, input_tokens=300)],
        )

        import_session(token_db, session, jnl)
        token_db.commit()

        first_total = token_db.execute(
            "SELECT total_input_tokens FROM session_metrics WHERE session_id = 'sess-tokens'"
        ).fetchone()[0]
        assert first_total == 500, f"Expected 500 input tokens after first import, got {first_total}"

        # Reimport same session
        import_session(token_db, session, jnl)
        token_db.commit()

        second_total = token_db.execute(
            "SELECT total_input_tokens FROM session_metrics WHERE session_id = 'sess-tokens'"
        ).fetchone()[0]
        assert second_total == first_total, (
            f"total_input_tokens doubled after reimport: {first_total} → {second_total}. "
            "session_metrics must use INSERT OR REPLACE (idempotent upsert), not accumulate."
        )

    def test_turn_count_not_doubled(self, token_db, tmp_path):
        """session_metrics.turn_count must equal the number of turns, not double on reimport."""
        jnl = _minimal_jnl(tmp_path)
        session = _make_session("sess-count", [_make_turn(1), _make_turn(2)])

        import_session(token_db, session, jnl)
        token_db.commit()

        import_session(token_db, session, jnl)
        token_db.commit()

        turn_count = token_db.execute(
            "SELECT turn_count FROM session_metrics WHERE session_id = 'sess-count'"
        ).fetchone()[0]
        assert turn_count == 2, (
            f"turn_count must be 2 (number of turns), not {turn_count} — "
            "signals that session_metrics is being summed rather than replaced"
        )

    def test_session_metrics_row_exists_after_import(self, token_db, tmp_path):
        """Exactly one session_metrics row must exist after two imports of the same session."""
        jnl = _minimal_jnl(tmp_path)
        session = _make_session("sess-single", [_make_turn(1)])

        import_session(token_db, session, jnl)
        token_db.commit()
        import_session(token_db, session, jnl)
        token_db.commit()

        count = token_db.execute(
            "SELECT COUNT(*) FROM session_metrics WHERE session_id = 'sess-single'"
        ).fetchone()[0]
        assert count == 1, (
            f"Expected exactly 1 session_metrics row, got {count} — "
            "INSERT OR REPLACE must upsert, not insert a second row"
        )
