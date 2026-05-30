"""Tests for the Google Health dashboard API endpoints.

Condensed: 32 → ~14 tests [bu-gg4y1].
Keeps: _derive_state state machine (parametrized), happy/degraded/unconfigured status
response, disconnect scope-selective removal, full-account disconnect union regression.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.models.google_health import GoogleHealthConnectorState
from butlers.api.routers.google_health import (
    GOOGLE_HEALTH_SCOPE_URLS,
    _derive_state,
    _extract_rate_limit_remaining,
    _fetch_ingest_counts,
    _filter_health_scopes,
    _parse_jsonb_metadata,
)
from butlers.api.routers.google_health import (
    _get_db_manager as _gh_get_db,
)

pytestmark = pytest.mark.unit

_ALL_HEALTH_SCOPES = sorted(GOOGLE_HEALTH_SCOPE_URLS)
_CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar"
_DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_shared_pool(
    *, primary_row, last_ingest_at=None, ingest_counts=None, captured_updates=None
):
    resolved_counts = ingest_counts or {"sleep_sessions_7d": 0, "daily_summaries_7d": 0}
    conn = AsyncMock()

    async def fake_fetchrow(query, *args):
        if "FROM public.google_accounts" in query:
            return primary_row
        if "FROM public.ingestion_events" in query:
            return resolved_counts
        return None

    async def fake_fetchval(query, *args):
        if "FROM public.ingestion_events" in query:
            return last_ingest_at
        return None

    async def fake_execute(query, *args):
        if captured_updates is not None and "UPDATE public.google_accounts" in query:
            captured_updates.append(args)
        return None

    conn.fetchrow = AsyncMock(side_effect=fake_fetchrow)
    conn.fetchval = AsyncMock(side_effect=fake_fetchval)
    conn.execute = AsyncMock(side_effect=fake_execute)

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool = MagicMock()
    pool.acquire = _acquire
    return pool


def _make_db(
    *,
    primary_row=None,
    last_ingest_at=None,
    ingest_counts=None,
    heartbeat_row=None,
    captured_updates=None,
    shared_available=True,
):
    shared_pool = _make_shared_pool(
        primary_row=primary_row,
        last_ingest_at=last_ingest_at,
        ingest_counts=ingest_counts,
        captured_updates=captured_updates,
    )
    swb_pool = AsyncMock()
    swb_pool.fetchrow = AsyncMock(return_value=heartbeat_row)

    db = MagicMock(spec=DatabaseManager)
    if shared_available:
        db.credential_shared_pool.return_value = shared_pool
    else:
        db.credential_shared_pool.side_effect = KeyError("no shared pool")
        db.butler_names = []

    db.pool.side_effect = lambda name: (
        swb_pool if name == "switchboard" else (_ for _ in ()).throw(KeyError(name))
    )
    return db


def _make_app(db):
    app = create_app(api_key="")
    app.dependency_overrides[_gh_get_db] = lambda: db
    return app


def _primary_row(*, granted_scopes=None, metadata=None, last_token_refresh_at=None):
    return {
        "id": uuid.uuid4(),
        "entity_id": uuid.uuid4(),
        "email": "owner@example.com",
        "granted_scopes": granted_scopes or list(_ALL_HEALTH_SCOPES),
        "status": "active",
        "last_token_refresh_at": last_token_refresh_at,
        "metadata": metadata or {},
    }


# ---------------------------------------------------------------------------
# Pure helper unit tests (parametrized)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "val,expected",
    [
        ({"a": 1}, {"a": 1}),
        ('{"google_health_test_mode": true}', {"google_health_test_mode": True}),
        (None, {}),
        ("not json", {}),
    ],
)
def test_parse_jsonb_metadata(val, expected):
    assert _parse_jsonb_metadata(val) == expected


@pytest.mark.parametrize(
    "row,expected",
    [
        (None, None),
        ({}, None),
        ({"metadata": {}}, None),
        ({"metadata": {"rate_limit_remaining": 7}}, 7),
        ({"metadata": '{"rate_limit_remaining": 42}'}, 42),
    ],
)
def test_extract_rate_limit_remaining(row, expected):
    assert _extract_rate_limit_remaining(row) == expected


@pytest.mark.parametrize(
    "account,granted,heartbeat,exp_state,exp_connected",
    [
        (None, [], None, GoogleHealthConnectorState.not_configured, False),
        (
            {"id": uuid.uuid4()},
            _ALL_HEALTH_SCOPES[:1],
            {"state": "healthy", "last_heartbeat_at": "NOW"},
            GoogleHealthConnectorState.degraded,
            False,
        ),
        (
            {"id": uuid.uuid4()},
            _ALL_HEALTH_SCOPES,
            {"state": "error"},
            GoogleHealthConnectorState.error,
            False,
        ),
        (
            {"id": uuid.uuid4()},
            _ALL_HEALTH_SCOPES,
            None,
            GoogleHealthConnectorState.degraded,
            False,
        ),
        (
            {"id": uuid.uuid4()},
            _ALL_HEALTH_SCOPES,
            {"state": "healthy", "last_heartbeat_at": "NOW"},
            GoogleHealthConnectorState.healthy,
            True,
        ),
    ],
)
def test_derive_state(account, granted, heartbeat, exp_state, exp_connected):
    # Resolve "NOW" sentinel at test-call time to avoid parametrize-eval staleness.
    # parametrize runs at module import; a stale timestamp causes false degraded→healthy
    # mismatches when CI takes more than _LIVENESS_THRESHOLD_SECONDS to reach this test.
    if heartbeat and heartbeat.get("last_heartbeat_at") == "NOW":
        heartbeat = {**heartbeat, "last_heartbeat_at": datetime.now(UTC)}
    state, connected = _derive_state(
        account=account, granted_health_scopes=granted, heartbeat=heartbeat
    )
    assert state is exp_state
    assert connected is exp_connected


def test_filter_health_scopes_preserves_and_drops_non_health():
    granted = ["openid", _ALL_HEALTH_SCOPES[0], _CALENDAR_SCOPE]
    result = _filter_health_scopes(granted)
    assert result == [_ALL_HEALTH_SCOPES[0]]
    assert _filter_health_scopes([]) == []
    assert _filter_health_scopes(None) == []


# ---------------------------------------------------------------------------
# _fetch_ingest_counts: zero-fallback contract
# ---------------------------------------------------------------------------


async def test_fetch_ingest_counts_zero_on_none_pool_or_row():
    assert await _fetch_ingest_counts(None) == {"sleep_sessions_7d": 0, "daily_summaries_7d": 0}
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=None)

    @asynccontextmanager
    async def _acq():
        yield conn

    pool = MagicMock()
    pool.acquire = _acq
    assert await _fetch_ingest_counts(pool) == {"sleep_sessions_7d": 0, "daily_summaries_7d": 0}


async def test_fetch_ingest_counts_uses_wellness_filter():
    """SQL must filter by source_channel = 'wellness' to match _fetch_last_ingest_at."""
    queries: list[str] = []
    conn = AsyncMock()

    async def cap(query, *args):
        queries.append(query)
        return {"sleep_sessions_7d": 0, "daily_summaries_7d": 0}

    conn.fetchrow = AsyncMock(side_effect=cap)

    @asynccontextmanager
    async def _acq():
        yield conn

    pool = MagicMock()
    pool.acquire = _acq
    await _fetch_ingest_counts(pool)
    assert queries and "source_channel = 'wellness'" in queries[0]


# ---------------------------------------------------------------------------
# GET /api/connectors/google-health/status
# ---------------------------------------------------------------------------


async def test_status_not_configured():
    db = _make_db(primary_row=None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_make_app(db)), base_url="http://test"
    ) as client:
        resp = await client.get("/api/connectors/google-health/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "not_configured"
    assert body["connected"] is False
    assert body["sleep_sessions_7d"] == 0


async def test_status_healthy_full_scopes_and_counts():
    now = datetime.now(UTC)
    db = _make_db(
        primary_row=_primary_row(
            granted_scopes=[_CALENDAR_SCOPE, *_ALL_HEALTH_SCOPES],
            metadata={"google_health_test_mode": True},
        ),
        heartbeat_row={
            "state": "healthy",
            "last_heartbeat_at": now,
            "metadata": {"rate_limit_remaining": 123},
        },
        last_ingest_at=now - timedelta(minutes=3),
        ingest_counts={"sleep_sessions_7d": 7, "daily_summaries_7d": 21},
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_make_app(db)), base_url="http://test"
    ) as client:
        resp = await client.get("/api/connectors/google-health/status")
    body = resp.json()
    assert body["state"] == "healthy"
    assert body["connected"] is True
    assert sorted(body["scopes_granted"]) == _ALL_HEALTH_SCOPES
    assert body["test_mode"] is True
    assert body["rate_limit_remaining"] == 123
    assert body["sleep_sessions_7d"] == 7
    assert body["daily_summaries_7d"] == 21


async def test_status_degraded_partial_scopes():
    db = _make_db(
        primary_row=_primary_row(granted_scopes=[_CALENDAR_SCOPE, _ALL_HEALTH_SCOPES[0]]),
        heartbeat_row={"state": "healthy", "last_heartbeat_at": datetime.now(UTC)},
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_make_app(db)), base_url="http://test"
    ) as client:
        resp = await client.get("/api/connectors/google-health/status")
    body = resp.json()
    assert body["state"] == "degraded"
    assert body["scopes_granted"] == [_ALL_HEALTH_SCOPES[0]]


async def test_status_db_unavailable_returns_not_configured():
    db = _make_db(primary_row=None, shared_available=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_make_app(db)), base_url="http://test"
    ) as client:
        resp = await client.get("/api/connectors/google-health/status")
    assert resp.status_code == 200
    assert resp.json()["state"] == "not_configured"


# ---------------------------------------------------------------------------
# DELETE /api/connectors/google-health/disconnect
# ---------------------------------------------------------------------------


async def test_disconnect_strips_health_preserves_calendar_drive():
    captured: list = []
    acct_id = uuid.uuid4()
    db = _make_db(
        primary_row={
            "id": acct_id,
            "entity_id": uuid.uuid4(),
            "email": "owner@example.com",
            "granted_scopes": [_CALENDAR_SCOPE, _DRIVE_SCOPE, *_ALL_HEALTH_SCOPES],
            "status": "active",
            "last_token_refresh_at": None,
            "metadata": {},
        },
        captured_updates=captured,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_make_app(db)), base_url="http://test"
    ) as client:
        resp = await client.delete("/api/connectors/google-health/disconnect")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert sorted(body["scopes_removed"]) == _ALL_HEALTH_SCOPES
    remaining, captured_id = captured[0]
    assert captured_id == acct_id
    assert sorted(remaining) == sorted([_CALENDAR_SCOPE, _DRIVE_SCOPE])


async def test_disconnect_no_account_is_noop():
    db = _make_db(primary_row=None, captured_updates=[])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_make_app(db)), base_url="http://test"
    ) as client:
        resp = await client.delete("/api/connectors/google-health/disconnect")
    assert resp.status_code == 200
    assert resp.json()["scopes_removed"] == []


# ---------------------------------------------------------------------------
# Regression: full-account disconnect still removes Google Health scopes.
# ---------------------------------------------------------------------------


async def test_full_disconnect_revokes_health_as_union(monkeypatch):
    """disconnect_account union-revokes all scopes including Google Health."""
    from butlers import google_account_registry as gar

    acct_id = uuid.uuid4()
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(
        return_value={
            "id": acct_id,
            "entity_id": uuid.uuid4(),
            "is_primary": True,
            "status": "active",
        }
    )
    conn.fetchval = AsyncMock(return_value=None)
    conn.execute = AsyncMock(return_value=None)

    @asynccontextmanager
    async def _acq():
        yield conn

    @asynccontextmanager
    async def _txn():
        yield

    conn.transaction = MagicMock(side_effect=_txn)
    pool = MagicMock()
    pool.acquire = _acq

    monkeypatch.setattr(gar, "_get_refresh_token", AsyncMock(return_value="tok"))
    monkeypatch.setattr(gar, "_revoke_token_with_google", AsyncMock(return_value=None))
    monkeypatch.setattr(gar, "_get_oldest_active_account_id", AsyncMock(return_value=None))

    await gar.disconnect_account(pool, acct_id, hard_delete=False)

    executed = [call.args[0] for call in conn.execute.await_args_list]
    assert any("DELETE FROM public.entity_info" in q for q in executed)
    assert any("UPDATE public.google_accounts" in q and "'revoked'" in q for q in executed)
