"""Tests for backfill.poll and backfill.progress MCP tool registrations.

Verifies that:
1. The underlying backfill connector functions are importable via butlers.tools path
2. backfill_poll correctly claims pending jobs (success and no-job cases)
3. backfill_progress correctly reports batch progress
4. Input validation (empty strings, negative values) is enforced

These tests exercise the roster/switchboard/tools/backfill/connector.py logic
directly, since the daemon wiring is a thin pass-through to those functions.
The tool registration in daemon.py is validated by smoke tests.

Issue: butlers-cgeo
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Import path validation: ensure butlers.tools.switchboard.backfill is accessible
# ---------------------------------------------------------------------------


def test_backfill_poll_importable_via_butlers_tools_path():
    """backfill_poll is importable through the butlers.tools.switchboard namespace."""
    from butlers.tools.switchboard.backfill.connector import backfill_poll

    assert callable(backfill_poll)


def test_backfill_progress_importable_via_butlers_tools_path():
    """backfill_progress is importable through the butlers.tools.switchboard namespace."""
    from butlers.tools.switchboard.backfill.connector import backfill_progress

    assert callable(backfill_progress)


def test_backfill_package_init_exports_both_functions():
    """The backfill package __init__.py re-exports poll and progress."""
    from butlers.tools.switchboard.backfill import backfill_poll, backfill_progress

    assert callable(backfill_poll)
    assert callable(backfill_progress)


# ---------------------------------------------------------------------------
# backfill_poll: success path
# ---------------------------------------------------------------------------


class TestBackfillPoll:
    """Tests for backfill_poll connector function (which backfill.poll MCP tool delegates to)."""

    async def test_returns_job_when_pending_job_exists(self):
        """backfill_poll returns a job dict when a pending job is available."""
        from butlers.tools.switchboard.backfill.connector import backfill_poll

        _JOB_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"

        mock_row = {
            "id": UUID(_JOB_ID),
            "target_categories": ["finance"],
            "date_from": None,
            "date_to": None,
            "rate_limit_per_hour": 100,
            "daily_cost_cap_cents": 500,
            "cursor": None,
        }
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=mock_row)

        result = await backfill_poll(
            mock_pool,
            connector_type="gmail",
            endpoint_identity="user@example.com",
        )

        assert result is not None
        assert result["job_id"] == _JOB_ID
        assert "params" in result
        assert result["params"]["rate_limit_per_hour"] == 100
        assert result["params"]["daily_cost_cap_cents"] == 500
        assert result["cursor"] is None

    async def test_returns_none_when_no_pending_job(self):
        """backfill_poll returns None when no pending job exists for connector."""
        from butlers.tools.switchboard.backfill.connector import backfill_poll

        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=None)

        result = await backfill_poll(
            mock_pool,
            connector_type="gmail",
            endpoint_identity="user@example.com",
        )

        assert result is None

    async def test_returns_job_with_target_categories_as_list(self):
        """backfill_poll normalizes target_categories from JSON string to list."""
        from butlers.tools.switchboard.backfill.connector import backfill_poll

        mock_row = {
            "id": UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890"),
            "target_categories": json.dumps(["finance", "health"]),  # JSON string
            "date_from": None,
            "date_to": None,
            "rate_limit_per_hour": 100,
            "daily_cost_cap_cents": 500,
            "cursor": None,
        }
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=mock_row)

        result = await backfill_poll(
            mock_pool,
            connector_type="gmail",
            endpoint_identity="user@example.com",
        )

        assert result is not None
        assert result["params"]["target_categories"] == ["finance", "health"]

    async def test_returns_job_with_cursor_when_present(self):
        """backfill_poll deserializes cursor JSONB correctly."""
        from butlers.tools.switchboard.backfill.connector import backfill_poll

        cursor_data = {"message_id": "msg-500", "page_token": "abc123"}
        mock_row = {
            "id": UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890"),
            "target_categories": ["finance"],
            "date_from": None,
            "date_to": None,
            "rate_limit_per_hour": 100,
            "daily_cost_cap_cents": 500,
            "cursor": cursor_data,
        }
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=mock_row)

        result = await backfill_poll(
            mock_pool,
            connector_type="gmail",
            endpoint_identity="user@example.com",
        )

        assert result is not None
        assert result["cursor"] == cursor_data

    async def test_raises_value_error_on_empty_connector_type(self):
        """backfill_poll raises ValueError when connector_type is empty."""
        from butlers.tools.switchboard.backfill.connector import backfill_poll

        mock_pool = AsyncMock()

        with pytest.raises(ValueError, match="connector_type"):
            await backfill_poll(
                mock_pool,
                connector_type="",
                endpoint_identity="user@example.com",
            )

    async def test_raises_value_error_on_empty_endpoint_identity(self):
        """backfill_poll raises ValueError when endpoint_identity is empty."""
        from butlers.tools.switchboard.backfill.connector import backfill_poll

        mock_pool = AsyncMock()

        with pytest.raises(ValueError, match="endpoint_identity"):
            await backfill_poll(
                mock_pool,
                connector_type="gmail",
                endpoint_identity="",
            )

    async def test_raises_runtime_error_on_db_failure(self):
        """backfill_poll raises RuntimeError when database operation fails."""
        from butlers.tools.switchboard.backfill.connector import backfill_poll

        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(side_effect=Exception("connection refused"))

        with pytest.raises(RuntimeError, match="backfill.poll database error"):
            await backfill_poll(
                mock_pool,
                connector_type="gmail",
                endpoint_identity="user@example.com",
            )


# ---------------------------------------------------------------------------
# backfill_progress: success path
# ---------------------------------------------------------------------------


class TestBackfillProgress:
    """Tests for backfill_progress connector function (backfill.progress MCP tool delegates to)."""

    _JOB_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"

    def _make_mock_pool(
        self,
        *,
        load_row: dict | None = None,
        update_row: dict | None = None,
    ) -> AsyncMock:
        """Build a mock pool that returns load_row on first fetchrow, update_row on second."""
        mock_pool = AsyncMock()
        if load_row is None:
            load_row = {
                "id": UUID(self._JOB_ID),
                "connector_type": "gmail",
                "endpoint_identity": "user@example.com",
                "status": "active",
                "cost_spent_cents": 100,
                "daily_cost_cap_cents": 500,
            }
        if update_row is None:
            update_row = {
                "status": "active",
                "rows_processed": 100,
                "rows_skipped": 5,
                "cost_spent_cents": 120,
            }
        mock_pool.fetchrow = AsyncMock(side_effect=[load_row, update_row])
        return mock_pool

    async def test_returns_active_status_on_batch_progress(self):
        """backfill_progress returns active status for a normal batch update."""
        from butlers.tools.switchboard.backfill.connector import backfill_progress

        mock_pool = self._make_mock_pool()

        result = await backfill_progress(
            mock_pool,
            job_id=self._JOB_ID,
            connector_type="gmail",
            endpoint_identity="user@example.com",
            rows_processed=100,
            rows_skipped=5,
            cost_spent_cents_delta=20,
        )

        assert result == {"status": "active"}

    async def test_marks_job_completed_on_terminal_status(self):
        """backfill_progress returns completed status when connector reports completion."""
        from butlers.tools.switchboard.backfill.connector import backfill_progress

        update_row = {
            "status": "completed",
            "rows_processed": 5000,
            "rows_skipped": 100,
            "cost_spent_cents": 480,
        }
        mock_pool = self._make_mock_pool(update_row=update_row)

        result = await backfill_progress(
            mock_pool,
            job_id=self._JOB_ID,
            connector_type="gmail",
            endpoint_identity="user@example.com",
            rows_processed=500,
            rows_skipped=10,
            cost_spent_cents_delta=5,
            status="completed",
        )

        assert result == {"status": "completed"}

    async def test_returns_cost_capped_when_cap_exceeded(self):
        """backfill_progress returns cost_capped when cumulative cost reaches cap."""
        from butlers.tools.switchboard.backfill.connector import backfill_progress

        load_row = {
            "id": UUID(self._JOB_ID),
            "connector_type": "gmail",
            "endpoint_identity": "user@example.com",
            "status": "active",
            "cost_spent_cents": 490,
            "daily_cost_cap_cents": 500,
        }
        update_row = {
            "status": "cost_capped",
            "rows_processed": 100,
            "rows_skipped": 0,
            "cost_spent_cents": 500,
        }
        mock_pool = self._make_mock_pool(load_row=load_row, update_row=update_row)

        result = await backfill_progress(
            mock_pool,
            job_id=self._JOB_ID,
            connector_type="gmail",
            endpoint_identity="user@example.com",
            rows_processed=100,
            rows_skipped=0,
            cost_spent_cents_delta=15,  # 490 + 15 = 505 >= 500 cap
        )

        assert result == {"status": "cost_capped"}

    async def test_raises_value_error_on_negative_rows_processed(self):
        """backfill_progress raises ValueError when rows_processed < 0."""
        from butlers.tools.switchboard.backfill.connector import backfill_progress

        mock_pool = AsyncMock()

        with pytest.raises(ValueError, match="rows_processed"):
            await backfill_progress(
                mock_pool,
                job_id=self._JOB_ID,
                connector_type="gmail",
                endpoint_identity="user@example.com",
                rows_processed=-1,
                rows_skipped=0,
                cost_spent_cents_delta=0,
            )

    async def test_raises_value_error_on_invalid_connector_status(self):
        """backfill_progress raises ValueError for unrecognized connector status."""
        from butlers.tools.switchboard.backfill.connector import backfill_progress

        mock_pool = AsyncMock()

        with pytest.raises(ValueError, match="Connector may only report status"):
            await backfill_progress(
                mock_pool,
                job_id=self._JOB_ID,
                connector_type="gmail",
                endpoint_identity="user@example.com",
                rows_processed=0,
                rows_skipped=0,
                cost_spent_cents_delta=0,
                status="bogus_status",
            )

    async def test_returns_current_status_when_job_not_active(self):
        """backfill_progress returns the authoritative status immediately if job is paused."""
        from butlers.tools.switchboard.backfill.connector import backfill_progress

        load_row = {
            "id": UUID(self._JOB_ID),
            "connector_type": "gmail",
            "endpoint_identity": "user@example.com",
            "status": "paused",
            "cost_spent_cents": 100,
            "daily_cost_cap_cents": 500,
        }
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=load_row)

        result = await backfill_progress(
            mock_pool,
            job_id=self._JOB_ID,
            connector_type="gmail",
            endpoint_identity="user@example.com",
            rows_processed=0,
            rows_skipped=0,
            cost_spent_cents_delta=0,
        )

        # Only one fetchrow call (the load); no update should happen
        assert mock_pool.fetchrow.call_count == 1
        assert result == {"status": "paused"}

    async def test_raises_value_error_when_job_not_found(self):
        """backfill_progress raises ValueError when job_id doesn't exist."""
        from butlers.tools.switchboard.backfill.connector import backfill_progress

        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=None)

        with pytest.raises(ValueError, match="not found"):
            await backfill_progress(
                mock_pool,
                job_id=self._JOB_ID,
                connector_type="gmail",
                endpoint_identity="user@example.com",
                rows_processed=0,
                rows_skipped=0,
                cost_spent_cents_delta=0,
            )

    async def test_raises_value_error_on_identity_mismatch(self):
        """backfill_progress raises ValueError when connector identity doesn't match job."""
        from butlers.tools.switchboard.backfill.connector import backfill_progress

        load_row = {
            "id": UUID(self._JOB_ID),
            "connector_type": "gmail",
            "endpoint_identity": "other@example.com",  # different identity
            "status": "active",
            "cost_spent_cents": 0,
            "daily_cost_cap_cents": 500,
        }
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=load_row)

        with pytest.raises(ValueError, match="identity mismatch"):
            await backfill_progress(
                mock_pool,
                job_id=self._JOB_ID,
                connector_type="gmail",
                endpoint_identity="user@example.com",  # different from job's identity
                rows_processed=0,
                rows_skipped=0,
                cost_spent_cents_delta=0,
            )
