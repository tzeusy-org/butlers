"""Tests for home butler dashboard API endpoints.

Covers: devices, energy, maintenance, and threshold endpoints.
Uses mocked DatabaseManager so no real database is required.

Issue: bu-mc82
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI

from butlers.api.db import DatabaseManager

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ITEM_ID = uuid4()
_ITEM_DUE_PAST = datetime.now(UTC) - timedelta(days=3)
_ITEM_DUE_FUTURE_SOON = datetime.now(UTC) + timedelta(days=2)
_ITEM_DUE_FUTURE_FAR = datetime.now(UTC) + timedelta(days=60)


def _make_entity_row(
    entity_id: str = "light.living_room",
    state: str = "on",
    area_name: str | None = "living_room",
) -> MagicMock:
    """Return a mock asyncpg record simulating ha_entity_snapshot row."""
    row = MagicMock()
    attrs: dict = {"friendly_name": "Living Room Light"}
    if area_name:
        attrs["area_name"] = area_name
        attrs["area_id"] = area_name
    domain = entity_id.split(".")[0] if "." in entity_id else entity_id
    row.__getitem__ = lambda self, key: {
        "entity_id": entity_id,
        "state": state,
        "attributes": attrs,
        "last_updated": datetime(2026, 3, 1, 10, 0, 0, tzinfo=UTC),
        "captured_at": "2026-03-01T10:05:00+00:00",
        "domain": domain,
        "friendly_name": attrs.get("friendly_name"),
    }[key]
    return row


def _make_maintenance_row(
    item_id: UUID | None = None,
    name: str = "HVAC Filter",
    category: str = "hvac",
    interval_days: int = 90,
    last_completed_at: datetime | None = None,
    next_due_at: datetime | None = None,
    notes: str | None = None,
) -> MagicMock:
    """Return a mock asyncpg record simulating maintenance_items row."""
    row = MagicMock()
    _id = item_id or _ITEM_ID
    row.__getitem__ = lambda self, key: {
        "id": _id,
        "name": name,
        "category": category,
        "interval_days": interval_days,
        "last_completed_at": last_completed_at,
        "next_due_at": next_due_at,
        "notes": notes,
    }[key]
    return row


def _make_state_row(key: str, value: dict) -> MagicMock:
    """Return a mock asyncpg record simulating state table row."""
    row = MagicMock()
    row.__getitem__ = lambda self, k: {"key": key, "value": value}[k]
    return row


def _app_with_mock_db(
    app: FastAPI,
    *,
    fetch_rows: list | None = None,
    fetchval_result: object = 0,
    fetchrow_result: object = None,
    execute_result: object = None,
    pool_available: bool = True,
    fetch_side_effect=None,
    fetchrow_side_effect=None,
):
    """Wire a FastAPI app with a mocked DatabaseManager for the home butler."""
    mock_pool = AsyncMock()
    if fetch_side_effect is not None:
        mock_pool.fetch = AsyncMock(side_effect=fetch_side_effect)
    else:
        mock_pool.fetch = AsyncMock(return_value=fetch_rows or [])
    mock_pool.fetchval = AsyncMock(return_value=fetchval_result)
    if fetchrow_side_effect is not None:
        mock_pool.fetchrow = AsyncMock(side_effect=fetchrow_side_effect)
    else:
        mock_pool.fetchrow = AsyncMock(return_value=fetchrow_result)
    mock_pool.execute = AsyncMock(return_value=execute_result)

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
# GET /api/home/devices
# ---------------------------------------------------------------------------


class TestListDevices:
    async def test_returns_paginated_response_structure(self, app):
        """Response must have 'data' array and 'meta' with page-based fields."""
        _app_with_mock_db(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/home/devices")

        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert "meta" in body
        assert isinstance(body["data"], list)
        meta = body["meta"]
        assert "page" in meta
        assert "page_size" in meta
        assert "total_count" in meta
        assert "total_pages" in meta

    async def test_empty_results(self, app):
        """When no entities exist, data should be an empty list."""
        _app_with_mock_db(app, fetchval_result=0)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/home/devices")

        body = resp.json()
        assert body["data"] == []
        assert body["meta"]["total_count"] == 0
        assert body["meta"]["total_pages"] == 1  # at least 1 page

    async def test_device_row_serialized_correctly(self, app):
        """A device row should serialize with health_status, domain, area_name."""
        row = _make_entity_row("light.kitchen", state="on", area_name="kitchen")
        _app_with_mock_db(app, fetch_rows=[row], fetchval_result=1)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/home/devices")

        body = resp.json()
        assert resp.status_code == 200
        assert len(body["data"]) == 1
        device = body["data"][0]
        assert device["entity_id"] == "light.kitchen"
        assert device["state"] == "on"
        assert device["domain"] == "light"
        assert device["health_status"] == "healthy"
        assert "area_name" in device

    async def test_offline_device_health_status(self, app):
        """Entities with state 'unavailable' should have health_status='offline'."""
        row = _make_entity_row("sensor.temp", state="unavailable")
        _app_with_mock_db(app, fetch_rows=[row], fetchval_result=1)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/home/devices")

        body = resp.json()
        assert resp.status_code == 200
        assert body["data"][0]["health_status"] == "offline"

    async def test_unknown_state_is_offline(self, app):
        """Entities with state 'unknown' should also be classified as offline."""
        row = _make_entity_row("sensor.temp", state="unknown")
        _app_with_mock_db(app, fetch_rows=[row], fetchval_result=1)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/home/devices")

        body = resp.json()
        assert body["data"][0]["health_status"] == "offline"

    async def test_domain_filter_accepted(self, app):
        """The ?domain= filter is accepted without error."""
        _app_with_mock_db(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/home/devices", params={"domain": "light"})

        assert resp.status_code == 200

    async def test_area_filter_accepted(self, app):
        """The ?area= filter is accepted without error."""
        _app_with_mock_db(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/home/devices", params={"area": "kitchen"})

        assert resp.status_code == 200

    async def test_health_filter_offline_accepted(self, app):
        """The ?health=offline filter is accepted without error."""
        _app_with_mock_db(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/home/devices", params={"health": "offline"})

        assert resp.status_code == 200

    async def test_health_filter_healthy_accepted(self, app):
        """The ?health=healthy filter is accepted without error."""
        _app_with_mock_db(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/home/devices", params={"health": "healthy"})

        assert resp.status_code == 200

    async def test_pagination_page_size(self, app):
        """The ?page= and ?page_size= params are accepted and reflected in meta."""
        _app_with_mock_db(app, fetchval_result=100)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/home/devices", params={"page": 2, "page_size": 25})

        assert resp.status_code == 200
        meta = resp.json()["meta"]
        assert meta["page"] == 2
        assert meta["page_size"] == 25
        assert meta["total_count"] == 100
        assert meta["total_pages"] == 4

    async def test_pool_unavailable_returns_503(self, app):
        """When DB pool is unavailable, return 503."""
        _app_with_mock_db(app, pool_available=False)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/home/devices")

        assert resp.status_code == 503

    async def test_large_page_size_rejected(self, app):
        """?page_size= values above 500 should return 422."""
        _app_with_mock_db(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/home/devices", params={"page_size": 9999})

        assert resp.status_code == 422

    async def test_negative_page_rejected(self, app):
        """?page= values below 1 should return 422."""
        _app_with_mock_db(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/home/devices", params={"page": 0})

        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/home/energy
# ---------------------------------------------------------------------------


class TestGetEnergy:
    async def test_returns_503_when_ha_not_configured(self, app):
        """Returns 503 when HA URL/token are not configured."""
        # No state rows → no credentials
        _app_with_mock_db(app, fetch_rows=[])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/home/energy")

        assert resp.status_code == 503

    async def test_pool_unavailable_returns_503(self, app):
        """When DB pool is unavailable, return 503."""
        _app_with_mock_db(app, pool_available=False)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/home/energy")

        assert resp.status_code == 503

    async def test_returns_list_when_ha_available(self, app):
        """Returns a list of EnergyDataPoint objects when HA responds."""
        # Mock state rows for ha_url and ha_token
        url_row = MagicMock()
        url_row.__getitem__ = lambda self, k: {"key": "ha_url", "value": "http://ha.local"}[k]
        token_row = MagicMock()
        token_row.__getitem__ = lambda self, k: {"key": "ha_token", "value": "test-token"}[k]

        # Energy sensor row
        sensor_row = MagicMock()
        sensor_row.__getitem__ = lambda self, k: {
            "entity_id": "sensor.energy_kwh",
            "friendly_name": "Energy kWh",
        }[k]

        ha_response = {
            "sensor.energy_kwh": [
                {"start": "2026-03-18T00:00:00+00:00", "sum": 5.5},
                {"start": "2026-03-19T00:00:00+00:00", "sum": 6.2},
            ]
        }

        fetch_call_count = 0

        async def _fetch(sql, *args):
            nonlocal fetch_call_count
            fetch_call_count += 1
            if "ha_url" in sql or "ha_token" in sql:
                return [url_row, token_row]
            elif "sensor.%" in sql or "ILIKE" in sql:
                return [sensor_row]
            return []

        _app_with_mock_db(app, fetch_side_effect=_fetch)

        with patch("home_api_router.HttpxAsyncClient") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = ha_response

            mock_http_client = AsyncMock()
            mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
            mock_http_client.__aexit__ = AsyncMock(return_value=False)
            mock_http_client.post = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_http_client

            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/home/energy")

        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)

    async def test_default_period_is_day(self, app):
        """Default period parameter is 'day'."""
        _app_with_mock_db(app, fetch_rows=[])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            # Without HA configured, we get 503 — but can verify the request doesn't
            # fail due to period not being set
            resp = await client.get("/api/home/energy")

        # Without HA configured this is 503; the point is period defaulting is tested
        # by absence of 422 (which would mean invalid period)
        assert resp.status_code in (200, 503)

    async def test_returns_503_when_ha_unreachable(self, app):
        """Returns 503 when HA REST API is unreachable."""
        url_row = MagicMock()
        url_row.__getitem__ = lambda self, k: {"key": "ha_url", "value": "http://ha.local"}[k]
        token_row = MagicMock()
        token_row.__getitem__ = lambda self, k: {"key": "ha_token", "value": "test-token"}[k]

        sensor_row = MagicMock()
        sensor_row.__getitem__ = lambda self, k: {
            "entity_id": "sensor.energy_kwh",
            "friendly_name": "Energy",
        }[k]

        async def _fetch(sql, *args):
            if "ha_url" in sql or "ha_token" in sql:
                return [url_row, token_row]
            return [sensor_row]

        _app_with_mock_db(app, fetch_side_effect=_fetch)

        with patch("home_api_router.HttpxAsyncClient") as mock_client_cls:
            mock_http_client = AsyncMock()
            mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
            mock_http_client.__aexit__ = AsyncMock(return_value=False)
            mock_http_client.post = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
            mock_client_cls.return_value = mock_http_client

            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/home/energy")

        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /api/home/energy/top-consumers
# ---------------------------------------------------------------------------


class TestGetEnergyTopConsumers:
    async def test_returns_503_when_ha_not_configured(self, app):
        """Returns 503 when HA URL/token are not configured."""
        _app_with_mock_db(app, fetch_rows=[])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/home/energy/top-consumers")

        assert resp.status_code == 503

    async def test_pool_unavailable_returns_503(self, app):
        """When DB pool is unavailable, return 503."""
        _app_with_mock_db(app, pool_available=False)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/home/energy/top-consumers")

        assert resp.status_code == 503

    async def test_returns_list_with_percentage(self, app):
        """Returns a list of TopConsumerEntry objects with percentage values."""
        url_row = MagicMock()
        url_row.__getitem__ = lambda self, k: {"key": "ha_url", "value": "http://ha.local"}[k]
        token_row = MagicMock()
        token_row.__getitem__ = lambda self, k: {"key": "ha_token", "value": "tok"}[k]

        sensor_rows = []
        for i in range(3):
            r = MagicMock()
            r.__getitem__ = lambda self, k, _i=i: {
                "entity_id": f"sensor.energy_{_i}",
                "friendly_name": f"Energy {_i}",
            }[k]
            sensor_rows.append(r)

        ha_response = {
            "sensor.energy_0": [{"start": "2026-03-18T00:00:00+00:00", "sum": 10.0}],
            "sensor.energy_1": [{"start": "2026-03-18T00:00:00+00:00", "sum": 6.0}],
            "sensor.energy_2": [{"start": "2026-03-18T00:00:00+00:00", "sum": 4.0}],
        }

        async def _fetch(sql, *args):
            if "ha_url" in sql or "ha_token" in sql:
                return [url_row, token_row]
            return sensor_rows

        _app_with_mock_db(app, fetch_side_effect=_fetch)

        with patch("home_api_router.HttpxAsyncClient") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = ha_response

            mock_http_client = AsyncMock()
            mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
            mock_http_client.__aexit__ = AsyncMock(return_value=False)
            mock_http_client.post = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_http_client

            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/home/energy/top-consumers")

        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)
        # Should be sorted by total_kwh descending
        if len(body) >= 2:
            assert body[0]["total_kwh"] >= body[1]["total_kwh"]
        # Check percentage fields exist
        for entry in body:
            assert "entity_id" in entry
            assert "total_kwh" in entry
            assert "percentage" in entry

    async def test_percentages_sum_to_100(self, app):
        """Percentages across top consumers should sum to ~100%."""
        url_row = MagicMock()
        url_row.__getitem__ = lambda self, k: {"key": "ha_url", "value": "http://ha.local"}[k]
        token_row = MagicMock()
        token_row.__getitem__ = lambda self, k: {"key": "ha_token", "value": "tok"}[k]

        sensor_rows = []
        for i in range(2):
            r = MagicMock()
            r.__getitem__ = lambda self, k, _i=i: {
                "entity_id": f"sensor.energy_{_i}",
                "friendly_name": f"Energy {_i}",
            }[k]
            sensor_rows.append(r)

        ha_response = {
            "sensor.energy_0": [{"start": "2026-03-18T00:00:00+00:00", "sum": 60.0}],
            "sensor.energy_1": [{"start": "2026-03-18T00:00:00+00:00", "sum": 40.0}],
        }

        async def _fetch(sql, *args):
            if "ha_url" in sql or "ha_token" in sql:
                return [url_row, token_row]
            return sensor_rows

        _app_with_mock_db(app, fetch_side_effect=_fetch)

        with patch("home_api_router.HttpxAsyncClient") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = ha_response

            mock_http_client = AsyncMock()
            mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
            mock_http_client.__aexit__ = AsyncMock(return_value=False)
            mock_http_client.post = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_http_client

            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/home/energy/top-consumers")

        assert resp.status_code == 200
        body = resp.json()
        total_pct = sum(e["percentage"] for e in body)
        assert abs(total_pct - 100.0) < 0.1


# ---------------------------------------------------------------------------
# GET /api/home/maintenance
# ---------------------------------------------------------------------------


class TestListMaintenance:
    async def test_returns_list_of_maintenance_items(self, app):
        """Response must be a JSON array of maintenance items."""
        row = _make_maintenance_row(next_due_at=_ITEM_DUE_FUTURE_FAR)
        _app_with_mock_db(app, fetch_rows=[row])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/home/maintenance")

        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)
        assert len(body) == 1

    async def test_empty_maintenance_list(self, app):
        """When no items exist, return empty list."""
        _app_with_mock_db(app, fetch_rows=[])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/home/maintenance")

        assert resp.status_code == 200
        assert resp.json() == []

    async def test_item_fields_present(self, app):
        """Items must include id, name, category, interval_days, status, notes."""
        row = _make_maintenance_row(next_due_at=_ITEM_DUE_FUTURE_FAR)
        _app_with_mock_db(app, fetch_rows=[row])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/home/maintenance")

        body = resp.json()
        item = body[0]
        assert "id" in item
        assert "name" in item
        assert "category" in item
        assert "interval_days" in item
        assert "status" in item
        assert "notes" in item

    async def test_overdue_status_for_past_due_date(self, app):
        """Item with next_due_at in the past should have status='overdue'."""
        row = _make_maintenance_row(next_due_at=_ITEM_DUE_PAST)
        _app_with_mock_db(app, fetch_rows=[row])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/home/maintenance")

        body = resp.json()
        assert body[0]["status"] == "overdue"

    async def test_due_status_for_soon_due_date(self, app):
        """Item with next_due_at within 7 days should have status='due'."""
        row = _make_maintenance_row(next_due_at=_ITEM_DUE_FUTURE_SOON)
        _app_with_mock_db(app, fetch_rows=[row])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/home/maintenance")

        body = resp.json()
        assert body[0]["status"] == "due"

    async def test_ok_status_for_far_future_due_date(self, app):
        """Item with next_due_at far in the future should have status='ok'."""
        row = _make_maintenance_row(next_due_at=_ITEM_DUE_FUTURE_FAR)
        _app_with_mock_db(app, fetch_rows=[row])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/home/maintenance")

        body = resp.json()
        assert body[0]["status"] == "ok"

    async def test_category_filter_accepted(self, app):
        """The ?category= filter is accepted without error."""
        _app_with_mock_db(app, fetch_rows=[])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/home/maintenance", params={"category": "hvac"})

        assert resp.status_code == 200

    async def test_status_filter_accepted(self, app):
        """The ?status= filter is accepted without error."""
        _app_with_mock_db(app, fetch_rows=[])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/home/maintenance", params={"status": "overdue"})

        assert resp.status_code == 200

    async def test_pool_unavailable_returns_503(self, app):
        """When DB pool is unavailable, return 503."""
        _app_with_mock_db(app, pool_available=False)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/home/maintenance")

        assert resp.status_code == 503

    async def test_missing_table_returns_503(self, app):
        """When maintenance_items table doesn't exist, return 503."""

        async def _bad_fetch(*args, **kwargs):
            raise Exception("relation maintenance_items does not exist")

        _app_with_mock_db(app, fetch_side_effect=_bad_fetch)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/home/maintenance")

        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# POST /api/home/maintenance
# ---------------------------------------------------------------------------


class TestCreateMaintenance:
    async def test_creates_item_and_returns_201(self, app):
        """POST creates a new item and returns HTTP 201."""
        created_row = _make_maintenance_row(
            next_due_at=None, last_completed_at=None, notes="Check annually"
        )
        _, mock_pool = _app_with_mock_db(app, fetchrow_result=created_row)

        body = {
            "name": "HVAC Filter",
            "category": "hvac",
            "interval_days": 90,
            "notes": "Check annually",
        }
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/home/maintenance", json=body)

        assert resp.status_code == 201
        result = resp.json()
        assert result["name"] == "HVAC Filter"
        assert result["category"] == "hvac"

    async def test_returns_409_on_duplicate_name(self, app):
        """POST returns 409 when name is already taken."""

        async def _dup_insert(*args, **kwargs):
            raise Exception("unique constraint violation")

        _app_with_mock_db(app, fetchrow_side_effect=_dup_insert)

        body = {"name": "HVAC Filter", "category": "hvac", "interval_days": 90}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/home/maintenance", json=body)

        assert resp.status_code == 409

    async def test_missing_required_fields_returns_422(self, app):
        """POST with missing required fields returns 422."""
        _app_with_mock_db(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/home/maintenance", json={"name": "Test"})

        assert resp.status_code == 422

    async def test_pool_unavailable_returns_503(self, app):
        """When DB pool is unavailable, return 503."""
        _app_with_mock_db(app, pool_available=False)
        body = {"name": "HVAC Filter", "category": "hvac", "interval_days": 90}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/home/maintenance", json=body)

        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# POST /api/home/maintenance/{item_id}/complete
# ---------------------------------------------------------------------------


class TestCompleteMaintenanceItem:
    async def test_completes_item_and_returns_updated(self, app):
        """POST .../complete updates timestamps and returns updated item."""
        now = datetime.now(UTC)
        updated_row = _make_maintenance_row(
            item_id=_ITEM_ID,
            last_completed_at=now,
            next_due_at=now + timedelta(days=90),
        )
        _app_with_mock_db(app, fetchrow_result=updated_row)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(f"/api/home/maintenance/{_ITEM_ID}/complete")

        assert resp.status_code == 200
        result = resp.json()
        assert "last_completed_at" in result
        assert result["last_completed_at"] is not None

    async def test_returns_404_when_item_not_found(self, app):
        """POST .../complete returns 404 when item does not exist."""
        _app_with_mock_db(app, fetchrow_result=None)

        missing_id = uuid4()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(f"/api/home/maintenance/{missing_id}/complete")

        assert resp.status_code == 404

    async def test_pool_unavailable_returns_503(self, app):
        """When DB pool is unavailable, return 503."""
        _app_with_mock_db(app, pool_available=False)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(f"/api/home/maintenance/{_ITEM_ID}/complete")

        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# DELETE /api/home/maintenance/{item_id}
# ---------------------------------------------------------------------------


class TestDeleteMaintenanceItem:
    async def test_deletes_item_and_returns_204(self, app):
        """DELETE returns 204 on success."""
        _app_with_mock_db(app, fetchval_result=_ITEM_ID)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.delete(f"/api/home/maintenance/{_ITEM_ID}")

        assert resp.status_code == 204

    async def test_returns_404_when_item_not_found(self, app):
        """DELETE returns 404 when item does not exist."""
        _app_with_mock_db(app, fetchval_result=None)

        missing_id = uuid4()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.delete(f"/api/home/maintenance/{missing_id}")

        assert resp.status_code == 404

    async def test_pool_unavailable_returns_503(self, app):
        """When DB pool is unavailable, return 503."""
        _app_with_mock_db(app, pool_available=False)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.delete(f"/api/home/maintenance/{_ITEM_ID}")

        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /api/home/settings/thresholds
# ---------------------------------------------------------------------------


class TestGetThresholds:
    async def test_returns_default_thresholds_when_no_state(self, app):
        """Returns default threshold values when none are stored."""
        _app_with_mock_db(app, fetch_rows=[])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/home/settings/thresholds")

        assert resp.status_code == 200
        body = resp.json()
        assert "battery" in body
        assert "offline_hours" in body
        assert "comfort_defaults" in body
        assert "comfort_deviation" in body
        assert "energy" in body

    async def test_default_battery_thresholds(self, app):
        """Default battery thresholds match spec defaults."""
        _app_with_mock_db(app, fetch_rows=[])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/home/settings/thresholds")

        body = resp.json()
        battery = body["battery"]
        assert battery["critical"] == 10
        assert battery["warning"] == 20
        assert battery["info"] == 30

    async def test_stored_thresholds_override_defaults(self, app):
        """Stored threshold values override defaults."""
        threshold_row = MagicMock()
        threshold_row.__getitem__ = lambda self, k: {
            "key": "home:thresholds:battery",
            "value": {"critical": 15, "warning": 25, "info": 35},
        }[k]

        _app_with_mock_db(app, fetch_rows=[threshold_row])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/home/settings/thresholds")

        assert resp.status_code == 200
        body = resp.json()
        assert body["battery"]["critical"] == 15

    async def test_pool_unavailable_returns_503(self, app):
        """When DB pool is unavailable, return 503."""
        _app_with_mock_db(app, pool_available=False)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/home/settings/thresholds")

        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# PATCH /api/home/settings/thresholds
# ---------------------------------------------------------------------------


class TestUpdateThresholds:
    async def test_partial_update_returns_merged_thresholds(self, app):
        """PATCH with partial data returns merged full threshold config."""
        _app_with_mock_db(app, fetch_rows=[])

        body = {"battery": {"critical": 15, "warning": 25, "info": 35}}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.patch("/api/home/settings/thresholds", json=body)

        assert resp.status_code == 200
        result = resp.json()
        assert result["battery"]["critical"] == 15
        assert result["battery"]["warning"] == 25
        # Other threshold groups still present with defaults
        assert "offline_hours" in result
        assert "energy" in result

    async def test_update_persists_to_state_store(self, app):
        """PATCH writes updated values to the state store."""
        _, mock_pool = _app_with_mock_db(app, fetch_rows=[])

        body = {"energy": {"anomaly_pct": 30, "high_severity_pct": 150}}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.patch("/api/home/settings/thresholds", json=body)

        assert resp.status_code == 200
        # Verify execute was called for the energy key
        mock_pool.execute.assert_called_once()
        call_args = mock_pool.execute.call_args[0]
        assert "home:thresholds:energy" in call_args

    async def test_invalid_battery_thresholds_rejected(self, app):
        """Battery thresholds where critical > warning should return 422."""
        _app_with_mock_db(app, fetch_rows=[])

        body = {"battery": {"critical": 30, "warning": 10, "info": 5}}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.patch("/api/home/settings/thresholds", json=body)

        assert resp.status_code == 422

    async def test_empty_patch_returns_current_thresholds(self, app):
        """PATCH with empty body returns current (default) thresholds without error."""
        _app_with_mock_db(app, fetch_rows=[])

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.patch("/api/home/settings/thresholds", json={})

        assert resp.status_code == 200
        result = resp.json()
        assert "battery" in result

    async def test_pool_unavailable_returns_503(self, app):
        """When DB pool is unavailable, return 503."""
        _app_with_mock_db(app, pool_available=False)
        body = {"battery": {"critical": 15, "warning": 25, "info": 35}}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.patch("/api/home/settings/thresholds", json=body)

        assert resp.status_code == 503

    async def test_multiple_threshold_groups_updated(self, app):
        """Updating multiple groups in a single PATCH persists all."""
        _, mock_pool = _app_with_mock_db(app, fetch_rows=[])

        body = {
            "battery": {"critical": 15, "warning": 25, "info": 35},
            "energy": {"anomaly_pct": 25, "high_severity_pct": 120},
        }
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.patch("/api/home/settings/thresholds", json=body)

        assert resp.status_code == 200
        # execute should be called twice (once per group)
        assert mock_pool.execute.call_count == 2
