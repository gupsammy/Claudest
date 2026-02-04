#!/usr/bin/env python3
"""
Shared utilities for claude-memory plugin.

Consolidates common code used across memory scripts:
- Path constants and settings
- Database connection and schema
- Time formatting
- Logging setup
- JSONL parsing and branch detection
"""

import json
import logging
import re
import sqlite3
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Generator, Optional

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False

# Default paths
DEFAULT_DB_PATH = Path.home() / ".claude-memory" / "conversations.db"
DEFAULT_PROJECTS_DIR = Path.home() / ".claude" / "projects"
DEFAULT_SETTINGS_PATH = Path.home() / ".claude-memory" / "settings.local.md"
DEFAULT_LOG_PATH = Path.home() / ".claude-memory" / "memory.log"

# Default settings
DEFAULT_SETTINGS = {
    "db_path": str(DEFAULT_DB_PATH),
    "auto_inject_context": True,
    "max_context_sessions": 2,
    "exclude_projects": [],
    "logging_enabled": False,
    "sync_on_stop": True,
}

# Database schema — v3: messages stored once, branches as separate index
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

-- Sessions table (ONE row per session UUID)
CREATE TABLE IF NOT EXISTS sessions (
  id INTEGER PRIMARY KEY,
  uuid TEXT UNIQUE NOT NULL,
  project_id INTEGER REFERENCES projects(id),
  parent_session_id INTEGER REFERENCES sessions(id),
  git_branch TEXT,
  cwd TEXT,
  imported_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_id);

-- Branches table (one row per branch per session)
CREATE TABLE IF NOT EXISTS branches (
  id INTEGER PRIMARY KEY,
  session_id INTEGER NOT NULL REFERENCES sessions(id),
  leaf_uuid TEXT NOT NULL,
  fork_point_uuid TEXT,
  is_active INTEGER DEFAULT 1,
  started_at DATETIME,
  ended_at DATETIME,
  exchange_count INTEGER DEFAULT 0,
  files_modified TEXT,
  commits TEXT,
  UNIQUE(session_id, leaf_uuid)
);
CREATE INDEX IF NOT EXISTS idx_branches_session ON branches(session_id);
CREATE INDEX IF NOT EXISTS idx_branches_active ON branches(is_active);

-- Messages table (ALL messages stored ONCE per session)
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY,
  session_id INTEGER NOT NULL REFERENCES sessions(id),
  uuid TEXT,
  parent_uuid TEXT,
  timestamp DATETIME,
  role TEXT CHECK(role IN ('user', 'assistant')),
  content TEXT NOT NULL,
  tool_summary TEXT,
  has_tool_use INTEGER DEFAULT 0,
  has_thinking INTEGER DEFAULT 0,
  UNIQUE(session_id, uuid)
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp);
CREATE INDEX IF NOT EXISTS idx_messages_session_uuid ON messages(session_id, uuid);

-- Branch-messages mapping (many-to-many)
CREATE TABLE IF NOT EXISTS branch_messages (
  branch_id INTEGER NOT NULL REFERENCES branches(id),
  message_id INTEGER NOT NULL REFERENCES messages(id),
  PRIMARY KEY (branch_id, message_id)
);
CREATE INDEX IF NOT EXISTS idx_branch_messages_message ON branch_messages(message_id);

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
SELECT s.uuid as session_uuid, b.leaf_uuid as branch_id, b.is_active as is_active_branch,
       p.name as project, b.started_at, b.ended_at,
       b.exchange_count, b.files_modified, b.commits, s.git_branch
FROM sessions s
JOIN branches b ON b.session_id = s.id
JOIN projects p ON s.project_id = p.id
ORDER BY b.ended_at DESC;
"""


def migrate_db(conn: sqlite3.Connection) -> bool:
    """
    Migrate database to v3 schema (messages-once + branch index).
    Detects old schema by checking if 'branches' table exists.
    If not, deletes the DB file so memory-setup.sh triggers a fresh import.
    Returns True if migration was performed (DB was deleted and recreated).
    """
    cursor = conn.cursor()

    # Check if branches table exists (v3 indicator)
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='branches'")
    if cursor.fetchone():
        return False  # Already on v3

    # Check if sessions table exists at all (could be a fresh DB)
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'")
    if not cursor.fetchone():
        return False  # Fresh DB, no migration needed

    # Old schema detected — nuke and recreate
    db_path = None
    # Get the database file path from connection
    cursor.execute("PRAGMA database_list")
    for row in cursor.fetchall():
        if row[1] == "main" and row[2]:
            db_path = Path(row[2])
            break

    conn.close()

    if db_path and db_path.exists():
        db_path.unlink()

    # Reconnect and create fresh schema
    new_conn = sqlite3.connect(str(db_path) if db_path else ":memory:")
    new_conn.executescript(SCHEMA)
    new_conn.commit()

    # We can't return the new connection through the old reference,
    # so we signal that migration happened and caller should reconnect
    new_conn.close()
    return True


def load_settings(settings_path: Optional[Path] = None) -> dict:
    """
    Load settings from YAML frontmatter in settings file.
    Returns default settings if file doesn't exist or parsing fails.
    """
    path = settings_path or DEFAULT_SETTINGS_PATH
    settings = DEFAULT_SETTINGS.copy()

    if not path.exists():
        return settings

    if not HAS_YAML:
        return settings

    try:
        content = path.read_text()
        # Parse YAML frontmatter (between --- markers)
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                frontmatter = yaml.safe_load(parts[1])  # type: ignore[possibly-undefined]
                if isinstance(frontmatter, dict):
                    settings.update(frontmatter)
    except Exception:
        pass

    return settings


def get_db_path(settings: Optional[dict] = None) -> Path:
    """Get database path from settings or default."""
    if settings and "db_path" in settings:
        return Path(settings["db_path"]).expanduser()
    return DEFAULT_DB_PATH


def _migrate_columns(conn: sqlite3.Connection) -> None:
    """Add any missing columns to existing tables (idempotent)."""
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(messages)")
    existing = {row[1] for row in cursor.fetchall()}
    if "tool_summary" not in existing:
        cursor.execute("ALTER TABLE messages ADD COLUMN tool_summary TEXT")
        conn.commit()


def get_db_connection(settings: Optional[dict] = None) -> sqlite3.Connection:
    """
    Get database connection, initializing schema and running migrations if needed.
    Uses settings-based path if provided.
    """
    db_path = get_db_path(settings)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)

    # Check if migration needed (old schema -> v3)
    migrated = migrate_db(conn)
    if migrated:
        # Connection was closed during migration, reconnect
        conn = sqlite3.connect(db_path)
    else:
        # Apply schema (handles fresh databases, idempotent)
        conn.executescript(SCHEMA)
        conn.commit()

    # Add any missing columns (e.g. tool_summary)
    _migrate_columns(conn)

    return conn


def setup_logging(settings: Optional[dict] = None) -> logging.Logger:
    """
    Set up logging with rotation.
    Returns a null logger if logging is disabled.
    """
    logger = logging.getLogger("claude-memory")
    logger.handlers = []  # Clear existing handlers

    if not settings or not settings.get("logging_enabled", False):
        logger.addHandler(logging.NullHandler())
        return logger

    log_path = DEFAULT_LOG_PATH
    log_path.parent.mkdir(parents=True, exist_ok=True)

    handler = RotatingFileHandler(
        log_path,
        maxBytes=1_000_000,  # 1MB
        backupCount=2
    )
    formatter = logging.Formatter(
        "%(asctime)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    return logger


def format_time(ts_str: Optional[str], fmt: str = "%H:%M") -> str:
    """
    Format ISO timestamp to specified format.
    Default: HH:MM
    """
    if not ts_str:
        return "??:??"
    try:
        dt = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
        return dt.astimezone().strftime(fmt)
    except Exception:
        return ts_str[:16] if ts_str else "??:??"


def format_time_full(ts_str: Optional[str]) -> str:
    """Format ISO timestamp to YYYY-MM-DD HH:MM."""
    return format_time(ts_str, "%Y-%m-%d %H:%M")


def get_project_key(cwd: str) -> str:
    """Convert working directory to project key format."""
    return cwd.replace("/", "-").replace(".", "-")


def parse_project_key(key: str) -> str:
    """Convert directory key back to original path."""
    return "/" + key.replace("-", "/").lstrip("/")


def extract_project_name(path: str) -> str:
    """Extract short project name from path."""
    return Path(path).name


def format_markdown_session(session: dict, verbose: bool = False) -> str:
    """Format a single session as markdown."""
    lines = []

    started = format_time_full(session.get("started_at"))
    project = session.get("project", "Unknown")
    lines.append(f"## {project} | {started}")
    lines.append(f"Session: {session.get('uuid', 'unknown')[:8]}")

    if session.get("git_branch"):
        lines.append(f"Branch: {session['git_branch']}")

    if verbose:
        files = session.get("files_modified", [])
        if files:
            lines.append("\n### Files Modified")
            for f in files[-10:]:
                lines.append(f"- `{f}`")
            if len(files) > 10:
                lines.append(f"- ...and {len(files) - 10} more")

        commits = session.get("commits", [])
        if commits:
            lines.append("\n### Commits")
            for c in commits:
                lines.append(f"- {c}")

    lines.append("\n### Conversation\n")

    for msg in session.get("messages", []):
        role = "User" if msg["role"] == "user" else "Assistant"
        lines.append(f"**{role}:** {msg['content']}\n")

    lines.append("---\n")
    return "\n".join(lines)


def format_json_sessions(sessions: list[dict], extra: Optional[dict] = None) -> str:
    """Format sessions as JSON with metadata."""
    total_messages = sum(len(s.get("messages", [])) for s in sessions)
    output = {
        "sessions": sessions,
        "total_sessions": len(sessions),
        "total_messages": total_messages
    }
    if extra:
        output.update(extra)
    return json.dumps(output, indent=2)


# Content extraction utilities

def extract_text_content(content) -> tuple[str, bool, bool, str | None]:
    """
    Extract text from message content.
    Returns: (text, has_tool_use, has_thinking, tool_summary_json)

    tool_summary_json is a JSON string like '{"Bash":3,"Read":2}' or None.
    Tool use markers are NOT materialized into text.
    """
    has_tool_use = False
    has_thinking = False
    tool_counts: dict[str, int] = {}

    if isinstance(content, str):
        # Clean up command artifacts
        text = re.sub(r'<command-name>.*?</command-name>', '', content, flags=re.DOTALL)
        text = re.sub(r'<command-message>.*?</command-message>', '', text, flags=re.DOTALL)
        text = re.sub(r'<command-args>.*?</command-args>', '', text, flags=re.DOTALL)
        text = re.sub(r'<local-command-stdout>.*?</local-command-stdout>', '', text, flags=re.DOTALL)
        return text.strip(), False, False, None

    if isinstance(content, list):
        texts = []
        for item in content:
            if isinstance(item, dict):
                item_type = item.get("type", "")
                if item_type == "text":
                    texts.append(item.get("text", ""))
                elif item_type == "tool_use":
                    has_tool_use = True
                    tool_name = item.get("name", "")
                    if tool_name:
                        tool_counts[tool_name] = tool_counts.get(tool_name, 0) + 1
                elif item_type == "thinking":
                    has_thinking = True
        tool_summary = json.dumps(tool_counts) if tool_counts else None
        return "\n".join(texts).strip(), has_tool_use, has_thinking, tool_summary

    return "", False, False, None


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


# JSONL parsing utilities (consolidated from sync_current.py / import_conversations.py)

def parse_jsonl_file(filepath: Path) -> Generator[dict, None, None]:
    """Parse JSONL file, yielding user/assistant entries for import."""
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if obj.get("isMeta"):
                    continue
                if obj.get("type") in ("user", "assistant"):
                    yield obj
            except json.JSONDecodeError:
                pass


def parse_all_with_uuids(filepath: Path) -> Generator[dict, None, None]:
    """
    Parse JSONL file yielding ALL entries with UUIDs.
    Used for building the parentUuid chain to find branches.
    """
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if obj.get("uuid"):
                    yield obj
            except json.JSONDecodeError:
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


def find_all_branches(all_entries: list[dict]) -> list[dict]:
    """
    Find all conversation branches (from rewinds).

    Returns list of branches, each with:
      - leaf_uuid: UUID of the last message in this branch
      - uuids: set of all UUIDs on this branch path
      - is_active: True if this is the current active branch
      - fork_point_uuid: UUID where this branch diverged (None for active)

    Algorithm:
    1. Find active branch (trace from latest message back to root)
    2. Find fork points on the active path where non-active children
       lead to subtrees with user messages (actual rewinds, not tree noise)
    3. For each rewind fork, collect the abandoned subtree + common prefix
    """
    uuid_to_entry: dict[str, dict] = {}
    uuid_to_parent: dict[str, str | None] = {}
    children: dict[str, list[str]] = {}

    for entry in all_entries:
        uuid = entry.get("uuid")
        if not uuid:
            continue
        uuid_to_entry[uuid] = entry
        parent = entry.get("parentUuid")
        uuid_to_parent[uuid] = parent
        if parent:
            children.setdefault(parent, []).append(uuid)

    if not uuid_to_entry:
        return []

    # Step 1: Find active branch (latest -> root)
    latest = max(uuid_to_entry.values(), key=lambda e: e.get("timestamp") or "")
    active_uuids: set[str] = set()
    current: str | None = latest["uuid"]
    while current:
        active_uuids.add(current)
        current = uuid_to_parent.get(current)

    branches: list[dict] = [
        {"leaf_uuid": latest["uuid"], "uuids": active_uuids, "is_active": True, "fork_point_uuid": None}
    ]

    # Step 2: Find rewind forks on the active path
    def has_user_descendant(uuid: str, depth: int = 0) -> bool:
        if depth > 100:
            return False
        entry = uuid_to_entry.get(uuid)
        if entry and entry.get("type") == "user":
            return True
        for kid in children.get(uuid, []):
            if has_user_descendant(kid, depth + 1):
                return True
        return False

    def collect_subtree(uuid: str) -> set[str]:
        result: set[str] = set()
        stack = [uuid]
        while stack:
            node = stack.pop()
            result.add(node)
            stack.extend(children.get(node, []))
        return result

    for uuid in active_uuids:
        kids = children.get(uuid, [])
        if len(kids) <= 1:
            continue

        for kid in kids:
            if kid in active_uuids:
                continue
            if not has_user_descendant(kid):
                continue

            # Real rewind fork — build the abandoned branch
            # Common prefix: fork point back to root
            prefix: set[str] = set()
            cur: str | None = uuid
            while cur:
                prefix.add(cur)
                cur = uuid_to_parent.get(cur)

            subtree = collect_subtree(kid)
            branch_uuids = prefix | subtree

            subtree_entries = [uuid_to_entry[u] for u in subtree if u in uuid_to_entry]
            if not subtree_entries:
                continue
            leaf = max(subtree_entries, key=lambda e: e.get("timestamp") or "")

            branches.append({
                "leaf_uuid": leaf["uuid"],
                "uuids": branch_uuids,
                "is_active": False,
                "fork_point_uuid": uuid,
            })

    return branches


def compute_branch_metadata(entries: list[dict]) -> tuple[int, list[str], list[str]]:
    """
    Compute metadata for a branch's entries in one pass.
    Returns: (exchange_count, files_modified, commits)
    """
    exchange_count = 0
    all_files = []
    all_commits = []
    has_user = False

    for entry in entries:
        entry_type = entry.get("type")
        if entry_type not in ("user", "assistant"):
            continue

        message = entry.get("message", {})
        content = message.get("content", "")

        if entry_type == "user" and is_tool_result(content):
            continue

        if entry_type == "user":
            if has_user:
                exchange_count += 1
            has_user = True

        if entry_type == "assistant":
            all_files.extend(extract_files_modified(content))
            all_commits.extend(extract_commits(content))

    if has_user:
        exchange_count += 1

    # Deduplicate files preserving order
    seen = {}
    unique_files = []
    for f in all_files:
        if f not in seen:
            seen[f] = True
            unique_files.append(f)

    return exchange_count, unique_files, all_commits
