"""Shared fixtures for claude-memory tests."""

import sqlite3
from pathlib import Path

import pytest

from memory_lib.db import SCHEMA, _migrate_columns

FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def memory_db():
    """In-memory SQLite database with full v3 schema applied."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA)
    conn.commit()
    _migrate_columns(conn)
    yield conn
    conn.close()



@pytest.fixture(params=sorted(FIXTURE_DIR.glob("*.jsonl")), ids=lambda p: p.stem)
def jsonl_fixture(request):
    """Parameterized fixture yielding each JSONL file path."""
    return request.param
