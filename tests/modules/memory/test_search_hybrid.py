"""Unit tests for hybrid_search (RRF fusion) in search.py."""

from __future__ import annotations

import importlib.util
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from ._test_helpers import MEMORY_MODULE_PATH

# ---------------------------------------------------------------------------
# Load the search module from disk (roster/ is not a Python package).
# ---------------------------------------------------------------------------

_SEARCH_PATH = MEMORY_MODULE_PATH / "search.py"


def _load_search_module():
    spec = importlib.util.spec_from_file_location("search", _SEARCH_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_mod = _load_search_module()
hybrid_search = _mod.hybrid_search
semantic_search = _mod.semantic_search
keyword_search = _mod.keyword_search
_RRF_K = _mod._RRF_K
_VALID_TABLES = _mod._VALID_TABLES

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DUMMY_EMBEDDING = [0.1] * 384


def _make_row(id_: uuid.UUID, **extra) -> dict:
    """Build a minimal row dict with an id and optional extra fields."""
    return {"id": id_, "content": f"content-{id_}", **extra}


def _rrf(s_rank: int, k_rank: int) -> float:
    """Compute expected RRF score."""
    return 1.0 / (_RRF_K + s_rank) + 1.0 / (_RRF_K + k_rank)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestHybridSearch:
    """Tests for hybrid_search() RRF fusion logic."""

    async def test_calls_both_searches(self) -> None:
        """hybrid_search invokes both semantic_search and keyword_search."""
        pool = AsyncMock()
        sem_mock = AsyncMock(return_value=[])
        kw_mock = AsyncMock(return_value=[])

        with (
            patch.object(_mod, "semantic_search", sem_mock),
            patch.object(_mod, "keyword_search", kw_mock),
        ):
            await hybrid_search(pool, "test query", _DUMMY_EMBEDDING, "facts")

        sem_mock.assert_awaited_once_with(
            pool,
            _DUMMY_EMBEDDING,
            "facts",
            limit=10,
            scope=None,
        )
        kw_mock.assert_awaited_once_with(
            pool,
            "test query",
            "facts",
            limit=10,
            scope=None,
        )

    async def test_rrf_score_overlapping_results(self) -> None:
        """Results in both lists get correct RRF score from both ranks."""
        id_a = uuid.uuid4()
        id_b = uuid.uuid4()

        sem_results = [_make_row(id_a, similarity=0.9), _make_row(id_b, similarity=0.7)]
        kw_results = [_make_row(id_b, rank=0.8), _make_row(id_a, rank=0.5)]

        pool = AsyncMock()
        with (
            patch.object(_mod, "semantic_search", AsyncMock(return_value=sem_results)),
            patch.object(_mod, "keyword_search", AsyncMock(return_value=kw_results)),
        ):
            results = await hybrid_search(pool, "q", _DUMMY_EMBEDDING, "facts", limit=10)

        scores = {r["id"]: r for r in results}

        # id_a: semantic_rank=1, keyword_rank=2
        assert scores[id_a]["semantic_rank"] == 1
        assert scores[id_a]["keyword_rank"] == 2
        assert scores[id_a]["rrf_score"] == pytest.approx(_rrf(1, 2))

        # id_b: semantic_rank=2, keyword_rank=1
        assert scores[id_b]["semantic_rank"] == 2
        assert scores[id_b]["keyword_rank"] == 1
        assert scores[id_b]["rrf_score"] == pytest.approx(_rrf(2, 1))

    async def test_single_list_uses_default_rank(self) -> None:
        """Results in only one list use limit+1 as default rank for the other."""
        id_sem = uuid.uuid4()
        id_kw = uuid.uuid4()
        limit = 5

        sem_results = [_make_row(id_sem, similarity=0.9)]
        kw_results = [_make_row(id_kw, rank=0.8)]

        pool = AsyncMock()
        with (
            patch.object(_mod, "semantic_search", AsyncMock(return_value=sem_results)),
            patch.object(_mod, "keyword_search", AsyncMock(return_value=kw_results)),
        ):
            results = await hybrid_search(
                pool,
                "q",
                _DUMMY_EMBEDDING,
                "facts",
                limit=limit,
            )

        scores = {r["id"]: r for r in results}
        default_rank = limit + 1  # 6

        # id_sem: semantic_rank=1, keyword_rank=6 (default)
        assert scores[id_sem]["semantic_rank"] == 1
        assert scores[id_sem]["keyword_rank"] == default_rank
        assert scores[id_sem]["rrf_score"] == pytest.approx(_rrf(1, default_rank))

        # id_kw: semantic_rank=6 (default), keyword_rank=1
        assert scores[id_kw]["semantic_rank"] == default_rank
        assert scores[id_kw]["keyword_rank"] == 1
        assert scores[id_kw]["rrf_score"] == pytest.approx(_rrf(default_rank, 1))

    async def test_sorted_by_rrf_score_descending(self) -> None:
        """Results are sorted by rrf_score descending."""
        ids = [uuid.uuid4() for _ in range(4)]

        # Semantic returns: ids[0], ids[1], ids[2], ids[3] (rank 1-4)
        sem_results = [_make_row(ids[i], similarity=0.9 - i * 0.1) for i in range(4)]
        # Keyword returns: ids[3], ids[2], ids[1], ids[0] (rank 1-4)
        kw_results = [_make_row(ids[3 - i], rank=0.9 - i * 0.1) for i in range(4)]

        pool = AsyncMock()
        with (
            patch.object(_mod, "semantic_search", AsyncMock(return_value=sem_results)),
            patch.object(_mod, "keyword_search", AsyncMock(return_value=kw_results)),
        ):
            results = await hybrid_search(pool, "q", _DUMMY_EMBEDDING, "facts", limit=10)

        rrf_scores = [r["rrf_score"] for r in results]
        assert rrf_scores == sorted(rrf_scores, reverse=True)

    async def test_limit_applied_to_output(self) -> None:
        """Output is truncated to the limit parameter."""
        ids = [uuid.uuid4() for _ in range(5)]

        sem_results = [_make_row(ids[i], similarity=0.9 - i * 0.1) for i in range(5)]
        kw_results = [_make_row(ids[i], rank=0.9 - i * 0.1) for i in range(5)]

        pool = AsyncMock()
        with (
            patch.object(_mod, "semantic_search", AsyncMock(return_value=sem_results)),
            patch.object(_mod, "keyword_search", AsyncMock(return_value=kw_results)),
        ):
            results = await hybrid_search(pool, "q", _DUMMY_EMBEDDING, "facts", limit=3)

        assert len(results) <= 3

    async def test_invalid_table_raises_value_error(self) -> None:
        """Passing an invalid table name raises ValueError."""
        pool = AsyncMock()
        with pytest.raises(ValueError, match="Invalid table"):
            await hybrid_search(pool, "q", _DUMMY_EMBEDDING, "invalid_table")

    async def test_empty_semantic_nonempty_keyword(self) -> None:
        """Works when semantic returns nothing but keyword returns results."""
        id_kw = uuid.uuid4()

        pool = AsyncMock()
        with (
            patch.object(_mod, "semantic_search", AsyncMock(return_value=[])),
            patch.object(
                _mod,
                "keyword_search",
                AsyncMock(return_value=[_make_row(id_kw, rank=0.8)]),
            ),
        ):
            results = await hybrid_search(pool, "q", _DUMMY_EMBEDDING, "facts", limit=5)

        assert len(results) == 1
        assert results[0]["id"] == id_kw
        assert results[0]["semantic_rank"] == 6  # default = limit + 1
        assert results[0]["keyword_rank"] == 1

    async def test_nonempty_semantic_empty_keyword(self) -> None:
        """Works when keyword returns nothing but semantic returns results."""
        id_sem = uuid.uuid4()

        pool = AsyncMock()
        with (
            patch.object(
                _mod,
                "semantic_search",
                AsyncMock(return_value=[_make_row(id_sem, similarity=0.9)]),
            ),
            patch.object(_mod, "keyword_search", AsyncMock(return_value=[])),
        ):
            results = await hybrid_search(pool, "q", _DUMMY_EMBEDDING, "facts", limit=5)

        assert len(results) == 1
        assert results[0]["id"] == id_sem
        assert results[0]["semantic_rank"] == 1
        assert results[0]["keyword_rank"] == 6  # default = limit + 1

    async def test_both_empty_returns_empty(self) -> None:
        """When both searches return empty, hybrid returns empty list."""
        pool = AsyncMock()
        with (
            patch.object(_mod, "semantic_search", AsyncMock(return_value=[])),
            patch.object(_mod, "keyword_search", AsyncMock(return_value=[])),
        ):
            results = await hybrid_search(pool, "q", _DUMMY_EMBEDDING, "facts")

        assert results == []

    async def test_scope_passed_through(self) -> None:
        """Scope parameter is forwarded to both sub-searches."""
        pool = AsyncMock()
        sem_mock = AsyncMock(return_value=[])
        kw_mock = AsyncMock(return_value=[])

        with (
            patch.object(_mod, "semantic_search", sem_mock),
            patch.object(_mod, "keyword_search", kw_mock),
        ):
            await hybrid_search(
                pool,
                "q",
                _DUMMY_EMBEDDING,
                "episodes",
                limit=20,
                scope="my-butler",
            )

        sem_mock.assert_awaited_once_with(
            pool,
            _DUMMY_EMBEDDING,
            "episodes",
            limit=20,
            scope="my-butler",
        )
        kw_mock.assert_awaited_once_with(
            pool,
            "q",
            "episodes",
            limit=20,
            scope="my-butler",
        )

    async def test_output_contains_rank_fields(self) -> None:
        """Each result dict contains rrf_score, semantic_rank, keyword_rank."""
        id_a = uuid.uuid4()

        pool = AsyncMock()
        with (
            patch.object(
                _mod,
                "semantic_search",
                AsyncMock(return_value=[_make_row(id_a, similarity=0.9)]),
            ),
            patch.object(
                _mod,
                "keyword_search",
                AsyncMock(return_value=[_make_row(id_a, rank=0.8)]),
            ),
        ):
            results = await hybrid_search(pool, "q", _DUMMY_EMBEDDING, "facts")

        assert len(results) == 1
        r = results[0]
        assert "rrf_score" in r
        assert "semantic_rank" in r
        assert "keyword_rank" in r
        assert isinstance(r["rrf_score"], float)
        assert isinstance(r["semantic_rank"], int)
        assert isinstance(r["keyword_rank"], int)

    async def test_rrf_k_constant_is_60(self) -> None:
        """The RRF constant k is 60 as specified."""
        assert _RRF_K == 60

    async def test_prefers_semantic_data_for_overlapping(self) -> None:
        """When a result appears in both lists, row data comes from semantic."""
        id_a = uuid.uuid4()

        sem_row = _make_row(id_a, similarity=0.95, source="semantic")
        kw_row = _make_row(id_a, rank=0.8, source="keyword")

        pool = AsyncMock()
        with (
            patch.object(_mod, "semantic_search", AsyncMock(return_value=[sem_row])),
            patch.object(_mod, "keyword_search", AsyncMock(return_value=[kw_row])),
        ):
            results = await hybrid_search(pool, "q", _DUMMY_EMBEDDING, "facts")

        assert results[0]["source"] == "semantic"

    async def test_all_valid_tables_accepted(self) -> None:
        """hybrid_search accepts all valid table names without error."""
        pool = AsyncMock()
        for table in sorted(_VALID_TABLES):
            with (
                patch.object(_mod, "semantic_search", AsyncMock(return_value=[])),
                patch.object(_mod, "keyword_search", AsyncMock(return_value=[])),
            ):
                result = await hybrid_search(pool, "q", _DUMMY_EMBEDDING, table)
                assert result == []


class TestSemanticSearch:
    """Basic validation tests for semantic_search."""

    async def test_invalid_table_raises(self) -> None:
        pool = AsyncMock()
        with pytest.raises(ValueError, match="Invalid table"):
            await semantic_search(pool, _DUMMY_EMBEDDING, "bad_table")


class TestKeywordSearch:
    """Basic validation tests for keyword_search."""

    async def test_invalid_table_raises(self) -> None:
        pool = AsyncMock()
        with pytest.raises(ValueError, match="Invalid table"):
            await keyword_search(pool, "query", "bad_table")

    async def test_empty_query_returns_empty(self) -> None:
        pool = AsyncMock()
        result = await keyword_search(pool, "", "facts")
        assert result == []
        pool.fetch.assert_not_awaited()

    async def test_none_query_returns_empty(self) -> None:
        pool = AsyncMock()
        result = await keyword_search(pool, None, "facts")
        assert result == []
        pool.fetch.assert_not_awaited()
