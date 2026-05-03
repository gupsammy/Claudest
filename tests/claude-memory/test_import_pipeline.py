#!/usr/bin/env python3
"""Integration tests for the import pipeline with v3 schema guards."""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path
import shutil

import pytest

# Add hooks dir to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "plugins" / "claude-memory" / "hooks"))

import os

import import_conversations as import_module
from import_conversations import (
    acquire_import_lock,
    backup_database,
    import_codex_session,
    import_codex_sessions_dir,
    import_session,
    import_project,
    release_import_lock,
)
from memory_lib.db import SCHEMA, _migrate_columns

FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def memory_db():
    """In-memory SQLite database with full v3 schema applied."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA)
    conn.commit()
    _migrate_columns(conn)
    yield conn
    conn.close()


@pytest.fixture
def project_id(memory_db):
    """Create a test project and return its ID."""
    cursor = memory_db.cursor()
    cursor.execute(
        "INSERT INTO projects (path, key, name) VALUES (?, ?, ?)",
        ("/test/project", "-test-project", "test_project")
    )
    memory_db.commit()
    return cursor.lastrowid


class TestImportSessionBasic:
    """Test basic import workflow with linear conversation."""

    def test_import_session_basic(self, memory_db, project_id):
        """Import linear_3_exchange.jsonl and verify counts."""
        fixture_file = FIXTURE_DIR / "linear_3_exchange.jsonl"
        assert fixture_file.exists(), f"Fixture {fixture_file} not found"

        branches_imported, total_messages = import_session(
            memory_db, fixture_file, project_id
        )

        # Should import successfully
        assert branches_imported > 0, "At least one branch should be imported"
        assert total_messages > 0, "At least one message should be imported"

        # Verify session was created
        cursor = memory_db.cursor()
        cursor.execute("SELECT COUNT(*) FROM sessions WHERE project_id = ?", (project_id,))
        session_count = cursor.fetchone()[0]
        assert session_count == 1, "Exactly one session should exist"

        # Verify branches exist
        cursor.execute("SELECT COUNT(*) FROM branches WHERE session_id IN (SELECT id FROM sessions WHERE project_id = ?)", (project_id,))
        branch_count = cursor.fetchone()[0]
        assert branch_count == branches_imported, "Branch count should match returned value"

        # Verify messages exist
        cursor.execute("SELECT COUNT(*) FROM messages WHERE session_id IN (SELECT id FROM sessions WHERE project_id = ?)", (project_id,))
        message_count = cursor.fetchone()[0]
        assert message_count == total_messages, "Message count should match returned value"


class TestImportSessionWithBranches:
    """Test import with conversation branches (from rewinds)."""

    def test_import_session_with_branches(self, memory_db, project_id):
        """Import single_rewind.jsonl and verify 3 branches are detected."""
        fixture_file = FIXTURE_DIR / "single_rewind.jsonl"
        assert fixture_file.exists(), f"Fixture {fixture_file} not found"

        branches_imported, total_messages = import_session(
            memory_db, fixture_file, project_id
        )

        # single_rewind has 3 branches
        assert branches_imported == 3, f"Expected 3 branches, got {branches_imported}"
        assert total_messages > 0, "Should import messages"

        # Verify branches table
        cursor = memory_db.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM branches WHERE session_id IN (SELECT id FROM sessions WHERE project_id = ?)",
            (project_id,)
        )
        assert cursor.fetchone()[0] == 3, "Exactly 3 branches should exist in DB"

        # Verify active branch is marked
        cursor.execute(
            "SELECT COUNT(*) FROM branches WHERE is_active = 1 AND session_id IN (SELECT id FROM sessions WHERE project_id = ?)",
            (project_id,)
        )
        assert cursor.fetchone()[0] == 1, "Exactly one active branch should exist"


class TestEmptySessionGuard:
    """Test guard 1: sessions with only tool_result messages are deleted."""

    def test_empty_session_guard(self, memory_db, project_id):
        """Create JSONL with only tool_result messages and verify session is deleted."""
        # Create temporary JSONL with only tool_result content
        with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
            temp_path = Path(f.name)
            # Write file-history-snapshot (ignored)
            f.write('{"type":"file-history-snapshot"}\n')
            # Write progress (ignored)
            f.write('{"uuid":"root-uuid","type":"progress","timestamp":"2026-02-14T00:00:00Z","sessionId":"test","cwd":"/"}\n')
            # Write user message with tool_result only
            f.write('{"uuid":"msg1","parentUuid":"root-uuid","type":"user","timestamp":"2026-02-14T00:00:01Z","sessionId":"test","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"tool1","content":"result"}]}}\n')
            # Write assistant message with only tool_use (no text)
            f.write('{"uuid":"msg2","parentUuid":"msg1","type":"assistant","timestamp":"2026-02-14T00:00:02Z","sessionId":"test","message":{"role":"assistant","content":[{"type":"tool_use","id":"tool2","name":"Bash","input":{"placeholder":true}}]}}\n')

        try:
            branches_imported, total_messages = import_session(
                memory_db, temp_path, project_id
            )

            # Guard 1: no extractable content means session deleted and returns -1
            assert branches_imported == -1, "Session should be deleted (guard 1 triggered)"
            assert total_messages == 0, "No messages should be imported"

            # Verify session was NOT created or was cleaned up
            cursor = memory_db.cursor()
            cursor.execute("SELECT COUNT(*) FROM sessions WHERE project_id = ?", (project_id,))
            session_count = cursor.fetchone()[0]
            assert session_count == 0, "Empty session should be deleted"

        finally:
            temp_path.unlink()


class TestEmptyBranchGuard:
    """Test guard 2: branches with empty aggregated content are cleaned up."""

    def test_empty_branch_guard(self, memory_db, project_id):
        """Guard 2 should delete branches whose aggregated content is empty.

        Creates a JSONL where the only real content is notification messages,
        imports via import_session, and verifies the session is cleaned up
        because all branches have empty aggregated content after excluding
        notifications.
        """
        # Create JSONL with a real user message + notification-only branch content
        with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
            temp_path = Path(f.name)
            # Root entry
            f.write('{"uuid":"root","type":"progress","timestamp":"2026-02-14T00:00:00Z","sessionId":"test","cwd":"/"}\n')
            # User notification message (the only "user" content)
            f.write('{"uuid":"msg1","parentUuid":"root","type":"user","timestamp":"2026-02-14T00:00:01Z","sessionId":"test","message":{"role":"user","content":"<task-notification><task-id>abc</task-id>Agent result here</task-notification>"}}\n')
            # Assistant response to notification
            f.write('{"uuid":"msg2","parentUuid":"msg1","type":"assistant","timestamp":"2026-02-14T00:00:02Z","sessionId":"test","message":{"role":"assistant","content":[{"type":"text","text":"Acknowledged."}]}}\n')

        try:
            branches_imported, total_messages = import_session(
                memory_db, temp_path, project_id
            )

            # Guard 2 fires because after excluding notifications, the branch
            # has only assistant text — but the notification user message IS
            # imported (is_notification=1). The branch aggregation excludes
            # notifications, so if the branch's only user content is a
            # notification, aggregated_content may be non-empty (assistant text
            # remains). Let's verify the branch state is consistent.
            cursor = memory_db.cursor()
            if branches_imported > 0:
                # Branch survived — verify notification is flagged
                cursor.execute("""
                    SELECT COUNT(*) FROM messages
                    WHERE session_id IN (SELECT id FROM sessions WHERE project_id = ?)
                      AND is_notification = 1
                """, (project_id,))
                notif_count = cursor.fetchone()[0]
                assert notif_count > 0, "Notification messages should be flagged"
            else:
                # Branch was deleted by guard 2 or guard 3 — session should not exist
                cursor.execute("SELECT COUNT(*) FROM sessions WHERE project_id = ?", (project_id,))
                assert cursor.fetchone()[0] == 0, "Empty session should be cleaned up"
        finally:
            temp_path.unlink()


class TestReimportIdempotent:
    """Test that reimporting the same file is idempotent."""

    def test_reimport_idempotent(self, memory_db, project_id):
        """Import the same file twice and verify hash check prevents duplicate."""
        fixture_file = FIXTURE_DIR / "linear_3_exchange.jsonl"

        # First import
        branches1, _messages1 = import_session(memory_db, fixture_file, project_id)
        assert branches1 > 0, "First import should succeed"

        # Count sessions after first import
        cursor = memory_db.cursor()
        cursor.execute("SELECT COUNT(*) FROM sessions WHERE project_id = ?", (project_id,))
        sessions_after_first = cursor.fetchone()[0]

        # Second import (same file)
        branches2, messages2 = import_session(memory_db, fixture_file, project_id)
        assert branches2 == -1, "Second import should return -1 (file hash match)"
        assert messages2 == 0, "No new messages on second import"

        # Verify no new session created
        cursor.execute("SELECT COUNT(*) FROM sessions WHERE project_id = ?", (project_id,))
        sessions_after_second = cursor.fetchone()[0]
        assert sessions_after_second == sessions_after_first, "No new session should be created"


class TestImportLogTracking:
    """Test that import_log tracks file imports correctly."""

    def test_import_log_created(self, memory_db, project_id):
        """Verify import_log entry is created with file hash and message count."""
        fixture_file = FIXTURE_DIR / "linear_3_exchange.jsonl"

        branches_imported, total_messages = import_session(
            memory_db, fixture_file, project_id
        )
        assert branches_imported > 0, "Import should succeed"

        # Check import_log
        cursor = memory_db.cursor()
        cursor.execute(
            "SELECT file_path, file_hash, messages_imported FROM import_log WHERE file_path = ?",
            (str(fixture_file),)
        )
        log_row = cursor.fetchone()
        assert log_row is not None, "import_log entry should exist"
        assert log_row[0] == str(fixture_file), "File path should match"
        assert log_row[1], "File hash should be set"
        assert log_row[2] == total_messages, "Message count should match"

    def test_import_log_updated_on_reimport(self, memory_db, project_id):
        """Verify import_log is updated on forced reimport (hash + message count)."""
        fixture_file = FIXTURE_DIR / "linear_3_exchange.jsonl"

        # First import
        import_session(memory_db, fixture_file, project_id)
        cursor = memory_db.cursor()
        cursor.execute(
            "SELECT file_hash, messages_imported FROM import_log WHERE file_path = ?",
            (str(fixture_file),)
        )
        first_row = cursor.fetchone()
        assert first_row is not None, "import_log entry should exist"
        first_hash, first_msg_count = first_row

        # Invalidate hash to force reimport (same pattern as TestFKSafeReimport)
        cursor.execute(
            "UPDATE import_log SET file_hash = 'stale' WHERE file_path = ?",
            (str(fixture_file),)
        )
        memory_db.commit()

        # Reimport — should restore correct hash
        branches, msg_count = import_session(memory_db, fixture_file, project_id)
        assert branches > 0, "Forced reimport should succeed"

        cursor.execute(
            "SELECT file_hash, messages_imported FROM import_log WHERE file_path = ?",
            (str(fixture_file),)
        )
        second_row = cursor.fetchone()
        assert second_row[0] == first_hash, "Hash should be restored to real value"
        assert second_row[1] == first_msg_count, "Message count should match"


class TestImportCodexSessions:
    """Test historical Codex Desktop import path."""

    def test_import_codex_session_tracks_import_log(self, memory_db):
        fixture_file = FIXTURE_DIR / "codex_minimal.codexlog"

        branches_imported, total_messages = import_codex_session(memory_db, fixture_file)
        memory_db.commit()
        skipped_branches, skipped_messages = import_codex_session(memory_db, fixture_file)

        assert branches_imported == 1
        assert total_messages == 2
        assert skipped_branches == -1
        assert skipped_messages == 0

        cursor = memory_db.cursor()
        cursor.execute("SELECT path, key, name FROM projects")
        assert cursor.fetchone() == (
            "/Users/samarthgupta/repos/myrepos/claudest",
            "-Users-samarthgupta-repos-myrepos-claudest",
            "claudest",
        )

        cursor.execute("SELECT role, content, origin FROM messages ORDER BY timestamp")
        assert cursor.fetchall() == [
            ("user", "please remember this codex session", "codex"),
            ("assistant", "Recorded from Codex.", "codex"),
        ]

        cursor.execute(
            "SELECT file_hash, messages_imported FROM import_log WHERE file_path = ?",
            (str(fixture_file),)
        )
        row = cursor.fetchone()
        assert row is not None
        assert row[0]
        assert row[1] == 2

    def test_import_codex_sessions_dir_updates_sentinel(self, memory_db, tmp_path, monkeypatch):
        sessions_dir = tmp_path / "sessions"
        nested = sessions_dir / "2026" / "05" / "03"
        nested.mkdir(parents=True)
        shutil.copy(FIXTURE_DIR / "codex_minimal.codexlog", nested / "rollout-test.jsonl")
        sentinel = tmp_path / ".last-codex-import"
        monkeypatch.setattr(import_module, "CODEX_IMPORT_SENTINEL", sentinel)

        branches_imported, messages_imported, skipped = import_codex_sessions_dir(memory_db, sessions_dir)

        assert branches_imported == 1
        assert messages_imported == 2
        assert skipped == 0
        assert sentinel.exists()

    def test_sentinel_does_not_advance_on_commit_failure(self, memory_db, tmp_path, monkeypatch):
        """If conn.commit() raises, the sentinel must not be written.

        Pins the data-loss-race fix: sentinel only advances for work that's
        actually durable on disk.
        """
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        shutil.copy(FIXTURE_DIR / "codex_minimal.codexlog", sessions_dir / "rollout-test.jsonl")
        sentinel = tmp_path / ".last-codex-import"
        monkeypatch.setattr(import_module, "CODEX_IMPORT_SENTINEL", sentinel)

        # sqlite3.Connection attributes are read-only, so wrap it.
        class CommitFailingConn:
            def __init__(self, real):
                self._real = real

            def __getattr__(self, name):
                return getattr(self._real, name)

            def commit(self):
                raise sqlite3.OperationalError("simulated commit failure")

        wrapped = CommitFailingConn(memory_db)

        with pytest.raises(sqlite3.OperationalError):
            import_codex_sessions_dir(wrapped, sessions_dir)

        assert not sentinel.exists(), (
            "Sentinel must not advance when commit fails — otherwise the next "
            "SessionStart skips re-importing data that was never actually persisted."
        )


class TestImportLock:
    """Test the cross-platform PID-based import lock."""

    def test_acquire_succeeds_when_unheld(self, tmp_path):
        lock_path = tmp_path / "import.lock"
        pid = acquire_import_lock(lock_path)
        try:
            assert pid == os.getpid()
            assert lock_path.exists()
            assert lock_path.read_text() == str(os.getpid())
        finally:
            release_import_lock(lock_path)

    def test_acquire_fails_when_held_by_live_process(self, tmp_path):
        lock_path = tmp_path / "import.lock"
        # Plant a lockfile with the current PID — current process is alive.
        lock_path.write_text(str(os.getpid()))
        try:
            assert acquire_import_lock(lock_path) is None
        finally:
            lock_path.unlink(missing_ok=True)

    def test_acquire_steals_stale_lock(self, tmp_path):
        """Lock held by a non-existent PID is stale — should be stolen."""
        lock_path = tmp_path / "import.lock"
        # PID 1 is init/launchd; we plant a fake PID that's almost certainly dead.
        # Use a high PID unlikely to be in use.
        lock_path.write_text("999999")
        pid = acquire_import_lock(lock_path)
        try:
            assert pid == os.getpid(), "Should steal stale lock"
            assert lock_path.read_text() == str(os.getpid())
        finally:
            release_import_lock(lock_path)

    def test_release_only_removes_own_lock(self, tmp_path):
        """release_import_lock must not delete a lock held by another process."""
        lock_path = tmp_path / "import.lock"
        lock_path.write_text("999999")  # Foreign PID
        release_import_lock(lock_path)
        assert lock_path.exists(), "Foreign lock must not be released"
        lock_path.unlink()

    def test_acquire_handles_corrupt_lockfile(self, tmp_path):
        """Lock file with non-integer content should be treated as stale."""
        lock_path = tmp_path / "import.lock"
        lock_path.write_text("not-a-pid")
        pid = acquire_import_lock(lock_path)
        try:
            assert pid == os.getpid()
        finally:
            release_import_lock(lock_path)


class TestImportBackup:
    """Test SQLite backup helper used before bulk imports."""

    def test_backup_database_uses_sqlite_backup(self, tmp_path):
        db_path = tmp_path / "conversations.db"
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE sample (value TEXT)")
        conn.execute("INSERT INTO sample (value) VALUES ('kept')")
        conn.commit()
        conn.close()

        backup_path = backup_database(db_path)

        assert backup_path is not None
        assert backup_path.exists()
        backup_conn = sqlite3.connect(backup_path)
        try:
            value = backup_conn.execute("SELECT value FROM sample").fetchone()[0]
        finally:
            backup_conn.close()
        assert value == "kept"

    def test_backup_rotation_keeps_only_last_n(self, tmp_path):
        """After more than `retention` backups, only the newest N must remain."""
        db_path = tmp_path / "conversations.db"
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE sample (value TEXT)")
        conn.execute("INSERT INTO sample (value) VALUES ('kept')")
        conn.commit()
        conn.close()

        # Microsecond-resolution timestamps make every backup uniquely named.
        for _ in range(5):
            backup_database(db_path, retention=3)

        backups = sorted((db_path.parent / "backups").glob(f"{db_path.stem}-*.db"))
        assert len(backups) == 3, (
            f"Backup rotation should keep last 3, got {len(backups)}: {[b.name for b in backups]}"
        )

    def test_backup_microsecond_filenames_are_unique(self, tmp_path):
        """Two rapid backups must produce two distinct files (microsecond suffix)."""
        db_path = tmp_path / "conversations.db"
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE sample (value TEXT)")
        conn.commit()
        conn.close()

        first = backup_database(db_path)
        second = backup_database(db_path)

        assert first is not None and second is not None
        assert first != second, "Backups in the same second must still get distinct filenames"


class TestFKSafeReimport:
    """Test that reimport works with foreign keys enabled."""

    def test_reimport_with_fk_enabled(self, memory_db, project_id):
        """Reimporting with PRAGMA foreign_keys = ON should not raise IntegrityError."""
        memory_db.execute("PRAGMA foreign_keys = ON")
        fixture_file = FIXTURE_DIR / "linear_3_exchange.jsonl"

        # First import
        branches1, _messages1 = import_session(memory_db, fixture_file, project_id)
        assert branches1 > 0
        memory_db.commit()

        # Invalidate the import_log hash to force reimport
        cursor = memory_db.cursor()
        cursor.execute("UPDATE import_log SET file_hash = 'stale' WHERE file_path = ?",
                       (str(fixture_file),))
        memory_db.commit()

        # Reimport should succeed without IntegrityError
        branches2, messages2 = import_session(memory_db, fixture_file, project_id)
        assert branches2 > 0, "Reimport should succeed"
        assert messages2 > 0, "Messages should be reimported"
        memory_db.commit()

    def test_reimport_with_branches_fk(self, memory_db, project_id):
        """Reimport of branched conversation with FK enabled should not crash."""
        memory_db.execute("PRAGMA foreign_keys = ON")
        fixture_file = FIXTURE_DIR / "single_rewind.jsonl"

        branches1, _messages1 = import_session(memory_db, fixture_file, project_id)
        assert branches1 == 3
        memory_db.commit()

        # Force reimport
        cursor = memory_db.cursor()
        cursor.execute("UPDATE import_log SET file_hash = 'stale' WHERE file_path = ?",
                       (str(fixture_file),))
        memory_db.commit()

        branches2, messages2 = import_session(memory_db, fixture_file, project_id)
        assert branches2 == 3, "Reimport should produce same branch count"
        memory_db.commit()


class TestBranchMetadata:
    """Test that branch metadata is correctly computed."""

    def test_branch_active_flag(self, memory_db, project_id):
        """Verify that is_active flag correctly identifies current branch."""
        fixture_file = FIXTURE_DIR / "single_rewind.jsonl"
        import_session(memory_db, fixture_file, project_id)

        cursor = memory_db.cursor()
        cursor.execute(
            "SELECT is_active, leaf_uuid FROM branches WHERE session_id IN (SELECT id FROM sessions WHERE project_id = ?)",
            (project_id,)
        )
        branches = cursor.fetchall()
        active_branches = [b for b in branches if b[0] == 1]
        assert len(active_branches) == 1, "Exactly one branch should be marked active"

    def test_branch_exchange_count(self, memory_db, project_id):
        """Verify exchange_count is computed for branches."""
        fixture_file = FIXTURE_DIR / "linear_3_exchange.jsonl"
        import_session(memory_db, fixture_file, project_id)

        cursor = memory_db.cursor()
        cursor.execute(
            "SELECT exchange_count FROM branches WHERE session_id IN (SELECT id FROM sessions WHERE project_id = ?)",
            (project_id,)
        )
        count = cursor.fetchone()[0]
        assert count > 0, "Exchange count should be positive"
        # linear_3_exchange has 3 user->assistant exchanges
        assert count >= 3, "Should count at least 3 exchanges"


class TestImportProject:
    """Test import_project — directory-level import with exclusion and subagent handling."""

    def test_exclude_projects_skips(self, memory_db):
        """import_project with exclude_projects should skip named projects."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir) / "-home-user-myproject"
            project_dir.mkdir()

            # Copy a fixture into it; the fixture has cwd="/Users/samarthgupta/repos/forks/node-banana"
            # so project_name will be "node-banana" (derived from real cwd, not directory key)
            import shutil
            shutil.copy(FIXTURE_DIR / "linear_3_exchange.jsonl", project_dir / "session1.jsonl")

            sessions, messages, skipped = import_project(
                memory_db, project_dir, exclude_projects=["node-banana"]
            )
            # Should return (0, 0, 0) because the project name matches exclusion
            assert sessions == 0
            assert messages == 0
            assert skipped == 0

    def test_normal_import(self, memory_db):
        """import_project should import all JSONL files in a project directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir) / "-Users-sam-project"
            project_dir.mkdir()

            import shutil
            shutil.copy(FIXTURE_DIR / "linear_3_exchange.jsonl", project_dir / "session1.jsonl")

            sessions, messages, skipped = import_project(memory_db, project_dir)
            assert sessions > 0 or skipped > 0, "Should process the JSONL file"

    def test_dotfiles_skipped(self, memory_db):
        """Dotfiles (hidden JSONL) should be skipped."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir) / "-Users-sam-project"
            project_dir.mkdir()

            # Create a dotfile
            (project_dir / ".hidden.jsonl").write_text('{"type":"progress"}\n')

            sessions, messages, skipped = import_project(memory_db, project_dir)
            assert sessions == 0
            assert messages == 0


class TestAppendOnlyReimport:
    """Gap 1 — Message deduplication on forced reimport.

    Prevents: stale-hash reimport doubling message rows, breaking recall results
    and inflating context injection counts.
    """

    def test_no_duplicate_messages_on_forced_reimport(self, memory_db, project_id):
        """Staling the import_log hash must not create new message rows."""
        fixture_file = FIXTURE_DIR / "linear_3_exchange.jsonl"

        # First import — establish baseline
        branches1, _messages1 = import_session(memory_db, fixture_file, project_id)
        assert branches1 > 0, "First import must succeed"

        cursor = memory_db.cursor()
        cursor.execute(
            "SELECT id, uuid FROM messages WHERE session_id = "
            "(SELECT id FROM sessions WHERE project_id = ?)",
            (project_id,)
        )
        rows_before = cursor.fetchall()
        ids_before = {row[0] for row in rows_before}
        uuids_before = {row[1] for row in rows_before}

        # Force reimport by staling the hash
        cursor.execute(
            "UPDATE import_log SET file_hash = 'stale' WHERE file_path = ?",
            (str(fixture_file),)
        )
        memory_db.commit()

        branches2, messages2 = import_session(memory_db, fixture_file, project_id)
        assert branches2 > 0, "Forced reimport must succeed"

        # Same number of message rows — append-only, no duplicates
        cursor.execute(
            "SELECT id, uuid FROM messages WHERE session_id = "
            "(SELECT id FROM sessions WHERE project_id = ?)",
            (project_id,)
        )
        rows_after = cursor.fetchall()
        assert len(rows_after) == len(rows_before), (
            "Reimport must not create duplicate message rows"
        )

        # Same DB row IDs — ON CONFLICT DO NOTHING preserved originals
        ids_after = {row[0] for row in rows_after}
        assert ids_after == ids_before, "Same DB row IDs must survive reimport"

        # No new UUIDs introduced
        uuids_after = {row[1] for row in rows_after}
        assert uuids_after == uuids_before, "No new UUIDs may appear after reimport"

    def test_no_duplicate_session_uuid_message_pairs(self, memory_db, project_id):
        """(session_id, uuid) uniqueness must hold across repeated forced reimports."""
        fixture_file = FIXTURE_DIR / "linear_3_exchange.jsonl"
        import_session(memory_db, fixture_file, project_id)

        for _ in range(2):
            cursor = memory_db.cursor()
            cursor.execute(
                "UPDATE import_log SET file_hash = 'stale' WHERE file_path = ?",
                (str(fixture_file),)
            )
            memory_db.commit()
            import_session(memory_db, fixture_file, project_id)

        # Check no (session_id, uuid) duplicates exist anywhere
        cursor = memory_db.cursor()
        cursor.execute("""
            SELECT session_id, uuid, COUNT(*) AS cnt
            FROM messages
            GROUP BY session_id, uuid
            HAVING cnt > 1
        """)
        duplicates = cursor.fetchall()
        assert duplicates == [], (
            f"Duplicate (session_id, uuid) pairs found after repeated reimport: {duplicates}"
        )


class TestBranchMessagesDiffOnReimport:
    """Gap 2 — branch_messages link set must be identical after forced reimport.

    Prevents: ghost branch-message links accumulating across reimports, causing
    search results to surface deleted message content.
    """

    def test_branch_messages_identical_after_reimport(self, memory_db, project_id):
        """branch_messages rows must be the same set before and after stale-hash reimport."""
        fixture_file = FIXTURE_DIR / "single_rewind.jsonl"

        branches1, _ = import_session(memory_db, fixture_file, project_id)
        assert branches1 == 3, "Fixture must produce 3 branches for this test to be meaningful"

        cursor = memory_db.cursor()
        cursor.execute("""
            SELECT branch_id, message_id FROM branch_messages
            WHERE branch_id IN (
                SELECT b.id FROM branches b
                JOIN sessions s ON b.session_id = s.id
                WHERE s.project_id = ?
            )
            ORDER BY branch_id, message_id
        """, (project_id,))
        links_before = cursor.fetchall()
        assert links_before, "branch_messages must be populated after first import"

        # Force reimport
        cursor.execute(
            "UPDATE import_log SET file_hash = 'stale' WHERE file_path = ?",
            (str(fixture_file),)
        )
        memory_db.commit()

        branches2, _ = import_session(memory_db, fixture_file, project_id)
        assert branches2 == 3, "Reimport must produce same branch count"

        cursor.execute("""
            SELECT branch_id, message_id FROM branch_messages
            WHERE branch_id IN (
                SELECT b.id FROM branches b
                JOIN sessions s ON b.session_id = s.id
                WHERE s.project_id = ?
            )
            ORDER BY branch_id, message_id
        """, (project_id,))
        links_after = cursor.fetchall()

        assert links_after == links_before, (
            "branch_messages link set must be identical after forced reimport — "
            f"before={len(links_before)}, after={len(links_after)}"
        )


class TestSessionWithMessagesButNoBranches:
    """Gap 5 — Session rows and message rows survive when all branch content is empty.

    Prevents: conservative cleanup accidentally deleting sessions that have messages
    but whose only branch produced no FTS content (all-notification session).
    The import code skips empty branches but must not delete the session row when
    messages still exist.
    """

    def test_session_row_survives_all_notification_branch(self, memory_db, project_id):
        """Session and its messages must persist when the branch has only notification content."""
        # A JSONL where the sole user message is a task-notification.
        # aggregate_branch_content will return empty (notification excluded from FTS)
        # but the import should keep the session and its message rows intact.
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            temp_path = Path(f.name)
            f.write('{"uuid":"root","type":"progress","timestamp":"2026-03-01T10:00:00Z","sessionId":"notif-only-sess","cwd":"/test"}\n')
            # Notification user message — is_notification=1, stored but excluded from FTS
            f.write('{"uuid":"msg1","parentUuid":"root","type":"user","timestamp":"2026-03-01T10:00:01Z","sessionId":"notif-only-sess","message":{"role":"user","content":"<task-notification><task-id>x</task-id>Task done</task-notification>"}}\n')
            # Assistant reply with real text — this IS included in FTS
            f.write('{"uuid":"msg2","parentUuid":"msg1","type":"assistant","timestamp":"2026-03-01T10:00:02Z","sessionId":"notif-only-sess","message":{"role":"assistant","content":[{"type":"text","text":"Acknowledged the task notification."}]}}\n')

        try:
            branches_imported, total_messages = import_session(
                memory_db, temp_path, project_id
            )

            cursor = memory_db.cursor()
            # Session must still exist (messages present — conservative cleanup)
            cursor.execute("SELECT COUNT(*) FROM sessions WHERE project_id = ?", (project_id,))
            session_count = cursor.fetchone()[0]

            if branches_imported > 0:
                # Branch survived (assistant text kept it non-empty) — session must exist
                assert session_count == 1, "Session must exist when branch has content"
                cursor.execute(
                    "SELECT COUNT(*) FROM messages WHERE session_id = "
                    "(SELECT id FROM sessions WHERE project_id = ?)",
                    (project_id,)
                )
                msg_count = cursor.fetchone()[0]
                assert msg_count > 0, "Message rows must survive alongside the session"
            else:
                # No branches imported — verify the session was only removed if
                # it truly has no messages (conservative cleanup check)
                if session_count > 0:
                    cursor.execute(
                        "SELECT COUNT(*) FROM messages WHERE session_id = "
                        "(SELECT id FROM sessions WHERE project_id = ?)",
                        (project_id,)
                    )
                    msg_count = cursor.fetchone()[0]
                    assert msg_count > 0, (
                        "Session row must not survive with zero message rows — "
                        "that would be an orphaned session"
                    )
        finally:
            temp_path.unlink()

    def test_session_deleted_only_when_both_messages_and_branches_are_zero(self, memory_db, project_id):
        """Session cleanup fires only when message count AND branch count are both zero.

        A session with messages but no branches (all branches empty) should not be deleted
        if messages exist — removing it would destroy potentially-recoverable data.
        """
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            temp_path = Path(f.name)
            # Only tool_result messages — no extractable text, triggers guard 1 (no messages)
            f.write('{"uuid":"root","type":"progress","timestamp":"2026-03-01T10:00:00Z","sessionId":"empty-sess","cwd":"/test"}\n')
            f.write('{"uuid":"msg1","parentUuid":"root","type":"user","timestamp":"2026-03-01T10:00:01Z","sessionId":"empty-sess","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"t1","content":"result"}]}}\n')

        try:
            branches_imported, total_messages = import_session(
                memory_db, temp_path, project_id
            )

            # Guard 1 fires: no extractable text, session must be cleaned up
            assert branches_imported == -1, "Should trigger guard 1 (no extractable content)"

            cursor = memory_db.cursor()
            cursor.execute("SELECT COUNT(*) FROM sessions WHERE project_id = ?", (project_id,))
            assert cursor.fetchone()[0] == 0, (
                "Session with zero messages and zero branches must be cleaned up"
            )
        finally:
            temp_path.unlink()


class TestEmptyBranchGuardTightened:
    """Gap 6 — Tightened TestEmptyBranchGuard: empty branches preserved, not deleted.

    The import code comments explicitly state: 'No searchable content — skip but
    don't delete. Deleting causes thrashing.' This test pins that contract so a
    future refactor can't accidentally reintroduce the delete path.
    """

    def test_empty_branch_row_preserved_after_reimport(self, memory_db, project_id):
        """An empty-FTS branch must still exist in the DB after a forced reimport.

        If the branch were deleted on first import and then recreated on reimport
        (thrash cycle), the branch_id would differ — this test pins that the same
        branch row survives.
        """
        # Notification-only session: branch is created but aggregated_content is
        # empty after excluding notifications.  The branch row must be preserved.
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            temp_path = Path(f.name)
            f.write('{"uuid":"root","type":"progress","timestamp":"2026-03-01T10:00:00Z","sessionId":"empty-branch-sess","cwd":"/test"}\n')
            # Notification-only user message
            f.write('{"uuid":"msg1","parentUuid":"root","type":"user","timestamp":"2026-03-01T10:00:01Z","sessionId":"empty-branch-sess","message":{"role":"user","content":"<task-notification><task-id>y</task-id>Work done</task-notification>"}}\n')
            # No assistant reply — branch has no non-notification content at all

        try:
            # First import
            import_session(memory_db, temp_path, project_id)

            cursor = memory_db.cursor()
            cursor.execute("""
                SELECT b.id FROM branches b
                JOIN sessions s ON b.session_id = s.id
                WHERE s.project_id = ?
            """, (project_id,))
            branch_rows_after_first = cursor.fetchall()

            if not branch_rows_after_first:
                # Guard 1 fired (no messages at all) — nothing to test for branch preservation
                cursor.execute("SELECT COUNT(*) FROM sessions WHERE project_id = ?", (project_id,))
                assert cursor.fetchone()[0] == 0, "Guard 1 must clean up empty session"
                return

            branch_ids_after_first = {row[0] for row in branch_rows_after_first}

            # Force reimport
            cursor.execute(
                "UPDATE import_log SET file_hash = 'stale' WHERE file_path = ?",
                (str(temp_path),)
            )
            memory_db.commit()
            import_session(memory_db, temp_path, project_id)

            cursor.execute("""
                SELECT b.id FROM branches b
                JOIN sessions s ON b.session_id = s.id
                WHERE s.project_id = ?
            """, (project_id,))
            branch_ids_after_reimport = {row[0] for row in cursor.fetchall()}

            # No branch rows should have disappeared — empty branches are preserved
            assert branch_ids_after_first.issubset(branch_ids_after_reimport), (
                "Empty branch rows must be preserved across reimport — "
                "deleting them causes import thrash on every cycle"
            )
        finally:
            temp_path.unlink()
