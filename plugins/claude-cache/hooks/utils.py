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
    """Return the state directory for a specific session.

    Validates that the resolved path stays within SESSIONS_DIR to prevent
    path traversal via crafted session_id values (e.g. '../../.bashrc').
    """
    candidate = (SESSIONS_DIR / session_id).resolve()
    resolved_base = SESSIONS_DIR.resolve()
    if not str(candidate).startswith(str(resolved_base) + os.sep) and candidate != resolved_base:
        raise ValueError(f"Invalid session_id: {session_id!r}")
    return candidate


def pid_file(session_id: str) -> Path:
    return session_dir(session_id) / "pid"


def state_file(session_id: str) -> Path:
    return session_dir(session_id) / "state"


def bypass_file(session_id: str) -> Path:
    return session_dir(session_id) / "bypass"


def keepalive_file(session_id: str) -> Path:
    return session_dir(session_id) / "keepalive-active"


def keepalive_job_file(session_id: str) -> Path:
    """Stores the CronCreate job ID separately from the sentinel, so the cron
    prompt can still read the ID after the sentinel is deleted."""
    return session_dir(session_id) / "keepalive-job-id"


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
    """Remove session directories where all files are older than STALE_THRESHOLD.

    Checks the newest file mtime within each directory, not the directory mtime
    itself. Directory mtime only updates on file creation/deletion, not content
    writes — so a long-running session whose timer.py overwrites state files
    would appear stale by directory mtime while still being active.
    """
    if not SESSIONS_DIR.exists():
        return
    now = time.time()
    try:
        for entry in SESSIONS_DIR.iterdir():
            if not entry.is_dir():
                continue
            try:
                # Find the newest file mtime in the session directory
                newest = 0.0
                for f in entry.iterdir():
                    try:
                        newest = max(newest, f.stat().st_mtime)
                    except OSError:
                        pass
                # Only prune if all files are older than threshold (or dir is empty)
                if now - newest > STALE_THRESHOLD:
                    for f in entry.iterdir():
                        _unlink_safe(f)
                    entry.rmdir()
            except OSError:
                pass
    except OSError:
        pass


def spawn_background(script: str, *args: str) -> None:
    """Spawn a Python script as a background process.

    On macOS the child stays in the hook runner's process tree (no
    start_new_session) so it inherits session membership — required for
    cmux socket access. On Windows it fully detaches via creation flags.
    """
    script_path = Path(__file__).resolve().parent / script
    cmd = [sys.executable, str(script_path)] + list(args)

    kwargs: dict = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if sys.platform == "win32":
        kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS  # type: ignore[attr-defined]
        )

    subprocess.Popen(cmd, **kwargs)


def _play_sound(sound: str = "Glass") -> None:
    """Play a macOS system sound in the background (non-blocking)."""
    try:
        subprocess.Popen(
            ["afplay", f"/System/Library/Sounds/{sound}.aiff"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (FileNotFoundError, OSError):
        pass


def _detect_terminal_bundle() -> str:
    """Walk process tree to find the terminal emulator's macOS bundle ID.

    Looks for an ancestor whose binary lives inside /Applications/*.app/.
    Returns e.g. 'com.cmuxterm.app' or '' if not found.
    """
    try:
        pid = os.getpid()
        while pid > 1:
            result = subprocess.run(
                ["ps", "-o", "comm=,ppid=", "-p", str(pid)],
                capture_output=True, text=True, timeout=2,
            )
            parts = result.stdout.strip().rsplit(None, 1)
            if len(parts) < 2:
                break
            comm, ppid = parts[0], parts[1]
            if comm.startswith("/Applications/") and ".app/" in comm:
                app_path = comm[:comm.index(".app/") + 4]
                mdls = subprocess.run(
                    ["mdls", "-name", "kMDItemCFBundleIdentifier", app_path],
                    capture_output=True, text=True, timeout=2,
                )
                for part in mdls.stdout.split('"'):
                    if "." in part and "kMDItem" not in part:
                        return part
            pid = int(ppid)
    except (subprocess.TimeoutExpired, ValueError, OSError):
        pass
    return ""


def _notify_cmux(message: str, title: str = "Claude Code") -> bool:
    """Send notification via cmux CLI with pane-level targeting.

    Returns True if successful. Requires CMUX_SURFACE_ID env var and the
    cmux binary at the standard path.
    """
    surface = os.environ.get("CMUX_SURFACE_ID", "")
    if not surface:
        return False
    cmux_bin = "/Applications/cmux.app/Contents/Resources/bin/cmux"
    if not os.path.isfile(cmux_bin):
        return False
    try:
        result = subprocess.run(
            [cmux_bin, "notify", "--title", title, "--body", message, "--surface", surface],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def _notify_terminal_notifier(message: str, title: str = "Claude Code") -> bool:
    """Send notification via terminal-notifier with click-to-activate terminal app.

    Returns True if successful. Auto-detects terminal bundle ID for -activate.
    """
    try:
        cmd = ["terminal-notifier", "-title", title, "-message", message, "-sound", "Glass"]
        bundle = _detect_terminal_bundle()
        if bundle:
            cmd += ["-activate", bundle]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def notify(message: str, title: str = "Claude Code", project: str = "") -> None:
    """Send a desktop notification. Cross-platform: macOS, Linux, Windows.

    On macOS, tries three tiers:
    1. cmux notify — pane-level focus via surface targeting (best UX)
    2. terminal-notifier — app-level focus via bundle activation
    3. osascript — no click-to-focus but always available
    """
    try:
        if sys.platform == "darwin":
            if _notify_cmux(message, title):
                _play_sound()
            elif _notify_terminal_notifier(message, title):
                pass  # terminal-notifier handles sound via -sound flag
            else:
                safe_msg = message.replace("\\", "\\\\").replace('"', '\\"')
                safe_title = title.replace("\\", "\\\\").replace('"', '\\"')
                script = f'display notification "{safe_msg}" with title "{safe_title}"'
                if project:
                    safe_project = project.replace("\\", "\\\\").replace('"', '\\"')
                    script = f'display notification "{safe_msg}" with title "{safe_title}" subtitle "{safe_project}"'
                subprocess.run(
                    ["osascript", "-e", script],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                )
                _play_sound()
        elif sys.platform == "win32":
            safe_msg = message.replace("'", "''")
            safe_title = title.replace("'", "''")
            ps_cmd = (
                f"if (Get-Module -ListAvailable -Name BurntToast) {{ "
                f"New-BurntToastNotification -Text '{safe_title}', '{safe_msg}' "
                f"}}"
            )
            subprocess.Popen(
                ["powershell", "-Command", ps_cmd],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            subprocess.run(
                ["notify-send", title, message],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
