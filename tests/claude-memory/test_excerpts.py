"""Tests for search_conversations.py --excerpts and the timeline topic title.

Coverage:
1. fetch_excerpts — FTS5 path: only matching messages, highlighted, stemmed like the
   session ranking, capped per session, returned in conversation order.
2. LIKE fallback — window around the first hit.
3. search_sessions(excerpts=True) — sessions carry excerpts instead of messages.
4. Formatting — markdown section and JSON counter.
5. CLI — --excerpts conflicts with --summary.
6. summary_topic — timeline title is the topic, not the date header.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import search_conversations
from memory_lib.formatting import format_json_sessions, format_markdown_session
from recent_chats import summary_topic
from search_conversations import EXCERPTS_PER_SESSION, fetch_excerpts, search_sessions

SCRIPT = Path(search_conversations.__file__)


def _seed(conn: sqlite3.Connection, messages: list[tuple[str, str]]) -> int:
    """One project/session/branch holding `messages` (role, content); returns branch id."""
    cur = conn.cursor()
    cur.execute("INSERT INTO projects (path, key, name) VALUES ('/p', '-p', 'p')")
    cur.execute("INSERT INTO sessions (uuid, project_id) VALUES ('uuid-1', ?)", (cur.lastrowid,))
    sid = cur.lastrowid
    cur.execute(
        """INSERT INTO branches (session_id, leaf_uuid, is_active, exchange_count,
               started_at, ended_at, aggregated_content)
           VALUES (?, 'leaf', 1, 1, '2026-01-01T00:00:00Z', '2026-01-01T01:00:00Z', ?)""",
        (sid, " ".join(c for _, c in messages)),
    )
    bid = cur.lastrowid
    for i, (role, content) in enumerate(messages):
        cur.execute(
            "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
            (sid, role, content, f"2026-01-01T00:{i:02d}:00Z"),
        )
        cur.execute("INSERT INTO branch_messages (branch_id, message_id) VALUES (?, ?)",
                    (bid, cur.lastrowid))
    conn.commit()
    return bid


class TestFetchExcerptsFts5:
    def test_returns_only_matching_messages_highlighted(self, memory_db):
        bid = _seed(memory_db, [
            ("user", "how do we rotate the signing key"),
            ("assistant", "unrelated chatter about lunch"),
            ("assistant", "rotate it with the vault command"),
        ])
        out = fetch_excerpts(memory_db.cursor(), bid, "fts5", '"rotate"', ["rotate"], False)
        assert [e["role"] for e in out] == ["user", "assistant"]
        assert all("**rotate**" in e["text"] for e in out)

    def test_matches_stemmed_forms_like_the_ranker(self, memory_db):
        # Porter stemming: the session matched "decided" via "decide"; the excerpt must too.
        bid = _seed(memory_db, [("assistant", "we decide to keep sqlite")])
        out = fetch_excerpts(memory_db.cursor(), bid, "fts5", '"decided"', ["decided"], False)
        assert len(out) == 1

    def test_caps_per_session_and_keeps_conversation_order(self, memory_db):
        bid = _seed(memory_db, [("user", f"cache note {i}") for i in range(EXCERPTS_PER_SESSION + 3)])
        out = fetch_excerpts(memory_db.cursor(), bid, "fts5", '"cache"', ["cache"], False)
        assert len(out) == EXCERPTS_PER_SESSION
        assert [e["timestamp"] for e in out] == sorted(e["timestamp"] for e in out)


def test_like_fallback_windows_around_first_hit(memory_db):
    long_text = "x" * 500 + " needle " + "y" * 500
    bid = _seed(memory_db, [("assistant", long_text)])
    out = fetch_excerpts(memory_db.cursor(), bid, None, None, ["needle"], False)
    text = out[0]["text"]
    assert "needle" in text
    assert text.startswith("…") and text.endswith("…")
    assert len(text) < len(long_text)


def test_search_sessions_excerpts_replace_messages(memory_db):
    _seed(memory_db, [("user", "migrate the schema"), ("assistant", "done")])
    sessions = search_sessions(memory_db, "schema", "fts5", 5, None, False, False, excerpts=True)
    assert len(sessions) == 1
    assert "messages" not in sessions[0]
    assert [e["role"] for e in sessions[0]["excerpts"]] == ["user"]


def test_formatting_renders_excerpts_and_json_counter():
    session = {"uuid": "uuid-1", "project": "p", "started_at": None,
               "excerpts": [{"role": "user", "timestamp": None, "text": "…the **key**…"}]}
    md = format_markdown_session(session)
    assert "### Excerpts" in md and "### Conversation" not in md
    assert json.loads(format_json_sessions([session]))["total_excerpts"] == 1


def test_cli_rejects_excerpts_with_summary(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "-q", "x", "--excerpts", "--summary",
         "--db", str(tmp_path / "none.db")],
        capture_output=True, text=True, cwd=SCRIPT.parent,
    )
    assert proc.returncode == 2
    assert "mutually exclusive" in proc.stderr


class TestSummaryTopic:
    def test_extracts_topic_not_header(self):
        md = ("### Session: 2026-01-01 10:00 -> 2026-01-01 11:00 (branch: main)\n\n"
              "**Topic:** fix the importer | **Status:** COMPLETED\n")
        assert summary_topic(md) == "fix the importer"

    def test_empty_when_no_topic_line(self):
        assert summary_topic("### Session: 2026-01-01 10:00 -> 2026-01-01 11:00\n") == ""
        assert summary_topic(None) == ""
