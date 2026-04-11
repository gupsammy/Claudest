"""Tests for memory-context.py — session selection and context injection."""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path
from uuid import uuid4


# Load memory-context.py as a module (hyphen in filename)
HOOKS_DIR = Path(__file__).resolve().parents[2] / "plugins" / "claude-memory" / "hooks"
sys.path.insert(0, str(HOOKS_DIR.parent / "skills" / "recall-conversations" / "scripts"))

memory_context_path = HOOKS_DIR / "memory-context.py"
spec = importlib.util.spec_from_file_location("memory_context", memory_context_path)
memory_context = importlib.util.module_from_spec(spec)
spec.loader.exec_module(memory_context)

select_sessions = memory_context.select_sessions
build_context = memory_context.build_context
_build_fallback_context = memory_context._build_fallback_context


class TestSessionSelection:
    """Test the exchange-count based session selection algorithm."""

    def _insert_project(self, memory_db: sqlite3.Connection, key: str) -> int:
        """Helper to insert a project and return its ID."""
        cursor = memory_db.cursor()
        cursor.execute(
            "INSERT INTO projects (path, key, name) VALUES (?, ?, ?)",
            (f"/home/user/{key}", key, key.replace("-", "_"))
        )
        memory_db.commit()
        return cursor.lastrowid

    def _insert_session(
        self,
        memory_db: sqlite3.Connection,
        project_id: int,
        uuid: str | None = None,
        parent_session_id: int | None = None,
        git_branch: str = "main"
    ) -> int:
        """Helper to insert a session and return its ID."""
        cursor = memory_db.cursor()
        uuid = uuid or str(uuid4())
        cursor.execute(
            "INSERT INTO sessions (uuid, project_id, parent_session_id, git_branch) VALUES (?, ?, ?, ?)",
            (uuid, project_id, parent_session_id, git_branch)
        )
        memory_db.commit()
        return cursor.lastrowid

    def _insert_branch(
        self,
        memory_db: sqlite3.Connection,
        session_id: int,
        exchange_count: int,
        is_active: int = 1
    ) -> int:
        """Helper to insert a branch and return its ID."""
        cursor = memory_db.cursor()
        leaf_uuid = str(uuid4())
        cursor.execute(
            "INSERT INTO branches (session_id, leaf_uuid, exchange_count, is_active, started_at, ended_at) "
            "VALUES (?, ?, ?, ?, datetime('now', '-1 hour'), datetime('now'))",
            (session_id, leaf_uuid, exchange_count, is_active)
        )
        memory_db.commit()
        return cursor.lastrowid

    def _insert_message(
        self,
        memory_db: sqlite3.Connection,
        session_id: int,
        role: str,
        content: str,
        is_notification: int = 0
    ) -> int:
        """Helper to insert a message and return its ID."""
        cursor = memory_db.cursor()
        cursor.execute(
            "INSERT INTO messages (session_id, role, content, is_notification, timestamp) VALUES (?, ?, ?, ?, datetime('now'))",
            (session_id, role, content, is_notification)
        )
        memory_db.commit()
        return cursor.lastrowid

    def _link_message_to_branch(
        self,
        memory_db: sqlite3.Connection,
        branch_id: int,
        message_id: int
    ):
        """Helper to link a message to a branch."""
        cursor = memory_db.cursor()
        cursor.execute(
            "INSERT INTO branch_messages (branch_id, message_id) VALUES (?, ?)",
            (branch_id, message_id)
        )
        memory_db.commit()

    def test_select_sessions_skips_single_exchange(self, memory_db: sqlite3.Connection):
        """Sessions with exchange_count=1 should be excluded."""
        project_id = self._insert_project(memory_db, "test-proj")
        session_id = self._insert_session(memory_db, project_id)
        branch_id = self._insert_branch(memory_db, session_id, exchange_count=1)

        # Add a message to make the branch non-empty
        msg_id = self._insert_message(memory_db, session_id, "user", "test")
        self._link_message_to_branch(memory_db, branch_id, msg_id)

        current_session = str(uuid4())
        selected = select_sessions(memory_db, "test-proj", current_session, max_sessions=5)

        assert selected == []

    def test_select_sessions_collects_short_sessions(self, memory_db: sqlite3.Connection):
        """Multiple 2-exchange sessions should all be collected up to max."""
        project_id = self._insert_project(memory_db, "test-proj")
        current_session = str(uuid4())

        # Create 3 sessions with 2 exchanges each (older to newer for ordering)
        for i in range(3):
            session_id = self._insert_session(memory_db, project_id, uuid=f"sess-{i}")
            branch_id = self._insert_branch(memory_db, session_id, exchange_count=2)
            # Add messages to branch
            for j in range(2):
                msg_id = self._insert_message(memory_db, session_id, "user" if j == 0 else "assistant", f"message-{i}-{j}")
                self._link_message_to_branch(memory_db, branch_id, msg_id)

        selected = select_sessions(memory_db, "test-proj", current_session, max_sessions=5)

        # All 3 short sessions should be selected (oldest first by branch.ended_at DESC)
        assert len(selected) == 3
        assert all(s["exchange_count"] == 2 for s in selected)

    def test_select_sessions_stops_at_long_session(self, memory_db: sqlite3.Connection):
        """Selection should stop after finding a session with >2 exchanges."""
        project_id = self._insert_project(memory_db, "test-proj")
        current_session = str(uuid4())

        # Create: 2 short sessions + 1 long session + 1 more short session (after long)
        for i in range(2):
            session_id = self._insert_session(memory_db, project_id, uuid=f"sess-short-{i}")
            branch_id = self._insert_branch(memory_db, session_id, exchange_count=2)
            msg_id = self._insert_message(memory_db, session_id, "user", f"msg-{i}")
            self._link_message_to_branch(memory_db, branch_id, msg_id)

        # Long session (3 exchanges) - should stop here
        long_session_id = self._insert_session(memory_db, project_id, uuid="sess-long")
        long_branch_id = self._insert_branch(memory_db, long_session_id, exchange_count=3)
        for j in range(2):
            msg_id = self._insert_message(memory_db, long_session_id, "user" if j == 0 else "assistant", f"long-{j}")
            self._link_message_to_branch(memory_db, long_branch_id, msg_id)

        # Another short session after the long one (should be skipped)
        after_session_id = self._insert_session(memory_db, project_id, uuid="sess-after")
        after_branch_id = self._insert_branch(memory_db, after_session_id, exchange_count=2)
        msg_id = self._insert_message(memory_db, after_session_id, "user", "after")
        self._link_message_to_branch(memory_db, after_branch_id, msg_id)

        selected = select_sessions(memory_db, "test-proj", current_session, max_sessions=5)

        # Should get 2 short + 1 long, then stop (the "after" session is skipped)
        assert len(selected) == 3
        assert selected[-1]["exchange_count"] == 3  # Last one is the long session
        # The after session should not be included
        assert not any(s["uuid"] == "sess-after" for s in selected)

    def test_select_sessions_excludes_subagents(self, memory_db: sqlite3.Connection):
        """Sessions with parent_session_id set should be excluded."""
        project_id = self._insert_project(memory_db, "test-proj")
        current_session = str(uuid4())

        # Create a parent session
        parent_session_id = self._insert_session(memory_db, project_id, uuid="parent-sess")
        parent_branch_id = self._insert_branch(memory_db, parent_session_id, exchange_count=3)
        msg_id = self._insert_message(memory_db, parent_session_id, "user", "parent")
        self._link_message_to_branch(memory_db, parent_branch_id, msg_id)

        # Create a subagent session (with parent_session_id set)
        subagent_session_id = self._insert_session(
            memory_db,
            project_id,
            uuid="subagent-sess",
            parent_session_id=parent_session_id
        )
        subagent_branch_id = self._insert_branch(memory_db, subagent_session_id, exchange_count=3)
        msg_id = self._insert_message(memory_db, subagent_session_id, "user", "subagent")
        self._link_message_to_branch(memory_db, subagent_branch_id, msg_id)

        selected = select_sessions(memory_db, "test-proj", current_session, max_sessions=5)

        # Only parent should be selected, subagent excluded
        assert len(selected) == 1
        assert selected[0]["uuid"] == "parent-sess"
        assert not any(s["uuid"] == "subagent-sess" for s in selected)

    def test_select_sessions_excludes_current(self, memory_db: sqlite3.Connection):
        """Current session UUID should be excluded."""
        project_id = self._insert_project(memory_db, "test-proj")
        current_session = str(uuid4())

        # Create current session
        current_session_id = self._insert_session(memory_db, project_id, uuid=current_session)
        current_branch_id = self._insert_branch(memory_db, current_session_id, exchange_count=3)
        msg_id = self._insert_message(memory_db, current_session_id, "user", "current")
        self._link_message_to_branch(memory_db, current_branch_id, msg_id)

        # Create another session (should be selected)
        other_session_id = self._insert_session(memory_db, project_id, uuid="other-sess")
        other_branch_id = self._insert_branch(memory_db, other_session_id, exchange_count=3)
        msg_id = self._insert_message(memory_db, other_session_id, "user", "other")
        self._link_message_to_branch(memory_db, other_branch_id, msg_id)

        selected = select_sessions(memory_db, "test-proj", current_session, max_sessions=5)

        # Only "other" should be selected
        assert len(selected) == 1
        assert selected[0]["uuid"] == "other-sess"
        assert not any(s["uuid"] == current_session for s in selected)

    def test_batch_message_loading(self, memory_db: sqlite3.Connection):
        """Batch query should load correct messages for multiple branches."""
        project_id = self._insert_project(memory_db, "test-proj")
        current_session = str(uuid4())

        # Create 2 sessions with different messages
        for sess_idx in range(2):
            session_id = self._insert_session(memory_db, project_id, uuid=f"sess-{sess_idx}")
            branch_id = self._insert_branch(memory_db, session_id, exchange_count=2)

            # Add 2 messages per branch
            for msg_idx in range(2):
                role = "user" if msg_idx == 0 else "assistant"
                content = f"session-{sess_idx}-message-{msg_idx}"
                msg_id = self._insert_message(memory_db, session_id, role, content)
                self._link_message_to_branch(memory_db, branch_id, msg_id)

        selected = select_sessions(memory_db, "test-proj", current_session, max_sessions=5)

        # Both sessions should be selected
        assert len(selected) == 2

        # Each session should have exactly 2 messages
        for session in selected:
            assert len(session["messages"]) == 2

        # Messages should have correct structure
        for session in selected:
            for msg in session["messages"]:
                assert "role" in msg
                assert "content" in msg
                assert "timestamp" in msg
                assert msg["role"] in ("user", "assistant")

    def test_batch_message_loading_excludes_notifications(self, memory_db: sqlite3.Connection):
        """Batch query should exclude notification messages (is_notification=1)."""
        project_id = self._insert_project(memory_db, "test-proj")
        current_session = str(uuid4())

        session_id = self._insert_session(memory_db, project_id, uuid="sess-test")
        branch_id = self._insert_branch(memory_db, session_id, exchange_count=2)

        # Add 1 regular message and 1 notification message
        msg1_id = self._insert_message(memory_db, session_id, "user", "regular message", is_notification=0)
        msg2_id = self._insert_message(memory_db, session_id, "assistant", "notification message", is_notification=1)

        self._link_message_to_branch(memory_db, branch_id, msg1_id)
        self._link_message_to_branch(memory_db, branch_id, msg2_id)

        selected = select_sessions(memory_db, "test-proj", current_session, max_sessions=5)

        # Should have 1 session
        assert len(selected) == 1
        # But only 1 message (the notification is excluded)
        assert len(selected[0]["messages"]) == 1
        assert selected[0]["messages"][0]["content"] == "regular message"

    def test_select_sessions_max_limit(self, memory_db: sqlite3.Connection):
        """max_sessions parameter should limit the number of 2-exchange sessions collected."""
        project_id = self._insert_project(memory_db, "test-proj")
        current_session = str(uuid4())

        # Create 5 sessions with 2 exchanges each
        for i in range(5):
            session_id = self._insert_session(memory_db, project_id, uuid=f"sess-{i}")
            branch_id = self._insert_branch(memory_db, session_id, exchange_count=2)
            msg_id = self._insert_message(memory_db, session_id, "user", f"msg-{i}")
            self._link_message_to_branch(memory_db, branch_id, msg_id)

        # Request only 3 sessions max
        selected = select_sessions(memory_db, "test-proj", current_session, max_sessions=3)

        # Should get exactly 3 (respects the limit for 2-exchange sessions)
        assert len(selected) == 3

    def test_select_sessions_empty_project(self, memory_db: sqlite3.Connection):
        """Selecting from an empty or non-existent project should return empty list."""
        current_session = str(uuid4())

        # Query non-existent project
        selected = select_sessions(memory_db, "nonexistent-proj", current_session, max_sessions=5)

        assert selected == []

    def test_select_sessions_no_active_branches(self, memory_db: sqlite3.Connection):
        """Sessions with only inactive branches should be skipped, even when
        other sessions with active branches exist."""
        project_id = self._insert_project(memory_db, "test-proj")
        current_session = str(uuid4())

        # Session 1: only has an inactive branch (should be skipped)
        sess1_id = self._insert_session(memory_db, project_id, uuid="sess-inactive")
        branch1_id = self._insert_branch(memory_db, sess1_id, exchange_count=3, is_active=0)
        msg1_id = self._insert_message(memory_db, sess1_id, "user", "inactive msg")
        self._link_message_to_branch(memory_db, branch1_id, msg1_id)

        # Session 2: has an active branch (should be selected)
        sess2_id = self._insert_session(memory_db, project_id, uuid="sess-active")
        branch2_id = self._insert_branch(memory_db, sess2_id, exchange_count=3, is_active=1)
        msg2_id = self._insert_message(memory_db, sess2_id, "user", "active msg")
        self._link_message_to_branch(memory_db, branch2_id, msg2_id)

        selected = select_sessions(memory_db, "test-proj", current_session, max_sessions=5)

        # Only sess-active should be selected; sess-inactive is excluded by is_active filter
        assert len(selected) == 1
        assert selected[0]["uuid"] == "sess-active"


class TestBuildContext:
    """Test build_context() — uses cached summaries or fallback."""

    def test_empty_sessions_returns_empty(self):
        assert build_context([]) == ""

    def test_cached_summary_used_directly(self):
        """When context_summary is present, it should be used as-is."""
        sessions = [{
            "context_summary": "### Session: 2025-01-15 14:30 -> 15:00\nCached content here.",
            "started_at": "2025-01-15T14:30:00Z",
            "ended_at": "2025-01-15T15:00:00Z",
        }]
        result = build_context(sessions)
        assert "Cached content here." in result

    def test_fallback_renders_exchange(self):
        """Without context_summary, fallback should render exchanges."""
        sessions = [{
            "started_at": "2025-01-15T14:30:00Z",
            "ended_at": "2025-01-15T15:00:00Z",
            "files_modified": [],
            "commits": [],
            "messages": [
                {"role": "user", "content": "How do I use pytest?", "timestamp": "2025-01-15T14:30:00Z"},
                {"role": "assistant", "content": "Run pytest from the project root.", "timestamp": "2025-01-15T14:31:00Z"},
            ],
        }]
        result = build_context(sessions)
        assert "### Session:" in result
        assert "How do I use pytest?" in result
        assert "Run pytest from the project root." in result
        assert "User:**" in result
        assert "Assistant:**" in result
        assert "/recall-conversations" in result

    def test_multi_session_separator(self):
        sessions = [
            {
                "started_at": "2025-01-15T14:00:00Z",
                "ended_at": "2025-01-15T14:30:00Z",
                "files_modified": [],
                "commits": [],
                "messages": [
                    {"role": "user", "content": "First session", "timestamp": "2025-01-15T14:00:00Z"},
                    {"role": "assistant", "content": "OK", "timestamp": "2025-01-15T14:01:00Z"},
                ],
            },
            {
                "context_summary": "### Session: Second\nCached.",
            },
        ]
        result = build_context(sessions)
        assert "---" in result
        assert "First session" in result
        assert "Cached." in result

    def test_mixed_cached_and_fallback(self):
        """Sessions can mix cached and uncached."""
        sessions = [
            {"context_summary": "Cached session 1."},
            {
                "started_at": "2025-01-15T15:00:00Z",
                "ended_at": "2025-01-15T15:30:00Z",
                "files_modified": [],
                "commits": [],
                "messages": [
                    {"role": "user", "content": "Uncached session", "timestamp": "2025-01-15T15:00:00Z"},
                    {"role": "assistant", "content": "OK", "timestamp": "2025-01-15T15:01:00Z"},
                ],
            },
        ]
        result = build_context(sessions)
        assert "Cached session 1." in result
        assert "Uncached session" in result


class TestBuildFallbackContext:
    """Test _build_fallback_context() — the fallback renderer for uncached branches."""

    def test_tool_markers_stripped(self):
        session = {
            "started_at": "2025-01-15T14:30:00Z",
            "ended_at": "2025-01-15T15:00:00Z",
            "files_modified": [],
            "commits": [],
            "messages": [
                {"role": "user", "content": "Read the file", "timestamp": "2025-01-15T14:30:00Z"},
                {"role": "assistant", "content": "Here is the content [Tool: Read] of the file [Tool: Bash].", "timestamp": "2025-01-15T14:31:00Z"},
            ],
        }
        result = _build_fallback_context(session)
        assert "[Tool: Read]" not in result
        assert "[Tool: Bash]" not in result
        assert "Here is the content" in result

    def test_no_messages(self):
        session = {
            "started_at": "2025-01-15T14:30:00Z",
            "ended_at": "2025-01-15T15:00:00Z",
            "files_modified": [],
            "commits": [],
            "messages": [],
        }
        result = _build_fallback_context(session)
        assert "### Session:" in result
        assert "User:**" not in result

    def test_files_modified_compact(self):
        session = {
            "started_at": "2025-01-15T14:30:00Z",
            "ended_at": "2025-01-15T15:00:00Z",
            "files_modified": ["/src/main.py", "/src/utils.py"],
            "commits": [],
            "messages": [
                {"role": "user", "content": "Fix it", "timestamp": "2025-01-15T14:30:00Z"},
                {"role": "assistant", "content": "Done.", "timestamp": "2025-01-15T14:31:00Z"},
            ],
        }
        result = _build_fallback_context(session)
        assert "Modified:" in result
        assert "`/src/main.py`" in result

    def test_files_truncated_at_6(self):
        files = [f"/file{i}.py" for i in range(10)]
        session = {
            "started_at": "2025-01-15T14:30:00Z",
            "ended_at": "2025-01-15T15:00:00Z",
            "files_modified": files,
            "commits": [],
            "messages": [
                {"role": "user", "content": "Work", "timestamp": "2025-01-15T14:30:00Z"},
                {"role": "assistant", "content": "Done.", "timestamp": "2025-01-15T14:31:00Z"},
            ],
        }
        result = _build_fallback_context(session)
        assert "+4 more" in result

    def test_commits_rendered(self):
        session = {
            "started_at": "2025-01-15T14:30:00Z",
            "ended_at": "2025-01-15T15:00:00Z",
            "files_modified": [],
            "commits": ["fix: resolve parsing bug"],
            "messages": [
                {"role": "user", "content": "Commit it", "timestamp": "2025-01-15T14:30:00Z"},
                {"role": "assistant", "content": "Committed.", "timestamp": "2025-01-15T14:31:00Z"},
            ],
        }
        result = _build_fallback_context(session)
        assert "Commits:" in result
        assert "fix: resolve parsing bug" in result

    def test_user_without_assistant_response(self):
        session = {
            "started_at": "2025-01-15T14:30:00Z",
            "ended_at": "2025-01-15T15:00:00Z",
            "files_modified": [],
            "commits": [],
            "messages": [
                {"role": "user", "content": "Last question unanswered", "timestamp": "2025-01-15T14:30:00Z"},
            ],
        }
        result = _build_fallback_context(session)
        assert "Last question unanswered" in result
        assert "User:**" in result

    def test_long_session_has_first_and_last(self):
        """Sessions with >8 exchanges should show first 2 + gap + last 6."""
        messages = []
        for i in range(12):
            messages.append({"role": "user", "content": f"Q{i}", "timestamp": f"2025-01-15T10:{i:02d}:00Z"})
            messages.append({"role": "assistant", "content": f"A{i}", "timestamp": f"2025-01-15T10:{i:02d}:30Z"})
        session = {
            "started_at": "2025-01-15T10:00:00Z",
            "ended_at": "2025-01-15T10:12:00Z",
            "exchange_count": 12,
            "files_modified": [],
            "commits": [],
            "messages": messages,
        }
        result = _build_fallback_context(session)
        assert "### Earlier in This Session" in result
        assert "### Where We Left Off" in result
        assert "Q0" in result   # First exchange
        assert "Q1" in result   # Second exchange
        assert "Q11" in result  # Last exchange
        assert "Q6" in result   # First of last 6

    def test_recall_footer_present(self):
        session = {
            "started_at": "2025-01-15T14:30:00Z",
            "ended_at": "2025-01-15T15:00:00Z",
            "files_modified": [],
            "commits": [],
            "messages": [
                {"role": "user", "content": "Hello", "timestamp": "2025-01-15T14:30:00Z"},
                {"role": "assistant", "content": "Hi", "timestamp": "2025-01-15T14:31:00Z"},
            ],
        }
        result = _build_fallback_context(session)
        assert "/recall-conversations" in result
