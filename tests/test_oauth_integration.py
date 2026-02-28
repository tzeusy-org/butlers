"""Integration tests for the full Google OAuth bootstrap flow.

Covers the end-to-end path from state generation through callback to
credential storage and startup guard evaluation. These tests wire together
multiple components to verify that the system behaves correctly as a whole.

Scenarios:
- Full happy path: start → callback → credentials stored → guard passes
- Full failure path: guard blocks startup when no credentials exist
- State machine integrity: expired / replayed states block flow
- Dev workflow: --skip-oauth-check equivalent (missing creds, gating disabled)
- Pre-start enforcement: startup guard blocks correctly when OAuth missing
"""

from __future__ import annotations

import unittest.mock as mock
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
    _store_state,
    _TokenExchangeError,
    _validate_and_consume_state,
)
from butlers.startup_guard import (
    check_google_credentials,
    require_google_credentials_or_exit,
)

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GOOGLE_ENV = {
    "GOOGLE_OAUTH_CLIENT_ID": "test-client-id.apps.googleusercontent.com",
    "GOOGLE_OAUTH_CLIENT_SECRET": "test-client-secret",
    "GOOGLE_OAUTH_REDIRECT_URI": "http://localhost:40200/api/oauth/google/callback",
}

_FAKE_TOKEN_RESPONSE = {
    "access_token": "ya29.fake_access_token",
    "refresh_token": "1//fake_refresh_token_xyz",
    "scope": (
        "https://www.googleapis.com/auth/gmail.modify https://www.googleapis.com/auth/calendar"
    ),
    "token_type": "Bearer",
    "expires_in": 3600,
}

_EXCHANGE_PATCH_TARGET = "butlers.api.routers.oauth._exchange_code_for_tokens"


@pytest.fixture(autouse=True)
def clear_states():
    """Ensure state store is empty before and after each test."""
    _clear_state_store()
    yield
    _clear_state_store()


def _make_app(
    *,
    db_client_id: str = "test-client-id.apps.googleusercontent.com",
    db_client_secret: str = "test-client-secret",
    db_refresh_token: str | None = "1//fake-refresh-token-from-db",
    with_db_manager: bool = True,
):
    app = create_app()
    if not with_db_manager:
        return app

    secrets = {
        "GOOGLE_OAUTH_CLIENT_ID": db_client_id,
        "GOOGLE_OAUTH_CLIENT_SECRET": db_client_secret,
    }
    if db_refresh_token is not None:
        secrets["GOOGLE_REFRESH_TOKEN"] = db_refresh_token

    conn = AsyncMock()

    async def _fetchrow(_query: str, key: str | None = None):
        if key is None:
            # Query from upsert_owner_contact_info (owner lookup)
            if "shared.contacts" in _query:
                owner_row = MagicMock()
                owner_row.__getitem__ = lambda self, k: "owner-uuid" if k == "id" else None
                return owner_row
            return None
        value = secrets.get(key)
        if not value:
            return None
        return {"secret_value": value}

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
# Full OAuth flow: happy path
# ---------------------------------------------------------------------------


class TestFullOAuthFlowHappyPath:
    """Tests covering the complete flow: start → callback → credential storage."""

    async def test_full_flow_state_to_callback_returns_success(self):
        """End-to-end: start generates state, callback exchanges code, returns 200."""
        app = _make_app()

        # Phase 1: Call /start to get state token
        with patch.dict("os.environ", GOOGLE_ENV, clear=False):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                follow_redirects=False,
            ) as client:
                start_resp = await client.get(
                    "/api/oauth/google/start", params={"redirect": "false"}
                )

        assert start_resp.status_code == 200
        start_body = start_resp.json()
        state = start_body["state"]
        assert state  # State was generated

        # Phase 2: Use the same state token in callback
        mock_exchange = AsyncMock(return_value=_FAKE_TOKEN_RESPONSE)
        with (
            patch.dict("os.environ", GOOGLE_ENV, clear=False),
            patch(_EXCHANGE_PATCH_TARGET, mock_exchange),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                callback_resp = await client.get(
                    "/api/oauth/google/callback",
                    params={"code": "4/fake_auth_code", "state": state},
                )

        assert callback_resp.status_code == 200
        callback_body = callback_resp.json()
        assert callback_body["success"] is True
        assert callback_body["provider"] == "google"

    async def test_full_flow_state_consumed_after_successful_callback(self):
        """After a successful callback, the state token cannot be reused."""
        app = _make_app()

        with patch.dict("os.environ", GOOGLE_ENV, clear=False):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                follow_redirects=False,
            ) as client:
                start_resp = await client.get(
                    "/api/oauth/google/start", params={"redirect": "false"}
                )
        state = start_resp.json()["state"]

        # First callback: succeeds
        mock_exchange = AsyncMock(return_value=_FAKE_TOKEN_RESPONSE)
        with (
            patch.dict("os.environ", GOOGLE_ENV, clear=False),
            patch(_EXCHANGE_PATCH_TARGET, mock_exchange),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                resp1 = await client.get(
                    "/api/oauth/google/callback",
                    params={"code": "4/first_code", "state": state},
                )
                # Replay same state: must fail
                resp2 = await client.get(
                    "/api/oauth/google/callback",
                    params={"code": "4/second_code", "state": state},
                )

        assert resp1.status_code == 200
        assert resp2.status_code == 400
        assert resp2.json()["error_code"] == "invalid_state"

    async def test_full_flow_credentials_persisted_on_success(self):
        """Successful callback stores credentials in DB via store_google_credentials."""
        app = _make_app()

        state = _generate_state()
        _store_state(state)

        mock_exchange = AsyncMock(return_value=_FAKE_TOKEN_RESPONSE)
        mock_store = AsyncMock()

        with (
            patch.dict("os.environ", GOOGLE_ENV, clear=False),
            patch(_EXCHANGE_PATCH_TARGET, mock_exchange),
            patch("butlers.api.routers.oauth.store_google_credentials", mock_store),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                resp = await client.get(
                    "/api/oauth/google/callback",
                    params={"code": "4/auth_code", "state": state},
                )

        assert resp.status_code == 200
        assert resp.json()["success"] is True
        mock_store.assert_awaited_once()
        # Verify the stored credentials include expected fields
        call_kwargs = mock_store.call_args[1]
        assert call_kwargs["client_id"] == GOOGLE_ENV["GOOGLE_OAUTH_CLIENT_ID"]
        assert call_kwargs["client_secret"] == GOOGLE_ENV["GOOGLE_OAUTH_CLIENT_SECRET"]
        assert call_kwargs["refresh_token"] == _FAKE_TOKEN_RESPONSE["refresh_token"]


# ---------------------------------------------------------------------------
# Full OAuth flow: failure paths
# ---------------------------------------------------------------------------


class TestFullOAuthFlowFailurePaths:
    """Tests for failure paths in the full OAuth flow."""

    async def test_flow_fails_when_code_expired_on_exchange(self):
        """Exchange with an expired code results in 400; state is consumed."""
        app = _make_app()
        state = _generate_state()
        _store_state(state)

        mock_exchange = AsyncMock(side_effect=_TokenExchangeError("invalid_grant"))
        with (
            patch.dict("os.environ", GOOGLE_ENV, clear=False),
            patch(_EXCHANGE_PATCH_TARGET, mock_exchange),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                resp = await client.get(
                    "/api/oauth/google/callback",
                    params={"code": "4/expired_code", "state": state},
                )

        assert resp.status_code == 400
        assert resp.json()["error_code"] == "token_exchange_failed"
        # State should have been consumed (cannot replay)
        assert _validate_and_consume_state(state) is False

    async def test_flow_fails_without_refresh_token_in_response(self):
        """Callback fails if token response lacks refresh_token (no offline access)."""
        app = _make_app()
        state = _generate_state()
        _store_state(state)

        no_refresh = {
            "access_token": "ya29.only_access",
            "token_type": "Bearer",
            "expires_in": 3600,
            # No refresh_token
        }
        mock_exchange = AsyncMock(return_value=no_refresh)
        with (
            patch.dict("os.environ", GOOGLE_ENV, clear=False),
            patch(_EXCHANGE_PATCH_TARGET, mock_exchange),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                resp = await client.get(
                    "/api/oauth/google/callback",
                    params={"code": "4/code", "state": state},
                )

        assert resp.status_code == 400
        body = resp.json()
        assert body["error_code"] == "no_refresh_token"
        # Error message must explain how to fix
        assert "offline" in body["message"].lower() or "consent" in body["message"].lower()

    async def test_flow_fails_when_state_was_never_issued(self):
        """Callback with a state token that was never generated is rejected."""
        app = _make_app()

        with patch.dict("os.environ", GOOGLE_ENV, clear=False):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                resp = await client.get(
                    "/api/oauth/google/callback",
                    params={"code": "4/code", "state": "forged-state-token"},
                )

        assert resp.status_code == 400
        assert resp.json()["error_code"] == "invalid_state"

    async def test_flow_fails_when_state_expired_before_callback(self):
        """Callback with a state token that has expired is rejected."""
        import time

        app = _make_app()
        state = _generate_state()
        # Store with already-expired timestamp
        _state_store[state] = time.monotonic() - 1

        with patch.dict("os.environ", GOOGLE_ENV, clear=False):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                resp = await client.get(
                    "/api/oauth/google/callback",
                    params={"code": "4/code", "state": state},
                )

        assert resp.status_code == 400
        assert resp.json()["error_code"] == "invalid_state"

    async def test_flow_fails_when_provider_denies_consent(self):
        """When user denies OAuth consent, callback returns actionable error."""
        app = _make_app()

        with patch.dict("os.environ", GOOGLE_ENV, clear=False):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                resp = await client.get(
                    "/api/oauth/google/callback",
                    params={"error": "access_denied"},
                )

        assert resp.status_code == 400
        body = resp.json()
        assert body["success"] is False
        assert body["error_code"] == "provider_error"
        # Message must be human-readable (not raw Google error code)
        assert "access_denied" not in body["message"].lower() or "denied" in body["message"].lower()


# ---------------------------------------------------------------------------
# Pre-start enforcement: startup guard integration
# ---------------------------------------------------------------------------


class TestPreStartEnforcement:
    """Tests that check startup guard behavior in the context of the OAuth flow."""

    def test_startup_guard_blocks_when_no_oauth_credentials(self):
        """require_google_credentials_or_exit() exits under DB-only contract."""
        with pytest.raises(SystemExit) as exc_info:
            require_google_credentials_or_exit(caller="gmail-connector")

        assert exc_info.value.code == 1

    def test_startup_guard_still_blocks_even_with_env_credentials(self):
        """Env credentials do not bypass DB-only startup guard behavior."""
        good_creds = {
            "GOOGLE_OAUTH_CLIENT_ID": "client-id-123",
            "GOOGLE_OAUTH_CLIENT_SECRET": "client-secret-abc",
            "GOOGLE_REFRESH_TOKEN": "1//refresh-token-xyz",
        }
        with mock.patch.dict("os.environ", good_creds, clear=True):
            with pytest.raises(SystemExit):
                require_google_credentials_or_exit(caller="gmail-connector")

    def test_startup_guard_reports_db_only_remediation(self):
        """check_google_credentials() returns DB-only remediation guidance."""
        result = check_google_credentials()
        assert result.ok is False
        assert result.missing_vars == []
        assert "db-managed" in result.message.lower()

    def test_startup_guard_skip_mode_does_not_call_exit(self):
        """Simulates --skip-oauth-check: guard check returns result but caller skips exit."""
        # In skip mode the caller checks and decides to continue.
        result = check_google_credentials()
        assert result.ok is False

    def test_startup_guard_remediation_mentions_dashboard_oauth(self):
        """Remediation text for missing creds should mention the dashboard OAuth flow."""
        result = check_google_credentials()

        assert result.ok is False
        remediation = result.remediation.lower()
        # Should mention the dashboard or OAuth flow
        assert "dashboard" in remediation or "localhost" in remediation or "oauth" in remediation


# ---------------------------------------------------------------------------
# Dev workflow: missing creds but startup not blocked
# ---------------------------------------------------------------------------


class TestDevWorkflowWithMissingCredentials:
    """Tests mimicking the dev.sh workflow where creds may be missing at startup."""

    async def test_oauth_status_endpoint_reflects_not_configured_when_no_creds(self):
        """GET /api/oauth/status shows not_configured when no credentials are set."""
        app = _make_app(db_client_id="", db_client_secret="", db_refresh_token=None)

        with patch.dict("os.environ", GOOGLE_ENV, clear=False):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                resp = await client.get("/api/oauth/status")

        assert resp.status_code == 200
        body = resp.json()
        assert body["google"]["state"] == "not_configured"
        assert body["google"]["connected"] is False

    async def test_oauth_start_still_works_without_stored_refresh_token(self):
        """GET /api/oauth/google/start works even when no token is stored yet."""
        app = _make_app(db_refresh_token=None)

        with patch.dict("os.environ", GOOGLE_ENV, clear=False):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                follow_redirects=False,
            ) as client:
                resp = await client.get("/api/oauth/google/start")

        # Start is always available to initiate the OAuth bootstrap
        assert resp.status_code == 302
        assert "accounts.google.com" in resp.headers["location"]

    async def test_multiple_start_calls_create_distinct_states(self):
        """Multiple concurrent /start calls each produce a unique state token."""
        app = _make_app(db_refresh_token=None)

        states = []
        with patch.dict("os.environ", GOOGLE_ENV, clear=False):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                follow_redirects=False,
            ) as client:
                for _ in range(5):
                    resp = await client.get("/api/oauth/google/start", params={"redirect": "false"})
                    states.append(resp.json()["state"])

        # All states must be unique (no collisions)
        assert len(set(states)) == 5

    async def test_oauth_status_shows_connected_after_credentials_configured(self):
        """Status changes to connected once credentials are configured in DB."""
        from butlers.api.models.oauth import OAuthCredentialState, OAuthCredentialStatus

        app = _make_app()

        connected_status = OAuthCredentialStatus(
            state=OAuthCredentialState.connected,
            scopes_granted=[
                "https://www.googleapis.com/auth/gmail.modify",
                "https://www.googleapis.com/auth/calendar",
            ],
        )

        mock_probe = AsyncMock(return_value=connected_status)
        with (
            patch.dict("os.environ", GOOGLE_ENV, clear=False),
            patch("butlers.api.routers.oauth._probe_google_token", mock_probe),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                resp = await client.get("/api/oauth/status")

        assert resp.status_code == 200
        body = resp.json()
        assert body["google"]["state"] == "connected"
        assert body["google"]["connected"] is True
