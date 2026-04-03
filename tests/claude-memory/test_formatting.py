"""Tests for memory_lib.formatting — time formatting, project paths, session rendering."""

from __future__ import annotations

import re

from memory_lib.formatting import (
    extract_project_name,
    format_markdown_session,
    format_time,
    format_time_full,
    get_project_key,
    normalize_cwd,
    normalize_project_key,
    parse_project_key,
)


class TestFormatTime:
    def test_valid_iso_timestamp(self):
        result = format_time("2025-01-15T14:30:00Z")
        # Should produce HH:MM in local timezone
        assert re.match(r"\d{2}:\d{2}$", result), f"Expected HH:MM format, got {result!r}"

    def test_none_returns_placeholder(self):
        assert format_time(None) == "??:??"

    def test_empty_string_returns_placeholder(self):
        assert format_time("") == "??:??"

    def test_malformed_string_fallback(self):
        result = format_time("not-a-timestamp")
        # Should return first 16 chars as fallback
        assert result == "not-a-timestamp"

    def test_custom_format(self):
        result = format_time("2025-01-15T14:30:00Z", "%Y-%m-%d")
        assert "2025" in result
        assert "01" in result
        assert "15" in result


class TestFormatTimeFull:
    def test_valid_timestamp(self):
        result = format_time_full("2025-01-15T14:30:00Z")
        # Should contain YYYY-MM-DD
        assert "2025" in result
        assert "01-15" in result


class TestProjectKey:
    def test_get_project_key(self):
        assert get_project_key("/Users/sam/project") == "-Users-sam-project"

    def test_get_project_key_with_dots(self):
        assert get_project_key("/home/user/.config") == "-home-user--config"

    def test_parse_project_key_roundtrip(self):
        """parse_project_key(get_project_key(path)) should roundtrip for paths without dashes."""
        original = "/Users/sam/project"
        key = get_project_key(original)
        reconstructed = parse_project_key(key)
        assert reconstructed == original, f"Expected exact roundtrip, got {reconstructed!r}"

    def test_parse_project_key_lossy_with_dashes(self):
        """parse_project_key is lossy for paths containing dashes (dashes become /)."""
        original = "/home/user/my-project"
        key = get_project_key(original)
        reconstructed = parse_project_key(key)
        # Dashes in "my-project" become "/" — this is a known limitation
        assert reconstructed != original, "Paths with dashes should NOT roundtrip"
        assert reconstructed.startswith("/")

    def test_get_project_key_worktree_resolves_to_base(self):
        """Worktree paths should resolve to the same key as the base repo."""
        base = "/Users/sam/repos/myproject"
        worktree = "/Users/sam/repos/myproject/.claude/worktrees/feature-branch"
        assert get_project_key(worktree) == get_project_key(base)

    def test_get_project_key_worktree_with_dots_in_name(self):
        """Worktree names with dots should still resolve correctly."""
        base = "/Users/sam/repos/myproject"
        worktree = "/Users/sam/repos/myproject/.claude/worktrees/fix.auth.bug"
        assert get_project_key(worktree) == get_project_key(base)

    def test_get_project_key_non_worktree_unchanged(self):
        """Regular paths should be unaffected by worktree resolution."""
        path = "/Users/sam/repos/myproject"
        assert get_project_key(path) == "-Users-sam-repos-myproject"

    def test_get_project_key_worktree_in_dotconfig(self):
        """A .claude/worktrees/ path under .config should still resolve."""
        base = "/home/user/.config/tool"
        worktree = "/home/user/.config/tool/.claude/worktrees/test-wt"
        assert get_project_key(worktree) == get_project_key(base)

    def test_normalize_project_key_strips_worktree(self):
        """Encoded worktree keys should normalize to base repo key."""
        base_key = "-Users-sam-repos-myproject"
        worktree_key = "-Users-sam-repos-myproject--claude-worktrees-feature-branch"
        assert normalize_project_key(worktree_key) == base_key

    def test_normalize_project_key_no_op_for_regular(self):
        """Regular keys should pass through unchanged."""
        key = "-Users-sam-repos-myproject"
        assert normalize_project_key(key) == key

    def test_normalize_matches_get_project_key(self):
        """normalize_project_key on encoded worktree should match get_project_key on base path."""
        base_path = "/Users/sam/repos/myproject"
        worktree_dir_name = "-Users-sam-repos-myproject--claude-worktrees-feat"
        assert normalize_project_key(worktree_dir_name) == get_project_key(base_path)

    def test_get_project_key_rfind_uses_last_marker(self):
        """If .claude/worktrees/ appears multiple times, rfind strips only the last one."""
        path = "/tmp/.claude/worktrees/repo/.claude/worktrees/feat"
        # rfind strips the last worktree suffix, leaving the first as part of the base path
        assert get_project_key(path) == "-tmp--claude-worktrees-repo"

    def test_normalize_project_key_rfind_uses_last_marker(self):
        """Encoded key with multiple worktree markers uses last occurrence."""
        key = "-tmp--claude-worktrees-repo--claude-worktrees-feat"
        assert normalize_project_key(key) == "-tmp--claude-worktrees-repo"

    def test_normalize_cwd_strips_worktree(self):
        """normalize_cwd returns base repo path from a worktree path."""
        wt = "/Users/sam/repos/myproject/.claude/worktrees/feat"
        assert normalize_cwd(wt) == "/Users/sam/repos/myproject"

    def test_normalize_cwd_noop_for_regular(self):
        """normalize_cwd passes through non-worktree paths unchanged."""
        path = "/Users/sam/repos/myproject"
        assert normalize_cwd(path) == path

    def test_normalize_cwd_project_name_is_base(self):
        """After normalize_cwd, Path().name should be the base repo name, not the worktree name."""
        from pathlib import Path
        wt = "/Users/sam/repos/myproject/.claude/worktrees/feat"
        assert Path(normalize_cwd(wt)).name == "myproject"

    def test_parse_project_key_adds_leading_slash(self):
        result = parse_project_key("-Users-sam-project")
        assert result.startswith("/")

    def test_extract_project_name(self):
        assert extract_project_name("/Users/sam/my-project") == "my-project"

    def test_extract_project_name_trailing_slash(self):
        # Path().name handles trailing slashes
        assert extract_project_name("/Users/sam/project") == "project"


class TestFormatMarkdownSession:
    def test_minimal_session(self):
        session = {
            "uuid": "abcdef12-3456-7890-abcd-ef1234567890",
            "project": "test-project",
            "started_at": "2025-01-15T14:30:00Z",
            "messages": [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there"},
            ],
        }
        md = format_markdown_session(session)
        assert "## test-project" in md
        assert "abcdef12" in md  # First 8 chars of UUID
        assert "**User:** Hello" in md
        assert "**Assistant:** Hi there" in md
        assert md.endswith("---\n")

    def test_verbose_with_files_and_commits(self):
        session = {
            "uuid": "abcdef12-3456-7890-abcd-ef1234567890",
            "project": "proj",
            "started_at": "2025-01-15T14:30:00Z",
            "git_branch": "feature-x",
            "files_modified": ["/a.py", "/b.py"],
            "commits": ["Fix bug"],
            "messages": [],
        }
        md = format_markdown_session(session, verbose=True)
        assert "Branch: feature-x" in md
        assert "### Files Modified" in md
        assert "`/a.py`" in md
        assert "### Commits" in md
        assert "Fix bug" in md

    def test_non_verbose_hides_files(self):
        session = {
            "uuid": "abcdef12",
            "project": "proj",
            "started_at": None,
            "files_modified": ["/a.py"],
            "commits": ["Fix"],
            "messages": [],
        }
        md = format_markdown_session(session, verbose=False)
        assert "### Files Modified" not in md
        assert "### Commits" not in md

    def test_many_files_truncated(self):
        session = {
            "uuid": "abcdef12",
            "project": "proj",
            "started_at": None,
            "files_modified": [f"/file{i}.py" for i in range(15)],
            "messages": [],
        }
        md = format_markdown_session(session, verbose=True)
        assert "...and 5 more" in md

    def test_notification_message_labeled(self):
        session = {
            "uuid": "abcdef12",
            "project": "proj",
            "started_at": None,
            "messages": [
                {"role": "user", "content": "Start research"},
                {"role": "assistant", "content": "Launching agents."},
                {"role": "user", "content": "<task-notification>result</task-notification>", "is_notification": 1},
                {"role": "assistant", "content": "Agent done."},
            ],
        }
        md = format_markdown_session(session)
        assert "**Subagent Result:**" in md
        assert "**User:** Start research" in md
        assert "**Assistant:** Launching agents." in md

    def test_non_notification_user_not_labeled_as_subagent(self):
        session = {
            "uuid": "abcdef12",
            "project": "proj",
            "started_at": None,
            "messages": [
                {"role": "user", "content": "Hello", "is_notification": 0},
            ],
        }
        md = format_markdown_session(session)
        assert "**User:** Hello" in md
        assert "Subagent Result" not in md
