"""Unit tests for scope filtering in the Memory Butler search module.

Verifies that:
- scope=None returns all results (no scope filter applied)
- scope="butler-x" generates `scope IN ('global', 'butler-x')` for facts/rules
- scope="butler-x" generates `butler = 'butler-x'` for episodes
- The scope SQL is correct for each of: semantic_search, keyword_search,
  hybrid_search, and the general search() function.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Load the search module from disk (roster/ is not a Python package).
# ---------------------------------------------------------------------------

_SEARCH_PATH = Path(__file__).resolve().parent.parent / "search.py"


def _load_search_module():
    """Load search.py from disk with sentence_transformers mocked."""
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
keyword_search = _mod.keyword_search
hybrid_search = _mod.hybrid_search
search_fn = _mod.search

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAMPLE_EMBEDDING = [0.1] * 384


@pytest.fixture()
def mock_pool() -> AsyncMock:
    """Return an AsyncMock asyncpg pool with a mocked fetch method."""
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[])
    return pool


def _fake_engine(embedding=None):
    """Return a mock embedding engine."""
    engine = MagicMock()
    engine.embed = MagicMock(return_value=embedding or _SAMPLE_EMBEDDING)
    return engine


# ===================================================================
# semantic_search scope filtering
# ===================================================================


class TestSemanticSearchScopeFiltering:
    """Scope filtering in semantic_search()."""

    async def test_no_scope_no_filter_facts(self, mock_pool: AsyncMock) -> None:
        """scope=None on facts produces no scope or butler condition."""
        await semantic_search(mock_pool, _SAMPLE_EMBEDDING, "facts", scope=None)
        sql = mock_pool.fetch.call_args[0][0]
        assert "scope" not in sql.lower().split("from")[1].split("order")[0].replace(
            "similarity", ""
        ) or "scope IN" not in sql
        # More precise: no scope IN and no butler = in WHERE
        assert "scope IN" not in sql
        assert "butler =" not in sql

    async def test_no_scope_no_filter_episodes(self, mock_pool: AsyncMock) -> None:
        """scope=None on episodes produces no butler condition."""
        await semantic_search(mock_pool, _SAMPLE_EMBEDDING, "episodes", scope=None)
        sql = mock_pool.fetch.call_args[0][0]
        assert "butler =" not in sql
        assert "scope IN" not in sql

    async def test_no_scope_no_filter_rules(self, mock_pool: AsyncMock) -> None:
        """scope=None on rules produces no scope condition."""
        await semantic_search(mock_pool, _SAMPLE_EMBEDDING, "rules", scope=None)
        sql = mock_pool.fetch.call_args[0][0]
        assert "scope IN" not in sql
        assert "butler =" not in sql

    async def test_scope_facts_uses_in_global(self, mock_pool: AsyncMock) -> None:
        """scope='butler-x' on facts generates scope IN ('global', $N)."""
        await semantic_search(
            mock_pool, _SAMPLE_EMBEDDING, "facts", scope="butler-x"
        )
        sql = mock_pool.fetch.call_args[0][0]
        assert "scope IN ('global', $2)" in sql
        args = mock_pool.fetch.call_args[0]
        assert "butler-x" in args

    async def test_scope_rules_uses_in_global(self, mock_pool: AsyncMock) -> None:
        """scope='butler-x' on rules generates scope IN ('global', $N)."""
        await semantic_search(
            mock_pool, _SAMPLE_EMBEDDING, "rules", scope="butler-x"
        )
        sql = mock_pool.fetch.call_args[0][0]
        assert "scope IN ('global', $2)" in sql
        args = mock_pool.fetch.call_args[0]
        assert "butler-x" in args

    async def test_scope_episodes_uses_butler_column(self, mock_pool: AsyncMock) -> None:
        """scope='butler-x' on episodes generates butler = $N."""
        await semantic_search(
            mock_pool, _SAMPLE_EMBEDDING, "episodes", scope="butler-x"
        )
        sql = mock_pool.fetch.call_args[0][0]
        assert "butler = $2" in sql
        assert "scope" not in sql
        args = mock_pool.fetch.call_args[0]
        assert "butler-x" in args

    async def test_scope_episodes_param_index(self, mock_pool: AsyncMock) -> None:
        """Episodes with scope: $1=embedding, $2=butler, $3=limit."""
        await semantic_search(
            mock_pool, _SAMPLE_EMBEDDING, "episodes", scope="butler-x", limit=5
        )
        args = mock_pool.fetch.call_args[0]
        sql = args[0]
        assert "$1" in sql
        assert "butler = $2" in sql
        assert "LIMIT $3" in sql
        assert args[1] == str(_SAMPLE_EMBEDDING)  # $1
        assert args[2] == "butler-x"  # $2
        assert args[3] == 5  # $3

    async def test_scope_facts_param_index(self, mock_pool: AsyncMock) -> None:
        """Facts with scope: $1=embedding, $2=scope, $3=limit."""
        await semantic_search(
            mock_pool, _SAMPLE_EMBEDDING, "facts", scope="butler-x", limit=7
        )
        args = mock_pool.fetch.call_args[0]
        sql = args[0]
        assert "scope IN ('global', $2)" in sql
        assert "LIMIT $3" in sql
        assert args[1] == str(_SAMPLE_EMBEDDING)  # $1
        assert args[2] == "butler-x"  # $2
        assert args[3] == 7  # $3


# ===================================================================
# keyword_search scope filtering
# ===================================================================


class TestKeywordSearchScopeFiltering:
    """Scope filtering in keyword_search()."""

    async def test_no_scope_no_filter_facts(self, mock_pool: AsyncMock) -> None:
        """scope=None on facts produces no scope or butler condition."""
        await keyword_search(mock_pool, "test", "facts", scope=None)
        sql = mock_pool.fetch.call_args[0][0]
        assert "scope IN" not in sql
        assert "butler =" not in sql

    async def test_no_scope_no_filter_episodes(self, mock_pool: AsyncMock) -> None:
        """scope=None on episodes produces no butler condition."""
        await keyword_search(mock_pool, "test", "episodes", scope=None)
        sql = mock_pool.fetch.call_args[0][0]
        assert "butler =" not in sql
        assert "scope IN" not in sql

    async def test_no_scope_no_filter_rules(self, mock_pool: AsyncMock) -> None:
        """scope=None on rules produces no scope condition."""
        await keyword_search(mock_pool, "test", "rules", scope=None)
        sql = mock_pool.fetch.call_args[0][0]
        assert "scope IN" not in sql
        assert "butler =" not in sql

    async def test_scope_facts_uses_in_global(self, mock_pool: AsyncMock) -> None:
        """scope='butler-x' on facts generates scope IN ('global', $N)."""
        await keyword_search(mock_pool, "test", "facts", scope="butler-x")
        sql = mock_pool.fetch.call_args[0][0]
        assert "scope IN ('global', $2)" in sql
        args = mock_pool.fetch.call_args[0]
        assert args[2] == "butler-x"

    async def test_scope_rules_uses_in_global(self, mock_pool: AsyncMock) -> None:
        """scope='butler-x' on rules generates scope IN ('global', $N)."""
        await keyword_search(mock_pool, "test", "rules", scope="butler-x")
        sql = mock_pool.fetch.call_args[0][0]
        assert "scope IN ('global', $2)" in sql
        args = mock_pool.fetch.call_args[0]
        assert args[2] == "butler-x"

    async def test_scope_episodes_uses_butler_column(self, mock_pool: AsyncMock) -> None:
        """scope='butler-x' on episodes generates butler = $N."""
        await keyword_search(mock_pool, "test", "episodes", scope="butler-x")
        sql = mock_pool.fetch.call_args[0][0]
        assert "butler = $2" in sql
        assert "scope" not in sql
        args = mock_pool.fetch.call_args[0]
        assert args[2] == "butler-x"

    async def test_scope_episodes_param_index(self, mock_pool: AsyncMock) -> None:
        """Episodes with scope: $1=query, $2=butler, $3=limit."""
        await keyword_search(
            mock_pool, "test", "episodes", scope="butler-x", limit=5
        )
        args = mock_pool.fetch.call_args[0]
        sql = args[0]
        assert "butler = $2" in sql
        assert "LIMIT $3" in sql
        assert args[1] == "test"  # $1
        assert args[2] == "butler-x"  # $2
        assert args[3] == 5  # $3

    async def test_scope_facts_shifts_limit_param(self, mock_pool: AsyncMock) -> None:
        """Facts with scope: limit shifts to $3."""
        await keyword_search(mock_pool, "test", "facts", scope="butler-x")
        sql = mock_pool.fetch.call_args[0][0]
        assert "LIMIT $3" in sql

    async def test_no_scope_episodes_limit_at_dollar2(self, mock_pool: AsyncMock) -> None:
        """Episodes without scope: limit is $2."""
        await keyword_search(mock_pool, "test", "episodes", scope=None)
        sql = mock_pool.fetch.call_args[0][0]
        assert "LIMIT $2" in sql


# ===================================================================
# hybrid_search scope filtering (delegates to semantic + keyword)
# ===================================================================


class TestHybridSearchScopeFiltering:
    """Scope filtering in hybrid_search() — verifies scope is passed through."""

    async def test_scope_passed_to_semantic_search(self, mock_pool: AsyncMock) -> None:
        """hybrid_search passes scope to semantic_search."""
        mock_pool.fetch.return_value = []

        with (
            patch.object(
                _mod, "semantic_search", new_callable=AsyncMock, return_value=[]
            ) as mock_sem,
            patch.object(
                _mod, "keyword_search", new_callable=AsyncMock, return_value=[]
            ),
        ):
            await hybrid_search(
                mock_pool, "test", _SAMPLE_EMBEDDING, "facts", scope="butler-x"
            )
            _, kwargs = mock_sem.call_args
            assert kwargs["scope"] == "butler-x"

    async def test_scope_passed_to_keyword_search(self, mock_pool: AsyncMock) -> None:
        """hybrid_search passes scope to keyword_search."""
        mock_pool.fetch.return_value = []

        with (
            patch.object(
                _mod, "semantic_search", new_callable=AsyncMock, return_value=[]
            ),
            patch.object(
                _mod, "keyword_search", new_callable=AsyncMock, return_value=[]
            ) as mock_kw,
        ):
            await hybrid_search(
                mock_pool, "test", _SAMPLE_EMBEDDING, "facts", scope="butler-x"
            )
            _, kwargs = mock_kw.call_args
            assert kwargs["scope"] == "butler-x"

    async def test_no_scope_passed_as_none(self, mock_pool: AsyncMock) -> None:
        """hybrid_search passes scope=None when not specified."""
        mock_pool.fetch.return_value = []

        with (
            patch.object(
                _mod, "semantic_search", new_callable=AsyncMock, return_value=[]
            ) as mock_sem,
            patch.object(
                _mod, "keyword_search", new_callable=AsyncMock, return_value=[]
            ) as mock_kw,
        ):
            await hybrid_search(
                mock_pool, "test", _SAMPLE_EMBEDDING, "facts"
            )
            _, sem_kwargs = mock_sem.call_args
            _, kw_kwargs = mock_kw.call_args
            assert sem_kwargs["scope"] is None
            assert kw_kwargs["scope"] is None


# ===================================================================
# General search() scope filtering
# ===================================================================


class TestSearchScopeFiltering:
    """Scope filtering in the general search() function."""

    async def test_scope_passed_to_hybrid_search(self) -> None:
        """search() passes scope to hybrid_search for each type."""
        pool = MagicMock()
        engine = _fake_engine()
        mock_hybrid = AsyncMock(return_value=[])

        with patch.object(_mod, "hybrid_search", mock_hybrid):
            await search_fn(pool, "q", engine, scope="butler-x")

        for call in mock_hybrid.call_args_list:
            _, kwargs = call
            assert kwargs["scope"] == "butler-x"

    async def test_scope_passed_to_semantic_search(self) -> None:
        """search() passes scope to semantic_search in semantic mode."""
        pool = MagicMock()
        engine = _fake_engine()
        mock_semantic = AsyncMock(return_value=[])

        with patch.object(_mod, "semantic_search", mock_semantic):
            await search_fn(pool, "q", engine, scope="butler-x", mode="semantic")

        for call in mock_semantic.call_args_list:
            _, kwargs = call
            assert kwargs["scope"] == "butler-x"

    async def test_scope_passed_to_keyword_search(self) -> None:
        """search() passes scope to keyword_search in keyword mode."""
        pool = MagicMock()
        engine = _fake_engine()
        mock_keyword = AsyncMock(return_value=[])

        with patch.object(_mod, "keyword_search", mock_keyword):
            await search_fn(pool, "q", engine, scope="butler-x", mode="keyword")

        for call in mock_keyword.call_args_list:
            _, kwargs = call
            assert kwargs["scope"] == "butler-x"

    async def test_no_scope_passed_as_none(self) -> None:
        """search() passes scope=None to underlying search when not specified."""
        pool = MagicMock()
        engine = _fake_engine()
        mock_hybrid = AsyncMock(return_value=[])

        with patch.object(_mod, "hybrid_search", mock_hybrid):
            await search_fn(pool, "q", engine)

        for call in mock_hybrid.call_args_list:
            _, kwargs = call
            assert kwargs["scope"] is None

    async def test_scope_applied_across_all_types(self) -> None:
        """search() applies scope to all three table types."""
        pool = MagicMock()
        engine = _fake_engine()
        tables_and_scopes: list[tuple[str, str | None]] = []

        async def capture(p, q, e, table, **kw):
            tables_and_scopes.append((table, kw.get("scope")))
            return []

        with patch.object(_mod, "hybrid_search", side_effect=capture):
            await search_fn(pool, "q", engine, scope="butler-x")

        assert len(tables_and_scopes) == 3
        for table, scope in tables_and_scopes:
            assert scope == "butler-x"
        tables = {t for t, _ in tables_and_scopes}
        assert tables == {"episodes", "facts", "rules"}
