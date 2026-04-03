"""Tests for fetch_pr_comments.py — extraction, classification, formatting, dedup."""

from __future__ import annotations

import json

from fetch_pr_comments import (
    BOT_BODY_LIMIT,
    _deduplicate_actionable,
    _extract_section_key,
    _keys_match,
    _normalize_key,
    _parse_slurped,
    _short_date,
    _truncate,
    build_result,
    classify_inline_comment,
    extract_sections,
    format_text,
    is_bot,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_comment(user="alice", ctype="issue_comment", body="hello",
                  is_bot_user=False, **extra):
    base = {
        "type": ctype,
        "id": extra.pop("id", 1),
        "user": user,
        "is_bot": is_bot_user,
        "body": body,
        "created_at": "2025-03-15T10:00:00Z",
        "url": "",
    }
    base.update(extra)
    return base


def _make_result(human=None, bot=None, must_fix=None, optional=None,
                 inline=None, pr=42):
    return {
        "pr_number": pr,
        "total_comments": len(human or []) + len(bot or []),
        "human_count": len(human or []),
        "bot_count": len(bot or []),
        "human_comments": human or [],
        "bot_comments": bot or [],
        "inline_comments": inline or [],
        "actionable": {
            "must_fix": must_fix or [],
            "optional": optional or [],
        },
    }


# ---------------------------------------------------------------------------
# _parse_slurped
# ---------------------------------------------------------------------------

class TestParseSlurped:
    def test_empty_string(self):
        assert _parse_slurped("") == []

    def test_single_page(self):
        data = json.dumps([[{"id": 1}, {"id": 2}]])
        assert _parse_slurped(data) == [{"id": 1}, {"id": 2}]

    def test_multiple_pages_flattened(self):
        data = json.dumps([[{"id": 1}], [{"id": 2}], [{"id": 3}]])
        assert _parse_slurped(data) == [{"id": 1}, {"id": 2}, {"id": 3}]

    def test_empty_pages(self):
        data = json.dumps([[], [{"id": 1}], []])
        assert _parse_slurped(data) == [{"id": 1}]


# ---------------------------------------------------------------------------
# is_bot
# ---------------------------------------------------------------------------

class TestIsBot:
    def test_bot_suffix(self):
        assert is_bot("dependabot[bot]") is True

    def test_human(self):
        assert is_bot("alice") is False

    def test_partial_match(self):
        assert is_bot("botuser") is False


# ---------------------------------------------------------------------------
# extract_sections
# ---------------------------------------------------------------------------

class TestExtractSections:
    def test_must_fix_section(self):
        body = "## Must-Fix\n\n**Use PLUGIN_ROOT** — hardcoded path breaks portability.\n"
        result = extract_sections(body)
        assert len(result["must_fix"]) == 1
        assert "PLUGIN_ROOT" in result["must_fix"][0]

    def test_optional_section(self):
        body = "## Optional\n\nConsider adding type hints.\n"
        result = extract_sections(body)
        assert len(result["optional"]) == 1

    def test_both_sections(self):
        body = (
            "## Must-Fix\n\nFix the path.\n\n"
            "## Optional\n\nAdd docstring.\n"
        )
        result = extract_sections(body)
        assert len(result["must_fix"]) == 1
        assert len(result["optional"]) == 1

    def test_none_body_skipped(self):
        body = "## Must-Fix\n\nNone.\n"
        result = extract_sections(body)
        assert len(result["must_fix"]) == 0

    def test_na_body_skipped(self):
        body = "## Optional\n\nN/A\n"
        result = extract_sections(body)
        assert len(result["optional"]) == 0

    def test_empty_body(self):
        result = extract_sections("")
        assert result == {"must_fix": [], "optional": []}

    def test_no_sections(self):
        body = "This is just a general comment with no headers."
        result = extract_sections(body)
        assert result == {"must_fix": [], "optional": []}

    def test_blocking_header(self):
        body = "## Blocking\n\nThis must be addressed.\n"
        result = extract_sections(body)
        assert len(result["must_fix"]) == 1

    def test_nit_header(self):
        body = "## Nit\n\nMinor style issue.\n"
        result = extract_sections(body)
        assert len(result["optional"]) == 1

    def test_bold_must_fix(self):
        body = "Some text.\n\n**Must-Fix**: broken import.\n"
        result = extract_sections(body)
        # Bold patterns match within section parts
        assert len(result["must_fix"]) == 1


# ---------------------------------------------------------------------------
# classify_inline_comment
# ---------------------------------------------------------------------------

class TestClassifyInlineComment:
    def test_must_fix_keyword(self):
        assert classify_inline_comment("This is a must-fix issue") == "must_fix"

    def test_blocking_keyword(self):
        assert classify_inline_comment("blocking: needs rewrite") == "must_fix"

    def test_nit_keyword(self):
        assert classify_inline_comment("nit: rename this variable") == "optional"

    def test_optional_keyword(self):
        assert classify_inline_comment("optional suggestion: use f-string") == "optional"

    def test_no_severity(self):
        assert classify_inline_comment("Looks good to me!") is None

    def test_p1_badge(self):
        assert classify_inline_comment("![P1] Fix this") == "must_fix"

    def test_p2_bold(self):
        assert classify_inline_comment("**P2** consider refactoring") == "optional"

    def test_critical_keyword(self):
        assert classify_inline_comment("critical: SQL injection risk") == "must_fix"

    def test_minor_keyword(self):
        assert classify_inline_comment("minor: inconsistent naming") == "optional"


# ---------------------------------------------------------------------------
# _normalize_key / _extract_section_key / _keys_match
# ---------------------------------------------------------------------------

class TestDeduplicationHelpers:
    def test_normalize_strips_code(self):
        assert "path" not in _normalize_key("`some/path.py`")

    def test_normalize_strips_numbering(self):
        assert _normalize_key("1. Fix the bug") == "fix the bug"

    def test_normalize_collapses_whitespace(self):
        assert "  " not in _normalize_key("fix   the   bug")

    def test_extract_section_key_bold_title(self):
        section = "## Must-Fix\n\n**Use PLUGIN_ROOT** — details here"
        key = _extract_section_key(section)
        assert "plugin" in key.lower() and "root" in key.lower()

    def test_extract_section_key_no_bold(self):
        section = "## Must-Fix\n\nFix the hardcoded path in line 42"
        key = _extract_section_key(section)
        assert "fix" in key.lower()

    def test_keys_match_identical(self):
        assert _keys_match("fix the bug", "fix the bug") is True

    def test_keys_match_similar(self):
        assert _keys_match("use plugin root path", "use plugin root variable") is True

    def test_keys_match_different(self):
        assert _keys_match("fix authentication", "add documentation") is False

    def test_keys_match_empty(self):
        assert _keys_match("", "") is True  # identical


# ---------------------------------------------------------------------------
# _deduplicate_actionable
# ---------------------------------------------------------------------------

class TestDeduplicateActionable:
    def test_no_duplicates(self):
        items = [
            {"source_user": "alice", "content": "**Fix path** — details"},
            {"source_user": "alice", "content": "**Add tests** — details"},
        ]
        result = _deduplicate_actionable(items)
        assert len(result) == 2

    def test_same_user_duplicate_keeps_latest(self):
        items = [
            {"source_user": "bob", "content": "**Fix path** — old version"},
            {"source_user": "bob", "content": "**Fix path** — new version"},
        ]
        result = _deduplicate_actionable(items)
        assert len(result) == 1
        assert "new version" in result[0]["content"]

    def test_different_users_not_deduped(self):
        items = [
            {"source_user": "alice", "content": "**Fix path** — alice's take"},
            {"source_user": "bob", "content": "**Fix path** — bob's take"},
        ]
        result = _deduplicate_actionable(items)
        assert len(result) == 2

    def test_empty_list(self):
        assert _deduplicate_actionable([]) == []


# ---------------------------------------------------------------------------
# format_text helpers
# ---------------------------------------------------------------------------

class TestFormatHelpers:
    def test_short_date_iso(self):
        assert _short_date("2025-01-15T14:30:00Z") == "2025-01-15"

    def test_short_date_empty(self):
        assert _short_date("") == ""

    def test_short_date_none(self):
        assert _short_date(None) == ""

    def test_short_date_short_string(self):
        assert _short_date("2025") == ""

    def test_truncate_under_limit(self):
        assert _truncate("short", 100) == "short"

    def test_truncate_at_limit(self):
        text = "x" * 300
        assert _truncate(text, 300) == text

    def test_truncate_over_limit(self):
        text = "x" * 500
        result = _truncate(text, 300)
        assert result.endswith("[...]")
        assert len(result) == 300 + len(" [...]")


# ---------------------------------------------------------------------------
# format_text (integration)
# ---------------------------------------------------------------------------

class TestFormatText:
    def test_empty_pr(self):
        result = _make_result()
        text = format_text(result)
        assert "PR #42" in text
        assert "0 human" in text
        assert "0 bot" in text

    def test_summary_line_counts(self):
        result = _make_result(
            human=[_make_comment()],
            bot=[_make_comment(user="ci[bot]", is_bot_user=True)],
        )
        text = format_text(result)
        assert "1 human" in text
        assert "1 bot" in text

    def test_must_fix_appears_before_comments(self):
        result = _make_result(
            human=[_make_comment(body="general comment")],
            must_fix=[{"source_user": "alice", "content": "Fix the bug"}],
        )
        text = format_text(result)
        must_fix_pos = text.index("MUST FIX")
        comments_pos = text.index("HUMAN COMMENTS")
        assert must_fix_pos < comments_pos

    def test_optional_section_present(self):
        result = _make_result(
            optional=[{"source_user": "bob", "content": "Consider renaming"}],
        )
        text = format_text(result)
        assert "OPTIONAL" in text
        assert "Consider renaming" in text

    def test_bot_bodies_truncated(self):
        long_body = "x" * 1000
        result = _make_result(
            bot=[_make_comment(user="ci[bot]", body=long_body, is_bot_user=True)],
        )
        text = format_text(result)
        assert "[...]" in text
        assert long_body not in text

    def test_human_bodies_not_truncated(self):
        long_body = "x" * 1000
        result = _make_result(
            human=[_make_comment(body=long_body)],
        )
        text = format_text(result)
        assert long_body in text

    def test_inline_comment_shows_path(self):
        comment = _make_comment(
            ctype="inline_comment",
            path="src/main.py",
            line=42,
        )
        result = _make_result(human=[comment])
        text = format_text(result)
        assert "src/main.py:42" in text

    def test_review_shows_state(self):
        comment = _make_comment(ctype="review", state="CHANGES_REQUESTED",
                                submitted_at="2025-03-15T10:00:00Z")
        result = _make_result(human=[comment])
        text = format_text(result)
        assert "CHANGES_REQUESTED" in text

    def test_compact_date_format(self):
        result = _make_result(
            human=[_make_comment(created_at="2025-03-15T10:30:45Z")],
        )
        text = format_text(result)
        # Should have date only, not full ISO timestamp
        assert "2025-03-15" in text
        assert "T10:30:45Z" not in text

    def test_no_sections_when_empty(self):
        result = _make_result()
        text = format_text(result)
        assert "MUST FIX" not in text
        assert "OPTIONAL" not in text
        assert "HUMAN COMMENTS" not in text
        assert "BOT COMMENTS" not in text
