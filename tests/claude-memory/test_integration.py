"""Integration tests for notification classification (task-notification + teammate-message) end-to-end."""

import sqlite3
from pathlib import Path

from memory_lib.content import extract_text_content, is_task_notification, is_teammate_message, is_tool_result
from memory_lib.db import SCHEMA, _migrate_columns
from memory_lib.parsing import (
    aggregate_branch_content,
    compute_branch_metadata,
    find_all_branches,
    parse_all_with_uuids,
    parse_jsonl_file,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures"
NOTIF_FIXTURE = FIXTURE_DIR / "with_notifications.jsonl"


def _setup_db_and_import(filepath: Path) -> sqlite3.Connection:
    """Create in-memory DB and import a fixture file, flagging notifications."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA)
    conn.commit()
    _migrate_columns(conn)
    cursor = conn.cursor()

    # Create project and session
    cursor.execute("INSERT INTO projects (path, key, name) VALUES (?, ?, ?)",
                   ("/home/user/project", "-home-user-project", "project"))
    project_id = cursor.lastrowid
    cursor.execute("INSERT INTO sessions (uuid, project_id) VALUES (?, ?)",
                   ("notif-test-session", project_id))
    session_id = cursor.lastrowid

    # Parse and import messages
    all_entries = list(parse_all_with_uuids(filepath))
    messages = list(parse_jsonl_file(filepath))

    for entry in messages:
        entry_type = entry.get("type")
        if entry_type not in ("user", "assistant"):
            continue
        message = entry.get("message", {})
        content = message.get("content", "")
        if entry_type == "user" and is_tool_result(content):
            continue
        notification = 1 if (entry_type == "user" and (is_task_notification(content) or is_teammate_message(content))) else 0
        text, has_tool_use, has_thinking, tool_summary = extract_text_content(content)
        if not text:
            continue
        cursor.execute("""
            INSERT INTO messages (session_id, uuid, parent_uuid, timestamp, role, content,
                                  tool_summary, has_tool_use, has_thinking, is_notification)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id, uuid) DO NOTHING
        """, (
            session_id, entry.get("uuid"), entry.get("parentUuid"),
            entry.get("timestamp"), entry_type, text, tool_summary,
            has_tool_use, has_thinking, notification,
        ))

    # Build branches
    branches = find_all_branches(all_entries)
    cursor.execute("SELECT id, uuid FROM messages WHERE session_id = ? AND uuid IS NOT NULL",
                   (session_id,))
    uuid_to_msg_id = {row[1]: row[0] for row in cursor.fetchall()}

    for branch in branches:
        branch_msgs = [m for m in messages if m.get("uuid") in branch["uuids"]]
        branch_msgs.sort(key=lambda e: e.get("timestamp") or "")
        exchange_count, files, commits, _tool_counts = compute_branch_metadata(branch_msgs)

        cursor.execute("""
            INSERT INTO branches (session_id, leaf_uuid, is_active, exchange_count)
            VALUES (?, ?, ?, ?)
        """, (session_id, branch["leaf_uuid"], int(branch["is_active"]), exchange_count))
        branch_db_id = cursor.lastrowid

        for uuid in branch["uuids"]:
            msg_id = uuid_to_msg_id.get(uuid)
            if msg_id:
                cursor.execute("INSERT OR IGNORE INTO branch_messages VALUES (?, ?)",
                               (branch_db_id, msg_id))

        agg = aggregate_branch_content(cursor, branch_db_id)
        cursor.execute("UPDATE branches SET aggregated_content = ? WHERE id = ?",
                       (agg, branch_db_id))

    conn.commit()
    return conn


class TestNotificationEndToEnd:
    def test_notifications_flagged(self):
        """Notification messages should have is_notification=1."""
        conn = _setup_db_and_import(NOTIF_FIXTURE)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM messages WHERE is_notification = 1")
        assert cursor.fetchone()[0] == 2  # Two task-notification messages

        cursor.execute("SELECT COUNT(*) FROM messages WHERE is_notification = 0 AND role = 'user'")
        assert cursor.fetchone()[0] == 2  # Two real user messages
        conn.close()

    def test_aggregate_excludes_notifications(self):
        """Branch aggregated content should not contain notification text."""
        conn = _setup_db_and_import(NOTIF_FIXTURE)
        cursor = conn.cursor()
        cursor.execute("SELECT aggregated_content FROM branches WHERE is_active = 1")
        agg = cursor.fetchone()[0]
        assert "<task-notification>" not in agg
        assert "Research AI agent memory" in agg or "summarize the key takeaways" in agg
        conn.close()

    def test_exchange_count_correct(self):
        """Exchange count should reflect human exchanges only, not notifications."""
        conn = _setup_db_and_import(NOTIF_FIXTURE)
        cursor = conn.cursor()
        cursor.execute("SELECT exchange_count FROM branches WHERE is_active = 1")
        count = cursor.fetchone()[0]
        assert count == 2  # "Research AI agent memory" and "summarize the key takeaways"
        conn.close()

    def test_context_injection_query_excludes_notifications(self):
        """The query pattern used by memory-context.py should exclude notifications."""
        conn = _setup_db_and_import(NOTIF_FIXTURE)
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM branches WHERE is_active = 1")
        branch_db_id = cursor.fetchone()[0]

        # This mirrors the query in memory-context.py
        cursor.execute("""
            SELECT m.role, m.content, m.timestamp
            FROM branch_messages bm
            JOIN messages m ON bm.message_id = m.id
            WHERE bm.branch_id = ?
              AND COALESCE(m.is_notification, 0) = 0
            ORDER BY m.timestamp ASC
        """, (branch_db_id,))
        messages = cursor.fetchall()

        roles = [m[0] for m in messages]
        contents = [m[1] for m in messages]
        assert "user" in roles
        assert "assistant" in roles
        for content in contents:
            assert "<task-notification>" not in content
        conn.close()

    def test_include_notifications_query(self):
        """With notifications included, all messages should be returned."""
        conn = _setup_db_and_import(NOTIF_FIXTURE)
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM branches WHERE is_active = 1")
        branch_db_id = cursor.fetchone()[0]

        # Without filter (include_notifications=True pattern)
        cursor.execute("""
            SELECT m.role, m.content, COALESCE(m.is_notification, 0) as is_notification
            FROM branch_messages bm
            JOIN messages m ON bm.message_id = m.id
            WHERE bm.branch_id = ?
            ORDER BY m.timestamp ASC
        """, (branch_db_id,))
        messages = cursor.fetchall()

        notif_count = sum(1 for m in messages if m[2] == 1)
        assert notif_count == 2
        assert len(messages) > notif_count  # Should also have regular messages
        conn.close()


TEAMMATE_FIXTURE = FIXTURE_DIR / "with_teammate_messages.jsonl"


class TestTeammateMessageEndToEnd:
    def test_teammate_messages_flagged(self):
        """Teammate messages should have is_notification=1."""
        conn = _setup_db_and_import(TEAMMATE_FIXTURE)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM messages WHERE is_notification = 1")
        assert cursor.fetchone()[0] == 2  # Two teammate messages (report + idle)

        cursor.execute("SELECT COUNT(*) FROM messages WHERE is_notification = 0 AND role = 'user'")
        assert cursor.fetchone()[0] == 2  # Two real user messages
        conn.close()

    def test_aggregate_excludes_teammate_messages(self):
        """Branch aggregated content should not contain teammate message text."""
        conn = _setup_db_and_import(TEAMMATE_FIXTURE)
        cursor = conn.cursor()
        cursor.execute("SELECT aggregated_content FROM branches WHERE is_active = 1")
        agg = cursor.fetchone()[0]
        assert "<teammate-message" not in agg
        assert "idle_notification" not in agg
        assert "Implement the security fixes" in agg or "commit and push" in agg
        conn.close()

    def test_exchange_count_excludes_teammate_messages(self):
        """Exchange count should reflect human exchanges only, not teammate messages."""
        conn = _setup_db_and_import(TEAMMATE_FIXTURE)
        cursor = conn.cursor()
        cursor.execute("SELECT exchange_count FROM branches WHERE is_active = 1")
        count = cursor.fetchone()[0]
        assert count == 2  # "Implement the security fixes" and "awesome, looks great. now commit"
        conn.close()

    def test_context_injection_query_excludes_teammate_messages(self):
        """The query pattern used by memory-context.py should exclude teammate messages."""
        conn = _setup_db_and_import(TEAMMATE_FIXTURE)
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM branches WHERE is_active = 1")
        branch_db_id = cursor.fetchone()[0]

        cursor.execute("""
            SELECT m.role, m.content, m.timestamp
            FROM branch_messages bm
            JOIN messages m ON bm.message_id = m.id
            WHERE bm.branch_id = ?
              AND COALESCE(m.is_notification, 0) = 0
            ORDER BY m.timestamp ASC
        """, (branch_db_id,))
        messages = cursor.fetchall()

        for _, content, _ in messages:
            assert "<teammate-message" not in content
            assert "idle_notification" not in content
        conn.close()
