#!/usr/bin/env python3
"""
Retrieve recent conversation sessions from the memory database.

Defaults to the current project (auto-detected from CWD). Use --project NAME
to target a specific project, or --all-projects to widen scope.

Returns markdown by default (token-efficient), JSON with --json or --format json.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys

from memory_lib.cli_common import (
    add_common_args,
    emit_error,
    open_db_or_exit,
    resolve_format,
    resolve_scope,
    validate_limit,
)
from memory_lib.formatting import format_markdown_session, format_json_sessions


EXAMPLES = """
EXAMPLES
  Last 5 sessions in current project (auto-detected):
    recent_chats.py --limit 5 --verbose

  Run-retro on current project:
    recent_chats.py --limit 20 --verbose

  Cross-project recap:
    recent_chats.py --limit 10 --all-projects

  Time-bounded retrieval:
    recent_chats.py --after 2026-04-01 --before 2026-04-15

  JSON output for downstream tooling:
    recent_chats.py --limit 20 --json | jq '.sessions[].project'

  Override project (auto-detect doesn't apply):
    recent_chats.py --project claudest,pkm
"""


def get_recent_sessions(
    conn: sqlite3.Connection,
    limit: int,
    sort_order: str,
    before: str | None,
    after: str | None,
    projects: list[str] | None,
    verbose: bool,
    include_notifications: bool,
) -> list[dict]:
    """Get recent sessions with their messages."""
    cursor = conn.cursor()

    cursor.execute("PRAGMA table_info(branches)")
    branch_columns = {row[1] for row in cursor.fetchall()}
    has_tool_counts = "tool_counts" in branch_columns

    tool_counts_col = ", b.tool_counts" if has_tool_counts else ""
    sql = f"""
        SELECT s.id, s.uuid, b.started_at, b.ended_at, b.exchange_count,
               b.files_modified, b.commits, s.git_branch,
               p.name as project, p.path as project_path,
               b.id as branch_db_id{tool_counts_col}
        FROM sessions s
        JOIN branches b ON b.session_id = s.id AND b.is_active = 1
        JOIN projects p ON s.project_id = p.id
        WHERE 1=1
    """
    params: list = []

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
    params.append(limit)

    cursor.execute(sql, params)
    sessions = cursor.fetchall()

    results = []
    for session in sessions:
        if has_tool_counts:
            (_session_id, uuid, started_at, ended_at, _exchange_count,
             files_json, commits_json, git_branch, project, _project_path,
             branch_db_id, tool_counts_json) = session
        else:
            (_session_id, uuid, started_at, ended_at, _exchange_count,
             files_json, commits_json, git_branch, project, _project_path,
             branch_db_id) = session
            tool_counts_json = None

        notif_clause = "" if include_notifications else "AND COALESCE(m.is_notification, 0) = 0"
        cursor.execute(f"""
            SELECT m.role, m.content, m.timestamp, COALESCE(m.is_notification, 0) as is_notification
            FROM branch_messages bm
            JOIN messages m ON bm.message_id = m.id
            WHERE bm.branch_id = ? {notif_clause}
            ORDER BY m.timestamp ASC
        """, (branch_db_id,))

        messages = [{"role": r, "content": c, "timestamp": t, "is_notification": notif}
                    for r, c, t, notif in cursor.fetchall()]

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
            session_data["tool_counts"] = json.loads(tool_counts_json) if tool_counts_json else {}

        results.append(session_data)

    return results


def format_markdown(sessions: list[dict], verbose: bool = False) -> str:
    if not sessions:
        return "No sessions found."
    lines = [f"# Recent Conversations ({len(sessions)} sessions)\n"]
    for session in sessions:
        lines.append(format_markdown_session(session, verbose=verbose))
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Retrieve recent conversation sessions (defaults to current project).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=EXAMPLES,
    )
    add_common_args(parser, default_limit=5)
    parser.add_argument("--sort-order", choices=["desc", "asc"], default="desc",
                        help="Sort order (default: desc).")
    parser.add_argument("--before", type=str,
                        help="Sessions before this datetime (ISO).")
    parser.add_argument("--after", type=str,
                        help="Sessions after this datetime (ISO).")

    args = parser.parse_args()
    fmt = resolve_format(args)
    limit = validate_limit(args, fmt)

    conn = open_db_or_exit(args.db, fmt)
    try:
        projects, auto_detected = resolve_scope(args, conn, fmt)
        sessions = get_recent_sessions(
            conn,
            limit=limit,
            sort_order=args.sort_order,
            before=args.before,
            after=args.after,
            projects=projects,
            verbose=args.verbose,
            include_notifications=args.include_notifications,
        )
    except Exception as e:
        emit_error("query_failed", str(e), None, fmt)
        sys.exit(1)
    finally:
        conn.close()

    if fmt == "json":
        meta = {
            "scope": {
                "projects": projects,
                "auto_detected": auto_detected,
            },
            "has_more": len(sessions) == limit,
        }
        print(format_json_sessions(sessions, meta))
    else:
        print(format_markdown(sessions, verbose=args.verbose))


if __name__ == "__main__":
    main()
