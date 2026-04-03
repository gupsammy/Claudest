#!/usr/bin/env python3
"""
Import Claude Code JSONL conversations into SQLite memory database.

Extracts only searchable text content, skipping progress entries (90% of file size).
Detects conversation branches (from rewind) and stores each branch separately.

v3 schema: messages stored once per session, branches as separate index.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from typing import Optional

# Add path to shared utils
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent / "skills" / "recall-conversations" / "scripts"))

from memory_lib.db import (
    DEFAULT_DB_PATH, DEFAULT_PROJECTS_DIR, get_db_path,
    get_db_connection, load_settings, setup_logging, detect_fts_support,
)
from memory_lib.content import extract_text_content, is_task_notification, is_teammate_message, is_tool_result, sanitize_fts_term
from memory_lib.parsing import (
    parse_jsonl_file, parse_all_with_uuids, extract_session_metadata,
    find_all_branches, compute_branch_metadata, aggregate_branch_content,
)
from memory_lib.formatting import normalize_cwd, normalize_project_key, parse_project_key, extract_project_name
from memory_lib.summarizer import compute_context_summary


def get_file_hash(filepath: Path) -> str:
    """Get MD5 hash of file for change detection."""
    h = hashlib.md5()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def import_session(
    conn: sqlite3.Connection,
    filepath: Path,
    project_id: int,
    parent_session_id: Optional[int] = None
) -> tuple[int, int]:
    """
    Import a single session JSONL file with v3 schema.
    Messages stored once, branches tracked via branch_messages.
    Returns: (branches_imported, total_message_count)
    """
    cursor = conn.cursor()

    # Check if already imported with same hash
    file_hash = get_file_hash(filepath)
    cursor.execute(
        "SELECT id, file_hash FROM import_log WHERE file_path = ?",
        (str(filepath),)
    )
    log_row = cursor.fetchone()
    if log_row and log_row[1] == file_hash:
        return -1, 0

    # Parse all entries for branch detection
    all_entries = list(parse_all_with_uuids(filepath))
    if not all_entries:
        return -1, 0

    # Find all branches
    branches = find_all_branches(all_entries)
    if not branches:
        return -1, 0

    # Parse user/assistant messages
    messages = list(parse_jsonl_file(filepath))
    if not messages:
        return -1, 0

    # Extract session UUID from filename
    session_uuid = filepath.stem
    if session_uuid.startswith("agent-"):
        session_uuid = session_uuid[6:]

    # Extract session-level metadata
    meta = extract_session_metadata(all_entries)

    # Step 1: Upsert ONE session row
    cursor.execute("""
        INSERT INTO sessions (uuid, project_id, parent_session_id, git_branch, cwd)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(uuid) DO UPDATE SET
            git_branch = COALESCE(excluded.git_branch, sessions.git_branch),
            cwd = COALESCE(excluded.cwd, sessions.cwd),
            parent_session_id = COALESCE(excluded.parent_session_id, sessions.parent_session_id)
    """, (session_uuid, project_id, parent_session_id, meta["git_branch"], meta["cwd"]))
    cursor.execute("SELECT id FROM sessions WHERE uuid = ?", (session_uuid,))
    session_id = cursor.fetchone()[0]

    # Step 2: Clear existing data for re-import (FK-safe order: branch_messages → branches → messages)
    cursor.execute("SELECT id FROM branches WHERE session_id = ?", (session_id,))
    old_branch_ids = [row[0] for row in cursor.fetchall()]
    if old_branch_ids:
        placeholders = ",".join("?" * len(old_branch_ids))
        # Placeholders are auto-generated "?" strings; values are DB-generated integers
        cursor.execute(f"DELETE FROM branch_messages WHERE branch_id IN ({placeholders})", old_branch_ids)
    cursor.execute("DELETE FROM branches WHERE session_id = ?", (session_id,))
    cursor.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))

    total_messages = 0
    for entry in messages:
        entry_type = entry.get("type")
        if entry_type not in ("user", "assistant"):
            continue

        message = entry.get("message", {})
        content = message.get("content", "")

        if entry_type == "user" and is_tool_result(content):
            continue

        notification = 1 if (entry_type == "user" and (is_task_notification(content) or is_teammate_message(content))) else 0

        text, has_tool_use, has_thinking, tool_summary = extract_text_content(content)
        if not text:
            continue

        cursor.execute("""
            INSERT INTO messages (session_id, uuid, parent_uuid, timestamp, role, content, tool_summary, has_tool_use, has_thinking, is_notification)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id, uuid) DO NOTHING
        """, (
            session_id,
            entry.get("uuid"),
            entry.get("parentUuid"),
            entry.get("timestamp"),
            entry_type,
            text,
            tool_summary,
            has_tool_use,
            has_thinking,
            notification,
        ))
        if cursor.rowcount > 0:
            total_messages += 1

    # Skip sessions with no extractable messages
    if total_messages == 0:
        cursor.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        return -1, 0

    # Step 3: Build uuid -> message_id mapping
    cursor.execute(
        "SELECT id, uuid FROM messages WHERE session_id = ? AND uuid IS NOT NULL",
        (session_id,)
    )
    uuid_to_msg_id = {row[1]: row[0] for row in cursor.fetchall()}

    # Step 4: Build branches + branch_messages
    branches_imported = 0

    for branch in branches:
        leaf_uuid = branch["leaf_uuid"]
        branch_uuids = branch["uuids"]
        is_active = branch["is_active"]
        fork_point_uuid = branch.get("fork_point_uuid")

        # Filter messages to this branch
        branch_msgs = [m for m in messages if m.get("uuid") in branch_uuids]
        branch_msgs.sort(key=lambda e: e.get("timestamp") or "")

        if not branch_msgs:
            continue

        # Compute branch metadata
        branch_meta = extract_session_metadata(branch_msgs)
        exchange_count, files, commits, tool_counts = compute_branch_metadata(branch_msgs)

        # Insert branch
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

        # Insert branch_messages mapping
        for uuid in branch_uuids:
            msg_id = uuid_to_msg_id.get(uuid)
            if msg_id:
                cursor.execute(
                    "INSERT OR IGNORE INTO branch_messages (branch_id, message_id) VALUES (?, ?)",
                    (branch_db_id, msg_id)
                )

        # Aggregate branch content for FTS
        agg_content = aggregate_branch_content(cursor, branch_db_id)
        if not agg_content:
            # No searchable content — remove this branch
            cursor.execute("DELETE FROM branch_messages WHERE branch_id = ?", (branch_db_id,))
            cursor.execute("DELETE FROM branches WHERE id = ?", (branch_db_id,))
            continue

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
            pass  # Don't fail import on summary errors

        branches_imported += 1

    # Clean up orphaned messages (not referenced by any branch)
    cursor.execute("""
        DELETE FROM messages
        WHERE session_id = ? AND id NOT IN (
            SELECT DISTINCT bm.message_id FROM branch_messages bm
            JOIN branches b ON bm.branch_id = b.id
            WHERE b.session_id = ?
        )
    """, (session_id, session_id))

    # If no branches survived, remove the empty session
    if branches_imported == 0:
        cursor.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        cursor.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        return -1, 0

    # Step 5: Update import_log
    if log_row:
        cursor.execute(
            "UPDATE import_log SET file_hash = ?, imported_at = CURRENT_TIMESTAMP, messages_imported = ? WHERE file_path = ?",
            (file_hash, total_messages, str(filepath))
        )
    else:
        cursor.execute(
            "INSERT INTO import_log (file_path, file_hash, messages_imported) VALUES (?, ?, ?)",
            (str(filepath), file_hash, total_messages)
        )

    return branches_imported, total_messages


def import_project(
    conn: sqlite3.Connection,
    project_dir: Path,
    exclude_projects: list[str] | None = None
) -> tuple[int, int, int]:
    """
    Import all sessions from a project directory.
    Returns: (sessions_imported, messages_imported, sessions_skipped)
    """
    cursor = conn.cursor()

    project_key = normalize_project_key(project_dir.name)
    # Try to get real path from first session's metadata (avoids lossy hyphen reconstruction)
    raw_path = None
    for f in sorted(project_dir.glob("*.jsonl"))[:1]:
        try:
            first_entries = list(parse_all_with_uuids(f))
            meta = extract_session_metadata(first_entries)
            if meta.get("cwd"):
                raw_path = meta["cwd"]
                break
        except Exception:
            pass
    if not raw_path:
        raw_path = parse_project_key(project_key)
    project_path = normalize_cwd(raw_path)
    project_name = extract_project_name(project_path)

    if exclude_projects and project_name in exclude_projects:
        return 0, 0, 0

    cursor.execute("SELECT id, path FROM projects WHERE key = ?", (project_key,))
    existing = cursor.fetchone()
    if existing:
        project_id = existing[0]
        if project_path != existing[1]:
            cursor.execute(
                "UPDATE projects SET path = ?, name = ? WHERE id = ?",
                (project_path, project_name, project_id),
            )
    else:
        cursor.execute(
            "INSERT INTO projects (path, key, name) VALUES (?, ?, ?)"
            " ON CONFLICT(path) DO UPDATE SET key = excluded.key, name = excluded.name",
            (project_path, project_key, project_name),
        )
        cursor.execute("SELECT id FROM projects WHERE key = ?", (project_key,))
        project_id = cursor.fetchone()[0]

    sessions_imported = 0
    messages_imported = 0
    sessions_skipped = 0

    for jsonl_file in project_dir.glob("*.jsonl"):
        if jsonl_file.name.startswith("."):
            continue

        branches_count, msg_count = import_session(conn, jsonl_file, project_id)
        if branches_count == -1:
            sessions_skipped += 1
        else:
            sessions_imported += branches_count
            messages_imported += msg_count

        # Check for subagents
        session_uuid = jsonl_file.stem
        subagents_dir = project_dir / session_uuid / "subagents"
        if subagents_dir.exists():
            for subagent_file in subagents_dir.glob("*.jsonl"):
                # Skip prompt_suggestion agents (autocomplete noise)
                if "prompt_suggestion" in subagent_file.stem:
                    sessions_skipped += 1
                    continue
                # For subagents, find parent session id
                cursor.execute(
                    "SELECT id FROM sessions WHERE uuid = ? LIMIT 1",
                    (session_uuid,)
                )
                parent_row = cursor.fetchone()
                parent_sid = parent_row[0] if parent_row else None

                sub_branches, sub_msg_count = import_session(
                    conn, subagent_file, project_id, parent_session_id=parent_sid
                )
                if sub_branches != -1:
                    sessions_imported += sub_branches
                    messages_imported += sub_msg_count
                else:
                    sessions_skipped += 1

    return sessions_imported, messages_imported, sessions_skipped


def main():
    parser = argparse.ArgumentParser(
        description="Import Claude Code conversations into SQLite"
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB_PATH,
        help=f"Database path (default: {DEFAULT_DB_PATH})"
    )
    parser.add_argument(
        "--projects-dir",
        type=Path,
        default=DEFAULT_PROJECTS_DIR,
        help=f"Projects directory (default: {DEFAULT_PROJECTS_DIR})"
    )
    parser.add_argument(
        "--project",
        type=str,
        help="Import only specific project (by directory name)"
    )
    parser.add_argument(
        "--search",
        type=str,
        help="Search conversations instead of importing"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Search result limit"
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Show database statistics"
    )

    args = parser.parse_args()

    settings = load_settings()
    logger = setup_logging(settings)

    if args.db != DEFAULT_DB_PATH:
        settings["db_path"] = str(args.db)
    db_path = get_db_path(settings)
    exclude_projects = settings.get("exclude_projects", [])

    # Use get_db_connection which handles migration
    conn = get_db_connection(settings)

    if args.stats:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM projects")
        projects = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM sessions")
        sessions = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM messages")
        messages = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM branches")
        total_branches = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM branches WHERE is_active = 1")
        active = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM branches WHERE is_active = 0")
        abandoned = cursor.fetchone()[0]

        db_size = db_path.stat().st_size if db_path.exists() else 0

        print(f"Database: {db_path}")
        print(f"Size: {db_size / 1024 / 1024:.2f} MB")
        print(f"Projects: {projects}")
        print(f"Sessions: {sessions}")
        print(f"Branches: {total_branches} ({active} active, {abandoned} abandoned)")
        print(f"Messages: {messages}")
        return

    if args.search:
        cursor = conn.cursor()
        terms = args.search.split()
        fts_level = detect_fts_support(conn)

        if fts_level in ("fts5", "fts4"):
            sanitized_terms = [sanitize_fts_term(term) for term in terms]
            sanitized_terms = [t for t in sanitized_terms if t]  # Remove empty terms
            if not sanitized_terms:
                print("No valid search terms after sanitization")
                sys.exit(0)
            fts_query = " OR ".join(f'"{term}"' for term in sanitized_terms)

            if fts_level == "fts5":
                sql = """
                    SELECT
                        m.id, m.timestamp, m.role,
                        snippet(messages_fts, 0, '>>>', '<<<', '...', 32) as snippet,
                        m.content, s.uuid as session_uuid,
                        p.name as project_name, p.path as project_path,
                        bm25(messages_fts) as rank
                    FROM messages_fts
                    JOIN messages m ON messages_fts.rowid = m.id
                    JOIN sessions s ON m.session_id = s.id
                    JOIN projects p ON s.project_id = p.id
                    WHERE messages_fts MATCH ?
                """
            else:
                sql = """
                    SELECT
                        m.id, m.timestamp, m.role,
                        snippet(messages_fts, '>>>', '<<<', '...', -1, 32) as snippet,
                        m.content, s.uuid as session_uuid,
                        p.name as project_name, p.path as project_path,
                        0 as rank
                    FROM messages_fts
                    JOIN messages m ON messages_fts.rowid = m.id
                    JOIN sessions s ON m.session_id = s.id
                    JOIN projects p ON s.project_id = p.id
                    WHERE messages_fts MATCH ?
                """
            params: list = [fts_query]

            if args.project:
                sql += " AND p.name LIKE ?"
                params.append(f"%{args.project}%")

            if fts_level == "fts5":
                sql += " ORDER BY rank LIMIT ?"
            else:
                sql += " ORDER BY m.timestamp DESC LIMIT ?"
            params.append(args.limit)

        else:
            # LIKE fallback
            like_clauses = " AND ".join("m.content LIKE ?" for _ in terms)
            sql = f"""
                SELECT
                    m.id, m.timestamp, m.role,
                    substr(m.content, 1, 200) as snippet,
                    m.content, s.uuid as session_uuid,
                    p.name as project_name, p.path as project_path,
                    0 as rank
                FROM messages m
                JOIN sessions s ON m.session_id = s.id
                JOIN projects p ON s.project_id = p.id
                WHERE {like_clauses}
            """
            params = [f"%{term}%" for term in terms]

            if args.project:
                sql += " AND p.name LIKE ?"
                params.append(f"%{args.project}%")

            sql += " ORDER BY m.timestamp DESC LIMIT ?"
            params.append(args.limit)

        cursor.execute(sql, params)

        results = cursor.fetchall()
        if not results:
            print("No results found.")
            return

        for row in results:
            print(f"\n{'-' * 60}")
            print(f"{row[6]} / {row[5][:8]} - {row[1]} - {row[2]}")
            print(f"{row[3]}")
        print(f"\n{'-' * 60}")
        print(f"Found {len(results)} results")
        return

    # Import mode
    total_sessions = 0
    total_messages = 0
    total_skipped = 0

    if args.project:
        project_dir = args.projects_dir / args.project
        if not project_dir.exists():
            print(f"Project not found: {project_dir}")
            return

        sessions, messages, skipped = import_project(conn, project_dir, exclude_projects)
        conn.commit()
        total_sessions += sessions
        total_messages += messages
        total_skipped += skipped
        print(f"Imported {args.project}: {sessions} branches, {messages} messages")
    else:
        for project_dir in args.projects_dir.iterdir():
            if not project_dir.is_dir() or project_dir.name.startswith("."):
                continue

            sessions, messages, skipped = import_project(conn, project_dir, exclude_projects)
            conn.commit()  # Per-project commit to minimize write-lock window
            total_sessions += sessions
            total_messages += messages
            total_skipped += skipped

            if sessions > 0 or messages > 0:
                print(f"Imported {project_dir.name}: {sessions} branches, {messages} messages")

    conn.close()

    logger.info(f"Import complete: {total_sessions} branches, {total_messages} messages")
    print(f"\nTotal: {total_sessions} branches, {total_messages} messages imported ({total_skipped} unchanged)")

    if db_path.exists():
        db_size = db_path.stat().st_size
        print(f"Database size: {db_size / 1024 / 1024:.2f} MB")


if __name__ == "__main__":
    main()
