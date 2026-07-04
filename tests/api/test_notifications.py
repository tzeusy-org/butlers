"""Tests for GET /api/notifications, /api/notifications/stats, and the
butler-scoped /api/butlers/{name}/notifications -- the source_available
degraded-mode contract (bu-qvnce.1).

A Switchboard pool that is genuinely unreachable (KeyError from
db.pool("switchboard")) must be distinguishable from a truthful "no
notifications match" result: both currently render an all-zero/empty
payload, but only the unreachable case must carry ``source_available: false``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.db import DatabaseManager
from butlers.api.routers.notifications import _get_db_manager

pytestmark = pytest.mark.unit


def _make_unavailable_db() -> MagicMock:
    """A DatabaseManager stand-in whose switchboard pool is unreachable."""
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.side_effect = KeyError("switchboard")
    return mock_db


def _make_available_db() -> tuple[MagicMock, AsyncMock]:
    """A DatabaseManager stand-in with a working switchboard pool."""
    pool = AsyncMock()
    pool.fetchval = AsyncMock(return_value=0)
    pool.fetch = AsyncMock(return_value=[])
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = pool
    return mock_db, pool


async def test_list_notifications_flags_source_unavailable_when_pool_unreachable(app):
    app.dependency_overrides[_get_db_manager] = _make_unavailable_db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/notifications")

    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] == []
    assert body["source_available"] is False


async def test_list_butler_notifications_flags_source_unavailable_when_pool_unreachable(app):
    app.dependency_overrides[_get_db_manager] = _make_unavailable_db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/butlers/finance/notifications")

    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] == []
    assert body["source_available"] is False


async def test_notification_stats_flags_source_unavailable_when_pool_unreachable(app):
    app.dependency_overrides[_get_db_manager] = _make_unavailable_db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/notifications/stats")

    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["total"] == 0
    assert body["data"]["source_available"] is False


async def test_list_notifications_reports_source_available_on_success(app):
    mock_db, _pool = _make_available_db()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/notifications")

    assert resp.status_code == 200
    body = resp.json()
    assert body["source_available"] is True


async def test_notification_stats_reports_source_available_on_success(app):
    mock_db, _pool = _make_available_db()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/notifications/stats")

    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["source_available"] is True
