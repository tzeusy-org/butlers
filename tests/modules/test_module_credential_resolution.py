"""Tests for CredentialStore-based credential resolution in EmailModule,
TelegramModule, and CalendarModule.

All tests use a minimal in-memory CredentialStore mock — no real DB required.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.credential_store import CredentialStore
from butlers.modules.email import EmailModule
from butlers.modules.telegram import TelegramModule

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_credential_store(**resolved: str) -> CredentialStore:
    """Build a CredentialStore mock that resolves the given key→value pairs.

    Keys not in ``resolved`` return ``None``.  env_fallback is honoured
    by returning None when ``env_fallback=False`` and the key is unknown.
    """
    store = MagicMock(spec=CredentialStore)

    async def _resolve(key: str, *, env_fallback: bool = True) -> str | None:
        val = resolved.get(key)
        if val is not None:
            return val
        if env_fallback:
            return os.environ.get(key)
        return None

    store.resolve = AsyncMock(side_effect=_resolve)
    return store


# ---------------------------------------------------------------------------
# EmailModule — CredentialStore integration
# ---------------------------------------------------------------------------


class TestEmailModuleCredentialStore:
    """Verify EmailModule resolves credentials via CredentialStore at startup."""

    async def test_on_startup_caches_bot_credentials(self) -> None:
        """Bot credentials resolved from store are cached in _resolved_credentials."""
        store = _make_credential_store(
            BUTLER_EMAIL_ADDRESS="bot@example.com",
            BUTLER_EMAIL_PASSWORD="bot-secret",
        )
        mod = EmailModule()
        await mod.on_startup(config=None, db=None, credential_store=store)

        assert mod._resolved_credentials["BUTLER_EMAIL_ADDRESS"] == "bot@example.com"
        assert mod._resolved_credentials["BUTLER_EMAIL_PASSWORD"] == "bot-secret"

    async def test_on_startup_does_not_resolve_user_credentials_from_store(self) -> None:
        """User-scope credentials are NOT resolved from CredentialStore.

        User-scope credentials come exclusively from owner contact_info.
        CredentialStore is only used for bot-scope.
        """
        store = _make_credential_store(
            BUTLER_EMAIL_ADDRESS="bot@example.com",
            BUTLER_EMAIL_PASSWORD="bot-secret",
            USER_EMAIL_ADDRESS="user@example.com",
            USER_EMAIL_PASSWORD="user-secret",
        )
        mod = EmailModule()
        config = {"user": {"enabled": True}}
        await mod.on_startup(config=config, db=None, credential_store=store)

        # User-scope keys should NOT be in cache (only contact_info provides them)
        assert "USER_EMAIL_ADDRESS" not in mod._resolved_credentials
        assert "USER_EMAIL_PASSWORD" not in mod._resolved_credentials
        # Bot-scope should be resolved from store
        assert mod._resolved_credentials["BUTLER_EMAIL_ADDRESS"] == "bot@example.com"

    async def test_get_credentials_uses_cached_store_value(self) -> None:
        """_get_credentials() returns cached value even when env var differs."""
        store = _make_credential_store(
            BUTLER_EMAIL_ADDRESS="db@example.com",
            BUTLER_EMAIL_PASSWORD="db-secret",
        )
        mod = EmailModule()
        with patch.dict(
            os.environ,
            {
                "BUTLER_EMAIL_ADDRESS": "env@example.com",
                "BUTLER_EMAIL_PASSWORD": "env-secret",
            },
        ):
            await mod.on_startup(config=None, db=None, credential_store=store)
            address, password = mod._get_credentials()

        assert address == "db@example.com"
        assert password == "db-secret"

    async def test_get_credentials_raises_without_store_or_contact_info(self) -> None:
        """Without a store or contact_info, _get_credentials() raises RuntimeError.

        There is no os.environ fallback — credentials must come from
        CredentialStore (bot-scope) or owner contact_info (user-scope).
        """
        mod = EmailModule()
        await mod.on_startup(config=None, db=None, credential_store=None)
        with pytest.raises(RuntimeError, match="Missing email credentials"):
            mod._get_credentials()

    async def test_get_credentials_raises_when_not_in_store_or_env(self) -> None:
        """_get_credentials() raises RuntimeError if credentials are unavailable."""
        store = _make_credential_store()  # empty store
        mod = EmailModule()
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("BUTLER_EMAIL_ADDRESS", "BUTLER_EMAIL_PASSWORD")
        }
        with patch.dict(os.environ, env, clear=True):
            await mod.on_startup(config=None, db=None, credential_store=store)
            with pytest.raises(RuntimeError, match="modules.email.bot"):
                mod._get_credentials()

    async def test_on_startup_without_store_does_not_populate_cache(self) -> None:
        """Without a store, _resolved_credentials stays empty at startup."""
        mod = EmailModule()
        await mod.on_startup(config=None, db=None, credential_store=None)
        assert mod._resolved_credentials == {}

    async def test_store_resolve_called_for_each_credential_key(self) -> None:
        """CredentialStore.resolve is called once per configured credential key."""
        store = _make_credential_store()
        mod = EmailModule()
        await mod.on_startup(config=None, db=None, credential_store=store)

        resolved_keys = [call.args[0] for call in store.resolve.call_args_list]
        # Default config: bot enabled, user disabled — 2 keys (address + password)
        assert "BUTLER_EMAIL_ADDRESS" in resolved_keys
        assert "BUTLER_EMAIL_PASSWORD" in resolved_keys

    async def test_user_scope_not_resolved_from_store_when_user_enabled(self) -> None:
        """When user scope is enabled, user keys are NOT resolved from CredentialStore.

        User-scope credentials come exclusively from owner contact_info.
        """
        store = _make_credential_store()
        mod = EmailModule()
        await mod.on_startup(config={"user": {"enabled": True}}, db=None, credential_store=store)
        resolved_keys = [call.args[0] for call in store.resolve.call_args_list]
        # Only bot-scope keys should be resolved from store
        assert "USER_EMAIL_ADDRESS" not in resolved_keys
        assert "USER_EMAIL_PASSWORD" not in resolved_keys
        assert "BUTLER_EMAIL_ADDRESS" in resolved_keys
        assert "BUTLER_EMAIL_PASSWORD" in resolved_keys

    async def test_db_value_wins_over_env_in_get_credentials(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """DB-sourced (cached) credential takes priority over env var."""
        monkeypatch.setenv("BUTLER_EMAIL_ADDRESS", "env@example.com")
        monkeypatch.setenv("BUTLER_EMAIL_PASSWORD", "env-secret")

        store = _make_credential_store(
            BUTLER_EMAIL_ADDRESS="db@example.com",
            BUTLER_EMAIL_PASSWORD="db-secret",
        )
        mod = EmailModule()
        await mod.on_startup(config=None, db=None, credential_store=store)

        address, password = mod._get_credentials()
        assert address == "db@example.com"
        assert password == "db-secret"


# ---------------------------------------------------------------------------
# TelegramModule — CredentialStore integration
# ---------------------------------------------------------------------------


class TestTelegramModuleCredentialStore:
    """Verify TelegramModule resolves credentials via CredentialStore at startup."""

    async def test_on_startup_caches_bot_token(self) -> None:
        """Bot token resolved from store is cached in _resolved_credentials."""
        store = _make_credential_store(BUTLER_TELEGRAM_TOKEN="1234:ABCD")
        mod = TelegramModule()
        await mod.on_startup(config=None, db=None, credential_store=store)
        await mod.on_shutdown()

        assert mod._resolved_credentials["BUTLER_TELEGRAM_TOKEN"] == "1234:ABCD"

    async def test_on_startup_does_not_resolve_user_token_from_store(self) -> None:
        """User-scope token is NOT resolved from CredentialStore.

        User-scope credentials come exclusively from owner contact_info.
        CredentialStore is only used for bot-scope.
        """
        store = _make_credential_store(
            BUTLER_TELEGRAM_TOKEN="bot-token",
            USER_TELEGRAM_TOKEN="user-token",
        )
        mod = TelegramModule()
        config = {"user": {"enabled": True}}
        await mod.on_startup(config=config, db=None, credential_store=store)
        await mod.on_shutdown()

        # User-scope key should NOT be in cache (only contact_info provides it)
        assert "USER_TELEGRAM_TOKEN" not in mod._resolved_credentials
        # Bot-scope should be resolved from store
        assert mod._resolved_credentials["BUTLER_TELEGRAM_TOKEN"] == "bot-token"

    async def test_get_bot_token_uses_cached_store_value(self) -> None:
        """_get_bot_token() returns cached value even when env var differs."""
        store = _make_credential_store(BUTLER_TELEGRAM_TOKEN="db-token")
        mod = TelegramModule()
        with patch.dict(os.environ, {"BUTLER_TELEGRAM_TOKEN": "env-token"}):
            await mod.on_startup(config=None, db=None, credential_store=store)
            token = mod._get_bot_token()
        await mod.on_shutdown()

        assert token == "db-token"

    async def test_get_bot_token_raises_without_store(self) -> None:
        """Without a store, _get_bot_token() raises RuntimeError.

        There is no os.environ fallback — bot token must come from CredentialStore.
        """
        mod = TelegramModule()
        await mod.on_startup(config=None, db=None, credential_store=None)
        with pytest.raises(RuntimeError, match="Missing Telegram bot token"):
            mod._get_bot_token()
        await mod.on_shutdown()

    async def test_get_bot_token_raises_when_not_in_store_or_env(self) -> None:
        """_get_bot_token() raises RuntimeError if token is unavailable."""
        store = _make_credential_store()  # empty store
        mod = TelegramModule()
        env = {k: v for k, v in os.environ.items() if k != "BUTLER_TELEGRAM_TOKEN"}
        with patch.dict(os.environ, env, clear=True):
            await mod.on_startup(config=None, db=None, credential_store=store)
            with pytest.raises(RuntimeError, match="modules.telegram.bot"):
                mod._get_bot_token()
        await mod.on_shutdown()

    async def test_on_startup_without_store_does_not_populate_cache(self) -> None:
        """Without a store, _resolved_credentials stays empty at startup."""
        mod = TelegramModule()
        await mod.on_startup(config=None, db=None, credential_store=None)
        await mod.on_shutdown()
        assert mod._resolved_credentials == {}

    async def test_store_resolve_called_for_bot_token_key(self) -> None:
        """CredentialStore.resolve is called for the configured bot token key."""
        store = _make_credential_store()
        mod = TelegramModule()
        await mod.on_startup(config=None, db=None, credential_store=store)
        await mod.on_shutdown()

        resolved_keys = [call.args[0] for call in store.resolve.call_args_list]
        assert "BUTLER_TELEGRAM_TOKEN" in resolved_keys

    async def test_db_value_wins_over_env_in_get_bot_token(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """DB-sourced (cached) token takes priority over env var."""
        monkeypatch.setenv("BUTLER_TELEGRAM_TOKEN", "env-token")
        store = _make_credential_store(BUTLER_TELEGRAM_TOKEN="db-token")
        mod = TelegramModule()
        await mod.on_startup(config=None, db=None, credential_store=store)
        token = mod._get_bot_token()
        await mod.on_shutdown()

        assert token == "db-token"


# ---------------------------------------------------------------------------
# CalendarModule — CredentialStore integration
# ---------------------------------------------------------------------------


class TestCalendarModuleCredentialStore:
    """Verify CalendarModule uses CredentialStore for credential resolution."""

    async def test_startup_uses_credential_store_first(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When CredentialStore has all Google keys, they are used directly."""
        from butlers.modules.calendar import CalendarModule

        store = _make_credential_store(
            GOOGLE_OAUTH_CLIENT_ID="cs-client-id",
            GOOGLE_OAUTH_CLIENT_SECRET="cs-client-secret",
            GOOGLE_REFRESH_TOKEN="cs-refresh-token",
            GOOGLE_CALENDAR_ID="primary",
        )
        mod = CalendarModule()
        await mod.on_startup(
            {"provider": "google"},
            db=None,
            credential_store=store,
        )

        # Verify provider initialised — this would fail if credentials were wrong
        assert mod._provider is not None

    async def test_startup_raises_when_store_empty(self) -> None:
        """With empty CredentialStore, startup fails under DB-only contract."""
        from butlers.modules.calendar import CalendarModule

        store = _make_credential_store()  # empty — will not find anything
        mod = CalendarModule()
        with pytest.raises(RuntimeError):
            await mod.on_startup(
                {"provider": "google"},
                db=None,
                credential_store=store,
            )

    async def test_resolve_credentials_uses_store_values(self) -> None:
        """CredentialStore values are used for startup resolution."""
        from butlers.modules.calendar import CalendarModule

        store = _make_credential_store(
            GOOGLE_OAUTH_CLIENT_ID="db-client-id",
            GOOGLE_OAUTH_CLIENT_SECRET="db-client-secret",
            GOOGLE_REFRESH_TOKEN="db-refresh-token",
            GOOGLE_CALENDAR_ID="primary",
        )
        mod = CalendarModule()
        await mod.on_startup(
            {"provider": "google"},
            db=None,
            credential_store=store,
        )

        # Provider should use DB credentials; verify the provider resolved
        assert mod._provider is not None
        # Verify store was actually consulted
        store.resolve.assert_called()

    async def test_startup_without_store_raises(self) -> None:
        """Without CredentialStore, startup fails under DB-only contract."""
        from butlers.modules.calendar import CalendarModule

        mod = CalendarModule()
        with pytest.raises(RuntimeError):
            await mod.on_startup(
                {"provider": "google"},
                db=None,
                credential_store=None,
            )
