"""Smoke tests — route_inbox crash recovery (bu-dl98i.5.4).

Covers three behavioral contracts for the durable work queue:

1. ``route_inbox_scan_unprocessed`` returns rows in ``'accepted'`` and
   ``'processing'`` states older than the grace period.
2. ``route_inbox_recovery_sweep`` dispatches exactly once per stuck row and
   returns the recovered count.
3. A row dispatched by the sweep can reach a terminal state (``'processed'``).

No Docker required — all DB interactions are mocked via asyncpg pool stubs.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.core.route_inbox import (
    STATE_ACCEPTED,
    STATE_PROCESSED,
    STATE_PROCESSING,
    route_inbox_mark_processed,
    route_inbox_recovery_sweep,
    route_inbox_scan_unprocessed,
)

pytestmark = pytest.mark.smoke


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pool() -> tuple[Any, Any]:
    """Build a minimal asyncpg pool mock (no real DB)."""
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value=None)
    conn.fetch = AsyncMock(return_value=[])

    pool = MagicMock()
    pool.acquire = MagicMock(
        return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=conn),
            __aexit__=AsyncMock(return_value=False),
        )
    )
    return pool, conn


def _stub_row(row_id: uuid.UUID | None = None) -> dict:
    """Build a stub route_inbox row as asyncpg would return it."""
    return {
        "id": row_id or uuid.uuid4(),
        # Naive UTC datetime; route_inbox_recovery_sweep calls .replace(tzinfo=UTC).
        "received_at": datetime.now(UTC).replace(tzinfo=None),
        "route_envelope": {"schema_version": "route.v1", "input": {"prompt": "recover me"}},
    }


# ---------------------------------------------------------------------------
# Contract 1: scan returns rows in 'accepted' and 'processing' states
# ---------------------------------------------------------------------------


async def test_scan_returns_accepted_and_processing_rows() -> None:
    """scan_unprocessed includes both 'accepted' and 'processing' rows.

    A daemon crash or graceful shutdown (which cancels in-flight background tasks)
    can leave rows in either state with no task to complete them.  The scanner
    must include both so every stuck row is recovered.
    """
    pool, conn = _make_pool()
    accepted_id = uuid.uuid4()
    processing_id = uuid.uuid4()

    conn.fetch = AsyncMock(return_value=[_stub_row(accepted_id), _stub_row(processing_id)])

    results = await route_inbox_scan_unprocessed(pool, grace_s=10, batch_size=50)

    assert len(results) == 2, "scan must return all rows past the grace period"
    returned_ids = {r["id"] for r in results}
    assert accepted_id in returned_ids
    assert processing_id in returned_ids

    # Each row must carry the full payload needed for recovery dispatch without
    # a second DB round-trip: received_at (for age-gating) and route_envelope
    # (for re-dispatching the original request).
    for row in results:
        assert "received_at" in row, (
            "scan must return rows with 'received_at' so the recovery sweep can "
            "verify the row age without an additional DB query"
        )
        assert "route_envelope" in row, (
            "scan must return rows with 'route_envelope' so the recovery sweep can "
            "re-dispatch the original request without a second DB read"
        )

    # Verify the SQL query filters by both lifecycle states.
    sql_args = conn.fetch.call_args.args
    states_arg = sql_args[1]  # second positional arg: the states list $1
    assert STATE_ACCEPTED in states_arg, "scan must query 'accepted' rows"
    assert STATE_PROCESSING in states_arg, "scan must query 'processing' rows"


async def test_scan_returns_empty_when_no_stuck_rows() -> None:
    """scan_unprocessed returns an empty list when no rows are past the grace period."""
    pool, conn = _make_pool()
    conn.fetch = AsyncMock(return_value=[])

    results = await route_inbox_scan_unprocessed(pool, grace_s=10, batch_size=50)

    assert results == [], "scan must return empty list when there are no stuck rows"


# ---------------------------------------------------------------------------
# Contract 2: recovery_sweep dispatches once per row, returns recovered count
# ---------------------------------------------------------------------------


async def test_recovery_sweep_dispatches_once_per_row() -> None:
    """recovery_sweep calls dispatch_fn exactly once for each stuck row.

    This is the core delivery guarantee: no row is silently skipped and no row
    receives more than one recovery dispatch per sweep.
    """
    pool, conn = _make_pool()
    row_ids = [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]
    conn.fetch = AsyncMock(return_value=[_stub_row(rid) for rid in row_ids])

    dispatched: list[uuid.UUID] = []

    async def dispatch_fn(*, row_id: uuid.UUID, route_envelope: dict) -> None:
        dispatched.append(row_id)

    recovered = await route_inbox_recovery_sweep(pool, dispatch_fn=dispatch_fn, grace_s=0)

    assert recovered == 3, "recovery_sweep must return the number of dispatched rows"
    assert len(dispatched) == 3, "dispatch_fn must be called once per stuck row"
    assert set(dispatched) == set(row_ids), "dispatch_fn must receive each row's id"


async def test_recovery_sweep_returns_zero_when_no_rows() -> None:
    """recovery_sweep returns 0 and never calls dispatch_fn when there are no stuck rows."""
    pool, conn = _make_pool()
    conn.fetch = AsyncMock(return_value=[])
    dispatch = AsyncMock()

    recovered = await route_inbox_recovery_sweep(pool, dispatch_fn=dispatch, grace_s=0)

    assert recovered == 0
    dispatch.assert_not_awaited()


async def test_recovery_sweep_counts_only_successful_dispatches() -> None:
    """recovery_sweep excludes failed dispatches from the recovered count.

    A transient dispatch failure (e.g. spawner timeout) must not prevent the
    remaining rows from being re-dispatched.
    """
    pool, conn = _make_pool()
    row_ids = [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]
    conn.fetch = AsyncMock(return_value=[_stub_row(rid) for rid in row_ids])

    call_count = 0

    async def dispatch_fn_one_fail(*, row_id: uuid.UUID, route_envelope: dict) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise RuntimeError("simulated transient dispatch failure")

    recovered = await route_inbox_recovery_sweep(pool, dispatch_fn=dispatch_fn_one_fail, grace_s=0)

    assert call_count == 3, "dispatch_fn must be attempted for all rows even after a failure"
    assert recovered == 2, "only successful dispatches count toward the recovered total"


# ---------------------------------------------------------------------------
# Contract 3: recovered row reaches a terminal state
# ---------------------------------------------------------------------------


async def test_recovered_row_reaches_terminal_state() -> None:
    """dispatch_fn invoked by recovery_sweep can transition the row to 'processed'.

    The contract: after dispatch_fn calls mark_processed, the row's lifecycle_state
    is written to the DB as 'processed' (a terminal state).  This test verifies the
    full path: scan → dispatch → mark_processed → DB write.
    """
    pool, conn = _make_pool()
    row_id = uuid.uuid4()
    session_id = uuid.uuid4()

    conn.fetch = AsyncMock(return_value=[_stub_row(row_id)])
    conn.execute = AsyncMock(return_value=None)

    terminal_reached = False

    async def dispatch_fn(*, row_id: uuid.UUID, route_envelope: dict) -> None:
        nonlocal terminal_reached
        # Successful dispatch: mark the row as processed (terminal state).
        await route_inbox_mark_processed(pool, row_id, session_id)
        terminal_reached = True

    recovered = await route_inbox_recovery_sweep(pool, dispatch_fn=dispatch_fn, grace_s=0)

    assert recovered == 1
    assert terminal_reached, "dispatch_fn must have been called and reached terminal logic"

    # mark_processed must have written the terminal state to the DB.
    assert conn.execute.called, "mark_processed must issue a DB write"
    # First positional argument to UPDATE must be STATE_PROCESSED.
    assert conn.execute.call_args.args[1] == STATE_PROCESSED, (
        f"DB write must set lifecycle_state to {STATE_PROCESSED!r}, "
        f"got {conn.execute.call_args.args[1]!r}"
    )
