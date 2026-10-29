"""Regression tests for user turns that recall could not see.

Coverage:
1. find_all_branches — the active branch crosses compaction boundaries, and a
   parallel tool call is not mistaken for a rewind.
2. Import — re-importing a grown transcript leaves one branch per real leaf, not
   one per earlier snapshot.
3. extract_text_content — a slash command keeps its name and typed arguments.
4. Codex — new-format UserMessage items import as user turns without shifting
   the synthetic UUIDs of rows imported before; Dayflow runs are skipped and,
   once logged, not re-read.
5. Migration v7 — drops Dayflow sessions, queues on-disk transcripts for
   re-import, puts every stored message of a lost transcript on its active
   branch, and waits when the backup fails.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "plugins" / "claude-memory" / "hooks"))

import import_conversations as import_module
from import_conversations import import_codex_session, import_session
from memory_lib.content import extract_text_content, is_notification_content
from memory_lib.db import SCHEMA, _migrate_columns, get_db_connection
from memory_lib.parsing import find_all_branches
from sync_current import _codex_message_uuid, parse_codex_session

DAYFLOW_CWD = "/Users/me/Library/Application Support/Dayflow/chatcli"


@pytest.fixture
def memory_db():
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA)
    conn.commit()
    _migrate_columns(conn)
    yield conn
    conn.close()


@pytest.fixture
def project_id(memory_db):
    cur = memory_db.execute(
        "INSERT INTO projects (path, key, name) VALUES ('/test/project', '-test-project', 'project')"
    )
    memory_db.commit()
    return cur.lastrowid


def _entry(uuid, parent, kind, content, minute, **extra):
    entry = {
        "uuid": uuid,
        "parentUuid": parent,
        "type": kind,
        "timestamp": f"2026-10-28T10:{minute:02d}:00Z",
        "sessionId": "11111111-1111-1111-1111-111111111111",
        "cwd": "/test/project",
        "message": {"role": kind, "content": content},
    }
    entry.update(extra)
    return entry


def _tool_use(tid):
    return [{"type": "tool_use", "id": tid, "name": "Bash", "input": {}}]


def _tool_result(tid):
    return [{"type": "tool_result", "tool_use_id": tid, "content": "ok"}]


def _compacted_session():
    """Typed turn, compaction, typed turn. The boundary has no parentUuid."""
    return [
        _entry("u1", None, "user", "build the reactor scene", 0),
        _entry("a1", "u1", "assistant", [{"type": "text", "text": "built it"}], 1),
        {"uuid": "cb", "parentUuid": None, "logicalParentUuid": "a1", "type": "system",
         "subtype": "compact_boundary", "timestamp": "2026-10-28T10:02:00Z"},
        _entry("u2", "cb", "user", "this was a mistake, wait for my go", 3),
        _entry("a2", "u2", "assistant", [{"type": "text", "text": "understood"}], 4),
    ]


class TestBranchDetection:
    def test_active_branch_crosses_compaction_boundary(self):
        branches = find_all_branches(_compacted_session())

        assert len(branches) == 1
        assert {"u1", "a1", "u2", "a2"} <= branches[0]["uuids"]

    def test_parallel_tool_call_is_not_a_rewind(self):
        # a1 issues two tool calls: a2 (second call) and r1 (first call's result)
        # both hang off a1. Only typed turns mark a rewind.
        entries = [
            _entry("u1", None, "user", "run both checks", 0),
            _entry("a1", "u1", "assistant", _tool_use("t1"), 1),
            _entry("a2", "a1", "assistant", _tool_use("t2"), 2),
            _entry("r1", "a1", "user", _tool_result("t1"), 3),
            _entry("r2", "a2", "user", _tool_result("t2"), 4),
            _entry("a3", "r2", "assistant", [{"type": "text", "text": "both pass"}], 5),
        ]

        assert len(find_all_branches(entries)) == 1

    def test_real_rewind_still_detected(self):
        entries = [
            _entry("u1", None, "user", "first idea", 0),
            _entry("a1", "u1", "assistant", [{"type": "text", "text": "ok"}], 1),
            _entry("u2", "a1", "user", "abandoned direction", 2),
            _entry("a2", "u2", "assistant", [{"type": "text", "text": "ok"}], 3),
            _entry("u3", "a1", "user", "kept direction", 4),
            _entry("a3", "u3", "assistant", [{"type": "text", "text": "ok"}], 5),
        ]

        branches = find_all_branches(entries)

        assert len(branches) == 2
        assert "u2" in branches[1]["uuids"] and "u2" not in branches[0]["uuids"]


def _write(path: Path, entries):
    path.write_text("".join(json.dumps(e) + "\n" for e in entries))


def _active_user_turns(conn):
    return [r[0] for r in conn.execute("""
        SELECT m.content FROM branches b
        JOIN branch_messages bm ON bm.branch_id = b.id
        JOIN messages m ON m.id = bm.message_id
        WHERE b.is_active = 1 AND m.role = 'user' ORDER BY m.timestamp""")]


class TestImportKeepsTurns:
    def test_pre_compaction_turns_on_active_branch(self, memory_db, project_id, tmp_path):
        f = tmp_path / "11111111-1111-1111-1111-111111111111.jsonl"
        _write(f, _compacted_session())

        import_session(memory_db, f, project_id)

        assert _active_user_turns(memory_db) == ["build the reactor scene", "this was a mistake, wait for my go"]

    def test_reimport_of_grown_file_leaves_one_branch(self, memory_db, project_id, tmp_path):
        f = tmp_path / "11111111-1111-1111-1111-111111111111.jsonl"
        entries = _compacted_session()
        _write(f, entries[:2])
        import_session(memory_db, f, project_id)
        _write(f, entries)
        import_session(memory_db, f, project_id)

        assert memory_db.execute("SELECT COUNT(*), SUM(is_active) FROM branches").fetchone() == (1, 1)


class TestSlashCommands:
    def test_command_with_args_keeps_both(self):
        text = ("<command-message>mattpocock-skills:ask-matt</command-message>\n"
                "<command-name>/mattpocock-skills:ask-matt</command-name>\n"
                "<command-args>issue 12 .. which skill?</command-args>")
        assert extract_text_content(text)[0] == "/mattpocock-skills:ask-matt issue 12 .. which skill?"

    def test_command_without_args_keeps_name(self):
        text = "<command-message>clean-branches</command-message>\n<command-name>/clean-branches</command-name>"
        assert extract_text_content(text)[0] == "/clean-branches"

    def test_bare_command_is_not_a_typed_turn(self):
        # Stored and searchable, but kept out of topics, exchange counts and embeddings.
        bare = "<command-name>/clear</command-name><command-message>clear</command-message><command-args></command-args>"
        assert extract_text_content(bare)[0] == "/clear"
        assert is_notification_content(bare)
        assert not is_notification_content(
            "<command-name>/ask-matt</command-name><command-args>issue 12</command-args>")

    def test_local_command_output_still_dropped(self):
        assert extract_text_content("<local-command-stdout>Set model to opus</local-command-stdout>")[0] == ""


def _codex_file(path: Path, cwd: str, events: list[dict]):
    sid = "01a0e7e6-d080-7bb2-b7ba-1aec3bc854f2"
    lines = [{"timestamp": "2026-09-28T12:00:00.000Z", "type": "session_meta",
              "payload": {"id": sid, "cwd": cwd, "originator": "Codex Desktop"}}]
    lines += events
    path.write_text("".join(json.dumps(e) + "\n" for e in lines))
    return sid


def _user_item(text, minute):
    return {"timestamp": f"2026-09-28T12:{minute:02d}:00.000Z", "type": "event_msg",
            "payload": {"type": "item_completed", "item": {"type": "UserMessage", "id": f"item-{minute}",
                                                           "content": [{"type": "text", "text": text}]}}}


def _legacy_user(text, minute):
    return {"timestamp": f"2026-09-28T12:{minute:02d}:00.000Z", "type": "event_msg",
            "payload": {"type": "user_message", "message": text}}


def _final(text, minute):
    return {"timestamp": f"2026-09-28T12:{minute:02d}:00.000Z", "type": "response_item",
            "payload": {"type": "message", "role": "assistant", "phase": "final_answer",
                        "content": [{"type": "output_text", "text": text}]}}


class TestCodexUserTurns:
    def test_user_message_item_imports(self, tmp_path):
        f = tmp_path / "rollout.jsonl"
        sid = _codex_file(f, "/repo", [_user_item("start the website please", 1), _final("Started.", 2)])

        _, _, messages, _ = parse_codex_session(f)

        assert [(m["type"], m["message"]["content"]) for m in messages] == [
            ("user", "start the website please"), ("assistant", "Started.")]
        # The answer keeps the UUID it got when only final answers were read.
        assert messages[1]["uuid"] == _codex_message_uuid(sid, "response_item.message.final_answer", 0)

    def test_items_without_id_stay_distinct(self, tmp_path):
        f = tmp_path / "rollout.jsonl"
        a, b = _user_item("first", 1), _user_item("second", 3)
        del a["payload"]["item"]["id"], b["payload"]["item"]["id"]
        _codex_file(f, "/repo", [a, _final("one", 2), b, _final("two", 4)])

        _, _, messages, _ = parse_codex_session(f)

        assert len({m["uuid"] for m in messages}) == 4

    def test_legacy_event_wins_over_item(self, tmp_path):
        f = tmp_path / "rollout.jsonl"
        _codex_file(f, "/repo", [_legacy_user("hello", 1), _user_item("hello", 1), _final("hi", 2)])

        _, _, messages, _ = parse_codex_session(f)

        assert [m["type"] for m in messages] == ["user", "assistant"]


class TestDayflowSkip:
    def test_dayflow_run_is_logged_not_imported(self, memory_db, tmp_path, monkeypatch):
        f = tmp_path / "rollout.jsonl"
        _codex_file(f, DAYFLOW_CWD, [_user_item("make timeline cards", 1), _final("{}", 2)])

        assert import_codex_session(memory_db, f) == (-1, 0)
        assert memory_db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0

        # An unchanged file is not read again: not even hashed.
        monkeypatch.setattr(import_module, "get_file_hash", lambda _: pytest.fail("rehashed"))
        assert import_codex_session(memory_db, f) == (-1, 0)


def _db_at_v6(db_file: Path, on_disk: Path):
    """v6 DB with a Dayflow session, a session whose transcript is gone and whose
    active branch holds only its tail, and a session whose transcript is on disk."""
    conn = get_db_connection({"db_path": str(db_file)})
    cur = conn.cursor()
    cur.execute("INSERT INTO projects (path, key, name) VALUES ('/p', '-p', 'p')")
    pid = cur.lastrowid
    ids = {}
    for name, cwd in (("dayflow", DAYFLOW_CWD), ("gone", "/p"), ("disk", "/p")):
        cur.execute("INSERT INTO sessions (uuid, project_id, cwd) VALUES (?, ?, ?)", (f"uuid-{name}", pid, cwd))
        sid = ids[name] = cur.lastrowid
        msg_ids = []
        for i, (role, text) in enumerate([("user", f"{name} early"), ("assistant", "ok"),
                                          ("user", f"{name} late"), ("assistant", "ok")]):
            cur.execute(
                "INSERT INTO messages (session_id, uuid, role, content, timestamp) VALUES (?, ?, ?, ?, ?)",
                (sid, f"{name}{i}", role, text, f"2026-10-28T10:0{i}:00Z"))
            msg_ids.append(cur.lastrowid)
        cur.execute("""INSERT INTO branches (session_id, leaf_uuid, is_active, exchange_count,
                       aggregated_content, summary_version) VALUES (?, 'old', 0, 1, 'x', 1)""", (sid,))
        cur.execute("INSERT INTO branch_messages VALUES (?, ?)", (cur.lastrowid, msg_ids[0]))
        cur.execute("""INSERT INTO branches (session_id, leaf_uuid, is_active, exchange_count,
                       aggregated_content, summary_version) VALUES (?, 'tail', 1, 1, 'x', 1)""", (sid,))
        tail = cur.lastrowid
        for mid in msg_ids[2:]:
            cur.execute("INSERT INTO branch_messages VALUES (?, ?)", (tail, mid))
        path = on_disk if name == "disk" else Path(f"/gone/{name}.jsonl")
        cur.execute("INSERT INTO import_log (file_path, file_hash) VALUES (?, 'h')", (str(path),))
    conn.execute("PRAGMA user_version = 6")
    conn.commit()
    conn.close()
    return ids


class TestV7Migration:
    @pytest.fixture(autouse=True)
    def _empty_transcript_dirs(self, tmp_path, monkeypatch):
        import memory_lib.db as db_module
        monkeypatch.setattr(db_module, "DEFAULT_PROJECTS_DIR", tmp_path / "projects")
        monkeypatch.setattr(db_module, "DEFAULT_CODEX_SESSIONS_DIR", tmp_path / "codex")

    def test_unlogged_transcript_on_disk_is_queued_not_merged(self, tmp_path):
        # The Stop hook never writes import_log, so a synced session can have its
        # transcript on disk and no log row.
        db_file = tmp_path / "conversations.db"
        ids = _db_at_v6(db_file, tmp_path / "uuid-disk.jsonl")
        transcript = tmp_path / "projects" / "-p" / "uuid-gone.jsonl"
        transcript.parent.mkdir(parents=True)
        transcript.write_text("{}\n")

        conn = get_db_connection({"db_path": str(db_file)})

        assert conn.execute("SELECT file_hash FROM import_log WHERE file_path = ?",
                            (str(transcript),)).fetchone() == (None,)
        users = conn.execute("""
            SELECT COUNT(*) FROM branches b JOIN branch_messages bm ON bm.branch_id = b.id
            JOIN messages m ON m.id = bm.message_id
            WHERE b.session_id = ? AND b.is_active = 1 AND m.role = 'user'""", (ids["gone"],)).fetchone()[0]
        assert users == 1  # left for the re-import, not merged from the DB
        conn.close()

    def test_repairs_branches_and_drops_dayflow(self, tmp_path):
        db_file = tmp_path / "conversations.db"
        on_disk = tmp_path / "uuid-disk.jsonl"
        on_disk.write_text("{}\n")
        ids = _db_at_v6(db_file, on_disk)

        conn = get_db_connection({"db_path": str(db_file)})

        assert conn.execute("PRAGMA user_version").fetchone()[0] == 7
        assert list(tmp_path.glob("conversations.pre-v7-*.db"))
        for table in ("sessions", "messages", "branches"):
            col = "id" if table == "sessions" else "session_id"
            assert conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {col} = ?", (ids["dayflow"],)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM branch_messages WHERE branch_id NOT IN (SELECT id FROM branches)"
                            ).fetchone()[0] == 0

        users, agg, count, version = conn.execute("""
            SELECT (SELECT COUNT(*) FROM branch_messages bm JOIN messages m ON m.id = bm.message_id
                    WHERE bm.branch_id = b.id AND m.role = 'user'),
                   aggregated_content, exchange_count, summary_version
            FROM branches b WHERE session_id = ? AND is_active = 1""", (ids["gone"],)).fetchone()
        assert (users, count, version) == (2, 2, None)
        assert "gone early" in agg

        hashes = dict(conn.execute("SELECT file_path, file_hash FROM import_log"))
        assert hashes[str(on_disk)] is None
        assert hashes["/gone/gone.jsonl"] == "h"
        conn.close()

    def test_waits_when_backup_fails(self, tmp_path, monkeypatch):
        import memory_lib.db as db_module
        monkeypatch.setattr(db_module, "_backup_db_before_migration", lambda *_: False)
        db_file = tmp_path / "conversations.db"
        _db_at_v6(db_file, tmp_path / "uuid-disk.jsonl")

        conn = get_db_connection({"db_path": str(db_file)})

        assert conn.execute("PRAGMA user_version").fetchone()[0] == 6
        assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 3
        conn.close()
