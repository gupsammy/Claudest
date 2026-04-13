#!/usr/bin/env python3
"""
Stop hook: record the time Claude last finished responding, for cache expiry warning.

Called after every Claude turn. Writes ~/.claude-memory/cache-warn/<session_id>.json
with the current timestamp so cache-expiry-warn.py (UserPromptSubmit hook) can
compute the idle gap on the next user prompt.

Preserves any existing warned_gaps list so the warn-once-per-gap logic survives
across multiple turns in the same session.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

CACHE_WARN_DIR = Path.home() / ".claude-memory" / "cache-warn"


def main() -> None:
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
        session_id = data.get("session_id", "")
        if not session_id:
            return

        CACHE_WARN_DIR.mkdir(parents=True, exist_ok=True)
        state_path = CACHE_WARN_DIR / f"{session_id}.json"

        warned_gaps: list = []
        if state_path.exists():
            try:
                existing = json.loads(state_path.read_text())
                warned_gaps = existing.get("warned_gaps", [])
            except (json.JSONDecodeError, OSError):
                pass

        state_path.write_text(json.dumps({
            "session_id": session_id,
            "last_stop_time": datetime.now(timezone.utc).isoformat(),
            "warned_gaps": warned_gaps,
        }))
    except Exception:
        pass  # Never block the Stop hook


if __name__ == "__main__":
    main()
