"""Tests for the --summary retrieval mode and volume/fan-out signal.

Coverage:
1. volume_signal — boundary at 50000, mode-dependent hint direction.
2. format_markdown_session summary branch — renders ### Summary, no ### Conversation;
   empty/blank summary shows placeholder.
3. get_recent_sessions(summary=True/False) — via memory_db fixture.
4. JSON meta flag truth table (is_large × summary_mode → 4 states).
5. search_sessions(summary=True/False) — LIKE fallback path.
"""

from __future__ import annotations

import sqlite3

import pytest

from memory_lib.cli_common import FANOUT_SUGGEST_CHARS, volume_flags, volume_signal
from memory_lib.formatting import format_markdown_session
from recent_chats import get_recent_sessions
from search_conversations import search_sessions


# ---------------------------------------------------------------------------
# Helpers shared across tests
# ---------------------------------------------------------------------------

def _seed_project(conn: sqlite3.Connection, name: str = "testproj") -> int:
    """Insert a project row and return its id."""
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO projects (path, key, name) VALUES (?, ?, ?)",
        (f"/{name}", f"-{name}", name),
    )
    conn.commit()
    return cursor.lastrowid


def _seed_session(conn: sqlite3.Connection, project_id: int, uuid: str = "sess-1") -> int:
    """Insert a session row and return its id."""
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO sessions (uuid, project_id) VALUES (?, ?)",
        (uuid, project_id),
    )
    conn.commit()
    return cursor.lastrowid


def _seed_branch(
    conn: sqlite3.Connection,
    session_id: int,
    context_summary: str | None = "A precomputed summary.",
    aggregated_content: str = "some content",
    leaf_uuid: str = "leaf-1",
) -> int:
    """Insert a branch row and return its id."""
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO branches
            (session_id, leaf_uuid, is_active, exchange_count,
             started_at, ended_at, context_summary, aggregated_content)
        VALUES
            (?, ?, 1, 3,
             datetime('now', '-2 hours'), datetime('now', '-1 hour'), ?, ?)
        """,
        (session_id, leaf_uuid, context_summary, aggregated_content),
    )
    conn.commit()
    return cursor.lastrowid


def _seed_message(
    conn: sqlite3.Connection,
    session_id: int,
    branch_id: int,
    role: str = "user",
    content: str = "Hello world",
) -> int:
    """Insert a message and link it to a branch; return message id."""
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, ?, ?, datetime('now'))",
        (session_id, role, content),
    )
    msg_id = cursor.lastrowid
    cursor.execute(
        "INSERT INTO branch_messages (branch_id, message_id) VALUES (?, ?)",
        (branch_id, msg_id),
    )
    conn.commit()
    return msg_id


# ---------------------------------------------------------------------------
# 1. volume_signal — boundary and hint direction
# ---------------------------------------------------------------------------

class TestVolumeSignal:
    """volume_signal classifies output size mechanically; no guessing or I/O."""

    def test_just_under_threshold_is_not_large(self):
        """49 999 chars must not trigger the large signal."""
        is_large, hint = volume_signal(FANOUT_SUGGEST_CHARS - 1, False)
        assert is_large is False
        assert hint == ""

    def test_at_threshold_is_not_large(self):
        """Exactly 50 000 chars falls on the non-large side (strict >)."""
        is_large, hint = volume_signal(FANOUT_SUGGEST_CHARS, False)
        assert is_large is False
        assert hint == ""

    def test_just_over_threshold_is_large(self):
        """50 001 chars must trigger the large signal."""
        is_large, hint = volume_signal(FANOUT_SUGGEST_CHARS + 1, False)
        assert is_large is True
        assert hint != ""

    def test_full_mode_hint_suggests_summary(self):
        """In full mode, the hint must steer toward --summary, not fan-out."""
        _, hint = volume_signal(FANOUT_SUGGEST_CHARS + 1, False)
        assert "--summary" in hint
        # Must NOT tell the caller to fan out first (that's the summary-mode hint)
        assert "fan out" not in hint.split("--summary")[0]

    def test_summary_mode_hint_suggests_fanout(self):
        """In summary mode, even summaries are large — hint must steer toward fan-out."""
        _, hint = volume_signal(FANOUT_SUGGEST_CHARS + 1, True)
        assert "fan out" in hint
        assert "exceeds" in hint

    def test_small_output_returns_empty_hint_in_summary_mode(self):
        """Small output is quiet regardless of summary mode."""
        is_large, hint = volume_signal(FANOUT_SUGGEST_CHARS - 1, True)
        assert is_large is False
        assert hint == ""

    def test_zero_chars_is_not_large(self):
        """Zero chars — empty result set — must never trigger the signal."""
        is_large, hint = volume_signal(0, False)
        assert is_large is False
        assert hint == ""


# ---------------------------------------------------------------------------
# 2. format_markdown_session — summary branch
# ---------------------------------------------------------------------------

class TestFormatMarkdownSessionSummaryBranch:
    """format_markdown_session(session) with a 'summary' key takes a fast path."""

    def _base_session(self, **overrides) -> dict:
        s = {
            "uuid": "abcdef12-0000-0000-0000-000000000000",
            "project": "myproj",
            "started_at": "2025-03-10T10:00:00Z",
            "messages": [{"role": "user", "content": "Should not appear"}],
        }
        s.update(overrides)
        return s

    def test_summary_key_emits_summary_heading(self):
        """When session has 'summary', output contains ### Summary."""
        md = format_markdown_session(self._base_session(summary="Got it."))
        assert "### Summary" in md

    def test_summary_key_skips_conversation_heading(self):
        """The ### Conversation block must be absent when summary key is present."""
        md = format_markdown_session(self._base_session(summary="Got it."))
        assert "### Conversation" not in md

    def test_summary_key_skips_message_content(self):
        """Message rows must not be rendered when summary key is present."""
        md = format_markdown_session(self._base_session(summary="Got it."))
        assert "Should not appear" not in md

    def test_summary_text_is_present_in_output(self):
        """The literal summary text must appear in the rendered markdown."""
        md = format_markdown_session(self._base_session(summary="Implemented caching."))
        assert "Implemented caching." in md

    def test_empty_string_summary_renders_placeholder(self):
        """Empty string summary → placeholder, not a blank block."""
        md = format_markdown_session(self._base_session(summary=""))
        assert "summary unavailable" in md
        assert "### Summary" in md

    def test_whitespace_only_summary_renders_placeholder(self):
        """Whitespace-only summary is treated as blank; placeholder is shown."""
        md = format_markdown_session(self._base_session(summary="   \n  "))
        assert "summary unavailable" in md

    def test_placeholder_does_not_contain_user_content(self):
        """Placeholder must not accidentally include message content."""
        md = format_markdown_session(self._base_session(summary=""))
        assert "Should not appear" not in md

    def test_no_summary_key_still_renders_conversation(self):
        """Absence of 'summary' key falls through to the normal Conversation path."""
        session = self._base_session()
        del session["messages"]
        session["messages"] = [{"role": "user", "content": "Normal message"}]
        md = format_markdown_session(session)
        assert "### Conversation" in md
        assert "Normal message" in md

    def test_output_ends_with_separator(self):
        """Both summary and full paths end with the --- separator."""
        md_sum = format_markdown_session(self._base_session(summary="x"))
        md_full = format_markdown_session(self._base_session())
        assert md_sum.endswith("---\n")
        assert md_full.endswith("---\n")


# ---------------------------------------------------------------------------
# 3. get_recent_sessions — summary=True vs default
# ---------------------------------------------------------------------------

class TestGetRecentSessionsSummaryMode:
    """Verify summary routing in get_recent_sessions against the in-memory DB."""

    def _setup_one_session(
        self,
        memory_db: sqlite3.Connection,
        context_summary: str | None = "Precomputed summary text.",
    ) -> None:
        proj_id = _seed_project(memory_db)
        sess_id = _seed_session(memory_db, proj_id)
        branch_id = _seed_branch(memory_db, sess_id, context_summary=context_summary)
        _seed_message(memory_db, sess_id, branch_id, content="Actual message content")

    def test_default_mode_emits_messages(self, memory_db: sqlite3.Connection):
        """Without summary=True, session data contains 'messages' with content."""
        self._setup_one_session(memory_db)
        results = get_recent_sessions(
            memory_db, limit=5, sort_order="desc",
            before=None, after=None, projects=["testproj"],
            verbose=False, include_notifications=False,
        )
        assert len(results) == 1
        assert "messages" in results[0]
        assert results[0]["messages"][0]["content"] == "Actual message content"

    def test_default_mode_omits_summary_key(self, memory_db: sqlite3.Connection):
        """Full mode must not include a 'summary' key in the result dict."""
        self._setup_one_session(memory_db)
        results = get_recent_sessions(
            memory_db, limit=5, sort_order="desc",
            before=None, after=None, projects=["testproj"],
            verbose=False, include_notifications=False,
        )
        assert "summary" not in results[0]

    def test_summary_mode_emits_precomputed_text(self, memory_db: sqlite3.Connection):
        """summary=True must set 'summary' to the branch's context_summary."""
        self._setup_one_session(memory_db, context_summary="Precomputed summary text.")
        results = get_recent_sessions(
            memory_db, limit=5, sort_order="desc",
            before=None, after=None, projects=["testproj"],
            verbose=False, include_notifications=False,
            summary=True,
        )
        assert len(results) == 1
        assert results[0]["summary"] == "Precomputed summary text."

    def test_summary_mode_skips_message_query(self, memory_db: sqlite3.Connection):
        """summary=True must not include a 'messages' key — no per-message query fires."""
        self._setup_one_session(memory_db)
        results = get_recent_sessions(
            memory_db, limit=5, sort_order="desc",
            before=None, after=None, projects=["testproj"],
            verbose=False, include_notifications=False,
            summary=True,
        )
        assert "messages" not in results[0]

    def test_null_context_summary_becomes_empty_string(self, memory_db: sqlite3.Connection):
        """NULL context_summary in DB → 'summary' key is empty string, not None or crash."""
        self._setup_one_session(memory_db, context_summary=None)
        results = get_recent_sessions(
            memory_db, limit=5, sort_order="desc",
            before=None, after=None, projects=["testproj"],
            verbose=False, include_notifications=False,
            summary=True,
        )
        assert results[0]["summary"] == ""

    def test_summary_mode_result_is_json_serialisable(self, memory_db: sqlite3.Connection):
        """Summary dicts must round-trip through JSON (no non-serialisable types)."""
        import json
        self._setup_one_session(memory_db)
        results = get_recent_sessions(
            memory_db, limit=5, sort_order="desc",
            before=None, after=None, projects=["testproj"],
            verbose=False, include_notifications=False,
            summary=True,
        )
        # Should not raise
        json.dumps(results)


# ---------------------------------------------------------------------------
# 4. JSON meta flag truth table  (is_large × summary_mode → 4 states)
#
# Seam note: both recall scripts build their JSON meta from the shared
# cli_common.volume_flags() helper. This test calls that same helper directly,
# so it guards the production derivation (not a copy). Invoking main() would add
# process-level mocking (DB path, argparse, stdout capture) without exercising
# more logic than volume_flags() already exposes.
# ---------------------------------------------------------------------------

class TestJsonMetaFlagTruthTable:
    """Pin the 4-state flag derivation that drives agent routing decisions."""

    def _flags(self, output_chars: int, summary_mode: bool) -> tuple[bool, bool]:
        """Call the SAME shared helper both recall scripts use to build JSON meta,
        so this test guards production rather than a copy of the logic."""
        m = volume_flags(output_chars, summary_mode)
        return m["summary_suggested"], m["fanout_suggested"]

    def test_small_full_both_false(self):
        """Small volume, full mode → neither flag set."""
        summary_suggested, fanout_suggested = self._flags(FANOUT_SUGGEST_CHARS, False)
        assert summary_suggested is False
        assert fanout_suggested is False

    def test_large_full_summary_suggested(self):
        """Large volume, full mode → summary_suggested=True, fanout_suggested=False."""
        summary_suggested, fanout_suggested = self._flags(FANOUT_SUGGEST_CHARS + 1, False)
        assert summary_suggested is True
        assert fanout_suggested is False

    def test_large_summary_fanout_suggested(self):
        """Large volume, summary mode → fanout_suggested=True, summary_suggested=False."""
        summary_suggested, fanout_suggested = self._flags(FANOUT_SUGGEST_CHARS + 1, True)
        assert summary_suggested is False
        assert fanout_suggested is True

    def test_small_summary_both_false(self):
        """Small volume, summary mode → neither flag set (summary already helped)."""
        summary_suggested, fanout_suggested = self._flags(FANOUT_SUGGEST_CHARS, True)
        assert summary_suggested is False
        assert fanout_suggested is False

    def test_flags_are_mutually_exclusive_when_large(self):
        """summary_suggested and fanout_suggested can never both be True."""
        for summary_mode in (False, True):
            ss, fs = self._flags(FANOUT_SUGGEST_CHARS + 1, summary_mode)
            assert not (ss and fs), (
                f"Both flags True for summary_mode={summary_mode} — violates mutual exclusivity"
            )


# ---------------------------------------------------------------------------
# 5. search_sessions — summary parity (LIKE fallback path)
# ---------------------------------------------------------------------------

class TestSearchSessionsSummaryMode:
    """search_sessions(summary=True) routes to context_summary, not messages."""

    def _setup_searchable_session(self, memory_db: sqlite3.Connection) -> None:
        proj_id = _seed_project(memory_db, name="searchproj")
        sess_id = _seed_session(memory_db, proj_id, uuid="search-sess-1")
        _seed_branch(
            memory_db, sess_id,
            context_summary="Search precomputed summary.",
            aggregated_content="findable keyword here",
        )
        _seed_message(memory_db, sess_id, _get_last_branch_id(memory_db), content="findable keyword here")

    def test_full_mode_returns_messages(self, memory_db: sqlite3.Connection):
        """LIKE search without summary=True returns messages, not summary."""
        self._setup_searchable_session(memory_db)
        results = search_sessions(
            memory_db, query="findable", fts_level=None,
            limit=5, projects=["searchproj"],
            verbose=False, include_notifications=False,
            summary=False,
        )
        assert len(results) == 1
        assert "messages" in results[0]
        assert "summary" not in results[0]

    def test_summary_mode_returns_context_summary(self, memory_db: sqlite3.Connection):
        """LIKE search with summary=True returns context_summary instead of messages."""
        self._setup_searchable_session(memory_db)
        results = search_sessions(
            memory_db, query="findable", fts_level=None,
            limit=5, projects=["searchproj"],
            verbose=False, include_notifications=False,
            summary=True,
        )
        assert len(results) == 1
        assert results[0]["summary"] == "Search precomputed summary."
        assert "messages" not in results[0]

    def test_summary_mode_null_db_summary_becomes_empty_string(self, memory_db: sqlite3.Connection):
        """NULL context_summary in DB → empty string, not None or crash."""
        proj_id = _seed_project(memory_db, name="searchproj2")
        sess_id = _seed_session(memory_db, proj_id, uuid="search-sess-null")
        _seed_branch(
            memory_db, sess_id,
            context_summary=None,
            aggregated_content="unique keyword zxqwerty",
            leaf_uuid="leaf-null",
        )
        _seed_message(memory_db, sess_id, _get_last_branch_id(memory_db), content="unique keyword zxqwerty")
        results = search_sessions(
            memory_db, query="unique keyword zxqwerty", fts_level=None,
            limit=5, projects=["searchproj2"],
            verbose=False, include_notifications=False,
            summary=True,
        )
        assert len(results) == 1
        assert results[0]["summary"] == ""


def _get_last_branch_id(conn: sqlite3.Connection) -> int:
    """Return the most recently inserted branch id."""
    return conn.execute("SELECT MAX(id) FROM branches").fetchone()[0]


# ---------------------------------------------------------------------------
# 6. Regression: unmigrated DB missing the context_summary column.
#    Both PR reviewers flagged that an unconditional `SELECT b.context_summary`
#    crashes a normal recall on a DB created before the migration. recall must
#    degrade gracefully: full mode works, summary mode falls back to "".
# ---------------------------------------------------------------------------

class TestMissingContextSummaryColumn:
    """recall must not fail with 'no such column' on a pre-migration database."""

    def _seed_then_drop_column(self, conn: sqlite3.Connection) -> None:
        proj_id = _seed_project(conn, name="oldproj")
        sess_id = _seed_session(conn, proj_id, uuid="old-sess-1")
        branch_id = _seed_branch(
            conn, sess_id,
            context_summary="will be dropped",
            aggregated_content="legacy aggregated content",
        )
        _seed_message(conn, sess_id, branch_id, content="legacy message")
        # Simulate a DB created before the context_summary migration ran.
        conn.execute("ALTER TABLE branches DROP COLUMN context_summary")
        conn.commit()

    def test_recent_full_mode_survives_missing_column(self, memory_db: sqlite3.Connection):
        """A normal (non-summary) recall must not crash on a pre-migration DB."""
        self._seed_then_drop_column(memory_db)
        results = get_recent_sessions(
            memory_db, limit=5, sort_order="desc",
            before=None, after=None, projects=["oldproj"],
            verbose=False, include_notifications=False,
        )
        assert len(results) == 1
        assert results[0]["messages"][0]["content"] == "legacy message"

    def test_recent_summary_mode_falls_back_to_empty(self, memory_db: sqlite3.Connection):
        """summary=True on a pre-migration DB yields '' rather than crashing."""
        self._seed_then_drop_column(memory_db)
        results = get_recent_sessions(
            memory_db, limit=5, sort_order="desc",
            before=None, after=None, projects=["oldproj"],
            verbose=False, include_notifications=False,
            summary=True,
        )
        assert len(results) == 1
        assert results[0]["summary"] == ""

    def test_search_summary_mode_survives_missing_column(self, memory_db: sqlite3.Connection):
        """LIKE search with summary=True must not crash on a pre-migration DB."""
        self._seed_then_drop_column(memory_db)
        results = search_sessions(
            memory_db, query="legacy", fts_level=None,
            limit=5, projects=["oldproj"],
            verbose=False, include_notifications=False,
            summary=True,
        )
        assert len(results) == 1
        assert results[0]["summary"] == ""
