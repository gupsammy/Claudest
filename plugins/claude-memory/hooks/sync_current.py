#!/usr/bin/env python3
"""
Incremental sync for current session only.
Designed to be called from a Stop hook - fast and lightweight.

Reads session_id from stdin (or --input-file) and only syncs that session file.
Detects conversation branches (from rewind) and stores each branch separately.

v3 schema: messages stored once per session, branches as a separate index.
"""

from __future__ import annotations

import argparse
import uuid as uuidlib
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

_UUID_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.IGNORECASE)
# Codex thread IDs are UUIDs today (Codex Desktop emits UUIDv7), but OpenAI's
# Assistants API uses `thread_<random>` strings. Keep this looser to survive
# an upstream ID-format change, while still blocking path traversal: no
# slashes, no dots, no NULs, bounded length.
_CODEX_ID_RE = re.compile(r'^[A-Za-z0-9_-]{8,128}$')


def validate_session_id(session_id: str) -> bool:
    """Validate that session_id is a proper UUID to prevent path traversal."""
    return bool(session_id and _UUID_RE.match(session_id))


def validate_codex_thread_id(thread_id: str) -> bool:
    """Validate a Codex thread ID. Accepts UUIDs and OpenAI-style identifiers
    (thread_<random>), still rejects path traversal characters."""
    return bool(thread_id and _CODEX_ID_RE.match(thread_id))

# Add path to shared utils
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent / "skills" / "recall-conversations" / "scripts"))

from memory_lib.db import (
    CODEX_UNKNOWN_PROJECT_PATH,
    DEFAULT_CODEX_SESSIONS_DIR,
    DEFAULT_PROJECTS_DIR,
    get_db_connection,
    load_settings,
    setup_logging,
)
from memory_lib.content import extract_text_content, is_task_notification, is_teammate_message, is_tool_result, parse_origin
from memory_lib.parsing import (
    parse_jsonl_file, parse_all_with_uuids, extract_session_metadata,
    find_all_branches, compute_branch_metadata, aggregate_branch_content,
)
from memory_lib.formatting import get_project_key, normalize_cwd, normalize_project_key, parse_project_key
from memory_lib.summarizer import compute_context_summary



def _is_under(path: Path, base: Path) -> bool:
    """Check if resolved path is under base directory (Python 3.7+ compatible)."""
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def get_session_file(projects_dir: Path, session_id: str) -> Path | None:
    """Find the JSONL file for a session ID. Validates path stays under projects_dir."""
    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir():
            continue

        # Check main session files
        session_file = project_dir / f"{session_id}.jsonl"
        if session_file.exists():
            # Verify resolved path is still under projects_dir (symlink escape prevention)
            if _is_under(session_file, projects_dir):
                return session_file
            continue

        # Check subagent files
        for subdir in project_dir.iterdir():
            if subdir.is_dir():
                subagents_dir = subdir / "subagents"
                if subagents_dir.exists():
                    for f in subagents_dir.glob(f"*{session_id}*.jsonl"):
                        if _is_under(f, projects_dir):
                            return f

    return None


def get_codex_session_file(sessions_dir: Path, session_id: str) -> Path | None:
    """Find a Codex JSONL transcript by session/thread ID."""
    if not sessions_dir.exists():
        return None
    for f in sessions_dir.rglob(f"*{session_id}*.jsonl"):
        if _is_under(f, sessions_dir):
            return f
    return None


def _extract_codex_text(payload: dict) -> str:
    """Extract visible text from a Codex message payload."""
    content = payload.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        texts = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") in ("input_text", "output_text", "text"):
                text = item.get("text")
                if text:
                    texts.append(text)
        return "\n".join(texts).strip()
    return ""


def _codex_message_uuid(session_id: str, raw_kind: str, ordinal: int) -> str:
    """Create a stable synthetic UUID for a Codex message row."""
    return str(uuidlib.uuid5(uuidlib.NAMESPACE_URL, f"codex:{session_id}:{raw_kind}:{ordinal}"))


def parse_codex_session(filepath: Path) -> tuple[str, list[dict], list[dict], str]:
    """
    Convert a Codex event-stream JSONL into Claude-shaped message entries.

    Minimal adapter: import only visible user/final assistant messages. Commentary,
    tool calls, tool outputs, developer/system context, and reasoning are left for a
    fuller adapter.
    """
    session_meta: dict = {}
    raw_messages: list[tuple[str, str, str, str]] = []

    with filepath.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            timestamp = obj.get("timestamp")
            obj_type = obj.get("type")
            payload = obj.get("payload") or {}

            if obj_type == "session_meta":
                session_meta = payload
                continue

            if obj_type == "event_msg":
                event_type = payload.get("type")
                if event_type == "user_message":
                    text = (payload.get("message") or "").strip()
                    if text:
                        raw_messages.append(("user", "event_msg.user_message", timestamp, text))
                continue

            if obj_type == "response_item":
                if payload.get("type") != "message":
                    continue
                if payload.get("role") != "assistant":
                    continue
                if payload.get("phase") != "final_answer":
                    continue
                text = _extract_codex_text(payload)
                if text:
                    raw_messages.append(("assistant", "response_item.message.final_answer", timestamp, text))

    session_id = session_meta.get("id") or filepath.stem
    cwd = session_meta.get("cwd")
    parent_uuid = None
    entries = []

    for ordinal, (role, raw_kind, timestamp, text) in enumerate(raw_messages):
        msg_uuid = _codex_message_uuid(session_id, raw_kind, ordinal)
        entry = {
            "uuid": msg_uuid,
            "parentUuid": parent_uuid,
            "type": role,
            "timestamp": timestamp,
            "cwd": cwd,
            "gitBranch": None,
            "message": {"role": role, "content": text},
            "origin": {"kind": "channel", "server": "plugin:codex:codex"},
        }
        entries.append(entry)
        parent_uuid = msg_uuid

    return session_id, entries, entries, cwd or ""


def sync_entries(
    conn: sqlite3.Connection,
    session_uuid: str,
    all_entries: list[dict],
    messages: list[dict],
    project_key: str,
    fallback_project_path: str,
) -> int:
    """
    Sync a single session file using v3 schema.
    Messages stored once, branches tracked via branch_messages mapping.
    Returns total number of new messages added.
    """
    cursor = conn.cursor()

    if not all_entries:
        return 0

    # Find all branches
    branches = find_all_branches(all_entries)
    if not branches:
        return 0

    if not messages:
        return 0

    # Extract session-level metadata from all entries (before project insert so we can use cwd)
    meta = extract_session_metadata(all_entries)

    # Get or create project
    project_key = normalize_project_key(project_key)
    raw_path = meta["cwd"] if meta.get("cwd") else fallback_project_path
    project_path = normalize_cwd(raw_path)
    project_name = Path(project_path).name

    # First try to find existing project by key
    cursor.execute("SELECT id, path FROM projects WHERE key = ?", (project_key,))
    existing = cursor.fetchone()
    if existing:
        project_id = existing[0]
        # Update path/name if we now have better data
        if project_path != existing[1]:
            cursor.execute("UPDATE projects SET path = ?, name = ? WHERE id = ?",
                           (project_path, project_name, project_id))
    else:
        cursor.execute("""
            INSERT INTO projects (path, key, name)
            VALUES (?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET key = excluded.key, name = excluded.name
        """, (project_path, project_key, project_name))
        cursor.execute("SELECT id FROM projects WHERE key = ?", (project_key,))
        project_id = cursor.fetchone()[0]

    # Step 1: Upsert ONE session row
    cursor.execute("""
        INSERT INTO sessions (uuid, project_id, git_branch, cwd)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(uuid) DO UPDATE SET
            git_branch = COALESCE(excluded.git_branch, sessions.git_branch),
            cwd = COALESCE(excluded.cwd, sessions.cwd)
    """, (session_uuid, project_id, meta["git_branch"], meta["cwd"]))
    cursor.execute("SELECT id FROM sessions WHERE uuid = ?", (session_uuid,))
    session_id = cursor.fetchone()[0]

    # Build set of all UUIDs claimed by any branch (filter for message insertion)
    valid_branch_uuids = set()
    for branch in branches:
        valid_branch_uuids.update(branch["uuids"])

    # Step 2: Insert messages that belong to a branch, dedup by (session_id, uuid)
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

        uuid = entry.get("uuid")
        if not uuid:
            continue

        # Skip messages not claimed by any branch (prevents orphan ingestion)
        if uuid not in valid_branch_uuids:
            continue

        notification = 1 if (entry_type == "user" and (is_task_notification(content) or is_teammate_message(content))) else 0

        text, has_tool_use, has_thinking, tool_summary = extract_text_content(content)
        if not text:
            continue

        if uuid in existing_uuids:
            continue

        origin = parse_origin(entry)
        cursor.execute("""
            INSERT INTO messages (session_id, uuid, parent_uuid, timestamp, role, content, tool_summary, has_tool_use, has_thinking, is_notification, origin)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            has_thinking,
            notification,
            origin,
        ))
        if cursor.rowcount > 0:
            new_count += 1
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

    for branch in branches:
        leaf_uuid = branch["leaf_uuid"]
        branch_uuids = branch["uuids"]
        is_active = branch["is_active"]
        fork_point_uuid = branch.get("fork_point_uuid")

        # Filter messages to this branch
        branch_msgs = [m for m in messages if m.get("uuid") in branch_uuids]
        branch_msgs.sort(key=lambda e: e.get("timestamp") or "")

        # Compute branch metadata
        branch_meta = extract_session_metadata(branch_msgs)
        exchange_count, files, commits, tool_counts = compute_branch_metadata(branch_msgs)

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
                    commits = ?,
                    tool_counts = ?
                WHERE id = ?
            """, (
                int(is_active),
                fork_point_uuid,
                branch_meta["started_at"],
                branch_meta["ended_at"],
                exchange_count,
                json.dumps(files) if files else None,
                json.dumps(commits) if commits else None,
                json.dumps(tool_counts) if tool_counts else None,
                branch_db_id
            ))
        else:
            # Insert new branch
            cursor.execute("""
                INSERT INTO branches (session_id, leaf_uuid, fork_point_uuid, is_active,
                                      started_at, ended_at, exchange_count, files_modified, commits, tool_counts)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                session_id,
                leaf_uuid,
                fork_point_uuid,
                int(is_active),
                branch_meta["started_at"],
                branch_meta["ended_at"],
                exchange_count,
                json.dumps(files) if files else None,
                json.dumps(commits) if commits else None,
                json.dumps(tool_counts) if tool_counts else None
            ))
            branch_db_id = cursor.lastrowid

        # Ensure only one active branch
        if is_active:
            cursor.execute("""
                UPDATE branches SET is_active = 0
                WHERE session_id = ? AND id != ? AND is_active = 1
            """, (session_id, branch_db_id))

        # Update branch_messages via targeted diff (not full rebuild)
        cursor.execute(
            "SELECT message_id FROM branch_messages WHERE branch_id = ?",
            (branch_db_id,)
        )
        existing_bm_ids = {row[0] for row in cursor.fetchall()}

        desired_bm_ids = set()
        for uuid in branch_uuids:
            msg_id = uuid_to_msg_id.get(uuid)
            if msg_id:
                desired_bm_ids.add(msg_id)

        to_add = desired_bm_ids - existing_bm_ids
        to_remove = existing_bm_ids - desired_bm_ids

        if to_remove:
            ph = ",".join("?" * len(to_remove))
            cursor.execute(
                f"DELETE FROM branch_messages WHERE branch_id = ? AND message_id IN ({ph})",
                (branch_db_id, *to_remove)
            )
        if to_add:
            cursor.executemany(
                "INSERT OR IGNORE INTO branch_messages (branch_id, message_id) VALUES (?, ?)",
                [(branch_db_id, mid) for mid in to_add]
            )

        # Aggregate branch content for FTS
        agg_content = aggregate_branch_content(cursor, branch_db_id)
        cursor.execute(
            "UPDATE branches SET aggregated_content = ? WHERE id = ?",
            (agg_content, branch_db_id)
        )

        # Compute and store context summary
        try:
            summary_md, summary_json = compute_context_summary(cursor, branch_db_id)
            cursor.execute("""
                UPDATE branches SET context_summary = ?, context_summary_json = ?, summary_version = 2
                WHERE id = ?
            """, (summary_md, summary_json, branch_db_id))
        except Exception:
            pass  # Don't fail sync on summary errors

    return new_count


def sync_session(conn: sqlite3.Connection, filepath: Path, project_dir: Path) -> int:
    """
    Sync a single Claude Code session file using v3 schema.
    Messages stored once, branches tracked via branch_messages mapping.
    Returns total number of new messages added.
    """
    session_uuid = filepath.stem
    if session_uuid.startswith("agent-"):
        session_uuid = session_uuid[6:]

    all_entries = list(parse_all_with_uuids(filepath))
    messages = list(parse_jsonl_file(filepath))
    project_key = project_dir.name
    fallback_project_path = parse_project_key(project_key)
    return sync_entries(conn, session_uuid, all_entries, messages, project_key, fallback_project_path)


def sync_codex_session(conn: sqlite3.Connection, filepath: Path) -> int:
    """
    Sync a Codex Desktop session file using the minimal visible transcript adapter.
    """
    session_uuid, all_entries, messages, cwd = parse_codex_session(filepath)
    if not validate_codex_thread_id(session_uuid):
        return 0
    fallback_project_path = cwd or CODEX_UNKNOWN_PROJECT_PATH
    project_key = get_project_key(fallback_project_path)
    return sync_entries(conn, session_uuid, all_entries, messages, project_key, fallback_project_path)


def main():
    parser = argparse.ArgumentParser(description="Sync current session to memory database")
    parser.add_argument(
        "--input-file",
        type=Path,
        help="Read hook input from file instead of stdin (used by memory-sync.py wrapper)"
    )
    args = parser.parse_args()

    # Load settings
    settings = load_settings()
    logger = setup_logging(settings)

    # Check if sync is disabled
    if not settings.get("sync_on_stop", True):
        logger.info("Sync disabled by settings")
        print(json.dumps({"continue": True}))
        return

    # Read hook input from file or stdin
    if args.input_file:
        try:
            hook_input = json.loads(args.input_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            hook_input = {}
        finally:
            # Clean up temp file
            try:
                os.unlink(args.input_file)
            except OSError:
                pass
    else:
        try:
            hook_input = json.load(sys.stdin)
        except (json.JSONDecodeError, EOFError):
            hook_input = {}

    session_id = hook_input.get("session_id") or os.environ.get("CODEX_THREAD_ID")
    transcript_path = str(hook_input.get("transcript_path") or "")
    codex_thread_id = os.environ.get("CODEX_THREAD_ID")

    # Mirror memory-sync.py:28 detection — env var OR transcript path. Env-only
    # silently misroutes manual reruns of this script (no inherited env) to the
    # Claude path, where the rglob finds nothing and the import zeros out.
    is_codex = bool(
        (codex_thread_id and (not session_id or session_id == codex_thread_id))
        or "/.codex/sessions/" in transcript_path
    )

    # Validate against the appropriate ID format. Claude sessions are
    # guaranteed UUID; Codex thread IDs are UUID today but may shift to
    # OpenAI-style thread_* in the future.
    if is_codex:
        valid = bool(session_id and validate_codex_thread_id(session_id))
    else:
        valid = bool(session_id and validate_session_id(session_id))

    if not valid:
        # No session ID or invalid format — exit silently
        print(json.dumps({"continue": True}))
        return

    # Find session file
    if is_codex:
        session_file = get_codex_session_file(DEFAULT_CODEX_SESSIONS_DIR, session_id)
    else:
        session_file = get_session_file(DEFAULT_PROJECTS_DIR, session_id)

    if not session_file:
        print(json.dumps({"continue": True}))
        return

    # Sync
    try:
        conn = get_db_connection(settings)
        if is_codex:
            new_messages = sync_codex_session(conn, session_file)
        else:
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
