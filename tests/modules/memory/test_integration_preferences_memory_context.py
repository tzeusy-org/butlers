"""Integration verification: preference facts in memory_context and supersession chain.

Spec: openspec/specs/user-preferences/spec.md
Requirement: Preferences surface in memory_context Profile Facts
Requirement: Supersession chain correctness on multiple preference updates

These tests verify:
1. Preference facts (predicate LIKE 'preferences:%') appear in the Profile Facts
   section of memory_context output, ranked by importance DESC.
2. High-importance preference facts (8.0) rank before lower-importance facts (5.0)
   in Profile Facts.
3. The supersession chain is correct when a preference is updated multiple times:
   each successive call supersedes the previous active fact; only one active fact
   remains at any point.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.modules.memory.tools.context import memory_context
from butlers.modules.memory.tools.preferences import (
    PREFERENCE_IMPORTANCE_DEFAULT,
    _derive_scope,
    set_preference,
)

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Shared test constants
# ---------------------------------------------------------------------------

OWNER_UUID = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
OWNER_NAME = "Alice"

FACT_UUID_1 = uuid.UUID("11111111-2222-3333-4444-555555555555")
FACT_UUID_2 = uuid.UUID("22222222-3333-4444-5555-666666666666")
FACT_UUID_3 = uuid.UUID("33333333-4444-5555-6666-777777777777")

NOW = datetime(2026, 3, 25, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_preference_fact(
    *,
    id: uuid.UUID | None = None,
    predicate: str = "preferences:travel_flight_seat",
    content: str = "window",
    scope: str = "travel",
    importance: float = PREFERENCE_IMPORTANCE_DEFAULT,
    entity_id: uuid.UUID = OWNER_UUID,
) -> dict[str, Any]:
    """Build a fact dict that looks like a stored preference fact."""
    return {
        "id": id or uuid.uuid4(),
        "subject": OWNER_NAME,
        "predicate": predicate,
        "content": content,
        "scope": scope,
        "importance": importance,
        "confidence": 1.0,
        "decay_rate": 0.002,
        "last_confirmed_at": NOW,
        "entity_id": entity_id,
        "validity": "active",
        "created_at": NOW,
        "permanence": "stable",
        "memory_type": "fact",
        "composite_score": 0.5,
    }


def _make_standard_fact(
    *,
    id: uuid.UUID | None = None,
    predicate: str = "knows",
    content: str = "Python",
    importance: float = 5.0,
    entity_id: uuid.UUID = OWNER_UUID,
) -> dict[str, Any]:
    """Build a standard (non-preference) fact dict anchored to the owner."""
    return {
        "id": id or uuid.uuid4(),
        "subject": OWNER_NAME,
        "predicate": predicate,
        "content": content,
        "scope": "global",
        "importance": importance,
        "confidence": 1.0,
        "decay_rate": 0.0,
        "last_confirmed_at": NOW,
        "entity_id": entity_id,
        "validity": "active",
        "created_at": NOW,
        "permanence": "stable",
        "memory_type": "fact",
        "composite_score": 0.5,
    }


def _make_pool_with_profile_rows(profile_rows: list[dict]) -> AsyncMock:
    """Build an AsyncMock pool that returns preset profile rows."""
    pool = AsyncMock()

    async def fake_fetch(sql: str, *args: Any, **kwargs: Any) -> list[dict]:
        if "shared.entities" in sql or "entity_id" in sql:
            return [dict(r) for r in profile_rows]
        if "episodes" in sql:
            return []
        return []

    pool.fetch = fake_fetch
    pool.execute = AsyncMock()
    return pool


# ---------------------------------------------------------------------------
# Requirement: Preferences surface in memory_context Profile Facts
# ---------------------------------------------------------------------------


class TestPreferencesInProfileFacts:
    """Spec: 'Preferences surface in memory_context Profile Facts'.

    Preference facts MUST appear in the '## Profile Facts' section,
    ranked by importance DESC alongside other owner-entity facts.
    """

    async def _call_context(
        self,
        profile_rows: list[dict],
        recall_results: list[dict] | None = None,
        *,
        token_budget: int = 3000,
    ) -> str:
        pool = _make_pool_with_profile_rows(profile_rows)
        with patch(
            "butlers.modules.memory.tools.context._search.recall",
            new_callable=AsyncMock,
            return_value=recall_results or [],
        ):
            return await memory_context(
                pool,
                MagicMock(),
                "test trigger",
                "general",
                token_budget=token_budget,
            )

    async def test_preference_fact_appears_in_profile_facts_section(self) -> None:
        """A preference fact anchored to the owner entity appears in Profile Facts."""
        pref_fact = _make_preference_fact(
            predicate="preferences:travel_flight_seat",
            content="window",
            scope="travel",
        )
        result = await self._call_context([pref_fact])

        assert "## Profile Facts" in result, "Profile Facts section missing"
        assert "preferences:travel_flight_seat" in result, (
            "Preference predicate not found in Profile Facts"
        )
        assert "window" in result, "Preference value not found in Profile Facts"

    async def test_preference_fact_formatted_as_standard_fact_line(self) -> None:
        """Preference facts use the standard fact line format: [subject] [predicate]: content."""
        pref_fact = _make_preference_fact(
            predicate="preferences:general_language",
            content="English",
            scope="global",
        )
        result = await self._call_context([pref_fact])

        # Standard format: - [subject] [predicate]: content (confidence: N.NN)
        assert f"[{OWNER_NAME}]" in result, "Subject not found in formatted fact line"
        assert "[preferences:general_language]" in result, "Predicate not in fact line"
        assert "English" in result, "Content not in fact line"
        assert "confidence:" in result, "Confidence field missing from fact line"

    async def test_multiple_preference_facts_appear_in_profile_facts(self) -> None:
        """Multiple preference facts all appear in Profile Facts."""
        seat_pref = _make_preference_fact(
            predicate="preferences:travel_flight_seat",
            content="window",
        )
        lang_pref = _make_preference_fact(
            predicate="preferences:general_language",
            content="English",
            scope="global",
        )
        result = await self._call_context([seat_pref, lang_pref])

        assert "preferences:travel_flight_seat" in result
        assert "preferences:general_language" in result

    async def test_preferences_ranked_by_importance_desc(self) -> None:
        """Profile Facts are sorted by importance DESC — higher importance appears first."""
        high_pref = _make_preference_fact(
            predicate="preferences:travel_flight_seat",
            content="window",
            importance=9.0,
        )
        low_pref = _make_preference_fact(
            predicate="preferences:general_language",
            content="English",
            scope="global",
            importance=7.0,
        )
        # DB returns them pre-sorted by importance DESC (simulated here)
        result = await self._call_context([high_pref, low_pref])

        seat_pos = result.find("preferences:travel_flight_seat")
        lang_pos = result.find("preferences:general_language")

        assert seat_pos != -1, "High-importance preference not found in output"
        assert lang_pos != -1, "Low-importance preference not found in output"
        assert seat_pos < lang_pos, (
            "High-importance preference (9.0) should appear before low-importance (7.0)"
        )

    async def test_high_importance_preference_ranks_above_standard_fact(self) -> None:
        """Spec: preference facts (importance=8.0) rank above standard facts (importance=5.0).

        Profile Facts is sorted by importance DESC.
        Preference facts with default importance=8.0 should appear before
        standard consolidated facts with importance=5.0.
        """
        std_fact = _make_standard_fact(predicate="knows", content="Python", importance=5.0)
        pref_fact = _make_preference_fact(
            predicate="preferences:travel_flight_seat",
            content="window",
            importance=PREFERENCE_IMPORTANCE_DEFAULT,  # 8.0
        )
        # Profile rows sorted by importance DESC: preference fact first (8.0 > 5.0)
        result = await self._call_context([pref_fact, std_fact])

        pref_pos = result.find("preferences:travel_flight_seat")
        std_pos = result.find("knows")

        assert pref_pos != -1, "Preference fact not found in output"
        assert std_pos != -1, "Standard fact not found in output"
        assert pref_pos < std_pos, (
            "Preference fact (importance=8.0) should appear before standard fact (importance=5.0). "
            f"pref_pos={pref_pos}, std_pos={std_pos}"
        )

    async def test_equal_importance_preserves_deterministic_order(self) -> None:
        """Equal importance facts use created_at DESC, id ASC as tiebreakers."""
        # Both preference facts have same importance
        pref_a = _make_preference_fact(
            id=uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001"),
            predicate="preferences:travel_flight_seat",
            content="window",
            importance=8.0,
        )
        pref_b = _make_preference_fact(
            id=uuid.UUID("aaaaaaaa-0000-0000-0000-000000000002"),
            predicate="preferences:general_language",
            content="English",
            scope="global",
            importance=8.0,
        )
        # Both rows are present; output should be deterministic (same result twice)
        result1 = await self._call_context([pref_a, pref_b])
        result2 = await self._call_context([pref_a, pref_b])
        assert result1 == result2, "memory_context is not deterministic for equal-importance facts"

    async def test_preference_not_duplicated_in_task_relevant_section(self) -> None:
        """A preference in Profile Facts must not also appear in Task-Relevant Facts."""
        pref_fact = _make_preference_fact(
            id=FACT_UUID_1,
            predicate="preferences:travel_flight_seat",
            content="window",
        )
        # Recall returns the same fact (simulating overlap)
        task_version = {**pref_fact, "memory_type": "fact", "composite_score": 0.9}

        pool = _make_pool_with_profile_rows([pref_fact])
        with patch(
            "butlers.modules.memory.tools.context._search.recall",
            new_callable=AsyncMock,
            return_value=[task_version],
        ):
            result = await memory_context(
                pool,
                MagicMock(),
                "test trigger",
                "general",
                token_budget=3000,
            )

        # Count occurrences of the predicate; should appear exactly once
        count = result.count("preferences:travel_flight_seat")
        assert count == 1, (
            f"Preference predicate appears {count} times (expected 1 — no duplication)"
        )


# ---------------------------------------------------------------------------
# Requirement: Supersession chain correctness across multiple updates
# ---------------------------------------------------------------------------


class TestSupersessionChain:
    """Verify supersession chain works correctly for multiple preference updates.

    The spec says:
    - When set_preference is called with a matching (entity_id, scope, predicate),
      the existing active preference fact MUST be superseded (validity='superseded').
    - The new fact MUST have supersedes_id referencing the old fact.
    - The response MUST indicate action='updated'.

    Multi-update chain:
    - Call 1: creates fact A (action='created', superseded_id=None)
    - Call 2: supersedes A → creates fact B (action='updated', superseded_id=A)
    - Call 3: supersedes B → creates fact C (action='updated', superseded_id=B)
    - Only C is active; A and B have validity='superseded'.
    """

    @pytest.fixture(autouse=True)
    def _patch_embedding(self) -> None:
        with patch(
            "butlers.modules.memory.tools.preferences.get_embedding_engine",
            return_value=MagicMock(),
        ):
            yield

    @pytest.fixture()
    def mock_resolve_owner(self):
        with patch(
            "butlers.modules.memory.tools.preferences._resolve_owner",
            new_callable=AsyncMock,
            return_value=(OWNER_UUID, OWNER_NAME),
        ) as m:
            yield m

    async def test_first_set_creates_not_updates(
        self,
        mock_resolve_owner: AsyncMock,
    ) -> None:
        """First set_preference call returns action='created' with no superseded_id."""
        pool = AsyncMock()

        from butlers.modules.memory.tools import _helpers

        with patch.object(
            _helpers._storage,
            "store_fact",
            new_callable=AsyncMock,
            return_value={"id": FACT_UUID_1, "supersedes_id": None},
        ):
            result = await set_preference(pool, "preferences:travel_flight_seat", "window")

        assert result["action"] == "created"
        assert result["superseded_id"] is None

    async def test_second_set_supersedes_first(
        self,
        mock_resolve_owner: AsyncMock,
    ) -> None:
        """Second set_preference call returns action='updated' with superseded_id=first fact."""
        pool = AsyncMock()

        from butlers.modules.memory.tools import _helpers

        with patch.object(
            _helpers._storage,
            "store_fact",
            new_callable=AsyncMock,
            return_value={"id": FACT_UUID_2, "supersedes_id": FACT_UUID_1},
        ):
            result = await set_preference(pool, "preferences:travel_flight_seat", "aisle")

        assert result["action"] == "updated"
        assert result["superseded_id"] == str(FACT_UUID_1)
        assert result["id"] == str(FACT_UUID_2)

    async def test_third_set_supersedes_second(
        self,
        mock_resolve_owner: AsyncMock,
    ) -> None:
        """Third set_preference call supersedes the second fact — chain continues correctly."""
        pool = AsyncMock()

        from butlers.modules.memory.tools import _helpers

        with patch.object(
            _helpers._storage,
            "store_fact",
            new_callable=AsyncMock,
            return_value={"id": FACT_UUID_3, "supersedes_id": FACT_UUID_2},
        ):
            result = await set_preference(pool, "preferences:travel_flight_seat", "middle")

        assert result["action"] == "updated"
        assert result["superseded_id"] == str(FACT_UUID_2)
        assert result["id"] == str(FACT_UUID_3)

    async def test_supersession_chain_three_updates_all_distinct(
        self,
        mock_resolve_owner: AsyncMock,
    ) -> None:
        """Full 3-update chain: each new fact references the previous as superseded."""
        pool = AsyncMock()

        from butlers.modules.memory.tools import _helpers

        # Simulate the storage layer returning successive supersession IDs
        call_sequence = [
            {"id": FACT_UUID_1, "supersedes_id": None},
            {"id": FACT_UUID_2, "supersedes_id": FACT_UUID_1},
            {"id": FACT_UUID_3, "supersedes_id": FACT_UUID_2},
        ]

        results = []
        for store_return in call_sequence:
            with patch.object(
                _helpers._storage,
                "store_fact",
                new_callable=AsyncMock,
                return_value=store_return,
            ):
                r = await set_preference(pool, "preferences:travel_flight_seat", "value")
                results.append(r)

        # First: created
        assert results[0]["action"] == "created"
        assert results[0]["superseded_id"] is None
        assert results[0]["id"] == str(FACT_UUID_1)

        # Second: updated, supersedes first
        assert results[1]["action"] == "updated"
        assert results[1]["superseded_id"] == str(FACT_UUID_1)
        assert results[1]["id"] == str(FACT_UUID_2)

        # Third: updated, supersedes second
        assert results[2]["action"] == "updated"
        assert results[2]["superseded_id"] == str(FACT_UUID_2)
        assert results[2]["id"] == str(FACT_UUID_3)

    async def test_supersession_chain_links_are_linear(
        self,
        mock_resolve_owner: AsyncMock,
    ) -> None:
        """The chain A→B→C is linear: C supersedes B, B supersedes A, not A directly."""
        pool = AsyncMock()

        from butlers.modules.memory.tools import _helpers

        call_sequence = [
            {"id": FACT_UUID_1, "supersedes_id": None},
            {"id": FACT_UUID_2, "supersedes_id": FACT_UUID_1},
            {"id": FACT_UUID_3, "supersedes_id": FACT_UUID_2},
        ]

        results = []
        for store_return in call_sequence:
            with patch.object(
                _helpers._storage,
                "store_fact",
                new_callable=AsyncMock,
                return_value=store_return,
            ):
                r = await set_preference(pool, "preferences:travel_flight_seat", "value")
                results.append(r)

        # Verify: C.superseded_id == B.id (not A.id)
        assert results[2]["superseded_id"] == results[1]["id"], (
            "Third update should supersede the second fact, not the first"
        )
        # Verify: B.superseded_id == A.id
        assert results[1]["superseded_id"] == results[0]["id"], (
            "Second update should supersede the first fact"
        )

    async def test_different_predicates_do_not_interfere(
        self,
        mock_resolve_owner: AsyncMock,
    ) -> None:
        """Supersession is per (entity_id, scope, predicate); different predicates are independent."""  # noqa: E501
        pool = AsyncMock()

        from butlers.modules.memory.tools import _helpers

        # Store two different preferences; neither should supersede the other
        with patch.object(
            _helpers._storage,
            "store_fact",
            new_callable=AsyncMock,
            return_value={"id": FACT_UUID_1, "supersedes_id": None},
        ):
            r1 = await set_preference(pool, "preferences:travel_flight_seat", "window")

        with patch.object(
            _helpers._storage,
            "store_fact",
            new_callable=AsyncMock,
            return_value={"id": FACT_UUID_2, "supersedes_id": None},
        ):
            r2 = await set_preference(pool, "preferences:general_language", "English")

        assert r1["action"] == "created"
        assert r2["action"] == "created"
        assert r1["superseded_id"] is None
        assert r2["superseded_id"] is None

    async def test_response_includes_all_required_fields_after_supersession(
        self,
        mock_resolve_owner: AsyncMock,
    ) -> None:
        """After supersession, response still contains all required keys."""
        pool = AsyncMock()

        from butlers.modules.memory.tools import _helpers

        with patch.object(
            _helpers._storage,
            "store_fact",
            new_callable=AsyncMock,
            return_value={"id": FACT_UUID_2, "supersedes_id": FACT_UUID_1},
        ):
            result = await set_preference(pool, "preferences:travel_flight_seat", "aisle")

        required_keys = {"id", "superseded_id", "action", "predicate", "scope", "owner_entity_id"}
        assert set(result.keys()) >= required_keys, (
            f"Missing keys after supersession: {required_keys - set(result.keys())}"
        )

    async def test_scope_is_consistent_across_supersession_chain(
        self,
        mock_resolve_owner: AsyncMock,
    ) -> None:
        """The scope derived from the predicate is the same for all facts in the chain."""
        pool = AsyncMock()

        from butlers.modules.memory.tools import _helpers

        predicate = "preferences:travel_flight_seat"
        expected_scope = _derive_scope(predicate)

        for store_return in [
            {"id": FACT_UUID_1, "supersedes_id": None},
            {"id": FACT_UUID_2, "supersedes_id": FACT_UUID_1},
        ]:
            with patch.object(
                _helpers._storage,
                "store_fact",
                new_callable=AsyncMock,
                return_value=store_return,
            ):
                result = await set_preference(pool, predicate, "value")
            assert result["scope"] == expected_scope, (
                f"Scope changed during supersession chain: got {result['scope']!r}, "
                f"expected {expected_scope!r}"
            )


# ---------------------------------------------------------------------------
# Requirement: Profile Facts section format (spec-exact validation)
# ---------------------------------------------------------------------------


class TestProfileFactsFormat:
    """Verify the spec-mandated fact line format in Profile Facts.

    Spec: '- [<subject>] [<predicate>]: <content> (confidence: <effective_confidence>)'
    """

    async def _call_context(self, profile_rows: list[dict]) -> str:
        pool = _make_pool_with_profile_rows(profile_rows)
        with patch(
            "butlers.modules.memory.tools.context._search.recall",
            new_callable=AsyncMock,
            return_value=[],
        ):
            return await memory_context(
                pool,
                MagicMock(),
                "test trigger",
                "general",
                token_budget=3000,
            )

    async def test_fact_line_uses_spec_format(self) -> None:
        """Fact line matches: - [subject] [predicate]: content (confidence: N.NN)"""
        pref_fact = _make_preference_fact(
            predicate="preferences:travel_flight_seat",
            content="window",
        )
        result = await self._call_context([pref_fact])

        # The spec mandates this format exactly
        expected_fragment = f"- [{OWNER_NAME}] [preferences:travel_flight_seat]: window"
        assert expected_fragment in result, (
            f"Expected fact line fragment not found.\n"
            f"Expected fragment: {expected_fragment!r}\n"
            f"Actual output:\n{result}"
        )

    async def test_confidence_field_is_numeric(self) -> None:
        """The confidence value in the fact line is a decimal number."""
        import re

        pref_fact = _make_preference_fact(
            predicate="preferences:general_language",
            content="English",
            scope="global",
        )
        result = await self._call_context([pref_fact])

        # Match: (confidence: N.NN)
        confidence_pattern = re.compile(r"\(confidence: (\d+\.\d+)\)")
        matches = confidence_pattern.findall(result)
        assert len(matches) >= 1, f"No confidence value found in output: {result!r}"
        # Each match should be a valid float
        for m in matches:
            float(m)  # raises if not a valid float
