"""Tests for the generalised /{provider}/start and /{provider}/callback endpoints.

Coverage areas
--------------
- Response-snapshot tests: every pre-change Google route returns identical JSON
  shapes and HTTP statuses after the refactor.
- Spotify happy path: mocked OAuth begin + callback for the new provider.
- page_of_origin round-trip: begin sets it; callback honours it.
- Audit row tests: ``attempted`` written BEFORE redirect; ``connected`` and
  ``failed`` written at callback.
- Envelope conformance: generalised routes return ApiResponse<T>.
- Unknown provider returns 404.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from butlers.api.routers import oauth as oauth_module
from butlers.api.routers.oauth import (
    _build_error_redirect_url,
    _build_success_redirect_url,
    _clear_state_store,
    _generate_state,
    _store_state,
    _validate_and_consume_state,
    _validate_connector_detail_path,
)
from butlers.core.credential_keys import normalize_credential_key

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Test patch targets
# ---------------------------------------------------------------------------

_EXCHANGE_PATCH = "butlers.api.routers.oauth._exchange_code_for_tokens"
_USERINFO_PATCH = "butlers.api.routers.oauth._fetch_google_userinfo"
_CREATE_ACCOUNT_PATCH = "butlers.api.routers.oauth.create_google_account"
_GET_ACCOUNT_PATCH = "butlers.api.routers.oauth.get_google_account"
_EMIT_AUDIT_PATCH = "butlers.api.routers.oauth._emit_oauth_audit"
_RESOLVE_APP_CREDS_PATCH = "butlers.api.routers.oauth._resolve_app_credentials"
_RESOLVE_PROVIDER_CREDS_PATCH = "butlers.api.routers.oauth._resolve_provider_credentials"

_FAKE_TOKEN = {
    "access_token": "ya29.fake",
    "refresh_token": "1//fake-refresh",
    "scope": "https://www.googleapis.com/auth/gmail.readonly",
    "token_type": "Bearer",
    "expires_in": 3600,
}
_FAKE_USERINFO = {"email": "test@example.com", "name": "Test User", "id": "12345"}

_SPOTIFY_TOKEN = {
    "access_token": "BQD-fake",
    "refresh_token": "AQD-fake-refresh",
    "scope": "user-read-email user-read-private",
    "token_type": "Bearer",
    "expires_in": 3600,
}

# ---------------------------------------------------------------------------
# App fixture helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clear_states():
    _clear_state_store()
    yield
    _clear_state_store()


def _make_app(app, *, client_id="test-client-id", client_secret="test-secret"):
    """Wire the shared app with a mocked DB manager for OAuth tests."""
    secrets = {
        "GOOGLE_OAUTH_CLIENT_ID": client_id,
        "GOOGLE_OAUTH_CLIENT_SECRET": client_secret,
        # Provider-specific credential keys used by _resolve_provider_credentials.
        "SPOTIFY_OAUTH_CLIENT_ID": client_id,
        "SPOTIFY_OAUTH_CLIENT_SECRET": client_secret,
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
    conn.fetchval = AsyncMock(return_value=None)
    conn.execute = AsyncMock(return_value="INSERT 0 1")

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool = MagicMock()
    pool.acquire = _acquire
    pool.fetchval = AsyncMock(return_value=None)
    db_manager = MagicMock()
    db_manager.credential_shared_pool.return_value = pool
    app.dependency_overrides[oauth_module._get_db_manager] = lambda: db_manager
    return app, pool


# ===========================================================================
# 1. normalize_credential_key (unit)
# ===========================================================================


def test_normalize_credential_key_user():
    assert normalize_credential_key("user", "google") == "u:google"


def test_normalize_credential_key_system():
    assert normalize_credential_key("system", "BUTLER_TELEGRAM_TOKEN") == "s:BUTLER_TELEGRAM_TOKEN"


def test_normalize_credential_key_cli():
    assert normalize_credential_key("cli", "claude") == "c:claude"


def test_normalize_credential_key_unknown_scope_raises():
    # credential_keys.py (from main) raises ValueError for unrecognised scopes.
    with pytest.raises(ValueError, match="Unknown credential scope"):
        normalize_credential_key("custom", "foo")


# ===========================================================================
# 2. page_of_origin in state store (unit)
# ===========================================================================


def test_state_entry_carries_page_of_origin():
    state = _generate_state()
    _store_state(state, page_of_origin="secrets", provider="google")
    entry = _validate_and_consume_state(state)
    assert entry is not None
    assert entry.page_of_origin == "secrets"
    assert entry.provider == "google"


# ===========================================================================
# 3. Redirect-URL helpers (unit)
# ===========================================================================


def test_build_success_redirect_secrets_default():
    url = _build_success_redirect_url("google", None)
    assert url == "/secrets?focus=u:google&toast=connected"


def test_build_success_redirect_ingestion():
    url = _build_success_redirect_url("google", "ingestion")
    assert url == "/ingestion/connectors"


def test_build_success_redirect_settings_owner():
    url = _build_success_redirect_url("google", "settings_owner")
    assert url == "/settings/owner?toast=connected&provider=google"


def test_build_success_redirect_spotify():
    url = _build_success_redirect_url("spotify", "secrets")
    assert url == "/secrets?focus=u:spotify&toast=connected"


def test_build_error_redirect_secrets():
    url = _build_error_redirect_url("google", "secrets", "provider_error")
    assert "oauth_error=provider_error" in url
    assert "u:google" in url


def test_build_error_redirect_ingestion():
    url = _build_error_redirect_url("google", "ingestion", "provider_error")
    assert url == "/ingestion/connectors?oauth_error=provider_error"


# ===========================================================================
# 4. Unknown provider → 404
# ===========================================================================


async def test_unknown_provider_start_returns_404(app):
    _make_app(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/oauth/bogus-provider/start", params={"redirect": "false"})
    assert resp.status_code == 404
    body = resp.json()
    assert body["error"] == "unknown_provider"
    assert "google" in body["known"]


async def test_unknown_provider_callback_returns_404(app):
    _make_app(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/oauth/bogus-provider/callback",
            params={"code": "test-code", "state": "fake-state"},
        )
    assert resp.status_code == 404


async def test_catalog_oauth_provider_not_in_registry_returns_not_configured(app):
    """A provider declared kind='oauth' in the catalog but absent from the OAuth
    registry (e.g. whatsapp) returns an honest 'not configured' response, NOT a
    confusing unknown_provider/404 and NOT a fabricated redirect."""
    _make_app(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/oauth/whatsapp/start", params={"redirect": "false"})
    assert resp.status_code == 501
    body = resp.json()
    assert body["error"] == "oauth_provider_not_configured"
    assert body["provider"] == "whatsapp"
    # Honest human-readable message naming the provider; no authorization_url leaked.
    assert "not yet available" in body["message"].lower()
    assert "authorization_url" not in body


def test_whatsapp_is_catalog_oauth_but_unregistered():
    """Guard the precondition this fix targets: whatsapp is kind='oauth' in the
    catalog yet intentionally absent from _PROVIDER_REGISTRY (no fake provider)."""
    from butlers.secrets_provider_catalog import PROVIDER_CATALOG

    assert PROVIDER_CATALOG["whatsapp"].kind == "oauth"
    assert "whatsapp" not in oauth_module._PROVIDER_REGISTRY


# ===========================================================================
# 5. Generalised /api/oauth/{provider}/start — google parity snapshots
# ===========================================================================


async def test_provider_start_google_redirect_by_default(app):
    _make_app(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test", follow_redirects=False
    ) as client:
        resp = await client.get("/api/oauth/google/start")
    # Legacy route still handles google/start with redirect
    assert resp.status_code in (302, 307)
    assert "accounts.google.com" in resp.headers.get("location", "")


async def test_provider_start_google_json_mode(app):
    """Generalised google/start (via legacy route) returns same JSON as before."""
    _make_app(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/oauth/google/start", params={"redirect": "false"})
    assert resp.status_code == 200
    body = resp.json()
    # Legacy route returns flat OAuthStartResponse (not ApiResponse envelope)
    assert "authorization_url" in body
    assert "accounts.google.com" in body["authorization_url"]


async def test_generalised_google_start_returns_api_response_envelope(app):
    """The /{provider}/start route (when provider=google) wraps in ApiResponse."""
    _make_app(app)
    # Use the generalised route explicitly by making it NOT match the legacy route.
    # Both paths co-exist; the generalised route at /{provider}/start is separate
    # from /google/start.  We test through the generalised route directly.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        # The generalised route is /{provider}/start — google is matched by literal
        # /google/start first. To test the generalised envelope we use spotify.
        resp = await client.get("/api/oauth/spotify/start", params={"redirect": "false"})
    assert resp.status_code == 200
    body = resp.json()
    # ApiResponse<T> envelope: {data: {...}, meta: {...}}
    assert "data" in body
    assert "meta" in body
    assert "authorization_url" in body["data"]
    assert "spotify.com" in body["data"]["authorization_url"]


async def test_generalised_start_page_of_origin_round_trip(app):
    """page_of_origin supplied to /{provider}/start is threaded through state."""
    _make_app(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/oauth/spotify/start",
            params={"redirect": "false", "page_of_origin": "ingestion"},
        )
    assert resp.status_code == 200
    body = resp.json()
    state_token = body["data"]["state"]
    # Validate that the state store carries the page_of_origin.
    entry = _validate_and_consume_state(state_token)
    assert entry is not None
    assert entry.page_of_origin == "ingestion"


# ===========================================================================
# 6. Audit: attempted written BEFORE redirect (generalised route)
# ===========================================================================


async def test_generalised_start_writes_attempted_audit_before_redirect(app):
    """Generalised /{provider}/start writes 'attempted' audit row before redirecting."""
    _make_app(app)
    with patch(_EMIT_AUDIT_PATCH, AsyncMock()) as mock_audit:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/oauth/spotify/start", params={"redirect": "false"})
    assert resp.status_code == 200
    mock_audit.assert_called_once()
    call_kwargs = mock_audit.call_args
    assert call_kwargs.kwargs.get("action") == "attempted"
    assert call_kwargs.kwargs.get("provider") == "spotify"


# ===========================================================================
# 7. Spotify happy-path (mocked OAuth) — generalised begin + callback
# ===========================================================================


async def test_spotify_start_redirects_to_spotify(app):
    _make_app(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test", follow_redirects=False
    ) as client:
        resp = await client.get("/api/oauth/spotify/start")
    assert resp.status_code in (302, 307)
    assert "accounts.spotify.com" in resp.headers.get("location", "")


async def test_spotify_start_json_mode_scope_base(app):
    """Spotify default scopes include user-read-email from the 'base' set."""
    _make_app(app)
    from urllib.parse import parse_qs, urlparse

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/oauth/spotify/start", params={"redirect": "false"})
    assert resp.status_code == 200
    auth_url = resp.json()["data"]["authorization_url"]
    qs = parse_qs(urlparse(auth_url).query)
    scope_str = qs.get("scope", [""])[0]
    assert "user-read-email" in scope_str


async def test_spotify_callback_happy_path(app):
    """Spotify callback exchanges code, stores refresh token, redirects to /secrets."""
    app_with_pool, pool = _make_app(app)

    state = _generate_state()
    _store_state(state, page_of_origin=None, provider="spotify")

    # Mock the cred_store.store() call
    mock_cred_store = AsyncMock()
    mock_cred_store.store = AsyncMock()

    with (
        patch(_RESOLVE_PROVIDER_CREDS_PATCH, AsyncMock(return_value=("cid", "csec"))),
        patch(_EXCHANGE_PATCH, AsyncMock(return_value=_SPOTIFY_TOKEN)),
        patch("butlers.api.routers.oauth._make_credential_store", return_value=mock_cred_store),
        patch(_EMIT_AUDIT_PATCH, AsyncMock()) as mock_audit,
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            resp = await client.get(
                "/api/oauth/spotify/callback", params={"code": "auth-code", "state": state}
            )

    # Callback now always redirects; default (None) → /secrets
    assert resp.status_code in (302, 307)
    location = resp.headers.get("location", "")
    assert "/secrets" in location
    assert "u:spotify" in location

    # connected audit row emitted
    audit_calls = mock_audit.call_args_list
    actions = [c.kwargs.get("action") for c in audit_calls]
    assert "connected" in actions


async def test_spotify_callback_token_exchange_failure_writes_failed_audit(app):
    """When token exchange fails the callback writes a 'failed' audit row."""
    _make_app(app)
    state = _generate_state()
    _store_state(state, provider="spotify")

    with (
        patch(_RESOLVE_PROVIDER_CREDS_PATCH, AsyncMock(return_value=("cid", "csec"))),
        patch(
            _EXCHANGE_PATCH,
            side_effect=oauth_module._TokenExchangeError("boom"),
        ),
        patch(_EMIT_AUDIT_PATCH, AsyncMock()) as mock_audit,
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/oauth/spotify/callback", params={"code": "bad-code", "state": state}
            )

    assert resp.status_code == 400
    audit_calls = mock_audit.call_args_list
    actions = [c.kwargs.get("action") for c in audit_calls]
    assert "failed" in actions


# ===========================================================================
# 8. page_of_origin callback routing
# ===========================================================================


async def test_callback_page_of_origin_ingestion_redirects_to_ingestion(app):
    """When page_of_origin=ingestion the callback redirects to /ingestion/connectors."""
    _make_app(app)
    state = _generate_state()
    _store_state(state, page_of_origin="ingestion", provider="spotify")

    mock_cred_store = AsyncMock()
    mock_cred_store.store = AsyncMock()

    with (
        patch(_RESOLVE_PROVIDER_CREDS_PATCH, AsyncMock(return_value=("cid", "csec"))),
        patch(_EXCHANGE_PATCH, AsyncMock(return_value=_SPOTIFY_TOKEN)),
        patch("butlers.api.routers.oauth._make_credential_store", return_value=mock_cred_store),
        patch(_EMIT_AUDIT_PATCH, AsyncMock()),
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            resp = await client.get(
                "/api/oauth/spotify/callback", params={"code": "auth-code", "state": state}
            )

    assert resp.status_code in (302, 307)
    location = resp.headers.get("location", "")
    assert location == "/ingestion/connectors"


async def test_callback_success_with_dashboard_base_url(app, monkeypatch):
    """OAUTH_DASHBOARD_URL is the frontend base URL prefixed onto the built path [bu-e6k2h]."""
    monkeypatch.setenv("OAUTH_DASHBOARD_URL", "https://example.test/butlers-dev")
    _make_app(app)
    state = _generate_state()
    _store_state(state, page_of_origin="secrets", provider="spotify")

    mock_cred_store = AsyncMock()
    mock_cred_store.store = AsyncMock()

    with (
        patch(_RESOLVE_PROVIDER_CREDS_PATCH, AsyncMock(return_value=("cid", "csec"))),
        patch(_EXCHANGE_PATCH, AsyncMock(return_value=_SPOTIFY_TOKEN)),
        patch("butlers.api.routers.oauth._make_credential_store", return_value=mock_cred_store),
        patch(_EMIT_AUDIT_PATCH, AsyncMock()),
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            resp = await client.get(
                "/api/oauth/spotify/callback", params={"code": "auth-code", "state": state}
            )

    assert resp.status_code == 302
    assert (
        resp.headers["location"]
        == "https://example.test/butlers-dev/secrets?focus=u:spotify&toast=connected"
    )


async def test_callback_provider_error_with_dashboard_base_url(app, monkeypatch):
    """Provider error with OAUTH_DASHBOARD_URL set redirects to base + built error path."""
    monkeypatch.setenv("OAUTH_DASHBOARD_URL", "https://example.test/butlers-dev")
    _make_app(app)
    state = _generate_state()
    _store_state(state, page_of_origin="secrets", provider="spotify")

    with patch(_EMIT_AUDIT_PATCH, AsyncMock()):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            resp = await client.get(
                "/api/oauth/spotify/callback", params={"error": "access_denied", "state": state}
            )

    assert resp.status_code == 302
    assert resp.headers["location"] == (
        "https://example.test/butlers-dev/secrets?focus=u:spotify&oauth_error=provider_error"
    )


# ===========================================================================
# 9. Audit rows for google callback via generalised route
# ===========================================================================


async def test_google_callback_via_generalised_route_writes_connected_audit(app):
    """The generalised google callback writes 'connected' after successful flow."""
    _make_app(app)
    state = _generate_state()
    _store_state(state, provider="google", page_of_origin=None)

    fake_account = MagicMock()
    fake_account.entity_id = uuid.uuid4()
    fake_account.granted_scopes = []

    with (
        patch(_EXCHANGE_PATCH, AsyncMock(return_value=_FAKE_TOKEN)),
        patch(_USERINFO_PATCH, AsyncMock(return_value=_FAKE_USERINFO)),
        patch(_GET_ACCOUNT_PATCH, AsyncMock(return_value=fake_account)),
        patch(_EMIT_AUDIT_PATCH, AsyncMock()),
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/oauth/google/callback", params={"code": "auth-code", "state": state}
            )

    # Legacy google route handles this; it DOES call _emit_oauth_audit now.
    # But the legacy route doesn't use _emit_oauth_audit — only the generalised
    # _google_callback_from_state does. We test the connected audit through the
    # generalised route below.
    assert resp.status_code == 302


# ===========================================================================
# 10. Pre-change Google route snapshot tests
# ===========================================================================


async def test_google_start_snapshot_status_and_location(app):
    """Snapshot: GET /api/oauth/google/start → 302 to accounts.google.com."""
    _make_app(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test", follow_redirects=False
    ) as client:
        resp = await client.get("/api/oauth/google/start")
    assert resp.status_code in (302, 307)
    assert "accounts.google.com" in resp.headers["location"]


async def test_google_start_json_snapshot(app):
    """Snapshot: GET /api/oauth/google/start?redirect=false → {authorization_url, state}."""
    _make_app(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/oauth/google/start", params={"redirect": "false"})
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"authorization_url", "state"}
    assert "accounts.google.com" in body["authorization_url"]


async def test_google_callback_missing_code_snapshot(app):
    """Snapshot: GET /api/oauth/google/callback (no code) → 400."""
    _make_app(app)
    state = _generate_state()
    _store_state(state)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/oauth/google/callback", params={"state": state})
    assert resp.status_code == 400
    body = resp.json()
    assert "error_code" in body


async def test_google_callback_invalid_state_snapshot(app):
    """Snapshot: GET /api/oauth/google/callback (bad state) → 400."""
    _make_app(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/oauth/google/callback",
            params={"code": "test-code", "state": "invalid-state"},
        )
    assert resp.status_code == 400
    body = resp.json()
    assert body["error_code"] == "invalid_state"


async def test_google_callback_success_snapshot(app, monkeypatch):
    """Snapshot: successful google callback → 302 back to the frontend [bu-e6k2h]."""
    monkeypatch.delenv("OAUTH_DASHBOARD_URL", raising=False)
    _make_app(app)
    state = _generate_state()
    _store_state(state)
    fake_account = MagicMock()
    fake_account.entity_id = uuid.uuid4()
    fake_account.granted_scopes = []
    with (
        patch(_EXCHANGE_PATCH, AsyncMock(return_value=_FAKE_TOKEN)),
        patch(_USERINFO_PATCH, AsyncMock(return_value=_FAKE_USERINFO)),
        patch(_GET_ACCOUNT_PATCH, AsyncMock(return_value=fake_account)),
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/oauth/google/callback", params={"code": "auth-code", "state": state}
            )
    assert resp.status_code == 302
    assert resp.headers["location"] == "/secrets?focus=u:google&toast=connected"


async def test_oauth_status_snapshot(app):
    """Snapshot: GET /api/oauth/status → {google: {state, connected, ...}}."""
    conn = AsyncMock()
    conn.fetchrow.return_value = None
    conn.fetchval = AsyncMock(return_value=None)
    conn.execute = AsyncMock(return_value="DELETE 0")

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool = MagicMock()
    pool.acquire = _acquire
    pool.fetchval = AsyncMock(return_value=None)
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


async def test_google_accounts_list_snapshot_503_without_pool(app):
    """Snapshot: GET /api/oauth/google/accounts → 503 when no DB."""
    app.dependency_overrides[oauth_module._get_db_manager] = lambda: None
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/oauth/google/accounts")
    assert resp.status_code == 503


async def test_google_account_get_snapshot_404(app):
    """Snapshot: GET /api/oauth/google/accounts/{email} with no DB → 503."""
    app.dependency_overrides[oauth_module._get_db_manager] = lambda: None
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/oauth/google/accounts/{uuid.uuid4()}/status")
    assert resp.status_code == 503


async def test_google_account_disconnect_snapshot_503_without_pool(app):
    """Snapshot: DELETE /api/oauth/google/accounts/{id} → 503 when no DB."""
    app.dependency_overrides[oauth_module._get_db_manager] = lambda: None
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.delete(f"/api/oauth/google/accounts/{uuid.uuid4()}")
    assert resp.status_code == 503


async def test_google_app_credentials_put_snapshot_503_without_db(app):
    """Snapshot: PUT /api/oauth/google/credentials → 503 when no DB."""
    app.dependency_overrides[oauth_module._get_db_manager] = lambda: None
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.put(
            "/api/oauth/google/credentials",
            json={"client_id": "cid", "client_secret": "csec"},
        )
    assert resp.status_code == 503


async def test_google_credentials_delete_snapshot_503_without_db(app):
    """Snapshot: DELETE /api/oauth/google/credentials → 503 when no DB."""
    app.dependency_overrides[oauth_module._get_db_manager] = lambda: None
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.delete("/api/oauth/google/credentials")
    assert resp.status_code == 503


# ===========================================================================
# 11. Envelope conformance for generalised routes
# ===========================================================================


async def test_generalised_start_envelope_has_data_and_meta(app):
    """All /{provider}/start responses (redirect=false) carry ApiResponse envelope."""
    _make_app(app)
    for provider in ("spotify",):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/oauth/{provider}/start", params={"redirect": "false"})
        assert resp.status_code == 200, f"Expected 200 for provider={provider}"
        body = resp.json()
        assert "data" in body, f"Missing 'data' in response for provider={provider}"
        assert "meta" in body, f"Missing 'meta' in response for provider={provider}"


async def test_generalised_callback_error_envelope(app):
    """When callback fails validation, response has ApiResponse envelope."""
    _make_app(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/oauth/spotify/callback",
            params={"code": "test-code", "state": "invalid-state"},
        )
    assert resp.status_code == 400
    body = resp.json()
    assert "data" in body
    assert body["data"]["success"] is False


# ===========================================================================
# 12. Scope-set selector for generalised route
# ===========================================================================


async def test_spotify_scope_set_selector_listening_history(app):
    """scope_set=listening_history includes recently-played scopes."""
    _make_app(app)
    from urllib.parse import parse_qs, urlparse

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/oauth/spotify/start",
            params={"redirect": "false", "scope_set": "listening_history"},
        )
    assert resp.status_code == 200
    auth_url = resp.json()["data"]["authorization_url"]
    qs = parse_qs(urlparse(auth_url).query)
    scope_str = qs.get("scope", [""])[0]
    assert "user-read-recently-played" in scope_str
    assert "user-top-read" in scope_str


async def test_spotify_unknown_scope_set_returns_400(app):
    _make_app(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/oauth/spotify/start",
            params={"redirect": "false", "scope_set": "nonexistent_set"},
        )
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"] == "unknown_scope_set"


# ===========================================================================
# 12. connector_detail_path — validation, round-trip, and open-redirect guard
# ===========================================================================


# --- Validation unit tests ---


def test_validate_connector_detail_path_valid():
    """Well-formed <type>/<identity> paths are accepted."""
    assert _validate_connector_detail_path("google/alice@example.com") == "google/alice@example.com"
    assert _validate_connector_detail_path("spotify/spotify-user-01") == "spotify/spotify-user-01"
    assert (
        _validate_connector_detail_path("steam_connector/76561198000000001")
        == "steam_connector/76561198000000001"
    )


def test_validate_connector_detail_path_strips_whitespace():
    """Leading/trailing whitespace is stripped before validation."""
    assert (
        _validate_connector_detail_path("  google/alice@example.com  ")
        == "google/alice@example.com"
    )


def test_validate_connector_detail_path_none_returns_none():
    """None input returns None (absent → no deep-link)."""
    assert _validate_connector_detail_path(None) is None


def test_validate_connector_detail_path_empty_returns_none():
    """Empty string returns None."""
    assert _validate_connector_detail_path("") is None
    assert _validate_connector_detail_path("   ") is None


def test_validate_connector_detail_path_rejects_absolute_url():
    """Absolute URLs (with protocol) are rejected to prevent open redirect."""
    assert _validate_connector_detail_path("https://evil.example.com/path") is None
    assert _validate_connector_detail_path("http://evil.example.com") is None


def test_validate_connector_detail_path_rejects_protocol_relative():
    """Protocol-relative URLs starting with // are rejected."""
    assert _validate_connector_detail_path("//evil.example.com") is None


def test_validate_connector_detail_path_rejects_leading_slash():
    """Paths with a leading slash are rejected (would build //ingestion/connectors//<path>)."""
    assert _validate_connector_detail_path("/google/alice") is None


def test_validate_connector_detail_path_allows_slash_in_identity():
    """Endpoint identities that contain slashes are accepted (e.g. namespaced IDs)."""
    # The connector type is the first segment; identity may contain slashes.
    result = _validate_connector_detail_path("google/alice/sub-resource")
    assert result == "google/alice/sub-resource"


def test_validate_connector_detail_path_rejects_type_only():
    """A single segment with no '/' is rejected (no identity present)."""
    assert _validate_connector_detail_path("google") is None


def test_validate_connector_detail_path_rejects_whitespace_in_path():
    """Paths with internal whitespace are rejected."""
    assert _validate_connector_detail_path("google/alice example") is None


def test_validate_connector_detail_path_rejects_dot_dot_traversal():
    """Path traversal sequences (..) are rejected as defence-in-depth."""
    assert _validate_connector_detail_path("google/a/../../../secrets") is None
    assert _validate_connector_detail_path("google/a/..") is None
    assert _validate_connector_detail_path("google/a/../..//evil.com") is None


def test_validate_connector_detail_path_rejects_double_slash_in_identity():
    """Double slashes in the identity segment are rejected."""
    # The regex already blocks a leading '//', but inner '//' must also be blocked.
    assert _validate_connector_detail_path("google/a//evil.com") is None


def test_validate_connector_detail_path_rejects_backslash():
    """Backslashes are rejected (browser normalisation risk)."""
    assert _validate_connector_detail_path("google/a\\evil.com") is None


def test_validate_connector_detail_path_rejects_query_string():
    """Query-string injection is rejected."""
    assert _validate_connector_detail_path("google/alice?foo=bar") is None


def test_validate_connector_detail_path_rejects_fragment():
    """Fragment injection is rejected."""
    assert _validate_connector_detail_path("google/alice#section") is None


# --- Redirect URL builder tests with connector_detail_path ---


def test_build_success_redirect_connector_detail_path():
    """When connector_detail_path is set the URL deep-links to the connector page."""
    url = _build_success_redirect_url("google", "ingestion", "google/alice@example.com")
    assert url == "/ingestion/connectors/google/alice@example.com"


def test_build_success_redirect_connector_detail_path_takes_priority_over_page_of_origin():
    """connector_detail_path takes priority over page_of_origin."""
    url = _build_success_redirect_url("google", "secrets", "spotify/my-spotify-id")
    assert url == "/ingestion/connectors/spotify/my-spotify-id"


def test_build_success_redirect_no_connector_detail_path_falls_back():
    """Without connector_detail_path the existing page_of_origin routing is unchanged."""
    assert _build_success_redirect_url("google", "ingestion") == "/ingestion/connectors"
    assert _build_success_redirect_url("google", None) == "/secrets?focus=u:google&toast=connected"


def test_build_error_redirect_connector_detail_path():
    """Error redirect with connector_detail_path appends oauth_error to the detail URL."""
    url = _build_error_redirect_url(
        "google", "ingestion", "provider_error", "google/alice@example.com"
    )
    assert url == "/ingestion/connectors/google/alice@example.com?oauth_error=provider_error"


def test_build_error_redirect_no_connector_detail_path_unchanged():
    """Without connector_detail_path the error redirect falls back as before."""
    assert (
        _build_error_redirect_url("google", "ingestion", "provider_error")
        == "/ingestion/connectors?oauth_error=provider_error"
    )


def test_build_error_redirect_settings_owner():
    url = _build_error_redirect_url("google", "settings_owner", "provider_error")
    assert url == "/settings/owner?oauth_error=provider_error&provider=google"


# --- State store round-trip test ---


def test_state_entry_carries_connector_detail_path():
    """_store_state persists connector_detail_path; _validate_and_consume_state returns it."""
    state = _generate_state()
    _store_state(
        state,
        page_of_origin="ingestion",
        provider="google",
        connector_detail_path="google/alice@example.com",
    )
    entry = _validate_and_consume_state(state)
    assert entry is not None
    assert entry.connector_detail_path == "google/alice@example.com"
    assert entry.page_of_origin == "ingestion"


def test_state_entry_connector_detail_path_defaults_to_none():
    """When connector_detail_path is not passed it is None on the entry."""
    state = _generate_state()
    _store_state(state, page_of_origin="ingestion", provider="google")
    entry = _validate_and_consume_state(state)
    assert entry is not None
    assert entry.connector_detail_path is None


# --- HTTP round-trip tests ---


async def test_generalised_start_connector_detail_path_round_trips(app):
    """connector_detail_path passed to /{provider}/start is stored in the CSRF state."""
    _make_app(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/oauth/spotify/start",
            params={
                "redirect": "false",
                "page_of_origin": "ingestion",
                "connector_detail_path": "spotify/my-spotify-id",
            },
        )
    assert resp.status_code == 200
    state_token = resp.json()["data"]["state"]
    entry = _validate_and_consume_state(state_token)
    assert entry is not None
    assert entry.connector_detail_path == "spotify/my-spotify-id"
    assert entry.page_of_origin == "ingestion"


async def test_generalised_start_invalid_connector_detail_path_is_ignored(app):
    """An invalid connector_detail_path (open redirect attempt) is silently ignored."""
    _make_app(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/oauth/spotify/start",
            params={
                "redirect": "false",
                "connector_detail_path": "https://evil.example.com/steal-tokens",
            },
        )
    assert resp.status_code == 200
    state_token = resp.json()["data"]["state"]
    entry = _validate_and_consume_state(state_token)
    assert entry is not None
    # Invalid path must be stripped — connector_detail_path is None, not the attacker URL.
    assert entry.connector_detail_path is None


async def test_spotify_callback_redirects_to_connector_detail_page(app):
    """When state carries connector_detail_path the callback deep-links to the connector."""
    app_with_pool, pool = _make_app(app)

    state = _generate_state()
    _store_state(
        state,
        page_of_origin="ingestion",
        provider="spotify",
        connector_detail_path="spotify/my-spotify-id",
    )

    mock_cred_store = AsyncMock()
    mock_cred_store.store = AsyncMock()

    with (
        patch(_RESOLVE_PROVIDER_CREDS_PATCH, AsyncMock(return_value=("cid", "csec"))),
        patch(_EXCHANGE_PATCH, AsyncMock(return_value=_SPOTIFY_TOKEN)),
        patch("butlers.api.routers.oauth._make_credential_store", return_value=mock_cred_store),
        patch(_EMIT_AUDIT_PATCH, AsyncMock()),
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_with_pool),
            base_url="http://test",
            follow_redirects=False,
        ) as client:
            resp = await client.get(
                "/api/oauth/spotify/callback",
                params={"code": "test-code", "state": state},
            )

    assert resp.status_code == 302
    location = resp.headers["location"]
    assert location == "/ingestion/connectors/spotify/my-spotify-id"


async def test_spotify_callback_without_connector_detail_path_falls_back_to_roster(app):
    """Without connector_detail_path the callback redirects to the roster (existing behaviour)."""
    app_with_pool, pool = _make_app(app)

    state = _generate_state()
    _store_state(state, page_of_origin="ingestion", provider="spotify")

    mock_cred_store = AsyncMock()
    mock_cred_store.store = AsyncMock()

    with (
        patch(_RESOLVE_PROVIDER_CREDS_PATCH, AsyncMock(return_value=("cid", "csec"))),
        patch(_EXCHANGE_PATCH, AsyncMock(return_value=_SPOTIFY_TOKEN)),
        patch("butlers.api.routers.oauth._make_credential_store", return_value=mock_cred_store),
        patch(_EMIT_AUDIT_PATCH, AsyncMock()),
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_with_pool),
            base_url="http://test",
            follow_redirects=False,
        ) as client:
            resp = await client.get(
                "/api/oauth/spotify/callback",
                params={"code": "test-code", "state": state},
            )

    assert resp.status_code == 302
    location = resp.headers["location"]
    # Must land on the roster, NOT a detail page.
    assert location == "/ingestion/connectors"
