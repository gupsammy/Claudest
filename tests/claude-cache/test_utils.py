"""Tests for claude-cache utils.py — state management, security, and cleanup."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

import utils
from utils import (
    bypass_file,
    cleanup_session,
    cleanup_stale_sessions,
    ensure_session_dir,
    keepalive_file,
    kill_timer,
    pid_file,
    session_dir,
    state_file,
)


class TestSessionDir:
    """session_dir path resolution and traversal protection."""

    def test_returns_path_under_sessions_dir(self, tmp_cache_dir):
        result = session_dir("abc-123")
        assert result == utils.SESSIONS_DIR / "abc-123"

    def test_path_traversal_blocked(self, tmp_cache_dir):
        with pytest.raises(ValueError, match="Invalid session_id"):
            session_dir("../../.bashrc")

    def test_path_traversal_with_dotdot(self, tmp_cache_dir):
        with pytest.raises(ValueError, match="Invalid session_id"):
            session_dir("foo/../../etc/passwd")


class TestFileHelpers:
    """pid_file, state_file, bypass_file, keepalive_file return correct paths."""

    def test_pid_file(self, tmp_cache_dir):
        assert pid_file("s1") == utils.SESSIONS_DIR / "s1" / "pid"

    def test_state_file(self, tmp_cache_dir):
        assert state_file("s1") == utils.SESSIONS_DIR / "s1" / "state"

    def test_bypass_file(self, tmp_cache_dir):
        assert bypass_file("s1") == utils.SESSIONS_DIR / "s1" / "bypass"

    def test_keepalive_file(self, tmp_cache_dir):
        assert keepalive_file("s1") == utils.SESSIONS_DIR / "s1" / "keepalive-active"


class TestEnsureSessionDir:

    def test_creates_nested_dirs(self, tmp_cache_dir):
        ensure_session_dir("new-session")
        assert (utils.SESSIONS_DIR / "new-session").is_dir()

    def test_idempotent(self, tmp_cache_dir):
        ensure_session_dir("s1")
        ensure_session_dir("s1")  # should not raise
        assert (utils.SESSIONS_DIR / "s1").is_dir()


class TestKillTimer:

    def test_removes_pid_file(self, tmp_cache_dir):
        """kill_timer removes the PID file even if the process doesn't exist."""
        ensure_session_dir("s1")
        pf = pid_file("s1")
        pf.write_text("99999999")  # non-existent PID
        kill_timer("s1")
        assert not pf.exists()

    def test_no_pid_file_is_safe(self, tmp_cache_dir):
        """kill_timer is a no-op when there's no PID file."""
        ensure_session_dir("s1")
        kill_timer("s1")  # should not raise

    def test_kills_real_process(self, tmp_cache_dir):
        """kill_timer sends SIGTERM to a real background process."""
        import subprocess
        import sys

        ensure_session_dir("s1")
        # Spawn a long-sleeping process we can safely kill
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(300)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        pid_file("s1").write_text(str(proc.pid))
        kill_timer("s1")
        # Process should be terminated
        proc.wait(timeout=5)
        assert not pid_file("s1").exists()


class TestCleanupSession:

    def test_removes_all_state_files(self, tmp_cache_dir):
        ensure_session_dir("s1")
        state_file("s1").write_text("active")
        pid_file("s1").write_text("123")
        bypass_file("s1").write_text("bypass")

        cleanup_session("s1")

        assert not session_dir("s1").exists()

    def test_safe_when_no_session(self, tmp_cache_dir):
        cleanup_session("nonexistent")  # should not raise


class TestCleanupStaleSessions:

    def test_prunes_old_sessions(self, tmp_cache_dir):
        """Sessions with files older than STALE_THRESHOLD get removed."""
        ensure_session_dir("old-session")
        sf = state_file("old-session")
        sf.write_text("active")
        # Backdate the file mtime to 48 hours ago
        old_time = time.time() - 172800
        os.utime(sf, (old_time, old_time))

        cleanup_stale_sessions()

        assert not session_dir("old-session").exists()

    def test_keeps_recent_sessions(self, tmp_cache_dir):
        """Sessions with recent files are preserved."""
        ensure_session_dir("recent-session")
        state_file("recent-session").write_text("active")

        cleanup_stale_sessions()

        assert session_dir("recent-session").exists()
