"""Behavioral tests for memory management MCP tools.

Covers: memory_stats, predicate_list, memory_context section compiler.
(memory_forget is tested in test_tools_reading.py)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest

from butlers.modules.memory.search import CatalogReadPolicy
from butlers.modules.memory.tools import memory_stats, predicate_list
from butlers.modules.memory.tools.context import _fetch_fleet_knowledge, memory_context

pytestmark = pytest.mark.unit


@pytest.fixture()
def mock_pool() -> AsyncMock:
    pool = AsyncMock()
    pool.fetchval = AsyncMock(return_value=0)
    pool.fetch = AsyncMock(return_value=[])
    return pool


# ---------------------------------------------------------------------------
# memory_stats
# ---------------------------------------------------------------------------


class TestMemoryStats:
    async def test_result_shape(self, mock_pool: AsyncMock) -> None:
        result = await memory_stats(mock_pool)
        assert set(result.keys()) == {"episodes", "facts", "rules"}
        assert set(result["episodes"].keys()) == {"total", "unconsolidated", "backlog_age_hours"}
        assert set(result["facts"].keys()) == {"active", "fading", "superseded", "expired"}
        assert set(result["rules"].keys()) == {
            "candidate",
            "established",
            "proven",
            "anti_pattern",
            "forgotten",
            "retired",
        }

    async def test_chronicler_stats_filter_every_fact_count_before_aggregation(
        self, mock_pool: AsyncMock
    ) -> None:
        queries: list[str] = []

        async def _fetchval(query: str, *args: object) -> int | bool:
            if "to_regclass('chronicler_mem.facts_readable')" in query:
                return True
            queries.append(query)
            return 0

        mock_pool.fetchval = AsyncMock(side_effect=_fetchval)

        await memory_stats(mock_pool, memory_schema="chronicler_mem")

        fact_queries = [query for query in queries if "FROM facts AS f" in query]
        assert len(fact_queries) == 4
        assert all("chronicler_mem.facts_readable" in query for query in fact_queries)

    async def test_returns_integer_counts(self, mock_pool: AsyncMock) -> None:
        mock_pool.fetchval = AsyncMock(return_value=5)
        result = await memory_stats(mock_pool)
        assert result["episodes"]["total"] == 5

    async def test_every_maturity_bucket_excludes_forgotten_rules(
        self, mock_pool: AsyncMock
    ) -> None:
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

        mock_pool.fetchval = AsyncMock(side_effect=_fetchval)
        await memory_stats(mock_pool)

        maturity_queries = [q for q in queries if "FROM rules WHERE maturity" in q]
        assert len(maturity_queries) == 4, maturity_queries
        for query in maturity_queries:
            assert "(metadata->>'forgotten')::boolean IS NOT TRUE" in query, query

    async def test_every_maturity_bucket_excludes_retired_rules(self, mock_pool: AsyncMock) -> None:
        """bu-rjdihn: a retired rule (retired_at set, bu-6t8ix.3) is a deliberate
        decommission, not a live standing order — every maturity bucket must
        exclude it the same way it excludes forgotten rules, and the retired
        count must be reported separately rather than silently dropped.
        """
        queries: list[str] = []

        async def _fetchval(query: str, *args: object) -> int:
            queries.append(query)
            return 0

        mock_pool.fetchval = AsyncMock(side_effect=_fetchval)
        result = await memory_stats(mock_pool)

        maturity_queries = [q for q in queries if "FROM rules WHERE maturity" in q]
        assert len(maturity_queries) == 4, maturity_queries
        for query in maturity_queries:
            assert "retired_at IS NULL" in query, query

        retired_queries = [q for q in queries if "retired_at IS NOT NULL" in q]
        assert len(retired_queries) == 1, queries
        assert result["rules"]["retired"] == 0


# ---------------------------------------------------------------------------
# predicate_list
# ---------------------------------------------------------------------------


class TestPredicateList:
    async def test_returns_empty_list_when_no_predicates(self, mock_pool: AsyncMock) -> None:
        mock_pool.fetch = AsyncMock(return_value=[])
        result = await predicate_list(mock_pool)
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
    # No runtime_config row / no withheld facts in this mocked pool -- both
    # load_catalog_read_policy and the profile-facts withheld-count query
    # call pool.fetchval, so it must return a real falsy value rather than
    # AsyncMock's default (a MagicMock, whose __int__ defaults to 1).
    pool.fetchval = AsyncMock(return_value=None)

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
        fact = _fact("dark mode")
        result = await _call_context([fact])
        assert "## Task-Relevant Facts" in result
        assert "dark mode" in result
        assert f"[memory_ref=fact:{fact['id']}]" in result

        profile_result = await _call_context([], profile_rows=[fact])
        assert "## Profile Facts" in profile_result
        assert f"[memory_ref=fact:{fact['id']}]" in profile_result

    async def test_rules_section_present(self) -> None:
        rule = _rule("Be concise")
        result = await _call_context([rule])
        assert "## Active Rules" in result
        assert "Be concise" in result
        assert f"[memory_ref=rule:{rule['id']}]" in result

    async def test_token_budget_respected(self) -> None:
        big_items = [_fact("x" * 200) for _ in range(50)] + [_rule("y" * 200) for _ in range(20)]
        result = await _call_context(big_items, token_budget=500)
        assert len(result) <= 500 * 4

    async def test_reference_overhead_can_omit_an_otherwise_fitting_line(self) -> None:
        fact = _fact("x")
        header = "\n## Task-Relevant Facts\n"
        legacy_line = "- [User] [info]: x (confidence: 1.00)\n"
        reference = f" [memory_ref=fact:{fact['id']}]"
        # The section can afford the pre-reference representation but not the
        # complete actionable line. Partial references are never emitted.
        preamble_chars = len("# Memory Context\n")
        token_budget = next(
            budget
            for budget in range(1, 1000)
            if len(header) + len(legacy_line)
            <= int((budget * 4 - preamble_chars) * 0.35)
            < len(header) + len(legacy_line.rstrip("\n")) + len(reference) + 1
        )

        result = await _call_context([fact], token_budget=token_budget)

        assert "Task-Relevant Facts" not in result
        assert "memory_ref=" not in result
        assert len(result) <= token_budget * 4

    async def test_non_owner_facts_are_qualified_whatever_their_subject(self) -> None:
        """REQ-module-memory-013: every non-owner fact renders attributed, about anyone."""
        mallory, ghost = uuid.uuid4(), uuid.uuid4()

        def authored(content: str, subject: str, authority: str | None, sender=None) -> dict:
            return {
                **_fact(content),
                "subject": subject,
                "content_authority": authority,
                "authority_entity_id": sender,
                "source_butler": "finance",  # curator only: never the reporter
            }

        facts = [
            authored("owner says aisle", "Owner", "owner"),
            authored("bob owes money", "Bob", "third_party", mallory),
            authored("owner wires money", "Owner", "third_party", ghost),
            authored("bob moved", "Bob", "mixed", mallory),
            authored("bob likes tea", "Bob", None),
            authored("nightly digest ran", "Owner", "system"),
        ]
        mock_pool = AsyncMock()

        async def _fetch(sql: str, *args: Any) -> list[dict]:
            if "canonical_name" in sql:
                # ``ghost`` has no resolvable entity row; Bob is not a sender.
                return [{"id": mallory, "canonical_name": "Mallory"}]
            return []

        mock_pool.fetch = _fetch
        mock_pool.fetchval = AsyncMock(return_value=None)
        with patch(
            "butlers.modules.memory.tools.context._search.recall",
            new_callable=AsyncMock,
            return_value=facts,
        ):
            result = await memory_context(mock_pool, MagicMock(), "prompt", "general")

        lines = {
            line.split("]: ", 1)[1].split(" (confidence")[0]
            for line in result.splitlines()
            if "]: " in line
        }
        assert lines == {
            "owner says aisle",
            "bob owes money (reported by Mallory)",
            "owner wires money (reported by a third party)",
            "bob moved (partly reported by Mallory)",
            "bob likes tea (source unverified)",
            "nightly digest ran",
        }
        assert "finance" not in result

    async def test_failed_sender_lookup_keeps_the_qualification(self) -> None:
        fact = {
            **_fact("bob owes money"),
            "content_authority": "third_party",
            "authority_entity_id": uuid.uuid4(),
        }
        mock_pool = AsyncMock()

        async def _fetch(sql: str, *args: Any) -> list[dict]:
            if "canonical_name" in sql:
                raise RuntimeError("entities unavailable")
            return []

        mock_pool.fetch = _fetch
        mock_pool.fetchval = AsyncMock(return_value=None)
        with patch(
            "butlers.modules.memory.tools.context._search.recall",
            new_callable=AsyncMock,
            return_value=[fact],
        ):
            result = await memory_context(mock_pool, MagicMock(), "prompt", "general")

        assert "bob owes money (reported by a third party)" in result

    async def test_budget_drops_whole_attributed_lines_never_their_label(self) -> None:
        facts = [{**_fact(f"report {i}"), "content_authority": "third_party"} for i in range(40)]
        result = await _call_context(facts, token_budget=200)

        fact_lines = [line for line in result.splitlines() if line.startswith("- [User]")]
        assert 0 < len(fact_lines) < len(facts)
        assert all(
            "(reported by a third party)" in line and line.endswith("]") for line in fact_lines
        )

    async def test_proven_rules_before_candidate(self) -> None:
        candidate = _rule("cand", maturity="candidate")
        proven = _rule("proven", maturity="proven")
        result = await _call_context([candidate, proven])
        assert result.find("proven") < result.find("cand")

    async def test_request_context_tenant_propagated(self) -> None:
        captured: dict = {}

        async def _fake_recall(
            mock_pool: Any,
            topic: Any,
            engine: Any,
            *,
            scope: Any,
            limit: Any,
            tenant_id: str,
            **kw: Any,
        ) -> list:
            captured["tenant_id"] = tenant_id
            return []

        with patch("butlers.modules.memory.tools.context._search.recall", side_effect=_fake_recall):
            mock_pool = AsyncMock()
            mock_pool.fetch = AsyncMock(return_value=[])
            mock_pool.execute = AsyncMock()
            await memory_context(
                mock_pool,
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
    async def test_fleet_search_uses_one_precomputed_embedding_with_real_catalog_search(
        self,
    ) -> None:
        pool = AsyncMock()
        pool.fetchval = AsyncMock(return_value="public.memory_catalog")
        own_row = _catalog_row("general", "Own knowledge")
        other_row = _catalog_row("finance", "Budget rule")
        pool.fetch = AsyncMock(return_value=[own_row, other_row])
        engine = MagicMock()
        vector = [0.25, -0.5]
        engine.embed.return_value = vector

        results = await _fetch_fleet_knowledge(
            pool,
            engine,
            "prompt",
            "general",
            "shared",
            CatalogReadPolicy("normal", ("normal",)),
        )

        assert {row["source_butler"] for row in results} == {"finance"}
        engine.embed.assert_called_once_with("prompt")
        pool.fetchval.assert_awaited_once_with(
            "SELECT pg_catalog.to_regclass('public.memory_catalog')"
        )
        assert pool.fetch.await_count == 2
        semantic_call, keyword_call = pool.fetch.await_args_list
        assert "embedding <=> $1" in semantic_call.args[0]
        assert "chronicler_catalog_row_visible(memory_catalog.id)" in semantic_call.args[0]
        assert semantic_call.args[1] == str(vector)
        assert "plainto_tsquery" in keyword_call.args[0]

    async def test_absent_public_catalog_remains_best_effort(self) -> None:
        pool = AsyncMock()
        pool.fetchval = AsyncMock(return_value=None)
        engine = MagicMock()
        engine.embed.return_value = [0.0] * 384

        with patch(
            "butlers.modules.memory.tools.context._search.search_catalog",
            new_callable=AsyncMock,
            return_value=[],
        ) as guarded_search:
            result = await _fetch_fleet_knowledge(
                pool,
                engine,
                "prompt",
                "general",
                "shared",
                CatalogReadPolicy("normal", ("normal",)),
            )

        assert result == []
        pool.fetchval.assert_awaited_once_with(
            "SELECT pg_catalog.to_regclass('public.memory_catalog')"
        )
        guarded_search.assert_not_awaited()

    @pytest.mark.parametrize(
        ("error_type", "message"),
        [
            pytest.param(TimeoutError, "catalog query timed out", id="timeout"),
            pytest.param(asyncpg.InterfaceError, "catalog connection failed", id="interface"),
            pytest.param(RuntimeError, "unexpected catalog execution failure", id="unexpected"),
            pytest.param(
                asyncpg.InsufficientPrivilegeError,
                "catalog visibility denied",
                id="visibility-acl",
            ),
        ],
    )
    async def test_guarded_catalog_execution_error_propagates(
        self, error_type: type[Exception], message: str
    ) -> None:
        """Errors from the guarded SQL call must propagate without becoming empty results."""
        pool = AsyncMock()
        pool.fetchval = AsyncMock(return_value="memory_catalog")
        queries: list[str] = []
        expected_error = error_type(message)

        async def _fetch(query: str, *args: object) -> list[dict[str, Any]]:
            queries.append(query)
            assert "FROM public.memory_catalog AS memory_catalog" in query
            assert "public.chronicler_catalog_row_visible(memory_catalog.id)" in query
            raise expected_error

        pool.fetch = AsyncMock(side_effect=_fetch)
        engine = MagicMock()
        engine.embed.return_value = [0.0] * 384

        with pytest.raises(error_type) as caught:
            await _fetch_fleet_knowledge(
                pool,
                engine,
                "prompt",
                "general",
                "shared",
                CatalogReadPolicy("normal", ("normal",)),
            )

        assert caught.value is expected_error
        assert engine.embed.call_count == 1
        assert engine.embed.call_args.args == ("prompt",)
        assert len(queries) == 1
        pool.fetchval.assert_awaited_once_with(
            "SELECT pg_catalog.to_regclass('public.memory_catalog')"
        )

    async def _pool(self) -> AsyncMock:
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])
        pool.execute = AsyncMock()
        return pool

    async def test_default_off_no_catalog_search(self) -> None:
        mock_pool = await self._pool()
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
            result = await memory_context(mock_pool, MagicMock(), "prompt", "general")

        mock_catalog.assert_not_called()
        assert "Fleet Knowledge" not in result

    async def test_include_fleet_knowledge_adds_section_excluding_own_butler(self) -> None:
        mock_pool = await self._pool()
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
                mock_pool, MagicMock(), "prompt", "general", include_fleet_knowledge=True
            )

        assert "## Fleet Knowledge (cross-butler)" in result
        assert "Budget rule" in result
        assert "Own knowledge" not in result
        assert "memory_ref=" not in result

    async def test_catalog_search_failure_propagates(self) -> None:
        mock_pool = await self._pool()
        with (
            patch(
                "butlers.modules.memory.tools.context._search.recall",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "butlers.modules.memory.tools.context._search.search_catalog",
                new_callable=AsyncMock,
                side_effect=RuntimeError("catalog execution failed"),
            ),
            pytest.raises(RuntimeError, match="catalog execution failed"),
        ):
            await memory_context(
                mock_pool, MagicMock(), "prompt", "general", include_fleet_knowledge=True
            )

    async def test_embedding_failure_remains_best_effort(self) -> None:
        pool = AsyncMock()
        pool.fetchval = AsyncMock(return_value="memory_catalog")
        pool.fetch = AsyncMock(return_value=[])
        engine = MagicMock()
        engine.embed.side_effect = RuntimeError("embedding backend unavailable")

        result = await _fetch_fleet_knowledge(
            pool,
            engine,
            "prompt",
            "general",
            "shared",
            CatalogReadPolicy("normal", ("normal",)),
        )

        assert result == []
        engine.embed.assert_called_once_with("prompt")
        pool.fetchval.assert_awaited_once_with(
            "SELECT pg_catalog.to_regclass('public.memory_catalog')"
        )
        pool.fetch.assert_not_awaited()
