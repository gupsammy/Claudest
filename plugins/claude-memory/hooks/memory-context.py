#!/usr/bin/env python3
"""
Load previous session context from memory database for SessionStart hook.

Selection Algorithm:
1. Get recent sessions for current project (excluding current session)
2. Skip sessions with exchange_count <= 1 (noise)
3. Load sessions with exchange_count == 2, keep looking (up to MAX_SESSIONS)
4. Stop at first session with exchange_count > 2 (sufficient context)

Output: JSON with hookSpecificOutput for context injection
"""

import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

DEFAULT_DB_PATH = Path.home() / ".claude-memory" / "conversations.db"
MAX_SESSIONS = 2


def format_time(ts_str: str | None) -> str:
    """Format ISO timestamp to HH:MM."""
    if not ts_str:
        return "??:??"
    try:
        dt = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
        return dt.strftime("%H:%M")
    except:
        return "??:??"


def get_project_key(cwd: str) -> str:
    """Convert working directory to project key format."""
    return cwd.replace("/", "-").replace(".", "-")


def select_sessions(conn: sqlite3.Connection, project_key: str, current_session_id: str) -> list[dict]:
    """
    Select sessions for context using the exchange-count algorithm.
    Returns list of session dicts with messages.
    """
    cursor = conn.cursor()

    # Get project ID
    cursor.execute("SELECT id FROM projects WHERE key = ?", (project_key,))
    row = cursor.fetchone()
    if not row:
        return []
    project_id = row[0]

    # Get recent sessions (newest first), excluding current and subagents
    cursor.execute("""
        SELECT id, uuid, started_at, ended_at, exchange_count, files_modified, commits, git_branch
        FROM sessions
        WHERE project_id = ?
          AND uuid != ?
          AND parent_session_id IS NULL
        ORDER BY started_at DESC
        LIMIT 20
    """, (project_id, current_session_id))

    candidates = cursor.fetchall()
    selected = []

    for session in candidates:
        session_id, uuid, started_at, ended_at, exchange_count, files_json, commits_json, git_branch = session

        # Skip 1-exchange sessions (noise)
        if exchange_count <= 1:
            continue

        # Get messages for this session
        cursor.execute("""
            SELECT role, content, timestamp
            FROM messages
            WHERE session_id = ?
            ORDER BY timestamp ASC
        """, (session_id,))

        messages = [{"role": r, "content": c, "timestamp": t} for r, c, t in cursor.fetchall()]

        session_data = {
            "uuid": uuid,
            "started_at": started_at,
            "ended_at": ended_at,
            "exchange_count": exchange_count,
            "files_modified": json.loads(files_json) if files_json else [],
            "commits": json.loads(commits_json) if commits_json else [],
            "git_branch": git_branch,
            "messages": messages
        }

        # 2-exchange: load it, keep looking unless at limit
        if exchange_count == 2:
            selected.append(session_data)
            if len(selected) >= MAX_SESSIONS:
                break
            continue

        # >2 exchanges: load it and stop (sufficient context)
        if exchange_count > 2:
            selected.append(session_data)
            break

    return selected


def build_context(sessions: list[dict]) -> str:
    """Build markdown context from selected sessions."""
    if not sessions:
        return ""

    lines = []

    for i, session in enumerate(sessions):
        if i > 0:
            lines.append("\n---\n")

        # Session timeline
        start = format_time(session["started_at"])
        end = format_time(session["ended_at"])
        lines.append(f"### Session: {start} → {end}\n")

        # Files modified
        files = session.get("files_modified", [])
        if files:
            lines.append("### Files Modified")
            for f in files[-10:]:  # Last 10
                lines.append(f"- `{f}`")
            if len(files) > 10:
                lines.append(f"- ...and {len(files) - 10} more")
            lines.append("")

        # Git commits
        commits = session.get("commits", [])
        if commits:
            lines.append("### Git Commits")
            for c in commits:
                lines.append(f"- {c}")
            lines.append("")

        # Messages - structure: first (goal), middle (summarized), last 3 (full)
        messages = session.get("messages", [])
        user_messages = [m for m in messages if m["role"] == "user"]
        total = len(user_messages)

        if total == 0:
            continue

        last3_start = max(0, total - 3)
        last3_idx = set(range(last3_start, total))

        # First exchange if not in last 3
        if 0 not in last3_idx and total > 3:
            lines.append("### Session Goal")
            lines.append(user_messages[0]["content"][:1000])
            lines.append("")

        # Middle requests (summarized)
        if total > 4:
            lines.append("### Other Requests")
            for idx in range(1, last3_start):
                msg = user_messages[idx]["content"]
                lines.append(f"- {msg[:300]}..." if len(msg) > 300 else f"- {msg}")
            lines.append("")

        # Last 3 exchanges in full
        lines.append("### Where We Left Off\n")

        # Build exchange pairs from all messages
        exchanges = []
        current_user = None
        current_asst = []

        for m in messages:
            if m["role"] == "user":
                if current_user is not None:
                    exchanges.append({"user": current_user, "asst": "\n\n".join(current_asst), "ts": m["timestamp"]})
                current_user = m["content"]
                current_asst = []
            elif m["role"] == "assistant" and current_user is not None:
                current_asst.append(m["content"])

        if current_user is not None:
            exchanges.append({"user": current_user, "asst": "\n\n".join(current_asst), "ts": None})

        # Show last 3 exchanges
        for ex in exchanges[-3:]:
            t = format_time(ex.get("ts"))
            lines.append(f"**[{t}] User:**")
            lines.append(ex["user"][:2000])
            lines.append("")
            if ex["asst"]:
                lines.append(f"**[{t}] Assistant:**")
                lines.append(ex["asst"][:2000])
                lines.append("")

    return "\n".join(lines)


def main():
    # Read hook input from stdin
    try:
        hook_input = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        hook_input = {}

    cwd = hook_input.get("cwd")
    session_id = hook_input.get("session_id")
    source = hook_input.get("source", "startup")

    # Only inject on fresh sessions
    if source not in ("startup", "clear"):
        print(json.dumps({}))
        return

    if not cwd or not session_id:
        print(json.dumps({}))
        return

    # Check if database exists
    if not DEFAULT_DB_PATH.exists():
        print(json.dumps({}))
        return

    try:
        conn = sqlite3.connect(DEFAULT_DB_PATH)
        project_key = get_project_key(cwd)
        sessions = select_sessions(conn, project_key, session_id)
        conn.close()

        if not sessions:
            print(json.dumps({}))
            return

        context = build_context(sessions)
        if not context:
            print(json.dumps({}))
            return

        # Wrap in section header
        full_context = f"## Previous Session Context\n\n{context}"

        output = {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": full_context
            }
        }
        print(json.dumps(output))

    except Exception as e:
        # Don't block session start on errors
        print(json.dumps({}))
        sys.exit(0)


if __name__ == "__main__":
    main()
