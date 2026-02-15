"""Unit tests for search vector generation helpers."""

from __future__ import annotations

import importlib.util

import pytest
from ._test_helpers import MEMORY_MODULE_PATH

# Load the module directly from disk so we do not need the roster package
# installed as a proper Python package.
_MODULE_PATH = MEMORY_MODULE_PATH / "search_vector.py"
_spec = importlib.util.spec_from_file_location("search_vector", _MODULE_PATH)
assert _spec is not None and _spec.loader is not None
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

preprocess_text = _mod.preprocess_text
tsvector_sql = _mod.tsvector_sql
tsquery_sql = _mod.tsquery_sql
websearch_tsquery_sql = _mod.websearch_tsquery_sql
preprocess_search_query = _mod.preprocess_search_query
MAX_TEXT_BYTES = _mod.MAX_TEXT_BYTES
TS_CONFIG = _mod.TS_CONFIG

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# preprocess_text
# ---------------------------------------------------------------------------


class TestPreprocessText:
    """Tests for preprocess_text()."""

    def test_normal_text(self) -> None:
        """Normal text passes through with minimal changes."""
        result = preprocess_text("Hello world, this is a test.")
        assert result == "Hello world, this is a test."

    def test_none_returns_empty(self) -> None:
        """None input returns an empty string."""
        assert preprocess_text(None) == ""

    def test_empty_string_returns_empty(self) -> None:
        """Empty string input returns an empty string."""
        assert preprocess_text("") == ""

    def test_whitespace_only_returns_empty(self) -> None:
        """Whitespace-only input returns an empty string after strip."""
        assert preprocess_text("   \t\n  ") == ""

    def test_collapses_whitespace(self) -> None:
        """Multiple spaces, tabs, newlines collapse to single space."""
        result = preprocess_text("hello   world\t\tfoo\n\nbar")
        assert result == "hello world foo bar"

    def test_strips_leading_trailing_whitespace(self) -> None:
        """Leading/trailing whitespace is removed."""
        result = preprocess_text("  hello world  ")
        assert result == "hello world"

    def test_removes_nul_bytes(self) -> None:
        """NUL bytes are stripped from the text."""
        result = preprocess_text("hello\x00world")
        assert result == "helloworld"

    def test_special_characters_preserved(self) -> None:
        """Special characters (quotes, parens, etc.) are preserved."""
        text = """He said "it's (not) a bug" & filed issue #42 — really?!"""
        result = preprocess_text(text)
        assert result == text

    def test_unicode_preserved(self) -> None:
        """Unicode characters are preserved after preprocessing."""
        text = "cafe\u0301 \u00fc\u00f1\u00ee\u00e7\u00f6\u00f0\u00e9"
        result = preprocess_text(text)
        assert result == text

    def test_long_text_truncated(self) -> None:
        """Text longer than MAX_TEXT_BYTES is truncated."""
        # Create text that is definitely over 1 MB in UTF-8
        long_text = "a" * (MAX_TEXT_BYTES + 1000)
        result = preprocess_text(long_text)
        assert len(result.encode("utf-8")) <= MAX_TEXT_BYTES

    def test_long_text_under_limit_not_truncated(self) -> None:
        """Text just under the limit is not truncated."""
        text = "a" * (MAX_TEXT_BYTES - 1)
        result = preprocess_text(text)
        assert result == text

    def test_truncation_respects_codepoint_boundary(self) -> None:
        """Truncation does not break multi-byte Unicode characters."""
        # Each emoji is 4 bytes in UTF-8.  Fill to just over limit.
        emoji = "\U0001f600"  # grinning face
        count = MAX_TEXT_BYTES // 4 + 10  # slightly over limit
        long_text = emoji * count
        result = preprocess_text(long_text)
        # Must be valid UTF-8 (would raise on decode if broken)
        result.encode("utf-8")
        assert len(result.encode("utf-8")) <= MAX_TEXT_BYTES

    def test_backslash_preserved(self) -> None:
        """Backslashes in text are preserved."""
        result = preprocess_text(r"path\to\file")
        assert result == r"path\to\file"


# ---------------------------------------------------------------------------
# SQL expression helpers
# ---------------------------------------------------------------------------


class TestTsvectorSql:
    """Tests for tsvector_sql()."""

    def test_default_param(self) -> None:
        """Default parameter is $1."""
        assert tsvector_sql() == f"to_tsvector('{TS_CONFIG}', $1)"

    def test_custom_param(self) -> None:
        """Custom parameter is respected."""
        assert tsvector_sql("$3") == f"to_tsvector('{TS_CONFIG}', $3)"

    def test_config_is_english(self) -> None:
        """The text-search config should be 'english'."""
        assert TS_CONFIG == "english"


class TestTsquerySql:
    """Tests for tsquery_sql()."""

    def test_default_param(self) -> None:
        """Default parameter is $1."""
        assert tsquery_sql() == f"plainto_tsquery('{TS_CONFIG}', $1)"

    def test_custom_param(self) -> None:
        """Custom parameter is respected."""
        assert tsquery_sql("$2") == f"plainto_tsquery('{TS_CONFIG}', $2)"


class TestWebsearchTsquerySql:
    """Tests for websearch_tsquery_sql()."""

    def test_default_param(self) -> None:
        """Default parameter is $1."""
        assert websearch_tsquery_sql() == f"websearch_to_tsquery('{TS_CONFIG}', $1)"

    def test_custom_param(self) -> None:
        """Custom parameter is respected."""
        assert websearch_tsquery_sql("$4") == f"websearch_to_tsquery('{TS_CONFIG}', $4)"


# ---------------------------------------------------------------------------
# preprocess_search_query
# ---------------------------------------------------------------------------


class TestPreprocessSearchQuery:
    """Tests for preprocess_search_query()."""

    def test_normal_query(self) -> None:
        """Normal query passes through."""
        assert preprocess_search_query("find memory facts") == "find memory facts"

    def test_none_returns_empty(self) -> None:
        """None input returns empty string."""
        assert preprocess_search_query(None) == ""

    def test_empty_string_returns_empty(self) -> None:
        """Empty string returns empty string."""
        assert preprocess_search_query("") == ""

    def test_collapses_whitespace(self) -> None:
        """Whitespace is collapsed."""
        assert preprocess_search_query("hello   world") == "hello world"

    def test_strips_whitespace(self) -> None:
        """Leading/trailing whitespace is stripped."""
        assert preprocess_search_query("  hello  ") == "hello"

    def test_removes_nul_bytes(self) -> None:
        """NUL bytes are removed."""
        assert preprocess_search_query("test\x00query") == "testquery"

    def test_special_chars_preserved(self) -> None:
        """Special characters are preserved (tsquery functions handle them)."""
        query = '"exact phrase" -exclude OR alternative'
        result = preprocess_search_query(query)
        assert result == query

    def test_tabs_and_newlines_collapsed(self) -> None:
        """Tabs and newlines in queries are collapsed to single spaces."""
        assert preprocess_search_query("hello\tworld\nfoo") == "hello world foo"

    def test_multiple_nul_bytes(self) -> None:
        """Multiple NUL bytes are all removed."""
        assert preprocess_search_query("a\x00b\x00c") == "abc"

    def test_whitespace_only_returns_empty(self) -> None:
        """Whitespace-only query returns empty string after strip."""
        assert preprocess_search_query("   \t\n  ") == ""

    def test_unicode_query_preserved(self) -> None:
        """Unicode characters in queries are preserved."""
        query = "\u00fc\u00f1\u00ee\u00e7\u00f6\u00f0\u00e9 search"
        assert preprocess_search_query(query) == query

    def test_does_not_truncate_long_query(self) -> None:
        """Search queries are not truncated (unlike preprocess_text)."""
        long_query = "word " * 10000
        result = preprocess_search_query(long_query)
        # Should be collapsed but not truncated
        assert result == "word " * 9999 + "word"


# ---------------------------------------------------------------------------
# Additional preprocess_text edge cases
# ---------------------------------------------------------------------------


class TestPreprocessTextEdgeCases:
    """Additional edge cases for preprocess_text()."""

    def test_multiple_nul_bytes(self) -> None:
        """Multiple NUL bytes scattered throughout are all removed."""
        result = preprocess_text("a\x00b\x00c\x00d")
        assert result == "abcd"

    def test_nul_bytes_with_whitespace(self) -> None:
        """NUL bytes adjacent to whitespace are handled correctly."""
        result = preprocess_text("hello \x00 world")
        assert result == "hello world"

    def test_only_nul_bytes(self) -> None:
        """A string of only NUL bytes should return empty string."""
        result = preprocess_text("\x00\x00\x00")
        assert result == ""

    def test_mixed_whitespace_types(self) -> None:
        """Various whitespace characters are all collapsed."""
        result = preprocess_text("a\r\nb\r\nc\td\ve\ff")
        assert result == "a b c d e f"

    def test_exactly_at_byte_limit(self) -> None:
        """Text exactly at MAX_TEXT_BYTES is not truncated."""
        text = "a" * MAX_TEXT_BYTES
        result = preprocess_text(text)
        assert len(result.encode("utf-8")) == MAX_TEXT_BYTES

    def test_single_char(self) -> None:
        """Single character passes through."""
        assert preprocess_text("x") == "x"

    def test_two_byte_utf8_truncation(self) -> None:
        """Truncation handles 2-byte UTF-8 characters correctly."""
        # Latin small letter u with diaeresis is 2 bytes in UTF-8
        char = "\u00fc"
        count = MAX_TEXT_BYTES // 2 + 10
        long_text = char * count
        result = preprocess_text(long_text)
        encoded = result.encode("utf-8")
        assert len(encoded) <= MAX_TEXT_BYTES
        # Result should be valid UTF-8
        encoded.decode("utf-8")

    def test_three_byte_utf8_truncation(self) -> None:
        """Truncation handles 3-byte UTF-8 characters correctly."""
        # CJK character is 3 bytes in UTF-8
        char = "\u4e16"  # Chinese character for 'world'
        count = MAX_TEXT_BYTES // 3 + 10
        long_text = char * count
        result = preprocess_text(long_text)
        encoded = result.encode("utf-8")
        assert len(encoded) <= MAX_TEXT_BYTES
        encoded.decode("utf-8")


# ---------------------------------------------------------------------------
# SQL helpers additional tests
# ---------------------------------------------------------------------------


class TestSqlHelpersEdgeCases:
    """Additional tests for SQL generation helpers."""

    def test_tsvector_sql_named_param(self) -> None:
        """tsvector_sql handles named parameter-like strings."""
        result = tsvector_sql("$10")
        assert result == f"to_tsvector('{TS_CONFIG}', $10)"

    def test_tsquery_sql_named_param(self) -> None:
        result = tsquery_sql("$10")
        assert result == f"plainto_tsquery('{TS_CONFIG}', $10)"

    def test_websearch_tsquery_sql_named_param(self) -> None:
        result = websearch_tsquery_sql("$10")
        assert result == f"websearch_to_tsquery('{TS_CONFIG}', $10)"

    def test_max_text_bytes_is_1mb(self) -> None:
        """MAX_TEXT_BYTES should be 1 MB (1,048,576 bytes)."""
        assert MAX_TEXT_BYTES == 1_048_576

    def test_ts_config_constant(self) -> None:
        """TS_CONFIG should be 'english'."""
        assert TS_CONFIG == "english"
