"""Memory write paths embed off the shared event loop.

bu-dhloi1.214: store_fact, store_rule, store_episode and friends called the embedding engine
synchronously on the loop every cohosted butler shares in butlers-up. Since bu-dhloi1.192 the
engine also serialises encodes behind a lock, so an on-loop write could wait there too.
"""

from __future__ import annotations

import ast
import asyncio
import threading
import time
import uuid
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.modules.memory import storage

pytestmark = pytest.mark.unit

STORAGE = Path(storage.__file__)


class Engine:
    """Records every embed call and the thread it ran on."""

    model_name = "test-model"

    def __init__(self, delay: float = 0.0) -> None:
        self.calls: list[tuple[str, int]] = []
        self.delay = delay

    def embed(self, text: str) -> list[float]:
        self.calls.append((text, threading.get_ident()))
        if self.delay:
            time.sleep(self.delay)
        return [0.0] * 384


async def _run_until_embedded(call) -> None:
    """Run a write path; whatever the mock pool does after the embed is irrelevant."""
    try:
        await call
    except Exception:  # noqa: BLE001 - only the embed placement is under test
        pass


def _assert_off_loop(engine: Engine) -> None:
    assert engine.calls, "the write path never embedded"
    loop_thread = threading.get_ident()
    assert all(ident != loop_thread for _, ident in engine.calls)


def _pool() -> AsyncMock:
    return AsyncMock()


async def test_store_fact_embeds_off_the_loop():
    engine = Engine()
    await _run_until_embedded(
        storage.store_fact(_pool(), "owner", "likes", "tea", cast(Any, engine))
    )
    _assert_off_loop(engine)


async def test_store_rule_embeds_off_the_loop():
    engine = Engine()
    await _run_until_embedded(storage.store_rule(_pool(), "Ping vendor daily", cast(Any, engine)))
    _assert_off_loop(engine)


async def test_store_episode_embeds_off_the_loop():
    engine = Engine()
    await _run_until_embedded(
        storage.store_episode(_pool(), "met Sam", "general", cast(Any, engine))
    )
    _assert_off_loop(engine)


async def test_invert_to_anti_pattern_embeds_off_the_loop():
    engine = Engine()
    conn = AsyncMock()
    conn.fetchrow.return_value = {
        "harmful_count": 3,
        "effectiveness_score": 0.1,
        "maturity": "proven",
        "content": "do the thing",
        "metadata": {},
    }
    conn.transaction = MagicMock(return_value=_NullCtx())
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_AcquireCtx(conn))
    await _run_until_embedded(storage.invert_to_anti_pattern(pool, uuid.uuid4(), cast(Any, engine)))
    _assert_off_loop(engine)


class _NullCtx:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *exc):
        return False


class _AcquireCtx:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *exc):
        return False


async def test_loop_keeps_ticking_while_a_write_embeds():
    engine = Engine(delay=0.3)
    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    task = asyncio.create_task(ticker())
    await _run_until_embedded(storage.store_rule(_pool(), "Ping vendor daily", cast(Any, engine)))
    task.cancel()
    assert ticks >= 10  # a blocking embed would leave ~0 ticks across the 0.3s encode


def test_nothing_calls_the_sync_embed_directly():
    """Every embed outside the engine itself goes through embed_async, in src/ and roster/."""
    repo = Path(__file__).resolve().parents[3]
    engine = repo / "src/butlers/modules/memory/embedding.py"
    offenders = []
    for root in ("src", "roster"):
        for path in sorted((repo / root).rglob("*.py")):
            if path == engine:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            offenders += [
                f"{path.relative_to(repo)}:{node.lineno}"
                for node in ast.walk(tree)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"embed", "embed_batch"}
            ]
    assert not offenders, f"synchronous embed calls: {offenders}"


class _OrderConn:
    """A connection that records, in order, the embed and the entity lock of a store_fact."""

    def __init__(self, events: list[str]) -> None:
        self.events = events

    def transaction(self):
        return _NullCtx()

    async def fetchrow(self, sql, *args):
        if "predicate_registry" in sql:
            return {
                "is_edge": True,
                "is_temporal": False,
                "status": "active",
                "superseded_by": None,
                "expected_subject_type": None,
                "expected_object_type": None,
                "inverse_of": "parent_of",
                "is_symmetric": False,
            }
        if "public.entities" in sql:
            return {"id": args[0], "entity_type": "person"}
        return None

    async def fetch(self, sql, *args):
        # The entity lock is a locking SELECT, whose rows the writer then judges live or dead.
        if "FOR KEY SHARE" in sql:
            self.events.append("lock")
            raise _StopAtLock
        return []

    async def fetchval(self, sql, *args):
        return None

    async def execute(self, sql, *args):
        if "FOR KEY SHARE" in sql:
            self.events.append("lock")
            raise _StopAtLock
        return "OK"


class _StopAtLock(Exception):
    pass


async def test_inverse_embed_happens_before_the_entity_locks():
    events: list[str] = []

    class Recording(Engine):
        def embed(self, text):
            events.append(f"embed:{text}")
            return super().embed(text)

    conn = _OrderConn(events)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_AcquireCtx(conn))
    child, parent = uuid.uuid4(), uuid.uuid4()
    with pytest.raises(_StopAtLock):
        await storage.store_fact(
            pool,
            "Sam",
            "child_of",
            "Alex",
            cast(Any, Recording()),
            entity_id=child,
            object_entity_id=parent,
        )
    assert events[-1] == "lock"
    assert any(e.startswith("embed:Alex parent_of Sam") for e in events), events


async def test_provenance_placeholder_embedding_is_computed_once_per_model():
    engine = Engine()
    for _ in range(3):
        await storage._provenance_placeholder_embedding(cast(Any, engine))
    assert len(engine.calls) == 1
