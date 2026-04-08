"""Tests for memory_lib.summarizer — context summary extraction and rendering."""

from __future__ import annotations

import json
import sqlite3

import pytest

from memory_lib.summarizer import (
    build_exchange_pairs,
    truncate_mid,
    build_context_summary_json,
    compute_context_summary,
    extract_markers,
    render_context_summary,
)
from memory_lib.db import SCHEMA, _migrate_columns


class TestTruncateMid:
    def test_short_text_unchanged(self):
        text = "Short text."
        assert truncate_mid(text) == text

    def test_long_text_truncated(self):
        text = "A" * 300 + "B" * 100 + "C" * 600
        result = truncate_mid(text)
        assert result.startswith("A" * 300)
        assert "[... truncated ...]" in result
        assert result.endswith("C" * 600)
        assert len(result) < len(text)

    def test_empty_text(self):
        assert truncate_mid("") == ""
        assert truncate_mid(None) is None


class TestBuildExchangePairs:
    def test_simple_exchange(self):
        messages = [
            {"role": "user", "content": "Hello", "timestamp": "2025-01-01T10:00:00Z"},
            {"role": "assistant", "content": "Hi there", "timestamp": "2025-01-01T10:01:00Z"},
        ]
        exchanges = build_exchange_pairs(messages)
        assert len(exchanges) == 1
        assert exchanges[0]["user"] == "Hello"
        assert exchanges[0]["assistant"] == "Hi there"
        assert exchanges[0]["index"] == 0

    def test_multiple_exchanges(self):
        messages = [
            {"role": "user", "content": "Q1", "timestamp": "2025-01-01T10:00:00Z"},
            {"role": "assistant", "content": "A1", "timestamp": "2025-01-01T10:01:00Z"},
            {"role": "user", "content": "Q2", "timestamp": "2025-01-01T10:02:00Z"},
            {"role": "assistant", "content": "A2", "timestamp": "2025-01-01T10:03:00Z"},
        ]
        exchanges = build_exchange_pairs(messages)
        assert len(exchanges) == 2
        assert exchanges[0]["user"] == "Q1"
        assert exchanges[1]["user"] == "Q2"

    def test_tool_markers_stripped(self):
        messages = [
            {"role": "user", "content": "Read file", "timestamp": "2025-01-01T10:00:00Z"},
            {"role": "assistant", "content": "Content [Tool: Read] here", "timestamp": "2025-01-01T10:01:00Z"},
        ]
        exchanges = build_exchange_pairs(messages)
        assert "[Tool: Read]" not in exchanges[0]["assistant"]
        assert "Content" in exchanges[0]["assistant"]

    def test_user_without_response(self):
        messages = [
            {"role": "user", "content": "Last question", "timestamp": "2025-01-01T10:00:00Z"},
        ]
        exchanges = build_exchange_pairs(messages)
        assert len(exchanges) == 1
        assert exchanges[0]["user"] == "Last question"
        assert exchanges[0]["assistant"] == ""


class TestExtractMarkers:
    def test_keyword_decided(self):
        exchanges = [
            {"user": "What approach?", "assistant": "We decided to use Redis for caching instead of memcached.", "index": 0},
        ]
        markers = extract_markers(exchanges)
        assert any(m["type"] == "DECIDED" for m in markers)

    def test_keyword_next_step(self):
        exchanges = [
            {"user": "What's next?", "assistant": "The next step is implementing the API endpoint for users.", "index": 0},
        ]
        markers = extract_markers(exchanges)
        assert any(m["type"] == "NEXT" for m in markers)

    def test_keyword_blocked(self):
        exchanges = [
            {"user": "Status?", "assistant": "We're blocked on the auth service being down in staging.", "index": 0},
        ]
        markers = extract_markers(exchanges)
        assert any(m["type"] == "OPEN" for m in markers)

    def test_keyword_rejected(self):
        exchanges = [
            {"user": "skip the tests for now and just deploy", "assistant": "OK, skipping tests.", "index": 0},
        ]
        markers = extract_markers(exchanges)
        assert any(m["type"] == "REJECTED" for m in markers)

    def test_user_intent_prefix(self):
        exchanges = [
            {"user": "let's refactor the database module to use connection pooling", "assistant": "Sounds good.", "index": 0},
        ]
        markers = extract_markers(exchanges)
        assert any(m["type"] == "OPEN" for m in markers)

    def test_positional_last_sentence(self):
        exchanges = [
            {"user": "Q1", "assistant": "Short answer.", "index": 0},
            {"user": "Q2", "assistant": "Let me explain. First this. Then that. Finally, we need to implement the caching layer before deploy.", "index": 1},
        ]
        markers = extract_markers(exchanges)
        # Last sentence of final response should produce a NEXT marker
        assert len(markers) > 0

    def test_question_detection(self):
        exchanges = [
            {"user": "Should we proceed with the migration?", "assistant": "Want me to start the migration now?", "index": 0},
        ]
        markers = extract_markers(exchanges)
        assert any(m["type"] == "OPEN" for m in markers)

    def test_dedup_by_substring(self):
        exchanges = [
            {"user": "plan?", "assistant": "We decided to use Redis. We decided to use Redis for caching.", "index": 0},
        ]
        markers = extract_markers(exchanges)
        decided = [m for m in markers if m["type"] == "DECIDED"]
        assert len(decided) <= 1  # Deduped

    def test_cap_per_type(self):
        # Generate many DECIDED markers
        exchanges = [
            {"user": "plan?", "assistant": f"We decided to use approach {i} for component {i} in system." * 2, "index": i}
            for i in range(10)
        ]
        markers = extract_markers(exchanges)
        decided_count = sum(1 for m in markers if m["type"] == "DECIDED")
        assert decided_count <= 3

    def test_cap_total(self):
        # Generate many markers of different types
        exchanges = [
            {"user": f"let's implement feature number {i} in the codebase", "assistant": f"We decided to use approach {i} for the implementation. Next step is testing approach {i} thoroughly.", "index": i}
            for i in range(20)
        ]
        markers = extract_markers(exchanges)
        assert len(markers) <= 10

    def test_empty_exchanges(self):
        assert extract_markers([]) == []

    def test_short_text_ignored(self):
        exchanges = [
            {"user": "ok", "assistant": "done", "index": 0},
        ]
        markers = extract_markers(exchanges)
        # Short text (<10 chars) should not produce markers
        assert len(markers) == 0


class TestBuildContextSummaryJson:
    def test_basic_structure(self):
        branch_row = {
            "started_at": "2025-01-15T14:00:00Z",
            "ended_at": "2025-01-15T15:00:00Z",
            "exchange_count": 3,
            "files_modified": '["src/main.py"]',
            "commits": '["fix: bug"]',
            "tool_counts": '{"Read": 5}',
            "git_branch": "main",
        }
        messages = [
            {"role": "user", "content": "Fix the bug in main.py", "timestamp": "2025-01-15T14:00:00Z"},
            {"role": "assistant", "content": "Found the issue.", "timestamp": "2025-01-15T14:01:00Z"},
            {"role": "user", "content": "Apply the fix", "timestamp": "2025-01-15T14:02:00Z"},
            {"role": "assistant", "content": "Done, fixed.", "timestamp": "2025-01-15T14:03:00Z"},
        ]
        result = build_context_summary_json(branch_row, messages)

        assert result["version"] == 2
        assert result["topic"] == "Fix the bug in main.py"
        assert len(result["first_exchanges"]) == 2
        assert result["first_exchanges"][0]["user"] == "Fix the bug in main.py"
        assert result["metadata"]["git_branch"] == "main"
        assert result["metadata"]["files_modified"] == ["src/main.py"]
        assert result["metadata"]["tool_counts"] == {"Read": 5}

    def test_empty_messages(self):
        branch_row = {"started_at": None, "ended_at": None}
        result = build_context_summary_json(branch_row, [])
        assert result["first_exchanges"] == []
        assert result["last_exchanges"] == []

    def test_short_session_all_in_last(self):
        branch_row = {"exchange_count": 5}
        messages = []
        for i in range(5):
            messages.append({"role": "user", "content": f"Q{i}", "timestamp": f"t{i}"})
            messages.append({"role": "assistant", "content": f"A{i}", "timestamp": f"t{i}"})
        result = build_context_summary_json(branch_row, messages)
        # Short/medium session (<=8): all exchanges in last_exchanges
        assert len(result["last_exchanges"]) == 5
        assert len(result["first_exchanges"]) == 2

    def test_medium_session_all_in_last(self):
        branch_row = {"exchange_count": 8}
        messages = []
        for i in range(8):
            messages.append({"role": "user", "content": f"Q{i}", "timestamp": f"t{i}"})
            messages.append({"role": "assistant", "content": f"A{i}", "timestamp": f"t{i}"})
        result = build_context_summary_json(branch_row, messages)
        # At threshold (<=8): all exchanges in last_exchanges
        assert len(result["last_exchanges"]) == 8
        assert len(result["first_exchanges"]) == 2

    def test_long_session_last_6(self):
        branch_row = {"exchange_count": 10}
        messages = []
        for i in range(10):
            messages.append({"role": "user", "content": f"Q{i}", "timestamp": f"t{i}"})
            messages.append({"role": "assistant", "content": f"A{i}", "timestamp": f"t{i}"})
        result = build_context_summary_json(branch_row, messages)
        assert len(result["last_exchanges"]) == 6
        assert result["last_exchanges"][0]["user"] == "Q4"
        assert len(result["first_exchanges"]) == 2
        assert result["first_exchanges"][0]["user"] == "Q0"
        assert result["first_exchanges"][1]["user"] == "Q1"

    def test_topic_truncated(self):
        branch_row = {}
        long_msg = "x" * 200
        messages = [
            {"role": "user", "content": long_msg, "timestamp": "t1"},
            {"role": "assistant", "content": "OK", "timestamp": "t1"},
        ]
        result = build_context_summary_json(branch_row, messages)
        assert len(result["topic"]) <= 123  # 120 + "..."


class TestRenderContextSummary:
    def test_short_session_no_first_last_split(self):
        summary = {
            "version": 2,
            "topic": "test",
            "markers": [],
            "first_exchanges": [
                {"user": "Q1", "assistant": "A1", "timestamp": "2025-01-15T10:00:00Z"},
            ],
            "last_exchanges": [
                {"user": "Q1", "assistant": "A1", "timestamp": "2025-01-15T10:00:00Z"},
                {"user": "Q2", "assistant": "A2", "timestamp": "2025-01-15T10:01:00Z"},
            ],
            "metadata": {"exchange_count": 2, "started_at": "2025-01-15T10:00:00Z",
                          "ended_at": "2025-01-15T10:30:00Z", "git_branch": "main",
                          "files_modified": [], "commits": [], "tool_counts": {}},
        }
        result = render_context_summary(summary)
        assert "### Conversation" in result
        assert "### First Exchanges" not in result
        assert "### Where We Left Off" not in result
        assert "/recall-conversations" in result

    def test_long_session_has_first_and_last(self):
        summary = {
            "version": 2,
            "topic": "test",
            "markers": [],
            "first_exchanges": [
                {"user": "Q1", "assistant": "A1", "timestamp": "2025-01-15T10:00:00Z"},
                {"user": "Q2", "assistant": "A2", "timestamp": "2025-01-15T10:01:00Z"},
            ],
            "last_exchanges": [
                {"user": "Q7", "assistant": "A7", "timestamp": "2025-01-15T10:09:00Z"},
                {"user": "Q8", "assistant": "A8", "timestamp": "2025-01-15T10:10:00Z"},
                {"user": "Q9", "assistant": "A9", "timestamp": "2025-01-15T10:11:00Z"},
                {"user": "Q10", "assistant": "A10", "timestamp": "2025-01-15T10:12:00Z"},
                {"user": "Q11", "assistant": "A11", "timestamp": "2025-01-15T10:13:00Z"},
                {"user": "Q12", "assistant": "A12", "timestamp": "2025-01-15T10:14:00Z"},
            ],
            "metadata": {"exchange_count": 12, "started_at": "2025-01-15T10:00:00Z",
                          "ended_at": "2025-01-15T11:00:00Z", "git_branch": "feat/x",
                          "files_modified": ["src/a.py", "src/b.py"], "commits": ["fix: thing"],
                          "tool_counts": {"Read": 10, "Edit": 3}},
        }
        result = render_context_summary(summary)
        assert "### Earlier in This Session" in result
        assert "### Where We Left Off" in result
        assert "[... 4 earlier exchanges covering: a.py, b.py ...]" in result  # 12 - 2 - 6 = 4
        assert "feat/x" in result
        assert "Modified:" in result
        assert "Tools:" in result
        assert "/recall-conversations" in result

    def test_markers_rendered(self):
        summary = {
            "version": 2,
            "topic": "test",
            "markers": [
                {"type": "DECIDED", "text": "Use Redis for caching", "source_exchange": 3},
                {"type": "OPEN", "text": "Auth service needs fixing", "source_exchange": 5},
            ],
            "first_exchanges": [
                {"user": "Q1", "assistant": "A1", "timestamp": "2025-01-15T10:00:00Z"},
            ],
            "last_exchanges": [{"user": "Q1", "assistant": "A1", "timestamp": "2025-01-15T10:00:00Z"}],
            "metadata": {"exchange_count": 1, "started_at": "2025-01-15T10:00:00Z",
                          "ended_at": "2025-01-15T10:30:00Z", "git_branch": "main",
                          "files_modified": [], "commits": [], "tool_counts": {}},
        }
        result = render_context_summary(summary)
        assert "### Key Signals" in result
        assert "[DECIDED] Use Redis for caching" in result
        assert "[OPEN] Auth service needs fixing" in result

    def test_mid_truncation_in_render(self):
        long_response = "Start " + "x" * 1000 + " End"
        summary = {
            "version": 2,
            "topic": "test",
            "markers": [],
            "first_exchanges": [
                {"user": "Q1", "assistant": long_response, "timestamp": "2025-01-15T10:00:00Z"},
            ],
            "last_exchanges": [{"user": "Q1", "assistant": long_response, "timestamp": "2025-01-15T10:00:00Z"}],
            "metadata": {"exchange_count": 1, "started_at": "2025-01-15T10:00:00Z",
                          "ended_at": "2025-01-15T10:30:00Z", "git_branch": "main",
                          "files_modified": [], "commits": [], "tool_counts": {}},
        }
        result = render_context_summary(summary)
        assert "[... truncated ...]" in result

    def test_empty_summary(self):
        assert render_context_summary({}) == ""
        assert render_context_summary({"first_exchanges": []}) == ""


class TestComputeContextSummary:
    """End-to-end test with a real in-memory DB."""

    @pytest.fixture
    def db_with_session(self):
        conn = sqlite3.connect(":memory:")
        conn.executescript(SCHEMA)
        conn.commit()
        _migrate_columns(conn)

        cursor = conn.cursor()
        cursor.execute("INSERT INTO projects (path, key, name) VALUES (?, ?, ?)",
                       ("/test/proj", "-test-proj", "proj"))
        cursor.execute("INSERT INTO sessions (uuid, project_id, git_branch) VALUES (?, ?, ?)",
                       ("sess-1", 1, "main"))
        cursor.execute("""
            INSERT INTO branches (session_id, leaf_uuid, is_active, started_at, ended_at,
                                  exchange_count, files_modified, commits, tool_counts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (1, "leaf-1", 1, "2025-01-15T10:00:00Z", "2025-01-15T11:00:00Z",
              3, '["src/main.py"]', '["fix: bug"]', '{"Read": 5}'))
        branch_id = cursor.lastrowid

        # Add messages
        msgs = [
            (1, "user-1", "2025-01-15T10:00:00Z", "user", "How do I fix the parser bug?"),
            (1, "asst-1", "2025-01-15T10:01:00Z", "assistant", "The bug is in the tokenizer. Let me show you."),
            (1, "user-2", "2025-01-15T10:05:00Z", "user", "Can you apply that fix?"),
            (1, "asst-2", "2025-01-15T10:06:00Z", "assistant", "Done. I decided to use a regex-based approach for the fix."),
            (1, "user-3", "2025-01-15T10:10:00Z", "user", "Run the tests"),
            (1, "asst-3", "2025-01-15T10:11:00Z", "assistant", "All tests pass. Next step is deploying to staging."),
        ]
        for session_id, uuid, ts, role, content in msgs:
            cursor.execute("""
                INSERT INTO messages (session_id, uuid, timestamp, role, content, is_notification)
                VALUES (?, ?, ?, ?, ?, 0)
            """, (session_id, uuid, ts, role, content))
            msg_id = cursor.lastrowid
            cursor.execute("INSERT INTO branch_messages (branch_id, message_id) VALUES (?, ?)",
                           (branch_id, msg_id))

        conn.commit()
        yield conn, branch_id
        conn.close()

    def test_compute_returns_markdown_and_json(self, db_with_session):
        conn, branch_id = db_with_session
        cursor = conn.cursor()

        md, json_str = compute_context_summary(cursor, branch_id)

        assert md
        assert json_str
        assert "### Session:" in md
        assert "/recall-conversations" in md

        parsed = json.loads(json_str)
        assert parsed["version"] == 2
        assert parsed["topic"] == "How do I fix the parser bug?"
        assert parsed["metadata"]["git_branch"] == "main"
        assert len(parsed["last_exchanges"]) == 3  # Short session (3 exchanges, <=8), all in last
        assert len(parsed["first_exchanges"]) == 2

    def test_compute_nonexistent_branch(self, db_with_session):
        conn, _ = db_with_session
        cursor = conn.cursor()
        md, json_str = compute_context_summary(cursor, 99999)
        assert md == ""
        assert json_str == ""
