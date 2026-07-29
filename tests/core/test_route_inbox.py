"""Unit tests for route_inbox durable work queue (butlers-963.6) — condensed."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.core import route_inbox as route_inbox_module
from butlers.core.route_inbox import (
    STATE_ACCEPTED,
    STATE_ERRORED,
    STATE_PROCESSED,
    STATE_PROCESSING,
    RouteInboxLeaseLost,
    route_inbox_insert,
    route_inbox_insert_on_connection,
    route_inbox_mark_errored,
    route_inbox_mark_processed,
    route_inbox_mark_processing,
    route_inbox_processing_lease_heartbeat,
    route_inbox_recovery_sweep,
    route_inbox_scan_unprocessed,
    route_inbox_wait_while_claimed,
)

pytestmark = pytest.mark.unit


def _make_pool() -> Any:
    pool = AsyncMock()
    conn = AsyncMock()
    pool.acquire = MagicMock(
        return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=conn),
            __aexit__=AsyncMock(return_value=False),
        )
    )
    return pool, conn


def _sample_envelope() -> dict:
    return {
        "schema_version": "route.v1",
        "request_context": {
            "request_id": "018f6f4e-5b3b-7b2d-9c2f-7b7b6b6b6b6b",
            "received_at": "2026-02-18T10:00:00Z",
            "source_channel": "telegram_bot",
            "source_endpoint_identity": "switchboard",
            "source_sender_identity": "health",
        },
        "input": {"prompt": "Run a health check."},
    }


async def test_insert_and_lifecycle_mutations() -> None:
    """insert returns UUID; INSERT with accepted state; mark_processing/processed/errored
    each correct."""
    # Insert: returns a UUID and inserts in the accepted state
    pool, conn = _make_pool()
    conn.execute = AsyncMock()
    result = await route_inbox_insert(pool, route_envelope=_sample_envelope())
    assert isinstance(result, uuid.UUID)
    assert conn.execute.call_args.args[3] == STATE_ACCEPTED
    # route_envelope is passed as a dict (asyncpg JSONB codec handles encoding)
    assert conn.execute.call_args.args[2]["schema_version"] == "route.v1"

    # A caller-owned connection is required when a route-inbox write must be
    # atomic with another durable control-plane transition.
    connection_id = await route_inbox_insert_on_connection(
        conn,
        route_envelope=_sample_envelope(),
    )
    assert isinstance(connection_id, uuid.UUID)
    assert conn.execute.call_args.args[3] == STATE_ACCEPTED

    row_id = uuid.uuid4()
    session_id = uuid.uuid4()

    # mark_processing: transitions accepted -> processing for the row
    pool2, conn2 = _make_pool()
    processing_claim_id = uuid.uuid4()
    conn2.fetchval = AsyncMock(return_value=processing_claim_id)
    assert await route_inbox_mark_processing(pool2, row_id) is True
    args = conn2.fetchval.call_args.args
    assert STATE_PROCESSING in args and row_id in args and STATE_ACCEPTED in args

    # mark_processed (with and without session_id)
    pool3, conn3 = _make_pool()
    conn3.fetchval = AsyncMock(return_value=row_id)
    assert await route_inbox_mark_processed(pool3, row_id, session_id) is True
    assert (
        STATE_PROCESSED in conn3.fetchval.call_args.args
        and session_id in conn3.fetchval.call_args.args
    )
    conn3.fetchval.reset_mock()
    await route_inbox_mark_processed(pool3, row_id, None)
    conn3.fetchval.assert_awaited_once()

    # mark_errored: records the errored state and the error text
    pool4, conn4 = _make_pool()
    conn4.fetchval = AsyncMock(return_value=row_id)
    error = "TimeoutError: spawner timed out"
    assert await route_inbox_mark_errored(pool4, row_id, error) is True
    args4 = conn4.fetchval.call_args.args
    assert STATE_ERRORED in args4 and error in args4 and row_id in args4


async def test_terminal_writes_are_monotonic_after_the_first_processing_settlement() -> None:
    """An uncertain DB response cannot turn a committed success into an error."""

    row_id = uuid.uuid4()
    claim_id = uuid.uuid4()

    class _MonotonicConnection:
        lifecycle_state = STATE_PROCESSING

        async def fetchval(self, query: str, new_state: str, *_args: object) -> uuid.UUID | None:
            # The database predicate is the actual compare-and-set boundary;
            # a claim id alone remains valid after the row becomes terminal.
            assert "AND lifecycle_state = $5" in query
            if self.lifecycle_state != STATE_PROCESSING:
                return None
            self.lifecycle_state = new_state
            return row_id

    conn = _MonotonicConnection()
    pool = MagicMock()
    pool.acquire = MagicMock(
        return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=conn),
            __aexit__=AsyncMock(return_value=False),
        )
    )

    assert await route_inbox_mark_processed(
        pool,
        row_id,
        uuid.uuid4(),
        processing_claim_id=claim_id,
    )
    assert not await route_inbox_mark_errored(
        pool,
        row_id,
        "response lost after committed success",
        processing_claim_id=claim_id,
    )
    assert conn.lifecycle_state == STATE_PROCESSED


async def test_lease_loss_cancels_a_live_invocation_before_recovery_can_replay() -> None:
    """A local worker must not keep invoking after it loses its route lease."""

    pool, conn = _make_pool()
    row_id = uuid.uuid4()
    claim_id = uuid.uuid4()
    conn.fetchval = AsyncMock(return_value=row_id)
    lease_lost = asyncio.Event()
    started = asyncio.Event()
    stopped = asyncio.Event()
    never = asyncio.Event()

    async def live_invocation() -> None:
        started.set()
        try:
            await never.wait()
        finally:
            stopped.set()

    waiter = asyncio.create_task(
        route_inbox_wait_while_claimed(pool, row_id, claim_id, lease_lost, live_invocation)
    )
    await started.wait()
    lease_lost.set()

    with pytest.raises(RouteInboxLeaseLost):
        await waiter
    assert stopped.is_set()


async def test_lease_loss_before_start_does_not_construct_an_invocation() -> None:
    """A known-lost claim must not create work that cannot be safely owned."""

    pool, _ = _make_pool()
    row_id = uuid.uuid4()
    claim_id = uuid.uuid4()
    lease_lost = asyncio.Event()
    lease_lost.set()
    invoked = False

    async def invocation() -> None:
        nonlocal invoked
        invoked = True

    with pytest.raises(RouteInboxLeaseLost):
        await route_inbox_wait_while_claimed(pool, row_id, claim_id, lease_lost, invocation)

    assert invoked is False


async def test_processing_lease_heartbeat_refuses_lost_claim_before_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A displaced claim cannot enter protected pre-spawn work."""

    pool, _ = _make_pool()
    row_id = uuid.uuid4()
    claim_id = uuid.uuid4()
    monkeypatch.setattr(
        route_inbox_module,
        "route_inbox_renew_processing_claim",
        AsyncMock(return_value=False),
    )

    entered = False
    with pytest.raises(RouteInboxLeaseLost, match="before protected work"):
        async with route_inbox_processing_lease_heartbeat(pool, row_id, claim_id):
            entered = True

    assert entered is False


async def test_wait_while_claimed_fences_database_ownership_before_invocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale in-memory event cannot bypass the authoritative claim fence."""

    pool, _ = _make_pool()
    row_id = uuid.uuid4()
    claim_id = uuid.uuid4()
    monkeypatch.setattr(
        route_inbox_module,
        "route_inbox_renew_processing_claim",
        AsyncMock(return_value=False),
    )

    invoked = False

    async def invocation() -> None:
        nonlocal invoked
        invoked = True

    with pytest.raises(RouteInboxLeaseLost, match="before invocation"):
        await route_inbox_wait_while_claimed(
            pool,
            row_id,
            claim_id,
            asyncio.Event(),
            invocation,
        )

    assert invoked is False


async def test_lease_loss_dominates_runtime_cleanup_failure() -> None:
    """Cancellation cleanup cannot fall through to a generic queue settlement."""

    pool, conn = _make_pool()
    row_id = uuid.uuid4()
    claim_id = uuid.uuid4()
    conn.fetchval = AsyncMock(return_value=row_id)
    lease_lost = asyncio.Event()
    started = asyncio.Event()
    never = asyncio.Event()

    async def invocation() -> None:
        started.set()
        try:
            await never.wait()
        except asyncio.CancelledError as exc:
            raise RuntimeError("runtime cleanup failed") from exc

    waiter = asyncio.create_task(
        route_inbox_wait_while_claimed(pool, row_id, claim_id, lease_lost, invocation)
    )
    await started.wait()
    lease_lost.set()

    with pytest.raises(RouteInboxLeaseLost):
        await waiter


async def test_scan_and_recovery_sweep() -> None:
    """scan: empty/with rows/grace_s+batch_size params/states filter; recovery:
    count/dispatch/continue on failure."""
    # Empty scan
    pool, conn = _make_pool()
    conn.fetch = AsyncMock(return_value=[])
    assert await route_inbox_scan_unprocessed(pool, grace_s=10, batch_size=50) == []

    # Scan with one row
    row_id = uuid.uuid4()
    now = datetime.now(UTC)
    conn.fetch = AsyncMock(
        return_value=[
            {
                "id": row_id,
                "received_at": now,
                "route_envelope": {"schema_version": "route.v1", "input": {"prompt": "test"}},
            }
        ]
    )
    result = await route_inbox_scan_unprocessed(pool, grace_s=10, batch_size=50)
    assert len(result) == 1 and result[0]["id"] == row_id

    # Parameters forwarded to query
    conn.fetch = AsyncMock(return_value=[])
    await route_inbox_scan_unprocessed(pool, grace_s=42, batch_size=7)
    assert 42 in conn.fetch.call_args.args and 7 in conn.fetch.call_args.args

    # The stale-candidate filter includes accepted + processing separately.
    await route_inbox_scan_unprocessed(pool)
    assert conn.fetch.call_args.args[1] == STATE_ACCEPTED
    assert conn.fetch.call_args.args[2] == STATE_PROCESSING

    # Recovery: zero when no rows
    pool2, conn2 = _make_pool()
    conn2.fetch = AsyncMock(return_value=[])
    dispatch = AsyncMock()
    assert await route_inbox_recovery_sweep(pool2, dispatch_fn=dispatch) == 0
    dispatch.assert_not_awaited()

    # Recovery: one row dispatched, count=1
    rr_id = uuid.uuid4()
    conn2.fetch = AsyncMock(
        return_value=[
            {
                "id": rr_id,
                "received_at": now.replace(tzinfo=None),
                "route_envelope": {"schema_version": "route.v1", "input": {"prompt": "hi"}},
            }
        ]
    )
    claim_id = uuid.uuid4()
    conn2.fetchval = AsyncMock(return_value=claim_id)
    dispatch_calls: list[dict] = []

    async def collect_dispatch(
        *,
        row_id: uuid.UUID,
        route_envelope: dict,
        processing_claim_id: uuid.UUID,
        recovery_from_processing: bool,
    ) -> None:
        dispatch_calls.append(
            {
                "row_id": row_id,
                "processing_claim_id": processing_claim_id,
                "recovery_from_processing": recovery_from_processing,
            }
        )

    recovered = await route_inbox_recovery_sweep(
        pool2, dispatch_fn=collect_dispatch, grace_s=10, batch_size=50
    )
    assert recovered == 1 and dispatch_calls[0]["row_id"] == rr_id
    assert dispatch_calls[0]["processing_claim_id"] == claim_id
    assert dispatch_calls[0]["recovery_from_processing"] is False

    # A stale processing row has already crossed the runtime handoff boundary.
    # Its dispatcher needs that fact to avoid a dashboard replay.
    conn2.fetch = AsyncMock(
        return_value=[
            {
                "id": rr_id,
                "received_at": now.replace(tzinfo=None),
                "route_envelope": {"schema_version": "route.v1", "input": {"prompt": "hi"}},
                "lifecycle_state": STATE_PROCESSING,
            }
        ]
    )
    stale_claim_id = uuid.uuid4()
    conn2.fetchval = AsyncMock(return_value=stale_claim_id)
    recovered_processing = await route_inbox_recovery_sweep(
        pool2, dispatch_fn=collect_dispatch, grace_s=10, batch_size=50
    )
    assert recovered_processing == 1
    assert dispatch_calls[-1] == {
        "row_id": rr_id,
        "processing_claim_id": stale_claim_id,
        "recovery_from_processing": True,
    }

    # Recovery: continues on failure; count excludes failed rows
    rows = [
        {
            "id": uuid.uuid4(),
            "received_at": now.replace(tzinfo=None),
            "route_envelope": {"schema_version": "route.v1", "input": {"prompt": f"msg{i}"}},
        }
        for i in range(3)
    ]
    conn2.fetch = AsyncMock(return_value=rows)
    conn2.fetchval = AsyncMock(return_value=uuid.uuid4())
    call_count = 0

    async def dispatch_fn_fail(
        *,
        row_id: uuid.UUID,
        route_envelope: dict,
        processing_claim_id: uuid.UUID,
        recovery_from_processing: bool,
    ) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise RuntimeError("simulated failure")

    recovered2 = await route_inbox_recovery_sweep(pool2, dispatch_fn=dispatch_fn_fail)
    assert recovered2 == 2 and call_count == 3

    # A concurrent hot/recovery worker that won the lease suppresses replay.
    conn2.fetch = AsyncMock(return_value=rows[:1])
    conn2.fetchval = AsyncMock(return_value=None)
    skipped_dispatch = AsyncMock()
    assert await route_inbox_recovery_sweep(pool2, dispatch_fn=skipped_dispatch) == 0
    skipped_dispatch.assert_not_awaited()
