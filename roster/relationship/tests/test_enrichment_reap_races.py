"""The enrichment reap against a writer or merge in flight, on real Postgres (bu-dhloi1.101).

The reap soft-deletes a placeholder only if nothing references it. A waiting UPDATE re-checks
its row but not its statement's snapshot, so a reference still uncommitted when the reap
started goes unseen.
"""

from __future__ import annotations

import asyncio
import shutil
import uuid

import asyncpg
import pytest

from butlers.roster_imports import roster_module

reap = roster_module(
    "relationship", "jobs/relationship_jobs"
).archive_rejected_identity_enrichment_orphans
merge_entity_pair = roster_module("relationship", "tools/entity_merge").merge_entity_pair

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]


@pytest.fixture
async def rel_pool(db):
    return await db("core", "memory", "relationship", "approvals", min_size=2, max_size=8)


async def _placeholder(pool: asyncpg.Pool) -> uuid.UUID:
    return await pool.fetchval(
        "INSERT INTO public.entities (canonical_name, entity_type, metadata) "
        "VALUES ($1, 'person', $2::jsonb) RETURNING id",
        f"Placeholder {uuid.uuid4()}",
        {
            "proposed_source": "email_identity_enrichment",
            "proposed_from_address": f"{uuid.uuid4().hex}@example.test",
        },
    )


async def _entity(pool: asyncpg.Pool, tag: str) -> uuid.UUID:
    return await pool.fetchval(
        "INSERT INTO public.entities (canonical_name, entity_type) "
        "VALUES ($1, 'person') RETURNING id",
        f"{tag} {uuid.uuid4()}",
    )


_INSERT_FACT = (
    "INSERT INTO relationship.entity_facts (subject, predicate, object, object_kind, src) "
    "VALUES ($1, 'has-email', $2, 'literal', 'test') RETURNING id"
)


async def _deleted_at(pool: asyncpg.Pool, entity: uuid.UUID) -> str | None:
    return await pool.fetchval(
        "SELECT metadata->>'deleted_at' FROM public.entities WHERE id = $1", entity
    )


async def _settled_or_lock_waiting(pool: asyncpg.Pool, task: asyncio.Task) -> None:
    for _ in range(500):
        if task.done() or await pool.fetchval(
            "SELECT count(*) FROM pg_stat_activity "
            "WHERE datname = current_database() AND wait_event_type = 'Lock'"
        ):
            return
        await asyncio.sleep(0.01)
    pytest.fail("the reap neither finished nor waited on a lock")


@pytest.mark.asyncio(loop_scope="session")
async def test_the_reap_leaves_an_entity_with_a_fact_write_in_flight(rel_pool) -> None:
    """An approved proposal is writing its first fact: this entity has graduated."""
    entity = await _placeholder(rel_pool)

    async with rel_pool.acquire() as conn, conn.transaction():
        await conn.fetchval(_INSERT_FACT, entity, "in-flight@example.test")
        # A batch job must not queue behind the writer; it skips the row for the next run.
        assert await asyncio.wait_for(reap(rel_pool), timeout=5) == 0

    assert await reap(rel_pool) == 0
    assert await _deleted_at(rel_pool, entity) is None


class _CommitsAFactAfterTheCandidateScan:
    """A writer commits after the scan chose its candidates, and before the reap locks one."""

    def __init__(self, pool: asyncpg.Pool, entity: uuid.UUID) -> None:
        self._pool = pool
        self._entity = entity

    def __getattr__(self, name: str) -> object:
        return getattr(self._pool, name)

    async def fetch(self, query: str, *args: object) -> list:
        rows = await self._pool.fetch(query, *args)
        await self._pool.fetchval(_INSERT_FACT, self._entity, "late@example.test")
        return rows


@pytest.mark.asyncio(loop_scope="session")
async def test_the_reap_rechecks_under_its_lock(rel_pool) -> None:
    entity = await _placeholder(rel_pool)

    assert await reap(_CommitsAFactAfterTheCandidateScan(rel_pool, entity)) == 0

    assert await _deleted_at(rel_pool, entity) is None


@pytest.mark.asyncio(loop_scope="session")
async def test_the_reap_comes_back_for_an_entity_whose_writer_rolled_back(rel_pool) -> None:
    entity = await _placeholder(rel_pool)

    conn = await rel_pool.acquire()
    try:
        transaction = conn.transaction()
        await transaction.start()
        await conn.fetchval(_INSERT_FACT, entity, "abandoned@example.test")
        reaping = asyncio.create_task(reap(rel_pool))
        await _settled_or_lock_waiting(rel_pool, reaping)
        await transaction.rollback()
    finally:
        await rel_pool.release(conn)

    first = await asyncio.wait_for(reaping, timeout=10)
    assert first + await reap(rel_pool) == 1
    assert await _deleted_at(rel_pool, entity) is not None


@pytest.mark.asyncio(loop_scope="session")
async def test_the_reap_leaves_the_target_of_a_merge_in_flight(rel_pool) -> None:
    """The merge moves facts onto the placeholder, which the reap's snapshot cannot see."""
    target = await _placeholder(rel_pool)
    source = await _entity(rel_pool, "Source")
    fact = await rel_pool.fetchval(_INSERT_FACT, source, "moved@example.test")

    locked = asyncio.Event()
    resume = asyncio.Event()

    async def pause_under_the_locks(_conn, _pair) -> None:
        locked.set()
        await resume.wait()

    merge = asyncio.create_task(
        merge_entity_pair(
            rel_pool,
            source_entity_id=source,
            target_entity_id=target,
            locked_guard=pause_under_the_locks,
        )
    )
    await asyncio.wait_for(locked.wait(), timeout=10)
    reaping = asyncio.create_task(reap(rel_pool))
    await _settled_or_lock_waiting(rel_pool, reaping)
    resume.set()
    await asyncio.wait_for(asyncio.gather(merge, reaping), timeout=20)

    assert await reap(rel_pool) == 0
    assert await _deleted_at(rel_pool, target) is None
    row = await rel_pool.fetchrow(
        "SELECT subject, validity FROM relationship.entity_facts WHERE id = $1", fact
    )
    assert (row["subject"], row["validity"]) == (target, "active")


@pytest.mark.asyncio(loop_scope="session")
async def test_a_merge_refuses_a_target_the_reap_already_took(rel_pool) -> None:
    """The other order: the placeholder is already reaped, so a merge onto it is refused."""
    target = await _placeholder(rel_pool)
    source = await _entity(rel_pool, "Source")
    fact = await rel_pool.fetchval(_INSERT_FACT, source, "kept@example.test")

    assert await reap(rel_pool) == 1
    with pytest.raises(
        roster_module("relationship", "tools/entity_merge").TargetEntityTombstonedError
    ):
        await merge_entity_pair(rel_pool, source_entity_id=source, target_entity_id=target)

    assert (
        await rel_pool.fetchval("SELECT subject FROM relationship.entity_facts WHERE id = $1", fact)
        == source
    )
