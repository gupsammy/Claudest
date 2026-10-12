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
    emit_warning,
    open_db_or_exit,
    resolve_format,
    resolve_scope,
    validate_limit,
    volume_flags,
)
from memory_lib.content import sanitize_fts_term, strip_system_reminders
from memory_lib.db import detect_fts_support, load_settings
from memory_lib.embeddings import (
    QUERY_PROMPT,
    EmbeddingError,
    embed,
    fuse,
    index_size,
    vector_hits,
)
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

  Matching passages only, then read one session in full:
    search_conversations.py -q "worktree backup" --excerpts
    recent_chats.py --session 1c634ac2
"""

EXCERPTS_PER_SESSION = 3
# FTS5 caps snippet() at 64 tokens; 48 keeps each excerpt to a few sentences.
SNIPPET_TOKENS = 48
LIKE_WINDOW_CHARS = 150
# Candidates each ranker contributes before rank fusion trims to --limit.
HYBRID_POOL_MIN = 25
EXCHANGE_EXCERPT_CHARS = 400


def fetch_excerpts(
    cursor: sqlite3.Cursor,
    branch_db_id: int,
    fts_level: str | None,
    fts_query: str | None,
    terms: list[str],
    include_notifications: bool,
) -> list[dict]:
    """Return the best-matching passages of one session, in conversation order.

    Uses the message-level index with the same query and tokenizer that ranked the
    session, so an excerpt exists exactly when the session matched on that message.
    """
    notif_clause = "" if include_notifications else "AND COALESCE(m.is_notification, 0) = 0"
    if fts_level == "fts5":
        cursor.execute(f"""
            SELECT m.role, m.timestamp,
                   snippet(messages_fts, 0, '**', '**', '…', {SNIPPET_TOKENS})
            FROM messages_fts
            JOIN messages m ON messages_fts.rowid = m.id
            JOIN branch_messages bm ON bm.message_id = m.id
            WHERE messages_fts MATCH ? AND bm.branch_id = ? {notif_clause}
            ORDER BY bm25(messages_fts) LIMIT ?
        """, (fts_query, branch_db_id, EXCERPTS_PER_SESSION))
        rows = cursor.fetchall()
    elif fts_level == "fts4":
        cursor.execute(f"""
            SELECT m.role, m.timestamp,
                   snippet(messages_fts, '**', '**', '…', -1, {SNIPPET_TOKENS})
            FROM messages_fts
            JOIN messages m ON messages_fts.rowid = m.id
            JOIN branch_messages bm ON bm.message_id = m.id
            WHERE messages_fts MATCH ? AND bm.branch_id = ? {notif_clause}
            ORDER BY m.timestamp LIMIT ?
        """, (fts_query, branch_db_id, EXCERPTS_PER_SESSION))
        rows = cursor.fetchall()
    else:
        like = " OR ".join("m.content LIKE ?" for _ in terms)
        cursor.execute(f"""
            SELECT m.role, m.timestamp, m.content
            FROM branch_messages bm
            JOIN messages m ON bm.message_id = m.id
            WHERE bm.branch_id = ? AND ({like}) {notif_clause}
            ORDER BY m.timestamp LIMIT ?
        """, (branch_db_id, *(f"%{t}%" for t in terms), EXCERPTS_PER_SESSION))
        rows = [(role, ts, _like_window(content or "", terms)) for role, ts, content in cursor.fetchall()]

    rows.sort(key=lambda r: r[1] or "")
    return [{"role": role, "timestamp": ts, "text": text} for role, ts, text in rows]


def _like_window(content: str, terms: list[str]) -> str:
    """Cut a window around the first term hit, for the no-FTS fallback."""
    lowered = content.lower()
    hits = [i for i in (lowered.find(t.lower()) for t in terms) if i != -1]
    if not hits:
        return content[: 2 * LIKE_WINDOW_CHARS]
    start = max(0, min(hits) - LIKE_WINDOW_CHARS)
    end = min(len(content), min(hits) + LIKE_WINDOW_CHARS)
    return ("…" if start else "") + content[start:end] + ("…" if end < len(content) else "")


def search_sessions(
    conn: sqlite3.Connection,
    query: str,
    fts_level: str | None,
    limit: int,
    scope: ScopeFilter | None,
    verbose: bool,
    include_notifications: bool,
    summary: bool = False,
    excerpts: bool = False,
    vector: list[tuple[int, float, int]] | None = None,
) -> list[dict]:
    """Search sessions via FTS5/FTS4 (BM25-ranked when available) or LIKE fallback.

    With `vector` (branch_id, similarity, message_id hits from semantic search),
    keyword and vector rankings are fused by reciprocal rank.
    Each session carries one body: its summary, its matching excerpts, or every message.
    """
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
    fts_query = None
    pool = max(limit * 5, HYBRID_POOL_MIN) if vector is not None else limit
    columns = f"""s.id, s.uuid, b.started_at, b.ended_at, b.files_modified,
                   b.commits, s.git_branch, p.name as project, b.id as branch_db_id{context_summary_col}"""

    if fts_level in ("fts5", "fts4"):
        sanitized_terms = [sanitize_fts_term(term) for term in terms]
        sanitized_terms = [t for t in sanitized_terms if t]
        if not sanitized_terms:
            return []
        fts_query = " OR ".join(f'"{term}"' for term in sanitized_terms)

        sql = f"""
            SELECT {columns}
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
        params.append(pool)

    else:
        like_clauses = " AND ".join("b.aggregated_content LIKE ?" for _ in terms)
        sql = f"""
            SELECT {columns}
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
        params.append(pool)

    cursor.execute(sql, params)
    sessions = cursor.fetchall()

    matched_exchange: dict[int, int] = {}
    if vector is not None:
        by_branch = {row[8]: row for row in sessions}
        matched_exchange = {bid: mid for bid, _score, mid in vector}
        fused = fuse(list(by_branch), [bid for bid, _s, _m in vector], limit)
        missing = [bid for bid in fused if bid not in by_branch]
        if missing:
            cursor.execute(f"""
                SELECT {columns}
                FROM branches b
                JOIN sessions s ON b.session_id = s.id
                JOIN projects p ON s.project_id = p.id
                WHERE b.id IN ({",".join("?" * len(missing))})
            """, missing)
            by_branch.update((row[8], row) for row in cursor.fetchall())
        sessions = [by_branch[bid] for bid in fused if bid in by_branch]

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
        elif excerpts:
            session_data["excerpts"] = fetch_excerpts(
                cursor, branch_db_id, fts_level, fts_query, terms, include_notifications
            )
            if not session_data["excerpts"] and branch_db_id in matched_exchange:
                session_data["excerpts"] = exchange_excerpt(cursor, matched_exchange[branch_db_id])
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


def exchange_excerpt(cursor: sqlite3.Cursor, message_id: int) -> list[dict]:
    """The human turn a semantic hit matched, for sessions with no keyword passage."""
    row = cursor.execute("SELECT role, timestamp, content FROM messages WHERE id = ?",
                         (message_id,)).fetchone()
    if not row:
        return []
    text = strip_system_reminders(row[2] or "")
    if len(text) > EXCHANGE_EXCERPT_CHARS:
        text = text[:EXCHANGE_EXCERPT_CHARS] + "…"
    return [{"role": row[0], "timestamp": row[1], "text": text}]


def semantic_hits(
    conn: sqlite3.Connection,
    query: str,
    settings: dict,
    scope: ScopeFilter | None,
    include_notifications: bool,
    limit: int,
) -> list[tuple[int, float, int]] | None:
    """Vector hits for the query, or None when the index holds nothing for the model.

    Raises EmbeddingError when Ollama cannot embed the query.
    """
    model = settings["embedding_model"]
    if not index_size(conn, model):
        return None
    query_vec = embed([QUERY_PROMPT.format(query)], model, settings["ollama_url"], timeout=10.0)[0]
    pool = max(limit * 5, HYBRID_POOL_MIN)
    return vector_hits(conn, query_vec, model, scope, include_notifications, pool,
                       min_score=float(settings["semantic_min_score"]))


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
    parser.add_argument("--excerpts", action="store_true",
                        help=f"Return up to {EXCERPTS_PER_SESSION} matching passages per session "
                             "instead of the full transcript. Read a whole session with "
                             "recent_chats.py --session UUID.")
    parser.add_argument("--lexical", action="store_true",
                        help="Keyword ranking only, even when semantic_search is enabled.")

    args = parser.parse_args()
    if args.excerpts and args.summary:
        emit_error("invalid_args", "--excerpts and --summary are mutually exclusive",
                   "drop one of the two flags", resolve_format(args))
        sys.exit(2)
    fmt = resolve_format(args)
    limit = validate_limit(args, fmt)

    settings = load_settings()
    conn = open_db_or_exit(args.db, fmt)
    try:
        scope, auto_detected = resolve_scope(args, conn, fmt)
        fts_level = detect_fts_support(conn)
        # retrieval stays None when semantic search is off, so output is unchanged.
        retrieval = vector = None
        if settings.get("semantic_search"):
            retrieval = "lexical"
            if not args.lexical:
                try:
                    vector = semantic_hits(conn, args.query, settings, scope,
                                           args.include_notifications, limit)
                except EmbeddingError as e:
                    emit_warning(f"semantic search unavailable ({e}); using keyword search", fmt)
                if vector is not None:
                    retrieval = "hybrid"
        sessions = search_sessions(
            conn,
            query=args.query,
            fts_level=fts_level,
            limit=limit,
            scope=scope,
            verbose=args.verbose,
            include_notifications=args.include_notifications,
            summary=args.summary,
            excerpts=args.excerpts,
            vector=vector,
        )
    except Exception as e:
        emit_error("query_failed", str(e), None, fmt)
        sys.exit(1)
    finally:
        conn.close()

    content_chars = sum(
        len(s.get("summary") or "")
        + sum(len(m.get("content") or "") for m in s.get("messages", []))
        + sum(len(e["text"] or "") for e in s.get("excerpts", []))
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
        if retrieval:
            meta["retrieval"] = retrieval
        print(format_json_sessions(sessions, meta))
    else:
        print(format_markdown(sessions, args.query, verbose=args.verbose))
        emit_volume_signal(content_chars, len(sessions), args.summary, fmt)


if __name__ == "__main__":
    main()
