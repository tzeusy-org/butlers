"""Tests for GET /api/butlers/{name}/analytics/session-kinds.

Verifies:
- Sessions with multiple trigger_source values bucket correctly.
- window_days defaults to 7 (endpoint callable without the param).
- window_days=0 is valid (non-negative); window_days=-1 returns 422.
- window_days is forwarded to the SQL as an integer (not a string).
- Empty result (no sessions) returns kinds=[].
- Missing butler DB returns 503.
- butler_name is passed as $1 to the SQL.
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


def _make_kind_row(kind: str, count: int) -> MagicMock:
    """Build a mock asyncpg Record for one trigger_source bucket."""
    row = MagicMock()
    row.__getitem__ = MagicMock(
        side_effect=lambda key: {"trigger_source": kind, "count": count}[key]
    )
    return row


def _make_app_with_kind_rows(rows: list[MagicMock]) -> object:
    """Wire a fresh app with a mock pool returning the given fetch result."""
    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(return_value=rows)

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


async def test_session_kinds_multiple_sources() -> None:
    """Sessions with multiple trigger_source values bucket correctly."""
    rows = [
        _make_kind_row("tick", 42),
        _make_kind_row("manual", 7),
        _make_kind_row("qa", 3),
        _make_kind_row("healing", 1),
    ]
    app = _make_app_with_kind_rows(rows)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/butlers/atlas/analytics/session-kinds")

    assert resp.status_code == 200
    kinds = resp.json()["data"]["kinds"]
    assert len(kinds) == 4

    by_kind = {item["kind"]: item["count"] for item in kinds}
    assert by_kind["tick"] == 42
    assert by_kind["manual"] == 7
    assert by_kind["qa"] == 3
    assert by_kind["healing"] == 1


async def test_session_kinds_default_window_days() -> None:
    """window_days defaults to 7 — endpoint is callable without the param."""
    rows = [_make_kind_row("tick", 10)]
    app = _make_app_with_kind_rows(rows)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/butlers/general/analytics/session-kinds")

    assert resp.status_code == 200
    assert resp.json()["data"]["kinds"][0]["kind"] == "tick"


async def test_session_kinds_window_days_forwarded_as_int() -> None:
    """window_days and butler_name are forwarded to the SQL correctly."""
    captured_args: list = []

    async def _fetch(_sql: str, *args):
        captured_args.extend(args)
        return [_make_kind_row("schedule:daily", 5)]

    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(side_effect=_fetch)
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool

    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/butlers/atlas/analytics/session-kinds?window_days=30")

    assert resp.status_code == 200
    # $1 = butler_name, $2 = window_days as int
    assert captured_args[0] == "atlas"
    assert captured_args[1] == 30
    assert isinstance(captured_args[1], int)


async def test_session_kinds_empty_result() -> None:
    """When no sessions exist in the window, returns kinds=[]."""
    app = _make_app_with_kind_rows([])

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/butlers/atlas/analytics/session-kinds")

    assert resp.status_code == 200
    assert resp.json()["data"]["kinds"] == []


async def test_session_kinds_missing_butler_db_returns_503() -> None:
    """Returns 503 when the butler's DB pool is not registered."""
    app = _make_app_missing_butler("unknown-butler")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/butlers/unknown-butler/analytics/session-kinds")

    assert resp.status_code == 503


async def test_session_kinds_window_days_zero_is_valid() -> None:
    """window_days=0 is accepted (non-negative integer)."""
    app = _make_app_with_kind_rows([])

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/butlers/atlas/analytics/session-kinds?window_days=0")

    assert resp.status_code == 200


async def test_session_kinds_negative_window_days_returns_422() -> None:
    """window_days=-1 is rejected with 422."""
    app = _make_app_with_kind_rows([])

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/butlers/atlas/analytics/session-kinds?window_days=-1")

    assert resp.status_code == 422


async def test_session_kinds_schedule_wildcard_source() -> None:
    """schedule:* trigger sources are surfaced as-is without normalization."""
    rows = [
        _make_kind_row("schedule:morning-check", 15),
        _make_kind_row("schedule:nightly-cleanup", 8),
    ]
    app = _make_app_with_kind_rows(rows)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/butlers/herald/analytics/session-kinds")

    assert resp.status_code == 200
    kinds = resp.json()["data"]["kinds"]
    by_kind = {item["kind"]: item["count"] for item in kinds}
    assert by_kind["schedule:morning-check"] == 15
    assert by_kind["schedule:nightly-cleanup"] == 8
