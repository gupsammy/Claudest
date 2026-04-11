#!/usr/bin/env python3
"""
Load previous session context from memory database for SessionStart hook.

Selection Algorithm (startup):
  Exclude current session, find most recent substantive (>2 exchanges)
  plus recent short sessions (2 exchanges) in remaining slots.

Selection Algorithm (clear):
  Read handoff file written by SessionEnd hook to hard-link to the exact
  cleared-from session. If not substantive (≤2 exchanges), append most
  recent substantive as supplementary. Falls through to startup logic
  if handoff file is missing, stale, or session not in DB.

Output: JSON with hookSpecificOutput for context injection
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add path to shared utils
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent / "skills" / "recall-conversations" / "scripts"))

from memory_lib.db import get_db_path, load_config, load_settings, setup_logging, get_db_connection
from memory_lib.formatting import format_time, format_time_full, get_project_key, normalize_cwd
from memory_lib.summarizer import build_exchange_pairs, truncate_mid


def _row_to_entry(row) -> dict:
    """Convert a candidate row to an entry dict."""
    _session_id, uuid, started_at, ended_at, exchange_count, files_json, commits_json, git_branch, branch_db_id, context_summary = row
    return {
        "uuid": uuid,
        "started_at": started_at,
        "ended_at": ended_at,
        "exchange_count": exchange_count,
        "files_modified": json.loads(files_json) if files_json else [],
        "commits": json.loads(commits_json) if commits_json else [],
        "git_branch": git_branch,
        "branch_db_id": branch_db_id,
        "context_summary": context_summary,
    }


_CANDIDATE_QUERY = """
    SELECT s.id, s.uuid, b.started_at, b.ended_at, b.exchange_count,
           b.files_modified, b.commits, s.git_branch, b.id as branch_db_id,
           b.context_summary
    FROM sessions s
    JOIN branches b ON b.session_id = s.id AND b.is_active = 1
    WHERE s.project_id = ?
      AND s.uuid != ?
      AND s.parent_session_id IS NULL
    ORDER BY b.ended_at DESC
    LIMIT 20
"""

_SESSION_BY_UUID_QUERY = """
    SELECT s.id, s.uuid, b.started_at, b.ended_at, b.exchange_count,
           b.files_modified, b.commits, s.git_branch, b.id as branch_db_id,
           b.context_summary
    FROM sessions s
    JOIN branches b ON b.session_id = s.id AND b.is_active = 1
    WHERE s.project_id = ?
      AND s.uuid = ?
      AND s.parent_session_id IS NULL
    ORDER BY b.ended_at DESC
    LIMIT 1
"""


def _find_first_substantive(cursor, project_id: int, exclude_uuid: str) -> dict | None:
    """Find the most recent substantive session (>2 exchanges), excluding a given uuid."""
    cursor.execute(_CANDIDATE_QUERY, (project_id, exclude_uuid))
    for row in cursor.fetchall():
        entry = _row_to_entry(row)
        if entry["exchange_count"] > 2:
            return entry
    return None


def _load_messages_for(cursor, entries: list[dict]) -> None:
    """Load messages for entries that lack a cached context_summary, in-place."""
    uncached_ids = [s["branch_db_id"] for s in entries if not s.get("context_summary")]
    if not uncached_ids:
        return

    placeholders = ",".join("?" * len(uncached_ids))
    cursor.execute(f"""
        SELECT bm.branch_id, m.role, m.content, m.timestamp
        FROM branch_messages bm
        JOIN messages m ON bm.message_id = m.id
        WHERE bm.branch_id IN ({placeholders})
          AND COALESCE(m.is_notification, 0) = 0
        ORDER BY bm.branch_id, m.timestamp ASC
    """, uncached_ids)

    branch_messages: dict[int, list[dict]] = {}
    for branch_id, role, content, timestamp in cursor.fetchall():
        branch_messages.setdefault(branch_id, []).append(
            {"role": role, "content": content, "timestamp": timestamp}
        )

    for entry in entries:
        if not entry.get("context_summary"):
            entry["messages"] = branch_messages.get(entry["branch_db_id"], [])


def _finalize(entries: list[dict]) -> list[dict]:
    """Strip internal branch_db_id from entries before returning."""
    for entry in entries:
        entry.pop("branch_db_id", None)
    return entries


def _find_cleared_from_session_uuid(db_path: Path, cwd: str) -> str | None:
    """
    Read and consume the clear-handoff.json file written by the SessionEnd hook.
    Returns the previous session_id if valid (recent, same cwd), otherwise None.
    Deletes on: valid consumption, stale timestamp, corrupt JSON.
    Preserves on: cwd mismatch (another session may claim it).
    """

    handoff_path = db_path.parent / "clear-handoff.json"
    if not handoff_path.exists():
        return None
    try:
        data = json.loads(handoff_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        try:
            handoff_path.unlink()
        except OSError:
            pass
        return None

    session_id = data.get("session_id")
    handoff_cwd = data.get("cwd")
    timestamp_str = data.get("timestamp")

    # Validate before consuming: wrong cwd means this handoff belongs to another session.
    # Normalize both sides so worktree paths (.claude/worktrees/<name>) match the base repo.
    if not session_id or normalize_cwd(handoff_cwd or "") != normalize_cwd(cwd):
        return None

    # Stale guard: reject handoffs older than 30 seconds
    if timestamp_str:
        try:
            written = datetime.fromisoformat(timestamp_str)
            age = (datetime.now(timezone.utc) - written).total_seconds()
            if age > 30:
                try:
                    handoff_path.unlink()
                except OSError:
                    pass
                return None
        except Exception:
            pass

    # Consume the file only after validation passes
    try:
        handoff_path.unlink()
    except OSError:
        pass

    return session_id


def select_sessions(conn: sqlite3.Connection, project_key: str, current_session_id: str, max_sessions: int, source: str = "startup", db_path: Path | None = None, cwd: str = "") -> list[dict]:
    """
    Select sessions for context using the exchange-count algorithm.

    On startup: exclude current session, find most recent substantive + recent shorts.
    On clear: read handoff file written by SessionEnd hook to hard-link to
              the exact cleared-from session by its session_id.
              If cleared-from is not substantive (≤2 exchanges), also append the most
              recent substantive session as supplementary context.
              Falls through to startup logic if cleared-from session can't be identified.
    """
    cursor = conn.cursor()

    # Get project ID
    cursor.execute("SELECT id FROM projects WHERE key = ?", (project_key,))
    row = cursor.fetchone()
    if not row:
        return []
    project_id = row[0]

    # --- Clear path: hard-link via handoff file written by SessionEnd hook ---
    if source == "clear" and db_path is not None:
        prev_session_uuid = _find_cleared_from_session_uuid(db_path, cwd)

        if prev_session_uuid:
            cursor.execute(_SESSION_BY_UUID_QUERY, (project_id, prev_session_uuid))
            prev_row = cursor.fetchone()

            if prev_row:
                cleared_from = _row_to_entry(prev_row)

                if cleared_from["exchange_count"] > 0:
                    filtered = [cleared_from]

                    # If cleared-from is not substantive, add supplementary
                    if cleared_from["exchange_count"] <= 2 and max_sessions > 1:
                        supplementary = _find_first_substantive(cursor, project_id, prev_session_uuid)
                        if supplementary:
                            filtered.append(supplementary)

                    _load_messages_for(cursor, filtered)
                    return _finalize(filtered)

        # Session not found in DB or no recent /clear — fall through to startup logic

    # --- Startup path (also fallback for clear with no handoff) ---
    cursor.execute(_CANDIDATE_QUERY, (project_id, current_session_id))
    candidates = cursor.fetchall()

    short_sessions = []  # exchange_count == 2
    substantive = None
    for row in candidates:
        entry = _row_to_entry(row)

        if entry["exchange_count"] <= 1:
            continue

        if entry["exchange_count"] == 2:
            short_sessions.append(entry)
            continue

        # First substantive session found — stop searching
        substantive = entry
        break

    # Build filtered list: substantive session always gets a slot,
    # remaining slots go to short sessions that are more recent
    if substantive:
        recent_shorts = short_sessions[:max_sessions - 1]
        filtered = recent_shorts + [substantive]
    else:
        filtered = short_sessions[:max_sessions]

    if not filtered:
        return []

    _load_messages_for(cursor, filtered)
    return _finalize(filtered)


def _build_fallback_context(session: dict) -> str:
    """
    Fallback for sessions without cached context_summary.
    Renders truncated last-3 exchanges in the same format as render_context_summary.
    """
    from memory_lib.summarizer import detect_disposition

    lines = []

    # Session header
    start = format_time_full(session["started_at"])
    end = format_time_full(session["ended_at"])
    header = f"### Session: {start} -> {end}"
    branch = session.get("git_branch")
    if branch:
        header += f" (branch: {branch})"
    uuid = session.get("uuid", "")
    if uuid:
        header += f" [{uuid}]"
    lines.append(header + "\n")

    # Build exchanges early so we can derive topic/disposition
    messages = session.get("messages", [])
    exchanges = build_exchange_pairs(messages) if messages else []

    # Topic and disposition
    topic = exchanges[0]["user"][:120] if exchanges else ""
    disposition = detect_disposition(exchanges) if exchanges else ""
    if topic or disposition:
        parts = []
        if topic:
            parts.append(f"**Topic:** {topic}")
        if disposition:
            parts.append(f"**Status:** {disposition}")
        lines.append(" | ".join(parts))
        lines.append("")

    # Files modified (compact)
    files = session.get("files_modified", [])
    if files:
        file_strs = [f"`{f}`" for f in files[:6]]
        line = "Modified: " + ", ".join(file_strs)
        if len(files) > 6:
            line += f" +{len(files) - 6} more"
        lines.append(line)

    # Commits
    commits = session.get("commits", [])
    if commits:
        lines.append("Commits: " + "; ".join(commits[:3]))

    lines.append("")

    if not exchanges:
        return "\n".join(lines)

    exchange_count = session.get("exchange_count", len(exchanges))

    if len(exchanges) <= 8:
        lines.append("### Conversation\n")
        for ex in exchanges:
            t = format_time(ex.get("timestamp"))
            lines.append(f"**[{t}] User:**")
            lines.append(ex["user"])
            lines.append("")
            if ex["assistant"]:
                lines.append(f"**[{t}] Assistant:**")
                lines.append(truncate_mid(ex["assistant"]))
                lines.append("")
    else:
        # Last 6 exchanges first — most recent context at top, where attention is highest
        # and where truncation (if any) will clip from below rather than above.
        lines.append("### Where We Left Off\n")
        for ex in exchanges[-6:]:
            t = format_time(ex.get("timestamp"))
            lines.append(f"**[{t}] User:**")
            lines.append(ex["user"])
            lines.append("")
            if ex["assistant"]:
                lines.append(f"**[{t}] Assistant:**")
                lines.append(truncate_mid(ex["assistant"]))
                lines.append("")

        # Gap with file summary
        gap = exchange_count - 8
        if gap > 0:
            gap_files = [f.rsplit("/", 1)[-1] for f in files[:3]] if files else []
            if gap_files:
                lines.append(f"[... {gap} earlier exchanges covering: {', '.join(gap_files)} ...]\n")
            else:
                lines.append(f"[... {gap} earlier exchanges ...]\n")

        # First 2 exchanges — kept for origin context, placed last so it's the
        # first thing clipped under inline-preview truncation.
        lines.append("### Earlier in This Session\n")
        for ex in exchanges[:2]:
            t = format_time(ex.get("timestamp"))
            lines.append(f"**[{t}] User:**")
            lines.append(ex["user"])
            lines.append("")
            if ex["assistant"]:
                lines.append(f"**[{t}] Assistant:**")
                lines.append(truncate_mid(ex["assistant"]))
                lines.append("")

    # Contextual recall priming footer
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


def _extract_topic(session: dict) -> str:
    """Extract topic string from a session entry.

    Prefers cached context_summary (always present for synced sessions).
    Falls back to first user message content for uncached branches.
    """
    cached = session.get("context_summary", "")
    if cached:
        for line in cached.splitlines():
            if "**Topic:**" in line:
                part = line.split("**Topic:**", 1)[1]
                return part.split(" | ")[0].strip()
    for msg in session.get("messages", []):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            return (content[:120] + "...") if len(content) > 120 else content
    return ""


def build_origin_block(source: str, sessions: list[dict]) -> str:
    """Build a structured Session Origin block that tells the new session where it came from."""
    if not sessions:
        return ""

    primary = sessions[0]
    started = format_time_full(primary.get("started_at", ""))
    ended = format_time_full(primary.get("ended_at", ""))
    branch = primary.get("git_branch", "unknown")
    exchanges = primary.get("exchange_count", 0)

    if source == "clear":
        source_label = "clear (continuing same session)"
    else:
        source_label = "startup (new session)"

    uuid = primary.get("uuid", "")
    lines = [
        "## Session Origin",
        f"- Source: {source_label}",
        f"- Session: {uuid}",
        f"- Previous session started: {started}",
        f"- Branch: {branch}",
        f"- Exchanges: {exchanges}",
        f"- Last active: {ended}",
    ]

    if source == "clear":
        topic = _extract_topic(primary)
        if topic:
            lines.append(f"- Topic: {topic}")

    if len(sessions) > 1:
        lines.append(f"- +{len(sessions) - 1} supplementary session(s) included below")

    return "\n".join(lines)


def build_context(sessions: list[dict]) -> str:
    """Build markdown context from selected sessions.

    Uses cached context_summary when available, falls back to
    truncated last-3 exchanges for uncached branches.
    """
    if not sessions:
        return ""

    parts = []
    for session in sessions:
        cached = session.get("context_summary")
        if cached:
            parts.append(cached)
        else:
            parts.append(_build_fallback_context(session))

    return "\n\n---\n\n".join(parts)


def main():
    # Load settings
    settings = load_settings()
    logger = setup_logging(settings)

    # Read hook input from stdin
    try:
        hook_input = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        hook_input = {}

    cwd = hook_input.get("cwd")
    session_id = hook_input.get("session_id")
    source = hook_input.get("source", "startup")

    # Only inject on fresh sessions
    if source not in ("startup", "clear"):
        print(json.dumps({}))
        return

    # Gate: require onboarding to be completed before injecting context
    config = load_config()
    if not config.get("onboarding_completed"):
        print(json.dumps({}))
        return

    # Check if auto-inject is disabled
    if not settings.get("auto_inject_context", True):
        logger.info("Context injection disabled by settings")
        print(json.dumps({}))
        return

    if not cwd or not session_id:
        print(json.dumps({}))
        return

    # Check if database exists
    db_path = get_db_path(settings)
    if not db_path.exists():
        print(json.dumps({}))
        return

    try:
        conn = get_db_connection(settings)
        project_key = get_project_key(cwd)
        max_sessions = settings.get("max_context_sessions", 2)
        sessions = select_sessions(conn, project_key, session_id, max_sessions, source=source, db_path=db_path, cwd=cwd)
        conn.close()

        if not sessions:
            print(json.dumps({}))
            return

        context = build_context(sessions)
        if not context:
            print(json.dumps({}))
            return

        logger.info(f"Injecting context from {len(sessions)} session(s) for project {project_key}")

        # Top-of-context directive: placed first because the hook's inline
        # preview may be truncated by the harness, and because earlier tokens
        # receive more attention. Tells Claude how to read the rest of this
        # injection and when to reach for the persisted file or recall skill.
        directive = (
            "## How To Use This Context\n"
            "- Sessions below are ordered most-recent first, and within each session "
            "the most recent exchanges come first. Read top-down to get the freshest "
            "context before older context.\n"
            "- If this hook's output was truncated inline and a persisted file path "
            "is referenced, Read that file before answering any message that references "
            "prior work — the last exchanges of the previous session may live only there.\n"
            "- For anything beyond the sessions shown here, use the "
            "`recall-conversations` skill rather than guessing."
        )

        # Wrap in directive + origin block + session content
        origin = build_origin_block(source, sessions)
        full_context = f"{directive}\n\n{origin}\n\n{context}"

        output = {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": full_context
            }
        }
        print(json.dumps(output))

    except Exception as e:
        logger.error(f"Context injection error: {e}")
        # Don't block session start on errors
        print(json.dumps({}))
        sys.exit(0)


if __name__ == "__main__":
    main()
