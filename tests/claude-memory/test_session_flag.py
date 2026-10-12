"""Tests for recent_chats.py --session: fetch named sessions by uuid prefix.

Coverage:
1. get_recent_sessions(session_prefixes=...) — selects by prefix, ignores limit.
2. parse_session_prefixes — rejects anything that could act as a LIKE wildcard.
3. CLI wiring — bypasses CWD scope, warns on unmatched prefixes, conflicts with --timeline.
4. Empty-summary placeholder points at --session.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

import recent_chats
from memory_lib.db import SCHEMA, _migrate_columns
from memory_lib.formatting import format_markdown_session
from recent_chats import get_recent_sessions, parse_session_prefixes

SCRIPT = Path(recent_chats.__file__)

UUID_A = "aaaaaaaa-1111-4000-8000-000000000001"
UUID_B = "bbbbbbbb-2222-4000-8000-000000000002"
UUID_C = "cccccccc-3333-4000-8000-000000000003"


def _seed(conn: sqlite3.Connection) -> None:
    """Three sessions: A and B in project 'alpha', C in project 'beta'."""
    cur = conn.cursor()
    projects = {}
    for name in ("alpha", "beta"):
        cur.execute("INSERT INTO projects (path, key, name) VALUES (?, ?, ?)",
                    (f"/{name}", f"-{name}", name))
        projects[name] = cur.lastrowid
    for uuid, proj, hours in ((UUID_A, "alpha", 3), (UUID_B, "alpha", 2), (UUID_C, "beta", 1)):
        cur.execute("INSERT INTO sessions (uuid, project_id) VALUES (?, ?)", (uuid, projects[proj]))
        sid = cur.lastrowid
        cur.execute(
            """INSERT INTO branches (session_id, leaf_uuid, is_active, exchange_count,
                   started_at, ended_at, context_summary, aggregated_content)
               VALUES (?, ?, 1, 1, datetime('now', ?), datetime('now', ?), NULL, 'x')""",
            (sid, f"leaf-{uuid}", f"-{hours} hours", f"-{hours} hours"),
        )
        bid = cur.lastrowid
        cur.execute("INSERT INTO messages (session_id, role, content, timestamp) "
                    "VALUES (?, 'user', ?, datetime('now'))", (sid, f"body of {uuid[:8]}"))
        cur.execute("INSERT INTO branch_messages (branch_id, message_id) VALUES (?, ?)",
                    (bid, cur.lastrowid))
    conn.commit()


def _fetch(conn: sqlite3.Connection, prefixes: list[str], limit: int = 5) -> list[dict]:
    return get_recent_sessions(
        conn, limit=limit, sort_order="desc", before=None, after=None, scope=None,
        verbose=False, include_notifications=False, session_prefixes=prefixes,
    )


class TestSessionPrefixQuery:
    def test_prefix_selects_only_matching_session(self, memory_db):
        _seed(memory_db)
        results = _fetch(memory_db, ["bbbbbbbb"])
        assert [r["uuid"] for r in results] == [UUID_B]
        assert results[0]["messages"][0]["content"] == "body of bbbbbbbb"

    def test_multiple_prefixes_across_projects(self, memory_db):
        _seed(memory_db)
        uuids = {r["uuid"] for r in _fetch(memory_db, ["aaaaaaaa", "cccccccc"])}
        assert uuids == {UUID_A, UUID_C}

    def test_limit_does_not_truncate_named_sessions(self, memory_db):
        _seed(memory_db)
        assert len(_fetch(memory_db, ["aaaaaaaa", "bbbbbbbb", "cccccccc"], limit=1)) == 3

    def test_full_uuid_matches(self, memory_db):
        _seed(memory_db)
        assert [r["uuid"] for r in _fetch(memory_db, [UUID_C])] == [UUID_C]


class TestParseSessionPrefixes:
    def test_accepts_and_lowercases(self):
        assert parse_session_prefixes(" AAAAAAAA , bbbbbbbb-2222 ", "json") == ["aaaaaaaa", "bbbbbbbb-2222"]

    @pytest.mark.parametrize("raw", ["abc", "aaaa%aaa", "aaaa_aaaa", "zzzzzzzz", ",", ""])
    def test_rejects_short_or_wildcard_input(self, raw, capsys):
        with pytest.raises(SystemExit) as exc:
            parse_session_prefixes(raw, "json")
        assert exc.value.code == 2
        assert json.loads(capsys.readouterr().err)["error"] == "invalid_session"


@pytest.fixture
def db_file(tmp_path: Path) -> Path:
    path = tmp_path / "conversations.db"
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    _migrate_columns(conn)
    _seed(conn)
    conn.close()
    return path


def _run(db_file: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--db", str(db_file), *args],
        capture_output=True, text=True, cwd=SCRIPT.parent,
    )


class TestSessionCli:
    def test_bypasses_cwd_scope(self, db_file):
        # --cwd resolves to project alpha; the named beta session must still come back.
        proc = _run(db_file, "--cwd", "/alpha", "--session", "cccccccc", "--json")
        assert proc.returncode == 0, proc.stderr
        out = json.loads(proc.stdout)
        assert [s["uuid"] for s in out["sessions"]] == [UUID_C]
        assert out["has_more"] is False

    def test_warns_on_unmatched_prefix(self, db_file):
        proc = _run(db_file, "--session", "aaaaaaaa,deadbeef")
        assert proc.returncode == 0
        assert "deadbeef" in proc.stderr
        assert "aaaaaaaa" not in proc.stderr

    def test_conflicts_with_timeline(self, db_file):
        proc = _run(db_file, "--session", "aaaaaaaa", "--timeline")
        assert proc.returncode == 2


def test_empty_summary_placeholder_names_session_prefix():
    md = format_markdown_session({"uuid": UUID_A, "project": "alpha", "summary": ""})
    assert "--session aaaaaaaa" in md
