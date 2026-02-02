#!/usr/bin/env python3
"""
Incremental sync for current session only.
Designed to be called from a Stop hook - fast and lightweight.

Reads session_id from stdin (hook input) and only syncs that session file.
"""

import json
import sqlite3
import sys
from pathlib import Path

# Import from local import_conversations module
from import_conversations import (
    DEFAULT_DB_PATH,
    DEFAULT_PROJECTS_DIR,
    parse_jsonl_file,
    extract_text_content,
    extract_session_metadata,
    extract_files_modified,
    extract_commits,
    is_tool_result,
    init_database,
)


def get_session_file(projects_dir: Path, session_id: str) -> Path | None:
    """Find the JSONL file for a session ID."""
    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir():
            continue

        # Check main session files
        session_file = project_dir / f"{session_id}.jsonl"
        if session_file.exists():
            return session_file

        # Check subagent files
        for subdir in project_dir.iterdir():
            if subdir.is_dir():
                subagents_dir = subdir / "subagents"
                if subagents_dir.exists():
                    for f in subagents_dir.glob(f"*{session_id}*.jsonl"):
                        return f

    return None


def sync_session(conn: sqlite3.Connection, filepath: Path, project_dir: Path) -> int:
    """
    Sync a single session file incrementally.
    Returns number of new messages added.
    """
    cursor = conn.cursor()

    # Get or create project
    project_key = project_dir.name
    project_path = "/" + project_key.replace("-", "/").lstrip("/")
    project_name = Path(project_path).name

    cursor.execute("""
        INSERT INTO projects (path, key, name)
        VALUES (?, ?, ?)
        ON CONFLICT(path) DO NOTHING
    """, (project_path, project_key, project_name))

    cursor.execute("SELECT id FROM projects WHERE path = ?", (project_path,))
    project_id = cursor.fetchone()[0]

    # Get session UUID
    session_uuid = filepath.stem
    if session_uuid.startswith("agent-"):
        session_uuid = session_uuid[6:]

    # Parse file
    entries = list(parse_jsonl_file(filepath))
    if not entries:
        return 0

    metadata = extract_session_metadata(entries)

    # Get or create session
    cursor.execute("""
        INSERT INTO sessions (uuid, project_id, started_at, ended_at, git_branch, cwd)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(uuid) DO UPDATE SET
            ended_at = excluded.ended_at
        RETURNING id
    """, (
        session_uuid,
        project_id,
        metadata["started_at"],
        metadata["ended_at"],
        metadata["git_branch"],
        metadata["cwd"]
    ))
    session_id = cursor.fetchone()[0]

    # Get existing message UUIDs for this session
    cursor.execute(
        "SELECT uuid FROM messages WHERE session_id = ? AND uuid IS NOT NULL",
        (session_id,)
    )
    existing_uuids = {row[0] for row in cursor.fetchall()}

    # Track session-level metadata
    new_count = 0
    exchange_count = 0
    all_files = []
    all_commits = []
    has_user = False

    # Insert only new messages and collect metadata
    for entry in entries:
        entry_type = entry.get("type")
        if entry_type not in ("user", "assistant"):
            continue

        message = entry.get("message", {})
        content = message.get("content", "")

        # Skip tool results for exchange counting
        if entry_type == "user" and is_tool_result(content):
            continue

        # Count exchanges (all user messages, not just new ones)
        if entry_type == "user":
            if has_user:
                exchange_count += 1
            has_user = True

        # Extract files and commits from all assistant messages
        if entry_type == "assistant":
            all_files.extend(extract_files_modified(content))
            all_commits.extend(extract_commits(content))

        uuid = entry.get("uuid")
        if uuid and uuid in existing_uuids:
            continue  # Already imported

        text, has_tool_use, has_thinking = extract_text_content(content)

        if not text:
            continue

        cursor.execute("""
            INSERT INTO messages (session_id, uuid, parent_uuid, timestamp, role, content, has_tool_use, has_thinking)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            session_id,
            uuid,
            entry.get("parentUuid"),
            entry.get("timestamp"),
            entry_type,
            text,
            has_tool_use,
            has_thinking
        ))
        new_count += 1

    # Final exchange count
    if has_user:
        exchange_count += 1

    # Deduplicate files
    seen_files = {}
    for f in all_files:
        seen_files[f] = True
    unique_files = list(seen_files.keys())

    # Update session with all metadata
    cursor.execute("""
        UPDATE sessions SET
            message_count = (SELECT COUNT(*) FROM messages WHERE session_id = ?),
            exchange_count = ?,
            files_modified = ?,
            commits = ?
        WHERE id = ?
    """, (
        session_id,
        exchange_count,
        json.dumps(unique_files) if unique_files else None,
        json.dumps(all_commits) if all_commits else None,
        session_id
    ))

    return new_count


def main():
    # Read hook input from stdin
    try:
        hook_input = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        hook_input = {}

    session_id = hook_input.get("session_id")

    if not session_id:
        # No session ID provided, exit silently
        print(json.dumps({"continue": True}))
        return

    # Find session file
    session_file = get_session_file(DEFAULT_PROJECTS_DIR, session_id)

    if not session_file:
        print(json.dumps({"continue": True}))
        return

    # Sync
    try:
        conn = init_database(DEFAULT_DB_PATH)
        project_dir = session_file.parent

        # Handle subagent paths
        if project_dir.name == "subagents":
            project_dir = project_dir.parent.parent

        new_messages = sync_session(conn, session_file, project_dir)
        conn.commit()
        conn.close()

        # Output for hook (continue = True means don't block)
        output = {"continue": True}
        if new_messages > 0:
            output["suppressOutput"] = True  # Don't show in transcript

        print(json.dumps(output))

    except Exception as e:
        # Don't block Claude on sync errors
        print(json.dumps({"continue": True}))
        sys.exit(0)


if __name__ == "__main__":
    main()
