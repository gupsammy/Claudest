#!/usr/bin/env python3
"""
Database connection, schema management, settings, and logging.
"""

from __future__ import annotations

import logging
import sqlite3
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

# Default paths
DEFAULT_DB_PATH = Path.home() / ".claude-memory" / "conversations.db"
DEFAULT_PROJECTS_DIR = Path.home() / ".claude" / "projects"
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
# Split into core (tables/indexes) and FTS variants for compatibility
SCHEMA_CORE = """
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
  tool_counts TEXT,
  aggregated_content TEXT,
  context_summary TEXT,
  context_summary_json TEXT,
  summary_version INTEGER DEFAULT 0,
  UNIQUE(session_id, leaf_uuid)
);
CREATE INDEX IF NOT EXISTS idx_branches_session ON branches(session_id);
CREATE INDEX IF NOT EXISTS idx_branches_active ON branches(is_active);
CREATE INDEX IF NOT EXISTS idx_branches_summary_version ON branches(summary_version);

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
  is_notification INTEGER DEFAULT 0,
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

-- Import tracking
CREATE TABLE IF NOT EXISTS import_log (
  id INTEGER PRIMARY KEY,
  file_path TEXT UNIQUE NOT NULL,
  file_hash TEXT,
  imported_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  messages_imported INTEGER DEFAULT 0
);

"""

# FTS5 schema (best: porter stemming + unicode61, BM25 ranking)
SCHEMA_FTS5 = """
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

CREATE VIRTUAL TABLE IF NOT EXISTS branches_fts USING fts5(
  aggregated_content,
  content=branches,
  content_rowid=id,
  tokenize='porter unicode61'
);

CREATE TRIGGER IF NOT EXISTS branches_ai AFTER INSERT ON branches BEGIN
  INSERT INTO branches_fts(rowid, aggregated_content) VALUES (new.id, new.aggregated_content);
END;
CREATE TRIGGER IF NOT EXISTS branches_ad AFTER DELETE ON branches BEGIN
  INSERT INTO branches_fts(branches_fts, rowid, aggregated_content) VALUES('delete', old.id, old.aggregated_content);
END;
CREATE TRIGGER IF NOT EXISTS branches_au AFTER UPDATE ON branches BEGIN
  INSERT INTO branches_fts(branches_fts, rowid, aggregated_content) VALUES('delete', old.id, old.aggregated_content);
  INSERT INTO branches_fts(rowid, aggregated_content) VALUES (new.id, new.aggregated_content);
END;
"""

# FTS4 schema (fallback: porter stemming, no BM25 but supports MATCH + snippet)
SCHEMA_FTS4 = """
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts4(
  content,
  content=messages,
  tokenize=porter
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

CREATE VIRTUAL TABLE IF NOT EXISTS branches_fts USING fts4(
  aggregated_content,
  content=branches,
  tokenize=porter
);

CREATE TRIGGER IF NOT EXISTS branches_ai AFTER INSERT ON branches BEGIN
  INSERT INTO branches_fts(rowid, aggregated_content) VALUES (new.id, new.aggregated_content);
END;
CREATE TRIGGER IF NOT EXISTS branches_ad AFTER DELETE ON branches BEGIN
  INSERT INTO branches_fts(branches_fts, rowid, aggregated_content) VALUES('delete', old.id, old.aggregated_content);
END;
CREATE TRIGGER IF NOT EXISTS branches_au AFTER UPDATE ON branches BEGIN
  INSERT INTO branches_fts(branches_fts, rowid, aggregated_content) VALUES('delete', old.id, old.aggregated_content);
  INSERT INTO branches_fts(rowid, aggregated_content) VALUES (new.id, new.aggregated_content);
END;
"""

# Combined schema (core + FTS5) for test fixtures and simple single-shot setup
SCHEMA = SCHEMA_CORE + SCHEMA_FTS5


def detect_fts_support(conn: sqlite3.Connection) -> str | None:
    """Detect the best available FTS extension."""
    try:
        opts = {row[0] for row in conn.execute("PRAGMA compile_options").fetchall()}
    except Exception:
        return None
    if "ENABLE_FTS5" in opts:
        return "fts5"
    if "ENABLE_FTS4" in opts or "ENABLE_FTS3" in opts:
        return "fts4"
    return None


def migrate_db(conn: sqlite3.Connection) -> bool:
    """
    Migrate database to v3 schema (messages-once + branch index).
    Detects old schema by checking if 'branches' table exists.
    If not, deletes the DB file so a fresh import is triggered.
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
    new_conn.execute("PRAGMA journal_mode = WAL")
    new_conn.execute("PRAGMA busy_timeout = 5000")
    fts = detect_fts_support(new_conn)
    new_conn.executescript(SCHEMA_CORE)
    if fts == "fts5":
        new_conn.executescript(SCHEMA_FTS5)
    elif fts == "fts4":
        new_conn.executescript(SCHEMA_FTS4)
    new_conn.commit()

    # We can't return the new connection through the old reference,
    # so we signal that migration happened and caller should reconnect
    new_conn.close()
    return True


def load_settings() -> dict:
    """
    Return default settings.
    Previously loaded from YAML frontmatter, but PyYAML is not stdlib
    so settings were silently ignored for most users.
    """
    return DEFAULT_SETTINGS.copy()


def get_db_path(settings: Optional[dict] = None) -> Path:
    """Get database path from settings or default."""
    if settings and "db_path" in settings:
        return Path(settings["db_path"]).expanduser()
    return DEFAULT_DB_PATH


def _reaggregate_notification_branches(cursor: sqlite3.Cursor) -> None:
    """Re-aggregate branches that contain notification messages.

    Updates aggregated_content and exchange_count to exclude notifications.
    Called after backfilling is_notification on existing messages.
    """
    cursor.execute("""
        SELECT DISTINCT bm.branch_id
        FROM branch_messages bm
        JOIN messages m ON bm.message_id = m.id
        WHERE m.is_notification = 1
    """)
    affected_branches = [row[0] for row in cursor.fetchall()]
    for bid in affected_branches:
        cursor.execute("""
            SELECT m.content FROM branch_messages bm
            JOIN messages m ON bm.message_id = m.id
            WHERE bm.branch_id = ? AND COALESCE(m.is_notification, 0) = 0
            ORDER BY m.timestamp ASC
        """, (bid,))
        agg = "\n".join(row[0] for row in cursor.fetchall())
        cursor.execute("UPDATE branches SET aggregated_content = ? WHERE id = ?", (agg, bid))
        cursor.execute("""
            SELECT COUNT(*) FROM branch_messages bm
            JOIN messages m ON bm.message_id = m.id
            WHERE bm.branch_id = ? AND m.role = 'user' AND COALESCE(m.is_notification, 0) = 0
        """, (bid,))
        human_user_count = cursor.fetchone()[0]
        cursor.execute("UPDATE branches SET exchange_count = ? WHERE id = ?",
                       (human_user_count, bid))


def _migrate_columns(conn: sqlite3.Connection) -> None:
    """Add missing columns (DDL, idempotent) and run versioned data migrations (DML)."""
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(messages)")
    existing = {row[1] for row in cursor.fetchall()}

    # --- DDL migrations (column-existence gated, idempotent) ---
    if "tool_summary" not in existing:
        cursor.execute("ALTER TABLE messages ADD COLUMN tool_summary TEXT")
        conn.commit()
    if "is_notification" not in existing:
        cursor.execute("ALTER TABLE messages ADD COLUMN is_notification INTEGER DEFAULT 0")
        conn.commit()

    # branches DDL migration
    cursor.execute("PRAGMA table_info(branches)")
    branch_cols = {row[1] for row in cursor.fetchall()}
    if "tool_counts" not in branch_cols:
        cursor.execute("ALTER TABLE branches ADD COLUMN tool_counts TEXT")
        conn.commit()
    if "context_summary" not in branch_cols:
        cursor.execute("ALTER TABLE branches ADD COLUMN context_summary TEXT")
    if "context_summary_json" not in branch_cols:
        cursor.execute("ALTER TABLE branches ADD COLUMN context_summary_json TEXT")
    if "summary_version" not in branch_cols:
        cursor.execute("ALTER TABLE branches ADD COLUMN summary_version INTEGER DEFAULT 0")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_branches_summary_version ON branches(summary_version)")
    conn.commit()

    # token_snapshots table (new table, not a column add)
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='token_snapshots'")
    if not cursor.fetchone():
        cursor.executescript("""
CREATE TABLE IF NOT EXISTS token_snapshots (
  id INTEGER PRIMARY KEY,
  session_uuid TEXT UNIQUE NOT NULL,
  project_path TEXT,
  start_time DATETIME,
  duration_minutes INTEGER,
  user_message_count INTEGER,
  assistant_message_count INTEGER,
  input_tokens INTEGER DEFAULT 0,
  output_tokens INTEGER DEFAULT 0,
  cache_read_tokens INTEGER DEFAULT 0,
  cache_creation_tokens INTEGER DEFAULT 0,
  tool_counts TEXT,
  tool_errors INTEGER DEFAULT 0,
  uses_task_agent INTEGER DEFAULT 0,
  uses_web_search INTEGER DEFAULT 0,
  uses_web_fetch INTEGER DEFAULT 0,
  user_response_times TEXT,
  lines_added INTEGER DEFAULT 0,
  lines_removed INTEGER DEFAULT 0,
  goal_categories TEXT,
  outcome TEXT,
  session_type TEXT,
  friction_counts TEXT,
  brief_summary TEXT,
  imported_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_token_snapshots_session ON token_snapshots(session_uuid);
CREATE INDEX IF NOT EXISTS idx_token_snapshots_start ON token_snapshots(start_time);
""")
        conn.commit()

    # Ensure data_source column exists (added by get-token-insights ingest script)
    try:
        conn.execute("ALTER TABLE token_snapshots ADD COLUMN data_source TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass

    # --- DML migrations (version-gated via PRAGMA user_version, run once) ---
    version = conn.execute("PRAGMA user_version").fetchone()[0]

    if version < 1:
        # v0.5.0: Backfill task-notification messages
        cursor.execute("""
            UPDATE messages SET is_notification = 1
            WHERE role = 'user' AND content LIKE '<task-notification>%' AND is_notification = 0
        """)
        _reaggregate_notification_branches(cursor)
        conn.execute("PRAGMA user_version = 1")
        conn.commit()

    if version < 2:
        # v0.7.1: Backfill teammate messages as notifications
        cursor.execute("""
            UPDATE messages SET is_notification = 1
            WHERE role = 'user' AND content LIKE '<teammate-message%' AND is_notification = 0
        """)
        _reaggregate_notification_branches(cursor)
        conn.execute("PRAGMA user_version = 2")
        conn.commit()


def _migrate_project_paths(conn: sqlite3.Connection) -> None:
    """Fix project paths that were incorrectly derived from hyphenated directory keys.

    When projects were first imported, path/name were derived from the Claude project
    directory key (e.g. '-Users-foo-repos-meta-ads-cli') using a lossy replace('-', '/')
    heuristic. For directories with hyphens in their name (e.g. 'meta-ads-cli'), this
    produces wrong paths ('/Users/foo/repos/meta/ads/cli' instead of the real path).

    The sessions.cwd column stores the REAL filesystem path recorded at runtime. This
    migration uses the most-common cwd across each project's sessions to correct the
    project path and name. It also merges duplicate projects that resolve to the same
    real path.

    This is idempotent: after fixing, the project path matches session cwd, so subsequent
    runs find no mismatch and do nothing.
    """
    cursor = conn.cursor()

    # Find all projects that have at least one session with a non-null cwd
    cursor.execute("""
        SELECT p.id, p.path, p.name,
               s.cwd,
               COUNT(*) AS cwd_count
        FROM projects p
        JOIN sessions s ON s.project_id = p.id
        WHERE s.cwd IS NOT NULL AND s.cwd != ''
        GROUP BY p.id, s.cwd
        ORDER BY p.id, cwd_count DESC
    """)
    rows = cursor.fetchall()
    if not rows:
        return

    # For each project, pick the most common cwd as the authoritative real path
    best_cwd: dict[int, str] = {}
    for proj_id, _path, _name, cwd, _count in rows:
        if proj_id not in best_cwd:
            best_cwd[proj_id] = cwd  # rows ordered by cwd_count DESC, first wins

    # Now check which projects need updating
    cursor.execute("SELECT id, path, name FROM projects")
    projects = cursor.fetchall()

    for proj_id, stored_path, stored_name in projects:
        real_cwd = best_cwd.get(proj_id)
        if not real_cwd or real_cwd == stored_path:
            continue  # No cwd data or already correct

        real_name = Path(real_cwd).name

        # Check if another project already has real_cwd as its path (merge conflict)
        cursor.execute("SELECT id FROM projects WHERE path = ? AND id != ?", (real_cwd, proj_id))
        existing = cursor.fetchone()

        if existing:
            # Merge: reassign all sessions from this (wrong) project to the existing one
            keeper_id = existing[0]
            cursor.execute(
                "UPDATE sessions SET project_id = ? WHERE project_id = ?",
                (keeper_id, proj_id)
            )
            cursor.execute("DELETE FROM projects WHERE id = ?", (proj_id,))
        else:
            # Simple fix: update path and name
            cursor.execute(
                "UPDATE projects SET path = ?, name = ? WHERE id = ?",
                (real_cwd, real_name, proj_id)
            )

    conn.commit()


def get_db_connection(settings: Optional[dict] = None) -> sqlite3.Connection:
    """
    Get database connection, initializing schema and running migrations if needed.
    Uses settings-based path if provided.
    Sets WAL mode and busy_timeout for concurrent access safety.
    """
    db_path = get_db_path(settings)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)

    # WAL mode: readers never block writers, writers never block readers
    conn.execute("PRAGMA journal_mode = WAL")
    # busy_timeout: wait up to 5s on writer-writer collisions instead of failing
    conn.execute("PRAGMA busy_timeout = 5000")
    # Enforce foreign key constraints to prevent orphaned data
    conn.execute("PRAGMA foreign_keys = ON")

    # Check if migration needed (old schema -> v3)
    migrated = migrate_db(conn)
    if migrated:
        # Connection was closed during migration, reconnect
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA foreign_keys = ON")

    if not migrated:
        # Apply schema (handles fresh databases, idempotent)
        fts = detect_fts_support(conn)
        conn.executescript(SCHEMA_CORE)
        if fts == "fts5":
            conn.executescript(SCHEMA_FTS5)
        elif fts == "fts4":
            conn.executescript(SCHEMA_FTS4)
        conn.commit()

    # Add any missing columns (e.g. tool_summary)
    _migrate_columns(conn)

    # Fix project paths that were incorrectly derived from hyphenated directory keys
    try:
        _migrate_project_paths(conn)
    except Exception:
        pass  # Never block DB connection on data migration errors

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
