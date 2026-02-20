"""Tests for secret redaction across all OAuth-related modules.

Verifies that credential material (client_secret, refresh_token, access_token)
never appears in:
- GoogleCredentials repr() / str()
- Log output from google_credentials, startup_guard, and oauth router
- OAuth callback response bodies (no raw tokens leaked to clients)
- Startup guard stderr error output

This acts as a cross-module security regression test.
"""

from __future__ import annotations

import logging
import unittest.mock as mock
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from butlers.api.routers import oauth as oauth_module
from butlers.google_credentials import (
    GoogleCredentials,
    load_google_credentials,
    resolve_google_credentials,
    store_google_credentials,
)
from butlers.startup_guard import (
    check_google_credentials,
    require_google_credentials_or_exit,
)

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SECRET_VALUE = "SUPER-SECRET-REFRESH-TOKEN-XYZ-12345"
_CLIENT_SECRET = "SUPER-SECRET-CLIENT-SECRET-ABC-67890"
_ACCESS_TOKEN = "ya29.SUPER-SECRET-ACCESS-TOKEN-XYZ"

FAKE_CREDS = GoogleCredentials(
    client_id="client-id-123.apps.googleusercontent.com",
    client_secret=_CLIENT_SECRET,
    refresh_token=_SECRET_VALUE,
    scope="https://www.googleapis.com/auth/gmail.readonly",
)

GOOGLE_ENV = {
    "GOOGLE_OAUTH_CLIENT_ID": "test-client-id.apps.googleusercontent.com",
    "GOOGLE_OAUTH_CLIENT_SECRET": _CLIENT_SECRET,
    "GOOGLE_OAUTH_REDIRECT_URI": "http://localhost:40200/api/oauth/google/callback",
}

FAKE_TOKEN_RESPONSE = {
    "access_token": _ACCESS_TOKEN,
    "refresh_token": _SECRET_VALUE,
    "scope": "https://www.googleapis.com/auth/gmail.readonly",
    "token_type": "Bearer",
    "expires_in": 3600,
}


def _make_app(
    *,
    db_client_id: str = "test-client-id.apps.googleusercontent.com",
    db_client_secret: str = "test-client-secret",
    db_refresh_token: str | None = "1//fake-refresh-token",
):
    from butlers.api.app import create_app

    app = create_app()
    secrets = {
        "GOOGLE_OAUTH_CLIENT_ID": db_client_id,
        "GOOGLE_OAUTH_CLIENT_SECRET": db_client_secret,
    }
    if db_refresh_token is not None:
        secrets["GOOGLE_REFRESH_TOKEN"] = db_refresh_token

    conn = AsyncMock()

    async def _fetchrow(_query: str, key: str):
        value = secrets.get(key)
        if not value:
            return None
        return {"secret_value": value}

    conn.fetchrow.side_effect = _fetchrow
    conn.execute = AsyncMock(return_value=None)

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
# GoogleCredentials: repr and str redaction
# ---------------------------------------------------------------------------


class TestGoogleCredentialsRedaction:
    def test_repr_does_not_leak_client_secret(self):
        """client_secret must be REDACTED in repr()."""
        r = repr(FAKE_CREDS)
        assert _CLIENT_SECRET not in r
        assert "REDACTED" in r

    def test_repr_does_not_leak_refresh_token(self):
        """refresh_token must be REDACTED in repr()."""
        r = repr(FAKE_CREDS)
        assert _SECRET_VALUE not in r
        assert "REDACTED" in r

    def test_str_does_not_leak_client_secret(self):
        """client_secret must not appear in str()."""
        s = str(FAKE_CREDS)
        assert _CLIENT_SECRET not in s

    def test_str_does_not_leak_refresh_token(self):
        """refresh_token must not appear in str()."""
        s = str(FAKE_CREDS)
        assert _SECRET_VALUE not in s

    def test_client_id_is_visible_in_repr(self):
        """client_id is safe to log/display — must appear in repr."""
        r = repr(FAKE_CREDS)
        assert "client-id-123.apps.googleusercontent.com" in r


# ---------------------------------------------------------------------------
# store_google_credentials: log redaction
# ---------------------------------------------------------------------------


class TestStoreCredentialsLogRedaction:
    async def test_store_does_not_log_client_secret(self, caplog: pytest.LogCaptureFixture) -> None:
        """store_google_credentials must not log client_secret at any level."""
        conn = AsyncMock()
        conn.execute.return_value = None

        with caplog.at_level(logging.DEBUG, logger="butlers.google_credentials"):
            await store_google_credentials(
                conn,
                client_id="public-id",
                client_secret=_CLIENT_SECRET,
                refresh_token=_SECRET_VALUE,
                scope="https://www.googleapis.com/auth/gmail.readonly",
            )

        assert _CLIENT_SECRET not in caplog.text
        assert _SECRET_VALUE not in caplog.text

    async def test_store_logs_safe_client_id(self, caplog: pytest.LogCaptureFixture) -> None:
        """store_google_credentials logs client_id (public) at DEBUG."""
        conn = AsyncMock()
        conn.execute.return_value = None

        with caplog.at_level(logging.DEBUG, logger="butlers.google_credentials"):
            await store_google_credentials(
                conn,
                client_id="public-id-is-ok",
                client_secret=_CLIENT_SECRET,
                refresh_token=_SECRET_VALUE,
            )

        assert "public-id-is-ok" in caplog.text


# ---------------------------------------------------------------------------
# load_google_credentials / resolve_google_credentials: log redaction
# ---------------------------------------------------------------------------


class TestLoadCredentialsLogRedaction:
    async def test_load_does_not_log_secrets(self, caplog: pytest.LogCaptureFixture) -> None:
        """load_google_credentials must not log secrets at any log level."""
        payload = {
            "client_id": "pub-id",
            "client_secret": _CLIENT_SECRET,
            "refresh_token": _SECRET_VALUE,
        }
        record = MagicMock()
        record.__getitem__ = lambda self, key: {"credentials": payload}[key]
        conn = AsyncMock()
        conn.fetchrow.return_value = record

        with caplog.at_level(logging.DEBUG, logger="butlers.google_credentials"):
            await load_google_credentials(conn)

        assert _CLIENT_SECRET not in caplog.text
        assert _SECRET_VALUE not in caplog.text

    async def test_resolve_does_not_log_secrets(self, caplog: pytest.LogCaptureFixture) -> None:
        """resolve_google_credentials must not log secret material."""
        payload = {
            "client_id": "pub-id",
            "client_secret": _CLIENT_SECRET,
            "refresh_token": _SECRET_VALUE,
        }
        record = MagicMock()
        record.__getitem__ = lambda self, key: {"credentials": payload}[key]
        conn = AsyncMock()
        conn.fetchrow.return_value = record

        with caplog.at_level(logging.DEBUG, logger="butlers.google_credentials"):
            await resolve_google_credentials(conn, caller="test")

        assert _CLIENT_SECRET not in caplog.text
        assert _SECRET_VALUE not in caplog.text


# ---------------------------------------------------------------------------
# startup_guard: stderr redaction
# ---------------------------------------------------------------------------


class TestStartupGuardSecretRedaction:
    def test_stderr_does_not_echo_partial_secret_values(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        """Startup guard stderr must not echo any partial credential values."""
        partial = {"GMAIL_CLIENT_ID": "PARTIAL-SECRET-CLIENT-ID-VALUE"}

        with mock.patch.dict("os.environ", partial, clear=True):
            with pytest.raises(SystemExit):
                require_google_credentials_or_exit(caller="test")

        captured = capsys.readouterr()
        assert "PARTIAL-SECRET-CLIENT-ID-VALUE" not in captured.err
        assert "PARTIAL-SECRET-CLIENT-ID-VALUE" not in captured.out

    def test_check_does_not_include_secret_values_in_message(self) -> None:
        """check_google_credentials() message must not contain any env var values."""
        partial = {
            "GMAIL_CLIENT_ID": "LEAKED-CLIENT-ID-VALUE",
            "GMAIL_CLIENT_SECRET": "LEAKED-SECRET-VALUE",
        }

        with mock.patch.dict("os.environ", partial, clear=True):
            result = check_google_credentials()

        assert "LEAKED-CLIENT-ID-VALUE" not in result.message
        assert "LEAKED-SECRET-VALUE" not in result.message
        assert "LEAKED-CLIENT-ID-VALUE" not in result.remediation
        assert "LEAKED-SECRET-VALUE" not in result.remediation


# ---------------------------------------------------------------------------
# OAuth callback response: no raw tokens in response body
# ---------------------------------------------------------------------------


class TestOAuthCallbackResponseRedaction:
    """Verify that the OAuth callback response never leaks raw token material."""

    async def test_callback_success_does_not_leak_access_token(self) -> None:
        """Successful callback response must not include the raw access_token."""
        from butlers.api.routers.oauth import (
            _clear_state_store,
            _generate_state,
            _store_state,
        )

        _clear_state_store()
        app = _make_app()
        state = _generate_state()
        _store_state(state)

        mock_exchange = AsyncMock(return_value=FAKE_TOKEN_RESPONSE)
        with (
            patch.dict("os.environ", GOOGLE_ENV, clear=False),
            patch(
                "butlers.api.routers.oauth._exchange_code_for_tokens",
                mock_exchange,
            ),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                resp = await client.get(
                    "/api/oauth/google/callback",
                    params={"code": "4/code", "state": state},
                )

        _clear_state_store()

        assert resp.status_code == 200
        raw_body = resp.text
        # Access token must NEVER be in the response body
        assert _ACCESS_TOKEN not in raw_body

    async def test_callback_success_does_not_leak_refresh_token(self) -> None:
        """Successful callback response must not include the raw refresh_token."""
        from butlers.api.routers.oauth import (
            _clear_state_store,
            _generate_state,
            _store_state,
        )

        _clear_state_store()
        app = _make_app()
        state = _generate_state()
        _store_state(state)

        mock_exchange = AsyncMock(return_value=FAKE_TOKEN_RESPONSE)
        with (
            patch.dict("os.environ", GOOGLE_ENV, clear=False),
            patch(
                "butlers.api.routers.oauth._exchange_code_for_tokens",
                mock_exchange,
            ),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                resp = await client.get(
                    "/api/oauth/google/callback",
                    params={"code": "4/code", "state": state},
                )

        _clear_state_store()

        assert resp.status_code == 200
        raw_body = resp.text
        # Refresh token must NEVER be in the response body
        assert _SECRET_VALUE not in raw_body

    async def test_callback_error_does_not_leak_google_internal_error_codes(
        self,
    ) -> None:
        """Unknown Google error codes are sanitized — never echoed back to client."""
        from butlers.api.app import create_app

        app = create_app()

        with patch.dict("os.environ", GOOGLE_ENV, clear=False):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                resp = await client.get(
                    "/api/oauth/google/callback",
                    params={"error": "internal_weird_error_code_9876"},
                )

        assert resp.status_code == 400
        # The raw internal error code must not appear in the message
        assert "internal_weird_error_code_9876" not in resp.json()["message"]
