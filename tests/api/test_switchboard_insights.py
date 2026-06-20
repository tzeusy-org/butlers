"""Tests for GET /api/switchboard/insights — read-only insight-candidate reader.

Bead: bu-sqjc7.3.

The reader is hosted on the Switchboard role because ``butler_switchboard_rw``
is the only butler role with SELECT on ``public.insight_candidates`` (migration
core_010); the others have INSERT-only access.

Coverage:
- Happy path: rows mapped to the response model with all fields
- Default status=pending applied and bound as the first SQL arg
- ``butler`` filter adds an origin_butler condition
- Invalid status → 422
- limit out of range → 422 (FastAPI validation)
- 503 when the switchboard pool is unavailable
- Graceful degrade: table query fails → 200 with empty list
"""

from __future__ import annotations

import datetime
import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.db import DatabaseManager

pytestmark = pytest.mark.unit

_MODULE_NAME = "switchboard_api_router"
_roster_root = Path(__file__).resolve().parents[2] / "roster"
_router_path = _roster_root / "switchboard" / "api" / "router.py"


def _get_db_dep():
    if _MODULE_NAME not in sys.modules:
        spec = importlib.util.spec_from_file_location(_MODULE_NAME, _router_path)
        if spec is None or spec.loader is None:
            raise ValueError(f"Could not load spec from {_router_path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[_MODULE_NAME] = module
        spec.loader.exec_module(module)
    return sys.modules[_MODULE_NAME]._get_db_manager


def _make_row(data: dict):
    row = MagicMock()
    row.__getitem__ = lambda self, k: data[k]
    row.get = lambda k, default=None: data.get(k, default)
    row.keys = lambda: data.keys()
    row.__iter__ = lambda self: iter(data)
    return row


def _app_with_mock(
    app,
    *,
    fetch_rows=None,
    fetch_side_effect=None,
    pool_available=True,
):
    mock_pool = AsyncMock()
    if fetch_side_effect is not None:
        mock_pool.fetch = AsyncMock(side_effect=fetch_side_effect)
    else:
        mock_pool.fetch = AsyncMock(return_value=fetch_rows or [])

    mock_db = MagicMock(spec=DatabaseManager)
    if pool_available:
        mock_db.pool.return_value = mock_pool
    else:
        mock_db.pool.side_effect = KeyError("No pool")

    app.dependency_overrides[_get_db_dep()] = lambda: mock_db
    return app, mock_pool


def _sample_row(**overrides):
    base = {
        "id": "11111111-1111-1111-1111-111111111111",
        "origin_butler": "finance",
        "priority": 80,
        "category": "bill",
        "dedup_key": "bill:water:2026-06",
        "cooldown_days": 7,
        "expires_at": datetime.datetime(2026, 6, 30, tzinfo=datetime.UTC),
        "message": "Water bill due soon",
        "channel": "telegram",
        "metadata": {"amount": "42.00"},
        "created_at": datetime.datetime(2026, 6, 20, tzinfo=datetime.UTC),
        "status": "pending",
        "delivered_at": None,
        "delivery_attempt_count": 0,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_insights_happy_path_maps_all_fields(app):
    app, _ = _app_with_mock(app, fetch_rows=[_make_row(_sample_row())])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/switchboard/insights")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data) == 1
    item = data[0]
    assert item["id"] == "11111111-1111-1111-1111-111111111111"
    assert item["origin_butler"] == "finance"
    assert item["priority"] == 80
    assert item["category"] == "bill"
    assert item["dedup_key"] == "bill:water:2026-06"
    assert item["cooldown_days"] == 7
    assert item["message"] == "Water bill due soon"
    assert item["channel"] == "telegram"
    assert item["metadata"] == {"amount": "42.00"}
    assert item["status"] == "pending"
    assert item["delivered_at"] is None
    assert item["delivery_attempt_count"] == 0
    assert item["expires_at"] is not None
    assert item["created_at"] is not None


# ---------------------------------------------------------------------------
# Default status + SQL binding
# ---------------------------------------------------------------------------


async def test_insights_defaults_to_pending(app):
    app, mock_pool = _app_with_mock(app, fetch_rows=[])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/switchboard/insights")

    assert resp.status_code == 200
    assert resp.json()["data"] == []
    call = mock_pool.fetch.call_args
    sql = call[0][0]
    assert "public.insight_candidates" in sql
    assert "status = $1" in sql
    # First bound arg is the status filter.
    assert call[0][1] == "pending"


async def test_insights_butler_filter_adds_condition(app):
    app, mock_pool = _app_with_mock(app, fetch_rows=[])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/switchboard/insights", params={"butler": "health", "status": "delivered"}
        )

    assert resp.status_code == 200
    call = mock_pool.fetch.call_args
    sql = call[0][0]
    assert "origin_butler = $2" in sql
    # Bound args: status, butler, then limit.
    assert call[0][1] == "delivered"
    assert call[0][2] == "health"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


async def test_insights_invalid_status_422(app):
    _app_with_mock(app, fetch_rows=[])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/switchboard/insights", params={"status": "bogus"})
    assert resp.status_code == 422


async def test_insights_limit_out_of_range_422(app):
    _app_with_mock(app, fetch_rows=[])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/switchboard/insights", params={"limit": 99999})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Degraded modes
# ---------------------------------------------------------------------------


async def test_insights_503_when_pool_unavailable(app):
    _app_with_mock(app, pool_available=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/switchboard/insights")
    assert resp.status_code == 503


async def test_insights_graceful_degrade_when_table_missing(app):
    _app_with_mock(app, fetch_side_effect=RuntimeError("relation does not exist"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/switchboard/insights")
    assert resp.status_code == 200
    assert resp.json()["data"] == []
