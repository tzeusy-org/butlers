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


def test_storage_never_calls_the_sync_embed_directly():
    """Every embed in storage.py goes through embed_async (covers sites needing a full DB)."""
    tree = ast.parse(STORAGE.read_text(encoding="utf-8"))
    sync_calls = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"embed", "embed_batch"}
    ]
    assert not sync_calls, f"synchronous embed calls in storage.py at lines {sync_calls}"
