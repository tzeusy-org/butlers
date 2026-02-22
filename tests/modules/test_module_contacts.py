"""Tests for the Contacts module configuration scaffold and sync lifecycle."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel, ValidationError

from butlers.modules.base import Module
from butlers.modules.contacts import ContactsConfig, ContactsModule, ContactsSyncConfig
from butlers.modules.contacts.sync import ContactsSyncRuntime
from butlers.modules.registry import default_registry

pytestmark = pytest.mark.unit


class TestModuleABCCompliance:
    """Verify ContactsModule satisfies the shared module contract."""

    def test_is_module_subclass(self) -> None:
        assert issubclass(ContactsModule, Module)

    def test_instantiates(self) -> None:
        mod = ContactsModule()
        assert isinstance(mod, Module)

    def test_name(self) -> None:
        assert ContactsModule().name == "contacts"

    def test_config_schema(self) -> None:
        schema = ContactsModule().config_schema
        assert schema is ContactsConfig
        assert issubclass(schema, BaseModel)

    def test_dependencies_empty(self) -> None:
        assert ContactsModule().dependencies == []

    def test_credentials_env_declared(self) -> None:
        assert ContactsModule().credentials_env == []

    def test_migration_revisions_returns_contacts_chain(self) -> None:
        assert ContactsModule().migration_revisions() == "contacts"

    def test_module_discovered_in_default_registry(self) -> None:
        assert "contacts" in default_registry().available_modules


class TestContactsConfig:
    """Verify config validation defaults and strictness."""

    def test_provider_required(self) -> None:
        with pytest.raises(ValidationError):
            ContactsConfig()

    def test_defaults(self) -> None:
        config = ContactsConfig(provider="google")
        assert config.provider == "google"
        assert config.include_other_contacts is False
        assert isinstance(config.sync, ContactsSyncConfig)
        assert config.sync.enabled is True
        assert config.sync.run_on_startup is True
        assert config.sync.interval_minutes == 15
        assert config.sync.full_sync_interval_days == 6

    def test_provider_normalization(self) -> None:
        config = ContactsConfig(provider="  GOOGLE  ")
        assert config.provider == "google"

    def test_provider_non_empty_error(self) -> None:
        with pytest.raises(ValidationError, match="provider must be a non-empty string"):
            ContactsConfig(provider="   ")

    def test_top_level_forbids_unknown_fields(self) -> None:
        with pytest.raises(ValidationError) as error:
            ContactsConfig(provider="google", unsupported=True)
        assert error.value.errors()[0]["loc"] == ("unsupported",)
        assert error.value.errors()[0]["type"] == "extra_forbidden"

    def test_sync_forbids_unknown_fields(self) -> None:
        with pytest.raises(ValidationError) as error:
            ContactsConfig(provider="google", sync={"interval_minutes": 15, "bogus": 1})
        assert error.value.errors()[0]["loc"] == ("sync", "bogus")
        assert error.value.errors()[0]["type"] == "extra_forbidden"


def _make_credential_store(
    *,
    client_id: str = "cid",
    client_secret: str = "csecret",
    refresh_token: str = "rtoken",
) -> Any:
    """Build an AsyncMock credential store that returns the given values."""
    store = MagicMock()

    async def _resolve(key: str, *, env_fallback: bool = True) -> str | None:
        mapping = {
            "GOOGLE_OAUTH_CLIENT_ID": client_id,
            "GOOGLE_OAUTH_CLIENT_SECRET": client_secret,
            "GOOGLE_REFRESH_TOKEN": refresh_token,
        }
        return mapping.get(key)

    store.resolve = AsyncMock(side_effect=_resolve)
    return store


class TestModuleStartup:
    """Verify startup provider selection behavior."""

    async def test_startup_fails_on_unsupported_provider(self) -> None:
        mod = ContactsModule()
        with pytest.raises(RuntimeError) as excinfo:
            await mod.on_startup({"provider": "outlook"}, db=None)

        error_message = str(excinfo.value)
        assert "Unsupported contacts provider 'outlook'" in error_message
        assert "Supported providers: google" in error_message

    async def test_startup_skips_runtime_when_sync_disabled(self) -> None:
        """When sync.enabled=False, no credential lookup or runtime is created."""
        mod = ContactsModule()
        await mod.on_startup({"provider": "google", "sync": {"enabled": False}}, db=None)
        config = getattr(mod, "_config")
        assert isinstance(config, ContactsConfig)
        assert config.provider == "google"
        assert getattr(mod, "_runtime") is None

    async def test_startup_creates_runtime_when_credentials_present(self) -> None:
        """on_startup() creates and starts ContactsSyncRuntime when sync.enabled=True."""
        mod = ContactsModule()
        credential_store = _make_credential_store()

        with patch.object(ContactsSyncRuntime, "start", new_callable=AsyncMock) as mock_start:
            await mod.on_startup(
                {"provider": "google"},
                db=None,
                credential_store=credential_store,
            )
            mock_start.assert_awaited_once()

        runtime = getattr(mod, "_runtime")
        assert isinstance(runtime, ContactsSyncRuntime)

    async def test_startup_missing_credentials_raises_actionable_error(self) -> None:
        """Missing credentials produce an actionable RuntimeError message."""
        mod = ContactsModule()
        store = MagicMock()
        store.resolve = AsyncMock(return_value=None)

        with pytest.raises(RuntimeError) as excinfo:
            await mod.on_startup({"provider": "google"}, db=None, credential_store=store)

        msg = str(excinfo.value)
        assert "GOOGLE_OAUTH_CLIENT_ID" in msg
        assert "GOOGLE_OAUTH_CLIENT_SECRET" in msg
        assert "GOOGLE_REFRESH_TOKEN" in msg
        assert "butler_secrets" in msg

    async def test_startup_no_credential_store_raises_actionable_error(self) -> None:
        """Absent credential_store (None) raises a clear RuntimeError."""
        mod = ContactsModule()
        with pytest.raises(RuntimeError) as excinfo:
            await mod.on_startup({"provider": "google"}, db=None, credential_store=None)

        msg = str(excinfo.value)
        assert "butler_secrets" in msg
        assert "GOOGLE_OAUTH_CLIENT_ID" in msg

    async def test_startup_config_stored_on_module(self) -> None:
        """on_startup() sets _config regardless of sync.enabled."""
        mod = ContactsModule()
        await mod.on_startup({"provider": "google", "sync": {"enabled": False}}, db=None)
        config = getattr(mod, "_config")
        assert isinstance(config, ContactsConfig)
        assert config.provider == "google"

    async def test_register_tools_accepts_validated_config(self) -> None:
        mod = ContactsModule()
        cfg = ContactsConfig(provider="google")
        await mod.register_tools(mcp=_StubMCP(), config=cfg, db=None)
        assert isinstance(getattr(mod, "_config"), ContactsConfig)

    async def test_register_tools_accepts_dict_config(self) -> None:
        mod = ContactsModule()
        await mod.register_tools(mcp=_StubMCP(), config={"provider": "google"}, db=None)
        stored = getattr(mod, "_config")
        assert isinstance(stored, ContactsConfig)
        assert stored.provider == "google"


class TestModuleShutdown:
    """Verify shutdown lifecycle stops runtime and releases provider."""

    async def test_shutdown_stops_runtime(self) -> None:
        """on_shutdown() calls runtime.stop()."""
        mod = ContactsModule()
        credential_store = _make_credential_store()

        with patch.object(ContactsSyncRuntime, "start", new_callable=AsyncMock):
            await mod.on_startup(
                {"provider": "google"},
                db=None,
                credential_store=credential_store,
            )

        runtime = getattr(mod, "_runtime")
        assert runtime is not None

        with patch.object(runtime, "stop", new_callable=AsyncMock) as mock_stop:
            await mod.on_shutdown()
            mock_stop.assert_awaited_once()

        assert getattr(mod, "_runtime") is None

    async def test_shutdown_releases_provider(self) -> None:
        """on_shutdown() calls provider.shutdown()."""
        mod = ContactsModule()
        credential_store = _make_credential_store()

        with patch.object(ContactsSyncRuntime, "start", new_callable=AsyncMock):
            await mod.on_startup(
                {"provider": "google"},
                db=None,
                credential_store=credential_store,
            )

        provider = getattr(mod, "_provider")
        assert provider is not None

        with (
            patch.object(ContactsSyncRuntime, "stop", new_callable=AsyncMock),
            patch.object(provider, "shutdown", new_callable=AsyncMock) as mock_shutdown,
        ):
            await mod.on_shutdown()
            mock_shutdown.assert_awaited_once()

        assert getattr(mod, "_provider") is None

    async def test_shutdown_clears_config_and_db(self) -> None:
        """on_shutdown() clears _config and _db references."""
        mod = ContactsModule()
        await mod.on_startup({"provider": "google", "sync": {"enabled": False}}, db=object())

        await mod.on_shutdown()

        assert getattr(mod, "_config") is None
        assert getattr(mod, "_db") is None

    async def test_shutdown_noop_when_not_started(self) -> None:
        """on_shutdown() is safe to call without a prior on_startup()."""
        mod = ContactsModule()
        await mod.on_shutdown()  # Must not raise
        assert getattr(mod, "_runtime") is None
        assert getattr(mod, "_provider") is None

    async def test_shutdown_noop_when_sync_disabled(self) -> None:
        """on_shutdown() is safe when sync was disabled and runtime was never created."""
        mod = ContactsModule()
        await mod.on_startup({"provider": "google", "sync": {"enabled": False}}, db=None)
        await mod.on_shutdown()  # Must not raise


class TestRuntimeAccessibility:
    """Verify that _runtime is accessible for MCP tool registration."""

    async def test_runtime_accessible_after_startup(self) -> None:
        """After on_startup(), _runtime is a ContactsSyncRuntime instance."""
        mod = ContactsModule()
        credential_store = _make_credential_store()

        with patch.object(ContactsSyncRuntime, "start", new_callable=AsyncMock):
            await mod.on_startup(
                {"provider": "google"},
                db=None,
                credential_store=credential_store,
            )

        runtime = getattr(mod, "_runtime")
        assert isinstance(runtime, ContactsSyncRuntime)

    async def test_runtime_is_none_after_shutdown(self) -> None:
        """After on_shutdown(), _runtime is None."""
        mod = ContactsModule()
        credential_store = _make_credential_store()

        with patch.object(ContactsSyncRuntime, "start", new_callable=AsyncMock):
            await mod.on_startup(
                {"provider": "google"},
                db=None,
                credential_store=credential_store,
            )

        with patch.object(ContactsSyncRuntime, "stop", new_callable=AsyncMock):
            await mod.on_shutdown()

        assert getattr(mod, "_runtime") is None

    def test_runtime_none_before_startup(self) -> None:
        """Before on_startup(), _runtime is None."""
        mod = ContactsModule()
        assert getattr(mod, "_runtime") is None


class _StubMCP:
    """Minimal MCP stub used for register_tools signatures."""

    def __getattr__(self, _name: str) -> Any:
        raise AttributeError
