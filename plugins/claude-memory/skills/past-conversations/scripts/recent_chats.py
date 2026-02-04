#!/usr/bin/env python3
"""
Retrieve recent conversation sessions from the memory database.

Returns markdown by default (token-efficient), JSON with --format json.
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

# Local imports
from memory_utils import (
    DEFAULT_DB_PATH,
    format_markdown_session,
    format_json_sessions,
)


def get_recent_sessions(
    conn: sqlite3.Connection,
    n: int = 3,
    sort_order: str = "desc",
    before: str | None = None,
    after: str | None = None,
    projects: list[str] | None = None,
    verbose: bool = False
) -> list[dict]:
    """Get n most recent sessions with all their messages."""
    cursor = conn.cursor()

    sql = """
        SELECT s.id, s.uuid, b.started_at, b.ended_at, b.exchange_count,
               b.files_modified, b.commits, s.git_branch,
               p.name as project, p.path as project_path,
               b.id as branch_db_id
        FROM sessions s
        JOIN branches b ON b.session_id = s.id AND b.is_active = 1
        JOIN projects p ON s.project_id = p.id
        WHERE 1=1
    """
    params = []

    if before:
        sql += " AND b.started_at < ?"
        params.append(before)

    if after:
        sql += " AND b.started_at > ?"
        params.append(after)

    if projects:
        placeholders = ",".join("?" * len(projects))
        sql += f" AND p.name IN ({placeholders})"
        params.extend(projects)

    order = "DESC" if sort_order == "desc" else "ASC"
    sql += f" ORDER BY b.ended_at {order} LIMIT ?"
    params.append(n)

    cursor.execute(sql, params)
    sessions = cursor.fetchall()

    results = []

    for session in sessions:
        (_session_id, uuid, started_at, ended_at, _exchange_count,
         files_json, commits_json, git_branch, project, _project_path, branch_db_id) = session

        cursor.execute("""
            SELECT m.role, m.content, m.timestamp
            FROM branch_messages bm
            JOIN messages m ON bm.message_id = m.id
            WHERE bm.branch_id = ?
            ORDER BY m.timestamp ASC
        """, (branch_db_id,))

        messages = [{"role": r, "content": c, "timestamp": t} for r, c, t in cursor.fetchall()]

        session_data = {
            "uuid": uuid,
            "project": project,
            "started_at": started_at,
            "ended_at": ended_at,
            "git_branch": git_branch,
            "messages": messages
        }

        if verbose:
            session_data["files_modified"] = json.loads(files_json) if files_json else []
            session_data["commits"] = json.loads(commits_json) if commits_json else []

        results.append(session_data)

    return results


def format_markdown(sessions: list[dict], verbose: bool = False) -> str:
    """Format sessions as markdown."""
    if not sessions:
        return "No sessions found."

    lines = [f"# Recent Conversations ({len(sessions)} sessions)\n"]
    for session in sessions:
        lines.append(format_markdown_session(session, verbose=verbose))

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Get recent conversation sessions")
    parser.add_argument("--n", "-n", type=int, default=3, help="Number of sessions (1-20, default: 3)")
    parser.add_argument("--sort-order", choices=["desc", "asc"], default="desc", help="Sort order (default: desc)")
    parser.add_argument("--before", type=str, help="Sessions before this datetime (ISO)")
    parser.add_argument("--after", type=str, help="Sessions after this datetime (ISO)")
    parser.add_argument("--project", type=str, help="Filter by project name(s), comma-separated")
    parser.add_argument("--format", choices=["markdown", "json"], default="markdown", help="Output format (default: markdown)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Include files_modified and commits")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="Database path")

    args = parser.parse_args()
    n = max(1, min(20, args.n))
    projects = [p.strip() for p in args.project.split(",")] if args.project else None

    if not args.db.exists():
        if args.format == "json":
            print(json.dumps({"error": "Database not found", "sessions": [], "total_sessions": 0}))
        else:
            print("Error: Database not found. Run memory setup first.")
        sys.exit(1)

    try:
        conn = sqlite3.connect(args.db)
        sessions = get_recent_sessions(conn, n=n, sort_order=args.sort_order,
                                        before=args.before, after=args.after,
                                        projects=projects, verbose=args.verbose)
        conn.close()

        if args.format == "json":
            print(format_json_sessions(sessions))
        else:
            print(format_markdown(sessions, verbose=args.verbose))

    except Exception as e:
        if args.format == "json":
            print(json.dumps({"error": str(e), "sessions": [], "total_sessions": 0}))
        else:
            print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
