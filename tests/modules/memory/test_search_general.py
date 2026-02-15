"""Tests for the general-purpose search() function.

All tests patch the underlying search primitives (semantic_search,
keyword_search, hybrid_search) so no database is needed.
"""

from __future__ import annotations

import importlib.util
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from ._test_helpers import MEMORY_MODULE_PATH

# ---------------------------------------------------------------------------
# Load search module from disk (roster/ is not a Python package)
# ---------------------------------------------------------------------------

_SEARCH_PY = MEMORY_MODULE_PATH / "search.py"


def _load_search():
    spec = importlib.util.spec_from_file_location("search", _SEARCH_PY)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


search_mod = _load_search()
search_fn = search_mod.search

# Module-level references for patching
_MOD_PATH = search_mod.__name__


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_pool():
    """Return a mock pool (unused since we patch search primitives)."""
    return MagicMock()


def _fake_engine(embedding=None):
    """Return a mock embedding engine."""
    engine = MagicMock()
    engine.embed = MagicMock(return_value=embedding or [0.1, 0.2, 0.3])
    return engine


def _make_result(id_val: str, **extras) -> dict:
    return {"id": id_val, **extras}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_default_mode_is_hybrid():
    """Default mode should be 'hybrid', calling hybrid_search."""
    pool = _fake_pool()
    engine = _fake_engine()
    mock_hybrid = AsyncMock(return_value=[])

    with (
        patch.object(search_mod, "hybrid_search", mock_hybrid),
        patch.object(search_mod, "semantic_search", AsyncMock(return_value=[])),
        patch.object(search_mod, "keyword_search", AsyncMock(return_value=[])),
    ):
        await search_fn(pool, "test query", engine)
        assert mock_hybrid.call_count == 3  # once per type


@pytest.mark.unit
@pytest.mark.asyncio
async def test_default_types_is_all_three():
    """Default types should cover episode, fact, and rule."""
    pool = _fake_pool()
    engine = _fake_engine()
    tables_searched = []

    async def capture_table(p, q, e, table, **kw):
        tables_searched.append(table)
        return []

    with (
        patch.object(search_mod, "hybrid_search", side_effect=capture_table),
    ):
        await search_fn(pool, "test", engine)
        assert set(tables_searched) == {"episodes", "facts", "rules"}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_hybrid_mode_calls_hybrid_search_for_each_type():
    """In hybrid mode, hybrid_search should be called for each type."""
    pool = _fake_pool()
    engine = _fake_engine()
    mock_hybrid = AsyncMock(return_value=[])

    with patch.object(search_mod, "hybrid_search", mock_hybrid):
        await search_fn(pool, "q", engine, mode="hybrid")
        assert mock_hybrid.call_count == 3
        tables_called = [c.args[3] for c in mock_hybrid.call_args_list]
        assert set(tables_called) == {"episodes", "facts", "rules"}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_semantic_mode_calls_semantic_search_for_each_type():
    """In semantic mode, semantic_search should be called for each type."""
    pool = _fake_pool()
    engine = _fake_engine()
    mock_semantic = AsyncMock(return_value=[])

    with (
        patch.object(search_mod, "semantic_search", mock_semantic),
        patch.object(search_mod, "keyword_search", AsyncMock(return_value=[])),
        patch.object(search_mod, "hybrid_search", AsyncMock(return_value=[])),
    ):
        await search_fn(pool, "q", engine, mode="semantic")
        assert mock_semantic.call_count == 3
        tables_called = [c.args[2] for c in mock_semantic.call_args_list]
        assert set(tables_called) == {"episodes", "facts", "rules"}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_keyword_mode_calls_keyword_search_for_each_type():
    """In keyword mode, keyword_search should be called for each type."""
    pool = _fake_pool()
    engine = _fake_engine()
    mock_keyword = AsyncMock(return_value=[])

    with (
        patch.object(search_mod, "semantic_search", AsyncMock(return_value=[])),
        patch.object(search_mod, "keyword_search", mock_keyword),
        patch.object(search_mod, "hybrid_search", AsyncMock(return_value=[])),
    ):
        await search_fn(pool, "q", engine, mode="keyword")
        assert mock_keyword.call_count == 3
        tables_called = [c.args[2] for c in mock_keyword.call_args_list]
        assert set(tables_called) == {"episodes", "facts", "rules"}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_embeds_query_once_for_semantic_mode():
    """Embedding should happen exactly once in semantic mode."""
    pool = _fake_pool()
    engine = _fake_engine()

    with (
        patch.object(search_mod, "semantic_search", AsyncMock(return_value=[])),
    ):
        await search_fn(pool, "q", engine, mode="semantic")
        engine.embed.assert_called_once_with("q")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_embeds_query_once_for_hybrid_mode():
    """Embedding should happen exactly once in hybrid mode."""
    pool = _fake_pool()
    engine = _fake_engine()

    with (
        patch.object(search_mod, "hybrid_search", AsyncMock(return_value=[])),
    ):
        await search_fn(pool, "q", engine, mode="hybrid")
        engine.embed.assert_called_once_with("q")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_does_not_embed_for_keyword_mode():
    """Embedding should NOT happen in keyword mode."""
    pool = _fake_pool()
    engine = _fake_engine()

    with (
        patch.object(search_mod, "keyword_search", AsyncMock(return_value=[])),
    ):
        await search_fn(pool, "q", engine, mode="keyword")
        engine.embed.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_tags_results_with_memory_type():
    """Each result should have 'memory_type' set to the corresponding type."""
    pool = _fake_pool()
    engine = _fake_engine()

    async def fake_hybrid(p, q, e, table, **kw):
        if table == "episodes":
            return [_make_result("ep1", rrf_score=0.9)]
        elif table == "facts":
            return [_make_result("f1", rrf_score=0.8)]
        else:
            return [_make_result("r1", rrf_score=0.7)]

    with patch.object(search_mod, "hybrid_search", side_effect=fake_hybrid):
        results = await search_fn(pool, "q", engine)

    types_found = {r["memory_type"] for r in results}
    assert types_found == {"episode", "fact", "rule"}
    for r in results:
        if r["id"] == "ep1":
            assert r["memory_type"] == "episode"
        elif r["id"] == "f1":
            assert r["memory_type"] == "fact"
        elif r["id"] == "r1":
            assert r["memory_type"] == "rule"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_filters_by_min_confidence():
    """Results below min_confidence should be filtered out."""
    pool = _fake_pool()
    engine = _fake_engine()

    async def fake_hybrid(p, q, e, table, **kw):
        if table == "facts":
            return [
                _make_result("f1", rrf_score=0.9, confidence=0.8),
                _make_result("f2", rrf_score=0.7, confidence=0.3),
            ]
        return []

    with patch.object(search_mod, "hybrid_search", side_effect=fake_hybrid):
        results = await search_fn(pool, "q", engine, types=["fact"], min_confidence=0.5)

    assert len(results) == 1
    assert results[0]["id"] == "f1"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_invalid_mode_raises_value_error():
    """An invalid mode should raise ValueError."""
    pool = _fake_pool()
    engine = _fake_engine()

    with pytest.raises(ValueError, match="Invalid mode"):
        await search_fn(pool, "q", engine, mode="invalid")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_invalid_type_raises_value_error():
    """An invalid type should raise ValueError."""
    pool = _fake_pool()
    engine = _fake_engine()

    with pytest.raises(ValueError, match="Invalid type"):
        await search_fn(pool, "q", engine, types=["episode", "invalid_type"])


@pytest.mark.unit
@pytest.mark.asyncio
async def test_custom_types_filters_tables():
    """Specifying types=['fact'] should only search the facts table."""
    pool = _fake_pool()
    engine = _fake_engine()
    mock_hybrid = AsyncMock(return_value=[])

    with patch.object(search_mod, "hybrid_search", mock_hybrid):
        await search_fn(pool, "q", engine, types=["fact"])
        assert mock_hybrid.call_count == 1
        assert mock_hybrid.call_args.args[3] == "facts"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_scope_passed_through():
    """Scope parameter should be forwarded to the underlying search calls."""
    pool = _fake_pool()
    engine = _fake_engine()
    mock_hybrid = AsyncMock(return_value=[])

    with patch.object(search_mod, "hybrid_search", mock_hybrid):
        await search_fn(pool, "q", engine, types=["fact"], scope="butler-x")
        _, kwargs = mock_hybrid.call_args
        assert kwargs["scope"] == "butler-x"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_limit_applied_to_final_output():
    """The final output should be capped at limit."""
    pool = _fake_pool()
    engine = _fake_engine()

    async def fake_hybrid(p, q, e, table, **kw):
        # Return 5 results per table (15 total for 3 types)
        return [_make_result(f"{table}-{i}", rrf_score=0.9 - i * 0.01) for i in range(5)]

    with patch.object(search_mod, "hybrid_search", side_effect=fake_hybrid):
        results = await search_fn(pool, "q", engine, limit=3)
        assert len(results) == 3


@pytest.mark.unit
@pytest.mark.asyncio
async def test_results_sorted_by_rrf_score_in_hybrid_mode():
    """In hybrid mode, results should be sorted by rrf_score descending."""
    pool = _fake_pool()
    engine = _fake_engine()

    async def fake_hybrid(p, q, e, table, **kw):
        if table == "episodes":
            return [_make_result("ep1", rrf_score=0.5)]
        elif table == "facts":
            return [_make_result("f1", rrf_score=0.9)]
        else:
            return [_make_result("r1", rrf_score=0.7)]

    with patch.object(search_mod, "hybrid_search", side_effect=fake_hybrid):
        results = await search_fn(pool, "q", engine, mode="hybrid")

    assert results[0]["id"] == "f1"
    assert results[1]["id"] == "r1"
    assert results[2]["id"] == "ep1"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_results_sorted_by_similarity_in_semantic_mode():
    """In semantic mode, results should be sorted by similarity descending."""
    pool = _fake_pool()
    engine = _fake_engine()

    async def fake_semantic(p, emb, table, **kw):
        if table == "episodes":
            return [_make_result("ep1", similarity=0.3)]
        elif table == "facts":
            return [_make_result("f1", similarity=0.95)]
        else:
            return [_make_result("r1", similarity=0.6)]

    with patch.object(search_mod, "semantic_search", side_effect=fake_semantic):
        results = await search_fn(pool, "q", engine, mode="semantic")

    assert results[0]["id"] == "f1"
    assert results[1]["id"] == "r1"
    assert results[2]["id"] == "ep1"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_results_sorted_by_rank_in_keyword_mode():
    """In keyword mode, results should be sorted by rank descending."""
    pool = _fake_pool()
    engine = _fake_engine()

    async def fake_keyword(p, q, table, **kw):
        if table == "episodes":
            return [_make_result("ep1", rank=0.1)]
        elif table == "facts":
            return [_make_result("f1", rank=0.8)]
        else:
            return [_make_result("r1", rank=0.5)]

    with patch.object(search_mod, "keyword_search", side_effect=fake_keyword):
        results = await search_fn(pool, "q", engine, mode="keyword")

    assert results[0]["id"] == "f1"
    assert results[1]["id"] == "r1"
    assert results[2]["id"] == "ep1"
