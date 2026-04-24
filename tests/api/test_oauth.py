"""Tests for OAuth and OAuth status API endpoints.

Condensed from test_oauth.py (58) + test_oauth_status.py (25) → ~12 tests (bu-egmz6).
Keeps: state store contract (unit), redirect/JSON mode, callback validation,
oauth_status list/detail error paths.
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
    _store_state,
    _validate_and_consume_state,
)

pytestmark = pytest.mark.unit

_EXCHANGE_PATCH = "butlers.api.routers.oauth._exchange_code_for_tokens"
_USERINFO_PATCH = "butlers.api.routers.oauth._fetch_google_userinfo"
_CREATE_ACCOUNT_PATCH = "butlers.api.routers.oauth.create_google_account"

_FAKE_TOKEN = {
    "access_token": "ya29.fake",
    "refresh_token": "1//fake-refresh",
    "scope": "https://www.googleapis.com/auth/gmail.readonly",
    "token_type": "Bearer",
    "expires_in": 3600,
}

_FAKE_USERINFO = {"email": "test@example.com", "name": "Test User", "id": "12345"}


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


# ---------------------------------------------------------------------------
# State store (unit)
# ---------------------------------------------------------------------------


class TestStateStore:
    def test_generate_state_unique_and_url_safe(self):
        states = {_generate_state() for _ in range(5)}
        assert len(states) == 5
        for s in states:
            assert len(s) >= 32
            valid = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_=")
            assert all(c in valid for c in s)

    def test_store_and_validate_one_time_use(self):
        state = _generate_state()
        _store_state(state)
        assert _validate_and_consume_state(state) is not None
        assert _validate_and_consume_state(state) is None  # one-time use

    def test_unknown_state_rejected(self):
        assert _validate_and_consume_state("totally-fake-state") is None

    def test_expired_state_rejected(self):

        from butlers.api.routers import oauth as _mod

        state = _generate_state()
        entry = _mod._StateEntry(expiry=0.0)
        _mod._state_store[state] = entry
        assert _validate_and_consume_state(state) is None


# ---------------------------------------------------------------------------
# OAuth start
# ---------------------------------------------------------------------------


class TestOAuthStart:
    async def test_start_redirects_by_default(self, app):
        _make_app(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as client:
            resp = await client.get("/api/oauth/google/start")
        assert resp.status_code in (302, 307)
        assert "accounts.google.com" in resp.headers.get("location", "")

    async def test_start_returns_json_when_redirect_false(self, app):
        _make_app(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/oauth/google/start", params={"redirect": "false"})
        assert resp.status_code == 200
        assert "authorization_url" in resp.json()

    async def test_start_missing_credentials_returns_503(self, app):
        app.dependency_overrides[oauth_module._get_db_manager] = lambda: None
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/oauth/google/start")
        assert resp.status_code in (503, 500)


# ---------------------------------------------------------------------------
# OAuth start — scope_set selector (bu-k5l35.1.3)
# ---------------------------------------------------------------------------


def _extract_scope_param(authorization_url: str) -> list[str]:
    """Extract and split the `scope` query parameter from a Google auth URL."""
    from urllib.parse import parse_qs, urlparse

    parsed = urlparse(authorization_url)
    qs = parse_qs(parsed.query)
    raw = qs.get("scope", [""])[0]
    return raw.split() if raw else []


class TestScopeSetSelector:
    """Verify the scope_set query param behaviour on /api/oauth/google/start."""

    _HEALTH_SCOPES = frozenset(
        [
            "https://www.googleapis.com/auth/googlehealth.sleep",
            "https://www.googleapis.com/auth/googlehealth.activity_and_fitness",
            "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements",
        ]
    )
    _BASE_SCOPES = frozenset(["openid", "email", "profile"])

    async def test_scope_set_health_produces_full_health_scope_urls(self, app):
        _make_app(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/oauth/google/start",
                params={"redirect": "false", "scope_set": "health"},
            )
        assert resp.status_code == 200
        scopes = set(_extract_scope_param(resp.json()["authorization_url"]))
        # All three Google Health scopes present as full URLs.
        assert self._HEALTH_SCOPES.issubset(scopes)
        # Base scopes (openid/email/profile) are always implicitly included.
        assert self._BASE_SCOPES.issubset(scopes)
        # Calendar and Drive are NOT included when only 'health' is requested.
        assert "https://www.googleapis.com/auth/calendar" not in scopes
        assert "https://www.googleapis.com/auth/drive" not in scopes

    async def test_scope_set_multi_set_composes_union(self, app):
        _make_app(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/oauth/google/start",
                params={
                    "redirect": "false",
                    "scope_set": "calendar,drive,health",
                    "force_consent": "true",
                    "account_hint": "owner@example.com",
                },
            )
        assert resp.status_code == 200
        scopes = set(_extract_scope_param(resp.json()["authorization_url"]))
        # Union of all three sets plus base.
        assert self._BASE_SCOPES.issubset(scopes)
        assert self._HEALTH_SCOPES.issubset(scopes)
        assert "https://www.googleapis.com/auth/calendar" in scopes
        assert "https://www.googleapis.com/auth/drive" in scopes

    async def test_scope_set_unknown_returns_400_with_actionable_json(self, app):
        _make_app(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/oauth/google/start",
                params={"redirect": "false", "scope_set": "bogus"},
            )
        assert resp.status_code == 400
        body = resp.json()
        assert body["error"] == "unknown_scope_set"
        assert body["scope_set"] == "bogus"
        # Known sets list is returned so the caller can self-correct.
        assert "health" in body["known"]
        assert "calendar" in body["known"]
        assert "drive" in body["known"]
        assert "base" in body["known"]

    async def test_scope_set_unknown_in_multi_set_returns_400(self, app):
        _make_app(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/oauth/google/start",
                params={"redirect": "false", "scope_set": "calendar,bogus,health"},
            )
        assert resp.status_code == 400
        body = resp.json()
        assert body["error"] == "unknown_scope_set"
        assert body["scope_set"] == "bogus"

    async def test_scope_set_omitted_preserves_backward_compat(self, app):
        """Omitting scope_set yields the pre-change default scope composition."""
        _make_app(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp_default = await client.get("/api/oauth/google/start", params={"redirect": "false"})
        assert resp_default.status_code == 200
        default_scopes = set(_extract_scope_param(resp_default.json()["authorization_url"]))
        # Default composition must cover gmail, calendar, drive, contacts, and base.
        assert self._BASE_SCOPES.issubset(default_scopes)
        assert "https://www.googleapis.com/auth/gmail.modify" in default_scopes
        assert "https://www.googleapis.com/auth/calendar" in default_scopes
        assert "https://www.googleapis.com/auth/drive" in default_scopes
        # Health scopes MUST NOT leak into the default composition — they are
        # only granted when scope_set=health is explicitly requested.
        assert self._HEALTH_SCOPES.isdisjoint(default_scopes)

    async def test_scope_set_empty_string_treated_as_omitted(self, app):
        """scope_set= (empty) falls back to default composition for backward compat."""
        _make_app(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/oauth/google/start", params={"redirect": "false", "scope_set": ""}
            )
        assert resp.status_code == 200
        scopes = set(_extract_scope_param(resp.json()["authorization_url"]))
        # Health scopes MUST NOT leak when the selector is empty.
        assert self._HEALTH_SCOPES.isdisjoint(scopes)

    async def test_scope_set_base_explicit_no_duplicates(self, app):
        """Requesting base explicitly should not duplicate the base scopes."""
        _make_app(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/oauth/google/start",
                params={"redirect": "false", "scope_set": "base,health"},
            )
        assert resp.status_code == 200
        scope_list = _extract_scope_param(resp.json()["authorization_url"])
        # No duplicate entries in the scope list.
        assert len(scope_list) == len(set(scope_list))


# ---------------------------------------------------------------------------
# OAuth callback
# ---------------------------------------------------------------------------


class TestOAuthCallback:
    async def test_callback_missing_code_returns_400(self, app):
        _make_app(app)
        state = _generate_state()
        _store_state(state)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/oauth/google/callback", params={"state": state})
        assert resp.status_code == 400

    async def test_callback_invalid_state_returns_400(self, app):
        _make_app(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/oauth/google/callback",
                params={"code": "test-code", "state": "invalid-state"},
            )
        assert resp.status_code == 400

    async def test_callback_success(self, app):
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
                    "/api/oauth/google/callback",
                    params={"code": "test-code", "state": state},
                )
        assert resp.status_code == 200
        body = resp.json()
        assert "account_email" in body or "email" in body or "status" in body or "provider" in body


# ---------------------------------------------------------------------------
# OAuth status endpoint
# ---------------------------------------------------------------------------

_BASE_ENV = {
    "GOOGLE_OAUTH_CLIENT_ID": "test-client-id.apps.googleusercontent.com",
    "GOOGLE_OAUTH_CLIENT_SECRET": "test-client-secret",
    "GOOGLE_OAUTH_REDIRECT_URI": "http://localhost:41200/api/oauth/google/callback",
}


def _make_status_app(
    app,
    *,
    client_id="test-client-id.apps.googleusercontent.com",
    client_secret="test-secret",
    refresh_token=None,
):
    """Wire app for oauth/status tests."""
    secrets = {"GOOGLE_OAUTH_CLIENT_ID": client_id, "GOOGLE_OAUTH_CLIENT_SECRET": client_secret}
    contact_info = {}
    if refresh_token is not None:
        contact_info["google_oauth_refresh"] = refresh_token

    conn = AsyncMock()
    fake_entity_id = uuid.uuid4()

    async def _fetchrow(query, *args):
        if "google_accounts" in query:
            row = MagicMock()
            row.__getitem__ = lambda self, k: fake_entity_id if k == "entity_id" else None
            return row
        if "entities" in query:
            row = MagicMock()
            row.__getitem__ = lambda self, k: "owner-uuid" if k == "id" else None
            return row
        if "entity_info" in query:
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


class TestOAuthStatus:
    async def test_no_client_id_returns_not_configured(self, app):
        _make_status_app(app, client_id="")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/oauth/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["google"]["state"] == OAuthCredentialState.not_configured
        assert body["google"]["connected"] is False

    async def test_no_refresh_token_returns_not_configured(self, app):
        _make_status_app(app, refresh_token=None)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/oauth/status")
        assert resp.status_code == 200
        assert resp.json()["google"]["state"] == OAuthCredentialState.not_configured

    async def test_status_returns_google_structure(self, app):
        """OAuth status always returns a 'google' key with state and connected fields."""
        _make_status_app(app, client_id="")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/oauth/status")
        assert resp.status_code == 200
        body = resp.json()
        assert "google" in body
        assert "state" in body["google"]
        assert "connected" in body["google"]
