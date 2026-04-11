"""Integration tests for sync_current.py hook."""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures"

# Add hooks directory to sys.path to import sync_current
HOOKS_DIR = Path(__file__).parent.parent.parent / "plugins" / "claude-memory" / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from sync_current import sync_session, validate_session_id
from memory_lib.db import SCHEMA, _migrate_columns


@pytest.fixture
def memory_db_with_project():
    """In-memory SQLite database with schema and a test project."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA)
    conn.commit()
    _migrate_columns(conn)

    # Create a test project
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO projects (path, key, name) VALUES (?, ?, ?)",
        ("/test/project", "-test-project", "project")
    )
    cursor.execute("SELECT id FROM projects WHERE path = ?", ("/test/project",))
    project_id = cursor.fetchone()[0]

    conn.commit()
    yield conn, project_id
    conn.close()


class TestSyncSessionCreatesBranches:
    """Test that sync_session creates branches correctly from JSONL fixture."""

    def test_sync_session_creates_branches(self, memory_db_with_project):
        """sync_session should create branches from a fixture with rewinding."""
        conn, project_id = memory_db_with_project
        fixture_path = FIXTURE_DIR / "single_rewind.jsonl"

        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)

            # Sync the session
            new_count = sync_session(conn, fixture_path, project_dir)

            # Verify messages were added
            assert new_count > 0, "Should have added messages"

            # Verify a session was created
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM sessions")
            assert cursor.fetchone()[0] == 1

            # Verify branches were created
            cursor.execute("SELECT COUNT(*) FROM branches")
            branch_count = cursor.fetchone()[0]
            assert branch_count > 0, "Should have created at least one branch"

            # Verify branch_messages were created
            cursor.execute("SELECT COUNT(*) FROM branch_messages")
            branch_msg_count = cursor.fetchone()[0]
            assert branch_msg_count > 0, "Should have linked messages to branches"

            # Verify only one active branch
            cursor.execute("SELECT COUNT(*) FROM branches WHERE is_active = 1")
            assert cursor.fetchone()[0] == 1, "Should have exactly one active branch"

    def test_sync_session_populates_branch_content(self, memory_db_with_project):
        """Aggregated content should be populated after sync."""
        conn, project_id = memory_db_with_project
        fixture_path = FIXTURE_DIR / "single_rewind.jsonl"

        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            sync_session(conn, fixture_path, project_dir)

            cursor = conn.cursor()
            cursor.execute(
                "SELECT aggregated_content FROM branches WHERE is_active = 1"
            )
            row = cursor.fetchone()
            assert row is not None
            content = row[0]
            assert content, "Active branch should have aggregated content"

    def test_sync_session_populates_context_summary(self, memory_db_with_project):
        """Context summary and summary_version should be populated after sync."""
        conn, project_id = memory_db_with_project
        fixture_path = FIXTURE_DIR / "single_rewind.jsonl"

        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            sync_session(conn, fixture_path, project_dir)
            conn.commit()

            cursor = conn.cursor()
            cursor.execute(
                "SELECT context_summary, summary_version FROM branches WHERE is_active = 1"
            )
            row = cursor.fetchone()
            assert row is not None
            summary, version = row
            assert summary, "Active branch should have context_summary"
            assert version == 2, "summary_version should be 2 after sync"
            assert "### Session:" in summary
            assert "/recall-conversations" in summary


class TestSyncSessionUpdatesExisting:
    """Test that syncing the same session twice updates rather than duplicates."""

    def test_sync_session_updates_existing(self, memory_db_with_project):
        """Syncing the same session twice should update, not duplicate messages.

        Verifies both the Python-level dedup (existing_uuids set check)
        and the overall idempotency of sync_session.
        """
        conn, project_id = memory_db_with_project
        fixture_path = FIXTURE_DIR / "single_rewind.jsonl"

        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)

            # First sync
            new_count_1 = sync_session(conn, fixture_path, project_dir)
            conn.commit()
            assert new_count_1 > 0, "First sync should add messages"

            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM messages")
            msg_count_1 = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM sessions")
            session_count_1 = cursor.fetchone()[0]

            # Record branch structure after first sync
            cursor.execute("SELECT id, leaf_uuid, is_active FROM branches ORDER BY id")
            branches_1 = cursor.fetchall()

            # Record message UUIDs (these are what the Python-level dedup tracks)
            cursor.execute("SELECT uuid FROM messages WHERE uuid IS NOT NULL ORDER BY uuid")
            uuids_1 = [row[0] for row in cursor.fetchall()]
            assert len(uuids_1) > 0, "Messages should have UUIDs for dedup tracking"

            # Second sync (same session)
            new_count_2 = sync_session(conn, fixture_path, project_dir)
            conn.commit()

            cursor.execute("SELECT COUNT(*) FROM messages")
            msg_count_2 = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM sessions")
            session_count_2 = cursor.fetchone()[0]

            # Record UUIDs after second sync
            cursor.execute("SELECT uuid FROM messages WHERE uuid IS NOT NULL ORDER BY uuid")
            uuids_2 = [row[0] for row in cursor.fetchall()]

            # Session count should not increase
            assert session_count_2 == session_count_1, "Session count should not increase"

            # Message count should be the same (no duplicates)
            assert msg_count_2 == msg_count_1, "Messages should not be duplicated"

            # Second sync should have zero new messages — this proves the Python-level
            # existing_uuids check works, because the code loads existing UUIDs into a
            # set and skips them before reaching the SQL INSERT
            assert new_count_2 == 0, "Second sync should add no new messages"

            # UUID set should be identical (same messages, no extras)
            assert uuids_1 == uuids_2, "Message UUID set should be unchanged"

            # Branch structure should be preserved (updated, not recreated)
            cursor.execute("SELECT id, leaf_uuid, is_active FROM branches ORDER BY id")
            branches_2 = cursor.fetchall()
            assert len(branches_2) == len(branches_1), "Branch count should be unchanged"
            assert [b[1] for b in branches_2] == [b[1] for b in branches_1], \
                "Branch leaf_uuids should be unchanged"


class TestValidateSessionIdValid:
    """Test that validate_session_id accepts valid UUIDs."""

    def test_validate_session_id_lowercase(self):
        """Should accept lowercase UUID format."""
        session_id = "016e1f0d-cff2-4552-9e21-43833c9a468e"
        assert validate_session_id(session_id) is True

    def test_validate_session_id_uppercase(self):
        """Should accept uppercase UUID format."""
        session_id = "016E1F0D-CFF2-4552-9E21-43833C9A468E"
        assert validate_session_id(session_id) is True

    def test_validate_session_id_mixed_case(self):
        """Should accept mixed case UUID format."""
        session_id = "016e1F0d-CfF2-4552-9E21-43833c9A468e"
        assert validate_session_id(session_id) is True


class TestValidateSessionIdRejectsTraversal:
    """Test that validate_session_id rejects path traversal and invalid formats."""

    def test_validate_session_id_rejects_path_traversal(self):
        """Should reject path traversal attempts."""
        assert validate_session_id("../etc/passwd") is False

    def test_validate_session_id_rejects_empty_string(self):
        """Should reject empty string."""
        assert validate_session_id("") is False

    def test_validate_session_id_rejects_non_uuid(self):
        """Should reject non-UUID formats."""
        assert validate_session_id("not-a-uuid") is False

    def test_validate_session_id_rejects_partial_uuid(self):
        """Should reject partial UUIDs."""
        assert validate_session_id("016e1f0d-cff2-4552-9e21") is False

    def test_validate_session_id_rejects_sql_injection(self):
        """Should reject SQL injection patterns."""
        assert validate_session_id("' OR '1'='1") is False

    def test_validate_session_id_rejects_none(self):
        """Should reject None (edge case)."""
        assert validate_session_id(None) is False

    def test_validate_session_id_rejects_uuid_with_extra(self):
        """Should reject UUID with extra characters."""
        assert validate_session_id("016e1f0d-cff2-4552-9e21-43833c9a468e-extra") is False


class TestSyncBranchMessagesDiff:
    """Test that sync_session's branch_messages diff is stable across repeated syncs.

    Prevents: ghost branch-message links accumulating on every PostToolUse turn,
    which would cause search to surface deleted/stale message content and bloat
    branch_messages with duplicate rows that survive until manual DB repair.
    """

    def test_branch_messages_stable_on_resync(self, memory_db_with_project):
        """branch_messages row set must be identical after a second sync of the same session."""
        conn, _ = memory_db_with_project
        fixture_path = FIXTURE_DIR / "single_rewind.jsonl"

        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)

            # First sync — populate branches and branch_messages
            sync_session(conn, fixture_path, project_dir)
            conn.commit()

            cursor = conn.cursor()
            cursor.execute(
                "SELECT branch_id, message_id FROM branch_messages ORDER BY branch_id, message_id"
            )
            links_after_first = cursor.fetchall()
            assert links_after_first, "branch_messages must be populated after first sync"

            # Second sync — same file, same session; the diff should be a no-op
            sync_session(conn, fixture_path, project_dir)
            conn.commit()

            cursor.execute(
                "SELECT branch_id, message_id FROM branch_messages ORDER BY branch_id, message_id"
            )
            links_after_second = cursor.fetchall()

            assert links_after_second == links_after_first, (
                "branch_messages link set must be identical after resync — "
                f"before={len(links_after_first)}, after={len(links_after_second)}"
            )

    def test_no_duplicate_branch_messages_on_repeated_sync(self, memory_db_with_project):
        """Repeated syncs must never produce duplicate (branch_id, message_id) pairs."""
        conn, _ = memory_db_with_project
        fixture_path = FIXTURE_DIR / "single_rewind.jsonl"

        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)

            for _ in range(3):
                sync_session(conn, fixture_path, project_dir)
                conn.commit()

            cursor = conn.cursor()
            cursor.execute("""
                SELECT branch_id, message_id, COUNT(*) AS cnt
                FROM branch_messages
                GROUP BY branch_id, message_id
                HAVING cnt > 1
            """)
            duplicates = cursor.fetchall()
            assert not duplicates, (
                f"Duplicate (branch_id, message_id) pairs found after 3 syncs: {duplicates}"
            )
