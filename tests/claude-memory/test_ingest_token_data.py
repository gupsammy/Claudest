#!/usr/bin/env python3
"""Tests for ingest_token_data — token ingest append-only behaviour.

Gap 4: turns must be skip-if-exists on reimport, and session_metrics totals
must not double when the same session is ingested twice.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

# Add the ingest script's directory to sys.path so imports resolve
_INGEST_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "plugins" / "claude-memory" / "skills"
    / "get-token-insights" / "scripts"
)
sys.path.insert(0, str(_INGEST_DIR))

from ingest_token_data import (
    JnlFile,
    ParsedSession,
    Turn,
    ensure_schema,
    import_session,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal_jnl(tmp_path: Path) -> JnlFile:
    """A JnlFile pointing at a dummy path (file not actually read in these tests)."""
    fake_path = tmp_path / "fake-session.jsonl"
    fake_path.write_text("")
    return JnlFile(
        path=fake_path,
        project_cwd="/test/project",
        is_sidechain=False,
        parent_session_id=None,
    )


def _make_session(session_id: str, turns: list[Turn]) -> ParsedSession:
    """Build a minimal ParsedSession from an explicit turn list."""
    s = ParsedSession(session_id=session_id, project_path="/test/project")
    s.turns = turns
    return s


def _make_turn(index: int, input_tokens: int = 100, output_tokens: int = 50) -> Turn:
    return Turn(
        index=index,
        message_id=f"msg-{index}",
        timestamp=f"2026-03-01T10:0{index}:00Z",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


@pytest.fixture
def token_db():
    """In-memory DB with full token ingest schema."""
    conn = sqlite3.connect(":memory:")
    ensure_schema(conn)
    conn.commit()
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# Gap 4a — turns are append-only (skip-if-exists)
# ---------------------------------------------------------------------------

class TestTurnsAppendOnly:
    """Importing the same session twice must not duplicate turn rows."""

    def test_first_import_creates_turns(self, token_db, tmp_path):
        """First ingest writes one row per turn into the turns table."""
        jnl = _minimal_jnl(tmp_path)
        session = _make_session("sess-abc", [_make_turn(1), _make_turn(2)])

        import_session(token_db, session, jnl)
        token_db.commit()

        count = token_db.execute(
            "SELECT COUNT(*) FROM turns WHERE session_id = 'sess-abc'"
        ).fetchone()[0]
        assert count == 2, f"Expected 2 turn rows after first import, got {count}"

    def test_second_import_skips_existing_turns(self, token_db, tmp_path):
        """Second ingest of the same session must not add new turn rows."""
        jnl = _minimal_jnl(tmp_path)
        session = _make_session("sess-dedup", [_make_turn(1), _make_turn(2)])

        import_session(token_db, session, jnl)
        token_db.commit()

        # Import identical session again
        import_session(token_db, session, jnl)
        token_db.commit()

        count = token_db.execute(
            "SELECT COUNT(*) FROM turns WHERE session_id = 'sess-dedup'"
        ).fetchone()[0]
        assert count == 2, (
            f"Duplicate import must not create extra turn rows — got {count}"
        )

    def test_repeated_imports_no_duplicate_turn_index(self, token_db, tmp_path):
        """(session_id, turn_index) uniqueness must hold after multiple imports."""
        jnl = _minimal_jnl(tmp_path)
        session = _make_session("sess-unique", [_make_turn(1), _make_turn(2)])

        for _ in range(3):
            import_session(token_db, session, jnl)
            token_db.commit()

        rows = token_db.execute("""
            SELECT session_id, turn_index, COUNT(*) AS cnt
            FROM turns
            WHERE session_id = 'sess-unique'
            GROUP BY session_id, turn_index
            HAVING cnt > 1
        """).fetchall()
        assert rows == [], (
            f"(session_id, turn_index) must be unique — duplicates found: {rows}"
        )


# ---------------------------------------------------------------------------
# Gap 4b — session_metrics totals do not double on reimport
# ---------------------------------------------------------------------------

class TestSessionMetricsStableOnReimport:
    """session_metrics totals must reflect the session once, not accumulate."""

    def test_total_input_tokens_not_doubled(self, token_db, tmp_path):
        """total_input_tokens in session_metrics must be the same after two imports."""
        jnl = _minimal_jnl(tmp_path)
        # Two turns with known token counts
        session = _make_session(
            "sess-tokens",
            [_make_turn(1, input_tokens=200), _make_turn(2, input_tokens=300)],
        )

        import_session(token_db, session, jnl)
        token_db.commit()

        first_total = token_db.execute(
            "SELECT total_input_tokens FROM session_metrics WHERE session_id = 'sess-tokens'"
        ).fetchone()[0]
        assert first_total == 500, f"Expected 500 input tokens after first import, got {first_total}"

        # Reimport same session
        import_session(token_db, session, jnl)
        token_db.commit()

        second_total = token_db.execute(
            "SELECT total_input_tokens FROM session_metrics WHERE session_id = 'sess-tokens'"
        ).fetchone()[0]
        assert second_total == first_total, (
            f"total_input_tokens doubled after reimport: {first_total} → {second_total}. "
            "session_metrics must use INSERT OR REPLACE (idempotent upsert), not accumulate."
        )

    def test_turn_count_not_doubled(self, token_db, tmp_path):
        """session_metrics.turn_count must equal the number of turns, not double on reimport."""
        jnl = _minimal_jnl(tmp_path)
        session = _make_session("sess-count", [_make_turn(1), _make_turn(2)])

        import_session(token_db, session, jnl)
        token_db.commit()

        import_session(token_db, session, jnl)
        token_db.commit()

        turn_count = token_db.execute(
            "SELECT turn_count FROM session_metrics WHERE session_id = 'sess-count'"
        ).fetchone()[0]
        assert turn_count == 2, (
            f"turn_count must be 2 (number of turns), not {turn_count} — "
            "signals that session_metrics is being summed rather than replaced"
        )

    def test_session_metrics_row_exists_after_import(self, token_db, tmp_path):
        """Exactly one session_metrics row must exist after two imports of the same session."""
        jnl = _minimal_jnl(tmp_path)
        session = _make_session("sess-single", [_make_turn(1)])

        import_session(token_db, session, jnl)
        token_db.commit()
        import_session(token_db, session, jnl)
        token_db.commit()

        count = token_db.execute(
            "SELECT COUNT(*) FROM session_metrics WHERE session_id = 'sess-single'"
        ).fetchone()[0]
        assert count == 1, (
            f"Expected exactly 1 session_metrics row, got {count} — "
            "INSERT OR REPLACE must upsert, not insert a second row"
        )


# ---------------------------------------------------------------------------
# token_import_log schema
# ---------------------------------------------------------------------------

class TestTokenImportLogSchema:
    """token_import_log must exist with the correct columns after ensure_schema."""

    def test_table_exists(self, token_db):
        """token_import_log must be present after schema initialisation."""
        row = token_db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='token_import_log'"
        ).fetchone()
        assert row is not None, "token_import_log table is missing after ensure_schema"

    def test_required_columns_present(self, token_db):
        """token_import_log must have file_path, mtime_ns, imported_at, session_id, turn_count."""
        cursor = token_db.execute("PRAGMA table_info(token_import_log)")
        columns = {row[1] for row in cursor.fetchall()}
        required = {"file_path", "mtime_ns", "imported_at", "session_id", "turn_count"}
        missing = required - columns
        assert not missing, f"token_import_log is missing columns: {missing}"

    def test_file_path_unique_constraint(self, token_db, tmp_path):
        """file_path must be UNIQUE — second insert for the same path must use INSERT OR REPLACE."""
        f = tmp_path / "dup.jsonl"
        f.write_text("")
        token_db.execute(
            "INSERT INTO token_import_log (file_path, mtime_ns) VALUES (?, ?)",
            (str(f), 1000),
        )
        token_db.commit()
        # A plain INSERT should violate the unique constraint
        with pytest.raises(sqlite3.IntegrityError):
            token_db.execute(
                "INSERT INTO token_import_log (file_path, mtime_ns) VALUES (?, ?)",
                (str(f), 2000),
            )
            token_db.commit()

    def test_import_log_not_present(self, token_db):
        """The legacy import_log table must NOT exist — ensure_schema drops it on v4 init."""
        row = token_db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='import_log'"
        ).fetchone()
        assert row is None, (
            "Legacy import_log table must not exist in a v4 schema — "
            "token_import_log is the authoritative source for ingest state"
        )


# ---------------------------------------------------------------------------
# should_skip_file
# ---------------------------------------------------------------------------

class TestShouldSkipFile:
    """should_skip_file must gate re-ingestion correctly based on mtime."""

    def test_new_file_not_skipped(self, token_db, tmp_path):
        """A file with no entry in token_import_log must return False."""
        # Prevents: new JSONL files silently ignored after a fresh schema install
        from ingest_token_data import should_skip_file
        f = tmp_path / "new-session.jsonl"
        f.write_text("")
        assert should_skip_file(token_db, f) is False

    def test_already_imported_same_mtime_skipped(self, token_db, tmp_path):
        """A file already recorded with its current mtime must return True."""
        # Prevents: files being re-parsed on every run, inflating cost metrics
        from ingest_token_data import record_import, should_skip_file
        f = tmp_path / "imported-session.jsonl"
        f.write_text("")
        record_import(token_db, f, "sess-skip", turn_count=3)
        token_db.commit()
        assert should_skip_file(token_db, f) is True

    def test_updated_mtime_not_skipped(self, token_db, tmp_path):
        """A file recorded with an older mtime must return False so it is re-ingested."""
        # Prevents: appended JSONL files (new turns) being silently skipped
        from ingest_token_data import should_skip_file
        f = tmp_path / "updated-session.jsonl"
        f.write_text("")
        stale_mtime = f.stat().st_mtime_ns - 1_000_000  # 1 ms in the past
        token_db.execute(
            "INSERT INTO token_import_log (file_path, mtime_ns) VALUES (?, ?)",
            (str(f), stale_mtime),
        )
        token_db.commit()
        assert should_skip_file(token_db, f) is False

    def test_missing_file_skipped(self, token_db, tmp_path):
        """A file that no longer exists on disk must return True (cannot be read)."""
        from ingest_token_data import should_skip_file
        ghost = tmp_path / "gone.jsonl"
        # ghost does not exist; should_skip_file catches OSError and returns True
        assert should_skip_file(token_db, ghost) is True


# ---------------------------------------------------------------------------
# record_import
# ---------------------------------------------------------------------------

class TestRecordImport:
    """record_import must write and overwrite token_import_log rows correctly."""

    def test_inserts_new_row(self, token_db, tmp_path):
        """First call must create a row in token_import_log with correct values."""
        # Prevents: import state not being persisted, causing every run to re-ingest everything
        from ingest_token_data import record_import
        f = tmp_path / "record-new.jsonl"
        f.write_text("")
        record_import(token_db, f, "sess-new", turn_count=5)
        token_db.commit()

        row = token_db.execute(
            "SELECT session_id, turn_count, mtime_ns FROM token_import_log WHERE file_path = ?",
            (str(f),),
        ).fetchone()
        assert row is not None, "record_import must create a row in token_import_log"
        session_id, turn_count, mtime_ns = row
        assert session_id == "sess-new"
        assert turn_count == 5
        assert mtime_ns == f.stat().st_mtime_ns

    def test_updates_existing_row(self, token_db, tmp_path):
        """Second call for the same file must overwrite the existing row, not insert a duplicate."""
        # Prevents: unique constraint violations crashing the ingest loop on re-runs
        from ingest_token_data import record_import
        f = tmp_path / "record-update.jsonl"
        f.write_text("")
        record_import(token_db, f, "sess-v1", turn_count=2)
        token_db.commit()

        # Simulate more turns being added (mtime changes after a write)
        f.write_text("updated")
        record_import(token_db, f, "sess-v1", turn_count=7)
        token_db.commit()

        rows = token_db.execute(
            "SELECT turn_count, mtime_ns FROM token_import_log WHERE file_path = ?",
            (str(f),),
        ).fetchall()
        assert len(rows) == 1, f"Expected 1 row after upsert, got {len(rows)}"
        assert rows[0][0] == 7, "turn_count must be updated to latest value"
        assert rows[0][1] == f.stat().st_mtime_ns, "mtime_ns must reflect current file state"

    def test_imported_at_is_set(self, token_db, tmp_path):
        """imported_at must be a non-null datetime string after record_import."""
        from ingest_token_data import record_import
        f = tmp_path / "record-ts.jsonl"
        f.write_text("")
        record_import(token_db, f, "sess-ts", turn_count=1)
        token_db.commit()

        imported_at = token_db.execute(
            "SELECT imported_at FROM token_import_log WHERE file_path = ?",
            (str(f),),
        ).fetchone()[0]
        assert imported_at is not None, "imported_at must be set by record_import"


# ---------------------------------------------------------------------------
# Table isolation — token_import_log vs import_log
# ---------------------------------------------------------------------------

class TestTableIsolation:
    """token_import_log rows must never affect import_log and vice-versa."""

    def test_token_import_log_writes_do_not_create_import_log(self, token_db, tmp_path):
        """Writing to token_import_log must not cause import_log to appear."""
        from ingest_token_data import record_import
        f = tmp_path / "isolation-check.jsonl"
        f.write_text("")
        record_import(token_db, f, "sess-iso", turn_count=1)
        token_db.commit()

        row = token_db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='import_log'"
        ).fetchone()
        assert row is None, (
            "import_log must not be created as a side-effect of token_import_log writes"
        )

    def test_schema_version_table_present(self, token_db):
        """schema_version table must exist — used to gate v4 migration logic."""
        row = token_db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
        ).fetchone()
        assert row is not None, "schema_version table must exist after ensure_schema"

    def test_schema_version_is_4(self, token_db):
        """schema_version must be SCHEMA_VERSION (4) after a fresh ensure_schema."""
        from ingest_token_data import SCHEMA_VERSION
        version = token_db.execute(
            "SELECT version FROM schema_version"
        ).fetchone()[0]
        assert version == SCHEMA_VERSION, (
            f"Expected schema version {SCHEMA_VERSION}, got {version}"
        )


# ---------------------------------------------------------------------------
# v3 → v4 migration
# ---------------------------------------------------------------------------

def _v3_token_db() -> sqlite3.Connection:
    """Build an in-memory DB that mimics a pre-v4 token ingest schema.

    We let ensure_schema create all the standard tables via SCHEMA_SQL, then
    retroactively patch the DB to look like a v3 state:
    - Recreate the legacy import_log table that existed before v4
    - Set schema_version to 3 so ensure_schema triggers the upgrade path
    - Optionally seed stale token_import_log rows to verify they are cleared

    We do NOT pre-stub turns/session_metrics — ensure_schema must create them
    at their full column width to avoid OperationalError on ALTER TABLE calls.
    """
    from ingest_token_data import SCHEMA_SQL
    conn = sqlite3.connect(":memory:")
    # Apply the full schema so all tables exist with correct columns
    conn.executescript(SCHEMA_SQL)
    conn.commit()

    # Legacy table that must be dropped by v4 migration
    conn.execute("""
        CREATE TABLE IF NOT EXISTS import_log (
            id INTEGER PRIMARY KEY,
            file_path TEXT UNIQUE NOT NULL,
            file_hash TEXT,
            imported_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            messages_imported INTEGER DEFAULT 0
        )
    """)
    # Set schema_version to 3 to trigger the v4 upgrade path
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
    conn.execute("DELETE FROM schema_version")
    conn.execute("INSERT INTO schema_version (version) VALUES (3)")
    conn.commit()
    return conn


class TestV3ToV4Migration:
    """ensure_schema on a v3 DB must upgrade cleanly to v4."""

    def test_migration_bumps_schema_version(self):
        """After ensure_schema on a v3 DB, schema_version must be 4."""
        from ingest_token_data import SCHEMA_VERSION, ensure_schema
        conn = _v3_token_db()
        ensure_schema(conn)
        version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
        assert version == SCHEMA_VERSION
        conn.close()

    def test_migration_drops_legacy_import_log(self):
        """ensure_schema on a v3 DB must DROP the legacy import_log table."""
        # Prevents: legacy import_log rows for conversation files interfering with
        # the token ingest skip-file logic after a schema upgrade.
        from ingest_token_data import ensure_schema
        conn = _v3_token_db()
        # Confirm import_log exists before migration
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='import_log'"
        ).fetchone() is not None

        ensure_schema(conn)

        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='import_log'"
        ).fetchone()
        assert row is None, (
            "import_log must be dropped during v3→v4 migration — "
            "token_import_log is the sole ingest-state table post-v4"
        )
        conn.close()

    def test_migration_clears_stale_token_import_log(self):
        """ensure_schema on a v3 DB must DELETE all stale token_import_log rows."""
        # Prevents: old mtime fingerprints from a different schema causing files
        # to be permanently skipped after a schema upgrade forces full re-import.
        from ingest_token_data import ensure_schema
        conn = _v3_token_db()
        conn.execute(
            "INSERT INTO token_import_log (file_path, mtime_ns) VALUES ('/old/file.jsonl', 999)"
        )
        conn.commit()

        ensure_schema(conn)

        count = conn.execute("SELECT COUNT(*) FROM token_import_log").fetchone()[0]
        assert count == 0, (
            f"token_import_log must be cleared on schema upgrade, found {count} rows"
        )
        conn.close()

    def test_migration_token_import_log_functional_after_upgrade(self, tmp_path):
        """After v3→v4 migration, record_import and should_skip_file must work correctly."""
        from ingest_token_data import ensure_schema, record_import, should_skip_file
        conn = _v3_token_db()
        ensure_schema(conn)

        f = tmp_path / "post-migration.jsonl"
        f.write_text("")

        # File is new — must not be skipped
        assert should_skip_file(conn, f) is False

        record_import(conn, f, "sess-post-migration", turn_count=4)
        conn.commit()

        # Same mtime — must be skipped now
        assert should_skip_file(conn, f) is True
        conn.close()

    def test_migration_idempotent(self):
        """Calling ensure_schema twice on an already-v4 DB must be a safe no-op."""
        from ingest_token_data import SCHEMA_VERSION, ensure_schema
        conn = _v3_token_db()
        ensure_schema(conn)   # v3 → v4
        ensure_schema(conn)   # already v4, no-op
        version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
        assert version == SCHEMA_VERSION
        conn.close()
