"""Integration tests for sync_current.py hook."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures"

# Add hooks directory to sys.path to import sync_current
HOOKS_DIR = Path(__file__).parent.parent.parent / "plugins" / "claude-memory" / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from sync_current import (
    CODEX_UNKNOWN_PROJECT_PATH,
    _codex_message_uuid,
    parse_codex_session,
    sync_codex_session,
    sync_session,
    validate_codex_thread_id,
    validate_session_id,
)
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


class TestCodexMinimalSync:
    """Test the minimal Codex Desktop transcript adapter."""

    def test_parse_codex_session_imports_visible_minimal_messages(self):
        fixture_path = FIXTURE_DIR / "codex_minimal.codexlog"

        session_uuid, all_entries, messages, cwd = parse_codex_session(fixture_path)

        assert session_uuid == "019dee22-efcc-7b13-ba1c-f2bc9f3959a3"
        assert cwd == "/Users/samarthgupta/repos/myrepos/claudest"
        assert [m["type"] for m in messages] == ["user", "assistant"]
        assert messages[0]["message"]["content"] == "please remember this codex session"
        assert messages[1]["message"]["content"] == "Recorded from Codex."
        assert "I am checking that now." not in [m["message"]["content"] for m in messages]
        assert all_entries == messages
        assert messages[1]["parentUuid"] == messages[0]["uuid"]

    def test_parse_codex_session_handles_negative_paths(self):
        """Single fixture exercises four edge cases at once.

        Asserts the parser drops:
        - a malformed JSON line (returns 2 visible messages, not 0 or 3)
        - a response_item with content as a raw string (no phase)
        - a response_item with phase=commentary (visible but not final_answer)
        And keeps:
        - the visible user_message ('q')
        - the final_answer assistant message ('a')
        """
        fixture_path = FIXTURE_DIR / "codex_negative.codexlog"

        session_uuid, all_entries, messages, cwd = parse_codex_session(fixture_path)

        # Session metadata must survive even when later lines are malformed.
        assert session_uuid == "019dee22-efcc-7b13-ba1c-f2bc9f3960aa"
        assert cwd == "/Users/samarthgupta/repos/myrepos/claudest"

        # Exactly the user 'q' and assistant 'a' — three lines (malformed,
        # string-content no-phase, commentary) are all dropped.
        assert [m["type"] for m in messages] == ["user", "assistant"]
        assert messages[0]["message"]["content"] == "q"
        assert messages[1]["message"]["content"] == "a"

        # Specifically pin: commentary text must not appear anywhere.
        joined = " ".join(m["message"]["content"] for m in messages)
        assert "commentary text" not in joined
        assert "plain string content" not in joined

    def test_sync_codex_session_routes_missing_cwd_to_unknown_project(self, memory_db_with_project):
        """Sessions without session_meta.cwd must NOT create a project per Codex date dir.

        Previously the fallback was filepath.parent which produced project rows
        named '03', '04', '05' — one per date directory under ~/.codex/sessions/.
        Now they all funnel into one (unknown-codex) sentinel project.
        """
        conn, _ = memory_db_with_project
        fixture = FIXTURE_DIR / "codex_no_cwd.codexlog"

        new_count = sync_codex_session(conn, fixture)
        conn.commit()

        assert new_count == 2

        cursor = conn.cursor()
        cursor.execute("SELECT path, name FROM projects WHERE path = ?", (CODEX_UNKNOWN_PROJECT_PATH,))
        row = cursor.fetchone()
        assert row is not None, "Missing-cwd Codex session must route to (unknown-codex)"
        assert row[0] == CODEX_UNKNOWN_PROJECT_PATH

        # No phantom date-named project should exist.
        cursor.execute("SELECT COUNT(*) FROM projects WHERE name IN ('03', '04', '05', 'sessions')")
        assert cursor.fetchone()[0] == 0, "No phantom date-directory projects allowed"

    def test_sync_codex_session_is_idempotent(self, memory_db_with_project):
        conn, project_id = memory_db_with_project
        fixture_path = FIXTURE_DIR / "codex_minimal.codexlog"

        new_count_1 = sync_codex_session(conn, fixture_path)
        conn.commit()
        new_count_2 = sync_codex_session(conn, fixture_path)
        conn.commit()

        assert new_count_1 == 2
        assert new_count_2 == 0

        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM sessions WHERE uuid = ?", ("019dee22-efcc-7b13-ba1c-f2bc9f3959a3",))
        assert cursor.fetchone()[0] == 1

        cursor.execute("SELECT role, content, origin FROM messages ORDER BY timestamp")
        rows = cursor.fetchall()
        assert rows == [
            ("user", "please remember this codex session", "codex"),
            ("assistant", "Recorded from Codex.", "codex"),
        ]

        cursor.execute("SELECT COUNT(*) FROM branches WHERE is_active = 1")
        assert cursor.fetchone()[0] == 1


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


class TestCodexMessageUuidDeterminism:
    """Pin the synthetic UUID generation invariant.

    Same (session_id, raw_kind, ordinal) → same UUID. This is what makes
    Codex re-imports idempotent: ON CONFLICT DO NOTHING on the message PK
    only works because the PK is deterministic.
    """

    def test_same_inputs_produce_same_uuid(self):
        a = _codex_message_uuid("sid-1", "event_msg.user_message", 0)
        b = _codex_message_uuid("sid-1", "event_msg.user_message", 0)
        assert a == b

    def test_different_ordinals_produce_different_uuids(self):
        a = _codex_message_uuid("sid-1", "event_msg.user_message", 0)
        b = _codex_message_uuid("sid-1", "event_msg.user_message", 1)
        assert a != b

    def test_different_kinds_produce_different_uuids(self):
        a = _codex_message_uuid("sid-1", "event_msg.user_message", 0)
        b = _codex_message_uuid("sid-1", "response_item.message.final_answer", 0)
        assert a != b

    def test_different_sessions_produce_different_uuids(self):
        a = _codex_message_uuid("sid-1", "event_msg.user_message", 0)
        b = _codex_message_uuid("sid-2", "event_msg.user_message", 0)
        assert a != b


class TestValidateCodexThreadId:
    """Test the looser Codex thread ID validator.

    Codex Desktop emits UUIDs today, but OpenAI's Assistants API uses
    `thread_<random>` strings. The validator must accept both shapes
    while still blocking path-traversal characters.
    """

    def test_accepts_uuid(self):
        assert validate_codex_thread_id("019dee22-efcc-7b13-ba1c-f2bc9f3959a3")

    def test_accepts_openai_thread_format(self):
        assert validate_codex_thread_id("thread_abc123XYZ_456")

    def test_accepts_simple_alphanumeric(self):
        assert validate_codex_thread_id("a" * 8)
        assert validate_codex_thread_id("a" * 128)

    def test_rejects_too_short(self):
        assert not validate_codex_thread_id("a" * 7)

    def test_rejects_too_long(self):
        assert not validate_codex_thread_id("a" * 129)

    def test_rejects_path_separator(self):
        assert not validate_codex_thread_id("../etc/passwd")
        assert not validate_codex_thread_id("foo/bar")
        assert not validate_codex_thread_id("foo\\bar")

    def test_rejects_dots_for_traversal(self):
        assert not validate_codex_thread_id("..thread_x")
        assert not validate_codex_thread_id("thread.x.y")

    def test_rejects_null_byte(self):
        assert not validate_codex_thread_id("thread_\x00malicious")

    def test_rejects_empty(self):
        assert not validate_codex_thread_id("")

    def test_rejects_none(self):
        assert not validate_codex_thread_id(None)


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


class TestMemorySyncStopHookOutput:
    """Exact-bytes contract tests for memory-sync.py Stop hook output.

    Codex's Stop hook contract is byte-fragile: a trailing newline or a
    `{"continue": true}` payload triggers 'invalid stop hook JSON output'.
    These tests assert the exact bytes for both branches so a future regression
    in JSON formatting fails loudly.
    """

    HOOK_PATH = Path(__file__).resolve().parent.parent.parent / "plugins" / "claude-memory" / "hooks" / "memory-sync.py"

    def _run_hook(self, hook_input: dict, env_extra: dict[str, str] | None = None) -> bytes:
        """Run memory-sync.py with given stdin JSON and env, return raw stdout bytes."""
        env = os.environ.copy()
        # Strip CODEX_THREAD_ID by default so individual tests control it
        env.pop("CODEX_THREAD_ID", None)
        if env_extra:
            env.update(env_extra)
        proc = subprocess.run(
            [sys.executable, str(self.HOOK_PATH)],
            input=json.dumps(hook_input).encode("utf-8"),
            capture_output=True,
            env=env,
            timeout=10,
        )
        assert proc.returncode == 0, f"Hook exited {proc.returncode}: {proc.stderr.decode()!r}"
        return proc.stdout

    def test_codex_branch_via_env_outputs_empty_object(self):
        """CODEX_THREAD_ID set → stdout must be exactly b'{}' (no continue:true)."""
        out = self._run_hook(
            {"session_id": "019dee22-efcc-7b13-ba1c-f2bc9f3959a3"},
            env_extra={"CODEX_THREAD_ID": "019dee22-efcc-7b13-ba1c-f2bc9f3959a3"},
        )
        assert out.strip() == b"{}", (
            f"Codex Stop hook output must be {{}} (any trailing newline OK), got {out!r}"
        )
        # The literal payload must not contain 'continue' — that is what trips Codex.
        assert b"continue" not in out

    def test_codex_branch_via_transcript_path_outputs_empty_object(self):
        """transcript_path under /.codex/sessions/ → stdout must be {} even without env."""
        out = self._run_hook(
            {
                "session_id": "019dee22-efcc-7b13-ba1c-f2bc9f3959a3",
                "transcript_path": "/Users/x/.codex/sessions/2026/05/03/rollout.jsonl",
            },
        )
        assert out.strip() == b"{}", (
            f"transcript_path-detected Codex must also output {{}}, got {out!r}"
        )
        assert b"continue" not in out

    def test_claude_branch_outputs_continue_true(self):
        """No Codex signals → stdout must contain {"continue": true}."""
        out = self._run_hook(
            {
                "session_id": "016e1f0d-cff2-4552-9e21-43833c9a468e",
                "transcript_path": "/Users/x/.claude/projects/-foo/bar.jsonl",
            },
        )
        payload = json.loads(out)
        assert payload == {"continue": True}, (
            f"Claude Stop hook must emit {{'continue': true}}, got {payload!r}"
        )


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

    def test_new_messages_add_branch_links_without_removing_old(self, memory_db_with_project):
        """Growing a session mid-sync must add new branch_messages and keep existing ones.

        Prevents: the diff logic silently dropping links when new messages arrive
        (to_add stays empty or to_remove incorrectly prunes valid links), which
        would cause a mid-session Stop hook to lose newly-synced conversation turns
        from search results until the next full reimport.
        """
        conn, _ = memory_db_with_project
        fixture_lines = (FIXTURE_DIR / "single_rewind.jsonl").read_text().splitlines()

        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)

            # Use a UUID-shaped stem so sync_session can parse it as a session UUID.
            # Both files share the same stem so they map to the same session row in the DB.
            session_stem = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
            partial_path = project_dir / f"{session_stem}.jsonl"
            full_path = project_dir / f"{session_stem}.jsonl"

            # First sync: a truncated session (first 20 raw lines — 2 user/assistant
            # exchanges that survive the text-content filter).
            partial_path.write_text("\n".join(fixture_lines[:20]))
            sync_session(conn, partial_path, project_dir)
            conn.commit()

            cursor = conn.cursor()
            cursor.execute(
                "SELECT branch_id, message_id FROM branch_messages ORDER BY branch_id, message_id"
            )
            links_after_partial = set(cursor.fetchall())
            assert links_after_partial, "branch_messages must be populated after partial sync"

            # Second sync: the full session (all fixture lines — many more exchanges).
            full_path.write_text("\n".join(fixture_lines))
            sync_session(conn, full_path, project_dir)
            conn.commit()

            cursor.execute(
                "SELECT branch_id, message_id FROM branch_messages ORDER BY branch_id, message_id"
            )
            links_after_full = set(cursor.fetchall())

            # All links from the partial sync must still exist (append-only for existing links)
            assert links_after_partial.issubset(links_after_full), (
                "branch_messages from partial sync were removed after full sync — "
                f"missing: {links_after_partial - links_after_full}"
            )

            # The full sync must have added new links (growth was actually recorded)
            assert len(links_after_full) > len(links_after_partial), (
                "branch_messages did not grow after syncing a longer session — "
                f"partial={len(links_after_partial)}, full={len(links_after_full)}"
            )
