"""Tests for home butler dashboard API endpoints.

Condensed from 53 tests to ~8 tests (bu-egmz6) → 3 tests (bu-2yw2d).
Keeps: devices 200 + 503 combined, validation 422, maintenance status (parametrized).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
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


def _app_with_mock_db(
    app: FastAPI, *, fetch_rows=None, fetchval_result=0, pool_available=True
):
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
