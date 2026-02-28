"""Tests for the GET /api/oauth/status endpoint.

Verifies:
- ``not_configured`` state when env vars are absent.
- ``not_configured`` state when refresh token is missing.
- ``connected`` state for valid credentials with correct scopes.
- ``missing_scope`` state when granted scopes are insufficient.
- ``expired`` / ``invalid_grant`` classification.
- ``redirect_uri_mismatch`` / ``invalid_client`` classification.
- ``unapproved_tester`` / ``access_denied`` classification.
- ``unknown_error`` for unclassified Google responses.
- Network error handling.
- Response model shape and field presence.

Mocking strategy:
- Tests that require no HTTP call (env var checks) patch at the env level only.
- Tests that exercise the token probe mock ``_probe_google_token`` directly;
  this avoids patching httpx.AsyncClient globally and accidentally clobbering
  the ASGI test transport.
- ``TestProbeGoogleToken`` tests the probe internals by patching httpx inside
  the unit method scope only (not via the ASGI client).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from butlers.api.models.oauth import (
    OAuthCredentialState,
    OAuthCredentialStatus,
)
from butlers.api.routers import oauth as oauth_module
from butlers.api.routers.oauth import _probe_google_token

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BASE_ENV = {
    "GOOGLE_OAUTH_CLIENT_ID": "test-client-id.apps.googleusercontent.com",
    "GOOGLE_OAUTH_CLIENT_SECRET": "test-client-secret",
    "GOOGLE_OAUTH_REDIRECT_URI": "http://localhost:40200/api/oauth/google/callback",
    "GOOGLE_REFRESH_TOKEN": "1//fake-refresh-token",
}

_FULL_SCOPES = (
    "https://www.googleapis.com/auth/gmail.readonly "
    "https://www.googleapis.com/auth/gmail.modify "
    "https://www.googleapis.com/auth/calendar"
)

_FULL_SCOPE_LIST = [s for s in _FULL_SCOPES.split() if s]

STATUS_URL = "/api/oauth/status"

_PROBE_PATCH = "butlers.api.routers.oauth._probe_google_token"
_HTTPX_CLIENT_PATCH = "butlers.api.routers.oauth.httpx.AsyncClient"


def _make_app(
    app,
    *,
    db_client_id: str = "test-client-id.apps.googleusercontent.com",
    db_client_secret: str = "test-client-secret",
    db_refresh_token: str | None = "1//fake-refresh-token",
    with_db_manager: bool = True,
):
    if not with_db_manager:
        return app

    secrets = {
        "GOOGLE_OAUTH_CLIENT_ID": db_client_id,
        "GOOGLE_OAUTH_CLIENT_SECRET": db_client_secret,
    }

    # contact_info entries (refresh token lives here, not in butler_secrets)
    contact_info: dict[str, str] = {}
    if db_refresh_token is not None:
        contact_info["google_oauth_refresh"] = db_refresh_token

    conn = AsyncMock()

    async def _fetchrow(_query: str, key: str | None = None):
        if key is None:
            if "shared.contacts" in _query:
                owner_row = MagicMock()
                owner_row.__getitem__ = lambda self, k: "owner-uuid" if k == "id" else None
                return owner_row
            return None
        # resolve_owner_contact_info queries shared.contact_info with type as $1
        if "shared.contact_info" in _query:
            value = contact_info.get(key)
            if not value:
                return None
            row = MagicMock()
            row.__getitem__ = lambda self, k: value if k == "value" else None
            return row
        value = secrets.get(key)
        if not value:
            return None
        return {"secret_value": value}

    conn.fetchrow.side_effect = _fetchrow

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool = MagicMock()
    pool.acquire = _acquire

    db_manager = MagicMock()
    db_manager.credential_shared_pool.return_value = pool
    app.dependency_overrides[oauth_module._get_db_manager] = lambda: db_manager
    return app


def _connected_status() -> OAuthCredentialStatus:
    return OAuthCredentialStatus(
        state=OAuthCredentialState.connected,
        scopes_granted=_FULL_SCOPE_LIST,
    )


def _status(state: OAuthCredentialState, **kwargs) -> OAuthCredentialStatus:
    return OAuthCredentialStatus(
        state=state,
        remediation=kwargs.get("remediation", "Please reconnect."),
        detail=kwargs.get("detail"),
        scopes_granted=kwargs.get("scopes_granted"),
    )


def _make_mock_http_client(mock_response: httpx.Response) -> MagicMock:
    """Build a fake httpx.AsyncClient class that returns ``mock_response`` from .post()."""
    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_response)

    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_client)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    return MagicMock(return_value=mock_cm)


def _make_mock_http_client_raises(exc: Exception) -> MagicMock:
    """Build a fake httpx.AsyncClient class whose .post() raises ``exc``."""
    mock_client = MagicMock()
    mock_client.post = AsyncMock(side_effect=exc)

    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_client)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    return MagicMock(return_value=mock_cm)


def _mock_response(*, status_code: int = 200, body: dict) -> httpx.Response:
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = status_code
    mock_resp.json.return_value = body
    return mock_resp


# ---------------------------------------------------------------------------
# Tests: not_configured (no HTTP probe needed — checked before probe call)
# ---------------------------------------------------------------------------


class TestOAuthStatusNotConfigured:
    async def test_no_client_id_returns_not_configured(self, app):
        """Missing DB client_id → not_configured."""
        app = _make_app(app, db_client_id="")
        env = _BASE_ENV
        with patch.dict("os.environ", env, clear=False):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(STATUS_URL)

        assert resp.status_code == 200
        body = resp.json()
        assert body["google"]["state"] == OAuthCredentialState.not_configured
        assert body["google"]["connected"] is False
        assert body["google"]["remediation"] is not None

    async def test_no_client_secret_returns_not_configured(self, app):
        """Missing DB client_secret → not_configured."""
        app = _make_app(app, db_client_secret="")
        env = _BASE_ENV
        with patch.dict("os.environ", env, clear=False):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(STATUS_URL)

        assert resp.status_code == 200
        body = resp.json()
        assert body["google"]["state"] == OAuthCredentialState.not_configured
        assert body["google"]["connected"] is False

    async def test_no_refresh_token_returns_not_configured(self, app):
        """No DB refresh token → not_configured with connect guidance."""
        app = _make_app(app, db_refresh_token=None)
        env = _BASE_ENV
        with patch.dict("os.environ", env, clear=False):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(STATUS_URL)

        assert resp.status_code == 200
        body = resp.json()
        assert body["google"]["state"] == OAuthCredentialState.not_configured
        assert body["google"]["connected"] is False
        remediation = body["google"]["remediation"].lower()
        assert "connect" in remediation or "authorize" in remediation or "oauth" in remediation

    async def test_google_refresh_token_env_var_not_used(self, app):
        """Env refresh token does not bypass missing DB refresh token."""
        app = _make_app(app, db_refresh_token=None)
        env = {**_BASE_ENV, "GOOGLE_REFRESH_TOKEN": "1//fallback"}

        mock_probe = AsyncMock(return_value=_connected_status())
        with patch.dict("os.environ", env, clear=False), patch(_PROBE_PATCH, mock_probe):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(STATUS_URL)

        assert resp.status_code == 200
        body = resp.json()
        assert body["google"]["state"] == OAuthCredentialState.not_configured
        mock_probe.assert_not_awaited()


# ---------------------------------------------------------------------------
# Tests: connected
# ---------------------------------------------------------------------------


class TestOAuthStatusConnected:
    async def test_valid_token_returns_connected(self, app):
        """Valid refresh token with required scopes → connected."""
        app = _make_app(app)

        mock_probe = AsyncMock(return_value=_connected_status())
        with patch.dict("os.environ", _BASE_ENV, clear=False), patch(_PROBE_PATCH, mock_probe):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(STATUS_URL)

        assert resp.status_code == 200
        body = resp.json()
        assert body["google"]["state"] == OAuthCredentialState.connected
        assert body["google"]["connected"] is True
        assert body["google"]["remediation"] is None

    async def test_connected_includes_scopes_granted(self, app):
        """Connected state includes scopes_granted list."""
        app = _make_app(app)

        mock_probe = AsyncMock(return_value=_connected_status())
        with patch.dict("os.environ", _BASE_ENV, clear=False), patch(_PROBE_PATCH, mock_probe):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(STATUS_URL)

        body = resp.json()
        scopes = body["google"]["scopes_granted"]
        assert isinstance(scopes, list)
        assert any("gmail" in s for s in scopes)
        assert any("calendar" in s for s in scopes)

    async def test_provider_field_is_google(self, app):
        """Status response includes provider=google."""
        app = _make_app(app)

        mock_probe = AsyncMock(return_value=_connected_status())
        with patch.dict("os.environ", _BASE_ENV, clear=False), patch(_PROBE_PATCH, mock_probe):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(STATUS_URL)

        body = resp.json()
        assert body["google"]["provider"] == "google"


# ---------------------------------------------------------------------------
# Tests: missing_scope
# ---------------------------------------------------------------------------


class TestOAuthStatusMissingScope:
    async def test_missing_scope_state(self, app):
        """Token lacking required scopes → missing_scope with remediation."""
        app = _make_app(app)
        limited = ["https://www.googleapis.com/auth/gmail.readonly"]
        probe_result = _status(
            OAuthCredentialState.missing_scope,
            scopes_granted=limited,
            remediation="Re-run the OAuth flow and grant all required permissions.",
        )

        mock_probe = AsyncMock(return_value=probe_result)
        with patch.dict("os.environ", _BASE_ENV, clear=False), patch(_PROBE_PATCH, mock_probe):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(STATUS_URL)

        assert resp.status_code == 200
        body = resp.json()
        assert body["google"]["state"] == OAuthCredentialState.missing_scope
        assert body["google"]["connected"] is False
        assert body["google"]["remediation"] is not None
        remediation = body["google"]["remediation"].lower()
        assert "permission" in remediation or "scope" in remediation or "grant" in remediation

    async def test_missing_scope_includes_partial_scopes_granted(self, app):
        """missing_scope state populates scopes_granted with what was received."""
        app = _make_app(app)
        limited = ["https://www.googleapis.com/auth/gmail.readonly"]
        probe_result = _status(
            OAuthCredentialState.missing_scope,
            scopes_granted=limited,
            remediation="Grant missing permissions.",
        )

        mock_probe = AsyncMock(return_value=probe_result)
        with patch.dict("os.environ", _BASE_ENV, clear=False), patch(_PROBE_PATCH, mock_probe):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(STATUS_URL)

        body = resp.json()
        assert body["google"]["scopes_granted"] is not None
        assert len(body["google"]["scopes_granted"]) > 0


# ---------------------------------------------------------------------------
# Tests: expired (invalid_grant)
# ---------------------------------------------------------------------------


class TestOAuthStatusExpired:
    async def test_expired_state(self, app):
        """Expired/revoked token → expired state with re-auth guidance."""
        app = _make_app(app)
        probe_result = _status(
            OAuthCredentialState.expired,
            remediation=(
                "Your Google authorization has expired or been revoked. "
                "Click 'Connect Google' to re-run the OAuth flow."
            ),
        )

        mock_probe = AsyncMock(return_value=probe_result)
        with patch.dict("os.environ", _BASE_ENV, clear=False), patch(_PROBE_PATCH, mock_probe):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(STATUS_URL)

        assert resp.status_code == 200
        body = resp.json()
        assert body["google"]["state"] == OAuthCredentialState.expired
        assert body["google"]["connected"] is False
        remediation = body["google"]["remediation"].lower()
        assert "expired" in remediation or "revoked" in remediation or "re-run" in remediation


# ---------------------------------------------------------------------------
# Tests: redirect_uri_mismatch (invalid_client)
# ---------------------------------------------------------------------------


class TestOAuthStatusRedirectUriMismatch:
    async def test_redirect_uri_mismatch_state(self, app):
        """Client credential mismatch → redirect_uri_mismatch state."""
        app = _make_app(app)
        probe_result = _status(
            OAuthCredentialState.redirect_uri_mismatch,
            remediation=(
                "OAuth client credentials are invalid or the redirect URI does not match. "
                "Verify your client credentials and re-run the OAuth flow."
            ),
        )

        mock_probe = AsyncMock(return_value=probe_result)
        with patch.dict("os.environ", _BASE_ENV, clear=False), patch(_PROBE_PATCH, mock_probe):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(STATUS_URL)

        assert resp.status_code == 200
        body = resp.json()
        assert body["google"]["state"] == OAuthCredentialState.redirect_uri_mismatch
        assert body["google"]["connected"] is False
        remediation = body["google"]["remediation"].lower()
        assert "redirect" in remediation or "client" in remediation or "credentials" in remediation


# ---------------------------------------------------------------------------
# Tests: unapproved_tester (access_denied)
# ---------------------------------------------------------------------------


class TestOAuthStatusUnapprovedTester:
    async def test_unapproved_tester_state(self, app):
        """Access denied → unapproved_tester state with tester guidance."""
        app = _make_app(app)
        probe_result = _status(
            OAuthCredentialState.unapproved_tester,
            remediation=(
                "Access was denied. If your OAuth app is in testing mode, "
                "add your account as a tester in Google Cloud Console."
            ),
        )

        mock_probe = AsyncMock(return_value=probe_result)
        with patch.dict("os.environ", _BASE_ENV, clear=False), patch(_PROBE_PATCH, mock_probe):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(STATUS_URL)

        assert resp.status_code == 200
        body = resp.json()
        assert body["google"]["state"] == OAuthCredentialState.unapproved_tester
        assert body["google"]["connected"] is False
        remediation = body["google"]["remediation"].lower()
        assert (
            "tester" in remediation or "test user" in remediation or "testing mode" in remediation
        )


# ---------------------------------------------------------------------------
# Tests: unknown_error
# ---------------------------------------------------------------------------


class TestOAuthStatusUnknownError:
    async def test_unknown_error_state(self, app):
        """Unrecognized error → unknown_error state."""
        app = _make_app(app)
        probe_result = _status(
            OAuthCredentialState.unknown_error,
            remediation=(
                "An unexpected error occurred. Check server logs and retry the OAuth flow."
            ),
        )

        mock_probe = AsyncMock(return_value=probe_result)
        with patch.dict("os.environ", _BASE_ENV, clear=False), patch(_PROBE_PATCH, mock_probe):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(STATUS_URL)

        assert resp.status_code == 200
        body = resp.json()
        assert body["google"]["state"] == OAuthCredentialState.unknown_error
        assert body["google"]["connected"] is False
        assert body["google"]["remediation"] is not None

    async def test_network_error_state(self, app):
        """Network failure → unknown_error with network guidance."""
        app = _make_app(app)
        probe_result = _status(
            OAuthCredentialState.unknown_error,
            remediation=(
                "Unable to reach Google's authorization server. "
                "Check your network connectivity and try again."
            ),
        )

        mock_probe = AsyncMock(return_value=probe_result)
        with patch.dict("os.environ", _BASE_ENV, clear=False), patch(_PROBE_PATCH, mock_probe):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(STATUS_URL)

        assert resp.status_code == 200
        body = resp.json()
        assert body["google"]["state"] == OAuthCredentialState.unknown_error
        assert body["google"]["connected"] is False
        remediation = body["google"]["remediation"].lower()
        assert "network" in remediation or "reach" in remediation or "connectivity" in remediation


# ---------------------------------------------------------------------------
# Tests: _probe_google_token unit tests (test the probe logic directly)
# These tests patch httpx.AsyncClient at the module level only for the
# duration of the probe call — no ASGI transport is involved.
# ---------------------------------------------------------------------------


class TestProbeGoogleToken:
    """Unit tests for _probe_google_token: covers HTTP response classification."""

    async def test_probe_connected_with_full_scopes(self, app):
        """Successful token refresh with required scopes → connected status."""
        full_scope = (
            "https://www.googleapis.com/auth/gmail.modify https://www.googleapis.com/auth/calendar"
        )
        mock_class = _make_mock_http_client(
            _mock_response(body={"access_token": "tok", "scope": full_scope})
        )
        with patch(_HTTPX_CLIENT_PATCH, mock_class):
            result = await _probe_google_token(
                client_id="test-id",
                client_secret="test-secret",
                refresh_token="refresh-token",
            )

        assert result.state == OAuthCredentialState.connected
        assert result.connected is True
        assert result.scopes_granted is not None

    async def test_probe_expired_on_invalid_grant(self, app):
        """invalid_grant HTTP response → expired status."""
        mock_class = _make_mock_http_client(
            _mock_response(
                status_code=400,
                body={"error": "invalid_grant", "error_description": "Token has been revoked"},
            )
        )
        with patch(_HTTPX_CLIENT_PATCH, mock_class):
            result = await _probe_google_token(
                client_id="test-id",
                client_secret="test-secret",
                refresh_token="expired-token",
            )

        assert result.state == OAuthCredentialState.expired
        assert result.connected is False

    async def test_probe_redirect_uri_mismatch_on_invalid_client(self, app):
        """invalid_client HTTP response → redirect_uri_mismatch status."""
        mock_class = _make_mock_http_client(
            _mock_response(
                status_code=401,
                body={"error": "invalid_client", "error_description": "bad creds"},
            )
        )
        with patch(_HTTPX_CLIENT_PATCH, mock_class):
            result = await _probe_google_token(
                client_id="test-id",
                client_secret="test-secret",
                refresh_token="some-token",
            )

        assert result.state == OAuthCredentialState.redirect_uri_mismatch
        assert result.connected is False

    async def test_probe_unapproved_tester_on_access_denied(self, app):
        """access_denied HTTP response → unapproved_tester status."""
        mock_class = _make_mock_http_client(
            _mock_response(
                status_code=400,
                body={"error": "access_denied", "error_description": "denied"},
            )
        )
        with patch(_HTTPX_CLIENT_PATCH, mock_class):
            result = await _probe_google_token(
                client_id="test-id",
                client_secret="test-secret",
                refresh_token="some-token",
            )

        assert result.state == OAuthCredentialState.unapproved_tester
        assert result.connected is False

    async def test_probe_unknown_on_unrecognized_error(self, app):
        """Unrecognized error code → unknown_error status."""
        mock_class = _make_mock_http_client(
            _mock_response(
                status_code=500,
                body={"error": "unknown_code", "error_description": "weird"},
            )
        )
        with patch(_HTTPX_CLIENT_PATCH, mock_class):
            result = await _probe_google_token(
                client_id="test-id",
                client_secret="test-secret",
                refresh_token="some-token",
            )

        assert result.state == OAuthCredentialState.unknown_error
        assert result.connected is False

    async def test_probe_network_error_returns_unknown_error(self, app):
        """Network failure → unknown_error status."""
        mock_class = _make_mock_http_client_raises(httpx.TransportError("conn refused"))
        with patch(_HTTPX_CLIENT_PATCH, mock_class):
            result = await _probe_google_token(
                client_id="test-id",
                client_secret="test-secret",
                refresh_token="some-token",
            )

        assert result.state == OAuthCredentialState.unknown_error
        assert result.connected is False
        assert "network" in result.remediation.lower() or "reach" in result.remediation.lower()

    async def test_probe_missing_scope(self, app):
        """Successful refresh with insufficient scopes → missing_scope status."""
        partial_scope = "https://www.googleapis.com/auth/gmail.readonly"
        mock_class = _make_mock_http_client(
            _mock_response(body={"access_token": "tok", "scope": partial_scope})
        )
        with patch(_HTTPX_CLIENT_PATCH, mock_class):
            result = await _probe_google_token(
                client_id="test-id",
                client_secret="test-secret",
                refresh_token="partial-token",
            )

        assert result.state == OAuthCredentialState.missing_scope
        assert result.connected is False
        assert result.scopes_granted is not None

    async def test_probe_connected_when_scope_absent(self, app):
        """Scope field absent from refresh response → connected (not missing_scope).

        Google may omit `scope` in refresh responses when scopes are unchanged.
        The probe must treat this as connected rather than flagging missing_scope.
        """
        mock_class = _make_mock_http_client(
            _mock_response(body={"access_token": "tok"})  # no "scope" key
        )
        with patch(_HTTPX_CLIENT_PATCH, mock_class):
            result = await _probe_google_token(
                client_id="test-id",
                client_secret="test-secret",
                refresh_token="some-token",
            )

        assert result.state == OAuthCredentialState.connected
        assert result.connected is True
        # scopes_granted is None when scope field is absent
        assert result.scopes_granted is None


# ---------------------------------------------------------------------------
# Tests: response envelope shape
# ---------------------------------------------------------------------------


class TestOAuthStatusResponseShape:
    async def test_response_has_google_key(self, app):
        """Top-level response always has a 'google' key."""
        app = _make_app(app)
        env = {**_BASE_ENV, "GOOGLE_REFRESH_TOKEN": ""}
        with patch.dict("os.environ", env, clear=False):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(STATUS_URL)

        assert resp.status_code == 200
        body = resp.json()
        assert "google" in body

    async def test_credential_status_has_required_fields(self, app):
        """OAuthCredentialStatus payload has required fields: state, connected, provider."""
        app = _make_app(app)
        env = {**_BASE_ENV, "GOOGLE_REFRESH_TOKEN": ""}
        with patch.dict("os.environ", env, clear=False):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(STATUS_URL)

        google = resp.json()["google"]
        assert "state" in google
        assert "connected" in google
        assert "provider" in google

    async def test_connected_false_always_has_remediation(self, app):
        """When connected=False, remediation must be a non-empty string."""
        app = _make_app(app)
        env = {**_BASE_ENV, "GOOGLE_REFRESH_TOKEN": ""}
        with patch.dict("os.environ", env, clear=False):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(STATUS_URL)

        google = resp.json()["google"]
        assert google["connected"] is False
        assert isinstance(google["remediation"], str)
        assert len(google["remediation"]) > 0

    async def test_endpoint_returns_200_always(self, app):
        """Status endpoint always returns HTTP 200 — errors are in the payload."""
        app = _make_app(app)
        # No env vars at all — extreme not_configured case
        env = {
            "GOOGLE_OAUTH_CLIENT_ID": "",
            "GOOGLE_OAUTH_CLIENT_SECRET": "",
            "GOOGLE_REFRESH_TOKEN": "",
        }
        with patch.dict("os.environ", env, clear=False):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(STATUS_URL)

        assert resp.status_code == 200
