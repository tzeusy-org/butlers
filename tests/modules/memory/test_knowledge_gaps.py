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
