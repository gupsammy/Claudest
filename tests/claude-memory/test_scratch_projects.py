"""Folderless chats: every scratch-workspace session belongs to one "(scratch)" project.

Coverage:
1. Import — two folderless chats in different scratch directories land in one project.
2. Existing rows — per-chat scratch projects from before the fold merge on next connection.
3. Lookup — a brand-new scratch directory resolves to that project's key, so
   SessionStart injects earlier folderless chats.
4. Recall scope — recall scripts run from a scratch directory (or a Codex worktree)
   scope to the project import filed it under, never to an ancestor project such
   as the home directory.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from memory_lib.cli_common import ScopeFilter, resolve_project
from memory_lib.db import get_db_connection
from memory_lib.formatting import SCRATCH_PROJECT_PATH, get_project_key

HOOKS_DIR = Path(__file__).resolve().parents[2] / "plugins/claude-memory/hooks"
sys.path.insert(0, str(HOOKS_DIR))
_spec = importlib.util.spec_from_file_location("import_conversations_scratch", HOOKS_DIR / "import_conversations.py")
import_conversations = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(import_conversations)

FIXTURE = Path(__file__).parent / "fixtures" / "linear_3_exchange.jsonl"
FIXTURE_CWD = "/Users/samarthgupta/repos/forks/node-banana"
FIXTURE_SESSION = "d99039db-adce-4135-8b0a-b5347e39c051"
ACCOUNT = "/Users/me/.claude-accounts/work/scratch-workspaces"


def _scratch_dir(n: int) -> str:
    return f"{ACCOUNT}/8069997{n}-dd66-4fa4-983b-f6b768d5eeb6/2c458eed-7cce-450a-888b-2c00e38755e{n}/scratch-2026-10-1{n}-91cd0{n}"


def _encoded(path: str) -> str:
    """Claude Code's projects-directory name for a cwd."""
    return path.replace("/", "-").replace(".", "-")


def _write_session(projects: Path, cwd: str, session_id: str) -> Path:
    project_dir = projects / _encoded(cwd)
    project_dir.mkdir(parents=True)
    text = FIXTURE.read_text().replace(FIXTURE_CWD, cwd).replace(FIXTURE_SESSION, session_id)
    (project_dir / f"{session_id}.jsonl").write_text(text)
    return project_dir


def test_folderless_chats_import_into_one_project(tmp_path):
    conn = get_db_connection({"db_path": str(tmp_path / "c.db")})
    for n in (1, 2):
        project_dir = _write_session(tmp_path / "projects", _scratch_dir(n), f"{n}" * 8 + FIXTURE_SESSION[8:])
        import_conversations.import_project(conn, project_dir, [])
    conn.commit()

    rows = conn.execute(
        "SELECT p.path, p.key, COUNT(s.id) FROM projects p JOIN sessions s ON s.project_id = p.id GROUP BY p.id"
    ).fetchall()
    assert rows == [(SCRATCH_PROJECT_PATH, get_project_key(SCRATCH_PROJECT_PATH), 2)]


def test_existing_per_chat_projects_fold_on_next_connection(tmp_path):
    db = {"db_path": str(tmp_path / "c.db")}
    conn = get_db_connection(db)
    for n in (1, 2):
        cwd = _scratch_dir(n)
        cur = conn.execute("INSERT INTO projects (path, key, name) VALUES (?, ?, ?)",
                           (cwd, _encoded(cwd), Path(cwd).name))
        conn.execute("INSERT INTO sessions (uuid, project_id, cwd) VALUES (?, ?, ?)",
                     (f"uuid-{n}", cur.lastrowid, cwd))
    conn.execute("INSERT INTO projects (path, key, name) VALUES ('/repo', '-repo', 'repo')")
    conn.commit()
    conn.close()

    conn = get_db_connection(db)
    rows = conn.execute(
        "SELECT p.path, p.key, COUNT(s.id) FROM projects p LEFT JOIN sessions s ON s.project_id = p.id "
        "GROUP BY p.id ORDER BY p.path"
    ).fetchall()
    assert rows == [(SCRATCH_PROJECT_PATH, get_project_key(SCRATCH_PROJECT_PATH), 2), ("/repo", "-repo", 0)]


def test_new_scratch_directory_resolves_to_scratch_key(tmp_path):
    conn = get_db_connection({"db_path": str(tmp_path / "c.db")})
    project_dir = _write_session(tmp_path / "projects", _scratch_dir(1), FIXTURE_SESSION)
    import_conversations.import_project(conn, project_dir, [])
    conn.commit()

    # A later folderless chat in a never-seen scratch dir, as memory-context computes it.
    new_cwd = f"{ACCOUNT}/aaaaaaaa-0000-4000-8000-000000000000/bbbbbbbb-0000-4000-8000-000000000000/scratch-2026-11-01-ffffff"
    assert conn.execute("SELECT COUNT(*) FROM projects WHERE key = ?", (get_project_key(new_cwd),)).fetchone()[0] == 1


def _add_home_project(conn) -> None:
    """A project at the account's home dir, an ancestor of every scratch dir."""
    cur = conn.execute("INSERT INTO projects (path, key, name) VALUES ('/Users/me', '-Users-me', 'me')")
    conn.execute("INSERT INTO sessions (uuid, project_id, cwd) VALUES ('home-1', ?, '/Users/me')", (cur.lastrowid,))
    conn.commit()


def test_recall_scope_from_scratch_dir_is_scratch_project(tmp_path):
    conn = get_db_connection({"db_path": str(tmp_path / "c.db")})
    project_dir = _write_session(tmp_path / "projects", _scratch_dir(1), FIXTURE_SESSION)
    import_conversations.import_project(conn, project_dir, [])
    _add_home_project(conn)

    assert resolve_project(_scratch_dir(2), conn) == ScopeFilter("path", [SCRATCH_PROJECT_PATH])


def test_recall_scope_from_scratch_dir_without_scratch_project_is_unresolved(tmp_path):
    """No scratch sessions yet: return None so the caller warns, not the home project."""
    conn = get_db_connection({"db_path": str(tmp_path / "c.db")})
    _add_home_project(conn)

    assert resolve_project(_scratch_dir(2), conn) is None


def test_recall_scope_from_codex_worktree_is_main_checkout(tmp_path):
    conn = get_db_connection({"db_path": str(tmp_path / "c.db")})
    _add_home_project(conn)
    conn.execute("INSERT INTO projects (path, key, name) VALUES ('/r/tiny', '-r-tiny', 'tiny')")
    conn.commit()

    assert resolve_project("/Users/me/.codex/worktrees/aa5e/tiny/src", conn) == ScopeFilter("path", ["/r/tiny"])
