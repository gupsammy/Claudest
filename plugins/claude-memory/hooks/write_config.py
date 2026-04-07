#!/usr/bin/env python3
"""Write or update ~/.claude-memory/config.json.

Called by Claude during onboarding to persist user configuration choices.
Atomic write via tmp+replace to prevent partial writes.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent / "skills" / "recall-conversations" / "scripts"))

from memory_lib.db import CONFIG_PATH, CURRENT_ONBOARDING_VERSION

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
        "--defaults",
        action="store_true",
        help="Write recommended defaults without requiring explicit flags",
    )
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

    # Load existing config or start from defaults.
    # Skip merge when --defaults is set so it always writes DEFAULT_CONFIG as-is.
    config = DEFAULT_CONFIG.copy()
    if CONFIG_PATH.exists() and not args.defaults:
        try:
            existing = json.loads(CONFIG_PATH.read_text())
            if isinstance(existing, dict):
                config.update(existing)
        except Exception:
            pass

    # Apply CLI arguments (only reached when --defaults is not set)
    if not args.defaults:
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
    fd, tmp_path = tempfile.mkstemp(dir=CONFIG_PATH.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(json.dumps(config, indent=2) + "\n")
        Path(tmp_path).replace(CONFIG_PATH)
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise

    print(f"Config saved to {CONFIG_PATH}")


if __name__ == "__main__":
    main()
