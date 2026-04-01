#!/usr/bin/env python3
"""Background countdown timer for cache expiry notifications.

Spawned by the Stop hook as a detached process. Receives session_id as CLI arg.
Sends desktop notifications at 3:00, 4:00, 4:30, and 5:00 minutes, then writes
"expired" state. Skips notifications if keepalive mode is active.

Usage: timer.py <session_id>
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# Import from sibling module
sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils import keepalive_file, notify, pid_file, session_dir, state_file


def main() -> None:
    if len(sys.argv) < 2:
        return

    session_id = sys.argv[1]

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
        time.sleep(sleep_secs)

        # If keepalive is active, skip notification (cache is being kept warm)
        if keepalive_file(session_id).exists():
            continue

        notify(message)

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


if __name__ == "__main__":
    main()
