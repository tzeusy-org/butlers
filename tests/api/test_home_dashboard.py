"""Tests for home butler dashboard API endpoints.

Condensed from 53 tests to ~8 tests (bu-egmz6) → 3 tests (bu-2yw2d).
Keeps: devices 200 + 503 combined, validation 422, maintenance status (parametrized).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from butlers.api.db import DatabaseManager

pytestmark = pytest.mark.unit

_NOW = datetime.now(UTC)


def _make_entity_row(entity_id="light.living_room", state="on"):
    row = MagicMock()
    domain = entity_id.split(".")[0] if "." in entity_id else entity_id
    row.__getitem__ = lambda self, key: {
        "entity_id": entity_id,
        "state": state,
        "domain": domain,
        "attributes": {"friendly_name": "Light", "area_name": "living_room", "area_id": "lr"},
        "last_updated": datetime(2026, 3, 1, 10, 0, 0, tzinfo=UTC),
        "captured_at": "2026-03-01T10:05:00+00:00",
        "friendly_name": "Light",
    }[key]
    return row


def _make_maintenance_row(next_due_at=None):
    row = MagicMock()
    row.__getitem__ = lambda self, key: {
        "id": uuid4(),
        "name": "HVAC Filter",
        "category": "hvac",
        "interval_days": 90,
        "last_completed_at": None,
        "next_due_at": next_due_at,
        "notes": None,
    }[key]
    return row


def _app_with_mock_db(app: FastAPI, *, fetch_rows=None, fetchval_result=0, pool_available=True):
    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(return_value=fetch_rows or [])
    mock_pool.fetchval = AsyncMock(return_value=fetchval_result)
    mock_pool.fetchrow = AsyncMock(return_value=None)
    mock_pool.execute = AsyncMock(return_value=None)

    mock_db = MagicMock(spec=DatabaseManager)
    if pool_available:
        mock_db.pool.return_value = mock_pool
    else:
        mock_db.pool.side_effect = KeyError("No pool for butler: home")

    for butler_name, router_module in app.state.butler_routers:
        if butler_name == "home" and hasattr(router_module, "_get_db_manager"):
            app.dependency_overrides[router_module._get_db_manager] = lambda: mock_db
            break

    return app, mock_pool


# ---------------------------------------------------------------------------
# Devices — 200 structure + 503 fallback
# ---------------------------------------------------------------------------


async def test_devices_200_and_503(app):
    row = _make_entity_row("light.kitchen")
    _app_with_mock_db(app, fetch_rows=[row], fetchval_result=1)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/home/devices")
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body and "meta" in body
    assert body["data"][0]["entity_id"] == "light.kitchen"

    # 503 when pool unavailable
    _app_with_mock_db(app, pool_available=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp_503 = await client.get("/api/home/devices")
    assert resp_503.status_code == 503


# ---------------------------------------------------------------------------
# Devices — large page size rejected
# ---------------------------------------------------------------------------


async def test_devices_large_page_size_rejected(app):
    _app_with_mock_db(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/home/devices", params={"page_size": 9999})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Maintenance — status classification (parametrized)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "due_offset_days,expected_status",
    [(-3, "overdue"), (60, "ok")],
    ids=["overdue", "ok"],
)
async def test_maintenance_status_classification(app, due_offset_days, expected_status):
    due = _NOW + timedelta(days=due_offset_days)
    row = _make_maintenance_row(next_due_at=due)
    _app_with_mock_db(app, fetch_rows=[row])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/home/maintenance")
    assert resp.status_code == 200
    assert resp.json()[0]["status"] == expected_status


# ---------------------------------------------------------------------------
# Atmosphere feed — not configured / healthy / degraded, and location patch
# ---------------------------------------------------------------------------


def _home_router_module(app: FastAPI):
    for butler_name, router_module in app.state.butler_routers:
        if butler_name == "home" and hasattr(router_module, "_get_db_manager"):
            return router_module
    raise AssertionError("home router module not found")


async def test_atmosphere_current_not_configured(app):
    _app, pool = _app_with_mock_db(app)
    pool.fetchrow = AsyncMock(return_value=None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/home/atmosphere/current")
    assert resp.status_code == 200
    body = resp.json()
    assert body["configured"] is False
    assert body["temperature_c"] is None
    assert body["stale"] is False
    assert body["source_error"] is False


async def test_atmosphere_current_healthy(app):
    _app, pool = _app_with_mock_db(app)

    status_row = MagicMock()
    status_row.__getitem__ = lambda self, key: {
        "configured": True,
        "latitude": 40.0,
        "longitude": -74.0,
        "last_success_at": _NOW,
        "last_error": None,
        "consecutive_failures": 0,
    }[key]

    reading_row = MagicMock()
    reading_row.__getitem__ = lambda self, key: {
        "observed_at": _NOW,
        "temperature_c": 21.5,
        "apparent_temperature_c": 20.0,
        "relative_humidity_pct": 55.0,
        "precipitation_mm": 0.0,
        "weather_code": 1,
        "wind_speed_kph": 12.0,
        "aqi_us": 42,
        "aqi_european": 20,
        "pm2_5": 5.1,
        "pm10": 8.2,
        "pollen_tree": None,
        "pollen_grass": None,
        "pollen_weed": None,
        "pollen_available": False,
        "fetched_at": _NOW,
    }[key]

    pool.fetchrow = AsyncMock(side_effect=[status_row, reading_row])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/home/atmosphere/current")
    assert resp.status_code == 200
    body = resp.json()
    assert body["configured"] is True
    assert body["temperature_c"] == 21.5
    assert body["stale"] is False
    assert body["source_error"] is False
    assert body["pollen_available"] is False


async def test_atmosphere_current_degraded_when_fetch_failing(app):
    _app, pool = _app_with_mock_db(app)

    status_row = MagicMock()
    status_row.__getitem__ = lambda self, key: {
        "configured": True,
        "latitude": 40.0,
        "longitude": -74.0,
        "last_success_at": _NOW - timedelta(hours=5),
        "last_error": "connect timeout",
        "consecutive_failures": 3,
    }[key]

    pool.fetchrow = AsyncMock(side_effect=[status_row, None])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/home/atmosphere/current")
    assert resp.status_code == 200
    body = resp.json()
    assert body["configured"] is True
    assert body["stale"] is True
    assert body["source_error"] is True
    assert body["last_error"] == "connect timeout"


async def test_atmosphere_location_patch_success_and_no_owner(app):
    router_module = _home_router_module(app)
    _app_with_mock_db(app)

    with patch.object(
        router_module, "upsert_owner_entity_info", AsyncMock(return_value=True)
    ) as mock_upsert:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.patch(
                "/api/home/atmosphere/location",
                json={"latitude": 40.7128, "longitude": -74.0060},
            )
    assert resp.status_code == 200
    assert resp.json() == {"latitude": 40.7128, "longitude": -74.0060}
    mock_upsert.assert_awaited_once()
    assert mock_upsert.await_args.args[1] == "home_coordinates"
    assert mock_upsert.await_args.args[2] == "40.7128,-74.006"

    with patch.object(router_module, "upsert_owner_entity_info", AsyncMock(return_value=False)):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.patch(
                "/api/home/atmosphere/location",
                json={"latitude": 40.7128, "longitude": -74.0060},
            )
    assert resp.status_code == 503


async def test_atmosphere_location_patch_validates_bounds(app):
    _app_with_mock_db(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.patch(
            "/api/home/atmosphere/location",
            json={"latitude": 400.0, "longitude": -74.0060},
        )
    assert resp.status_code == 422
