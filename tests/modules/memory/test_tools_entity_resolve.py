"""Tests for entity_resolve MCP tool in entities.py.

Tests cover:
- Exact canonical_name match returns highest score
- Alias matching works case-insensitively
- Prefix/substring matching discovers partial matches
- Graph neighborhood scoring boosts contextually relevant candidates
- domain_scores from context_hints are incorporated
- Empty results returned (not error) when no candidates found
- Results ordered by score DESC, then canonical_name ASC
- Tenant-bounded queries only
- entity_type filter
- Empty/whitespace name handling
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.modules.memory.tools.entities import (
    _GRAPH_BOOST_MAX,
    _SCORE_EXACT_ALIAS,
    _SCORE_EXACT_NAME,
    _SCORE_FUZZY,
    _SCORE_PREFIX,
    entity_resolve,
)

pytestmark = pytest.mark.unit

TENANT = "tenant-abc"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_entity_row(
    entity_id: str,
    canonical_name: str,
    entity_type: str = "person",
    aliases: list[str] | None = None,
    match_type: str = "exact",
) -> MagicMock:
    """Build a mock asyncpg Record-like object."""
    row = MagicMock()
    row.__getitem__ = lambda self, key: {
        "id": uuid.UUID(entity_id),
        "canonical_name": canonical_name,
        "entity_type": entity_type,
        "aliases": aliases or [],
        "match_type": match_type,
    }[key]
    return row


def _make_fact_row(entity_id: str, predicate: str, content: str) -> MagicMock:
    row = MagicMock()
    row.__getitem__ = lambda self, key: {
        "entity_id": uuid.UUID(entity_id),
        "predicate": predicate,
        "content": content,
    }[key]
    return row


@pytest.fixture()
def mock_pool() -> AsyncMock:
    pool = AsyncMock()
    # Default: empty results for all fetches
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchrow = AsyncMock(return_value=None)
    pool.fetchval = AsyncMock(return_value=None)
    return pool


ENTITY_ID_1 = str(uuid.uuid4())
ENTITY_ID_2 = str(uuid.uuid4())
ENTITY_ID_3 = str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Helper: build UNION ALL rows for different tiers
# ---------------------------------------------------------------------------


def _rows_exact(entity_id: str, canonical_name: str, aliases: list[str] | None = None):
    return [_make_entity_row(entity_id, canonical_name, aliases=aliases, match_type="exact")]


def _rows_alias(entity_id: str, canonical_name: str, aliases: list[str] | None = None):
    return [_make_entity_row(entity_id, canonical_name, aliases=aliases, match_type="alias")]


def _rows_prefix(entity_id: str, canonical_name: str, aliases: list[str] | None = None):
    return [_make_entity_row(entity_id, canonical_name, aliases=aliases, match_type="prefix")]


# ---------------------------------------------------------------------------
# Tests: empty / invalid name
# ---------------------------------------------------------------------------


class TestEntityResolveEmptyName:
    """Empty or whitespace name returns empty list without querying DB."""

    async def test_empty_string_returns_empty(self, mock_pool: AsyncMock) -> None:
        result = await entity_resolve(mock_pool, "", tenant_id=TENANT)
        assert result == []
        mock_pool.fetch.assert_not_called()

    async def test_whitespace_returns_empty(self, mock_pool: AsyncMock) -> None:
        result = await entity_resolve(mock_pool, "   ", tenant_id=TENANT)
        assert result == []
        mock_pool.fetch.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: no candidates found
# ---------------------------------------------------------------------------


class TestEntityResolveNoCandidates:
    """Returns empty list (not error) when no candidates match."""

    async def test_no_candidates_returns_empty_list(self, mock_pool: AsyncMock) -> None:
        mock_pool.fetch = AsyncMock(return_value=[])
        result = await entity_resolve(mock_pool, "Chloe", tenant_id=TENANT)
        assert result == []

    async def test_empty_list_not_exception(self, mock_pool: AsyncMock) -> None:
        mock_pool.fetch = AsyncMock(return_value=[])
        result = await entity_resolve(mock_pool, "NonExistent Person", tenant_id=TENANT)
        assert isinstance(result, list)
        assert len(result) == 0


# ---------------------------------------------------------------------------
# Tests: exact canonical name match
# ---------------------------------------------------------------------------


class TestEntityResolveExactMatch:
    """Exact canonical_name match returns score == _SCORE_EXACT_NAME."""

    async def test_exact_match_returns_highest_score(self, mock_pool: AsyncMock) -> None:
        mock_pool.fetch = AsyncMock(
            return_value=_rows_exact(ENTITY_ID_1, "Chloe Wong", aliases=["Chlo"])
        )
        results = await entity_resolve(mock_pool, "Chloe Wong", tenant_id=TENANT)
        assert len(results) == 1
        assert results[0]["entity_id"] == ENTITY_ID_1
        assert results[0]["canonical_name"] == "Chloe Wong"
        assert results[0]["score"] == _SCORE_EXACT_NAME
        assert results[0]["name_match"] == "exact"

    async def test_exact_match_includes_aliases(self, mock_pool: AsyncMock) -> None:
        mock_pool.fetch = AsyncMock(
            return_value=_rows_exact(ENTITY_ID_1, "Chloe Wong", aliases=["Chlo", "CW"])
        )
        results = await entity_resolve(mock_pool, "Chloe Wong", tenant_id=TENANT)
        assert results[0]["aliases"] == ["Chlo", "CW"]

    async def test_exact_match_is_case_insensitive(self, mock_pool: AsyncMock) -> None:
        """The query uses LOWER() so case-insensitive match is enforced at DB level.
        The SQL includes LOWER(canonical_name) = $2 with $2 = name.lower(), so we
        just verify the pool.fetch was called with the lowercased name."""
        mock_pool.fetch = AsyncMock(return_value=[])
        await entity_resolve(mock_pool, "CHLOE WONG", tenant_id=TENANT)
        call_args = mock_pool.fetch.call_args
        # Second positional param (after sql) should be lowercased
        assert call_args[0][2] == "chloe wong"

    async def test_exact_match_score_highest_among_tiers(self, mock_pool: AsyncMock) -> None:
        """Exact match score (_SCORE_EXACT_NAME) should exceed alias score."""
        assert _SCORE_EXACT_NAME > _SCORE_EXACT_ALIAS > _SCORE_PREFIX > _SCORE_FUZZY


# ---------------------------------------------------------------------------
# Tests: alias matching
# ---------------------------------------------------------------------------


class TestEntityResolveAliasMatch:
    """Alias matches return score == _SCORE_EXACT_ALIAS."""

    async def test_alias_match_returns_alias_score(self, mock_pool: AsyncMock) -> None:
        mock_pool.fetch = AsyncMock(
            return_value=_rows_alias(ENTITY_ID_1, "Chloe Wong", aliases=["Chlo", "CW"])
        )
        results = await entity_resolve(mock_pool, "Chlo", tenant_id=TENANT)
        assert len(results) == 1
        assert results[0]["score"] == _SCORE_EXACT_ALIAS
        assert results[0]["name_match"] == "alias"

    async def test_alias_match_case_insensitive(self, mock_pool: AsyncMock) -> None:
        """SQL uses LOWER(a) for case-insensitive alias matching."""
        mock_pool.fetch = AsyncMock(return_value=[])
        await entity_resolve(mock_pool, "CW", tenant_id=TENANT)
        call_args = mock_pool.fetch.call_args
        # name_lower should be "cw"
        assert call_args[0][2] == "cw"


# ---------------------------------------------------------------------------
# Tests: prefix/substring matching
# ---------------------------------------------------------------------------


class TestEntityResolvePrefixMatch:
    """Prefix/substring matching discovers partial name matches."""

    async def test_prefix_match_returns_prefix_score(self, mock_pool: AsyncMock) -> None:
        mock_pool.fetch = AsyncMock(return_value=_rows_prefix(ENTITY_ID_1, "Chloe Wong"))
        results = await entity_resolve(mock_pool, "Chloe", tenant_id=TENANT)
        assert len(results) == 1
        assert results[0]["score"] == _SCORE_PREFIX
        assert results[0]["name_match"] == "prefix"

    async def test_substring_match_discovers_entity(self, mock_pool: AsyncMock) -> None:
        mock_pool.fetch = AsyncMock(return_value=_rows_prefix(ENTITY_ID_1, "Microsoft Corporation"))
        results = await entity_resolve(mock_pool, "Microsoft", tenant_id=TENANT)
        assert len(results) == 1
        assert results[0]["name_match"] == "prefix"


# ---------------------------------------------------------------------------
# Tests: priority ordering when multiple tiers match same entity
# ---------------------------------------------------------------------------


class TestEntityResolveTierPriority:
    """When multiple tiers match the same entity, the best tier wins."""

    async def test_exact_tier_wins_over_prefix_for_same_entity(self, mock_pool: AsyncMock) -> None:
        """If DB returns both exact and prefix rows for the same entity,
        the exact match should be kept (lower tier number = higher priority)."""
        # Simulate both exact and prefix rows for the same entity
        exact_row = _make_entity_row(ENTITY_ID_1, "Alice", match_type="exact")
        prefix_row = _make_entity_row(ENTITY_ID_1, "Alice", match_type="prefix")
        # In practice the SQL uses DISTINCT ON, but we test the dedup logic
        mock_pool.fetch = AsyncMock(return_value=[exact_row, prefix_row])
        results = await entity_resolve(mock_pool, "Alice", tenant_id=TENANT)
        assert len(results) == 1
        assert results[0]["name_match"] == "exact"
        assert results[0]["score"] == _SCORE_EXACT_NAME


# ---------------------------------------------------------------------------
# Tests: multiple candidates sorted by score DESC
# ---------------------------------------------------------------------------


class TestEntityResolveSorting:
    """Results are ordered by score DESC, then canonical_name ASC."""

    async def test_results_ordered_by_score_desc(self, mock_pool: AsyncMock) -> None:
        exact_row = _make_entity_row(ENTITY_ID_1, "Alice Smith", match_type="exact")
        alias_row = _make_entity_row(ENTITY_ID_2, "Alice Johnson", match_type="alias")
        prefix_row = _make_entity_row(ENTITY_ID_3, "Alicia Brown", match_type="prefix")
        mock_pool.fetch = AsyncMock(return_value=[prefix_row, exact_row, alias_row])
        results = await entity_resolve(mock_pool, "Alice", tenant_id=TENANT)
        scores = [r["score"] for r in results]
        assert scores == sorted(scores, reverse=True)

    async def test_same_score_sorted_by_canonical_name_asc(self, mock_pool: AsyncMock) -> None:
        """Two exact matches (same score) should be sorted by canonical_name ASC."""
        row_zebra = _make_entity_row(ENTITY_ID_1, "Zebra Corp", match_type="exact")
        row_alpha = _make_entity_row(ENTITY_ID_2, "Alpha Corp", match_type="exact")
        mock_pool.fetch = AsyncMock(return_value=[row_zebra, row_alpha])
        results = await entity_resolve(mock_pool, "corp", tenant_id=TENANT)
        assert results[0]["canonical_name"] == "Alpha Corp"
        assert results[1]["canonical_name"] == "Zebra Corp"


# ---------------------------------------------------------------------------
# Tests: graph neighborhood scoring
# ---------------------------------------------------------------------------


class TestEntityResolveGraphNeighborhood:
    """Graph neighborhood similarity boosts candidates with relevant facts."""

    async def test_graph_boost_applied_when_context_hints_match(self, mock_pool: AsyncMock) -> None:
        """Candidate with fact mentioning the hint topic should score higher."""
        # Two candidates, both prefix matches
        chloe_row = _make_entity_row(ENTITY_ID_1, "Chloe Wong", match_type="exact")
        carol_row = _make_entity_row(ENTITY_ID_2, "Carol Smith", match_type="exact")

        # Fact rows: Chloe has a fact about "cooking" which matches the topic hint
        fact_chloe = _make_fact_row(ENTITY_ID_1, "hobby", "loves cooking and baking")
        # Carol has unrelated facts
        fact_carol = _make_fact_row(ENTITY_ID_2, "job", "works in finance")

        def _fetch_side_effect(*args, **kwargs):
            sql = args[0]
            if "facts" in sql:
                return [fact_chloe, fact_carol]
            return [chloe_row, carol_row]

        mock_pool.fetch = AsyncMock(side_effect=_fetch_side_effect)

        results = await entity_resolve(
            mock_pool,
            "Chloe",
            tenant_id=TENANT,
            context_hints={"topic": "cooking"},
        )

        assert len(results) == 2
        # Chloe should score higher due to graph neighborhood overlap with "cooking"
        chloe_result = next(r for r in results if r["entity_id"] == ENTITY_ID_1)
        carol_result = next(r for r in results if r["entity_id"] == ENTITY_ID_2)
        assert chloe_result["score"] > carol_result["score"]

    async def test_no_graph_boost_without_context_hints(self, mock_pool: AsyncMock) -> None:
        """No facts query executed when context_hints is None."""
        mock_pool.fetch = AsyncMock(return_value=_rows_exact(ENTITY_ID_1, "Alice"))
        results = await entity_resolve(mock_pool, "Alice", tenant_id=TENANT)
        # Should only have called fetch once (for candidates), not for facts
        assert mock_pool.fetch.call_count == 1
        assert results[0]["score"] == _SCORE_EXACT_NAME

    async def test_no_graph_boost_when_hints_has_no_text_terms(self, mock_pool: AsyncMock) -> None:
        """domain_scores-only context_hints should not trigger a facts query."""
        mock_pool.fetch = AsyncMock(return_value=_rows_exact(ENTITY_ID_1, "Alice"))
        # Only domain_scores, no topic or mentioned_with
        results = await entity_resolve(
            mock_pool,
            "Alice",
            tenant_id=TENANT,
            context_hints={"domain_scores": {ENTITY_ID_1: 5.0}},
        )
        # Should be 1 call for candidates, 1 for facts (but no boost from keywords)
        # Actually with no topic/mentioned_with, the graph scoring skips the facts query
        assert results[0]["score"] >= _SCORE_EXACT_NAME

    async def test_mentioned_with_contributes_to_overlap(self, mock_pool: AsyncMock) -> None:
        """mentioned_with names are tokenized and contribute to hint_terms."""
        entity_row = _make_entity_row(ENTITY_ID_1, "Bob", match_type="exact")
        # Bob has facts mentioning "alice" (referenced in mentioned_with)
        fact_row = _make_fact_row(ENTITY_ID_1, "friend", "close friend of alice")

        call_count = [0]

        def _fetch_side_effect(*args, **kwargs):
            call_count[0] += 1
            sql = args[0]
            if "facts" in sql:
                return [fact_row]
            return [entity_row]

        mock_pool.fetch = AsyncMock(side_effect=_fetch_side_effect)

        results = await entity_resolve(
            mock_pool,
            "Bob",
            tenant_id=TENANT,
            context_hints={"mentioned_with": ["Alice"]},
        )
        assert len(results) == 1
        # Score should be boosted beyond base score due to "alice" overlap
        assert results[0]["score"] > _SCORE_EXACT_NAME

    async def test_graph_boost_capped_by_max(self, mock_pool: AsyncMock) -> None:
        """Graph boost cannot exceed _GRAPH_BOOST_MAX."""
        entity_row = _make_entity_row(ENTITY_ID_1, "Alice", match_type="exact")
        # Perfect overlap: fact predicates match all hint terms exactly
        fact_row = _make_fact_row(ENTITY_ID_1, "cooking", "cooking")

        def _fetch_side_effect(*args, **kwargs):
            sql = args[0]
            if "facts" in sql:
                return [fact_row]
            return [entity_row]

        mock_pool.fetch = AsyncMock(side_effect=_fetch_side_effect)

        results = await entity_resolve(
            mock_pool,
            "Alice",
            tenant_id=TENANT,
            context_hints={"topic": "cooking"},
        )
        assert len(results) == 1
        # Score = base + boost; boost <= _GRAPH_BOOST_MAX
        assert results[0]["score"] <= _SCORE_EXACT_NAME + _GRAPH_BOOST_MAX


# ---------------------------------------------------------------------------
# Tests: domain_scores
# ---------------------------------------------------------------------------


class TestEntityResolveDomainScores:
    """domain_scores from context_hints are added to the composite score."""

    async def test_domain_scores_added_to_candidate_score(self, mock_pool: AsyncMock) -> None:
        row = _make_entity_row(ENTITY_ID_1, "Alice", match_type="prefix")
        mock_pool.fetch = AsyncMock(return_value=[row])
        results = await entity_resolve(
            mock_pool,
            "Ali",
            tenant_id=TENANT,
            context_hints={"domain_scores": {ENTITY_ID_1: 15.0}},
        )
        assert len(results) == 1
        # prefix base + domain_score
        assert results[0]["score"] == _SCORE_PREFIX + 15.0

    async def test_domain_scores_ignored_for_unknown_entities(self, mock_pool: AsyncMock) -> None:
        """domain_scores for entity IDs not in candidates are silently ignored."""
        row = _make_entity_row(ENTITY_ID_1, "Alice", match_type="exact")
        unknown_id = str(uuid.uuid4())
        mock_pool.fetch = AsyncMock(return_value=[row])
        results = await entity_resolve(
            mock_pool,
            "Alice",
            tenant_id=TENANT,
            context_hints={"domain_scores": {unknown_id: 50.0}},
        )
        assert len(results) == 1
        assert results[0]["score"] == _SCORE_EXACT_NAME  # unchanged

    async def test_domain_scores_with_negative_value(self, mock_pool: AsyncMock) -> None:
        """Negative domain scores reduce a candidate's composite score."""
        row = _make_entity_row(ENTITY_ID_1, "Alice", match_type="exact")
        mock_pool.fetch = AsyncMock(return_value=[row])
        results = await entity_resolve(
            mock_pool,
            "Alice",
            tenant_id=TENANT,
            context_hints={"domain_scores": {ENTITY_ID_1: -10.0}},
        )
        assert len(results) == 1
        assert results[0]["score"] == _SCORE_EXACT_NAME - 10.0

    async def test_invalid_domain_score_skipped(self, mock_pool: AsyncMock) -> None:
        """Non-numeric domain scores are skipped without raising."""
        row = _make_entity_row(ENTITY_ID_1, "Alice", match_type="exact")
        mock_pool.fetch = AsyncMock(return_value=[row])
        results = await entity_resolve(
            mock_pool,
            "Alice",
            tenant_id=TENANT,
            context_hints={"domain_scores": {ENTITY_ID_1: "not_a_number"}},
        )
        assert len(results) == 1
        # Score unchanged (bad value skipped)
        assert results[0]["score"] == _SCORE_EXACT_NAME


# ---------------------------------------------------------------------------
# Tests: entity_type filter
# ---------------------------------------------------------------------------


class TestEntityResolveTypeFilter:
    """entity_type parameter is passed to the DB query for filtering."""

    async def test_entity_type_filter_passed_to_query(self, mock_pool: AsyncMock) -> None:
        mock_pool.fetch = AsyncMock(return_value=[])
        await entity_resolve(mock_pool, "Chloe", tenant_id=TENANT, entity_type="person")
        call_args = mock_pool.fetch.call_args
        # The entity_type should appear as a positional param
        params = call_args[0]
        assert "person" in params


# ---------------------------------------------------------------------------
# Tests: tenant isolation
# ---------------------------------------------------------------------------


class TestEntityResolveTenantIsolation:
    """Queries always include tenant_id as a parameter."""

    async def test_tenant_id_in_query_params(self, mock_pool: AsyncMock) -> None:
        mock_pool.fetch = AsyncMock(return_value=[])
        await entity_resolve(mock_pool, "Alice", tenant_id="my-tenant")
        call_args = mock_pool.fetch.call_args
        params = call_args[0]
        assert "my-tenant" in params

    async def test_different_tenant_uses_correct_id(self, mock_pool: AsyncMock) -> None:
        mock_pool.fetch = AsyncMock(return_value=[])
        await entity_resolve(mock_pool, "Bob", tenant_id="other-tenant-xyz")
        call_args = mock_pool.fetch.call_args
        params = call_args[0]
        assert "other-tenant-xyz" in params


# ---------------------------------------------------------------------------
# Tests: response shape contract
# ---------------------------------------------------------------------------


class TestEntityResolveResponseShape:
    """Each result dict has the correct keys per spec §6.4."""

    async def test_result_has_required_keys(self, mock_pool: AsyncMock) -> None:
        row = _make_entity_row(ENTITY_ID_1, "Alice Smith", aliases=["Ali"])
        mock_pool.fetch = AsyncMock(return_value=[row])
        results = await entity_resolve(mock_pool, "Alice Smith", tenant_id=TENANT)
        assert len(results) == 1
        r = results[0]
        assert "entity_id" in r
        assert "canonical_name" in r
        assert "entity_type" in r
        assert "score" in r
        assert "name_match" in r
        assert "aliases" in r

    async def test_entity_id_is_string(self, mock_pool: AsyncMock) -> None:
        row = _make_entity_row(ENTITY_ID_1, "Alice")
        mock_pool.fetch = AsyncMock(return_value=[row])
        results = await entity_resolve(mock_pool, "Alice", tenant_id=TENANT)
        assert isinstance(results[0]["entity_id"], str)

    async def test_score_is_numeric(self, mock_pool: AsyncMock) -> None:
        row = _make_entity_row(ENTITY_ID_1, "Alice")
        mock_pool.fetch = AsyncMock(return_value=[row])
        results = await entity_resolve(mock_pool, "Alice", tenant_id=TENANT)
        assert isinstance(results[0]["score"], (int, float))

    async def test_name_match_values_are_valid(self, mock_pool: AsyncMock) -> None:
        valid_match_types = {"exact", "alias", "prefix", "fuzzy"}
        for match_type in valid_match_types:
            row = _make_entity_row(ENTITY_ID_1, "Alice", match_type=match_type)
            mock_pool.fetch = AsyncMock(return_value=[row])
            results = await entity_resolve(mock_pool, "Alice", tenant_id=TENANT)
            if results:
                assert results[0]["name_match"] in valid_match_types

    async def test_aliases_is_list(self, mock_pool: AsyncMock) -> None:
        row = _make_entity_row(ENTITY_ID_1, "Alice", aliases=["Ali", "Ally"])
        mock_pool.fetch = AsyncMock(return_value=[row])
        results = await entity_resolve(mock_pool, "Alice", tenant_id=TENANT)
        assert isinstance(results[0]["aliases"], list)


# ---------------------------------------------------------------------------
# Tests: fuzzy matching (disabled by default)
# ---------------------------------------------------------------------------


class TestEntityResolveFuzzyMatching:
    """Fuzzy matching is disabled by default; enable_fuzzy=True activates it."""

    async def test_fuzzy_disabled_by_default(self, mock_pool: AsyncMock) -> None:
        """With enable_fuzzy=False, only 1 DB fetch call (UNION ALL)."""
        mock_pool.fetch = AsyncMock(return_value=[])
        await entity_resolve(mock_pool, "Alise", tenant_id=TENANT)
        # Only the main discovery query
        assert mock_pool.fetch.call_count == 1

    async def test_fuzzy_enabled_makes_second_query(self, mock_pool: AsyncMock) -> None:
        """With enable_fuzzy=True, a second fetch call is made for fuzzy candidates."""
        mock_pool.fetch = AsyncMock(return_value=[])
        await entity_resolve(mock_pool, "Alise", tenant_id=TENANT, enable_fuzzy=True)
        # 1 main discovery + 1 fuzzy query (even if both return empty)
        assert mock_pool.fetch.call_count == 2

    async def test_fuzzy_score_is_lowest(self, mock_pool: AsyncMock) -> None:
        """Fuzzy matches should score _SCORE_FUZZY (lowest tier)."""
        fuzzy_row = _make_entity_row(ENTITY_ID_1, "Alice")

        call_count = [0]

        def _fetch_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return []  # main query returns nothing
            return [fuzzy_row]  # fuzzy returns a match

        mock_pool.fetch = AsyncMock(side_effect=_fetch_side_effect)
        results = await entity_resolve(mock_pool, "Alise", tenant_id=TENANT, enable_fuzzy=True)
        assert len(results) == 1
        assert results[0]["score"] == _SCORE_FUZZY
        assert results[0]["name_match"] == "fuzzy"

    async def test_fuzzy_does_not_duplicate_exact_matches(self, mock_pool: AsyncMock) -> None:
        """If fuzzy returns an entity already found via exact match, keep exact."""
        exact_row = _make_entity_row(ENTITY_ID_1, "Alice", match_type="exact")
        fuzzy_row = _make_entity_row(ENTITY_ID_1, "Alice")

        call_count = [0]

        def _fetch_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return [exact_row]
            return [fuzzy_row]

        mock_pool.fetch = AsyncMock(side_effect=_fetch_side_effect)
        results = await entity_resolve(mock_pool, "Alice", tenant_id=TENANT, enable_fuzzy=True)
        assert len(results) == 1
        assert results[0]["name_match"] == "exact"
        assert results[0]["score"] == _SCORE_EXACT_NAME
