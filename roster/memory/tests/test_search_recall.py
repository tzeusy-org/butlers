"""Unit tests for the recall() high-level retrieval function in search.py."""

from __future__ import annotations

import importlib.util
import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Load the search module from disk (roster/ is not a Python package).
# ---------------------------------------------------------------------------

_SEARCH_PATH = Path(__file__).resolve().parent.parent / "search.py"


def _load_search_module():
    spec = importlib.util.spec_from_file_location("search", _SEARCH_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_mod = _load_search_module()
recall = _mod.recall
CompositeWeights = _mod.CompositeWeights
_RRF_K = _mod._RRF_K

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DUMMY_EMBEDDING = [0.1] * 384

# Max possible RRF for rank 1 in both lists
_MAX_RRF = 2.0 / (_RRF_K + 1)


def _make_fact(
    id_: uuid.UUID | None = None,
    *,
    rrf_score: float = 0.02,
    importance: float = 5.0,
    confidence: float = 0.8,
    last_referenced_at: datetime | None = None,
    **extra,
) -> dict:
    """Build a minimal fact-like row from hybrid_search."""
    if id_ is None:
        id_ = uuid.uuid4()
    return {
        "id": id_,
        "content": f"fact-{id_}",
        "rrf_score": rrf_score,
        "importance": importance,
        "confidence": confidence,
        "last_referenced_at": last_referenced_at,
        **extra,
    }


def _make_rule(
    id_: uuid.UUID | None = None,
    *,
    rrf_score: float = 0.015,
    importance: float = 5.0,
    confidence: float = 0.7,
    last_referenced_at: datetime | None = None,
    **extra,
) -> dict:
    """Build a minimal rule-like row from hybrid_search."""
    if id_ is None:
        id_ = uuid.uuid4()
    return {
        "id": id_,
        "content": f"rule-{id_}",
        "rrf_score": rrf_score,
        "importance": importance,
        "confidence": confidence,
        "last_referenced_at": last_referenced_at,
        **extra,
    }


def _make_engine(embedding: list[float] | None = None) -> MagicMock:
    """Create a mock EmbeddingEngine."""
    engine = MagicMock()
    engine.embed.return_value = embedding or _DUMMY_EMBEDDING
    return engine


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRecall:
    """Tests for recall() composite retrieval."""

    async def test_embeds_topic_text(self) -> None:
        """recall() calls embedding_engine.embed(topic)."""
        pool = AsyncMock()
        engine = _make_engine()

        with patch.object(_mod, "hybrid_search", AsyncMock(return_value=[])):
            await recall(pool, "my topic", engine)

        engine.embed.assert_called_once_with("my topic")

    async def test_calls_hybrid_search_for_facts_and_rules(self) -> None:
        """recall() calls hybrid_search for both 'facts' and 'rules'."""
        pool = AsyncMock()
        engine = _make_engine()
        hs_mock = AsyncMock(return_value=[])

        with patch.object(_mod, "hybrid_search", hs_mock):
            await recall(pool, "topic", engine, limit=5, scope="butler-a")

        assert hs_mock.await_count == 2
        hs_mock.assert_any_await(
            pool,
            "topic",
            _DUMMY_EMBEDDING,
            "facts",
            limit=5,
            scope="butler-a",
        )
        hs_mock.assert_any_await(
            pool,
            "topic",
            _DUMMY_EMBEDDING,
            "rules",
            limit=5,
            scope="butler-a",
        )

    async def test_tags_results_with_memory_type(self) -> None:
        """Facts get memory_type='fact', rules get memory_type='rule'."""
        pool = AsyncMock()
        engine = _make_engine()

        fact = _make_fact()
        rule = _make_rule()

        async def hs_side_effect(pool, text, emb, table, **kw):
            if table == "facts":
                return [fact]
            return [rule]

        with patch.object(_mod, "hybrid_search", AsyncMock(side_effect=hs_side_effect)):
            results = await recall(pool, "topic", engine)

        types = {r["id"]: r["memory_type"] for r in results}
        assert types[fact["id"]] == "fact"
        assert types[rule["id"]] == "rule"

    async def test_computes_composite_scores(self) -> None:
        """Each returned result has a composite_score key."""
        pool = AsyncMock()
        engine = _make_engine()

        fact = _make_fact(rrf_score=0.02, importance=7.0, confidence=0.9)

        with patch.object(
            _mod,
            "hybrid_search",
            AsyncMock(side_effect=lambda *a, **kw: [fact] if a[3] == "facts" else []),
        ):
            results = await recall(pool, "topic", engine)

        assert len(results) == 1
        assert "composite_score" in results[0]
        assert isinstance(results[0]["composite_score"], float)
        assert results[0]["composite_score"] > 0.0

    async def test_filters_by_min_confidence(self) -> None:
        """Results with confidence below min_confidence are excluded."""
        pool = AsyncMock()
        engine = _make_engine()

        fact_good = _make_fact(confidence=0.5)
        fact_low = _make_fact(confidence=0.1)

        async def hs_side_effect(pool, text, emb, table, **kw):
            if table == "facts":
                return [fact_good, fact_low]
            return []

        with patch.object(_mod, "hybrid_search", AsyncMock(side_effect=hs_side_effect)):
            results = await recall(pool, "topic", engine, min_confidence=0.2)

        ids = {r["id"] for r in results}
        assert fact_good["id"] in ids
        assert fact_low["id"] not in ids

    async def test_bumps_reference_counts(self) -> None:
        """pool.execute is called with UPDATE for each returned result."""
        pool = AsyncMock()
        engine = _make_engine()

        fact_id = uuid.uuid4()
        rule_id = uuid.uuid4()
        fact = _make_fact(id_=fact_id)
        rule = _make_rule(id_=rule_id)

        async def hs_side_effect(pool, text, emb, table, **kw):
            if table == "facts":
                return [fact]
            return [rule]

        with patch.object(_mod, "hybrid_search", AsyncMock(side_effect=hs_side_effect)):
            await recall(pool, "topic", engine)

        # Check that pool.execute was called with UPDATE statements
        execute_calls = pool.execute.await_args_list
        assert len(execute_calls) == 2

        # Verify the SQL and IDs
        update_sqls = [c.args[0] for c in execute_calls]
        update_ids = [c.args[1] for c in execute_calls]

        assert any("UPDATE facts" in sql for sql in update_sqls)
        assert any("UPDATE rules" in sql for sql in update_sqls)
        assert fact_id in update_ids
        assert rule_id in update_ids

    async def test_sorted_by_composite_score_descending(self) -> None:
        """Results are returned sorted by composite_score descending."""
        pool = AsyncMock()
        engine = _make_engine()

        # Create facts with different rrf_scores to get different composite scores
        high = _make_fact(rrf_score=_MAX_RRF, importance=9.0, confidence=0.95)
        medium = _make_fact(rrf_score=_MAX_RRF * 0.5, importance=5.0, confidence=0.7)
        low = _make_fact(rrf_score=_MAX_RRF * 0.1, importance=2.0, confidence=0.3)

        async def hs_side_effect(pool, text, emb, table, **kw):
            if table == "facts":
                # Return in unsorted order
                return [low, high, medium]
            return []

        with patch.object(_mod, "hybrid_search", AsyncMock(side_effect=hs_side_effect)):
            results = await recall(pool, "topic", engine)

        scores = [r["composite_score"] for r in results]
        assert scores == sorted(scores, reverse=True)
        assert len(results) == 3

    async def test_respects_limit(self) -> None:
        """Output is truncated to the limit parameter."""
        pool = AsyncMock()
        engine = _make_engine()

        facts = [_make_fact(rrf_score=0.02 - i * 0.001) for i in range(5)]

        async def hs_side_effect(pool, text, emb, table, **kw):
            if table == "facts":
                return list(facts)
            return []

        with patch.object(_mod, "hybrid_search", AsyncMock(side_effect=hs_side_effect)):
            results = await recall(pool, "topic", engine, limit=3)

        assert len(results) <= 3

    async def test_passes_scope_to_hybrid_search(self) -> None:
        """scope parameter is forwarded to hybrid_search calls."""
        pool = AsyncMock()
        engine = _make_engine()
        hs_mock = AsyncMock(return_value=[])

        with patch.object(_mod, "hybrid_search", hs_mock):
            await recall(pool, "topic", engine, scope="my-butler")

        for c in hs_mock.await_args_list:
            assert c.kwargs.get("scope") == "my-butler"

    async def test_custom_weights_passed_through(self) -> None:
        """Custom CompositeWeights affect scoring."""
        pool = AsyncMock()
        engine = _make_engine()

        fact = _make_fact(
            rrf_score=_MAX_RRF,
            importance=10.0,
            confidence=1.0,
            last_referenced_at=datetime.now(UTC),
        )

        async def hs_side_effect(pool, text, emb, table, **kw):
            if table == "facts":
                return [fact.copy()]
            return []

        # Score with default weights
        with patch.object(_mod, "hybrid_search", AsyncMock(side_effect=hs_side_effect)):
            results_default = await recall(pool, "topic", engine)

        # Score with all weight on relevance
        custom_weights = CompositeWeights(
            relevance=1.0,
            importance=0.0,
            recency=0.0,
            confidence=0.0,
        )
        with patch.object(_mod, "hybrid_search", AsyncMock(side_effect=hs_side_effect)):
            results_custom = await recall(pool, "topic", engine, weights=custom_weights)

        # With max RRF and normalized to 1.0, relevance-only should score exactly 1.0
        assert results_custom[0]["composite_score"] == pytest.approx(1.0)
        # Default weights give a different score since importance, recency, confidence also matter
        assert results_default[0]["composite_score"] != results_custom[0]["composite_score"]

    async def test_empty_results_returns_empty_list(self) -> None:
        """When both searches return no results, recall returns empty list."""
        pool = AsyncMock()
        engine = _make_engine()

        with patch.object(_mod, "hybrid_search", AsyncMock(return_value=[])):
            results = await recall(pool, "topic", engine)

        assert results == []
        pool.execute.assert_not_awaited()

    async def test_reference_bump_only_for_returned_results(self) -> None:
        """Only results that pass confidence filter get reference bumps."""
        pool = AsyncMock()
        engine = _make_engine()

        good_id = uuid.uuid4()
        bad_id = uuid.uuid4()
        good = _make_fact(id_=good_id, confidence=0.8)
        bad = _make_fact(id_=bad_id, confidence=0.05)

        async def hs_side_effect(pool, text, emb, table, **kw):
            if table == "facts":
                return [good, bad]
            return []

        with patch.object(_mod, "hybrid_search", AsyncMock(side_effect=hs_side_effect)):
            results = await recall(pool, "topic", engine, min_confidence=0.2)

        # Only the good fact should be returned and bumped
        assert len(results) == 1
        assert pool.execute.await_count == 1
        bump_call = pool.execute.await_args_list[0]
        assert bump_call.args[1] == good_id

    async def test_default_min_confidence_is_0_2(self) -> None:
        """Default min_confidence is 0.2."""
        pool = AsyncMock()
        engine = _make_engine()

        fact_borderline = _make_fact(confidence=0.19)
        fact_passing = _make_fact(confidence=0.21)

        async def hs_side_effect(pool, text, emb, table, **kw):
            if table == "facts":
                return [fact_borderline, fact_passing]
            return []

        with patch.object(_mod, "hybrid_search", AsyncMock(side_effect=hs_side_effect)):
            # Use default min_confidence (should be 0.2)
            results = await recall(pool, "topic", engine)

        ids = {r["id"] for r in results}
        assert fact_passing["id"] in ids
        assert fact_borderline["id"] not in ids

    async def test_default_limit_is_10(self) -> None:
        """Default limit is 10."""
        pool = AsyncMock()
        engine = _make_engine()
        hs_mock = AsyncMock(return_value=[])

        with patch.object(_mod, "hybrid_search", hs_mock):
            await recall(pool, "topic", engine)

        # hybrid_search should be called with limit=10
        for c in hs_mock.await_args_list:
            assert c.kwargs.get("limit") == 10

    async def test_missing_confidence_defaults_to_1(self) -> None:
        """Results without a confidence key default to 1.0 (always pass filter)."""
        pool = AsyncMock()
        engine = _make_engine()

        fact_no_conf = {
            "id": uuid.uuid4(),
            "content": "no conf",
            "rrf_score": 0.02,
            "importance": 5.0,
        }

        async def hs_side_effect(pool, text, emb, table, **kw):
            if table == "facts":
                return [fact_no_conf]
            return []

        with patch.object(_mod, "hybrid_search", AsyncMock(side_effect=hs_side_effect)):
            results = await recall(pool, "topic", engine)

        # Should be included since default confidence=1.0 > 0.2
        assert len(results) == 1

    async def test_missing_importance_defaults_to_5(self) -> None:
        """Results without importance key default to 5.0."""
        pool = AsyncMock()
        engine = _make_engine()

        fact_no_imp = {
            "id": uuid.uuid4(),
            "content": "no imp",
            "rrf_score": 0.02,
            "confidence": 0.8,
        }

        async def hs_side_effect(pool, text, emb, table, **kw):
            if table == "facts":
                return [fact_no_imp]
            return []

        with patch.object(_mod, "hybrid_search", AsyncMock(side_effect=hs_side_effect)):
            results = await recall(pool, "topic", engine)

        assert len(results) == 1
        assert results[0]["composite_score"] > 0.0
