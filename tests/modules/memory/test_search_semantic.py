"""Unit tests for semantic_search() in the Memory butler search module."""

from __future__ import annotations

import importlib.util
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from ._test_helpers import MEMORY_MODULE_PATH

# ---------------------------------------------------------------------------
# Load the search module from disk (roster/ is not a Python package).
# We must mock sentence_transformers before loading, because search.py
# imports search_vector.py (no ML dep) but sibling loads might chain.
# ---------------------------------------------------------------------------

_SEARCH_PATH = MEMORY_MODULE_PATH / "search.py"


def _load_search_module():
    """Load search.py from disk."""

    # Ensure sentence_transformers is mocked so any transitive import of
    # embedding.py does not fail.
    mock_st = MagicMock()
    mock_st.SentenceTransformer.return_value = MagicMock()
    # sys.modules.setdefault("sentence_transformers", mock_st)

    spec = importlib.util.spec_from_file_location("search", _SEARCH_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_mod = _load_search_module()
semantic_search = _mod.semantic_search
_VALID_TABLES = _mod._VALID_TABLES
_SCOPED_TABLES = _mod._SCOPED_TABLES

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAMPLE_EMBEDDING = [0.1] * 384


def _make_row(row_id: uuid.UUID | None = None, similarity: float = 0.95, **extra):
    """Create a dict that behaves like an asyncpg Record for testing.

    asyncpg Records support dict() conversion, so our mock rows are plain
    dicts wrapped in a MagicMock that supports dict() conversion.
    """
    data = {
        "id": row_id or uuid.uuid4(),
        "similarity": similarity,
        **extra,
    }

    class _FakeRecord(dict):
        """Dict subclass so dict(record) works like asyncpg."""

    return _FakeRecord(data)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_pool() -> AsyncMock:
    """Return an AsyncMock asyncpg pool with a mocked fetch method."""
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[])
    return pool


# ---------------------------------------------------------------------------
# Tests: Basic behaviour
# ---------------------------------------------------------------------------


class TestSemanticSearchBasic:
    """Basic semantic_search behaviour."""

    async def test_returns_results_ordered_by_similarity(self, mock_pool: AsyncMock) -> None:
        """Results are returned as list of dicts from pool.fetch."""
        row1 = _make_row(similarity=0.95, content="highly relevant")
        row2 = _make_row(similarity=0.80, content="somewhat relevant")
        mock_pool.fetch.return_value = [row1, row2]

        results = await semantic_search(mock_pool, _SAMPLE_EMBEDDING, "episodes")

        assert len(results) == 2
        assert results[0]["similarity"] == 0.95
        assert results[1]["similarity"] == 0.80

    async def test_empty_results_returns_empty_list(self, mock_pool: AsyncMock) -> None:
        """When no rows match, an empty list is returned."""
        mock_pool.fetch.return_value = []

        results = await semantic_search(mock_pool, _SAMPLE_EMBEDDING, "facts")

        assert results == []

    async def test_result_dicts_contain_all_keys(self, mock_pool: AsyncMock) -> None:
        """Each result dict should have all keys from the row plus similarity."""
        row = _make_row(
            similarity=0.9,
            content="test content",
            subject="test",
            predicate="is",
        )
        mock_pool.fetch.return_value = [row]

        results = await semantic_search(mock_pool, _SAMPLE_EMBEDDING, "facts")

        assert "similarity" in results[0]
        assert "content" in results[0]
        assert "subject" in results[0]


# ---------------------------------------------------------------------------
# Tests: Invalid table
# ---------------------------------------------------------------------------


class TestSemanticSearchInvalidTable:
    """Validation of table parameter."""

    async def test_invalid_table_raises_value_error(self, mock_pool: AsyncMock) -> None:
        """An unrecognised table name raises ValueError."""
        with pytest.raises(ValueError, match="Invalid table.*'bogus'"):
            await semantic_search(mock_pool, _SAMPLE_EMBEDDING, "bogus")

    async def test_invalid_table_does_not_query(self, mock_pool: AsyncMock) -> None:
        """No query should be issued when the table is invalid."""
        with pytest.raises(ValueError):
            await semantic_search(mock_pool, _SAMPLE_EMBEDDING, "users")
        mock_pool.fetch.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: Scope filtering
# ---------------------------------------------------------------------------


class TestSemanticSearchScope:
    """Scope filtering behaviour."""

    async def test_scope_applied_for_facts(self, mock_pool: AsyncMock) -> None:
        """When scope is given for facts, the SQL includes global+scope condition."""
        mock_pool.fetch.return_value = []

        await semantic_search(mock_pool, _SAMPLE_EMBEDDING, "facts", scope="butler-a")

        sql = mock_pool.fetch.call_args[0][0]
        assert "scope IN ('global', $2)" in sql
        # scope value passed as parameter
        args = mock_pool.fetch.call_args[0]
        assert "butler-a" in args

    async def test_scope_applied_for_rules(self, mock_pool: AsyncMock) -> None:
        """When scope is given for rules, the SQL includes global+scope condition."""
        mock_pool.fetch.return_value = []

        await semantic_search(mock_pool, _SAMPLE_EMBEDDING, "rules", scope="butler-b")

        sql = mock_pool.fetch.call_args[0][0]
        assert "scope IN ('global', $2)" in sql
        args = mock_pool.fetch.call_args[0]
        assert "butler-b" in args

    async def test_scope_episodes_uses_butler_column(self, mock_pool: AsyncMock) -> None:
        """Episodes use butler column for scope filtering, not scope column."""
        mock_pool.fetch.return_value = []

        await semantic_search(mock_pool, _SAMPLE_EMBEDDING, "episodes", scope="butler-c")

        sql = mock_pool.fetch.call_args[0][0]
        assert "butler = $2" in sql
        assert "scope" not in sql

    async def test_no_scope_no_scope_filter(self, mock_pool: AsyncMock) -> None:
        """When scope is None, no scope condition appears in the SQL."""
        mock_pool.fetch.return_value = []

        await semantic_search(mock_pool, _SAMPLE_EMBEDDING, "facts", scope=None)

        sql = mock_pool.fetch.call_args[0][0]
        assert "scope =" not in sql


# ---------------------------------------------------------------------------
# Tests: Table-specific filters
# ---------------------------------------------------------------------------


class TestSemanticSearchTableFilters:
    """Table-specific WHERE clause conditions."""

    async def test_facts_filter_active_validity(self, mock_pool: AsyncMock) -> None:
        """Facts queries include a validity = 'active' condition."""
        mock_pool.fetch.return_value = []

        await semantic_search(mock_pool, _SAMPLE_EMBEDDING, "facts")

        sql = mock_pool.fetch.call_args[0][0]
        assert "validity = 'active'" in sql

    async def test_rules_filter_forgotten(self, mock_pool: AsyncMock) -> None:
        """Rules queries exclude forgotten rules."""
        mock_pool.fetch.return_value = []

        await semantic_search(mock_pool, _SAMPLE_EMBEDDING, "rules")

        sql = mock_pool.fetch.call_args[0][0]
        assert "forgotten" in sql
        assert "IS NOT TRUE" in sql

    async def test_episodes_no_extra_filters(self, mock_pool: AsyncMock) -> None:
        """Episodes queries have no validity or forgotten filter."""
        mock_pool.fetch.return_value = []

        await semantic_search(mock_pool, _SAMPLE_EMBEDDING, "episodes")

        sql = mock_pool.fetch.call_args[0][0]
        assert "validity" not in sql
        assert "forgotten" not in sql


# ---------------------------------------------------------------------------
# Tests: Limit parameter
# ---------------------------------------------------------------------------


class TestSemanticSearchLimit:
    """Limit parameter handling."""

    async def test_default_limit_is_10(self, mock_pool: AsyncMock) -> None:
        """When no limit is given, 10 is used."""
        mock_pool.fetch.return_value = []

        await semantic_search(mock_pool, _SAMPLE_EMBEDDING, "episodes")

        args = mock_pool.fetch.call_args[0]
        # The last positional arg should be the limit value (10).
        assert args[-1] == 10

    async def test_custom_limit_passed_through(self, mock_pool: AsyncMock) -> None:
        """A custom limit is passed as a query parameter."""
        mock_pool.fetch.return_value = []

        await semantic_search(mock_pool, _SAMPLE_EMBEDDING, "episodes", limit=5)

        args = mock_pool.fetch.call_args[0]
        assert args[-1] == 5


# ---------------------------------------------------------------------------
# Tests: Embedding parameter
# ---------------------------------------------------------------------------


class TestSemanticSearchEmbedding:
    """Embedding vector parameter handling."""

    async def test_embedding_passed_as_string(self, mock_pool: AsyncMock) -> None:
        """The embedding is converted to its string representation for pgvector."""
        mock_pool.fetch.return_value = []

        await semantic_search(mock_pool, _SAMPLE_EMBEDDING, "episodes")

        args = mock_pool.fetch.call_args[0]
        # $1 parameter (second positional arg, first after sql) is the embedding string.
        embedding_param = args[1]
        assert embedding_param == str(_SAMPLE_EMBEDDING)

    async def test_sql_uses_cosine_distance(self, mock_pool: AsyncMock) -> None:
        """The SQL uses pgvector's <=> cosine distance operator."""
        mock_pool.fetch.return_value = []

        await semantic_search(mock_pool, _SAMPLE_EMBEDDING, "facts")

        sql = mock_pool.fetch.call_args[0][0]
        assert "<=>" in sql

    async def test_similarity_computed_as_one_minus_distance(
        self,
        mock_pool: AsyncMock,
    ) -> None:
        """The SQL computes similarity as 1 - cosine_distance."""
        mock_pool.fetch.return_value = []

        await semantic_search(mock_pool, _SAMPLE_EMBEDDING, "facts")

        sql = mock_pool.fetch.call_args[0][0]
        assert "1 - (embedding <=> $1)" in sql


# ---------------------------------------------------------------------------
# Tests: SQL structure
# ---------------------------------------------------------------------------


class TestSemanticSearchSQL:
    """Verify generated SQL structure."""

    async def test_orders_by_cosine_distance_ascending(
        self,
        mock_pool: AsyncMock,
    ) -> None:
        """Results are ordered by cosine distance ascending (closest first)."""
        mock_pool.fetch.return_value = []

        await semantic_search(mock_pool, _SAMPLE_EMBEDDING, "facts")

        sql = mock_pool.fetch.call_args[0][0]
        assert "ORDER BY embedding <=> $1" in sql

    async def test_selects_from_correct_table(self, mock_pool: AsyncMock) -> None:
        """The FROM clause references the requested table."""
        for table in ("episodes", "facts", "rules"):
            mock_pool.fetch.return_value = []
            await semantic_search(mock_pool, _SAMPLE_EMBEDDING, table)
            sql = mock_pool.fetch.call_args[0][0]
            assert f"FROM {table}" in sql

    async def test_param_indices_with_scope(self, mock_pool: AsyncMock) -> None:
        """When scope is provided, parameter indices are correct."""
        mock_pool.fetch.return_value = []

        await semantic_search(mock_pool, _SAMPLE_EMBEDDING, "facts", scope="global", limit=20)

        args = mock_pool.fetch.call_args[0]
        sql = args[0]
        # $1 = embedding, $2 = scope, $3 = limit
        assert "$1" in sql
        assert "$2" in sql
        assert "$3" in sql
        assert args[1] == str(_SAMPLE_EMBEDDING)  # $1
        assert args[2] == "global"  # $2
        assert args[3] == 20  # $3

    async def test_param_indices_without_scope(self, mock_pool: AsyncMock) -> None:
        """When no scope, parameter indices skip the scope param."""
        mock_pool.fetch.return_value = []

        await semantic_search(mock_pool, _SAMPLE_EMBEDDING, "episodes", limit=3)

        args = mock_pool.fetch.call_args[0]
        sql = args[0]
        # $1 = embedding, $2 = limit (no scope for episodes)
        assert "LIMIT $2" in sql
        assert args[1] == str(_SAMPLE_EMBEDDING)
        assert args[2] == 3
