#!/usr/bin/env python3
"""SessionStart hook: first-run onboarding for claude-memory.

Injects onboarding instructions into Claude's context when config.json
is missing or onboarding hasn't been completed. Once onboarding completes
(config.json written with onboarding_completed=true), this hook becomes
a silent no-op.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent / "skills" / "recall-conversations" / "scripts"))

from memory_lib.db import CONFIG_PATH, load_config

CURRENT_ONBOARDING_VERSION = 1

WRITE_CONFIG_PATH = str(SCRIPT_DIR / "write_config.py")


def _build_onboarding_context() -> str:
    return f"""\
## Claude Memory: Onboarding Pending

claude-memory is installed but unconfigured. Its features (session context \
injection, consolidation reminders) are paused until setup completes.

Address the user's message first — complete their task normally. At the end \
of your response, append a one-time notice in natural prose (not AskUserQuestion) \
mentioning that claude-memory is installed and offering setup. Mention three \
capabilities briefly: session context recall, /recall-conversations for searching \
past work, and /extract-learnings reminders. Offer two choices: (1) walk through \
settings, or (2) enable recommended defaults. Note they can change settings later \
in ~/.claude-memory/config.json.

## User Response Handling

Flat branches — handle the user's reply to the notice:

**Walkthrough (default)** — any affirmative reply ("ok", "sure", "let's do it", \
"set it up", "yes") routes here. Use AskUserQuestion for two settings:

1. Session context injection (auto-recall last session on startup): Yes / No
2. Consolidation reminders (/extract-learnings nudges): Yes with defaults / \
Yes with custom thresholds / No

If custom thresholds, ask once: hours and sessions between reminders.

Then run write_config.py with chosen values:
```
python3 {WRITE_CONFIG_PATH} \\
  --auto-inject-context <true|false> \\
  --consolidation-enabled <true|false> \\
  --consolidation-min-hours <N> \\
  --consolidation-min-sessions <N>
```
Confirm: preferences saved, features activate next session.

**Explicit defaults** — only when the user specifically says "defaults", \
"just use defaults", or "skip the walkthrough". Run via Bash:
```
python3 {WRITE_CONFIG_PATH} --defaults
```
Confirm briefly: setup complete, features activate next session.

**Decline ("no", "later", ignores it)** — do nothing. Config stays unwritten; \
this notice reappears next session.
"""


def main():
    config = load_config()

    # Already onboarded — exit silently
    if (
        config.get("onboarding_completed") is True
        and config.get("onboarding_version", 0) >= CURRENT_ONBOARDING_VERSION
    ):
        print(json.dumps({}))
        return

    # Inject onboarding instructions
    output = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": _build_onboarding_context(),
        }
    }
    print(json.dumps(output))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Never block session start
        print(json.dumps({}))
