#!/usr/bin/env python3
"""Write or update ~/.claude-memory/config.json.

Called by Claude during onboarding to persist user configuration choices.
Atomic write via tmp+replace to prevent partial writes.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

CONFIG_PATH = Path.home() / ".claude-memory" / "config.json"
CURRENT_ONBOARDING_VERSION = 1

DEFAULT_CONFIG = {
    "onboarding_completed": False,
    "onboarding_version": 0,
    "auto_inject_context": True,
    "consolidation_reminder_enabled": True,
    "consolidation_min_hours": 24,
    "consolidation_min_sessions": 5,
}


def main():
    parser = argparse.ArgumentParser(description="Write claude-memory config")
    parser.add_argument(
        "--auto-inject-context",
        choices=["true", "false"],
        help="Enable session context injection on startup",
    )
    parser.add_argument(
        "--consolidation-enabled",
        choices=["true", "false"],
        help="Enable extract-learnings consolidation reminders",
    )
    parser.add_argument(
        "--consolidation-min-hours",
        type=int,
        help="Hours between consolidation reminders",
    )
    parser.add_argument(
        "--consolidation-min-sessions",
        type=int,
        help="Sessions between consolidation reminders",
    )
    args = parser.parse_args()

    # Load existing config or start from defaults
    config = DEFAULT_CONFIG.copy()
    if CONFIG_PATH.exists():
        try:
            config.update(json.loads(CONFIG_PATH.read_text()))
        except Exception:
            pass

    # Apply CLI arguments
    if args.auto_inject_context is not None:
        config["auto_inject_context"] = args.auto_inject_context == "true"
    if args.consolidation_enabled is not None:
        config["consolidation_reminder_enabled"] = args.consolidation_enabled == "true"
    if args.consolidation_min_hours is not None:
        config["consolidation_min_hours"] = max(1, args.consolidation_min_hours)
    if args.consolidation_min_sessions is not None:
        config["consolidation_min_sessions"] = max(1, args.consolidation_min_sessions)

    # Mark onboarding complete
    config["onboarding_completed"] = True
    config["onboarding_version"] = CURRENT_ONBOARDING_VERSION

    # Atomic write
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(config, indent=2) + "\n")
    tmp.replace(CONFIG_PATH)

    print(json.dumps(config, indent=2))


if __name__ == "__main__":
    main()
