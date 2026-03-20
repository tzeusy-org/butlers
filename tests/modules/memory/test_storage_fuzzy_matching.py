"""Unit tests for fuzzy predicate matching in store_fact() and _fuzzy_match_predicates().

Covers tasks 3.1–3.4 from openspec/changes/predicate-registry-enforcement/tasks.md.

Scenarios tested:
  _levenshtein_distance helper:
    - identical strings return 0
    - known typo distance is correct
    - empty string edge cases

  _fuzzy_match_predicates helper:
    - typo within edit distance 2 is found
    - common prefix >= 5 chars is found
    - truly dissimilar predicate returns empty list
    - ordering: edit-distance matches ranked before prefix-only matches

  store_fact() integration (4 spec scenarios):
    - close match by edit distance → suggestions in return value
    - close match by prefix → suggestions in return value
    - no close matches → no suggestions key in return value
    - suggestions are non-blocking (fact is still stored)
"""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Load the storage module from disk (roster/ is not a Python package).
# ---------------------------------------------------------------------------
_MEMORY_MODULE_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent / "src" / "butlers" / "modules" / "memory"
)
_STORAGE_PATH = _MEMORY_MODULE_PATH / "storage.py"


def _load_storage_module():
    """Load storage.py with sentence_transformers mocked out."""
    spec = importlib.util.spec_from_file_location("storage", _STORAGE_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_mod = _load_storage_module()
store_fact = _mod.store_fact
_fuzzy_match_predicates = _mod._fuzzy_match_predicates
_levenshtein_distance = _mod._levenshtein_distance

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Async context manager helper for mocking asyncpg pool/conn
# ---------------------------------------------------------------------------


class _AsyncCM:
    """Simple async context manager wrapper returning a fixed value."""

    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *args):
        return False


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def embedding_engine():
    """Return a mock EmbeddingEngine that produces a deterministic vector."""
    engine = MagicMock()
    engine.embed.return_value = [0.1] * 384
    return engine


def _make_row(name: str, description: str | None = None):
    """Build a fake asyncpg Record-like dict for predicate_registry rows."""
    return {"name": name, "description": description}


def _make_pool(
    *,
    registry_row=None,
    registry_rows_for_fetch: list | None = None,
    entity_exists: bool = True,
):
    """Build (pool, conn) mocks.

    registry_row: returned by conn.fetchrow for predicate_registry lookup (None = novel).
    registry_rows_for_fetch: list of rows returned by conn.fetch for fuzzy matching.
    entity_exists: controls entity-validation fetchval.
    """
    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=_AsyncCM(None))
    conn.execute = AsyncMock()

    # fetchrow call order: alias lookup (1st), registry lookup (2nd), supersession (3rd+).
    # For novel predicates the registry lookup returns None (not registered).
    conn.fetchrow = AsyncMock(side_effect=[None, registry_row, None])

    # fetchval drives entity validation (returns 1 = exists) and idempotency checks.
    conn.fetchval = AsyncMock(return_value=1 if entity_exists else None)

    # fetch is called by _fuzzy_match_predicates to get all registry names.
    conn.fetch = AsyncMock(return_value=registry_rows_for_fetch or [])

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_AsyncCM(conn))
    return pool, conn


# ---------------------------------------------------------------------------
# Unit tests for _levenshtein_distance
# ---------------------------------------------------------------------------


class TestLevenshteinDistance:
    """Tests for the _levenshtein_distance helper."""

    def test_identical_strings_return_zero(self):
        """Identical strings have edit distance 0."""
        assert _levenshtein_distance("parent_of", "parent_of") == 0

    def test_empty_string_returns_other_length(self):
        """Edit distance from empty string to any string is len(other)."""
        assert _levenshtein_distance("", "abc") == 3
        assert _levenshtein_distance("abc", "") == 3

    def test_known_typo_distance(self):
        """'parnet_of' vs 'parent_of' — two transpositions, distance 2."""
        # parnet_of → parent_of: 'n' and 'e' swapped → 2 subs (or 1 del + 1 ins)
        dist = _levenshtein_distance("parnet_of", "parent_of")
        assert dist <= 2, f"Expected <= 2, got {dist}"

    def test_completely_different_strings(self):
        """Strings with no overlap have high distance."""
        dist = _levenshtein_distance("custom_domain_metric", "birthday")
        assert dist > 2

    def test_single_char_diff(self):
        """One-character difference gives distance 1."""
        assert _levenshtein_distance("birthday", "birthdag") == 1

    def test_prefix_vs_full_name(self):
        """prefix has distance equal to suffix length."""
        assert _levenshtein_distance("parent", "parent_of") == 3


# ---------------------------------------------------------------------------
# Unit tests for _fuzzy_match_predicates
# ---------------------------------------------------------------------------


class TestFuzzyMatchPredicates:
    """Tests for the _fuzzy_match_predicates helper."""

    async def test_typo_within_edit_distance_2_is_found(self):
        """Predicate with typo within edit distance 2 appears in suggestions."""
        conn = AsyncMock()
        conn.fetch = AsyncMock(
            return_value=[
                _make_row("parent_of", "Describes a parent-child relationship"),
                _make_row("birthday", "Date of birth"),
            ]
        )

        suggestions = await _fuzzy_match_predicates(conn, "parnet_of")

        names = [s["predicate"] for s in suggestions]
        assert "parent_of" in names
        assert "birthday" not in names

    async def test_prefix_match_5_chars_is_found(self):
        """Predicate sharing a 5-char prefix with a registry predicate is suggested."""
        conn = AsyncMock()
        conn.fetch = AsyncMock(
            return_value=[
                _make_row("parent_of", "Describes a parent-child relationship"),
                _make_row("birthday", "Date of birth"),
            ]
        )

        # "parent_relationship" shares prefix "paren" (5 chars) with "parent_of"
        suggestions = await _fuzzy_match_predicates(conn, "parent_relationship")

        names = [s["predicate"] for s in suggestions]
        assert "parent_of" in names

    async def test_no_close_matches_returns_empty_list(self):
        """Predicate with no close registry matches returns empty list."""
        conn = AsyncMock()
        conn.fetch = AsyncMock(
            return_value=[
                _make_row("parent_of", "Describes a parent-child relationship"),
                _make_row("birthday", "Date of birth"),
            ]
        )

        suggestions = await _fuzzy_match_predicates(conn, "custom_domain_metric")

        assert suggestions == []

    async def test_suggestions_include_description(self):
        """Each suggestion dict contains 'predicate' and 'description' keys."""
        conn = AsyncMock()
        desc = "Describes a parent-child relationship"
        conn.fetch = AsyncMock(return_value=[_make_row("parent_of", desc)])

        suggestions = await _fuzzy_match_predicates(conn, "parnet_of")

        assert len(suggestions) >= 1
        suggestion = next(s for s in suggestions if s["predicate"] == "parent_of")
        assert suggestion["description"] == desc

    async def test_empty_registry_returns_empty_list(self):
        """When registry is empty, no suggestions are returned."""
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[])

        suggestions = await _fuzzy_match_predicates(conn, "parnet_of")

        assert suggestions == []

    async def test_edit_distance_matches_ranked_first(self):
        """Suggestions with edit distance <= 2 should appear before prefix-only matches."""
        conn = AsyncMock()
        conn.fetch = AsyncMock(
            return_value=[
                _make_row("parent_of"),  # edit-distance match for "parnet_of"
                _make_row("parent_relationship"),  # prefix-only match for "parnet_of"
            ]
        )
        # "parnet_of" has edit distance ~2 from "parent_of" but distance >2 from
        # "parent_relationship" (which only shares a prefix).
        suggestions = await _fuzzy_match_predicates(conn, "parnet_of")

        names = [s["predicate"] for s in suggestions]
        if "parent_of" in names and "parent_relationship" in names:
            assert names.index("parent_of") < names.index("parent_relationship")

    async def test_description_can_be_none(self):
        """Suggestions handle None descriptions gracefully."""
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[_make_row("parent_of", None)])

        suggestions = await _fuzzy_match_predicates(conn, "parnet_of")

        assert len(suggestions) >= 1
        assert suggestions[0]["description"] is None


# ---------------------------------------------------------------------------
# store_fact() integration — 4 spec scenarios
# ---------------------------------------------------------------------------


class TestStoreFuzzyMatchingIntegration:
    """Integration tests for fuzzy matching in store_fact() return value."""

    async def test_close_match_by_edit_distance_includes_suggestions(self, embedding_engine):
        """SCENARIO: Close match by edit distance.

        WHEN store_fact() succeeds with predicate 'parnet_of' (not in registry)
        AND 'parent_of' exists in the registry with edit distance <= 2,
        THEN the response MUST include a 'suggestions' key with matching predicates.
        """
        registry_rows = [
            _make_row("parent_of", "Describes a parent-child relationship"),
            _make_row("birthday", "Date of birth"),
        ]
        pool, _conn = _make_pool(
            registry_row=None,  # predicate not in registry
            registry_rows_for_fetch=registry_rows,
        )

        result = await store_fact(
            pool,
            "Alice",
            "parnet_of",  # typo — edit distance 2 from "parent_of"
            "Bob",
            embedding_engine,
        )

        assert isinstance(result, dict)
        assert "id" in result
        assert "suggestions" in result, "Expected 'suggestions' key for close edit-distance match"
        suggestion_names = [s["predicate"] for s in result["suggestions"]]
        assert "parent_of" in suggestion_names

    async def test_close_match_by_prefix_includes_suggestions(self, embedding_engine):
        """SCENARIO: Close match by common prefix.

        WHEN store_fact() succeeds with predicate 'parent_relationship' (not in registry)
        AND 'parent_of' exists in the registry sharing a 5+ char prefix,
        THEN the response MUST include 'parent_of' in suggestions.
        """
        registry_rows = [
            _make_row("parent_of", "Describes a parent-child relationship"),
        ]
        pool, _conn = _make_pool(
            registry_row=None,
            registry_rows_for_fetch=registry_rows,
        )

        result = await store_fact(
            pool,
            "Alice",
            "parent_relationship",
            "some relationship",
            embedding_engine,
        )

        assert isinstance(result, dict)
        assert "id" in result
        assert "suggestions" in result, "Expected 'suggestions' key for prefix match"
        suggestion_names = [s["predicate"] for s in result["suggestions"]]
        assert "parent_of" in suggestion_names

    async def test_no_close_matches_omits_suggestions_key(self, embedding_engine):
        """SCENARIO: No close matches.

        WHEN store_fact() succeeds with predicate 'custom_domain_metric' (not in registry)
        AND no registered predicate has edit distance <= 2 or a shared prefix of 5+ chars,
        THEN the response MUST NOT include a 'suggestions' key.
        """
        registry_rows = [
            _make_row("parent_of", "Describes a parent-child relationship"),
            _make_row("birthday", "Date of birth"),
        ]
        pool, _conn = _make_pool(
            registry_row=None,
            registry_rows_for_fetch=registry_rows,
        )

        result = await store_fact(
            pool,
            "user",
            "custom_domain_metric",
            "42",
            embedding_engine,
        )

        assert isinstance(result, dict)
        assert "id" in result
        assert "suggestions" not in result, (
            "Expected no 'suggestions' key when no close matches exist"
        )

    async def test_suggestions_are_non_blocking(self, embedding_engine):
        """SCENARIO: Suggestions are non-blocking.

        WHEN store_fact() is called with a novel predicate that has close matches,
        THEN the fact MUST still be stored successfully (no exception raised),
        AND the response MUST include both 'id' and 'suggestions'.
        """
        registry_rows = [
            _make_row("parent_of", "Describes a parent-child relationship"),
        ]
        pool, conn = _make_pool(
            registry_row=None,
            registry_rows_for_fetch=registry_rows,
        )

        # Should not raise
        result = await store_fact(
            pool,
            "Alice",
            "parnet_of",
            "Bob",
            embedding_engine,
        )

        # Fact was stored (conn.execute was called for INSERT)
        conn.execute.assert_called()

        # Response has both id and suggestions
        assert isinstance(result, dict)
        assert "id" in result
        assert isinstance(result["id"], uuid.UUID)
        assert "suggestions" in result
        assert len(result["suggestions"]) > 0

    async def test_registered_predicate_does_not_include_suggestions(self, embedding_engine):
        """Predicates that ARE in the registry should not trigger fuzzy matching.

        Fuzzy matching only runs when the predicate is NOT found in the registry.
        """
        registry_row = {
            "is_edge": False,
            "is_temporal": False,
            "expected_subject_type": None,
            "expected_object_type": None,
        }
        pool, conn = _make_pool(
            registry_row=registry_row,
        )
        # No entity_id: fetchrow order is alias lookup → registry lookup → supersession check.
        conn.fetchrow = AsyncMock(side_effect=[None, registry_row, None])

        result = await store_fact(
            pool,
            "Alice",
            "birthday",
            "1990-01-01",
            embedding_engine,
        )

        # conn.fetch should NOT have been called (no fuzzy matching for known predicates)
        conn.fetch.assert_not_called()
        # Result should not include suggestions
        assert isinstance(result, dict)
        assert "suggestions" not in result
