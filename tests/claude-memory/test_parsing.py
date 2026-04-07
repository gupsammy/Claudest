"""Tests for memory_lib.parsing — branch detection, JSONL parsing, metadata."""

import json
import uuid as uuid_mod
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from memory_lib.parsing import (
    compute_branch_metadata,
    extract_session_metadata,
    find_all_branches,
    parse_all_with_uuids,
    parse_jsonl_file,
)

# Verified expected values from real fixture analysis
EXPECTED = {
    "linear_3_exchange": {"branches": 1, "active_exchanges": 3},
    "tool_heavy": {"branches": 1, "active_exchanges": 2},
    "single_rewind": {"branches": 3, "active_exchanges": 5},
    "multi_rewind": {"branches": 4, "active_exchanges": 7},
    "with_notifications": {"branches": 1, "active_exchanges": 2},
    "with_teammate_messages": {"branches": 1, "active_exchanges": 2},
    "channel_telegram": {"branches": 1, "active_exchanges": 2},
}


# ── Hypothesis property tests for find_all_branches ──


def _build_uuid_tree(n_entries, fork_indices):
    """
    Build a synthetic UUID tree with controlled fork points.
    Returns list of entry dicts with uuid, parentUuid, type, timestamp.
    """
    entries = []
    uuids = [str(uuid_mod.uuid4()) for _ in range(n_entries)]
    roles = ["user", "assistant"]

    # Build a linear chain first
    for i, uid in enumerate(uuids):
        entry = {
            "uuid": uid,
            "parentUuid": uuids[i - 1] if i > 0 else None,
            "type": roles[i % 2],
            "timestamp": f"2025-01-01T00:00:{i:02d}Z",
        }
        entries.append(entry)

    # Add fork branches at specified indices
    for fork_idx in fork_indices:
        if fork_idx >= len(uuids):
            continue
        fork_parent = uuids[fork_idx]
        branch_len = max(2, n_entries // 4)
        for j in range(branch_len):
            uid = str(uuid_mod.uuid4())
            entry = {
                "uuid": uid,
                "parentUuid": fork_parent if j == 0 else entries[-1]["uuid"],
                "type": roles[j % 2],
                # Earlier timestamps so the main chain stays active
                "timestamp": f"2024-06-01T00:00:{j:02d}Z",
            }
            entries.append(entry)

    return entries


@st.composite
def uuid_trees(draw):
    """Hypothesis strategy generating random UUID trees."""
    n = draw(st.integers(min_value=2, max_value=30))
    n_forks = draw(st.integers(min_value=0, max_value=min(3, n - 1)))
    fork_indices = draw(
        st.lists(
            st.integers(min_value=0, max_value=n - 1),
            min_size=n_forks,
            max_size=n_forks,
            unique=True,
        )
    )
    return _build_uuid_tree(n, fork_indices)


class TestFindAllBranchesProperties:
    @given(entries=uuid_trees())
    @settings(max_examples=200)
    def test_exactly_one_active_branch(self, entries):
        branches = find_all_branches(entries)
        active = [b for b in branches if b["is_active"]]
        assert len(active) == 1

    @given(entries=uuid_trees())
    @settings(max_examples=200)
    def test_active_contains_latest_entry(self, entries):
        branches = find_all_branches(entries)
        latest = max(entries, key=lambda e: e.get("timestamp") or "")
        active = [b for b in branches if b["is_active"]][0]
        assert latest["uuid"] in active["uuids"]

    @given(entries=uuid_trees())
    @settings(max_examples=200)
    def test_no_duplicate_leaf_uuids(self, entries):
        branches = find_all_branches(entries)
        leaves = [b["leaf_uuid"] for b in branches]
        assert len(leaves) == len(set(leaves))

    @given(entries=uuid_trees())
    @settings(max_examples=200)
    def test_fork_points_on_active_path(self, entries):
        """Non-active branches should fork from a UUID on the active branch."""
        branches = find_all_branches(entries)
        active = [b for b in branches if b["is_active"]][0]
        for b in branches:
            if not b["is_active"] and b["fork_point_uuid"]:
                assert b["fork_point_uuid"] in active["uuids"]

    def test_empty_entries(self):
        assert find_all_branches([]) == []

    def test_entries_without_uuids(self):
        entries = [{"type": "user", "timestamp": "2025-01-01T00:00:00Z"}]
        assert find_all_branches(entries) == []

    def test_single_entry(self):
        entries = [{"uuid": "abc", "type": "user", "timestamp": "2025-01-01T00:00:00Z"}]
        branches = find_all_branches(entries)
        assert len(branches) == 1
        assert branches[0]["is_active"] is True
        assert "abc" in branches[0]["uuids"]


# ── Fixture-driven tests ──


class TestFixtureBranches:
    def test_branch_count(self, jsonl_fixture):
        all_entries = list(parse_all_with_uuids(jsonl_fixture))
        branches = find_all_branches(all_entries)
        expected = EXPECTED[jsonl_fixture.stem]
        assert len(branches) == expected["branches"], (
            f"{jsonl_fixture.stem}: expected {expected['branches']} branches, got {len(branches)}"
        )

    def test_active_exchange_count(self, jsonl_fixture):
        all_entries = list(parse_all_with_uuids(jsonl_fixture))
        branches = find_all_branches(all_entries)
        active = [b for b in branches if b["is_active"]][0]
        active_entries = [e for e in all_entries if e.get("uuid") in active["uuids"]]
        exchange_count, _, _, _ = compute_branch_metadata(active_entries)
        expected = EXPECTED[jsonl_fixture.stem]
        assert exchange_count == expected["active_exchanges"], (
            f"{jsonl_fixture.stem}: expected {expected['active_exchanges']} exchanges, got {exchange_count}"
        )

    def test_active_branch_has_fork_point_none(self, jsonl_fixture):
        all_entries = list(parse_all_with_uuids(jsonl_fixture))
        branches = find_all_branches(all_entries)
        active = [b for b in branches if b["is_active"]][0]
        assert active["fork_point_uuid"] is None


class TestParseJsonlFile:
    def test_filters_non_user_assistant(self, jsonl_fixture):
        """parse_jsonl_file should only yield user and assistant entries."""
        for entry in parse_jsonl_file(jsonl_fixture):
            assert entry["type"] in ("user", "assistant")

    def test_filters_meta_entries(self, jsonl_fixture):
        """isMeta entries without origin are still filtered; those with origin pass through."""
        for entry in parse_jsonl_file(jsonl_fixture):
            if entry.get("isMeta"):
                assert entry.get("origin") is not None

    def test_yields_entries(self, jsonl_fixture):
        """Should yield at least one entry for each fixture."""
        entries = list(parse_jsonl_file(jsonl_fixture))
        assert len(entries) > 0

    def test_channel_messages_pass_through(self):
        """Channel messages with isMeta=true + origin should pass through."""
        fixture = Path(__file__).parent / "fixtures" / "channel_telegram.jsonl"
        entries = list(parse_jsonl_file(fixture))
        has_channel = any(e.get("origin") for e in entries)
        assert has_channel

    def test_meta_without_origin_still_filtered(self):
        """isMeta entries without origin should still be filtered."""
        fixture = Path(__file__).parent / "fixtures" / "channel_telegram.jsonl"
        for entry in parse_jsonl_file(fixture):
            if entry.get("isMeta"):
                assert entry.get("origin") is not None


class TestExtractSessionMetadata:
    def test_timestamps_from_entries(self):
        entries = [
            {"timestamp": "2025-01-01T10:00:00Z", "cwd": "/home/user", "gitBranch": "main"},
            {"timestamp": "2025-01-01T10:05:00Z"},
            {"timestamp": "2025-01-01T10:10:00Z"},
        ]
        meta = extract_session_metadata(entries)
        assert meta["started_at"] == "2025-01-01T10:00:00Z"
        assert meta["ended_at"] == "2025-01-01T10:10:00Z"
        assert meta["cwd"] == "/home/user"
        assert meta["git_branch"] == "main"

    def test_empty_entries(self):
        meta = extract_session_metadata([])
        assert meta["started_at"] is None
        assert meta["ended_at"] is None

    def test_entries_without_timestamps(self):
        entries = [{"cwd": "/tmp"}]
        meta = extract_session_metadata(entries)
        assert meta["started_at"] is None
        assert meta["cwd"] == "/tmp"


class TestComputeBranchMetadata:
    def test_simple_exchange_count(self):
        entries = [
            {"type": "user", "message": {"content": "Hi"}},
            {"type": "assistant", "message": {"content": "Hello"}},
            {"type": "user", "message": {"content": "How?"}},
            {"type": "assistant", "message": {"content": "Like this."}},
        ]
        count, files, commits, _ = compute_branch_metadata(entries)
        assert count == 2

    def test_notifications_not_counted_as_exchanges(self):
        """Task notification messages should not inflate exchange_count."""
        entries = [
            {"type": "user", "message": {"content": "Research AI memory"}},
            {"type": "assistant", "message": {"content": "Let me launch agents."}},
            {"type": "user", "message": {"content": "<task-notification><task-id>abc</task-id><result>done</result></task-notification>"}},
            {"type": "assistant", "message": {"content": "Agent completed."}},
            {"type": "user", "message": {"content": "<task-notification><task-id>def</task-id><result>also done</result></task-notification>"}},
            {"type": "assistant", "message": {"content": "All agents done."}},
            {"type": "user", "message": {"content": "Summarize the results"}},
            {"type": "assistant", "message": {"content": "Here is the summary."}},
        ]
        count, _, _, _ = compute_branch_metadata(entries)
        assert count == 2  # Only "Research AI memory" and "Summarize the results"

    def test_tool_results_not_counted_as_exchanges(self):
        entries = [
            {"type": "user", "message": {"content": "Do something"}},
            {"type": "assistant", "message": {"content": [
                {"type": "text", "text": "ok"},
                {"type": "tool_use", "name": "Read", "input": {}},
            ]}},
            # tool_result from user — should not count as a new exchange
            {"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "abc", "content": "data"},
            ]}},
            {"type": "assistant", "message": {"content": "Done."}},
        ]
        count, _, _, _ = compute_branch_metadata(entries)
        assert count == 1

    def test_files_modified_extracted(self):
        entries = [
            {"type": "user", "message": {"content": "Edit files"}},
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Edit", "input": {"file_path": "/a.py"}},
                {"type": "tool_use", "name": "Write", "input": {"file_path": "/b.py"}},
            ]}},
        ]
        _, files, _, _ = compute_branch_metadata(entries)
        assert files == ["/a.py", "/b.py"]

    def test_files_deduplicated(self):
        entries = [
            {"type": "user", "message": {"content": "Edit"}},
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Edit", "input": {"file_path": "/a.py"}},
            ]}},
            {"type": "user", "message": {"content": "Again"}},
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Edit", "input": {"file_path": "/a.py"}},
                {"type": "tool_use", "name": "Write", "input": {"file_path": "/b.py"}},
            ]}},
        ]
        _, files, _, _ = compute_branch_metadata(entries)
        assert files == ["/a.py", "/b.py"]

    def test_single_user_message(self):
        entries = [{"type": "user", "message": {"content": "Hello"}}]
        count, _, _, _ = compute_branch_metadata(entries)
        assert count == 1

    def test_empty_entries(self):
        count, files, commits, _ = compute_branch_metadata([])
        assert count == 0
        assert files == []
        assert commits == []
