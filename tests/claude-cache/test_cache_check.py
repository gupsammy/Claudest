"""Tests for cache-check.py — UserPromptSubmit hook logic.

Verifies the expired/block/bypass flow, timer killing, and keepalive cleanup.
"""

from __future__ import annotations

import importlib
import json

import pytest

from utils import (
    bypass_file,
    ensure_session_dir,
    keepalive_file,
    pid_file,
    state_file,
)

# Import the hook module (filename has a hyphen, so use importlib)
cache_check = importlib.import_module("cache-check")


def run_hook(make_hook_input, data: dict) -> dict:
    """Run cache-check.main() with given input, return parsed JSON output."""
    stdin_patch, stdout_patch, stdout_capture = make_hook_input(data)
    with stdin_patch, stdout_patch:
        cache_check.main()
    return json.loads(stdout_capture.getvalue())


class TestCacheCheckContinue:
    """Cases where the hook should allow the message through."""

    def test_no_session_id(self, tmp_cache_dir, make_hook_input):
        result = run_hook(make_hook_input, {})
        assert result == {"continue": True}

    def test_no_state_file(self, tmp_cache_dir, session_id, make_hook_input):
        ensure_session_dir(session_id)
        result = run_hook(make_hook_input, {"session_id": session_id})
        assert result == {"continue": True}

    def test_active_state_continues(self, tmp_cache_dir, session_id, make_hook_input):
        ensure_session_dir(session_id)
        state_file(session_id).write_text("active")
        result = run_hook(make_hook_input, {"session_id": session_id})
        assert result == {"continue": True}
        # State file should be cleaned up
        assert not state_file(session_id).exists()


class TestCacheCheckBlock:
    """The expired → block → bypass flow."""

    def test_expired_first_submit_blocks(self, tmp_cache_dir, session_id, make_hook_input):
        ensure_session_dir(session_id)
        state_file(session_id).write_text("expired")

        result = run_hook(make_hook_input, {"session_id": session_id})

        assert result["decision"] == "block"
        assert "5 minutes" in result["reason"]
        assert "copy your original prompt" in result["reason"]
        # Bypass flag should be set for next attempt
        assert bypass_file(session_id).exists()

    def test_expired_second_submit_continues(self, tmp_cache_dir, session_id, make_hook_input):
        ensure_session_dir(session_id)
        state_file(session_id).write_text("expired")
        bypass_file(session_id).write_text("bypass")

        result = run_hook(make_hook_input, {"session_id": session_id})

        assert result == {"continue": True}
        # Both files should be cleaned up
        assert not bypass_file(session_id).exists()
        assert not state_file(session_id).exists()


class TestCacheCheckSideEffects:
    """Timer kill and keepalive cleanup on user message."""

    def test_kills_timer_on_submit(self, tmp_cache_dir, session_id, make_hook_input):
        ensure_session_dir(session_id)
        pid_file(session_id).write_text("99999999")  # non-existent PID

        run_hook(make_hook_input, {"session_id": session_id})

        assert not pid_file(session_id).exists()

    def test_removes_keepalive_on_submit(self, tmp_cache_dir, session_id, make_hook_input):
        ensure_session_dir(session_id)
        keepalive_file(session_id).write_text("active")

        run_hook(make_hook_input, {"session_id": session_id})

        assert not keepalive_file(session_id).exists()
