"""Tests for the Contacts module configuration scaffold."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from butlers.modules.base import Module
from butlers.modules.contacts import ContactsConfig, ContactsModule, ContactsSyncConfig
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


class TestModuleStartup:
    """Verify startup provider selection behavior."""

    async def test_startup_accepts_supported_provider(self) -> None:
        mod = ContactsModule()
        await mod.on_startup({"provider": "google"}, db=None)
        config = getattr(mod, "_config")
        assert isinstance(config, ContactsConfig)
        assert config.provider == "google"

    async def test_startup_fails_on_unsupported_provider(self) -> None:
        mod = ContactsModule()
        with pytest.raises(RuntimeError) as excinfo:
            await mod.on_startup({"provider": "outlook"}, db=None)

        error_message = str(excinfo.value)
        assert "Unsupported contacts provider 'outlook'" in error_message
        assert "Supported providers: google" in error_message

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


class _StubMCP:
    """Minimal MCP stub used for register_tools signatures."""

    def __getattr__(self, _name: str) -> Any:
        raise AttributeError
