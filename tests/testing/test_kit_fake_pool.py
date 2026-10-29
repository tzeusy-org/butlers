"""Strict configured calls and real async context cleanup, without a DB."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from butlers.testing.fake_pool import AsyncCtx, fake_pool

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["success", "error", "cancel"])
async def test_async_context_exits_once_without_suppressing_body(outcome):
    value = object()
    released = AsyncMock()
    context = AsyncCtx(value, on_exit=released)
    entered = asyncio.Event()

    async def body():
        async with context as actual:
            assert actual is value
            entered.set()
            if outcome == "error":
                raise RuntimeError("body refusal")
            if outcome == "cancel":
                await asyncio.Event().wait()

    task = asyncio.create_task(body())
    await entered.wait()
    if outcome == "cancel":
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    elif outcome == "error":
        with pytest.raises(RuntimeError, match="body refusal"):
            await task
    else:
        await task
    assert context.enter_count == context.exit_count == 1
    released.assert_awaited_once_with(value)


@pytest.mark.asyncio
async def test_fake_pool_configured_calls_share_connection_and_release_once():
    queued = AsyncMock(side_effect=[{"id": 1}, {"id": 2}])
    pool = fake_pool(
        fetch=[{"id": 1}], fetchrow=queued, fetchval=None, execute="UPDATE 1", executemany=None
    )
    async with pool.acquire() as conn:
        async with conn.transaction() as transaction:
            assert transaction is None
            assert await conn.fetch("SELECT", 1) == [{"id": 1}]
            assert await conn.fetchrow("FIRST") == {"id": 1}
            assert await pool.fetchrow("SECOND") == {"id": 2}
            assert await conn.fetchval("NULL") is None
            assert await conn.execute("UPDATE", 2) == "UPDATE 1"
            assert await conn.executemany("INSERT", [(1,), (2,)]) is None
    pool.fetch.assert_awaited_once_with("SELECT", 1)
    assert pool.fetchrow is queued is conn.fetchrow
    assert pool.fetchrow.await_count == 2
    pool.execute.assert_awaited_once_with("UPDATE", 2)
    pool.release.assert_awaited_once_with(conn)
    pool.acquire.assert_called_once_with()
    conn.transaction.assert_called_once_with()


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["fetch", "fetchrow", "fetchval", "execute", "executemany"])
async def test_fake_pool_refuses_unconfigured_operations(method):
    pool = fake_pool()
    with pytest.raises(AssertionError, match=f"fake_pool: unexpected {method}"):
        await getattr(pool, method)("any SQL")
    async with pool.acquire() as conn:
        with pytest.raises(AssertionError, match=f"fake_pool: unexpected {method}"):
            await getattr(conn, method)("any SQL")
    pool.release.assert_awaited_once_with(conn)


@pytest.mark.asyncio
async def test_fake_pool_propagates_configured_failure_and_instances_are_isolated():
    pool = fake_pool(execute=AsyncMock(side_effect=ValueError("configured refusal")))
    other = fake_pool(execute="OK")
    with pytest.raises(ValueError, match="configured refusal"):
        async with pool.acquire() as conn:
            await conn.execute("UPDATE")
    pool.release.assert_awaited_once_with(pool.connection)
    assert await other.execute("UPDATE") == "OK"
    assert pool.connection is not other.connection
    assert pool.execute is not other.execute


def test_fake_pool_refuses_sql_dispatch_and_unknown_configuration():
    with pytest.raises(TypeError, match="callable"):
        fake_pool(fetch=lambda sql: [])
    with pytest.raises(TypeError, match="unknown"):
        fake_pool(unknown=[])
