#!/usr/bin/env python3
"""Background countdown timer for cache expiry notifications.

Spawned by the Stop hook as a detached process. Receives session_id as CLI arg.
Sends desktop notifications at 3:00, 4:00, 4:30, and 5:00 minutes, then writes
"expired" state. Skips notifications if keepalive mode is active.

Usage: timer.py <session_id> [project_name]
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# Import from sibling module
sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils import cleanup_session, keepalive_file, notify, pid_file, session_dir, state_file

# Speed factor for testing — set CLAUDE_CACHE_SPEED_FACTOR=60 to compress 5 min → 5 sec
SPEED_FACTOR = max(1, int(os.environ.get("CLAUDE_CACHE_SPEED_FACTOR", "1")))


def main() -> None:
    if len(sys.argv) < 2:
        return

    session_id = sys.argv[1]
    project_name = sys.argv[2] if len(sys.argv) > 2 else ""

    # Ensure session directory exists
    session_dir(session_id).mkdir(parents=True, exist_ok=True)

    # Write PID and initial state
    pid_file(session_id).write_text(str(os.getpid()))
    state_file(session_id).write_text("active")

    # Notification schedule: (sleep_seconds, message)
    schedule = [
        (180, "Cache expiring in 2 min \u2014 interact to keep it warm"),
        (60,  "Cache expiring in 1 min"),
        (30,  "30 seconds until cache expires!"),
        (30,  "Cache likely expired \u2014 consider /clear"),
    ]

    for sleep_secs, message in schedule:
        time.sleep(sleep_secs / SPEED_FACTOR)

        # If keepalive is active, skip notification (cache is being kept warm)
        if keepalive_file(session_id).exists():
            continue

        notify(message, project=project_name)

    # Mark cache as expired — but only if keepalive isn't active.
    # If keepalive pings are running, the cache is warm; writing "expired"
    # would cause a spurious block on the next user message.
    if not keepalive_file(session_id).exists():
        state_file(session_id).write_text("expired")

    # Clean up PID file — timer has completed its job
    try:
        pid_file(session_id).unlink()
    except (FileNotFoundError, OSError):
        pass

    # Self-clean: wait 10 more minutes, then remove the session directory.
    # If the user returns before this, cache-check.py kills this process (via
    # the PID file — but it's already gone). However, the user returning means
    # the Stop hook spawns a NEW timer, and cache-check already cleared state.
    # So this sleep only completes if the session stays idle post-expiry.
    SELF_CLEAN_SECS = 600  # 10 minutes
    time.sleep(SELF_CLEAN_SECS / SPEED_FACTOR)

    # Only clean up if state is still "expired" (no new timer took over)
    sf = state_file(session_id)
    try:
        if sf.exists() and sf.read_text().strip() == "expired":
            cleanup_session(session_id)
    except OSError:
        pass


if __name__ == "__main__":
    main()
