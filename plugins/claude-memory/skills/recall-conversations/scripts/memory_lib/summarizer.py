#!/usr/bin/env python3
"""
Precompute structured context summaries for session injection.

Runs at Stop time (sync_current.py) and import time. Produces both a JSON
source-of-truth and a pre-rendered markdown template stored on the branches table.
All extraction is deterministic Python — no LLM calls.
"""

from __future__ import annotations

import json
import re
import sqlite3

from memory_lib.formatting import format_time, format_time_full

# Truncation limits
_FRONT_CHARS = 300
_BACK_CHARS = 600

# Session disposition patterns
_COMPLETION_RE = re.compile(
    r'(?:done|pushed|merged|all (?:tests? )?pass|completed|finished|shipped|deployed|'
    r'PR #?\d+|commit(?:ted)?|changes? (?:are )?live)',
    re.IGNORECASE
)
_SHORT_CONFIRM_RE = re.compile(
    r'^(?:y(?:a|ep|es)?|thanks?|(?:looks? )?good|nice|perfect|great|ok|lgtm|k)\s*[.!]?$',
    re.IGNORECASE
)
_NEW_INSTRUCTION_RE = re.compile(
    r'^(?:now |next |also |can you |let\'?s |please |I (?:want|need) )',
    re.IGNORECASE
)


def truncate_mid(text: str, front: int = _FRONT_CHARS, back: int = _BACK_CHARS) -> str:
    """Mid-truncate text, keeping front and back portions."""
    if not text or len(text) <= front + back + 20:
        return text
    return text[:front] + "\n[... truncated ...]\n" + text[-back:]


def detect_disposition(exchanges: list[dict]) -> str:
    """Classify session ending as COMPLETED, IN_PROGRESS, or INTERRUPTED.

    Heuristics based on the final exchange pair:
    - COMPLETED: assistant uses completion language and user confirms briefly
    - IN_PROGRESS: user gives a new instruction as their last message
    - INTERRUPTED: default / session ends mid-flow
    """
    if not exchanges:
        return "INTERRUPTED"

    last = exchanges[-1]
    last_user = last.get("user", "").strip()
    last_asst = last.get("assistant", "").strip()

    # If user's last message is a new instruction, work is in progress
    if _NEW_INSTRUCTION_RE.search(last_user):
        return "IN_PROGRESS"

    # If assistant used completion language and user confirmed briefly
    if _COMPLETION_RE.search(last_asst) and _SHORT_CONFIRM_RE.match(last_user):
        return "COMPLETED"

    # If assistant used completion language (even without user confirm — session may have ended)
    if _COMPLETION_RE.search(last_asst) and len(last_user) < 30:
        return "COMPLETED"

    # If user confirmed briefly (likely accepting the work)
    if _SHORT_CONFIRM_RE.match(last_user):
        return "COMPLETED"

    return "IN_PROGRESS"


def build_exchange_pairs(messages: list[dict]) -> list[dict]:
    """
    Build exchange pairs from sequential messages.

    Each message is {"role": str, "content": str, "timestamp": str}.
    Returns list of {"user": str, "assistant": str, "timestamp": str, "index": int}.
    """
    exchanges = []
    current_user = None
    current_user_ts = None
    current_asst_parts = []  # type: list[str]

    for m in messages:
        if m["role"] == "user":
            if current_user is not None:
                exchanges.append({
                    "user": current_user,
                    "assistant": "\n\n".join(current_asst_parts),
                    "timestamp": current_user_ts,
                    "index": len(exchanges),
                })
            current_user = m["content"]
            current_user_ts = m.get("timestamp")
            current_asst_parts = []
        elif m["role"] == "assistant" and current_user is not None:
            cleaned = re.sub(r'\[Tool: \w+\]', '', m["content"]).strip()
            if cleaned:
                current_asst_parts.append(cleaned)

    if current_user is not None:
        exchanges.append({
            "user": current_user,
            "assistant": "\n\n".join(current_asst_parts),
            "timestamp": current_user_ts,
            "index": len(exchanges),
        })

    return exchanges


def build_context_summary_json(branch_row: dict, messages: list[dict]) -> dict:
    """
    Assemble the structured JSON summary from branch metadata and messages.

    branch_row keys: started_at, ended_at, exchange_count, files_modified,
                     commits, tool_counts, git_branch.
    messages: list of {"role", "content", "timestamp"} dicts, ordered by time.
    """
    exchanges = build_exchange_pairs(messages)
    if not exchanges:
        return {"version": 2, "topic": "", "first_exchanges": [],
                "last_exchanges": [], "metadata": {}}

    # Topic from first user message
    topic = exchanges[0]["user"]
    if len(topic) > 120:
        topic = topic[:120] + "..."

    disposition = detect_disposition(exchanges)

    # First exchanges (up to 2)
    first_exchanges = [
        {"user": ex["user"], "assistant": ex["assistant"], "timestamp": ex["timestamp"]}
        for ex in exchanges[:2]
    ]

    # Last exchanges (up to 6)
    if len(exchanges) <= 8:
        # Short/medium session: all exchanges go into last_exchanges
        last_exchanges = [
            {"user": ex["user"], "assistant": ex["assistant"], "timestamp": ex["timestamp"]}
            for ex in exchanges
        ]
    else:
        # Take last 6 exchanges
        last_exchanges = [
            {"user": ex["user"], "assistant": ex["assistant"], "timestamp": ex["timestamp"]}
            for ex in exchanges[-6:]
        ]

    # Parse JSON fields from branch_row
    files = branch_row.get("files_modified") or "[]"
    if isinstance(files, str):
        try:
            files = json.loads(files)
        except (json.JSONDecodeError, TypeError):
            files = []

    commits = branch_row.get("commits") or "[]"
    if isinstance(commits, str):
        try:
            commits = json.loads(commits)
        except (json.JSONDecodeError, TypeError):
            commits = []

    tool_counts = branch_row.get("tool_counts") or "{}"
    if isinstance(tool_counts, str):
        try:
            tool_counts = json.loads(tool_counts)
        except (json.JSONDecodeError, TypeError):
            tool_counts = {}

    return {
        "version": 2,
        "topic": topic,
        "disposition": disposition,
        "first_exchanges": first_exchanges,
        "last_exchanges": last_exchanges,
        "metadata": {
            "exchange_count": branch_row.get("exchange_count", len(exchanges)),
            "files_modified": files,
            "commits": commits,
            "tool_counts": tool_counts,
            "started_at": branch_row.get("started_at"),
            "ended_at": branch_row.get("ended_at"),
            "git_branch": branch_row.get("git_branch"),
        },
    }


def _build_gap_summary(summary_json: dict) -> str:
    """Build a one-line summary of what happened in the omitted middle exchanges."""
    files = summary_json.get("metadata", {}).get("files_modified", [])
    if files:
        short = [f.rsplit("/", 1)[-1] for f in files[:3]]
        return ", ".join(short)
    return ""


def render_context_summary(summary_json: dict) -> str:
    """
    Render the JSON summary to injection-ready markdown.

    Short sessions (<=8 exchanges) render all exchanges once, no first/last split.
    Longer sessions show first 2 exchanges + gap + last 6 exchanges.
    """
    if not summary_json or not summary_json.get("first_exchanges"):
        return ""

    meta = summary_json.get("metadata", {})
    lines = []

    # Header
    start = format_time_full(meta.get("started_at"))
    end = format_time_full(meta.get("ended_at"))
    header = f"### Session: {start} -> {end}"
    branch = meta.get("git_branch")
    if branch:
        header += f" (branch: {branch})"
    lines.append(header + "\n")

    # Topic and disposition
    topic = summary_json.get("topic", "")
    disposition = summary_json.get("disposition", "")
    if topic or disposition:
        parts = []
        if topic:
            parts.append(f"**Topic:** {topic}")
        if disposition:
            parts.append(f"**Status:** {disposition}")
        lines.append(" | ".join(parts))
        lines.append("")

    # Metadata: files, commits, tools
    files = meta.get("files_modified", [])
    if files:
        file_strs = [f"`{f}`" for f in files[:6]]
        line = "Modified: " + ", ".join(file_strs)
        if len(files) > 6:
            line += f" +{len(files) - 6} more"
        lines.append(line)

    commits = meta.get("commits", [])
    if commits:
        commit_strs = commits[:3]
        lines.append("Commits: " + "; ".join(commit_strs))

    tool_counts = meta.get("tool_counts", {})
    if tool_counts:
        sorted_tools = sorted(tool_counts.items(), key=lambda x: x[1], reverse=True)[:8]
        tools_str = ", ".join(f"{name}({count})" for name, count in sorted_tools)
        lines.append("Tools: " + tools_str)

    lines.append("")

    # Key Signals section (omitted if no markers)
    exchange_count = meta.get("exchange_count", 0)
    first_exs = summary_json.get("first_exchanges", [])
    last_exs = summary_json.get("last_exchanges", [])

    if exchange_count <= 8:
        # Short/medium session: render all exchanges once
        lines.append("### Conversation\n")
        for ex in last_exs:
            t = format_time(ex.get("timestamp"))
            lines.append(f"**[{t}] User:**")
            lines.append(ex["user"])
            lines.append("")
            if ex["assistant"]:
                lines.append(f"**[{t}] Assistant:**")
                lines.append(truncate_mid(ex["assistant"]))
                lines.append("")
    else:
        # Where We Left Off first — most recent context at top, where attention is
        # highest and where inline-preview truncation (if any) clips from below.
        lines.append("### Where We Left Off\n")
        for ex in last_exs:
            t = format_time(ex.get("timestamp"))
            lines.append(f"**[{t}] User:**")
            lines.append(ex["user"])
            lines.append("")
            if ex["assistant"]:
                lines.append(f"**[{t}] Assistant:**")
                lines.append(truncate_mid(ex["assistant"]))
                lines.append("")

        # Gap indicator with summary of middle exchanges
        gap = exchange_count - len(first_exs) - len(last_exs)
        if gap > 0:
            gap_detail = _build_gap_summary(summary_json)
            if gap_detail:
                lines.append(f"[... {gap} earlier exchanges covering: {gap_detail} ...]\n")
            else:
                lines.append(f"[... {gap} earlier exchanges ...]\n")

        # Earlier in This Session — first 2 exchanges kept for origin context,
        # placed last so they're the first thing clipped under truncation.
        lines.append("### Earlier in This Session\n")
        for ex in first_exs:
            t = format_time(ex.get("timestamp"))
            lines.append(f"**[{t}] User:**")
            lines.append(ex["user"])
            lines.append("")
            if ex["assistant"]:
                lines.append(f"**[{t}] Assistant:**")
                lines.append(truncate_mid(ex["assistant"]))
                lines.append("")

    # Contextual recall priming footer
    topic = summary_json.get("topic", "")
    files = meta.get("files_modified", [])
    footer_parts = [f"{exchange_count} exchanges"]
    if topic:
        short_topic = topic[:80] + "..." if len(topic) > 80 else topic
        footer_parts.append(f'about "{short_topic}"')
    if files:
        short_files = [f.rsplit("/", 1)[-1] for f in files[:3]]
        footer_parts.append(f"({', '.join(short_files)})")
    footer = " ".join(footer_parts)
    lines.append(
        f"[{footer} — proactively use /recall-conversations "
        "to retrieve relevant context from past conversations when the user references "
        "prior work, asks about decisions made earlier, or when you sense useful context "
        "from previous sessions would improve your response.]"
    )

    return "\n".join(lines)


def compute_context_summary(cursor: sqlite3.Cursor, branch_db_id: int) -> tuple[str, str]:
    """
    Orchestrator: fetch branch + messages from DB, return (markdown, json_string).

    Raises on DB errors; caller should wrap in try/except.
    """
    # Fetch branch row
    cursor.execute("""
        SELECT b.started_at, b.ended_at, b.exchange_count, b.files_modified,
               b.commits, b.tool_counts, s.git_branch
        FROM branches b
        JOIN sessions s ON b.session_id = s.id
        WHERE b.id = ?
    """, (branch_db_id,))
    row = cursor.fetchone()
    if not row:
        return "", ""

    branch_row = {
        "started_at": row[0],
        "ended_at": row[1],
        "exchange_count": row[2],
        "files_modified": row[3],
        "commits": row[4],
        "tool_counts": row[5],
        "git_branch": row[6],
    }

    # Fetch messages for this branch
    cursor.execute("""
        SELECT m.role, m.content, m.timestamp
        FROM branch_messages bm
        JOIN messages m ON bm.message_id = m.id
        WHERE bm.branch_id = ?
          AND COALESCE(m.is_notification, 0) = 0
        ORDER BY m.timestamp ASC
    """, (branch_db_id,))

    messages = [
        {"role": r, "content": c, "timestamp": t}
        for r, c, t in cursor.fetchall()
    ]

    if not messages:
        return "", ""

    summary_json = build_context_summary_json(branch_row, messages)
    summary_md = render_context_summary(summary_json)
    json_str = json.dumps(summary_json, ensure_ascii=False)

    return summary_md, json_str
