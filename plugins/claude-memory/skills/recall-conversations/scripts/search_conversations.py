#!/usr/bin/env python3
"""
Search conversations using full-text search with FTS5/FTS4/LIKE fallback.

Returns markdown by default (token-efficient), JSON with --format json.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

# Local imports
from memory_lib.db import DEFAULT_DB_PATH, detect_fts_support
from memory_lib.content import sanitize_fts_term
from memory_lib.formatting import format_markdown_session, format_json_sessions


def search_sessions(
    conn: sqlite3.Connection,
    query: str,
    fts_level: str | None,
    max_results: int = 5,
    projects: list[str] | None = None,
    verbose: bool = False,
    include_notifications: bool = False
) -> list[dict]:
    """Search for sessions using branch-level FTS with BM25 ranking, FTS4 MATCH, or LIKE fallback."""
    cursor = conn.cursor()

    terms = query.split()
    if not terms:
        return []

    params: list = []

    if fts_level in ("fts5", "fts4"):
        sanitized_terms = [sanitize_fts_term(term) for term in terms]
        sanitized_terms = [t for t in sanitized_terms if t]  # Remove empty terms
        if not sanitized_terms:
            return []
        fts_query = " OR ".join(f'"{term}"' for term in sanitized_terms)

        if fts_level == "fts5":
            sql = """
                SELECT s.id, s.uuid, b.started_at, b.ended_at, b.files_modified,
                       b.commits, s.git_branch, p.name as project, b.id as branch_db_id
                FROM branches_fts
                JOIN branches b ON branches_fts.rowid = b.id
                JOIN sessions s ON b.session_id = s.id
                JOIN projects p ON s.project_id = p.id
                WHERE b.is_active = 1
                  AND branches_fts MATCH ?
            """
        else:
            sql = """
                SELECT s.id, s.uuid, b.started_at, b.ended_at, b.files_modified,
                       b.commits, s.git_branch, p.name as project, b.id as branch_db_id
                FROM branches_fts
                JOIN branches b ON branches_fts.rowid = b.id
                JOIN sessions s ON b.session_id = s.id
                JOIN projects p ON s.project_id = p.id
                WHERE b.is_active = 1
                  AND branches_fts MATCH ?
            """
        params.append(fts_query)

        if projects:
            placeholders = ",".join("?" * len(projects))
            sql += f" AND p.name IN ({placeholders})"
            params.extend(projects)

        if fts_level == "fts5":
            sql += " ORDER BY bm25(branches_fts) LIMIT ?"
        else:
            sql += " ORDER BY b.ended_at DESC LIMIT ?"
        params.append(max_results)

    else:
        # LIKE fallback: no FTS available
        like_clauses = " AND ".join(
            "b.aggregated_content LIKE ?" for _ in terms
        )
        sql = f"""
            SELECT s.id, s.uuid, b.started_at, b.ended_at, b.files_modified,
                   b.commits, s.git_branch, p.name as project, b.id as branch_db_id
            FROM branches b
            JOIN sessions s ON b.session_id = s.id
            JOIN projects p ON s.project_id = p.id
            WHERE b.is_active = 1
              AND {like_clauses}
        """
        params.extend(f"%{term}%" for term in terms)

        if projects:
            placeholders = ",".join("?" * len(projects))
            sql += f" AND p.name IN ({placeholders})"
            params.extend(projects)

        sql += " ORDER BY b.ended_at DESC LIMIT ?"
        params.append(max_results)

    cursor.execute(sql, params)
    sessions = cursor.fetchall()

    results = []

    for session in sessions:
        _session_id, uuid, started_at, ended_at, files_json, commits_json, git_branch, project, branch_db_id = session

        # Get messages for active branch via branch_messages
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

        results.append(session_data)

    return results


def format_markdown(sessions: list[dict], query: str, verbose: bool = False) -> str:
    """Format sessions as markdown."""
    if not sessions:
        return f"No sessions found for query: {query}"

    lines = [f"# Search Results: \"{query}\" ({len(sessions)} sessions)\n"]
    for session in sessions:
        lines.append(format_markdown_session(session, verbose=verbose))

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Search conversation sessions")
    parser.add_argument("--query", "-q", type=str, required=True, help="Search keywords")
    parser.add_argument("--max-results", type=int, default=5, help="Max sessions (1-10, default: 5)")
    parser.add_argument("--project", type=str, help="Filter by project name(s), comma-separated")
    parser.add_argument("--format", choices=["markdown", "json"], default="markdown", help="Output format (default: markdown)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Include files_modified and commits")
    parser.add_argument("--include-notifications", action="store_true", help="Include task notification messages (hidden by default)")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="Database path")

    args = parser.parse_args()
    max_results = max(1, min(10, args.max_results))
    projects = [p.strip() for p in args.project.split(",")] if args.project else None

    if not args.db.exists():
        if args.format == "json":
            print(json.dumps({"error": "Database not found", "sessions": [], "query": args.query}))
        else:
            print("Error: Database not found. Run memory setup first.")
        sys.exit(1)

    try:
        conn = sqlite3.connect(args.db)
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        fts_level = detect_fts_support(conn)

        sessions = search_sessions(conn, query=args.query, fts_level=fts_level,
                                   max_results=max_results, projects=projects,
                                   verbose=args.verbose,
                                   include_notifications=args.include_notifications)
        conn.close()

        if args.format == "json":
            print(format_json_sessions(sessions, {"query": args.query}))
        else:
            print(format_markdown(sessions, args.query, verbose=args.verbose))

    except Exception as e:
        if args.format == "json":
            print(json.dumps({"error": str(e), "sessions": [], "query": args.query}))
        else:
            print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
