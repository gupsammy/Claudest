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
    # Hyphens join words ("trade-off"); split them so the quoted term matches
    # the tokenizer's adjacent tokens instead of an invented "tradeoff".
    sanitized = term.replace('-', ' ')
    # Remove quotes, parentheses, asterisks, and word boundaries
    sanitized = re.sub(r'["\(\)*^]', '', sanitized)
    # Remove FTS keywords: NEAR, AND, OR, NOT (case-insensitive)
    sanitized = re.sub(r'\b(NEAR|AND|OR|NOT)\b', '', sanitized, flags=re.IGNORECASE)
    # Collapse the gaps left by removals and hyphen splits
    sanitized = " ".join(sanitized.split())
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
        # A slash command is the person's turn: keep "/name args" as they typed it.
        name = re.search(r'<command-name>(.*?)</command-name>', content, flags=re.DOTALL)
        args = re.search(r'<command-args>(.*?)</command-args>', content, flags=re.DOTALL)
        typed = " ".join(p.strip() for p in (name and name.group(1), args and args.group(1)) if p and p.strip())
        text = re.sub(r'<command-name>.*?</command-name>', '', content, flags=re.DOTALL)
        text = re.sub(r'<command-message>.*?</command-message>', '', text, flags=re.DOTALL)
        text = re.sub(r'<command-args>.*?</command-args>', '', text, flags=re.DOTALL)
        if typed:
            text = typed + "\n" + text
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
    if not origin or not isinstance(origin, dict):
        return None
    server = origin.get("server") or ""
    if not server:
        return None
    # Pattern: "plugin:telegram:telegram" -> "telegram"
    parts = server.split(":")
    if len(parts) >= 2 and parts[1]:
        return parts[1]
    return None


_SYSTEM_REMINDER_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)


def strip_system_reminders(text: str) -> str:
    """Remove harness-injected <system-reminder> blocks, leaving what the person typed."""
    return _SYSTEM_REMINDER_RE.sub("", text).strip()


def _user_text(content) -> str:
    """Joined text blocks of a user message, stripped; '' for anything else."""
    if isinstance(content, list):
        texts = [item.get("text", "") for item in content
                 if isinstance(item, dict) and item.get("type") == "text"]
        return "\n".join(texts).strip()
    if isinstance(content, str):
        return content.strip()
    return ""


def is_task_notification(content) -> bool:
    """Check if content is a task-notification message (subagent result)."""
    return _user_text(content).startswith("<task-notification>")


def is_teammate_message(content) -> bool:
    """Detect teammate coordination messages (team reports, idle notifications, shutdown)."""
    return _user_text(content).startswith("<teammate-message")


# User-role messages the harness writes on the person's behalf. Kept out of
# exchanges, topics and summaries because the person did not type them.
_HARNESS_PREFIXES = (
    "This session is being continued from a previous conversation",  # compaction summary
    "<artifact-content-authored-by-others/>",  # claude.ai summarized conversation
    "<ci-monitor-event>",  # desktop PR monitor
    "Another Claude session sent a message:",  # inter-session message
    "<agent-message",
)


def is_harness_message(content) -> bool:
    """Detect harness-written user-role messages (compaction, PR monitor, inter-session)."""
    return _user_text(content).startswith(_HARNESS_PREFIXES)


_COMMAND_ARGS_RE = re.compile(r"<command-args>(.*?)</command-args>", re.DOTALL)


def is_bare_command(content) -> bool:
    """A slash command with no arguments (/clear, /model, a bare skill call).

    Stored as "/name" so it stays searchable, but it is not a typed turn: it must
    not become a session topic or count as an exchange.
    """
    text = _user_text(content)
    if not text.startswith(("<command-name>", "<command-message>")):
        return False
    args = _COMMAND_ARGS_RE.search(text)
    return not (args and args.group(1).strip())


def is_notification_content(content) -> bool:
    """True for any user-role message the person did not type; stored as is_notification."""
    return (is_task_notification(content) or is_teammate_message(content)
            or is_harness_message(content) or is_bare_command(content))


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
