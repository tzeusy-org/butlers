"""Tests for hourly_events timeseries in GET /api/ingestion/connectors/summaries.

Verifies that each connector entry in the summaries response includes a
24-element ``hourly_events`` array (oldest bucket first, newest last) populated
from ``public.ingestion_events``.

Behavior under test:
  - hourly_events is always present on every connector entry
  - Zero-fill: buckets with no events are 0; array is always exactly length 24
  - Correct per-connector counts: events for connector A do not appear in B
  - Bucket ordering: oldest bucket (index 0) is 23 hours ago; newest (index 23)
    is the current hour
  - Degraded mode: hourly_events remains populated even when Prometheus is
    unreachable (it is DB-backed, not Prometheus-backed)
  - Hourly timeseries failure: if the second fetch fails, connectors fall back
    to all-zeros; the response still returns HTTP 200
  - Empty registry: summaries endpoint with zero connectors still returns 200

bu-5je09
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI

from butlers.api.db import DatabaseManager
from butlers.api.routers.ingestion_connectors import _get_db_manager

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_row(data: dict) -> MagicMock:
    """Build a mock asyncpg record."""
    row = MagicMock()
    row.__getitem__ = lambda self, k: data[k]
    row.get = lambda k, default=None: data.get(k, default)
    return row


def _registry_row(
    *,
    connector_type: str,
    endpoint_identity: str,
    state: str = "healthy",
    first_seen_at: dt.datetime | None = None,
    last_heartbeat_at: dt.datetime | None = None,
) -> MagicMock:
    if first_seen_at is None:
        first_seen_at = dt.datetime(2024, 1, 1, 0, 0, 0, tzinfo=dt.UTC)
    return _make_row(
        {
            "connector_type": connector_type,
            "endpoint_identity": endpoint_identity,
            "state": state,
            "error_message": None,
            "version": "1.0",
            "uptime_s": 3600,
            "last_heartbeat_at": last_heartbeat_at,
            "first_seen_at": first_seen_at,
            "counter_messages_ingested": 10,
            "counter_messages_failed": 0,
        }
    )


def _hourly_row(
    *,
    connector_type: str,
    endpoint_identity: str,
    hour_bucket: dt.datetime,
    event_count: int,
) -> MagicMock:
    return _make_row(
        {
            "connector_type": connector_type,
            "endpoint_identity": endpoint_identity,
            "hour_bucket": hour_bucket,
            "event_count": event_count,
        }
    )


def _make_pool_with_fetch_sequence(fetch_calls: list[list]) -> AsyncMock:
    """Build a pool whose fetch() returns successive results from fetch_calls."""
    pool = AsyncMock()
    pool.fetch = AsyncMock(side_effect=fetch_calls)
    pool.fetchrow = AsyncMock(return_value=None)
    pool.execute = AsyncMock(return_value=None)
    return pool


def _wire_db(app: FastAPI, pool: AsyncMock) -> None:
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = pool
    app.dependency_overrides[_get_db_manager] = lambda: mock_db


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_hourly_events_all_zeros_when_no_events(app: FastAPI) -> None:
    """hourly_events is all zeros when no ingestion events exist for the connector."""
    registry_rows = [_registry_row(connector_type="gmail", endpoint_identity="user@example.com")]
    hourly_rows: list = []  # no events
    pool = _make_pool_with_fetch_sequence([registry_rows, hourly_rows])
    _wire_db(app, pool)

    from butlers.api.routers import ingestion_pipeline as _pip_mod

    _pip_mod._pipeline_cache.clear()

    with patch.dict("os.environ", {"PROMETHEUS_URL": ""}, clear=False):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/ingestion/connectors/summaries")

    assert resp.status_code == 200
    body = resp.json()
    connectors = body["data"]["connectors"]
    assert len(connectors) == 1
    hourly = connectors[0]["hourly_events"]
    assert len(hourly) == 24
    assert all(v == 0 for v in hourly)


async def test_hourly_events_correct_bucket_placement(app: FastAPI) -> None:
    """Events land in the correct bucket index (oldest=0, newest=23)."""
    now = dt.datetime.now(dt.UTC).replace(minute=0, second=0, microsecond=0)
    window_start = now - dt.timedelta(hours=23)

    # Put 5 events in bucket 0 (oldest = window_start) and 7 in bucket 23 (current hour)
    registry_rows = [_registry_row(connector_type="gmail", endpoint_identity="user@example.com")]
    hourly_rows = [
        _hourly_row(
            connector_type="gmail",
            endpoint_identity="user@example.com",
            hour_bucket=window_start,
            event_count=5,
        ),
        _hourly_row(
            connector_type="gmail",
            endpoint_identity="user@example.com",
            hour_bucket=now,
            event_count=7,
        ),
    ]
    pool = _make_pool_with_fetch_sequence([registry_rows, hourly_rows])
    _wire_db(app, pool)

    from butlers.api.routers import ingestion_pipeline as _pip_mod

    _pip_mod._pipeline_cache.clear()

    with patch.dict("os.environ", {"PROMETHEUS_URL": ""}, clear=False):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/ingestion/connectors/summaries")

    assert resp.status_code == 200
    hourly = resp.json()["data"]["connectors"][0]["hourly_events"]
    assert len(hourly) == 24
    assert hourly[0] == 5  # oldest bucket
    assert hourly[23] == 7  # newest bucket
    # All middle buckets should be zero
    assert all(v == 0 for v in hourly[1:23])


async def test_hourly_events_per_connector_isolation(app: FastAPI) -> None:
    """Events for connector A do not appear in connector B's hourly_events."""
    now = dt.datetime.now(dt.UTC).replace(minute=0, second=0, microsecond=0)
    window_start = now - dt.timedelta(hours=23)

    registry_rows = [
        _registry_row(connector_type="gmail", endpoint_identity="alice@example.com"),
        _registry_row(connector_type="telegram_bot", endpoint_identity="bot123"),
    ]
    hourly_rows = [
        # Only gmail has events
        _hourly_row(
            connector_type="gmail",
            endpoint_identity="alice@example.com",
            hour_bucket=window_start + dt.timedelta(hours=10),
            event_count=42,
        ),
    ]
    pool = _make_pool_with_fetch_sequence([registry_rows, hourly_rows])
    _wire_db(app, pool)

    from butlers.api.routers import ingestion_pipeline as _pip_mod

    _pip_mod._pipeline_cache.clear()

    with patch.dict("os.environ", {"PROMETHEUS_URL": ""}, clear=False):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/ingestion/connectors/summaries")

    assert resp.status_code == 200
    connectors = resp.json()["data"]["connectors"]
    by_type = {c["connector_type"]: c for c in connectors}

    gmail_hourly = by_type["gmail"]["hourly_events"]
    telegram_hourly = by_type["telegram_bot"]["hourly_events"]

    assert len(gmail_hourly) == 24
    assert len(telegram_hourly) == 24
    assert gmail_hourly[10] == 42
    assert sum(gmail_hourly) == 42  # only one bucket populated

    # telegram has no events → all zeros
    assert all(v == 0 for v in telegram_hourly)


async def test_hourly_events_present_in_degraded_mode(app: FastAPI) -> None:
    """hourly_events is populated even when Prometheus is unreachable (DB-backed)."""
    now = dt.datetime.now(dt.UTC).replace(minute=0, second=0, microsecond=0)
    window_start = now - dt.timedelta(hours=23)

    registry_rows = [_registry_row(connector_type="gmail", endpoint_identity="user@example.com")]
    hourly_rows = [
        _hourly_row(
            connector_type="gmail",
            endpoint_identity="user@example.com",
            hour_bucket=window_start + dt.timedelta(hours=5),
            event_count=99,
        ),
    ]
    pool = _make_pool_with_fetch_sequence([registry_rows, hourly_rows])
    _wire_db(app, pool)

    from butlers.api.routers import ingestion_pipeline as _pip_mod

    _pip_mod._pipeline_cache.clear()

    # Prometheus NOT configured → aggregates_available=false
    with patch.dict("os.environ", {"PROMETHEUS_URL": ""}, clear=False):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/ingestion/connectors/summaries")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["aggregates_available"] is False

    hourly = data["connectors"][0]["hourly_events"]
    assert len(hourly) == 24
    assert hourly[5] == 99  # bucket 5 has data
    assert sum(hourly) == 99


async def test_hourly_events_fallback_to_zeros_on_query_failure(app: FastAPI) -> None:
    """If the hourly timeseries fetch fails, connectors get all-zeros; response still 200."""
    registry_rows = [_registry_row(connector_type="gmail", endpoint_identity="user@example.com")]

    pool = AsyncMock()
    # First call (registry) succeeds; second call (hourly) raises
    pool.fetch = AsyncMock(side_effect=[registry_rows, Exception("DB error")])
    pool.fetchrow = AsyncMock(return_value=None)
    pool.execute = AsyncMock(return_value=None)
    _wire_db(app, pool)

    from butlers.api.routers import ingestion_pipeline as _pip_mod

    _pip_mod._pipeline_cache.clear()

    with patch.dict("os.environ", {"PROMETHEUS_URL": ""}, clear=False):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/ingestion/connectors/summaries")

    assert resp.status_code == 200
    connectors = resp.json()["data"]["connectors"]
    assert len(connectors) == 1
    hourly = connectors[0]["hourly_events"]
    assert len(hourly) == 24
    assert all(v == 0 for v in hourly)


async def test_hourly_events_empty_registry_returns_200(app: FastAPI) -> None:
    """Summaries endpoint with zero connectors still returns 200 with empty connectors list."""
    # Empty registry — the hourly fetch should NOT be made (no rows → skip)
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchrow = AsyncMock(return_value=None)
    pool.execute = AsyncMock(return_value=None)
    _wire_db(app, pool)

    from butlers.api.routers import ingestion_pipeline as _pip_mod

    _pip_mod._pipeline_cache.clear()

    with patch.dict("os.environ", {"PROMETHEUS_URL": ""}, clear=False):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/ingestion/connectors/summaries")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["connectors"] == []
    # Hourly fetch is skipped when registry is empty (guarded by `if rows:`)
    assert pool.fetch.call_count == 1, (
        f"Expected exactly 1 fetch call (registry only), got {pool.fetch.call_count}"
    )
