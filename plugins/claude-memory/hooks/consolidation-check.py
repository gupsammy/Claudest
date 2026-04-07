#!/usr/bin/env python3
"""
SessionStart hook: check if memory consolidation is overdue.

Dual-gate (mirrors auto-dream): fires when BOTH conditions are met:
  1. 24+ hours since last consolidation
  2. 5+ sessions since last consolidation

If no .last-consolidation marker exists and 10+ total sessions exist,
treats as "never consolidated" and nudges.

Output: hookSpecificOutput nudge or {} (silent).
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add path to shared utils
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent / "skills" / "recall-conversations" / "scripts"))

from memory_lib.db import DEFAULT_PROJECTS_DIR, get_db_path, load_settings, setup_logging
from memory_lib.formatting import get_project_key

# Gating thresholds
MIN_HOURS = 24
MIN_SESSIONS = 5
NEVER_CONSOLIDATED_MIN_SESSIONS = 10


def get_consolidation_marker(project_key: str) -> Path:
    """Return path to the .last-consolidation marker for a project."""
    return DEFAULT_PROJECTS_DIR / project_key / "memory" / ".last-consolidation"


def read_last_consolidation(marker: Path) -> datetime | None:
    """Read the ISO timestamp from the consolidation marker file."""
    if not marker.exists():
        return None
    try:
        text = marker.read_text().strip()
        # Unix timestamp written by `date +%s`
        if text.isdigit():
            return datetime.fromtimestamp(int(text), tz=timezone.utc)
        # ISO format (preferred)
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (ValueError, OSError):
        return None


def count_sessions_since(conn: sqlite3.Connection, project_key: str, since_iso: str | None) -> int:
    """Count distinct non-subagent sessions since a given timestamp."""
    cursor = conn.cursor()

    if since_iso:
        cursor.execute("""
            SELECT COUNT(DISTINCT s.uuid)
            FROM sessions s
            JOIN projects p ON s.project_id = p.id
            JOIN branches b ON b.session_id = s.id
            WHERE p.key = ?
              AND b.ended_at > ?
              AND s.parent_session_id IS NULL
        """, (project_key, since_iso))
    else:
        # No marker — count all sessions for the project
        cursor.execute("""
            SELECT COUNT(DISTINCT s.uuid)
            FROM sessions s
            JOIN projects p ON s.project_id = p.id
            WHERE p.key = ?
              AND s.parent_session_id IS NULL
        """, (project_key,))

    row = cursor.fetchone()
    return row[0] if row else 0


def main() -> None:
    settings = load_settings()
    logger = setup_logging(settings)

    # Read hook input from stdin
    try:
        hook_input = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        hook_input = {}

    source = hook_input.get("source", "startup")
    cwd = hook_input.get("cwd")

    # Only check on fresh sessions
    if source not in ("startup", "clear"):
        print(json.dumps({}))
        return

    if not cwd:
        print(json.dumps({}))
        return

    # Check if database exists
    db_path = get_db_path(settings)
    if not db_path.exists():
        print(json.dumps({}))
        return

    project_key = get_project_key(cwd)
    marker = get_consolidation_marker(project_key)
    last_ts = read_last_consolidation(marker)

    try:
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")

        since_iso = last_ts.isoformat() if last_ts else None
        try:
            session_count = count_sessions_since(conn, project_key, since_iso)
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"Consolidation check error: {e}")
        print(json.dumps({}))
        return

    # Apply gating logic
    now = datetime.now(timezone.utc)
    should_nudge = False
    days_str = ""

    if last_ts is None:
        # Never consolidated — nudge if enough sessions exist
        if session_count >= NEVER_CONSOLIDATED_MIN_SESSIONS:
            should_nudge = True
            days_str = "never"
    else:
        hours_elapsed = (now - last_ts).total_seconds() / 3600
        if hours_elapsed >= MIN_HOURS and session_count >= MIN_SESSIONS:
            should_nudge = True
            days = (now - last_ts).days
            days_str = f"{days} day{'s' if days != 1 else ''}" if days >= 1 else f"{int(hours_elapsed)} hours"

    if not should_nudge:
        print(json.dumps({}))
        return

    # Build nudge message
    if last_ts is None:
        nudge = (
            f"Memory consolidation recommended — {session_count} sessions found "
            f"and consolidation has never been run for this project. "
            f"Suggest running /extract-learnings in consolidation mode to review "
            f"and organize accumulated knowledge."
        )
    else:
        nudge = (
            f"Memory consolidation recommended — {session_count} sessions over "
            f"{days_str} since last consolidation. Suggest running "
            f"/extract-learnings in consolidation mode to detect stale memories, "
            f"capture uncaptured learnings, and prune the memory index."
        )

    logger.info(f"Consolidation nudge for {project_key}: {session_count} sessions, last={last_ts}")

    output = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": (
                f"## Consolidation Check\n\n{nudge}\n\n"
                "Briefly mention this recommendation to the user in your first response."
            )
        }
    }
    print(json.dumps(output))


if __name__ == "__main__":
    main()
