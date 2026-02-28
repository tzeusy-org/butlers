"""Tests for home butler API endpoints.

Verifies the API contract (status codes, response shapes) for home
endpoints. Uses a mocked DatabaseManager so no real database is required.

Issue: butlers-kxbo.7
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_entity_row(entity_id: str = "light.living_room") -> MagicMock:
    """Return a mock asyncpg record simulating ha_entity_snapshot row."""
    row = MagicMock()
    domain = entity_id.split(".")[0] if "." in entity_id else entity_id
    row.__getitem__ = lambda self, key: {
        "entity_id": entity_id,
        "state": "on",
        "attributes": {"friendly_name": "Living Room Light", "area_id": "living_room"},
        "last_updated": "2026-02-28T10:00:00+00:00",
        "captured_at": "2026-02-28T10:05:00+00:00",
        "domain": domain,
        "cnt": 3,
        "area_id": "living_room",
        "entity_count": 2,
    }[key]
    return row


def _make_command_row(id: int = 1) -> MagicMock:
    """Return a mock asyncpg record simulating ha_command_log row."""
    row = MagicMock()
    row.__getitem__ = lambda self, key: {
        "id": id,
        "domain": "light",
        "service": "turn_on",
        "target": {"entity_id": "light.living_room"},
        "data": {"brightness": 255},
        "result": {},
        "context_id": "abc123",
        "issued_at": "2026-02-28T10:00:00+00:00",
    }[key]
    return row


def _make_bounds_row(
    oldest: str = "2026-02-01T00:00:00+00:00",
    newest: str = "2026-02-28T10:00:00+00:00",
) -> MagicMock:
    """Return a mock asyncpg record simulating oldest/newest bounds."""
    row = MagicMock()
    row.__getitem__ = lambda self, key: {"oldest": oldest, "newest": newest}[key]
    return row


def _app_with_mock_db(
    *,
    fetch_rows: list | None = None,
    fetchval_result: int = 0,
    fetchrow_result=None,
    pool_available: bool = True,
):
    """Create a FastAPI app with a mocked DatabaseManager for the home butler.

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
        mock_db.pool.side_effect = KeyError("No pool for butler: home")

    app = create_app()

    # Override the dependency for the dynamically-loaded home router
    for butler_name, router_module in app.state.butler_routers:
        if butler_name == "home" and hasattr(router_module, "_get_db_manager"):
            app.dependency_overrides[router_module._get_db_manager] = lambda: mock_db
            break

    return app


# ---------------------------------------------------------------------------
# GET /api/home/entities
# ---------------------------------------------------------------------------


class TestListEntities:
    async def test_returns_paginated_response_structure(self):
        """Response must have 'data' array and 'meta' with pagination fields."""
        app = _app_with_mock_db()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/home/entities")

        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert "meta" in body
        assert isinstance(body["data"], list)
        assert "total" in body["meta"]
        assert "offset" in body["meta"]
        assert "limit" in body["meta"]

    async def test_empty_results(self):
        """When no entities exist, data should be an empty list."""
        app = _app_with_mock_db(fetchval_result=0)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/home/entities")

        body = resp.json()
        assert body["data"] == []
        assert body["meta"]["total"] == 0

    async def test_domain_filter_accepted(self):
        """The ?domain= query parameter must not cause an error."""
        app = _app_with_mock_db()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/home/entities", params={"domain": "light"})

        assert resp.status_code == 200

    async def test_area_filter_accepted(self):
        """The ?area= query parameter must not cause an error."""
        app = _app_with_mock_db()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/home/entities", params={"area": "living_room"})

        assert resp.status_code == 200

    async def test_pagination_params_accepted(self):
        """The ?offset= and ?limit= query parameters must be accepted."""
        app = _app_with_mock_db()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/home/entities", params={"offset": 10, "limit": 25})

        assert resp.status_code == 200

    async def test_entity_row_serialized_correctly(self):
        """A populated entity row should be serialized to EntitySummaryResponse fields."""
        row = _make_entity_row("light.kitchen")
        app = _app_with_mock_db(fetch_rows=[row], fetchval_result=1)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/home/entities")

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 1
        entity = body["data"][0]
        assert entity["entity_id"] == "light.kitchen"
        assert entity["state"] == "on"
        assert entity["domain"] == "light"
        assert "captured_at" in entity

    async def test_pool_unavailable_returns_503(self):
        """When the home DB pool is unavailable, return 503."""
        app = _app_with_mock_db(pool_available=False)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/home/entities")

        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /api/home/entities/{entity_id}
# ---------------------------------------------------------------------------


class TestGetEntity:
    async def test_returns_entity_detail_when_found(self):
        """When entity exists, return full EntityStateResponse."""
        row = _make_entity_row("light.living_room")
        app = _app_with_mock_db(fetchrow_result=row)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/home/entities/light.living_room")

        assert resp.status_code == 200
        body = resp.json()
        assert body["entity_id"] == "light.living_room"
        assert body["state"] == "on"
        assert "attributes" in body
        assert "captured_at" in body

    async def test_returns_404_when_not_found(self):
        """When entity is not in cache, return 404."""
        app = _app_with_mock_db(fetchrow_result=None)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/home/entities/sensor.missing")

        assert resp.status_code == 404

    async def test_pool_unavailable_returns_503(self):
        """When the home DB pool is unavailable, return 503."""
        app = _app_with_mock_db(pool_available=False)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/home/entities/light.living_room")

        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /api/home/areas
# ---------------------------------------------------------------------------


class TestListAreas:
    async def test_returns_list_of_areas(self):
        """Response must be a JSON array of AreaResponse objects."""
        area_row = MagicMock()
        area_row.__getitem__ = lambda self, key: {
            "area_id": "living_room",
            "entity_count": 5,
        }[key]

        app = _app_with_mock_db(fetch_rows=[area_row])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/home/areas")

        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)
        assert len(body) == 1
        assert body[0]["area_id"] == "living_room"
        assert body[0]["entity_count"] == 5

    async def test_empty_areas(self):
        """When no entities have area_id attributes, return empty list."""
        app = _app_with_mock_db(fetch_rows=[])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/home/areas")

        assert resp.status_code == 200
        assert resp.json() == []

    async def test_pool_unavailable_returns_503(self):
        """When the home DB pool is unavailable, return 503."""
        app = _app_with_mock_db(pool_available=False)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/home/areas")

        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /api/home/command-log
# ---------------------------------------------------------------------------


class TestListCommandLog:
    async def test_returns_paginated_response_structure(self):
        """Response must have 'data' array and 'meta' with pagination fields."""
        app = _app_with_mock_db()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/home/command-log")

        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert "meta" in body
        assert isinstance(body["data"], list)
        assert "total" in body["meta"]

    async def test_empty_results(self):
        """When no command log entries exist, data should be an empty list."""
        app = _app_with_mock_db(fetchval_result=0)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/home/command-log")

        body = resp.json()
        assert body["data"] == []
        assert body["meta"]["total"] == 0

    async def test_time_range_filters_accepted(self):
        """The ?start= and ?end= query parameters must not cause an error."""
        app = _app_with_mock_db()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/home/command-log",
                params={"start": "2026-02-01T00:00:00Z", "end": "2026-02-28T23:59:59Z"},
            )

        assert resp.status_code == 200

    async def test_domain_filter_accepted(self):
        """The ?domain= query parameter must not cause an error."""
        app = _app_with_mock_db()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/home/command-log", params={"domain": "light"})

        assert resp.status_code == 200

    async def test_pagination_params_accepted(self):
        """The ?offset= and ?limit= query parameters must be accepted."""
        app = _app_with_mock_db()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/home/command-log", params={"offset": 5, "limit": 10})

        assert resp.status_code == 200

    async def test_command_row_serialized_correctly(self):
        """A populated command log row should be serialized to CommandLogEntry fields."""
        row = _make_command_row(id=42)
        app = _app_with_mock_db(fetch_rows=[row], fetchval_result=1)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/home/command-log")

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 1
        entry = body["data"][0]
        assert entry["id"] == 42
        assert entry["domain"] == "light"
        assert entry["service"] == "turn_on"
        assert "issued_at" in entry

    async def test_pool_unavailable_returns_503(self):
        """When the home DB pool is unavailable, return 503."""
        app = _app_with_mock_db(pool_available=False)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/home/command-log")

        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /api/home/snapshot-status
# ---------------------------------------------------------------------------


class TestSnapshotStatus:
    async def test_returns_statistics_response(self):
        """Response must have total_entities, domains, and freshness timestamps."""
        domain_row = MagicMock()
        domain_row.__getitem__ = lambda self, key: {"domain": "light", "cnt": 3}[key]
        bounds_row = _make_bounds_row()

        mock_pool = AsyncMock()
        mock_pool.fetchval = AsyncMock(return_value=5)
        mock_pool.fetch = AsyncMock(return_value=[domain_row])
        mock_pool.fetchrow = AsyncMock(return_value=bounds_row)

        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.pool.return_value = mock_pool

        app = create_app()
        for butler_name, router_module in app.state.butler_routers:
            if butler_name == "home" and hasattr(router_module, "_get_db_manager"):
                app.dependency_overrides[router_module._get_db_manager] = lambda: mock_db
                break

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/home/snapshot-status")

        assert resp.status_code == 200
        body = resp.json()
        assert "total_entities" in body
        assert "domains" in body
        assert "oldest_captured_at" in body
        assert "newest_captured_at" in body
        assert body["total_entities"] == 5
        assert body["domains"]["light"] == 3

    async def test_empty_snapshot_cache(self):
        """When no entities are cached, return zeros and null timestamps."""
        mock_pool = AsyncMock()
        mock_pool.fetchval = AsyncMock(return_value=0)
        mock_pool.fetch = AsyncMock(return_value=[])
        bounds_row = MagicMock()
        bounds_row.__getitem__ = lambda self, key: {"oldest": None, "newest": None}[key]
        mock_pool.fetchrow = AsyncMock(return_value=bounds_row)

        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.pool.return_value = mock_pool

        app = create_app()
        for butler_name, router_module in app.state.butler_routers:
            if butler_name == "home" and hasattr(router_module, "_get_db_manager"):
                app.dependency_overrides[router_module._get_db_manager] = lambda: mock_db
                break

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/home/snapshot-status")

        assert resp.status_code == 200
        body = resp.json()
        assert body["total_entities"] == 0
        assert body["domains"] == {}
        assert body["oldest_captured_at"] is None
        assert body["newest_captured_at"] is None

    async def test_pool_unavailable_returns_503(self):
        """When the home DB pool is unavailable, return 503."""
        app = _app_with_mock_db(pool_available=False)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/home/snapshot-status")

        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Auto-discovery integration
# ---------------------------------------------------------------------------


class TestHomeRouterDiscovery:
    def test_home_router_is_discovered(self):
        """The home butler router must be discoverable via the real roster."""
        from fastapi import APIRouter

        from butlers.api.router_discovery import discover_butler_routers

        routers = discover_butler_routers()
        butler_names = [name for name, _ in routers]
        assert "home" in butler_names

        home_module = next(m for n, m in routers if n == "home")
        assert hasattr(home_module, "router")
        assert isinstance(home_module.router, APIRouter)
        assert home_module.router.prefix == "/api/home"

    def test_home_router_exports_get_db_manager(self):
        """The home router module must export a _get_db_manager stub."""
        from butlers.api.router_discovery import discover_butler_routers

        routers = discover_butler_routers()
        home_module = next((m for n, m in routers if n == "home"), None)
        assert home_module is not None
        assert hasattr(home_module, "_get_db_manager")


# ---------------------------------------------------------------------------
# Combined filter edge cases
# ---------------------------------------------------------------------------


class TestEntityListCombinedFilters:
    async def test_domain_and_area_filters_combined(self):
        """Both ?domain= and ?area= can be provided simultaneously."""
        app = _app_with_mock_db()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/home/entities", params={"domain": "light", "area": "living_room"}
            )

        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert "meta" in body

    async def test_large_limit_clamped(self):
        """?limit= values above 500 should return 422 (validation error)."""
        app = _app_with_mock_db()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/home/entities", params={"limit": 9999})

        assert resp.status_code == 422

    async def test_negative_offset_returns_422(self):
        """?offset= values below 0 should return 422."""
        app = _app_with_mock_db()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/home/entities", params={"offset": -1})

        assert resp.status_code == 422

    async def test_meta_reflects_pagination_params(self):
        """Response meta should echo back the requested offset and limit."""
        app = _app_with_mock_db(fetchval_result=100)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/home/entities", params={"offset": 20, "limit": 10})

        assert resp.status_code == 200
        meta = resp.json()["meta"]
        assert meta["offset"] == 20
        assert meta["limit"] == 10
        assert meta["total"] == 100


# ---------------------------------------------------------------------------
# Command log combined filters
# ---------------------------------------------------------------------------


class TestCommandLogCombinedFilters:
    async def test_all_filters_combined(self):
        """start, end, domain, offset, and limit can all be provided together."""
        app = _app_with_mock_db()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/home/command-log",
                params={
                    "start": "2026-01-01T00:00:00Z",
                    "end": "2026-01-31T23:59:59Z",
                    "domain": "lock",
                    "offset": 0,
                    "limit": 10,
                },
            )

        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert "meta" in body

    async def test_command_log_large_limit_rejected(self):
        """?limit= values above 500 should be rejected with 422."""
        app = _app_with_mock_db()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/home/command-log", params={"limit": 9999})

        assert resp.status_code == 422

    async def test_command_log_row_has_optional_null_fields(self):
        """CommandLogEntry serializes correctly when target, data, result, context_id are None."""
        row = MagicMock()
        row.__getitem__ = lambda self, key: {
            "id": 5,
            "domain": "script",
            "service": "run_script",
            "target": None,
            "data": None,
            "result": None,
            "context_id": None,
            "issued_at": "2026-02-28T12:00:00+00:00",
        }[key]
        app = _app_with_mock_db(fetch_rows=[row], fetchval_result=1)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/home/command-log")

        assert resp.status_code == 200
        body = resp.json()
        entry = body["data"][0]
        assert entry["id"] == 5
        assert entry["target"] is None
        assert entry["data"] is None
        assert entry["result"] is None
        assert entry["context_id"] is None


# ---------------------------------------------------------------------------
# Snapshot status edge cases
# ---------------------------------------------------------------------------


class TestSnapshotStatusEdgeCases:
    async def test_snapshot_bounds_row_is_none(self):
        """When fetchrow returns None for bounds, timestamps should be null."""
        mock_pool = AsyncMock()
        mock_pool.fetchval = AsyncMock(return_value=0)
        mock_pool.fetch = AsyncMock(return_value=[])
        mock_pool.fetchrow = AsyncMock(return_value=None)

        mock_db = MagicMock()
        from butlers.api.db import DatabaseManager

        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.pool.return_value = mock_pool

        from butlers.api.app import create_app

        app = create_app()
        for butler_name, router_module in app.state.butler_routers:
            if butler_name == "home" and hasattr(router_module, "_get_db_manager"):
                app.dependency_overrides[router_module._get_db_manager] = lambda: mock_db
                break

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/home/snapshot-status")

        assert resp.status_code == 200
        body = resp.json()
        assert body["oldest_captured_at"] is None
        assert body["newest_captured_at"] is None
