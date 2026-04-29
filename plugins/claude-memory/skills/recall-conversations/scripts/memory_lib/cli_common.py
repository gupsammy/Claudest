"""
Shared CLI plumbing for recall-conversations scripts.

Provides:
- Common argument parsing (--project, --all-projects, --limit, --json, etc.)
- Project auto-detection from CWD against the projects table
- Structured stderr error/warning emission for agent callers
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Optional

from .db import DEFAULT_DB_PATH


def _get_plugin_version() -> str:
    """Read version from plugin.json. Returns 'unknown' on any failure."""
    try:
        plugin_root = Path(__file__).resolve().parents[4]
        plugin_json = plugin_root / ".claude-plugin" / "plugin.json"
        with open(plugin_json) as f:
            return json.load(f).get("version", "unknown")
    except Exception:
        return "unknown"


PLUGIN_VERSION = _get_plugin_version()
LIMIT_MIN = 1
LIMIT_MAX = 50


def resolve_project(cwd: str, conn: sqlite3.Connection) -> Optional[list[str]]:
    """
    Resolve current project from CWD by walking up the path tree against
    projects.path in the DB. Returns [project_name] or None if no match.

    Strategy:
    1. Walk up from CWD; for each ancestor, look up projects.path = ancestor.
    2. If no path match, try projects.name = basename(cwd) as a fallback.
    3. If both fail, return None — caller decides how to handle (warn + widen).
    """
    cur = os.path.abspath(cwd)
    cursor = conn.cursor()
    while True:
        cursor.execute("SELECT name FROM projects WHERE path = ?", (cur,))
        row = cursor.fetchone()
        if row and row[0]:
            return [row[0]]
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent

    derived = os.path.basename(os.path.abspath(cwd))
    if derived:
        cursor.execute("SELECT 1 FROM projects WHERE name = ?", (derived,))
        if cursor.fetchone():
            return [derived]
    return None


def add_common_args(parser: argparse.ArgumentParser, default_limit: int = 5) -> None:
    """Add the shared CLI flags to a parser."""
    parser.add_argument(
        "--limit", "-n", type=int, default=default_limit,
        help=f"Number of sessions ({LIMIT_MIN}-{LIMIT_MAX}, default: {default_limit})"
    )
    parser.add_argument("--n", type=int, dest="limit", help=argparse.SUPPRESS)
    parser.add_argument("--max-results", type=int, dest="limit", help=argparse.SUPPRESS)

    scope = parser.add_mutually_exclusive_group()
    scope.add_argument(
        "--project", type=str,
        help="Filter by project name(s), comma-separated. "
             "Default: auto-detect from CWD."
    )
    scope.add_argument(
        "--all-projects", action="store_true",
        help="Search across all projects (overrides auto-detect)."
    )

    fmt_group = parser.add_mutually_exclusive_group()
    fmt_group.add_argument(
        "--format", choices=["markdown", "json"], default="markdown",
        help="Output format (default: markdown)."
    )
    fmt_group.add_argument(
        "--json", action="store_true",
        help="Alias for --format json."
    )

    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Include files_modified, commits, tool_counts.")
    parser.add_argument("--include-notifications", action="store_true",
                        help="Include task notification messages (hidden by default).")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH,
                        help=f"Database path (default: {DEFAULT_DB_PATH}).")
    parser.add_argument("--cwd", type=str, default=None,
                        help="Override CWD for project auto-detect (default: current working directory).")
    parser.add_argument("--version", action="version",
                        version=f"recall-conversations {PLUGIN_VERSION}")


def resolve_format(args: argparse.Namespace) -> str:
    """Resolve --json alias to canonical format value."""
    return "json" if getattr(args, "json", False) else args.format


def validate_limit(args: argparse.Namespace, fmt: str) -> int:
    """Validate --limit is in range; emit structured error and exit on failure."""
    if not (LIMIT_MIN <= args.limit <= LIMIT_MAX):
        emit_error(
            "invalid_limit",
            f"--limit must be in [{LIMIT_MIN},{LIMIT_MAX}], got {args.limit}",
            f"--limit 20  (any value in {LIMIT_MIN}-{LIMIT_MAX})",
            fmt,
        )
        sys.exit(2)
    return args.limit


def resolve_scope(
    args: argparse.Namespace,
    conn: sqlite3.Connection,
    fmt: str,
) -> tuple[Optional[list[str]], bool]:
    """
    Resolve project scope from args. Returns (projects_or_none, auto_detected).

    - If --all-projects: returns (None, False) — no filter.
    - If --project NAME[,NAME]: returns ([names...], False).
    - Otherwise: auto-detect from CWD. If auto-detect succeeds, returns ([name], True).
      If auto-detect fails, prints stderr warning and returns (None, False) — falls
      through to all-projects so the user gets *some* result rather than zero.
    """
    if args.all_projects:
        return None, False
    if args.project:
        return [p.strip() for p in args.project.split(",") if p.strip()], False

    cwd = args.cwd or os.getcwd()
    projects = resolve_project(cwd, conn)
    if projects is not None:
        return projects, True

    emit_warning(
        f"Could not auto-detect project for CWD {cwd}; searching all projects. "
        "Pass --project NAME or --all-projects to make scope explicit.",
        fmt,
    )
    return None, False


def emit_error(code: str, message: str, hint: Optional[str], fmt: str) -> None:
    """Emit a structured error to stderr."""
    if fmt == "json":
        payload = {"error": code, "message": message, "hint": hint}
        sys.stderr.write(json.dumps(payload) + "\n")
    else:
        sys.stderr.write(f"Error: {message}\n")
        if hint:
            sys.stderr.write(f"Hint: {hint}\n")


def emit_warning(message: str, fmt: str) -> None:
    """Emit a warning to stderr."""
    if fmt == "json":
        sys.stderr.write(json.dumps({"warning": message}) + "\n")
    else:
        sys.stderr.write(f"WARN: {message}\n")


def open_db_or_exit(db_path: Path, fmt: str) -> sqlite3.Connection:
    """Open the DB. If it doesn't exist or fails to open, emit structured error and exit."""
    if not db_path.exists():
        emit_error(
            "db_not_found",
            f"Database not found at {db_path}",
            "ls ~/.claude-memory/",
            fmt,
        )
        sys.exit(1)
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn
    except sqlite3.OperationalError as e:
        emit_error("db_open_failed", str(e), None, fmt)
        sys.exit(1)
