"""Tests for search_conversations.py — FTS5/FTS4/LIKE search cascade."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

# Add scripts dir to path for search_conversations import
SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "plugins" / "claude-memory" / "skills" / "recall-conversations" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from search_conversations import search_sessions
from memory_lib.cli_common import ScopeFilter
from memory_lib.db import SCHEMA, _migrate_columns, detect_fts_support


@pytest.fixture
def search_db():
    """In-memory DB with schema, seeded with searchable sessions."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA)
    conn.commit()
    _migrate_columns(conn)

    cursor = conn.cursor()

    # Create two projects
    cursor.execute("INSERT INTO projects (path, key, name) VALUES (?, ?, ?)",
                   ("/home/user/alpha", "-home-user-alpha", "alpha"))
    alpha_id = cursor.lastrowid
    cursor.execute("INSERT INTO projects (path, key, name) VALUES (?, ?, ?)",
                   ("/home/user/beta", "-home-user-beta", "beta"))
    beta_id = cursor.lastrowid

    # Session 1 in alpha: talks about "pytest fixtures"
    cursor.execute("INSERT INTO sessions (uuid, project_id) VALUES (?, ?)",
                   ("sess-alpha-1", alpha_id))
    s1_id = cursor.lastrowid
    cursor.execute("""
        INSERT INTO branches (session_id, leaf_uuid, is_active, exchange_count, aggregated_content)
        VALUES (?, ?, 1, 2, ?)
    """, (s1_id, "leaf-a1", "How do pytest fixtures work? They provide reusable test setup."))
    b1_id = cursor.lastrowid
    cursor.execute("INSERT INTO messages (session_id, uuid, role, content, timestamp) VALUES (?, ?, ?, ?, ?)",
                   (s1_id, "m1", "user", "How do pytest fixtures work?", "2025-01-15T14:00:00Z"))
    m1_id = cursor.lastrowid
    cursor.execute("INSERT INTO messages (session_id, uuid, role, content, timestamp) VALUES (?, ?, ?, ?, ?)",
                   (s1_id, "m2", "assistant", "They provide reusable test setup.", "2025-01-15T14:01:00Z"))
    m2_id = cursor.lastrowid
    cursor.execute("INSERT INTO branch_messages VALUES (?, ?)", (b1_id, m1_id))
    cursor.execute("INSERT INTO branch_messages VALUES (?, ?)", (b1_id, m2_id))

    # Session 2 in alpha: talks about "database migration"
    cursor.execute("INSERT INTO sessions (uuid, project_id) VALUES (?, ?)",
                   ("sess-alpha-2", alpha_id))
    s2_id = cursor.lastrowid
    cursor.execute("""
        INSERT INTO branches (session_id, leaf_uuid, is_active, exchange_count, aggregated_content)
        VALUES (?, ?, 1, 3, ?)
    """, (s2_id, "leaf-a2", "How do I migrate the database? Use alembic for schema migrations."))
    b2_id = cursor.lastrowid
    cursor.execute("INSERT INTO messages (session_id, uuid, role, content, timestamp) VALUES (?, ?, ?, ?, ?)",
                   (s2_id, "m3", "user", "How do I migrate the database?", "2025-01-15T15:00:00Z"))
    m3_id = cursor.lastrowid
    cursor.execute("INSERT INTO messages (session_id, uuid, role, content, timestamp) VALUES (?, ?, ?, ?, ?)",
                   (s2_id, "m4", "assistant", "Use alembic for schema migrations.", "2025-01-15T15:01:00Z"))
    m4_id = cursor.lastrowid
    cursor.execute("INSERT INTO branch_messages VALUES (?, ?)", (b2_id, m3_id))
    cursor.execute("INSERT INTO branch_messages VALUES (?, ?)", (b2_id, m4_id))

    # Session 3 in beta: talks about "pytest mocking"
    cursor.execute("INSERT INTO sessions (uuid, project_id) VALUES (?, ?)",
                   ("sess-beta-1", beta_id))
    s3_id = cursor.lastrowid
    cursor.execute("""
        INSERT INTO branches (session_id, leaf_uuid, is_active, exchange_count, aggregated_content)
        VALUES (?, ?, 1, 2, ?)
    """, (s3_id, "leaf-b1", "How do I mock in pytest? Use unittest.mock or pytest-mock."))
    b3_id = cursor.lastrowid
    cursor.execute("INSERT INTO messages (session_id, uuid, role, content, timestamp) VALUES (?, ?, ?, ?, ?)",
                   (s3_id, "m5", "user", "How do I mock in pytest?", "2025-01-15T16:00:00Z"))
    m5_id = cursor.lastrowid
    cursor.execute("INSERT INTO messages (session_id, uuid, role, content, timestamp) VALUES (?, ?, ?, ?, ?)",
                   (s3_id, "m6", "assistant", "Use unittest.mock or pytest-mock.", "2025-01-15T16:01:00Z"))
    m6_id = cursor.lastrowid
    cursor.execute("INSERT INTO branch_messages VALUES (?, ?)", (b3_id, m5_id))
    cursor.execute("INSERT INTO branch_messages VALUES (?, ?)", (b3_id, m6_id))

    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def search_db_with_notification(search_db):
    """Extends search_db with a notification message in sess-alpha-1.

    Used to verify include_notifications filtering: the notification message
    content is distinct ("SYSTEM NOTIFICATION: background task done") so tests
    can confirm its presence/absence in returned messages.
    """
    cursor = search_db.cursor()

    # Fetch sess-alpha-1 IDs we need to attach to
    cursor.execute("SELECT id FROM sessions WHERE uuid = ?", ("sess-alpha-1",))
    s1_id = cursor.fetchone()[0]
    cursor.execute("SELECT id FROM branches WHERE session_id = ?", (s1_id,))
    b1_id = cursor.fetchone()[0]

    cursor.execute(
        "INSERT INTO messages (session_id, uuid, role, content, timestamp, is_notification) VALUES (?, ?, ?, ?, ?, ?)",
        (s1_id, "m-notif", "assistant", "SYSTEM NOTIFICATION: background task done",
         "2025-01-15T14:02:00Z", 1),
    )
    notif_id = cursor.lastrowid
    cursor.execute("INSERT INTO branch_messages VALUES (?, ?)", (b1_id, notif_id))
    search_db.commit()
    return search_db


class TestSearchSessionsFTS:
    """Test search with FTS5 (default on most SQLite builds)."""

    def test_search_returns_matching_sessions(self, search_db):
        fts_level = detect_fts_support(search_db)
        if fts_level not in ("fts5", "fts4"):
            pytest.skip("FTS not available")

        results = search_sessions(search_db, "pytest", fts_level, limit=10,
                                  scope=None, verbose=False, include_notifications=False)
        assert len(results) >= 2, "Should match sessions mentioning 'pytest'"
        uuids = {r["uuid"] for r in results}
        assert "sess-alpha-1" in uuids
        assert "sess-beta-1" in uuids

    def test_search_database_specific(self, search_db):
        fts_level = detect_fts_support(search_db)
        if fts_level not in ("fts5", "fts4"):
            pytest.skip("FTS not available")

        results = search_sessions(search_db, "database migration", fts_level, limit=10,
                                  scope=None, verbose=False, include_notifications=False)
        assert len(results) >= 1
        assert any(r["uuid"] == "sess-alpha-2" for r in results)

    def test_empty_query_returns_empty(self, search_db):
        fts_level = detect_fts_support(search_db)
        results = search_sessions(search_db, "", fts_level, limit=10,
                                  scope=None, verbose=False, include_notifications=False)
        assert results == []

    def test_limit_respected(self, search_db):
        fts_level = detect_fts_support(search_db)
        if fts_level not in ("fts5", "fts4"):
            pytest.skip("FTS not available")

        results = search_sessions(search_db, "pytest", fts_level, limit=1,
                                  scope=None, verbose=False, include_notifications=False)
        assert len(results) <= 1

    def test_project_filter(self, search_db):
        fts_level = detect_fts_support(search_db)
        if fts_level not in ("fts5", "fts4"):
            pytest.skip("FTS not available")

        results = search_sessions(search_db, "pytest", fts_level, limit=10,
                                  scope=ScopeFilter("name", ["alpha"]), verbose=False, include_notifications=False)
        assert all(r["project"] == "alpha" for r in results), "Should only return alpha project"
        assert len(results) >= 1

    def test_messages_loaded(self, search_db):
        fts_level = detect_fts_support(search_db)
        if fts_level not in ("fts5", "fts4"):
            pytest.skip("FTS not available")

        results = search_sessions(search_db, "pytest fixtures", fts_level, limit=5,
                                  scope=None, verbose=False, include_notifications=False)
        matching = [r for r in results if r["uuid"] == "sess-alpha-1"]
        assert len(matching) == 1
        session = matching[0]
        assert len(session["messages"]) == 2
        assert session["messages"][0]["role"] == "user"
        assert session["messages"][1]["role"] == "assistant"

    def test_projects_none_spans_all_projects(self, search_db):
        # Prevents accidental implicit project scoping: unfiltered search must return
        # sessions from more than one project, proving no WHERE project= clause is applied.
        fts_level = detect_fts_support(search_db)
        if fts_level not in ("fts5", "fts4"):
            pytest.skip("FTS not available")

        results = search_sessions(search_db, "pytest", fts_level, limit=10,
                                  scope=None, verbose=False, include_notifications=False)
        projects_found = {r["project"] for r in results}
        assert len(projects_found) > 1, (
            "scope=None should return sessions from multiple projects, not just one"
        )

    def test_unknown_project_returns_empty(self, search_db):
        # Prevents SQL errors (wrong placeholder count, mis-constructed IN clause) from
        # raising instead of returning an empty list for a project with no sessions.
        fts_level = detect_fts_support(search_db)
        if fts_level not in ("fts5", "fts4"):
            pytest.skip("FTS not available")

        results = search_sessions(search_db, "pytest", fts_level, limit=10,
                                  scope=ScopeFilter("name", ["nonexistent"]), verbose=False, include_notifications=False)
        assert results == [], "Unknown project name should return empty list, not raise"


class TestSearchSessionsLIKE:
    """Test LIKE fallback when FTS is not available."""

    def test_like_search_returns_results(self, search_db):
        results = search_sessions(search_db, "pytest", fts_level=None, limit=10,
                                  scope=None, verbose=False, include_notifications=False)
        assert len(results) >= 2
        uuids = {r["uuid"] for r in results}
        assert "sess-alpha-1" in uuids
        assert "sess-beta-1" in uuids

    def test_like_multiple_terms_and_logic(self, search_db):
        # LIKE fallback ANDs all terms — "pytest fixtures" must match both words in aggregated_content.
        # Only sess-alpha-1 contains both "pytest" and "fixtures"; sess-beta-1 has pytest but not fixtures.
        results = search_sessions(search_db, "pytest fixtures", fts_level=None, limit=10,
                                  scope=None, verbose=False, include_notifications=False)
        uuids = {r["uuid"] for r in results}
        assert uuids == {"sess-alpha-1"}, (
            "AND-logic should exclude sessions that match only one term"
        )

    def test_like_project_filter(self, search_db):
        results = search_sessions(search_db, "pytest", fts_level=None, limit=10,
                                  scope=ScopeFilter("name", ["beta"]), verbose=False, include_notifications=False)
        assert all(r["project"] == "beta" for r in results)

    def test_like_empty_query(self, search_db):
        results = search_sessions(search_db, "", fts_level=None, limit=10,
                                  scope=None, verbose=False, include_notifications=False)
        assert results == []

    def test_like_limit(self, search_db):
        results = search_sessions(search_db, "pytest", fts_level=None, limit=1,
                                  scope=None, verbose=False, include_notifications=False)
        assert len(results) <= 1

    def test_like_projects_none_spans_all_projects(self, search_db):
        # Mirrors TestSearchSessionsFTS: LIKE path must also return cross-project results
        # when scope=None, confirming the filter is absent in both code paths.
        results = search_sessions(search_db, "pytest", fts_level=None, limit=10,
                                  scope=None, verbose=False, include_notifications=False)
        projects_found = {r["project"] for r in results}
        assert len(projects_found) > 1, (
            "scope=None should return sessions from multiple projects, not just one"
        )

    def test_like_unknown_project_returns_empty(self, search_db):
        # Prevents SQL errors from the LIKE path's IN clause when the project name
        # matches nothing — should return [] not raise.
        results = search_sessions(search_db, "pytest", fts_level=None, limit=10,
                                  scope=ScopeFilter("name", ["nonexistent"]), verbose=False, include_notifications=False)
        assert results == [], "Unknown project name should return empty list, not raise"


class TestVerboseFlag:
    """verbose=True adds files_modified and commits to each result dict."""

    def test_verbose_false_omits_file_fields(self, search_db):
        # Prevents callers from accidentally relying on fields that are absent by default
        results = search_sessions(search_db, "pytest", fts_level=None, limit=5,
                                  scope=None, verbose=False, include_notifications=False)
        assert len(results) >= 1
        for r in results:
            assert "files_modified" not in r
            assert "commits" not in r

    def test_verbose_true_includes_file_fields(self, search_db):
        # Prevents regression where verbose path silently drops metadata
        results = search_sessions(search_db, "pytest", fts_level=None, limit=5,
                                  scope=None, verbose=True, include_notifications=False)
        assert len(results) >= 1
        for r in results:
            assert "files_modified" in r
            assert "commits" in r
            # Fixture branches have no files_modified/commits set, so expect empty lists
            assert isinstance(r["files_modified"], list)
            assert isinstance(r["commits"], list)


class TestIncludeNotificationsFlag:
    """include_notifications=False (default) strips is_notification=1 messages."""

    def test_notifications_excluded_by_default(self, search_db_with_notification):
        # Prevents notification noise from polluting context injection results
        results = search_sessions(search_db_with_notification, "pytest fixtures",
                                  fts_level=None, limit=5,
                                  scope=None, verbose=False, include_notifications=False)
        matching = [r for r in results if r["uuid"] == "sess-alpha-1"]
        assert len(matching) == 1
        contents = [m["content"] for m in matching[0]["messages"]]
        assert not any("SYSTEM NOTIFICATION" in c for c in contents), (
            "Notification message must be excluded when include_notifications=False"
        )

    def test_notifications_included_when_flag_set(self, search_db_with_notification):
        # Verifies the flag actually toggles behavior, not just that the default filters
        results = search_sessions(search_db_with_notification, "pytest fixtures",
                                  fts_level=None, limit=5,
                                  scope=None, verbose=False, include_notifications=True)
        matching = [r for r in results if r["uuid"] == "sess-alpha-1"]
        assert len(matching) == 1
        contents = [m["content"] for m in matching[0]["messages"]]
        assert any("SYSTEM NOTIFICATION" in c for c in contents), (
            "Notification message must appear when include_notifications=True"
        )
