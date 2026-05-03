"""Condensed tests for miscellaneous API endpoints.

Condensed from:
  test_sessions.py (15) + test_notifications.py (15) + test_issues.py (15)
  + test_sse.py (15) + test_connectivity.py (11) + test_middleware.py (11)
  + test_general.py (16) + test_health.py (16) + test_home.py (16)
  + test_finance_api.py (16) + test_ha_credentials_roundtrip.py (6)
  + test_auth.py (10) + test_app.py (9) + test_app_integration.py (8)
  + test_audit.py (14) → ~20 tests (bu-egmz6) → 8 tests (bu-2yw2d)

Keeps: health/CORS, auth gate (parametrized), middleware error codes,
       sessions 200+404, SSE broadcast, audit 200, notifications 200, home 503.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.deps import (
    ButlerNotFoundError,
    ButlerUnreachableError,
)
from butlers.api.routers.audit import _get_db_manager as _audit_get_db
from butlers.api.routers.sessions import _get_db_manager as _sessions_get_db
from butlers.api.routers.sse import _subscribers, broadcast

pytestmark = pytest.mark.unit

_NOW = datetime.now(tz=UTC)
_roster_root = Path(__file__).resolve().parents[2] / "roster"


# ---------------------------------------------------------------------------
# App / health / CORS / auth (parametrized)
# ---------------------------------------------------------------------------


async def test_health_returns_ok():
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.parametrize(
    "api_key,headers,path,expected",
    [
        ("", {}, "/api/health", 200),           # auth disabled
        ("secret", {}, "/api/butlers", 401),    # missing key → 401
        ("secret", {}, "/api/health", 200),     # health bypasses auth
    ],
    ids=["auth-disabled", "missing-key-401", "health-bypasses-auth"],
)
async def test_auth_gate(api_key, headers, path, expected):
    app = create_app(api_key=api_key)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(path, headers=headers)
    assert resp.status_code == expected


async def test_valid_api_key_grants_access_to_protected_endpoint():
    """A valid X-API-Key must not be rejected with 401 on a protected route."""
    app = create_app(api_key="secret")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/butlers", headers={"X-API-Key": "secret"})
    assert resp.status_code != 401


# ---------------------------------------------------------------------------
# Error middleware
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path,exception,expected",
    [
        ("/api/test/unreachable",
         ButlerUnreachableError("atlas", cause=ConnectionRefusedError("conn refused")),
         (502, 503)),
        ("/api/test/not-found", ButlerNotFoundError("atlas"), (404, 404)),
    ],
    ids=["unreachable-5xx", "not-found-404"],
)
async def test_middleware_error_codes(app, path, exception, expected):
    @app.get(path)
    async def _raise():
        raise exception

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(path)
    lo, hi = expected
    assert lo <= resp.status_code <= hi


# ---------------------------------------------------------------------------
# Sessions API
# ---------------------------------------------------------------------------


class TestSessionsAPI:
    def _make_app(self, app, *, fetch_rows=None, fetchrow=None, fetchval=0):
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=fetch_rows or [])
        pool.fetchval = AsyncMock(return_value=fetchval)
        pool.fetchrow = AsyncMock(return_value=fetchrow)
        db = MagicMock(spec=DatabaseManager)
        db.pool.return_value = pool
        db.butler_names = ["atlas"]
        db.fan_out = AsyncMock(return_value={})
        app.dependency_overrides[_sessions_get_db] = lambda: db
        return app

    async def test_list_returns_paginated_structure_and_404_for_missing(self, app):
        self._make_app(app)
        sid = uuid4()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            r_list = await client.get("/api/sessions")
            r_404 = await client.get(f"/api/butlers/atlas/sessions/{sid}")
        assert r_list.status_code == 200
        assert "data" in r_list.json() and "meta" in r_list.json()
        assert r_404.status_code == 404


# ---------------------------------------------------------------------------
# Audit API
# ---------------------------------------------------------------------------


async def test_audit_log_returns_paginated_structure(app):
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchval = AsyncMock(return_value=0)
    pool.execute = AsyncMock()
    db = MagicMock(spec=DatabaseManager)
    db.pool.return_value = pool
    app.dependency_overrides[_audit_get_db] = lambda: db
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/audit-log")
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body and "meta" in body


# ---------------------------------------------------------------------------
# SSE — broadcast delivers to subscriber
# ---------------------------------------------------------------------------


def test_sse_broadcast_delivers_to_subscriber():
    queue: asyncio.Queue = asyncio.Queue(maxsize=256)
    _subscribers.append(queue)
    try:
        broadcast("test_event", {"hello": "world"})
        event = queue.get_nowait()
        assert event["type"] == "test_event"
    finally:
        _subscribers.remove(queue)


# ---------------------------------------------------------------------------
# Notifications API
# ---------------------------------------------------------------------------


async def test_notifications_returns_paginated_structure():
    from tests.api.conftest import build_notifications_app

    app, _pool, _db = build_notifications_app(rows=[], total=0)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/notifications")
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body and "meta" in body


# ---------------------------------------------------------------------------
# Home butler API — 503 when pool unavailable
# ---------------------------------------------------------------------------


async def test_home_devices_503_when_pool_unavailable(app):
    db = MagicMock(spec=DatabaseManager)
    db.pool.side_effect = KeyError("no pool")
    for butler_name, router_module in app.state.butler_routers:
        if butler_name == "home" and hasattr(router_module, "_get_db_manager"):
            app.dependency_overrides[router_module._get_db_manager] = lambda: db
            break
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/home/devices")
    assert resp.status_code == 503
