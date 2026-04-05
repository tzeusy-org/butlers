"""Tests for backfill.poll and backfill.progress MCP tool functions.

Verifies importability, key success paths, and critical validation.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

pytestmark = pytest.mark.unit


async def test_backfill_poll_returns_job_when_available():
    """backfill_poll returns a structured job dict when a pending job exists."""
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
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=mock_row)

    result = await backfill_poll(
        pool, connector_type="plaid", endpoint_identity="alice@example.com"
    )
    result_data = json.loads(result) if isinstance(result, str) else result
    assert result_data["job_id"] == _JOB_ID
    assert result_data["target_categories"] == ["finance"]


async def test_backfill_poll_returns_none_when_no_jobs():
    """backfill_poll returns no-job response when no pending jobs exist."""
    from butlers.tools.switchboard.backfill.connector import backfill_poll

    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)

    result = await backfill_poll(pool, connector_type="plaid", endpoint_identity="alice@ex.com")
    result_data = json.loads(result) if isinstance(result, str) else result
    assert result_data.get("job_id") is None


async def test_backfill_poll_validates_empty_inputs():
    """backfill_poll raises ValueError for empty connector_type or endpoint_identity."""
    from butlers.tools.switchboard.backfill.connector import backfill_poll

    pool = AsyncMock()
    with pytest.raises(ValueError, match="connector_type"):
        await backfill_poll(pool, connector_type="", endpoint_identity="alice@ex.com")
    with pytest.raises(ValueError, match="endpoint_identity"):
        await backfill_poll(pool, connector_type="plaid", endpoint_identity="")


async def test_backfill_progress_active_status():
    """backfill_progress returns active status on batch progress."""
    from butlers.tools.switchboard.backfill.connector import backfill_progress

    _JOB_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    active_row = {
        "id": UUID(_JOB_ID),
        "status": "in_progress",
        "rows_processed": 0,
        "daily_cost_cents": 0,
        "daily_cost_cap_cents": 500,
        "cursor": None,
    }
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=active_row)

    result = await backfill_progress(
        pool,
        job_id=_JOB_ID,
        connector_type="plaid",
        endpoint_identity="alice@ex.com",
        rows_processed=50,
        cost_cents=25,
        status="active",
    )
    result_data = json.loads(result) if isinstance(result, str) else result
    assert result_data["status"] in ("active", "in_progress")


async def test_backfill_progress_validates_negative_rows():
    """backfill_progress raises ValueError for negative rows_processed."""
    from butlers.tools.switchboard.backfill.connector import backfill_progress

    pool = AsyncMock()
    with pytest.raises(ValueError):
        await backfill_progress(
            pool, job_id="job-1", connector_type="plaid",
            endpoint_identity="alice@ex.com", rows_processed=-1, cost_cents=0, status="active",
        )
