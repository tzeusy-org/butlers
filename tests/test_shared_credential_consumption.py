"""Tests for shared Google credential consumption by Gmail and Calendar startup paths.

Verifies DB-backed credential behavior for:
- Shared credential persistence helpers (`store_google_credentials`, `load_google_credentials`)
- Google credential resolution (`resolve_google_credentials`) with no env fallback
- Gmail connector config hydration from explicitly injected DB-resolved secrets
- Calendar module startup with CredentialStore-backed resolution
"""

from __future__ import annotations

import json
import unittest.mock as mock
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.google_credentials import (
    GoogleCredentials,
    MissingGoogleCredentialsError,
    load_google_credentials,
    resolve_google_credentials,
    store_google_credentials,
)

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SHARED_CREDS = {
    "client_id": "shared-client-id.apps.googleusercontent.com",
    "client_secret": "shared-client-secret-abc",
    "refresh_token": "1//shared-refresh-token-xyz",
    "scope": (
        "https://www.googleapis.com/auth/gmail.modify https://www.googleapis.com/auth/calendar"
    ),
}

# Env vars set by the OAuth bootstrap flow
_OAUTH_BOOTSTRAP_ENV = {
    "GOOGLE_OAUTH_CLIENT_ID": _SHARED_CREDS["client_id"],
    "GOOGLE_OAUTH_CLIENT_SECRET": _SHARED_CREDS["client_secret"],
    "GOOGLE_REFRESH_TOKEN": _SHARED_CREDS["refresh_token"],
}

# Legacy env vars are no longer accepted by runtime resolution paths.
_LEGACY_GMAIL_ENV = {
    "GMAIL_CLIENT_ID": _SHARED_CREDS["client_id"],
    "GMAIL_CLIENT_SECRET": _SHARED_CREDS["client_secret"],
    "GMAIL_REFRESH_TOKEN": _SHARED_CREDS["refresh_token"],
}

_LEGACY_CALENDAR_JSON_BLOB_ENV = {
    "BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON": json.dumps(
        {
            "client_id": _SHARED_CREDS["client_id"],
            "client_secret": _SHARED_CREDS["client_secret"],
            "refresh_token": _SHARED_CREDS["refresh_token"],
        }
    )
}


def _make_conn(row_data: dict | None = None) -> AsyncMock:
    """Build a fake asyncpg connection that returns a stored credential row."""
    conn = AsyncMock()
    conn.fetchrow.return_value = row_data
    conn.execute.return_value = None
    return conn


def _make_db_conn_with_creds(creds: dict) -> AsyncMock:
    """Return a fake conn that serves the given credentials dict."""
    return _make_conn(row_data={"credentials": creds})


# ---------------------------------------------------------------------------
# Shared GoogleCredentials model contract
# ---------------------------------------------------------------------------


class TestGoogleCredentialsModelContract:
    def test_from_env_factory_is_removed(self) -> None:
        assert not hasattr(GoogleCredentials, "from_env")


class TestGoogleCredentialsFromJsonForCalendar:
    """Verify calendar compatibility for stored JSON payload parsing."""

    def test_calendar_from_json_accepts_shared_payload(self) -> None:
        """Calendar helper parses shared credential payload JSON."""
        from butlers.modules.calendar import _GoogleOAuthCredentials

        shared_blob = json.dumps(
            {
                "client_id": _SHARED_CREDS["client_id"],
                "client_secret": _SHARED_CREDS["client_secret"],
                "refresh_token": _SHARED_CREDS["refresh_token"],
                "scope": _SHARED_CREDS["scope"],
                "stored_at": "2026-02-19T00:00:00+00:00",
            }
        )
        creds = _GoogleOAuthCredentials.from_json(shared_blob)

        assert creds.client_id == _SHARED_CREDS["client_id"]
        assert creds.client_secret == _SHARED_CREDS["client_secret"]
        assert creds.refresh_token == _SHARED_CREDS["refresh_token"]


# ---------------------------------------------------------------------------
# Gmail connector config consumes DB-injected credentials
# ---------------------------------------------------------------------------


class TestGmailConnectorAcceptsSharedOAuthBootstrapCredentials:
    """Verify Gmail connector config uses explicit injected credentials."""

    def test_gmail_connector_config_uses_injected_credentials(self) -> None:
        """GmailConnectorConfig.from_env() uses explicitly injected credential values."""
        from butlers.connectors.gmail import GmailConnectorConfig

        required_non_creds = {
            "SWITCHBOARD_MCP_URL": "http://localhost:9000/mcp",
            "CONNECTOR_ENDPOINT_IDENTITY": "gmail:user:test@gmail.com",
            "CONNECTOR_CURSOR_PATH": "/tmp/test_cursor",
        }
        env = {
            **required_non_creds,
            **_OAUTH_BOOTSTRAP_ENV,
        }

        with mock.patch.dict("os.environ", env, clear=True):
            config = GmailConnectorConfig.from_env(
                gmail_client_id=_SHARED_CREDS["client_id"],
                gmail_client_secret=_SHARED_CREDS["client_secret"],
                gmail_refresh_token=_SHARED_CREDS["refresh_token"],
            )

        assert config.gmail_client_id == _SHARED_CREDS["client_id"]
        assert config.gmail_client_secret == _SHARED_CREDS["client_secret"]
        assert config.gmail_refresh_token == _SHARED_CREDS["refresh_token"]

    def test_gmail_connector_config_ignores_env_credential_vars(self) -> None:
        """Injected credentials take precedence over any env credential values."""
        from butlers.connectors.gmail import GmailConnectorConfig

        required_non_creds = {
            "SWITCHBOARD_MCP_URL": "http://localhost:9000/mcp",
            "CONNECTOR_ENDPOINT_IDENTITY": "gmail:user:test@gmail.com",
            "CONNECTOR_CURSOR_PATH": "/tmp/test_cursor",
        }
        env = {
            **required_non_creds,
            **_LEGACY_GMAIL_ENV,
            **_OAUTH_BOOTSTRAP_ENV,
        }

        with mock.patch.dict("os.environ", env, clear=True):
            config = GmailConnectorConfig.from_env(
                gmail_client_id="db-client-id",
                gmail_client_secret="db-client-secret",
                gmail_refresh_token="db-refresh-token",
            )
        assert config.gmail_client_id == "db-client-id"
        assert config.gmail_client_secret == "db-client-secret"
        assert config.gmail_refresh_token == "db-refresh-token"

    def test_gmail_connector_config_fails_with_empty_injected_credentials(self) -> None:
        """GmailConnectorConfig.from_env() raises when injected credentials are empty."""
        from butlers.connectors.gmail import GmailConnectorConfig

        required_non_creds = {
            "SWITCHBOARD_MCP_URL": "http://localhost:9000/mcp",
            "CONNECTOR_ENDPOINT_IDENTITY": "gmail:user:test@gmail.com",
            "CONNECTOR_CURSOR_PATH": "/tmp/test_cursor",
        }

        with mock.patch.dict("os.environ", required_non_creds, clear=True):
            with pytest.raises(ValueError, match="DB-resolved Gmail credentials missing"):
                GmailConnectorConfig.from_env(
                    gmail_client_id="",
                    gmail_client_secret="client-secret",
                    gmail_refresh_token="refresh-token",
                )


# ---------------------------------------------------------------------------
# Calendar module accepts canonical env credentials
# ---------------------------------------------------------------------------


class TestCalendarModuleAcceptsSharedCredentialsFormat:
    """Verify Calendar module credential resolution against shared credential format."""

    def test_calendar_from_env_factory_is_removed(self) -> None:
        from butlers.modules.calendar import _GoogleOAuthCredentials

        assert not hasattr(_GoogleOAuthCredentials, "from_env")

    def test_shared_blob_is_compatible_with_calendar_json_parser(self) -> None:
        """A shared credential blob is parseable via Calendar's from_json parser."""
        from butlers.modules.calendar import _GoogleOAuthCredentials

        shared_blob = json.dumps(
            {
                "client_id": _SHARED_CREDS["client_id"],
                "client_secret": _SHARED_CREDS["client_secret"],
                "refresh_token": _SHARED_CREDS["refresh_token"],
                "scope": _SHARED_CREDS["scope"],
                "stored_at": "2026-02-19T00:00:00+00:00",
            }
        )
        creds = _GoogleOAuthCredentials.from_json(shared_blob)

        assert creds.client_id == _SHARED_CREDS["client_id"]
        assert creds.client_secret == _SHARED_CREDS["client_secret"]
        assert creds.refresh_token == _SHARED_CREDS["refresh_token"]


# ---------------------------------------------------------------------------
# resolve_google_credentials: both callers get same credentials from DB
# ---------------------------------------------------------------------------


class TestResolveSharedCredentialsBothCallers:
    """Both gmail and calendar callers resolve the same credentials from DB."""

    async def test_gmail_caller_resolves_from_db(self) -> None:
        """resolve_google_credentials(caller='gmail') returns DB credentials."""
        conn = _make_db_conn_with_creds(_SHARED_CREDS)

        result = await resolve_google_credentials(conn, caller="gmail")

        assert result.client_id == _SHARED_CREDS["client_id"]
        assert result.client_secret == _SHARED_CREDS["client_secret"]
        assert result.refresh_token == _SHARED_CREDS["refresh_token"]

    async def test_calendar_caller_resolves_from_db(self) -> None:
        """resolve_google_credentials(caller='calendar') returns same DB credentials."""
        conn = _make_db_conn_with_creds(_SHARED_CREDS)

        result = await resolve_google_credentials(conn, caller="calendar")

        assert result.client_id == _SHARED_CREDS["client_id"]
        assert result.client_secret == _SHARED_CREDS["client_secret"]
        assert result.refresh_token == _SHARED_CREDS["refresh_token"]

    async def test_both_callers_get_identical_credentials_from_same_db_record(
        self,
    ) -> None:
        """Gmail and Calendar resolve identical credentials from the same DB record."""
        conn = _make_db_conn_with_creds(_SHARED_CREDS)

        gmail_creds = await resolve_google_credentials(conn, caller="gmail")
        calendar_creds = await resolve_google_credentials(conn, caller="calendar")

        # Both must get the same credential material
        assert gmail_creds.client_id == calendar_creds.client_id
        assert gmail_creds.client_secret == calendar_creds.client_secret
        assert gmail_creds.refresh_token == calendar_creds.refresh_token

    async def test_resolve_rejects_legacy_gmail_env_vars_when_db_empty(self) -> None:
        """resolve_google_credentials rejects legacy GMAIL_* env vars when DB is empty."""
        conn = _make_conn(row_data=None)  # Empty DB

        with mock.patch.dict("os.environ", _LEGACY_GMAIL_ENV, clear=True):
            with pytest.raises(MissingGoogleCredentialsError):
                await resolve_google_credentials(conn, caller="gmail")

    async def test_resolve_raises_when_db_empty_even_if_env_is_populated(self) -> None:
        """resolve_google_credentials is DB-only and ignores env variables."""
        conn = _make_conn(row_data=None)  # Empty DB

        with mock.patch.dict("os.environ", _OAUTH_BOOTSTRAP_ENV, clear=True):
            with pytest.raises(MissingGoogleCredentialsError):
                await resolve_google_credentials(conn, caller="calendar")

    async def test_resolve_raises_when_db_empty_and_no_env_vars(self) -> None:
        """resolve_google_credentials raises MissingGoogleCredentialsError when fully unset."""
        conn = _make_conn(row_data=None)  # Empty DB

        with pytest.raises(MissingGoogleCredentialsError) as exc_info:
            await resolve_google_credentials(conn, caller="gmail")

        msg = str(exc_info.value)
        # Must explain how to fix the problem
        assert "bootstrap" in msg.lower() or "oauth" in msg.lower()
        assert "gmail" in msg.lower()  # Caller name in message

    async def test_resolve_db_first_ignores_env_when_db_has_credentials(self) -> None:
        """When DB has credentials, env vars are ignored (DB takes priority)."""
        conn = _make_db_conn_with_creds(_SHARED_CREDS)
        different_env = {
            "GOOGLE_OAUTH_CLIENT_ID": "different-id-from-env",
            "GOOGLE_OAUTH_CLIENT_SECRET": "different-secret-from-env",
            "GOOGLE_REFRESH_TOKEN": "different-token-from-env",
        }

        with mock.patch.dict("os.environ", different_env, clear=True):
            result = await resolve_google_credentials(conn, caller="test")

        # DB credentials take precedence over env vars
        assert result.client_id == _SHARED_CREDS["client_id"]
        assert result.client_secret == _SHARED_CREDS["client_secret"]


# ---------------------------------------------------------------------------
# store_google_credentials → load_google_credentials round-trip
# ---------------------------------------------------------------------------


class TestStoreAndLoadRoundTrip:
    """Verify that stored credentials are recoverable via load_google_credentials."""

    async def test_store_then_load_returns_same_credentials(self) -> None:
        """Round-trip: store + load returns identical credential values."""
        stored_data: dict = {}

        async def fake_execute(sql: str, *args: object) -> None:
            # Capture the stored JSON payload
            if "INSERT INTO" in sql and len(args) >= 2:
                stored_data["key"] = args[0]
                stored_data["payload"] = json.loads(args[1])

        async def fake_fetchrow(sql: str, *args: object):
            if stored_data:
                return {"credentials": stored_data["payload"]}
            return None

        conn = AsyncMock()
        conn.execute.side_effect = fake_execute
        conn.fetchrow.side_effect = fake_fetchrow

        # Store credentials
        await store_google_credentials(
            conn,
            client_id=_SHARED_CREDS["client_id"],
            client_secret=_SHARED_CREDS["client_secret"],
            refresh_token=_SHARED_CREDS["refresh_token"],
            scope=_SHARED_CREDS["scope"],
        )

        # Load them back
        result = await load_google_credentials(conn)

        assert result is not None
        assert result.client_id == _SHARED_CREDS["client_id"]
        assert result.client_secret == _SHARED_CREDS["client_secret"]
        assert result.refresh_token == _SHARED_CREDS["refresh_token"]
        assert result.scope == _SHARED_CREDS["scope"]

    async def test_store_without_scope_round_trips_correctly(self) -> None:
        """Credentials stored without scope have scope=None after load."""
        stored_data: dict = {}

        async def fake_execute(sql: str, *args: object) -> None:
            if "INSERT INTO" in sql and len(args) >= 2:
                stored_data["payload"] = json.loads(args[1])

        async def fake_fetchrow(sql: str, *args: object):
            if stored_data:
                return {"credentials": stored_data["payload"]}
            return None

        conn = AsyncMock()
        conn.execute.side_effect = fake_execute
        conn.fetchrow.side_effect = fake_fetchrow

        await store_google_credentials(
            conn,
            client_id=_SHARED_CREDS["client_id"],
            client_secret=_SHARED_CREDS["client_secret"],
            refresh_token=_SHARED_CREDS["refresh_token"],
            # No scope
        )

        result = await load_google_credentials(conn)
        assert result is not None
        assert result.scope is None


# ---------------------------------------------------------------------------
# CalendarModule.on_startup DB-first credential path
# ---------------------------------------------------------------------------


class TestCalendarModuleOnStartupDbFirst:
    """Verify CalendarModule.on_startup resolves credentials from DB when pool available."""

    async def test_on_startup_uses_credential_store_when_available(self) -> None:
        """CalendarModule resolves Google credentials from credential_store during on_startup."""
        from butlers.modules.calendar import CalendarModule

        async def _resolve(key: str, env_fallback: bool = True) -> str | None:
            values = {
                "GOOGLE_OAUTH_CLIENT_ID": _SHARED_CREDS["client_id"],
                "GOOGLE_OAUTH_CLIENT_SECRET": _SHARED_CREDS["client_secret"],
                "GOOGLE_REFRESH_TOKEN": _SHARED_CREDS["refresh_token"],
            }
            assert env_fallback is False
            return values.get(key)

        credential_store = AsyncMock()
        credential_store.resolve.side_effect = _resolve
        db = MagicMock()

        mod = CalendarModule()
        with mock.patch.dict("os.environ", {}, clear=True):
            await mod.on_startup(
                {"provider": "google", "calendar_id": "primary"},
                db=db,
                credential_store=credential_store,
            )

        provider = getattr(mod, "_provider")
        assert provider is not None
        assert provider.name == "google"
        # Verify the credentials on the OAuth client
        assert provider._oauth._credentials.client_id == _SHARED_CREDS["client_id"]
        assert provider._oauth._credentials.refresh_token == _SHARED_CREDS["refresh_token"]

    async def test_on_startup_raises_when_no_credential_store_is_provided(self) -> None:
        """CalendarModule startup fails without a DB-backed credential store."""
        from butlers.modules.calendar import CalendarModule

        mod = CalendarModule()
        with pytest.raises(RuntimeError):
            await mod.on_startup({"provider": "google", "calendar_id": "primary"}, db=None)
