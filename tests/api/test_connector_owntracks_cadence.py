"""OwnTracks point-cadence diagnostics on connector summaries."""

from __future__ import annotations

import datetime as dt
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI

from butlers.api.db import DatabaseManager
from butlers.api.routers.ingestion_connectors import _get_db_manager

pytestmark = pytest.mark.unit


def _row(data: dict) -> MagicMock:
    row = MagicMock()
    row.__getitem__ = lambda self, key: data[key]
    row.get = lambda key, default=None: data.get(key, default)
    return row


def _registry_row(
    endpoint_identity: str,
    *,
    archived_at: dt.datetime | None = None,
) -> MagicMock:
    now = dt.datetime.now(dt.UTC)
    return _row(
        {
            "connector_type": "owntracks",
            "endpoint_identity": endpoint_identity,
            "state": "healthy",
            "error_message": None,
            "version": "1.0",
            "uptime_s": 3600,
            "last_heartbeat_at": now,
            "first_seen_at": now - dt.timedelta(days=30),
            "counter_messages_ingested": 100,
            "counter_messages_failed": 0,
            "archived_at": archived_at,
        }
    )


def _cadence_row(endpoint_identity: str, point_count: int) -> MagicMock:
    return _row({"endpoint_identity": endpoint_identity, "point_count": point_count})


def _wire_db(app: FastAPI, pool: AsyncMock) -> None:
    db = MagicMock(spec=DatabaseManager)
    db.pool.return_value = pool
    app.dependency_overrides[_get_db_manager] = lambda: db


async def _get_summaries(app: FastAPI, pool: AsyncMock) -> dict:
    _wire_db(app, pool)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/ingestion/connectors/summaries")
    assert response.status_code == 200
    return response.json()["data"]


async def test_sparse_owntracks_points_surface_operational_warning(app: FastAPI) -> None:
    endpoint = "owntracks:phone"
    pool = AsyncMock()
    pool.fetch = AsyncMock(
        side_effect=[
            [_registry_row(endpoint)],
            [],  # hourly ingestion series
            [],  # per-device liveness
            [_cadence_row(endpoint, 3)],
        ]
    )

    data = await _get_summaries(app, pool)

    assert data["owntracks_cadence_available"] is True
    connector = data["connectors"][0]
    assert connector["state"] == "healthy"
    assert connector["liveness"] == "online"
    assert connector["operational_warnings"] == [
        "Only 3 OwnTracks location points were recorded in the last 24 hours. "
        "The operational baseline is 24; use Move mode during waking hours."
    ]


async def test_sufficient_owntracks_points_have_no_cadence_warning(app: FastAPI) -> None:
    endpoint = "owntracks:phone"
    pool = AsyncMock()
    pool.fetch = AsyncMock(
        side_effect=[
            [_registry_row(endpoint)],
            [],
            [],
            [_cadence_row(endpoint, 24)],
        ]
    )

    data = await _get_summaries(app, pool)

    assert data["connectors"][0]["operational_warnings"] == []


async def test_cadence_query_failure_is_explicit_not_an_all_clear(app: FastAPI) -> None:
    endpoint = "owntracks:phone"
    pool = AsyncMock()
    pool.fetch = AsyncMock(
        side_effect=[
            [_registry_row(endpoint)],
            [],
            [],
            Exception("owntracks_points unavailable"),
        ]
    )

    data = await _get_summaries(app, pool)

    assert data["owntracks_cadence_available"] is False
    assert data["connectors"][0]["operational_warnings"] == []


async def test_archived_owntracks_identity_does_not_query_or_warn(app: FastAPI) -> None:
    endpoint = "owntracks:retired-phone"
    pool = AsyncMock()
    pool.fetch = AsyncMock(
        side_effect=[
            [_registry_row(endpoint, archived_at=dt.datetime.now(dt.UTC))],
            [],
            [],
        ]
    )

    data = await _get_summaries(app, pool)

    assert data["owntracks_cadence_available"] is True
    assert data["connectors"][0]["archived"] is True
    assert data["connectors"][0]["operational_warnings"] == []
    assert pool.fetch.await_count == 3
