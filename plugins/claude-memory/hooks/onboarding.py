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

ONBOARDING_CONTEXT = """\
## Claude Memory: First-Run Setup

claude-memory has been installed but hasn't been configured yet. \
Before responding to the user's first message, complete this onboarding flow.

**Step 1 — Welcome the user and pitch the product:**

Say something like: "Welcome to claude-memory! This plugin gives me three \
powerful capabilities:

1. **Session Context** — When you start a new session, I automatically recall \
what we worked on last time so you never have to re-explain context.

2. **Recall Conversations** — The /recall-conversations skill lets me search \
your entire conversation history. When you say things like 'remember when we...' \
or 'what did we decide about...', I'll automatically search for relevant past \
conversations.

3. **Extract Learnings Reminder** — I'll periodically remind you to run \
/extract-learnings, which consolidates discoveries, decisions, and patterns \
from recent sessions into permanent memory files.

Let me set these up for you."

**Step 2 — Ask the user how they want to configure:**

Use the AskUserQuestion tool to ask:
"How would you like to set up claude-memory?"
Options:
- "Use recommended defaults" (description: "Enables all three features with \
default settings — session context on, consolidation reminders every 24 hours \
/ 5 sessions")
- "Walk me through each setting" (description: "I'll ask about each feature \
individually so you can customize")

**If "Use recommended defaults":**
Run this command via the Bash tool:
```
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/write_config.py" --auto-inject-context true --consolidation-enabled true --consolidation-min-hours 24 --consolidation-min-sessions 5
```
Then confirm: "All set! claude-memory is configured with recommended defaults. \
Your preferences are saved to ~/.claude-memory/config.json. \
These settings will take effect from your next session."

**If "Walk me through each setting":**

Use AskUserQuestion for each:

Question 1: "Enable session context injection? When you start a new session, \
I'll automatically summarize what we last worked on in this project."
Options: "Yes, enable (Recommended)", "No, disable"

Question 2: "Enable extract-learnings reminders? I'll periodically suggest \
running /extract-learnings to consolidate discoveries into permanent memory."
Options: "Yes, with defaults (24 hours, 5 sessions)", \
"Yes, but let me customize thresholds", "No, disable reminders"

If they chose to customize thresholds, ask:
"What thresholds would you like for consolidation reminders?"
Options: "12 hours, 3 sessions", "24 hours, 5 sessions (default)", \
"48 hours, 10 sessions"

Then run write_config.py via Bash with the chosen values. For example:
```
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/write_config.py" --auto-inject-context true --consolidation-enabled true --consolidation-min-hours 24 --consolidation-min-sessions 5
```

Confirm: "All set! Your preferences are saved to ~/.claude-memory/config.json. \
These settings will take effect from your next session."

**Step 3 — Continue with the user's original request.**

After onboarding is complete, address whatever the user originally asked. \
Do not skip or delay the onboarding — it only takes a minute and ensures \
the plugin works the way the user wants.
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
            "additionalContext": ONBOARDING_CONTEXT,
        }
    }
    print(json.dumps(output))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Never block session start
        print(json.dumps({}))
