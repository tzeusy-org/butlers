"""Tests verifying that preference facts surface in memory_context Profile Facts output.

Per spec requirement: "Preferences surface in memory_context Profile Facts"
Preference facts with high importance (8.0) anchored to the owner entity
appear in the Profile Facts section via the existing owner-entity fact query,
with no changes to the memory_context code path.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.modules.memory.tools.context import (
    _format_fact_line,
    memory_context,
)

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OWNER_UUID = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
NOW = datetime(2026, 3, 25, 12, 0, 0, tzinfo=UTC)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_preference_fact(
    predicate: str = "preferences:travel_flight_seat",
    content: str = "window",
    scope: str = "travel",
    importance: float = 8.0,
    subject: str = "Alice",
    confidence: float = 1.0,
    decay_rate: float = 0.002,
) -> dict[str, Any]:
    """Build a preference fact row (as returned by _fetch_profile_facts)."""
    return {
        "id": uuid.uuid4(),
        "subject": subject,
        "predicate": predicate,
        "content": content,
        "scope": scope,
        "importance": importance,
        "confidence": confidence,
        "decay_rate": decay_rate,
        "last_confirmed_at": NOW,
        "created_at": NOW,
        "validity": "active",
        "entity_id": OWNER_UUID,
        "memory_type": "fact",
        "composite_score": 0.8,
        "permanence": "stable",
    }


def _make_standard_fact(
    predicate: str = "likes",
    content: str = "coffee",
    importance: float = 5.0,
    subject: str = "Alice",
) -> dict[str, Any]:
    """Build a standard (non-preference) fact row."""
    return {
        "id": uuid.uuid4(),
        "subject": subject,
        "predicate": predicate,
        "content": content,
        "scope": "global",
        "importance": importance,
        "confidence": 1.0,
        "decay_rate": 0.0,
        "last_confirmed_at": NOW,
        "created_at": NOW,
        "validity": "active",
        "entity_id": OWNER_UUID,
        "memory_type": "fact",
        "composite_score": 0.5,
        "permanence": "ephemeral",
    }


def _make_pool_with_profile(profile_rows: list[dict]) -> AsyncMock:
    """Build a mock pool that returns preset profile fact rows."""
    pool = AsyncMock()
    profile_dicts = [dict(r) for r in profile_rows]

    async def fake_fetch(sql: str, *args: Any, **kwargs: Any) -> list[dict]:
        if "shared.entities" in sql or "entity_id" in sql:
            return profile_dicts
        if "episodes" in sql:
            return []
        return []

    pool.fetch = fake_fetch
    pool.execute = AsyncMock()
    return pool


# ---------------------------------------------------------------------------
# Tests — preference facts in Profile Facts
# ---------------------------------------------------------------------------


class TestPreferencesInProfileFacts:
    """Preference facts with high importance surface in Profile Facts section."""

    async def _call_context(
        self,
        profile_rows: list[dict],
        recall_results: list[dict] | None = None,
        *,
        token_budget: int = 3000,
    ) -> str:
        pool = _make_pool_with_profile(profile_rows)
        engine = MagicMock()
        with patch(
            "butlers.modules.memory.tools.context._search.recall",
            new_callable=AsyncMock,
            return_value=recall_results or [],
        ):
            return await memory_context(
                pool,
                engine,
                "test prompt",
                "general",
                token_budget=token_budget,
            )

    async def test_preference_fact_appears_in_profile_facts_section(self) -> None:
        """A preference fact anchored to the owner entity appears in Profile Facts."""
        pref_fact = _make_preference_fact(
            predicate="preferences:travel_flight_seat",
            content="window",
            subject="Alice",
        )
        result = await self._call_context([pref_fact])
        assert "## Profile Facts" in result
        assert "window" in result

    async def test_preference_predicate_visible_in_profile_facts(self) -> None:
        """The full preference predicate string appears in Profile Facts output."""
        pref_fact = _make_preference_fact(
            predicate="preferences:general_language",
            content="English",
            subject="Alice",
            scope="global",
        )
        result = await self._call_context([pref_fact])
        assert "preferences:general_language" in result

    async def test_preference_fact_formatted_as_standard_fact_line(self) -> None:
        """Preference facts use the standard [subject] [predicate]: content format."""
        pref_fact = _make_preference_fact(
            predicate="preferences:travel_flight_seat",
            content="aisle",
            subject="Bob",
        )
        result = await self._call_context([pref_fact])
        # Standard format: - [Bob] [preferences:travel_flight_seat]: aisle
        assert "[Bob]" in result
        assert "[preferences:travel_flight_seat]" in result
        assert "aisle" in result

    async def test_high_importance_preference_ranks_above_standard_fact(self) -> None:
        """Preference facts (importance=8.0) appear before standard facts (importance=5.0)."""
        pref_fact = _make_preference_fact(
            predicate="preferences:travel_flight_seat",
            content="window",
            importance=8.0,
        )
        standard_fact = _make_standard_fact(
            predicate="likes",
            content="coffee",
            importance=5.0,
        )
        result = await self._call_context([pref_fact, standard_fact])
        assert "## Profile Facts" in result
        pref_pos = result.find("window")
        standard_pos = result.find("coffee")
        assert pref_pos < standard_pos, (
            "High-importance preference fact must appear before lower-importance standard fact"
        )

    async def test_multiple_preference_facts_all_appear_in_profile(self) -> None:
        """Multiple preference facts all appear in Profile Facts."""
        facts = [
            _make_preference_fact(
                predicate="preferences:travel_flight_seat",
                content="window",
                scope="travel",
            ),
            _make_preference_fact(
                predicate="preferences:general_language",
                content="English",
                scope="global",
                importance=7.5,
            ),
            _make_preference_fact(
                predicate="preferences:health_dietary_restriction",
                content="no shellfish",
                scope="health",
            ),
        ]
        result = await self._call_context(facts)
        assert "window" in result
        assert "English" in result
        assert "no shellfish" in result

    async def test_preference_fact_not_duplicated_in_task_relevant(self) -> None:
        """A fact shown in Profile Facts must not appear again in Task-Relevant Facts."""
        pref_fact = _make_preference_fact(
            predicate="preferences:travel_flight_seat",
            content="window",
            subject="Alice",
        )
        # Simulate recall also returning the same preference fact
        task_pref = {**pref_fact, "composite_score": 0.9}
        other_fact = _make_standard_fact(predicate="lives_in", content="Berlin")
        other_task = {**other_fact, "composite_score": 0.7}

        pool = _make_pool_with_profile([pref_fact])
        engine = MagicMock()
        with patch(
            "butlers.modules.memory.tools.context._search.recall",
            new_callable=AsyncMock,
            return_value=[task_pref, other_task],
        ):
            result = await memory_context(pool, engine, "prompt", "general", token_budget=3000)

        # "window" should appear exactly once (in Profile Facts, not duplicated in Task)
        assert result.count("window") == 1
        # Other task fact should still appear
        assert "Berlin" in result

    async def test_empty_profile_no_preference_section(self) -> None:
        """When no preference facts exist, Profile Facts section is absent."""
        result = await self._call_context([])
        assert "preferences:" not in result
        assert "## Profile Facts" not in result

    async def test_preference_confidence_shown_in_output(self) -> None:
        """Profile Facts include confidence value for preference facts."""
        pref_fact = _make_preference_fact(
            predicate="preferences:travel_flight_seat",
            content="window",
            confidence=1.0,
            decay_rate=0.0,
        )
        result = await self._call_context([pref_fact])
        # Standard line format includes (confidence: <value>)
        assert "confidence:" in result

    async def test_preference_with_stable_permanence_has_low_decay(self) -> None:
        """Stable permanence (no decay) still shows full confidence in Profile Facts."""
        pref_fact = _make_preference_fact(
            predicate="preferences:general_timezone",
            content="America/New_York",
            confidence=1.0,
            decay_rate=0.0,  # no decay — confidence is time-independent
        )
        result = await self._call_context([pref_fact])
        assert "America/New_York" in result
        # No decay means confidence stays at 1.00 regardless of when the test runs
        assert "confidence: 1.00" in result


# ---------------------------------------------------------------------------
# Tests — _format_fact_line with preference predicates
# ---------------------------------------------------------------------------


class TestFormatFactLineWithPreferences:
    """_format_fact_line renders preference predicates correctly."""

    def test_format_preference_fact_line(self) -> None:
        """Preference predicate formatted with standard fact line format."""
        row = {
            "subject": "Alice",
            "predicate": "preferences:travel_flight_seat",
            "content": "window",
            "confidence": 1.0,
            "decay_rate": 0.0,
            "last_confirmed_at": None,
        }
        line = _format_fact_line(row)
        assert "- [Alice] [preferences:travel_flight_seat]: window" in line
        assert "(confidence:" in line

    def test_format_general_preference_fact_line(self) -> None:
        """General scope preference predicate formatted correctly."""
        row = {
            "subject": "Alice",
            "predicate": "preferences:general_language",
            "content": "English",
            "confidence": 1.0,
            "decay_rate": 0.0,
            "last_confirmed_at": None,
        }
        line = _format_fact_line(row)
        assert "- [Alice] [preferences:general_language]: English" in line

    def test_format_health_preference_fact_line(self) -> None:
        """Health domain preference predicate formatted correctly."""
        row = {
            "subject": "Alice",
            "predicate": "preferences:health_dietary_restriction",
            "content": "no shellfish",
            "confidence": 0.95,
            "decay_rate": 0.0,
            "last_confirmed_at": None,
        }
        line = _format_fact_line(row)
        assert "- [Alice] [preferences:health_dietary_restriction]: no shellfish" in line
