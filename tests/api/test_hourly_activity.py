"""Tests for GET /api/butlers/{name}/analytics/hourly-activity.

Verifies:
- Default window_hours=24 returns 24 buckets (dense series, all hours present).
- Custom window_hours=6 returns 6 buckets including zero-count hours.
- hour_index=0 is the newest hour; hour_index increases backward in time.
- Butler with no sessions in window returns 24 zero-count buckets.
- Missing butler DB returns 503.
- window_hours outside [1, 24] is rejected with 422.
- SQL uses a single positional arg (window_hours) with no butler_name filter.
"""

from __future__ import annotations

import datetime
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.routers.sessions import _HOURLY_ACTIVITY_SQL, _get_db_manager

pytestmark = pytest.mark.unit

_BASE = "http://test"
_URL = "/api/butlers/atlas/analytics/hourly-activity"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_hourly_row(*, hour_start: datetime.datetime, sessions_count: int) -> MagicMock:
    """Build a mock asyncpg Record for the hourly-activity query."""
    row = MagicMock()
    row.__getitem__ = MagicMock(
        side_effect=lambda key: {
            "hour_start": hour_start,
            "sessions_count": sessions_count,
        }[key]
    )
    return row


def _make_app_with_rows(rows: list[MagicMock]) -> object:
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


async def test_hourly_activity_default_window() -> None:
    """Default window_hours=24 returns 24 buckets (dense series, newest-first)."""
    now = datetime.datetime(2026, 5, 11, 14, 0, 0, tzinfo=datetime.UTC)
    # Dense series: SQL returns all 24 hours newest-first, zero-count hours included.
    rows = [
        _make_hourly_row(hour_start=now - datetime.timedelta(hours=i), sessions_count=0)
        for i in range(24)
    ]
    rows[0] = _make_hourly_row(hour_start=now, sessions_count=1)
    rows[2] = _make_hourly_row(hour_start=now - datetime.timedelta(hours=2), sessions_count=4)
    rows[5] = _make_hourly_row(hour_start=now - datetime.timedelta(hours=5), sessions_count=2)
    app = _make_app_with_rows(rows)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=_BASE) as client:
        resp = await client.get(_URL)

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "buckets" in data
    buckets = data["buckets"]
    assert len(buckets) == 24


async def test_hourly_activity_window_hours_6() -> None:
    """window_hours=6 returns exactly 6 buckets including zero-count hours."""
    now = datetime.datetime(2026, 5, 11, 14, 0, 0, tzinfo=datetime.UTC)
    # Dense series: SQL returns all 6 hours newest-first, zero-count hours included.
    rows = [
        _make_hourly_row(hour_start=now - datetime.timedelta(hours=i), sessions_count=0)
        for i in range(6)
    ]
    rows[1] = _make_hourly_row(hour_start=now - datetime.timedelta(hours=1), sessions_count=5)
    rows[3] = _make_hourly_row(hour_start=now - datetime.timedelta(hours=3), sessions_count=3)
    app = _make_app_with_rows(rows)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=_BASE) as client:
        resp = await client.get(f"{_URL}?window_hours=6")

    assert resp.status_code == 200
    buckets = resp.json()["data"]["buckets"]
    assert len(buckets) == 6


async def test_hourly_activity_hour_index_ordering() -> None:
    """hour_index=0 is the newest (current) hour; higher indexes go back in time.

    Verifies the midnight boundary case: rows spanning two calendar days are
    still ordered correctly by hour_index.  The dense-series SQL orders rows
    newest-first so hour_index equals the enumeration position directly.
    """
    # Midnight boundary: rows span 2026-05-10 22:00 → 2026-05-11 00:00
    h0 = datetime.datetime(2026, 5, 11, 0, 0, 0, tzinfo=datetime.UTC)  # newest
    h1 = datetime.datetime(2026, 5, 10, 23, 0, 0, tzinfo=datetime.UTC)
    h2 = datetime.datetime(2026, 5, 10, 22, 0, 0, tzinfo=datetime.UTC)

    # Dense-series SQL returns newest-first (window_hours=3)
    rows = [
        _make_hourly_row(hour_start=h0, sessions_count=2),
        _make_hourly_row(hour_start=h1, sessions_count=3),
        _make_hourly_row(hour_start=h2, sessions_count=1),
    ]
    app = _make_app_with_rows(rows)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=_BASE) as client:
        resp = await client.get(f"{_URL}?window_hours=3")

    assert resp.status_code == 200
    buckets = resp.json()["data"]["buckets"]
    assert len(buckets) == 3

    # hour_index=0 must be the newest hour
    assert buckets[0]["hour_index"] == 0
    assert buckets[0]["sessions_count"] == 2  # h0

    # hour_index=1 is one hour back
    assert buckets[1]["hour_index"] == 1
    assert buckets[1]["sessions_count"] == 3  # h1

    # hour_index=2 is two hours back (crosses midnight)
    assert buckets[2]["hour_index"] == 2
    assert buckets[2]["sessions_count"] == 1  # h2


async def test_hourly_activity_empty_butler() -> None:
    """When the butler has no sessions, returns 24 zero-count buckets (dense series)."""
    now = datetime.datetime(2026, 5, 11, 14, 0, 0, tzinfo=datetime.UTC)
    # Dense-series SQL always returns N rows even with no sessions.
    rows = [
        _make_hourly_row(hour_start=now - datetime.timedelta(hours=i), sessions_count=0)
        for i in range(24)
    ]
    app = _make_app_with_rows(rows)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=_BASE) as client:
        resp = await client.get(_URL)

    assert resp.status_code == 200
    data = resp.json()["data"]
    buckets = data["buckets"]
    assert len(buckets) == 24
    assert all(b["sessions_count"] == 0 for b in buckets)


async def test_hourly_activity_missing_butler_db_returns_503() -> None:
    """Returns 503 when the butler's DB pool is not registered."""
    app = _make_app_missing_butler()

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=_BASE) as client:
        resp = await client.get(_URL)

    assert resp.status_code == 503


async def test_hourly_activity_window_hours_too_low_returns_422() -> None:
    """window_hours=0 is below minimum and must be rejected with 422."""
    app = _make_app_with_rows([])

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=_BASE) as client:
        resp = await client.get(f"{_URL}?window_hours=0")

    assert resp.status_code == 422


async def test_hourly_activity_window_hours_too_high_returns_422() -> None:
    """window_hours=25 exceeds maximum and must be rejected with 422."""
    app = _make_app_with_rows([])

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=_BASE) as client:
        resp = await client.get(f"{_URL}?window_hours=25")

    assert resp.status_code == 422


async def test_hourly_activity_bucket_structure() -> None:
    """Each bucket has hour_start, sessions_count, and hour_index fields."""
    h = datetime.datetime(2026, 5, 11, 9, 0, 0, tzinfo=datetime.UTC)
    rows = [_make_hourly_row(hour_start=h, sessions_count=7)]
    app = _make_app_with_rows(rows)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=_BASE) as client:
        resp = await client.get(_URL)

    assert resp.status_code == 200
    bucket = resp.json()["data"]["buckets"][0]
    assert "hour_start" in bucket
    assert bucket["sessions_count"] == 7
    assert bucket["hour_index"] == 0


async def test_hourly_activity_sql_uses_single_positional_arg() -> None:
    """_HOURLY_ACTIVITY_SQL uses exactly one positional parameter ($1) for window_hours.

    Verifies the pool.fetch call is invoked with (sql, window_hours) only — no
    butler_name column filter is injected.  The sessions table is already
    butler-scoped via the DB pool; adding ``WHERE butler_name = $N`` would be a
    schema error because that column does not exist on the sessions table.
    """
    now = datetime.datetime(2026, 5, 11, 12, 0, 0, tzinfo=datetime.UTC)
    rows = [_make_hourly_row(hour_start=now, sessions_count=1)]
    app = _make_app_with_rows(rows)
    mock_pool = app.dependency_overrides[_get_db_manager]().pool()

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=_BASE) as client:
        await client.get(f"{_URL}?window_hours=6")

    # pool.fetch must be called with exactly (sql, window_hours) — no extra args.
    mock_pool.fetch.assert_called_once_with(_HOURLY_ACTIVITY_SQL, 6)
