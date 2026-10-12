"""Tests for human-only SessionStart summaries (recall-v2 design, part 1).

Coverage:
1. is_harness_message — the four harness prefixes tag; typed text and
   reminder-plus-typed-text do not.
2. Migration v6 — tags only harness rows, rebuilds only the branches it touched,
   resets their summary_version, and waits when the backup fails.
3. Summarizer — reminders stripped, topic is one line, total budget honored with
   the newest exchange kept and the opening exchanges dropped first.
4. build_context — the whole injection fits the budget; stored JSON is re-rendered
   in place of stale oversized markdown; short sessions hand slack onward.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from memory_lib.content import is_harness_message, is_notification_content
from memory_lib.db import get_db_connection
from memory_lib.summarizer import (
    build_context_summary_json,
    build_exchange_pairs,
    render_context_summary,
)

_hook = Path(__file__).resolve().parents[2] / "plugins/claude-memory/hooks/memory-context.py"
_spec = importlib.util.spec_from_file_location("memory_context_v2", _hook)
memory_context = importlib.util.module_from_spec(_spec)
sys.modules["memory_context_v2"] = memory_context
_spec.loader.exec_module(memory_context)

COMPACTION = "This session is being continued from a previous conversation that ran out of context. " + "x" * 5000
REMINDER = "<system-reminder>\nCodebase instructions " + "r" * 3000 + "\n</system-reminder>"


class TestHarnessPredicate:
    @pytest.mark.parametrize("text", [
        COMPACTION,
        "<artifact-content-authored-by-others/>\nSummary of the claude.ai chat",
        "<ci-monitor-event>checks failed</ci-monitor-event>",
        "Another Claude session sent a message:\nhello",
        '<agent-message from="peer">hi</agent-message>',
    ])
    def test_harness_prefixes_tag(self, text):
        assert is_harness_message(text)
        assert is_harness_message([{"type": "text", "text": "  " + text}])
        assert is_notification_content(text)

    @pytest.mark.parametrize("text", [
        "fix the importer",
        REMINDER + "\nfix the importer",
        "why did it say 'This session is being continued from a previous conversation'?",
    ])
    def test_typed_text_does_not_tag(self, text):
        assert not is_harness_message(text)
        assert not is_notification_content(text)


def _db_at_v5(db_file):
    """File-backed DB at v5: branch A mixes typed and harness turns, branch B is typed only."""
    conn = get_db_connection({"db_path": str(db_file)})
    cur = conn.cursor()
    cur.execute("INSERT INTO projects (path, key, name) VALUES ('/p', '-p', 'p')")
    pid = cur.lastrowid
    branches = {}
    for name, msgs in {
        "A": [("user", "fix the importer"), ("assistant", "done"), ("user", COMPACTION), ("assistant", "resuming")],
        "B": [("user", "add a test"), ("assistant", "added")],
    }.items():
        cur.execute("INSERT INTO sessions (uuid, project_id) VALUES (?, ?)", (f"uuid-{name}", pid))
        sid = cur.lastrowid
        cur.execute(
            """INSERT INTO branches (session_id, leaf_uuid, is_active, exchange_count,
                   aggregated_content, summary_version) VALUES (?, 'leaf', 1, ?, 'stale', 1)""",
            (sid, sum(r == "user" for r, _ in msgs)),
        )
        bid = branches[name] = cur.lastrowid
        for i, (role, content) in enumerate(msgs):
            cur.execute(
                "INSERT INTO messages (session_id, uuid, role, content, timestamp) VALUES (?, ?, ?, ?, ?)",
                (sid, f"{name}{i}", role, content, f"2026-01-01T00:0{i}:00Z"),
            )
            cur.execute("INSERT INTO branch_messages (branch_id, message_id) VALUES (?, ?)", (bid, cur.lastrowid))
    conn.execute("PRAGMA user_version = 5")
    conn.commit()
    conn.close()
    return branches


class TestV6Migration:
    def test_tags_only_harness_rows_and_rebuilds_only_their_branches(self, tmp_path):
        db_file = tmp_path / "conversations.db"
        branches = _db_at_v5(db_file)

        conn = get_db_connection({"db_path": str(db_file)})

        assert conn.execute("PRAGMA user_version").fetchone()[0] == 6
        assert list(tmp_path.glob("conversations.pre-v6-harness-turns-*.db"))
        tagged = [r[0] for r in conn.execute("SELECT content FROM messages WHERE is_notification = 1")]
        assert tagged == [COMPACTION]

        agg, count, version = conn.execute(
            "SELECT aggregated_content, exchange_count, summary_version FROM branches WHERE id = ?",
            (branches["A"],)).fetchone()
        assert "previous conversation" not in agg and "fix the importer" in agg
        assert count == 1
        assert version is None

        # Branch B gained no tag, so the migration must leave it alone.
        assert conn.execute(
            "SELECT aggregated_content, summary_version FROM branches WHERE id = ?",
            (branches["B"],)).fetchone() == ("stale", 1)
        conn.close()

    def test_waits_when_backup_fails(self, tmp_path, monkeypatch):
        import memory_lib.db as db_module
        monkeypatch.setattr(db_module, "_backup_db_before_migration", lambda *_: False)
        db_file = tmp_path / "conversations.db"
        _db_at_v5(db_file)

        conn = get_db_connection({"db_path": str(db_file)})

        assert conn.execute("PRAGMA user_version").fetchone()[0] == 5
        assert conn.execute("SELECT COUNT(*) FROM messages WHERE is_notification = 1").fetchone()[0] == 0
        conn.close()


def _messages(n_exchanges: int, user_chars: int = 2000, asst_chars: int = 2000) -> list[dict]:
    msgs = []
    for i in range(n_exchanges):
        ts = f"2026-01-01T{i // 60:02d}:{i % 60:02d}:00Z"
        msgs.append({"role": "user", "content": f"ask{i} " + "u" * user_chars, "timestamp": ts})
        msgs.append({"role": "assistant", "content": f"reply{i} " + "a" * asst_chars, "timestamp": ts})
    return msgs


def _summary_json(n_exchanges: int, **kw) -> dict:
    return build_context_summary_json({"exchange_count": n_exchanges}, _messages(n_exchanges, **kw))


class TestSummarizer:
    def test_reminders_stripped_and_reminder_only_turn_skipped(self):
        exchanges = build_exchange_pairs([
            {"role": "user", "content": REMINDER, "timestamp": "t0"},
            {"role": "user", "content": REMINDER + "\nfix the importer", "timestamp": "t1"},
            {"role": "assistant", "content": "done", "timestamp": "t2"},
        ])
        assert [ex["user"] for ex in exchanges] == ["fix the importer"]

    def test_topic_is_one_line(self):
        summary = build_context_summary_json(
            {"exchange_count": 1},
            [{"role": "user", "content": "fix\n\n  the   importer", "timestamp": "t"},
             {"role": "assistant", "content": "ok", "timestamp": "t"}],
        )
        assert summary["topic"] == "fix the importer"

    def test_budget_keeps_newest_and_drops_opening_first(self):
        summary = _summary_json(30)
        unbounded = render_context_summary(summary, max_chars=10 ** 6)
        assert "### Earlier in This Session" in unbounded and len(unbounded) > 8000

        text = render_context_summary(summary, max_chars=8000)
        assert len(text) <= 8000
        assert "ask29" in text, "newest exchange dropped"
        assert "### Earlier in This Session" not in text

    def test_tiny_budget_still_holds_newest_exchange(self):
        text = render_context_summary(_summary_json(30), max_chars=1500)
        assert len(text) <= 1500
        assert "ask29" in text


def _session(n_exchanges: int, **kw) -> dict:
    summary = _summary_json(n_exchanges, **kw)
    return {
        "uuid": f"uuid-{n_exchanges}",
        "started_at": "2026-01-01T00:00:00Z",
        "ended_at": "2026-01-01T01:00:00Z",
        "exchange_count": n_exchanges,
        # Pre-v2 stored markdown: oversized and rendered without a budget.
        "context_summary": render_context_summary(summary, max_chars=10 ** 6),
        "context_summary_json": json.dumps(summary),
    }


class TestBuildContextBudget:
    def test_total_fits_budget(self):
        out = memory_context.build_context([_session(30), _session(40)], max_chars=8000)
        assert len(out) <= 8000
        assert "ask29" in out and "ask39" in out

    def test_rerenders_json_instead_of_stale_markdown(self):
        session = _session(30)
        out = memory_context.build_context([session], max_chars=8000)
        assert "ask29" in out, "stale markdown was cut, losing where the session left off"

    def test_short_session_hands_slack_to_the_next(self):
        short = _session(2, user_chars=20, asst_chars=20)
        long = _session(30)
        out = memory_context.build_context([short, long], max_chars=8000)
        alone = memory_context.build_context([long], max_chars=4000)
        long_part = out.split("\n\n---\n\n")[1]
        assert len(long_part) > len(alone), "unused share of the short session was lost"


_bf_path = Path(__file__).resolve().parents[2] / "plugins/claude-memory/hooks/backfill_summaries.py"
_bf_spec = importlib.util.spec_from_file_location("backfill_summaries_v2", _bf_path)
backfill = importlib.util.module_from_spec(_bf_spec)
_bf_spec.loader.exec_module(backfill)


class _Abort(BaseException):
    """Escapes backfill's `except Exception` so a retry loop ends the test instead of hanging it."""


class TestBackfill:
    def _db(self, tmp_path, monkeypatch, versions):
        db_file = tmp_path / "conversations.db"
        conn = get_db_connection({"db_path": str(db_file)})
        cur = conn.cursor()
        cur.execute("INSERT INTO projects (path, key, name) VALUES ('/p', '-p', 'p')")
        cur.execute("INSERT INTO sessions (uuid, project_id) VALUES ('s', ?)", (cur.lastrowid,))
        sid = cur.lastrowid
        for v in versions:
            cur.execute("INSERT INTO branches (session_id, leaf_uuid, summary_version) VALUES (?, 'l', ?)", (sid, v))
        conn.commit()
        conn.close()
        monkeypatch.setattr(backfill, "load_settings", lambda: {"db_path": str(db_file)})
        return db_file

    def test_rebuilds_summaries_below_current_version(self, tmp_path, monkeypatch):
        from memory_lib.summarizer import SUMMARY_VERSION
        db_file = self._db(tmp_path, monkeypatch, [SUMMARY_VERSION - 1])
        backfill.main()
        conn = get_db_connection({"db_path": str(db_file)})
        assert conn.execute("SELECT summary_version FROM branches").fetchone()[0] == SUMMARY_VERSION
        conn.close()

    def test_failed_branch_is_not_retried(self, tmp_path, monkeypatch):
        self._db(tmp_path, monkeypatch, [None])
        calls = []

        def failing(*_):
            calls.append(1)
            if len(calls) > 3:
                raise _Abort
            raise RuntimeError("bad branch")
        monkeypatch.setattr(backfill, "compute_context_summary", failing)
        backfill.main()
        assert len(calls) == 1
