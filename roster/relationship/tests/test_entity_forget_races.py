"""Forget and delete against a concurrent merge, on real Postgres (bu-dhloi1.74).

A merge holds both entity rows ``FOR UPDATE``. A forget or delete that writes its
marker last, without that lock, neither waits for a merge nor makes one wait, so
references can land on the entity after it retracted what was there.
"""

from __future__ import annotations

import asyncio
import shutil
import uuid
from unittest.mock import MagicMock, patch

import asyncpg
import pytest
from fastapi import HTTPException

from butlers import entity_rebind
from butlers.api.router_discovery import discover_butler_routers
from butlers.api.routers import memory as memory_router
from butlers.modules.memory import storage as memory_storage
from butlers.modules.memory.storage import store_fact
from butlers.modules.memory.tools import entities as memory_entities
from butlers.roster_imports import roster_module

entity_merge = roster_module("relationship", "tools/entity_merge")
TargetEntityTombstonedError = entity_merge.TargetEntityTombstonedError
merge_entity_pair = entity_merge.merge_entity_pair

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]


@pytest.fixture
async def rel_pool(db):
    """Core + memory + relationship chains, with the owner the forget route requires."""
    pool = await db("core", "memory", "relationship", min_size=2, max_size=8)
    await _entity(pool, "Owner", roles=["owner"])
    return pool


async def _entity(pool: asyncpg.Pool, tag: str, *, roles: list[str] | None = None) -> uuid.UUID:
    return await pool.fetchval(
        "INSERT INTO public.entities (canonical_name, entity_type, roles) "
        "VALUES ($1, 'person', $2) RETURNING id",
        f"{tag} {uuid.uuid4()}",
        roles or [],
    )


async def _entity_fact(pool: asyncpg.Pool, subject: uuid.UUID, value: str) -> uuid.UUID:
    return await pool.fetchval(
        "INSERT INTO relationship.entity_facts (subject, predicate, object, object_kind, src) "
        "VALUES ($1, 'has-email', $2, 'literal', 'test') RETURNING id",
        subject,
        value,
    )


async def _memory_fact(pool: asyncpg.Pool, entity: uuid.UUID) -> uuid.UUID:
    return await pool.fetchval(
        "INSERT INTO facts (subject, entity_id, predicate, content) "
        "VALUES ('opaque-subject', $1, 'note', 'opaque') RETURNING id",
        entity,
    )


async def _settled_or_lock_waiting(pool: asyncpg.Pool, task: asyncio.Task) -> None:
    """Return once ``task`` has finished, or is blocked on a row lock."""
    for _ in range(500):
        if task.done() or await pool.fetchval(
            "SELECT count(*) FROM pg_stat_activity "
            "WHERE datname = current_database() AND wait_event_type = 'Lock'"
        ):
            return
        await asyncio.sleep(0.01)
    pytest.fail("the concurrent operation neither finished nor waited on a lock")


def _dashboard_db(pool: object) -> MagicMock:
    """A DatabaseManager-shaped stub whose every butler resolves to ``pool``."""
    db = MagicMock()
    db.butler_names = ["relationship"]
    db.pool = MagicMock(return_value=pool)
    return db


def _forget_route():
    router = next(mod for name, mod in discover_butler_routers() if name == "relationship")
    return router.forget_entity


class _PausingPool:
    """Pauses after the delete route's fact retirement, before it writes its marker."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool
        self.retired = asyncio.Event()
        self.resume = asyncio.Event()

    def __getattr__(self, name: str) -> object:
        return getattr(self._pool, name)

    async def execute(self, query: str, *args: object) -> str:
        result = await self._pool.execute(query, *args)
        if "validity = 'retracted'" in query:
            self.retired.set()
            await self.resume.wait()
        return result


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("forgotten_has_a_fact", [False, True], ids=["stranding", "deadlock"])
async def test_a_merge_cannot_slip_into_a_forget_in_flight(rel_pool, forgotten_has_a_fact) -> None:
    """Without the lock first, a merge strands a fact, or deadlocks over a retracted row."""
    forgotten = await _entity(rel_pool, "Forgotten")
    source = await _entity(rel_pool, "Source")
    fact = await _entity_fact(rel_pool, source, "source@example.test")
    if forgotten_has_a_fact:
        await _entity_fact(rel_pool, forgotten, "forgotten@example.test")

    retracted = asyncio.Event()
    resume = asyncio.Event()
    real_retract = memory_entities._retract_facts_on_conn

    async def pausing_retract(conn, entity_id):
        await real_retract(conn, entity_id)
        retracted.set()
        await resume.wait()

    with patch.object(memory_entities, "_retract_facts_on_conn", pausing_retract):
        forget = asyncio.create_task(_forget_route()(forgotten, db=_dashboard_db(rel_pool)))
        await asyncio.wait_for(retracted.wait(), timeout=10)
        merge = asyncio.create_task(
            merge_entity_pair(rel_pool, source_entity_id=source, target_entity_id=forgotten)
        )
        await _settled_or_lock_waiting(rel_pool, merge)
        resume.set()
        forget_result, merge_result = await asyncio.wait_for(
            asyncio.gather(forget, merge, return_exceptions=True), timeout=20
        )

    assert not isinstance(forget_result, BaseException), repr(forget_result)
    assert isinstance(merge_result, TargetEntityTombstonedError), repr(merge_result)
    row = await rel_pool.fetchrow(
        "SELECT subject, validity FROM relationship.entity_facts WHERE id = $1", fact
    )
    assert (row["subject"], row["validity"]) == (source, "active")


@pytest.mark.asyncio(loop_scope="session")
async def test_a_forget_waits_for_a_merge_in_flight_and_retracts_what_it_moved(rel_pool) -> None:
    """The other order: the merge goes first, and what it moved must not outlive the forget."""
    forgotten = await _entity(rel_pool, "Forgotten")
    source = await _entity(rel_pool, "Source")
    fact = await _entity_fact(rel_pool, source, "source@example.test")

    locked = asyncio.Event()
    resume = asyncio.Event()

    async def pause_under_the_locks(_conn, _pair) -> None:
        locked.set()
        await resume.wait()

    merge = asyncio.create_task(
        merge_entity_pair(
            rel_pool,
            source_entity_id=source,
            target_entity_id=forgotten,
            locked_guard=pause_under_the_locks,
        )
    )
    await asyncio.wait_for(locked.wait(), timeout=10)
    forget = asyncio.create_task(_forget_route()(forgotten, db=_dashboard_db(rel_pool)))
    await _settled_or_lock_waiting(rel_pool, forget)
    resume.set()
    await asyncio.wait_for(asyncio.gather(merge, forget), timeout=20)

    row = await rel_pool.fetchrow(
        "SELECT subject, validity FROM relationship.entity_facts WHERE id = $1", fact
    )
    assert (row["subject"], row["validity"]) == (forgotten, "retracted")


@pytest.mark.asyncio(loop_scope="session")
async def test_a_merge_cannot_slip_into_a_memory_delete_in_flight(rel_pool) -> None:
    """Retirement is done and the marker unwritten: a merge must wait, then be refused."""
    deleted = await _entity(rel_pool, "Deleted")
    source = await _entity(rel_pool, "Source")
    await _memory_fact(rel_pool, deleted)
    fact = await _memory_fact(rel_pool, source)
    pausing = _PausingPool(rel_pool)

    delete = asyncio.create_task(
        memory_router.delete_entity(str(deleted), retire_facts=True, db=_dashboard_db(pausing))
    )
    await asyncio.wait_for(pausing.retired.wait(), timeout=10)
    merge = asyncio.create_task(
        merge_entity_pair(rel_pool, source_entity_id=source, target_entity_id=deleted)
    )
    await _settled_or_lock_waiting(rel_pool, merge)
    pausing.resume.set()
    delete_result, merge_result = await asyncio.wait_for(
        asyncio.gather(delete, merge, return_exceptions=True), timeout=20
    )

    assert not isinstance(delete_result, BaseException), repr(delete_result)
    assert isinstance(merge_result, TargetEntityTombstonedError), repr(merge_result)
    row = await rel_pool.fetchrow("SELECT entity_id, validity FROM facts WHERE id = $1", fact)
    assert (row["entity_id"], row["validity"]) == (source, "active")


@pytest.mark.asyncio(loop_scope="session")
async def test_a_memory_delete_that_cannot_finish_writes_no_marker_and_can_be_retried(
    rel_pool,
) -> None:
    """A writer holding a fact and waiting on the entity is a cycle Postgres cannot see.

    The delete's lock and its retirement are on different connections, so only the
    delete's own deadline can end it. It rolls back, and a retry then completes.
    """
    deleted = await _entity(rel_pool, "Deleted")
    fact = await _memory_fact(rel_pool, deleted)
    writer_holds_fact = asyncio.Event()
    delete_failed = asyncio.Event()

    async def writer() -> None:
        async with rel_pool.acquire() as conn, conn.transaction():
            await conn.execute("UPDATE facts SET content = 'edited' WHERE id = $1", fact)
            writer_holds_fact.set()
            await delete_failed.wait()
            await conn.execute("SELECT 1 FROM public.entities WHERE id = $1 FOR KEY SHARE", deleted)

    writing = asyncio.create_task(writer())
    await writer_holds_fact.wait()
    with (
        patch.object(memory_router, "_ENTITY_DELETE_DEADLINE_S", 0.5),
        pytest.raises(HTTPException) as refused,
    ):
        await asyncio.wait_for(
            memory_router.delete_entity(
                str(deleted), retire_facts=True, db=_dashboard_db(rel_pool)
            ),
            timeout=15,
        )
    delete_failed.set()
    await asyncio.wait_for(writing, timeout=10)

    assert refused.value.status_code == 503
    marker = "SELECT metadata->>'deleted_at' FROM public.entities WHERE id = $1"
    assert await rel_pool.fetchval(marker, deleted) is None

    await memory_router.delete_entity(str(deleted), retire_facts=True, db=_dashboard_db(rel_pool))

    assert await rel_pool.fetchval(marker, deleted) is not None
    assert await rel_pool.fetchval("SELECT validity FROM facts WHERE id = $1", fact) == "retracted"


async def _pending_rebind(pool: asyncpg.Pool, source: uuid.UUID, target: uuid.UUID) -> uuid.UUID:
    rebind_id = uuid.uuid4()
    await pool.execute(
        """
        INSERT INTO public.entity_rebind_log (
            rebind_id, source_entity_id, target_entity_id, target_schema
        ) VALUES ($1, $2, $3, 'relationship')
        """,
        rebind_id,
        source,
        target,
    )
    return rebind_id


async def _forget_or_delete_paused_after_retiring(
    pool: asyncpg.Pool, entity: uuid.UUID, route: str
):
    """Start the route, wait until it has retired facts; returns (task, resume, patcher)."""
    if route == "delete":
        pausing = _PausingPool(pool)
        task = asyncio.create_task(
            memory_router.delete_entity(str(entity), retire_facts=True, db=_dashboard_db(pausing))
        )
        await asyncio.wait_for(pausing.retired.wait(), timeout=10)
        return task, pausing.resume, None

    retracted = asyncio.Event()
    resume = asyncio.Event()
    real_retract = memory_entities._retract_facts_on_conn

    async def pausing_retract(conn, entity_id):
        await real_retract(conn, entity_id)
        retracted.set()
        await resume.wait()

    patcher = patch.object(memory_entities, "_retract_facts_on_conn", pausing_retract)
    patcher.start()
    task = asyncio.create_task(_forget_route()(entity, db=_dashboard_db(pool)))
    await asyncio.wait_for(retracted.wait(), timeout=10)
    return task, resume, patcher


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("route", ["forget", "delete"])
async def test_a_rebind_cannot_slip_into_a_forget_or_delete_in_flight(rel_pool, route) -> None:
    """The route has retired facts and not written its marker: the rebind must wait, then refuse."""
    forgotten = await _entity(rel_pool, "Forgotten")
    source = await _entity(rel_pool, "Source")
    await _memory_fact(rel_pool, forgotten)  # The delete pauses only after retiring something.
    fact = await _memory_fact(rel_pool, source)
    rebind_id = await _pending_rebind(rel_pool, source, forgotten)

    task, resume, patcher = await _forget_or_delete_paused_after_retiring(
        rel_pool, forgotten, route
    )
    try:
        rebind = asyncio.create_task(
            entity_rebind.process_pending_entity_rebinds(rel_pool, target_schema="relationship")
        )
        await _settled_or_lock_waiting(rel_pool, rebind)
        resume.set()
        await asyncio.wait_for(asyncio.gather(task, rebind), timeout=20)
    finally:
        if patcher is not None:
            patcher.stop()

    row = await rel_pool.fetchrow("SELECT entity_id, validity FROM facts WHERE id = $1", fact)
    assert (row["entity_id"], row["validity"]) == (source, "active")
    receipt = await rel_pool.fetchrow(
        "SELECT status, error_class FROM public.entity_rebind_log WHERE rebind_id = $1", rebind_id
    )
    assert (receipt["status"], receipt["error_class"]) == ("failed", "EntityRebindTargetForgotten")


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("route", ["forget", "delete"])
async def test_a_forget_or_delete_waits_for_a_rebind_and_retires_what_it_moved(
    rel_pool, route
) -> None:
    """The other order: what the rebind moved must not outlive the forget or delete."""
    forgotten = await _entity(rel_pool, "Forgotten")
    source = await _entity(rel_pool, "Source")
    fact = await _memory_fact(rel_pool, source)
    await _pending_rebind(rel_pool, source, forgotten)

    moved = asyncio.Event()
    resume = asyncio.Event()
    real_repoint = entity_rebind._repoint_facts

    async def pausing_repoint(conn, from_entity, to_entity):
        count = await real_repoint(conn, from_entity, to_entity)
        moved.set()
        await resume.wait()
        return count

    with patch.object(entity_rebind, "_repoint_facts", pausing_repoint):
        rebind = asyncio.create_task(
            entity_rebind.process_pending_entity_rebinds(rel_pool, target_schema="relationship")
        )
        await asyncio.wait_for(moved.wait(), timeout=10)
        if route == "forget":
            task = asyncio.create_task(_forget_route()(forgotten, db=_dashboard_db(rel_pool)))
        else:
            task = asyncio.create_task(
                memory_router.delete_entity(
                    str(forgotten), retire_facts=True, db=_dashboard_db(rel_pool)
                )
            )
        await _settled_or_lock_waiting(rel_pool, task)
        resume.set()
        await asyncio.wait_for(asyncio.gather(rebind, task), timeout=20)

    row = await rel_pool.fetchrow("SELECT entity_id, validity FROM facts WHERE id = $1", fact)
    assert (row["entity_id"], row["validity"]) == (forgotten, "retracted")


def _fake_engine() -> MagicMock:
    engine = MagicMock()
    engine.embed.return_value = [0.0] * 384
    engine.model_name = "test-model"
    return engine


async def _store(
    pool: asyncpg.Pool, subject: uuid.UUID, content: str, obj: uuid.UUID | None = None
) -> uuid.UUID:
    result = await store_fact(
        pool,
        subject="opaque-subject",
        predicate="note",
        content=content,
        embedding_engine=_fake_engine(),
        entity_id=subject,
        object_entity_id=obj,
    )
    return result["id"]


def _forget_or_delete(pool: asyncpg.Pool, entity: uuid.UUID, route: str):
    if route == "forget":
        return _forget_route()(entity, db=_dashboard_db(pool))
    return memory_router.delete_entity(str(entity), retire_facts=True, db=_dashboard_db(pool))


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize(
    ("route", "edge"),
    [("forget", False), ("forget", True), ("delete", False)],
    ids=["forget", "forget_edge_object", "delete"],
)
async def test_a_supersede_in_flight_finishes_before_a_forget_or_delete(
    rel_pool, route, edge
) -> None:
    """The writer got there first: its new fact is retired too, and neither side fails."""
    removed = await _entity(rel_pool, "Removed")
    subject = await _entity(rel_pool, "Other") if edge else removed
    obj = removed if edge else None
    old = await _store(rel_pool, subject, "old", obj)

    superseded = asyncio.Event()
    resume = asyncio.Event()
    real_insert = memory_storage._insert_fact_record

    async def pausing_insert(*args, **kwargs):
        superseded.set()
        await resume.wait()
        return await real_insert(*args, **kwargs)

    with (
        patch.object(memory_storage, "_insert_fact_record", pausing_insert),
        patch.object(memory_router, "_ENTITY_DELETE_DEADLINE_S", 3.0),
    ):
        writer = asyncio.create_task(_store(rel_pool, subject, "new", obj))
        await asyncio.wait_for(superseded.wait(), timeout=10)
        task = asyncio.create_task(_forget_or_delete(rel_pool, removed, route))
        await _settled_or_lock_waiting(rel_pool, task)
        resume.set()
        new, result = await asyncio.wait_for(
            asyncio.gather(writer, task, return_exceptions=True), timeout=20
        )

    assert not isinstance(new, BaseException), repr(new)
    assert not isinstance(result, BaseException), repr(result)
    validity = "SELECT validity FROM facts WHERE id = $1"
    assert await rel_pool.fetchval(validity, old) == "superseded"
    assert await rel_pool.fetchval(validity, new) == "retracted"


@pytest.mark.asyncio(loop_scope="session")
async def test_a_supersede_waits_for_a_forget_that_holds_the_entity(rel_pool) -> None:
    """The forget got there first, before retracting: the writer must not take the fact."""
    forgotten = await _entity(rel_pool, "Forgotten")
    old = await _store(rel_pool, forgotten, "old")

    locked = asyncio.Event()
    resume = asyncio.Event()
    real_retract = memory_entities._retract_facts_on_conn

    async def pausing_retract(conn, entity_id):
        locked.set()
        await resume.wait()
        return await real_retract(conn, entity_id)

    with patch.object(memory_entities, "_retract_facts_on_conn", pausing_retract):
        forget = asyncio.create_task(_forget_route()(forgotten, db=_dashboard_db(rel_pool)))
        await asyncio.wait_for(locked.wait(), timeout=10)
        writer = asyncio.create_task(_store(rel_pool, forgotten, "new"))
        await _settled_or_lock_waiting(rel_pool, writer)
        resume.set()
        result, new = await asyncio.wait_for(
            asyncio.gather(forget, writer, return_exceptions=True), timeout=20
        )

    assert not isinstance(result, BaseException), repr(result)
    assert not isinstance(new, BaseException), repr(new)
    assert await rel_pool.fetchval("SELECT validity FROM facts WHERE id = $1", old) == "retracted"


@pytest.mark.asyncio(loop_scope="session")
async def test_two_deletes_on_a_pool_of_two_both_complete(db) -> None:
    """Each delete holds a connection across its fan-out, and deployed pools have two."""
    pool = await db("core", "memory", "relationship", min_size=2, max_size=2)
    first = await _entity(pool, "First")
    second = await _entity(pool, "Second")
    await _memory_fact(pool, first)
    await _memory_fact(pool, second)

    with patch.object(memory_router, "_ENTITY_DELETE_DEADLINE_S", 2.0):
        results = await asyncio.wait_for(
            asyncio.gather(
                _forget_or_delete(pool, first, "delete"),
                _forget_or_delete(pool, second, "delete"),
                return_exceptions=True,
            ),
            timeout=20,
        )

    assert results == [None, None]
