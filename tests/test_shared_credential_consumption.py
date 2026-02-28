"""Tests for shared Google credential consumption by Gmail and Calendar startup paths.

Verifies DB-backed credential behavior for:
- Shared credential persistence helpers (`store_google_credentials`, `load_google_credentials`)
- Google credential resolution (`resolve_google_credentials`) with no env fallback
- Gmail connector config hydration from explicitly injected DB-resolved secrets
- Calendar module startup with CredentialStore-backed resolution

Behavior matrix
---------------
Layer: CredentialStore / google_credentials module
  - resolve_google_credentials: DB-only (ignores all env vars at this layer)
  - store_google_credentials -> load_google_credentials round-trip

Layer: gmail connector
  - GmailConnectorConfig.from_env() accepts injected DB-resolved credentials
  - Injected credentials take priority over any env vars

Layer: calendar module model
  - _GoogleOAuthCredentials.from_json parses shared credential payload
  - from_env factory has been removed

Cross-layer coverage note:
  - resolve_google_credentials basic success/failure is canonical in
    tests/test_google_credentials_credential_store.py.
  - CalendarModule.on_startup credential resolution is canonical in
    tests/modules/test_module_credential_resolution.py
    (TestCalendarModuleCredentialStore).
"""

from __future__ import annotations

import json
import unittest.mock as mock
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.credential_store import CredentialStore
from butlers.google_credentials import (
    KEY_CLIENT_ID,
    KEY_CLIENT_SECRET,
    KEY_REFRESH_TOKEN,
    KEY_SCOPES,
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


def _make_pool_with_values(key_to_value: dict[str, str | None]) -> MagicMock:
    """Build a pool mock for CredentialStore that returns per-key values."""

    async def _fetchrow(query: str, key: str) -> MagicMock | None:
        val = key_to_value.get(key)
        if val is None:
            return None
        row = MagicMock()
        row.__getitem__ = lambda self, k: val if k == "secret_value" else None
        return row

    async def _execute(*args, **kwargs) -> str:
        return "INSERT 0 1"

    conn = MagicMock()
    conn.fetchrow = _fetchrow
    conn.execute = _execute

    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)

    pool = MagicMock()
    pool.acquire.return_value = cm
    return pool


def _make_empty_store() -> CredentialStore:
    """CredentialStore backed by an empty pool."""
    return CredentialStore(_make_pool_with_values({}))


def _make_store_with_creds(creds: dict) -> CredentialStore:
    """CredentialStore backed by a pool with the given credential values."""
    return CredentialStore(
        _make_pool_with_values(
            {
                KEY_CLIENT_ID: creds.get("client_id"),
                KEY_CLIENT_SECRET: creds.get("client_secret"),
                KEY_REFRESH_TOKEN: creds.get("refresh_token"),
                KEY_SCOPES: creds.get("scope"),
            }
        )
    )


# ---------------------------------------------------------------------------
# Shared GoogleCredentials model contract
# ---------------------------------------------------------------------------


class TestGoogleCredentialsModelContract:
    def test_from_env_factory_is_removed(self) -> None:
        assert not hasattr(GoogleCredentials, "from_env")


# ---------------------------------------------------------------------------
# Calendar model: from_env factory removed; from_json parses shared payload
# ---------------------------------------------------------------------------


class TestCalendarModuleAcceptsSharedCredentialsFormat:
    """Calendar module credential model compatibility with shared credential format."""

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
# resolve_google_credentials: DB-only contract
#
# Basic success/failure paths are canonical in test_google_credentials_credential_store.py.
# This class verifies the cross-cutting contract that the function is
# DB-only and ignores all env variable variants (legacy and bootstrap).
# ---------------------------------------------------------------------------


class TestResolveGoogleCredentialsIsDbOnly:
    """resolve_google_credentials never falls back to env vars at any boundary."""

    async def test_resolve_raises_with_legacy_gmail_env_when_db_empty(self) -> None:
        """Legacy GMAIL_* env vars are rejected; DB is the only source."""
        store = _make_empty_store()
        with mock.patch.dict("os.environ", _LEGACY_GMAIL_ENV, clear=True):
            with pytest.raises(MissingGoogleCredentialsError):
                await resolve_google_credentials(store, caller="gmail")

    async def test_resolve_raises_with_bootstrap_env_when_db_empty(self) -> None:
        """Canonical GOOGLE_OAUTH_* env vars are also ignored; DB is required."""
        store = _make_empty_store()
        with mock.patch.dict("os.environ", _OAUTH_BOOTSTRAP_ENV, clear=True):
            with pytest.raises(MissingGoogleCredentialsError):
                await resolve_google_credentials(store, caller="calendar")

    async def test_resolve_error_message_includes_caller_and_guidance(self) -> None:
        """Error message names the caller and includes remediation guidance."""
        store = _make_empty_store()
        with pytest.raises(MissingGoogleCredentialsError) as exc_info:
            await resolve_google_credentials(store, caller="gmail")
        msg = str(exc_info.value)
        assert "bootstrap" in msg.lower() or "oauth" in msg.lower()
        assert "gmail" in msg.lower()

    async def test_resolve_db_creds_take_priority_over_bootstrap_env(self) -> None:
        """When DB has credentials, bootstrap env vars are ignored (DB wins)."""
        store = _make_store_with_creds(_SHARED_CREDS)
        ci_pool = MagicMock()
        different_env = {
            "GOOGLE_OAUTH_CLIENT_ID": "different-id-from-env",
            "GOOGLE_OAUTH_CLIENT_SECRET": "different-secret-from-env",
            "GOOGLE_REFRESH_TOKEN": "different-token-from-env",
        }

        with (
            mock.patch.dict("os.environ", different_env, clear=True),
            mock.patch(
                "butlers.google_credentials.resolve_owner_contact_info",
                new_callable=AsyncMock,
                return_value=_SHARED_CREDS["refresh_token"],
            ),
        ):
            result = await resolve_google_credentials(store, pool=ci_pool, caller="test")

        assert result.client_id == _SHARED_CREDS["client_id"]
        assert result.client_secret == _SHARED_CREDS["client_secret"]
        assert result.refresh_token == _SHARED_CREDS["refresh_token"]


# ---------------------------------------------------------------------------
# store_google_credentials -> load_google_credentials round-trip
# ---------------------------------------------------------------------------


class TestStoreAndLoadRoundTrip:
    """Verify that stored credentials are recoverable via load_google_credentials."""

    async def test_store_then_load_returns_same_credentials(self) -> None:
        """Round-trip: store + load returns identical credential values.

        Refresh token is stored in and read from contact_info (not butler_secrets).
        """
        stored: dict[str, str] = {}

        store = MagicMock(spec=CredentialStore)

        async def fake_store(key: str, value: str, **kwargs) -> None:
            stored[key] = value

        async def fake_load(key: str) -> str | None:
            return stored.get(key)

        store.store = AsyncMock(side_effect=fake_store)
        store.load = AsyncMock(side_effect=fake_load)
        ci_pool = MagicMock()

        with (
            patch(
                "butlers.google_credentials.upsert_owner_contact_info",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "butlers.google_credentials.resolve_owner_contact_info",
                new_callable=AsyncMock,
                return_value=_SHARED_CREDS["refresh_token"],
            ),
        ):
            await store_google_credentials(
                store,
                pool=ci_pool,
                client_id=_SHARED_CREDS["client_id"],
                client_secret=_SHARED_CREDS["client_secret"],
                refresh_token=_SHARED_CREDS["refresh_token"],
                scope=_SHARED_CREDS["scope"],
            )

            result = await load_google_credentials(store, pool=ci_pool)

        assert result is not None
        assert result.client_id == _SHARED_CREDS["client_id"]
        assert result.client_secret == _SHARED_CREDS["client_secret"]
        assert result.refresh_token == _SHARED_CREDS["refresh_token"]
        assert result.scope == _SHARED_CREDS["scope"]

    async def test_store_then_load_with_pool_round_trips_via_contact_info(self) -> None:
        """Round-trip with pool: refresh token goes through contact_info."""
        stored: dict[str, str] = {}

        store = MagicMock(spec=CredentialStore)

        async def fake_store(key: str, value: str, **kwargs) -> None:
            stored[key] = value

        async def fake_load(key: str) -> str | None:
            return stored.get(key)

        store.store = AsyncMock(side_effect=fake_store)
        store.load = AsyncMock(side_effect=fake_load)
        ci_pool = MagicMock()

        with (
            patch(
                "butlers.google_credentials.upsert_owner_contact_info",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "butlers.google_credentials.resolve_owner_contact_info",
                new_callable=AsyncMock,
                return_value=_SHARED_CREDS["refresh_token"],
            ),
        ):
            await store_google_credentials(
                store,
                pool=ci_pool,
                client_id=_SHARED_CREDS["client_id"],
                client_secret=_SHARED_CREDS["client_secret"],
                refresh_token=_SHARED_CREDS["refresh_token"],
                scope=_SHARED_CREDS["scope"],
            )

            result = await load_google_credentials(store, pool=ci_pool)

        assert result is not None
        assert result.refresh_token == _SHARED_CREDS["refresh_token"]

    async def test_store_without_scope_round_trips_correctly(self) -> None:
        """Credentials stored without scope have scope=None after load."""
        stored: dict[str, str] = {}

        store = MagicMock(spec=CredentialStore)

        async def fake_store(key: str, value: str, **kwargs) -> None:
            stored[key] = value

        async def fake_load(key: str) -> str | None:
            return stored.get(key)

        store.store = AsyncMock(side_effect=fake_store)
        store.load = AsyncMock(side_effect=fake_load)
        ci_pool = MagicMock()

        with (
            patch(
                "butlers.google_credentials.upsert_owner_contact_info",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "butlers.google_credentials.resolve_owner_contact_info",
                new_callable=AsyncMock,
                return_value=_SHARED_CREDS["refresh_token"],
            ),
        ):
            await store_google_credentials(
                store,
                pool=ci_pool,
                client_id=_SHARED_CREDS["client_id"],
                client_secret=_SHARED_CREDS["client_secret"],
                refresh_token=_SHARED_CREDS["refresh_token"],
                # No scope
            )

            result = await load_google_credentials(store, pool=ci_pool)
        assert result is not None
        assert result.scope is None
