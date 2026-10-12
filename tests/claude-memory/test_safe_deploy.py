"""Tests for upgrade safety: one migrator per DB, atomic backups, no foreground migration.

Coverage:
1. Concurrent connections to a v5 DB back up and migrate exactly once.
2. A live migration lock defers the migration; a stale one is stolen.
3. migrate=False applies no data migration and takes no backup.
4. A fresh DB is stamped with the latest version and takes no backups.
5. A backup that dies mid-copy leaves neither a final-named file nor a .partial.
6. memory-setup leaves the migration to the background backfill worker.
7. A second backfill exits while the first holds its lock.
"""

from __future__ import annotations

import importlib.util
import os
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path

import memory_lib.db as db_module
from memory_lib.db import LATEST_DATA_VERSION, MIGRATE_LOCK_NAME, get_db_connection
from test_harness_turns import COMPACTION, _db_at_v5

SCRIPTS_DIR = Path(db_module.__file__).resolve().parents[1]
HOOKS_DIR = Path(__file__).resolve().parents[2] / "plugins/claude-memory/hooks"


def _load_hook(name: str):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HOOKS_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(HOOKS_DIR))
    spec.loader.exec_module(module)
    return module


def _version(db_file: Path) -> int:
    conn = sqlite3.connect(db_file)
    try:
        return conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()


def _backups(tmp_path: Path) -> list[Path]:
    return sorted(tmp_path.glob("conversations.pre-*"))


class TestConcurrentMigration:
    def test_racing_connections_back_up_and_migrate_once(self, tmp_path):
        db_file = tmp_path / "conversations.db"
        _db_at_v5(db_file)
        # Each process slows its backup so all of them reach the v6 gate together.
        child = textwrap.dedent(f"""
            import sys, time
            sys.path.insert(0, {str(SCRIPTS_DIR)!r})
            import memory_lib.db as d
            real = d._backup_db_before_migration
            def slow(path, label):
                print("BACKUP", flush=True)
                time.sleep(0.5)
                return real(path, label)
            d._backup_db_before_migration = slow
            d.get_db_connection({{"db_path": {str(db_file)!r}}}).close()
        """)
        procs = [subprocess.Popen([sys.executable, "-c", child], stdout=subprocess.PIPE, text=True)
                 for _ in range(4)]
        outputs = [p.communicate(timeout=60)[0] for p in procs]

        assert all(p.returncode == 0 for p in procs)
        assert sum(out.count("BACKUP") for out in outputs) == 1
        assert _version(db_file) == 6
        conn = sqlite3.connect(db_file)
        assert [r[0] for r in conn.execute("SELECT content FROM messages WHERE is_notification = 1")] == [COMPACTION]
        conn.close()
        # Every migrator released the lock.
        assert db_module.acquire_lock(tmp_path / MIGRATE_LOCK_NAME) == os.getpid()
        db_module.release_lock(tmp_path / MIGRATE_LOCK_NAME)


class TestMigrationLock:
    def test_live_lock_defers_then_migration_runs(self, tmp_path, monkeypatch):
        monkeypatch.setattr(db_module, "MIGRATE_LOCK_WAIT_SECONDS", 0)
        db_file = tmp_path / "conversations.db"
        _db_at_v5(db_file)
        lock = tmp_path / MIGRATE_LOCK_NAME
        assert db_module.acquire_lock(lock)  # a live migrator holds it

        get_db_connection({"db_path": str(db_file)}).close()
        assert _version(db_file) == 5
        assert _backups(tmp_path) == []

        db_module.release_lock(lock)
        get_db_connection({"db_path": str(db_file)}).close()
        assert _version(db_file) == 6

    def test_lock_left_by_killed_migrator_does_not_block(self, tmp_path):
        db_file = tmp_path / "conversations.db"
        _db_at_v5(db_file)
        holder = subprocess.Popen(
            [sys.executable, "-c",
             f"import sys, time; sys.path.insert(0, {str(SCRIPTS_DIR)!r}); from pathlib import Path;"
             f"from memory_lib.db import acquire_lock; acquire_lock(Path({str(tmp_path / MIGRATE_LOCK_NAME)!r}));"
             "print('held', flush=True); time.sleep(60)"],
            stdout=subprocess.PIPE, text=True,
        )
        assert holder.stdout.readline().strip() == "held"
        holder.kill()  # dies mid-migration, no cleanup
        holder.wait()

        get_db_connection({"db_path": str(db_file)}).close()

        assert _version(db_file) == 6


class TestNoForegroundMigration:
    def test_migrate_false_skips_data_migrations(self, tmp_path):
        db_file = tmp_path / "conversations.db"
        _db_at_v5(db_file)

        conn = get_db_connection({"db_path": str(db_file)}, migrate=False)

        assert conn.execute("PRAGMA user_version").fetchone()[0] == 5
        assert conn.execute("SELECT COUNT(*) FROM messages WHERE is_notification = 1").fetchone()[0] == 0
        conn.close()
        assert _backups(tmp_path) == []

    def test_fresh_db_is_stamped_latest_without_backups(self, tmp_path):
        db_file = tmp_path / "conversations.db"

        get_db_connection({"db_path": str(db_file)}).close()

        assert _version(db_file) == LATEST_DATA_VERSION
        assert _backups(tmp_path) == []


class TestAtomicBackup:
    def test_copy_that_dies_midway_leaves_no_file(self, tmp_path, monkeypatch):
        db_file = tmp_path / "conversations.db"
        _db_at_v5(db_file)
        real_connect = sqlite3.connect

        seen_mid_copy = []

        class DiesAfterCopy:
            """Source connection whose backup writes every page, then dies like a killed process."""
            def __init__(self, conn):
                self._conn = conn
            def backup(self, dst):
                self._conn.backup(dst)
                # A kill here skips all cleanup, so what is on disk now is what survives.
                seen_mid_copy.extend(p.name for p in tmp_path.glob("conversations.pre-*.db"))
                raise OSError("killed")
            def close(self):
                self._conn.close()

        def connect(path, *args, **kwargs):
            conn = real_connect(path, *args, **kwargs)
            return DiesAfterCopy(conn) if Path(path) == db_file else conn

        monkeypatch.setattr(db_module.sqlite3, "connect", connect)
        assert db_module._backup_db_before_migration(db_file, "v6-harness-turns") is False
        monkeypatch.undo()

        assert seen_mid_copy == [], "a killed copy must never carry a valid backup name"
        assert _backups(tmp_path) == []

    def test_completed_backup_is_a_valid_copy(self, tmp_path):
        db_file = tmp_path / "conversations.db"
        _db_at_v5(db_file)

        assert db_module._backup_db_before_migration(db_file, "v6-harness-turns") is True

        # One self-contained file: no .partial, no orphaned -wal/-shm.
        [backup] = _backups(tmp_path)
        assert backup.suffix == ".db"
        # Opens read-only, as an inspection or restore would; a WAL-mode copy cannot.
        conn = sqlite3.connect(f"file:{backup}?mode=ro", uri=True)
        assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 6
        conn.close()


class TestHooks:
    def test_setup_hands_pending_migration_to_background(self, tmp_path, monkeypatch, capsys):
        db_file = tmp_path / "conversations.db"
        _db_at_v5(db_file)
        setup = _load_hook("memory-setup")
        # get_db_connection() with no settings resolves db_module.DEFAULT_DB_PATH.
        monkeypatch.setattr(db_module, "DEFAULT_DB_PATH", db_file)
        monkeypatch.setattr(setup, "DEFAULT_DB_PATH", db_file)
        monkeypatch.setattr(setup, "_needs_backfill", lambda: False)
        monkeypatch.setattr(setup, "_codex_sessions_newer_than_sentinel", lambda: False)
        spawned = []
        monkeypatch.setattr(setup, "_spawn_background", lambda name, *a: spawned.append(name))

        setup.main()

        assert _version(db_file) == 5  # nothing migrated in the foreground
        assert _backups(tmp_path) == []
        assert spawned == ["backfill_summaries.py"]
        assert '"continue": true' in capsys.readouterr().out

    def test_second_backfill_exits_while_first_holds_lock(self, tmp_path, monkeypatch):
        backfill = _load_hook("backfill_summaries")
        lock = tmp_path / "backfill.lock"
        assert db_module.acquire_lock(lock)  # the first backfill is running
        monkeypatch.setattr(backfill, "BACKFILL_LOCK_PATH", lock)
        ran = []
        monkeypatch.setattr(backfill, "backfill", lambda *a: ran.append(True))

        backfill.main()
        assert ran == []

        db_module.release_lock(lock)
        backfill.main()
        assert ran == [True]
        assert db_module.acquire_lock(lock)  # released after running
        db_module.release_lock(lock)
