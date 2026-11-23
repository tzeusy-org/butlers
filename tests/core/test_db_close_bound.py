"""``Database.close`` is bounded (bu-dhloi1.153).

``pool.close()`` waits for every connection to be released, and a connection stuck in a query
never is, so shutdown could block there for good and be SIGKILLed with the rest unreleased.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.db import Database

pytestmark = pytest.mark.unit


def _db(pool) -> Database:
    db = Database(db_name="x", host="h", port=1, user="u", password="p")
    db.pool = pool
    return db


async def test_a_pool_that_will_not_close_is_terminated_at_the_timeout() -> None:
    stuck = asyncio.Event()
    pool = MagicMock(close=AsyncMock(side_effect=stuck.wait), terminate=MagicMock())
    db = _db(pool)

    async with asyncio.timeout(5):
        await db.close(timeout=0.05)

    pool.terminate.assert_called_once()
    assert db.pool is None


async def test_a_pool_that_closes_in_time_is_not_terminated() -> None:
    pool = MagicMock(close=AsyncMock(), terminate=MagicMock())
    db = _db(pool)

    await db.close()

    pool.close.assert_awaited_once()
    pool.terminate.assert_not_called()
    assert db.pool is None
