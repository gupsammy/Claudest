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
    ScopeFilter,
    add_common_args,
    emit_error,
    emit_volume_signal,
    open_db_or_exit,
    resolve_format,
    resolve_scope,
    validate_limit,
    volume_flags,
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

  Full uncapped arc of a project, oldest-first (triage index, no message bodies):
    recent_chats.py --project PKM --timeline --sort-order asc

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
    scope: ScopeFilter | None,
    verbose: bool,
    include_notifications: bool,
    summary: bool = False,
) -> list[dict]:
    """Get recent sessions with their messages (or precomputed summaries if summary=True)."""
    cursor = conn.cursor()

    cursor.execute("PRAGMA table_info(branches)")
    branch_columns = {row[1] for row in cursor.fetchall()}
    has_tool_counts = "tool_counts" in branch_columns
    has_context_summary = "context_summary" in branch_columns

    # Both columns are optional: an unmigrated DB (opened before _migrate_columns
    # ran via the import/sync path) may lack either. Gate each read on its own probe
    # so a normal recall never fails with "no such column" on an old database.
    context_summary_col = ", b.context_summary" if has_context_summary else ""
    tool_counts_col = ", b.tool_counts" if has_tool_counts else ""
    sql = f"""
        SELECT s.id, s.uuid, b.started_at, b.ended_at, b.exchange_count,
               b.files_modified, b.commits, s.git_branch,
               p.name as project, p.path as project_path,
               b.id as branch_db_id{context_summary_col}{tool_counts_col}
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
    if scope and scope.values:
        placeholders = ",".join("?" * len(scope.values))
        sql += f" AND p.{scope.column} IN ({placeholders})"
        params.extend(scope.values)

    order = "DESC" if sort_order == "desc" else "ASC"
    sql += f" ORDER BY b.ended_at {order} LIMIT ?"
    params.append(limit)

    cursor.execute(sql, params)
    sessions = cursor.fetchall()

    results = []
    for session in sessions:
        # Fixed columns first, then the optional columns in SELECT order
        # (context_summary, then tool_counts) — index-based to handle any combination.
        (_session_id, uuid, started_at, ended_at, _exchange_count,
         files_json, commits_json, git_branch, project, _project_path,
         branch_db_id) = session[:11]
        col = 11
        context_summary = None
        if has_context_summary:
            context_summary = session[col]
            col += 1
        tool_counts_json = session[col] if has_tool_counts else None

        session_data = {
            "uuid": uuid,
            "project": project,
            "started_at": started_at,
            "ended_at": ended_at,
            "git_branch": git_branch,
        }

        if summary:
            session_data["summary"] = context_summary or ""
        else:
            notif_clause = "" if include_notifications else "AND COALESCE(m.is_notification, 0) = 0"
            cursor.execute(f"""
                SELECT m.role, m.content, m.timestamp, COALESCE(m.is_notification, 0) as is_notification
                FROM branch_messages bm
                JOIN messages m ON bm.message_id = m.id
                WHERE bm.branch_id = ? {notif_clause}
                ORDER BY m.timestamp ASC
            """, (branch_db_id,))
            session_data["messages"] = [
                {"role": r, "content": c, "timestamp": t, "is_notification": notif}
                for r, c, t, notif in cursor.fetchall()
            ]

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


def get_timeline(
    conn: sqlite3.Connection,
    sort_order: str,
    before: str | None,
    after: str | None,
    scope: ScopeFilter | None,
) -> list[dict]:
    """Compact, UNCAPPED session index — one row per session, no message bodies.

    The triage primitive for reconstructing a project's full arc: a single cheap
    call returns every session ordered by time, so a miner can see the whole shape
    before deep-reading the dense ones. Carries project_path so ambiguous names
    (two projects sharing a basename) stay distinguishable per-row.
    """
    cursor = conn.cursor()

    cursor.execute("PRAGMA table_info(branches)")
    branch_columns = {row[1] for row in cursor.fetchall()}
    has_tool_counts = "tool_counts" in branch_columns
    has_context_summary = "context_summary" in branch_columns
    context_summary_col = ", b.context_summary" if has_context_summary else ""
    tool_counts_col = ", b.tool_counts" if has_tool_counts else ""

    sql = f"""
        SELECT s.uuid, p.name as project, p.path as project_path,
               s.git_branch, b.started_at, b.ended_at,
               b.exchange_count{context_summary_col}{tool_counts_col}
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
    if scope and scope.values:
        placeholders = ",".join("?" * len(scope.values))
        sql += f" AND p.{scope.column} IN ({placeholders})"
        params.extend(scope.values)

    order = "DESC" if sort_order == "desc" else "ASC"
    sql += f" ORDER BY b.started_at {order}"  # no LIMIT — the whole arc, by design

    cursor.execute(sql, params)

    # Stream rows straight off the cursor instead of fetchall() — the no-LIMIT
    # design means a large store could return thousands of rows, and we only ever
    # build the compact `results` list, so materialising the raw tuples too just
    # doubles peak memory. --before/--after remain the way to window the range.
    results = []
    for row in cursor:
        uuid, project, project_path, git_branch, started_at, ended_at, exchange_count = row[:7]
        col = 7
        context_summary = None
        if has_context_summary:
            context_summary = row[col]
            col += 1
        tool_counts_json = row[col] if has_tool_counts else None

        tools = 0
        if tool_counts_json:
            try:
                tools = sum(json.loads(tool_counts_json).values())
            except (ValueError, TypeError, AttributeError):
                tools = 0
        title = ""
        if context_summary:
            title = context_summary.strip().splitlines()[0][:120] if context_summary.strip() else ""

        results.append({
            "uuid": uuid,
            "project": project,
            "project_path": project_path,
            "git_branch": git_branch,
            "started_at": started_at,
            "ended_at": ended_at,
            "exchanges": exchange_count,
            "tools": tools,
            "title": title,
        })
    return results


def format_timeline_markdown(rows: list[dict]) -> str:
    if not rows:
        return "No sessions found."
    lines = [f"# Session Timeline ({len(rows)} sessions)\n"]
    for r in rows:
        date = (r["started_at"] or "")[:10]
        branch = r["git_branch"] or "-"
        lines.append(
            f"- {date}  [{r['exchanges']}ex/{r['tools']}t]  ({branch})  "
            f"{r['project']}  {r['uuid']}"
        )
        if r["title"]:
            lines.append(f"    {r['title']}")
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
    parser.add_argument("--timeline", action="store_true",
                        help="Compact UNCAPPED session index (no message bodies, no 50-limit) — "
                             "the triage primitive for reconstructing a project's full arc. "
                             "Pair with --sort-order asc for oldest-first. "
                             "Note: --verbose and --summary are ignored in timeline mode.")

    args = parser.parse_args()
    fmt = resolve_format(args)

    if args.timeline:
        conn = open_db_or_exit(args.db, fmt)
        try:
            scope, auto_detected = resolve_scope(args, conn, fmt)
            rows = get_timeline(conn, args.sort_order, args.before, args.after, scope)
        except Exception as e:
            emit_error("query_failed", str(e), None, fmt)
            sys.exit(1)
        finally:
            conn.close()
        if fmt == "json":
            print(json.dumps({
                "timeline": rows,
                "count": len(rows),
                "scope": {"projects": scope.values if scope else None,
                          "auto_detected": auto_detected},
            }, indent=2))
        else:
            print(format_timeline_markdown(rows))
        return

    limit = validate_limit(args, fmt)

    conn = open_db_or_exit(args.db, fmt)
    try:
        scope, auto_detected = resolve_scope(args, conn, fmt)
        sessions = get_recent_sessions(
            conn,
            limit=limit,
            sort_order=args.sort_order,
            before=args.before,
            after=args.after,
            scope=scope,
            verbose=args.verbose,
            include_notifications=args.include_notifications,
            summary=args.summary,
        )
    except Exception as e:
        emit_error("query_failed", str(e), None, fmt)
        sys.exit(1)
    finally:
        conn.close()

    content_chars = sum(
        len(s.get("summary") or "")
        + sum(len(m.get("content") or "") for m in s.get("messages", []))
        for s in sessions
    )

    if fmt == "json":
        meta = {
            "scope": {
                "projects": scope.values if scope else None,
                "auto_detected": auto_detected,
            },
            "has_more": len(sessions) == limit,
            **volume_flags(content_chars, args.summary),
        }
        print(format_json_sessions(sessions, meta))
    else:
        print(format_markdown(sessions, verbose=args.verbose))
        emit_volume_signal(content_chars, len(sessions), args.summary, fmt)


if __name__ == "__main__":
    main()
