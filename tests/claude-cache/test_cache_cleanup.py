"""Tests for cache-cleanup.py — SessionStart hook logic.

Verifies session cleanup on startup and stale session pruning.
"""

from __future__ import annotations

import importlib
import json
import os
import time

from utils import ensure_session_dir, pid_file, session_dir, state_file

cache_cleanup = importlib.import_module("cache-cleanup")


def run_hook(make_hook_input, data: dict) -> dict:
    """Run cache-cleanup.main() with given input, return parsed JSON output."""
    stdin_patch, stdout_patch, stdout_capture = make_hook_input(data)
    with stdin_patch, stdout_patch:
        cache_cleanup.main()
    return json.loads(stdout_capture.getvalue())


class TestCleanupOnStartup:

    def test_kills_timer_and_cleans_session(self, tmp_cache_dir, session_id, make_hook_input):
        ensure_session_dir(session_id)
        state_file(session_id).write_text("expired")
        pid_file(session_id).write_text("99999999")

        result = run_hook(make_hook_input, {"session_id": session_id})

        assert result == {"continue": True}
        assert not session_dir(session_id).exists()

    def test_no_session_id_still_continues(self, tmp_cache_dir, make_hook_input):
        result = run_hook(make_hook_input, {})
        assert result == {"continue": True}


class TestCleanupPrunesStale:

    def test_prunes_stale_other_sessions(self, tmp_cache_dir, session_id, make_hook_input):
        # Create a stale session from another "session"
        ensure_session_dir("stale-old")
        sf = state_file("stale-old")
        sf.write_text("active")
        old_time = time.time() - 172800  # 48 hours ago
        os.utime(sf, (old_time, old_time))

        run_hook(make_hook_input, {"session_id": session_id})

        assert not session_dir("stale-old").exists()

    def test_keeps_recent_other_sessions(self, tmp_cache_dir, session_id, make_hook_input):
        ensure_session_dir("recent-other")
        state_file("recent-other").write_text("active")

        run_hook(make_hook_input, {"session_id": session_id})

        assert session_dir("recent-other").exists()
