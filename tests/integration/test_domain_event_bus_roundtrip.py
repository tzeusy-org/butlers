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
- Reconciliation-sweep mutual exclusion: overlapping sweep invocations do
  not double-progress one retryable delivery toward its terminal attempt
  limit.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import uuid
from datetime import timedelta
from typing import Any

import asyncpg
import pytest

from butlers.core.domain_event_wake import handle_receive_domain_event, task_name_for
from butlers.core.domain_events import (
    claim_delivery,
    get_active_subscribers,
    list_recent_deliveries,
    list_subscriptions,
    mark_delivery_delivered,
    mark_delivery_failed,
    upsert_subscription,
)
from butlers.core_tools._domain_events import (
    fan_out_event,
    publish_domain_event,
    run_domain_event_reconciliation_sweep,
)
from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


_PUBLISHED_TEST_EVENT_TYPES = (
    "travel.trip_booked",
    "travel.document_expiring",
    "travel.self_test",
    "travel.trip_unsubscribed_event",
)


@pytest.fixture(autouse=True)
def permissive_contracts():
    """Admit this file's synthetic event types (bu-6jv4m.8).

    Publishing is fail-closed against the publisher's git-declared contract.
    These tests are about *transport* -- fan-out, idempotence, retry
    classification -- not about payload minimization, so they declare their
    own permissive contracts rather than bending the production roster's.
    Contract admission itself is covered in
    tests/core_tools/test_domain_event_admission.py.
    """
    from butlers.core.domain_event_contracts import (
        DomainEventContractRegistry,
        set_contract_registry,
    )

    set_contract_registry(
        DomainEventContractRegistry.from_declarations(
            [
                (
                    "travel",
                    {
                        "type": event_type,
                        "schema_version": 1,
                        "summary": f"Synthetic test event {event_type}.",
                        "retention_policy": "standard",
                        "reaction_expectation": "optional",
                        "reaction_contract": "Test-only; no reaction is expected.",
                        "permitted_subscribers": ["finance", "health", "travel"],
                        "required_fields": [],
                        "optional_fields": [
                            "trip_id",
                            "destination",
                            "name",
                            "status",
                            "start_date",
                            "end_date",
                            "n",
                        ],
                    },
                )
                for event_type in _PUBLISHED_TEST_EVENT_TYPES
            ]
        )
    )
    yield
    set_contract_registry(None)


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    return create_migrated_test_db(postgres_container, migration_db_name(), chains=["core"])


@pytest.fixture
async def pool(migrated_db_url: str) -> asyncpg.Pool:
    p = await asyncpg.create_pool(
        migrated_db_url, min_size=1, max_size=5, init=register_jsonb_codec
    )
    yield p
    await p.close()


class _ReceivingClient:
    """Stub switchboard_client that routes straight into the real subscriber handler.

    Simulates what the Switchboard's ``route()`` primitive does in
    production -- deliver the call to the target butler's own
    ``receive_domain_event`` MCP tool -- without needing a live MCP server.
    Both sides share the same real Postgres pool, exactly as they would in
    production (each butler's own connection, same database).

    Wraps the target's own return value in ``{"result": ...}`` -- matching
    ``route()``'s actual envelope (see ``roster/switchboard/tools/routing/
    route.py::route``, which returns ``{"result": <target tool's return>}``
    on success or ``{"error": ...}`` on failure) so this stub does not mask
    the unwrap contract ``_dispatch_receive_via_switchboard`` depends on.
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
        return SimpleNamespace(is_error=False, data={"result": result})


class _IncompleteSuccessClient:
    """Simulate a target tool that claims success without delivery provenance."""

    async def call_tool(self, tool_name: str, args: dict[str, Any]) -> Any:
        from types import SimpleNamespace

        assert tool_name == "route"
        assert args["tool_name"] == "receive_domain_event"
        return SimpleNamespace(is_error=False, data={"result": {"status": "ok"}})


class _BlockingIncompleteSuccessClient:
    """Holds an incomplete target success until a terminal fence lands."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def call_tool(self, tool_name: str, args: dict[str, Any]) -> Any:
        from types import SimpleNamespace

        assert tool_name == "route"
        assert args["tool_name"] == "receive_domain_event"
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return SimpleNamespace(is_error=False, data={"result": {"state": "task_created"}})


class _UnexpectedRouteClient:
    """Records any accidental dispatch of a terminal delivery."""

    def __init__(self) -> None:
        self.calls = 0

    async def call_tool(self, tool_name: str, args: dict[str, Any]) -> Any:
        self.calls += 1
        raise AssertionError(f"terminal delivery was dispatched via {tool_name!r}: {args!r}")


class _BlockingSuccessClient:
    """Holds a valid target success until a concurrent terminal fence lands."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0
        self.task_id = str(uuid.uuid4())

    async def call_tool(self, tool_name: str, args: dict[str, Any]) -> Any:
        from types import SimpleNamespace

        assert tool_name == "route"
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return SimpleNamespace(
            is_error=False,
            data={
                "result": {
                    "state": "task_created",
                    "task_id": self.task_id,
                    "task_name": "domain-event-terminal-fence-race",
                }
            },
        )


class _BlockingConflictClient:
    """Holds a real task-conflict result until a terminal fence lands."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def call_tool(self, tool_name: str, args: dict[str, Any]) -> Any:
        from types import SimpleNamespace

        assert tool_name == "route"
        assert args["tool_name"] == "receive_domain_event"
        self.calls += 1
        self.started.set()
        await self.release.wait()
        call_args = args["args"]
        result = await handle_receive_domain_event(
            self._pool,
            event_id=call_args["event_id"],
            event_type=call_args["event_type"],
            source_butler=call_args["source_butler"],
            payload=call_args["payload"],
            subscriber_butler=args["target_butler"],
        )
        assert result["state"] == "task_conflict"
        return SimpleNamespace(is_error=False, data={"result": result})


class _PermanentFailureClient:
    """Returns a route-level terminal failure and counts actual dispatches."""

    def __init__(self) -> None:
        self.calls = 0

    async def call_tool(self, tool_name: str, args: dict[str, Any]) -> Any:
        from types import SimpleNamespace

        assert tool_name == "route"
        assert args["tool_name"] == "receive_domain_event"
        self.calls += 1
        return SimpleNamespace(
            is_error=False,
            data={
                "error": "RuntimeError: permanent terminal fence",
                "retryable": False,
            },
        )


class _BlockingRetryableFailureClient:
    """Holds a retryable route failure until a terminal fence lands."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def call_tool(self, tool_name: str, args: dict[str, Any]) -> Any:
        from types import SimpleNamespace

        assert tool_name == "route"
        assert args["tool_name"] == "receive_domain_event"
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return SimpleNamespace(
            is_error=False,
            data={
                "error": "TimeoutError: stale retryable route failure",
                "retryable": True,
            },
        )


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


async def test_incomplete_target_success_is_recorded_failed_permanent(pool: asyncpg.Pool) -> None:
    """A malformed target success cannot be selected for later retry reconciliation."""
    result = await publish_domain_event(
        pool,
        _IncompleteSuccessClient(),
        event_type="travel.trip_booked",
        source_butler="travel",
        payload={"trip_id": "trip-incomplete-target-result"},
    )

    assert result["deliveries"][0]["subscriber_butler"] == "finance"
    assert result["deliveries"][0]["status"] == "failed_permanent"
    assert "incomplete success payload" in result["deliveries"][0]["error"]
    delivery = await pool.fetchrow(
        "SELECT status, attempt_count, error_message FROM public.domain_event_deliveries "
        "WHERE event_id = $1 AND subscriber_butler = 'finance'",
        result["event_id"],
    )
    assert delivery["status"] == "failed_permanent"
    assert delivery["attempt_count"] == 1
    assert "incomplete success payload" in delivery["error_message"]


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


# ---------------------------------------------------------------------------
# Periodic reconciliation sweep (bu-1yw6d, PR #3585 review follow-up)
# ---------------------------------------------------------------------------


def _patch_switchboard_route(monkeypatch, pool: asyncpg.Pool, *, route_fn) -> None:
    """Patch the module-level ``route`` the sweep's in-process client imports
    at call time (``_SwitchboardInProcessRouteClient.call_tool``), so the
    sweep exercises its real dispatch path (claim/mark idempotence, envelope
    unwrap) without a live cross-butler MCP server.

    Uses ``importlib.import_module`` rather than ``import ...routing.route as
    x`` -- ``butlers.tools.switchboard.routing``'s ``__init__`` re-exports
    ``route`` (the function) at the package level, shadowing the ``route``
    *submodule* attribute on the parent package, so a dotted ``as``-import
    would silently bind to the function instead of the module.
    """
    import importlib

    route_module = importlib.import_module("butlers.tools.switchboard.routing.route")
    monkeypatch.setattr(route_module, "route", route_fn)


async def _ok_switchboard_route(
    pool: asyncpg.Pool,
    target_butler: str,
    tool_name: str,
    args: dict[str, Any],
    source_butler: str = "switchboard",
    **_kwargs: Any,
) -> dict[str, Any]:
    """Real-envelope-shaped stand-in for route() that actually reconciles the
    subscriber's wake task, mirroring `_ReceivingClient` above."""
    assert tool_name == "receive_domain_event"
    result = await handle_receive_domain_event(
        pool,
        event_id=args["event_id"],
        event_type=args["event_type"],
        source_butler=args["source_butler"],
        payload=args["payload"],
        subscriber_butler=target_butler,
    )
    return {"result": result}


async def _permanent_failure_switchboard_route(
    pool: asyncpg.Pool,
    target_butler: str,
    tool_name: str,
    args: dict[str, Any],
    source_butler: str = "switchboard",
    **_kwargs: Any,
) -> dict[str, Any]:
    """Simulates the target lacking the `domain_events` core group -- an
    "unknown tool" route()-level failure, permanent per
    `_is_retryable_route_error_text`."""
    return {"error": "RuntimeError: Unknown tool: receive_domain_event"}


async def _transient_failure_switchboard_route(
    pool: asyncpg.Pool,
    target_butler: str,
    tool_name: str,
    args: dict[str, Any],
    source_butler: str = "switchboard",
    **_kwargs: Any,
) -> dict[str, Any]:
    """Simulates a real route envelope with a concrete nonlegacy transport name."""
    return {"error": "ClientConnectorError: connection refused", "retryable": True}


async def _insert_delivery_row(
    pool: asyncpg.Pool,
    *,
    event_type: str,
    source_butler: str,
    subscriber_butler: str,
    status: str,
    updated_at_ago: timedelta,
    attempt_count: int = 0,
) -> tuple[Any, Any]:
    """Seed one durable subscription + event + delivery row, with `updated_at`
    backdated by `updated_at_ago` -- the sweep's staleness/backoff signal."""
    await upsert_subscription(pool, subscriber_butler=subscriber_butler, event_type=event_type)
    event_id = await pool.fetchval(
        "INSERT INTO public.domain_events (event_type, source_butler, payload) "
        "VALUES ($1, $2, $3::jsonb) RETURNING id",
        event_type,
        source_butler,
        "{}",
    )
    delivery_id = await pool.fetchval(
        """
        INSERT INTO public.domain_event_deliveries
            (event_id, subscriber_butler, status, attempt_count, updated_at)
        VALUES ($1, $2, $3, $4, now() - $5::interval)
        RETURNING id
        """,
        event_id,
        subscriber_butler,
        status,
        attempt_count,
        updated_at_ago,
    )
    return event_id, delivery_id


async def _delivery_status(pool: asyncpg.Pool, delivery_id: Any) -> tuple[str, int]:
    row = await pool.fetchrow(
        "SELECT status, attempt_count FROM public.domain_event_deliveries WHERE id = $1",
        delivery_id,
    )
    return row["status"], row["attempt_count"]


async def test_fanout_replay_does_not_dispatch_a_real_failed_permanent_delivery(
    pool: asyncpg.Pool,
) -> None:
    """A durable terminal ledger row is re-observed, never sent to route() again."""
    event_type = f"terminal_replay.{uuid.uuid4().hex}"
    event_id, delivery_id = await _insert_delivery_row(
        pool,
        event_type=event_type,
        source_butler="travel",
        subscriber_butler="finance",
        status="pending",
        updated_at_ago=timedelta(seconds=0),
    )
    transition = await mark_delivery_failed(
        pool,
        delivery_id,
        "RuntimeError: subscriber intentionally terminal",
        retryable=False,
        max_attempts=5,
    )
    assert transition == "failed_permanent"

    client = _UnexpectedRouteClient()
    result = await fan_out_event(
        pool,
        client,
        event_id=str(event_id),
        event_type=event_type,
        source_butler="travel",
        payload={},
    )

    assert result == {
        "event_id": str(event_id),
        "deliveries": [{"subscriber_butler": "finance", "status": "failed_permanent"}],
    }
    assert client.calls == 0
    assert await _delivery_status(pool, delivery_id) == ("failed_permanent", 1)


async def test_terminal_fence_wins_over_a_concurrent_route_success(pool: asyncpg.Pool) -> None:
    """A stale route success cannot overwrite or misreport a permanent terminal state."""
    event_type = f"terminal_fence.{uuid.uuid4().hex}"
    event_id, delivery_id = await _insert_delivery_row(
        pool,
        event_type=event_type,
        source_butler="travel",
        subscriber_butler="finance",
        status="pending",
        updated_at_ago=timedelta(seconds=0),
    )
    client = _BlockingSuccessClient()
    fanout = asyncio.create_task(
        fan_out_event(
            pool,
            client,
            event_id=str(event_id),
            event_type=event_type,
            source_butler="travel",
            payload={},
        )
    )

    await asyncio.wait_for(client.started.wait(), timeout=5)
    try:
        transition = await mark_delivery_failed(
            pool,
            delivery_id,
            "RuntimeError: permanent terminal fence",
            retryable=False,
            max_attempts=5,
        )
        assert transition == "failed_permanent"
    finally:
        client.release.set()

    result = await asyncio.wait_for(fanout, timeout=5)

    assert result == {
        "event_id": str(event_id),
        "deliveries": [{"subscriber_butler": "finance", "status": "failed_permanent"}],
    }
    assert client.calls == 1
    assert await _delivery_status(pool, delivery_id) == ("failed_permanent", 1)


@pytest.mark.parametrize("winning_status", ["delivered", "failed_permanent"])
async def test_terminal_fence_wins_over_a_concurrent_incomplete_success(
    pool: asyncpg.Pool, winning_status: str
) -> None:
    """An incomplete target result reports the terminal row that won the real-PG race."""
    event_type = f"terminal_incomplete_fence.{uuid.uuid4().hex}"
    event_id, delivery_id = await _insert_delivery_row(
        pool,
        event_type=event_type,
        source_butler="travel",
        subscriber_butler="finance",
        status="pending",
        updated_at_ago=timedelta(seconds=0),
    )
    incomplete_client = _BlockingIncompleteSuccessClient()
    stale_fanout = asyncio.create_task(
        fan_out_event(
            pool,
            incomplete_client,
            event_id=str(event_id),
            event_type=event_type,
            source_butler="travel",
            payload={},
        )
    )

    await asyncio.wait_for(incomplete_client.started.wait(), timeout=5)
    try:
        if winning_status == "delivered":
            winner = await mark_delivery_delivered(
                pool,
                delivery_id,
                task_id=str(uuid.uuid4()),
                task_name="domain-event-incomplete-success-race",
            )
        else:
            winner = await mark_delivery_failed(
                pool,
                delivery_id,
                "RuntimeError: permanent terminal fence",
                retryable=False,
                max_attempts=5,
            )
        assert winner == winning_status
    finally:
        incomplete_client.release.set()

    stale_result = await asyncio.wait_for(stale_fanout, timeout=5)
    assert stale_result["deliveries"][0]["subscriber_butler"] == "finance"
    assert stale_result["deliveries"][0]["status"] == winning_status
    assert stale_result["deliveries"][0]["error"].startswith(
        "receive_domain_event on 'finance' returned an incomplete success payload"
    )
    assert incomplete_client.calls == 1
    expected_attempt_count = 0 if winning_status == "delivered" else 1
    assert await _delivery_status(pool, delivery_id) == (winning_status, expected_attempt_count)


async def test_terminal_fence_wins_over_a_concurrent_task_conflict_and_prevents_replay(
    pool: asyncpg.Pool, monkeypatch
) -> None:
    """A stale task conflict cannot demote a terminal failure or re-enable fan-out."""
    from butlers.core_tools import _domain_events

    event_type = f"terminal_conflict_fence.{uuid.uuid4().hex}"
    event_id, delivery_id = await _insert_delivery_row(
        pool,
        event_type=event_type,
        source_butler="travel",
        subscriber_butler="finance",
        status="pending",
        updated_at_ago=timedelta(seconds=0),
    )
    await pool.execute(
        """
        INSERT INTO scheduled_tasks (name, cron, dispatch_mode, prompt, source, enabled)
        VALUES ($1, '* * * * *', 'prompt', 'an unrelated hand-crafted task', 'db', true)
        """,
        task_name_for(event_id, "finance"),
    )

    metric_calls: list[dict[str, str]] = []
    monkeypatch.setattr(
        _domain_events,
        "record_domain_event_delivery_failed_permanent",
        lambda **kwargs: metric_calls.append(kwargs),
    )
    conflict_client = _BlockingConflictClient(pool)
    permanent_client = _PermanentFailureClient()
    conflict_fanout = asyncio.create_task(
        fan_out_event(
            pool,
            conflict_client,
            event_id=str(event_id),
            event_type=event_type,
            source_butler="travel",
            payload={},
        )
    )

    await asyncio.wait_for(conflict_client.started.wait(), timeout=5)
    try:
        terminal_result = await asyncio.wait_for(
            fan_out_event(
                pool,
                permanent_client,
                event_id=str(event_id),
                event_type=event_type,
                source_butler="travel",
                payload={},
            ),
            timeout=5,
        )
    finally:
        conflict_client.release.set()

    assert terminal_result["deliveries"] == [
        {
            "subscriber_butler": "finance",
            "status": "failed_permanent",
            "error": "RuntimeError: permanent terminal fence",
        }
    ]
    conflict_result = await asyncio.wait_for(conflict_fanout, timeout=5)

    assert conflict_result == {
        "event_id": str(event_id),
        "deliveries": [{"subscriber_butler": "finance", "status": "failed_permanent"}],
    }
    assert conflict_client.calls == 1
    assert await _delivery_status(pool, delivery_id) == ("failed_permanent", 1)

    replay = await fan_out_event(
        pool,
        permanent_client,
        event_id=str(event_id),
        event_type=event_type,
        source_butler="travel",
        payload={},
    )

    assert replay == {
        "event_id": str(event_id),
        "deliveries": [{"subscriber_butler": "finance", "status": "failed_permanent"}],
    }
    assert permanent_client.calls == 1
    assert metric_calls == [
        {
            "source_butler": "travel",
            "destination_butler": "finance",
            "reason": "non_retryable",
        }
    ]


async def test_terminal_fence_wins_over_a_concurrent_stale_route_failure(
    pool: asyncpg.Pool, monkeypatch
) -> None:
    """A stale retryable route failure re-observes the terminal row without a second metric."""
    from butlers.core_tools import _domain_events

    event_type = f"terminal_failure_fence.{uuid.uuid4().hex}"
    event_id, delivery_id = await _insert_delivery_row(
        pool,
        event_type=event_type,
        source_butler="travel",
        subscriber_butler="finance",
        status="pending",
        updated_at_ago=timedelta(seconds=0),
    )

    metric_calls: list[dict[str, str]] = []
    monkeypatch.setattr(
        _domain_events,
        "record_domain_event_delivery_failed_permanent",
        lambda **kwargs: metric_calls.append(kwargs),
    )
    stale_failure_client = _BlockingRetryableFailureClient()
    permanent_client = _PermanentFailureClient()
    stale_fanout = asyncio.create_task(
        fan_out_event(
            pool,
            stale_failure_client,
            event_id=str(event_id),
            event_type=event_type,
            source_butler="travel",
            payload={},
        )
    )

    await asyncio.wait_for(stale_failure_client.started.wait(), timeout=5)
    try:
        terminal_result = await asyncio.wait_for(
            fan_out_event(
                pool,
                permanent_client,
                event_id=str(event_id),
                event_type=event_type,
                source_butler="travel",
                payload={},
            ),
            timeout=5,
        )
    finally:
        stale_failure_client.release.set()

    assert terminal_result["deliveries"] == [
        {
            "subscriber_butler": "finance",
            "status": "failed_permanent",
            "error": "RuntimeError: permanent terminal fence",
        }
    ]
    stale_result = await asyncio.wait_for(stale_fanout, timeout=5)
    assert stale_result == {
        "event_id": str(event_id),
        "deliveries": [
            {
                "subscriber_butler": "finance",
                "status": "failed_permanent",
                "error": "TimeoutError: stale retryable route failure",
            }
        ],
    }
    assert stale_failure_client.calls == 1
    assert permanent_client.calls == 1
    assert await _delivery_status(pool, delivery_id) == ("failed_permanent", 1)
    assert metric_calls == [
        {
            "source_butler": "travel",
            "destination_butler": "finance",
            "reason": "non_retryable",
        }
    ]


async def test_sweep_redrives_a_stale_pending_delivery(pool: asyncpg.Pool, monkeypatch) -> None:
    _patch_switchboard_route(monkeypatch, pool, route_fn=_ok_switchboard_route)
    event_type = f"sweep.stale_pending.{uuid.uuid4().hex}"
    event_id, delivery_id = await _insert_delivery_row(
        pool,
        event_type=event_type,
        source_butler="travel",
        subscriber_butler="finance",
        status="pending",
        updated_at_ago=timedelta(minutes=20),
    )

    result = await run_domain_event_reconciliation_sweep(
        pool, stale_pending_after=timedelta(minutes=10)
    )

    assert result["stale_pending_candidates"] == 1
    assert result["stale_pending_redriven"] == 1
    assert result["stale_pending_delivered"] == 1

    status, _ = await _delivery_status(pool, delivery_id)
    assert status == "delivered"

    count = await pool.fetchval(
        "SELECT count(*) FROM scheduled_tasks WHERE name = $1",
        task_name_for(event_id, "finance"),
    )
    assert count == 1


async def test_sweep_does_not_redrive_a_fresh_in_flight_pending_delivery(
    pool: asyncpg.Pool, monkeypatch
) -> None:
    """A `pending` row claimed moments ago (still comfortably inside a normal
    dispatch's lifetime) must never be treated as stuck."""
    dispatch_calls = 0

    async def _counting_route(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal dispatch_calls
        dispatch_calls += 1
        return await _ok_switchboard_route(*args, **kwargs)

    _patch_switchboard_route(monkeypatch, pool, route_fn=_counting_route)
    event_type = f"sweep.fresh_pending.{uuid.uuid4().hex}"
    _event_id, delivery_id = await _insert_delivery_row(
        pool,
        event_type=event_type,
        source_butler="travel",
        subscriber_butler="finance",
        status="pending",
        updated_at_ago=timedelta(seconds=1),
    )

    result = await run_domain_event_reconciliation_sweep(
        pool, stale_pending_after=timedelta(minutes=10)
    )

    assert result["stale_pending_candidates"] == 0
    assert dispatch_calls == 0
    status, _ = await _delivery_status(pool, delivery_id)
    assert status == "pending"


async def test_sweep_retries_transient_failure_up_to_bound_then_permanent(
    pool: asyncpg.Pool, monkeypatch
) -> None:
    """A `failed` row with a transient (retryable) error keeps retrying, with
    attempt_count climbing each sweep tick, until the retry bound is reached
    -- then it transitions to the terminal `failed_permanent`, never retried
    again."""
    _patch_switchboard_route(monkeypatch, pool, route_fn=_transient_failure_switchboard_route)
    event_type = f"sweep.transient_retry.{uuid.uuid4().hex}"
    _event_id, delivery_id = await _insert_delivery_row(
        pool,
        event_type=event_type,
        source_butler="travel",
        subscriber_butler="finance",
        status="failed",
        updated_at_ago=timedelta(minutes=30),
        attempt_count=0,
    )

    max_attempts = 3
    for expected_attempt in range(1, max_attempts + 1):
        result = await run_domain_event_reconciliation_sweep(
            pool,
            failed_retry_backoff=timedelta(seconds=0),
            max_attempts=max_attempts,
        )
        assert result["failed_retried"] == 1
        status, attempt_count = await _delivery_status(pool, delivery_id)
        assert attempt_count == expected_attempt
        if expected_attempt < max_attempts:
            assert status == "failed"
        else:
            assert status == "failed_permanent"

    # A further sweep tick must not touch the now-terminal row again.
    result = await run_domain_event_reconciliation_sweep(
        pool,
        failed_retry_backoff=timedelta(seconds=0),
        max_attempts=max_attempts,
    )
    assert result["failed_retry_candidates"] == 0
    _, final_attempt_count = await _delivery_status(pool, delivery_id)
    assert final_attempt_count == max_attempts


async def test_sweep_does_not_recount_a_concurrently_won_terminal_transition(
    pool: asyncpg.Pool, monkeypatch, caplog
) -> None:
    """A sweep loser reports the durable terminal row without a duplicate terminal signal."""
    from butlers.core_tools import _domain_events

    route_started = asyncio.Event()
    release_route = asyncio.Event()
    route_calls = 0

    async def _blocking_transient_route(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        nonlocal route_calls
        route_calls += 1
        route_started.set()
        await release_route.wait()
        return {"error": "TimeoutError: stale retryable route failure", "retryable": True}

    _patch_switchboard_route(monkeypatch, pool, route_fn=_blocking_transient_route)
    event_type = f"sweep.fenced_terminal_signal.{uuid.uuid4().hex}"
    event_id, delivery_id = await _insert_delivery_row(
        pool,
        event_type=event_type,
        source_butler="travel",
        subscriber_butler="finance",
        status="failed",
        updated_at_ago=timedelta(days=1),
        attempt_count=4,
    )
    metric_calls: list[dict[str, str]] = []
    monkeypatch.setattr(
        _domain_events,
        "record_domain_event_delivery_failed_permanent",
        lambda **kwargs: metric_calls.append(kwargs),
    )
    permanent_client = _PermanentFailureClient()

    with caplog.at_level(logging.ERROR):
        sweep = asyncio.create_task(
            run_domain_event_reconciliation_sweep(
                pool,
                failed_retry_backoff=timedelta(seconds=0),
                max_attempts=5,
                limit=1,
            )
        )
        await asyncio.wait_for(route_started.wait(), timeout=5)
        try:
            terminal_result = await asyncio.wait_for(
                fan_out_event(
                    pool,
                    permanent_client,
                    event_id=str(event_id),
                    event_type=event_type,
                    source_butler="travel",
                    payload={},
                ),
                timeout=5,
            )
        finally:
            release_route.set()
        sweep_result = await asyncio.wait_for(sweep, timeout=5)

    assert terminal_result["deliveries"][0]["status"] == "failed_permanent"
    assert sweep_result["failed_retry_candidates"] == 1
    assert sweep_result["failed_retried"] == 1
    assert sweep_result["newly_permanently_failed"] == 0
    assert not any(
        "delivery permanently failed after retries" in message for message in caplog.messages
    )
    assert route_calls == 1
    assert permanent_client.calls == 1
    assert await _delivery_status(pool, delivery_id) == ("failed_permanent", 5)
    assert metric_calls == [
        {
            "source_butler": "travel",
            "destination_butler": "finance",
            "reason": "non_retryable",
        }
    ]

    replay = await run_domain_event_reconciliation_sweep(
        pool,
        failed_retry_backoff=timedelta(seconds=0),
        max_attempts=5,
        limit=1,
    )
    assert replay["failed_retry_candidates"] == 0
    assert replay["newly_permanently_failed"] == 0
    assert metric_calls == [
        {
            "source_butler": "travel",
            "destination_butler": "finance",
            "reason": "non_retryable",
        }
    ]


async def test_overlapping_sweeps_do_not_double_progress_a_failed_delivery(
    pool: asyncpg.Pool, monkeypatch
) -> None:
    """A busy sweep owns retry progression until it records its outcome.

    The first route call pauses before ``mark_delivery_failed``.  While it is
    paused, a second sweep must return as skipped instead of dispatching the
    same pre-existing ``failed`` row and consuming a second retry attempt.
    """
    first_dispatch_started = asyncio.Event()
    second_dispatch_started = asyncio.Event()
    release_dispatch = asyncio.Event()
    dispatch_calls = 0

    async def _delayed_transient_route(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        nonlocal dispatch_calls
        dispatch_calls += 1
        if dispatch_calls == 1:
            first_dispatch_started.set()
        else:
            second_dispatch_started.set()
        await release_dispatch.wait()
        return {"error": "TimeoutError: simulated transient failure", "retryable": True}

    _patch_switchboard_route(monkeypatch, pool, route_fn=_delayed_transient_route)
    event_type = f"sweep.overlap.{uuid.uuid4().hex}"
    _event_id, delivery_id = await _insert_delivery_row(
        pool,
        event_type=event_type,
        source_butler="travel",
        subscriber_butler="finance",
        status="failed",
        updated_at_ago=timedelta(minutes=30),
        attempt_count=0,
    )

    first_sweep = asyncio.create_task(
        run_domain_event_reconciliation_sweep(
            pool, failed_retry_backoff=timedelta(seconds=0), max_attempts=5
        )
    )
    await asyncio.wait_for(first_dispatch_started.wait(), timeout=5)

    second_sweep = asyncio.create_task(
        run_domain_event_reconciliation_sweep(
            pool, failed_retry_backoff=timedelta(seconds=0), max_attempts=5
        )
    )
    second_dispatch_wait = asyncio.create_task(second_dispatch_started.wait())
    try:
        done, _pending = await asyncio.wait(
            {second_sweep, second_dispatch_wait}, timeout=5, return_when=asyncio.FIRST_COMPLETED
        )
        assert second_sweep in done, "second sweep dispatched the delivery while the first owned it"
        second_result = second_sweep.result()
        assert second_result["skipped_due_to_active_sweep"] is True
    finally:
        release_dispatch.set()
        second_dispatch_wait.cancel()
        await asyncio.gather(second_dispatch_wait, return_exceptions=True)
        await asyncio.gather(first_sweep, second_sweep, return_exceptions=True)

    first_result = first_sweep.result()
    assert first_result["failed_retried"] == 1
    assert dispatch_calls == 1
    status, attempt_count = await _delivery_status(pool, delivery_id)
    assert status == "failed"
    assert attempt_count == 1
    # This module-scoped migrated database is shared with the later permanent-
    # route-error test. Remove the intentionally retryable fixture row after
    # proving its single-attempt result so a later zero-backoff sweep cannot
    # select it as unrelated work.
    await pool.execute("DELETE FROM public.domain_event_deliveries WHERE id = $1", delivery_id)


async def test_sweep_marks_permanent_route_error_failed_permanent_immediately(
    pool: asyncpg.Pool, monkeypatch
) -> None:
    """A route error classified permanent (e.g. the subscriber lacks the
    domain_events core group) must transition straight to `failed_permanent`
    on its very next retry -- it must never wait for the attempt bound, and
    it must be visible via the dashboard-facing status filter."""
    _patch_switchboard_route(monkeypatch, pool, route_fn=_permanent_failure_switchboard_route)
    event_type = f"sweep.permanent_failure.{uuid.uuid4().hex}"
    _event_id, delivery_id = await _insert_delivery_row(
        pool,
        event_type=event_type,
        source_butler="travel",
        subscriber_butler="health",
        status="failed",
        updated_at_ago=timedelta(minutes=30),
        attempt_count=1,
    )

    result = await run_domain_event_reconciliation_sweep(
        pool, failed_retry_backoff=timedelta(seconds=0), max_attempts=5
    )

    assert result["newly_permanently_failed"] == 1
    status, attempt_count = await _delivery_status(pool, delivery_id)
    assert status == "failed_permanent"
    assert attempt_count == 2

    _total, rows = await list_recent_deliveries(
        pool, subscriber_butler="health", status="failed_permanent"
    )
    assert any(row["id"] == delivery_id for row in rows)


async def test_sweep_is_idempotent_when_run_concurrently_with_live_fanout(
    pool: asyncpg.Pool, monkeypatch
) -> None:
    """The sweep must be safe to run concurrently with a live in-flight
    fan-out for the same delivery: claim_delivery's idempotent claim/
    re-observe plus the subscriber's deterministic-task-name reconciliation
    must converge to exactly one delivered row and one scheduled_task, never
    a duplicate or corrupted outcome."""
    _patch_switchboard_route(monkeypatch, pool, route_fn=_ok_switchboard_route)
    event_type = f"sweep.concurrent.{uuid.uuid4().hex}"
    event_id, delivery_id = await _insert_delivery_row(
        pool,
        event_type=event_type,
        source_butler="travel",
        subscriber_butler="finance",
        status="pending",
        updated_at_ago=timedelta(minutes=20),
    )

    client = _ReceivingClient(pool)

    results = await asyncio.gather(
        run_domain_event_reconciliation_sweep(pool, stale_pending_after=timedelta(minutes=10)),
        fan_out_event(
            pool,
            client,
            event_id=str(event_id),
            event_type=event_type,
            source_butler="travel",
            payload={},
        ),
    )
    assert results[0]["stale_pending_candidates"] == 1

    status, _ = await _delivery_status(pool, delivery_id)
    assert status == "delivered"

    delivery_count = await pool.fetchval(
        "SELECT count(*) FROM public.domain_event_deliveries WHERE event_id = $1", event_id
    )
    assert delivery_count == 1

    task_count = await pool.fetchval(
        "SELECT count(*) FROM scheduled_tasks WHERE name = $1",
        task_name_for(event_id, "finance"),
    )
    assert task_count == 1


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
