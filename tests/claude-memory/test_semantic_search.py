"""Tests for opt-in semantic search (recall-v2 design, part 2).

A stub Ollama server stands in for the real one: its vectors encode a few concept
groups, so "vehicle" lands near "automobile" with no shared keyword. Dimensions
past DIMS carry a large constant that would swamp every comparison if the
client stopped truncating.

Coverage:
1. embed — truncation, normalization, request shape, Ollama-down error.
2. Indexing — exchange text, reminder-only and notification turns, no re-embedding.
3. vector_hits / fuse — best exchange per session, scope, reciprocal rank fusion.
4. search — a session with no keyword hit but a close vector ranks in, with its
   matching exchange as the excerpt.
6. Continuous indexing — the Stop-hook sync embeds the turn it just synced; off
   means no call; Ollama down leaves a backlog the next run clears; one indexer
   at a time.
5. CLI — off: unchanged output and no Ollama call; down: lexical with a warning;
   --lexical: no Ollama call.
"""

from __future__ import annotations

import importlib.util
import io
import json
import math
import re
import sqlite3
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

import search_conversations
from memory_lib.cli_common import ScopeFilter
from memory_lib.db import DEFAULT_SETTINGS, get_db_connection
from memory_lib.embeddings import (
    DIMS,
    EmbeddingError,
    embed,
    exchange_text,
    fuse,
    has_pending,
    pending_exchanges,
    vector_hits,
)

HOOKS_DIR = Path(__file__).resolve().parents[2] / "plugins/claude-memory/hooks"
sys.path.insert(0, str(HOOKS_DIR))
_hook = HOOKS_DIR / "embed_index.py"
_spec = importlib.util.spec_from_file_location("embed_index", _hook)
embed_index = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(embed_index)

MODEL = "stub-model"
CONCEPTS = [
    {"car", "automobile", "vehicle", "engine"},
    {"sqlite", "schema", "database", "migrate"},
    {"lunch", "food", "sandwich"},
]


def _stub_vector(text: str) -> list[float]:
    words = set(re.findall(r"[a-z]+", text.lower()))
    v = [0.0] * 768
    for i, group in enumerate(CONCEPTS):
        v[i] = float(len(words & group))
    v[DIMS - 1] = 0.1  # keeps unrelated texts non-zero
    v[DIMS + 10] = 100.0  # past the cut: dominates cosine unless truncated
    return v


class _Handler(BaseHTTPRequestHandler):
    requests: list = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _Handler.requests.append((self.path, body))
        payload = json.dumps({"embeddings": [_stub_vector(t) for t in body["input"]]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_):
        pass


@pytest.fixture
def ollama():
    _Handler.requests = []
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.fixture
def dead_url():
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    server.server_close()
    return f"http://127.0.0.1:{port}"


def _seed(db_file, sessions: dict[str, list[tuple[str, str]]], project="proj"):
    """File DB with one project and one active branch per session; returns {name: branch_id}."""
    conn = get_db_connection({"db_path": str(db_file)})
    cur = conn.cursor()
    cur.execute("INSERT INTO projects (path, key, name) VALUES (?, ?, ?)", (f"/{project}", f"-{project}", project))
    pid = cur.lastrowid
    ids = {}
    for n, (name, msgs) in enumerate(sessions.items()):
        cur.execute("INSERT INTO sessions (uuid, project_id) VALUES (?, ?)", (f"{project}-{name}", pid))
        sid = cur.lastrowid
        cur.execute(
            """INSERT INTO branches (session_id, leaf_uuid, is_active, exchange_count, started_at,
                   ended_at, aggregated_content) VALUES (?, 'leaf', 1, 1, ?, ?, ?)""",
            (sid, f"2026-01-0{n + 1}T00:00:00Z", f"2026-01-0{n + 1}T01:00:00Z",
             "\n".join(c for _, c in msgs)),
        )
        bid = ids[name] = cur.lastrowid
        for i, (role, content) in enumerate(msgs):
            notif = 1 if role == "notification" else 0
            cur.execute(
                """INSERT INTO messages (session_id, uuid, role, content, timestamp, is_notification)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (sid, f"{name}{i}", "user" if notif else role, content, f"2026-01-0{n + 1}T00:{i:02d}:00Z", notif),
            )
            cur.execute("INSERT INTO branch_messages (branch_id, message_id) VALUES (?, ?)", (bid, cur.lastrowid))
    conn.commit()
    return conn, ids


SESSIONS = {
    "db": [("user", "how do we migrate the sqlite schema"), ("assistant", "bump PRAGMA user_version")],
    "car": [("user", "my automobile makes a noise"), ("assistant", "check the engine mounts")],
    "lunch": [("user", "order a sandwich"), ("assistant", "ordered")],
}


class TestEmbed:
    def test_truncates_normalizes_and_sends_model(self, ollama):
        [v] = embed(["vehicle"], MODEL, ollama)
        assert len(v) == DIMS
        assert math.isclose(sum(x * x for x in v), 1.0, rel_tol=1e-6)
        path, body = _Handler.requests[0]
        assert path == "/api/embed" and body == {"model": MODEL, "input": ["vehicle"]}

    def test_unreachable_server_raises(self, dead_url):
        with pytest.raises(EmbeddingError):
            embed(["x"], MODEL, dead_url, timeout=2)


class TestIndexing:
    def test_exchange_text_strips_reminders_and_clips(self):
        assert exchange_text("<system-reminder>ctx</system-reminder>", "reply") == ""
        text = exchange_text("<system-reminder>ctx</system-reminder>ask " + "u" * 5000, "a" * 5000)
        assert text.startswith("ask") and "ctx" not in text
        assert len(text) <= 1200 + 2 + 800

    def test_reply_runs_to_next_human_turn(self, tmp_path):
        conn, _ = _seed(tmp_path / "c.db", {"s": [
            ("user", "first ask"), ("assistant", "part one"), ("notification", "subagent done"),
            ("assistant", "part two"), ("user", "second ask"), ("assistant", "other reply"),
        ]})
        texts = dict(pending_exchanges(conn, MODEL, 10))
        assert len(texts) == 2, "notification turn was treated as an exchange"
        first = next(t for t in texts.values() if t.startswith("first ask"))
        assert "part one" in first and "part two" in first and "other reply" not in first

    def test_index_pending_embeds_once_and_marks_empty_turns(self, tmp_path, ollama):
        conn, _ = _seed(tmp_path / "c.db", {
            **SESSIONS, "noise": [("user", "<system-reminder>only</system-reminder>")]})
        assert embed_index.index_pending(conn, MODEL, ollama) == 3
        assert not has_pending(conn, MODEL)
        assert conn.execute("SELECT COUNT(*) FROM exchange_embeddings WHERE vec = x''").fetchone()[0] == 1
        calls = len(_Handler.requests)
        assert embed_index.index_pending(conn, MODEL, ollama) == 0
        assert len(_Handler.requests) == calls, "already-indexed exchanges were re-embedded"

    def test_other_model_rows_do_not_count(self, tmp_path, ollama):
        conn, _ = _seed(tmp_path / "c.db", SESSIONS)
        embed_index.index_pending(conn, MODEL, ollama)
        assert has_pending(conn, "another-model")

    def test_ollama_down_propagates(self, tmp_path, dead_url):
        conn, _ = _seed(tmp_path / "c.db", SESSIONS)
        with pytest.raises(EmbeddingError):
            embed_index.index_pending(conn, MODEL, dead_url)


class TestRanking:
    def test_vector_hits_rank_by_closest_exchange_within_scope(self, tmp_path, ollama):
        conn, ids = _seed(tmp_path / "c.db", SESSIONS)
        _, other = _seed(tmp_path / "c.db", {"car2": SESSIONS["car"]}, project="other")
        embed_index.index_pending(conn, MODEL, ollama)
        [q] = embed(["vehicle trouble"], MODEL, ollama)

        hits = vector_hits(conn, q, MODEL, None, False, 10)
        assert {h[0] for h in hits[:2]} == {ids["car"], other["car2"]}

        scoped = vector_hits(conn, q, MODEL, ScopeFilter("name", ["proj"]), False, 10)
        assert scoped[0][0] == ids["car"]
        assert other["car2"] not in {h[0] for h in scoped}

    def test_inactive_branch_excluded(self, tmp_path, ollama):
        conn, ids = _seed(tmp_path / "c.db", SESSIONS)
        embed_index.index_pending(conn, MODEL, ollama)
        conn.execute("UPDATE branches SET is_active = 0 WHERE id = ?", (ids["car"],))
        [q] = embed(["vehicle"], MODEL, ollama)
        assert ids["car"] not in {h[0] for h in vector_hits(conn, q, MODEL, None, False, 10)}

    def test_min_score_drops_weak_matches(self, tmp_path, ollama):
        conn, _ = _seed(tmp_path / "c.db", SESSIONS)
        embed_index.index_pending(conn, MODEL, ollama)
        [q] = embed(["vehicle trouble"], MODEL, ollama)
        hits = vector_hits(conn, q, MODEL, None, False, 10)
        assert len(hits) >= 2
        floor = (hits[0][1] + hits[1][1]) / 2
        assert [h[0] for h in vector_hits(conn, q, MODEL, None, False, 10, min_score=floor)] == [hits[0][0]]

    def test_fuse_rewards_agreement(self):
        # B is second in both lists; 2/(60+2) beats 1/(60+1).
        assert fuse(["A", "B"], ["C", "B"], 3) == ["B", "A", "C"]
        assert fuse(["A"], [], 5) == ["A"]


class TestHybridSearch:
    def test_vector_only_session_ranks_in_with_its_exchange(self, tmp_path, ollama):
        conn, ids = _seed(tmp_path / "c.db", SESSIONS)
        embed_index.index_pending(conn, MODEL, ollama)
        settings = {**DEFAULT_SETTINGS, "embedding_model": MODEL, "ollama_url": ollama}

        lexical = search_conversations.search_sessions(
            conn, "vehicle", "fts5", 5, None, False, False, excerpts=True)
        assert lexical == []

        hits = search_conversations.semantic_hits(conn, "vehicle", settings, None, False, 5)
        hybrid = search_conversations.search_sessions(
            conn, "vehicle", "fts5", 5, None, False, False, excerpts=True, vector=hits)
        assert hybrid[0]["uuid"] == "proj-car"
        assert hybrid[0]["excerpts"][0]["text"] == "my automobile makes a noise"

    def test_semantic_hits_none_without_index(self, tmp_path, dead_url):
        conn, _ = _seed(tmp_path / "c.db", SESSIONS)
        settings = {**DEFAULT_SETTINGS, "embedding_model": MODEL, "ollama_url": dead_url}
        assert search_conversations.semantic_hits(conn, "x", settings, None, False, 5) is None


def _run_cli(monkeypatch, capsys, db_file, settings, *extra):
    monkeypatch.setattr(search_conversations, "load_settings", lambda: {**DEFAULT_SETTINGS, **settings})
    monkeypatch.setattr(sys, "argv", ["search_conversations.py", "-q", "sqlite schema", "--all-projects",
                                      "--json", "--db", str(db_file), *extra])
    search_conversations.main()
    out = capsys.readouterr()
    return json.loads(out.out), out.err


class TestCli:
    @pytest.fixture
    def indexed(self, tmp_path, ollama):
        db_file = tmp_path / "c.db"
        conn, _ = _seed(db_file, SESSIONS)
        embed_index.index_pending(conn, MODEL, ollama)
        conn.close()
        return db_file

    def _no_embed(self, monkeypatch):
        def boom(*_a, **_k):
            raise AssertionError("Ollama called")
        monkeypatch.setattr(search_conversations, "embed", boom)

    def test_off_means_no_ollama_call_and_no_retrieval_key(self, monkeypatch, capsys, indexed):
        self._no_embed(monkeypatch)
        out, _ = _run_cli(monkeypatch, capsys, indexed, {"semantic_search": False})
        assert "retrieval" not in out
        assert [s["uuid"] for s in out["sessions"]] == ["proj-db"]

    def test_lexical_flag_skips_ollama(self, monkeypatch, capsys, indexed, ollama):
        self._no_embed(monkeypatch)
        settings = {"semantic_search": True, "embedding_model": MODEL, "ollama_url": ollama}
        out, _ = _run_cli(monkeypatch, capsys, indexed, settings, "--lexical")
        assert out["retrieval"] == "lexical"

    def test_ollama_down_falls_back_with_warning(self, monkeypatch, capsys, indexed, dead_url):
        settings = {"semantic_search": True, "embedding_model": MODEL, "ollama_url": dead_url}
        out, err = _run_cli(monkeypatch, capsys, indexed, settings)
        assert out["retrieval"] == "lexical"
        assert "semantic search unavailable" in err
        assert [s["uuid"] for s in out["sessions"]] == ["proj-db"]

    def test_hybrid_when_enabled(self, monkeypatch, capsys, indexed, ollama):
        settings = {"semantic_search": True, "embedding_model": MODEL, "ollama_url": ollama}
        out, _ = _run_cli(monkeypatch, capsys, indexed, settings)
        assert out["retrieval"] == "hybrid"
        assert out["sessions"][0]["uuid"] == "proj-db"


_sync_spec = importlib.util.spec_from_file_location("sync_current_hook", HOOKS_DIR / "sync_current.py")
sync_current = importlib.util.module_from_spec(_sync_spec)
_sync_spec.loader.exec_module(sync_current)
FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE_SESSION = "d99039db-adce-4135-8b0a-b5347e39c051"


class TestContinuousIndexing:
    """The Stop-hook sync embeds the turn that just ended; no SessionStart needed."""

    @pytest.fixture
    def stop_hook(self, tmp_path, monkeypatch):
        projects = tmp_path / "projects" / "-proj"
        projects.mkdir(parents=True)
        (projects / f"{FIXTURE_SESSION}.jsonl").write_text(
            (FIXTURES / "linear_3_exchange.jsonl").read_text())
        monkeypatch.setattr(sync_current, "DEFAULT_PROJECTS_DIR", tmp_path / "projects")
        monkeypatch.setattr(embed_index, "EMBED_LOCK_PATH", tmp_path / "embed.lock")
        sys.modules["embed_index"] = embed_index
        db_file = tmp_path / "conversations.db"

        def run(url: str, semantic: bool) -> sqlite3.Connection:
            settings = {"db_path": str(db_file), "semantic_search": semantic,
                        "embedding_model": MODEL, "ollama_url": url, "sync_on_stop": True}
            monkeypatch.setattr(sync_current, "load_settings", lambda: settings)
            monkeypatch.setattr(sys, "argv", ["sync_current.py"])
            monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"session_id": FIXTURE_SESSION})))
            sync_current.main()
            return sqlite3.connect(db_file)
        return run

    def test_turn_is_embedded_when_its_sync_runs(self, stop_hook, ollama):
        conn = stop_hook(ollama, semantic=True)
        typed = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE role = 'user' AND COALESCE(is_notification, 0) = 0"
        ).fetchone()[0]
        assert typed > 0
        assert conn.execute("SELECT COUNT(*) FROM exchange_embeddings").fetchone()[0] == typed
        assert not has_pending(conn, MODEL)

    def test_off_means_no_embedding_and_no_ollama_call(self, stop_hook, ollama):
        conn = stop_hook(ollama, semantic=False)
        assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] > 0
        assert conn.execute("SELECT COUNT(*) FROM exchange_embeddings").fetchone()[0] == 0
        assert _Handler.requests == []

    def test_ollama_down_still_syncs_and_retries_later(self, stop_hook, dead_url, ollama):
        conn = stop_hook(dead_url, semantic=True)
        assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] > 0
        assert has_pending(conn, MODEL)

        # Nothing new to sync, so the Stop hook leaves the backlog to SessionStart's indexer.
        settings = {"db_path": str(conn.execute("PRAGMA database_list").fetchone()[2]),
                    "semantic_search": True, "embedding_model": MODEL, "ollama_url": ollama}
        assert embed_index.run(settings, sync_current.setup_logging(None)) > 0
        assert not has_pending(conn, MODEL)

    def test_second_indexer_skips_while_first_runs(self, tmp_path, monkeypatch, ollama):
        from memory_lib.db import acquire_lock, release_lock
        lock = tmp_path / "embed.lock"
        monkeypatch.setattr(embed_index, "EMBED_LOCK_PATH", lock)
        conn, _ = _seed(tmp_path / "c.db", SESSIONS)
        settings = {"db_path": str(tmp_path / "c.db"), "semantic_search": True,
                    "embedding_model": MODEL, "ollama_url": ollama}
        assert acquire_lock(lock)
        try:
            assert embed_index.run(settings, sync_current.setup_logging(None)) == 0
            assert _Handler.requests == []
        finally:
            release_lock(lock)
        assert embed_index.run(settings, sync_current.setup_logging(None)) == 3
