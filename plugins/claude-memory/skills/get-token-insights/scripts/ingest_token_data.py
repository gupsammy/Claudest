#!/usr/bin/env python3
"""
Token Insights v3 — JSONL-first ingest.

Parses raw JSONL conversation files from ~/.claude/projects/*/,
populates turns + turn_tool_calls + session_metrics tables,
backfills token_snapshots, and outputs a JSON analysis blob to stdout.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path.home() / ".claude-memory" / "conversations.db"
PROJECTS_DIR = Path.home() / ".claude" / "projects"
DASHBOARD_TEMPLATE_PATH = Path(__file__).parent.parent / "templates" / "dashboard.html"
DASHBOARD_OUT_PATH = DB_PATH.parent / "dashboard.html"

BATCH_SIZE = 50
PROGRESS_INTERVAL = 100
COMMAND_TRUNCATE = 200
SCHEMA_VERSION = 2

# ── Pricing (USD per million tokens) ─────────────────────────────────
# Source: https://docs.anthropic.com/en/docs/about-claude/pricing
# Keys are substrings matched against model IDs (checked in order).
# cache_write_5m = 1.25x input, cache_write_1h = 2x input, cache_read = 0.1x input.

MODEL_PRICING: list[tuple[str, dict[str, float]]] = [
    ("opus-4-6",   {"input": 5.0,  "output": 25.0, "cache_write_5m": 6.25, "cache_write_1h": 10.0, "cache_read": 0.50}),
    ("opus-4-5",   {"input": 5.0,  "output": 25.0, "cache_write_5m": 6.25, "cache_write_1h": 10.0, "cache_read": 0.50}),
    ("opus-4-1",   {"input": 15.0, "output": 75.0, "cache_write_5m": 18.75, "cache_write_1h": 30.0, "cache_read": 1.50}),
    ("opus-4",     {"input": 15.0, "output": 75.0, "cache_write_5m": 18.75, "cache_write_1h": 30.0, "cache_read": 1.50}),
    ("sonnet",     {"input": 3.0,  "output": 15.0, "cache_write_5m": 3.75, "cache_write_1h": 6.0, "cache_read": 0.30}),
    ("haiku",      {"input": 1.0,  "output": 5.0,  "cache_write_5m": 1.25, "cache_write_1h": 2.0, "cache_read": 0.10}),
]


def _get_pricing(model: str | None) -> dict[str, float]:
    """Return pricing dict for a model ID, falling back to Sonnet rates."""
    if model:
        m = model.lower()
        for substr, rates in MODEL_PRICING:
            if substr in m:
                return rates
    return MODEL_PRICING[4][1]  # default: sonnet


def _turn_cost(input_tok: int, output_tok: int, cache_read: int,
               cache_creation: int, ephem_5m: int, ephem_1h: int,
               pricing: dict[str, float]) -> float:
    """Compute dollar cost for a single turn."""
    # Cache creation split: use exact tier amounts where available,
    # attribute remainder to 5m tier (cheaper, conservative estimate).
    unclassified_creation = max(0, cache_creation - ephem_5m - ephem_1h)
    cost = (
        input_tok * pricing["input"]
        + output_tok * pricing["output"]
        + cache_read * pricing["cache_read"]
        + (ephem_5m + unclassified_creation) * pricing["cache_write_5m"]
        + ephem_1h * pricing["cache_write_1h"]
    ) / 1_000_000
    return cost

# ── Schema ────────────────────────────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS turns (
  id                    INTEGER PRIMARY KEY,
  session_id            TEXT NOT NULL,
  turn_index            INTEGER NOT NULL,
  timestamp             TEXT NOT NULL,
  model                 TEXT,
  input_tokens          INTEGER DEFAULT 0,
  output_tokens         INTEGER DEFAULT 0,
  cache_read_tokens     INTEGER DEFAULT 0,
  cache_creation_tokens INTEGER DEFAULT 0,
  ephem_5m_tokens       INTEGER DEFAULT 0,
  ephem_1h_tokens       INTEGER DEFAULT 0,
  thinking_tokens       INTEGER DEFAULT 0,
  stop_reason           TEXT,
  turn_duration_ms      INTEGER,
  user_gap_ms           INTEGER,
  is_sidechain          INTEGER DEFAULT 0,
  cache_read_ratio      REAL,
  UNIQUE(session_id, turn_index)
);
CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id);
CREATE INDEX IF NOT EXISTS idx_turns_ts ON turns(timestamp);

CREATE TABLE IF NOT EXISTS turn_tool_calls (
  id          INTEGER PRIMARY KEY,
  turn_id     INTEGER NOT NULL REFERENCES turns(id) ON DELETE CASCADE,
  session_id  TEXT NOT NULL,
  tool_name   TEXT NOT NULL,
  tool_use_id TEXT,
  file_path   TEXT,
  command     TEXT,
  is_error    INTEGER DEFAULT 0,
  error_text  TEXT,
  agent_id       TEXT,
  skill_name     TEXT,
  subagent_type  TEXT,
  agent_model    TEXT
);
CREATE INDEX IF NOT EXISTS idx_ttc_turn ON turn_tool_calls(turn_id);
CREATE INDEX IF NOT EXISTS idx_ttc_session ON turn_tool_calls(session_id);
CREATE INDEX IF NOT EXISTS idx_ttc_tool ON turn_tool_calls(tool_name);
CREATE TABLE IF NOT EXISTS session_metrics (
  session_id          TEXT PRIMARY KEY,
  project_path        TEXT,
  git_branch          TEXT,
  cc_version          TEXT,
  slug                TEXT,
  entrypoint          TEXT,
  is_sidechain        INTEGER DEFAULT 0,
  parent_session_id   TEXT,
  first_turn_ts       TEXT,
  last_turn_ts        TEXT,
  turn_count          INTEGER DEFAULT 0,
  user_msg_count      INTEGER DEFAULT 0,
  total_input_tokens  INTEGER DEFAULT 0,
  total_output_tokens INTEGER DEFAULT 0,
  total_cache_read    INTEGER DEFAULT 0,
  total_cache_creation INTEGER DEFAULT 0,
  total_ephem_5m      INTEGER DEFAULT 0,
  total_ephem_1h      INTEGER DEFAULT 0,
  total_thinking      INTEGER DEFAULT 0,
  total_turn_ms       INTEGER DEFAULT 0,
  total_hook_ms       INTEGER DEFAULT 0,
  api_error_count     INTEGER DEFAULT 0,
  cache_cliff_count   INTEGER DEFAULT 0,
  tool_error_count    INTEGER DEFAULT 0,
  max_tokens_stops    INTEGER DEFAULT 0,
  uses_agent          INTEGER DEFAULT 0,
  models_used         TEXT,
  model_switch_count  INTEGER DEFAULT 0,
  imported_at         TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_sm_project ON session_metrics(project_path);
CREATE INDEX IF NOT EXISTS idx_sm_ts ON session_metrics(first_turn_ts);
CREATE INDEX IF NOT EXISTS idx_sm_sidechain ON session_metrics(is_sidechain);

CREATE TABLE IF NOT EXISTS hook_executions (
  id           INTEGER PRIMARY KEY,
  session_id   TEXT NOT NULL,
  hook_command TEXT NOT NULL,
  duration_ms  INTEGER DEFAULT 0,
  is_error     INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_he_session ON hook_executions(session_id);
CREATE INDEX IF NOT EXISTS idx_he_command ON hook_executions(hook_command);

CREATE TABLE IF NOT EXISTS import_log (
  id INTEGER PRIMARY KEY,
  file_path TEXT UNIQUE NOT NULL,
  file_hash TEXT,
  imported_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  messages_imported INTEGER DEFAULT 0
);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    # Ensure token_snapshots table exists (for backfill target)
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
    """)
    # Add data_source column if missing
    for col, typedef in [("data_source", "TEXT"), ("cache_read_tokens", "INTEGER DEFAULT 0"),
                          ("cache_creation_tokens", "INTEGER DEFAULT 0")]:
        try:
            conn.execute(f"ALTER TABLE token_snapshots ADD COLUMN {col} {typedef}")
        except sqlite3.OperationalError:
            pass
    # Ensure import_log has mtime_ns
    try:
        conn.execute("ALTER TABLE import_log ADD COLUMN mtime_ns INTEGER")
    except sqlite3.OperationalError:
        pass
    # Add new workflow-analytics columns if missing (v2 schema)
    for col, typedef in [("skill_name", "TEXT"), ("subagent_type", "TEXT"), ("agent_model", "TEXT")]:
        try:
            conn.execute(f"ALTER TABLE turn_tool_calls ADD COLUMN {col} {typedef}")
        except sqlite3.OperationalError:
            pass
    # Create indexes for new columns (safe after ALTER TABLE)
    for idx_sql in [
        "CREATE INDEX IF NOT EXISTS idx_ttc_skill ON turn_tool_calls(skill_name)",
        "CREATE INDEX IF NOT EXISTS idx_ttc_subagent ON turn_tool_calls(subagent_type)",
    ]:
        conn.execute(idx_sql)
    # Schema version tracking + auto re-import on upgrade
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
    row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
    current = row[0] if row else 0
    if current < SCHEMA_VERSION:
        print(f"Schema upgraded to v{SCHEMA_VERSION} — full re-import required", file=sys.stderr)
        conn.execute("DELETE FROM import_log")
        if current == 0:
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
        else:
            conn.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION,))
    conn.commit()


# ── File Discovery ────────────────────────────────────────────────────

@dataclass
class JnlFile:
    path: Path
    project_cwd: str
    is_sidechain: bool
    parent_session_id: str | None


def _decode_project_cwd(dirname: str) -> str:
    """Convert '-Users-samarthgupta-repos-foo' to '/Users/samarthgupta/repos/foo'."""
    return "/" + dirname.lstrip("-").replace("-", "/")


def discover_jsonl_files() -> list[JnlFile]:
    results: list[JnlFile] = []
    if not PROJECTS_DIR.exists():
        return results
    for proj_dir in PROJECTS_DIR.iterdir():
        if not proj_dir.is_dir():
            continue
        project_cwd = _decode_project_cwd(proj_dir.name)
        # Top-level session files
        for jf in proj_dir.glob("*.jsonl"):
            results.append(JnlFile(jf, project_cwd, False, None))
        # Subagent files
        for jf in proj_dir.glob("*/subagents/*.jsonl"):
            parent_id = jf.parent.parent.name  # the session UUID directory
            results.append(JnlFile(jf, project_cwd, True, parent_id))
    return results


def should_skip_file(conn: sqlite3.Connection, filepath: Path) -> bool:
    try:
        mtime_ns = filepath.stat().st_mtime_ns
    except OSError:
        return True
    cur = conn.execute(
        "SELECT mtime_ns FROM import_log WHERE file_path = ?",
        (str(filepath),)
    )
    row = cur.fetchone()
    if row and row[0] == mtime_ns:
        return True
    return False


def record_import(conn: sqlite3.Connection, filepath: Path, session_id: str, turn_count: int) -> None:
    mtime_ns = filepath.stat().st_mtime_ns
    conn.execute(
        """INSERT OR REPLACE INTO import_log (file_path, file_hash, imported_at, messages_imported, mtime_ns)
           VALUES (?, ?, datetime('now'), ?, ?)""",
        (str(filepath), session_id, turn_count, mtime_ns)
    )


# ── JSONL Parser ──────────────────────────────────────────────────────

@dataclass
class ToolCall:
    tool_name: str
    tool_use_id: str
    file_path: str | None = None
    command: str | None = None
    is_error: int = 0
    error_text: str | None = None
    agent_id: str | None = None
    skill_name: str | None = None
    subagent_type: str | None = None
    agent_model: str | None = None


@dataclass
class Turn:
    index: int
    message_id: str
    timestamp: str
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    ephem_5m_tokens: int = 0
    ephem_1h_tokens: int = 0
    thinking_tokens: int = 0
    stop_reason: str | None = None
    turn_duration_ms: int | None = None
    user_gap_ms: int | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    # Pending tool_use IDs awaiting results
    _pending_tools: dict[str, ToolCall] = field(default_factory=dict)


@dataclass
class ParsedSession:
    session_id: str
    project_path: str | None = None
    git_branch: str | None = None
    cc_version: str | None = None
    slug: str | None = None
    entrypoint: str | None = None
    turns: list[Turn] = field(default_factory=list)
    user_msg_count: int = 0
    api_error_count: int = 0
    total_hook_ms: int = 0
    uses_agent: bool = False
    hook_calls: list[dict] = field(default_factory=list)


def _parse_timestamp(line: dict) -> str | None:
    return line.get("timestamp")


def _extract_usage(msg: dict) -> dict:
    usage = msg.get("usage", {}) or {}
    cache_creation = usage.get("cache_creation", {}) or {}
    return {
        "input_tokens": usage.get("input_tokens", 0) or 0,
        "output_tokens": usage.get("output_tokens", 0) or 0,
        "cache_read_tokens": usage.get("cache_read_input_tokens", 0) or 0,
        "cache_creation_tokens": usage.get("cache_creation_input_tokens", 0) or 0,
        "ephem_5m_tokens": cache_creation.get("ephemeral_5m_input_tokens", 0) or 0,
        "ephem_1h_tokens": cache_creation.get("ephemeral_1h_input_tokens", 0) or 0,
    }


def parse_session(filepath: Path, jnl: JnlFile) -> ParsedSession | None:
    session = ParsedSession(session_id="", project_path=jnl.project_cwd)

    current_turn: Turn | None = None
    turn_index = 0
    last_assistant_ts: str | None = None
    metadata_captured = False

    try:
        lines = filepath.read_text().splitlines()
    except Exception:
        return None

    for raw_line in lines:
        if not raw_line.strip():
            continue
        try:
            line = json.loads(raw_line)
        except json.JSONDecodeError:
            continue

        line_type = line.get("type")
        subtype = line.get("subtype", "")
        ts = _parse_timestamp(line)

        # Capture session metadata from any line that has it
        if not metadata_captured:
            sid = line.get("sessionId")
            if sid:
                session.session_id = sid
                metadata_captured = True
        if line.get("sessionId") and not session.session_id:
            session.session_id = line["sessionId"]
        if line.get("version") and not session.cc_version:
            session.cc_version = line["version"]
        if line.get("slug") and not session.slug:
            session.slug = line["slug"]
        if line.get("entrypoint") and not session.entrypoint:
            session.entrypoint = line["entrypoint"]
        # Take LAST observed branch (can change mid-session)
        if line.get("gitBranch"):
            session.git_branch = line["gitBranch"]

        # ── Assistant events (grouped by message.id) ──
        if line_type == "assistant":
            msg = line.get("message", {}) or {}
            mid = msg.get("id", "")
            if not mid:
                continue

            # Same logical turn?
            if current_turn and current_turn.message_id == mid:
                # Merge: accumulate tool_use blocks, update usage to latest
                pass
            else:
                # Finalize previous turn
                if current_turn:
                    session.turns.append(current_turn)
                # Start new turn
                turn_index += 1
                current_turn = Turn(
                    index=turn_index,
                    message_id=mid,
                    timestamp=ts or "",
                    model=msg.get("model"),
                )
                # Compute user gap from last assistant finish
                if last_assistant_ts and ts:
                    try:
                        prev = datetime.fromisoformat(last_assistant_ts.replace("Z", "+00:00"))
                        curr = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                        current_turn.user_gap_ms = int((curr - prev).total_seconds() * 1000)
                    except (ValueError, TypeError):
                        pass

            # Always update usage from the latest event for this message.id
            u = _extract_usage(msg)
            current_turn.input_tokens = u["input_tokens"]
            current_turn.output_tokens = u["output_tokens"]
            current_turn.cache_read_tokens = u["cache_read_tokens"]
            current_turn.cache_creation_tokens = u["cache_creation_tokens"]
            current_turn.ephem_5m_tokens = u["ephem_5m_tokens"]
            current_turn.ephem_1h_tokens = u["ephem_1h_tokens"]

            stop = msg.get("stop_reason")
            if stop:
                current_turn.stop_reason = stop

            if msg.get("model"):
                current_turn.model = msg["model"]

            # Extract content blocks
            content = msg.get("content", []) or []
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")

                if btype == "thinking":
                    # Count thinking text length as proxy (actual token count not in JSONL)
                    thinking_text = block.get("thinking", "")
                    # Rough estimate: 1 token ≈ 4 chars
                    current_turn.thinking_tokens += len(thinking_text) // 4

                elif btype == "tool_use":
                    tc = ToolCall(
                        tool_name=block.get("name", "unknown"),
                        tool_use_id=block.get("id", ""),
                    )
                    inp = block.get("input", {}) or {}
                    # Extract file_path from various tool input formats
                    tc.file_path = inp.get("file_path") or inp.get("path") or inp.get("file") or None
                    if "command" in inp:
                        tc.command = str(inp["command"])[:COMMAND_TRUNCATE]

                    # Extract workflow-specific metadata
                    if tc.tool_name == "Skill":
                        tc.skill_name = inp.get("skill") or None
                    elif tc.tool_name == "Agent":
                        session.uses_agent = True
                        tc.subagent_type = inp.get("subagent_type") or None
                        tc.agent_model = inp.get("model") or None
                        # Store agent description as command if no command set
                        if not tc.command:
                            desc = inp.get("description") or ""
                            if desc:
                                tc.command = str(desc)[:COMMAND_TRUNCATE]

                    current_turn.tool_calls.append(tc)
                    current_turn._pending_tools[tc.tool_use_id] = tc

        # ── User events ──
        elif line_type == "user":
            session.user_msg_count += 1

            msg = line.get("message", {}) or {}
            content = msg.get("content", []) or []
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_result":
                    tuid = block.get("tool_use_id", "")
                    is_err = block.get("is_error", False)
                    # Match to pending tool call (pop to avoid re-matching)
                    if current_turn:
                        tc = current_turn._pending_tools.pop(tuid, None)
                        if tc:
                            tc.is_error = 1 if is_err else 0
                            if is_err:
                                # Extract error text
                                result_content = block.get("content", "")
                                if isinstance(result_content, list):
                                    texts = [c.get("text", "") for c in result_content if isinstance(c, dict)]
                                    result_content = " ".join(texts)
                                tc.error_text = str(result_content)[:200] if result_content else None

        # ── System events ──
        elif line_type == "system":
            if subtype == "turn_duration":
                duration_ms = line.get("durationMs")
                if duration_ms is not None and current_turn:
                    current_turn.turn_duration_ms = duration_ms
                    if ts:
                        last_assistant_ts = ts
            elif subtype in ("stop_hook_summary", "hook_summary"):
                hook_infos = line.get("hookInfos", []) or []
                hook_errors = line.get("hookErrors", []) or []
                error_commands = {e.get("command") for e in hook_errors if isinstance(e, dict)}
                for h in hook_infos:
                    dur = h.get("durationMs", 0) or 0
                    session.total_hook_ms += dur
                    cmd = h.get("command") or h.get("hook_command") or "unknown"
                    session.hook_calls.append({
                        "hook_command": str(cmd)[:COMMAND_TRUNCATE],
                        "duration_ms": dur,
                        "is_error": 1 if cmd in error_commands else 0,
                    })
            elif subtype == "api_error":
                session.api_error_count += 1

        # Skip: progress, file-history-snapshot, local_command, etc.

    # Finalize last turn
    if current_turn:
        session.turns.append(current_turn)

    if not session.session_id:
        # Derive from filename
        session.session_id = filepath.stem

    # Subagent JSONL files inherit the parent's sessionId — use the filename
    # as a unique ID to avoid overwriting the parent session's data.
    if jnl.is_sidechain:
        session.session_id = filepath.stem

    return session if session.turns else None


# ── Session Analytics ─────────────────────────────────────────────────

def compute_session_analytics(session: ParsedSession) -> dict:
    """Compute cache cliffs, max_tokens stops, model switches."""
    cache_cliff_count = 0
    max_tokens_stops = 0
    model_switch_count = 0
    prev_model = None
    prev_cache_ratio = None

    models_seen: list[str] = []
    total_turn_ms = 0
    total_tool_errors = 0

    for turn in session.turns:
        # Cache cliff detection
        denom = turn.cache_read_tokens + turn.cache_creation_tokens
        ratio = turn.cache_read_tokens / denom if denom > 0 else None
        turn.cache_read_ratio = ratio

        if ratio is not None and prev_cache_ratio is not None:
            drop = prev_cache_ratio - ratio
            if drop > 0.5 and turn.user_gap_ms and turn.user_gap_ms > 300_000:
                cache_cliff_count += 1
        if ratio is not None:
            prev_cache_ratio = ratio

        # Max tokens stops
        if turn.stop_reason == "max_tokens":
            max_tokens_stops += 1

        # Model switches
        if turn.model:
            if prev_model and turn.model != prev_model:
                model_switch_count += 1
            prev_model = turn.model
            if turn.model not in models_seen:
                models_seen.append(turn.model)

        # Accumulate
        if turn.turn_duration_ms:
            total_turn_ms += turn.turn_duration_ms
        for tc in turn.tool_calls:
            if tc.is_error:
                total_tool_errors += 1

    return {
        "cache_cliff_count": cache_cliff_count,
        "max_tokens_stops": max_tokens_stops,
        "model_switch_count": model_switch_count,
        "models_used": models_seen,
        "total_turn_ms": total_turn_ms,
        "tool_error_count": total_tool_errors,
    }


# ── DB Import ─────────────────────────────────────────────────────────

def import_session(conn: sqlite3.Connection, session: ParsedSession, jnl: JnlFile) -> None:
    sid = session.session_id

    # Delete existing data for this session (idempotent re-import)
    conn.execute("DELETE FROM hook_executions WHERE session_id = ?", (sid,))
    conn.execute("DELETE FROM turn_tool_calls WHERE session_id = ?", (sid,))
    conn.execute("DELETE FROM turns WHERE session_id = ?", (sid,))
    conn.execute("DELETE FROM session_metrics WHERE session_id = ?", (sid,))

    analytics = compute_session_analytics(session)

    # Insert turns
    for turn in session.turns:
        conn.execute(
            """INSERT INTO turns (session_id, turn_index, timestamp, model,
               input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens,
               ephem_5m_tokens, ephem_1h_tokens, thinking_tokens, stop_reason,
               turn_duration_ms, user_gap_ms, is_sidechain, cache_read_ratio)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (sid, turn.index, turn.timestamp, turn.model,
             turn.input_tokens, turn.output_tokens, turn.cache_read_tokens, turn.cache_creation_tokens,
             turn.ephem_5m_tokens, turn.ephem_1h_tokens, turn.thinking_tokens, turn.stop_reason,
             turn.turn_duration_ms, turn.user_gap_ms, 1 if jnl.is_sidechain else 0,
             turn.cache_read_ratio)
        )
        turn_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        # Insert tool calls
        for tc in turn.tool_calls:
            conn.execute(
                """INSERT INTO turn_tool_calls (turn_id, session_id, tool_name, tool_use_id,
                   file_path, command, is_error, error_text, agent_id,
                   skill_name, subagent_type, agent_model)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (turn_id, sid, tc.tool_name, tc.tool_use_id,
                 tc.file_path, tc.command, tc.is_error, tc.error_text, tc.agent_id,
                 tc.skill_name, tc.subagent_type, tc.agent_model)
            )

    # Insert session_metrics
    first_ts = session.turns[0].timestamp if session.turns else None
    last_ts = session.turns[-1].timestamp if session.turns else None

    conn.execute(
        """INSERT INTO session_metrics (session_id, project_path, git_branch, cc_version,
           slug, entrypoint, is_sidechain, parent_session_id,
           first_turn_ts, last_turn_ts, turn_count, user_msg_count,
           total_input_tokens, total_output_tokens, total_cache_read, total_cache_creation,
           total_ephem_5m, total_ephem_1h, total_thinking,
           total_turn_ms, total_hook_ms, api_error_count,
           cache_cliff_count, tool_error_count, max_tokens_stops,
           uses_agent, models_used, model_switch_count)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (sid, session.project_path, session.git_branch, session.cc_version,
         session.slug, session.entrypoint, 1 if jnl.is_sidechain else 0, jnl.parent_session_id,
         first_ts, last_ts, len(session.turns), session.user_msg_count,
         sum(t.input_tokens for t in session.turns),
         sum(t.output_tokens for t in session.turns),
         sum(t.cache_read_tokens for t in session.turns),
         sum(t.cache_creation_tokens for t in session.turns),
         sum(t.ephem_5m_tokens for t in session.turns),
         sum(t.ephem_1h_tokens for t in session.turns),
         sum(t.thinking_tokens for t in session.turns),
         analytics["total_turn_ms"], session.total_hook_ms, session.api_error_count,
         analytics["cache_cliff_count"], analytics["tool_error_count"],
         analytics["max_tokens_stops"],
         1 if session.uses_agent else 0,
         json.dumps(analytics["models_used"]),
         analytics["model_switch_count"])
    )

    # Insert per-hook execution records
    for hc in session.hook_calls:
        conn.execute(
            """INSERT INTO hook_executions (session_id, hook_command, duration_ms, is_error)
               VALUES (?,?,?,?)""",
            (sid, hc["hook_command"], hc["duration_ms"], hc["is_error"])
        )


# ── Backfill token_snapshots ─────────────────────────────────────────

def backfill_token_snapshots(conn: sqlite3.Connection) -> None:
    """Upsert quantitative data from session_metrics into token_snapshots.
    Preserves AI-generated facet columns (outcome, session_type, etc.)."""

    # Build tool_counts JSON per session from turn_tool_calls
    tool_counts_by_session: dict[str, dict[str, int]] = {}
    cur = conn.execute(
        "SELECT session_id, tool_name, COUNT(*) FROM turn_tool_calls GROUP BY session_id, tool_name"
    )
    for sid, tool, cnt in cur:
        if sid not in tool_counts_by_session:
            tool_counts_by_session[sid] = {}
        tool_counts_by_session[sid][tool] = cnt

    rows = conn.execute(
        """SELECT session_id, project_path, first_turn_ts, turn_count, user_msg_count,
                  total_input_tokens, total_output_tokens, total_cache_read, total_cache_creation,
                  tool_error_count, uses_agent, total_turn_ms
           FROM session_metrics WHERE is_sidechain = 0"""
    ).fetchall()

    for row in rows:
        (sid, project, first_ts, turns, user_msgs, inp, out, cr, cc,
         tool_errs, uses_agent, turn_ms) = row

        duration_min = round(turn_ms / 60000, 1) if turn_ms else None
        tc_json = json.dumps(tool_counts_by_session.get(sid, {}))

        conn.execute(
            """INSERT INTO token_snapshots (session_uuid, project_path, start_time,
                   duration_minutes, user_message_count, assistant_message_count,
                   input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens,
                   tool_counts, tool_errors, uses_task_agent, data_source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'jsonl_v3')
               ON CONFLICT(session_uuid) DO UPDATE SET
                   input_tokens = excluded.input_tokens,
                   output_tokens = excluded.output_tokens,
                   cache_read_tokens = excluded.cache_read_tokens,
                   cache_creation_tokens = excluded.cache_creation_tokens,
                   tool_counts = excluded.tool_counts,
                   tool_errors = excluded.tool_errors,
                   duration_minutes = excluded.duration_minutes,
                   user_message_count = excluded.user_message_count,
                   assistant_message_count = excluded.assistant_message_count,
                   uses_task_agent = excluded.uses_task_agent,
                   data_source = 'jsonl_v3'
            """,
            (sid, project, first_ts, duration_min, user_msgs, turns,
             inp, out, cr, cc, tc_json, tool_errs, 1 if uses_agent else 0)
        )
    conn.commit()


# ── Helpers ───────────────────────────────────────────────────────────

def _percentile(sorted_vals: list, p: float) -> float:
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * p / 100.0
    f = int(k)
    c = f + 1
    if c >= len(sorted_vals):
        return float(sorted_vals[-1])
    return sorted_vals[f] + (k - f) * (sorted_vals[c] - sorted_vals[f])


def _avg(arr: list) -> float:
    return sum(arr) / len(arr) if arr else 0.0


def _project_slug(path: str | None) -> str:
    """Build a short but unique project label from the full path.

    Uses the last 2-3 meaningful path segments, skipping common prefixes
    like /Users/<user>/repos/*, to produce labels like 'meta-ads-cli'
    instead of just 'cli'.
    """
    if not path:
        return "unknown"
    path = path.rstrip("/")
    parts = path.split("/")
    # Drop common path prefixes to find the meaningful suffix
    # e.g. /Users/samarthgupta/repos/forks/meta/ads/cli → meta/ads/cli
    skip_prefixes = {"Users", "home", "repos", "myrepos", "forks", "projects"}
    meaningful = []
    for p in parts:
        if not p or p in skip_prefixes or p.startswith("."):
            continue
        # Skip the username segment (first non-empty after /Users/)
        if len(meaningful) == 0 and len(parts) > 3 and parts[1] in ("Users", "home"):
            # This is the username — skip it
            if p == parts[2]:
                continue
        meaningful.append(p)
    # Take last 2-3 segments depending on length
    if len(meaningful) <= 2:
        slug = "-".join(meaningful) if meaningful else "unknown"
    else:
        slug = "-".join(meaningful[-3:])
    # Cap length for chart labels
    return slug[:30] if slug else "unknown"


# ── Build Output ──────────────────────────────────────────────────────

def build_output(conn: sqlite3.Connection) -> dict:
    cur = conn.cursor()

    # ── KPI totals (top-level sessions only) ──
    kpis = cur.execute("""
        SELECT COUNT(*), SUM(turn_count), SUM(total_output_tokens),
               SUM(total_cache_read), SUM(total_cache_creation),
               SUM(cache_cliff_count), SUM(max_tokens_stops),
               SUM(tool_error_count), SUM(total_input_tokens),
               SUM(total_thinking)
        FROM session_metrics WHERE is_sidechain = 0
    """).fetchone()
    total_sessions = kpis[0] or 0
    total_turns = kpis[1] or 0
    total_output = kpis[2] or 0
    total_cache_read = kpis[3] or 0
    total_cache_creation = kpis[4] or 0
    total_cache_cliffs = kpis[5] or 0
    total_max_token_stops = kpis[6] or 0
    total_tool_errors = kpis[7] or 0
    total_input = kpis[8] or 0
    total_thinking = kpis[9] or 0

    cache_denom = total_cache_read + total_cache_creation
    global_cache_ratio = round(total_cache_read / cache_denom, 4) if cache_denom > 0 else 0.0

    # Total tool calls (denominator for error rate KPI)
    total_tool_calls = cur.execute("""
        SELECT COUNT(*) FROM turn_tool_calls tc
        JOIN session_metrics sm ON tc.session_id = sm.session_id AND sm.is_sidechain = 0
    """).fetchone()[0] or 0

    # Date range
    dr = cur.execute("""
        SELECT MIN(first_turn_ts), MAX(last_turn_ts)
        FROM session_metrics WHERE is_sidechain = 0
    """).fetchone()

    # ── Chart 1: Sessions by day ──
    sessions_by_day = []
    for row in cur.execute("""
        SELECT DATE(first_turn_ts) as day, COUNT(*),
               SUM(total_input_tokens), SUM(total_output_tokens),
               SUM(total_cache_read), SUM(total_cache_creation)
        FROM session_metrics WHERE is_sidechain = 0 AND first_turn_ts IS NOT NULL
        GROUP BY day ORDER BY day
    """):
        sessions_by_day.append({
            "date": row[0], "session_count": row[1],
            "input_tokens": row[2], "output_tokens": row[3],
            "cache_read": row[4], "cache_creation": row[5],
        })

    # ── Chart 3: Top 10 tools ──
    top_tools = []
    for row in cur.execute("""
        SELECT tool_name, COUNT(*) as cnt
        FROM turn_tool_calls tc
        JOIN session_metrics sm ON tc.session_id = sm.session_id AND sm.is_sidechain = 0
        GROUP BY tool_name ORDER BY cnt DESC LIMIT 15
    """):
        top_tools.append({"tool": row[0], "count": row[1]})

    # ── Chart 4: Model cost split (with dollar costs) ──
    model_split = []
    for row in cur.execute("""
        SELECT model, SUM(input_tokens) as inp, SUM(output_tokens) as out,
               SUM(thinking_tokens) as think,
               SUM(cache_read_tokens) as cr, SUM(cache_creation_tokens) as cc,
               SUM(ephem_5m_tokens) as e5, SUM(ephem_1h_tokens) as e1
        FROM turns t
        JOIN session_metrics sm ON t.session_id = sm.session_id AND sm.is_sidechain = 0
        WHERE model IS NOT NULL
        GROUP BY model ORDER BY inp + out DESC
    """):
        pricing = _get_pricing(row[0])
        cost = _turn_cost(row[1], row[2], row[4], row[5], row[6], row[7], pricing)
        model_split.append({
            "model": row[0], "input_tokens": row[1],
            "output_tokens": row[2], "thinking_tokens": row[3],
            "cost_usd": round(cost, 4),
        })

    # ── Dollar cost: total, by day, by project ──
    total_cost_usd = sum(m["cost_usd"] for m in model_split)

    # Cost by day (per-turn granularity for accurate model-aware costing)
    cost_by_day: dict[str, float] = {}
    for row in cur.execute("""
        SELECT DATE(t.timestamp) as day, t.model,
               SUM(t.input_tokens), SUM(t.output_tokens),
               SUM(t.cache_read_tokens), SUM(t.cache_creation_tokens),
               SUM(t.ephem_5m_tokens), SUM(t.ephem_1h_tokens)
        FROM turns t
        JOIN session_metrics sm ON t.session_id = sm.session_id AND sm.is_sidechain = 0
        WHERE t.timestamp IS NOT NULL
        GROUP BY day, t.model
    """):
        day = row[0]
        pricing = _get_pricing(row[1])
        day_cost = _turn_cost(row[2], row[3], row[4], row[5], row[6], row[7], pricing)
        cost_by_day[day] = cost_by_day.get(day, 0.0) + day_cost

    cost_by_day_list = [{"date": d, "cost_usd": round(c, 4)}
                        for d, c in sorted(cost_by_day.items())]

    # Cost by project
    cost_by_project: dict[str, float] = {}
    for row in cur.execute("""
        SELECT sm.project_path, t.model,
               SUM(t.input_tokens), SUM(t.output_tokens),
               SUM(t.cache_read_tokens), SUM(t.cache_creation_tokens),
               SUM(t.ephem_5m_tokens), SUM(t.ephem_1h_tokens)
        FROM turns t
        JOIN session_metrics sm ON t.session_id = sm.session_id AND sm.is_sidechain = 0
        GROUP BY sm.project_path, t.model
    """):
        slug = _project_slug(row[0])
        pricing = _get_pricing(row[1])
        proj_cost = _turn_cost(row[2], row[3], row[4], row[5], row[6], row[7], pricing)
        cost_by_project[slug] = cost_by_project.get(slug, 0.0) + proj_cost

    cost_by_project_list = sorted(
        [{"project": p, "cost_usd": round(c, 4)} for p, c in cost_by_project.items()],
        key=lambda x: x["cost_usd"], reverse=True,
    )[:10]

    # ── Chart 5: Cache trajectory (5 sample sessions with most cache data) ──
    cache_trajectory = []
    trajectory_sessions = cur.execute("""
        SELECT session_id FROM session_metrics
        WHERE is_sidechain = 0 AND total_cache_read + total_cache_creation > 0
        ORDER BY total_cache_read + total_cache_creation DESC LIMIT 5
    """).fetchall()
    for (tsid,) in trajectory_sessions:
        turns_data = []
        for row in cur.execute("""
            SELECT turn_index, cache_read_ratio, cache_read_tokens, cache_creation_tokens, user_gap_ms
            FROM turns WHERE session_id = ? ORDER BY turn_index LIMIT 30
        """, (tsid,)):
            turns_data.append({
                "turn": row[0], "ratio": row[1], "read": row[2],
                "creation": row[3], "gap_ms": row[4],
            })
        proj = cur.execute(
            "SELECT project_path FROM session_metrics WHERE session_id = ?", (tsid,)
        ).fetchone()
        cache_trajectory.append({
            "session_id": tsid[:8],
            "project": _project_slug(proj[0] if proj else None),
            "turns": turns_data,
        })

    # ── Chart 6: Ephemeral cache tier split by project ──
    ephem_split = []
    for row in cur.execute("""
        SELECT sm.project_path, SUM(t.ephem_5m_tokens) as e5, SUM(t.ephem_1h_tokens) as e1
        FROM turns t
        JOIN session_metrics sm ON t.session_id = sm.session_id AND sm.is_sidechain = 0
        GROUP BY sm.project_path
        HAVING e5 + e1 > 0
        ORDER BY e5 + e1 DESC LIMIT 8
    """):
        ephem_split.append({
            "project": _project_slug(row[0]),
            "ephem_5m": row[1], "ephem_1h": row[2],
        })

    # ── Chart 7: Bash antipattern rate by project (computed at query time) ──
    bash_antipatterns = []
    for row in cur.execute("""
        SELECT sm.project_path,
               SUM(CASE WHEN tc.tool_name = 'Bash' AND (
                   tc.command LIKE 'cat %' OR tc.command LIKE 'head %' OR
                   tc.command LIKE 'tail %' OR tc.command LIKE 'grep %' OR
                   tc.command LIKE 'find %' OR tc.command LIKE 'ls %'
               ) AND tc.command NOT LIKE 'cat <<%' THEN 1 ELSE 0 END) as antipatterns,
               SUM(CASE WHEN tc.tool_name = 'Bash' THEN 1 ELSE 0 END) as total_bash
        FROM turn_tool_calls tc
        JOIN session_metrics sm ON tc.session_id = sm.session_id AND sm.is_sidechain = 0
        GROUP BY sm.project_path
        HAVING antipatterns > 0
        ORDER BY antipatterns DESC LIMIT 10
    """):
        bash_antipatterns.append({
            "project": _project_slug(row[0]),
            "antipatterns": row[1], "total_bash": row[2],
        })
    # TRUE total across all projects (not capped by chart LIMIT)
    total_bash_antipatterns = cur.execute("""
        SELECT SUM(CASE WHEN tc.tool_name = 'Bash' AND (
            tc.command LIKE 'cat %' OR tc.command LIKE 'head %' OR
            tc.command LIKE 'tail %' OR tc.command LIKE 'grep %' OR
            tc.command LIKE 'find %' OR tc.command LIKE 'ls %'
        ) AND tc.command NOT LIKE 'cat <<%' THEN 1 ELSE 0 END)
        FROM turn_tool_calls tc
        JOIN session_metrics sm ON tc.session_id = sm.session_id AND sm.is_sidechain = 0
    """).fetchone()[0] or 0

    # ── Chart 8: Tool error rate by tool ──
    tool_errors_by_tool = []
    for row in cur.execute("""
        SELECT tool_name, SUM(is_error) as errors, COUNT(*) as total
        FROM turn_tool_calls tc
        JOIN session_metrics sm ON tc.session_id = sm.session_id AND sm.is_sidechain = 0
        GROUP BY tool_name
        HAVING errors > 0
        ORDER BY errors DESC LIMIT 10
    """):
        tool_errors_by_tool.append({
            "tool": row[0], "errors": row[1], "total": row[2],
            "rate": round(row[1] / row[2], 4) if row[2] else 0,
        })

    # ── Chart 9: Redundant read hotspots (computed at query time) ──
    redundant_reads = []
    for row in cur.execute("""
        SELECT tc.session_id, tc.file_path, COUNT(*) as cnt
        FROM turn_tool_calls tc
        JOIN session_metrics sm ON tc.session_id = sm.session_id AND sm.is_sidechain = 0
        WHERE tc.tool_name = 'Read' AND tc.file_path IS NOT NULL
        GROUP BY tc.session_id, tc.file_path
        HAVING cnt > 2
        ORDER BY cnt DESC LIMIT 20
    """):
        redundant_reads.append({
            "session_id": row[0][:8], "file": row[1].rsplit("/", 1)[-1] if row[1] else "?",
            "count": row[2],
        })
    # TRUE total across all files (not capped by chart LIMIT)
    total_redundant_reads = cur.execute("""
        SELECT SUM(cnt - 1) FROM (
            SELECT COUNT(*) as cnt
            FROM turn_tool_calls tc
            JOIN session_metrics sm ON tc.session_id = sm.session_id AND sm.is_sidechain = 0
            WHERE tc.tool_name = 'Read' AND tc.file_path IS NOT NULL
            GROUP BY tc.session_id, tc.file_path
            HAVING cnt > 2
        )
    """).fetchone()[0] or 0

    # ── Chart 10: Edit retry chains by project ──
    # Consecutive Edit/Write to same file where prior had is_error=1
    # Join through turns table using turn_index to avoid auto-increment ID gaps
    edit_retries = []
    for row in cur.execute("""
        SELECT sm.project_path, COUNT(*) as retries
        FROM turn_tool_calls tc1
        JOIN turns t1 ON tc1.turn_id = t1.id
        JOIN turns t2 ON t1.session_id = t2.session_id AND t2.turn_index = t1.turn_index + 1
        JOIN turn_tool_calls tc2 ON tc2.turn_id = t2.id
            AND tc2.file_path = tc1.file_path
            AND tc1.tool_name IN ('Edit', 'Write')
            AND tc2.tool_name IN ('Edit', 'Write')
            AND tc1.is_error = 1
        JOIN session_metrics sm ON tc1.session_id = sm.session_id AND sm.is_sidechain = 0
        GROUP BY sm.project_path
        HAVING retries > 0
        ORDER BY retries DESC LIMIT 10
    """):
        edit_retries.append({"project": _project_slug(row[0]), "retries": row[1]})
    # TRUE total across all projects (not capped by chart LIMIT)
    total_edit_retries = cur.execute("""
        SELECT COUNT(*)
        FROM turn_tool_calls tc1
        JOIN turns t1 ON tc1.turn_id = t1.id
        JOIN turns t2 ON t1.session_id = t2.session_id AND t2.turn_index = t1.turn_index + 1
        JOIN turn_tool_calls tc2 ON tc2.turn_id = t2.id
            AND tc2.file_path = tc1.file_path
            AND tc1.tool_name IN ('Edit', 'Write')
            AND tc2.tool_name IN ('Edit', 'Write')
            AND tc1.is_error = 1
        JOIN session_metrics sm ON tc1.session_id = sm.session_id AND sm.is_sidechain = 0
    """).fetchone()[0] or 0

    # ── Chart 11: Agent cost attribution ──
    agent_cost = []
    for row in cur.execute("""
        SELECT parent.project_path,
               SUM(CASE WHEN child.is_sidechain = 0 THEN child.total_input_tokens + child.total_cache_creation ELSE 0 END) as parent_cost,
               SUM(CASE WHEN child.is_sidechain = 1 THEN child.total_input_tokens + child.total_cache_creation ELSE 0 END) as agent_cost
        FROM session_metrics parent
        JOIN session_metrics child ON child.parent_session_id = parent.session_id OR child.session_id = parent.session_id
        WHERE parent.is_sidechain = 0 AND parent.uses_agent = 1
        GROUP BY parent.project_path
        ORDER BY agent_cost DESC LIMIT 10
    """):
        agent_cost.append({
            "project": _project_slug(row[0]),
            "parent_cost": row[1], "agent_cost": row[2],
        })

    # ── Chart 12: Turn complexity distribution ──
    turn_complexity = {"minimal": 0, "light": 0, "medium": 0, "heavy": 0, "runaway": 0}
    thinking_sum_complexity = {"minimal": 0, "light": 0, "medium": 0, "heavy": 0, "runaway": 0}
    for row in cur.execute("""
        SELECT t.output_tokens, t.thinking_tokens
        FROM turns t
        JOIN session_metrics sm ON t.session_id = sm.session_id AND sm.is_sidechain = 0
    """):
        out, think = row[0] or 0, row[1] or 0
        if out < 100:
            bucket = "minimal"
        elif out < 500:
            bucket = "light"
        elif out < 2000:
            bucket = "medium"
        elif out < 8000:
            bucket = "heavy"
        else:
            bucket = "runaway"
        turn_complexity[bucket] += 1
        thinking_sum_complexity[bucket] += think
    # Average thinking tokens per turn per bucket (avoids scale mismatch with turn counts)
    thinking_in_complexity = {
        k: round(thinking_sum_complexity[k] / turn_complexity[k])
        if turn_complexity[k] > 0 else 0
        for k in turn_complexity
    }

    # ── Chart 13: User response time distribution ──
    response_time_dist = {"under_30s": 0, "30s_2m": 0, "2m_5m": 0, "5m_15m": 0, "over_15m": 0}
    for row in cur.execute("""
        SELECT user_gap_ms FROM turns t
        JOIN session_metrics sm ON t.session_id = sm.session_id AND sm.is_sidechain = 0
        WHERE user_gap_ms IS NOT NULL AND user_gap_ms > 0
    """):
        gap_s = (row[0] or 0) / 1000
        if gap_s < 30:
            response_time_dist["under_30s"] += 1
        elif gap_s < 120:
            response_time_dist["30s_2m"] += 1
        elif gap_s < 300:
            response_time_dist["2m_5m"] += 1
        elif gap_s < 900:
            response_time_dist["5m_15m"] += 1
        else:
            response_time_dist["over_15m"] += 1

    # ── Chart 14: Hook overhead top 10 ──
    hook_overhead = []
    for row in cur.execute("""
        SELECT project_path, SUM(total_hook_ms) as hook_ms, COUNT(*) as sessions
        FROM session_metrics WHERE is_sidechain = 0 AND total_hook_ms > 0
        GROUP BY project_path
        ORDER BY hook_ms DESC LIMIT 10
    """):
        hook_overhead.append({
            "project": _project_slug(row[0]),
            "hook_ms": row[1], "sessions": row[2],
            "avg_hook_ms": round(row[1] / row[2]) if row[2] else 0,
        })

    # ── Chart 15: Per-project token spend ──
    project_spend = []
    for row in cur.execute("""
        SELECT project_path,
               SUM(total_input_tokens) as inp,
               SUM(total_output_tokens) as out,
               SUM(total_cache_creation) as cc,
               SUM(total_cache_read) as cr,
               COUNT(*) as sessions
        FROM session_metrics WHERE is_sidechain = 0
        GROUP BY project_path
        ORDER BY inp + cc DESC LIMIT 10
    """):
        project_spend.append({
            "project": _project_slug(row[0]),
            "input_tokens": row[1], "output_tokens": row[2],
            "cache_creation": row[3], "cache_read": row[4],
            "sessions": row[5],
        })

    # ── Chart 16: Per-project tool profile ──
    project_tool_profile = []
    top5_projects = [p["project"] for p in project_spend[:5]]
    if top5_projects:
        # Get full paths for top 5
        project_paths = {}
        for row in cur.execute("""
            SELECT project_path, SUM(total_input_tokens + total_cache_creation) as cost
            FROM session_metrics WHERE is_sidechain = 0
            GROUP BY project_path ORDER BY cost DESC LIMIT 5
        """):
            project_paths[_project_slug(row[0])] = row[0]

        for proj_slug, proj_path in project_paths.items():
            tools = {}
            for row in cur.execute("""
                SELECT tc.tool_name, COUNT(*)
                FROM turn_tool_calls tc
                JOIN session_metrics sm ON tc.session_id = sm.session_id
                WHERE sm.project_path = ? AND sm.is_sidechain = 0
                GROUP BY tc.tool_name
                ORDER BY COUNT(*) DESC LIMIT 8
            """, (proj_path,)):
                tools[row[0]] = row[1]
            project_tool_profile.append({"project": proj_slug, "tools": tools})

    # ── Chart 17: Skill usage ──
    skill_usage = []
    for row in cur.execute("""
        SELECT tc.skill_name, COUNT(*) as cnt,
               SUM(tc.is_error) as errs
        FROM turn_tool_calls tc
        JOIN session_metrics sm ON tc.session_id = sm.session_id AND sm.is_sidechain = 0
        WHERE tc.skill_name IS NOT NULL
        GROUP BY tc.skill_name
        ORDER BY cnt DESC
    """):
        skill_usage.append({
            "skill": row[0], "count": row[1], "errors": row[2],
        })

    # Skill usage by day (trend)
    skill_usage_by_day = []
    for row in cur.execute("""
        SELECT DATE(t.timestamp) as day, tc.skill_name, COUNT(*) as cnt
        FROM turn_tool_calls tc
        JOIN turns t ON tc.turn_id = t.id
        JOIN session_metrics sm ON tc.session_id = sm.session_id AND sm.is_sidechain = 0
        WHERE tc.skill_name IS NOT NULL
        GROUP BY day, tc.skill_name
        ORDER BY day
    """):
        skill_usage_by_day.append({
            "date": row[0], "skill": row[1], "count": row[2],
        })

    # ── Chart 18: Agent delegation ──
    agent_delegation = []
    for row in cur.execute("""
        SELECT tc.subagent_type, COUNT(*) as cnt,
               SUM(tc.is_error) as errs
        FROM turn_tool_calls tc
        JOIN session_metrics sm ON tc.session_id = sm.session_id AND sm.is_sidechain = 0
        WHERE tc.subagent_type IS NOT NULL
        GROUP BY tc.subagent_type
        ORDER BY cnt DESC
    """):
        agent_delegation.append({
            "subagent_type": row[0], "count": row[1], "errors": row[2],
        })

    # Agent model distribution
    agent_model_dist = []
    for row in cur.execute("""
        SELECT tc.agent_model, COUNT(*) as cnt
        FROM turn_tool_calls tc
        JOIN session_metrics sm ON tc.session_id = sm.session_id AND sm.is_sidechain = 0
        WHERE tc.agent_model IS NOT NULL
        GROUP BY tc.agent_model
        ORDER BY cnt DESC
    """):
        agent_model_dist.append({
            "model": row[0], "count": row[1],
        })

    # ── Chart 19: Hook performance ──
    hook_performance = []
    for row in cur.execute("""
        SELECT he.hook_command, COUNT(*) as runs,
               SUM(he.duration_ms) as total_ms,
               ROUND(AVG(he.duration_ms)) as avg_ms,
               SUM(he.is_error) as errs
        FROM hook_executions he
        JOIN session_metrics sm ON he.session_id = sm.session_id AND sm.is_sidechain = 0
        GROUP BY he.hook_command
        ORDER BY total_ms DESC
    """):
        hook_performance.append({
            "hook_command": row[0], "runs": row[1],
            "total_ms": row[2], "avg_ms": row[3], "errors": row[4],
        })

    # ── Root-cause detail queries (for insights engine) ──

    # Cache cliffs by project
    cache_cliff_projects = []
    for row in cur.execute("""
        SELECT project_path, SUM(cache_cliff_count) as cliffs, COUNT(*) as sessions
        FROM session_metrics WHERE is_sidechain = 0 AND cache_cliff_count > 0
        GROUP BY project_path ORDER BY cliffs DESC LIMIT 5
    """):
        cache_cliff_projects.append({
            "project": _project_slug(row[0]), "cliffs": row[1], "sessions": row[2],
        })

    # Top antipattern commands (actual command prefixes)
    top_bash_cmds = []
    for row in cur.execute("""
        SELECT SUBSTR(tc.command, 1, 60) as cmd, COUNT(*) as cnt
        FROM turn_tool_calls tc
        JOIN session_metrics sm ON tc.session_id = sm.session_id AND sm.is_sidechain = 0
        WHERE tc.tool_name = 'Bash' AND (
            tc.command LIKE 'cat %' OR tc.command LIKE 'head %' OR
            tc.command LIKE 'tail %' OR tc.command LIKE 'grep %' OR
            tc.command LIKE 'find %' OR tc.command LIKE 'ls %'
        ) AND tc.command NOT LIKE 'cat <<%'
        GROUP BY cmd ORDER BY cnt DESC LIMIT 5
    """):
        top_bash_cmds.append({"command": row[0], "count": row[1]})

    # Compute weighted average cost rates for waste-to-dollar conversion
    total_all_input = total_input + total_cache_read + total_cache_creation
    avg_input_cpm = 5.0  # default to Opus 4.6 rate
    avg_output_cpm = 25.0
    if model_split:
        weighted_in = sum(
            m["input_tokens"] * _get_pricing(m["model"])["input"] for m in model_split
        )
        weighted_out = sum(
            m["output_tokens"] * _get_pricing(m["model"])["output"] for m in model_split
        )
        total_in = sum(m["input_tokens"] for m in model_split) or 1
        total_out = sum(m["output_tokens"] for m in model_split) or 1
        avg_input_cpm = weighted_in / total_in
        avg_output_cpm = weighted_out / total_out

    # ── Insights engine (unified findings + recommendations) ──
    insights = _build_insights(
        total_output=total_output, total_input=total_input,
        cache_cliffs=total_cache_cliffs, max_token_stops=total_max_token_stops,
        bash_antipatterns=total_bash_antipatterns,
        redundant_reads=total_redundant_reads, edit_retries=total_edit_retries,
        total_thinking=total_thinking, total_tool_errors=total_tool_errors,
        global_cache_ratio=global_cache_ratio, total_sessions=total_sessions,
        response_time_dist=response_time_dist,
        bash_antipattern_projects=bash_antipatterns[:3],
        top_bash_antipattern_cmds=top_bash_cmds,
        cache_cliff_projects=cache_cliff_projects,
        top_redundant_files=redundant_reads[:3],
        edit_retry_projects=edit_retries[:3],
        cost_by_project=cost_by_project_list,
        total_cost_usd=total_cost_usd,
        avg_input_cost_per_mtok=avg_input_cpm,
        avg_output_cost_per_mtok=avg_output_cpm,
    )

    # Dashboard backward compat: split insights into findings + recommendations
    findings = _insights_to_findings(insights)
    recommendations = _insights_to_recommendations(insights)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_sessions": total_sessions,
        "date_range": {"earliest": dr[0][:10] if dr and dr[0] else None,
                       "latest": dr[1][:10] if dr and dr[1] else None},
        "kpis": {
            "total_sessions": total_sessions,
            "total_turns": total_turns,
            "total_output_tokens": total_output,
            "global_cache_ratio": global_cache_ratio,
            "cache_cliffs": total_cache_cliffs,
            "max_token_stops": total_max_token_stops,
            "bash_antipatterns": total_bash_antipatterns,
            "tool_error_rate": round(total_tool_errors / total_tool_calls, 4) if total_tool_calls else 0,
            "total_cost_usd": round(total_cost_usd, 2),
        },
        "sessions_by_day": sessions_by_day,
        "top_tools": top_tools,
        "model_split": model_split,
        "cost_by_day": cost_by_day_list,
        "cost_by_project": cost_by_project_list,
        "cache_trajectory": cache_trajectory,
        "ephem_split": ephem_split,
        "bash_antipatterns": bash_antipatterns,
        "tool_errors_by_tool": tool_errors_by_tool,
        "redundant_reads": redundant_reads,
        "edit_retries": edit_retries,
        "agent_cost": agent_cost,
        "turn_complexity": turn_complexity,
        "thinking_in_complexity": thinking_in_complexity,
        "response_time_dist": response_time_dist,
        "hook_overhead": hook_overhead,
        "project_spend": project_spend,
        "project_tool_profile": project_tool_profile,
        "skill_usage": skill_usage,
        "skill_usage_by_day": skill_usage_by_day,
        "agent_delegation": agent_delegation,
        "agent_model_dist": agent_model_dist,
        "hook_performance": hook_performance,
        "insights": insights,
        "findings": findings,
        "recommendations": recommendations,
    }


# ── Insights Engine (findings + root causes + solutions) ──────────────

def _build_insights(**kw) -> list[dict]:
    """Build unified insights: finding + root cause + solution + dollar cost.

    Each insight is a complete diagnosis-to-action unit. Severity is
    rate-normalized (per-session) not absolute count.
    """
    insights = []
    sessions = kw["total_sessions"] or 1
    # Average pricing for waste-to-dollar conversion (weighted by model mix)
    avg_input_rate = kw.get("avg_input_cost_per_mtok", 5.0)
    avg_output_rate = kw.get("avg_output_cost_per_mtok", 25.0)

    def _waste_usd(tokens: int, is_output: bool = False) -> float:
        rate = avg_output_rate if is_output else avg_input_rate
        return round(tokens * rate / 1_000_000, 2)

    def _severity(count: int, high_rate: float, crit_rate: float) -> str:
        rate = count / sessions
        if rate >= crit_rate:
            return "CRITICAL"
        if rate >= high_rate:
            return "WARNING"
        return "INFO"

    # ── Cache Cliffs ──
    if kw["cache_cliffs"] > 0:
        waste_tok = kw["cache_cliffs"] * 15000
        waste_dollars = _waste_usd(waste_tok)
        # Root cause: which projects have the most cliffs
        cliff_projects = kw.get("cache_cliff_projects", [])
        top_proj = cliff_projects[0] if cliff_projects else None
        root_cause = (
            f"Worst: {top_proj['project']} ({top_proj['cliffs']} cliffs across {top_proj['sessions']} sessions)"
            if top_proj else "Distributed across projects"
        )
        insights.append({
            "title": "Cache Cliffs",
            "severity": _severity(kw["cache_cliffs"], 0.1, 0.4),
            "finding": f"{kw['cache_cliffs']} cache cliffs detected — cache_read_ratio dropped >50% "
                       f"after 5min+ idle gaps.",
            "root_cause": f"When you're idle >5min, Anthropic's prompt cache expires. The next turn "
                          f"re-creates the entire cache from scratch. {root_cause}.",
            "waste_tokens": waste_tok,
            "waste_usd": waste_dollars,
            "solution": {
                "action": "Run /compact before stepping away from a session",
                "detail": "Compacting reduces context size so cache re-creation is cheaper when you return. "
                          "For planned breaks, also consider ending the session and starting fresh.",
                "claudemd_rule": None,
                "estimated_savings_usd": round(waste_dollars * 0.6, 2),
            },
        })

    # ── Context Pressure (max_tokens) ──
    if kw["max_token_stops"] > 0:
        waste_tok = kw["max_token_stops"] * 5000
        waste_dollars = _waste_usd(waste_tok, is_output=True)
        insights.append({
            "title": "Context Pressure",
            "severity": _severity(kw["max_token_stops"], 0.02, 0.1),
            "finding": f"{kw['max_token_stops']} turns hit max_tokens — model was cut off mid-response.",
            "root_cause": "The conversation context exceeded the model's output budget. This typically "
                          "happens in long sessions with many tool calls, large file reads, or when "
                          "CLAUDE.md/hooks inject significant context every turn.",
            "waste_tokens": waste_tok,
            "waste_usd": waste_dollars,
            "solution": {
                "action": "Run /compact proactively when sessions exceed ~40 turns, or split into smaller sessions",
                "detail": "Monitor turn count. If you're doing a large refactor, break it into focused sessions "
                          "(one per file/module) rather than one marathon session.",
                "claudemd_rule": None,
                "estimated_savings_usd": round(waste_dollars * 0.8, 2),
            },
        })

    # ── Bash Antipatterns ──
    if kw["bash_antipatterns"] > 0:
        waste_tok = kw["bash_antipatterns"] * 200
        waste_dollars = _waste_usd(waste_tok, is_output=True)
        # Root cause: per-project breakdown and top commands
        bash_projects = kw.get("bash_antipattern_projects", [])
        top_cmds = kw.get("top_bash_antipattern_cmds", [])

        proj_detail = "; ".join(
            f"{b['project']}: {b['antipatterns']}" for b in bash_projects[:3]
        ) if bash_projects else "unknown"

        cmd_detail = ", ".join(
            f"`{c['command'][:50]}` ({c['count']}x)" for c in top_cmds[:3]
        ) if top_cmds else "various cat/grep/find/ls calls"

        tool_map = {
            "cat": "Read", "head": "Read (with offset+limit)", "tail": "Read (with offset+limit)",
            "grep": "Grep", "find": "Glob", "ls": "Glob or Bash(ls)",
        }
        suggested_rules = []
        for cmd in top_cmds[:3]:
            prefix = cmd["command"].split()[0] if cmd.get("command") else ""
            replacement = tool_map.get(prefix)
            if replacement:
                suggested_rules.append(f"Use {replacement} instead of `{prefix}` command")

        insights.append({
            "title": "Bash Antipatterns",
            "severity": _severity(kw["bash_antipatterns"], 0.5, 2.0),
            "finding": f"{kw['bash_antipatterns']} Bash calls use cat/grep/find/ls instead of dedicated tools.",
            "root_cause": f"Claude is choosing Bash over dedicated tools. Top projects: {proj_detail}. "
                          f"Top commands: {cmd_detail}.",
            "waste_tokens": waste_tok,
            "waste_usd": waste_dollars,
            "solution": {
                "action": "Add CLAUDE.md rules to enforce tool usage",
                "detail": "Dedicated tools (Read, Grep, Glob) return structured output that uses fewer "
                          "tokens than raw shell output. Each antipattern wastes ~200 output tokens on "
                          "verbose formatting.",
                "claudemd_rule": "\n".join(suggested_rules) if suggested_rules else
                                 "Use Read instead of cat/head/tail. Use Grep instead of grep. Use Glob instead of find/ls.",
                "estimated_savings_usd": round(waste_dollars * 0.7, 2),
            },
        })

    # ── Redundant Reads ──
    if kw["redundant_reads"] > 0:
        waste_tok = kw["redundant_reads"] * 500
        waste_dollars = _waste_usd(waste_tok)
        top_files = kw.get("top_redundant_files", [])
        file_detail = "; ".join(
            f"`{f['file']}` read {f['count']}x in session {f['session_id']}"
            for f in top_files[:3]
        ) if top_files else "various files"

        insights.append({
            "title": "Redundant Reads",
            "severity": _severity(kw["redundant_reads"], 0.3, 1.0),
            "finding": f"{kw['redundant_reads']} extra file reads (same file read 3+ times in a session).",
            "root_cause": f"Claude is re-reading files it already has in context. This happens when earlier "
                          f"context gets compressed away or when Claude doesn't trust its cached knowledge. "
                          f"Worst offenders: {file_detail}.",
            "waste_tokens": waste_tok,
            "waste_usd": waste_dollars,
            "solution": {
                "action": "Add a CLAUDE.md rule: 'After reading a file, reference it from context — do not re-read unless the file was modified since last read'",
                "detail": "Each redundant read re-ingests the file as input tokens. For large files (1K+ lines) "
                          "this adds significant cost. The Read tool output note already says 'content unchanged since last read' but Claude sometimes ignores it.",
                "claudemd_rule": "After reading a file, reference it from context. Only re-read if the file was modified since last read.",
                "estimated_savings_usd": round(waste_dollars * 0.5, 2),
            },
        })

    # ── Edit Retry Chains ──
    if kw["edit_retries"] > 0:
        waste_tok = kw["edit_retries"] * 300
        waste_dollars = _waste_usd(waste_tok, is_output=True)
        retry_projects = kw.get("edit_retry_projects", [])
        proj_detail = "; ".join(
            f"{r['project']}: {r['retries']}x" for r in retry_projects[:3]
        ) if retry_projects else "various projects"

        insights.append({
            "title": "Edit Retry Chains",
            "severity": _severity(kw["edit_retries"], 0.2, 0.5),
            "finding": f"{kw['edit_retries']} failed-edit retry chains detected.",
            "root_cause": f"An Edit call fails (usually unique-match failure), then Claude retries on the same "
                          f"file next turn. The failure means Claude's mental model of the file diverged from "
                          f"reality — typically after a prior edit changed the file. Projects: {proj_detail}.",
            "waste_tokens": waste_tok,
            "waste_usd": waste_dollars,
            "solution": {
                "action": "Add a CLAUDE.md rule: 'Always read a file before editing if more than 2 turns have passed since last read'",
                "detail": "The root cause is stale context. Claude edits based on what it remembers, not "
                          "the current file state. A fresh read before each edit is cheap (~500 input tokens) "
                          "compared to the cost of a failed edit + retry (~300 output tokens wasted).",
                "claudemd_rule": "Read file before editing if >2 turns since last read, to avoid stale-context edit failures.",
                "estimated_savings_usd": round(waste_dollars * 0.7, 2),
            },
        })

    # ── Thinking Token Overhead ──
    if kw["total_thinking"] > 0:
        pct = round(kw["total_thinking"] / kw["total_output"] * 100, 1) if kw["total_output"] else 0
        thinking_dollars = _waste_usd(kw["total_thinking"], is_output=True)
        insights.append({
            "title": "Thinking Token Overhead",
            "severity": "INFO",
            "finding": f"~{pct}% of output tokens went to extended thinking ({kw['total_thinking'] // 1000}K tokens, "
                       f"~${thinking_dollars}).",
            "root_cause": "Extended thinking is Opus's reasoning mode — it produces internal chain-of-thought "
                          "tokens that are billed as output but not visible to you. This is expected for complex "
                          "tasks but can be excessive for simple ones.",
            "waste_tokens": 0,
            "waste_usd": 0,
            "solution": {
                "action": "Use Sonnet for routine tasks (file reads, simple edits, git operations) and reserve Opus for complex reasoning",
                "detail": f"Thinking tokens cost ${thinking_dollars} at output rates. If you're using Opus for "
                          f"everything, switching routine tasks to Sonnet (which doesn't use extended thinking) "
                          f"could save 50-70% of this cost.",
                "claudemd_rule": None,
                "estimated_savings_usd": round(thinking_dollars * 0.3, 2),
            },
        })

    # ── Idle Gap Impact ──
    idle_over_5m = kw["response_time_dist"].get("5m_15m", 0) + kw["response_time_dist"].get("over_15m", 0)
    if idle_over_5m > 0:
        total_gaps = sum(kw["response_time_dist"].values())
        pct = round(idle_over_5m / total_gaps * 100, 1) if total_gaps else 0
        waste_tok = idle_over_5m * 2000
        waste_dollars = _waste_usd(waste_tok)
        insights.append({
            "title": "Idle Gap Impact",
            "severity": _severity(idle_over_5m, 0.3, 1.0),
            "finding": f"{pct}% of turns follow 5min+ idle gaps ({idle_over_5m} of {total_gaps}).",
            "root_cause": "Anthropic's prompt cache has a 5-minute TTL. After 5 minutes of inactivity, "
                          "the entire cached context expires and must be re-created from scratch on the "
                          "next turn. This is the biggest hidden cost driver in interactive sessions.",
            "waste_tokens": waste_tok,
            "waste_usd": waste_dollars,
            "solution": {
                "action": "Batch your prompts — compose your full request before sending rather than sending partial messages",
                "detail": "If you routinely step away for 5+ minutes between prompts, consider: (1) using /compact "
                          "before breaks to reduce re-creation cost, (2) ending sessions before long breaks and "
                          "starting fresh, (3) using the 1-hour cache tier for system prompts if your API setup supports it.",
                "claudemd_rule": None,
                "estimated_savings_usd": round(waste_dollars * 0.4, 2),
            },
        })

    # ── Cost Concentration ──
    cost_by_project = kw.get("cost_by_project", [])
    total_cost = kw.get("total_cost_usd", 0)
    if cost_by_project and total_cost > 0:
        top = cost_by_project[0]
        top_pct = round(top["cost_usd"] / total_cost * 100, 1)
        if top_pct > 40:
            insights.append({
                "title": "Cost Concentration",
                "severity": "INFO",
                "finding": f"{top['project']} accounts for {top_pct}% of total spend (${top['cost_usd']:.2f} of ${total_cost:.2f}).",
                "root_cause": f"This project dominates your usage. Either it has the most sessions, uses "
                              f"the most expensive model, or both. Review whether all work in this project "
                              f"requires the current model tier.",
                "waste_tokens": 0,
                "waste_usd": 0,
                "solution": {
                    "action": f"Audit {top['project']} sessions — could routine tasks use Sonnet instead of Opus?",
                    "detail": f"Switching from Opus ($5/$25 per MTok) to Sonnet ($3/$15 per MTok) for 50% of "
                              f"{top['project']} work would save ~${top['cost_usd'] * 0.2:.2f}.",
                    "claudemd_rule": None,
                    "estimated_savings_usd": round(top["cost_usd"] * 0.2, 2),
                },
            })

    # Sort by waste (dollar) descending, with zero-waste items last
    insights.sort(key=lambda i: (i["waste_usd"] > 0, i["waste_usd"]), reverse=True)

    # Assign priority based on sorted position
    for i, ins in enumerate(insights):
        if i < 2:
            ins["priority"] = "P0"
        elif i < 5:
            ins["priority"] = "P1"
        else:
            ins["priority"] = "P2"

    return insights


# Legacy compatibility: dashboard still expects separate findings/recommendations
def _insights_to_findings(insights: list[dict]) -> list[dict]:
    return [
        {"title": i["title"], "severity": i["severity"],
         "text": f"{i['finding']} {i['root_cause']}", "waste": i["waste_tokens"]}
        for i in insights
    ]


def _insights_to_recommendations(insights: list[dict]) -> list[dict]:
    return [
        {"text": f"{i['solution']['action']}. {i['solution']['detail']}",
         "impact": i["waste_tokens"], "priority": i["priority"]}
        for i in insights if i.get("solution")
    ]


# ── Dashboard Deploy ──────────────────────────────────────────────────

def deploy_dashboard(json_str: str) -> None:
    try:
        html = DASHBOARD_TEMPLATE_PATH.read_text()
        html = html.replace(
            "/* __INLINE_DATA_PLACEHOLDER__ */",
            f"const _INLINE_DATA = {json_str};",
            1,
        )
        DASHBOARD_OUT_PATH.write_text(html)
    except Exception as e:
        print(f"Warning: could not deploy dashboard: {e}", file=sys.stderr)


# ── Main ──────────────────────────────────────────────────────────────

def main() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA foreign_keys = ON")

    ensure_schema(conn)

    # Discover files
    files = discover_jsonl_files()
    print(f"Discovered {len(files)} JSONL files", file=sys.stderr)

    # Filter to files needing import
    to_import = [f for f in files if not should_skip_file(conn, f.path)]
    print(f"Files to import: {len(to_import)} (skipping {len(files) - len(to_import)} unchanged)", file=sys.stderr)

    # Parse and import
    imported = 0
    errors = 0
    for i, jnl in enumerate(to_import):
        if i > 0 and i % PROGRESS_INTERVAL == 0:
            print(f"  Parsing {i}/{len(to_import)} files...", file=sys.stderr)
        try:
            session = parse_session(jnl.path, jnl)
            if session:
                import_session(conn, session, jnl)
                record_import(conn, jnl.path, session.session_id, len(session.turns))
                imported += 1
        except Exception as e:
            errors += 1
            if errors <= 5:
                print(f"  Error parsing {jnl.path.name}: {e}", file=sys.stderr)

        if (i + 1) % BATCH_SIZE == 0:
            conn.commit()

    conn.commit()
    print(f"Imported {imported} sessions ({errors} errors)", file=sys.stderr)

    # Backfill token_snapshots
    print("Backfilling token_snapshots...", file=sys.stderr)
    backfill_token_snapshots(conn)

    # Run ANALYZE for query planner optimization
    conn.execute("ANALYZE")
    conn.commit()

    # Build output
    output = build_output(conn)
    conn.close()

    # Full JSON for dashboard (all chart data)
    full_json = json.dumps(output, default=str)
    full_kb = len(full_json) / 1024
    print(f"Full JSON: {full_kb:.0f}KB", file=sys.stderr)

    deploy_dashboard(full_json)
    print(f"Dashboard deployed to {DASHBOARD_OUT_PATH}", file=sys.stderr)

    # Slim JSON for stdout (only what Claude needs for analysis)
    slim_keys = {
        "generated_at", "total_sessions", "date_range", "kpis", "insights",
        "cost_by_project", "model_split",
        "skill_usage", "agent_delegation", "hook_performance",
    }
    slim = {k: v for k, v in output.items() if k in slim_keys}
    slim_json = json.dumps(slim, default=str)
    slim_kb = len(slim_json) / 1024
    print(f"Slim stdout: {slim_kb:.0f}KB (full: {full_kb:.0f}KB)", file=sys.stderr)
    print(slim_json)


if __name__ == "__main__":
    main()
