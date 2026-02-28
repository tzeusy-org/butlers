"""Tests for switchboard butler API endpoints.

Verifies the API contract (status codes, response shapes) for switchboard
view endpoints.  Uses a mocked DatabaseManager so no real database is
required.

Issue: butlers-26h.12.3
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.db import DatabaseManager

# Dynamically load the switchboard router to get _get_db_manager
# Use the same module name as router_discovery.py to ensure we get the same module instance
_roster_root = Path(__file__).resolve().parents[2] / "roster"
_router_path = _roster_root / "switchboard" / "api" / "router.py"
spec = importlib.util.spec_from_file_location("switchboard_api_router", _router_path)
if spec is None or spec.loader is None:
    raise ValueError(f"Could not load spec from {_router_path}")
switchboard_module = importlib.util.module_from_spec(spec)
sys.modules["switchboard_api_router"] = switchboard_module
spec.loader.exec_module(switchboard_module)
_get_db_manager = switchboard_module._get_db_manager

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _app_with_mock_db(
    app,
    *,
    fetch_rows: list | None = None,
    fetchval_result: int = 0,
    fetchrow_result: dict | None = None,
    pool_available: bool = True,
):
    """Create a FastAPI app with a mocked DatabaseManager.

    The mock pool returns:
    - ``fetch_rows`` for pool.fetch() calls (default: [])
    - ``fetchval_result`` for pool.fetchval() calls (default: 0)
    - ``fetchrow_result`` for pool.fetchrow() calls (default: None)
    - ``pool_available`` controls whether db.pool() raises KeyError
    """
    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(return_value=fetch_rows or [])
    mock_pool.fetchval = AsyncMock(return_value=fetchval_result)
    mock_pool.fetchrow = AsyncMock(return_value=fetchrow_result)

    mock_db = MagicMock(spec=DatabaseManager)
    if pool_available:
        mock_db.pool.return_value = mock_pool
    else:
        mock_db.pool.side_effect = KeyError("No pool for butler: switchboard")

    app.dependency_overrides[_get_db_manager] = lambda: mock_db

    return app


# ---------------------------------------------------------------------------
# GET /api/switchboard/routing-log
# ---------------------------------------------------------------------------


class TestListRoutingLog:
    async def test_returns_paginated_response_structure(self, app):
        """Response must have 'data' array and 'meta' with pagination."""
        app = _app_with_mock_db(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/routing-log")

        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert "meta" in body
        assert isinstance(body["data"], list)
        assert "total" in body["meta"]
        assert "offset" in body["meta"]
        assert "limit" in body["meta"]

    async def test_source_butler_filter_accepted(self, app):
        """The ?source_butler= query parameter must not cause an error."""
        app = _app_with_mock_db(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/switchboard/routing-log", params={"source_butler": "health"}
            )

        assert resp.status_code == 200

    async def test_target_butler_filter_accepted(self, app):
        """The ?target_butler= query parameter must not cause an error."""
        app = _app_with_mock_db(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/switchboard/routing-log", params={"target_butler": "general"}
            )

        assert resp.status_code == 200

    async def test_since_until_filters_accepted(self, app):
        """The ?since= and ?until= query parameters must not cause an error."""
        app = _app_with_mock_db(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/switchboard/routing-log",
                params={"since": "2025-01-01", "until": "2025-12-31"},
            )

        assert resp.status_code == 200

    async def test_empty_results(self, app):
        """When no routing log entries exist, data should be an empty list."""
        app = _app_with_mock_db(app, fetchval_result=0)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/routing-log")

        body = resp.json()
        assert body["data"] == []
        assert body["meta"]["total"] == 0

    async def test_pagination_params_accepted(self, app):
        """The ?offset= and ?limit= query parameters must be accepted."""
        app = _app_with_mock_db(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/switchboard/routing-log", params={"offset": 10, "limit": 25}
            )

        assert resp.status_code == 200

    async def test_pool_unavailable_returns_503(self, app):
        """When the switchboard DB pool is unavailable, return 503."""
        app = _app_with_mock_db(app, pool_available=False)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/routing-log")

        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /api/switchboard/registry
# ---------------------------------------------------------------------------


class TestListRegistry:
    async def test_returns_api_response_structure(self, app):
        """Response must have 'data' array and 'meta' keys."""
        app = _app_with_mock_db(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/registry")

        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert "meta" in body
        assert isinstance(body["data"], list)

    async def test_empty_results(self, app):
        """When no registry entries exist, data should be an empty list."""
        app = _app_with_mock_db(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/registry")

        body = resp.json()
        assert body["data"] == []

    async def test_pool_unavailable_returns_503(self, app):
        """When the switchboard DB pool is unavailable, return 503."""
        app = _app_with_mock_db(app, pool_available=False)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/registry")

        assert resp.status_code == 503
