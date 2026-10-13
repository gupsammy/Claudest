#!/usr/bin/env python3
"""SessionStart hook: onboarding for claude-memory.

Injects onboarding instructions into Claude's context when config.json is
missing or onboarding hasn't been completed. After an upgrade that bumps
CURRENT_ONBOARDING_VERSION, users who finished an older onboarding are asked
only the questions whose config key they have never set. Every answer, yes or
no, is saved to config.json, so no question is asked twice.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent / "skills" / "recall-conversations" / "scripts"))

from memory_lib.db import CONFIG_PATH, CURRENT_ONBOARDING_VERSION, load_config  # noqa: F401 — CONFIG_PATH re-exported for test monkeypatching

PYTHON_EXE = sys.executable
WRITE_CONFIG_PATH = str(SCRIPT_DIR / "write_config.py")
SEMANTIC_SETUP_PATH = str(SCRIPT_DIR / "semantic_setup.py")

# Config keys of questions added after onboarding v1; an absent key means never asked.
NEW_QUESTION_KEYS = ("semantic_search",)


def _semantic_setup_steps() -> str:
    return f"""\
**Semantic search setup** (after a yes):
1. Run `{PYTHON_EXE} "{SEMANTIC_SETUP_PATH}" --check`.
2. If `ollama_running` is false and `ollama_installed` is false, run its \
`install_command` (the yes covers it). If installed but not running, start it.
3. Run `{PYTHON_EXE} "{SEMANTIC_SETUP_PATH}" --enable` (pulls the model, saves the choice).
4. If any step fails, tell the user why, record the answer with \
`{PYTHON_EXE} "{WRITE_CONFIG_PATH}" --semantic-search false`, and say they can \
retry any time with the --enable command above.
"""


def _semantic_question() -> str:
    return (
        "Semantic search: find past work by meaning, not just keywords, and list "
        "related past sessions at session start. Runs fully on this machine via "
        "Ollama with a ~360 MB model (Ollama is installed if missing). "
        "Options: Yes, set it up / No"
    )


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
3. {_semantic_question()}

If custom thresholds, ask once: hours and sessions between reminders.

Then run write_config.py with chosen values:
```
{PYTHON_EXE} "{WRITE_CONFIG_PATH}" \\
  --auto-inject-context <true|false> \\
  --consolidation-enabled <true|false> \\
  --consolidation-min-hours <N> \\
  --consolidation-min-sessions <N> \\
  --semantic-search false
```
Confirm: preferences saved, features activate next session. If semantic search \
was a yes, follow the setup steps below.

{_semantic_setup_steps()}
**Explicit defaults** — only when the user specifically says "defaults", \
"just use defaults", or "skip the walkthrough". Run via Bash:
```
{PYTHON_EXE} "{WRITE_CONFIG_PATH}" --defaults
```
Confirm briefly: setup complete, features activate next session.

**Decline ("no", "later", ignores it)** — do nothing. Config stays unwritten; \
this notice reappears next session.
"""


def _build_upgrade_context() -> str:
    return f"""\
## Claude Memory: New Option

claude-memory was updated and has one new setting to choose. Address the \
user's message first. At the end of your response, ask once with \
AskUserQuestion:

{_semantic_question()}

**No** — run `{PYTHON_EXE} "{WRITE_CONFIG_PATH}" --semantic-search false` and \
confirm briefly; they won't be asked again.

{_semantic_setup_steps()}"""


def main():
    config = load_config()

    if config.get("onboarding_completed") is not True:
        context = _build_onboarding_context()
    elif config.get("onboarding_version", 0) >= CURRENT_ONBOARDING_VERSION or all(
        key in config for key in NEW_QUESTION_KEYS
    ):
        # Onboarded, and every question added since has an answer on file.
        print(json.dumps({}))
        return
    else:
        context = _build_upgrade_context()

    output = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": context,
        }
    }
    print(json.dumps(output))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Never block session start
        print(json.dumps({}))
