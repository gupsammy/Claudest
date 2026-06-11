#!/usr/bin/env python3
"""
List/resolve projects in the memory database.

Maps each project NAME to its canonical path + encoded key (the transcript-dir
name), with session counts and date span. Exists because project names are NOT
unique — the same basename (e.g. two 'EzyCopy') maps to different paths, so
`recent_chats.py --project NAME` can silently pull the wrong project or merge
both. Resolve here first, then target by the disambiguated name (and verify the
path) before mining.

Doubles as the sibling-directory candidate generator: --match TOKEN surfaces
every project whose name OR path contains the token, so a project scattered
across CWDs (e.g. a skill that began inside another repo) shows all its homes.

Returns markdown by default, JSON with --json or --format json.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from memory_lib.cli_common import emit_error, open_db_or_exit, resolve_format, PLUGIN_VERSION
from memory_lib.db import DEFAULT_DB_PATH


EXAMPLES = """
EXAMPLES
  All projects, most-recently-active first:
    list_projects.py

  Resolve an ambiguous name to its path(s) + key(s):
    list_projects.py --match EzyCopy

  Find every home of a scattered project (sibling-dir discovery):
    list_projects.py --match wiki --json | jq '.projects[] | {name, path, sessions}'
"""


def get_projects(conn: sqlite3.Connection, match: str | None) -> list[dict]:
    """Return one row per project: name, path, key, session_count, date span."""
    cursor = conn.cursor()
    sql = """
        SELECT p.name, p.path, p.key,
               COUNT(DISTINCT s.id) AS sessions,
               MIN(b.started_at) AS first_seen,
               MAX(b.ended_at) AS last_seen
        FROM projects p
        LEFT JOIN sessions s ON s.project_id = p.id
        LEFT JOIN branches b ON b.session_id = s.id AND b.is_active = 1
    """
    params: list = []
    if match:
        sql += " WHERE p.name LIKE ? ESCAPE '\\' OR p.path LIKE ? ESCAPE '\\'"
        escaped = match.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        params.extend([f"%{escaped}%", f"%{escaped}%"])
    sql += " GROUP BY p.id ORDER BY last_seen DESC"

    cursor.execute(sql, params)
    return [
        {
            "name": name,
            "path": path,
            "key": key,
            "sessions": sessions,
            "first_seen": first_seen,
            "last_seen": last_seen,
        }
        for name, path, key, sessions, first_seen, last_seen in cursor.fetchall()
    ]


def format_markdown(rows: list[dict], match: str | None) -> str:
    if not rows:
        return f"No projects found{f' matching {match!r}' if match else ''}."
    header = f"# Projects ({len(rows)}{f', matching {match!r}' if match else ''})\n"
    lines = [header]
    for r in rows:
        span = f"{(r['first_seen'] or '?')[:10]}..{(r['last_seen'] or '?')[:10]}"
        lines.append(f"- {r['name']}  [{r['sessions']} sessions, {span}]")
        lines.append(f"    path: {r['path']}")
        lines.append(f"    key:  {r['key']}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="List/resolve projects in the memory database (name -> path + key).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=EXAMPLES,
    )
    parser.add_argument("--match", type=str, default=None,
                        help="Substring filter over project name OR path (case-insensitive). "
                             "Use to resolve ambiguous names or discover a project's sibling dirs.")
    fmt_group = parser.add_mutually_exclusive_group()
    fmt_group.add_argument("--format", choices=["markdown", "json"], default="markdown",
                           help="Output format (default: markdown).")
    fmt_group.add_argument("--json", action="store_true", help="Alias for --format json.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH,
                        help=f"Database path (default: {DEFAULT_DB_PATH}).")
    parser.add_argument("--version", action="version",
                        version=f"recall-conversations {PLUGIN_VERSION}")

    args = parser.parse_args()
    fmt = resolve_format(args)

    conn = open_db_or_exit(args.db, fmt)
    try:
        rows = get_projects(conn, args.match)
    except Exception as e:
        emit_error("query_failed", str(e), None, fmt)
        sys.exit(1)
    finally:
        conn.close()

    if fmt == "json":
        print(json.dumps({"projects": rows, "count": len(rows), "match": args.match}, indent=2))
    else:
        print(format_markdown(rows, args.match))


if __name__ == "__main__":
    main()
