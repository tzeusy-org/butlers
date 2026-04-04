"""Tests for home butler dashboard API endpoints.

Condensed from 53 tests to ~8 tests (bu-egmz6).
Keeps: paginated structure, serialization, 503 error path, maintenance status classification.
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


def _make_entity_row(entity_id="light.living_room", state="on", area_name="living_room"):
    row = MagicMock()
    domain = entity_id.split(".")[0] if "." in entity_id else entity_id
    row.__getitem__ = lambda self, key: {
        "entity_id": entity_id, "state": state, "domain": domain,
        "attributes": {"friendly_name": "Living Room Light", "area_name": area_name, "area_id": area_name},
        "last_updated": datetime(2026, 3, 1, 10, 0, 0, tzinfo=UTC),
        "captured_at": "2026-03-01T10:05:00+00:00",
        "friendly_name": "Living Room Light",
    }[key]
    return row


def _make_maintenance_row(name="HVAC Filter", next_due_at=None):
    row = MagicMock()
    row.__getitem__ = lambda self, key: {
        "id": uuid4(), "name": name, "category": "hvac",
        "interval_days": 90, "last_completed_at": None,
        "next_due_at": next_due_at, "notes": None,
    }[key]
    return row


def _app_with_mock_db(app: FastAPI, *, fetch_rows=None, fetchval_result=0,
                      fetchrow_result=None, pool_available=True):
    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(return_value=fetch_rows or [])
    mock_pool.fetchval = AsyncMock(return_value=fetchval_result)
    mock_pool.fetchrow = AsyncMock(return_value=fetchrow_result)
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


class TestDevices:
    async def test_returns_paginated_structure(self, app):
        _app_with_mock_db(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/home/devices")
        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body and "meta" in body
        assert "total_count" in body["meta"]

    async def test_device_serialized_correctly(self, app):
        row = _make_entity_row("light.kitchen", state="on")
        _app_with_mock_db(app, fetch_rows=[row], fetchval_result=1)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/home/devices")
        device = resp.json()["data"][0]
        assert device["entity_id"] == "light.kitchen"
        assert device["domain"] == "light"

    async def test_pool_unavailable_returns_503(self, app):
        _app_with_mock_db(app, pool_available=False)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/home/devices")
        assert resp.status_code == 503

    async def test_large_page_size_rejected(self, app):
        _app_with_mock_db(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/home/devices", params={"page_size": 9999})
        assert resp.status_code == 422


class TestMaintenance:
    async def test_returns_list_of_items(self, app):
        row = _make_maintenance_row()
        _app_with_mock_db(app, fetch_rows=[row])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/home/maintenance")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_overdue_status_for_past_due_date(self, app):
        past_due = _NOW - timedelta(days=3)
        row = _make_maintenance_row(next_due_at=past_due)
        _app_with_mock_db(app, fetch_rows=[row])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/home/maintenance")
        items = resp.json()
        assert len(items) == 1
        assert items[0]["status"] == "overdue"

    async def test_ok_status_for_future_due_date(self, app):
        future_due = _NOW + timedelta(days=60)
        row = _make_maintenance_row(next_due_at=future_due)
        _app_with_mock_db(app, fetch_rows=[row])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/home/maintenance")
        items = resp.json()
        assert items[0]["status"] == "ok"
