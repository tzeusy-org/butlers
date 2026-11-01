# ruff: noqa: F811
"""Inverse-edge closure of owner knowledge gaps against real Postgres (bu-q7vx1q.74).

A generated inverse (or symmetric) edge must answer the object-side gap with its own
committed citation, in one transaction with the fact write, under a lock order that two
reciprocal writers cannot deadlock on. Every test runs at a real seam: the registered
writer, ``storage.store_fact`` and the delivery job. Shared provisioning and the small
gap helpers come from the focused gap tests so each is defined in exactly one place.
"""

from __future__ import annotations

import asyncio
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import asyncpg
import pytest

from butlers.modules.memory import knowledge_gaps, storage
from butlers.testing.fake_pool import AsyncCtx
from tests.modules.memory.test_content_authority import (  # noqa: F401
    _engine,
    _routed,
    _session,
    pool_factory,
)
from tests.modules.memory.test_knowledge_gaps import (  # noqa: F401
    BUTLER,
    _conversation,
    _decline,
    _entity,
    _gap,
    _notices,
    _poster,
    _predicate,
    _store,
    session_hook,
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

    def acquire(self):
        return AsyncCtx(self.conn)

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

        async def _condition_met() -> bool:
            return await pool.fetchval(
                "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE pid = $1"
                " AND locktype = 'advisory' AND NOT granted)",
                pid,
            )

        await poll_until(_condition_met, interval_s=0.01)


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
                    question_summary="What narrative connection does Hana have with Mei?",
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
                        question_summary="What narrative connection does Hana have with Mei?",
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
    # This predicate permits properties as well as timestamped events. A
    # temporal-only registry entry would correctly reject the property control.
    forward, reverse = await _edge_predicates(pool)
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
                question_summary="What narrative connection does Hana have with Mei?",
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
        # A fresh pair avoids the existing tenant-free live property uniqueness
        # key; other-tenant remains metadata, not a new coexistence guarantee.
        fresh_left, fresh_right = await _entity(own), await _entity(own)
        original_answered_gap = await _gap(own, right, reverse)
        for pool, entity, predicate in (
            (own, fresh_left, forward),
            (own, fresh_right, reverse),
            (peer, fresh_right, reverse),
        ):
            await knowledge_gaps.record_gap(
                pool,
                entity_id=entity,
                predicate=predicate,
                question_summary="What narrative connection does Hana have with Mei?",
                conversation_id=conv,
                request_id=uuid.uuid4(),
                channel="dashboard",
            )
        fresh_direct_gap = await _gap(own, fresh_left, forward)
        fresh_inverse_gap = await _gap(own, fresh_right, reverse)
        fresh_sentinel = await _gap(peer, fresh_right, reverse)
        assert all(
            gap["status"] == "open" and gap["answered_by_ref"] is None
            for gap in (fresh_direct_gap, fresh_inverse_gap, fresh_sentinel)
        )
        assert (
            await own.fetchval(
                "SELECT count(*) FROM facts WHERE entity_id = ANY($1::uuid[])",
                [fresh_left, fresh_right],
            )
            == 0
        )
        second_request = str(uuid.uuid4())
        second_metadata = {"synthetic": "missing-table-source-kept"}
        # Missing-local-table control uses only this disposable own-schema fixture.
        # The normal migration login owns these fixture tables; runtime roles only
        # perform their authorized DML. No privilege/grant or peer access is added.
        fixture_conn = await asyncpg.connect(private_gap_db)
        try:
            await fixture_conn.execute(
                "ALTER TABLE general.knowledge_gaps RENAME TO knowledge_gaps_offline"
            )
            try:
                second = await _internal_edge(
                    own,
                    fresh_left,
                    fresh_right,
                    forward,
                    tenant_id="other-tenant",
                    request_id=second_request,
                    metadata=second_metadata,
                    source_butler=BUTLER,
                    retention_class="archive",
                )
                assert (
                    await own.fetchval("SELECT validity FROM facts WHERE id=$1", second["id"])
                    == "active"
                )
                assert await _inverse(own, left, right, reverse) is not None
                fresh_inverse = await _inverse(own, fresh_left, fresh_right, reverse)
                assert fresh_inverse is not None
                assert len({stored["id"], inverse["id"], second["id"], fresh_inverse["id"]}) == 4
                # New acquired reads identify the actual committed rows, not the
                # previous subcontrol's inverse or an already answered gap.
                async with own.acquire() as conn:
                    second_source = await conn.fetchrow(
                        "SELECT * FROM facts WHERE id=$1", second["id"]
                    )
                    second_inverse = await conn.fetchrow(
                        "SELECT * FROM facts WHERE id=$1", fresh_inverse["id"]
                    )
                    second_projections = await conn.fetch(
                        "SELECT * FROM public.entity_graph_edges WHERE source_id = ANY($1::uuid[])",
                        [second["id"], fresh_inverse["id"]],
                    )
                assert second_source["id"] == second["id"]
                assert second_inverse["id"] == fresh_inverse["id"]
                assert (
                    second_source["entity_id"],
                    second_source["object_entity_id"],
                    second_source["predicate"],
                    second_source["subject"],
                    second_source["content"],
                    second_source["validity"],
                ) == (fresh_left, fresh_right, forward, "Mei", "Hana", "active")
                assert (
                    second_inverse["entity_id"],
                    second_inverse["object_entity_id"],
                    second_inverse["predicate"],
                    second_inverse["subject"],
                    second_inverse["content"],
                    second_inverse["validity"],
                ) == (fresh_right, fresh_left, reverse, "Hana", "Mei", "active")
                assert (
                    second_source["tenant_id"],
                    second_source["request_id"],
                    second_source["metadata"],
                    second_source["source_butler"],
                    second_source["retention_class"],
                ) == ("other-tenant", second_request, second_metadata, BUTLER, "archive")
                assert second_source["source_episode_id"] is not None
                for column in (
                    "tenant_id",
                    "request_id",
                    "metadata",
                    "source_butler",
                    "source_episode_id",
                    "retention_class",
                    "sensitivity",
                    "content_authority",
                    "authority_entity_id",
                ):
                    assert second_inverse[column] == second_source[column]
                assert {p["source_id"] for p in second_projections} == {
                    second["id"],
                    fresh_inverse["id"],
                }
                assert len(second_projections) == 2
                for projection in second_projections:
                    expected = (
                        (fresh_left, forward, fresh_right)
                        if projection["source_id"] == second["id"]
                        else (fresh_right, reverse, fresh_left)
                    )
                    assert (
                        projection["source_schema"],
                        projection["source_table"],
                        projection["subject_entity_id"],
                        projection["predicate"],
                        projection["object_entity_id"],
                        projection["sensitivity"],
                        projection["withheld_reason"],
                    ) == ("general", "facts", *expected, "normal", None)
            finally:
                await fixture_conn.execute(
                    "ALTER TABLE general.knowledge_gaps_offline RENAME TO knowledge_gaps"
                )
        finally:
            await fixture_conn.close()
        assert (await _gap(peer, right, reverse)) == sentinel
        assert (await _gap(own, right, reverse))["id"] == own_gap["id"]
        assert (await _gap(own, right, reverse)) == original_answered_gap
        assert (await _gap(own, fresh_left, forward)) == fresh_direct_gap
        assert (await _gap(own, fresh_right, reverse)) == fresh_inverse_gap
        assert (await _gap(peer, fresh_right, reverse)) == fresh_sentinel
    finally:
        await own.close()
        await peer.close()


async def test_empty_string_inverse_of_is_treated_as_no_inverse(pool_factory, session_hook):
    pool, owner, _ = await pool_factory()
    session_hook(pool)
    left, right = await _entity(pool), await _entity(pool)
    forward = f"kg_narrative_{uuid.uuid4().hex}"
    await pool.execute(
        "INSERT INTO predicate_registry"
        " (name, is_edge, is_temporal, inverse_of, is_symmetric, status)"
        " VALUES ($1, true, false, '', false, 'active')",
        forward,
    )
    await _internal_edge(pool, left, right, forward, sender=owner)
    assert await pool.fetchval("SELECT count(*) FROM facts WHERE predicate = ''") == 0
