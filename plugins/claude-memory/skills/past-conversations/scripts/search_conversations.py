#!/usr/bin/env python3
"""
Search conversations using FTS5 full-text search.

Returns markdown by default (token-efficient), JSON with --format json.
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

DEFAULT_DB_PATH = Path.home() / ".claude-memory" / "conversations.db"


def format_time(ts_str: str | None) -> str:
    """Format ISO timestamp to readable form."""
    if not ts_str:
        return "?"
    try:
        dt = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
        return dt.strftime("%Y-%m-%d %H:%M")
    except:
        return ts_str[:16] if ts_str else "?"


def search_sessions(
    conn: sqlite3.Connection,
    query: str,
    max_results: int = 5,
    projects: list[str] | None = None,
    verbose: bool = False
) -> list[dict]:
    """Search for sessions containing query terms."""
    cursor = conn.cursor()

    terms = query.split()
    if not terms:
        return []

    fts_query = " OR ".join(f'"{term}"' for term in terms)

    # Find distinct session IDs with matches
    sql = """
        SELECT DISTINCT s.id
        FROM messages_fts
        JOIN messages m ON messages_fts.rowid = m.id
        JOIN sessions s ON m.session_id = s.id
        JOIN projects p ON s.project_id = p.id
        WHERE messages_fts MATCH ?
    """
    params = [fts_query]

    if projects:
        placeholders = ",".join("?" * len(projects))
        sql += f" AND p.name IN ({placeholders})"
        params.extend(projects)

    sql += " LIMIT ?"
    params.append(max_results)

    cursor.execute(sql, params)
    session_ids = [row[0] for row in cursor.fetchall()]

    if not session_ids:
        return []

    # Fetch full session details
    placeholders = ",".join("?" * len(session_ids))
    cursor.execute(f"""
        SELECT s.id, s.uuid, s.started_at, s.ended_at, s.files_modified,
               s.commits, s.git_branch, p.name as project
        FROM sessions s
        JOIN projects p ON s.project_id = p.id
        WHERE s.id IN ({placeholders})
        ORDER BY s.started_at DESC
    """, session_ids)
    sessions = cursor.fetchall()

    results = []

    for session in sessions:
        session_id, uuid, started_at, ended_at, files_json, commits_json, git_branch, project = session

        cursor.execute("""
            SELECT role, content, timestamp
            FROM messages
            WHERE session_id = ?
            ORDER BY timestamp ASC
        """, (session_id,))

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


def format_markdown(sessions: list[dict], query: str, verbose: bool = False) -> str:
    """Format sessions as markdown."""
    if not sessions:
        return f"No sessions found for query: {query}"

    lines = [f"# Search Results: \"{query}\" ({len(sessions)} sessions)\n"]

    for session in sessions:
        started = format_time(session["started_at"])
        lines.append(f"## {session['project']} | {started}")
        lines.append(f"Session: {session['uuid'][:8]}")

        if session.get("git_branch"):
            lines.append(f"Branch: {session['git_branch']}")

        if verbose:
            files = session.get("files_modified", [])
            if files:
                lines.append("\n### Files Modified")
                for f in files[-10:]:
                    lines.append(f"- `{f}`")
                if len(files) > 10:
                    lines.append(f"- ...and {len(files) - 10} more")

            commits = session.get("commits", [])
            if commits:
                lines.append("\n### Commits")
                for c in commits:
                    lines.append(f"- {c}")

        lines.append("\n### Conversation\n")

        for msg in session["messages"]:
            role = "User" if msg["role"] == "user" else "Assistant"
            lines.append(f"**{role}:** {msg['content']}\n")

        lines.append("---\n")

    return "\n".join(lines)


def format_json(sessions: list[dict], query: str) -> str:
    """Format sessions as JSON."""
    total_messages = sum(len(s["messages"]) for s in sessions)
    return json.dumps({
        "sessions": sessions,
        "total_sessions": len(sessions),
        "total_messages": total_messages,
        "query": query
    }, indent=2)


def main():
    parser = argparse.ArgumentParser(description="Search conversation sessions")
    parser.add_argument("--query", "-q", type=str, required=True, help="Search keywords")
    parser.add_argument("--max-results", type=int, default=5, help="Max sessions (1-10, default: 5)")
    parser.add_argument("--project", type=str, help="Filter by project name(s), comma-separated")
    parser.add_argument("--format", choices=["markdown", "json"], default="markdown", help="Output format (default: markdown)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Include files_modified and commits")
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
        sessions = search_sessions(conn, query=args.query, max_results=max_results,
                                   projects=projects, verbose=args.verbose)
        conn.close()

        if args.format == "json":
            print(format_json(sessions, args.query))
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
