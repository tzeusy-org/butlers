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


class TestWidenScopes:
    """Unit tests for the _widen_scopes helper."""

    def test_adds_missing_scopes(self):
        base = "openid email"
        granted = ["openid", "https://www.googleapis.com/auth/calendar"]
        result = _widen_scopes(base, granted)
        parts = result.split()
        assert "openid" in parts
        assert "email" in parts
        assert "https://www.googleapis.com/auth/calendar" in parts

    def test_no_duplicates(self):
        base = "openid email"
        granted = ["openid", "email"]
        result = _widen_scopes(base, granted)
        parts = result.split()
        assert parts.count("openid") == 1
        assert parts.count("email") == 1

    def test_empty_granted_scopes_returns_original(self):
        base = "openid email"
        result = _widen_scopes(base, [])
        assert result == base

    def test_preserves_requested_scope_order(self):
        base = "openid email profile"
        granted = ["https://www.googleapis.com/auth/calendar"]
        result = _widen_scopes(base, granted)
        parts = result.split()
        # First three must be the requested scopes in original order.
        assert parts[:3] == ["openid", "email", "profile"]
        # Calendar appended after.
        assert parts[3] == "https://www.googleapis.com/auth/calendar"

    def test_never_removes_scopes_from_base(self):
        base = "openid email https://www.googleapis.com/auth/calendar"
        granted = ["openid"]  # granted is a subset of base
        result = _widen_scopes(base, granted)
        parts = set(result.split())
        assert "https://www.googleapis.com/auth/calendar" in parts


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
# Scope widening (bu-xmirt)
# ---------------------------------------------------------------------------

_GET_ACCOUNT_PATCH = "butlers.api.routers.oauth.get_google_account"

_CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar"
_DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"
_DRIVE_READONLY_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
_HEALTH_SCOPES = frozenset(
    [
        "https://www.googleapis.com/auth/googlehealth.sleep",
        "https://www.googleapis.com/auth/googlehealth.activity_and_fitness",
        "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements",
    ]
)


def _make_mock_account(granted_scopes: list[str]) -> MagicMock:
    """Return a minimal GoogleAccount mock with the given granted_scopes."""
    account = MagicMock()
    account.granted_scopes = granted_scopes
    account.is_primary = False
    account.status = "active"
    return account


def _make_app_with_scope_widening(
    app,
    *,
    existing_granted_scopes: list[str],
    account_hint: str = "user@example.com",
):
    """Wire app so get_google_account returns an account with given granted_scopes.

    Uses patch context manager rather than DB-level mock so we can control
    the granted_scopes precisely without reimplementing the full asyncpg row
    adapter.  The fixture is consumed by the test via the returned patch context.
    """
    return _make_app(app), _make_mock_account(existing_granted_scopes)


class TestScopeWidening:
    """Scope-widening: union granted_scopes into the requested scope_set."""

    async def test_single_set_request_unions_granted_scopes(self, app):
        """GET /google/start?scope_set=health with a hinted account that has calendar+drive
        granted must return an auth URL that includes calendar+drive+health+base."""
        _make_app(app)
        existing_granted = [
            _CALENDAR_SCOPE,
            _DRIVE_SCOPE,
            _DRIVE_READONLY_SCOPE,
        ]
        mock_account = _make_mock_account(existing_granted)
        with patch(_GET_ACCOUNT_PATCH, AsyncMock(return_value=mock_account)):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(
                    "/api/oauth/google/start",
                    params={
                        "redirect": "false",
                        "scope_set": "health",
                        "account_hint": "user@example.com",
                    },
                )
        assert resp.status_code == 200
        scopes = set(_extract_scope_param(resp.json()["authorization_url"]))
        # Health scopes present (requested set).
        assert _HEALTH_SCOPES.issubset(scopes)
        # Previously-granted scopes are retained (scope-widening).
        assert _CALENDAR_SCOPE in scopes
        assert _DRIVE_SCOPE in scopes
        assert _DRIVE_READONLY_SCOPE in scopes
        # Base scopes always present.
        assert "openid" in scopes

    async def test_multi_set_request_unions_granted_scopes(self, app):
        """GET /google/start?scope_set=health with multiple sets still unions granted_scopes."""
        _make_app(app)
        existing_granted = [
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/gmail.modify",
        ]
        mock_account = _make_mock_account(existing_granted)
        with patch(_GET_ACCOUNT_PATCH, AsyncMock(return_value=mock_account)):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(
                    "/api/oauth/google/start",
                    params={
                        "redirect": "false",
                        "scope_set": "calendar,health",
                        "account_hint": "user@example.com",
                        "force_consent": "true",
                    },
                )
        assert resp.status_code == 200
        scopes = set(_extract_scope_param(resp.json()["authorization_url"]))
        # Requested sets are present.
        assert _HEALTH_SCOPES.issubset(scopes)
        assert _CALENDAR_SCOPE in scopes
        # Previously-granted Gmail scopes are unioned in.
        assert "https://www.googleapis.com/auth/gmail.modify" in scopes

    async def test_no_account_hint_no_widening(self, app):
        """Without account_hint, no DB lookup is done — only the requested set is returned."""
        _make_app(app)
        # Even if get_google_account would be callable it must NOT be called without a hint.
        with patch(
            _GET_ACCOUNT_PATCH, AsyncMock(side_effect=AssertionError("should not be called"))
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(
                    "/api/oauth/google/start",
                    params={"redirect": "false", "scope_set": "health"},
                )
        assert resp.status_code == 200
        scopes = set(_extract_scope_param(resp.json()["authorization_url"]))
        # Health scopes present.
        assert _HEALTH_SCOPES.issubset(scopes)
        # Calendar not requested and no account to widen from.
        assert _CALENDAR_SCOPE not in scopes

    async def test_unknown_account_hint_treated_as_no_hint(self, app):
        """When account_hint is provided but the account is not found, no widening occurs."""
        from butlers.google_account_registry import GoogleAccountNotFoundError

        _make_app(app)
        with (
            patch(
                _GET_ACCOUNT_PATCH, AsyncMock(side_effect=GoogleAccountNotFoundError("not found"))
            ),
            patch("butlers.api.routers.oauth._check_account_limit", AsyncMock(return_value=None)),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(
                    "/api/oauth/google/start",
                    params={
                        "redirect": "false",
                        "scope_set": "health",
                        "account_hint": "unknown@example.com",
                    },
                )
        # Must succeed (not 400) — unknown account hint is treated as a new account.
        assert resp.status_code == 200
        scopes = set(_extract_scope_param(resp.json()["authorization_url"]))
        # Health scopes are present (the requested set).
        assert _HEALTH_SCOPES.issubset(scopes)
        # No calendar without widening.
        assert _CALENDAR_SCOPE not in scopes

    async def test_no_scope_set_backward_compat_not_affected(self, app):
        """Omitting scope_set is not affected by scope-widening logic at all."""
        _make_app(app)
        existing_granted = [_CALENDAR_SCOPE, _DRIVE_SCOPE]
        mock_account = _make_mock_account(existing_granted)
        # Even if get_google_account returns an account, no widening on default path.
        with patch(_GET_ACCOUNT_PATCH, AsyncMock(return_value=mock_account)):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(
                    "/api/oauth/google/start",
                    params={"redirect": "false", "account_hint": "user@example.com"},
                )
        assert resp.status_code == 200
        scopes = set(_extract_scope_param(resp.json()["authorization_url"]))
        # Default composition must not include health scopes.
        assert _HEALTH_SCOPES.isdisjoint(scopes)


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
# Google Health contact_info registration (bu-k5l35.2)
# ---------------------------------------------------------------------------


class TestGoogleHealthContactInfoRegistration:
    """Covers the pre-registration contract from connector-google-health spec.

    When ``scope_set=health`` is granted, the callback upserts a
    ``public.contact_info(type='google_health', value=<google_user_id>)``
    row on the owner entity's contact so the Switchboard can resolve
    ``sender.identity`` without creating a temp contact.
    """

    async def test_upsert_contact_info_calls_on_conflict_do_nothing(self):
        """The upsert uses ON CONFLICT (type, value) DO NOTHING for idempotency."""
        from butlers.api.routers.oauth import _register_google_health_contact_info

        conn = AsyncMock()
        owner_entity_id = uuid.uuid4()
        owner_contact_id = uuid.uuid4()

        async def _fetchval(query, *args):
            if "FROM public.entities" in query:
                return owner_entity_id
            if "FROM public.contacts" in query:
                return owner_contact_id
            return None

        conn.fetchval.side_effect = _fetchval
        conn.execute = AsyncMock()

        @asynccontextmanager
        async def _acquire():
            yield conn

        @asynccontextmanager
        async def _txn():
            yield

        conn.transaction = lambda: _txn()

        pool = MagicMock()
        pool.acquire = _acquire

        await _register_google_health_contact_info(pool, google_user_id="owner@example.com")

        # Verify the INSERT was executed with ON CONFLICT DO NOTHING clause.
        assert conn.execute.await_count == 1
        sql = conn.execute.await_args.args[0]
        assert "INSERT INTO public.contact_info" in sql
        assert "ON CONFLICT" in sql
        assert "DO NOTHING" in sql
        # Values: contact_id, google_user_id
        assert conn.execute.await_args.args[1] == owner_contact_id
        assert conn.execute.await_args.args[2] == "owner@example.com"

    async def test_upsert_skipped_when_no_owner_entity(self):
        """No-op when owner entity not yet bootstrapped — does not raise."""
        from butlers.api.routers.oauth import _register_google_health_contact_info

        conn = AsyncMock()
        conn.fetchval.return_value = None  # no owner entity
        conn.execute = AsyncMock()

        @asynccontextmanager
        async def _acquire():
            yield conn

        @asynccontextmanager
        async def _txn():
            yield

        conn.transaction = lambda: _txn()

        pool = MagicMock()
        pool.acquire = _acquire

        await _register_google_health_contact_info(pool, google_user_id="owner@example.com")
        # No INSERT performed.
        assert conn.execute.await_count == 0


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


# ---------------------------------------------------------------------------
# Google Health test-mode metadata flag (unit helpers)
# ---------------------------------------------------------------------------


class TestHealthTestModeHelpers:
    """Unit tests for _is_google_health_test_mode and _has_health_scope."""

    def test_test_mode_off_by_default(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_TEST_MODE", raising=False)
        assert _is_google_health_test_mode() is False

    @pytest.mark.parametrize("val", ["1", "true", "True", "TRUE", "yes", "YES", "on", "ON"])
    def test_test_mode_truthy_values(self, monkeypatch, val):
        monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_TEST_MODE", val)
        assert _is_google_health_test_mode() is True

    @pytest.mark.parametrize("val", ["0", "false", "no", ""])
    def test_test_mode_falsy_values(self, monkeypatch, val):
        monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_TEST_MODE", val)
        assert _is_google_health_test_mode() is False

    def test_has_health_scope_googlehealth(self):
        assert _has_health_scope("https://www.googleapis.com/auth/googlehealth.readings") is True

    def test_has_health_scope_fitness(self):
        assert _has_health_scope("https://www.googleapis.com/auth/fitness.activity.read") is True

    def test_has_health_scope_not_present(self):
        scope = "https://www.googleapis.com/auth/gmail.readonly openid email"
        assert _has_health_scope(scope) is False

    def test_has_health_scope_none(self):
        assert _has_health_scope(None) is False

    def test_has_health_scope_empty(self):
        assert _has_health_scope("") is False


# ---------------------------------------------------------------------------
# OAuth callback — Google Health test-mode metadata writes
# ---------------------------------------------------------------------------

_HEALTH_SCOPE = "https://www.googleapis.com/auth/fitness.activity.read openid email"
_NON_HEALTH_SCOPE = "https://www.googleapis.com/auth/gmail.readonly openid email"

_FAKE_HEALTH_TOKEN = {
    "access_token": "ya29.fake-health",
    "refresh_token": "1//fake-health-refresh",
    "scope": _HEALTH_SCOPE,
    "token_type": "Bearer",
    "expires_in": 3600,
}

_GET_ACCOUNT_PATCH = "butlers.api.routers.oauth.get_google_account"
_SET_META_PATCH = "butlers.api.routers.oauth._set_account_health_test_mode"


def _make_callback_app_with_account(app, *, entity_id: uuid.UUID):
    """Wire app so get_google_account returns a mock with the given entity_id."""
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


class TestHealthTestModeCallback:
    async def test_sets_flag_when_health_scope_and_test_mode(self, app, monkeypatch):
        """Callback sets google_health_test_mode when health scope granted + test mode active."""
        monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_TEST_MODE", "true")
        entity_id = uuid.uuid4()
        fake_account = MagicMock()
        fake_account.entity_id = entity_id

        app, _pool = _make_callback_app_with_account(app, entity_id=entity_id)
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
                    "/api/oauth/google/callback",
                    params={"code": "auth-code", "state": state},
                )
        assert resp.status_code == 200
        mock_set_meta.assert_called_once_with(_pool, entity_id=entity_id)

    async def test_does_not_set_flag_when_prod_mode(self, app, monkeypatch):
        """Callback does NOT set flag when health scope granted but client is NOT in test mode."""
        monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_TEST_MODE", raising=False)
        entity_id = uuid.uuid4()
        fake_account = MagicMock()
        fake_account.entity_id = entity_id

        app, _pool = _make_callback_app_with_account(app, entity_id=entity_id)
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
                    "/api/oauth/google/callback",
                    params={"code": "auth-code", "state": state},
                )
        assert resp.status_code == 200
        mock_set_meta.assert_not_called()

    async def test_does_not_set_flag_when_no_health_scope(self, app, monkeypatch):
        """Callback does NOT set flag when health scope not in grant, even in test mode."""
        monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_TEST_MODE", "true")
        entity_id = uuid.uuid4()
        fake_account = MagicMock()
        fake_account.entity_id = entity_id

        non_health_token = {**_FAKE_HEALTH_TOKEN, "scope": _NON_HEALTH_SCOPE}

        app, _pool = _make_callback_app_with_account(app, entity_id=entity_id)
        state = _generate_state()
        _store_state(state)

        with (
            patch(_EXCHANGE_PATCH, AsyncMock(return_value=non_health_token)),
            patch(_USERINFO_PATCH, AsyncMock(return_value=_FAKE_USERINFO)),
            patch(_GET_ACCOUNT_PATCH, AsyncMock(return_value=fake_account)),
            patch(_SET_META_PATCH, AsyncMock()) as mock_set_meta,
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(
                    "/api/oauth/google/callback",
                    params={"code": "auth-code", "state": state},
                )
        assert resp.status_code == 200
        mock_set_meta.assert_not_called()

    async def test_idempotent_second_callback(self, app, monkeypatch):
        """Running the callback a second time (same account) calls set_meta again — idempotent."""
        monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_TEST_MODE", "true")
        entity_id = uuid.uuid4()
        fake_account = MagicMock()
        fake_account.entity_id = entity_id

        app, _pool = _make_callback_app_with_account(app, entity_id=entity_id)

        with (
            patch(_EXCHANGE_PATCH, AsyncMock(return_value=_FAKE_HEALTH_TOKEN)),
            patch(_USERINFO_PATCH, AsyncMock(return_value=_FAKE_USERINFO)),
            patch(_GET_ACCOUNT_PATCH, AsyncMock(return_value=fake_account)),
            patch(_SET_META_PATCH, AsyncMock()) as mock_set_meta,
        ):
            for _ in range(2):
                state = _generate_state()
                _store_state(state)
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    resp = await client.get(
                        "/api/oauth/google/callback",
                        params={"code": "auth-code", "state": state},
                    )
                assert resp.status_code == 200

        # Called twice (once per callback invocation), and the SQL itself is idempotent.
        assert mock_set_meta.call_count == 2
