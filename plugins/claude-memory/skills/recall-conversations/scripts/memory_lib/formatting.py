#!/usr/bin/env python3
"""
Session formatting, time utilities, and project path helpers.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional


def format_time(ts_str: Optional[str], fmt: str = "%H:%M") -> str:
    """
    Format ISO timestamp to specified format.
    Default: HH:MM
    """
    if not ts_str:
        return "??:??"
    try:
        dt = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
        return dt.astimezone().strftime(fmt)
    except Exception:
        return ts_str[:16] if ts_str else "??:??"


def format_time_full(ts_str: Optional[str]) -> str:
    """Format ISO timestamp to YYYY-MM-DD HH:MM."""
    return format_time(ts_str, "%Y-%m-%d %H:%M")


_WORKTREE_MARKER = "/.claude/worktrees/"
_WORKTREE_KEY_MARKER = "--claude-worktrees-"


def normalize_cwd(cwd: str) -> str:
    """Strip .claude/worktrees/<name> suffix from a raw path, returning the base repo path."""
    idx = cwd.rfind(_WORKTREE_MARKER)
    return cwd[:idx] if idx != -1 else cwd


def get_project_key(cwd: str) -> str:
    """Convert working directory to project key format.

    Resolves .claude/worktrees/<name> paths to the base repo path
    so worktree sessions share project context with the main repo.
    """
    return normalize_cwd(cwd).replace("/", "-").replace(".", "-")


def normalize_project_key(key: str) -> str:
    """Strip worktree suffix from an already-encoded project key.

    Encoded worktree keys contain '--claude-worktrees-' (from /.claude/worktrees/).
    """
    idx = key.rfind(_WORKTREE_KEY_MARKER)
    return key[:idx] if idx != -1 else key


def parse_project_key(key: str) -> str:
    """Convert directory key back to original path (lossy — hyphens in dir names are lost).
    Prefer using session cwd metadata when available."""
    return "/" + key.replace("-", "/").lstrip("/")


def extract_project_name(path: str) -> str:
    """Extract short project name from path."""
    return Path(path).name


def format_markdown_session(session: dict, verbose: bool = False) -> str:
    """Format a single session as markdown."""
    lines = []

    started = format_time_full(session.get("started_at"))
    project = session.get("project", "Unknown")
    lines.append(f"## {project} | {started}")
    lines.append(f"Session: {session.get('uuid', 'unknown')[:8]}")

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

        tool_counts = session.get("tool_counts", {})
        if tool_counts:
            sorted_tools = sorted(tool_counts.items(), key=lambda x: x[1], reverse=True)
            tools_str = ", ".join(f"{name}: {count}" for name, count in sorted_tools)
            lines.append("\n### Tools Used")
            lines.append(tools_str)

    lines.append("\n### Conversation\n")

    for msg in session.get("messages", []):
        if msg.get("is_notification"):
            role = "Subagent Result"
        else:
            role = "User" if msg["role"] == "user" else "Assistant"
        lines.append(f"**{role}:** {msg['content']}\n")

    lines.append("---\n")
    return "\n".join(lines)


def format_json_sessions(sessions: list[dict], extra: Optional[dict] = None) -> str:
    """Format sessions as JSON with metadata."""
    total_messages = sum(len(s.get("messages", [])) for s in sessions)
    output = {
        "sessions": sessions,
        "total_sessions": len(sessions),
        "total_messages": total_messages
    }
    if extra:
        output.update(extra)
    return json.dumps(output, indent=2)
