"""Behavioral tests for memory management MCP tools.

Covers: memory_stats, predicate_list, memory_context section compiler.
(memory_forget is tested in test_tools_reading.py)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.modules.memory.tools import memory_stats, predicate_list
from butlers.modules.memory.tools.context import memory_context

pytestmark = pytest.mark.unit


@pytest.fixture()
def pool() -> AsyncMock:
    pool = AsyncMock()
    pool.fetchval = AsyncMock(return_value=0)
    pool.fetch = AsyncMock(return_value=[])
    return pool


# ---------------------------------------------------------------------------
# memory_stats
# ---------------------------------------------------------------------------


class TestMemoryStats:
    async def test_result_shape(self, pool: AsyncMock) -> None:
        result = await memory_stats(pool)
        assert set(result.keys()) == {"episodes", "facts", "rules"}
        assert set(result["episodes"].keys()) == {"total", "unconsolidated", "backlog_age_hours"}
        assert set(result["facts"].keys()) == {"active", "fading", "superseded", "expired"}
        assert set(result["rules"].keys()) == {
            "candidate",
            "established",
            "proven",
            "anti_pattern",
            "forgotten",
        }

    async def test_returns_integer_counts(self, pool: AsyncMock) -> None:
        pool.fetchval = AsyncMock(return_value=5)
        result = await memory_stats(pool)
        assert result["episodes"]["total"] == 5


# ---------------------------------------------------------------------------
# predicate_list
# ---------------------------------------------------------------------------


class TestPredicateList:
    async def test_returns_empty_list_when_no_predicates(self, pool: AsyncMock) -> None:
        pool.fetch = AsyncMock(return_value=[])
        result = await predicate_list(pool)
        assert result == []


# ---------------------------------------------------------------------------
# memory_context
# ---------------------------------------------------------------------------


def _fact(content: str = "x", memory_type: str = "fact", composite_score: float = 0.5) -> dict:
    return {
        "id": uuid.uuid4(),
        "subject": "User",
        "predicate": "info",
        "content": content,
        "importance": 5.0,
        "confidence": 1.0,
        "decay_rate": 0.0,
        "last_confirmed_at": None,
        "memory_type": memory_type,
        "composite_score": composite_score,
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
    }


def _rule(content: str = "rule", maturity: str = "candidate") -> dict:
    return {
        "id": uuid.uuid4(),
        "content": content,
        "maturity": maturity,
        "effectiveness_score": 0.5,
        "memory_type": "rule",
        "composite_score": 0.4,
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
    }


async def _call_context(
    recall_items: list[dict[str, Any]],
    profile_rows: list[dict[str, Any]] | None = None,
    *,
    include_episodes: bool = False,
    token_budget: int = 3000,
    request_context: dict | None = None,
) -> str:
    pool = AsyncMock()

    async def _fake_fetch(sql: str, *args: Any, **kwargs: Any) -> list[dict]:
        if "episodes" in sql:
            return []
        return [dict(r) for r in (profile_rows or [])]

    pool.fetch = _fake_fetch
    pool.execute = AsyncMock()

    with patch(
        "butlers.modules.memory.tools.context._search.recall",
        new_callable=AsyncMock,
        return_value=recall_items,
    ):
        return await memory_context(
            pool,
            MagicMock(),
            "test prompt",
            "general",
            token_budget=token_budget,
            include_recent_episodes=include_episodes,
            request_context=request_context,
        )


class TestMemoryContext:
    async def test_empty_returns_header_only(self) -> None:
        result = await _call_context([])
        assert result == "# Memory Context\n"

    async def test_facts_section_present(self) -> None:
        result = await _call_context([_fact("dark mode")])
        assert "## Task-Relevant Facts" in result
        assert "dark mode" in result

    async def test_rules_section_present(self) -> None:
        result = await _call_context([_rule("Be concise")])
        assert "## Active Rules" in result
        assert "Be concise" in result

    async def test_token_budget_respected(self) -> None:
        big_items = [_fact("x" * 200) for _ in range(50)] + [_rule("y" * 200) for _ in range(20)]
        result = await _call_context(big_items, token_budget=500)
        assert len(result) <= 500 * 4 + 50

    async def test_proven_rules_before_candidate(self) -> None:
        candidate = _rule("cand", maturity="candidate")
        proven = _rule("proven", maturity="proven")
        result = await _call_context([candidate, proven])
        assert result.find("proven") < result.find("cand")

    async def test_request_context_tenant_propagated(self) -> None:
        captured: dict = {}

        async def _fake_recall(
            pool: Any, topic: Any, engine: Any, *, scope: Any, limit: Any, tenant_id: str, **kw: Any
        ) -> list:
            captured["tenant_id"] = tenant_id
            return []

        with patch("butlers.modules.memory.tools.context._search.recall", side_effect=_fake_recall):
            pool = AsyncMock()
            pool.fetch = AsyncMock(return_value=[])
            pool.execute = AsyncMock()
            await memory_context(
                pool,
                MagicMock(),
                "p",
                "g",
                request_context={"tenant_id": "health"},
            )
        assert captured["tenant_id"] == "health"
