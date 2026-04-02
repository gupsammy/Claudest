"""Shared fixtures for claude-cache tests."""

from __future__ import annotations

import io
import json
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

HOOKS_DIR = Path(__file__).parent.parent.parent / "plugins" / "claude-cache" / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import utils  # noqa: E402


@pytest.fixture
def tmp_cache_dir(tmp_path):
    """Redirect all cache state to a temporary directory.

    Patches utils.STATE_DIR and utils.SESSIONS_DIR so every test operates in
    isolation from the real ~/.claude-cache/.
    """
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    with mock.patch.object(utils, "STATE_DIR", tmp_path), \
         mock.patch.object(utils, "SESSIONS_DIR", sessions):
        yield tmp_path


@pytest.fixture
def session_id():
    """A fixed test session UUID."""
    return "test-session-00000000-0000-0000-0000-000000000001"


@pytest.fixture
def make_hook_input():
    """Factory that patches stdin with JSON hook input and captures stdout."""

    def _make(data: dict):
        stdin_mock = io.StringIO(json.dumps(data))
        stdout_capture = io.StringIO()
        return (
            mock.patch("sys.stdin", stdin_mock),
            mock.patch("sys.stdout", stdout_capture),
            stdout_capture,
        )

    return _make
