# ruff: noqa: F811
"""Owner knowledge gaps against real Postgres (bu-q7vx1q.9).

Every test runs at a real seam: the ``conversation_reply`` tool with the registered
session hook, ``storage.store_fact`` writing the answering fact, and the delivery job
posting into ``public.dashboard_messages``.  The migrated-DB fixture is shared with the
content-authority tests so provisioning stays defined in one place.
"""

from __future__ import annotations

import asyncio
import shutil
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import asyncpg
import pytest

from butlers.api.conversations import conversation_create, conversation_reply_create, message_create
from butlers.core import memory_hooks
from butlers.core_tools import _conversation_reply as reply_mod
from butlers.core_tools._base import ToolContext
from butlers.modules.memory import knowledge_gaps, storage

# Re-used so the migrated-DB provisioning stays defined in exactly one place.
from tests.modules.memory.test_content_authority import (  # noqa: F401
    _engine,
    _routed,
    _session,
    pool_factory,
)
from tests.modules.memory.test_memory_migration_integration import (  # noqa: F401
    memory_migrated_db,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

BUTLER = "kgtest"


async def _predicate(pool: asyncpg.Pool) -> str:
    return await pool.fetchval(
        "SELECT name FROM predicate_registry WHERE NOT is_edge AND status = 'active'"
        " ORDER BY name LIMIT 1"
    )


async def _entity(pool: asyncpg.Pool) -> uuid.UUID:
    return await pool.fetchval(
        "INSERT INTO public.entities (canonical_name, entity_type)"
        " VALUES ($1, 'person') RETURNING id",
        f"Mei-{uuid.uuid4().hex[:8]}",
    )


async def _conversation(pool: asyncpg.Pool, question: str = "What is Mei's daughter's name?"):
    conv = await conversation_create(pool, butler_name=BUTLER, first_message=question)
    await message_create(pool, conversation_id=conv["id"], role="user", content=question)
    return conv["id"]


async def _decline(pool, conv_id, entity_id, predicate, *, request_id=None):
    """Drive the real ``conversation_reply`` tool as a dashboard-routed decline."""
    registered: dict = {}

    def core_tool(_group, **_kw):
        def decorator(fn):
            registered[fn.__name__] = fn
            return fn

        return decorator

    original = reply_mod.tool_span
    reply_mod.tool_span = lambda *_a, **_kw: lambda fn: fn
    try:
        ctx = ToolContext(
            daemon=SimpleNamespace(),
            pool=pool,
            spawner=None,
            butler_name=BUTLER,
            butler_type=None,
            is_switchboard=False,
            is_messenger=False,
            route_metrics=None,
        )
        reply_mod.register_conversation_reply_tool(ctx, SimpleNamespace(), core_tool)
    finally:
        reply_mod.tool_span = original

    request_id = request_id or uuid.uuid4()
    session_id = str(uuid.uuid4())
    from butlers.core.tool_call_capture import (
        clear_runtime_session_routing_context,
        reset_current_runtime_session_id,
        set_current_runtime_session_id,
        set_runtime_session_routing_context,
    )

    set_runtime_session_routing_context(
        session_id,
        {"request_id": str(request_id), "request_context": {"source_channel": "dashboard"}},
    )
    token = set_current_runtime_session_id(session_id)
    try:
        result = await registered["conversation_reply"](
            conversation_id=str(conv_id),
            message="I don't know that yet.",
            gap={"entity_id": str(entity_id), "predicate": predicate},
        )
    finally:
        reset_current_runtime_session_id(token)
        clear_runtime_session_routing_context(session_id)
    return result, request_id


@pytest.fixture
def session_hook(pool_factory):  # noqa: F811
    """Register the real record-gap hook for BUTLER against whichever pool a test makes."""
    registered: list[memory_hooks.MemorySessionRuntime] = []

    def register(pool: asyncpg.Pool) -> None:
        async def record_gap(**gap):
            return await knowledge_gaps.record_gap_result(pool, **gap)

        registered.append(
            memory_hooks.register_memory_session_runtime(
                BUTLER, context=None, store_episode=None, record_gap=record_gap
            )
        )

    yield register
    for runtime in registered:
        memory_hooks.unregister_memory_session_runtime(BUTLER, runtime)


async def _store(pool, entity_id, predicate, content="Hana", sender=None):
    with _session(sender):
        return await storage.store_fact(
            pool, "Mei", predicate, content, _engine(), entity_id=entity_id, tenant_id="shared"
        )


async def _gap(pool, entity_id, predicate):
    return await pool.fetchrow(
        "SELECT * FROM knowledge_gaps WHERE entity_id = $1 AND predicate = $2"
        " ORDER BY asked_at DESC LIMIT 1",
        entity_id,
        predicate,
    )


def _poster(pool, sink: list | None = None, fail: bool = False):
    async def post(conversation_id: uuid.UUID, text: str) -> bool:
        if fail:
            raise ConnectionError("messenger down")
        message = await conversation_reply_create(pool, conversation_id, message=text)
        if sink is not None:
            sink.append((conversation_id, text))
        return message is not None

    return post


async def _notices(pool, conv_id) -> list[str]:
    rows = await pool.fetch(
        "SELECT content FROM public.dashboard_messages WHERE conversation_id = $1"
        " AND role = 'assistant' AND content LIKE 'You asked on%'",
        conv_id,
    )
    return [r["content"] for r in rows]


async def test_decline_records_one_open_gap_then_a_fact_write_answers_it_once(
    pool_factory, session_hook
):
    pool, owner, _ = await pool_factory()
    session_hook(pool)
    entity, predicate = await _entity(pool), await _predicate(pool)
    conv = await _conversation(pool)

    result, request_id = await _decline(pool, conv, entity, predicate)

    assert result["gap"]["status"] == "recorded"
    gap = await _gap(pool, entity, predicate)
    assert gap["status"] == "open"
    origin = await pool.fetchrow("SELECT * FROM knowledge_gap_origins WHERE gap_id = $1", gap["id"])
    assert (origin["conversation_id"], origin["request_id"], origin["channel"]) == (
        conv,
        request_id,
        "dashboard",
    )

    stored = await _store(pool, entity, predicate, sender=owner)
    gap = await _gap(pool, entity, predicate)
    assert gap["status"] == "answerable"
    assert gap["answered_by_ref"] == f"fact:{stored['id']}"
    assert gap["answered_authority"] == "owner"

    counts = await knowledge_gaps.deliver_knowledge_gaps(
        pool, post=_poster(pool), origin_butler=BUTLER
    )
    assert counts["delivered"] == 1
    [notice] = await _notices(pool, conv)
    assert "Now known: Hana" in notice and f"fact:{stored['id']}" in notice
    assert "What is Mei's daughter's name?" in notice

    again = await knowledge_gaps.deliver_knowledge_gaps(
        pool, post=_poster(pool), origin_butler=BUTLER
    )
    assert again["delivered"] == 0
    await _store(pool, entity, predicate, content="Hana again", sender=owner)
    assert await _notices(pool, conv) == [notice]
    assert (await _gap(pool, entity, predicate))["status"] == "delivered"


async def test_duplicate_declines_merge_and_each_thread_gets_one_notice(pool_factory, session_hook):
    pool, owner, _ = await pool_factory()
    session_hook(pool)
    entity, predicate = await _entity(pool), await _predicate(pool)
    first, second = await _conversation(pool), await _conversation(pool)

    await _decline(pool, first, entity, predicate)
    result, _ = await _decline(pool, second, entity, predicate)

    assert result["gap"]["merged"] is True
    assert (
        await pool.fetchval("SELECT count(*) FROM knowledge_gaps WHERE entity_id = $1", entity) == 1
    )
    await _store(pool, entity, predicate, sender=owner)
    await knowledge_gaps.deliver_knowledge_gaps(pool, post=_poster(pool), origin_butler=BUTLER)
    assert [len(await _notices(pool, c)) for c in (first, second)] == [1, 1]


async def test_a_fact_that_predates_the_gap_makes_it_born_answerable(pool_factory, session_hook):
    pool, owner, _ = await pool_factory()
    session_hook(pool)
    entity, predicate = await _entity(pool), await _predicate(pool)
    stored = await _store(pool, entity, predicate, sender=owner)

    result, _ = await _decline(pool, await _conversation(pool), entity, predicate)

    assert result["gap"]["gap_status"] == "answerable"
    assert (await _gap(pool, entity, predicate))["answered_by_ref"] == f"fact:{stored['id']}"


async def test_closure_commits_and_rolls_back_with_the_fact_write(pool_factory, session_hook):
    pool, owner, _ = await pool_factory()
    session_hook(pool)
    entity, predicate = await _entity(pool), await _predicate(pool)
    await _decline(pool, await _conversation(pool), entity, predicate)

    async with pool.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        await knowledge_gaps.close_matching_gaps(
            conn, entity_id=entity, predicate=predicate, ref="fact:x", value="v", authority=None
        )
        await tx.rollback()

    assert (await _gap(pool, entity, predicate))["status"] == "open"


async def test_a_third_party_fact_is_reported_not_established(pool_factory, session_hook):
    pool, owner, stranger = await pool_factory()
    session_hook(pool)
    entity, predicate = await _entity(pool), await _predicate(pool)
    conv = await _conversation(pool)
    await _decline(pool, conv, entity, predicate)

    await _store(pool, entity, predicate, content="Mallory says Hana", sender=stranger)
    await knowledge_gaps.deliver_knowledge_gaps(pool, post=_poster(pool), origin_butler=BUTLER)

    [notice] = await _notices(pool, conv)
    assert "Now known" not in notice
    assert "Reported by" in notice and "not verified" in notice


@pytest.mark.parametrize("terminal", ["expired", "dismissed"])
async def test_expired_and_dismissed_gaps_are_terminal(pool_factory, session_hook, terminal):
    pool, owner, _ = await pool_factory()
    session_hook(pool)
    entity, predicate = await _entity(pool), await _predicate(pool)
    conv = await _conversation(pool)
    await _decline(pool, conv, entity, predicate)
    gap = await _gap(pool, entity, predicate)

    if terminal == "expired":
        await pool.execute(
            "UPDATE knowledge_gaps SET expires_at = now() - interval '1 day' WHERE id = $1",
            gap["id"],
        )
        counts = await knowledge_gaps.deliver_knowledge_gaps(
            pool, post=_poster(pool), origin_butler=BUTLER
        )
        assert counts["expired"] >= 1
    else:
        assert await knowledge_gaps.dismiss_gap(pool, gap["id"]) == "dismissed"

    await _store(pool, entity, predicate, sender=owner)
    await knowledge_gaps.deliver_knowledge_gaps(pool, post=_poster(pool), origin_butler=BUTLER)
    assert (await _gap(pool, entity, predicate))["status"] == terminal
    assert await _notices(pool, conv) == []
    assert await knowledge_gaps.dismiss_gap(pool, gap["id"]) == terminal


async def test_delivery_failure_keeps_the_gap_retrying_then_surfaces_it(pool_factory, session_hook):
    pool, owner, _ = await pool_factory()
    session_hook(pool)
    entity, predicate = await _entity(pool), await _predicate(pool)
    conv = await _conversation(pool)
    await _decline(pool, conv, entity, predicate)
    await _store(pool, entity, predicate, sender=owner)

    now = datetime(2031, 1, 1, tzinfo=UTC)  # pinned: after every real-clock row timestamp
    for attempt in range(1, knowledge_gaps.MAX_DELIVERY_ATTEMPTS + 1):
        now += timedelta(days=1)
        await knowledge_gaps.deliver_knowledge_gaps(
            pool, post=_poster(pool, fail=True), origin_butler=BUTLER, now=now
        )
        gap = await _gap(pool, entity, predicate)
        assert (gap["status"], gap["delivery_attempts"]) == ("answerable", attempt)
        assert "messenger down" in gap["last_error"]

    [listed] = [
        g
        for g in await knowledge_gaps.list_gaps(pool, statuses=("answerable",))
        if g["entity_id"] == entity
    ]
    assert listed["delivery_failed"] is True
    assert (
        await pool.fetchval(
            "SELECT count(*) FROM public.attention_ledger WHERE dedup_key = $1 AND outcome = 'failed'",
            f"knowledge_gap:{gap['id']}",
        )
        == 1
    )
    assert await _notices(pool, conv) == []


async def test_invalid_entity_or_predicate_is_refused_without_blocking_the_reply(
    pool_factory, session_hook
):
    pool, owner, _ = await pool_factory()
    session_hook(pool)
    entity, predicate = await _entity(pool), await _predicate(pool)

    ghost, _ = await _decline(pool, await _conversation(pool), uuid.uuid4(), predicate)
    unregistered, _ = await _decline(pool, await _conversation(pool), entity, "no_such_predicate")

    assert ghost["status"] == unregistered["status"] == "ok"
    assert ghost["gap"]["status"] == unregistered["gap"]["status"] == "refused"
    assert (
        await pool.fetchval("SELECT count(*) FROM knowledge_gaps WHERE entity_id = $1", entity) == 0
    )


async def test_list_reports_open_gaps_as_unknown_and_answered_ones_as_present(
    pool_factory, session_hook
):
    pool, owner, _ = await pool_factory()
    session_hook(pool)
    entity, predicate = await _entity(pool), await _predicate(pool)
    await _decline(pool, await _conversation(pool), entity, predicate)

    [open_gap] = [g for g in await knowledge_gaps.list_gaps(pool) if g["entity_id"] == entity]
    assert (open_gap["status"], open_gap["coverage_state"]) == ("open", "unknown")

    await _store(pool, entity, predicate, sender=owner)
    [answered] = [g for g in await knowledge_gaps.list_gaps(pool) if g["entity_id"] == entity]
    assert (answered["status"], answered["coverage_state"]) == ("answerable", "present")


async def test_a_fact_write_racing_the_gap_insert_never_leaves_the_gap_open(
    pool_factory, session_hook
):
    pool, owner, _ = await pool_factory()
    session_hook(pool)
    predicate = await _predicate(pool)
    entities = [await _entity(pool) for _ in range(6)]
    convs = [await _conversation(pool) for _ in entities]

    await asyncio.gather(
        *(
            task
            for entity, conv in zip(entities, convs, strict=True)
            for task in (
                _decline(pool, conv, entity, predicate),
                _store(pool, entity, predicate, sender=owner),
            )
        )
    )

    statuses = {(await _gap(pool, entity, predicate))["status"] for entity in entities}
    assert statuses == {"answerable"}


async def test_a_closure_failure_never_fails_the_fact_write(
    pool_factory, session_hook, monkeypatch
):
    pool, owner, _ = await pool_factory()
    session_hook(pool)
    entity, predicate = await _entity(pool), await _predicate(pool)
    await _decline(pool, await _conversation(pool), entity, predicate)

    async def boom(*_a, **_kw):
        raise RuntimeError("lock service down")

    monkeypatch.setattr(knowledge_gaps, "_lock_pair", boom)
    before = knowledge_gaps.closure_failures

    stored = await _store(pool, entity, predicate, sender=owner)

    assert await pool.fetchval("SELECT validity FROM facts WHERE id = $1", stored["id"]) == "active"
    assert (await _gap(pool, entity, predicate))["status"] == "open"
    assert knowledge_gaps.closure_failures == before + 1


async def test_a_schema_without_the_gap_table_is_a_one_probe_no_op():
    from unittest.mock import AsyncMock, MagicMock

    conn = MagicMock()
    conn.fetchval = AsyncMock(return_value=False)

    await knowledge_gaps.close_matching_gaps(
        conn, entity_id=uuid.uuid4(), predicate="p", ref="fact:x", value="v", authority=None
    )

    conn.fetchval.assert_awaited_once()
    conn.transaction.assert_not_called()


async def _edge_predicates(pool, *, symmetric=False, temporal=False, inverse=True):
    """Register synthetic narrative edges, not the forbidden identity-edge vocabulary."""
    forward = f"kg_narrative_{uuid.uuid4().hex}"
    reverse = forward if symmetric else f"{forward}_reverse"
    names = [forward] if symmetric else [forward, reverse]
    for name in names:
        await pool.execute(
            "INSERT INTO predicate_registry"
            " (name, is_edge, is_temporal, inverse_of, is_symmetric, status)"
            " VALUES ($1, true, $2, $3, $4, 'active')",
            name,
            temporal,
            (reverse if name == forward else forward) if inverse and not symmetric else None,
            symmetric,
        )
    return forward, reverse


async def _registered_writer(pool):
    """Real MCP registration and writing tool; only embedding generation is unpaid."""
    from fastmcp import FastMCP

    from butlers.modules.memory import MemoryModule, MemoryModuleConfig

    module = MemoryModule()
    module._embedding_engine = _engine()
    config = MemoryModuleConfig(embedding_model="test-model", groups=["core"])
    mcp = FastMCP("knowledge-gap-regression")
    await module.register_tools(mcp, config, SimpleNamespace(pool=pool, schema="public"), BUTLER)
    return (await mcp.get_tool("memory_store_fact")).fn


async def _write_edge(writer, left, right, predicate, *, sender=None, **kwargs):
    with _session(sender):
        result = await writer(
            subject="Mei",
            content="Hana",
            predicate=predicate,
            entity_id=str(left),
            object_entity_id=str(right),
            **kwargs,
        )
    assert "error" not in result, result
    result["id"] = uuid.UUID(result["id"])
    return result


async def _inverse(pool, left, right, predicate):
    # This independently acquired read attests the actual committed inverse UUID.
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT * FROM facts WHERE entity_id = $1 AND object_entity_id = $2"
            " AND predicate = $3 AND validity = 'active' ORDER BY created_at DESC LIMIT 1",
            right,
            left,
            predicate,
        )


@pytest.mark.parametrize("symmetric", [False, True], ids=["inverse", "symmetric"])
async def test_generated_edge_answers_object_gap_with_its_own_committed_citation(
    pool_factory, session_hook, symmetric
):
    pool, owner, _ = await pool_factory()
    session_hook(pool)
    left, right = await _entity(pool), await _entity(pool)
    forward, reverse = await _edge_predicates(pool, symmetric=symmetric)
    left_conv, right_conv, other_origin = [await _conversation(pool) for _ in range(3)]
    await _decline(pool, left_conv, left, forward)
    await _decline(pool, right_conv, right, reverse)
    await _decline(pool, other_origin, right, reverse)
    writer = await _registered_writer(pool)

    stored = await _write_edge(writer, left, right, forward, sender=owner)

    inverse = await _inverse(pool, left, right, reverse)
    assert inverse is not None and inverse["id"] != stored["id"]
    assert (inverse["subject"], inverse["content"], inverse["validity"]) == (
        "Hana",
        "Mei",
        "active",
    )
    gap = await _gap(pool, right, reverse)
    assert gap["status"] == "answerable"  # Baseline defect: real inverse exists, gap is open.
    assert (gap["answered_by_ref"], gap["answered_value"]) == (f"fact:{inverse['id']}", "Mei")
    assert (gap["answered_authority"], gap["answered_authority_entity_id"]) == ("owner", owner)
    assert inverse["content_authority"] == "owner"
    assert inverse["authority_entity_id"] == owner
    direct = await _gap(pool, left, forward)
    assert direct["answered_by_ref"] == f"fact:{stored['id']}"
    async with pool.acquire() as conn:
        actual = await conn.fetchrow("SELECT * FROM facts WHERE id = $1", stored["id"])
        for column in (
            "tenant_id",
            "request_id",
            "source_butler",
            "source_episode_id",
            "retention_class",
            "sensitivity",
            "metadata",
            "content_authority",
            "authority_entity_id",
        ):
            assert inverse[column] == actual[column]
    [listed] = [g for g in await knowledge_gaps.list_gaps(pool) if g["id"] == gap["id"]]
    assert (listed["coverage_state"], listed["answered_by_ref"]) == (
        "present",
        f"fact:{inverse['id']}",
    )
    counts = await knowledge_gaps.deliver_knowledge_gaps(
        pool, post=_poster(pool), origin_butler=BUTLER
    )
    assert counts["delivered"] >= 2
    for conv in (right_conv, other_origin):
        [notice] = await _notices(pool, conv)
        assert "Now known: Mei" in notice and f"fact:{inverse['id']}" in notice
        assert f"fact:{stored['id']}" not in notice
    assert len(await _notices(pool, left_conv)) == 1
    await _write_edge(writer, left, right, forward, sender=owner)
    assert (await _gap(pool, right, reverse))["status"] == "delivered"
    assert (
        await knowledge_gaps.deliver_knowledge_gaps(pool, post=_poster(pool), origin_butler=BUTLER)
    )["delivered"] == 0
    assert [len(await _notices(pool, c)) for c in (left_conv, right_conv, other_origin)] == [
        1,
        1,
        1,
    ]


class _BorrowedPool:
    """Route the writer to an independently held real connection/outer transaction.

    No SQL result, transaction or lock is mocked. Pool-level reads still acquire a
    separate connection; catalog writes are disabled for rollback/ordering probes.
    """

    def __init__(self, pool, conn):
        self.pool, self.conn = pool, conn

    @asynccontextmanager
    async def acquire(self):
        yield self.conn

    def __getattr__(self, name):
        return getattr(self.pool, name)


async def _internal_edge(pool, left, right, predicate, *, sender=None, **kwargs):
    with _session(sender):
        return await storage.store_fact(
            pool,
            "Mei",
            predicate,
            "Hana",
            _engine(),
            entity_id=left,
            object_entity_id=right,
            **kwargs,
        )


async def _wait_for_advisory_wait(pool, pid):
    """Wait on PostgreSQL's actual lock waiter, never a scheduling sleep assumption."""
    async with asyncio.timeout(10):
        while not await pool.fetchval(
            "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE pid = $1"
            " AND locktype = 'advisory' AND NOT granted)",
            pid,
        ):
            await asyncio.sleep(0.01)


@pytest.mark.parametrize("first", ["capture", "writer"])
async def test_inverse_capture_serializes_in_both_database_lock_orders(
    pool_factory, session_hook, first
):
    pool, owner, _ = await pool_factory()
    session_hook(pool)
    left, right = await _entity(pool), await _entity(pool)
    forward, reverse = await _edge_predicates(pool)
    conv = await _conversation(pool)
    async with pool.acquire() as holder, pool.acquire() as waiter:
        tx = holder.transaction()
        await tx.start()
        pid = await waiter.fetchval("SELECT pg_backend_pid()")
        try:
            if first == "capture":
                # Actual record_gap holds its real pair key until outer commit.
                await knowledge_gaps.record_gap(
                    _BorrowedPool(pool, holder),
                    entity_id=right,
                    predicate=reverse,
                    conversation_id=conv,
                    request_id=uuid.uuid4(),
                    channel="dashboard",
                )
                pending = asyncio.create_task(
                    _internal_edge(_BorrowedPool(pool, waiter), left, right, forward, sender=owner)
                )
            else:
                stored = await _internal_edge(
                    _BorrowedPool(pool, holder), left, right, forward, sender=owner
                )
                pending = asyncio.create_task(
                    knowledge_gaps.record_gap(
                        _BorrowedPool(pool, waiter),
                        entity_id=right,
                        predicate=reverse,
                        conversation_id=conv,
                        request_id=uuid.uuid4(),
                        channel="dashboard",
                    )
                )
            await _wait_for_advisory_wait(pool, pid)
            assert not pending.done()
            await tx.commit()
            result = await asyncio.wait_for(pending, 10)
            if first == "capture":
                stored = result
        finally:
            if holder.is_in_transaction():
                await tx.rollback()
            if "pending" in locals() and not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
    inverse = await _inverse(pool, left, right, reverse)
    assert inverse is not None and inverse["id"] != stored["id"]
    gap = await _gap(pool, right, reverse)
    assert (gap["status"], gap["answered_by_ref"]) == ("answerable", f"fact:{inverse['id']}")


@pytest.mark.parametrize("first_direction", ["forward", "reverse"])
@pytest.mark.parametrize("symmetric", [False, True], ids=["inverse", "symmetric"])
async def test_reciprocal_writers_reserve_database_hashes_before_existing_fact_rows(
    pool_factory, session_hook, symmetric, first_direction
):
    pool, owner, _ = await pool_factory()
    session_hook(pool)
    left, right = await _entity(pool), await _entity(pool)
    forward, reverse = await _edge_predicates(pool, symmetric=symmetric)
    initial = await _internal_edge(pool, left, right, forward, sender=owner)
    initial_inverse = await _inverse(pool, left, right, reverse)
    await _decline(pool, await _conversation(pool), left, forward)
    await _decline(pool, await _conversation(pool), right, reverse)
    # Seed the genuine stranded-gap recovery condition; existing property rows
    # ensure acquiring reservation after their row locks would deadlock.
    await pool.execute(
        "UPDATE knowledge_gaps SET status = 'open', answered_by_ref = NULL,"
        " answered_at = NULL WHERE entity_id = ANY($1::uuid[])",
        [left, right],
    )
    reserved = []

    class Connection:
        def __init__(self, conn):
            self.conn = conn

        def __getattr__(self, name):
            return getattr(self.conn, name)

        async def execute(self, sql, *args):
            if sql == "SELECT pg_advisory_xact_lock($1::bigint)":
                reserved.append(args[0])
            return await self.conn.execute(sql, *args)

    async with pool.acquire() as first, pool.acquire() as second:
        tx = first.transaction()
        await tx.start()
        pending = None
        try:
            first_pair, second_pair = ((left, right, forward), (right, left, reverse))
            if first_direction == "reverse":
                first_pair, second_pair = second_pair, first_pair
            first_result = await _internal_edge(
                _BorrowedPool(pool, Connection(first)), *first_pair, sender=owner
            )
            pid = await second.fetchval("SELECT pg_backend_pid()")
            pending = asyncio.create_task(
                _internal_edge(_BorrowedPool(pool, second), *second_pair, sender=owner)
            )
            await _wait_for_advisory_wait(pool, pid)
            # Real signed hash keys are held by the first transaction, not
            # Python hashes or text-sorted keys. The waiter has no fact tuple lock.
            expected = await first.fetch(
                "SELECT DISTINCT hashtextextended(current_schema() || ':' || key, 0) AS id"
                " FROM unnest($1::text[]) AS k(key) ORDER BY id",
                [knowledge_gaps._lock_key(left, forward), knowledge_gaps._lock_key(right, reverse)],
            )
            assert reserved == [r["id"] for r in expected]
            reserved.clear()
            assert await knowledge_gaps.reserve_gap_pairs(
                Connection(first), [(left, forward), (right, reverse), (left, forward)]
            )
            assert reserved == [r["id"] for r in expected]  # actual duplicate-key deduplication
            held = await first.fetch(
                "SELECT (classid::bigint << 32) | objid::bigint AS id FROM pg_locks"
                " WHERE pid = pg_backend_pid() AND locktype = 'advisory' AND granted",
            )
            assert {r["id"] for r in expected} <= {r["id"] for r in held}
            assert not await first.fetchval(
                "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE pid = $1 AND locktype = 'tuple')",
                pid,
            )
            await tx.commit()
            second_result = await asyncio.wait_for(pending, 10)
        finally:
            if first.is_in_transaction():
                await tx.rollback()
            if pending is not None and not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
    assert first_result["supersedes_id"] == (
        initial["id"] if first_direction == "forward" else initial_inverse["id"]
    )
    assert second_result["supersedes_id"] is not None
    for entity, predicate in ((left, forward), (right, reverse)):
        assert (await _gap(pool, entity, predicate))["status"] == "answerable"
    assert (
        await pool.fetchval(
            "SELECT count(*) FROM facts WHERE id = ANY($1::uuid[]) AND validity = 'superseded'",
            [initial["id"], initial_inverse["id"]],
        )
        == 2
    )
    assert (
        await pool.fetchval(
            "SELECT count(*) FROM facts WHERE entity_id = ANY($1::uuid[])"
            " AND predicate = ANY($2::text[]) AND validity = 'active'",
            [left, right],
            [forward, reverse],
        )
        == 2
    )


@pytest.mark.parametrize("failure", ["sql", "cancel", "inverse-insert", "inverse-projection"])
async def test_connected_outer_failure_or_cancel_rolls_back_both_facts_and_answers(
    pool_factory, session_hook, failure
):
    pool, owner, _ = await pool_factory()
    session_hook(pool)
    left, right = await _entity(pool), await _entity(pool)
    forward, reverse = await _edge_predicates(pool)
    for entity, predicate in ((left, forward), (right, reverse)):
        await _decline(pool, await _conversation(pool), entity, predicate)
    inserted = asyncio.Event()
    release = asyncio.Event()

    class Connection:
        def __init__(self, conn):
            self.conn = conn

        def __getattr__(self, name):
            return getattr(self.conn, name)

        async def execute(self, sql, *args):
            if (
                failure == "inverse-insert"
                and "INSERT INTO facts" in sql
                and args[2] == reverse
                or failure == "inverse-projection"
                and "INSERT INTO public.entity_graph_edges" in sql
                and args[4] == reverse
            ):
                await self.conn.execute("SELECT 1 / 0")
            return await self.conn.execute(sql, *args)

    async def write_then_fail():
        async with pool.acquire() as conn, conn.transaction():
            await _internal_edge(
                _BorrowedPool(pool, Connection(conn)), left, right, forward, sender=owner
            )
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM knowledge_gaps WHERE entity_id = ANY($1::uuid[])"
                    " AND status = 'answerable'",
                    [left, right],
                )
                == 2
            )
            inserted.set()
            if failure == "sql":
                await conn.execute("SELECT 1 / 0")  # genuine failed outer transaction
            else:
                await release.wait()

    pending = asyncio.create_task(write_then_fail())
    if failure in ("sql", "cancel"):
        await asyncio.wait_for(inserted.wait(), 10)
    if failure == "cancel":
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
    else:
        with pytest.raises(asyncpg.DivisionByZeroError):
            await pending
    async with pool.acquire() as reader:
        assert (
            await reader.fetchval(
                "SELECT count(*) FROM facts WHERE entity_id = ANY($1::uuid[])"
                " AND predicate = ANY($2::text[])",
                [left, right],
                [forward, reverse],
            )
            == 0
        )
        assert (
            await reader.fetchval(
                "SELECT count(*) FROM knowledge_gaps WHERE entity_id = ANY($1::uuid[])"
                " AND status = 'open'",
                [left, right],
            )
            == 2
        )
        assert (
            await reader.fetchval(
                "SELECT count(*) FROM public.entity_graph_edges WHERE subject_entity_id = ANY($1::uuid[])",
                [left, right],
            )
            == 0
        )


@pytest.mark.parametrize("failure", ["reservation", "direct", "inverse"])
async def test_real_sql_bookkeeping_failure_recovers_its_savepoint_and_commits_facts(
    pool_factory, session_hook, failure
):
    pool, owner, _ = await pool_factory()
    session_hook(pool)
    left, right = await _entity(pool), await _entity(pool)
    forward, reverse = await _edge_predicates(pool)
    for entity, predicate in ((left, forward), (right, reverse)):
        await _decline(pool, await _conversation(pool), entity, predicate)
    before = knowledge_gaps.closure_failures

    # Transparent connection observer: the real SQL error is issued by PostgreSQL
    # inside the production savepoint; no lock, closure, or database result is mocked.
    class Connection:
        def __init__(self, conn):
            self.conn = conn

        def __getattr__(self, name):
            return getattr(self.conn, name)

        async def fetch(self, sql, *args):
            if failure == "reservation" and "AS lock_id" in sql:
                await self.conn.execute("SELECT 1 / 0")
            return await self.conn.fetch(sql, *args)

        async def execute(self, sql, *args):
            target = left if failure == "direct" else right
            if "UPDATE knowledge_gaps" in sql and args[0] == target:
                await self.conn.execute("SELECT 1 / 0")
            return await self.conn.execute(sql, *args)

    async with pool.acquire() as conn:
        stored = await _internal_edge(
            _BorrowedPool(pool, Connection(conn)), left, right, forward, sender=owner
        )
    inverse = await _inverse(pool, left, right, reverse)
    assert inverse is not None
    assert await pool.fetchval("SELECT validity FROM facts WHERE id = $1", stored["id"]) == "active"
    assert (
        await pool.fetchval(
            "SELECT count(*) FROM public.entity_graph_edges WHERE source_id = ANY($1::uuid[])",
            [stored["id"], inverse["id"]],
        )
        == 2
    )
    expected = (
        ("open", "open")
        if failure == "reservation"
        else (("open", "answerable") if failure == "direct" else ("answerable", "open"))
    )
    assert (
        tuple([(await _gap(pool, e, p))["status"] for e, p in ((left, forward), (right, reverse))])
        == expected
    )
    assert knowledge_gaps.closure_failures == before + 1
    [listed] = [
        g
        for g in await knowledge_gaps.list_gaps(pool)
        if g["entity_id"] == (left if failure != "inverse" else right)
    ]
    assert listed["coverage_state"] == "unknown"


async def test_inverse_authority_is_server_stamped_and_legacy_capture_fails_closed(
    pool_factory, session_hook
):
    from butlers.core.tool_call_capture import (
        clear_runtime_session_routing_context,
        reset_current_runtime_session_id,
        set_current_runtime_session_id,
        set_runtime_session_routing_context,
    )

    pool, owner, stranger = await pool_factory()
    session_hook(pool)
    writer = await _registered_writer(pool)
    # Each class has real rows, readers and dashboard notice assertions. Neither
    # fact anchor is the author; anchoring never confers owner authority.
    for authority in ("owner", "owner_device", "system", "third_party", "mixed", "legacy"):
        left, right = await _entity(pool), await _entity(pool)
        forward, reverse = await _edge_predicates(pool)
        conv = await _conversation(pool)
        if authority != "legacy":
            await _decline(pool, conv, right, reverse)
        session = str(uuid.uuid4())
        context = (
            {"request_context": {"source_channel": "dashboard"}}
            if authority == "owner_device"
            else _routed(owner if authority == "owner" else stranger)
        )
        if authority != "system":
            set_runtime_session_routing_context(session, context)
        token = set_current_runtime_session_id(session)
        try:
            if authority == "mixed":
                # Trusted derived-storage path, never a caller-asserted MCP stamp.
                stored = await storage.store_fact(
                    pool,
                    "Mei",
                    forward,
                    "Hana",
                    _engine(),
                    entity_id=left,
                    object_entity_id=right,
                    content_authority="mixed",
                )
            else:
                stored = await writer(
                    subject="Mei",
                    content="Hana",
                    predicate=forward,
                    entity_id=str(left),
                    object_entity_id=str(right),
                )
                assert "error" not in stored, stored
        finally:
            reset_current_runtime_session_id(token)
            clear_runtime_session_routing_context(session)
        inverse = await _inverse(pool, left, right, reverse)
        assert inverse is not None
        expected_authority = None if authority == "legacy" else authority
        expected_sender = (
            owner
            if authority in ("owner", "owner_device")
            else (stranger if authority == "third_party" else None)
        )
        if authority == "legacy":
            await pool.execute(
                "UPDATE facts SET content_authority = NULL, authority_entity_id = NULL WHERE id = $1",
                inverse["id"],
            )
            result, _ = await _decline(pool, conv, right, reverse)
            assert result["gap"]["gap_status"] == "answerable"
        else:
            assert (inverse["content_authority"], inverse["authority_entity_id"]) == (
                expected_authority,
                expected_sender,
            )
        gap = await _gap(pool, right, reverse)
        assert (gap["answered_authority"], gap["answered_authority_entity_id"]) == (
            expected_authority,
            expected_sender,
        )
        assert gap["answered_by_ref"] == f"fact:{inverse['id']}"
        await knowledge_gaps.deliver_knowledge_gaps(pool, post=_poster(pool), origin_butler=BUTLER)
        [notice] = await _notices(pool, conv)
        if authority in ("owner", "owner_device", "system"):
            assert "Now known: Mei" in notice
        else:
            assert (
                "Now known" not in notice and "Reported by" in notice and "not verified" in notice
            )
        assert f"fact:{inverse['id']}" in notice and f"fact:{stored['id']}" not in notice


@pytest.mark.parametrize("retained", ["active", "fading", "superseded", "retracted"])
async def test_temporal_inverse_replay_never_fabricates_an_answer_or_reactivates_a_row(
    pool_factory, session_hook, retained
):
    pool, owner, _ = await pool_factory()
    session_hook(pool)
    left, right = await _entity(pool), await _entity(pool)
    forward, reverse = await _edge_predicates(pool, temporal=True)
    at = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)
    first = await _internal_edge(
        pool, left, right, forward, sender=owner, valid_at=at, idempotency_key=f"{forward}:first"
    )
    inverse = await _inverse(pool, left, right, reverse)
    await pool.execute("UPDATE facts SET validity = $2 WHERE id = $1", inverse["id"], retained)
    result, _ = await _decline(pool, await _conversation(pool), right, reverse)
    assert result["gap"]["gap_status"] == ("answerable" if retained == "active" else "open")
    gap = await _gap(pool, right, reverse)
    # A genuine preexisting failed closure must not be healed by a skipped insert.
    if retained == "active":
        assert gap["answered_by_ref"] == f"fact:{inverse['id']}"
        await pool.execute(
            "UPDATE knowledge_gaps SET status = 'open', answered_by_ref = NULL,"
            " answered_at = NULL WHERE id = $1",
            gap["id"],
        )
    replay = await _internal_edge(
        pool, left, right, forward, sender=owner, valid_at=at, idempotency_key=f"{forward}:first"
    )
    assert replay == {"id": first["id"], "supersedes_id": None}
    # A distinct forward key with the same generated inverse key also skips closure.
    second = await _internal_edge(
        pool, left, right, forward, sender=owner, valid_at=at, idempotency_key=f"{forward}:second"
    )
    assert second["id"] != first["id"]
    async with pool.acquire() as reader:
        old = await reader.fetchrow("SELECT * FROM facts WHERE id = $1", inverse["id"])
        assert old["validity"] == retained
        assert (old["content"], old["authority_entity_id"], old["valid_at"]) == ("Mei", owner, at)
        assert (
            await reader.fetchval(
                "SELECT count(*) FROM facts WHERE entity_id = $1 AND predicate = $2", right, reverse
            )
            == 1
        )
    assert (await _gap(pool, right, reverse))["status"] == "open"
    assert (await _gap(pool, right, reverse))["answered_by_ref"] is None
    # A new event really inserts a new active inverse and can answer the gap.
    await _internal_edge(pool, left, right, forward, sender=owner, valid_at=at + timedelta(days=1))
    newest = await _inverse(pool, left, right, reverse)
    assert newest["id"] != inverse["id"]
    assert (await _gap(pool, right, reverse))["answered_by_ref"] == f"fact:{newest['id']}"
    await _internal_edge(pool, left, right, forward, sender=owner)  # property coexists with events
    assert (await _gap(pool, right, reverse))["answered_by_ref"] == f"fact:{newest['id']}"
    assert await pool.fetchval(
        "SELECT count(*) FROM facts WHERE entity_id = $1 AND predicate = $2 AND validity='active'",
        right,
        reverse,
    ) == (3 if retained == "active" else 2)


async def test_inverse_alias_property_rewrite_and_noninverse_admission_controls(
    pool_factory, session_hook
):
    pool, owner, _ = await pool_factory()
    session_hook(pool)
    left, right = await _entity(pool), await _entity(pool)
    forward, reverse = await _edge_predicates(pool)
    alias = f"kg_alias_{uuid.uuid4().hex}"
    await pool.execute(
        "UPDATE predicate_registry SET aliases = ARRAY[$2] WHERE name = $1", forward, alias
    )
    first = await _internal_edge(pool, left, right, forward, sender=owner)
    old = await _inverse(pool, left, right, reverse)
    await _decline(pool, await _conversation(pool), right, reverse)
    gap = await _gap(pool, right, reverse)
    assert gap["answered_by_ref"] == f"fact:{old['id']}"  # capture after actual inverse
    await pool.execute("UPDATE knowledge_gaps SET status = 'open' WHERE id = $1", gap["id"])
    stored = await _write_edge(await _registered_writer(pool), left, right, alias, sender=owner)
    inverse = await _inverse(pool, left, right, reverse)
    assert stored["superseded_id"] == str(first["id"])
    assert inverse["supersedes_id"] == old["id"]
    assert (await _gap(pool, right, reverse))["answered_by_ref"] == f"fact:{inverse['id']}"
    assert (
        await pool.fetchval(
            "SELECT count(*) FROM memory_links WHERE target_id = ANY($1::uuid[]) AND relation='supersedes'",
            [first["id"], old["id"]],
        )
        == 2
    )
    assert (
        await pool.fetchval(
            "SELECT count(*) FROM public.entity_graph_edges WHERE source_id = ANY($1::uuid[])",
            [first["id"], old["id"]],
        )
        == 0
    )
    # Snapshot retention is inherited: later retraction does not reopen/relabel it.
    await storage.forget_memory(pool, "fact", inverse["id"])
    assert (await _gap(pool, right, reverse))["answered_by_ref"] == f"fact:{inverse['id']}"
    bare, unrelated = await _edge_predicates(pool, inverse=False)
    await _decline(pool, await _conversation(pool), right, unrelated)
    await _internal_edge(pool, left, right, bare, sender=owner)
    assert await _inverse(pool, left, right, unrelated) is None
    assert (await _gap(pool, right, unrelated))["status"] == "open"
    novel = f"kg_unregistered_{uuid.uuid4().hex}"
    await _internal_edge(pool, left, right, novel, sender=owner)
    assert (
        await pool.fetchval("SELECT status FROM predicate_registry WHERE name=$1", novel)
        == "proposed"
    )
    assert (
        await pool.fetchval(
            "SELECT count(*) FROM facts WHERE entity_id=$1 AND object_entity_id=$2 AND predicate=$3",
            right,
            left,
            novel,
        )
        == 0
    )
    unary = await _predicate(pool)
    await _decline(pool, await _conversation(pool), right, unary)
    await _store(pool, left, unary, sender=owner)
    await storage.store_fact(pool, "Unanchored", unary, "Standalone", _engine())
    assert (await _gap(pool, right, unary))["status"] == "open"
    for subject_id, object_id in ((left, uuid.uuid4()), (left, left), (None, right)):
        with pytest.raises(ValueError):
            await storage.store_fact(
                pool,
                "Mei",
                forward,
                "Hana",
                _engine(),
                entity_id=subject_id,
                object_entity_id=object_id,
            )
    await pool.execute(
        "UPDATE public.entities SET metadata = jsonb_build_object('merged_into', $2::text) WHERE id = $1",
        right,
        str(left),
    )
    declined, _ = await _decline(pool, await _conversation(pool), right, reverse)
    assert declined["gap"]["status"] == "refused"


@pytest.mark.parametrize("terminal", ["dismissed", "expired"])
async def test_inverse_terminal_transition_wins_while_writer_waits_on_the_gap(
    pool_factory, session_hook, terminal
):
    pool, owner, _ = await pool_factory()
    session_hook(pool)
    left, right = await _entity(pool), await _entity(pool)
    forward, reverse = await _edge_predicates(pool)
    conv = await _conversation(pool)
    await _decline(pool, conv, right, reverse)
    gap = await _gap(pool, right, reverse)
    async with pool.acquire() as first, pool.acquire() as second:
        tx = first.transaction()
        await tx.start()
        pending = None
        try:
            await knowledge_gaps._lock_pair(first, right, reverse)
            await first.execute(
                "UPDATE knowledge_gaps SET status = $2 WHERE id = $1", gap["id"], terminal
            )
            pid = await second.fetchval("SELECT pg_backend_pid()")
            pending = asyncio.create_task(
                _internal_edge(_BorrowedPool(pool, second), left, right, forward, sender=owner)
            )
            await _wait_for_advisory_wait(pool, pid)
            await tx.commit()
            await asyncio.wait_for(pending, 10)
        finally:
            if first.is_in_transaction():
                await tx.rollback()
            if pending is not None and not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
    assert (await _gap(pool, right, reverse))["status"] == terminal
    await knowledge_gaps.deliver_knowledge_gaps(pool, post=_poster(pool), origin_butler=BUTLER)
    assert await _notices(pool, conv) == []


async def test_inverse_delivery_crash_repeats_only_at_the_adopted_post_commit_boundary(
    pool_factory, session_hook
):
    pool, owner, _ = await pool_factory()
    session_hook(pool)
    left, right = await _entity(pool), await _entity(pool)
    forward, reverse = await _edge_predicates(pool)
    conv = await _conversation(pool)
    await _decline(pool, conv, right, reverse)
    await _internal_edge(pool, left, right, forward, sender=owner)

    async def post_then_crash(conversation_id, text):
        assert await _poster(pool)(conversation_id, text)
        raise asyncio.CancelledError("crash after independently committed dashboard post")

    with pytest.raises(asyncio.CancelledError):
        await knowledge_gaps.deliver_knowledge_gaps(
            pool, post=post_then_crash, origin_butler=BUTLER
        )
    assert (await _gap(pool, right, reverse))["status"] == "answerable"
    assert len(await _notices(pool, conv)) == 1
    await knowledge_gaps.deliver_knowledge_gaps(pool, post=_poster(pool), origin_butler=BUTLER)
    assert (await _gap(pool, right, reverse))["status"] == "delivered"
    assert len(await _notices(pool, conv)) == 2
    await knowledge_gaps.deliver_knowledge_gaps(pool, post=_poster(pool), origin_butler=BUTLER)
    assert len(await _notices(pool, conv)) == 2
    # A separate, actually deleted origin uses the real sink's False outcome;
    # no endless retry and no fabricated replacement conversation.
    other = await _entity(pool)
    missing_conv = await _conversation(pool)
    await _decline(pool, missing_conv, other, reverse)
    await _internal_edge(pool, left, other, forward, sender=owner)
    await pool.execute("DELETE FROM public.dashboard_conversations WHERE id=$1", missing_conv)
    await knowledge_gaps.deliver_knowledge_gaps(pool, post=_poster(pool), origin_butler=BUTLER)
    assert (await _gap(pool, other, reverse))["status"] == "delivered"
    assert await _notices(pool, missing_conv) == []


@pytest.fixture(scope="module")
def private_gap_db(postgres_container):
    """Actual bootstrap/runtime roles and both private, fully migrated Memory schemas."""
    from butlers.migrations import run_migrations
    from butlers.testing.migration import create_migrated_test_db, migration_db_name

    db = create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "memory"],
        schemas={"memory": "general"},
    )
    asyncio.run(run_migrations(db, chain="memory", schema="health"))
    return db


async def test_inverse_closure_uses_only_the_runtime_roles_own_private_memory_schema(
    private_gap_db,
):
    from butlers.db import register_jsonb_codec

    async def general(conn):
        await conn.execute("SET ROLE butler_general_rw")
        await conn.execute("SET search_path TO general, public")

    async def health(conn):
        await conn.execute("SET ROLE butler_health_rw")
        await conn.execute("SET search_path TO health, public")

    own = await asyncpg.create_pool(
        private_gap_db, min_size=1, max_size=3, init=register_jsonb_codec, setup=general
    )
    peer = await asyncpg.create_pool(
        private_gap_db, min_size=1, max_size=3, init=register_jsonb_codec, setup=health
    )
    try:
        left, right = await _entity(own), await _entity(own)
        forward, reverse = await _edge_predicates(own)
        await peer.execute(
            "INSERT INTO predicate_registry (name, is_edge, inverse_of, status)"
            " VALUES ($1, true, $2, 'active'), ($2, true, $1, 'active')",
            forward,
            reverse,
        )
        conv = await _conversation(own)
        for pool in (own, peer):
            await knowledge_gaps.record_gap(
                pool,
                entity_id=right,
                predicate=reverse,
                conversation_id=conv,
                request_id=uuid.uuid4(),
                channel="dashboard",
            )
        own_gap, sentinel = await _gap(own, right, reverse), await _gap(peer, right, reverse)
        async with own.acquire() as conn:
            assert await conn.fetchval("SELECT current_user") == "butler_general_rw"
            assert await conn.fetchval("SELECT current_schema()") == "general"
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with conn.transaction():
                    await conn.fetchval("SELECT count(*) FROM health.knowledge_gaps")
        request = str(uuid.uuid4())
        stored = await _internal_edge(
            own,
            left,
            right,
            forward,
            tenant_id="synthetic-tenant",
            request_id=request,
            metadata={"synthetic": "source-kept"},
            sensitivity="confidential",
            retention_class="archive",
            source_butler=BUTLER,
        )
        inverse = await _inverse(own, left, right, reverse)
        assert (await _gap(own, right, reverse))["answered_by_ref"] == f"fact:{inverse['id']}"
        assert (await _gap(peer, right, reverse)) == sentinel
        assert inverse["metadata"] == {"synthetic": "source-kept"}
        source = await own.fetchrow("SELECT * FROM facts WHERE id=$1", stored["id"])
        assert inverse["source_episode_id"] is not None
        assert inverse["source_episode_id"] == source["source_episode_id"]
        assert (
            inverse["tenant_id"],
            inverse["request_id"],
            inverse["source_butler"],
            inverse["sensitivity"],
            inverse["retention_class"],
        ) == ("synthetic-tenant", request, BUTLER, "confidential", "archive")
        projections = await own.fetch(
            "SELECT * FROM public.entity_graph_edges WHERE source_id = ANY($1::uuid[])",
            [stored["id"], inverse["id"]],
        )
        assert len(projections) == 2
        assert all(
            p["predicate"] is None
            and p["object_entity_id"] is None
            and p["withheld_reason"] == "sensitivity"
            for p in projections
        )
        # Missing-local-table control uses only this disposable own-schema fixture.
        # The normal migration login owns these fixture tables; runtime roles only
        # perform their authorized DML. No privilege/grant or peer access is added.
        fixture_conn = await asyncpg.connect(private_gap_db)
        try:
            await fixture_conn.execute(
                "ALTER TABLE general.knowledge_gaps RENAME TO knowledge_gaps_offline"
            )
            try:
                second = await _internal_edge(own, left, right, forward, tenant_id="other-tenant")
                assert (
                    await own.fetchval("SELECT validity FROM facts WHERE id=$1", second["id"])
                    == "active"
                )
                assert await _inverse(own, left, right, reverse) is not None
            finally:
                await fixture_conn.execute(
                    "ALTER TABLE general.knowledge_gaps_offline RENAME TO knowledge_gaps"
                )
        finally:
            await fixture_conn.close()
        assert (await _gap(peer, right, reverse)) == sentinel
        assert (await _gap(own, right, reverse))["id"] == own_gap["id"]
    finally:
        await own.close()
        await peer.close()
