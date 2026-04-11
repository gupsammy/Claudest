"""Tests for memory_lib.db — schema creation, migration, settings."""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path


from memory_lib.db import (
    CURRENT_ONBOARDING_VERSION,
    DEFAULT_SETTINGS,
    SCHEMA,
    _migrate_columns,
    _migrate_project_paths,
    load_config,
    load_settings,
    migrate_db,
)


class TestSchemaCreation:
    def test_all_tables_exist(self, memory_db):
        cursor = memory_db.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = {row[0] for row in cursor.fetchall()}
        expected = {"projects", "sessions", "branches", "messages", "branch_messages", "import_log"}
        assert expected.issubset(tables)

    def test_fts_tables_exist(self, memory_db):
        cursor = memory_db.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%_fts%'")
        fts_tables = {row[0] for row in cursor.fetchall()}
        assert "messages_fts" in fts_tables
        assert "branches_fts" in fts_tables

    def test_schema_idempotent(self, memory_db):
        """Applying schema twice should not raise."""
        memory_db.executescript(SCHEMA)
        memory_db.commit()

    def test_insert_and_query(self, memory_db):
        """Basic insert/query roundtrip."""
        cursor = memory_db.cursor()
        cursor.execute("INSERT INTO projects (path, key, name) VALUES (?, ?, ?)",
                       ("/home/user/project", "-home-user-project", "project"))
        cursor.execute("INSERT INTO sessions (uuid, project_id) VALUES (?, ?)",
                       ("sess-1", cursor.lastrowid))
        memory_db.commit()
        cursor.execute("SELECT uuid FROM sessions")
        assert cursor.fetchone()[0] == "sess-1"


def _pre_migration_db(include_tool_summary=False):
    """Create in-memory DB with pre-migration schema (no is_notification column)."""
    conn = sqlite3.connect(":memory:")
    extra = ", tool_summary TEXT" if include_tool_summary else ""
    conn.execute(f"""
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY, session_id INTEGER, uuid TEXT,
            parent_uuid TEXT, timestamp DATETIME, role TEXT,
            content TEXT NOT NULL, has_tool_use INTEGER DEFAULT 0,
            has_thinking INTEGER DEFAULT 0{extra},
            UNIQUE(session_id, uuid)
        )
    """)
    conn.execute("""
        CREATE TABLE branches (
            id INTEGER PRIMARY KEY, session_id INTEGER, leaf_uuid TEXT,
            fork_point_uuid TEXT, is_active INTEGER DEFAULT 1,
            started_at DATETIME, ended_at DATETIME, exchange_count INTEGER DEFAULT 0,
            files_modified TEXT, commits TEXT, aggregated_content TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE branch_messages (
            branch_id INTEGER, message_id INTEGER, PRIMARY KEY (branch_id, message_id)
        )
    """)
    conn.execute("""
        CREATE TABLE import_log (
            id INTEGER PRIMARY KEY, file_path TEXT UNIQUE NOT NULL,
            file_hash TEXT, imported_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            messages_imported INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    return conn


class TestMigrateColumns:
    def test_adds_tool_summary_column(self):
        """_migrate_columns should add tool_summary and is_notification if missing."""
        conn = _pre_migration_db()

        _migrate_columns(conn)

        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(messages)")
        columns = {row[1] for row in cursor.fetchall()}
        assert "tool_summary" in columns
        assert "is_notification" in columns
        conn.close()

    def test_adds_context_summary_columns(self):
        """_migrate_columns should add context_summary, context_summary_json, summary_version to branches."""
        conn = _pre_migration_db()

        _migrate_columns(conn)

        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(branches)")
        columns = {row[1] for row in cursor.fetchall()}
        assert "context_summary" in columns
        assert "context_summary_json" in columns
        assert "summary_version" in columns
        conn.close()

    def test_idempotent_when_column_exists(self, memory_db):
        """_migrate_columns should not fail when column already exists."""
        _migrate_columns(memory_db)  # Already called in fixture, call again

    def test_migrate_backfills_notifications(self):
        """Migration should flag existing task-notification messages."""
        conn = _pre_migration_db(include_tool_summary=True)
        conn.execute("INSERT INTO messages (id, session_id, role, content) VALUES (1, 1, 'user', 'Hello world')")
        conn.execute("INSERT INTO messages (id, session_id, role, content) VALUES (2, 1, 'assistant', 'Hi there')")
        conn.execute("INSERT INTO messages (id, session_id, role, content) VALUES (3, 1, 'user', '<task-notification><task-id>abc</task-id></task-notification>')")
        conn.execute("INSERT INTO messages (id, session_id, role, content) VALUES (4, 1, 'user', 'Normal follow-up')")
        conn.commit()

        _migrate_columns(conn)

        cursor = conn.cursor()
        cursor.execute("SELECT id, is_notification FROM messages ORDER BY id")
        rows = cursor.fetchall()
        assert rows[0] == (1, 0)  # Normal user
        assert rows[1] == (2, 0)  # Assistant
        assert rows[2] == (3, 1)  # Task notification
        assert rows[3] == (4, 0)  # Normal user
        conn.close()

    def test_migrate_reaggregates_branches(self):
        """Migration should re-aggregate branch content excluding notifications."""
        conn = _pre_migration_db(include_tool_summary=True)
        conn.execute("INSERT INTO messages (id, session_id, timestamp, role, content) VALUES (1, 1, '2025-01-01T10:00:00Z', 'user', 'Hello')")
        conn.execute("INSERT INTO messages (id, session_id, timestamp, role, content) VALUES (2, 1, '2025-01-01T10:01:00Z', 'assistant', 'Hi there')")
        conn.execute("INSERT INTO messages (id, session_id, timestamp, role, content) VALUES (3, 1, '2025-01-01T10:02:00Z', 'user', '<task-notification>big agent result</task-notification>')")
        conn.execute("INSERT INTO branches (id, session_id, leaf_uuid, aggregated_content, exchange_count) VALUES (1, 1, 'leaf-1', 'Hello\nHi there\n<task-notification>big agent result</task-notification>', 2)")
        conn.execute("INSERT INTO branch_messages VALUES (1, 1)")
        conn.execute("INSERT INTO branch_messages VALUES (1, 2)")
        conn.execute("INSERT INTO branch_messages VALUES (1, 3)")
        conn.commit()

        _migrate_columns(conn)

        cursor = conn.cursor()
        cursor.execute("SELECT aggregated_content, exchange_count FROM branches WHERE id = 1")
        agg, exc = cursor.fetchone()
        assert "<task-notification>" not in agg
        assert "Hello" in agg
        assert "Hi there" in agg
        # exchange_count should be corrected: only 1 real user message
        assert exc == 1
        conn.close()


def _versioned_db(user_version=0, include_is_notification=True):
    """Create in-memory DB with specific user_version for testing versioned migrations."""
    conn = sqlite3.connect(":memory:")
    notif_col = ", is_notification INTEGER DEFAULT 0" if include_is_notification else ""
    conn.execute(f"""
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY, session_id INTEGER, uuid TEXT,
            parent_uuid TEXT, timestamp DATETIME, role TEXT,
            content TEXT NOT NULL, tool_summary TEXT,
            has_tool_use INTEGER DEFAULT 0, has_thinking INTEGER DEFAULT 0{notif_col},
            UNIQUE(session_id, uuid)
        )
    """)
    conn.execute("""
        CREATE TABLE branches (
            id INTEGER PRIMARY KEY, session_id INTEGER, leaf_uuid TEXT,
            fork_point_uuid TEXT, is_active INTEGER DEFAULT 1,
            started_at DATETIME, ended_at DATETIME, exchange_count INTEGER DEFAULT 0,
            files_modified TEXT, commits TEXT, aggregated_content TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE branch_messages (
            branch_id INTEGER, message_id INTEGER, PRIMARY KEY (branch_id, message_id)
        )
    """)
    conn.execute("""
        CREATE TABLE import_log (
            id INTEGER PRIMARY KEY, file_path TEXT UNIQUE NOT NULL,
            file_hash TEXT, imported_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            messages_imported INTEGER DEFAULT 0
        )
    """)
    conn.execute(f"PRAGMA user_version = {user_version}")
    conn.commit()
    return conn


class TestVersionedMigration:
    def test_fresh_db_gets_latest_version(self):
        """A fresh DB (no columns, version 0) should end up at the latest user_version."""
        conn = _pre_migration_db()
        _migrate_columns(conn)
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == 4
        conn.close()

    def test_v0_to_v2_backfills_both(self):
        """From version 0, both task-notification and teammate messages get backfilled."""
        conn = _versioned_db(user_version=0)
        conn.execute("INSERT INTO messages (id, session_id, role, content) VALUES (1, 1, 'user', 'Hello')")
        conn.execute("INSERT INTO messages (id, session_id, role, content) VALUES (2, 1, 'user', '<task-notification>task</task-notification>')")
        conn.execute("INSERT INTO messages (id, session_id, role, content) VALUES (3, 1, 'user', '<teammate-message teammate_id=\"x\">report</teammate-message>')")
        conn.commit()

        _migrate_columns(conn)

        cursor = conn.cursor()
        cursor.execute("SELECT id, is_notification FROM messages ORDER BY id")
        rows = cursor.fetchall()
        assert rows[0] == (1, 0)
        assert rows[1] == (2, 1)
        assert rows[2] == (3, 1)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
        conn.close()

    def test_v1_to_v2_backfills_only_teammate(self):
        """From version 1, only teammate messages get backfilled (task-notifications already done)."""
        conn = _versioned_db(user_version=1)
        # Simulate a DB where task-notifications were already flagged by version 1
        conn.execute("INSERT INTO messages (id, session_id, role, content, is_notification) VALUES (1, 1, 'user', '<task-notification>task</task-notification>', 1)")
        conn.execute("INSERT INTO messages (id, session_id, role, content, is_notification) VALUES (2, 1, 'user', '<teammate-message teammate_id=\"x\">report</teammate-message>', 0)")
        conn.execute("INSERT INTO messages (id, session_id, role, content, is_notification) VALUES (3, 1, 'user', 'Normal message', 0)")
        conn.commit()

        _migrate_columns(conn)

        cursor = conn.cursor()
        cursor.execute("SELECT id, is_notification FROM messages ORDER BY id")
        rows = cursor.fetchall()
        assert rows[0] == (1, 1)  # Already flagged, untouched
        assert rows[1] == (2, 1)  # Newly flagged by version 2
        assert rows[2] == (3, 0)  # Normal, untouched
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
        conn.close()

    def test_v2_skips_data_backfills(self):
        """From version 2, data backfills (v1, v2) do not re-run; v3 origin backfill runs."""
        conn = _versioned_db(user_version=2)
        conn.execute("INSERT INTO messages (id, session_id, role, content, is_notification) VALUES (1, 1, 'user', '<teammate-message>should stay 0</teammate-message>', 0)")
        conn.commit()

        _migrate_columns(conn)

        # Teammate message should NOT have been re-flagged (v2 backfill already ran)
        cursor = conn.cursor()
        cursor.execute("SELECT is_notification FROM messages WHERE id = 1")
        assert cursor.fetchone()[0] == 0
        # v3 migration runs from v2, bumping version to 3
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
        conn.close()

    def test_v2_to_v3_preserves_import_log(self):
        """v3 migration does selective backfill, not wholesale import_log clear."""
        conn = _versioned_db(user_version=2)
        conn.execute("INSERT INTO import_log (file_path, file_hash) VALUES ('/nonexistent/session.jsonl', 'abc123')")
        conn.commit()

        _migrate_columns(conn)

        # import_log row preserved (file doesn't exist, so backfill skips it)
        count = conn.execute("SELECT COUNT(*) FROM import_log").fetchone()[0]
        assert count == 1
        hash_val = conn.execute("SELECT file_hash FROM import_log").fetchone()[0]
        assert hash_val == "abc123"
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
        conn.close()

    def test_v3_backfill_updates_origin(self, tmp_path):
        """v3 migration backfills origin from JSONL for existing messages."""
        conn = _versioned_db(user_version=2)
        # Create a session and message
        conn.execute("CREATE TABLE sessions (id INTEGER PRIMARY KEY, uuid TEXT UNIQUE, project_id INTEGER, parent_session_id INTEGER, git_branch TEXT, cwd TEXT)")
        conn.execute("INSERT INTO sessions (id, uuid) VALUES (1, 'test-uuid')")
        conn.execute("INSERT INTO messages (id, session_id, uuid, role, content, is_notification) VALUES (1, 1, 'msg-uuid-1', 'user', 'Hello from Telegram', 0)")

        # Create a JSONL file with origin data
        jsonl_file = tmp_path / "test-uuid.jsonl"
        import json
        jsonl_file.write_text(
            json.dumps({"type": "user", "uuid": "msg-uuid-1", "origin": {"kind": "channel", "server": "plugin:telegram:telegram"}}) + "\n"
        )
        conn.execute("INSERT INTO import_log (file_path, file_hash) VALUES (?, 'abc')", (str(jsonl_file),))
        conn.commit()

        _migrate_columns(conn)

        origin = conn.execute("SELECT origin FROM messages WHERE id = 1").fetchone()[0]
        assert origin == "telegram"
        conn.close()

    def test_v3_backfill_preserves_hash_for_channel_sessions(self, tmp_path):
        """v3 migration preserves file_hash — no longer nullifies to trigger reimport.

        Phase 2 hash nullification was removed because the reimport path was
        destructive (delete-all-then-insert) and JSONL files expire after 30 days.
        """
        conn = _versioned_db(user_version=2)
        conn.execute("CREATE TABLE sessions (id INTEGER PRIMARY KEY, uuid TEXT UNIQUE, project_id INTEGER, parent_session_id INTEGER, git_branch TEXT, cwd TEXT)")
        conn.execute("INSERT INTO sessions (id, uuid) VALUES (1, 'chan-uuid')")

        # Create JSONL with an isMeta+origin entry (channel message previously filtered)
        jsonl_file = tmp_path / "chan-uuid.jsonl"
        import json
        lines = [
            json.dumps({"type": "user", "uuid": "u1", "message": {"content": "hi"}}),
            json.dumps({"isMeta": True, "type": "user", "uuid": "u2", "origin": {"kind": "channel", "server": "plugin:telegram:telegram"}, "message": {"content": "channel msg"}}),
        ]
        jsonl_file.write_text("\n".join(lines) + "\n")
        conn.execute("INSERT INTO import_log (file_path, file_hash) VALUES (?, 'original')", (str(jsonl_file),))
        conn.commit()

        _migrate_columns(conn)

        hash_val = conn.execute("SELECT file_hash FROM import_log").fetchone()[0]
        assert hash_val == "original"  # Hash preserved — no destructive reimport triggered
        conn.close()


class TestLoadSettings:
    def test_always_returns_defaults(self, tmp_path, monkeypatch):
        """load_settings returns hardcoded defaults when no config file exists."""
        import memory_lib.db as db_module
        monkeypatch.setattr(db_module, "CONFIG_PATH", tmp_path / "no_config.json")
        settings = load_settings()
        assert settings == DEFAULT_SETTINGS

    def test_returns_copy(self):
        """Each call should return a fresh copy, not a reference."""
        s1 = load_settings()
        s2 = load_settings()
        s1["max_context_sessions"] = 99
        assert s2["max_context_sessions"] == 2

    def test_default_values(self):
        assert DEFAULT_SETTINGS["auto_inject_context"] is True
        assert DEFAULT_SETTINGS["max_context_sessions"] == 2
        assert DEFAULT_SETTINGS["logging_enabled"] is False
        assert DEFAULT_SETTINGS["sync_on_stop"] is True
        assert isinstance(DEFAULT_SETTINGS["exclude_projects"], list)


class TestMigrateDb:
    """Test migrate_db — v2 schema detection and DB replacement."""

    def test_fresh_db_no_tables(self):
        """A fresh DB with no tables should return False (no migration needed)."""
        conn = sqlite3.connect(":memory:")
        result = migrate_db(conn)
        assert result is False

    def test_v3_db_no_migration(self):
        """A DB with branches table (v3) should return False."""
        conn = sqlite3.connect(":memory:")
        conn.executescript(SCHEMA)
        conn.commit()
        result = migrate_db(conn)
        assert result is False

    def test_v2_db_file_migrated(self):
        """A file-backed DB with sessions but no branches (v2) should be deleted and recreated."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)

        try:
            # Create a v2-style DB (sessions exists, branches doesn't)
            conn = sqlite3.connect(str(db_path))
            conn.execute("""
                CREATE TABLE sessions (
                    id INTEGER PRIMARY KEY,
                    uuid TEXT UNIQUE NOT NULL,
                    project_id INTEGER
                )
            """)
            conn.execute("INSERT INTO sessions (uuid) VALUES ('old-session')")
            conn.commit()

            result = migrate_db(conn)
            assert result is True, "Should detect v2 schema and migrate"

            # Old DB should have been deleted and recreated with v3 schema
            # Verify new schema has branches table
            new_conn = sqlite3.connect(str(db_path))
            cursor = new_conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='branches'")
            assert cursor.fetchone() is not None, "Recreated DB should have branches table"

            # Old session should be gone (DB was nuked)
            cursor.execute("SELECT COUNT(*) FROM sessions")
            assert cursor.fetchone()[0] == 0, "Old data should be gone after migration"
            new_conn.close()
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_v2_in_memory_migrated(self):
        """An in-memory DB with v2 schema should return True and handle gracefully."""
        conn = sqlite3.connect(":memory:")
        conn.execute("""
            CREATE TABLE sessions (
                id INTEGER PRIMARY KEY,
                uuid TEXT UNIQUE NOT NULL
            )
        """)
        conn.commit()

        result = migrate_db(conn)
        assert result is True, "Should detect v2 schema in memory DB"


def _project_path_db():
    """Create an in-memory DB with full schema for project path migration tests."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA)
    conn.commit()
    _migrate_columns(conn)
    return conn


class TestMigrateProjectPaths:
    def test_fixes_wrong_path_from_session_cwd(self):
        """Migration corrects a project whose path was derived from a hyphenated key."""
        conn = _project_path_db()
        cursor = conn.cursor()

        # Insert a project with a lossy hyphen-split path (e.g. meta-ads-cli became meta/ads/cli)
        cursor.execute(
            "INSERT INTO projects (path, key, name) VALUES (?, ?, ?)",
            ("/Users/foo/repos/meta/ads/cli", "-Users-foo-repos-meta-ads-cli", "cli")
        )
        proj_id = cursor.lastrowid

        # Insert a session with the real cwd
        cursor.execute(
            "INSERT INTO sessions (uuid, project_id, cwd) VALUES (?, ?, ?)",
            ("sess-uuid-1", proj_id, "/Users/foo/repos/meta-ads-cli")
        )
        conn.commit()

        _migrate_project_paths(conn)

        cursor.execute("SELECT path, name FROM projects WHERE id = ?", (proj_id,))
        path, name = cursor.fetchone()
        assert path == "/Users/foo/repos/meta-ads-cli"
        assert name == "meta-ads-cli"
        conn.close()

    def test_no_change_when_path_already_correct(self):
        """Migration leaves projects alone when path already matches session cwd."""
        conn = _project_path_db()
        cursor = conn.cursor()

        cursor.execute(
            "INSERT INTO projects (path, key, name) VALUES (?, ?, ?)",
            ("/Users/foo/repos/myproject", "-Users-foo-repos-myproject", "myproject")
        )
        proj_id = cursor.lastrowid
        cursor.execute(
            "INSERT INTO sessions (uuid, project_id, cwd) VALUES (?, ?, ?)",
            ("sess-uuid-2", proj_id, "/Users/foo/repos/myproject")
        )
        conn.commit()

        _migrate_project_paths(conn)

        cursor.execute("SELECT path, name FROM projects WHERE id = ?", (proj_id,))
        path, name = cursor.fetchone()
        assert path == "/Users/foo/repos/myproject"
        assert name == "myproject"
        conn.close()

    def test_merge_duplicate_on_path_collision(self):
        """When fixing a path would create a duplicate, sessions are merged into the keeper."""
        conn = _project_path_db()
        cursor = conn.cursor()

        # Project A: already has the correct path
        cursor.execute(
            "INSERT INTO projects (path, key, name) VALUES (?, ?, ?)",
            ("/Users/foo/repos/meta-ads-cli", "-Users-foo-repos-meta-ads-cli-correct", "meta-ads-cli")
        )
        keeper_id = cursor.lastrowid
        cursor.execute(
            "INSERT INTO sessions (uuid, project_id, cwd) VALUES (?, ?, ?)",
            ("sess-keeper", keeper_id, "/Users/foo/repos/meta-ads-cli")
        )

        # Project B: wrong path, but its sessions' cwd matches A's path
        cursor.execute(
            "INSERT INTO projects (path, key, name) VALUES (?, ?, ?)",
            ("/Users/foo/repos/meta/ads/cli", "-Users-foo-repos-meta-ads-cli-wrong", "cli")
        )
        dup_id = cursor.lastrowid
        cursor.execute(
            "INSERT INTO sessions (uuid, project_id, cwd) VALUES (?, ?, ?)",
            ("sess-dup", dup_id, "/Users/foo/repos/meta-ads-cli")
        )
        conn.commit()

        _migrate_project_paths(conn)

        # Duplicate project should be gone
        cursor.execute("SELECT id FROM projects WHERE id = ?", (dup_id,))
        assert cursor.fetchone() is None

        # The orphaned session should now belong to the keeper
        cursor.execute("SELECT project_id FROM sessions WHERE uuid = ?", ("sess-dup",))
        assert cursor.fetchone()[0] == keeper_id

        # Keeper's path is unchanged
        cursor.execute("SELECT path FROM projects WHERE id = ?", (keeper_id,))
        assert cursor.fetchone()[0] == "/Users/foo/repos/meta-ads-cli"
        conn.close()

    def test_idempotent(self):
        """Running migration twice produces the same result."""
        conn = _project_path_db()
        cursor = conn.cursor()

        cursor.execute(
            "INSERT INTO projects (path, key, name) VALUES (?, ?, ?)",
            ("/Users/foo/repos/meta/ads/cli", "-Users-foo-repos-meta-ads-cli", "cli")
        )
        proj_id = cursor.lastrowid
        cursor.execute(
            "INSERT INTO sessions (uuid, project_id, cwd) VALUES (?, ?, ?)",
            ("sess-idem", proj_id, "/Users/foo/repos/meta-ads-cli")
        )
        conn.commit()

        _migrate_project_paths(conn)
        _migrate_project_paths(conn)  # Second run should be a no-op

        cursor.execute("SELECT path, name FROM projects WHERE id = ?", (proj_id,))
        row = cursor.fetchone()
        assert row is not None
        assert row[0] == "/Users/foo/repos/meta-ads-cli"
        assert row[1] == "meta-ads-cli"
        conn.close()

    def test_uses_most_common_cwd(self):
        """When sessions have different cwds, the most common one wins."""
        conn = _project_path_db()
        cursor = conn.cursor()

        cursor.execute(
            "INSERT INTO projects (path, key, name) VALUES (?, ?, ?)",
            ("/Users/foo/repos/meta/ads/cli", "-Users-foo-repos-meta-ads-cli", "cli")
        )
        proj_id = cursor.lastrowid

        real_path = "/Users/foo/repos/meta-ads-cli"
        other_path = "/Users/foo/repos/other"
        for i, cwd in enumerate([real_path, real_path, real_path, other_path]):
            cursor.execute(
                "INSERT INTO sessions (uuid, project_id, cwd) VALUES (?, ?, ?)",
                (f"sess-multi-{i}", proj_id, cwd)
            )
        conn.commit()

        _migrate_project_paths(conn)

        cursor.execute("SELECT path FROM projects WHERE id = ?", (proj_id,))
        assert cursor.fetchone()[0] == real_path
        conn.close()

    def test_skips_project_with_no_sessions(self):
        """Projects without sessions are left untouched."""
        conn = _project_path_db()
        cursor = conn.cursor()

        cursor.execute(
            "INSERT INTO projects (path, key, name) VALUES (?, ?, ?)",
            ("/Users/foo/repos/meta/ads/cli", "-Users-foo-repos-meta-ads-cli", "cli")
        )
        proj_id = cursor.lastrowid
        conn.commit()

        _migrate_project_paths(conn)

        cursor.execute("SELECT path FROM projects WHERE id = ?", (proj_id,))
        assert cursor.fetchone()[0] == "/Users/foo/repos/meta/ads/cli"
        conn.close()


def _v3_db_with_messages():
    """Create an in-memory DB at user_version=3 with sample messages for v4 migration tests."""
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY, session_id INTEGER, uuid TEXT,
            parent_uuid TEXT, timestamp DATETIME, role TEXT,
            content TEXT NOT NULL, tool_summary TEXT,
            has_tool_use INTEGER DEFAULT 0, has_thinking INTEGER DEFAULT 0,
            is_notification INTEGER DEFAULT 0, origin TEXT,
            UNIQUE(session_id, uuid)
        )
    """)
    conn.execute("""
        CREATE TABLE branches (
            id INTEGER PRIMARY KEY, session_id INTEGER, leaf_uuid TEXT,
            fork_point_uuid TEXT, is_active INTEGER DEFAULT 1,
            started_at DATETIME, ended_at DATETIME, exchange_count INTEGER DEFAULT 0,
            files_modified TEXT, commits TEXT, aggregated_content TEXT,
            tool_counts TEXT, context_summary TEXT, context_summary_json TEXT,
            summary_version INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE branch_messages (
            branch_id INTEGER, message_id INTEGER, PRIMARY KEY (branch_id, message_id)
        )
    """)
    conn.execute("""
        CREATE TABLE import_log (
            id INTEGER PRIMARY KEY, file_path TEXT UNIQUE NOT NULL,
            file_hash TEXT, imported_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            messages_imported INTEGER DEFAULT 0
        )
    """)
    conn.execute("PRAGMA user_version = 3")
    conn.commit()
    return conn


class TestV4Migration:
    """user_version=3 -> 4: clear stale task-notification values from origin column."""

    def test_v4_bumps_user_version(self):
        """After migration from v3, user_version should be 4."""
        conn = _v3_db_with_messages()
        _migrate_columns(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
        conn.close()

    def test_v4_clears_task_notification_origin(self):
        """Rows with origin='task-notification' are set to NULL — parse_origin kind-fallback bug fix."""
        conn = _v3_db_with_messages()
        conn.execute("INSERT INTO messages (id, session_id, role, content, origin) VALUES (1, 1, 'user', 'hello', 'task-notification')")
        conn.execute("INSERT INTO messages (id, session_id, role, content, origin) VALUES (2, 1, 'user', 'telegram msg', 'telegram')")
        conn.execute("INSERT INTO messages (id, session_id, role, content, origin) VALUES (3, 1, 'user', 'no origin', NULL)")
        conn.commit()

        _migrate_columns(conn)

        cursor = conn.cursor()
        cursor.execute("SELECT id, origin FROM messages ORDER BY id")
        rows = {row[0]: row[1] for row in cursor.fetchall()}
        assert rows[1] is None, "task-notification origin should be cleared to NULL"
        assert rows[2] == "telegram", "non-task-notification origin must be preserved"
        assert rows[3] is None, "already-NULL origin must remain NULL"
        conn.close()

    def test_v4_idempotent(self):
        """Re-running _migrate_columns on a v4 DB is a safe no-op."""
        conn = _v3_db_with_messages()
        conn.execute("INSERT INTO messages (id, session_id, role, content, origin) VALUES (1, 1, 'user', 'hello', 'telegram')")
        conn.commit()

        _migrate_columns(conn)  # v3->v4
        _migrate_columns(conn)  # no-op

        assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
        origin = conn.execute("SELECT origin FROM messages WHERE id = 1").fetchone()[0]
        assert origin == "telegram", "idempotent re-run must not corrupt already-correct data"
        conn.close()

    def test_v4_skips_when_already_v4(self):
        """If user_version is already 4, the UPDATE must not run again."""
        conn = _v3_db_with_messages()
        # Pre-set to v4 with a 'task-notification' origin to confirm the DML is skipped
        conn.execute("INSERT INTO messages (id, session_id, role, content, origin) VALUES (1, 1, 'user', 'hello', 'task-notification')")
        conn.execute("PRAGMA user_version = 4")
        conn.commit()

        _migrate_columns(conn)

        # Because we're already at v4 the DML block is skipped — the value stays
        origin = conn.execute("SELECT origin FROM messages WHERE id = 1").fetchone()[0]
        assert origin == "task-notification", "DML should not re-run on a v4 DB"
        conn.close()


class TestLoadConfig:
    """load_config() must guard against malformed JSON written to CONFIG_PATH."""

    def test_returns_dict_for_valid_config(self, tmp_path, monkeypatch):
        """A well-formed JSON object is returned as-is."""
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"auto_inject_context": False, "onboarding_completed": True}))
        monkeypatch.setattr("memory_lib.db.CONFIG_PATH", cfg)

        result = load_config()
        assert result == {"auto_inject_context": False, "onboarding_completed": True}

    def test_returns_empty_dict_for_json_array(self, tmp_path, monkeypatch):
        """A JSON array (not a dict) must return {} — prevents callers from crashing on .get()."""
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps([1, 2, 3]))
        monkeypatch.setattr("memory_lib.db.CONFIG_PATH", cfg)

        assert load_config() == {}

    def test_returns_empty_dict_for_json_string(self, tmp_path, monkeypatch):
        """A JSON string must return {} — not a dict, should not propagate."""
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps("hello"))
        monkeypatch.setattr("memory_lib.db.CONFIG_PATH", cfg)

        assert load_config() == {}

    def test_returns_empty_dict_for_json_null(self, tmp_path, monkeypatch):
        """JSON null must return {} — null is not a valid settings container."""
        cfg = tmp_path / "config.json"
        cfg.write_text("null")
        monkeypatch.setattr("memory_lib.db.CONFIG_PATH", cfg)

        assert load_config() == {}

    def test_returns_empty_dict_for_missing_file(self, tmp_path, monkeypatch):
        """Missing config file returns {} without raising."""
        monkeypatch.setattr("memory_lib.db.CONFIG_PATH", tmp_path / "nonexistent.json")

        assert load_config() == {}

    def test_returns_empty_dict_for_invalid_json(self, tmp_path, monkeypatch):
        """Corrupt JSON returns {} without raising."""
        cfg = tmp_path / "config.json"
        cfg.write_text("{bad json}")
        monkeypatch.setattr("memory_lib.db.CONFIG_PATH", cfg)

        assert load_config() == {}


class TestLoadSettingsWithConfig:
    """load_settings() must stay safe when config.json contains non-dict JSON."""

    def test_non_dict_config_returns_defaults(self, tmp_path, monkeypatch):
        """load_settings() returns DEFAULT_SETTINGS when config.json is a JSON array."""
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps([]))
        monkeypatch.setattr("memory_lib.db.CONFIG_PATH", cfg)

        result = load_settings()
        assert result == DEFAULT_SETTINGS

    def test_config_overrides_applied(self, tmp_path, monkeypatch):
        """Valid config keys are merged into defaults."""
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"auto_inject_context": False, "max_context_sessions": 5}))
        monkeypatch.setattr("memory_lib.db.CONFIG_PATH", cfg)

        result = load_settings()
        assert result["auto_inject_context"] is False
        assert result["max_context_sessions"] == 5
        assert result["logging_enabled"] is False  # unchanged default

    def test_missing_config_returns_defaults(self, tmp_path, monkeypatch):
        """load_settings() returns DEFAULT_SETTINGS when config.json does not exist."""
        monkeypatch.setattr("memory_lib.db.CONFIG_PATH", tmp_path / "nonexistent.json")

        result = load_settings()
        assert result == DEFAULT_SETTINGS


class TestCurrentOnboardingVersion:
    """Import contract: CURRENT_ONBOARDING_VERSION must exist and equal 1."""

    def test_value_is_one(self):
        """Both write_config and onboarding.py depend on this being 1."""
        assert CURRENT_ONBOARDING_VERSION == 1


class TestMigrateDbBackupGuard:
    """Gap 3 — backup failure must block the destructive nuke in migrate_db.

    Prevents: a disk-full or permission error during backup silently destroying
    the only copy of conversation data that predates the 30-day JSONL expiry.
    """

    def test_backup_failure_blocks_nuke(self, tmp_path, monkeypatch):
        """When _backup_db_before_migration returns False, migrate_db must abort."""
        # Create a file-backed v2 DB (sessions exists, branches does not)
        db_file = tmp_path / "conversations.db"
        conn = sqlite3.connect(str(db_file))
        conn.execute("""
            CREATE TABLE sessions (
                id INTEGER PRIMARY KEY,
                uuid TEXT UNIQUE NOT NULL,
                project_id INTEGER
            )
        """)
        conn.execute("INSERT INTO sessions (uuid) VALUES ('keep-me')")
        conn.commit()

        import memory_lib.db as db_module
        monkeypatch.setattr(db_module, "_backup_db_before_migration", lambda *a, **kw: False)

        result = migrate_db(conn)

        # Must refuse to migrate when backup fails
        assert result is False, (
            "migrate_db must return False when _backup_db_before_migration returns False"
        )

        # Original DB file must still exist
        assert db_file.exists(), "DB file must not be deleted when backup failed"

        # File must still be readable and contain original data
        verify = sqlite3.connect(str(db_file))
        count = verify.execute("SELECT COUNT(*) FROM sessions WHERE uuid = 'keep-me'").fetchone()[0]
        verify.close()
        assert count == 1, (
            "Original session data must be intact after aborted migration"
        )
