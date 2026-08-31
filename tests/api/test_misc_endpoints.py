"""Condensed tests for miscellaneous API endpoints.

Condensed from:
  test_sessions.py (15) + test_notifications.py (15) + test_issues.py (15)
  + test_sse.py (15) + test_connectivity.py (11) + test_middleware.py (11)
  + test_general.py (16) + test_health.py (16) + test_home.py (16)
  + test_finance_api.py (16) + test_ha_credentials_roundtrip.py (6)
  + test_auth.py (10) + test_app.py (9) + test_app_integration.py (8)
  + test_audit.py (14) → ~20 tests (bu-egmz6) → 8 tests (bu-2yw2d)

Keeps: CORS, middleware error codes, sessions 200+404, SSE broadcast, audit
       200, notifications 200, home 503. Health and API-key coverage lives in
       their dedicated smoke and middleware suites.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest

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
# Error middleware
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path,exception,expected",
    [
        (
            "/api/test/unreachable",
            ButlerUnreachableError("atlas", cause=ConnectionRefusedError("conn refused")),
            (502, 503),
        ),
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
        db.fan_out_with_status = AsyncMock(return_value=({}, []))
        app.dependency_overrides[_sessions_get_db] = lambda: db
        return app

    async def test_list_returns_paginated_structure_and_404_for_missing(self, app):
        self._make_app(app)
        sid = uuid4()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            r_list = await client.get("/api/sessions")
            # Detail resolves ONLY via the global cross-butler fan-out now (the
            # butler-scoped detail route was deleted, bu-tpudw.2). fan_out is
            # stubbed empty with no degraded pools -> a genuine 404.
            r_404 = await client.get(f"/api/sessions/{sid}")
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
    db.credential_shared_pool.return_value = pool
    # The read path reads public.audit_log only (bu-j26e8 removed the legacy
    # UNION arm); a spare switchboard-pool stub is kept for any non-read code.
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


async def test_notifications_retried_filter_matches_computed_status_not_raw_column():
    # "retried" is never a stored `status` value -- it's a failed notification
    # superseded by a later sent one (see effective_status CASE). The filter
    # used to build `status = $1` bound to the literal 'retried', which could
    # never match any row. It must instead reuse the same EXISTS-based
    # condition the SELECT uses to compute effective_status (bu-qvnce.2).
    from tests.api.conftest import build_notifications_app

    app, pool, _db = build_notifications_app(rows=[], total=0)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/notifications", params={"status": "retried"})
    assert resp.status_code == 200

    data_sql, *data_args = pool.fetch.call_args.args
    assert "status = 'failed'" in data_sql
    assert "EXISTS" in data_sql
    assert "status = 'retried'" not in data_sql
    assert "retried" not in data_args


async def test_notifications_terminal_failed_filter_excludes_retried_attempts():
    """The dashboard's terminal-failure door uses the stats predicate.

    A retried attempt is still stored as ``status='failed'``, so a raw failed
    filter cannot be the destination for the dashboard's terminal-failure
    count.  The special filter must use the inverse of the same later-send
    predicate used by the stats query.
    """
    from tests.api.conftest import build_notifications_app

    app, pool, _db = build_notifications_app(rows=[], total=0)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/notifications", params={"status": "terminal_failed"})
    assert resp.status_code == 200

    data_sql, *data_args = pool.fetch.call_args.args
    assert "status = 'failed'" in data_sql
    assert "AND NOT (" in data_sql
    assert "EXISTS" in data_sql
    assert "status = 'terminal_failed'" not in data_sql
    assert "terminal_failed" not in data_args


async def test_notifications_status_filter_still_binds_non_retried_values():
    from tests.api.conftest import build_notifications_app

    app, pool, _db = build_notifications_app(rows=[], total=0)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/notifications", params={"status": "sent"})
    assert resp.status_code == 200

    data_sql, *data_args = pool.fetch.call_args.args
    assert "status = $1" in data_sql
    assert "sent" in data_args


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
