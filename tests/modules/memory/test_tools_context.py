"""Tests for the deterministic memory context section compiler."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.modules.memory.tools.context import (
    _effective_confidence,
    _fill_section,
    _format_episode_line,
    _format_fact_line,
    _format_rule_line,
    memory_context,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers / Fixtures
# ---------------------------------------------------------------------------


def _make_fact(
    *,
    id: str | None = None,
    subject: str = "User",
    predicate: str = "likes",
    content: str = "coffee",
    importance: float = 5.0,
    confidence: float = 1.0,
    decay_rate: float = 0.0,
    last_confirmed_at: datetime | None = None,
    memory_type: str = "fact",
    composite_score: float = 0.5,
    created_at: datetime | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    return {
        "id": uuid.UUID(id) if id else uuid.uuid4(),
        "subject": subject,
        "predicate": predicate,
        "content": content,
        "importance": importance,
        "confidence": confidence,
        "decay_rate": decay_rate,
        "last_confirmed_at": last_confirmed_at,
        "memory_type": memory_type,
        "composite_score": composite_score,
        "created_at": created_at or datetime(2026, 1, 1, tzinfo=UTC),
        **kwargs,
    }


def _make_rule(
    *,
    id: str | None = None,
    content: str = "Be concise",
    maturity: str = "candidate",
    effectiveness_score: float = 0.5,
    composite_score: float = 0.4,
    created_at: datetime | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    return {
        "id": uuid.uuid4() if id is None else uuid.UUID(id),
        "content": content,
        "maturity": maturity,
        "effectiveness_score": effectiveness_score,
        "memory_type": "rule",
        "composite_score": composite_score,
        "created_at": created_at or datetime(2026, 1, 1, tzinfo=UTC),
        **kwargs,
    }


def _make_episode(
    *,
    content: str = "Had a meeting",
    created_at: datetime | None = None,
    butler: str = "general",
) -> dict[str, Any]:
    return {
        "id": uuid.uuid4(),
        "content": content,
        "butler": butler,
        "created_at": created_at or datetime(2026, 1, 15, tzinfo=UTC),
    }


def _make_pool(profile_rows=None, episode_rows=None) -> AsyncMock:
    """Return an AsyncMock pool that returns preset rows for fetch calls."""
    pool = AsyncMock()
    # First fetch call → profile facts; second → episodes (if any)
    profile_rows = [dict(r) for r in (profile_rows or [])]
    episode_rows = [dict(r) for r in (episode_rows or [])]

    call_count = {"n": 0}

    async def fake_fetch(sql, *args, **kwargs):
        call_count["n"] += 1
        if "shared.entities" in sql or "entity_id" in sql:
            return profile_rows
        if "episodes" in sql:
            return episode_rows
        return []

    pool.fetch = fake_fetch
    pool.execute = AsyncMock()
    return pool


# ---------------------------------------------------------------------------
# Unit tests: helper functions
# ---------------------------------------------------------------------------


class TestEffectiveConfidence:
    """_effective_confidence computes decayed confidence."""

    def test_zero_decay_returns_confidence_unchanged(self):
        row = {"confidence": 0.8, "decay_rate": 0.0, "last_confirmed_at": None}
        assert _effective_confidence(row) == 0.8

    def test_none_last_confirmed_at_returns_zero_when_nonzero_decay(self):
        row = {"confidence": 1.0, "decay_rate": 0.01, "last_confirmed_at": None}
        assert _effective_confidence(row) == 0.0

    def test_recent_confirmation_minimal_decay(self):
        now = datetime.now(UTC)
        row = {"confidence": 1.0, "decay_rate": 0.008, "last_confirmed_at": now}
        eff = _effective_confidence(row)
        assert 0.99 < eff <= 1.0  # essentially no decay

    def test_old_confirmation_significant_decay(self):
        from datetime import timedelta

        old = datetime.now(UTC) - timedelta(days=100)
        row = {"confidence": 1.0, "decay_rate": 0.008, "last_confirmed_at": old}
        eff = _effective_confidence(row)
        assert eff < 0.5  # substantially decayed


class TestFormatFactLine:
    def test_formats_correctly(self):
        row = {
            "subject": "User",
            "predicate": "likes",
            "content": "espresso",
            "confidence": 1.0,
            "decay_rate": 0.0,
            "last_confirmed_at": None,
        }
        line = _format_fact_line(row)
        assert line == "- [User] [likes]: espresso (confidence: 1.00)\n"

    def test_missing_fields_use_question_marks(self):
        line = _format_fact_line({})
        assert "- [?] [?]:" in line


class TestFormatRuleLine:
    def test_formats_correctly(self):
        row = {"content": "Be concise", "maturity": "established", "effectiveness_score": 0.75}
        line = _format_rule_line(row)
        assert line == "- Be concise (maturity: established, effectiveness: 0.75)\n"


class TestFormatEpisodeLine:
    def test_formats_with_timestamp(self):
        dt = datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)
        row = {"content": "User asked about coffee", "created_at": dt}
        line = _format_episode_line(row)
        assert "User asked about coffee" in line
        assert "2026-03-01" in line

    def test_formats_without_timestamp(self):
        row = {"content": "Some episode", "created_at": None}
        line = _format_episode_line(row)
        assert "- Some episode\n" == line


class TestFillSection:
    def test_empty_items_returns_empty_string(self):
        result = _fill_section("\n## Test\n", [], _format_fact_line, 1000)
        assert result == ""

    def test_header_too_large_returns_empty_string(self):
        items = [_make_fact()]
        result = _fill_section("\n## Test\n", items, _format_fact_line, 5)  # tiny budget
        assert result == ""

    def test_adds_items_within_budget(self):
        items = [_make_fact(content="short")]
        result = _fill_section("\n## Test\n", items, _format_fact_line, 10000)
        assert "\n## Test\n" in result
        assert "short" in result

    def test_stops_when_budget_exhausted(self):
        # Use many items but tiny budget after header
        items = [_make_fact(content=f"fact-{i}") for i in range(20)]
        header = "\n## Test\n"
        budget = len(header) + 60  # only fits ~1 item
        result = _fill_section(header, items, _format_fact_line, budget)
        # Should have header + at most one item
        assert "\n## Test\n" in result
        item_count = result.count("\n- [")
        assert item_count <= 2  # at most a couple of short ones

    def test_returns_empty_when_no_items_fit_after_header(self):
        header = "\n## Test\n"
        # Budget exactly equal to header length → no items can fit
        items = [_make_fact(content="x" * 200)]
        result = _fill_section(header, items, _format_fact_line, len(header))
        assert result == ""


# ---------------------------------------------------------------------------
# Integration tests: memory_context()
# ---------------------------------------------------------------------------


class TestMemoryContext:
    """memory_context() — deterministic section compiler."""

    async def _call_context(
        self,
        recall_results: list[dict],
        profile_rows: list[dict] | None = None,
        episode_rows: list[dict] | None = None,
        *,
        token_budget: int = 3000,
        include_recent_episodes: bool = False,
        request_context: dict | None = None,
        butler: str = "general",
    ) -> str:
        pool = _make_pool(profile_rows or [], episode_rows or [])
        embedding_engine = MagicMock()

        with patch(
            "butlers.modules.memory.tools.context._search.recall",
            new_callable=AsyncMock,
            return_value=recall_results,
        ):
            return await memory_context(
                pool,
                embedding_engine,
                "test prompt",
                butler,
                token_budget=token_budget,
                include_recent_episodes=include_recent_episodes,
                request_context=request_context,
            )

    async def test_empty_results_returns_just_header(self):
        result = await self._call_context([])
        assert result == "# Memory Context\n"

    async def test_preamble_always_present(self):
        result = await self._call_context([_make_fact()])
        assert result.startswith("# Memory Context\n")

    async def test_task_relevant_facts_section_present(self):
        fact = _make_fact(subject="User", predicate="hobby", content="cycling")
        result = await self._call_context([fact])
        assert "## Task-Relevant Facts" in result
        assert "cycling" in result

    async def test_active_rules_section_present(self):
        rule = _make_rule(content="Always respond politely")
        result = await self._call_context([rule])
        assert "## Active Rules" in result
        assert "Always respond politely" in result

    async def test_recent_episodes_absent_by_default(self):
        result = await self._call_context([], episode_rows=[_make_episode()])
        assert "## Recent Episodes" not in result

    async def test_recent_episodes_present_when_opted_in(self):
        result = await self._call_context(
            [],
            episode_rows=[_make_episode(content="Discussed budget")],
            include_recent_episodes=True,
        )
        assert "## Recent Episodes" in result
        assert "Discussed budget" in result

    async def test_empty_sections_omitted(self):
        # Only a rule — no facts, no episodes
        rule = _make_rule(content="Test rule")
        result = await self._call_context([rule])
        assert "## Profile Facts" not in result
        assert "## Task-Relevant Facts" not in result
        assert "## Recent Episodes" not in result
        assert "## Active Rules" in result

    async def test_profile_facts_excluded_from_task_relevant(self):
        """A fact shown in Profile Facts must not appear in Task-Relevant Facts."""
        fact_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        profile_fact = _make_fact(id=fact_id, subject="Owner", predicate="name", content="Alice")
        # recall_results also returns the same fact (simulating overlap)
        task_fact = {**profile_fact, "memory_type": "fact", "composite_score": 0.9}
        other_fact = _make_fact(subject="City", predicate="is", content="Nairobi")
        other_task = {**other_fact, "memory_type": "fact", "composite_score": 0.8}

        pool = _make_pool(profile_rows=[profile_fact], episode_rows=[])

        with patch(
            "butlers.modules.memory.tools.context._search.recall",
            new_callable=AsyncMock,
            return_value=[task_fact, other_task],
        ):
            result = await memory_context(
                pool,
                MagicMock(),
                "prompt",
                "general",
                token_budget=3000,
            )

        # Alice should appear in Profile Facts (from profile_rows)
        # Nairobi should appear in Task-Relevant Facts
        # Alice should NOT appear again in Task-Relevant Facts
        assert "Alice" in result
        assert "Nairobi" in result

        # Count occurrences of "Alice" — should appear only once
        assert result.count("Alice") == 1

    async def test_determinism_same_inputs_same_output(self):
        facts = [_make_fact(content=f"fact-{i}", composite_score=float(i)) for i in range(5)]
        rules = [_make_rule(content=f"rule-{i}") for i in range(3)]
        items = facts + rules

        result1 = await self._call_context(items)
        result2 = await self._call_context(items)
        assert result1 == result2

    async def test_section_order(self):
        """Sections appear in the specified order: Profile, Task, Rules, Episodes."""
        profile_fact = _make_fact(subject="Owner", predicate="pref", content="tea")
        task_fact = _make_fact(subject="City", predicate="is", content="London")
        rule = _make_rule(content="Reply briefly")
        episode = _make_episode(content="Morning meeting")

        pool = _make_pool(profile_rows=[profile_fact], episode_rows=[episode])
        embedding_engine = MagicMock()

        with patch(
            "butlers.modules.memory.tools.context._search.recall",
            new_callable=AsyncMock,
            return_value=[
                {**task_fact, "memory_type": "fact", "composite_score": 0.8},
                {**rule, "memory_type": "rule", "composite_score": 0.6},
            ],
        ):
            result = await memory_context(
                pool,
                embedding_engine,
                "prompt",
                "general",
                token_budget=3000,
                include_recent_episodes=True,
            )

        profile_pos = result.find("## Profile Facts")
        task_pos = result.find("## Task-Relevant Facts")
        rules_pos = result.find("## Active Rules")
        episodes_pos = result.find("## Recent Episodes")

        assert profile_pos < task_pos < rules_pos < episodes_pos

    async def test_token_budget_respected(self):
        """Output must not exceed token_budget * 4 characters."""
        facts = [_make_fact(content="x" * 200) for _ in range(50)]
        rules = [_make_rule(content="y" * 200) for _ in range(20)]
        token_budget = 500
        result = await self._call_context(
            facts + rules, token_budget=token_budget, include_recent_episodes=False
        )
        assert len(result) <= token_budget * 4 + 50  # small slack for the preamble/headers

    async def test_request_context_tenant_id_used(self):
        """request_context['tenant_id'] is propagated to the recall call."""
        fact = _make_fact()
        pool = _make_pool()

        captured: dict = {}

        async def fake_recall(pool, topic, engine, *, scope, limit, tenant_id, **kw):
            captured["tenant_id"] = tenant_id
            return [fact]

        with patch(
            "butlers.modules.memory.tools.context._search.recall",
            side_effect=fake_recall,
        ):
            await memory_context(
                pool,
                MagicMock(),
                "prompt",
                "general",
                request_context={"tenant_id": "custom-tenant"},
            )

        assert captured["tenant_id"] == "custom-tenant"

    async def test_request_context_none_defaults_to_owner(self):
        """When request_context is None, tenant_id defaults to 'owner'."""
        pool = _make_pool()
        captured: dict = {}

        async def fake_recall(pool, topic, engine, *, scope, limit, tenant_id, **kw):
            captured["tenant_id"] = tenant_id
            return []

        with patch(
            "butlers.modules.memory.tools.context._search.recall",
            side_effect=fake_recall,
        ):
            await memory_context(pool, MagicMock(), "prompt", "general")

        assert captured["tenant_id"] == "owner"

    async def test_rules_sorted_by_maturity_rank(self):
        """Proven rules appear before candidate rules."""
        candidate = _make_rule(
            content="candidate-rule",
            maturity="candidate",
            effectiveness_score=0.9,
        )
        proven = _make_rule(
            content="proven-rule",
            maturity="proven",
            effectiveness_score=0.1,
        )
        result = await self._call_context([candidate, proven])
        proven_pos = result.find("proven-rule")
        candidate_pos = result.find("candidate-rule")
        assert proven_pos < candidate_pos

    async def test_profile_facts_absent_when_no_owner_entity(self):
        """No Profile Facts section when owner entity lookup returns empty."""
        fact = _make_fact(subject="Topic", predicate="info", content="data")
        result = await self._call_context(
            [{**fact, "memory_type": "fact", "composite_score": 0.7}],
            profile_rows=[],  # no owner entity facts
        )
        assert "## Profile Facts" not in result
        assert "## Task-Relevant Facts" in result

    async def test_pool_fetch_error_returns_empty_profile_facts(self):
        """If the profile facts query fails, section is omitted gracefully."""
        pool = AsyncMock()
        pool.fetch = AsyncMock(side_effect=Exception("DB error"))
        pool.execute = AsyncMock()

        fact = _make_fact()

        with patch(
            "butlers.modules.memory.tools.context._search.recall",
            new_callable=AsyncMock,
            return_value=[fact],
        ):
            result = await memory_context(pool, MagicMock(), "prompt", "general")

        # Should not crash; Profile Facts omitted, Task-Relevant Facts may appear
        assert result.startswith("# Memory Context\n")
