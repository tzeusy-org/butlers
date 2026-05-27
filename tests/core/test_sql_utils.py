"""Unit tests for butlers._sql_utils — shared SQL query helpers."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


class TestEscapeLikePattern:
    """Tests for escape_like_pattern()."""

    def test_percent_is_escaped(self):
        """% is escaped to \\% so it is treated as a literal character."""
        from butlers._sql_utils import escape_like_pattern

        assert escape_like_pattern("goog%") == "goog\\%"

    def test_underscore_is_escaped(self):
        """_ is escaped to \\_ so it is treated as a literal character."""
        from butlers._sql_utils import escape_like_pattern

        assert escape_like_pattern("g_ogle") == "g\\_ogle"

    def test_backslash_is_doubled(self):
        """Backslash is doubled before other escapes are applied."""
        from butlers._sql_utils import escape_like_pattern

        assert escape_like_pattern("go\\ogle") == "go\\\\ogle"

    def test_clean_value_is_unchanged(self):
        """A value with no metacharacters is returned unchanged."""
        from butlers._sql_utils import escape_like_pattern

        assert escape_like_pattern("google") == "google"

    def test_multiple_metacharacters(self):
        """All metacharacters in a single value are escaped."""
        from butlers._sql_utils import escape_like_pattern

        assert escape_like_pattern("%_foo%") == "\\%\\_foo\\%"

    def test_empty_string(self):
        """Empty string is returned unchanged."""
        from butlers._sql_utils import escape_like_pattern

        assert escape_like_pattern("") == ""
