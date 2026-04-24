"""Tests for the Google Health dashboard API endpoints.

Covers:
  GET    /api/connectors/google-health/status
  DELETE /api/connectors/google-health/disconnect

Spec: openspec/changes/google-health-connector/specs/dashboard-google-accounts/spec.md
      openspec/changes/google-health-connector/specs/google-multi-account-oauth/spec.md

The full-account-disconnect regression test (Scenario:
"Full account disconnect preserves semantics") lives here too so both
disconnect paths share a single authoritative test file.
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
# Shared fixtures: build an AsyncMock pool that returns scripted rows.
# ---------------------------------------------------------------------------


def _make_shared_pool(
    *,
    primary_row: dict | None,
    last_ingest_at: datetime | None = None,
    ingest_counts: dict | None = None,
    captured_updates: list | None = None,
):
    """Return a mock shared pool that responds with ``primary_row`` etc.

    ``ingest_counts`` (if provided) is returned for the ``_fetch_ingest_counts``
    fetchrow call; defaults to ``{sleep_sessions_7d: 0, daily_summaries_7d: 0}``.
    ``captured_updates`` (if provided) collects the args of the UPDATE call
    so tests can assert what was written back.
    """
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


def _make_switchboard_pool(heartbeat_row: dict | None):
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=heartbeat_row)
    return pool


def _make_db(
    *,
    primary_row: dict | None = None,
    last_ingest_at: datetime | None = None,
    ingest_counts: dict | None = None,
    heartbeat_row: dict | None = None,
    captured_updates: list | None = None,
    shared_available: bool = True,
):
    shared_pool = _make_shared_pool(
        primary_row=primary_row,
        last_ingest_at=last_ingest_at,
        ingest_counts=ingest_counts,
        captured_updates=captured_updates,
    )
    switchboard_pool = _make_switchboard_pool(heartbeat_row)

    db = MagicMock(spec=DatabaseManager)
    if shared_available:
        db.credential_shared_pool.return_value = shared_pool
    else:
        db.credential_shared_pool.side_effect = KeyError("no shared pool")
        db.butler_names = []

    def _pool(name):
        if name == "switchboard":
            return switchboard_pool
        raise KeyError(name)

    db.pool.side_effect = _pool
    return db


def _make_app(db):
    app = create_app(api_key="")
    app.dependency_overrides[_gh_get_db] = lambda: db
    return app


# ---------------------------------------------------------------------------
# Unit tests: pure helpers
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_filter_health_scopes_preserves_order_and_drops_others(self):
        granted = [
            "openid",
            _ALL_HEALTH_SCOPES[2],
            _CALENDAR_SCOPE,
            _ALL_HEALTH_SCOPES[0],
        ]
        assert _filter_health_scopes(granted) == [
            _ALL_HEALTH_SCOPES[2],
            _ALL_HEALTH_SCOPES[0],
        ]

    def test_filter_health_scopes_empty(self):
        assert _filter_health_scopes([]) == []
        assert _filter_health_scopes(None) == []

    def test_parse_jsonb_metadata_dict_passthrough(self):
        assert _parse_jsonb_metadata({"a": 1}) == {"a": 1}

    def test_parse_jsonb_metadata_json_string(self):
        assert _parse_jsonb_metadata('{"google_health_test_mode": true}') == {
            "google_health_test_mode": True
        }

    def test_parse_jsonb_metadata_none_or_invalid(self):
        assert _parse_jsonb_metadata(None) == {}
        assert _parse_jsonb_metadata("not json") == {}
        assert _parse_jsonb_metadata(42) == {}

    def test_extract_rate_limit_remaining_missing_returns_none(self):
        assert _extract_rate_limit_remaining(None) is None
        assert _extract_rate_limit_remaining({}) is None
        assert _extract_rate_limit_remaining({"metadata": {}}) is None

    def test_extract_rate_limit_remaining_reads_metadata_int(self):
        assert _extract_rate_limit_remaining({"metadata": {"rate_limit_remaining": 7}}) == 7

    def test_extract_rate_limit_remaining_parses_json_string(self):
        raw = {"metadata": '{"rate_limit_remaining": 42}'}
        assert _extract_rate_limit_remaining(raw) == 42

    def test_derive_state_no_account_is_not_configured(self):
        state, connected = _derive_state(account=None, granted_health_scopes=[], heartbeat=None)
        assert state is GoogleHealthConnectorState.not_configured
        assert connected is False

    def test_derive_state_missing_scopes_is_degraded(self):
        account = {"id": uuid.uuid4()}
        state, connected = _derive_state(
            account=account,
            granted_health_scopes=_ALL_HEALTH_SCOPES[:1],
            heartbeat={"state": "healthy", "last_heartbeat_at": datetime.now(UTC)},
        )
        assert state is GoogleHealthConnectorState.degraded
        assert connected is False

    def test_derive_state_error_heartbeat_short_circuits(self):
        # Even with all scopes granted, an error heartbeat forces error state.
        state, connected = _derive_state(
            account={"id": uuid.uuid4()},
            granted_health_scopes=_ALL_HEALTH_SCOPES,
            heartbeat={"state": "error"},
        )
        assert state is GoogleHealthConnectorState.error
        assert connected is False

    def test_derive_state_stale_heartbeat_is_degraded(self):
        stale = datetime.now(UTC) - timedelta(seconds=900)
        state, connected = _derive_state(
            account={"id": uuid.uuid4()},
            granted_health_scopes=_ALL_HEALTH_SCOPES,
            heartbeat={"state": "healthy", "last_heartbeat_at": stale},
        )
        assert state is GoogleHealthConnectorState.degraded
        assert connected is False

    def test_derive_state_healthy_path(self):
        state, connected = _derive_state(
            account={"id": uuid.uuid4()},
            granted_health_scopes=_ALL_HEALTH_SCOPES,
            heartbeat={"state": "healthy", "last_heartbeat_at": datetime.now(UTC)},
        )
        assert state is GoogleHealthConnectorState.healthy
        assert connected is True

    def test_derive_state_missing_heartbeat_is_degraded(self):
        state, connected = _derive_state(
            account={"id": uuid.uuid4()},
            granted_health_scopes=_ALL_HEALTH_SCOPES,
            heartbeat=None,
        )
        assert state is GoogleHealthConnectorState.degraded
        assert connected is False


# ---------------------------------------------------------------------------
# Unit tests: _fetch_ingest_counts
# ---------------------------------------------------------------------------


class TestFetchIngestCounts:
    async def test_returns_zeros_when_pool_is_none(self):
        result = await _fetch_ingest_counts(None)
        assert result == {"sleep_sessions_7d": 0, "daily_summaries_7d": 0}

    async def test_returns_zeros_when_row_is_none(self):
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value=None)

        @asynccontextmanager
        async def _acquire():
            yield conn

        pool = MagicMock()
        pool.acquire = _acquire

        result = await _fetch_ingest_counts(pool)
        assert result == {"sleep_sessions_7d": 0, "daily_summaries_7d": 0}

    async def test_returns_zeros_on_db_error(self):
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(side_effect=Exception("db error"))

        @asynccontextmanager
        async def _acquire():
            yield conn

        pool = MagicMock()
        pool.acquire = _acquire

        result = await _fetch_ingest_counts(pool)
        assert result == {"sleep_sessions_7d": 0, "daily_summaries_7d": 0}

    async def test_separates_sleep_from_daily_counts(self):
        # The SQL uses FILTER clauses to count each type. Mock the row
        # that the query would return with a realistic mix.
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value={"sleep_sessions_7d": 5, "daily_summaries_7d": 14})

        @asynccontextmanager
        async def _acquire():
            yield conn

        pool = MagicMock()
        pool.acquire = _acquire

        result = await _fetch_ingest_counts(pool)
        assert result == {"sleep_sessions_7d": 5, "daily_summaries_7d": 14}

    async def test_passes_connector_type_as_query_param(self):
        """Verify the query is parameterised with the connector type constant."""
        captured_args: list = []
        conn = AsyncMock()

        async def capture_fetchrow(query, *args):
            captured_args.extend(args)
            return {"sleep_sessions_7d": 0, "daily_summaries_7d": 0}

        conn.fetchrow = AsyncMock(side_effect=capture_fetchrow)

        @asynccontextmanager
        async def _acquire():
            yield conn

        pool = MagicMock()
        pool.acquire = _acquire

        await _fetch_ingest_counts(pool)
        assert captured_args == ["google_health"]

    async def test_query_includes_source_channel_filter(self):
        """Verify the SQL filters by source_channel = 'wellness', matching _fetch_last_ingest_at."""
        captured_queries: list[str] = []
        conn = AsyncMock()

        async def capture_fetchrow(query, *args):
            captured_queries.append(query)
            return {"sleep_sessions_7d": 0, "daily_summaries_7d": 0}

        conn.fetchrow = AsyncMock(side_effect=capture_fetchrow)

        @asynccontextmanager
        async def _acquire():
            yield conn

        pool = MagicMock()
        pool.acquire = _acquire

        await _fetch_ingest_counts(pool)
        assert captured_queries, "fetchrow was not called"
        assert "source_channel = 'wellness'" in captured_queries[0], (
            "_fetch_ingest_counts must filter by source_channel = 'wellness' "
            "to stay aligned with _fetch_last_ingest_at"
        )


# ---------------------------------------------------------------------------
# GET /status — integration-ish via ASGITransport
# ---------------------------------------------------------------------------


class TestGetStatus:
    async def test_returns_not_configured_when_no_primary_account(self):
        db = _make_db(primary_row=None)
        app = _make_app(db)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/connectors/google-health/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["state"] == "not_configured"
        assert body["connected"] is False
        assert body["scopes_granted"] == []
        assert body["test_mode"] is False
        assert body["last_ingest_at"] is None
        assert body["rate_limit_remaining"] is None

    async def test_returns_degraded_when_scopes_partial(self):
        db = _make_db(
            primary_row={
                "id": uuid.uuid4(),
                "entity_id": uuid.uuid4(),
                "email": "owner@example.com",
                "granted_scopes": [_CALENDAR_SCOPE, _ALL_HEALTH_SCOPES[0]],
                "status": "active",
                "last_token_refresh_at": None,
                "metadata": {},
            },
            heartbeat_row={"state": "healthy", "last_heartbeat_at": datetime.now(UTC)},
        )
        app = _make_app(db)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/connectors/google-health/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["state"] == "degraded"
        assert body["connected"] is False
        # Only the health scope is surfaced — Calendar is stripped from this field.
        assert body["scopes_granted"] == [_ALL_HEALTH_SCOPES[0]]

    async def test_returns_healthy_with_all_scopes_and_fresh_heartbeat(self):
        now = datetime.now(UTC)
        db = _make_db(
            primary_row={
                "id": uuid.uuid4(),
                "entity_id": uuid.uuid4(),
                "email": "owner@example.com",
                "granted_scopes": [_CALENDAR_SCOPE, *_ALL_HEALTH_SCOPES],
                "status": "active",
                "last_token_refresh_at": now - timedelta(hours=1),
                "metadata": {"google_health_test_mode": True},
            },
            heartbeat_row={
                "state": "healthy",
                "last_heartbeat_at": now,
                "uptime_s": 3600,
                "metadata": {"rate_limit_remaining": 123},
            },
            last_ingest_at=now - timedelta(minutes=3),
        )
        app = _make_app(db)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/connectors/google-health/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["state"] == "healthy"
        assert body["connected"] is True
        assert sorted(body["scopes_granted"]) == _ALL_HEALTH_SCOPES
        assert body["test_mode"] is True
        assert body["rate_limit_remaining"] == 123
        assert body["last_ingest_at"] is not None
        assert body["last_token_refresh_at"] is not None
        # Count fields are always present in the response shape.
        assert "sleep_sessions_7d" in body
        assert "daily_summaries_7d" in body

    async def test_response_includes_ingest_counts(self):
        now = datetime.now(UTC)
        db = _make_db(
            primary_row={
                "id": uuid.uuid4(),
                "entity_id": uuid.uuid4(),
                "email": "owner@example.com",
                "granted_scopes": list(_ALL_HEALTH_SCOPES),
                "status": "active",
                "last_token_refresh_at": None,
                "metadata": {},
            },
            heartbeat_row={"state": "healthy", "last_heartbeat_at": now},
            ingest_counts={"sleep_sessions_7d": 7, "daily_summaries_7d": 21},
        )
        app = _make_app(db)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/connectors/google-health/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["sleep_sessions_7d"] == 7
        assert body["daily_summaries_7d"] == 21

    async def test_response_counts_default_to_zero_when_none_ingested(self):
        db = _make_db(primary_row=None)
        app = _make_app(db)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/connectors/google-health/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["sleep_sessions_7d"] == 0
        assert body["daily_summaries_7d"] == 0

    async def test_rate_limit_remaining_is_null_when_header_never_observed(self):
        # metadata has no rate_limit_remaining key → field MUST be null, not 0.
        now = datetime.now(UTC)
        db = _make_db(
            primary_row={
                "id": uuid.uuid4(),
                "entity_id": uuid.uuid4(),
                "email": "owner@example.com",
                "granted_scopes": list(_ALL_HEALTH_SCOPES),
                "status": "active",
                "last_token_refresh_at": None,
                "metadata": {},
            },
            heartbeat_row={
                "state": "healthy",
                "last_heartbeat_at": now,
                "metadata": {},
            },
        )
        app = _make_app(db)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/connectors/google-health/status")
        body = resp.json()
        assert body["rate_limit_remaining"] is None

    async def test_returns_200_with_degraded_when_db_unavailable(self):
        # DB unavailable → endpoint still returns 200 with not_configured
        # so the dashboard status card can degrade gracefully.
        db = _make_db(primary_row=None, shared_available=False)
        app = _make_app(db)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/connectors/google-health/status")
        assert resp.status_code == 200
        assert resp.json()["state"] == "not_configured"


# ---------------------------------------------------------------------------
# DELETE /disconnect
# ---------------------------------------------------------------------------


class TestDisconnect:
    async def test_strips_only_health_scopes_and_preserves_calendar_drive(self):
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
        app = _make_app(db)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.delete("/api/connectors/google-health/disconnect")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert sorted(body["scopes_removed"]) == _ALL_HEALTH_SCOPES

        # The UPDATE captured the remaining scopes — Calendar & Drive only.
        assert len(captured) == 1
        remaining, captured_id = captured[0]
        assert captured_id == acct_id
        assert sorted(remaining) == sorted([_CALENDAR_SCOPE, _DRIVE_SCOPE])

    async def test_idempotent_when_no_health_scopes_present(self):
        captured: list = []
        db = _make_db(
            primary_row={
                "id": uuid.uuid4(),
                "entity_id": uuid.uuid4(),
                "email": "owner@example.com",
                "granted_scopes": [_CALENDAR_SCOPE],
                "status": "active",
                "last_token_refresh_at": None,
                "metadata": {},
            },
            captured_updates=captured,
        )
        app = _make_app(db)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.delete("/api/connectors/google-health/disconnect")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["scopes_removed"] == []
        assert captured == []

    async def test_no_primary_account_returns_success_no_op(self):
        captured: list = []
        db = _make_db(primary_row=None, captured_updates=captured)
        app = _make_app(db)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.delete("/api/connectors/google-health/disconnect")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["scopes_removed"] == []
        assert captured == []

    async def test_db_unavailable_returns_success_no_op(self):
        db = _make_db(primary_row=None, shared_available=False)
        app = _make_app(db)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.delete("/api/connectors/google-health/disconnect")
        assert resp.status_code == 200
        assert resp.json()["scopes_removed"] == []


# ---------------------------------------------------------------------------
# Regression: full-account disconnect still removes Google Health scopes.
#
# The pre-existing DELETE /api/oauth/google/accounts/<id> revokes everything
# on the companion entity — Calendar, Drive, Gmail, and Google Health. This
# test anchors that behaviour so the scope-selective endpoint above stays
# strictly additive.
# ---------------------------------------------------------------------------


class TestFullAccountDisconnectUnionRevocation:
    async def test_full_disconnect_revokes_health_as_union(self, monkeypatch):
        """Verify the union-revocation semantics via google_account_registry.disconnect_account.

        The registry function revokes the refresh token and marks the row
        revoked; we confirm it touches the health-scoped entity wholesale.
        """
        from butlers import google_account_registry as gar  # noqa: PLC0415

        acct_id = uuid.uuid4()
        entity_id = uuid.uuid4()

        conn = AsyncMock()
        conn.fetchrow = AsyncMock(
            return_value={
                "id": acct_id,
                "entity_id": entity_id,
                "is_primary": True,
                "status": "active",
            }
        )
        conn.fetchval = AsyncMock(return_value=None)
        conn.execute = AsyncMock(return_value=None)

        @asynccontextmanager
        async def _acquire():
            yield conn

        @asynccontextmanager
        async def _txn():
            yield

        conn.transaction = MagicMock(side_effect=_txn)

        pool = MagicMock()
        pool.acquire = _acquire

        monkeypatch.setattr(gar, "_get_refresh_token", AsyncMock(return_value="tok"))
        monkeypatch.setattr(gar, "_revoke_token_with_google", AsyncMock(return_value=None))
        monkeypatch.setattr(gar, "_get_oldest_active_account_id", AsyncMock(return_value=None))

        await gar.disconnect_account(pool, acct_id, hard_delete=False)

        # The registry called DELETE FROM entity_info (removing the refresh
        # token which covers ALL scopes) and UPDATE ... SET status='revoked'.
        # These together constitute union revocation — Google Health scopes
        # go with everything else.
        executed_queries = [call.args[0] for call in conn.execute.await_args_list]
        assert any("DELETE FROM public.entity_info" in q for q in executed_queries)
        assert any(
            "UPDATE public.google_accounts" in q and "'revoked'" in q for q in executed_queries
        )
