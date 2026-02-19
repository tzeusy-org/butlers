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
            "source_channel": "telegram",
            "source_endpoint_identity": "switchboard",
            "source_sender_identity": "health",
        },
        "input": {"prompt": "Run a health check."},
    }


# ---------------------------------------------------------------------------
# route_inbox_insert
# ---------------------------------------------------------------------------


class TestRouteInboxInsert:
    """Tests for the insert function."""

    async def test_insert_returns_uuid(self) -> None:
        """route_inbox_insert returns a UUID."""
        pool, conn = _make_pool()
        conn.execute = AsyncMock()

        result = await route_inbox_insert(pool, route_envelope=_sample_envelope())

        assert isinstance(result, uuid.UUID)

    async def test_insert_executes_sql(self) -> None:
        """route_inbox_insert executes an INSERT with 'accepted' state."""
        pool, conn = _make_pool()
        conn.execute = AsyncMock()

        await route_inbox_insert(pool, route_envelope=_sample_envelope())

        conn.execute.assert_awaited_once()
        call_args = conn.execute.call_args
        sql = call_args.args[0]
        assert "INSERT INTO route_inbox" in sql
        # Third positional arg should be STATE_ACCEPTED
        assert call_args.args[3] == STATE_ACCEPTED

    async def test_insert_passes_json_envelope(self) -> None:
        """route_inbox_insert serializes the envelope to JSON."""
        import json

        pool, conn = _make_pool()
        conn.execute = AsyncMock()
        envelope = _sample_envelope()

        await route_inbox_insert(pool, route_envelope=envelope)

        call_args = conn.execute.call_args
        # Second positional arg is the JSON string
        json_str = call_args.args[2]
        parsed = json.loads(json_str)
        assert parsed["schema_version"] == "route.v1"
        assert parsed["input"]["prompt"] == "Run a health check."


# ---------------------------------------------------------------------------
# route_inbox_mark_processing
# ---------------------------------------------------------------------------


class TestRouteInboxMarkProcessing:
    """Tests for the processing state transition."""

    async def test_mark_processing_updates_row(self) -> None:
        """route_inbox_mark_processing executes UPDATE to 'processing'."""
        pool, conn = _make_pool()
        conn.execute = AsyncMock()
        row_id = uuid.uuid4()

        await route_inbox_mark_processing(pool, row_id)

        conn.execute.assert_awaited_once()
        call_args = conn.execute.call_args
        sql = call_args.args[0]
        assert "UPDATE route_inbox" in sql
        # Check state values in call
        assert STATE_PROCESSING in call_args.args
        assert row_id in call_args.args
        assert STATE_ACCEPTED in call_args.args


# ---------------------------------------------------------------------------
# route_inbox_mark_processed
# ---------------------------------------------------------------------------


class TestRouteInboxMarkProcessed:
    """Tests for the processed state transition."""

    async def test_mark_processed_updates_row(self) -> None:
        """route_inbox_mark_processed updates state and session_id."""
        pool, conn = _make_pool()
        conn.execute = AsyncMock()
        row_id = uuid.uuid4()
        session_id = uuid.uuid4()

        await route_inbox_mark_processed(pool, row_id, session_id)

        conn.execute.assert_awaited_once()
        call_args = conn.execute.call_args
        sql = call_args.args[0]
        assert "UPDATE route_inbox" in sql
        assert STATE_PROCESSED in call_args.args
        assert session_id in call_args.args
        assert row_id in call_args.args

    async def test_mark_processed_with_none_session_id(self) -> None:
        """route_inbox_mark_processed accepts None session_id."""
        pool, conn = _make_pool()
        conn.execute = AsyncMock()
        row_id = uuid.uuid4()

        await route_inbox_mark_processed(pool, row_id, None)

        conn.execute.assert_awaited_once()


# ---------------------------------------------------------------------------
# route_inbox_mark_errored
# ---------------------------------------------------------------------------


class TestRouteInboxMarkErrored:
    """Tests for the errored state transition."""

    async def test_mark_errored_updates_row_with_error(self) -> None:
        """route_inbox_mark_errored stores error message."""
        pool, conn = _make_pool()
        conn.execute = AsyncMock()
        row_id = uuid.uuid4()
        error = "TimeoutError: spawner timed out"

        await route_inbox_mark_errored(pool, row_id, error)

        conn.execute.assert_awaited_once()
        call_args = conn.execute.call_args
        sql = call_args.args[0]
        assert "UPDATE route_inbox" in sql
        assert STATE_ERRORED in call_args.args
        assert error in call_args.args
        assert row_id in call_args.args


# ---------------------------------------------------------------------------
# route_inbox_scan_unprocessed
# ---------------------------------------------------------------------------


class TestRouteInboxScanUnprocessed:
    """Tests for the scanner that finds stuck rows."""

    async def test_scan_returns_empty_when_no_rows(self) -> None:
        """Returns an empty list when there are no stuck rows."""
        pool, conn = _make_pool()
        conn.fetch = AsyncMock(return_value=[])

        result = await route_inbox_scan_unprocessed(pool, grace_s=10, batch_size=50)

        assert result == []

    async def test_scan_returns_rows_as_dicts(self) -> None:
        """Returns a list of dicts for each stuck row."""
        pool, conn = _make_pool()
        row_id = uuid.uuid4()
        now = datetime.now(UTC)
        mock_row = {
            "id": row_id,
            "received_at": now,
            "route_envelope": {"schema_version": "route.v1", "input": {"prompt": "test"}},
        }
        conn.fetch = AsyncMock(return_value=[mock_row])

        result = await route_inbox_scan_unprocessed(pool, grace_s=10, batch_size=50)

        assert len(result) == 1
        assert result[0]["id"] == row_id
        assert result[0]["received_at"] == now
        assert result[0]["route_envelope"]["schema_version"] == "route.v1"

    async def test_scan_passes_grace_and_batch_to_query(self) -> None:
        """Scanner passes grace_s and batch_size to the SQL query."""
        pool, conn = _make_pool()
        conn.fetch = AsyncMock(return_value=[])

        await route_inbox_scan_unprocessed(pool, grace_s=42, batch_size=7)

        call_args = conn.fetch.call_args
        assert 42 in call_args.args
        assert 7 in call_args.args

    async def test_scan_filters_by_accepted_and_processing_state(self) -> None:
        """Scanner SQL filters by lifecycle_state IN ('accepted', 'processing')."""
        pool, conn = _make_pool()
        conn.fetch = AsyncMock(return_value=[])

        await route_inbox_scan_unprocessed(pool)

        call_args = conn.fetch.call_args
        # First arg is the list of states to recover
        states_arg = call_args.args[1]
        assert STATE_ACCEPTED in states_arg
        assert STATE_PROCESSING in states_arg


# ---------------------------------------------------------------------------
# route_inbox_recovery_sweep
# ---------------------------------------------------------------------------


class TestRouteInboxRecoverySweep:
    """Tests for crash recovery sweep."""

    async def test_recovery_calls_dispatch_fn_for_each_row(self) -> None:
        """dispatch_fn is called once per unprocessed row."""
        pool, conn = _make_pool()
        row_id = uuid.uuid4()
        now = datetime.now(UTC)
        conn.fetch = AsyncMock(
            return_value=[
                {
                    "id": row_id,
                    "received_at": now.replace(tzinfo=None),
                    "route_envelope": {"schema_version": "route.v1", "input": {"prompt": "hi"}},
                }
            ]
        )

        dispatch_calls: list[dict] = []

        async def dispatch_fn(*, row_id: uuid.UUID, route_envelope: dict) -> None:
            dispatch_calls.append({"row_id": row_id, "envelope": route_envelope})

        recovered = await route_inbox_recovery_sweep(
            pool,
            dispatch_fn=dispatch_fn,
            grace_s=10,
            batch_size=50,
        )

        assert recovered == 1
        assert len(dispatch_calls) == 1
        assert dispatch_calls[0]["row_id"] == row_id

    async def test_recovery_returns_zero_when_no_rows(self) -> None:
        """Returns 0 when there are no stuck rows."""
        pool, conn = _make_pool()
        conn.fetch = AsyncMock(return_value=[])
        dispatch_fn = AsyncMock()

        recovered = await route_inbox_recovery_sweep(pool, dispatch_fn=dispatch_fn)

        assert recovered == 0
        dispatch_fn.assert_not_awaited()

    async def test_recovery_continues_if_one_dispatch_fails(self) -> None:
        """If dispatch_fn raises for one row, recovery continues for others."""
        pool, conn = _make_pool()
        now = datetime.now(UTC)
        rows = [
            {
                "id": uuid.uuid4(),
                "received_at": now.replace(tzinfo=None),
                "route_envelope": {"schema_version": "route.v1", "input": {"prompt": f"msg{i}"}},
            }
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

        # Only 2 succeed (rows 1 and 3), row 2 raised
        assert recovered == 2
        assert call_count == 3

    async def test_recovery_returns_count_of_successful_dispatches(self) -> None:
        """Returns count of successfully dispatched rows (failures not counted)."""
        pool, conn = _make_pool()
        now = datetime.now(UTC)
        row_id = uuid.uuid4()
        conn.fetch = AsyncMock(
            return_value=[
                {
                    "id": row_id,
                    "received_at": now.replace(tzinfo=None),
                    "route_envelope": {"schema_version": "route.v1", "input": {"prompt": "go"}},
                }
            ]
        )

        async def good_dispatch(*, row_id: uuid.UUID, route_envelope: dict) -> None:
            pass

        recovered = await route_inbox_recovery_sweep(pool, dispatch_fn=good_dispatch)
        assert recovered == 1
