#!/usr/bin/env python3
"""
Search conversation sessions using full-text search (FTS5/FTS4/LIKE cascade).

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
from memory_lib.content import sanitize_fts_term
from memory_lib.db import detect_fts_support
from memory_lib.formatting import format_markdown_session, format_json_sessions


EXAMPLES = """
EXAMPLES
  Find decisions in current project:
    search_conversations.py -q "decided chose trade-off" --limit 10 --verbose

  Find antipatterns across all projects:
    search_conversations.py -q "again same mistake" --all-projects

  JSON output for downstream tooling:
    search_conversations.py -q "FTS5" --json | jq '.sessions[].uuid'

  Override project (auto-detect doesn't apply):
    search_conversations.py -q "auth" --project pkm
"""


def search_sessions(
    conn: sqlite3.Connection,
    query: str,
    fts_level: str | None,
    limit: int,
    scope: ScopeFilter | None,
    verbose: bool,
    include_notifications: bool,
    summary: bool = False,
) -> list[dict]:
    """Search sessions via FTS5/FTS4 (BM25-ranked when available) or LIKE fallback."""
    cursor = conn.cursor()

    # context_summary is optional: an unmigrated DB may lack it. Gate the read so a
    # normal search never fails with "no such column" on an old database.
    cursor.execute("PRAGMA table_info(branches)")
    has_context_summary = "context_summary" in {row[1] for row in cursor.fetchall()}
    context_summary_col = ", b.context_summary" if has_context_summary else ""

    terms = query.split()
    if not terms:
        return []

    params: list = []

    if fts_level in ("fts5", "fts4"):
        sanitized_terms = [sanitize_fts_term(term) for term in terms]
        sanitized_terms = [t for t in sanitized_terms if t]
        if not sanitized_terms:
            return []
        fts_query = " OR ".join(f'"{term}"' for term in sanitized_terms)

        sql = f"""
            SELECT s.id, s.uuid, b.started_at, b.ended_at, b.files_modified,
                   b.commits, s.git_branch, p.name as project, b.id as branch_db_id{context_summary_col}
            FROM branches_fts
            JOIN branches b ON branches_fts.rowid = b.id
            JOIN sessions s ON b.session_id = s.id
            JOIN projects p ON s.project_id = p.id
            WHERE b.is_active = 1
              AND branches_fts MATCH ?
        """
        params.append(fts_query)

        if scope and scope.values:
            placeholders = ",".join("?" * len(scope.values))
            sql += f" AND p.{scope.column} IN ({placeholders})"
            params.extend(scope.values)

        if fts_level == "fts5":
            sql += " ORDER BY bm25(branches_fts) LIMIT ?"
        else:
            sql += " ORDER BY b.ended_at DESC LIMIT ?"
        params.append(limit)

    else:
        like_clauses = " AND ".join("b.aggregated_content LIKE ?" for _ in terms)
        sql = f"""
            SELECT s.id, s.uuid, b.started_at, b.ended_at, b.files_modified,
                   b.commits, s.git_branch, p.name as project, b.id as branch_db_id{context_summary_col}
            FROM branches b
            JOIN sessions s ON b.session_id = s.id
            JOIN projects p ON s.project_id = p.id
            WHERE b.is_active = 1
              AND {like_clauses}
        """
        params.extend(f"%{term}%" for term in terms)

        if scope and scope.values:
            placeholders = ",".join("?" * len(scope.values))
            sql += f" AND p.{scope.column} IN ({placeholders})"
            params.extend(scope.values)

        sql += " ORDER BY b.ended_at DESC LIMIT ?"
        params.append(limit)

    cursor.execute(sql, params)
    sessions = cursor.fetchall()

    results = []
    for session in sessions:
        (_session_id, uuid, started_at, ended_at, files_json, commits_json,
         git_branch, project, branch_db_id) = session[:9]
        context_summary = session[9] if has_context_summary else None

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

        results.append(session_data)

    return results


def format_markdown(sessions: list[dict], query: str, verbose: bool = False) -> str:
    if not sessions:
        return f"No sessions found for query: {query}"
    lines = [f"# Search Results: \"{query}\" ({len(sessions)} sessions)\n"]
    for session in sessions:
        lines.append(format_markdown_session(session, verbose=verbose))
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Search conversation sessions by keyword (defaults to current project).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=EXAMPLES,
    )
    parser.add_argument("--query", "-q", type=str, required=True,
                        help="Search keywords (BM25-ranked when FTS5 is available).")
    add_common_args(parser, default_limit=5)

    args = parser.parse_args()
    fmt = resolve_format(args)
    limit = validate_limit(args, fmt)

    conn = open_db_or_exit(args.db, fmt)
    try:
        scope, auto_detected = resolve_scope(args, conn, fmt)
        fts_level = detect_fts_support(conn)
        sessions = search_sessions(
            conn,
            query=args.query,
            fts_level=fts_level,
            limit=limit,
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
            "query": args.query,
            "scope": {
                "projects": scope.values if scope else None,
                "auto_detected": auto_detected,
            },
            "has_more": len(sessions) == limit,
            **volume_flags(content_chars, args.summary),
        }
        print(format_json_sessions(sessions, meta))
    else:
        print(format_markdown(sessions, args.query, verbose=args.verbose))
        emit_volume_signal(content_chars, len(sessions), args.summary, fmt)


if __name__ == "__main__":
    main()
