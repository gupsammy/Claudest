"""Tests for cache-timer-start.py — Stop hook logic.

Verifies timer spawning, existing timer kill, and stop_hook_active guard.
"""

from __future__ import annotations

import importlib
import json
from unittest import mock

from utils import ensure_session_dir, pid_file

cache_timer_start = importlib.import_module("cache-timer-start")


def run_hook(make_hook_input, data: dict) -> dict:
    """Run cache-timer-start.main() with given input, return parsed JSON output."""
    stdin_patch, stdout_patch, stdout_capture = make_hook_input(data)
    with stdin_patch, stdout_patch:
        cache_timer_start.main()
    return json.loads(stdout_capture.getvalue())


class TestTimerStartSpawn:

    @mock.patch.object(cache_timer_start, "spawn_background")
    def test_spawns_timer(self, mock_spawn, tmp_cache_dir, session_id, make_hook_input):
        result = run_hook(make_hook_input, {"session_id": session_id})

        assert result == {"continue": True}
        mock_spawn.assert_called_once_with("timer.py", session_id, "")

    @mock.patch.object(cache_timer_start, "spawn_background")
    def test_kills_existing_timer_first(self, mock_spawn, tmp_cache_dir, session_id, make_hook_input):
        ensure_session_dir(session_id)
        pid_file(session_id).write_text("99999999")

        run_hook(make_hook_input, {"session_id": session_id})

        # Old PID file should be cleaned up
        assert not pid_file(session_id).exists()
        # New timer should still be spawned
        mock_spawn.assert_called_once_with("timer.py", session_id, "")


class TestTimerStartGuards:

    @mock.patch.object(cache_timer_start, "spawn_background")
    def test_stop_hook_active_skips(self, mock_spawn, tmp_cache_dir, session_id, make_hook_input):
        result = run_hook(make_hook_input, {
            "session_id": session_id,
            "stop_hook_active": True,
        })

        assert result == {"continue": True}
        mock_spawn.assert_not_called()

    @mock.patch.object(cache_timer_start, "spawn_background")
    def test_no_session_id_skips(self, mock_spawn, tmp_cache_dir, make_hook_input):
        result = run_hook(make_hook_input, {})

        assert result == {"continue": True}
        mock_spawn.assert_not_called()
