"""Tests for GET /api/butlers/{name}/analytics/daily-activity.

Verifies:
- 7-day window returns correct buckets (default window).
- 30-day window returns correct buckets.
- Empty result (no sessions in window) returns ``buckets: []``.
- window_days not in {7, 30} is rejected with 422.
- Missing butler DB returns 503.
"""

from __future__ import annotations

import datetime
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.routers.sessions import _get_db_manager

pytestmark = pytest.mark.unit

_BASE = "http://test"
_URL = "/api/butlers/atlas/analytics/daily-activity"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_activity_row(*, d: datetime.date, sessions_count: int) -> MagicMock:
    """Build a mock asyncpg Record for the daily-activity query."""
    row = MagicMock()
    row.__getitem__ = MagicMock(
        side_effect=lambda key: {"d": d, "sessions_count": sessions_count}[key]
    )
    return row


def _make_app_with_rows(butler_name: str, rows: list[MagicMock]) -> object:
    """Wire a fresh app with a mock pool returning the given fetch result."""
    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(return_value=rows)

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool

    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    return app


def _make_app_missing_butler() -> object:
    """Wire a fresh app where db.pool() raises KeyError."""
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.side_effect = KeyError("No pool for butler: atlas")

    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    return app


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_daily_activity_7d_window() -> None:
    """Default window_days=7 returns a bucket for each day with sessions."""
    today = datetime.date(2026, 5, 11)
    rows = [
        _make_activity_row(d=today - datetime.timedelta(days=2), sessions_count=3),
        _make_activity_row(d=today - datetime.timedelta(days=1), sessions_count=5),
        _make_activity_row(d=today, sessions_count=1),
    ]
    app = _make_app_with_rows("atlas", rows)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=_BASE) as client:
        resp = await client.get(_URL)

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "buckets" in data
    buckets = data["buckets"]
    assert len(buckets) == 3
    assert buckets[0]["sessions_count"] == 3
    assert buckets[1]["sessions_count"] == 5
    assert buckets[2]["sessions_count"] == 1


async def test_daily_activity_30d_window() -> None:
    """window_days=30 is accepted and returns correct buckets."""
    today = datetime.date(2026, 5, 11)
    rows = [
        _make_activity_row(d=today - datetime.timedelta(days=25), sessions_count=2),
        _make_activity_row(d=today, sessions_count=4),
    ]
    app = _make_app_with_rows("atlas", rows)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=_BASE) as client:
        resp = await client.get(f"{_URL}?window_days=30")

    assert resp.status_code == 200
    buckets = resp.json()["data"]["buckets"]
    assert len(buckets) == 2
    assert buckets[0]["sessions_count"] == 2
    assert buckets[1]["sessions_count"] == 4


async def test_daily_activity_empty_butler() -> None:
    """When the butler has no sessions in the window, returns empty buckets list."""
    app = _make_app_with_rows("atlas", [])

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=_BASE) as client:
        resp = await client.get(_URL)

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["buckets"] == []


async def test_daily_activity_invalid_window_days_1() -> None:
    """window_days=1 is not in {7, 30} and must be rejected with 422."""
    app = _make_app_with_rows("atlas", [])

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=_BASE) as client:
        resp = await client.get(f"{_URL}?window_days=1")

    assert resp.status_code == 422


async def test_daily_activity_invalid_window_days_14() -> None:
    """window_days=14 is not in {7, 30} and must be rejected with 422."""
    app = _make_app_with_rows("atlas", [])

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=_BASE) as client:
        resp = await client.get(f"{_URL}?window_days=14")

    assert resp.status_code == 422


async def test_daily_activity_missing_butler_db_returns_503() -> None:
    """Returns 503 when the butler's DB pool is not registered."""
    app = _make_app_missing_butler()

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=_BASE) as client:
        resp = await client.get(_URL)

    assert resp.status_code == 503


async def test_daily_activity_bucket_structure() -> None:
    """Each bucket has the expected ``date`` and ``sessions_count`` fields."""
    d = datetime.date(2026, 5, 10)
    rows = [_make_activity_row(d=d, sessions_count=7)]
    app = _make_app_with_rows("atlas", rows)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=_BASE) as client:
        resp = await client.get(_URL)

    assert resp.status_code == 200
    bucket = resp.json()["data"]["buckets"][0]
    assert bucket["date"] == "2026-05-10"
    assert bucket["sessions_count"] == 7
