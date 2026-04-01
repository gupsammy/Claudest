#!/usr/bin/env python3
"""Stop hook — kill any existing timer and spawn a fresh countdown.

Fires on every Stop event. Reads session_id from hook input to maintain
per-session timer isolation.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils import ensure_session_dir, kill_timer, read_hook_input, spawn_background


def main() -> None:
    hook_input = read_hook_input()

    # Claude Code sets stop_hook_active=true when a Stop hook blocks and the
    # turn re-fires. Guard against spawning duplicate timers in that scenario.
    if hook_input.get("stop_hook_active"):
        print(json.dumps({"continue": True}))
        return

    session_id = hook_input.get("session_id", "")
    if not session_id:
        print(json.dumps({"continue": True}))
        return

    # Extract project name from cwd for notification context
    cwd = hook_input.get("cwd", "")
    project_name = Path(cwd).name if cwd else ""

    try:
        ensure_session_dir(session_id)
        kill_timer(session_id)
        spawn_background("timer.py", session_id, project_name)
    except Exception:
        pass

    print(json.dumps({"continue": True}))


if __name__ == "__main__":
    main()
