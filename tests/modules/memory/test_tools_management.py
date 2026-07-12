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

    async def test_every_maturity_bucket_excludes_forgotten_rules(self, pool: AsyncMock) -> None:
        """bu-5ud8p.2: rules_anti_pattern used to be the one maturity bucket
        that didn't exclude forgotten rules, unlike its candidate/established/
        proven siblings in this same function -- an MCP-internal inconsistency.
        Assert every SELECT COUNT(*) FROM rules WHERE maturity = '...' query
        also carries the forgotten-exclusion predicate.
        """
        queries: list[str] = []

        async def _fetchval(query: str, *args: object) -> int:
            queries.append(query)
            return 0

        pool.fetchval = AsyncMock(side_effect=_fetchval)
        await memory_stats(pool)

        maturity_queries = [q for q in queries if "FROM rules WHERE maturity" in q]
        assert len(maturity_queries) == 4, maturity_queries
        for query in maturity_queries:
            assert "(metadata->>'forgotten')::boolean IS NOT TRUE" in query, query


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


# ---------------------------------------------------------------------------
# memory_context — Fleet Knowledge (cross-butler catalog) section (bu-qvnce.15)
# ---------------------------------------------------------------------------


def _catalog_row(source_butler: str, title: str) -> dict:
    return {
        "id": uuid.uuid4(),
        "source_schema": source_butler,
        "source_table": "facts",
        "source_id": uuid.uuid4(),
        "source_butler": source_butler,
        "memory_type": "fact",
        "title": title,
        "summary": f"{title} summary",
        "rrf_score": 0.5,
    }


class TestMemoryContextFleetKnowledge:
    async def _pool(self) -> AsyncMock:
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])
        pool.execute = AsyncMock()
        return pool

    async def test_default_off_no_catalog_search(self) -> None:
        pool = await self._pool()
        with (
            patch(
                "butlers.modules.memory.tools.context._search.recall",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "butlers.modules.memory.tools.context._search.search_catalog",
                new_callable=AsyncMock,
            ) as mock_catalog,
        ):
            result = await memory_context(pool, MagicMock(), "prompt", "general")

        mock_catalog.assert_not_called()
        assert "Fleet Knowledge" not in result

    async def test_include_fleet_knowledge_adds_section_excluding_own_butler(self) -> None:
        pool = await self._pool()
        own_row = _catalog_row("general", "Own knowledge")
        other_row = _catalog_row("finance", "Budget rule")
        with (
            patch(
                "butlers.modules.memory.tools.context._search.recall",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "butlers.modules.memory.tools.context._search.search_catalog",
                new_callable=AsyncMock,
                return_value=[own_row, other_row],
            ),
        ):
            result = await memory_context(
                pool, MagicMock(), "prompt", "general", include_fleet_knowledge=True
            )

        assert "## Fleet Knowledge (cross-butler)" in result
        assert "Budget rule" in result
        assert "Own knowledge" not in result

    async def test_catalog_search_failure_degrades_to_empty_section(self) -> None:
        pool = await self._pool()
        with (
            patch(
                "butlers.modules.memory.tools.context._search.recall",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "butlers.modules.memory.tools.context._search.search_catalog",
                new_callable=AsyncMock,
                side_effect=RuntimeError("catalog table missing"),
            ),
        ):
            result = await memory_context(
                pool, MagicMock(), "prompt", "general", include_fleet_knowledge=True
            )

        assert "Fleet Knowledge" not in result
