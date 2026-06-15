"""Tests for per-account GoogleHealthStatusResponse shape (multi-account ADR-1).

Covers:
- test_status_returns_per_account_entries_when_two_accounts_active
- test_status_back_compat_single_account_shape
- _worst_of_state helper
- _fetch_heartbeat_rows_by_email parsing
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.models.google_health import GoogleHealthConnectorState
from butlers.api.routers.google_health import (
    GOOGLE_HEALTH_SCOPE_URLS,
    _fetch_heartbeat_rows_by_email,
    _worst_of_state,
)
from butlers.api.routers.google_health import (
    _get_db_manager as _gh_get_db,
)

pytestmark = pytest.mark.unit

_ALL_HEALTH_SCOPES = sorted(GOOGLE_HEALTH_SCOPE_URLS)
_CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ga_row(
    *,
    email: str,
    is_primary: bool = False,
    granted_scopes: list[str] | None = None,
    last_token_refresh_at=None,
    metadata: dict | None = None,
    acct_id: uuid.UUID | None = None,
) -> dict:
    return {
        "id": acct_id or uuid.uuid4(),
        "entity_id": uuid.uuid4(),
        "email": email,
        "is_primary": is_primary,
        "granted_scopes": granted_scopes
        if granted_scopes is not None
        else list(_ALL_HEALTH_SCOPES),
        "status": "active",
        "last_token_refresh_at": last_token_refresh_at,
        "metadata": metadata or {},
    }


def _make_health_scoped_account(email: str, acct_id: uuid.UUID | None = None):
    """Stub for HealthScopedAccount-like namedtuple."""
    from butlers.google_account_registry import HealthScopedAccount

    return HealthScopedAccount(
        id=acct_id or uuid.uuid4(),
        email=email,
        entity_id=uuid.uuid4(),
        refresh_token_present=True,
    )


def _make_heartbeat_row(
    *, email: str, state: str = "healthy", last_heartbeat_at=None, rate_limit: int | None = None
) -> dict:
    meta = {}
    if rate_limit is not None:
        meta["rate_limit_remaining"] = rate_limit
    return {
        "state": state,
        "last_heartbeat_at": last_heartbeat_at or datetime.now(UTC),
        "uptime_s": 3600,
        "endpoint_identity": f"google_health:user:{email}",
        "metadata": meta,
    }


def _make_shared_pool(
    *,
    primary_row,
    last_ingest_at=None,
    ingest_counts=None,
    ga_rows: list | None = None,
):
    """Build a fake asyncpg shared pool with configurable responses."""
    resolved_counts = ingest_counts or {"sleep_sessions_7d": 0, "daily_summaries_7d": 0}
    conn = AsyncMock()

    async def fake_fetchrow(query, *args):
        if "WHERE is_primary = true" in query:
            return primary_row
        if "FROM public.ingestion_events" in query:
            # Could be per-account or global counts query
            return resolved_counts
        return None

    async def fake_fetch(query, *args):
        if "FROM public.google_accounts" in query and "WHERE status = 'active'" in query:
            return ga_rows or ([primary_row] if primary_row else [])
        return []

    async def fake_fetchval(query, *args):
        if "FROM public.ingestion_events" in query:
            return last_ingest_at
        return None

    conn.fetchrow = AsyncMock(side_effect=fake_fetchrow)
    conn.fetch = AsyncMock(side_effect=fake_fetch)
    conn.fetchval = AsyncMock(side_effect=fake_fetchval)

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool = MagicMock()
    pool.acquire = _acquire
    return pool


def _make_swb_pool(*, heartbeat_rows: list | None = None):
    """Build a fake switchboard pool returning multiple connector_registry rows."""
    swb = AsyncMock()
    # fetchrow returns the first row (for legacy _fetch_heartbeat_row).
    rows = heartbeat_rows or []
    swb.fetchrow = AsyncMock(return_value=rows[0] if rows else None)
    swb.fetch = AsyncMock(return_value=rows)
    return swb


def _make_db(
    *,
    primary_row=None,
    last_ingest_at=None,
    ingest_counts=None,
    heartbeat_rows: list | None = None,
    ga_rows: list | None = None,
    shared_available: bool = True,
):
    shared_pool = _make_shared_pool(
        primary_row=primary_row,
        last_ingest_at=last_ingest_at,
        ingest_counts=ingest_counts,
        ga_rows=ga_rows,
    )
    swb_pool = _make_swb_pool(heartbeat_rows=heartbeat_rows)

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


# ---------------------------------------------------------------------------
# Unit tests for _worst_of_state
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "states,expected",
    [
        ([], GoogleHealthConnectorState.not_configured),
        ([GoogleHealthConnectorState.healthy], GoogleHealthConnectorState.healthy),
        (
            [GoogleHealthConnectorState.healthy, GoogleHealthConnectorState.degraded],
            GoogleHealthConnectorState.degraded,
        ),
        (
            [GoogleHealthConnectorState.degraded, GoogleHealthConnectorState.error],
            GoogleHealthConnectorState.error,
        ),
        (
            [
                GoogleHealthConnectorState.healthy,
                GoogleHealthConnectorState.error,
                GoogleHealthConnectorState.degraded,
            ],
            GoogleHealthConnectorState.error,
        ),
        (
            [GoogleHealthConnectorState.not_configured, GoogleHealthConnectorState.healthy],
            GoogleHealthConnectorState.healthy,
        ),
    ],
)
def test_worst_of_state(states, expected):
    assert _worst_of_state(states) == expected


# ---------------------------------------------------------------------------
# Unit tests for _fetch_heartbeat_rows_by_email
# ---------------------------------------------------------------------------


async def test_fetch_heartbeat_rows_by_email_parses_per_account_rows():
    rows = [
        {
            "state": "healthy",
            "last_heartbeat_at": datetime.now(UTC),
            "uptime_s": 100,
            "endpoint_identity": "google_health:user:alice@example.com",
            "metadata": {},
            "error_message": None,
        },
        {
            "state": "degraded",
            "last_heartbeat_at": datetime.now(UTC),
            "uptime_s": 50,
            "endpoint_identity": "google_health:user:bob@example.com",
            "metadata": {"rate_limit_remaining": 10},
            "error_message": None,
        },
        # Degraded sentinel row — captured separately, not included in per-email dict
        {
            "state": "degraded",
            "last_heartbeat_at": datetime.now(UTC),
            "uptime_s": 0,
            "endpoint_identity": "google_health:degraded",
            "metadata": {},
            "error_message": "api_forbidden",
        },
    ]
    swb = AsyncMock()
    swb.fetch = AsyncMock(return_value=rows)

    per_email, sentinel = await _fetch_heartbeat_rows_by_email(swb)
    assert set(per_email.keys()) == {"alice@example.com", "bob@example.com"}
    assert per_email["alice@example.com"]["state"] == "healthy"
    assert per_email["bob@example.com"]["state"] == "degraded"
    # Sentinel is returned separately with its error_message intact.
    assert sentinel is not None
    assert sentinel["endpoint_identity"] == "google_health:degraded"
    assert sentinel["error_message"] == "api_forbidden"


async def test_fetch_heartbeat_rows_by_email_no_sentinel_when_absent():
    """When no degraded-sentinel row exists, sentinel is None."""
    rows = [
        {
            "state": "healthy",
            "last_heartbeat_at": datetime.now(UTC),
            "uptime_s": 100,
            "endpoint_identity": "google_health:user:alice@example.com",
            "metadata": {},
            "error_message": None,
        },
    ]
    swb = AsyncMock()
    swb.fetch = AsyncMock(return_value=rows)

    per_email, sentinel = await _fetch_heartbeat_rows_by_email(swb)
    assert set(per_email.keys()) == {"alice@example.com"}
    assert sentinel is None


async def test_fetch_heartbeat_rows_by_email_returns_empty_on_none_pool():
    per_email, sentinel = await _fetch_heartbeat_rows_by_email(None)
    assert per_email == {}
    assert sentinel is None


async def test_fetch_heartbeat_rows_by_email_returns_empty_on_exception():
    swb = AsyncMock()
    swb.fetch = AsyncMock(side_effect=RuntimeError("db error"))
    per_email, sentinel = await _fetch_heartbeat_rows_by_email(swb)
    assert per_email == {}
    assert sentinel is None


# ---------------------------------------------------------------------------
# Integration tests via HTTP client
# ---------------------------------------------------------------------------


async def test_status_returns_per_account_entries_when_two_accounts_active():
    """Two health-scoped accounts → response has accounts list with two entries."""
    now = datetime.now(UTC)
    email_a = "alice@example.com"
    email_b = "bob@example.com"

    row_a = _make_ga_row(
        email=email_a, is_primary=True, last_token_refresh_at=now - timedelta(hours=1)
    )
    row_b = _make_ga_row(email=email_b, is_primary=False)

    hb_a = _make_heartbeat_row(
        email=email_a, state="healthy", last_heartbeat_at=now, rate_limit=200
    )
    hb_b = _make_heartbeat_row(
        email=email_b, state="healthy", last_heartbeat_at=now, rate_limit=150
    )

    ha_a = _make_health_scoped_account(email_a, acct_id=row_a["id"])
    ha_b = _make_health_scoped_account(email_b, acct_id=row_b["id"])

    db = _make_db(
        primary_row=row_a,
        heartbeat_rows=[hb_a, hb_b],
        ga_rows=[row_a, row_b],
        ingest_counts={"sleep_sessions_7d": 5, "daily_summaries_7d": 10},
        last_ingest_at=now - timedelta(minutes=10),
    )

    with patch(
        "butlers.api.routers.google_health.list_health_scoped_accounts",
        AsyncMock(return_value=[ha_a, ha_b]),
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=_make_app(db)), base_url="http://test"
        ) as client:
            resp = await client.get("/api/connectors/google-health/status")

    assert resp.status_code == 200
    body = resp.json()

    # Top-level summary fields must be present.
    assert body["state"] == "healthy"
    assert body["connected"] is True
    assert body["primary_account_email"] == email_a

    # Per-account list has two entries.
    assert len(body["accounts"]) == 2
    emails_returned = {a["email"] for a in body["accounts"]}
    assert emails_returned == {email_a, email_b}

    # Each account entry has required fields.
    for acct in body["accounts"]:
        assert "email" in acct
        assert "state" in acct
        assert "scopes_granted" in acct
        assert "sleep_sessions_7d" in acct
        assert "daily_summaries_7d" in acct


async def test_status_worst_of_state_when_one_account_degraded():
    """Worst-of: one healthy + one degraded → top-level state = degraded."""
    now = datetime.now(UTC)
    email_a = "alice@example.com"
    email_b = "bob@example.com"

    row_a = _make_ga_row(email=email_a, is_primary=True)
    # bob only has one scope — will be degraded.
    row_b = _make_ga_row(email=email_b, is_primary=False, granted_scopes=[_ALL_HEALTH_SCOPES[0]])

    hb_a = _make_heartbeat_row(email=email_a, state="healthy", last_heartbeat_at=now)
    hb_b = _make_heartbeat_row(email=email_b, state="degraded", last_heartbeat_at=now)

    ha_a = _make_health_scoped_account(email_a, acct_id=row_a["id"])
    ha_b = _make_health_scoped_account(email_b, acct_id=row_b["id"])

    db = _make_db(
        primary_row=row_a,
        heartbeat_rows=[hb_a, hb_b],
        ga_rows=[row_a, row_b],
    )

    with patch(
        "butlers.api.routers.google_health.list_health_scoped_accounts",
        AsyncMock(return_value=[ha_a, ha_b]),
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=_make_app(db)), base_url="http://test"
        ) as client:
            resp = await client.get("/api/connectors/google-health/status")

    body = resp.json()
    assert body["state"] == "degraded"
    assert body["connected"] is False
    assert len(body["accounts"]) == 2


async def test_status_back_compat_single_account_shape():
    """Single health-scoped account → top-level fields identical to pre-multi-account shape."""
    now = datetime.now(UTC)
    email = "owner@example.com"

    row = _make_ga_row(email=email, is_primary=True, last_token_refresh_at=now - timedelta(hours=2))
    hb = _make_heartbeat_row(email=email, state="healthy", last_heartbeat_at=now, rate_limit=123)
    ha = _make_health_scoped_account(email, acct_id=row["id"])

    db = _make_db(
        primary_row=row,
        heartbeat_rows=[hb],
        ga_rows=[row],
        ingest_counts={"sleep_sessions_7d": 7, "daily_summaries_7d": 21},
        last_ingest_at=now - timedelta(minutes=5),
    )

    with patch(
        "butlers.api.routers.google_health.list_health_scoped_accounts",
        AsyncMock(return_value=[ha]),
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=_make_app(db)), base_url="http://test"
        ) as client:
            resp = await client.get("/api/connectors/google-health/status")

    assert resp.status_code == 200
    body = resp.json()

    # Back-compat: same top-level fields as before.
    assert body["state"] == "healthy"
    assert body["connected"] is True
    assert sorted(body["scopes_granted"]) == _ALL_HEALTH_SCOPES
    assert body["sleep_sessions_7d"] == 7
    assert body["daily_summaries_7d"] == 21
    assert body["rate_limit_remaining"] == 123
    assert body["primary_account_email"] == email

    # New field: exactly one account entry.
    assert len(body["accounts"]) == 1
    acct = body["accounts"][0]
    assert acct["email"] == email
    assert acct["state"] == "healthy"
    assert sorted(acct["scopes_granted"]) == _ALL_HEALTH_SCOPES


async def test_status_no_health_accounts_falls_back_to_not_configured():
    """No health-scoped accounts → not_configured, empty accounts list."""
    db = _make_db(primary_row=None)

    with patch(
        "butlers.api.routers.google_health.list_health_scoped_accounts",
        AsyncMock(return_value=[]),
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=_make_app(db)), base_url="http://test"
        ) as client:
            resp = await client.get("/api/connectors/google-health/status")

    body = resp.json()
    assert body["state"] == "not_configured"
    assert body["connected"] is False
    assert body["accounts"] == []
    assert body["primary_account_email"] is None


async def test_status_rate_limit_remaining_is_minimum_across_accounts():
    """Top-level rate_limit_remaining is the minimum across per-account values."""
    now = datetime.now(UTC)
    email_a = "alice@example.com"
    email_b = "bob@example.com"

    row_a = _make_ga_row(email=email_a, is_primary=True)
    row_b = _make_ga_row(email=email_b, is_primary=False)

    hb_a = _make_heartbeat_row(
        email=email_a, state="healthy", last_heartbeat_at=now, rate_limit=500
    )
    hb_b = _make_heartbeat_row(email=email_b, state="healthy", last_heartbeat_at=now, rate_limit=42)

    ha_a = _make_health_scoped_account(email_a, acct_id=row_a["id"])
    ha_b = _make_health_scoped_account(email_b, acct_id=row_b["id"])

    db = _make_db(
        primary_row=row_a,
        heartbeat_rows=[hb_a, hb_b],
        ga_rows=[row_a, row_b],
    )

    with patch(
        "butlers.api.routers.google_health.list_health_scoped_accounts",
        AsyncMock(return_value=[ha_a, ha_b]),
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=_make_app(db)), base_url="http://test"
        ) as client:
            resp = await client.get("/api/connectors/google-health/status")

    body = resp.json()
    # Most constrained account wins.
    assert body["rate_limit_remaining"] == 42


async def test_status_two_accounts_vs_one_account_shape():
    """Two-account response shape compared against the single-account (back-compat) shape.

    Asserts that the two-account response adds per-account entries while
    preserving all top-level fields present in the single-account response.
    This is a direct shape-comparison test: run the endpoint with 2 accounts,
    then again with only 1, and assert structural invariants hold for both.

    Acceptance test [bu-91zdb.7] §7.4.
    """
    now = datetime.now(UTC)
    email_primary = "primary@example.com"
    email_secondary = "secondary@example.com"

    row_primary = _make_ga_row(
        email=email_primary,
        is_primary=True,
        last_token_refresh_at=now - timedelta(hours=1),
    )
    row_secondary = _make_ga_row(email=email_secondary, is_primary=False)

    hb_primary = _make_heartbeat_row(
        email=email_primary, state="healthy", last_heartbeat_at=now, rate_limit=300
    )
    hb_secondary = _make_heartbeat_row(
        email=email_secondary, state="healthy", last_heartbeat_at=now, rate_limit=100
    )

    ha_primary = _make_health_scoped_account(email_primary, acct_id=row_primary["id"])
    ha_secondary = _make_health_scoped_account(email_secondary, acct_id=row_secondary["id"])

    counts_two = {"sleep_sessions_7d": 8, "daily_summaries_7d": 20}

    # ---- Two-account response ----
    db_two = _make_db(
        primary_row=row_primary,
        heartbeat_rows=[hb_primary, hb_secondary],
        ga_rows=[row_primary, row_secondary],
        ingest_counts=counts_two,
        last_ingest_at=now - timedelta(minutes=3),
    )
    with patch(
        "butlers.api.routers.google_health.list_health_scoped_accounts",
        AsyncMock(return_value=[ha_primary, ha_secondary]),
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=_make_app(db_two)), base_url="http://test"
        ) as client:
            resp_two = await client.get("/api/connectors/google-health/status")

    body_two = resp_two.json()
    assert resp_two.status_code == 200

    # Two-account shape: accounts list has two entries.
    assert len(body_two["accounts"]) == 2
    emails_two = {a["email"] for a in body_two["accounts"]}
    assert emails_two == {email_primary, email_secondary}

    # Each account entry has required per-account fields.
    for acct in body_two["accounts"]:
        assert "email" in acct
        assert "state" in acct
        assert "scopes_granted" in acct
        assert "sleep_sessions_7d" in acct
        assert "daily_summaries_7d" in acct

    # ---- Single-account response ----
    counts_one = {"sleep_sessions_7d": 3, "daily_summaries_7d": 9}
    db_one = _make_db(
        primary_row=row_primary,
        heartbeat_rows=[hb_primary],
        ga_rows=[row_primary],
        ingest_counts=counts_one,
        last_ingest_at=now - timedelta(minutes=5),
    )
    with patch(
        "butlers.api.routers.google_health.list_health_scoped_accounts",
        AsyncMock(return_value=[ha_primary]),
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=_make_app(db_one)), base_url="http://test"
        ) as client:
            resp_one = await client.get("/api/connectors/google-health/status")

    body_one = resp_one.json()
    assert resp_one.status_code == 200

    # Single-account shape: accounts list has exactly one entry.
    assert len(body_one["accounts"]) == 1
    assert body_one["accounts"][0]["email"] == email_primary

    # ---- Shape invariants hold for both ----
    _required_top_level = {
        "state",
        "connected",
        "primary_account_email",
        "accounts",
        "sleep_sessions_7d",
        "daily_summaries_7d",
        "scopes_granted",
    }
    for key in _required_top_level:
        assert key in body_two, f"Missing top-level key {key!r} in two-account response"
        assert key in body_one, f"Missing top-level key {key!r} in one-account response"

    # Both responses have state=healthy (all accounts are healthy).
    assert body_two["state"] == "healthy"
    assert body_one["state"] == "healthy"

    # primary_account_email is consistent.
    assert body_two["primary_account_email"] == email_primary
    assert body_one["primary_account_email"] == email_primary

    # counts are correctly reflected.
    assert body_two["sleep_sessions_7d"] == counts_two["sleep_sessions_7d"]
    assert body_two["daily_summaries_7d"] == counts_two["daily_summaries_7d"]
    assert body_one["sleep_sessions_7d"] == counts_one["sleep_sessions_7d"]
    assert body_one["daily_summaries_7d"] == counts_one["daily_summaries_7d"]


async def test_status_degraded_sentinel_surfaces_error_message_when_no_per_account_row():
    """Regression: /status must surface error_message from the connector-level degraded sentinel
    when no per-account heartbeat row exists yet (e.g. a 403 arrives before accounts are resolved).

    Before the fix, _fetch_heartbeat_rows_by_email silently discarded the degraded-sentinel row
    (endpoint_identity='google_health:degraded'), so heartbeat_by_email.get(email) returned None
    and _extract_error_message(None, state) returned None — disagreeing with /summaries which
    reads connector_registry directly and returns 'api_forbidden'.

    After the fix, the sentinel is returned as a second value and used as a fallback, so both
    endpoints return the same error_message.  This test exercises the /status path.

    Related: bu-scvfk.
    """
    now = datetime.now(UTC)
    email = "owner@example.com"

    # Account has all health scopes — the ONLY degraded signal is the 403.
    row = _make_ga_row(email=email, is_primary=True)
    ha = _make_health_scoped_account(email, acct_id=row["id"])

    # Only the connector-level degraded sentinel exists; no per-account row yet.
    degraded_sentinel = {
        "state": "degraded",
        "last_heartbeat_at": now,
        "uptime_s": 0,
        "endpoint_identity": "google_health:degraded",
        "metadata": {},
        "error_message": "api_forbidden",
    }

    db = _make_db(
        primary_row=row,
        heartbeat_rows=[degraded_sentinel],  # Only sentinel; no per-account row.
        ga_rows=[row],
    )

    with patch(
        "butlers.api.routers.google_health.list_health_scoped_accounts",
        AsyncMock(return_value=[ha]),
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=_make_app(db)), base_url="http://test"
        ) as client:
            resp = await client.get("/api/connectors/google-health/status")

    assert resp.status_code == 200
    body = resp.json()

    # Connector is degraded due to the 403.
    assert body["state"] == "degraded"
    assert body["connected"] is False

    # The error_message must be surfaced from the sentinel — this was the bug.
    assert body["error_message"] == "api_forbidden"

    # Per-account entry must also carry the error_message from the sentinel fallback.
    assert len(body["accounts"]) == 1
    acct = body["accounts"][0]
    assert acct["state"] == "degraded"
    assert acct["error_message"] == "api_forbidden"
