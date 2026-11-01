"""Real transaction contracts for the actual memory and no-reclaim outbox shapes.

These shape tests do not replace the migrated outbox/RLS and memory lifecycle
owners. They exercise the shared primitive without dispatching any transport.
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest

from butlers.core.queue_claim import claim_batch, fail_claimed
from butlers.modules.memory.consolidation import _EPISODE_CLAIMS
from butlers.tools.switchboard.runtime_attention.outbox import _OUTBOX_CLAIMS as _OUTBOX

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

_SCHEMA = """
CREATE TABLE episodes (
 id uuid PRIMARY KEY, tenant_id text NOT NULL DEFAULT 'tenant', butler text NOT NULL DEFAULT 'test',
 content text NOT NULL DEFAULT 'dummy', importance float8 DEFAULT 1, metadata jsonb DEFAULT '{}',
 created_at timestamptz NOT NULL DEFAULT now(), content_authority text DEFAULT 'third_party',
 authority_entity_id uuid, consolidated boolean DEFAULT false,
 consolidation_status text DEFAULT 'pending', consolidation_attempts integer DEFAULT 0,
 leased_until timestamptz, leased_by text, last_consolidation_error text,
 dead_letter_reason text, next_consolidation_retry_at timestamptz
);
CREATE TABLE runtime_attention_outbox (
 id uuid PRIMARY KEY, source text DEFAULT 'model_breaker', source_snapshot jsonb DEFAULT '{}',
 payload jsonb DEFAULT '{}', lifecycle_state text DEFAULT 'pending', next_attempt_at timestamptz DEFAULT now(),
 created_at timestamptz DEFAULT now(), claim_token uuid, claim_epoch bigint DEFAULT 0,
 delivery_lease_epoch bigint, claimed_by_instance text, claimed_at timestamptz,
 claim_expires_at timestamptz
);
CREATE TABLE audit_witness (id uuid NOT NULL);
"""


@pytest.fixture
async def queue_pool(provisioned_postgres_pool):
    async with provisioned_postgres_pool() as pool:
        await pool.execute(_SCHEMA)
        yield pool


def _queue(shape):
    return _EPISODE_CLAIMS if shape == "memory" else _OUTBOX


async def _seed(pool, shape, *, status=None, attempts=0, lease=None, order=0):
    key = uuid.uuid4()
    if shape == "memory":
        await pool.execute(
            "INSERT INTO episodes(id,consolidation_status,consolidation_attempts,leased_until,"
            "leased_by,created_at,next_consolidation_retry_at) "
            "VALUES($1,$2,$3,CASE WHEN $4::integer IS NULL THEN NULL ELSE now()+"
            "make_interval(secs=>$4) END,'old',timestamptz '2000-01-01'+make_interval(secs=>$5),now()-interval '1 day')",
            key,
            status or "pending",
            attempts,
            lease,
            order,
        )
    else:
        await pool.execute(
            "INSERT INTO runtime_attention_outbox(id,lifecycle_state,claim_expires_at,created_at,next_attempt_at) "
            "VALUES($1,$2,now()+make_interval(secs=>$3),"
            "timestamptz '2000-01-01'+make_interval(secs=>$4),timestamptz '2000-01-01')",
            key,
            status or "pending",
            lease or -1,
            order,
        )
    return key


async def _claim(conn, shape, *, owner="worker", limit=10):
    return await claim_batch(
        conn,
        _queue(shape),
        limit=limit,
        owner=owner,
        lease_seconds=30,
        args=(True,) if shape == "memory" else (uuid.uuid4(), 7),
    )


@pytest.mark.parametrize("shape", ["memory", "outbox"])
async def test_claim_refuses_an_unenlisted_real_connection(queue_pool, shape):
    key = await _seed(queue_pool, shape)
    async with queue_pool.acquire() as conn:
        with pytest.raises(RuntimeError, match="transaction"):
            await _claim(conn, shape)
    async with queue_pool.acquire() as conn, conn.transaction():
        assert [r["id"] for r in await _claim(conn, shape)] == [key]


@pytest.mark.parametrize("shape", ["memory", "outbox"])
async def test_concurrent_claimers_skip_locks_and_rollback_releases_work(queue_pool, shape):
    first = await _seed(queue_pool, shape, order=-2)
    second = await _seed(queue_pool, shape, order=-1)
    async with queue_pool.acquire() as a, queue_pool.acquire() as b:
        tx = a.transaction()
        await tx.start()
        try:
            claimed = await _claim(a, shape, limit=1)
            assert [r["id"] for r in claimed] == [first]
            async with b.transaction():
                assert [r["id"] for r in await _claim(b, shape)] == [second]
        finally:
            await tx.rollback()
        async with a.transaction():
            assert [r["id"] for r in await _claim(a, shape)] == [first]


@pytest.mark.parametrize("shape", ["memory", "outbox"])
async def test_live_lease_and_expiry_respect_the_queue_reclaim_policy(queue_pool, shape):
    pending = await _seed(queue_pool, shape)
    expired = await _seed(
        queue_pool, shape, status="pending" if shape == "memory" else "sending", lease=-1
    )
    live = await _seed(
        queue_pool, shape, status="pending" if shape == "memory" else "sending", lease=60
    )
    async with queue_pool.acquire() as conn, conn.transaction():
        ids = {r["id"] for r in await _claim(conn, shape)}
        assert ids == ({pending, expired} if shape == "memory" else {pending})
        assert live not in ids
    async with queue_pool.acquire() as conn, conn.transaction():
        assert await _claim(conn, shape) == []


@pytest.mark.parametrize("shape", ["memory", "outbox"])
async def test_batch_limit_and_locked_select_order_are_preserved(queue_pool, shape):
    ids = [await _seed(queue_pool, shape, order=i) for i in (-3, -2, -1)]
    async with queue_pool.acquire() as conn, conn.transaction():
        assert [r["id"] for r in await _claim(conn, shape, limit=2)] == ids[:2]
    async with queue_pool.acquire() as conn, conn.transaction():
        assert [r["id"] for r in await _claim(conn, shape, limit=2)] == ids[2:]


@pytest.mark.parametrize("shape", ["memory", "outbox"])
async def test_claim_is_durable_only_after_the_owning_commit(queue_pool, shape):
    key = await _seed(queue_pool, shape)
    async with queue_pool.acquire() as conn, conn.transaction():
        assert [r["id"] for r in await _claim(conn, shape, owner="new-owner")] == [key]
    if shape == "memory":
        row = await queue_pool.fetchrow("SELECT * FROM episodes WHERE id=$1", key)
        assert row["leased_by"] == "new-owner"
        assert row["leased_until"] is not None
        assert row["consolidation_attempts"] == 0
    else:
        row = await queue_pool.fetchrow("SELECT * FROM runtime_attention_outbox WHERE id=$1", key)
        assert row["lifecycle_state"] == "sending"
        assert row["claimed_by_instance"] == "new-owner"
        assert row["claim_epoch"] == 1
        assert row["delivery_lease_epoch"] == 7
        assert row["claim_token"] is not None


async def test_memory_exhausted_pending_and_failed_rows_are_not_claimed(queue_pool):
    await _seed(queue_pool, "memory", status="pending", attempts=5)
    await _seed(queue_pool, "memory", status="failed", attempts=5)
    async with queue_pool.acquire() as conn, conn.transaction():
        assert await _claim(conn, "memory") == []


async def test_memory_terminal_failure_deadletters_once_and_never_reclaims(queue_pool):
    key = await _seed(queue_pool, "memory", attempts=4)
    async with queue_pool.acquire() as conn, conn.transaction():
        await _claim(conn, "memory")
        rows = await fail_claimed(conn, _EPISODE_CLAIMS, [key], owner="worker", reason="safe")
        assert rows[0]["consolidation_attempts"] == 5
        assert rows[0]["consolidation_status"] == "dead_letter"
        assert await fail_claimed(conn, _EPISODE_CLAIMS, [key], owner="worker", reason="safe") == []
    row = await queue_pool.fetchrow("SELECT * FROM episodes WHERE id=$1", key)
    assert row["leased_by"] is None and row["leased_until"] is None
    assert row["dead_letter_reason"] == "safe"
    assert row["next_consolidation_retry_at"] is None
    async with queue_pool.acquire() as conn, conn.transaction():
        assert await _claim(conn, "memory") == []


async def test_memory_nonowner_replaced_and_expired_claims_cannot_fail(queue_pool):
    key = await _seed(queue_pool, "memory")
    async with queue_pool.acquire() as conn, conn.transaction():
        await _claim(conn, "memory")
        assert await fail_claimed(conn, _EPISODE_CLAIMS, [key], owner="stale", reason="safe") == []
    await queue_pool.execute("UPDATE episodes SET leased_by='replacement' WHERE id=$1", key)
    async with queue_pool.acquire() as conn, conn.transaction():
        assert await fail_claimed(conn, _EPISODE_CLAIMS, [key], owner="worker", reason="safe") == []
    await queue_pool.execute(
        "UPDATE episodes SET leased_until=now()-interval '1 second' WHERE id=$1", key
    )
    async with queue_pool.acquire() as conn, conn.transaction():
        assert (
            await fail_claimed(conn, _EPISODE_CLAIMS, [key], owner="replacement", reason="safe")
            == []
        )
    assert (
        await queue_pool.fetchval("SELECT consolidation_attempts FROM episodes WHERE id=$1", key)
        == 0
    )


async def test_memory_failed_event_rolls_back_the_transition_and_consumption(queue_pool):
    key = await _seed(queue_pool, "memory")
    with pytest.raises(asyncpg.NotNullViolationError):
        async with queue_pool.acquire() as conn, conn.transaction():
            await _claim(conn, "memory")
            await fail_claimed(conn, _EPISODE_CLAIMS, [key], owner="worker", reason="safe")
            await conn.execute("INSERT INTO audit_witness VALUES(NULL)")
    row = await queue_pool.fetchrow("SELECT * FROM episodes WHERE id=$1", key)
    assert row["consolidation_attempts"] == 0
    assert row["consolidation_status"] == "pending"
    assert row["leased_until"] is None


async def test_memory_retry_backoff_is_finite_and_does_not_retry_early(queue_pool):
    key = await _seed(queue_pool, "memory")
    async with queue_pool.acquire() as conn, conn.transaction():
        await _claim(conn, "memory")
        rows = await fail_claimed(conn, _EPISODE_CLAIMS, [key], owner="worker", reason="safe")
        assert rows[0]["consolidation_attempts"] == 1
        assert rows[0]["consolidation_status"] == "failed"
    assert (
        await queue_pool.fetchval(
            "SELECT next_consolidation_retry_at > now() AND "
            "next_consolidation_retry_at <= now()+interval '120 seconds' FROM episodes WHERE id=$1",
            key,
        )
        is True
    )
    async with queue_pool.acquire() as conn, conn.transaction():
        assert await _claim(conn, "memory") == []


async def test_memory_claim_keeps_tenant_content_authority_and_entity_binding(queue_pool):
    key = await _seed(queue_pool, "memory")
    entity = uuid.uuid4()
    await queue_pool.execute(
        "UPDATE episodes SET tenant_id='private',authority_entity_id=$2 WHERE id=$1", key, entity
    )
    async with queue_pool.acquire() as conn, conn.transaction():
        row = (await _claim(conn, "memory"))[0]
        assert row["tenant_id"] == "private"
        assert row["content_authority"] == "third_party"
        assert row["authority_entity_id"] == entity
