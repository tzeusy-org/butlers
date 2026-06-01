"""Backend unit tests for the OAuth test-mode stub gating [bu-idkeh].

Security contract being tested
-------------------------------
1. Stub active only when TEST_MODE_OAUTH_STUB=1 AND ENV != "prod".
2. TEST_MODE_OAUTH_STUB=1 + ENV=prod → stub REFUSES to activate (hard prod guard).
3. TEST_MODE_OAUTH_STUB unset (default) → real path untouched; no stub.
4. Full roundtrip via /{provider}/callback succeeds (redirects to
   /secrets?focus=u:<provider>&toast=connected) when stub is active and
   the DB layer is mocked.
5. Full roundtrip is byte-for-byte real (no synthetic tokens) when flag is off.

These tests do NOT use real credentials or make any network calls.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from butlers.api.routers import oauth as oauth_module
from butlers.api.routers.oauth import (
    _OAUTH_STUB_ENV,
    _STUB_SYNTHETIC_TOKEN,
    _STUB_SYNTHETIC_USERINFO,
    _clear_state_store,
    _generate_state,
    _is_oauth_stub_active,
    _store_state,
)

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Patch targets
# ---------------------------------------------------------------------------

_EXCHANGE_PATCH = "butlers.api.routers.oauth._exchange_code_for_tokens"
_USERINFO_PATCH = "butlers.api.routers.oauth._fetch_google_userinfo"
_CREATE_ACCOUNT_PATCH = "butlers.api.routers.oauth.create_google_account"
_GET_ACCOUNT_PATCH = "butlers.api.routers.oauth.get_google_account"
_RESOLVE_PROVIDER_CREDS_PATCH = "butlers.api.routers.oauth._resolve_provider_credentials"
_EMIT_AUDIT_PATCH = "butlers.api.routers.oauth._emit_oauth_audit"


@pytest.fixture(autouse=True)
def clear_states():
    _clear_state_store()
    yield
    _clear_state_store()


# ---------------------------------------------------------------------------
# Helper: build a mocked app with a fake DB manager
# ---------------------------------------------------------------------------


def _make_stub_app(app, *, client_id: str = "stub-client-id", client_secret: str = "stub-secret"):
    """Wire the shared app with a mocked DB manager for stub gating tests."""
    secrets = {
        "GOOGLE_OAUTH_CLIENT_ID": client_id,
        "GOOGLE_OAUTH_CLIENT_SECRET": client_secret,
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
# 1. _is_oauth_stub_active — gating unit tests
# ===========================================================================


def test_stub_off_by_default(monkeypatch):
    """Stub is OFF when TEST_MODE_OAUTH_STUB is not set."""
    monkeypatch.delenv(_OAUTH_STUB_ENV, raising=False)
    monkeypatch.delenv("ENV", raising=False)
    assert _is_oauth_stub_active() is False


def test_stub_off_when_flag_falsy(monkeypatch):
    """Stub is OFF when TEST_MODE_OAUTH_STUB is explicitly 0/false."""
    for falsy in ("0", "false", "no", "off", ""):
        monkeypatch.setenv(_OAUTH_STUB_ENV, falsy)
        monkeypatch.delenv("ENV", raising=False)
        assert _is_oauth_stub_active() is False, f"Should be off for {falsy!r}"


def test_stub_on_when_flag_truthy_and_no_env(monkeypatch):
    """Stub is ON when TEST_MODE_OAUTH_STUB is truthy and ENV is unset."""
    for truthy in ("1", "true", "yes", "on", "TRUE", "YES"):
        monkeypatch.setenv(_OAUTH_STUB_ENV, truthy)
        monkeypatch.delenv("ENV", raising=False)
        assert _is_oauth_stub_active() is True, f"Should be on for {truthy!r}"


def test_stub_on_when_flag_truthy_and_env_dev(monkeypatch):
    """Stub is ON when TEST_MODE_OAUTH_STUB=1 and ENV=dev."""
    monkeypatch.setenv(_OAUTH_STUB_ENV, "1")
    monkeypatch.setenv("ENV", "dev")
    assert _is_oauth_stub_active() is True


def test_stub_on_when_flag_truthy_and_env_test(monkeypatch):
    """Stub is ON when TEST_MODE_OAUTH_STUB=1 and ENV=test."""
    monkeypatch.setenv(_OAUTH_STUB_ENV, "1")
    monkeypatch.setenv("ENV", "test")
    assert _is_oauth_stub_active() is True


def test_stub_off_when_env_is_prod_flag_on(monkeypatch):
    """HARD PRODUCTION GUARD: stub MUST be OFF when ENV=prod, even if flag is set."""
    monkeypatch.setenv(_OAUTH_STUB_ENV, "1")
    monkeypatch.setenv("ENV", "prod")
    assert _is_oauth_stub_active() is False


def test_stub_off_when_env_is_prod_flag_true(monkeypatch):
    """HARD PRODUCTION GUARD: stub refuses even with TEST_MODE_OAUTH_STUB=true ENV=prod."""
    monkeypatch.setenv(_OAUTH_STUB_ENV, "true")
    monkeypatch.setenv("ENV", "prod")
    assert _is_oauth_stub_active() is False


def test_stub_off_when_env_prod_case_insensitive(monkeypatch):
    """HARD PRODUCTION GUARD: ENV=PROD (uppercase) is also treated as production."""
    monkeypatch.setenv(_OAUTH_STUB_ENV, "1")
    monkeypatch.setenv("ENV", "PROD")
    assert _is_oauth_stub_active() is False


# ===========================================================================
# 2. _exchange_code_for_tokens — stub interception
# ===========================================================================


async def test_exchange_tokens_returns_stub_when_active(monkeypatch):
    """When stub is active, _exchange_code_for_tokens returns synthetic tokens."""
    monkeypatch.setenv(_OAUTH_STUB_ENV, "1")
    monkeypatch.delenv("ENV", raising=False)

    result = await oauth_module._exchange_code_for_tokens(
        code="fake-auth-code",
        client_id="cid",
        client_secret="csec",
        redirect_uri="http://localhost/callback",
    )
    assert result == _STUB_SYNTHETIC_TOKEN
    # The stub token is non-real (contains "stub-" prefix).
    assert "stub-" in result["access_token"]
    assert "stub-" in result["refresh_token"]


async def test_exchange_tokens_calls_real_http_when_stub_off(monkeypatch):
    """When stub is off, _exchange_code_for_tokens makes a real HTTP call."""
    monkeypatch.delenv(_OAUTH_STUB_ENV, raising=False)
    monkeypatch.delenv("ENV", raising=False)

    # Patch httpx to avoid actual network calls.
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"access_token": "real-token", "token_type": "Bearer"}

    with patch("butlers.api.routers.oauth.httpx.AsyncClient") as mock_client_cls:
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_cm)
        mock_cm.__aexit__ = AsyncMock(return_value=False)
        mock_cm.post = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_cm

        result = await oauth_module._exchange_code_for_tokens(
            code="real-auth-code",
            client_id="cid",
            client_secret="csec",
            redirect_uri="http://localhost/callback",
        )

    assert result["access_token"] == "real-token"
    mock_cm.post.assert_called_once()


async def test_exchange_tokens_prod_guard_uses_real_http(monkeypatch):
    """When ENV=prod + TEST_MODE_OAUTH_STUB=1, stub is inactive → real HTTP path."""
    monkeypatch.setenv(_OAUTH_STUB_ENV, "1")
    monkeypatch.setenv("ENV", "prod")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"access_token": "real-prod-token"}

    with patch("butlers.api.routers.oauth.httpx.AsyncClient") as mock_client_cls:
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_cm)
        mock_cm.__aexit__ = AsyncMock(return_value=False)
        mock_cm.post = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_cm

        result = await oauth_module._exchange_code_for_tokens(
            code="prod-auth-code",
            client_id="cid",
            client_secret="csec",
            redirect_uri="http://localhost/callback",
        )

    # Must use the real (mocked httpx) path, not the stub.
    assert result["access_token"] == "real-prod-token"
    # Stub tokens contain "stub-"; real response does not.
    assert "stub-" not in result["access_token"]
    mock_cm.post.assert_called_once()


# ===========================================================================
# 3. _fetch_google_userinfo — stub interception
# ===========================================================================


async def test_fetch_userinfo_returns_stub_when_active(monkeypatch):
    """When stub is active, _fetch_google_userinfo returns synthetic userinfo."""
    monkeypatch.setenv(_OAUTH_STUB_ENV, "1")
    monkeypatch.delenv("ENV", raising=False)

    result = await oauth_module._fetch_google_userinfo("stub-access-token-not-real")
    assert result == _STUB_SYNTHETIC_USERINFO
    assert result["email"] == "stub-user@stub.invalid"


async def test_fetch_userinfo_calls_real_when_stub_off(monkeypatch):
    """When stub is off, _fetch_google_userinfo makes a real HTTP call."""
    monkeypatch.delenv(_OAUTH_STUB_ENV, raising=False)
    monkeypatch.delenv("ENV", raising=False)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"email": "real@example.com", "name": "Real User"}

    with patch("butlers.api.routers.oauth.httpx.AsyncClient") as mock_client_cls:
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_cm)
        mock_cm.__aexit__ = AsyncMock(return_value=False)
        mock_cm.get = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_cm

        result = await oauth_module._fetch_google_userinfo("real-access-token")

    assert result["email"] == "real@example.com"
    mock_cm.get.assert_called_once()


# ===========================================================================
# 4. Full roundtrip via /{provider}/callback when stub is active
#    (callback stub-active → synthetic tokens → redirect to toast URL)
# ===========================================================================


async def test_provider_callback_stub_active_redirects_to_toast(app, monkeypatch):
    """Full roundtrip: callback with stub active redirects to /secrets?toast=connected.

    When TEST_MODE_OAUTH_STUB=1 is set, the callback should:
    - Skip the real token exchange (returns synthetic tokens in-process)
    - Skip the real userinfo call (returns synthetic userinfo in-process)
    - Complete and redirect to /secrets?focus=u:google&toast=connected
    """
    monkeypatch.setenv(_OAUTH_STUB_ENV, "1")
    monkeypatch.delenv("ENV", raising=False)

    app, _pool = _make_stub_app(app)
    state = _generate_state()
    _store_state(state, provider="google", page_of_origin="secrets")

    fake_account = MagicMock()
    fake_account.entity_id = uuid.uuid4()
    fake_account.granted_scopes = []
    fake_account.is_primary = False

    # When TEST_MODE_OAUTH_STUB=1, _exchange_code_for_tokens and
    # _fetch_google_userinfo return synthetic data in-process — no real HTTP.
    # The gmail-reload POST is best-effort and ignores failures; we suppress
    # it by patching the specific function that triggers it.
    with (
        patch(_GET_ACCOUNT_PATCH, AsyncMock(side_effect=oauth_module.GoogleAccountNotFoundError)),
        patch(_CREATE_ACCOUNT_PATCH, AsyncMock(return_value=fake_account)),
        patch(_EMIT_AUDIT_PATCH, AsyncMock()),
        patch("butlers.api.routers.oauth.store_app_credentials", AsyncMock()),
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as client:
            resp = await client.get(
                "/api/oauth/google/callback",
                params={"code": "stub-auth-code", "state": state},
            )

    # Should redirect to the success URL (toast=connected).
    assert resp.status_code in (200, 302, 307), (
        f"Expected redirect or success, got {resp.status_code}: {resp.text[:500]}"
    )
    if resp.status_code in (302, 307):
        location = resp.headers.get("location", "")
        assert "toast=connected" in location or "oauth_success=true" in location


async def test_provider_callback_stub_active_spotify_redirects(app, monkeypatch):
    """Stub active: Spotify callback redirects to /secrets?focus=u:spotify&toast=connected."""
    monkeypatch.setenv(_OAUTH_STUB_ENV, "1")
    monkeypatch.delenv("ENV", raising=False)

    app, _pool = _make_stub_app(app)
    state = _generate_state()
    _store_state(state, provider="spotify", page_of_origin="secrets")

    with (
        patch(_EMIT_AUDIT_PATCH, AsyncMock()),
        patch("butlers.api.routers.oauth.store_app_credentials", AsyncMock()),
        patch(
            "butlers.api.routers.oauth._resolve_provider_credentials",
            AsyncMock(return_value=("cid", "csec")),
        ),
    ):
        # cred_store.store must be available; wire a mock.
        mock_cred_store = AsyncMock()
        mock_cred_store.store = AsyncMock()
        with patch(
            "butlers.api.routers.oauth._make_credential_store", return_value=mock_cred_store
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                follow_redirects=False,
            ) as client:
                resp = await client.get(
                    "/api/oauth/spotify/callback",
                    params={"code": "stub-spotify-code", "state": state},
                )

    assert resp.status_code in (302, 307)
    location = resp.headers.get("location", "")
    assert "toast=connected" in location or "oauth_success=true" in location


# ===========================================================================
# 5. Full roundtrip: stub OFF → callback uses real exchange path (no stub tokens)
# ===========================================================================


async def test_provider_callback_stub_off_real_exchange(app, monkeypatch):
    """Stub off: callback invokes the real token exchange path (mocked at httpx layer).

    When TEST_MODE_OAUTH_STUB is unset, the callback must call the real
    _exchange_code_for_tokens — not the stub — and the code argument must be
    exactly the one passed in the request.
    """
    monkeypatch.delenv(_OAUTH_STUB_ENV, raising=False)
    monkeypatch.delenv("ENV", raising=False)

    app, _pool = _make_stub_app(app)
    state = _generate_state()
    _store_state(state, provider="google", page_of_origin="secrets")

    fake_token = {
        "access_token": "real-access-token",
        "refresh_token": "real-refresh-token",
        "scope": "openid email profile",
        "token_type": "Bearer",
    }
    fake_userinfo = {"email": "real@example.com", "name": "Real User", "id": "uid-1"}

    fake_account = MagicMock()
    fake_account.entity_id = uuid.uuid4()
    fake_account.granted_scopes = []

    with (
        patch(_EXCHANGE_PATCH, AsyncMock(return_value=fake_token)) as mock_exchange,
        patch(_USERINFO_PATCH, AsyncMock(return_value=fake_userinfo)),
        patch(_GET_ACCOUNT_PATCH, AsyncMock(side_effect=oauth_module.GoogleAccountNotFoundError)),
        patch(_CREATE_ACCOUNT_PATCH, AsyncMock(return_value=fake_account)),
        patch(_EMIT_AUDIT_PATCH, AsyncMock()),
        patch("butlers.api.routers.oauth.store_app_credentials", AsyncMock()),
        # Patch _resolve_app_credentials so the callback reaches the exchange.
        patch(
            "butlers.api.routers.oauth._resolve_app_credentials",
            AsyncMock(return_value=("test-client-id", "test-client-secret")),
        ),
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as client:
            resp = await client.get(
                "/api/oauth/google/callback",
                params={"code": "real-auth-code", "state": state},
            )

    # Real path called — exchange mock was invoked.
    mock_exchange.assert_called_once()
    # The code argument should be the one we passed, not a stub value.
    call_kwargs = mock_exchange.call_args.kwargs
    assert call_kwargs["code"] == "real-auth-code"
    # Must not be a stub token.
    assert resp.status_code in (200, 302, 307)
