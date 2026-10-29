"""REQ-chronicler-api-017: migrated PostgreSQL recovery/refresh and rollback.

These bodies need the supported PostgreSQL executor. Collection or unit doubles
are not proof of the lock, serializable snapshot, or commit/readback assertions.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import asyncpg
import pytest

from butlers.chronicler.day_close_cache import day_close_cache_key, lock_day_close_cache_tuple
from butlers.chronicler.day_close_recovery import RecoveryRefused, apply_recovery, plan_recovery
from butlers.chronicler.storage import upsert_tier2_cache
from butlers.testing.migration import create_migrated_test_pool
from tests.chronicler.test_day_close_narration_selection import notification

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]
DAY = date(2026, 4, 24)
MESSAGE = "The day settled into focused reading and an evening walk."


@pytest.fixture(scope="module")
async def pool(postgres_container):
    value = await create_migrated_test_pool(
        postgres_container,
        chains=["core", "chronicler"],
        schemas={"core": "chronicler", "chronicler": "chronicler"},
        pool_schema="chronicler",
        min_pool_size=2,
        max_pool_size=5,
    )
    yield value
    await value.close()


@pytest.fixture(autouse=True)
async def clean(pool):
    await pool.execute("DROP TRIGGER IF EXISTS recovery_control ON tier2_cache")
    await pool.execute(
        "TRUNCATE tier2_cache, day_close_cache_locks, sessions, deferred_notifications CASCADE"
    )
    await pool.execute("DELETE FROM public.attention_ledger WHERE origin_butler = 'chronicler'")
    yield
    await pool.execute("DROP TRIGGER IF EXISTS recovery_control ON tier2_cache")


async def seed(pool, day=DAY):
    session_id, notification_id = uuid4(), uuid4()
    calls = [
        {
            "name": "chronicler_day_close_bundle",
            "outcome": "success",
            "input": {"date_label": day.isoformat(), "timezone": "UTC"},
            "result": {
                "date": day.isoformat(),
                "episodes": [{}],
                "events": [],
                "citations": ["fixture:source"],
            },
        },
        notification(),
    ]
    calls[1]["result"]["notification_id"] = str(notification_id)
    await pool.execute(
        "INSERT INTO sessions (id, prompt, trigger_source, request_id, success, completed_at, tool_calls)"
        " VALUES ($1, 'fixture', 'schedule:chronicler_day_close', $2, true, now(), $3)",
        session_id,
        str(uuid4()),
        calls,
    )
    await pool.execute(
        "INSERT INTO deferred_notifications (id, butler_name, channel, message, envelope, deliver_at)"
        " VALUES ($1, 'chronicler', 'telegram', $2, $3, now())",
        notification_id,
        MESSAGE,
        {
            "schema_version": "notify.v1",
            "origin_butler": "chronicler",
            "delivery": {
                "message": MESSAGE,
                "channel": "telegram",
                "intent": "send",
                "recipient": "fixture-owner",
            },
        },
    )
    await pool.execute(
        "INSERT INTO public.attention_ledger"
        " (origin_butler, source, outcome, channel, intent, notification_ref, metadata)"
        " VALUES ('chronicler', 'notify', 'deferred', 'telegram', 'send', $1, $2)",
        str(notification_id),
        {"session_id": str(session_id)},
    )
    return notification_id


async def destination(conn, prose, day=DAY):
    start = datetime.combine(day, datetime.min.time(), UTC)
    await upsert_tier2_cache(
        conn,
        cache_key=day_close_cache_key(day, "UTC"),
        start_at=start,
        end_at=start + timedelta(days=1),
        prose=prose,
        provenance_refs=["fixture:source"],
        date_label=day.isoformat(),
        invalid_reason=None,
    )


async def readback(pool):
    # A distinct acquisition only sees committed rows.
    return [dict(row) for row in await pool.fetch("SELECT * FROM tier2_cache ORDER BY cache_key")]


async def test_migrated_exact_recovery_is_idempotent_and_source_drift_refuses(pool):
    notification_id = await seed(pool)
    plan = await plan_recovery(pool, start=DAY, end=DAY, timezone="UTC")
    assert plan.entries[0].reason == "eligible"
    assert (await apply_recovery(pool, plan))[0].reason == "applied"
    before = deepcopy(await readback(pool))
    assert before[0]["prose"] == MESSAGE
    assert (await apply_recovery(pool, plan))[0].reason == "already_applied"
    assert await readback(pool) == before  # includes original build time/provenance
    await pool.execute(
        "UPDATE deferred_notifications SET status = 'cancelled' WHERE id = $1", notification_id
    )
    with pytest.raises(RecoveryRefused, match="source_drift"):
        await apply_recovery(pool, plan)
    assert await readback(pool) == before


async def test_legacy_scalar_tool_calls_row_does_not_abort_planning(pool):
    """A day-close session whose tool_calls JSONB is a double-encoded string (legacy
    rows exist on dev) is no source for any day; it must not fail the whole plan."""
    await seed(pool)
    await pool.execute(
        "INSERT INTO sessions (id, prompt, trigger_source, request_id, success, completed_at, tool_calls)"
        " VALUES ($1, 'fixture', 'schedule:chronicler_day_close', $2, true, now(), to_jsonb($3::text))",
        uuid4(),
        str(uuid4()),
        "[]",
    )
    plan = await plan_recovery(pool, start=DAY - timedelta(days=1), end=DAY, timezone="UTC")
    assert [entry.reason for entry in plan.entries] == ["missing_source", "eligible"]


async def wait_for_lock(pool, table):
    async with asyncio.timeout(5):
        # PostgreSQL lock waits have no Python event; keep this bounded real-DB witness.
        while not await pool.fetchval(  # noqa: ASYNC110
            "SELECT EXISTS (SELECT 1 FROM pg_stat_activity WHERE datname = current_database()"
            " AND wait_event_type = 'Lock' AND query LIKE $1 AND pid <> pg_backend_pid())",
            f"%{table}%",
        ):
            await asyncio.sleep(0.01)


async def test_concurrent_normal_refresh_wins_and_serializable_recovery_cannot_clobber(pool):
    await seed(pool)
    plan = await plan_recovery(pool, start=DAY, end=DAY, timezone="UTC")
    # Establish the registry row before both transactions take their snapshots.
    async with pool.acquire() as conn, conn.transaction():
        await lock_day_close_cache_tuple(conn, DAY, "UTC")
    task = None
    try:
        async with pool.acquire() as normal, normal.transaction():
            await lock_day_close_cache_tuple(normal, DAY, "UTC")
            await destination(normal, "A newer genuine normal-refresh retrospective.")
            task = asyncio.create_task(apply_recovery(pool, plan))
            await wait_for_lock(pool, "day_close_cache_locks")
        # The old snapshot cannot replace a cache committed after its lock attempt.
        with pytest.raises(asyncpg.SerializationError):
            await task
        rows = await readback(pool)
        assert len(rows) == 1
        assert rows[0]["prose"] == "A newer genuine normal-refresh retrospective."
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("cancel", [False, True])
async def test_later_tuple_failure_or_cancellation_rolls_back_the_whole_batch(pool, cancel):
    await seed(pool)
    await seed(pool, DAY + timedelta(days=1))
    plan = await plan_recovery(pool, start=DAY, end=DAY + timedelta(days=1), timezone="UTC")
    assert all(entry.reason == "eligible" for entry in plan.entries)
    # Only disposable fixture DDL injects the failure; all actual writes use the
    # production recovery implementation and fully migrated tables.
    operation = (
        "PERFORM pg_advisory_xact_lock(80424);"
        if cancel
        else ("RAISE EXCEPTION 'fixture recovery write refused';")
    )
    await pool.execute(
        "CREATE OR REPLACE FUNCTION recovery_control() RETURNS trigger LANGUAGE plpgsql AS $$"
        " BEGIN IF NEW.date_label = '2026-04-25' THEN " + operation + " END IF; RETURN NEW; END $$;"
        " CREATE TRIGGER recovery_control BEFORE INSERT OR UPDATE ON tier2_cache"
        " FOR EACH ROW EXECUTE FUNCTION recovery_control();"
    )
    task = None
    try:
        async with pool.acquire() as blocker:
            try:
                if cancel:
                    await blocker.execute("SELECT pg_advisory_lock(80424)")
                    task = asyncio.create_task(apply_recovery(pool, plan))
                    await wait_for_lock(pool, "INSERT INTO tier2_cache")
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
                else:
                    with pytest.raises(asyncpg.RaiseError, match="fixture recovery write refused"):
                        await apply_recovery(pool, plan)
                assert await readback(pool) == []
            finally:
                if cancel:
                    await blocker.execute("SELECT pg_advisory_unlock(80424)")
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
