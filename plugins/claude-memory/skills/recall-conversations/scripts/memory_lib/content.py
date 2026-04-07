#!/usr/bin/env python3
"""
Message content extraction and tool detection utilities.
"""

from __future__ import annotations

import json
import re


def sanitize_fts_term(term: str) -> str:
    """Remove FTS special characters from search term.

    Strips characters that are FTS operators or special syntax:
    quotes, parentheses, asterisks, and FTS keywords.
    Keeps alphanumeric, spaces, and basic punctuation.
    """
    # Remove quotes, parentheses, asterisks, and word boundaries
    sanitized = re.sub(r'["\(\)*\-^]', '', term)
    # Remove FTS keywords: NEAR, AND, OR, NOT (case-insensitive)
    sanitized = re.sub(r'\b(NEAR|AND|OR|NOT)\b', '', sanitized, flags=re.IGNORECASE)
    # Strip whitespace
    sanitized = sanitized.strip()
    return sanitized


def extract_text_content(content) -> tuple[str, bool, bool, str | None]:
    """
    Extract text from message content.
    Returns: (text, has_tool_use, has_thinking, tool_summary_json)

    tool_summary_json is a JSON string like '{"Bash":3,"Read":2}' or None.
    Tool use markers are NOT materialized into text.
    """
    has_tool_use = False
    has_thinking = False
    tool_counts: dict[str, int] = {}

    if isinstance(content, str):
        # Clean up command artifacts
        text = re.sub(r'<command-name>.*?</command-name>', '', content, flags=re.DOTALL)
        text = re.sub(r'<command-message>.*?</command-message>', '', text, flags=re.DOTALL)
        text = re.sub(r'<command-args>.*?</command-args>', '', text, flags=re.DOTALL)
        text = re.sub(r'<local-command-stdout>.*?</local-command-stdout>', '', text, flags=re.DOTALL)
        text = re.sub(r'<channel\b[^>]*>\n?([\s\S]*?)\n?</channel>', r'\1', text, flags=re.DOTALL)
        return text.strip(), False, False, None

    if isinstance(content, list):
        texts = []
        for item in content:
            if isinstance(item, dict):
                item_type = item.get("type", "")
                if item_type == "text":
                    texts.append(item.get("text", ""))
                elif item_type == "tool_use":
                    has_tool_use = True
                    tool_name = item.get("name", "")
                    if tool_name:
                        tool_counts[tool_name] = tool_counts.get(tool_name, 0) + 1
                elif item_type == "thinking":
                    has_thinking = True
        tool_summary = json.dumps(tool_counts) if tool_counts else None
        return "\n".join(texts).strip(), has_tool_use, has_thinking, tool_summary

    return "", False, False, None


def parse_origin(entry: dict) -> str | None:
    """Extract clean platform name from origin.server (e.g. 'telegram' from 'plugin:telegram:telegram')."""
    origin = entry.get("origin")
    if not origin:
        return None
    server = origin.get("server", "")
    # Pattern: "plugin:telegram:telegram" -> "telegram"
    parts = server.split(":")
    if len(parts) >= 2 and parts[1]:
        return parts[1]
    return origin.get("kind")


def is_task_notification(content) -> bool:
    """Check if content is a task-notification message (subagent result)."""
    if isinstance(content, list):
        texts = [item.get("text", "") for item in content
                 if isinstance(item, dict) and item.get("type") == "text"]
        text = "\n".join(texts).strip()
    elif isinstance(content, str):
        text = content.strip()
    else:
        return False
    return text.startswith("<task-notification>")


def is_teammate_message(content) -> bool:
    """Detect teammate coordination messages (team reports, idle notifications, shutdown)."""
    if isinstance(content, list):
        texts = [item.get("text", "") for item in content
                 if isinstance(item, dict) and item.get("type") == "text"]
        text = "\n".join(texts).strip()
    elif isinstance(content, str):
        text = content.strip()
    else:
        return False
    return text.startswith("<teammate-message")


def is_tool_result(content) -> bool:
    """Check if content is a tool result (not a real user message)."""
    if isinstance(content, list) and content:
        first = content[0]
        if isinstance(first, dict) and first.get("type") == "tool_result":
            return True
    return False


def extract_files_modified(content) -> list[str]:
    """Extract file paths from Edit/Write/MultiEdit tool uses."""
    files = []
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "tool_use":
                name = item.get("name", "")
                inp = item.get("input", {})
                if name in ("Edit", "Write", "MultiEdit") and "file_path" in inp:
                    files.append(inp["file_path"])
    return files


def extract_commits(content) -> list[str]:
    """Extract git commit messages from Bash tool uses."""
    commits = []
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "tool_use":
                if item.get("name") == "Bash":
                    cmd = item.get("input", {}).get("command", "")
                    if "git commit" in cmd:
                        m = re.search(r'-m\s+["\']([^"\']+)["\']', cmd)
                        if m:
                            commits.append(m.group(1)[:100])
    return commits
