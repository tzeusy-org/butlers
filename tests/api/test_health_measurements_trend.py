"""Tests for GET /api/health/measurements/trend (bu-eapmh).

Verifies that the trend endpoint aggregates facts rows into hourly/daily
buckets and handles edge cases correctly.

Coverage:
- daily bucketing returns correct shape
- hourly bucketing returns correct shape
- empty result returns {buckets: []}
- invalid window_days returns 422
- invalid bucket param returns 422
- 503 when the health DB pool is unavailable
- no butler_name filter in SQL (pool is butler-scoped)
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path
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


def _mock_pool(*, fetch_rows=None):
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=fetch_rows or [])
    pool.fetchrow = AsyncMock(return_value=None)
    pool.fetchval = AsyncMock(return_value=0)
    return pool


def _build_app(pool):
    """Create a FastAPI test app with the health router wired to *pool*."""
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


def _build_app_unavailable():
    """Create a test app where the health pool lookup raises KeyError (503)."""
    router_path = Path(__file__).resolve().parents[2] / "roster" / "health" / "api" / "router.py"
    module_name = "health_api_router"
    if module_name not in sys.modules:
        spec = importlib.util.spec_from_file_location(module_name, router_path)
        module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        sys.modules[module_name] = module
        spec.loader.exec_module(module)  # type: ignore[union-attr]
    else:
        module = sys.modules[module_name]

    db = MagicMock(spec=DatabaseManager)
    db.pool.side_effect = KeyError("health")

    app = create_app(api_key="")
    app.dependency_overrides[module._get_db_manager] = lambda: db
    return app


def _make_bucket_row(
    *,
    bucket_start: datetime,
    value_mean: float = 100.0,
    value_min: float = 95.0,
    value_max: float = 105.0,
    sample_count: int = 12,
) -> _Row:
    """Build an asyncpg-like aggregation row as returned by the trend SQL."""
    return _row(
        {
            "bucket_start": bucket_start,
            "value_mean": value_mean,
            "value_min": value_min,
            "value_max": value_max,
            "sample_count": sample_count,
        }
    )


# ---------------------------------------------------------------------------
# Tests — daily bucketing
# ---------------------------------------------------------------------------


class TestTrendDailyBucket:
    async def test_daily_bucketing_returns_correct_shape(self):
        """GET /measurements/trend?type=glucose&bucket=daily returns TrendResponse shape."""
        day1 = datetime(2026, 5, 1, 0, 0, 0, tzinfo=UTC)
        day2 = datetime(2026, 5, 2, 0, 0, 0, tzinfo=UTC)
        rows = [
            _make_bucket_row(
                bucket_start=day1,
                value_mean=98.0,
                value_min=80.0,
                value_max=140.0,
                sample_count=288,
            ),
            _make_bucket_row(
                bucket_start=day2,
                value_mean=102.0,
                value_min=85.0,
                value_max=155.0,
                sample_count=290,
            ),
        ]
        pool = _mock_pool(fetch_rows=rows)
        app = _build_app(pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/health/measurements/trend",
                params={"type": "glucose", "window_days": 14, "bucket": "daily"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["type"] == "glucose"
        assert body["window_days"] == 14
        assert body["bucket"] == "daily"
        assert len(body["buckets"]) == 2
        first = body["buckets"][0]
        assert first["value_mean"] == pytest.approx(98.0)
        assert first["value_min"] == pytest.approx(80.0)
        assert first["value_max"] == pytest.approx(140.0)
        assert first["sample_count"] == 288
        assert "bucket_start" in first

    async def test_daily_sql_has_no_butler_name_filter(self):
        """Pool is butler-scoped — the trend SQL must not filter on butler_name."""
        pool = _mock_pool(fetch_rows=[])
        app = _build_app(pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            await client.get(
                "/api/health/measurements/trend",
                params={"type": "weight", "bucket": "daily"},
            )

        fetch_calls = pool.fetch.call_args_list
        assert len(fetch_calls) == 1
        sql = fetch_calls[0][0][0]
        assert "butler_name" not in sql


# ---------------------------------------------------------------------------
# Tests — hourly bucketing
# ---------------------------------------------------------------------------


class TestTrendHourlyBucket:
    async def test_hourly_bucketing_returns_correct_shape(self):
        """GET /measurements/trend?type=glucose&bucket=hourly returns TrendResponse shape."""
        hour1 = datetime(2026, 5, 10, 8, 0, 0, tzinfo=UTC)
        hour2 = datetime(2026, 5, 10, 9, 0, 0, tzinfo=UTC)
        rows = [
            _make_bucket_row(
                bucket_start=hour1, value_mean=90.0, value_min=85.0, value_max=95.0, sample_count=12
            ),
            _make_bucket_row(
                bucket_start=hour2,
                value_mean=110.0,
                value_min=100.0,
                value_max=120.0,
                sample_count=11,
            ),
        ]
        pool = _mock_pool(fetch_rows=rows)
        app = _build_app(pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/health/measurements/trend",
                params={"type": "glucose", "window_days": 1, "bucket": "hourly"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["bucket"] == "hourly"
        assert len(body["buckets"]) == 2
        first = body["buckets"][0]
        assert first["value_mean"] == pytest.approx(90.0)
        assert first["sample_count"] == 12


# ---------------------------------------------------------------------------
# Tests — empty result
# ---------------------------------------------------------------------------


class TestTrendEmptyResult:
    async def test_empty_result_returns_empty_buckets(self):
        """When no facts match, returns {buckets: []} with correct envelope."""
        pool = _mock_pool(fetch_rows=[])
        app = _build_app(pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/health/measurements/trend",
                params={"type": "weight", "window_days": 7, "bucket": "daily"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["type"] == "weight"
        assert body["window_days"] == 7
        assert body["bucket"] == "daily"
        assert body["buckets"] == []


# ---------------------------------------------------------------------------
# Tests — parameter validation
# ---------------------------------------------------------------------------


class TestTrendValidation:
    async def test_invalid_window_days_returns_422(self):
        """window_days not in {1, 7, 14, 30, 90} returns HTTP 422."""
        pool = _mock_pool(fetch_rows=[])
        app = _build_app(pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/health/measurements/trend",
                params={"type": "glucose", "window_days": 15},
            )

        assert resp.status_code == 422

    async def test_invalid_bucket_returns_422(self):
        """bucket not in {'hourly', 'daily'} returns HTTP 422."""
        pool = _mock_pool(fetch_rows=[])
        app = _build_app(pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/health/measurements/trend",
                params={"type": "glucose", "bucket": "weekly"},
            )

        assert resp.status_code == 422

    async def test_missing_type_param_returns_422(self):
        """Omitting the required type param returns HTTP 422."""
        pool = _mock_pool(fetch_rows=[])
        app = _build_app(pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/health/measurements/trend")

        assert resp.status_code == 422

    @pytest.mark.parametrize("window_days", [1, 7, 14, 30, 90])
    async def test_all_valid_window_days_accepted(self, window_days: int):
        """Each value in the allowed set returns 200."""
        pool = _mock_pool(fetch_rows=[])
        app = _build_app(pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/health/measurements/trend",
                params={"type": "weight", "window_days": window_days},
            )

        assert resp.status_code == 200

    @pytest.mark.parametrize("bucket", ["hourly", "daily"])
    async def test_both_valid_bucket_values_accepted(self, bucket: str):
        """Both 'hourly' and 'daily' return 200."""
        pool = _mock_pool(fetch_rows=[])
        app = _build_app(pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/health/measurements/trend",
                params={"type": "glucose", "bucket": bucket},
            )

        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Tests — 503 path
# ---------------------------------------------------------------------------


class TestTrend503:
    async def test_503_when_pool_unavailable(self):
        """Returns HTTP 503 when the health DB pool is not available."""
        app = _build_app_unavailable()

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/health/measurements/trend",
                params={"type": "glucose"},
            )

        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Tests — scalar-only filter (object-valued metadata excluded from SQL)
# ---------------------------------------------------------------------------


class TestTrendScalarFilter:
    async def test_sql_filters_out_object_valued_metadata(self):
        """SQL must exclude compound JSON object values (e.g. blood_pressure).

        The trend SQL must include a guard so that rows whose ``metadata.value``
        is a JSON object (not a scalar number) are excluded at the database
        level.  This prevents Postgres cast errors when e.g. weight or
        blood_pressure rows store ``{kg: ...}`` / ``{systolic: ..., diastolic:
        ...}`` as the value.
        """
        pool = _mock_pool(fetch_rows=[])
        app = _build_app(pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            await client.get(
                "/api/health/measurements/trend",
                params={"type": "blood_pressure", "bucket": "daily"},
            )

        fetch_calls = pool.fetch.call_args_list
        assert len(fetch_calls) == 1
        sql = fetch_calls[0][0][0]
        # The SQL must guard against non-numeric JSON values before casting.
        # Either jsonb_typeof or a numeric regex guard must be present.
        has_type_guard = "jsonb_typeof" in sql
        has_regex_guard = "~ '^-?" in sql or "regexp_like" in sql
        assert has_type_guard or has_regex_guard, (
            "SQL must guard against non-numeric metadata.value before casting to float"
        )
