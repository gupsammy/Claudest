#!/usr/bin/env python3
"""
Ingest Claude Code usage data into token_snapshots table and output a JSON analysis blob.

Reads:
  ~/.claude/usage-data/session-meta/   (one JSON file per session)
  ~/.claude/usage-data/facets/         (one JSON file per session)

Upserts into ~/.claude-memory/conversations.db (token_snapshots table).
Outputs a single JSON blob to stdout.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path.home() / ".claude-memory" / "conversations.db"
SESSION_META_DIR = Path.home() / ".claude" / "usage-data" / "session-meta"
FACETS_DIR = Path.home() / ".claude" / "usage-data" / "facets"


def ensure_table(conn: sqlite3.Connection) -> None:
    # Schema must stay in sync with token_snapshots in db.py:_migrate_columns
    conn.executescript("""
CREATE TABLE IF NOT EXISTS token_snapshots (
  id INTEGER PRIMARY KEY,
  session_uuid TEXT UNIQUE NOT NULL,
  project_path TEXT,
  start_time DATETIME,
  duration_minutes INTEGER,
  user_message_count INTEGER,
  assistant_message_count INTEGER,
  input_tokens INTEGER DEFAULT 0,
  output_tokens INTEGER DEFAULT 0,
  cache_read_tokens INTEGER DEFAULT 0,
  cache_creation_tokens INTEGER DEFAULT 0,
  tool_counts TEXT,
  tool_errors INTEGER DEFAULT 0,
  uses_task_agent INTEGER DEFAULT 0,
  uses_web_search INTEGER DEFAULT 0,
  uses_web_fetch INTEGER DEFAULT 0,
  user_response_times TEXT,
  lines_added INTEGER DEFAULT 0,
  lines_removed INTEGER DEFAULT 0,
  goal_categories TEXT,
  outcome TEXT,
  session_type TEXT,
  friction_counts TEXT,
  brief_summary TEXT,
  imported_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_token_snapshots_session ON token_snapshots(session_uuid);
CREATE INDEX IF NOT EXISTS idx_token_snapshots_start ON token_snapshots(start_time);
""")
    conn.commit()


def load_json_files(directory: Path) -> dict[str, dict]:
    """Load all JSON files from a directory, keyed by session_id field or filename stem."""
    result: dict[str, dict] = {}
    if not directory.exists():
        return result
    for p in directory.iterdir():
        if p.suffix != ".json":
            continue
        try:
            data = json.loads(p.read_text())
            key = data.get("session_id") or p.stem
            result[key] = data
        except Exception:
            continue
    return result


def upsert_sessions(conn: sqlite3.Connection, metas: dict[str, dict], facets: dict[str, dict]) -> None:
    all_ids = set(metas.keys()) | set(facets.keys())
    for sid in all_ids:
        meta = metas.get(sid, {})
        facet = facets.get(sid, {})
        session_uuid = meta.get("session_id") or facet.get("session_id") or sid

        tool_counts = meta.get("tool_counts")
        user_response_times = meta.get("user_response_times")
        goal_categories = facet.get("goal_categories")
        friction_counts = facet.get("friction_counts")

        conn.execute(
            """
            INSERT OR REPLACE INTO token_snapshots (
                session_uuid, project_path, start_time, duration_minutes,
                user_message_count, assistant_message_count,
                input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens,
                tool_counts, tool_errors,
                uses_task_agent, uses_web_search, uses_web_fetch,
                user_response_times,
                lines_added, lines_removed,
                goal_categories, outcome, session_type, friction_counts, brief_summary
            ) VALUES (
                ?, ?, ?, ?,
                ?, ?,
                ?, ?, ?, ?,
                ?, ?,
                ?, ?, ?,
                ?,
                ?, ?,
                ?, ?, ?, ?, ?
            )
            """,
            (
                session_uuid,
                meta.get("project_path"),
                meta.get("start_time"),
                meta.get("duration_minutes"),
                meta.get("user_message_count"),
                meta.get("assistant_message_count"),
                meta.get("input_tokens", 0) or 0,
                meta.get("output_tokens", 0) or 0,
                meta.get("cache_read_tokens", 0) or 0,
                meta.get("cache_creation_tokens", 0) or 0,
                json.dumps(tool_counts) if isinstance(tool_counts, dict) else tool_counts,
                meta.get("tool_errors", 0) or 0,
                1 if meta.get("uses_task_agent") else 0,
                1 if meta.get("uses_web_search") else 0,
                1 if meta.get("uses_web_fetch") else 0,
                json.dumps(user_response_times) if isinstance(user_response_times, list) else user_response_times,
                meta.get("lines_added", 0) or 0,
                meta.get("lines_removed", 0) or 0,
                json.dumps(goal_categories) if isinstance(goal_categories, dict) else goal_categories,
                facet.get("outcome"),
                facet.get("session_type"),
                json.dumps(friction_counts) if isinstance(friction_counts, dict) else friction_counts,
                facet.get("brief_summary"),
            ),
        )
    conn.commit()


def build_output(conn: sqlite3.Connection) -> dict:
    cur = conn.cursor()

    # All rows
    cur.execute("""
        SELECT session_uuid, project_path, start_time, duration_minutes,
               input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens,
               tool_counts, tool_errors, uses_task_agent, uses_web_search, uses_web_fetch,
               user_response_times, lines_added, lines_removed,
               goal_categories, outcome, session_type, friction_counts, brief_summary
        FROM token_snapshots
        ORDER BY start_time ASC
    """)
    rows = cur.fetchall()

    total_sessions = len(rows)
    dates = [r[2][:10] for r in rows if r[2]]
    date_range = {
        "earliest": min(dates) if dates else None,
        "latest": max(dates) if dates else None,
    }

    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
        "total_tool_errors": 0,
        "total_lines_added": 0,
        "total_lines_removed": 0,
    }
    tool_aggregate: dict[str, int] = {}
    tool_errors_by_tool: dict[str, int] = {}
    outcome_dist: dict[str, int] = {}
    session_type_dist: dict[str, int] = {}
    response_times_all: list[float] = []
    sessions_with_task = 0
    sessions_with_web = 0
    days: dict[str, dict] = {}
    per_session = []

    for row in rows:
        (
            session_uuid, project_path, start_time, duration_minutes,
            input_tok, output_tok, cache_read, cache_creation,
            tool_counts_raw, tool_errors, uses_task, uses_web_search, uses_web_fetch,
            user_rt_raw, lines_added, lines_removed,
            goal_cats_raw, outcome, session_type, friction_raw, brief_summary,
        ) = row

        input_tok = input_tok or 0
        output_tok = output_tok or 0
        cache_read = cache_read or 0
        cache_creation = cache_creation or 0
        tool_errors = tool_errors or 0
        lines_added = lines_added or 0
        lines_removed = lines_removed or 0

        totals["input_tokens"] += input_tok
        totals["output_tokens"] += output_tok
        totals["cache_read_tokens"] += cache_read
        totals["cache_creation_tokens"] += cache_creation
        totals["total_tool_errors"] += tool_errors
        totals["total_lines_added"] += lines_added
        totals["total_lines_removed"] += lines_removed

        # Tool counts aggregation
        tool_counts: dict = {}
        if tool_counts_raw:
            try:
                tool_counts = json.loads(tool_counts_raw) if isinstance(tool_counts_raw, str) else tool_counts_raw
            except Exception:
                pass
        for tool, cnt in tool_counts.items():
            tool_aggregate[tool] = tool_aggregate.get(tool, 0) + (cnt or 0)

        # Outcome / session type
        if outcome:
            outcome_dist[outcome] = outcome_dist.get(outcome, 0) + 1
        if session_type:
            session_type_dist[session_type] = session_type_dist.get(session_type, 0) + 1

        # User response times
        user_rts: list = []
        if user_rt_raw:
            try:
                user_rts = json.loads(user_rt_raw) if isinstance(user_rt_raw, str) else user_rt_raw
                if isinstance(user_rts, list):
                    response_times_all.extend(float(t) for t in user_rts if t is not None)
            except Exception:
                pass

        if uses_task:
            sessions_with_task += 1
        if uses_web_search or uses_web_fetch:
            sessions_with_web += 1

        # Sessions by day
        if start_time:
            day = start_time[:10]
            if day not in days:
                days[day] = {"date": day, "session_count": 0, "input_tokens": 0, "output_tokens": 0, "cache_read": 0, "cache_creation": 0}
            days[day]["session_count"] += 1
            days[day]["input_tokens"] += input_tok
            days[day]["output_tokens"] += output_tok
            days[day]["cache_read"] += cache_read
            days[day]["cache_creation"] += cache_creation

        # Cache ratio per session
        sess_cache_denom = cache_read + cache_creation
        sess_cache_ratio = cache_read / sess_cache_denom if sess_cache_denom > 0 else 0.0

        per_session.append({
            "session_id": session_uuid,
            "project_path": project_path,
            "start_time": start_time,
            "duration_minutes": duration_minutes,
            "input_tokens": input_tok,
            "output_tokens": output_tok,
            "cache_read_tokens": cache_read,
            "cache_creation_tokens": cache_creation,
            "cache_ratio": round(sess_cache_ratio, 4),
            "tool_counts": tool_counts,
            "tool_errors": tool_errors,
            "outcome": outcome,
            "brief_summary": brief_summary,
        })

    # Global cache ratio
    cache_denom = totals["cache_read_tokens"] + totals["cache_creation_tokens"]
    cache_ratio = totals["cache_read_tokens"] / cache_denom if cache_denom > 0 else 0.0

    # Top 15 tools
    top_tools = sorted(
        [{"tool": t, "count": c} for t, c in tool_aggregate.items()],
        key=lambda x: x["count"],
        reverse=True,
    )[:15]

    # Idle gaps > 5 min
    idle_gaps = sum(1 for t in response_times_all if t > 300)

    avg_rt = sum(response_times_all) / len(response_times_all) if response_times_all else 0.0

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_sessions": total_sessions,
        "date_range": date_range,
        "totals": totals,
        "cache_ratio": round(cache_ratio, 4),
        "sessions_by_day": sorted(days.values(), key=lambda x: x["date"]),
        "top_tools": top_tools,
        "tool_error_rate_by_tool": tool_errors_by_tool,
        "outcome_distribution": outcome_dist,
        "session_type_distribution": session_type_dist,
        "avg_user_response_time_seconds": round(avg_rt, 2),
        "idle_gaps_over_5min": idle_gaps,
        "sessions_with_task_agent": sessions_with_task,
        "sessions_with_web": sessions_with_web,
        "per_session": per_session,
    }


def main() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")

    ensure_table(conn)

    metas = load_json_files(SESSION_META_DIR)
    facets = load_json_files(FACETS_DIR)

    upsert_sessions(conn, metas, facets)

    output = build_output(conn)
    conn.close()

    print(json.dumps(output, default=str))


if __name__ == "__main__":
    main()
