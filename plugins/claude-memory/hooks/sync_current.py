#!/usr/bin/env python3
"""
Incremental sync for current session only.
Designed to be called from a Stop hook - fast and lightweight.

Reads session_id from stdin (hook input) and only syncs that session file.
Detects conversation branches (from rewind) and stores each branch separately.

v3 schema: messages stored once per session, branches as a separate index.
"""

import json
import sqlite3
import sys
from pathlib import Path

# Add path to shared utils
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent / "skills" / "past-conversations" / "scripts"))

from memory_utils import (
    DEFAULT_PROJECTS_DIR,
    get_db_connection,
    load_settings,
    setup_logging,
    extract_text_content,
    is_tool_result,
    parse_jsonl_file,
    parse_all_with_uuids,
    extract_session_metadata,
    find_all_branches,
    compute_branch_metadata,
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
    Sync a single session file using v3 schema.
    Messages stored once, branches tracked via branch_messages mapping.
    Returns total number of new messages added.
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

    # Parse all entries with UUIDs for branch detection
    all_entries = list(parse_all_with_uuids(filepath))
    if not all_entries:
        return 0

    # Find all branches
    branches = find_all_branches(all_entries)
    if not branches:
        return 0

    # Parse user/assistant messages for import
    messages = list(parse_jsonl_file(filepath))
    if not messages:
        return 0

    # Extract session-level metadata from all entries
    meta = extract_session_metadata(all_entries)

    # Step 1: Upsert ONE session row
    cursor.execute("""
        INSERT INTO sessions (uuid, project_id, git_branch, cwd)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(uuid) DO UPDATE SET
            git_branch = COALESCE(excluded.git_branch, sessions.git_branch),
            cwd = COALESCE(excluded.cwd, sessions.cwd)
        RETURNING id
    """, (session_uuid, project_id, meta["git_branch"], meta["cwd"]))
    session_id = cursor.fetchone()[0]

    # Step 2: Insert ALL messages once, dedup by (session_id, uuid)
    existing_uuids = set()
    cursor.execute(
        "SELECT uuid FROM messages WHERE session_id = ? AND uuid IS NOT NULL",
        (session_id,)
    )
    existing_uuids = {row[0] for row in cursor.fetchall()}

    new_count = 0
    for entry in messages:
        entry_type = entry.get("type")
        if entry_type not in ("user", "assistant"):
            continue

        message = entry.get("message", {})
        content = message.get("content", "")

        if entry_type == "user" and is_tool_result(content):
            continue

        text, has_tool_use, has_thinking, tool_summary = extract_text_content(content)
        if not text:
            continue

        uuid = entry.get("uuid")
        if uuid and uuid in existing_uuids:
            continue

        cursor.execute("""
            INSERT INTO messages (session_id, uuid, parent_uuid, timestamp, role, content, tool_summary, has_tool_use, has_thinking)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id, uuid) DO NOTHING
        """, (
            session_id,
            uuid,
            entry.get("parentUuid"),
            entry.get("timestamp"),
            entry_type,
            text,
            tool_summary,
            has_tool_use,
            has_thinking
        ))
        if cursor.rowcount > 0:
            new_count += 1
            if uuid:
                existing_uuids.add(uuid)

    # Step 3: Build uuid -> message_id mapping
    cursor.execute(
        "SELECT id, uuid FROM messages WHERE session_id = ? AND uuid IS NOT NULL",
        (session_id,)
    )
    uuid_to_msg_id = {row[1]: row[0] for row in cursor.fetchall()}

    # Step 4: Get existing branch leaf_uuids for this session
    cursor.execute(
        "SELECT id, leaf_uuid FROM branches WHERE session_id = ?",
        (session_id,)
    )
    existing_branches = {row[1]: row[0] for row in cursor.fetchall()}

    current_leaf_uuids = set()

    for branch in branches:
        leaf_uuid = branch["leaf_uuid"]
        branch_uuids = branch["uuids"]
        is_active = branch["is_active"]
        fork_point_uuid = branch.get("fork_point_uuid")
        current_leaf_uuids.add(leaf_uuid)

        # Filter messages to this branch
        branch_msgs = [m for m in messages if m.get("uuid") in branch_uuids]
        branch_msgs.sort(key=lambda e: e.get("timestamp") or "")

        # Compute branch metadata
        branch_meta = extract_session_metadata(branch_msgs)
        exchange_count, files, commits = compute_branch_metadata(branch_msgs)

        if leaf_uuid in existing_branches:
            # Update existing branch
            branch_db_id = existing_branches[leaf_uuid]
            cursor.execute("""
                UPDATE branches SET
                    is_active = ?,
                    fork_point_uuid = ?,
                    started_at = ?,
                    ended_at = ?,
                    exchange_count = ?,
                    files_modified = ?,
                    commits = ?
                WHERE id = ?
            """, (
                int(is_active),
                fork_point_uuid,
                branch_meta["started_at"],
                branch_meta["ended_at"],
                exchange_count,
                json.dumps(files) if files else None,
                json.dumps(commits) if commits else None,
                branch_db_id
            ))
        else:
            # Insert new branch
            cursor.execute("""
                INSERT INTO branches (session_id, leaf_uuid, fork_point_uuid, is_active,
                                      started_at, ended_at, exchange_count, files_modified, commits)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                RETURNING id
            """, (
                session_id,
                leaf_uuid,
                fork_point_uuid,
                int(is_active),
                branch_meta["started_at"],
                branch_meta["ended_at"],
                exchange_count,
                json.dumps(files) if files else None,
                json.dumps(commits) if commits else None
            ))
            branch_db_id = cursor.fetchone()[0]

        # Ensure only one active branch
        if is_active:
            cursor.execute("""
                UPDATE branches SET is_active = 0
                WHERE session_id = ? AND id != ? AND is_active = 1
            """, (session_id, branch_db_id))

        # Rebuild branch_messages for this branch
        cursor.execute("DELETE FROM branch_messages WHERE branch_id = ?", (branch_db_id,))

        for uuid in branch_uuids:
            msg_id = uuid_to_msg_id.get(uuid)
            if msg_id:
                cursor.execute(
                    "INSERT OR IGNORE INTO branch_messages (branch_id, message_id) VALUES (?, ?)",
                    (branch_db_id, msg_id)
                )

    # Step 5: Clean up stale branches
    for old_leaf, old_branch_id in existing_branches.items():
        if old_leaf not in current_leaf_uuids:
            cursor.execute("DELETE FROM branch_messages WHERE branch_id = ?", (old_branch_id,))
            cursor.execute("DELETE FROM branches WHERE id = ?", (old_branch_id,))

    # Clean up orphaned messages (not referenced by any branch)
    cursor.execute("""
        DELETE FROM messages
        WHERE session_id = ? AND id NOT IN (
            SELECT DISTINCT bm.message_id FROM branch_messages bm
            JOIN branches b ON bm.branch_id = b.id
            WHERE b.session_id = ?
        )
    """, (session_id, session_id))

    return new_count


def main():
    # Load settings
    settings = load_settings()
    logger = setup_logging(settings)

    # Check if sync is disabled
    if not settings.get("sync_on_stop", True):
        logger.info("Sync disabled by settings")
        print(json.dumps({"continue": True}))
        return

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
        conn = get_db_connection(settings)
        project_dir = session_file.parent

        # Handle subagent paths
        if project_dir.name == "subagents":
            project_dir = project_dir.parent.parent

        new_messages = sync_session(conn, session_file, project_dir)
        conn.commit()
        conn.close()

        if new_messages > 0:
            logger.info(f"Synced {new_messages} new message(s) from session {session_id[:8]}")

        # Output for hook (continue = True means don't block)
        output = {"continue": True}
        if new_messages > 0:
            output["suppressOutput"] = True  # Don't show in transcript

        print(json.dumps(output))

    except Exception as e:
        logger.error(f"Sync error: {e}")
        # Don't block Claude on sync errors
        print(json.dumps({"continue": True}))
        sys.exit(0)


if __name__ == "__main__":
    main()
