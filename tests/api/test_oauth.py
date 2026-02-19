"""Tests for Google OAuth bootstrap endpoints.

Verifies the API contract for:
- GET /api/oauth/google/start — state generation, URL building, redirect behavior
- GET /api/oauth/google/callback — state validation, code exchange, error paths

All tests mock ``_exchange_code_for_tokens`` directly so no real
Google OAuth network requests are made.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.routers.oauth import (
    _clear_state_store,
    _generate_state,
    _sanitize_provider_error,
    _state_store,
    _store_state,
    _TokenExchangeError,
    _validate_and_consume_state,
)

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Constants and helpers
# ---------------------------------------------------------------------------

GOOGLE_ENV = {
    "GOOGLE_OAUTH_CLIENT_ID": "test-client-id.apps.googleusercontent.com",
    "GOOGLE_OAUTH_CLIENT_SECRET": "test-client-secret",
    "GOOGLE_OAUTH_REDIRECT_URI": "http://localhost:8200/api/oauth/google/callback",
}

_FAKE_TOKEN_RESPONSE = {
    "access_token": "ya29.fake_access_token",
    "refresh_token": "1//fake_refresh_token_xyz",
    "scope": "https://www.googleapis.com/auth/gmail.readonly",
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


def _make_app():
    """Create a FastAPI test app."""
    return create_app()


# ---------------------------------------------------------------------------
# Unit tests: state store helpers
# ---------------------------------------------------------------------------


class TestStateStore:
    def test_generate_state_is_url_safe_string(self):
        """State token must be a URL-safe string of reasonable length."""
        state = _generate_state()
        assert isinstance(state, str)
        assert len(state) >= 32
        valid_chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_="
        assert all(c in valid_chars for c in state)

    def test_generate_state_unique(self):
        """Each call generates a distinct token."""
        states = {_generate_state() for _ in range(10)}
        assert len(states) == 10

    def test_store_and_validate_state(self):
        """Stored state can be validated once."""
        state = _generate_state()
        _store_state(state)
        assert _validate_and_consume_state(state) is True

    def test_state_is_one_time_use(self):
        """State cannot be validated twice (one-time-use)."""
        state = _generate_state()
        _store_state(state)
        assert _validate_and_consume_state(state) is True
        assert _validate_and_consume_state(state) is False

    def test_unknown_state_is_invalid(self):
        """A state that was never stored is rejected."""
        assert _validate_and_consume_state("totally-fake-state") is False

    def test_expired_state_is_rejected(self):
        """An expired state token is rejected even if it was valid."""
        state = _generate_state()
        # Store with expiry in the past
        _state_store[state] = time.monotonic() - 1
        assert _validate_and_consume_state(state) is False

    def test_clear_state_store(self):
        """Clear removes all entries."""
        _store_state(_generate_state())
        _store_state(_generate_state())
        _clear_state_store()
        assert len(_state_store) == 0


# ---------------------------------------------------------------------------
# Unit tests: sanitize_provider_error
# ---------------------------------------------------------------------------


class TestSanitizeProviderError:
    def test_known_errors_return_friendly_message(self):
        """Known error codes return a human-readable message."""
        msg = _sanitize_provider_error("access_denied")
        assert "denied" in msg.lower()

    def test_unknown_errors_return_generic_message(self):
        """Unknown error codes return a generic message (no leakage)."""
        msg = _sanitize_provider_error("some_weird_google_error_xyz")
        assert "restart" in msg.lower() or "failed" in msg.lower()
        assert "some_weird_google_error_xyz" not in msg


# ---------------------------------------------------------------------------
# Integration tests: start endpoint
# ---------------------------------------------------------------------------


class TestOAuthGoogleStart:
    async def test_start_redirects_to_google_by_default(self):
        """GET /api/oauth/google/start redirects (302) to accounts.google.com."""
        app = _make_app()
        with patch.dict("os.environ", GOOGLE_ENV, clear=False):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                follow_redirects=False,
            ) as client:
                resp = await client.get("/api/oauth/google/start")

        assert resp.status_code == 302
        location = resp.headers["location"]
        assert "accounts.google.com" in location
        assert "client_id=test-client-id.apps.googleusercontent.com" in location
        assert "response_type=code" in location
        assert "access_type=offline" in location
        assert "state=" in location

    async def test_start_returns_json_when_redirect_false(self):
        """GET /api/oauth/google/start?redirect=false returns JSON payload."""
        app = _make_app()
        with patch.dict("os.environ", GOOGLE_ENV, clear=False):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                resp = await client.get("/api/oauth/google/start", params={"redirect": "false"})

        assert resp.status_code == 200
        body = resp.json()
        assert "authorization_url" in body
        assert "state" in body
        assert "accounts.google.com" in body["authorization_url"]

    async def test_start_includes_prompt_consent(self):
        """Start URL must include prompt=consent to force refresh token issuance."""
        app = _make_app()
        with patch.dict("os.environ", GOOGLE_ENV, clear=False):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                follow_redirects=False,
            ) as client:
                resp = await client.get("/api/oauth/google/start")

        location = resp.headers["location"]
        assert "prompt=consent" in location

    async def test_start_state_stored_in_state_store(self):
        """A state token is stored after calling the start endpoint."""
        app = _make_app()
        with patch.dict("os.environ", GOOGLE_ENV, clear=False):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                follow_redirects=False,
            ) as client:
                await client.get("/api/oauth/google/start")

        assert len(_state_store) == 1

    async def test_start_missing_client_id_returns_503(self):
        """When GOOGLE_OAUTH_CLIENT_ID is not set, start returns 503."""
        app = _make_app()
        env = {**GOOGLE_ENV, "GOOGLE_OAUTH_CLIENT_ID": ""}
        with patch.dict("os.environ", env, clear=False):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                resp = await client.get("/api/oauth/google/start")

        assert resp.status_code == 503

    async def test_start_uses_default_scopes(self):
        """Authorization URL includes Gmail and Calendar scopes by default."""
        app = _make_app()
        env = {k: v for k, v in GOOGLE_ENV.items() if k != "GOOGLE_OAUTH_SCOPES"}
        with patch.dict("os.environ", env, clear=False):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                follow_redirects=False,
            ) as client:
                resp = await client.get("/api/oauth/google/start")

        location = resp.headers["location"]
        assert "gmail" in location.lower()
        assert "calendar" in location.lower()

    async def test_start_custom_scopes_from_env(self):
        """GOOGLE_OAUTH_SCOPES env var overrides default scopes."""
        app = _make_app()
        env = {**GOOGLE_ENV, "GOOGLE_OAUTH_SCOPES": "https://www.googleapis.com/auth/drive"}
        with patch.dict("os.environ", env, clear=False):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                follow_redirects=False,
            ) as client:
                resp = await client.get("/api/oauth/google/start")

        location = resp.headers["location"]
        assert "drive" in location


# ---------------------------------------------------------------------------
# Integration tests: callback endpoint
# ---------------------------------------------------------------------------


class TestOAuthGoogleCallback:
    async def test_callback_success_returns_json(self):
        """Valid code+state callback returns 200 with success payload."""
        app = _make_app()
        state = _generate_state()
        _store_state(state)

        mock_exchange = AsyncMock(return_value=_FAKE_TOKEN_RESPONSE)
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
                    params={"code": "4/fake_auth_code", "state": state},
                )

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["provider"] == "google"

    async def test_callback_success_includes_granted_scope(self):
        """Success payload includes the scope returned by Google."""
        app = _make_app()
        state = _generate_state()
        _store_state(state)

        token_resp = {
            **_FAKE_TOKEN_RESPONSE,
            "scope": "https://www.googleapis.com/auth/gmail.readonly",
        }
        mock_exchange = AsyncMock(return_value=token_resp)
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

        body = resp.json()
        assert body["success"] is True
        assert "gmail" in body.get("scope", "")

    async def test_callback_missing_code_returns_400(self):
        """Callback without code returns 400 with actionable error."""
        app = _make_app()
        state = _generate_state()
        _store_state(state)

        with patch.dict("os.environ", GOOGLE_ENV, clear=False):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                resp = await client.get("/api/oauth/google/callback", params={"state": state})

        assert resp.status_code == 400
        body = resp.json()
        assert body["success"] is False
        assert body["error_code"] == "missing_code"

    async def test_callback_missing_state_returns_400(self):
        """Callback without state returns 400 with CSRF warning."""
        app = _make_app()
        with patch.dict("os.environ", GOOGLE_ENV, clear=False):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                resp = await client.get("/api/oauth/google/callback", params={"code": "4/code"})

        assert resp.status_code == 400
        body = resp.json()
        assert body["success"] is False
        assert body["error_code"] == "missing_state"

    async def test_callback_invalid_state_returns_400(self):
        """Callback with unknown/invalid state token returns 400."""
        app = _make_app()
        with patch.dict("os.environ", GOOGLE_ENV, clear=False):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                resp = await client.get(
                    "/api/oauth/google/callback",
                    params={"code": "4/code", "state": "not-a-valid-state"},
                )

        assert resp.status_code == 400
        body = resp.json()
        assert body["error_code"] == "invalid_state"

    async def test_callback_expired_state_returns_400(self):
        """Callback with an expired state token returns 400."""
        app = _make_app()
        state = _generate_state()
        _state_store[state] = time.monotonic() - 1  # Already expired

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
        body = resp.json()
        assert body["error_code"] == "invalid_state"

    async def test_callback_state_is_one_time_use(self):
        """Reusing a state token on a second callback returns 400."""
        app = _make_app()
        state = _generate_state()
        _store_state(state)

        mock_exchange = AsyncMock(return_value=_FAKE_TOKEN_RESPONSE)
        with (
            patch.dict("os.environ", GOOGLE_ENV, clear=False),
            patch(_EXCHANGE_PATCH_TARGET, mock_exchange),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                # First request — should succeed
                resp1 = await client.get(
                    "/api/oauth/google/callback",
                    params={"code": "4/code", "state": state},
                )
                # Second request with same state — should fail
                resp2 = await client.get(
                    "/api/oauth/google/callback",
                    params={"code": "4/code", "state": state},
                )

        assert resp1.status_code == 200
        assert resp2.status_code == 400
        assert resp2.json()["error_code"] == "invalid_state"

    async def test_callback_provider_error_access_denied(self):
        """User-denied consent (error=access_denied) returns 400 with friendly message."""
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
        assert "denied" in body["message"].lower()

    async def test_callback_provider_error_unknown_code(self):
        """Unknown provider error returns generic message (no leakage)."""
        app = _make_app()
        with patch.dict("os.environ", GOOGLE_ENV, clear=False):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                resp = await client.get(
                    "/api/oauth/google/callback",
                    params={"error": "weird_internal_error_9876"},
                )

        assert resp.status_code == 400
        body = resp.json()
        assert "weird_internal_error_9876" not in body["message"]

    async def test_callback_token_exchange_http_error(self):
        """HTTP error from token endpoint returns 400 with generic message."""
        app = _make_app()
        state = _generate_state()
        _store_state(state)

        mock_exchange = AsyncMock(side_effect=_TokenExchangeError("HTTP 400"))
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
                    params={"code": "expired_code", "state": state},
                )

        assert resp.status_code == 400
        body = resp.json()
        assert body["error_code"] == "token_exchange_failed"
        # Must not leak the raw Google error
        assert "invalid_grant" not in body["message"]

    async def test_callback_no_refresh_token_in_response(self):
        """Token response without refresh_token returns 400 with actionable guidance."""
        app = _make_app()
        state = _generate_state()
        _store_state(state)

        no_rt_response = {
            "access_token": "ya29.fake",
            "token_type": "Bearer",
            "expires_in": 3600,
            # No refresh_token field
        }
        mock_exchange = AsyncMock(return_value=no_rt_response)
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
        assert "offline" in body["message"].lower() or "consent" in body["message"].lower()

    async def test_callback_network_error_returns_400(self):
        """Network error during token exchange returns 400."""
        app = _make_app()
        state = _generate_state()
        _store_state(state)

        mock_exchange = AsyncMock(side_effect=_TokenExchangeError("Network error"))
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
        assert body["error_code"] == "token_exchange_failed"

    async def test_callback_success_redirects_to_dashboard_when_configured(self):
        """With OAUTH_DASHBOARD_URL set, success redirects to dashboard."""
        app = _make_app()
        state = _generate_state()
        _store_state(state)

        mock_exchange = AsyncMock(return_value=_FAKE_TOKEN_RESPONSE)
        env = {**GOOGLE_ENV, "OAUTH_DASHBOARD_URL": "http://localhost:5173"}
        with (
            patch.dict("os.environ", env, clear=False),
            patch(_EXCHANGE_PATCH_TARGET, mock_exchange),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                follow_redirects=False,
            ) as client:
                resp = await client.get(
                    "/api/oauth/google/callback",
                    params={"code": "4/code", "state": state},
                )

        assert resp.status_code == 302
        assert "localhost:5173" in resp.headers["location"]
        assert "oauth_success=true" in resp.headers["location"]

    async def test_callback_provider_error_redirects_to_dashboard_when_configured(self):
        """With OAUTH_DASHBOARD_URL set, provider error redirects to dashboard."""
        app = _make_app()
        env = {**GOOGLE_ENV, "OAUTH_DASHBOARD_URL": "http://localhost:5173"}
        with patch.dict("os.environ", env, clear=False):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                follow_redirects=False,
            ) as client:
                resp = await client.get(
                    "/api/oauth/google/callback",
                    params={"error": "access_denied"},
                )

        assert resp.status_code == 302
        assert "oauth_error=provider_error" in resp.headers["location"]

    async def test_callback_missing_client_secret_returns_503(self):
        """When GOOGLE_OAUTH_CLIENT_SECRET is missing, callback returns 503."""
        app = _make_app()
        state = _generate_state()
        _store_state(state)

        env = {**GOOGLE_ENV, "GOOGLE_OAUTH_CLIENT_SECRET": ""}
        with patch.dict("os.environ", env, clear=False):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                resp = await client.get(
                    "/api/oauth/google/callback",
                    params={"code": "4/code", "state": state},
                )

        assert resp.status_code == 503
