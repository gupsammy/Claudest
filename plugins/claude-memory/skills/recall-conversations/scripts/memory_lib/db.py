#!/usr/bin/env python3
"""
Database connection, schema management, settings, and logging.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from memory_lib.formatting import codex_worktree_repo, get_project_key, normalize_cwd, normalize_project_key

# Default paths
DEFAULT_DB_PATH = Path.home() / ".claude-memory" / "conversations.db"
DEFAULT_PROJECTS_DIR = Path.home() / ".claude" / "projects"
DEFAULT_LOG_PATH = Path.home() / ".claude-memory" / "memory.log"
CONFIG_PATH = Path.home() / ".claude-memory" / "config.json"

# Codex Desktop integration paths (consolidated here per single-home rule).
DEFAULT_CODEX_SESSIONS_DIR = Path.home() / ".codex" / "sessions"
CODEX_IMPORT_SENTINEL = Path.home() / ".claude-memory" / ".last-codex-import"
# Sentinel project path for Codex sessions where session_meta.cwd is missing.
# Without this, missing-cwd sessions create a project per Codex date directory
# (e.g. ".../2026/05/03" → project named "03").
CODEX_UNKNOWN_PROJECT_PATH = "/(unknown-codex)"

# Bulk-import coordination
IMPORT_LOCK_PATH = Path.home() / ".claude-memory" / "import.lock"
BACKUP_RETENTION = 3
BACKUP_MIN_INTERVAL_HOURS = 24

# Highest PRAGMA user_version the data migrations reach. A fresh DB is stamped
# with it, since it has no old rows to migrate.
LATEST_DATA_VERSION = 6
# Data migrations run under this lock, next to the DB, so concurrent hooks never
# migrate (and back up) the same DB twice. Background callers wait this long.
MIGRATE_LOCK_NAME = "migrate.lock"
MIGRATE_LOCK_WAIT_SECONDS = 300
# A migration backup's .partial file untouched this long belongs to a dead copy.
STALE_PARTIAL_SECONDS = 600

# Default settings
DEFAULT_SETTINGS = {
    "db_path": str(DEFAULT_DB_PATH),
    "auto_inject_context": True,
    "max_context_sessions": 2,
    "exclude_projects": [],
    "logging_enabled": False,
    "sync_on_stop": True,
    "consolidation_reminder_enabled": True,
    "consolidation_min_hours": 24,
    "consolidation_min_sessions": 5,
    # Opt-in semantic search: exchanges embedded by a local Ollama server.
    "semantic_search": False,
    "embedding_model": "embeddinggemma-2:270m",
    "ollama_url": "http://localhost:11434",
    # Cosine floor for a semantic hit, calibrated for embeddinggemma-2 at 256 dims:
    # off-topic queries peak near 0.69, real matches sit at 0.72-0.87.
    "semantic_min_score": 0.70,
}

# Keys in config.json that override DEFAULT_SETTINGS
_CONFIG_KEYS = {
    "auto_inject_context",
    "consolidation_reminder_enabled",
    "consolidation_min_hours",
    "consolidation_min_sessions",
    "max_context_sessions",
    "semantic_search",
    "embedding_model",
    "ollama_url",
    "semantic_min_score",
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
  origin TEXT,
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

-- One vector per human turn (the exchange it opens), for opt-in semantic search.
-- An empty vec marks a turn with no text to embed, so it is not retried.
CREATE TABLE IF NOT EXISTS exchange_embeddings (
  message_id INTEGER PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
  model TEXT NOT NULL,
  vec BLOB NOT NULL
);

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
CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE OF content ON messages BEGIN
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
CREATE TRIGGER IF NOT EXISTS branches_au AFTER UPDATE OF aggregated_content ON branches BEGIN
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
CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE OF content ON messages BEGIN
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
CREATE TRIGGER IF NOT EXISTS branches_au AFTER UPDATE OF aggregated_content ON branches BEGIN
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

    # Old schema detected — backup then nuke and recreate
    db_path = None
    # Get the database file path from connection
    cursor.execute("PRAGMA database_list")
    for row in cursor.fetchall():
        if row[1] == "main" and row[2]:
            db_path = Path(row[2])
            break

    if db_path and db_path.exists():
        # JSONL source files expire after 30 days — data older than that
        # exists only in this DB. Back up before destroying.
        backed_up = _backup_db_before_migration(db_path, "pre-v3-nuke")
        if not backed_up:
            # Backup failed (disk full, permissions, etc.) — refuse to destroy
            # the only copy. Return False so caller uses the old schema as-is.
            return False
        conn.close()
        db_path.unlink()
        # Clean up WAL/SHM files — orphaned WAL replayed into an empty DB
        # causes "database disk image is malformed" errors.
        for suffix in ("-wal", "-shm"):
            wal_path = db_path.with_name(db_path.name + suffix)
            if wal_path.exists():
                wal_path.unlink()

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


CURRENT_ONBOARDING_VERSION = 1


def load_config() -> dict:
    """Read ~/.claude-memory/config.json. Returns empty dict on missing/error."""
    try:
        if CONFIG_PATH.exists():
            result = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            return result if isinstance(result, dict) else {}
    except Exception:
        pass
    return {}


def load_settings() -> dict:
    """Return settings with config.json overrides merged on top of defaults."""
    settings = DEFAULT_SETTINGS.copy()
    config = load_config()
    for key in _CONFIG_KEYS:
        if key in config:
            settings[key] = config[key]
    return settings


def get_db_path(settings: Optional[dict] = None) -> Path:
    """Get database path from settings or default."""
    if settings and "db_path" in settings:
        return Path(settings["db_path"]).expanduser()
    return DEFAULT_DB_PATH


def _reaggregate_notification_branches(
    cursor: sqlite3.Cursor, branch_ids: Optional[list[int]] = None
) -> None:
    """Re-aggregate branches that contain notification messages.

    Updates aggregated_content and exchange_count to exclude notifications.
    Called after backfilling is_notification on existing messages; pass
    branch_ids to limit the work to branches whose tags just changed.
    """
    if branch_ids is None:
        cursor.execute("""
            SELECT DISTINCT bm.branch_id
            FROM branch_messages bm
            JOIN messages m ON bm.message_id = m.id
            WHERE m.is_notification = 1
        """)
        branch_ids = [row[0] for row in cursor.fetchall()]
    for bid in branch_ids:
        cursor.execute("""
            SELECT m.content, m.role FROM branch_messages bm
            JOIN messages m ON bm.message_id = m.id
            WHERE bm.branch_id = ? AND COALESCE(m.is_notification, 0) = 0
            ORDER BY m.timestamp ASC
        """, (bid,))
        rows = cursor.fetchall()
        agg = "\n".join(row[0] for row in rows)
        human_user_count = sum(1 for row in rows if row[1] == "user")
        cursor.execute("UPDATE branches SET aggregated_content = ?, exchange_count = ? WHERE id = ?",
                       (agg, human_user_count, bid))


def _migrate_columns(conn: sqlite3.Connection, data: bool = True) -> None:
    """Add missing columns (DDL, idempotent), then versioned data migrations (DML) when `data`."""
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
    if "origin" not in existing:
        cursor.execute("ALTER TABLE messages ADD COLUMN origin TEXT")
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

    if data:
        _run_data_migrations(conn)


def _run_data_migrations(conn: sqlite3.Connection) -> None:
    """Run pending version-gated migrations, at most one process per DB at a time.

    The version is re-read under the lock, so a caller that waited behind another
    migrator finds the work done and backs nothing up twice.
    """
    if conn.execute("PRAGMA user_version").fetchone()[0] >= LATEST_DATA_VERSION:
        return
    # PRAGMA database_list returns (seq, name, file); file is "" for in-memory DBs.
    db_path = Path(conn.execute("PRAGMA database_list").fetchone()[2])
    if not db_path.name:
        _data_migrations(conn, db_path)
        return
    lock_path = db_path.parent / MIGRATE_LOCK_NAME
    deadline = time.monotonic() + MIGRATE_LOCK_WAIT_SECONDS
    while acquire_lock(lock_path) is None:
        if time.monotonic() >= deadline:
            return  # Another process is still migrating; the next connection retries.
        time.sleep(0.5)
    try:
        _data_migrations(conn, db_path)
    finally:
        release_lock(lock_path)


def _data_migrations(conn: sqlite3.Connection, db_path: Path) -> None:
    """DML migrations, version-gated via PRAGMA user_version, each run once."""
    cursor = conn.cursor()
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

    if version < 3:
        # v0.8.0: Selective backfill of origin column from JSONL files.
        # Phase 1: UPDATE existing messages with origin data.
        # Phase 2: Nullify file_hash for sessions with channel messages
        #          (isMeta+origin entries previously filtered) so the next
        #          normal import re-processes just those sessions.
        _backfill_origin(conn, cursor)
        conn.execute("PRAGMA user_version = 3")
        conn.commit()

    if version < 4:
        # v0.8.70: Clear stale task-notification values from origin column.
        # parse_origin had a kind-fallback bug that leaked task-notification
        # into origin (reserved for channel sources: telegram, discord, slack).
        _backup_db_before_migration(db_path, "v4")
        cursor.execute(
            "UPDATE messages SET origin = NULL WHERE origin = 'task-notification'"
        )
        conn.execute("PRAGMA user_version = 4")
        conn.commit()

    if version < 5:
        # Worktree folding: _migrate_project_paths folds worktree projects into their
        # main checkout, moving sessions between projects. Snapshot first; the
        # fold is gated on v5, so it never runs without a backup on disk.
        if not db_path.name or _backup_db_before_migration(db_path, "v5-worktree-fold"):
            conn.execute("PRAGMA user_version = 5")
            conn.commit()

    # Re-read: v5 stays unset when its backup fails, and v6 must not skip past it.
    if conn.execute("PRAGMA user_version").fetchone()[0] == 5:
        # Harness-written user turns (compaction summaries, PR monitor events,
        # inter-session messages) join task notifications under is_notification,
        # so exchange counts, topics and summaries reflect what the person typed.
        if not db_path.name or _backup_db_before_migration(db_path, "v6-harness-turns"):
            _tag_harness_messages(cursor)
            conn.execute("PRAGMA user_version = 6")
            conn.commit()


def _tag_harness_messages(cursor: sqlite3.Cursor) -> None:
    """Flag existing harness-written user messages and rebuild only their branches.

    Applies the same Python predicate the importer uses, so a migrated row and a
    freshly imported row can never be classified differently. Reset summaries are
    rebuilt by the background backfill (summary_version IS NULL).
    """
    from memory_lib.content import is_harness_message

    cursor.execute(
        "SELECT id, content FROM messages WHERE role = 'user' AND COALESCE(is_notification, 0) = 0"
    )
    ids = [mid for mid, content in cursor.fetchall() if is_harness_message(content or "")]
    branch_ids: set[int] = set()
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        placeholders = ",".join("?" * len(chunk))
        cursor.execute(f"UPDATE messages SET is_notification = 1 WHERE id IN ({placeholders})", chunk)
        cursor.execute(
            f"SELECT DISTINCT branch_id FROM branch_messages WHERE message_id IN ({placeholders})", chunk
        )
        branch_ids.update(row[0] for row in cursor.fetchall())
    if not branch_ids:
        return
    ordered = sorted(branch_ids)
    _reaggregate_notification_branches(cursor, ordered)
    for i in range(0, len(ordered), 500):
        chunk = ordered[i:i + 500]
        placeholders = ",".join("?" * len(chunk))
        cursor.execute(f"UPDATE branches SET summary_version = NULL WHERE id IN ({placeholders})", chunk)


# Lockfile path -> open fd for each lock this process holds.
_HELD_LOCKS: dict[str, int] = {}


def _try_os_lock(fd: int) -> bool:
    """Non-blocking exclusive lock on fd; the OS drops it when the process dies."""
    try:
        if os.name == "nt":
            import msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def acquire_lock(lock_path: Path) -> int | None:
    """Acquire an exclusive lock on lock_path; returns our PID, or None if it is held.

    The OS holds the lock, so a crashed holder releases it on exit and there is no
    window where two processes both believe they own it. The PID inside the file
    is for humans debugging a stuck lock, not part of the protocol.
    """
    key = str(lock_path)
    if key in _HELD_LOCKS:
        return None
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(key, os.O_CREAT | os.O_RDWR, 0o600)
    if not _try_os_lock(fd):
        os.close(fd)
        return None
    _HELD_LOCKS[key] = fd
    pid = os.getpid()
    if os.name != "nt":
        # Windows locks the first byte against writes, so the PID note is POSIX-only.
        try:
            os.ftruncate(fd, 0)
            os.write(fd, str(pid).encode("ascii"))
        except OSError:
            pass  # the note is optional; the lock is already ours
    return pid


def release_lock(lock_path: Path) -> None:
    """Release the lock if this process holds it. The file stays: unlinking a
    locked file would let a later process lock a new inode alongside the old one."""
    fd = _HELD_LOCKS.pop(str(lock_path), None)
    if fd is None:
        return
    try:
        if os.name == "nt":
            import msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    except OSError:
        pass
    finally:
        os.close(fd)


def _backup_db_before_migration(db_path: Path, label: str) -> bool:
    """Create a timestamped WAL-safe backup using sqlite3.Connection.backup().

    Returns True if backup succeeded and was verified, False otherwise.
    sqlite3.Connection.backup() does a page-level copy that respects WAL journaling.
    The copy is written under a .partial name and renamed only once complete, so a
    process killed mid-copy (e.g. a hook timeout) never leaves a file that looks
    like a valid backup.
    """
    if not db_path.name or not db_path.exists():
        return False
    ts = time.strftime("%Y%m%d-%H%M%S")
    backup_path = db_path.with_suffix(f".pre-{label}-{ts}.db")
    partial_path = backup_path.with_name(backup_path.name + ".partial")
    # A copy killed mid-write (hook timeout, SIGKILL) skips the cleanup below and
    # leaves a DB-sized .partial. A live copy writes continuously, so one untouched
    # for STALE_PARTIAL_SECONDS is dead.
    for stale in db_path.parent.glob(f"{db_path.stem}.pre-*.db.partial*"):
        try:
            if time.time() - stale.stat().st_mtime > STALE_PARTIAL_SECONDS:
                stale.unlink()
        except OSError:
            pass
    ok = False
    try:
        src = sqlite3.connect(str(db_path))
        try:
            dst = sqlite3.connect(str(partial_path))
            try:
                src.backup(dst)
                # The copy inherits WAL mode from the source; switch it to a rollback
                # journal so the backup is one self-contained file with no -wal/-shm.
                dst.execute("PRAGMA journal_mode = DELETE")
                ok = dst.execute("PRAGMA quick_check").fetchone()[0] == "ok"
            finally:
                dst.close()
        finally:
            src.close()
        if ok:
            os.replace(partial_path, backup_path)
        return ok
    except Exception:
        return False
    finally:
        # Only files of our own copy; the source DB is never touched. The sidecars
        # hold nothing once the copy is in rollback-journal mode and closed.
        leftovers = [Path(f"{partial_path}-wal"), Path(f"{partial_path}-shm")]
        if not ok:
            leftovers.append(partial_path)
        for p in leftovers:
            try:
                p.unlink()
            except OSError:
                pass


def _backfill_origin(conn: sqlite3.Connection, cursor: sqlite3.Cursor) -> None:
    """Selectively backfill origin column from JSONL files without full reimport.

    For each file in import_log, scan for entries with origin fields
    and UPDATE existing message rows by session_id + uuid.

    Note: Previously had a Phase 2 that nullified file_hash to force reimport
    of sessions with channel messages. Removed because the reimport path is
    destructive (delete-all-then-insert) and JSONL files expire after 30 days —
    triggering a reimport risks irrecoverable data loss.
    """
    import json
    from memory_lib.content import parse_origin

    # Guard: sessions table may not exist in minimal test DBs
    tables = {r[0] for r in cursor.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "sessions" not in tables or "import_log" not in tables:
        return

    # Build file_path -> session mapping from import_log + sessions
    # The file stem (minus .jsonl and optional agent- prefix) is the session uuid
    file_session_map = {}
    all_import_rows = cursor.execute("SELECT file_path FROM import_log").fetchall()
    for (file_path,) in all_import_rows:
        p = Path(file_path)
        stem = p.stem
        if stem.startswith("agent-"):
            stem = stem[6:]
        row = cursor.execute(
            "SELECT id FROM sessions WHERE uuid = ?", (stem,)
        ).fetchone()
        if row:
            file_session_map[file_path] = row[0]

    for file_path, session_id in file_session_map.items():
        p = Path(file_path)
        if not p.exists():
            continue

        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    origin = obj.get("origin")
                    if not origin:
                        continue

                    # UPDATE origin for existing messages
                    uuid = obj.get("uuid")
                    if uuid and obj.get("type") in ("user", "assistant"):
                        origin_value = parse_origin(obj)
                        if origin_value:
                            cursor.execute(
                                "UPDATE messages SET origin = ? WHERE session_id = ? AND uuid = ?",
                                (origin_value, session_id, uuid)
                            )
        except OSError:
            continue

    conn.commit()


def resolve_project_cwd(conn: sqlite3.Connection, cwd: str) -> str:
    """Map a session cwd to its project root, folding worktrees into the main checkout.

    Claude worktrees (<repo>/.claude/worktrees/<name>) resolve by path. Codex
    worktrees (~/.codex/worktrees/<id>/<repo>) only carry the repo's directory
    name, so they resolve to the one non-worktree project with that name; with
    zero or several candidates the worktree path is kept as its own project.
    """
    path = normalize_cwd(cwd)
    repo = codex_worktree_repo(path)
    if repo is None:
        return path
    rows = conn.execute("SELECT path FROM projects WHERE name = ?", (repo,)).fetchall()
    mains = [p for (p,) in rows if p and codex_worktree_repo(p) is None and normalize_cwd(p) == p]
    return mains[0] if len(mains) == 1 else path


def _rekeyed_worktree_key(conn: sqlite3.Connection, proj_id: int, key: str, real_cwd: str) -> str:
    """Return the main-checkout key for a worktree project row, or its current key."""
    if normalize_project_key(key) != key:
        new_key = normalize_project_key(key)  # keeps Claude Code's own directory encoding
    elif "--codex-worktrees-" in key:  # get_project_key encoding of /.codex/worktrees/
        new_key = get_project_key(real_cwd)
    else:
        return key
    taken = conn.execute("SELECT 1 FROM projects WHERE key = ? AND id != ?", (new_key, proj_id)).fetchone()
    return key if taken else new_key


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

    Session cwds are resolved through resolve_project_cwd first, so worktree
    projects (Claude or Codex) merge into their main checkout's project.

    This is idempotent: after fixing, the project path matches session cwd, so subsequent
    runs find no mismatch and do nothing.
    """
    cursor = conn.cursor()

    # Find all projects that have at least one session with a non-null cwd
    cursor.execute("""
        SELECT p.id, s.cwd, COUNT(*) AS cwd_count
        FROM projects p
        JOIN sessions s ON s.project_id = p.id
        WHERE s.cwd IS NOT NULL AND s.cwd != ''
        GROUP BY p.id, s.cwd
        ORDER BY p.id, cwd_count DESC
    """)
    rows = cursor.fetchall()
    if not rows:
        return

    # For each project, pick the most common resolved root as the authoritative
    # path. Resolve everything before mutating: Codex resolution reads projects.
    tallies: dict[int, dict[str, int]] = {}
    for proj_id, cwd, count in rows:
        per_root = tallies.setdefault(proj_id, {})
        root = resolve_project_cwd(conn, cwd)
        per_root[root] = per_root.get(root, 0) + count
    # sorted() makes ties pick the same root every run, whatever the row order.
    best_cwd = {proj_id: max(sorted(per_root), key=per_root.get) for proj_id, per_root in tallies.items()}

    # Now check which projects need updating
    cursor.execute("SELECT id, path, key FROM projects")
    projects = cursor.fetchall()

    for proj_id, stored_path, stored_key in projects:
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
            # Simple fix: update path and name; a worktree row also takes the
            # main checkout's key so later imports of that checkout find it.
            cursor.execute(
                "UPDATE projects SET path = ?, name = ?, key = ? WHERE id = ?",
                (real_cwd, real_name, _rekeyed_worktree_key(conn, proj_id, stored_key, real_cwd), proj_id)
            )

    conn.commit()


def _apply_fts_schema(conn: sqlite3.Connection, schema: str) -> None:
    """Create the FTS tables and triggers, rescoping old update triggers.

    Old update triggers fired on any column, so every metadata or summary write
    re-indexed the row's full text. They are dropped and recreated scoped to the
    indexed column in one write transaction: a writer committing between the drop
    and the create would change content with no trigger and desync the index.
    """
    unscoped = []
    for name in ("messages_au", "branches_au"):
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = ?", (name,)
        ).fetchone()
        if row and " UPDATE OF " not in row[0].upper():
            unscoped.append(name)
    if not unscoped:
        conn.executescript(schema)
        return
    drops = "".join(f"DROP TRIGGER IF EXISTS {name};\n" for name in unscoped)
    try:
        conn.executescript(f"BEGIN IMMEDIATE;\n{drops}{schema}\nCOMMIT;")
    except sqlite3.Error:
        conn.rollback()
        raise


def get_db_connection(settings: Optional[dict] = None, migrate: bool = True) -> sqlite3.Connection:
    """
    Get database connection, initializing schema and running migrations if needed.
    Uses settings-based path if provided.
    Sets WAL mode and busy_timeout for concurrent access safety.

    migrate=False skips the version-gated data migrations (backups, row rewrites),
    so foreground hooks never block a session start on them; background workers
    keep the default and run them under the migration lock.
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
        fresh = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sessions'"
        ).fetchone() is None
        fts = detect_fts_support(conn)
        conn.executescript(SCHEMA_CORE)
        if fresh:
            # Nothing to migrate in an empty DB: skip every data migration and its backup.
            conn.execute(f"PRAGMA user_version = {LATEST_DATA_VERSION}")
        if fts == "fts5":
            _apply_fts_schema(conn, SCHEMA_FTS5)
        elif fts == "fts4":
            _apply_fts_schema(conn, SCHEMA_FTS4)
        conn.commit()

    # Add any missing columns (e.g. tool_summary)
    _migrate_columns(conn, data=migrate)

    # Fix project paths and fold worktree projects into their main checkout.
    # Waits for v5, whose migration takes the pre-fold backup.
    try:
        if conn.execute("PRAGMA user_version").fetchone()[0] >= 5:
            _migrate_project_paths(conn)
    except Exception:
        # Never block DB connection on data migration errors. Roll back so the
        # caller's next commit can't persist a half-applied fold; it retries on
        # the next connection.
        conn.rollback()

    return conn



def setup_logging(settings: Optional[dict] = None) -> logging.Logger:
    """
    Set up logging with rotation.
    Returns a null logger if logging is disabled.
    """
    logger = logging.getLogger("claude-memory")
    logger.handlers.clear()

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
