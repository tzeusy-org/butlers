"""Condensed tests for miscellaneous API endpoints.

Condensed from:
  test_sessions.py (15) + test_notifications.py (15) + test_issues.py (15)
  + test_sse.py (15) + test_connectivity.py (11) + test_middleware.py (11)
  + test_general.py (16) + test_health.py (16) + test_home.py (16)
  + test_finance_api.py (16) + test_ha_credentials_roundtrip.py (6)
  + test_auth.py (10) + test_app.py (9) + test_app_integration.py (8)
  + test_audit.py (14) → ~20 tests (bu-egmz6)

Keeps: key status codes, 503 error paths, structural assertions.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.deps import (
    ButlerConnectionInfo,
    ButlerNotFoundError,
    ButlerUnreachableError,
    MCPClientManager,
    get_butler_configs,
    get_mcp_manager,
)
from butlers.api.routers.audit import _get_db_manager as _audit_get_db
from butlers.api.routers.issues import _get_db_manager as _issues_get_db
from butlers.api.routers.sessions import _get_db_manager as _sessions_get_db
from butlers.api.routers.sse import _subscribers, broadcast

pytestmark = pytest.mark.unit

_NOW = datetime.now(tz=UTC)
_roster_root = Path(__file__).resolve().parents[2] / "roster"


# ---------------------------------------------------------------------------
# App / health / CORS
# ---------------------------------------------------------------------------


class TestApp:
    async def test_health_returns_ok(self):
        app = create_app()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            resp = await client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    async def test_cors_allows_configured_origin(self):
        app = create_app(cors_origins=["http://localhost:41173"])
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            resp = await client.options(
                "/api/health",
                headers={"origin": "http://localhost:41173",
                         "access-control-request-method": "GET"},
            )
        assert resp.status_code == 200
        assert resp.headers.get("access-control-allow-origin") == "http://localhost:41173"


# ---------------------------------------------------------------------------
# Auth middleware
# ---------------------------------------------------------------------------


class TestAuth:
    async def test_auth_disabled_allows_all(self):
        app = create_app(api_key="")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            resp = await client.get("/api/health")
        assert resp.status_code == 200

    async def test_auth_missing_key_returns_401(self):
        app = create_app(api_key="secret")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            resp = await client.get("/api/butlers")
        assert resp.status_code == 401

    async def test_auth_valid_key_allows_request(self):
        app = create_app(api_key="secret")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            resp = await client.get("/api/butlers",
                                    headers={"X-API-Key": "secret"})
        assert resp.status_code != 401

    async def test_health_bypasses_auth(self):
        app = create_app(api_key="secret")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            resp = await client.get("/api/health")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Error middleware
# ---------------------------------------------------------------------------


class TestMiddleware:
    def _make_app(self, app):
        @app.get("/api/test/unreachable")
        async def raise_unreachable():
            raise ButlerUnreachableError("atlas", cause=ConnectionRefusedError("conn refused"))

        @app.get("/api/test/not-found")
        async def raise_not_found():
            raise ButlerNotFoundError("atlas")

        return app

    async def test_unreachable_returns_5xx(self, app):
        self._make_app(app)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            resp = await client.get("/api/test/unreachable")
        assert resp.status_code in (502, 503)

    async def test_not_found_returns_404(self, app):
        self._make_app(app)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            resp = await client.get("/api/test/not-found")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Sessions API
# ---------------------------------------------------------------------------


class TestSessionsAPI:
    def _make_app(self, app, *, fetch_rows=None, fetchrow=None, fetchval=0, fan_out=None):
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=fetch_rows or [])
        pool.fetchval = AsyncMock(return_value=fetchval)
        pool.fetchrow = AsyncMock(return_value=fetchrow)
        db = MagicMock(spec=DatabaseManager)
        db.pool.return_value = pool
        db.butler_names = ["atlas"]
        db.fan_out = AsyncMock(return_value=fan_out or {})
        app.dependency_overrides[_sessions_get_db] = lambda: db
        return app

    async def test_list_sessions_returns_paginated_structure(self, app):
        self._make_app(app)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            resp = await client.get("/api/sessions")
        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body and "meta" in body

    async def test_get_session_404_when_not_found(self, app):
        self._make_app(app, fetchrow=None)
        sid = uuid4()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            resp = await client.get(f"/api/butlers/atlas/sessions/{sid}")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Audit API
# ---------------------------------------------------------------------------


class TestAuditAPI:
    def _make_app(self, app, *, fetch_rows=None, fetchval=0):
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=fetch_rows or [])
        pool.fetchval = AsyncMock(return_value=fetchval)
        pool.execute = AsyncMock()
        db = MagicMock(spec=DatabaseManager)
        db.pool.return_value = pool
        app.dependency_overrides[_audit_get_db] = lambda: db
        return app

    async def test_audit_log_returns_paginated_structure(self, app):
        self._make_app(app)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            resp = await client.get("/api/audit-log")
        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body and "meta" in body


# ---------------------------------------------------------------------------
# Issues API
# ---------------------------------------------------------------------------


class TestIssuesAPI:
    def _make_app(self, app, *, online=True):
        mgr = MagicMock(spec=MCPClientManager)
        if online:
            client = MagicMock()
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            client.ping = AsyncMock(return_value=True)
            mgr.get_client = AsyncMock(return_value=client)
        else:
            mgr.get_client = AsyncMock(side_effect=ButlerUnreachableError("general", "down"))
        configs = [ButlerConnectionInfo("general", 41101)]
        db = MagicMock(spec=DatabaseManager)
        db.pool.return_value = AsyncMock()
        app.dependency_overrides[get_mcp_manager] = lambda: mgr
        app.dependency_overrides[get_butler_configs] = lambda: configs
        app.dependency_overrides[_issues_get_db] = lambda: db
        return app

    async def test_issues_returns_200(self, app):
        self._make_app(app, online=True)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            resp = await client.get("/api/issues")
        assert resp.status_code == 200
        body = resp.json()
        # issues returns either a list or {"data": [...]}
        assert isinstance(body, list) or "data" in body


# ---------------------------------------------------------------------------
# SSE unit tests
# ---------------------------------------------------------------------------


class TestSSE:
    def test_broadcast_to_empty_subscribers_does_not_raise(self):
        broadcast("test", {"key": "value"})

    def test_broadcast_delivers_to_subscriber(self):
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


class TestNotificationsAPI:
    async def test_list_returns_paginated_structure(self):
        from tests.api.conftest import build_notifications_app
        app, _pool, _db = build_notifications_app(rows=[], total=0)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            resp = await client.get("/api/notifications")
        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body and "meta" in body


# ---------------------------------------------------------------------------
# Home butler API
# ---------------------------------------------------------------------------


class TestHomeButlerAPI:
    def _make_app(self, app, *, fetch_rows=None, fetchval=0, pool_available=True):
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=fetch_rows or [])
        pool.fetchval = AsyncMock(return_value=fetchval)
        db = MagicMock(spec=DatabaseManager)
        if pool_available:
            db.pool.return_value = pool
        else:
            db.pool.side_effect = KeyError("no pool")
        for butler_name, router_module in app.state.butler_routers:
            if butler_name == "home" and hasattr(router_module, "_get_db_manager"):
                app.dependency_overrides[router_module._get_db_manager] = lambda: db
                break
        return app

    async def test_home_devices_returns_200(self, app):
        self._make_app(app)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            resp = await client.get("/api/home/devices")
        assert resp.status_code == 200

    async def test_home_devices_503_when_pool_unavailable(self, app):
        self._make_app(app, pool_available=False)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            resp = await client.get("/api/home/devices")
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Finance butler API (roster-based)
# ---------------------------------------------------------------------------


def _load_finance_module():
    name = "finance_api_router"
    if name not in sys.modules:
        path = _roster_root / "finance" / "api" / "router.py"
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return sys.modules[name]


class TestFinanceAPI:
    def _make_app(self, *, fetch_rows=None, fetchrow=None):
        finance_mod = _load_finance_module()
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=fetch_rows or [])
        pool.fetchrow = AsyncMock(return_value=fetchrow)
        pool.fetchval = AsyncMock(return_value=0)
        db = MagicMock(spec=DatabaseManager)
        db.pool.return_value = pool
        app = create_app()
        app.dependency_overrides[finance_mod._get_db_manager] = lambda: db
        return app

    async def test_transactions_returns_paginated_structure(self):
        app = self._make_app()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            resp = await client.get("/api/finance/transactions")
        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
