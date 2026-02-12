"""Tests for management and context building MCP tools in tools.py."""

from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Load tools module (mocking sentence_transformers first)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Load tools module
# ---------------------------------------------------------------------------

from butlers.tools.memory import (
    memory_forget,
    memory_stats,
    memory_context,
)
from butlers.tools.memory import _helpers

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_pool() -> AsyncMock:
    """Return an AsyncMock asyncpg pool."""
    pool = AsyncMock()
    pool.fetchval = AsyncMock(return_value=0)
    pool.execute = AsyncMock(return_value="UPDATE 1")
    return pool


@pytest.fixture()
def memory_id() -> uuid.UUID:
    return uuid.UUID("12345678-1234-5678-1234-567812345678")


@pytest.fixture()
def mock_embedding_engine() -> MagicMock:
    engine = MagicMock()
    engine.embed.return_value = [0.1] * 384
    return engine


# ---------------------------------------------------------------------------
# memory_forget tests
# ---------------------------------------------------------------------------


class TestMemoryForget:
    """Tests for memory_forget tool wrapper."""

    async def test_delegates_to_storage_forget_memory(
        self, mock_pool: AsyncMock, memory_id: uuid.UUID
    ) -> None:
        """memory_forget delegates to _storage.forget_memory."""
        _helpers._storage.forget_memory = AsyncMock(return_value=True)
        result = await memory_forget(mock_pool, "fact", str(memory_id))
        _helpers._storage.forget_memory.assert_awaited_once_with(mock_pool, "fact", memory_id)
        assert result == {"forgotten": True}

    async def test_returns_forgotten_false_when_not_found(
        self, mock_pool: AsyncMock, memory_id: uuid.UUID
    ) -> None:
        """memory_forget returns {'forgotten': False} when storage returns False."""
        _helpers._storage.forget_memory = AsyncMock(return_value=False)
        result = await memory_forget(mock_pool, "fact", str(memory_id))
        assert result == {"forgotten": False}

    async def test_converts_string_id_to_uuid(
        self, mock_pool: AsyncMock, memory_id: uuid.UUID
    ) -> None:
        """memory_forget converts the string memory_id to a uuid.UUID."""
        _helpers._storage.forget_memory = AsyncMock(return_value=True)
        await memory_forget(mock_pool, "rule", str(memory_id))
        call_args = _helpers._storage.forget_memory.call_args[0]
        assert isinstance(call_args[2], uuid.UUID)
        assert call_args[2] == memory_id

    async def test_passes_memory_type_through(
        self, mock_pool: AsyncMock, memory_id: uuid.UUID
    ) -> None:
        """memory_forget passes memory_type verbatim to storage."""
        _helpers._storage.forget_memory = AsyncMock(return_value=True)
        for mtype in ("episode", "fact", "rule"):
            await memory_forget(mock_pool, mtype, str(memory_id))
            assert _helpers._storage.forget_memory.call_args[0][1] == mtype


# ---------------------------------------------------------------------------
# memory_stats tests
# ---------------------------------------------------------------------------


class TestMemoryStats:
    """Tests for memory_stats tool wrapper."""

    async def test_returns_all_expected_top_level_keys(
        self, mock_pool: AsyncMock
    ) -> None:
        """memory_stats returns episodes, facts, and rules top-level keys."""
        result = await memory_stats(mock_pool)
        assert "episodes" in result
        assert "facts" in result
        assert "rules" in result

    async def test_episodes_structure(self, mock_pool: AsyncMock) -> None:
        """Episodes section has total, unconsolidated, backlog_age_hours."""
        mock_pool.fetchval = AsyncMock(return_value=42)
        result = await memory_stats(mock_pool)
        ep = result["episodes"]
        assert "total" in ep
        assert "unconsolidated" in ep
        assert "backlog_age_hours" in ep

    async def test_facts_structure(self, mock_pool: AsyncMock) -> None:
        """Facts section has active, fading, superseded, expired."""
        result = await memory_stats(mock_pool)
        facts = result["facts"]
        assert "active" in facts
        assert "fading" in facts
        assert "superseded" in facts
        assert "expired" in facts

    async def test_rules_structure(self, mock_pool: AsyncMock) -> None:
        """Rules section has candidate, established, proven, anti_pattern, forgotten."""
        result = await memory_stats(mock_pool)
        rules = result["rules"]
        assert "candidate" in rules
        assert "established" in rules
        assert "proven" in rules
        assert "anti_pattern" in rules
        assert "forgotten" in rules

    async def test_backlog_age_hours_none_when_no_unconsolidated(
        self, mock_pool: AsyncMock
    ) -> None:
        """backlog_age_hours is None when there are no unconsolidated episodes."""
        # The third fetchval call returns backlog_age_hours
        call_count = 0

        async def _side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            # 1=ep_total, 2=ep_unconsolidated, 3=backlog_age
            if call_count == 3:
                return None
            return 0

        mock_pool.fetchval = AsyncMock(side_effect=_side_effect)
        result = await memory_stats(mock_pool)
        assert result["episodes"]["backlog_age_hours"] is None

    async def test_backlog_age_hours_float_when_present(
        self, mock_pool: AsyncMock
    ) -> None:
        """backlog_age_hours is a float when backlog exists."""
        call_count = 0

        async def _side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 3:
                return 48.5  # 48.5 hours
            return 10

        mock_pool.fetchval = AsyncMock(side_effect=_side_effect)
        result = await memory_stats(mock_pool)
        assert result["episodes"]["backlog_age_hours"] == 48.5

    async def test_scope_filtering_passes_scope_param(
        self, mock_pool: AsyncMock
    ) -> None:
        """When scope is provided, SQL queries for facts/rules include scope filter."""
        mock_pool.fetchval = AsyncMock(return_value=0)
        await memory_stats(mock_pool, scope="my-butler")

        # Check that at least some calls passed the scope parameter
        calls_with_scope = [
            c for c in mock_pool.fetchval.call_args_list
            if len(c.args) > 1 and c.args[-1] == "my-butler"
        ]
        # Facts have 4 queries and rules have 5 queries that need scope
        assert len(calls_with_scope) >= 4, (
            f"Expected at least 4 calls with scope param, got {len(calls_with_scope)}"
        )

    async def test_no_scope_no_extra_params(self, mock_pool: AsyncMock) -> None:
        """When scope is None, queries for facts/rules don't pass extra params."""
        mock_pool.fetchval = AsyncMock(return_value=0)
        await memory_stats(mock_pool)

        # Episode queries (first 3) should have 1 arg each (just SQL)
        for call in mock_pool.fetchval.call_args_list[:3]:
            assert len(call.args) == 1, f"Episode query should have 1 arg, got {call.args}"

    async def test_fetchval_called_for_all_counts(self, mock_pool: AsyncMock) -> None:
        """memory_stats issues the right number of fetchval calls."""
        mock_pool.fetchval = AsyncMock(return_value=0)
        await memory_stats(mock_pool)
        # 3 episode + 4 facts + 5 rules = 12 calls
        assert mock_pool.fetchval.call_count == 12


# ---------------------------------------------------------------------------
# memory_context tests
# ---------------------------------------------------------------------------


def _make_fact(
    id_: uuid.UUID | None = None,
    *,
    subject: str = "user",
    predicate: str = "prefers",
    content: str = "dark mode",
    confidence: float = 0.9,
    composite_score: float = 0.8,
    **extra,
) -> dict:
    if id_ is None:
        id_ = uuid.uuid4()
    return {
        "id": id_,
        "memory_type": "fact",
        "subject": subject,
        "predicate": predicate,
        "content": content,
        "confidence": confidence,
        "composite_score": composite_score,
        **extra,
    }


def _make_rule(
    id_: uuid.UUID | None = None,
    *,
    content: str = "Always greet warmly",
    maturity: str = "established",
    effectiveness_score: float = 0.75,
    composite_score: float = 0.6,
    **extra,
) -> dict:
    if id_ is None:
        id_ = uuid.uuid4()
    return {
        "id": id_,
        "memory_type": "rule",
        "content": content,
        "maturity": maturity,
        "effectiveness_score": effectiveness_score,
        "composite_score": composite_score,
        **extra,
    }


class TestMemoryContext:
    """Tests for memory_context tool."""

    async def test_formats_facts_section(
        self, mock_pool: AsyncMock, mock_embedding_engine: MagicMock
    ) -> None:
        """memory_context includes a Key Facts section with formatted entries."""
        _helpers._search.recall = AsyncMock(return_value=[
            _make_fact(subject="user", predicate="prefers", content="dark mode", confidence=0.9),
        ])
        result = await memory_context(
            mock_pool, mock_embedding_engine, "user preferences", "butler-1"
        )
        assert "# Memory Context" in result
        assert "## Key Facts" in result
        assert "[user] [prefers]: dark mode (confidence: 0.90)" in result

    async def test_formats_rules_section(
        self, mock_pool: AsyncMock, mock_embedding_engine: MagicMock
    ) -> None:
        """memory_context includes an Active Rules section with formatted entries."""
        _helpers._search.recall = AsyncMock(return_value=[
            _make_rule(
                content="Always greet warmly",
                maturity="established",
                effectiveness_score=0.75,
            ),
        ])
        result = await memory_context(
            mock_pool, mock_embedding_engine, "greeting behavior", "butler-1"
        )
        assert "## Active Rules" in result
        assert "Always greet warmly (maturity: established, effectiveness: 0.75)" in result

    async def test_both_sections_present(
        self, mock_pool: AsyncMock, mock_embedding_engine: MagicMock
    ) -> None:
        """When recall returns both facts and rules, both sections appear."""
        _helpers._search.recall = AsyncMock(return_value=[
            _make_fact(composite_score=0.9),
            _make_rule(composite_score=0.7),
        ])
        result = await memory_context(
            mock_pool, mock_embedding_engine, "anything", "butler-1"
        )
        assert "## Key Facts" in result
        assert "## Active Rules" in result

    async def test_empty_results_returns_header_only(
        self, mock_pool: AsyncMock, mock_embedding_engine: MagicMock
    ) -> None:
        """When recall returns nothing, the context is just the header."""
        _helpers._search.recall = AsyncMock(return_value=[])
        result = await memory_context(
            mock_pool, mock_embedding_engine, "anything", "butler-1"
        )
        assert "# Memory Context" in result
        assert "## Key Facts" not in result
        assert "## Active Rules" not in result

    async def test_respects_token_budget(
        self, mock_pool: AsyncMock, mock_embedding_engine: MagicMock
    ) -> None:
        """memory_context truncates output to stay within the token budget."""
        # Create many facts that would exceed a small budget
        many_facts = [
            _make_fact(
                subject=f"subject-{i}",
                predicate=f"predicate-{i}",
                content="x" * 100,
                composite_score=1.0 - i * 0.01,
            )
            for i in range(50)
        ]
        _helpers._search.recall = AsyncMock(return_value=many_facts)

        # Very small budget: 100 tokens = ~400 chars
        result = await memory_context(
            mock_pool, mock_embedding_engine, "test", "butler-1", token_budget=100
        )
        assert len(result) <= 400 + 200  # some tolerance for header

    async def test_orders_by_composite_score_descending(
        self, mock_pool: AsyncMock, mock_embedding_engine: MagicMock
    ) -> None:
        """Facts and rules are rendered in composite_score descending order."""
        # recall already returns sorted, but verify the tool preserves order
        fact_high = _make_fact(
            subject="high", predicate="score", content="important", composite_score=0.95
        )
        fact_low = _make_fact(
            subject="low", predicate="score", content="trivial", composite_score=0.3
        )
        _helpers._search.recall = AsyncMock(return_value=[fact_high, fact_low])

        result = await memory_context(
            mock_pool, mock_embedding_engine, "test", "butler-1"
        )

        # "important" should appear before "trivial"
        idx_high = result.index("important")
        idx_low = result.index("trivial")
        assert idx_high < idx_low

    async def test_calls_recall_with_correct_params(
        self, mock_pool: AsyncMock, mock_embedding_engine: MagicMock
    ) -> None:
        """memory_context calls recall with the right arguments."""
        _helpers._search.recall = AsyncMock(return_value=[])
        await memory_context(
            mock_pool, mock_embedding_engine, "my topic", "butler-x"
        )
        _helpers._search.recall.assert_awaited_once_with(
            mock_pool, "my topic", mock_embedding_engine, scope="butler-x", limit=20
        )

    async def test_no_facts_only_rules(
        self, mock_pool: AsyncMock, mock_embedding_engine: MagicMock
    ) -> None:
        """When recall returns only rules, the Key Facts section is absent."""
        _helpers._search.recall = AsyncMock(return_value=[
            _make_rule(content="Be helpful"),
        ])
        result = await memory_context(
            mock_pool, mock_embedding_engine, "test", "butler-1"
        )
        assert "## Key Facts" not in result
        assert "## Active Rules" in result
        assert "Be helpful" in result

    async def test_no_rules_only_facts(
        self, mock_pool: AsyncMock, mock_embedding_engine: MagicMock
    ) -> None:
        """When recall returns only facts, the Active Rules section is absent."""
        _helpers._search.recall = AsyncMock(return_value=[
            _make_fact(content="likes coffee"),
        ])
        result = await memory_context(
            mock_pool, mock_embedding_engine, "test", "butler-1"
        )
        assert "## Key Facts" in result
        assert "## Active Rules" not in result
        assert "likes coffee" in result
