"""Integration tests for the full Google OAuth bootstrap flow.

Condensed: 17 → ~8 tests [bu-gg4y1].
Keeps: happy-path full flow, state one-time-use, expired code, no-refresh-token,
startup guard blocks, startup guard DB-only contract, dev-mode oauth_start works.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.routers import oauth as oauth_module
from butlers.api.routers.oauth import (
    _clear_state_store,
    _generate_state,
    _state_store,
    _StateEntry,
    _store_state,
    _TokenExchangeError,
    _validate_and_consume_state,
)
from butlers.startup_guard import (
    check_google_credentials,
    require_google_credentials_or_exit,
)

pytestmark = pytest.mark.unit

GOOGLE_ENV = {
    "GOOGLE_OAUTH_CLIENT_ID": "test-client-id.apps.googleusercontent.com",
    "GOOGLE_OAUTH_CLIENT_SECRET": "test-client-secret",
    "GOOGLE_OAUTH_REDIRECT_URI": "http://localhost:41200/api/oauth/google/callback",
}

_FAKE_TOKEN_RESPONSE = {
    "access_token": "ya29.fake_access_token",
    "refresh_token": "1//fake_refresh_token_xyz",
    "scope": "https://www.googleapis.com/auth/gmail.modify https://www.googleapis.com/auth/calendar",
    "token_type": "Bearer",
    "expires_in": 3600,
}
_FAKE_USERINFO = {"email": "test@example.com", "name": "Test User"}

_EXCHANGE = "butlers.api.routers.oauth._exchange_code_for_tokens"
_USERINFO = "butlers.api.routers.oauth._fetch_google_userinfo"
_GET_ACCOUNT = "butlers.api.routers.oauth.get_google_account"
_CREATE_ACCOUNT = "butlers.api.routers.oauth.create_google_account"
_STORE_CREDS = "butlers.api.routers.oauth.store_app_credentials"


@pytest.fixture(autouse=True)
def clear_states():
    _clear_state_store()
    yield
    _clear_state_store()


def _make_app(
    *,
    db_client_id="test-client-id.apps.googleusercontent.com",
    db_client_secret="test-client-secret",
    db_refresh_token=None,
):
    app = create_app()
    secrets = {
        "GOOGLE_OAUTH_CLIENT_ID": db_client_id,
        "GOOGLE_OAUTH_CLIENT_SECRET": db_client_secret,
    }
    contact_info = {}
    if db_refresh_token:
        contact_info["google_oauth_refresh"] = db_refresh_token

    conn = AsyncMock()
    _fake_entity_id = uuid.uuid4()

    async def _fetchrow(query, *args):
        if "public.google_accounts" in query:
            row = MagicMock()
            row.__getitem__ = lambda self, k: _fake_entity_id if k == "entity_id" else None
            return row
        if "public.entities" in query:
            row = MagicMock()
            row.__getitem__ = lambda self, k: "owner-uuid" if k == "id" else None
            return row
        if "public.entity_info" in query:
            type_key = args[1] if len(args) > 1 else (args[0] if args else None)
            value = contact_info.get(type_key) if type_key else None
            if not value:
                return None
            row = MagicMock()
            row.__getitem__ = lambda self, k: value if k == "value" else None
            return row
        key = args[0] if args else None
        value = secrets.get(key) if key else None
        return {"secret_value": value} if value else None

    conn.fetchrow.side_effect = _fetchrow
    conn.execute = AsyncMock(return_value="DELETE 0")

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool = MagicMock()
    pool.acquire = _acquire
    db_manager = MagicMock()
    db_manager.credential_shared_pool.return_value = pool
    app.dependency_overrides[oauth_module._get_db_manager] = lambda: db_manager
    return app


# ---------------------------------------------------------------------------
# Happy path: full flow
# ---------------------------------------------------------------------------


async def test_full_oauth_flow_happy_path():
    """start → get state → callback with state → 200 success."""
    from butlers.google_account_registry import GoogleAccountNotFoundError

    app = _make_app()
    with patch.dict("os.environ", GOOGLE_ENV, clear=False):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            start_resp = await client.get("/api/oauth/google/start", params={"redirect": "false"})
    assert start_resp.status_code == 200
    state = start_resp.json()["state"]

    with (
        patch.dict("os.environ", GOOGLE_ENV, clear=False),
        patch(_EXCHANGE, AsyncMock(return_value=_FAKE_TOKEN_RESPONSE)),
        patch(_USERINFO, AsyncMock(return_value=_FAKE_USERINFO)),
        patch(_GET_ACCOUNT, AsyncMock(side_effect=GoogleAccountNotFoundError("not found"))),
        patch(_CREATE_ACCOUNT, AsyncMock()),
        patch(_STORE_CREDS, AsyncMock()),
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/oauth/google/callback", params={"code": "4/code", "state": state}
            )
    assert resp.status_code == 200
    assert resp.json()["success"] is True
    assert resp.json()["provider"] == "google"


async def test_state_consumed_after_successful_callback():
    """After a successful callback, replaying the same state returns 400."""
    from butlers.google_account_registry import GoogleAccountNotFoundError

    app = _make_app()
    state = _generate_state()
    _store_state(state)

    with (
        patch.dict("os.environ", GOOGLE_ENV, clear=False),
        patch(_EXCHANGE, AsyncMock(return_value=_FAKE_TOKEN_RESPONSE)),
        patch(_USERINFO, AsyncMock(return_value=_FAKE_USERINFO)),
        patch(_GET_ACCOUNT, AsyncMock(side_effect=GoogleAccountNotFoundError("not found"))),
        patch(_CREATE_ACCOUNT, AsyncMock()),
        patch(_STORE_CREDS, AsyncMock()),
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp1 = await client.get(
                "/api/oauth/google/callback", params={"code": "4/c1", "state": state}
            )
            resp2 = await client.get(
                "/api/oauth/google/callback", params={"code": "4/c2", "state": state}
            )
    assert resp1.status_code == 200
    assert resp2.status_code == 400
    assert resp2.json()["error_code"] == "invalid_state"


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


async def test_flow_fails_expired_code():
    app = _make_app()
    state = _generate_state()
    _store_state(state)
    with (
        patch.dict("os.environ", GOOGLE_ENV, clear=False),
        patch(_EXCHANGE, AsyncMock(side_effect=_TokenExchangeError("invalid_grant"))),
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/oauth/google/callback", params={"code": "4/expired", "state": state}
            )
    assert resp.status_code == 400
    assert resp.json()["error_code"] == "token_exchange_failed"
    assert _validate_and_consume_state(state) is None  # state consumed


async def test_flow_fails_no_refresh_token():
    """Callback fails if token response lacks refresh_token."""
    from butlers.google_account_registry import GoogleAccountNotFoundError

    app = _make_app()
    state = _generate_state()
    _store_state(state)
    no_refresh = {"access_token": "ya29.only_access", "token_type": "Bearer", "expires_in": 3600}
    with (
        patch.dict("os.environ", GOOGLE_ENV, clear=False),
        patch(_EXCHANGE, AsyncMock(return_value=no_refresh)),
        patch(_USERINFO, AsyncMock(return_value=_FAKE_USERINFO)),
        patch(_GET_ACCOUNT, AsyncMock(side_effect=GoogleAccountNotFoundError("not found"))),
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/oauth/google/callback", params={"code": "4/code", "state": state}
            )
    assert resp.status_code == 400
    assert resp.json()["error_code"] == "no_refresh_token"


async def test_flow_fails_expired_state():
    import time

    app = _make_app()
    state = _generate_state()
    _state_store[state] = _StateEntry(expiry=time.monotonic() - 1)
    with patch.dict("os.environ", GOOGLE_ENV, clear=False):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/oauth/google/callback", params={"code": "4/c", "state": state}
            )
    assert resp.status_code == 400
    assert resp.json()["error_code"] == "invalid_state"


# ---------------------------------------------------------------------------
# Startup guard
# ---------------------------------------------------------------------------


def test_startup_guard_blocks_without_db_credentials():
    with pytest.raises(SystemExit) as exc_info:
        require_google_credentials_or_exit(caller="gmail-connector")
    assert exc_info.value.code == 1


def test_startup_guard_db_only_contract():
    """check_google_credentials() returns DB-only remediation guidance."""
    result = check_google_credentials()
    assert result.ok is False
    assert result.missing_vars == []
    assert "db-managed" in result.message.lower()


# ---------------------------------------------------------------------------
# Dev workflow
# ---------------------------------------------------------------------------


async def test_oauth_start_works_without_stored_refresh_token():
    """GET /api/oauth/google/start works even when no token is stored yet."""
    app = _make_app(db_refresh_token=None)
    with patch.dict("os.environ", GOOGLE_ENV, clear=False):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            resp = await client.get("/api/oauth/google/start")
    assert resp.status_code == 302
    assert "accounts.google.com" in resp.headers["location"]
