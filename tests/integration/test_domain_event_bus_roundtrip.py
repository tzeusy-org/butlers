"""Real-Postgres regression: the domain-event bus (bu-ep4ks.10).

Exercises core_186 against a fully migrated Postgres instance (testcontainers)
-- not just mocked-pool unit tests (see ``tests/core_tools/test_domain_events.py``
for those):

- ``public.domain_events``/``public.butler_subscriptions``/``public.
  domain_event_deliveries`` round-trip through the real production writer/
  reader (``butlers.core.domain_events``).
- The migration seeds the one concrete pair this move wires end-to-end:
  Finance standing-subscribed to ``travel.trip_booked``.
- ``publish_domain_event`` -> ``fan_out_event`` -> the subscriber's
  ``receive_domain_event`` handler (``handle_receive_domain_event``) creates
  exactly one real ``scheduled_tasks`` row, end to end, through a stub
  switchboard client that simulates the Switchboard routing the call to the
  subscriber's own MCP tool (mirroring ``test_delegation_ask_dispatch_
  roundtrip.py``'s ``_OkClient``).
- Fan-out idempotence: a retried fan-out for an already-delivered event never
  re-dispatches or creates a second task; a conflicting deterministic task
  name fails closed as ``"conflict"`` on both the delivery ledger and the
  subscriber's own reconciliation.
"""

from __future__ import annotations

import shutil
from typing import Any

import asyncpg
import pytest

from butlers.core.domain_event_wake import handle_receive_domain_event, task_name_for
from butlers.core.domain_events import (
    claim_delivery,
    get_active_subscribers,
    list_recent_deliveries,
    list_subscriptions,
)
from butlers.core_tools._domain_events import fan_out_event, publish_domain_event
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    return create_migrated_test_db(postgres_container, migration_db_name(), chains=["core"])


@pytest.fixture
async def pool(migrated_db_url: str) -> asyncpg.Pool:
    p = await asyncpg.create_pool(migrated_db_url, min_size=1, max_size=5)
    yield p
    await p.close()


class _ReceivingClient:
    """Stub switchboard_client that routes straight into the real subscriber handler.

    Simulates what the Switchboard's ``route()`` primitive does in
    production -- deliver the call to the target butler's own
    ``receive_domain_event`` MCP tool -- without needing a live MCP server.
    Both sides share the same real Postgres pool, exactly as they would in
    production (each butler's own connection, same database).
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def call_tool(self, tool_name: str, args: dict[str, Any]) -> Any:
        from types import SimpleNamespace

        assert tool_name == "route"
        assert args["tool_name"] == "receive_domain_event"
        call_args = args["args"]
        result = await handle_receive_domain_event(
            self._pool,
            event_id=call_args["event_id"],
            event_type=call_args["event_type"],
            source_butler=call_args["source_butler"],
            payload=call_args["payload"],
            subscriber_butler=args["target_butler"],
        )
        return SimpleNamespace(is_error=False, data=result)


# ---------------------------------------------------------------------------
# Migration shape + seed
# ---------------------------------------------------------------------------


async def test_domain_event_tables_exist(pool: asyncpg.Pool) -> None:
    for table in ("domain_events", "butler_subscriptions", "domain_event_deliveries"):
        exists = await pool.fetchval("SELECT to_regclass($1) IS NOT NULL", f"public.{table}")
        assert exists, f"public.{table} should exist"


async def test_delivery_status_check_constraint_rejects_bogus_value(pool: asyncpg.Pool) -> None:
    event_id = await pool.fetchval(
        "INSERT INTO public.domain_events (event_type, source_butler, payload) "
        "VALUES ('travel.trip_booked', 'travel', '{}'::jsonb) RETURNING id"
    )
    await pool.execute(
        "INSERT INTO public.domain_event_deliveries (event_id, subscriber_butler, status) "
        "VALUES ($1, 'finance', 'pending')",
        event_id,
    )
    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute(
            "UPDATE public.domain_event_deliveries SET status = 'bogus' WHERE event_id = $1",
            event_id,
        )


async def test_migration_seeds_finance_trip_booked_subscription(pool: asyncpg.Pool) -> None:
    subscribers = await get_active_subscribers(pool, "travel.trip_booked")
    assert "finance" in subscribers

    rows = await list_subscriptions(
        pool, subscriber_butler="finance", event_type="travel.trip_booked"
    )
    assert len(rows) == 1
    assert rows[0]["active"] is True


async def test_migration_seeds_health_trip_active_subscription(pool: asyncpg.Pool) -> None:
    """bu-317s5 slice 2 (core_189): Health standing-subscribes to Travel's
    trip-active transition so it can front-load medication prep."""
    subscribers = await get_active_subscribers(pool, "travel.trip_active")
    assert "health" in subscribers

    rows = await list_subscriptions(
        pool, subscriber_butler="health", event_type="travel.trip_active"
    )
    assert len(rows) == 1
    assert rows[0]["active"] is True


# ---------------------------------------------------------------------------
# End-to-end: publish -> fan-out -> subscriber wake
# ---------------------------------------------------------------------------


async def test_publish_fans_out_to_seeded_finance_subscription(pool: asyncpg.Pool) -> None:
    client = _ReceivingClient(pool)

    result = await publish_domain_event(
        pool,
        client,
        event_type="travel.trip_booked",
        source_butler="travel",
        payload={"trip_id": "trip-1", "destination": "Tokyo", "name": "Trip to Tokyo"},
    )

    assert result["status"] == "ok"
    event_id = result["event_id"]
    assert result["deliveries"] == [{"subscriber_butler": "finance", "status": "delivered"}]

    event_row = await pool.fetchrow(
        "SELECT event_type, source_butler, payload FROM public.domain_events WHERE id = $1",
        event_id,
    )
    assert event_row["event_type"] == "travel.trip_booked"
    assert event_row["source_butler"] == "travel"

    delivery_row = await pool.fetchrow(
        "SELECT status, task_id, task_name FROM public.domain_event_deliveries "
        "WHERE event_id = $1 AND subscriber_butler = 'finance'",
        event_id,
    )
    assert delivery_row["status"] == "delivered"
    assert delivery_row["task_name"] == task_name_for(event_id, "finance")

    task_rows = await pool.fetch(
        "SELECT id, prompt FROM scheduled_tasks WHERE name = $1",
        task_name_for(event_id, "finance"),
    )
    assert len(task_rows) == 1
    assert str(task_rows[0]["id"]) == str(delivery_row["task_id"])
    assert "Tokyo" in task_rows[0]["prompt"]
    assert "travel.trip_booked" in task_rows[0]["prompt"]


async def test_no_active_subscribers_publishes_with_zero_deliveries(pool: asyncpg.Pool) -> None:
    client = _ReceivingClient(pool)

    result = await publish_domain_event(
        pool,
        client,
        event_type="travel.document_expiring",
        source_butler="travel",
        payload={"trip_id": "trip-2"},
    )

    assert result["status"] == "ok"
    assert result["deliveries"] == []

    event_row = await pool.fetchrow(
        "SELECT event_type FROM public.domain_events WHERE id = $1", result["event_id"]
    )
    assert event_row["event_type"] == "travel.document_expiring"


async def test_publisher_is_never_its_own_fanout_target(pool: asyncpg.Pool) -> None:
    from butlers.core.domain_events import upsert_subscription

    await upsert_subscription(pool, subscriber_butler="travel", event_type="travel.self_test")
    client = _ReceivingClient(pool)

    result = await publish_domain_event(
        pool, client, event_type="travel.self_test", source_butler="travel", payload={}
    )

    assert result["deliveries"] == []


# ---------------------------------------------------------------------------
# Fan-out idempotence: retries never double-dispatch
# ---------------------------------------------------------------------------


async def test_retried_fanout_of_already_delivered_event_does_not_redispatch(
    pool: asyncpg.Pool,
) -> None:
    client = _ReceivingClient(pool)
    first = await publish_domain_event(
        pool, client, event_type="travel.trip_booked", source_butler="travel", payload={"n": 1}
    )
    event_id = first["event_id"]

    # A second fan-out attempt for the same event (simulating a caller retry
    # or a periodic reconciliation sweep) must observe the already-delivered
    # row and skip re-dispatch entirely.
    second = await fan_out_event(
        pool,
        client,
        event_id=event_id,
        event_type="travel.trip_booked",
        source_butler="travel",
        payload={"n": 1},
    )
    assert second["deliveries"] == [{"subscriber_butler": "finance", "status": "delivered"}]

    count = await pool.fetchval(
        "SELECT count(*) FROM scheduled_tasks WHERE name = $1",
        task_name_for(event_id, "finance"),
    )
    assert count == 1

    delivery_count = await pool.fetchval(
        "SELECT count(*) FROM public.domain_event_deliveries WHERE event_id = $1", event_id
    )
    assert delivery_count == 1  # UNIQUE(event_id, subscriber_butler) — no duplicate row


async def test_claim_delivery_is_atomic_per_event_subscriber_pair(pool: asyncpg.Pool) -> None:
    event_id = await pool.fetchval(
        "INSERT INTO public.domain_events (event_type, source_butler, payload) "
        "VALUES ('travel.trip_booked', 'travel', '{}'::jsonb) RETURNING id"
    )

    first = await claim_delivery(pool, event_id=event_id, subscriber_butler="finance")
    second = await claim_delivery(pool, event_id=event_id, subscriber_butler="finance")

    assert first["id"] == second["id"]
    assert first["status"] == "pending"

    count = await pool.fetchval(
        "SELECT count(*) FROM public.domain_event_deliveries "
        "WHERE event_id = $1 AND subscriber_butler = 'finance'",
        event_id,
    )
    assert count == 1


async def test_handle_receive_domain_event_duplicate_delivery_returns_same_task(
    pool: asyncpg.Pool,
) -> None:
    event_id = await pool.fetchval(
        "INSERT INTO public.domain_events (event_type, source_butler, payload) "
        "VALUES ('travel.trip_booked', 'travel', '{}'::jsonb) RETURNING id"
    )

    first = await handle_receive_domain_event(
        pool,
        event_id=event_id,
        event_type="travel.trip_booked",
        source_butler="travel",
        payload={"trip_id": "t"},
        subscriber_butler="finance",
    )
    second = await handle_receive_domain_event(
        pool,
        event_id=event_id,
        event_type="travel.trip_booked",
        source_butler="travel",
        payload={"trip_id": "t"},
        subscriber_butler="finance",
    )

    assert first["status"] == "ok"
    assert second["task_id"] == first["task_id"]
    assert second.get("reconciled") is True

    count = await pool.fetchval(
        "SELECT count(*) FROM scheduled_tasks WHERE name = $1", task_name_for(event_id, "finance")
    )
    assert count == 1


async def test_conflicting_deterministic_name_fails_closed(pool: asyncpg.Pool) -> None:
    event_id = await pool.fetchval(
        "INSERT INTO public.domain_events (event_type, source_butler, payload) "
        "VALUES ('travel.trip_booked', 'travel', '{}'::jsonb) RETURNING id"
    )
    task_name = task_name_for(event_id, "finance")
    unrelated_task_id = await pool.fetchval(
        """
        INSERT INTO scheduled_tasks (name, cron, dispatch_mode, prompt, source, enabled)
        VALUES ($1, '* * * * *', 'prompt', 'an unrelated hand-crafted task', 'db', true)
        RETURNING id
        """,
        task_name,
    )

    result = await handle_receive_domain_event(
        pool,
        event_id=event_id,
        event_type="travel.trip_booked",
        source_butler="travel",
        payload={"trip_id": "t"},
        subscriber_butler="finance",
    )

    assert result["status"] == "conflict"

    task_rows = await pool.fetch("SELECT id FROM scheduled_tasks WHERE name = $1", task_name)
    assert len(task_rows) == 1
    assert task_rows[0]["id"] == unrelated_task_id


async def test_fanout_records_conflict_on_delivery_ledger(pool: asyncpg.Pool) -> None:
    event_id = await pool.fetchval(
        "INSERT INTO public.domain_events (event_type, source_butler, payload) "
        "VALUES ('travel.trip_booked', 'travel', '{}'::jsonb) RETURNING id"
    )
    task_name = task_name_for(event_id, "finance")
    await pool.execute(
        """
        INSERT INTO scheduled_tasks (name, cron, dispatch_mode, prompt, source, enabled)
        VALUES ($1, '* * * * *', 'prompt', 'an unrelated hand-crafted task', 'db', true)
        """,
        task_name,
    )
    client = _ReceivingClient(pool)

    result = await fan_out_event(
        pool,
        client,
        event_id=event_id,
        event_type="travel.trip_booked",
        source_butler="travel",
        payload={"trip_id": "t"},
    )

    assert result["deliveries"] == [{"subscriber_butler": "finance", "status": "conflict"}]

    delivery_row = await pool.fetchrow(
        "SELECT status FROM public.domain_event_deliveries "
        "WHERE event_id = $1 AND subscriber_butler = 'finance'",
        event_id,
    )
    assert delivery_row["status"] == "conflict"


# ---------------------------------------------------------------------------
# Dashboard subscription visibility (bu-317s5 slice 2): list_recent_deliveries
# ---------------------------------------------------------------------------


async def test_list_recent_deliveries_filters_by_subscriber_and_status(
    pool: asyncpg.Pool,
) -> None:
    client = _ReceivingClient(pool)

    await publish_domain_event(
        pool,
        client,
        event_type="travel.trip_booked",
        source_butler="travel",
        payload={"trip_id": "trip-recent-1"},
    )
    # A second event of a type nobody subscribes to -- no delivery row at all,
    # so it must never appear in a subscriber-filtered read.
    await publish_domain_event(
        pool,
        client,
        event_type="travel.trip_unsubscribed_event",
        source_butler="travel",
        payload={},
    )

    total, rows = await list_recent_deliveries(pool, subscriber_butler="finance")
    assert total >= 1
    assert all(row["subscriber_butler"] == "finance" for row in rows)
    assert any(
        row["event_type"] == "travel.trip_booked" and row["source_butler"] == "travel"
        for row in rows
    )

    total_delivered, rows_delivered = await list_recent_deliveries(
        pool, subscriber_butler="finance", status="delivered"
    )
    assert total_delivered >= 1
    assert all(row["status"] == "delivered" for row in rows_delivered)

    total_none, rows_none = await list_recent_deliveries(
        pool, subscriber_butler="a-butler-with-no-deliveries"
    )
    assert total_none == 0
    assert rows_none == []


async def test_list_recent_deliveries_orders_most_recent_first(pool: asyncpg.Pool) -> None:
    client = _ReceivingClient(pool)

    first = await publish_domain_event(
        pool,
        client,
        event_type="travel.trip_booked",
        source_butler="travel",
        payload={"trip_id": "trip-order-1"},
    )
    second = await publish_domain_event(
        pool,
        client,
        event_type="travel.trip_booked",
        source_butler="travel",
        payload={"trip_id": "trip-order-2"},
    )

    _total, rows = await list_recent_deliveries(pool, subscriber_butler="finance", limit=2)
    event_ids = [row["event_id"] for row in rows[:2]]
    assert str(event_ids[0]) == second["event_id"]
    assert str(event_ids[1]) == first["event_id"]
