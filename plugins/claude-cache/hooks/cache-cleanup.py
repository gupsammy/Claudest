#!/usr/bin/env python3
"""SessionStart hook — clean up stale cache state.

Fires on startup and clear events. Kills any orphaned timer process for this
session and removes its state files. Also prunes stale session directories
from other sessions that were never cleaned up (e.g., crashed sessions).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils import cleanup_session, cleanup_stale_sessions, kill_timer, read_hook_input


def main() -> None:
    hook_input = read_hook_input()
    session_id = hook_input.get("session_id", "")

    try:
        if session_id:
            kill_timer(session_id)
            cleanup_session(session_id)

        # Prune stale directories from old/crashed sessions (>24h)
        cleanup_stale_sessions()
    except Exception:
        pass

    print(json.dumps({"continue": True}))


if __name__ == "__main__":
    main()
