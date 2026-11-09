"""Recall embeds once, off the shared event loop, and bumps references in one statement.

bu-dhloi1.192: memory_context embedded the same text twice (recall and fleet knowledge),
synchronously on the loop every cohosted butler shares in butlers-up, then ran one
reference-count UPDATE per result.
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest

from butlers.modules.memory import search as _search
from butlers.modules.memory.tools import context as _context

pytestmark = pytest.mark.unit

POLICY = _search.resolve_catalog_read_policy("normal")


class Engine:
    """Records every embed call and the thread it ran on."""

    def __init__(self, delay: float = 0.0) -> None:
        self.calls: list[tuple[str, int]] = []
        self.delay = delay

    def embed(self, text: str) -> list[float]:
        self.calls.append((text, threading.get_ident()))
        if self.delay:
            time.sleep(self.delay)
        return [0.0] * 384


def _row(kind: str) -> dict:
    return {
        "id": uuid.uuid4(),
        "rrf_score": 0.02,
        "importance": 5.0,
        "confidence": 1.0,
        "decay_rate": 0.0,
        "last_confirmed_at": None,
        "last_referenced_at": None,
        "content": kind,
    }


@pytest.fixture
def searches(monkeypatch):
    """hybrid_search returns 3 facts and 2 rules; search_catalog returns nothing."""

    async def hybrid(pool, topic, embedding, table, **kw):
        return [_row(table) for _ in range(3 if table == "facts" else 2)]

    monkeypatch.setattr(_search, "hybrid_search", hybrid)
    catalog = AsyncMock(return_value=[])
    monkeypatch.setattr(_search, "search_catalog", catalog)
    monkeypatch.setattr(_context, "_fetch_profile_facts", AsyncMock(return_value=([], 0)))
    monkeypatch.setattr(_context, "_attribute_non_owner_facts", AsyncMock())
    return catalog


def _pool() -> AsyncMock:
    pool = AsyncMock()
    pool.fetchval.return_value = "public.memory_catalog"
    return pool


async def test_memory_context_embeds_once_even_with_fleet_knowledge(searches):
    engine = Engine()
    await _context.memory_context(
        _pool(),
        engine,
        "what is on today",
        "general",
        include_fleet_knowledge=True,
        catalog_read_policy=POLICY,
    )
    assert [text for text, _ in engine.calls] == ["what is on today"]
    # The fleet search received the one vector rather than embedding again.
    assert searches.await_args.kwargs["_query_embedding"] == [0.0] * 384


async def test_embedding_runs_off_the_event_loop_thread(searches):
    engine = Engine()
    loop_thread = threading.get_ident()
    await _context.memory_context(
        _pool(),
        engine,
        "topic",
        "general",
        include_fleet_knowledge=True,
        catalog_read_policy=POLICY,
    )
    assert engine.calls and all(ident != loop_thread for _, ident in engine.calls)


async def test_event_loop_stays_responsive_while_recall_embeds(searches):
    engine = Engine(delay=0.3)
    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    task = asyncio.create_task(ticker())
    try:
        await _search.recall(_pool(), "topic", engine, read_policy=POLICY)
    finally:
        task.cancel()
    # A blocking embed on the loop would allow ~0 ticks across 0.3s.
    assert ticks >= 10


@pytest.mark.parametrize(
    "call",
    [
        lambda pool, engine: _search.recall(pool, "q", engine, read_policy=POLICY),
        lambda pool, engine: _search.search(pool, "q", engine, read_policy=POLICY),
        lambda pool, engine: _search.search_catalog(
            pool, "q", engine, read_policy=POLICY, mode="semantic"
        ),
    ],
    ids=["recall", "search", "search_catalog"],
)
async def test_every_read_entry_point_embeds_off_loop(call, monkeypatch):
    # Real search functions here: stub only the SQL layers beneath them.
    async def hybrid(pool, topic, embedding, table, **kw):
        return []

    monkeypatch.setattr(_search, "hybrid_search", hybrid)
    for name in ("semantic_search", "keyword_search", "_catalog_semantic_search"):
        if hasattr(_search, name):
            monkeypatch.setattr(_search, name, AsyncMock(return_value=[]))
    engine = Engine()
    await call(_pool(), engine)
    assert len(engine.calls) == 1 and engine.calls[0][1] != threading.get_ident()


async def test_reference_counts_are_bumped_in_one_statement(searches):
    pool = _pool()
    results = await _search.recall(pool, "topic", Engine(), read_policy=POLICY)
    assert len(results) == 5
    assert pool.execute.await_count == 1
    sql, fact_ids, rule_ids = pool.execute.await_args.args
    assert "UPDATE facts" in sql and "UPDATE rules" in sql
    assert sorted(fact_ids) == sorted(r["id"] for r in results if r["memory_type"] == "fact")
    assert sorted(rule_ids) == sorted(r["id"] for r in results if r["memory_type"] == "rule")


async def test_no_results_means_no_update(monkeypatch):
    async def hybrid(pool, topic, embedding, table, **kw):
        return []

    monkeypatch.setattr(_search, "hybrid_search", hybrid)
    pool = _pool()
    assert await _search.recall(pool, "topic", Engine(), read_policy=POLICY) == []
    pool.execute.assert_not_awaited()


async def test_a_caller_supplied_embedding_is_not_recomputed(searches):
    engine = MagicMock()
    await _search.recall(_pool(), "topic", engine, read_policy=POLICY, query_embedding=[0.5] * 384)
    engine.embed.assert_not_called()


async def test_reference_bump_failure_does_not_fail_recall(caplog):
    # A deadlock with another multi-row writer must not fail memory_context.
    pool = AsyncMock()
    pool.execute.side_effect = asyncpg.DeadlockDetectedError("deadlock detected")
    with caplog.at_level("WARNING"):
        await _search._bump_reference_counts(pool, [{"id": uuid.uuid4(), "memory_type": "fact"}])
    assert "reference" in caplog.text.lower()


async def test_reference_bump_only_swallows_database_errors():
    pool = AsyncMock()
    pool.execute.side_effect = RuntimeError("a real bug")
    with pytest.raises(RuntimeError):
        await _search._bump_reference_counts(pool, [{"id": uuid.uuid4(), "memory_type": "fact"}])


async def test_reference_bump_locks_rows_in_sorted_order():
    # Every multi-row writer takes the same order, so two bumps cannot deadlock each other.
    ids = [uuid.UUID(int=n) for n in (9, 3, 7, 1)]
    results = [{"id": i, "memory_type": "fact"} for i in ids]
    results += [{"id": i, "memory_type": "rule"} for i in reversed(ids)]
    pool = AsyncMock()
    await _search._bump_reference_counts(pool, results)
    _, fact_ids, rule_ids = pool.execute.await_args.args
    assert fact_ids == sorted(ids) and rule_ids == sorted(ids)
