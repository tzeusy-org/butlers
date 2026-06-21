"""Tests for the calendar accounts control plane + per-source toggle [bu-6cf3ri].

Covers:
- GET /api/calendar/accounts: connected accounts joined with connector health;
  graceful degrade (health_available=false) when the health surface is down.
- POST /api/calendar/sources: enable/disable toggles sync_enabled on the
  existing calendar_sources row; unknown butler → 404.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx

from butlers.api.db import DatabaseManager
from butlers.api.routers.calendar_workspace import _get_db_manager


class _FakeAcquire:
    def __init__(self, conn) -> None:
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class _FakePool:
    """Pool double supporting both ``acquire()`` and direct fetch/fetchrow."""

    def __init__(self, *, conn_fetch=None, fetch=None, fetchrow=None) -> None:
        self._conn = MagicMock()
        self._conn.fetch = AsyncMock(return_value=conn_fetch or [])
        self._conn.execute = AsyncMock()
        self.fetch = AsyncMock(return_value=fetch or [])
        self.fetchrow = AsyncMock(return_value=fetchrow)
        self.execute = AsyncMock()

    def acquire(self):
        return _FakeAcquire(self._conn)


def _account_row(*, email: str, is_primary: bool = False, status: str = "active") -> dict:
    return {
        "id": uuid4(),
        "entity_id": uuid4(),
        "email": email,
        "display_name": email.split("@")[0].title(),
        "is_primary": is_primary,
        "granted_scopes": ["https://www.googleapis.com/auth/calendar"],
        "status": status,
        "connected_at": datetime(2026, 1, 1, tzinfo=UTC),
        "last_token_refresh_at": None,
    }


def _wire(app, *, shared_pool, switchboard_pool=None, butler_pool=None, butler_names=None) -> None:
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = butler_names or ["general"]
    mock_db.credential_shared_pool = MagicMock(return_value=shared_pool)

    def _pool(name: str):
        if name == "switchboard":
            if switchboard_pool is None:
                raise RuntimeError("switchboard unavailable")
            return switchboard_pool
        return butler_pool

    mock_db.pool = MagicMock(side_effect=_pool)
    app.dependency_overrides[_get_db_manager] = lambda: mock_db


async def test_accounts_returns_accounts_with_health(app):
    shared = _FakePool(conn_fetch=[_account_row(email="me@example.com", is_primary=True)])
    switchboard = _FakePool(
        fetch=[
            {
                "state": "healthy",
                "last_heartbeat_at": datetime(2026, 6, 21, tzinfo=UTC),
                "endpoint_identity": "google_calendar:user:me@example.com",
                "metadata": {},
                "error_message": None,
            }
        ]
    )
    _wire(app, shared_pool=shared, switchboard_pool=switchboard)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/calendar/accounts")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["health_available"] is True
    assert len(data["accounts"]) == 1
    acct = data["accounts"][0]
    assert acct["email"] == "me@example.com"
    assert acct["is_primary"] is True
    assert acct["health"]["state"] == "healthy"


async def test_accounts_degrade_when_health_unavailable(app):
    shared = _FakePool(conn_fetch=[_account_row(email="me@example.com")])
    # switchboard_pool=None → db.pool("switchboard") raises → health unavailable.
    _wire(app, shared_pool=shared, switchboard_pool=None)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/calendar/accounts")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["health_available"] is False
    assert len(data["accounts"]) == 1
    assert data["accounts"][0]["health"]["state"] == "unknown"


async def test_toggle_source_disables_and_returns_state(app):
    source_id = uuid4()
    butler_pool = _FakePool(
        fetchrow={"id": source_id, "source_key": "provider:google:work", "calendar_id": "work"}
    )
    switchboard = _FakePool()  # for audit logging
    _wire(
        app,
        shared_pool=_FakePool(),
        switchboard_pool=switchboard,
        butler_pool=butler_pool,
        butler_names=["general"],
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/calendar/sources",
            json={"butler": "general", "source_key": "provider:google:work", "enabled": False},
        )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["enabled"] is False
    assert data["source_key"] == "provider:google:work"
    assert data["calendar_id"] == "work"
    # The UPDATE was issued with the enabled flag + source_key (positional
    # args after the SQL string).
    args = butler_pool.fetchrow.await_args.args
    assert args[1] is False
    assert args[2] == "provider:google:work"


async def test_toggle_source_unknown_butler_404(app):
    _wire(app, shared_pool=_FakePool(), butler_pool=_FakePool(), butler_names=["general"])

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/calendar/sources",
            json={"butler": "nope", "source_key": "x", "enabled": True},
        )

    assert resp.status_code == 404


async def test_toggle_source_missing_row_404(app):
    butler_pool = _FakePool(fetchrow=None)  # UPDATE ... RETURNING found nothing
    _wire(
        app,
        shared_pool=_FakePool(),
        switchboard_pool=_FakePool(),
        butler_pool=butler_pool,
        butler_names=["general"],
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/calendar/sources",
            json={"butler": "general", "source_key": "ghost", "enabled": True},
        )

    assert resp.status_code == 404
