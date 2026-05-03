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
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# Add path to shared utils
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent / "skills" / "recall-conversations" / "scripts"))

from memory_lib.db import (
    BACKUP_RETENTION,
    CODEX_IMPORT_SENTINEL,
    DEFAULT_CODEX_SESSIONS_DIR,
    DEFAULT_DB_PATH,
    DEFAULT_PROJECTS_DIR,
    IMPORT_LOCK_PATH,
    detect_fts_support,
    get_db_connection,
    get_db_path,
    load_settings,
    setup_logging,
)
from memory_lib.content import extract_text_content, is_task_notification, is_teammate_message, is_tool_result, sanitize_fts_term, parse_origin
from memory_lib.parsing import (
    parse_jsonl_file, parse_all_with_uuids, extract_session_metadata,
    find_all_branches, compute_branch_metadata, aggregate_branch_content,
)
from memory_lib.formatting import get_project_key, normalize_cwd, normalize_project_key, parse_project_key, extract_project_name
from memory_lib.summarizer import compute_context_summary
from sync_current import parse_codex_session, sync_entries, validate_session_id
from memory_lib.db import CODEX_UNKNOWN_PROJECT_PATH


def _process_alive(pid: int) -> bool:
    """Return True if a process with the given PID exists and we can signal it.

    Uses os.kill(pid, 0) which raises ProcessLookupError if the PID is not in
    use, OSError(EPERM) if it exists but we can't signal it. EPERM still
    counts as alive — different user, but the process is real.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def acquire_import_lock(lock_path: Path = IMPORT_LOCK_PATH) -> int | None:
    """Acquire the bulk-import lock or return None if another import is live.

    Strategy: O_CREAT|O_EXCL to atomically create the lockfile with our PID.
    If it already exists, read the PID inside; if that process is dead, steal
    the stale lock. Otherwise return None — caller exits silently.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):  # Two attempts: one to try, one after stale-cleanup.
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            try:
                holder = lock_path.read_text(encoding="utf-8").strip()
                holder_pid = int(holder)
            except (OSError, ValueError):
                holder_pid = -1
            if _process_alive(holder_pid):
                return None
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
        return os.getpid()
    return None


def release_import_lock(lock_path: Path = IMPORT_LOCK_PATH) -> None:
    """Release the lock if we still hold it (lock contains our PID)."""
    try:
        holder = lock_path.read_text(encoding="utf-8").strip()
        if holder == str(os.getpid()):
            lock_path.unlink()
    except (OSError, ValueError):
        pass


def get_file_hash(filepath: Path) -> str:
    """Get MD5 hash of file for change detection."""
    h = hashlib.md5()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def backup_database(db_path: Path, retention: int = BACKUP_RETENTION) -> Path | None:
    """Create a consistent SQLite backup before bulk import.

    Filenames use microsecond resolution so concurrent runs (rare, gated by
    the import lock — but still possible if a stale lock was just stolen)
    don't collide on the same path.

    After writing the new backup, prune older ones for the same DB stem,
    keeping only the most recent `retention` files.
    """
    if not db_path.exists():
        return None

    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup_path = backup_dir / f"{db_path.stem}-{stamp}{db_path.suffix}"

    src = sqlite3.connect(str(db_path))
    dst = sqlite3.connect(str(backup_path))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()

    # Prune oldest backups for this DB stem. Sorted ascending by name → name
    # contains timestamp → ascending name = ascending time. Keep the newest N.
    backups = sorted(backup_dir.glob(f"{db_path.stem}-*{db_path.suffix}"))
    if len(backups) > retention:
        for old in backups[:-retention]:
            try:
                old.unlink()
            except OSError:
                pass

    return backup_path


def import_session(
    conn: sqlite3.Connection,
    filepath: Path,
    project_id: int,
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
        INSERT INTO sessions (uuid, project_id, git_branch, cwd)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(uuid) DO UPDATE SET
            git_branch = COALESCE(excluded.git_branch, sessions.git_branch),
            cwd = COALESCE(excluded.cwd, sessions.cwd)
    """, (session_uuid, project_id, meta["git_branch"], meta["cwd"]))
    cursor.execute("SELECT id FROM sessions WHERE uuid = ?", (session_uuid,))
    session_id = cursor.fetchone()[0]

    # Step 2: Append-only message insert (never delete — JSONL source expires after 30 days)
    # Build set of UUIDs claimed by any branch to filter noise
    valid_branch_uuids = set()
    for branch in branches:
        valid_branch_uuids.update(branch["uuids"])

    new_messages = 0
    for entry in messages:
        entry_type = entry.get("type")
        if entry_type not in ("user", "assistant"):
            continue

        message = entry.get("message", {})
        content = message.get("content", "")

        if entry_type == "user" and is_tool_result(content):
            continue

        uuid = entry.get("uuid")
        if not uuid or uuid not in valid_branch_uuids:
            continue

        notification = 1 if (entry_type == "user" and (is_task_notification(content) or is_teammate_message(content))) else 0

        text, has_tool_use, has_thinking, tool_summary = extract_text_content(content)
        if not text:
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
            new_messages += 1

    # Check total message count (pre-existing + new) — not just new inserts
    cursor.execute("SELECT COUNT(*) FROM messages WHERE session_id = ?", (session_id,))
    total_messages = cursor.fetchone()[0]

    # Skip sessions with no extractable messages at all
    if total_messages == 0:
        # Safe to remove: session was just created with no data
        cursor.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        return -1, 0

    # Step 3: Build uuid -> message_id mapping
    cursor.execute(
        "SELECT id, uuid FROM messages WHERE session_id = ? AND uuid IS NOT NULL",
        (session_id,)
    )
    uuid_to_msg_id = {row[1]: row[0] for row in cursor.fetchall()}

    # Step 4: Upsert branches + diff branch_messages (mirrors sync_current.py)
    cursor.execute(
        "SELECT id, leaf_uuid FROM branches WHERE session_id = ?",
        (session_id,)
    )
    existing_branches = {row[1]: row[0] for row in cursor.fetchall()}
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

        files_json = json.dumps(files) if files else None
        commits_json = json.dumps(commits) if commits else None
        tool_counts_json = json.dumps(tool_counts) if tool_counts else None

        if leaf_uuid in existing_branches:
            # Update existing branch metadata
            branch_db_id = existing_branches[leaf_uuid]
            cursor.execute("""
                UPDATE branches SET
                    is_active = ?, fork_point_uuid = ?,
                    started_at = ?, ended_at = ?,
                    exchange_count = ?, files_modified = ?, commits = ?, tool_counts = ?
                WHERE id = ?
            """, (
                int(is_active), fork_point_uuid,
                branch_meta["started_at"], branch_meta["ended_at"],
                exchange_count, files_json, commits_json, tool_counts_json,
                branch_db_id
            ))
        else:
            # Insert new branch
            cursor.execute("""
                INSERT INTO branches (session_id, leaf_uuid, fork_point_uuid, is_active,
                                      started_at, ended_at, exchange_count, files_modified, commits, tool_counts)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                session_id, leaf_uuid, fork_point_uuid, int(is_active),
                branch_meta["started_at"], branch_meta["ended_at"],
                exchange_count, files_json, commits_json, tool_counts_json
            ))
            branch_db_id = cursor.lastrowid

        # Ensure only one active branch per session
        if is_active:
            cursor.execute("""
                UPDATE branches SET is_active = 0
                WHERE session_id = ? AND id != ? AND is_active = 1
            """, (session_id, branch_db_id))

        # Diff branch_messages: add missing, remove stale links (not messages)
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
        if not agg_content:
            # No searchable content — skip but don't delete. Deleting causes
            # thrashing: branch exists in JSONL → recreated next import → empty
            # again → deleted again, every cycle. An empty branch row is harmless.
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

    # Check if session has any branches at all (pre-existing + new)
    cursor.execute("SELECT COUNT(*) FROM branches WHERE session_id = ?", (session_id,))
    if cursor.fetchone()[0] == 0:
        # Messages exist (guaranteed by early return above) but no branches —
        # nothing useful for branch-based search.
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

    return sessions_imported, messages_imported, sessions_skipped


def _get_or_create_project(conn: sqlite3.Connection, project_path: str) -> int | None:
    """Get or create a project row by normalized cwd."""
    if not project_path:
        return None

    cursor = conn.cursor()
    normalized_path = normalize_cwd(project_path)
    project_key = get_project_key(normalized_path)
    project_name = extract_project_name(normalized_path)

    cursor.execute("SELECT id, path FROM projects WHERE key = ?", (project_key,))
    existing = cursor.fetchone()
    if existing:
        project_id = existing[0]
        if normalized_path != existing[1]:
            cursor.execute(
                "UPDATE projects SET path = ?, name = ? WHERE id = ?",
                (normalized_path, project_name, project_id),
            )
        return project_id

    cursor.execute(
        "INSERT INTO projects (path, key, name) VALUES (?, ?, ?)"
        " ON CONFLICT(path) DO UPDATE SET key = excluded.key, name = excluded.name",
        (normalized_path, project_key, project_name),
    )
    cursor.execute("SELECT id FROM projects WHERE key = ?", (project_key,))
    row = cursor.fetchone()
    return row[0] if row else None


def import_codex_session(conn: sqlite3.Connection, filepath: Path) -> tuple[int, int]:
    """
    Import one Codex Desktop JSONL transcript using the minimal visible-message adapter.
    Returns: (branches_imported, total_message_count)
    """
    cursor = conn.cursor()

    file_hash = get_file_hash(filepath)
    cursor.execute(
        "SELECT id, file_hash FROM import_log WHERE file_path = ?",
        (str(filepath),)
    )
    log_row = cursor.fetchone()
    if log_row and log_row[1] == file_hash:
        return -1, 0

    session_uuid, all_entries, messages, cwd = parse_codex_session(filepath)
    if not validate_session_id(session_uuid) or not all_entries or not messages:
        return -1, 0

    fallback_project_path = cwd or CODEX_UNKNOWN_PROJECT_PATH
    project_key = get_project_key(fallback_project_path)
    sync_entries(conn, session_uuid, all_entries, messages, project_key, fallback_project_path)

    cursor.execute("SELECT id FROM sessions WHERE uuid = ?", (session_uuid,))
    session_row = cursor.fetchone()
    if not session_row:
        return -1, 0
    session_id = session_row[0]

    cursor.execute("SELECT COUNT(*) FROM messages WHERE session_id = ?", (session_id,))
    total_messages = cursor.fetchone()[0]
    if total_messages == 0:
        return -1, 0

    cursor.execute("SELECT COUNT(*) FROM branches WHERE session_id = ?", (session_id,))
    branches_imported = cursor.fetchone()[0]
    if branches_imported == 0:
        return -1, 0

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


def import_codex_sessions_dir(conn: sqlite3.Connection, sessions_dir: Path) -> tuple[int, int, int]:
    """
    Import all Codex Desktop JSONL transcripts under ~/.codex/sessions.

    Owns its transaction: commits the imported rows before advancing the
    sentinel, so a commit failure leaves the sentinel untouched and the
    next SessionStart re-runs the import.

    Returns: (sessions_imported, messages_imported, sessions_skipped)
    """
    if not sessions_dir.exists():
        return 0, 0, 0

    sessions_imported = 0
    messages_imported = 0
    sessions_skipped = 0

    for jsonl_file in sorted(sessions_dir.rglob("*.jsonl")):
        if jsonl_file.name.startswith("."):
            continue
        branches_count, msg_count = import_codex_session(conn, jsonl_file)
        if branches_count == -1:
            sessions_skipped += 1
        else:
            sessions_imported += branches_count
            messages_imported += msg_count

    # Commit before advancing the sentinel — sentinel must only move forward
    # for work that's actually durable on disk.
    conn.commit()

    CODEX_IMPORT_SENTINEL.parent.mkdir(parents=True, exist_ok=True)
    CODEX_IMPORT_SENTINEL.write_text(datetime.now().isoformat(), encoding="utf-8")

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
    parser.add_argument(
        "--include-codex",
        action="store_true",
        help=f"Also import Codex Desktop sessions from {DEFAULT_CODEX_SESSIONS_DIR}"
    )
    parser.add_argument(
        "--codex-sessions-dir",
        type=Path,
        default=DEFAULT_CODEX_SESSIONS_DIR,
        help=f"Codex sessions directory (default: {DEFAULT_CODEX_SESSIONS_DIR})"
    )
    parser.add_argument(
        "--backup-on-import",
        action="store_true",
        help="Create a SQLite backup before bulk import"
    )

    args = parser.parse_args()

    settings = load_settings()
    logger = setup_logging(settings)

    if args.db != DEFAULT_DB_PATH:
        settings["db_path"] = str(args.db)
    db_path = get_db_path(settings)
    exclude_projects = settings.get("exclude_projects", [])

    is_import_mode = not (args.stats or args.search)

    # Gate concurrent bulk imports (multiple SessionStart hooks racing).
    # Stats/search are read-only and skip the lock.
    if is_import_mode:
        if acquire_import_lock() is None:
            logger.info("Another import is already running; exiting")
            return

    try:
        if args.backup_on_import and is_import_mode:
            try:
                backup_path = backup_database(db_path)
                if backup_path:
                    print(f"Backup created: {backup_path}")
            except Exception as e:
                print(f"Backup failed; refusing to import: {e}", file=sys.stderr)
                sys.exit(1)

        # Use get_db_connection which handles migration
        conn = get_db_connection(settings)
        _run_main(args, conn, db_path, exclude_projects, logger)
    finally:
        if is_import_mode:
            release_import_lock()


def _run_main(args, conn, db_path, exclude_projects, logger):
    """Body of main() after lock acquisition. Split out so the lock try/finally
    in main() can wrap the whole import flow without a deep indent."""

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

    if args.include_codex:
        # import_codex_sessions_dir owns its commit + sentinel update.
        sessions, messages, skipped = import_codex_sessions_dir(conn, args.codex_sessions_dir)
        total_sessions += sessions
        total_messages += messages
        total_skipped += skipped
        if sessions > 0 or messages > 0:
            print(f"Imported Codex sessions: {sessions} branches, {messages} messages")

    conn.close()

    logger.info(f"Import complete: {total_sessions} branches, {total_messages} messages")
    print(f"\nTotal: {total_sessions} branches, {total_messages} messages imported ({total_skipped} unchanged)")

    if db_path.exists():
        db_size = db_path.stat().st_size
        print(f"Database size: {db_size / 1024 / 1024:.2f} MB")


if __name__ == "__main__":
    main()
