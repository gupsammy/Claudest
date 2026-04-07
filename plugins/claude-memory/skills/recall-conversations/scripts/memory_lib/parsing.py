#!/usr/bin/env python3
"""
JSONL parsing, branch detection, and metadata extraction.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Generator

from memory_lib.content import (
    extract_commits,
    extract_files_modified,
    is_task_notification,
    is_teammate_message,
    is_tool_result,
)


def parse_jsonl_file(filepath: Path) -> Generator[dict, None, None]:
    """Parse JSONL file, yielding user/assistant entries for import."""
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if obj.get("isMeta") and not obj.get("origin"):
                    continue
                if obj.get("type") in ("user", "assistant"):
                    yield obj
            except json.JSONDecodeError:
                pass


def parse_all_with_uuids(filepath: Path) -> Generator[dict, None, None]:
    """
    Parse JSONL file yielding ALL entries with UUIDs.
    Used for building the parentUuid chain to find branches.
    """
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if obj.get("uuid"):
                    yield obj
            except json.JSONDecodeError:
                pass


def extract_session_metadata(entries: list[dict]) -> dict:
    """Extract session metadata from entries."""
    metadata = {
        "started_at": None,
        "ended_at": None,
        "git_branch": None,
        "cwd": None,
    }

    for entry in entries:
        ts = entry.get("timestamp")
        if ts:
            if metadata["started_at"] is None or ts < metadata["started_at"]:
                metadata["started_at"] = ts
            if metadata["ended_at"] is None or ts > metadata["ended_at"]:
                metadata["ended_at"] = ts

        if not metadata["git_branch"]:
            metadata["git_branch"] = entry.get("gitBranch")
        if not metadata["cwd"]:
            metadata["cwd"] = entry.get("cwd")

    return metadata


def find_all_branches(all_entries: list[dict]) -> list[dict]:
    """
    Find all conversation branches (from rewinds).

    Returns list of branches, each with:
      - leaf_uuid: UUID of the last message in this branch
      - uuids: set of all UUIDs on this branch path
      - is_active: True if this is the current active branch
      - fork_point_uuid: UUID where this branch diverged (None for active)

    Algorithm:
    1. Find active branch (trace from latest message back to root)
    2. Find fork points on the active path where non-active children
       lead to subtrees with user messages (actual rewinds, not tree noise)
    3. For each rewind fork, collect the abandoned subtree + common prefix
    """
    uuid_to_entry: dict[str, dict] = {}
    uuid_to_parent: dict[str, str | None] = {}
    children: dict[str, list[str]] = {}

    for entry in all_entries:
        uuid = entry.get("uuid")
        if not uuid:
            continue
        uuid_to_entry[uuid] = entry
        parent = entry.get("parentUuid")
        uuid_to_parent[uuid] = parent
        if parent:
            children.setdefault(parent, []).append(uuid)

    if not uuid_to_entry:
        return []

    # Step 1: Find active branch (latest -> root)
    latest = max(uuid_to_entry.values(), key=lambda e: e.get("timestamp") or "")
    active_uuids: set[str] = set()
    current: str | None = latest["uuid"]
    while current:
        active_uuids.add(current)
        current = uuid_to_parent.get(current)

    branches: list[dict] = [
        {"leaf_uuid": latest["uuid"], "uuids": active_uuids, "is_active": True, "fork_point_uuid": None}
    ]

    # Step 2: Find rewind forks on the active path
    def has_user_descendant(uuid: str, depth: int = 0) -> bool:
        if depth > 100:
            return False
        entry = uuid_to_entry.get(uuid)
        if entry and entry.get("type") == "user":
            return True
        for kid in children.get(uuid, []):
            if has_user_descendant(kid, depth + 1):
                return True
        return False

    def collect_subtree(uuid: str) -> set[str]:
        result: set[str] = set()
        stack = [uuid]
        while stack:
            node = stack.pop()
            result.add(node)
            stack.extend(children.get(node, []))
        return result

    for uuid in active_uuids:
        kids = children.get(uuid, [])
        if len(kids) <= 1:
            continue

        for kid in kids:
            if kid in active_uuids:
                continue
            if not has_user_descendant(kid):
                continue

            # Real rewind fork — build the abandoned branch
            # Common prefix: fork point back to root
            prefix: set[str] = set()
            cur: str | None = uuid
            while cur:
                prefix.add(cur)
                cur = uuid_to_parent.get(cur)

            subtree = collect_subtree(kid)
            branch_uuids = prefix | subtree

            subtree_entries = [uuid_to_entry[u] for u in subtree if u in uuid_to_entry]
            if not subtree_entries:
                continue
            leaf = max(subtree_entries, key=lambda e: e.get("timestamp") or "")

            branches.append({
                "leaf_uuid": leaf["uuid"],
                "uuids": branch_uuids,
                "is_active": False,
                "fork_point_uuid": uuid,
            })

    return branches


def compute_branch_metadata(entries: list[dict]) -> tuple[int, list[str], list[str], dict[str, int]]:
    """
    Compute metadata for a branch's entries in one pass.
    Returns: (exchange_count, files_modified, commits, tool_counts)
    """
    exchange_count = 0
    all_files = []
    all_commits = []
    tool_counts: dict[str, int] = {}
    has_user = False

    for entry in entries:
        entry_type = entry.get("type")
        if entry_type not in ("user", "assistant"):
            continue

        message = entry.get("message", {})
        content = message.get("content", "")

        if entry_type == "user" and is_tool_result(content):
            continue

        if entry_type == "user" and (is_task_notification(content) or is_teammate_message(content)):
            continue

        if entry_type == "user":
            if has_user:
                exchange_count += 1
            has_user = True

        if entry_type == "assistant":
            all_files.extend(extract_files_modified(content))
            all_commits.extend(extract_commits(content))
            # Count tool usage from all assistant entries (including tool-only ones)
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "tool_use":
                        tool_name = item.get("name", "")
                        if tool_name:
                            tool_counts[tool_name] = tool_counts.get(tool_name, 0) + 1

    if has_user:
        exchange_count += 1

    # Deduplicate files preserving order
    seen = {}
    unique_files = []
    for f in all_files:
        if f not in seen:
            seen[f] = True
            unique_files.append(f)

    return exchange_count, unique_files, all_commits, tool_counts


def aggregate_branch_content(cursor: sqlite3.Cursor, branch_db_id: int) -> str:
    """Concatenate all message content for a branch in timestamp order, excluding notifications."""
    cursor.execute("""
        SELECT m.content FROM branch_messages bm
        JOIN messages m ON bm.message_id = m.id
        WHERE bm.branch_id = ? AND COALESCE(m.is_notification, 0) = 0
        ORDER BY m.timestamp ASC
    """, (branch_db_id,))
    return "\n".join(row[0] for row in cursor.fetchall())
