"""Shared fixtures for claude-memory tests."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import memory_lib.db as db_module
from memory_lib.db import SCHEMA, _migrate_columns

FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    """Point load_config() at an absent file so the developer's real
    ~/.claude-memory/config.json (e.g. semantic_search on) never steers a test.
    Tests that need a config patch CONFIG_PATH themselves, which overrides this."""
    monkeypatch.setattr(db_module, "CONFIG_PATH", tmp_path / "no-config.json")


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
