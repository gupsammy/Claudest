#!/usr/bin/env python3
"""Shared utilities for claude-cache hooks — cross-platform process and notification helpers.

All state is namespaced per session to support multiple concurrent Claude Code sessions.
State directory: ~/.claude-cache/sessions/{session_id}/
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

STATE_DIR = Path.home() / ".claude-cache"
SESSIONS_DIR = STATE_DIR / "sessions"

# Stale session directories older than this (seconds) are cleaned up on SessionStart
STALE_THRESHOLD = 86400  # 24 hours


def _unlink_safe(path: Path) -> None:
    """Remove a file, ignoring if it doesn't exist."""
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def session_dir(session_id: str) -> Path:
    """Return the state directory for a specific session."""
    return SESSIONS_DIR / session_id


def pid_file(session_id: str) -> Path:
    return session_dir(session_id) / "pid"


def state_file(session_id: str) -> Path:
    return session_dir(session_id) / "state"


def bypass_file(session_id: str) -> Path:
    return session_dir(session_id) / "bypass"


def keepalive_file(session_id: str) -> Path:
    return session_dir(session_id) / "keepalive-active"


def ensure_session_dir(session_id: str) -> None:
    """Create session state directory if it doesn't exist."""
    session_dir(session_id).mkdir(parents=True, exist_ok=True)


def read_hook_input() -> dict:
    """Read and parse JSON hook input from stdin."""
    try:
        return json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError, ValueError):
        return {}


def kill_timer(session_id: str) -> None:
    """Kill the running timer process for a session. Safe to call when no timer is running."""
    pf = pid_file(session_id)
    if not pf.exists():
        return
    try:
        pid = int(pf.read_text().strip())
        if sys.platform == "win32":
            subprocess.call(
                ["taskkill", "/PID", str(pid), "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            os.kill(pid, signal.SIGTERM)
    except (ValueError, OSError):
        # OSError covers ProcessLookupError (its subclass) and permission errors
        pass
    finally:
        _unlink_safe(pf)


def cleanup_session(session_id: str) -> None:
    """Remove all state files for a session. Safe to call when no state exists."""
    sd = session_dir(session_id)
    if not sd.exists():
        return
    for f in sd.iterdir():
        _unlink_safe(f)
    try:
        sd.rmdir()
    except OSError:
        pass


def cleanup_stale_sessions() -> None:
    """Remove session directories older than STALE_THRESHOLD. Best-effort."""
    if not SESSIONS_DIR.exists():
        return
    now = time.time()
    try:
        for entry in SESSIONS_DIR.iterdir():
            if not entry.is_dir():
                continue
            try:
                mtime = entry.stat().st_mtime
                if now - mtime > STALE_THRESHOLD:
                    for f in entry.iterdir():
                        _unlink_safe(f)
                    entry.rmdir()
            except OSError:
                pass
    except OSError:
        pass


def spawn_background(script: str, *args: str) -> None:
    """Spawn a Python script as a detached background process."""
    script_path = Path(__file__).resolve().parent / script
    cmd = [sys.executable, str(script_path)] + list(args)

    kwargs: dict = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if sys.platform == "win32":
        kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS  # type: ignore[attr-defined]
        )
    else:
        kwargs["start_new_session"] = True

    subprocess.Popen(cmd, **kwargs)


def notify(message: str, title: str = "Claude Code") -> None:
    """Send a desktop notification. Cross-platform: macOS, Linux, Windows."""
    try:
        if sys.platform == "darwin":
            # Escape backslashes and double quotes for AppleScript string literals
            safe_msg = message.replace("\\", "\\\\").replace('"', '\\"')
            safe_title = title.replace("\\", "\\\\").replace('"', '\\"')
            subprocess.run(
                [
                    "osascript", "-e",
                    f'display notification "{safe_msg}" with title "{safe_title}"',
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
        elif sys.platform == "win32":
            # Escape single quotes for PowerShell string literals
            safe_msg = message.replace("'", "''")
            safe_title = title.replace("'", "''")
            ps_cmd = (
                f"Add-Type -AssemblyName System.Windows.Forms; "
                f"[System.Windows.Forms.MessageBox]::Show('{safe_msg}','{safe_title}')"
            )
            subprocess.run(
                ["powershell", "-Command", ps_cmd],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
        else:
            # Linux — notify-send is available on most desktop distros
            subprocess.run(
                ["notify-send", title, message],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        # Notification is best-effort — never block on failure
        pass
