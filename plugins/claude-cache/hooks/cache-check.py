#!/usr/bin/env python3
"""UserPromptSubmit hook — kill timer, check cache expiry, block if needed.

Fires before every user message is processed. Responsibilities:
1. Kill the running countdown timer for this session (user is active)
2. Remove keepalive-active file (deactivates keepalive — user is back)
3. If cache has expired (>5 min idle):
   - First submission: block with warning, set bypass flag
   - Second submission: allow through, consume bypass flag
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils import (
    _unlink_safe,
    bypass_file,
    keepalive_file,
    kill_timer,
    read_hook_input,
    state_file,
)


def main() -> None:
    hook_input = read_hook_input()
    session_id = hook_input.get("session_id", "")

    if not session_id:
        print(json.dumps({"continue": True}))
        return

    try:
        # Always kill the timer — user is interacting
        kill_timer(session_id)

        # Deactivate keepalive if active (next cron fire will self-destruct)
        _unlink_safe(keepalive_file(session_id))

        # Check if cache has expired.
        # Read state THEN clear it: if the timer was killed mid-run (user
        # returned before 5 min), any stale "expired" written in a race
        # between timer completion and kill_timer() is cleared here. Only a
        # naturally-completed timer (PID file already gone when kill_timer ran)
        # leaves a meaningful "expired" state.
        sf = state_file(session_id)
        bf = bypass_file(session_id)
        expired = False
        try:
            state = sf.read_text().strip()
            expired = state == "expired"
            if not expired:
                _unlink_safe(sf)
        except FileNotFoundError:
            pass

        if expired and not bf.exists():
            # First submission after expiry — block and set bypass
            bf.write_text("bypass")
            print(json.dumps({
                "decision": "block",
                "reason": (
                    "Cache likely invalidated \u2014 idle time exceeded 5 minutes.\n\n"
                    "Recommended: type /clear to start a fresh session (saves tokens)\n"
                    "Continue anyway: press \u2191 then Enter to re-submit your message"
                ),
            }))
            return

        if expired and bf.exists():
            # Second submission — user chose to continue, consume bypass
            _unlink_safe(bf)
            _unlink_safe(sf)

    except Exception:
        pass

    print(json.dumps({"continue": True}))


if __name__ == "__main__":
    main()
