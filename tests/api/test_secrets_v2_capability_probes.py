"""Tests for capability-level probes on user credentials (bu-4v5es).

Green on the secrets passport used to mean "refresh token mints an access
token + userinfo returns 200" — zero real scopes exercised, which is why
Google Health could 403 while the credential showed healthy. This module
covers the fix: Google is probed with one cheap authenticated call PER
capability family (calendar, gmail, drive, health); every other provider
keeps its existing single live-verify call wrapped as one 'connectivity'
capability.

Test matrix
-----------
- All four Google capabilities ok → credential ok; four capability-qualified
  secret_probe_log rows persisted alongside the aggregate row.
- One capability (health) 403s while the others succeed → credential rolls
  up to failing, naming 'health', message says "restricted-scope"; only the
  failing capability's row (and the ok ones) get their own persisted rows.
- Token exchange failure is credential-wide: every capability shares the same
  failure — verified both with and without a flagged 7-day test-mode account
  (distinct message wording).
- Non-Google providers (GitHub PAT here) still get a 'connectivity'
  capability-qualified secret_probe_log row alongside the aggregate row.
- `_capability_for_scopes` classifies catalogue rows into calendar/gmail/
  drive/health for Google, None for scope-less Google rows, 'connectivity'
  for every other provider.
- `_fetch_capability_probe_logs_bulk` groups rows by base key and splits the
  capability suffix correctly.

Spec anchor
-----------
bu-4v5es
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.routers.secrets_v2 import (
    _capability_for_scopes,
    _fetch_capability_probe_logs_bulk,
    _get_db_manager,
)

pytestmark = pytest.mark.unit

_NOW = datetime.now(tz=UTC)
_REFRESH_TOKEN = "fake-refresh-token-xyz"
_ACCESS_TOKEN = "fake-access-token-abc"
_CLIENT_ID = "fake-client-id"
_CLIENT_SECRET = "fake-client-secret"

_GOOGLE_URL_MARKERS = {
    "calendar": "calendar",
    "gmail": "gmail",
    "drive": "drive",
    "health": "fitness",
}


def _make_row(**kwargs) -> MagicMock:
    m = MagicMock()
    m.__getitem__ = MagicMock(side_effect=lambda k: kwargs[k])
    return m


def _make_entity_info_row(
    *,
    entity_id: str | None = None,
    info_type: str = "google_oauth_refresh",
    value: str = _REFRESH_TOKEN,
    last_test_ok: bool | None = True,
) -> MagicMock:
    row_id = uuid4()
    eid = entity_id or str(uuid4())
    return _make_row(
        id=row_id,
        entity_id=eid,
        type=info_type,
        value=value,
        label=None,
        last_verified=None,
        last_test_ok=last_test_ok,
        last_test_code=None,
        last_test_message=None,
        created_at=_NOW,
    )


def _make_butler_secrets_row(key: str, value: str) -> MagicMock:
    return _make_row(secret_key=key, secret_value=value)


def _make_shared_pool(
    *,
    user_row: MagicMock | None,
    raw_token_value: str | None,
    client_id: str | None = _CLIENT_ID,
    client_secret: str | None = _CLIENT_SECRET,
    test_mode_expired: bool = False,
) -> AsyncMock:
    shared_pool = AsyncMock()
    execute_calls: list[tuple] = []

    async def _fetchrow(sql: str, *args):
        if "secret_probe_log" in sql:
            return None
        if "entity_info" in sql and "WHERE id = $1" in sql:
            if raw_token_value is not None:
                return _make_row(value=raw_token_value)
            return None
        if "entity_info" in sql or "entities" in sql:
            return user_row
        if "butler_secrets" in sql:
            if args:
                key = args[0]
                from butlers.google_credentials import KEY_CLIENT_ID, KEY_CLIENT_SECRET

                if key == KEY_CLIENT_ID and client_id:
                    return _make_butler_secrets_row(key, client_id)
                if key == KEY_CLIENT_SECRET and client_secret:
                    return _make_butler_secrets_row(key, client_secret)
            return None
        return None

    async def _fetch(sql: str, *args):
        if "google_accounts" in sql and "last_token_refresh_at" in sql:
            if test_mode_expired and user_row is not None:
                return [
                    _make_row(
                        entity_id=user_row["entity_id"],
                        last_token_refresh_at=_NOW - timedelta(days=10),
                    )
                ]
            return []
        return []

    async def _execute(sql: str, *args):
        execute_calls.append((sql, args))
        return "INSERT 0 1"

    shared_pool.fetchrow = AsyncMock(side_effect=_fetchrow)
    shared_pool.fetch = AsyncMock(side_effect=_fetch)
    shared_pool.execute = AsyncMock(side_effect=_execute)
    shared_pool.execute_calls = execute_calls

    fake_conn = AsyncMock()
    fake_conn.fetchrow = shared_pool.fetchrow
    fake_conn.fetch = shared_pool.fetch
    fake_conn.execute = shared_pool.execute

    @asynccontextmanager
    async def _transaction():
        yield

    fake_conn.transaction = _transaction

    @asynccontextmanager
    async def _acquire():
        yield fake_conn

    shared_pool.acquire = _acquire
    return shared_pool


def _make_db(shared_pool: AsyncMock) -> MagicMock:
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["general"]
    mock_db.pool = MagicMock(return_value=AsyncMock())
    mock_db.credential_shared_pool = MagicMock(return_value=shared_pool)
    return mock_db


def _build_app(mock_db: MagicMock) -> TestClient:
    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    return TestClient(app)


def _install_url_aware_client(
    monkeypatch,
    *,
    token_exchange_status: int = 200,
    token_exchange_body: dict | None = None,
    capability_status: dict[str, int] | None = None,
) -> list[dict]:
    """Install a fake httpx.AsyncClient that answers per-capability GET calls
    differently based on a URL marker, and records every call made."""
    calls: list[dict] = []
    token_body = token_exchange_body or {"access_token": _ACCESS_TOKEN, "expires_in": 3600}
    capability_status = capability_status or dict.fromkeys(_GOOGLE_URL_MARKERS, 200)

    async def _fake_post(url, **kwargs):
        calls.append({"method": "POST", "url": str(url)})
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = token_exchange_status
        resp.json = MagicMock(return_value=token_body)
        return resp

    async def _fake_get(url, **kwargs):
        calls.append({"method": "GET", "url": str(url), "headers": kwargs.get("headers")})
        for capability, marker in _GOOGLE_URL_MARKERS.items():
            if marker in str(url):
                status = capability_status[capability]
                resp = MagicMock(spec=httpx.Response)
                resp.status_code = status
                resp.json = MagicMock(return_value={})
                return resp
        raise AssertionError(f"Unexpected GET url with no capability marker match: {url}")

    fake_client = AsyncMock()
    fake_client.post = AsyncMock(side_effect=_fake_post)
    fake_client.get = AsyncMock(side_effect=_fake_get)

    async def _fake_aenter(self):
        return fake_client

    async def _fake_aexit(self, *args):
        pass

    monkeypatch.setattr(httpx.AsyncClient, "__aenter__", _fake_aenter)
    monkeypatch.setattr(httpx.AsyncClient, "__aexit__", _fake_aexit)
    return calls


# ---------------------------------------------------------------------------
# All capabilities succeed
# ---------------------------------------------------------------------------


def test_all_google_capabilities_ok_credential_passes(monkeypatch):
    row = _make_entity_info_row()
    shared_pool = _make_shared_pool(user_row=row, raw_token_value=_REFRESH_TOKEN)
    mock_db = _make_db(shared_pool)
    calls = _install_url_aware_client(monkeypatch)

    client = _build_app(mock_db)
    resp = client.post("/api/secrets/user/google/probe")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["ok"] is True
    assert data["message"] is None

    # Exactly one token exchange + one GET per capability family.
    post_calls = [c for c in calls if c["method"] == "POST"]
    get_calls = [c for c in calls if c["method"] == "GET"]
    assert len(post_calls) == 1
    assert len(get_calls) == 4
    for marker in _GOOGLE_URL_MARKERS.values():
        assert any(marker in c["url"] for c in get_calls), f"missing capability call for {marker}"
        assert any(c["headers"]["Authorization"] == f"Bearer {_ACCESS_TOKEN}" for c in get_calls)

    # All 4 capability-qualified rows + 1 aggregate row were persisted.
    insert_calls = [args for sql, args in shared_pool.execute_calls if "secret_probe_log" in sql]
    keys_written = {args[1] for args in insert_calls}
    assert keys_written == {
        "google_oauth_refresh",
        "google_oauth_refresh:calendar",
        "google_oauth_refresh:gmail",
        "google_oauth_refresh:drive",
        "google_oauth_refresh:health",
    }
    # Every persisted row in this all-ok case is ok=True.
    for args in insert_calls:
        assert args[2] is True


# ---------------------------------------------------------------------------
# Health capability fails while others succeed — the exact asymmetry bug
# ---------------------------------------------------------------------------


def test_health_capability_403_rolls_up_credential_to_failing(monkeypatch):
    row = _make_entity_info_row()
    shared_pool = _make_shared_pool(user_row=row, raw_token_value=_REFRESH_TOKEN)
    mock_db = _make_db(shared_pool)
    _install_url_aware_client(
        monkeypatch,
        capability_status={"calendar": 200, "gmail": 200, "drive": 200, "health": 403},
    )

    client = _build_app(mock_db)
    resp = client.post("/api/secrets/user/google/probe")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["ok"] is False
    assert data["code"] == 403
    assert "health" in data["message"]
    assert "restricted-scope" in data["message"]

    # Calendar/gmail/drive persisted as ok=True; health persisted as ok=False;
    # the aggregate row reflects the rolled-up failure.
    rows_by_key = {
        args[1]: args[2] for sql, args in shared_pool.execute_calls if "secret_probe_log" in sql
    }
    assert rows_by_key["google_oauth_refresh:calendar"] is True
    assert rows_by_key["google_oauth_refresh:gmail"] is True
    assert rows_by_key["google_oauth_refresh:drive"] is True
    assert rows_by_key["google_oauth_refresh:health"] is False
    assert rows_by_key["google_oauth_refresh"] is False


def test_multiple_capabilities_failing_names_all_in_message(monkeypatch):
    row = _make_entity_info_row()
    shared_pool = _make_shared_pool(user_row=row, raw_token_value=_REFRESH_TOKEN)
    mock_db = _make_db(shared_pool)
    _install_url_aware_client(
        monkeypatch,
        capability_status={"calendar": 200, "gmail": 500, "drive": 200, "health": 403},
    )

    client = _build_app(mock_db)
    resp = client.post("/api/secrets/user/google/probe")

    data = resp.json()["data"]
    assert data["ok"] is False
    assert "gmail" in data["message"]
    assert "health" in data["message"]
    assert "calendar" not in data["message"]
    assert "drive" not in data["message"]


# ---------------------------------------------------------------------------
# Token-exchange failure is credential-wide (every capability shares it)
# ---------------------------------------------------------------------------


def test_token_exchange_failure_fails_every_capability_generic_message(monkeypatch):
    row = _make_entity_info_row()
    shared_pool = _make_shared_pool(
        user_row=row, raw_token_value=_REFRESH_TOKEN, test_mode_expired=False
    )
    mock_db = _make_db(shared_pool)
    _install_url_aware_client(
        monkeypatch, token_exchange_status=400, token_exchange_body={"error": "invalid_grant"}
    )

    client = _build_app(mock_db)
    resp = client.post("/api/secrets/user/google/probe")

    data = resp.json()["data"]
    assert data["ok"] is False
    assert data["code"] == 400
    assert "test-mode" not in data["message"]

    rows_by_key = {
        args[1]: args[2] for sql, args in shared_pool.execute_calls if "secret_probe_log" in sql
    }
    # Every capability-qualified row + the aggregate row shares the failure.
    assert rows_by_key["google_oauth_refresh:calendar"] is False
    assert rows_by_key["google_oauth_refresh:gmail"] is False
    assert rows_by_key["google_oauth_refresh:drive"] is False
    assert rows_by_key["google_oauth_refresh:health"] is False


def test_token_exchange_failure_on_test_mode_account_names_expiry(monkeypatch):
    row = _make_entity_info_row()
    shared_pool = _make_shared_pool(
        user_row=row, raw_token_value=_REFRESH_TOKEN, test_mode_expired=True
    )
    mock_db = _make_db(shared_pool)
    _install_url_aware_client(
        monkeypatch, token_exchange_status=400, token_exchange_body={"error": "invalid_grant"}
    )

    client = _build_app(mock_db)
    resp = client.post("/api/secrets/user/google/probe")

    data = resp.json()["data"]
    assert data["ok"] is False
    assert "test-mode" in data["message"]
    assert "7-day" in data["message"]


# ---------------------------------------------------------------------------
# Non-Google providers still get a 'connectivity' capability row
# ---------------------------------------------------------------------------


def test_github_pat_probe_writes_connectivity_capability_row(monkeypatch):
    row = _make_entity_info_row(info_type="github_pat", value="ghp_faketoken")
    shared_pool = _make_shared_pool(user_row=row, raw_token_value="ghp_faketoken")
    mock_db = _make_db(shared_pool)

    async def _fake_get(url, **kwargs):
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        resp.json = MagicMock(return_value={"login": "tzeusy"})
        return resp

    fake_client = AsyncMock()
    fake_client.get = AsyncMock(side_effect=_fake_get)
    fake_client.post = AsyncMock(side_effect=AssertionError("no POST expected for PAT"))

    async def _fake_aenter(self):
        return fake_client

    async def _fake_aexit(self, *args):
        pass

    monkeypatch.setattr(httpx.AsyncClient, "__aenter__", _fake_aenter)
    monkeypatch.setattr(httpx.AsyncClient, "__aexit__", _fake_aexit)

    client = _build_app(mock_db)
    resp = client.post("/api/secrets/user/github/probe")

    assert resp.status_code == 200
    assert resp.json()["data"]["ok"] is True

    rows_by_key = {
        args[1]: args[2] for sql, args in shared_pool.execute_calls if "secret_probe_log" in sql
    }
    assert rows_by_key["github_pat"] is True
    assert rows_by_key["github_pat:connectivity"] is True


# ---------------------------------------------------------------------------
# _capability_for_scopes classification
# ---------------------------------------------------------------------------


class TestCapabilityForScopes:
    def test_google_health_scope_maps_to_health(self):
        assert (
            _capability_for_scopes("google", ["https://www.googleapis.com/auth/googlehealth.sleep.readonly"])
            == "health"
        )

    def test_google_calendar_scope_maps_to_calendar(self):
        assert (
            _capability_for_scopes("google", ["https://www.googleapis.com/auth/calendar"])
            == "calendar"
        )

    def test_google_gmail_scope_maps_to_gmail(self):
        assert (
            _capability_for_scopes("google", ["https://www.googleapis.com/auth/gmail.modify"])
            == "gmail"
        )

    def test_google_drive_scope_maps_to_drive(self):
        assert _capability_for_scopes("google", ["https://www.googleapis.com/auth/drive"]) == (
            "drive"
        )

    def test_google_no_scopes_maps_to_none(self):
        assert _capability_for_scopes("google", []) is None

    def test_non_google_provider_maps_to_connectivity(self):
        assert _capability_for_scopes("telegram", []) == "connectivity"
        assert _capability_for_scopes("home_assistant", []) == "connectivity"


# ---------------------------------------------------------------------------
# _fetch_capability_probe_logs_bulk
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_capability_probe_logs_bulk_groups_by_base_key():
    pool = AsyncMock()
    pool.fetch = AsyncMock(
        return_value=[
            _make_row(
                credential_key="google_oauth_refresh:calendar",
                ok=True,
                code=None,
                message=None,
                recorded_at=_NOW,
                latency_ms=120,
            ),
            _make_row(
                credential_key="google_oauth_refresh:health",
                ok=False,
                code=403,
                message="403 restricted-scope (health scope not granted)",
                recorded_at=_NOW,
                latency_ms=95,
            ),
            _make_row(
                credential_key="github_pat:connectivity",
                ok=True,
                code=None,
                message=None,
                recorded_at=_NOW,
                latency_ms=50,
            ),
        ]
    )

    result = await _fetch_capability_probe_logs_bulk(
        pool, "user", ["google_oauth_refresh", "github_pat"]
    )

    assert set(result.keys()) == {"google_oauth_refresh", "github_pat"}
    google_caps = {c.capability: c for c in result["google_oauth_refresh"]}
    assert google_caps["calendar"].test.ok is True
    assert google_caps["health"].test.ok is False
    assert google_caps["health"].test.code == 403
    assert result["github_pat"][0].capability == "connectivity"


@pytest.mark.asyncio
async def test_fetch_capability_probe_logs_bulk_empty_keys_returns_empty():
    pool = AsyncMock()
    result = await _fetch_capability_probe_logs_bulk(pool, "user", [])
    assert result == {}
    pool.fetch.assert_not_called()
