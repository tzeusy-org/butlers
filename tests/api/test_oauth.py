"""Tests for OAuth and OAuth status API endpoints.

Condensed: 54 → ~15 tests [bu-gg4y1].
Keeps: state store contract, redirect/JSON modes, scope-set selector contract,
scope-widening union, callback validation, oauth_status structure,
health test-mode flag contract.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from butlers.api.models.oauth import OAuthCredentialState
from butlers.api.routers import oauth as oauth_module
from butlers.api.routers.oauth import (
    _clear_state_store,
    _generate_state,
    _has_health_scope,
    _is_google_health_test_mode,
    _store_state,
    _validate_and_consume_state,
    _widen_scopes,
)

pytestmark = pytest.mark.unit

_EXCHANGE_PATCH = "butlers.api.routers.oauth._exchange_code_for_tokens"
_USERINFO_PATCH = "butlers.api.routers.oauth._fetch_google_userinfo"
_CREATE_ACCOUNT_PATCH = "butlers.api.routers.oauth.create_google_account"
_GET_ACCOUNT_PATCH = "butlers.api.routers.oauth.get_google_account"
_SET_META_PATCH = "butlers.api.routers.oauth._set_account_health_test_mode"

_FAKE_TOKEN = {
    "access_token": "ya29.fake",
    "refresh_token": "1//fake-refresh",
    "scope": "https://www.googleapis.com/auth/gmail.readonly",
    "token_type": "Bearer",
    "expires_in": 3600,
}
_FAKE_USERINFO = {"email": "test@example.com", "name": "Test User", "id": "12345"}

_CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar"
_DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"
_HEALTH_SCOPES = frozenset(
    [
        "https://www.googleapis.com/auth/googlehealth.sleep",
        "https://www.googleapis.com/auth/googlehealth.activity_and_fitness",
        "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements",
    ]
)
_HEALTH_SCOPE_STR = "https://www.googleapis.com/auth/fitness.activity.read openid email"
_NON_HEALTH_SCOPE_STR = "https://www.googleapis.com/auth/gmail.readonly openid email"
_FAKE_HEALTH_TOKEN = {**_FAKE_TOKEN, "scope": _HEALTH_SCOPE_STR}


@pytest.fixture(autouse=True)
def clear_states():
    _clear_state_store()
    yield
    _clear_state_store()


def _make_app(
    app, *, client_id="test-client-id.apps.googleusercontent.com", client_secret="test-secret"
):
    secrets = {
        "GOOGLE_OAUTH_CLIENT_ID": client_id,
        "GOOGLE_OAUTH_CLIENT_SECRET": client_secret,
    }
    conn = AsyncMock()

    async def _fetchrow(query, *args):
        if "google_accounts" in query:
            row = MagicMock()
            row.__getitem__ = lambda self, k: uuid.uuid4() if k == "entity_id" else None
            return row
        if "entities" in query:
            row = MagicMock()
            row.__getitem__ = lambda self, k: "owner-uuid" if k == "id" else None
            return row
        if "entity_info" in query:
            return None
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


def _extract_scope_param(authorization_url: str) -> list[str]:
    from urllib.parse import parse_qs, urlparse

    parsed = urlparse(authorization_url)
    qs = parse_qs(parsed.query)
    raw = qs.get("scope", [""])[0]
    return raw.split() if raw else []


# ---------------------------------------------------------------------------
# State store (unit)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "base,granted,must_contain,must_not_duplicate",
    [
        ("openid email", ["openid", _CALENDAR_SCOPE], [_CALENDAR_SCOPE], ["openid"]),
        ("openid email", [], None, ["openid"]),
        ("openid email profile", [_CALENDAR_SCOPE], None, None),
    ],
)
def test_widen_scopes(base, granted, must_contain, must_not_duplicate):
    result = _widen_scopes(base, granted)
    parts = result.split()
    if must_contain:
        for s in must_contain:
            assert s in parts
    if must_not_duplicate:
        for s in must_not_duplicate:
            assert parts.count(s) == 1
    # Base scopes are never removed.
    for s in base.split():
        assert s in parts


def test_state_store_one_time_use_and_rejection():
    state = _generate_state()
    _store_state(state)
    assert _validate_and_consume_state(state) is not None
    assert _validate_and_consume_state(state) is None  # one-time use
    assert _validate_and_consume_state("fake-state") is None


def test_state_expired_rejected():
    from butlers.api.routers import oauth as _mod

    state = _generate_state()
    _mod._state_store[state] = _mod._StateEntry(expiry=0.0)
    assert _validate_and_consume_state(state) is None


# ---------------------------------------------------------------------------
# OAuth start
# ---------------------------------------------------------------------------


async def test_start_redirects_by_default(app):
    _make_app(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test", follow_redirects=False
    ) as client:
        resp = await client.get("/api/oauth/google/start")
    assert resp.status_code in (302, 307)
    assert "accounts.google.com" in resp.headers.get("location", "")


async def test_start_returns_json_when_redirect_false(app):
    _make_app(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/oauth/google/start", params={"redirect": "false"})
    assert resp.status_code == 200
    assert "authorization_url" in resp.json()


async def test_start_missing_credentials_returns_503(app):
    app.dependency_overrides[oauth_module._get_db_manager] = lambda: None
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/oauth/google/start")
    assert resp.status_code in (503, 500)


# ---------------------------------------------------------------------------
# Scope-set selector
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "scope_set,expected_present,expected_absent",
    [
        ("health", list(_HEALTH_SCOPES), [_CALENDAR_SCOPE]),
        ("calendar,drive,health", list(_HEALTH_SCOPES) + [_CALENDAR_SCOPE, _DRIVE_SCOPE], []),
    ],
)
async def test_scope_set_selector(app, scope_set, expected_present, expected_absent):
    _make_app(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/oauth/google/start", params={"redirect": "false", "scope_set": scope_set}
        )
    assert resp.status_code == 200
    scopes = set(_extract_scope_param(resp.json()["authorization_url"]))
    for s in expected_present:
        assert s in scopes
    for s in expected_absent:
        assert s not in scopes


async def test_scope_set_unknown_returns_400(app):
    _make_app(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/oauth/google/start", params={"redirect": "false", "scope_set": "bogus"}
        )
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"] == "unknown_scope_set"
    assert "health" in body["known"]


async def test_scope_set_omitted_default_excludes_health(app):
    _make_app(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/oauth/google/start", params={"redirect": "false"})
    assert resp.status_code == 200
    scopes = set(_extract_scope_param(resp.json()["authorization_url"]))
    assert _HEALTH_SCOPES.isdisjoint(scopes)
    assert "https://www.googleapis.com/auth/gmail.modify" in scopes


# ---------------------------------------------------------------------------
# Scope widening
# ---------------------------------------------------------------------------


async def test_scope_widening_unions_granted_scopes(app):
    """scope_set=health with a hinted account that has calendar granted unions calendar in."""
    _make_app(app)
    existing_granted = [_CALENDAR_SCOPE, _DRIVE_SCOPE]
    mock_account = MagicMock()
    mock_account.granted_scopes = existing_granted
    with patch(_GET_ACCOUNT_PATCH, AsyncMock(return_value=mock_account)):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/oauth/google/start",
                params={"redirect": "false", "scope_set": "health", "account_hint": "u@example.com"},
            )
    assert resp.status_code == 200
    scopes = set(_extract_scope_param(resp.json()["authorization_url"]))
    assert _HEALTH_SCOPES.issubset(scopes)
    assert _CALENDAR_SCOPE in scopes
    assert _DRIVE_SCOPE in scopes


# ---------------------------------------------------------------------------
# OAuth callback
# ---------------------------------------------------------------------------


async def test_callback_missing_code_returns_400(app):
    _make_app(app)
    state = _generate_state()
    _store_state(state)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/oauth/google/callback", params={"state": state})
    assert resp.status_code == 400


async def test_callback_invalid_state_returns_400(app):
    _make_app(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/oauth/google/callback",
            params={"code": "test-code", "state": "invalid-state"},
        )
    assert resp.status_code == 400


async def test_callback_success(app):
    _make_app(app)
    state = _generate_state()
    _store_state(state)
    with (
        patch(_EXCHANGE_PATCH, AsyncMock(return_value=_FAKE_TOKEN)),
        patch(_USERINFO_PATCH, AsyncMock(return_value=_FAKE_USERINFO)),
        patch(_CREATE_ACCOUNT_PATCH, AsyncMock(return_value=MagicMock(id=uuid.uuid4()))),
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/oauth/google/callback", params={"code": "test-code", "state": state}
            )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# OAuth status
# ---------------------------------------------------------------------------


async def test_oauth_status_returns_google_structure(app):
    conn = AsyncMock()
    conn.fetchrow.return_value = None
    conn.execute = AsyncMock(return_value="DELETE 0")

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool = MagicMock()
    pool.acquire = _acquire
    db_manager = MagicMock()
    db_manager.credential_shared_pool.return_value = pool
    app.dependency_overrides[oauth_module._get_db_manager] = lambda: db_manager

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/oauth/status")
    assert resp.status_code == 200
    body = resp.json()
    assert "google" in body
    assert "state" in body["google"]
    assert "connected" in body["google"]
    assert body["google"]["state"] == OAuthCredentialState.not_configured


# ---------------------------------------------------------------------------
# Health test-mode helpers (parametrized)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("val,expected", [
    ("1", True), ("true", True), ("TRUE", True), ("yes", True),
    ("0", False), ("false", False), ("no", False), ("", False),
])
def test_is_google_health_test_mode(monkeypatch, val, expected):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_TEST_MODE", val)
    assert _is_google_health_test_mode() is expected


@pytest.mark.parametrize("scope,expected", [
    ("https://www.googleapis.com/auth/googlehealth.readings", True),
    ("https://www.googleapis.com/auth/fitness.activity.read", True),
    ("https://www.googleapis.com/auth/gmail.readonly openid email", False),
    (None, False),
    ("", False),
])
def test_has_health_scope(scope, expected):
    assert _has_health_scope(scope) is expected


# ---------------------------------------------------------------------------
# Health test-mode callback contract
# ---------------------------------------------------------------------------


def _make_callback_app(app):
    secrets = {
        "GOOGLE_OAUTH_CLIENT_ID": "test-client-id.apps.googleusercontent.com",
        "GOOGLE_OAUTH_CLIENT_SECRET": "test-secret",
    }
    conn = AsyncMock()

    async def _fetchrow(query, *args):
        key = args[0] if args else None
        value = secrets.get(key) if key else None
        return {"secret_value": value} if value else None

    conn.fetchrow.side_effect = _fetchrow
    conn.execute = AsyncMock(return_value="OK")

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool = MagicMock()
    pool.acquire = _acquire
    db_manager = MagicMock()
    db_manager.credential_shared_pool.return_value = pool
    app.dependency_overrides[oauth_module._get_db_manager] = lambda: db_manager
    return app, pool


async def test_health_test_mode_sets_flag_when_health_scope_and_test_mode(app, monkeypatch):
    """Callback sets google_health_test_mode only when health scope granted + test mode active."""
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_TEST_MODE", "true")
    entity_id = uuid.uuid4()
    fake_account = MagicMock()
    fake_account.entity_id = entity_id

    app, _pool = _make_callback_app(app)
    state = _generate_state()
    _store_state(state)

    with (
        patch(_EXCHANGE_PATCH, AsyncMock(return_value=_FAKE_HEALTH_TOKEN)),
        patch(_USERINFO_PATCH, AsyncMock(return_value=_FAKE_USERINFO)),
        patch(_GET_ACCOUNT_PATCH, AsyncMock(return_value=fake_account)),
        patch(_SET_META_PATCH, AsyncMock()) as mock_set_meta,
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/oauth/google/callback", params={"code": "auth-code", "state": state}
            )
    assert resp.status_code == 200
    mock_set_meta.assert_called_once_with(_pool, entity_id=entity_id)


async def test_health_test_mode_not_set_in_prod_mode(app, monkeypatch):
    """Callback does NOT set flag when health scope granted but client is in production mode."""
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_TEST_MODE", raising=False)
    entity_id = uuid.uuid4()
    fake_account = MagicMock()
    fake_account.entity_id = entity_id

    app, _pool = _make_callback_app(app)
    state = _generate_state()
    _store_state(state)

    with (
        patch(_EXCHANGE_PATCH, AsyncMock(return_value=_FAKE_HEALTH_TOKEN)),
        patch(_USERINFO_PATCH, AsyncMock(return_value=_FAKE_USERINFO)),
        patch(_GET_ACCOUNT_PATCH, AsyncMock(return_value=fake_account)),
        patch(_SET_META_PATCH, AsyncMock()) as mock_set_meta,
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/oauth/google/callback", params={"code": "auth-code", "state": state}
            )
    assert resp.status_code == 200
    mock_set_meta.assert_not_called()
