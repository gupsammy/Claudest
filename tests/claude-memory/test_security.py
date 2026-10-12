"""Tests for sanitize_fts_term — FTS injection prevention."""

from __future__ import annotations

from memory_lib.content import sanitize_fts_term


class TestSanitizeFtsTerm:
    """Test FTS operator/keyword sanitization."""

    def test_plain_term_unchanged(self):
        assert sanitize_fts_term("hello") == "hello"

    def test_removes_quotes(self):
        assert sanitize_fts_term('"hello"') == "hello"

    def test_removes_parentheses(self):
        assert sanitize_fts_term("(foo)") == "foo"

    def test_removes_asterisk(self):
        assert sanitize_fts_term("prefix*") == "prefix"

    def test_removes_and_keyword(self):
        result = sanitize_fts_term("hello AND world")
        assert "AND" not in result
        assert "hello" in result
        assert "world" in result

    def test_removes_or_keyword(self):
        result = sanitize_fts_term("hello OR world")
        assert "OR" not in result.split()
        assert "hello" in result
        assert "world" in result

    def test_removes_not_keyword(self):
        result = sanitize_fts_term("hello NOT world")
        assert "NOT" not in result.split()
        assert "hello" in result
        assert "world" in result

    def test_hyphen_splits_words(self):
        # FTS tokenizers split "trade-off" into "trade" "off"; the quoted
        # phrase must keep both words, not fuse them into "tradeoff".
        assert sanitize_fts_term("trade-off") == "trade off"

    def test_removes_near_keyword(self):
        result = sanitize_fts_term("near miss")
        # "near" is a keyword — removed, leaving "miss"
        assert result == "miss"

    def test_keyword_removal_case_insensitive(self):
        result = sanitize_fts_term("Near AND or not")
        # All keywords removed, only whitespace left
        assert result == ""

    def test_empty_after_sanitization(self):
        assert sanitize_fts_term("AND") == ""
        assert sanitize_fts_term('"*"') == ""

    def test_injection_attempt(self):
        result = sanitize_fts_term('") OR ("')
        # Quotes and parens removed, "OR" keyword removed
        assert result == ""

    def test_mixed_operators_and_text(self):
        result = sanitize_fts_term("hello (world*) NOT bad")
        assert "hello" in result
        assert "world" in result
        assert "bad" in result
        assert "NOT" not in result.split()
        assert "(" not in result
        assert "*" not in result

    def test_removes_dash_operator(self):
        # FTS5 uses -term as NOT shorthand
        assert sanitize_fts_term("-excluded") == "excluded"
        assert sanitize_fts_term("hello -world") == "hello world"

    def test_removes_caret_operator(self):
        # FTS5 uses ^term for initial token match
        assert sanitize_fts_term("^first") == "first"

    def test_preserves_normal_punctuation(self):
        # Underscores, dots should pass through
        assert sanitize_fts_term("file_name") == "file_name"
        assert sanitize_fts_term("v1.0.0") == "v1.0.0"

    def test_multiple_spaces_after_removal(self):
        result = sanitize_fts_term("a AND b")
        # After removing "AND", spaces remain but get stripped at edges
        assert "a" in result
        assert "b" in result
