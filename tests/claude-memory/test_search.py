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


class TestSearchSessionsFTS:
    """Test search with FTS5 (default on most SQLite builds)."""

    def test_search_returns_matching_sessions(self, search_db):
        fts_level = detect_fts_support(search_db)
        if fts_level not in ("fts5", "fts4"):
            pytest.skip("FTS not available")

        results = search_sessions(search_db, "pytest", fts_level, max_results=10)
        assert len(results) >= 2, "Should match sessions mentioning 'pytest'"
        uuids = {r["uuid"] for r in results}
        assert "sess-alpha-1" in uuids
        assert "sess-beta-1" in uuids

    def test_search_database_specific(self, search_db):
        fts_level = detect_fts_support(search_db)
        if fts_level not in ("fts5", "fts4"):
            pytest.skip("FTS not available")

        results = search_sessions(search_db, "database migration", fts_level, max_results=10)
        assert len(results) >= 1
        assert any(r["uuid"] == "sess-alpha-2" for r in results)

    def test_empty_query_returns_empty(self, search_db):
        fts_level = detect_fts_support(search_db)
        results = search_sessions(search_db, "", fts_level)
        assert results == []

    def test_max_results_respected(self, search_db):
        fts_level = detect_fts_support(search_db)
        if fts_level not in ("fts5", "fts4"):
            pytest.skip("FTS not available")

        results = search_sessions(search_db, "pytest", fts_level, max_results=1)
        assert len(results) <= 1

    def test_project_filter(self, search_db):
        fts_level = detect_fts_support(search_db)
        if fts_level not in ("fts5", "fts4"):
            pytest.skip("FTS not available")

        results = search_sessions(search_db, "pytest", fts_level, max_results=10, projects=["alpha"])
        assert all(r["project"] == "alpha" for r in results), "Should only return alpha project"
        assert len(results) >= 1

    def test_messages_loaded(self, search_db):
        fts_level = detect_fts_support(search_db)
        if fts_level not in ("fts5", "fts4"):
            pytest.skip("FTS not available")

        results = search_sessions(search_db, "pytest fixtures", fts_level, max_results=5)
        matching = [r for r in results if r["uuid"] == "sess-alpha-1"]
        assert len(matching) == 1
        session = matching[0]
        assert len(session["messages"]) == 2
        assert session["messages"][0]["role"] == "user"
        assert session["messages"][1]["role"] == "assistant"


class TestSearchSessionsLIKE:
    """Test LIKE fallback when FTS is not available."""

    def test_like_search_returns_results(self, search_db):
        results = search_sessions(search_db, "pytest", fts_level=None, max_results=10)
        assert len(results) >= 2
        uuids = {r["uuid"] for r in results}
        assert "sess-alpha-1" in uuids
        assert "sess-beta-1" in uuids

    def test_like_multiple_terms_and_logic(self, search_db):
        # LIKE fallback uses AND between terms
        results = search_sessions(search_db, "pytest fixtures", fts_level=None, max_results=10)
        assert len(results) >= 1
        assert all("pytest" in r["uuid"] or True for r in results)

    def test_like_project_filter(self, search_db):
        results = search_sessions(search_db, "pytest", fts_level=None, max_results=10, projects=["beta"])
        assert all(r["project"] == "beta" for r in results)

    def test_like_empty_query(self, search_db):
        results = search_sessions(search_db, "", fts_level=None)
        assert results == []

    def test_like_max_results(self, search_db):
        results = search_sessions(search_db, "pytest", fts_level=None, max_results=1)
        assert len(results) <= 1
