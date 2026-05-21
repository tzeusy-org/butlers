"""Tests for backfill.poll and backfill.progress MCP tool functions."""

from __future__ import annotations

import logging
import socket
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

pytestmark = pytest.mark.unit

_JOB_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"


async def test_backfill_poll():
    """backfill_poll returns job dict or None; raises for empty inputs."""
    from butlers.tools.switchboard.backfill.connector import backfill_poll

    # Job available
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(
        return_value={
            "id": UUID(_JOB_ID),
            "target_categories": ["finance"],
            "date_from": None,
            "date_to": None,
            "rate_limit_per_hour": 100,
            "daily_cost_cap_cents": 500,
            "cursor": None,
        }
    )
    result = await backfill_poll(
        pool, connector_type="plaid", endpoint_identity="alice@example.com"
    )
    assert result["job_id"] == _JOB_ID
    assert result["params"]["target_categories"] == ["finance"]

    # No jobs
    pool2 = AsyncMock()
    pool2.fetchrow = AsyncMock(return_value=None)
    assert (
        await backfill_poll(pool2, connector_type="plaid", endpoint_identity="alice@ex.com") is None
    )

    # Validation
    pool3 = AsyncMock()
    with pytest.raises(ValueError, match="connector_type"):
        await backfill_poll(pool3, connector_type="", endpoint_identity="alice@ex.com")
    with pytest.raises(ValueError, match="endpoint_identity"):
        await backfill_poll(pool3, connector_type="plaid", endpoint_identity="")


async def test_backfill_poll_transient_connectivity_logs_warning_without_endpoint(caplog):
    """Transient DB connectivity failures do not emit QA-triggering error logs or PII."""
    from butlers.tools.switchboard.backfill.connector import backfill_poll

    pool = AsyncMock()
    pool.fetchrow = AsyncMock(
        side_effect=socket.gaierror(-3, "Temporary failure in name resolution")
    )

    with caplog.at_level(logging.WARNING), pytest.raises(RuntimeError, match="database error"):
        await backfill_poll(
            pool,
            connector_type="gmail",
            endpoint_identity="alice@example.com",
        )

    assert any(
        "backfill.poll transient connectivity failure" in record.message
        for record in caplog.records
    )
    assert all(record.levelno < logging.ERROR for record in caplog.records)
    assert all("alice@example.com" not in record.message for record in caplog.records)


async def test_backfill_progress_validates_inputs():
    """backfill_progress raises ValueError for negative rows_processed or rows_skipped."""
    from butlers.tools.switchboard.backfill.connector import backfill_progress

    pool = AsyncMock()
    with pytest.raises(ValueError, match="rows_processed"):
        await backfill_progress(
            pool,
            job_id=_JOB_ID,
            connector_type="plaid",
            endpoint_identity="alice@ex.com",
            rows_processed=-1,
            rows_skipped=0,
            cost_spent_cents_delta=0,
        )
    with pytest.raises(ValueError, match="rows_skipped"):
        await backfill_progress(
            pool,
            job_id=_JOB_ID,
            connector_type="plaid",
            endpoint_identity="alice@ex.com",
            rows_processed=0,
            rows_skipped=-1,
            cost_spent_cents_delta=0,
        )


async def test_backfill_progress_transient_connectivity_logs_warning(caplog):
    """Transient update failures are propagated but logged below ERROR."""
    from butlers.tools.switchboard.backfill.connector import backfill_progress

    pool = AsyncMock()
    pool.fetchrow = AsyncMock(
        side_effect=[
            {
                "connector_type": "gmail",
                "endpoint_identity": "alice@example.com",
                "status": "active",
                "cost_spent_cents": 0,
                "daily_cost_cap_cents": 500,
            },
            socket.gaierror(-3, "Temporary failure in name resolution"),
        ]
    )

    with caplog.at_level(logging.WARNING), pytest.raises(RuntimeError, match="database error"):
        await backfill_progress(
            pool,
            job_id=_JOB_ID,
            connector_type="gmail",
            endpoint_identity="alice@example.com",
            rows_processed=1,
            rows_skipped=0,
            cost_spent_cents_delta=0,
        )

    assert any(
        "backfill.progress transient connectivity failure" in record.message
        for record in caplog.records
    )
    assert all(record.levelno < logging.ERROR for record in caplog.records)
    assert all("alice@example.com" not in record.message for record in caplog.records)
    assert any(
        "gmail" in record.message and "<endpoint-redacted>" in record.message
        for record in caplog.records
        if "transient connectivity failure" in record.message
    )
