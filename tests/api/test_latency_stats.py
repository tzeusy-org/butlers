"""Tests for GET /api/butlers/{name}/analytics/latency-stats.

Verifies:
- Seeded sessions with known durations return correct p50/p95/mean/count.
- window_days defaults to 7.
- Empty result (no sessions with duration_ms) returns count=0 and None fields.
- model field reflects the most-frequent model in the window.
- Missing butler DB returns 503.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.routers.sessions import _get_db_manager

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_latency_row(
    *,
    p50_ms: float | None,
    p95_ms: float | None,
    mean_ms: float | None,
    count: int,
    model: str | None,
) -> MagicMock:
    """Build a mock asyncpg Record for the latency-stats query."""
    row = MagicMock()
    row.__getitem__ = MagicMock(
        side_effect=lambda key: {
            "p50_ms": p50_ms,
            "p95_ms": p95_ms,
            "mean_ms": mean_ms,
            "count": count,
            "model": model,
        }[key]
    )
    return row


def _make_app_with_latency_row(
    row: MagicMock | None,
) -> object:
    """Wire a fresh app with a mock pool returning the given fetchrow result."""
    mock_pool = AsyncMock()
    mock_pool.fetchrow = AsyncMock(return_value=row)

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool

    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    return app


def _make_app_missing_butler(butler_name: str) -> object:
    """Wire a fresh app where db.pool() raises KeyError for the butler."""
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.side_effect = KeyError(f"No pool for butler: {butler_name}")

    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    return app


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_latency_stats_seeded_sessions() -> None:
    """Returns correct p50, p95, mean, count for sessions with known durations."""
    row = _make_latency_row(
        p50_ms=500.0,
        p95_ms=950.0,
        mean_ms=520.0,
        count=10,
        model="claude-sonnet-4-5",
    )
    app = _make_app_with_latency_row(row)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/butlers/atlas/analytics/latency-stats")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["p50_ms"] == pytest.approx(500.0)
    assert data["p95_ms"] == pytest.approx(950.0)
    assert data["mean_ms"] == pytest.approx(520.0)
    assert data["count"] == 10
    assert data["model"] == "claude-sonnet-4-5"


async def test_latency_stats_default_window_days() -> None:
    """window_days defaults to 7 — endpoint is callable without the param."""
    row = _make_latency_row(p50_ms=300.0, p95_ms=800.0, mean_ms=350.0, count=5, model=None)
    app = _make_app_with_latency_row(row)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        # No window_days param — should succeed with 200 using the default of 7
        resp = await client.get("/api/butlers/general/analytics/latency-stats")

    assert resp.status_code == 200
    assert resp.json()["data"]["count"] == 5


async def test_latency_stats_window_days_forwarded() -> None:
    """window_days query param is forwarded to the SQL query as an integer."""
    mock_pool = AsyncMock()
    captured_args: list = []

    async def _fetchrow(_sql: str, *args):
        captured_args.extend(args)
        return _make_latency_row(p50_ms=100.0, p95_ms=200.0, mean_ms=150.0, count=3, model=None)

    mock_pool.fetchrow = AsyncMock(side_effect=_fetchrow)
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool

    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/butlers/atlas/analytics/latency-stats?window_days=30")

    assert resp.status_code == 200
    # The first positional arg after the SQL string should be window_days as an int
    assert captured_args[0] == 30


async def test_latency_stats_empty_result_returns_zeros() -> None:
    """When no sessions have duration_ms, returns count=0 and None percentiles."""
    # Simulate the DB returning a row where count=0 (percentile_cont yields NULL)
    row = _make_latency_row(p50_ms=None, p95_ms=None, mean_ms=None, count=0, model=None)
    app = _make_app_with_latency_row(row)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/butlers/atlas/analytics/latency-stats")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["count"] == 0
    assert data["p50_ms"] is None
    assert data["p95_ms"] is None
    assert data["mean_ms"] is None
    assert data["model"] is None


async def test_latency_stats_null_fetchrow_returns_zeros() -> None:
    """When fetchrow returns None (table missing / empty), returns count=0."""
    app = _make_app_with_latency_row(None)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/butlers/atlas/analytics/latency-stats")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["count"] == 0
    assert data["p50_ms"] is None


async def test_latency_stats_missing_butler_db_returns_503() -> None:
    """Returns 503 when the butler's DB pool is not registered."""
    app = _make_app_missing_butler("unknown-butler")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/butlers/unknown-butler/analytics/latency-stats")

    assert resp.status_code == 503


async def test_latency_stats_model_most_frequent() -> None:
    """model field reflects the most-frequent model returned by mode() aggregate."""
    row = _make_latency_row(
        p50_ms=400.0,
        p95_ms=700.0,
        mean_ms=450.0,
        count=20,
        model="claude-opus-4-5",
    )
    app = _make_app_with_latency_row(row)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/butlers/atlas/analytics/latency-stats")

    assert resp.status_code == 200
    assert resp.json()["data"]["model"] == "claude-opus-4-5"


async def test_latency_stats_window_days_validation() -> None:
    """window_days must be >= 1 (FastAPI 422 for invalid values)."""
    app = _make_app_with_latency_row(None)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/butlers/atlas/analytics/latency-stats?window_days=0")

    assert resp.status_code == 422
