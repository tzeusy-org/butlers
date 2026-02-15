"""Tests for general butler API endpoints.

Verifies the API contract (status codes, response shapes) for general
butler endpoints.  Uses a mocked DatabaseManager so no real database is
required.

Issue: butlers-26h.12.3
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


def _app_with_mock_db(
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
    - ``fetchrow_result`` for pool.fetchrow() calls (default: None → 404)
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
        mock_db.pool.side_effect = KeyError("No pool for butler: general")

    app = create_app()

    # Override the dependency for the dynamically-loaded general router
    # The router module is loaded during create_app() and stored in app.state
    for butler_name, router_module in app.state.butler_routers:
        if butler_name == "general" and hasattr(router_module, "_get_db_manager"):
            app.dependency_overrides[router_module._get_db_manager] = lambda: mock_db
            break

    return app


# ---------------------------------------------------------------------------
# GET /api/general/collections
# ---------------------------------------------------------------------------


class TestListCollections:
    async def test_returns_paginated_response_structure(self):
        """Response must have 'data' array and 'meta' with pagination."""
        app = _app_with_mock_db()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/general/collections")

        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert "meta" in body
        assert isinstance(body["data"], list)
        assert "total" in body["meta"]
        assert "offset" in body["meta"]
        assert "limit" in body["meta"]

    async def test_empty_results(self):
        """When no collections exist, data should be an empty list."""
        app = _app_with_mock_db(fetchval_result=0)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/general/collections")

        body = resp.json()
        assert body["data"] == []
        assert body["meta"]["total"] == 0

    async def test_pagination_params_accepted(self):
        """The ?offset= and ?limit= query parameters must be accepted."""
        app = _app_with_mock_db()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/general/collections", params={"offset": 10, "limit": 25})

        assert resp.status_code == 200

    async def test_pool_unavailable_returns_503(self):
        """When the general DB pool is unavailable, return 503."""
        app = _app_with_mock_db(pool_available=False)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/general/collections")

        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /api/general/collections/{collection_id}/entities
# ---------------------------------------------------------------------------


class TestListCollectionEntities:
    async def test_returns_paginated_response_structure(self):
        """Response must have 'data' array and 'meta' with pagination."""
        app = _app_with_mock_db()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/general/collections/col-1/entities")

        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert "meta" in body
        assert isinstance(body["data"], list)

    async def test_empty_results(self):
        """When no entities exist in collection, data should be an empty list."""
        app = _app_with_mock_db(fetchval_result=0)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/general/collections/col-1/entities")

        body = resp.json()
        assert body["data"] == []
        assert body["meta"]["total"] == 0

    async def test_pool_unavailable_returns_503(self):
        """When the general DB pool is unavailable, return 503."""
        app = _app_with_mock_db(pool_available=False)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/general/collections/col-1/entities")

        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /api/general/entities
# ---------------------------------------------------------------------------


class TestListEntities:
    async def test_returns_paginated_response_structure(self):
        """Response must have 'data' array and 'meta' with pagination."""
        app = _app_with_mock_db()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/general/entities")

        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert "meta" in body
        assert isinstance(body["data"], list)

    async def test_search_param_accepted(self):
        """The ?q= query parameter must not cause an error."""
        app = _app_with_mock_db()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/general/entities", params={"q": "test"})

        assert resp.status_code == 200

    async def test_collection_filter_accepted(self):
        """The ?collection= query parameter must not cause an error."""
        app = _app_with_mock_db()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/general/entities", params={"collection": "bookmarks"})

        assert resp.status_code == 200

    async def test_tag_filter_accepted(self):
        """The ?tag= query parameter must not cause an error."""
        app = _app_with_mock_db()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/general/entities", params={"tag": "important"})

        assert resp.status_code == 200

    async def test_empty_results(self):
        """When no entities exist, data should be an empty list."""
        app = _app_with_mock_db(fetchval_result=0)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/general/entities")

        body = resp.json()
        assert body["data"] == []
        assert body["meta"]["total"] == 0

    async def test_pool_unavailable_returns_503(self):
        """When the general DB pool is unavailable, return 503."""
        app = _app_with_mock_db(pool_available=False)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/general/entities")

        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /api/general/entities/{entity_id}
# ---------------------------------------------------------------------------


class TestGetEntity:
    async def test_missing_entity_returns_404(self):
        """A non-existent entity should return 404 when fetchrow returns None."""
        app = _app_with_mock_db(fetchrow_result=None)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/general/entities/nonexistent-id")

        assert resp.status_code == 404

    async def test_returns_api_response_structure(self):
        """Response must have 'data' and 'meta' keys when entity exists."""
        row = {
            "id": "ent-1",
            "collection_id": "col-1",
            "collection_name": "bookmarks",
            "data": {"url": "https://example.com"},
            "tags": ["web"],
            "created_at": "2025-01-01T00:00:00",
            "updated_at": "2025-01-01T00:00:00",
        }
        app = _app_with_mock_db(fetchrow_result=row)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/general/entities/ent-1")

        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert "meta" in body
        assert body["data"]["id"] == "ent-1"
        assert body["data"]["collection_name"] == "bookmarks"

    async def test_pool_unavailable_returns_503(self):
        """When the general DB pool is unavailable, return 503."""
        app = _app_with_mock_db(pool_available=False)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/general/entities/ent-1")

        assert resp.status_code == 503
