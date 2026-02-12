"""Tests for health butler API endpoints.

Verifies the API contract (status codes, response shapes) for health
endpoints.  Uses a mocked DatabaseManager so no real database is required.

Issue: butlers-26h.11.3
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.routers.health import _get_db_manager

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
        mock_db.pool.side_effect = KeyError("No pool for butler: health")

    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db

    return app


# ---------------------------------------------------------------------------
# GET /api/health/measurements
# ---------------------------------------------------------------------------


class TestListMeasurements:
    async def test_returns_paginated_response_structure(self):
        """Response must have 'data' array and 'meta' with pagination."""
        app = _app_with_mock_db()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/health/measurements")

        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert "meta" in body
        assert isinstance(body["data"], list)
        assert "total" in body["meta"]
        assert "offset" in body["meta"]
        assert "limit" in body["meta"]

    async def test_type_filter_accepted(self):
        """The ?type= query parameter must not cause an error."""
        app = _app_with_mock_db()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/health/measurements", params={"type": "blood_pressure"})

        assert resp.status_code == 200

    async def test_since_until_filters_accepted(self):
        """The ?since= and ?until= query parameters must not cause an error."""
        app = _app_with_mock_db()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/health/measurements",
                params={"since": "2025-01-01", "until": "2025-12-31"},
            )

        assert resp.status_code == 200

    async def test_empty_results(self):
        """When no measurements exist, data should be an empty list."""
        app = _app_with_mock_db(fetchval_result=0)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/health/measurements")

        body = resp.json()
        assert body["data"] == []
        assert body["meta"]["total"] == 0

    async def test_pool_unavailable_returns_503(self):
        """When the health DB pool is unavailable, return 503."""
        app = _app_with_mock_db(pool_available=False)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/health/measurements")

        assert resp.status_code == 503

    async def test_pagination_params_accepted(self):
        """The ?offset= and ?limit= query parameters must be accepted."""
        app = _app_with_mock_db()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/health/measurements", params={"offset": 10, "limit": 25})

        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /api/health/medications
# ---------------------------------------------------------------------------


class TestListMedications:
    async def test_returns_paginated_response_structure(self):
        """Response must have 'data' array and 'meta' with pagination."""
        app = _app_with_mock_db()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/health/medications")

        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert "meta" in body
        assert isinstance(body["data"], list)

    async def test_active_filter_accepted(self):
        """The ?active= query parameter must not cause an error."""
        app = _app_with_mock_db()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/health/medications", params={"active": "true"})

        assert resp.status_code == 200

    async def test_empty_results(self):
        """When no medications exist, data should be an empty list."""
        app = _app_with_mock_db(fetchval_result=0)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/health/medications")

        body = resp.json()
        assert body["data"] == []
        assert body["meta"]["total"] == 0

    async def test_pool_unavailable_returns_503(self):
        """When the health DB pool is unavailable, return 503."""
        app = _app_with_mock_db(pool_available=False)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/health/medications")

        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /api/health/medications/{medication_id}/doses
# ---------------------------------------------------------------------------


class TestListMedicationDoses:
    async def test_returns_list_of_doses(self):
        """Response must be a JSON array of Dose objects."""
        app = _app_with_mock_db()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/health/medications/med-1/doses")

        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_since_until_filters_accepted(self):
        """The ?since= and ?until= query parameters must not cause an error."""
        app = _app_with_mock_db()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/health/medications/med-1/doses",
                params={"since": "2025-01-01", "until": "2025-12-31"},
            )

        assert resp.status_code == 200

    async def test_empty_results(self):
        """When no doses exist, response should be an empty list."""
        app = _app_with_mock_db()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/health/medications/med-1/doses")

        assert resp.json() == []

    async def test_pool_unavailable_returns_503(self):
        """When the health DB pool is unavailable, return 503."""
        app = _app_with_mock_db(pool_available=False)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/health/medications/med-1/doses")

        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /api/health/conditions
# ---------------------------------------------------------------------------


class TestListConditions:
    async def test_returns_paginated_response_structure(self):
        """Response must have 'data' array and 'meta' with pagination."""
        app = _app_with_mock_db()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/health/conditions")

        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert "meta" in body
        assert isinstance(body["data"], list)

    async def test_empty_results(self):
        """When no conditions exist, data should be an empty list."""
        app = _app_with_mock_db(fetchval_result=0)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/health/conditions")

        body = resp.json()
        assert body["data"] == []
        assert body["meta"]["total"] == 0

    async def test_pool_unavailable_returns_503(self):
        """When the health DB pool is unavailable, return 503."""
        app = _app_with_mock_db(pool_available=False)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/health/conditions")

        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /api/health/symptoms
# ---------------------------------------------------------------------------


class TestListSymptoms:
    async def test_returns_paginated_response_structure(self):
        """Response must have 'data' array and 'meta' with pagination."""
        app = _app_with_mock_db()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/health/symptoms")

        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert "meta" in body
        assert isinstance(body["data"], list)

    async def test_name_filter_accepted(self):
        """The ?name= query parameter must not cause an error."""
        app = _app_with_mock_db()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/health/symptoms", params={"name": "headache"})

        assert resp.status_code == 200

    async def test_since_until_filters_accepted(self):
        """The ?since= and ?until= query parameters must not cause an error."""
        app = _app_with_mock_db()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/health/symptoms",
                params={"since": "2025-01-01", "until": "2025-12-31"},
            )

        assert resp.status_code == 200

    async def test_empty_results(self):
        """When no symptoms exist, data should be an empty list."""
        app = _app_with_mock_db(fetchval_result=0)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/health/symptoms")

        body = resp.json()
        assert body["data"] == []
        assert body["meta"]["total"] == 0

    async def test_pool_unavailable_returns_503(self):
        """When the health DB pool is unavailable, return 503."""
        app = _app_with_mock_db(pool_available=False)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/health/symptoms")

        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /api/health/meals
# ---------------------------------------------------------------------------


class TestListMeals:
    async def test_returns_paginated_response_structure(self):
        """Response must have 'data' array and 'meta' with pagination."""
        app = _app_with_mock_db()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/health/meals")

        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert "meta" in body
        assert isinstance(body["data"], list)

    async def test_type_filter_accepted(self):
        """The ?type= query parameter must not cause an error."""
        app = _app_with_mock_db()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/health/meals", params={"type": "lunch"})

        assert resp.status_code == 200

    async def test_since_until_filters_accepted(self):
        """The ?since= and ?until= query parameters must not cause an error."""
        app = _app_with_mock_db()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/health/meals",
                params={"since": "2025-01-01", "until": "2025-12-31"},
            )

        assert resp.status_code == 200

    async def test_empty_results(self):
        """When no meals exist, data should be an empty list."""
        app = _app_with_mock_db(fetchval_result=0)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/health/meals")

        body = resp.json()
        assert body["data"] == []
        assert body["meta"]["total"] == 0

    async def test_pool_unavailable_returns_503(self):
        """When the health DB pool is unavailable, return 503."""
        app = _app_with_mock_db(pool_available=False)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/health/meals")

        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /api/health/research
# ---------------------------------------------------------------------------


class TestListResearch:
    async def test_returns_paginated_response_structure(self):
        """Response must have 'data' array and 'meta' with pagination."""
        app = _app_with_mock_db()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/health/research")

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
            resp = await client.get("/api/health/research", params={"q": "diabetes"})

        assert resp.status_code == 200

    async def test_tag_filter_accepted(self):
        """The ?tag= query parameter must not cause an error."""
        app = _app_with_mock_db()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/health/research", params={"tag": "nutrition"})

        assert resp.status_code == 200

    async def test_empty_results(self):
        """When no research entries exist, data should be an empty list."""
        app = _app_with_mock_db(fetchval_result=0)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/health/research")

        body = resp.json()
        assert body["data"] == []
        assert body["meta"]["total"] == 0

    async def test_pool_unavailable_returns_503(self):
        """When the health DB pool is unavailable, return 503."""
        app = _app_with_mock_db(pool_available=False)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/health/research")

        assert resp.status_code == 503
