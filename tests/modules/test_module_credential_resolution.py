"""Tests for CredentialStore-based credential resolution in EmailModule,
TelegramModule, and CalendarModule.

All tests use a minimal in-memory CredentialStore mock — no real DB required.
"""

from __future__ import annotations

import os
import uuid
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
    store = MagicMock(spec=CredentialStore)

    async def _resolve(key: str, *, env_fallback: bool = True) -> str | None:
        val = resolved.get(key)
        return val if val is not None else (os.environ.get(key) if env_fallback else None)

    async def _load_shared(key: str) -> str | None:
        return resolved.get(key)

    store.resolve = AsyncMock(side_effect=_resolve)
    store.load_shared = AsyncMock(side_effect=_load_shared)
    return store


# ---------------------------------------------------------------------------
# EmailModule — CredentialStore integration
# ---------------------------------------------------------------------------


class TestEmailModuleCredentialStore:
    async def test_startup_caches_bot_not_user_credentials(self) -> None:
        """Bot credentials cached; user-scope NOT resolved from store."""
        store = _make_credential_store(
            BUTLER_EMAIL_ADDRESS="bot@example.com",
            BUTLER_EMAIL_PASSWORD="bot-secret",
            USER_EMAIL_ADDRESS="user@example.com",
        )
        mod = EmailModule()
        await mod.on_startup(config={"user": {"enabled": True}}, db=None, credential_store=store)
        assert mod._resolved_credentials["BUTLER_EMAIL_ADDRESS"] == "bot@example.com"
        assert "USER_EMAIL_ADDRESS" not in mod._resolved_credentials

    async def test_get_credentials_db_over_env_and_raises_without(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """DB-sourced credential wins over env; raises when neither available."""
        monkeypatch.setenv("BUTLER_EMAIL_ADDRESS", "env@example.com")
        monkeypatch.setenv("BUTLER_EMAIL_PASSWORD", "env-secret")

        store = _make_credential_store(
            BUTLER_EMAIL_ADDRESS="db@example.com",
            BUTLER_EMAIL_PASSWORD="db-secret",
        )
        mod = EmailModule()
        await mod.on_startup(config=None, db=None, credential_store=store)
        address, password = mod._get_credentials()
        assert address == "db@example.com" and password == "db-secret"

        # Without store
        mod2 = EmailModule()
        await mod2.on_startup(config=None, db=None, credential_store=None)
        assert mod2._resolved_credentials == {}
        with pytest.raises(RuntimeError, match="Missing email credentials"):
            mod2._get_credentials()

    async def test_empty_store_raises(self) -> None:
        store = _make_credential_store()
        mod = EmailModule()
        env = {k: v for k, v in os.environ.items()
               if k not in ("BUTLER_EMAIL_ADDRESS", "BUTLER_EMAIL_PASSWORD")}
        with patch.dict(os.environ, env, clear=True):
            await mod.on_startup(config=None, db=None, credential_store=store)
            with pytest.raises(RuntimeError, match="modules.email.bot"):
                mod._get_credentials()


# ---------------------------------------------------------------------------
# TelegramModule — CredentialStore integration
# ---------------------------------------------------------------------------


class TestTelegramModuleCredentialStore:
    async def test_startup_caches_bot_not_user_token(self) -> None:
        store = _make_credential_store(
            BUTLER_TELEGRAM_TOKEN="bot-token",
            USER_TELEGRAM_TOKEN="user-token",
        )
        mod = TelegramModule()
        await mod.on_startup(config={"user": {"enabled": True}}, db=None, credential_store=store)
        await mod.on_shutdown()
        assert mod._resolved_credentials["BUTLER_TELEGRAM_TOKEN"] == "bot-token"
        assert "USER_TELEGRAM_TOKEN" not in mod._resolved_credentials

    async def test_get_bot_token_db_over_env_and_raises_without(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BUTLER_TELEGRAM_TOKEN", "env-token")
        store = _make_credential_store(BUTLER_TELEGRAM_TOKEN="db-token")
        mod = TelegramModule()
        await mod.on_startup(config=None, db=None, credential_store=store)
        assert mod._get_bot_token() == "db-token"
        await mod.on_shutdown()

        mod2 = TelegramModule()
        await mod2.on_startup(config=None, db=None, credential_store=None)
        assert mod2._resolved_credentials == {}
        with pytest.raises(RuntimeError, match="Missing Telegram bot token"):
            mod2._get_bot_token()
        await mod2.on_shutdown()

    async def test_empty_store_raises(self) -> None:
        store = _make_credential_store()
        mod = TelegramModule()
        env = {k: v for k, v in os.environ.items() if k != "BUTLER_TELEGRAM_TOKEN"}
        with patch.dict(os.environ, env, clear=True):
            await mod.on_startup(config=None, db=None, credential_store=store)
            with pytest.raises(RuntimeError, match="modules.telegram.bot"):
                mod._get_bot_token()
        await mod.on_shutdown()


# ---------------------------------------------------------------------------
# CalendarModule — CredentialStore integration
# ---------------------------------------------------------------------------


class TestCalendarModuleCredentialStore:
    async def test_startup_with_store_and_raises_without(self) -> None:
        from butlers.modules.calendar import CalendarModule

        store = _make_credential_store(
            GOOGLE_OAUTH_CLIENT_ID="cs-client-id",
            GOOGLE_OAUTH_CLIENT_SECRET="cs-client-secret",
            GOOGLE_CALENDAR_ID="primary",
        )
        db = MagicMock()
        db.pool = MagicMock()
        mod = CalendarModule()
        with (
            patch("butlers.google_credentials._resolve_account_entity_id",
                  new_callable=AsyncMock,
                  return_value=uuid.UUID("00000000-0000-0000-0000-000000000001")),
            patch("butlers.google_credentials._resolve_entity_refresh_token",
                  new_callable=AsyncMock, return_value="cs-refresh-token"),
        ):
            await mod.on_startup({"provider": "google"}, db=db, credential_store=store)
        assert mod._provider is not None
        store.resolve.assert_called()

        # Without store or empty store
        for cs in [None, _make_credential_store()]:
            mod2 = CalendarModule()
            with pytest.raises(RuntimeError):
                await mod2.on_startup({"provider": "google"}, db=None, credential_store=cs)
