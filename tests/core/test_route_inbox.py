"""Unit tests for route_inbox durable work queue (butlers-963.6).

Covers:
- route_inbox_insert: persist a row in 'accepted' state
- route_inbox_mark_processing / mark_processed / mark_errored: lifecycle transitions
- route_inbox_scan_unprocessed: find stuck rows
- route_inbox_recovery_sweep: dispatch_fn called for each stuck row
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.core.route_inbox import (
    STATE_ACCEPTED,
    STATE_ERRORED,
    STATE_PROCESSED,
    STATE_PROCESSING,
    route_inbox_insert,
    route_inbox_mark_errored,
    route_inbox_mark_processed,
    route_inbox_mark_processing,
    route_inbox_recovery_sweep,
    route_inbox_scan_unprocessed,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pool() -> Any:
    """Return an async mock pool that provides an async context manager for acquire()."""
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


# ---------------------------------------------------------------------------
# State transition tests (insert + lifecycle mutations)
# ---------------------------------------------------------------------------


class TestRouteInboxInsert:
    """Tests for the insert function."""

    async def test_insert_behavior(self) -> None:
        """route_inbox_insert returns UUID; executes INSERT with accepted state; serializes envelope."""
        import json

        pool, conn = _make_pool()
        conn.execute = AsyncMock()
        envelope = _sample_envelope()

        result = await route_inbox_insert(pool, route_envelope=envelope)

        assert isinstance(result, uuid.UUID)
        conn.execute.assert_awaited_once()
        call_args = conn.execute.call_args
        sql = call_args.args[0]
        assert "INSERT INTO route_inbox" in sql
        assert call_args.args[3] == STATE_ACCEPTED
        parsed = json.loads(call_args.args[2])
        assert parsed["schema_version"] == "route.v1"
        assert parsed["input"]["prompt"] == "Run a health check."


class TestRouteInboxLifecycleMutations:
    """Tests for mark_processing, mark_processed, and mark_errored state transitions."""

    async def test_lifecycle_transitions(self) -> None:
        """mark_processing/mark_processed/mark_errored each execute correct UPDATE SQL."""
        row_id = uuid.uuid4()
        session_id = uuid.uuid4()

        # mark_processing: transitions accepted → processing
        pool, conn = _make_pool()
        conn.execute = AsyncMock()
        await route_inbox_mark_processing(pool, row_id)
        conn.execute.assert_awaited_once()
        call_args = conn.execute.call_args
        assert "UPDATE route_inbox" in call_args.args[0]
        assert STATE_PROCESSING in call_args.args
        assert row_id in call_args.args
        assert STATE_ACCEPTED in call_args.args

        # mark_processed: stores session_id; None session_id accepted
        pool2, conn2 = _make_pool()
        conn2.execute = AsyncMock()
        await route_inbox_mark_processed(pool2, row_id, session_id)
        conn2.execute.assert_awaited_once()
        call_args2 = conn2.execute.call_args
        assert "UPDATE route_inbox" in call_args2.args[0]
        assert STATE_PROCESSED in call_args2.args
        assert session_id in call_args2.args
        conn2.execute.reset_mock()
        await route_inbox_mark_processed(pool2, row_id, None)
        conn2.execute.assert_awaited_once()

        # mark_errored: stores error message
        pool3, conn3 = _make_pool()
        conn3.execute = AsyncMock()
        error = "TimeoutError: spawner timed out"
        await route_inbox_mark_errored(pool3, row_id, error)
        conn3.execute.assert_awaited_once()
        call_args3 = conn3.execute.call_args
        assert "UPDATE route_inbox" in call_args3.args[0]
        assert STATE_ERRORED in call_args3.args
        assert error in call_args3.args
        assert row_id in call_args3.args


# ---------------------------------------------------------------------------
# route_inbox_scan_unprocessed
# ---------------------------------------------------------------------------


class TestRouteInboxScanUnprocessed:
    """Tests for the scanner that finds stuck rows."""

    async def test_scan_empty_and_with_rows(self) -> None:
        """Empty when no rows; returns dicts with row fields when rows exist."""
        pool, conn = _make_pool()
        conn.fetch = AsyncMock(return_value=[])
        assert await route_inbox_scan_unprocessed(pool, grace_s=10, batch_size=50) == []

        # With one row
        row_id = uuid.uuid4()
        now = datetime.now(UTC)
        conn.fetch = AsyncMock(return_value=[{
            "id": row_id, "received_at": now,
            "route_envelope": {"schema_version": "route.v1", "input": {"prompt": "test"}},
        }])
        result = await route_inbox_scan_unprocessed(pool, grace_s=10, batch_size=50)
        assert len(result) == 1
        assert result[0]["id"] == row_id and result[0]["received_at"] == now

    async def test_scan_query_parameters(self) -> None:
        """grace_s and batch_size forwarded to query; filter includes accepted+processing states."""
        pool, conn = _make_pool()
        conn.fetch = AsyncMock(return_value=[])

        await route_inbox_scan_unprocessed(pool, grace_s=42, batch_size=7)
        call_args = conn.fetch.call_args
        assert 42 in call_args.args and 7 in call_args.args

        # States filter
        await route_inbox_scan_unprocessed(pool)
        states_arg = conn.fetch.call_args.args[1]
        assert STATE_ACCEPTED in states_arg and STATE_PROCESSING in states_arg


# ---------------------------------------------------------------------------
# route_inbox_recovery_sweep
# ---------------------------------------------------------------------------


class TestRouteInboxRecoverySweep:
    """Tests for crash recovery sweep."""

    async def test_recovery_dispatch_and_count(self) -> None:
        """dispatch_fn called per row; returns 0 when no rows; returns count of successes."""
        pool, conn = _make_pool()
        now = datetime.now(UTC)

        # Zero when no rows
        conn.fetch = AsyncMock(return_value=[])
        dispatch_fn = AsyncMock()
        assert await route_inbox_recovery_sweep(pool, dispatch_fn=dispatch_fn) == 0
        dispatch_fn.assert_not_awaited()

        # One row → dispatched once, count=1
        row_id = uuid.uuid4()
        conn.fetch = AsyncMock(return_value=[{
            "id": row_id, "received_at": now.replace(tzinfo=None),
            "route_envelope": {"schema_version": "route.v1", "input": {"prompt": "hi"}},
        }])
        dispatch_calls: list[dict] = []
        async def collect_dispatch(*, row_id: uuid.UUID, route_envelope: dict) -> None:
            dispatch_calls.append({"row_id": row_id})
        recovered = await route_inbox_recovery_sweep(pool, dispatch_fn=collect_dispatch, grace_s=10, batch_size=50)
        assert recovered == 1 and len(dispatch_calls) == 1 and dispatch_calls[0]["row_id"] == row_id

    async def test_recovery_continues_on_failure(self) -> None:
        """Failure in one dispatch row does not stop recovery; count excludes failed rows."""
        pool, conn = _make_pool()
        now = datetime.now(UTC)
        rows = [
            {"id": uuid.uuid4(), "received_at": now.replace(tzinfo=None),
             "route_envelope": {"schema_version": "route.v1", "input": {"prompt": f"msg{i}"}}}
            for i in range(3)
        ]
        conn.fetch = AsyncMock(return_value=rows)

        call_count = 0
        async def dispatch_fn(*, row_id: uuid.UUID, route_envelope: dict) -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("simulated dispatch failure")

        recovered = await route_inbox_recovery_sweep(pool, dispatch_fn=dispatch_fn)
        assert recovered == 2 and call_count == 3
