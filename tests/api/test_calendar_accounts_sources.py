"""Tests for the calendar accounts control plane + per-source toggle [bu-6cf3ri].

Covers:
- GET /api/calendar/accounts: connected accounts joined with connector health;
  graceful degrade (health_available=false) when the health surface is down.
- POST /api/calendar/sources: enable/disable toggles sync_enabled on the
  existing calendar_sources row; unknown butler → 404.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock
from uuid import uuid4

import httpx

from butlers.api.db import DatabaseManager
from butlers.api.deps import get_db
from butlers.testing.fake_pool import fake_pool
from butlers.testing.records import Record, row


def _account_row(*, email: str, is_primary: bool = False, status: str = "active") -> Record:
    return Record(
        {
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
    )


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
    app.dependency_overrides[get_db] = lambda: mock_db


def _heartbeat_row(
    *,
    state: str = "healthy",
    last_heartbeat_at: datetime | None,
    error_message: str | None = None,
) -> Record:
    return Record(
        {
            "state": state,
            "last_heartbeat_at": last_heartbeat_at,
            "endpoint_identity": "google_calendar:user:me@example.com",
            "metadata": {},
            "error_message": error_message,
        }
    )


async def test_accounts_returns_accounts_with_health(app):
    shared = fake_pool(fetch=[_account_row(email="me@example.com", is_primary=True)])
    switchboard = fake_pool(
        fetch=[
            _heartbeat_row(
                state="healthy",
                last_heartbeat_at=datetime.now(UTC) - timedelta(seconds=10),
            )
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


async def test_accounts_health_degraded_when_heartbeat_stale_despite_healthy_state(app):
    """bu-27dxl.6.6: a stale heartbeat is offline (degraded) regardless of the
    last state written -- a state='healthy' row that stopped heartbeating
    weeks ago must not keep rendering as healthy forever. Regression guard
    for the pre-fix bug this exact fixture (a month-old heartbeat) triggered.
    """
    shared = fake_pool(fetch=[_account_row(email="me@example.com", is_primary=True)])
    switchboard = fake_pool(
        fetch=[
            _heartbeat_row(
                state="healthy",
                last_heartbeat_at=datetime.now(UTC) - timedelta(days=30),
            )
        ]
    )
    _wire(app, shared_pool=shared, switchboard_pool=switchboard)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/calendar/accounts")

    assert resp.status_code == 200
    acct = resp.json()["data"]["accounts"][0]
    assert acct["health"]["state"] == "degraded"


async def test_accounts_health_error_when_heartbeat_recent(app):
    """error/recent: a genuinely live connector reporting error stays error."""
    shared = fake_pool(fetch=[_account_row(email="me@example.com", is_primary=True)])
    switchboard = fake_pool(
        fetch=[
            _heartbeat_row(
                state="error",
                last_heartbeat_at=datetime.now(UTC) - timedelta(seconds=10),
                error_message="api_forbidden",
            )
        ]
    )
    _wire(app, shared_pool=shared, switchboard_pool=switchboard)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/calendar/accounts")

    acct = resp.json()["data"]["accounts"][0]
    assert acct["health"]["state"] == "error"
    assert acct["health"]["error_message"] == "api_forbidden"


async def test_accounts_health_degraded_when_no_heartbeat_row(app):
    """missing heartbeat: no connector_registry row yet -> unknown, not a
    fabricated health claim (heartbeat=None short-circuits before liveness)."""
    shared = fake_pool(fetch=[_account_row(email="me@example.com", is_primary=True)])
    switchboard = fake_pool(fetch=[])
    _wire(app, shared_pool=shared, switchboard_pool=switchboard)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/calendar/accounts")

    acct = resp.json()["data"]["accounts"][0]
    assert acct["health"]["state"] == "unknown"


async def test_accounts_health_degraded_for_future_dated_heartbeat(app):
    """future-dated heartbeat: clock skew must never read as live/healthy."""
    shared = fake_pool(fetch=[_account_row(email="me@example.com", is_primary=True)])
    switchboard = fake_pool(
        fetch=[
            _heartbeat_row(
                state="healthy",
                last_heartbeat_at=datetime.now(UTC) + timedelta(hours=1),
            )
        ]
    )
    _wire(app, shared_pool=shared, switchboard_pool=switchboard)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/calendar/accounts")

    acct = resp.json()["data"]["accounts"][0]
    assert acct["health"]["state"] == "degraded"


async def test_accounts_health_not_healthy_when_paused(app):
    """paused: explicit operator suppression is never presented as healthy."""
    shared = fake_pool(fetch=[_account_row(email="me@example.com", is_primary=True)])
    switchboard = fake_pool(
        fetch=[
            _heartbeat_row(
                state="paused",
                last_heartbeat_at=datetime.now(UTC) - timedelta(seconds=10),
            )
        ]
    )
    _wire(app, shared_pool=shared, switchboard_pool=switchboard)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/calendar/accounts")

    acct = resp.json()["data"]["accounts"][0]
    assert acct["health"]["state"] != "healthy"


async def test_accounts_degrade_when_health_unavailable(app):
    shared = fake_pool(fetch=[_account_row(email="me@example.com")])
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
    butler_pool = fake_pool(
        fetchrow=row(id=source_id, source_key="provider:google:work", calendar_id="work")
    )
    switchboard = fake_pool(execute=None)  # for audit logging
    _wire(
        app,
        shared_pool=fake_pool(),
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
    _wire(app, shared_pool=fake_pool(), butler_pool=fake_pool(), butler_names=["general"])

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/calendar/sources",
            json={"butler": "nope", "source_key": "x", "enabled": True},
        )

    assert resp.status_code == 404


async def test_toggle_source_missing_row_404(app):
    butler_pool = fake_pool(fetchrow=None)  # UPDATE ... RETURNING found nothing
    _wire(
        app,
        shared_pool=fake_pool(),
        switchboard_pool=fake_pool(execute=None),
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
