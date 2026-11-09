# ruff: noqa: F811
"""A fact insert that waited on the entity lock refuses an entity gone meanwhile (bu-dhloi1.94).

The foreign key only proves the row exists, so an insert that waited out a delete or a
merge would commit an active fact onto the tombstoned entity. Real Postgres, real lock
wait: one connection holds the entity ``FOR UPDATE`` as ``delete_entity`` does, the
writer blocks on the lock, and the marker commits before the writer is released.
"""

from __future__ import annotations

import asyncio
import shutil
import uuid

import pytest

from butlers.modules.memory import storage
from tests.modules.memory.test_content_authority import (  # noqa: F401
    _engine,
    pool_factory,
)
from tests.modules.memory.test_knowledge_gaps import _entity, session_hook  # noqa: F401
from tests.modules.memory.test_knowledge_gaps_inverse_pair import (  # noqa: F401
    _BorrowedPool,
    _edge_predicates,
)
from tests.modules.memory.test_memory_migration_integration import (  # noqa: F401
    memory_migrated_db,
)
from tests.polling import poll_until

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]


async def _wait_for_lock_wait(pool, pid):
    async with asyncio.timeout(10):

        async def _waiting() -> bool:
            return await pool.fetchval(
                "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE pid = $1 AND NOT granted)", pid
            )

        await poll_until(_waiting, interval_s=0.01)


async def _fact_count(pool, entity_id) -> int:
    return await pool.fetchval("SELECT count(*) FROM facts WHERE entity_id = $1", entity_id)


@pytest.mark.parametrize(
    "marker",
    [{"deleted_at": "2026-11-09T00:00:00+00:00"}, {"merged_into": str(uuid.uuid4())}],
    ids=["deleted", "merged"],
)
@pytest.mark.parametrize("as_object", [False, True], ids=["subject", "object"])
async def test_insert_that_waited_on_the_entity_lock_refuses_a_marked_entity(
    pool_factory, session_hook, marker, as_object
):
    pool, owner, _ = await pool_factory()
    session_hook(pool)
    live, doomed = await _entity(pool), await _entity(pool)
    forward, _ = await _edge_predicates(pool)
    subject, obj = (live, doomed) if as_object else (doomed, live)
    async with pool.acquire() as holder, pool.acquire() as waiter:
        tx = holder.transaction()
        await tx.start()
        pid = await waiter.fetchval("SELECT pg_backend_pid()")
        pending = None
        try:
            await holder.fetchval("SELECT id FROM public.entities WHERE id = $1 FOR UPDATE", doomed)
            pending = asyncio.create_task(
                storage.store_fact(
                    _BorrowedPool(pool, waiter),
                    "Mei",
                    forward,
                    "Hana",
                    _engine(),
                    entity_id=subject,
                    object_entity_id=obj,
                )
            )
            await _wait_for_lock_wait(pool, pid)
            assert not pending.done()
            await holder.execute(
                "UPDATE public.entities SET metadata = COALESCE(metadata, '{}'::jsonb) || $2"
                " WHERE id = $1",
                doomed,
                marker,
            )
            await tx.commit()
            with pytest.raises(storage.EntityUnavailableError):
                await asyncio.wait_for(pending, 10)
        finally:
            if holder.is_in_transaction():
                await tx.rollback()
            if pending is not None and not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
    assert await _fact_count(pool, doomed) == 0


async def test_insert_that_waited_on_a_hard_delete_refuses_the_missing_entity(
    pool_factory, session_hook
):
    pool, owner, _ = await pool_factory()
    session_hook(pool)
    doomed = await _entity(pool)
    forward, _ = await _edge_predicates(pool)
    del forward
    predicate = f"kg_attr_{uuid.uuid4().hex}"
    await pool.execute(
        "INSERT INTO predicate_registry (name, is_edge, is_temporal, status)"
        " VALUES ($1, false, false, 'active')",
        predicate,
    )
    async with pool.acquire() as holder, pool.acquire() as waiter:
        tx = holder.transaction()
        await tx.start()
        pid = await waiter.fetchval("SELECT pg_backend_pid()")
        pending = None
        try:
            await holder.fetchval("SELECT id FROM public.entities WHERE id = $1 FOR UPDATE", doomed)
            pending = asyncio.create_task(
                storage.store_fact(
                    _BorrowedPool(pool, waiter),
                    "Mei",
                    predicate,
                    "Hana",
                    _engine(),
                    entity_id=doomed,
                )
            )
            await _wait_for_lock_wait(pool, pid)
            await holder.execute("DELETE FROM public.entities WHERE id = $1", doomed)
            await tx.commit()
            with pytest.raises(storage.EntityUnavailableError):
                await asyncio.wait_for(pending, 10)
        finally:
            if holder.is_in_transaction():
                await tx.rollback()
            if pending is not None and not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
