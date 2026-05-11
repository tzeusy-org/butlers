"""Tests for the health butler measurements endpoints.

Covers:
  GET /api/health/measurements/latest?types=X,Y,Z
  GET /api/health/measurements/sleep/latest
  GET /api/health/measurements/sources

SQL queries do NOT filter on butler_name — the pool is butler-scoped.
"""

from __future__ import annotations

import json
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _Row(dict):
    """dict subclass mimicking asyncpg Record (attribute + item access)."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None

    def get(self, key: str, default: Any = None) -> Any:  # type: ignore[override]
        return super().get(key, default)


def _row(data: dict) -> _Row:
    return _Row(data)


def _mock_pool(*, fetch_rows=None, fetchrow_result=None, fetchval_result=0):
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=fetch_rows or [])
    pool.fetchrow = AsyncMock(return_value=fetchrow_result)
    pool.fetchval = AsyncMock(return_value=fetchval_result)
    return pool


def _build_app(pool):
    """Create a FastAPI test app with the health router wired to *pool*."""
    import importlib.util
    import sys
    from pathlib import Path

    # Load the health router module (may already be in sys.modules)
    router_path = Path(__file__).resolve().parents[2] / "roster" / "health" / "api" / "router.py"
    module_name = "health_api_router"
    if module_name not in sys.modules:
        spec = importlib.util.spec_from_file_location(module_name, router_path)
        module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        sys.modules[module_name] = module
        spec.loader.exec_module(module)  # type: ignore[union-attr]
    else:
        module = sys.modules[module_name]

    _get_db_manager = module._get_db_manager

    db = MagicMock(spec=DatabaseManager)
    db.pool.return_value = pool

    app = create_app(api_key="")
    app.dependency_overrides[_get_db_manager] = lambda: db
    return app


# ---------------------------------------------------------------------------
# GET /measurements/latest
# ---------------------------------------------------------------------------


class TestMeasurementsLatest:
    async def test_returns_requested_types(self):
        """Returns a key per requested type; present types have data."""
        from datetime import UTC, datetime

        now = datetime.now(tz=UTC)
        rows = [
            _row({"type": "weight", "value": {"value": 70.5}, "measured_at": now}),
        ]
        pool = _mock_pool(fetch_rows=rows)
        app = _build_app(pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/health/measurements/latest?types=weight,heart_rate")

        assert resp.status_code == 200
        body = resp.json()
        assert "measurements" in body
        assert "weight" in body["measurements"]
        assert body["measurements"]["weight"] is not None
        assert body["measurements"]["weight"]["value"] == {"value": 70.5}
        # heart_rate had no row → null
        assert body["measurements"]["heart_rate"] is None

    async def test_empty_result_for_unknown_types(self):
        """All requested types map to null when no rows exist."""
        pool = _mock_pool(fetch_rows=[])
        app = _build_app(pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/health/measurements/latest?types=foo,bar")

        assert resp.status_code == 200
        body = resp.json()
        assert body["measurements"]["foo"] is None
        assert body["measurements"]["bar"] is None

    async def test_empty_types_param_returns_empty(self):
        """An empty types param returns an empty measurements dict."""
        pool = _mock_pool(fetch_rows=[])
        app = _build_app(pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/health/measurements/latest?types=")

        assert resp.status_code == 200
        body = resp.json()
        assert body["measurements"] == {}

    async def test_missing_types_param_returns_422(self):
        """Omitting the required types param returns HTTP 422."""
        pool = _mock_pool()
        app = _build_app(pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/health/measurements/latest")

        assert resp.status_code == 422

    async def test_json_string_value_is_parsed(self):
        """asyncpg may return JSONB as a string; the endpoint must parse it."""
        from datetime import UTC, datetime

        now = datetime.now(tz=UTC)
        rows = [
            _row(
                {
                    "type": "blood_pressure",
                    "value": json.dumps({"systolic": 120, "diastolic": 80}),
                    "measured_at": now,
                }
            ),
        ]
        pool = _mock_pool(fetch_rows=rows)
        app = _build_app(pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/health/measurements/latest?types=blood_pressure")

        assert resp.status_code == 200
        bp = resp.json()["measurements"]["blood_pressure"]
        assert bp is not None
        assert bp["value"]["systolic"] == 120

    async def test_503_when_pool_unavailable(self):
        """Returns HTTP 503 when the health DB pool is not available."""
        db = MagicMock(spec=DatabaseManager)
        db.pool.side_effect = KeyError("health")

        import importlib.util
        import sys
        from pathlib import Path

        router_path = (
            Path(__file__).resolve().parents[2] / "roster" / "health" / "api" / "router.py"
        )
        module_name = "health_api_router"
        if module_name not in sys.modules:
            spec = importlib.util.spec_from_file_location(module_name, router_path)
            module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
            sys.modules[module_name] = module
            spec.loader.exec_module(module)  # type: ignore[union-attr]
        module = sys.modules[module_name]

        app = create_app(api_key="")
        app.dependency_overrides[module._get_db_manager] = lambda: db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/health/measurements/latest?types=weight")

        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /measurements/sleep/latest
# ---------------------------------------------------------------------------


class TestSleepLatest:
    async def test_returns_session_with_stages(self):
        """Returns full session data with parsed stages."""
        from datetime import UTC, datetime

        start = datetime(2026, 5, 10, 22, 0, 0, tzinfo=UTC)
        meta = {
            "duration_ms": 28_800_000,  # 8 hours = 480 min
            "end_time": "2026-05-11T06:00:00+00:00",
            "stages": {"deep": 90, "light": 210, "rem": 120, "awake": 60},
        }
        row = _row(
            {
                "id": uuid.uuid4(),
                "valid_at": start,
                "metadata": meta,
            }
        )
        pool = _mock_pool(fetchrow_result=row)
        app = _build_app(pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/health/measurements/sleep/latest")

        assert resp.status_code == 200
        body = resp.json()
        assert body["total_duration_minutes"] == 480
        assert body["session_end"] == "2026-05-11T06:00:00+00:00"
        kinds = {s["kind"] for s in body["stages"]}
        assert kinds == {"deep", "light", "rem", "awake"}

    async def test_returns_null_when_no_sleep_data(self):
        """Returns null body (HTTP 200 with null) when no sleep session exists."""
        pool = _mock_pool(fetchrow_result=None)
        app = _build_app(pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/health/measurements/sleep/latest")

        assert resp.status_code == 200
        assert resp.json() is None

    async def test_stages_absent_returns_empty_list(self):
        """Session without stage data returns an empty stages list."""
        from datetime import UTC, datetime

        start = datetime(2026, 5, 10, 22, 0, 0, tzinfo=UTC)
        meta = {"duration_ms": 25_200_000, "end_time": "2026-05-11T05:00:00+00:00"}
        row = _row({"id": uuid.uuid4(), "valid_at": start, "metadata": meta})
        pool = _mock_pool(fetchrow_result=row)
        app = _build_app(pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/health/measurements/sleep/latest")

        assert resp.status_code == 200
        body = resp.json()
        assert body["stages"] == []
        assert body["total_duration_minutes"] == 420

    async def test_json_string_metadata_is_parsed(self):
        """Handles asyncpg returning metadata as a JSON string."""
        from datetime import UTC, datetime

        start = datetime(2026, 5, 10, 22, 0, 0, tzinfo=UTC)
        meta_str = json.dumps({"duration_ms": 21_600_000})
        row = _row({"id": uuid.uuid4(), "valid_at": start, "metadata": meta_str})
        pool = _mock_pool(fetchrow_result=row)
        app = _build_app(pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/health/measurements/sleep/latest")

        assert resp.status_code == 200
        assert resp.json()["total_duration_minutes"] == 360


# ---------------------------------------------------------------------------
# GET /measurements/sources
# ---------------------------------------------------------------------------


class TestMeasurementSources:
    async def test_returns_sources_list(self):
        """Returns aggregated source rows from measurements.value->>'source'."""
        from datetime import UTC, datetime

        now = datetime.now(tz=UTC)
        rows = [
            _row({"name": "google_health", "last_sample_at": now, "sample_count": 42}),
            _row({"name": "manual", "last_sample_at": now, "sample_count": 7}),
        ]
        pool = _mock_pool(fetch_rows=rows)
        app = _build_app(pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/health/measurements/sources")

        assert resp.status_code == 200
        body = resp.json()
        assert "sources" in body
        assert len(body["sources"]) == 2
        names = {s["name"] for s in body["sources"]}
        assert names == {"google_health", "manual"}

    async def test_empty_sources_when_no_source_field(self):
        """Returns empty list when no measurements have a 'source' key."""
        pool = _mock_pool(fetch_rows=[])
        app = _build_app(pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/health/measurements/sources")

        assert resp.status_code == 200
        assert resp.json()["sources"] == []

    async def test_source_entry_has_required_fields(self):
        """Each source entry has name, last_sample_at, and sample_count."""
        from datetime import UTC, datetime

        now = datetime.now(tz=UTC)
        rows = [
            _row({"name": "fitbit", "last_sample_at": now, "sample_count": 10}),
        ]
        pool = _mock_pool(fetch_rows=rows)
        app = _build_app(pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/health/measurements/sources")

        body = resp.json()
        src = body["sources"][0]
        assert src["name"] == "fitbit"
        assert "last_sample_at" in src
        assert src["sample_count"] == 10
