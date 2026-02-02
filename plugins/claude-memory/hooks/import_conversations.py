#!/usr/bin/env python3
"""
Import Claude Code JSONL conversations into SQLite memory database.

Extracts only searchable text content, skipping progress entries (90% of file size).
"""

import argparse
import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional, Generator

# Default paths
DEFAULT_PROJECTS_DIR = Path.home() / ".claude" / "projects"
DEFAULT_DB_PATH = Path.home() / ".claude-memory" / "conversations.db"

# Database schema (FTS triggers auto-index on INSERT/UPDATE/DELETE)
SCHEMA = """
-- Projects table (derived from directory structure)
CREATE TABLE IF NOT EXISTS projects (
  id INTEGER PRIMARY KEY,
  path TEXT UNIQUE NOT NULL,
  key TEXT UNIQUE NOT NULL,
  name TEXT,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_projects_key ON projects(key);

-- Sessions table
CREATE TABLE IF NOT EXISTS sessions (
  id INTEGER PRIMARY KEY,
  uuid TEXT UNIQUE NOT NULL,
  project_id INTEGER REFERENCES projects(id),
  parent_session_id INTEGER REFERENCES sessions(id),
  started_at DATETIME,
  ended_at DATETIME,
  git_branch TEXT,
  cwd TEXT,
  message_count INTEGER DEFAULT 0,
  exchange_count INTEGER DEFAULT 0,
  files_modified TEXT,
  commits TEXT,
  imported_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_id);
CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at);
CREATE INDEX IF NOT EXISTS idx_sessions_exchange ON sessions(exchange_count);

-- Messages table (core content)
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY,
  session_id INTEGER NOT NULL REFERENCES sessions(id),
  uuid TEXT,
  parent_uuid TEXT,
  timestamp DATETIME,
  role TEXT CHECK(role IN ('user', 'assistant')),
  content TEXT NOT NULL,
  has_tool_use INTEGER DEFAULT 0,
  has_thinking INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp);
CREATE INDEX IF NOT EXISTS idx_messages_role ON messages(role);

-- FTS5 full-text search (auto-synced via triggers)
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
  content,
  content=messages,
  content_rowid=id,
  tokenize='porter unicode61'
);

CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
  INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
  INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.id, old.content);
END;
CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
  INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.id, old.content);
  INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;

-- Import tracking
CREATE TABLE IF NOT EXISTS import_log (
  id INTEGER PRIMARY KEY,
  file_path TEXT UNIQUE NOT NULL,
  file_hash TEXT,
  imported_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  messages_imported INTEGER DEFAULT 0
);

-- Views
CREATE VIEW IF NOT EXISTS search_results AS
SELECT m.id, m.timestamp, m.role, m.content, s.uuid as session_uuid, p.name as project_name, p.path as project_path
FROM messages m JOIN sessions s ON m.session_id = s.id JOIN projects p ON s.project_id = p.id;

CREATE VIEW IF NOT EXISTS recent_conversations AS
SELECT s.uuid as session_uuid, p.name as project, s.started_at, s.ended_at,
       s.message_count, s.exchange_count, s.files_modified, s.commits, s.git_branch
FROM sessions s JOIN projects p ON s.project_id = p.id ORDER BY s.started_at DESC;
"""


def get_file_hash(filepath: Path) -> str:
    """Get MD5 hash of file for change detection."""
    h = hashlib.md5()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_project_key(key: str) -> str:
    """Convert directory key back to original path."""
    # -Users-samarthgupta-repos-forks-clawdbot -> /Users/samarthgupta/repos/forks/clawdbot
    return "/" + key.replace("-", "/").lstrip("/")


def extract_project_name(path: str) -> str:
    """Extract short project name from path."""
    return Path(path).name


def extract_text_content(content) -> tuple[str, bool, bool]:
    """
    Extract text from message content.
    Returns: (text, has_tool_use, has_thinking)
    """
    has_tool_use = False
    has_thinking = False

    if isinstance(content, str):
        # Clean up command artifacts
        text = re.sub(r'<command-name>.*?</command-name>', '', content, flags=re.DOTALL)
        text = re.sub(r'<command-message>.*?</command-message>', '', text, flags=re.DOTALL)
        text = re.sub(r'<command-args>.*?</command-args>', '', text, flags=re.DOTALL)
        text = re.sub(r'<local-command-stdout>.*?</local-command-stdout>', '', text, flags=re.DOTALL)
        return text.strip(), False, False

    if isinstance(content, list):
        texts = []
        for item in content:
            if isinstance(item, dict):
                item_type = item.get("type", "")
                if item_type == "text":
                    texts.append(item.get("text", ""))
                elif item_type == "tool_use":
                    has_tool_use = True
                    # Optionally capture tool name for searchability
                    tool_name = item.get("name", "")
                    if tool_name:
                        texts.append(f"[Tool: {tool_name}]")
                elif item_type == "thinking":
                    has_thinking = True
                    # Skip thinking content by default (can be large)
                    pass
                elif item_type == "tool_result":
                    # Skip tool results (file contents, command outputs)
                    pass
        return "\n".join(texts).strip(), has_tool_use, has_thinking

    return "", False, False


def is_tool_result(content) -> bool:
    """Check if content is a tool result (not a real user message)."""
    if isinstance(content, list) and content:
        first = content[0]
        if isinstance(first, dict) and first.get("type") == "tool_result":
            return True
    return False


def extract_files_modified(content) -> list[str]:
    """Extract file paths from Edit/Write/MultiEdit tool uses."""
    files = []
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "tool_use":
                name = item.get("name", "")
                inp = item.get("input", {})
                if name in ("Edit", "Write", "MultiEdit") and "file_path" in inp:
                    files.append(inp["file_path"])
    return files


def extract_commits(content) -> list[str]:
    """Extract git commit messages from Bash tool uses."""
    commits = []
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "tool_use":
                if item.get("name") == "Bash":
                    cmd = item.get("input", {}).get("command", "")
                    if "git commit" in cmd:
                        m = re.search(r'-m\s+["\']([^"\']+)["\']', cmd)
                        if m:
                            commits.append(m.group(1)[:100])
    return commits


def parse_jsonl_file(filepath: Path) -> Generator[dict, None, None]:
    """Parse JSONL file line by line, yielding relevant entries."""
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                entry_type = obj.get("type")

                # Skip noise entries (90% of file size)
                if entry_type in ("progress", "file-history-snapshot", "queue-operation"):
                    continue

                # Skip meta messages
                if obj.get("isMeta"):
                    continue

                if entry_type in ("user", "assistant"):
                    yield obj

            except json.JSONDecodeError as e:
                # Skip malformed lines
                pass


def extract_session_metadata(entries: list[dict]) -> dict:
    """Extract session metadata from entries."""
    metadata = {
        "started_at": None,
        "ended_at": None,
        "git_branch": None,
        "cwd": None,
    }

    for entry in entries:
        ts = entry.get("timestamp")
        if ts:
            if metadata["started_at"] is None or ts < metadata["started_at"]:
                metadata["started_at"] = ts
            if metadata["ended_at"] is None or ts > metadata["ended_at"]:
                metadata["ended_at"] = ts

        if not metadata["git_branch"]:
            metadata["git_branch"] = entry.get("gitBranch")
        if not metadata["cwd"]:
            metadata["cwd"] = entry.get("cwd")

    return metadata


def import_session(
    conn: sqlite3.Connection,
    filepath: Path,
    project_id: int,
    parent_session_id: Optional[int] = None
) -> tuple[int, int]:
    """
    Import a single session JSONL file.
    Returns: (session_id, message_count)
    """
    cursor = conn.cursor()

    # Check if already imported with same hash
    file_hash = get_file_hash(filepath)
    cursor.execute(
        "SELECT id, file_hash FROM import_log WHERE file_path = ?",
        (str(filepath),)
    )
    row = cursor.fetchone()
    if row and row[1] == file_hash:
        # Already imported, skip
        return -1, 0

    # Parse all entries first
    entries = list(parse_jsonl_file(filepath))
    if not entries:
        return -1, 0

    # Extract session UUID from filename
    session_uuid = filepath.stem
    if session_uuid.startswith("agent-"):
        session_uuid = session_uuid[6:]  # Remove "agent-" prefix for subagents

    # Extract metadata
    metadata = extract_session_metadata(entries)

    # Insert or update session
    cursor.execute("""
        INSERT INTO sessions (uuid, project_id, parent_session_id, started_at, ended_at, git_branch, cwd)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(uuid) DO UPDATE SET
            started_at = excluded.started_at,
            ended_at = excluded.ended_at,
            git_branch = excluded.git_branch,
            cwd = excluded.cwd
        RETURNING id
    """, (
        session_uuid,
        project_id,
        parent_session_id,
        metadata["started_at"],
        metadata["ended_at"],
        metadata["git_branch"],
        metadata["cwd"]
    ))
    session_id = cursor.fetchone()[0]

    # Delete existing messages for this session (for re-import)
    cursor.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))

    # Track session-level metadata
    message_count = 0
    exchange_count = 0
    all_files = []
    all_commits = []
    has_user = False

    # Insert messages and collect metadata
    for entry in entries:
        entry_type = entry.get("type")
        if entry_type not in ("user", "assistant"):
            continue

        message = entry.get("message", {})
        content = message.get("content", "")

        # Skip tool results for exchange counting
        if entry_type == "user" and is_tool_result(content):
            continue

        text, has_tool_use, has_thinking = extract_text_content(content)
        if not text:
            continue

        role = entry_type
        timestamp = entry.get("timestamp")
        uuid = entry.get("uuid")
        parent_uuid = entry.get("parentUuid")

        cursor.execute("""
            INSERT INTO messages (session_id, uuid, parent_uuid, timestamp, role, content, has_tool_use, has_thinking)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (session_id, uuid, parent_uuid, timestamp, role, text, has_tool_use, has_thinking))
        message_count += 1

        # Count exchanges (real user messages)
        if entry_type == "user":
            if has_user:
                exchange_count += 1
            has_user = True

        # Extract files and commits from assistant messages
        if entry_type == "assistant":
            all_files.extend(extract_files_modified(content))
            all_commits.extend(extract_commits(content))

    # Final exchange count
    if has_user:
        exchange_count += 1

    # Deduplicate files (preserve order, keep last occurrence)
    seen_files = {}
    for f in all_files:
        seen_files[f] = True
    unique_files = list(seen_files.keys())

    # Update session with all metadata
    cursor.execute("""
        UPDATE sessions SET
            message_count = ?,
            exchange_count = ?,
            files_modified = ?,
            commits = ?
        WHERE id = ?
    """, (
        message_count,
        exchange_count,
        json.dumps(unique_files) if unique_files else None,
        json.dumps(all_commits) if all_commits else None,
        session_id
    ))

    # Log import
    if row:
        cursor.execute(
            "UPDATE import_log SET file_hash = ?, imported_at = CURRENT_TIMESTAMP, messages_imported = ? WHERE file_path = ?",
            (file_hash, message_count, str(filepath))
        )
    else:
        cursor.execute(
            "INSERT INTO import_log (file_path, file_hash, messages_imported) VALUES (?, ?, ?)",
            (str(filepath), file_hash, message_count)
        )

    return session_id, message_count


def import_project(conn: sqlite3.Connection, project_dir: Path) -> tuple[int, int, int]:
    """
    Import all sessions from a project directory.
    Returns: (sessions_imported, messages_imported, sessions_skipped)
    """
    cursor = conn.cursor()

    # Parse project info
    project_key = project_dir.name
    project_path = parse_project_key(project_key)
    project_name = extract_project_name(project_path)

    # Insert or get project
    cursor.execute("""
        INSERT INTO projects (path, key, name)
        VALUES (?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET key = excluded.key
        RETURNING id
    """, (project_path, project_key, project_name))
    project_id = cursor.fetchone()[0]

    sessions_imported = 0
    messages_imported = 0
    sessions_skipped = 0

    # Import main session files
    for jsonl_file in project_dir.glob("*.jsonl"):
        if jsonl_file.name.startswith("."):
            continue

        session_id, msg_count = import_session(conn, jsonl_file, project_id)
        if session_id == -1:
            sessions_skipped += 1
        else:
            sessions_imported += 1
            messages_imported += msg_count

        # Check for subagents
        session_uuid = jsonl_file.stem
        subagents_dir = project_dir / session_uuid / "subagents"
        if subagents_dir.exists():
            for subagent_file in subagents_dir.glob("*.jsonl"):
                sub_session_id, sub_msg_count = import_session(
                    conn, subagent_file, project_id, parent_session_id=session_id
                )
                if sub_session_id != -1:
                    sessions_imported += 1
                    messages_imported += sub_msg_count
                else:
                    sessions_skipped += 1

    return sessions_imported, messages_imported, sessions_skipped


def init_database(db_path: Path) -> sqlite3.Connection:
    """Initialize database with schema."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def search(conn: sqlite3.Connection, query: str, limit: int = 20, project: Optional[str] = None) -> list[dict]:
    """
    Search conversations using FTS5.
    Returns list of results with context.
    """
    cursor = conn.cursor()

    # Build FTS query
    terms = query.split()
    fts_query = " OR ".join(f'"{term}"' for term in terms)

    sql = """
        SELECT
            m.id,
            m.timestamp,
            m.role,
            snippet(messages_fts, 0, '>>>', '<<<', '...', 32) as snippet,
            m.content,
            s.uuid as session_uuid,
            p.name as project_name,
            p.path as project_path,
            bm25(messages_fts) as rank
        FROM messages_fts
        JOIN messages m ON messages_fts.rowid = m.id
        JOIN sessions s ON m.session_id = s.id
        JOIN projects p ON s.project_id = p.id
        WHERE messages_fts MATCH ?
    """

    params = [fts_query]

    if project:
        sql += " AND p.name LIKE ?"
        params.append(f"%{project}%")

    sql += " ORDER BY rank LIMIT ?"
    params.append(limit)

    cursor.execute(sql, params)

    results = []
    for row in cursor.fetchall():
        results.append({
            "id": row[0],
            "timestamp": row[1],
            "role": row[2],
            "snippet": row[3],
            "content": row[4],
            "session_uuid": row[5],
            "project": row[6],
            "project_path": row[7],
            "rank": row[8]
        })

    return results


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

    # Initialize or connect to database
    conn = init_database(args.db)

    if args.stats:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM projects")
        projects = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM sessions")
        sessions = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM messages")
        messages = cursor.fetchone()[0]

        db_size = args.db.stat().st_size if args.db.exists() else 0

        print(f"Database: {args.db}")
        print(f"Size: {db_size / 1024 / 1024:.2f} MB")
        print(f"Projects: {projects}")
        print(f"Sessions: {sessions}")
        print(f"Messages: {messages}")
        return

    if args.search:
        results = search(conn, args.search, args.limit, args.project)
        if not results:
            print("No results found.")
            return

        for r in results:
            print(f"\n{'─' * 60}")
            print(f"{r['project']} / {r['session_uuid'][:8]} · {r['timestamp']} · {r['role']}")
            print(f"{r['snippet']}")
        print(f"\n{'─' * 60}")
        print(f"Found {len(results)} results")
        return

    # Import mode
    total_sessions = 0
    total_messages = 0
    total_skipped = 0

    if args.project:
        # Import specific project
        project_dir = args.projects_dir / args.project
        if not project_dir.exists():
            print(f"Project not found: {project_dir}")
            return

        sessions, messages, skipped = import_project(conn, project_dir)
        total_sessions += sessions
        total_messages += messages
        total_skipped += skipped
        print(f"Imported {args.project}: {sessions} sessions, {messages} messages")
    else:
        # Import all projects
        for project_dir in args.projects_dir.iterdir():
            if not project_dir.is_dir() or project_dir.name.startswith("."):
                continue

            sessions, messages, skipped = import_project(conn, project_dir)
            total_sessions += sessions
            total_messages += messages
            total_skipped += skipped

            if sessions > 0 or messages > 0:
                print(f"Imported {project_dir.name}: {sessions} sessions, {messages} messages")

    conn.commit()
    conn.close()

    print(f"\nTotal: {total_sessions} sessions, {total_messages} messages imported ({total_skipped} unchanged)")

    # Show database size
    if args.db.exists():
        db_size = args.db.stat().st_size
        print(f"Database size: {db_size / 1024 / 1024:.2f} MB")


if __name__ == "__main__":
    main()
