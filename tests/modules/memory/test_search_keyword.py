"""Unit tests for keyword_search() in the Memory Butler search module."""

from __future__ import annotations

import importlib.util
from unittest.mock import AsyncMock, MagicMock

import pytest

from ._test_helpers import MEMORY_MODULE_PATH

# ---------------------------------------------------------------------------
# Load search module from disk (roster/ is not a Python package).
# ---------------------------------------------------------------------------

_SEARCH_PATH = MEMORY_MODULE_PATH / "search.py"


def _load_search_module():
    spec = importlib.util.spec_from_file_location("search", _SEARCH_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_mod = _load_search_module()
keyword_search = _mod.keyword_search
preprocess_search_query = _mod.preprocess_search_query
_VALID_TABLES = _mod._VALID_TABLES
_SCOPED_TABLES = _mod._SCOPED_TABLES
_TS_CONFIG = _mod._TS_CONFIG

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_pool() -> AsyncMock:
    """Return an AsyncMock asyncpg pool with a mocked fetch method."""
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[])
    return pool


def _make_row(data: dict) -> MagicMock:
    """Create a MagicMock that behaves like an asyncpg Record (dict-like)."""
    row = MagicMock()
    row.__iter__ = MagicMock(return_value=iter(data.items()))
    # Make dict(row) work by providing keys() and __getitem__
    row.keys.return_value = data.keys()
    row.__getitem__ = lambda self, key: data[key]
    return row


# ---------------------------------------------------------------------------
# Tests — Validation
# ---------------------------------------------------------------------------


class TestKeywordSearchValidation:
    """Tests for input validation in keyword_search()."""

    async def test_invalid_table_raises_value_error(self, mock_pool: AsyncMock) -> None:
        """Invalid table name raises ValueError."""
        with pytest.raises(ValueError, match="Invalid table"):
            await keyword_search(mock_pool, "test query", "nonexistent")

    async def test_invalid_table_lists_valid_options(self, mock_pool: AsyncMock) -> None:
        """ValueError message lists valid table names."""
        with pytest.raises(ValueError, match="episodes"):
            await keyword_search(mock_pool, "test query", "bad_table")

    @pytest.mark.parametrize("table", sorted(_VALID_TABLES))
    async def test_valid_tables_accepted(self, mock_pool: AsyncMock, table: str) -> None:
        """All valid table names are accepted without error."""
        result = await keyword_search(mock_pool, "test", table)
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# Tests — Empty / None query handling
# ---------------------------------------------------------------------------


class TestKeywordSearchEmptyQuery:
    """Tests for empty and None query handling."""

    async def test_empty_string_returns_empty_list(self, mock_pool: AsyncMock) -> None:
        """Empty query string returns empty list without querying DB."""
        result = await keyword_search(mock_pool, "", "episodes")
        assert result == []
        mock_pool.fetch.assert_not_awaited()

    async def test_whitespace_only_returns_empty_list(self, mock_pool: AsyncMock) -> None:
        """Whitespace-only query returns empty list without querying DB."""
        result = await keyword_search(mock_pool, "   \t\n  ", "facts")
        assert result == []
        mock_pool.fetch.assert_not_awaited()

    async def test_nul_bytes_only_returns_empty_list(self, mock_pool: AsyncMock) -> None:
        """Query of only NUL bytes returns empty list."""
        result = await keyword_search(mock_pool, "\x00\x00", "rules")
        assert result == []
        mock_pool.fetch.assert_not_awaited()


# ---------------------------------------------------------------------------
# Tests — Query preprocessing
# ---------------------------------------------------------------------------


class TestKeywordSearchPreprocessing:
    """Tests that query text is preprocessed before search."""

    async def test_query_is_preprocessed(self, mock_pool: AsyncMock) -> None:
        """Query text is cleaned via preprocess_search_query before use."""
        await keyword_search(mock_pool, "  hello   world  ", "episodes")
        # The first positional arg after sql should be the cleaned query
        call_args = mock_pool.fetch.call_args
        assert call_args is not None
        # $1 param is the cleaned query
        assert call_args[0][1] == "hello world"

    async def test_nul_bytes_removed_from_query(self, mock_pool: AsyncMock) -> None:
        """NUL bytes are stripped from the query before searching."""
        await keyword_search(mock_pool, "test\x00query", "episodes")
        call_args = mock_pool.fetch.call_args
        assert call_args is not None
        assert call_args[0][1] == "testquery"


# ---------------------------------------------------------------------------
# Tests — SQL generation
# ---------------------------------------------------------------------------


class TestKeywordSearchSQL:
    """Tests for correct SQL generation."""

    async def test_uses_plainto_tsquery(self, mock_pool: AsyncMock) -> None:
        """SQL uses plainto_tsquery for safe user input handling."""
        await keyword_search(mock_pool, "test", "episodes")
        sql = mock_pool.fetch.call_args[0][0]
        assert "plainto_tsquery('english', $1)" in sql

    async def test_uses_ts_rank(self, mock_pool: AsyncMock) -> None:
        """SQL uses ts_rank for result ranking."""
        await keyword_search(mock_pool, "test", "episodes")
        sql = mock_pool.fetch.call_args[0][0]
        assert "ts_rank(search_vector, plainto_tsquery('english', $1))" in sql

    async def test_uses_search_vector_match(self, mock_pool: AsyncMock) -> None:
        """SQL uses @@ operator for tsvector matching."""
        await keyword_search(mock_pool, "test", "episodes")
        sql = mock_pool.fetch.call_args[0][0]
        assert "search_vector @@ plainto_tsquery('english', $1)" in sql

    async def test_order_by_rank_desc(self, mock_pool: AsyncMock) -> None:
        """Results are ordered by rank descending."""
        await keyword_search(mock_pool, "test", "episodes")
        sql = mock_pool.fetch.call_args[0][0]
        assert "ORDER BY rank DESC" in sql

    async def test_selects_from_correct_table(self, mock_pool: AsyncMock) -> None:
        """SQL FROM clause uses the specified table."""
        for table in _VALID_TABLES:
            mock_pool.fetch.reset_mock()
            await keyword_search(mock_pool, "test", table)
            sql = mock_pool.fetch.call_args[0][0]
            assert f"FROM {table}" in sql


# ---------------------------------------------------------------------------
# Tests — Limit parameter
# ---------------------------------------------------------------------------


class TestKeywordSearchLimit:
    """Tests for the limit parameter."""

    async def test_default_limit_is_10(self, mock_pool: AsyncMock) -> None:
        """Default limit of 10 is passed to the SQL query."""
        await keyword_search(mock_pool, "test", "episodes")
        call_args = mock_pool.fetch.call_args[0]
        # Last positional param should be the limit
        assert call_args[-1] == 10

    async def test_custom_limit_passed(self, mock_pool: AsyncMock) -> None:
        """Custom limit value is passed to the SQL query."""
        await keyword_search(mock_pool, "test", "episodes", limit=25)
        call_args = mock_pool.fetch.call_args[0]
        assert call_args[-1] == 25

    async def test_limit_in_sql(self, mock_pool: AsyncMock) -> None:
        """SQL contains a LIMIT clause."""
        await keyword_search(mock_pool, "test", "episodes")
        sql = mock_pool.fetch.call_args[0][0]
        assert "LIMIT" in sql


# ---------------------------------------------------------------------------
# Tests — Scope filtering
# ---------------------------------------------------------------------------


class TestKeywordSearchScope:
    """Tests for scope filtering behaviour."""

    async def test_scope_applied_for_facts(self, mock_pool: AsyncMock) -> None:
        """Scope filter includes global+scope for facts."""
        await keyword_search(mock_pool, "test", "facts", scope="butler-a")
        sql = mock_pool.fetch.call_args[0][0]
        assert "scope IN ('global', $2)" in sql
        # scope value passed as second param
        call_args = mock_pool.fetch.call_args[0]
        assert call_args[2] == "butler-a"

    async def test_scope_applied_for_rules(self, mock_pool: AsyncMock) -> None:
        """Scope filter includes global+scope for rules."""
        await keyword_search(mock_pool, "test", "rules", scope="butler-b")
        sql = mock_pool.fetch.call_args[0][0]
        assert "scope IN ('global', $2)" in sql
        call_args = mock_pool.fetch.call_args[0]
        assert call_args[2] == "butler-b"

    async def test_scope_episodes_uses_butler_column(self, mock_pool: AsyncMock) -> None:
        """Episodes use butler column for scope filtering."""
        await keyword_search(mock_pool, "test", "episodes", scope="butler-a")
        sql = mock_pool.fetch.call_args[0][0]
        assert "butler = $2" in sql
        assert "scope" not in sql

    async def test_no_scope_no_filter(self, mock_pool: AsyncMock) -> None:
        """When scope is None, no scope filter is added."""
        await keyword_search(mock_pool, "test", "facts", scope=None)
        sql = mock_pool.fetch.call_args[0][0]
        assert "scope IN" not in sql
        assert "butler =" not in sql

    async def test_scope_shifts_limit_param_index(self, mock_pool: AsyncMock) -> None:
        """When scope is used, limit parameter index shifts to $3."""
        await keyword_search(mock_pool, "test", "facts", scope="global")
        sql = mock_pool.fetch.call_args[0][0]
        assert "LIMIT $3" in sql

    async def test_no_scope_limit_param_index(self, mock_pool: AsyncMock) -> None:
        """Without scope, limit parameter index is $2."""
        await keyword_search(mock_pool, "test", "episodes")
        sql = mock_pool.fetch.call_args[0][0]
        assert "LIMIT $2" in sql


# ---------------------------------------------------------------------------
# Tests — Table-specific filters
# ---------------------------------------------------------------------------


class TestKeywordSearchTableFilters:
    """Tests for table-specific WHERE clauses."""

    async def test_facts_filter_active_validity(self, mock_pool: AsyncMock) -> None:
        """Facts search includes validity = 'active' filter."""
        await keyword_search(mock_pool, "test", "facts")
        sql = mock_pool.fetch.call_args[0][0]
        assert "validity = 'active'" in sql

    async def test_rules_filter_not_forgotten(self, mock_pool: AsyncMock) -> None:
        """Rules search excludes forgotten rules."""
        await keyword_search(mock_pool, "test", "rules")
        sql = mock_pool.fetch.call_args[0][0]
        assert "(metadata->>'forgotten')::boolean IS NOT TRUE" in sql

    async def test_episodes_no_validity_filter(self, mock_pool: AsyncMock) -> None:
        """Episodes search does NOT include validity filter."""
        await keyword_search(mock_pool, "test", "episodes")
        sql = mock_pool.fetch.call_args[0][0]
        assert "validity" not in sql

    async def test_episodes_no_forgotten_filter(self, mock_pool: AsyncMock) -> None:
        """Episodes search does NOT include forgotten filter."""
        await keyword_search(mock_pool, "test", "episodes")
        sql = mock_pool.fetch.call_args[0][0]
        assert "forgotten" not in sql


# ---------------------------------------------------------------------------
# Tests — Result formatting
# ---------------------------------------------------------------------------


class TestKeywordSearchResults:
    """Tests for result formatting."""

    async def test_returns_list_of_dicts(self, mock_pool: AsyncMock) -> None:
        """Results are returned as a list of dicts."""
        row_data = {"id": "abc", "content": "hello", "rank": 0.5}
        mock_pool.fetch.return_value = [_make_row(row_data)]
        result = await keyword_search(mock_pool, "test", "episodes")
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], dict)

    async def test_result_contains_rank_key(self, mock_pool: AsyncMock) -> None:
        """Each result dict contains a 'rank' key from ts_rank."""
        row_data = {"id": "abc", "content": "hello", "rank": 0.75}
        mock_pool.fetch.return_value = [_make_row(row_data)]
        result = await keyword_search(mock_pool, "test", "episodes")
        assert "rank" in result[0]
        assert result[0]["rank"] == 0.75

    async def test_empty_db_results(self, mock_pool: AsyncMock) -> None:
        """Empty result set from DB returns empty list."""
        mock_pool.fetch.return_value = []
        result = await keyword_search(mock_pool, "test", "episodes")
        assert result == []

    async def test_multiple_results(self, mock_pool: AsyncMock) -> None:
        """Multiple results are all converted to dicts."""
        rows = [
            _make_row({"id": "a", "rank": 0.9}),
            _make_row({"id": "b", "rank": 0.5}),
            _make_row({"id": "c", "rank": 0.1}),
        ]
        mock_pool.fetch.return_value = rows
        result = await keyword_search(mock_pool, "test", "facts")
        assert len(result) == 3
        assert result[0]["id"] == "a"
        assert result[2]["id"] == "c"


# ---------------------------------------------------------------------------
# Tests — Module constants
# ---------------------------------------------------------------------------


class TestModuleConstants:
    """Tests for module-level constants."""

    def test_valid_tables(self) -> None:
        """_VALID_TABLES contains exactly the three memory tables."""
        assert _VALID_TABLES == {"episodes", "facts", "rules"}

    def test_scoped_tables(self) -> None:
        """_SCOPED_TABLES contains facts and rules (not episodes)."""
        assert _SCOPED_TABLES == {"facts", "rules"}

    def test_ts_config_is_english(self) -> None:
        """Text search config is 'english'."""
        assert _TS_CONFIG == "english"

    def test_preprocess_search_query_loaded(self) -> None:
        """preprocess_search_query is loaded from search_vector module."""
        # Verify the function works (it's loaded from search_vector.py)
        assert preprocess_search_query("hello world") == "hello world"
        assert preprocess_search_query("") == ""
        assert preprocess_search_query(None) == ""
