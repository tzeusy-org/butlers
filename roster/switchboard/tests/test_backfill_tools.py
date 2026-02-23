"""Tests for switchboard backfill MCP lifecycle tools.

Covers:
- create_backfill_job   (dashboard-facing)
- backfill_pause        (dashboard-facing)
- backfill_cancel       (dashboard-facing)
- backfill_resume       (dashboard-facing)
- backfill_list         (dashboard-facing)
- backfill_poll         (connector-facing)
- backfill_progress     (connector-facing)

All tests use AsyncMock pools — no real database required.
"""

from __future__ import annotations

import json
import uuid
from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from roster.switchboard.tools.backfill.connector import backfill_poll, backfill_progress
from roster.switchboard.tools.backfill.controls import (
    backfill_cancel,
    backfill_list,
    backfill_pause,
    backfill_resume,
    create_backfill_job,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CONNECTOR_TYPE = "gmail"
_ENDPOINT_IDENTITY = "user@example.com"
_JOB_ID = uuid.uuid4()


def _make_pool(*, fetchrow_return=None, fetch_return=None, execute_return=None):
    """Build an AsyncMock pool with configurable return values."""
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=fetchrow_return)
    pool.fetch = AsyncMock(return_value=fetch_return or [])
    pool.execute = AsyncMock(return_value=execute_return)
    return pool


def _fake_job_row(
    status="pending",
    rows_processed=0,
    rows_skipped=0,
    cost_spent_cents=0,
    daily_cost_cap_cents=500,
    cursor=None,
):
    """Build a minimal fake asyncpg-style record for a backfill_jobs row."""
    row = MagicMock()
    row.__getitem__ = lambda self, key: {
        "id": _JOB_ID,
        "connector_type": _CONNECTOR_TYPE,
        "endpoint_identity": _ENDPOINT_IDENTITY,
        "target_categories": json.dumps(["finance"]),
        "date_from": date(2020, 1, 1),
        "date_to": date(2023, 12, 31),
        "rate_limit_per_hour": 100,
        "daily_cost_cap_cents": daily_cost_cap_cents,
        "status": status,
        "rows_processed": rows_processed,
        "rows_skipped": rows_skipped,
        "cost_spent_cents": cost_spent_cents,
        "error": None,
        "created_at": None,
        "started_at": None,
        "completed_at": None,
        "updated_at": None,
        "cursor": cursor,
    }[key]
    return row


# ===========================================================================
# create_backfill_job
# ===========================================================================


class TestCreateBackfillJob:
    """Tests for create_backfill_job (dashboard-facing)."""

    @pytest.mark.asyncio
    async def test_creates_pending_job(self):
        """Happy path: valid inputs create a pending job."""
        connector_row = MagicMock()
        connector_row.__getitem__ = lambda s, k: {
            "connector_type": _CONNECTOR_TYPE,
            "endpoint_identity": _ENDPOINT_IDENTITY,
        }[k]

        created_row = MagicMock()
        created_row.__getitem__ = lambda s, k: {"id": _JOB_ID, "status": "pending"}[k]

        pool = _make_pool()
        pool.fetchrow = AsyncMock(side_effect=[connector_row, created_row])

        result = await create_backfill_job(
            pool,
            connector_type=_CONNECTOR_TYPE,
            endpoint_identity=_ENDPOINT_IDENTITY,
            target_categories=["finance", "health"],
            date_from=date(2020, 1, 1),
            date_to=date(2023, 12, 31),
        )

        assert result["status"] == "pending"
        assert "job_id" in result
        assert result["job_id"] == str(_JOB_ID)

    @pytest.mark.asyncio
    async def test_accepts_string_dates(self):
        """Accepts ISO date strings as well as date objects."""
        connector_row = _fake_job_row()
        created_row = MagicMock()
        created_row.__getitem__ = lambda s, k: {"id": _JOB_ID, "status": "pending"}[k]

        pool = _make_pool()
        pool.fetchrow = AsyncMock(side_effect=[connector_row, created_row])

        result = await create_backfill_job(
            pool,
            connector_type=_CONNECTOR_TYPE,
            endpoint_identity=_ENDPOINT_IDENTITY,
            target_categories=[],
            date_from="2020-01-01",
            date_to="2023-12-31",
        )
        assert result["status"] == "pending"

    @pytest.mark.asyncio
    async def test_rejects_unknown_connector(self):
        """Raises ValueError when connector is not in registry."""
        pool = _make_pool(fetchrow_return=None)

        with pytest.raises(ValueError, match="not found in connector_registry"):
            await create_backfill_job(
                pool,
                connector_type="unknown_type",
                endpoint_identity="nobody@example.com",
                target_categories=[],
                date_from=date(2020, 1, 1),
                date_to=date(2023, 12, 31),
            )

    @pytest.mark.asyncio
    async def test_rejects_inverted_date_range(self):
        """Raises ValueError when date_from > date_to."""
        pool = _make_pool()

        with pytest.raises(ValueError, match="date_from.*must not be after.*date_to"):
            await create_backfill_job(
                pool,
                connector_type=_CONNECTOR_TYPE,
                endpoint_identity=_ENDPOINT_IDENTITY,
                target_categories=[],
                date_from=date(2024, 1, 1),
                date_to=date(2020, 1, 1),
            )

    @pytest.mark.asyncio
    async def test_rejects_empty_connector_type(self):
        """Raises ValueError for empty connector_type."""
        pool = _make_pool()
        with pytest.raises(ValueError, match="connector_type must be a non-empty string"):
            await create_backfill_job(
                pool,
                connector_type="",
                endpoint_identity=_ENDPOINT_IDENTITY,
                target_categories=[],
                date_from=date(2020, 1, 1),
                date_to=date(2023, 12, 31),
            )

    @pytest.mark.asyncio
    async def test_rejects_empty_endpoint_identity(self):
        """Raises ValueError for empty endpoint_identity."""
        pool = _make_pool()
        with pytest.raises(ValueError, match="endpoint_identity must be a non-empty string"):
            await create_backfill_job(
                pool,
                connector_type=_CONNECTOR_TYPE,
                endpoint_identity="",
                target_categories=[],
                date_from=date(2020, 1, 1),
                date_to=date(2023, 12, 31),
            )

    @pytest.mark.asyncio
    async def test_rejects_zero_rate_limit(self):
        """Raises ValueError for non-positive rate_limit_per_hour."""
        pool = _make_pool()
        with pytest.raises(ValueError, match="rate_limit_per_hour must be a positive integer"):
            await create_backfill_job(
                pool,
                connector_type=_CONNECTOR_TYPE,
                endpoint_identity=_ENDPOINT_IDENTITY,
                target_categories=[],
                date_from=date(2020, 1, 1),
                date_to=date(2023, 12, 31),
                rate_limit_per_hour=0,
            )

    @pytest.mark.asyncio
    async def test_rejects_zero_cost_cap(self):
        """Raises ValueError for non-positive daily_cost_cap_cents."""
        pool = _make_pool()
        with pytest.raises(ValueError, match="daily_cost_cap_cents must be a positive integer"):
            await create_backfill_job(
                pool,
                connector_type=_CONNECTOR_TYPE,
                endpoint_identity=_ENDPOINT_IDENTITY,
                target_categories=[],
                date_from=date(2020, 1, 1),
                date_to=date(2023, 12, 31),
                daily_cost_cap_cents=0,
            )

    @pytest.mark.asyncio
    async def test_db_failure_raises_runtime_error(self):
        """Wraps unexpected DB errors in RuntimeError."""
        connector_row = _fake_job_row()
        pool = _make_pool()
        pool.fetchrow = AsyncMock(side_effect=[connector_row, Exception("DB boom")])

        with pytest.raises(RuntimeError, match="Failed to create backfill job"):
            await create_backfill_job(
                pool,
                connector_type=_CONNECTOR_TYPE,
                endpoint_identity=_ENDPOINT_IDENTITY,
                target_categories=[],
                date_from=date(2020, 1, 1),
                date_to=date(2023, 12, 31),
            )


# ===========================================================================
# backfill_pause
# ===========================================================================


class TestBackfillPause:
    """Tests for backfill_pause (dashboard-facing)."""

    @pytest.mark.asyncio
    async def test_pauses_active_job(self):
        """Happy path: active job transitions to paused."""
        existing = _fake_job_row(status="active")
        updated = MagicMock()
        updated.__getitem__ = lambda s, k: {"status": "paused"}[k]

        pool = _make_pool()
        pool.fetchrow = AsyncMock(side_effect=[existing, updated])

        result = await backfill_pause(pool, job_id=_JOB_ID)
        assert result == {"status": "paused"}

    @pytest.mark.asyncio
    async def test_pause_already_paused_is_idempotent(self):
        """Pausing an already-paused job returns paused without DB update."""
        existing = _fake_job_row(status="paused")
        pool = _make_pool(fetchrow_return=existing)

        result = await backfill_pause(pool, job_id=_JOB_ID)
        assert result == {"status": "paused"}
        # Only the initial fetchrow was called, no UPDATE
        pool.fetchrow.assert_called_once()

    @pytest.mark.asyncio
    async def test_pause_not_found_raises(self):
        """Raises ValueError when job does not exist."""
        pool = _make_pool(fetchrow_return=None)
        with pytest.raises(ValueError, match="not found"):
            await backfill_pause(pool, job_id=uuid.uuid4())

    @pytest.mark.asyncio
    async def test_pause_terminal_job_raises(self):
        """Raises ValueError when job is in a terminal state."""
        for terminal in ("completed", "cancelled", "error", "cost_capped"):
            existing = _fake_job_row(status=terminal)
            pool = _make_pool(fetchrow_return=existing)
            with pytest.raises(ValueError, match="terminal state"):
                await backfill_pause(pool, job_id=_JOB_ID)

    @pytest.mark.asyncio
    async def test_pause_pending_job(self):
        """Pauses a pending job (valid non-terminal state)."""
        existing = _fake_job_row(status="pending")
        updated = MagicMock()
        updated.__getitem__ = lambda s, k: {"status": "paused"}[k]

        pool = _make_pool()
        pool.fetchrow = AsyncMock(side_effect=[existing, updated])

        result = await backfill_pause(pool, job_id=_JOB_ID)
        assert result["status"] == "paused"


# ===========================================================================
# backfill_cancel
# ===========================================================================


class TestBackfillCancel:
    """Tests for backfill_cancel (dashboard-facing)."""

    @pytest.mark.asyncio
    async def test_cancels_active_job(self):
        """Happy path: active job transitions to cancelled."""
        existing = _fake_job_row(status="active")
        updated = MagicMock()
        updated.__getitem__ = lambda s, k: {"status": "cancelled"}[k]

        pool = _make_pool()
        pool.fetchrow = AsyncMock(side_effect=[existing, updated])

        result = await backfill_cancel(pool, job_id=_JOB_ID)
        assert result == {"status": "cancelled"}

    @pytest.mark.asyncio
    async def test_cancel_already_cancelled_is_idempotent(self):
        """Cancelling an already-cancelled job is idempotent."""
        existing = _fake_job_row(status="cancelled")
        pool = _make_pool(fetchrow_return=existing)

        result = await backfill_cancel(pool, job_id=_JOB_ID)
        assert result == {"status": "cancelled"}
        pool.fetchrow.assert_called_once()

    @pytest.mark.asyncio
    async def test_cancel_not_found_raises(self):
        """Raises ValueError when job does not exist."""
        pool = _make_pool(fetchrow_return=None)
        with pytest.raises(ValueError, match="not found"):
            await backfill_cancel(pool, job_id=uuid.uuid4())

    @pytest.mark.asyncio
    async def test_cancel_completed_job_raises(self):
        """Raises ValueError when job is already completed."""
        existing = _fake_job_row(status="completed")
        pool = _make_pool(fetchrow_return=existing)
        with pytest.raises(ValueError, match="terminal state"):
            await backfill_cancel(pool, job_id=_JOB_ID)

    @pytest.mark.asyncio
    async def test_cancel_error_job_raises(self):
        """Raises ValueError when job is in error state."""
        existing = _fake_job_row(status="error")
        pool = _make_pool(fetchrow_return=existing)
        with pytest.raises(ValueError, match="terminal state"):
            await backfill_cancel(pool, job_id=_JOB_ID)


# ===========================================================================
# backfill_resume
# ===========================================================================


class TestBackfillResume:
    """Tests for backfill_resume (dashboard-facing)."""

    @pytest.mark.asyncio
    async def test_resumes_paused_job(self):
        """Happy path: paused job transitions to pending."""
        existing = _fake_job_row(status="paused")
        updated = MagicMock()
        updated.__getitem__ = lambda s, k: {"status": "pending"}[k]

        pool = _make_pool()
        pool.fetchrow = AsyncMock(side_effect=[existing, updated])

        result = await backfill_resume(pool, job_id=_JOB_ID)
        assert result == {"status": "pending"}

    @pytest.mark.asyncio
    async def test_resumes_cost_capped_job(self):
        """cost_capped job can also be resumed."""
        existing = _fake_job_row(status="cost_capped")
        updated = MagicMock()
        updated.__getitem__ = lambda s, k: {"status": "pending"}[k]

        pool = _make_pool()
        pool.fetchrow = AsyncMock(side_effect=[existing, updated])

        result = await backfill_resume(pool, job_id=_JOB_ID)
        assert result == {"status": "pending"}

    @pytest.mark.asyncio
    async def test_resume_active_job_raises(self):
        """Raises ValueError when trying to resume an active job."""
        existing = _fake_job_row(status="active")
        pool = _make_pool(fetchrow_return=existing)
        with pytest.raises(ValueError, match="cannot be resumed"):
            await backfill_resume(pool, job_id=_JOB_ID)

    @pytest.mark.asyncio
    async def test_resume_completed_job_raises(self):
        """Raises ValueError when trying to resume a completed job."""
        existing = _fake_job_row(status="completed")
        pool = _make_pool(fetchrow_return=existing)
        with pytest.raises(ValueError, match="cannot be resumed"):
            await backfill_resume(pool, job_id=_JOB_ID)

    @pytest.mark.asyncio
    async def test_resume_not_found_raises(self):
        """Raises ValueError when job does not exist."""
        pool = _make_pool(fetchrow_return=None)
        with pytest.raises(ValueError, match="not found"):
            await backfill_resume(pool, job_id=uuid.uuid4())


# ===========================================================================
# backfill_list
# ===========================================================================


class TestBackfillList:
    """Tests for backfill_list (dashboard-facing)."""

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_jobs(self):
        """Returns empty list when no jobs match."""
        pool = _make_pool(fetch_return=[])
        result = await backfill_list(pool)
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_job_summaries(self):
        """Returns list of job summary dicts with all required fields."""
        import datetime as dt

        row = MagicMock()
        row.__getitem__ = lambda s, k: {
            "id": _JOB_ID,
            "connector_type": _CONNECTOR_TYPE,
            "endpoint_identity": _ENDPOINT_IDENTITY,
            "target_categories": json.dumps(["finance"]),
            "date_from": date(2020, 1, 1),
            "date_to": date(2023, 12, 31),
            "rate_limit_per_hour": 100,
            "daily_cost_cap_cents": 500,
            "status": "active",
            "rows_processed": 50,
            "rows_skipped": 5,
            "cost_spent_cents": 12,
            "error": None,
            "created_at": dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
            "started_at": dt.datetime(2026, 1, 2, tzinfo=dt.UTC),
            "completed_at": None,
            "updated_at": dt.datetime(2026, 1, 3, tzinfo=dt.UTC),
        }[k]

        pool = _make_pool(fetch_return=[row])
        result = await backfill_list(pool)

        assert len(result) == 1
        summary = result[0]
        # Verify all required fields present
        required_fields = {
            "job_id",
            "connector_type",
            "endpoint_identity",
            "target_categories",
            "date_from",
            "date_to",
            "rate_limit_per_hour",
            "daily_cost_cap_cents",
            "status",
            "rows_processed",
            "rows_skipped",
            "cost_spent_cents",
            "error",
            "created_at",
            "started_at",
            "completed_at",
            "updated_at",
        }
        assert required_fields.issubset(set(summary.keys()))
        assert summary["job_id"] == str(_JOB_ID)
        assert summary["status"] == "active"
        assert summary["target_categories"] == ["finance"]

    @pytest.mark.asyncio
    async def test_filters_by_connector_type(self):
        """Passes connector_type filter to DB query."""
        pool = _make_pool(fetch_return=[])
        await backfill_list(pool, connector_type="gmail")
        call_args = pool.fetch.call_args
        query = call_args[0][0]
        assert "connector_type" in query

    @pytest.mark.asyncio
    async def test_filters_by_status(self):
        """Passes status filter to DB query."""
        pool = _make_pool(fetch_return=[])
        await backfill_list(pool, status="active")
        call_args = pool.fetch.call_args
        assert "active" in str(call_args)

    @pytest.mark.asyncio
    async def test_filters_by_endpoint_identity(self):
        """Passes endpoint_identity filter to DB query."""
        pool = _make_pool(fetch_return=[])
        await backfill_list(pool, endpoint_identity="user@example.com")
        call_args = pool.fetch.call_args
        assert "user@example.com" in str(call_args)


# ===========================================================================
# backfill_poll
# ===========================================================================


class TestBackfillPoll:
    """Tests for backfill_poll (connector-facing)."""

    @pytest.mark.asyncio
    async def test_returns_none_when_no_pending_job(self):
        """Returns None when no pending job exists for the connector."""
        pool = _make_pool(fetchrow_return=None)
        result = await backfill_poll(
            pool,
            connector_type=_CONNECTOR_TYPE,
            endpoint_identity=_ENDPOINT_IDENTITY,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_job_with_params_and_cursor(self):
        """Returns job_id, params, and cursor when a job is claimed."""
        row = MagicMock()
        row.__getitem__ = lambda s, k: {
            "id": _JOB_ID,
            "target_categories": json.dumps(["finance"]),
            "date_from": date(2020, 1, 1),
            "date_to": date(2023, 12, 31),
            "rate_limit_per_hour": 100,
            "daily_cost_cap_cents": 500,
            "cursor": None,
        }[k]

        pool = _make_pool(fetchrow_return=row)
        result = await backfill_poll(
            pool,
            connector_type=_CONNECTOR_TYPE,
            endpoint_identity=_ENDPOINT_IDENTITY,
        )

        assert result is not None
        assert result["job_id"] == str(_JOB_ID)
        assert "params" in result
        params = result["params"]
        assert params["target_categories"] == ["finance"]
        assert params["date_from"] == "2020-01-01"
        assert params["date_to"] == "2023-12-31"
        assert params["rate_limit_per_hour"] == 100
        assert params["daily_cost_cap_cents"] == 500
        assert result["cursor"] is None

    @pytest.mark.asyncio
    async def test_returns_cursor_when_present(self):
        """Cursor is deserialised from JSONB and returned."""
        cursor_data = {"last_message_id": "msg-9999", "page_token": "tok-abc"}
        row = MagicMock()
        row.__getitem__ = lambda s, k: {
            "id": _JOB_ID,
            "target_categories": json.dumps([]),
            "date_from": date(2020, 1, 1),
            "date_to": date(2023, 12, 31),
            "rate_limit_per_hour": 50,
            "daily_cost_cap_cents": 300,
            "cursor": json.dumps(cursor_data),
        }[k]

        pool = _make_pool(fetchrow_return=row)
        result = await backfill_poll(
            pool,
            connector_type=_CONNECTOR_TYPE,
            endpoint_identity=_ENDPOINT_IDENTITY,
        )

        assert result["cursor"] == cursor_data

    @pytest.mark.asyncio
    async def test_rejects_empty_connector_type(self):
        """Raises ValueError for empty connector_type."""
        pool = _make_pool()
        with pytest.raises(ValueError, match="connector_type must be a non-empty string"):
            await backfill_poll(pool, connector_type="", endpoint_identity=_ENDPOINT_IDENTITY)

    @pytest.mark.asyncio
    async def test_rejects_empty_endpoint_identity(self):
        """Raises ValueError for empty endpoint_identity."""
        pool = _make_pool()
        with pytest.raises(ValueError, match="endpoint_identity must be a non-empty string"):
            await backfill_poll(pool, connector_type=_CONNECTOR_TYPE, endpoint_identity="")

    @pytest.mark.asyncio
    async def test_db_failure_raises_runtime_error(self):
        """Wraps DB errors in RuntimeError."""
        pool = _make_pool()
        pool.fetchrow = AsyncMock(side_effect=Exception("connection lost"))

        with pytest.raises(RuntimeError, match="backfill.poll database error"):
            await backfill_poll(
                pool,
                connector_type=_CONNECTOR_TYPE,
                endpoint_identity=_ENDPOINT_IDENTITY,
            )

    @pytest.mark.asyncio
    async def test_query_uses_skip_locked(self):
        """The poll query uses SKIP LOCKED for race-safe claiming."""
        pool = _make_pool(fetchrow_return=None)
        await backfill_poll(
            pool,
            connector_type=_CONNECTOR_TYPE,
            endpoint_identity=_ENDPOINT_IDENTITY,
        )
        call_sql = pool.fetchrow.call_args[0][0]
        assert "SKIP LOCKED" in call_sql


# ===========================================================================
# backfill_progress
# ===========================================================================


class TestBackfillProgress:
    """Tests for backfill_progress (connector-facing)."""

    def _make_select_row(
        self,
        status="active",
        cost_spent_cents=0,
        daily_cost_cap_cents=500,
    ):
        """Build a fake select row for progress tests."""
        row = MagicMock()
        row.__getitem__ = lambda s, k: {
            "id": _JOB_ID,
            "connector_type": _CONNECTOR_TYPE,
            "endpoint_identity": _ENDPOINT_IDENTITY,
            "status": status,
            "cost_spent_cents": cost_spent_cents,
            "daily_cost_cap_cents": daily_cost_cap_cents,
        }[k]
        return row

    def _make_update_row(self, status="active", rows_processed=50, rows_skipped=5, cost=12):
        """Build a fake update result row."""
        row = MagicMock()
        row.__getitem__ = lambda s, k: {
            "status": status,
            "rows_processed": rows_processed,
            "rows_skipped": rows_skipped,
            "cost_spent_cents": cost,
        }[k]
        return row

    @pytest.mark.asyncio
    async def test_updates_counters_and_returns_active(self):
        """Happy path: progress update returns active status."""
        select_row = self._make_select_row()
        update_row = self._make_update_row()

        pool = _make_pool()
        pool.fetchrow = AsyncMock(side_effect=[select_row, update_row])

        result = await backfill_progress(
            pool,
            job_id=_JOB_ID,
            connector_type=_CONNECTOR_TYPE,
            endpoint_identity=_ENDPOINT_IDENTITY,
            rows_processed=50,
            rows_skipped=5,
            cost_spent_cents_delta=12,
        )
        assert result == {"status": "active"}

    @pytest.mark.asyncio
    async def test_transitions_to_cost_capped_when_cap_reached(self):
        """Transitions to cost_capped when cumulative cost >= daily_cost_cap_cents."""
        select_row = self._make_select_row(
            cost_spent_cents=480,
            daily_cost_cap_cents=500,
        )
        update_row = self._make_update_row(status="cost_capped", cost=500)

        pool = _make_pool()
        pool.fetchrow = AsyncMock(side_effect=[select_row, update_row])

        result = await backfill_progress(
            pool,
            job_id=_JOB_ID,
            connector_type=_CONNECTOR_TYPE,
            endpoint_identity=_ENDPOINT_IDENTITY,
            rows_processed=10,
            rows_skipped=0,
            cost_spent_cents_delta=20,  # 480 + 20 = 500 >= 500 cap
        )
        assert result["status"] == "cost_capped"

    @pytest.mark.asyncio
    async def test_transitions_to_cost_capped_exactly_at_cap(self):
        """Cost_capped triggers when cost exactly equals cap."""
        select_row = self._make_select_row(cost_spent_cents=490, daily_cost_cap_cents=500)
        update_row = self._make_update_row(status="cost_capped", cost=500)

        pool = _make_pool()
        pool.fetchrow = AsyncMock(side_effect=[select_row, update_row])

        result = await backfill_progress(
            pool,
            job_id=_JOB_ID,
            connector_type=_CONNECTOR_TYPE,
            endpoint_identity=_ENDPOINT_IDENTITY,
            rows_processed=5,
            rows_skipped=0,
            cost_spent_cents_delta=10,  # 490 + 10 = 500 >= 500
        )
        assert result["status"] == "cost_capped"

    @pytest.mark.asyncio
    async def test_connector_can_report_completed(self):
        """Connector can report completed status to close out the job."""
        select_row = self._make_select_row()
        update_row = self._make_update_row(status="completed")

        pool = _make_pool()
        pool.fetchrow = AsyncMock(side_effect=[select_row, update_row])

        result = await backfill_progress(
            pool,
            job_id=_JOB_ID,
            connector_type=_CONNECTOR_TYPE,
            endpoint_identity=_ENDPOINT_IDENTITY,
            rows_processed=100,
            rows_skipped=0,
            cost_spent_cents_delta=0,
            status="completed",
        )
        assert result["status"] == "completed"

    @pytest.mark.asyncio
    async def test_connector_can_report_error(self):
        """Connector can report error status with error detail."""
        select_row = self._make_select_row()
        update_row = self._make_update_row(status="error")

        pool = _make_pool()
        pool.fetchrow = AsyncMock(side_effect=[select_row, update_row])

        result = await backfill_progress(
            pool,
            job_id=_JOB_ID,
            connector_type=_CONNECTOR_TYPE,
            endpoint_identity=_ENDPOINT_IDENTITY,
            rows_processed=10,
            rows_skipped=0,
            cost_spent_cents_delta=0,
            status="error",
            error="Gmail API rate limit exceeded after 5 retries",
        )
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_returns_current_status_when_job_not_active(self):
        """Returns authoritative non-active status immediately (no DB update)."""
        for non_active in ("paused", "cancelled", "cost_capped"):
            select_row = self._make_select_row(status=non_active)
            pool = _make_pool(fetchrow_return=select_row)

            result = await backfill_progress(
                pool,
                job_id=_JOB_ID,
                connector_type=_CONNECTOR_TYPE,
                endpoint_identity=_ENDPOINT_IDENTITY,
                rows_processed=0,
                rows_skipped=0,
                cost_spent_cents_delta=0,
            )
            assert result["status"] == non_active
            # Only one fetchrow call (select), no update fetchrow
            pool.fetchrow.assert_called_once()

    @pytest.mark.asyncio
    async def test_enforces_connector_identity_scoping(self):
        """Raises ValueError when connector identity does not match job."""
        select_row = self._make_select_row()
        pool = _make_pool(fetchrow_return=select_row)

        with pytest.raises(ValueError, match="Connector identity mismatch"):
            await backfill_progress(
                pool,
                job_id=_JOB_ID,
                connector_type="imap",  # Wrong type
                endpoint_identity=_ENDPOINT_IDENTITY,
                rows_processed=0,
                rows_skipped=0,
                cost_spent_cents_delta=0,
            )

    @pytest.mark.asyncio
    async def test_job_not_found_raises(self):
        """Raises ValueError when job does not exist."""
        pool = _make_pool(fetchrow_return=None)
        with pytest.raises(ValueError, match="not found"):
            await backfill_progress(
                pool,
                job_id=uuid.uuid4(),
                connector_type=_CONNECTOR_TYPE,
                endpoint_identity=_ENDPOINT_IDENTITY,
                rows_processed=0,
                rows_skipped=0,
                cost_spent_cents_delta=0,
            )

    @pytest.mark.asyncio
    async def test_rejects_negative_rows_processed(self):
        """Raises ValueError for negative rows_processed."""
        pool = _make_pool()
        with pytest.raises(ValueError, match="rows_processed must be >= 0"):
            await backfill_progress(
                pool,
                job_id=_JOB_ID,
                connector_type=_CONNECTOR_TYPE,
                endpoint_identity=_ENDPOINT_IDENTITY,
                rows_processed=-1,
                rows_skipped=0,
                cost_spent_cents_delta=0,
            )

    @pytest.mark.asyncio
    async def test_rejects_negative_rows_skipped(self):
        """Raises ValueError for negative rows_skipped."""
        pool = _make_pool()
        with pytest.raises(ValueError, match="rows_skipped must be >= 0"):
            await backfill_progress(
                pool,
                job_id=_JOB_ID,
                connector_type=_CONNECTOR_TYPE,
                endpoint_identity=_ENDPOINT_IDENTITY,
                rows_processed=0,
                rows_skipped=-1,
                cost_spent_cents_delta=0,
            )

    @pytest.mark.asyncio
    async def test_rejects_negative_cost_delta(self):
        """Raises ValueError for negative cost_spent_cents_delta."""
        pool = _make_pool()
        with pytest.raises(ValueError, match="cost_spent_cents_delta must be >= 0"):
            await backfill_progress(
                pool,
                job_id=_JOB_ID,
                connector_type=_CONNECTOR_TYPE,
                endpoint_identity=_ENDPOINT_IDENTITY,
                rows_processed=0,
                rows_skipped=0,
                cost_spent_cents_delta=-5,
            )

    @pytest.mark.asyncio
    async def test_rejects_invalid_connector_status(self):
        """Raises ValueError when connector reports an invalid status."""
        pool = _make_pool()
        with pytest.raises(ValueError, match="Connector may only report status"):
            await backfill_progress(
                pool,
                job_id=_JOB_ID,
                connector_type=_CONNECTOR_TYPE,
                endpoint_identity=_ENDPOINT_IDENTITY,
                rows_processed=0,
                rows_skipped=0,
                cost_spent_cents_delta=0,
                status="paused",  # Only connector can set completed/error
            )

    @pytest.mark.asyncio
    async def test_cursor_update(self):
        """Cursor is serialised and passed to the UPDATE query."""
        select_row = self._make_select_row()
        update_row = self._make_update_row()

        pool = _make_pool()
        pool.fetchrow = AsyncMock(side_effect=[select_row, update_row])

        cursor_data = {"last_message_id": "msg-777", "page_token": "xyz"}
        await backfill_progress(
            pool,
            job_id=_JOB_ID,
            connector_type=_CONNECTOR_TYPE,
            endpoint_identity=_ENDPOINT_IDENTITY,
            rows_processed=10,
            rows_skipped=0,
            cost_spent_cents_delta=5,
            cursor=cursor_data,
        )

        # The UPDATE fetchrow should have received the serialised cursor
        update_call = pool.fetchrow.call_args_list[1]
        call_args = update_call[0]
        assert json.dumps(cursor_data) in [str(a) for a in call_args]

    @pytest.mark.asyncio
    async def test_db_failure_raises_runtime_error(self):
        """Wraps DB update errors in RuntimeError."""
        select_row = self._make_select_row()
        pool = _make_pool()
        pool.fetchrow = AsyncMock(side_effect=[select_row, Exception("DB gone")])

        with pytest.raises(RuntimeError, match="backfill.progress database error"):
            await backfill_progress(
                pool,
                job_id=_JOB_ID,
                connector_type=_CONNECTOR_TYPE,
                endpoint_identity=_ENDPOINT_IDENTITY,
                rows_processed=0,
                rows_skipped=0,
                cost_spent_cents_delta=0,
            )

    @pytest.mark.asyncio
    async def test_cost_capped_overrides_connector_completed_status(self):
        """cost_capped takes precedence over connector-reported completed."""
        # Cumulative cost would exceed cap — cost_capped should win
        select_row = self._make_select_row(cost_spent_cents=495, daily_cost_cap_cents=500)
        update_row = self._make_update_row(status="cost_capped", cost=510)

        pool = _make_pool()
        pool.fetchrow = AsyncMock(side_effect=[select_row, update_row])

        result = await backfill_progress(
            pool,
            job_id=_JOB_ID,
            connector_type=_CONNECTOR_TYPE,
            endpoint_identity=_ENDPOINT_IDENTITY,
            rows_processed=10,
            rows_skipped=0,
            cost_spent_cents_delta=15,  # 495+15=510 >= 500
            status="completed",  # Connector thinks it's done
        )
        assert result["status"] == "cost_capped"
