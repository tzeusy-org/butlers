"""Tests for calendar module config and provider interface scaffolding."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from butlers.modules.base import Module
from butlers.modules.calendar import (
    CalendarConfig,
    CalendarModule,
    CalendarProvider,
)

pytestmark = pytest.mark.unit


class TestModuleABCCompliance:
    """Verify CalendarModule satisfies the shared module contract."""

    def test_is_module_subclass(self):
        assert issubclass(CalendarModule, Module)

    def test_instantiates(self):
        mod = CalendarModule()
        assert isinstance(mod, Module)

    def test_name(self):
        assert CalendarModule().name == "calendar"

    def test_config_schema(self):
        schema = CalendarModule().config_schema
        assert schema is CalendarConfig
        assert issubclass(schema, BaseModel)

    def test_dependencies_empty(self):
        assert CalendarModule().dependencies == []

    def test_migration_revisions_none(self):
        assert CalendarModule().migration_revisions() is None


class TestCalendarConfig:
    """Verify config validation, required fields, and defaults."""

    def test_required_fields_provider_and_calendar_id(self):
        with pytest.raises(ValidationError):
            CalendarConfig(calendar_id="primary")

        with pytest.raises(ValidationError):
            CalendarConfig(provider="google")

    def test_defaults(self):
        config = CalendarConfig(provider="google", calendar_id="primary")
        assert config.provider == "google"
        assert config.calendar_id == "primary"
        assert config.timezone == "UTC"

        assert config.conflicts.policy == "suggest"
        assert config.conflicts.require_approval_for_overlap is True

        assert config.event_defaults.enabled is True
        assert config.event_defaults.minutes_before == 15
        assert config.event_defaults.color_id is None

    def test_string_normalization(self):
        config = CalendarConfig(
            provider="  GOOGLE  ",
            calendar_id="  primary  ",
            timezone="  America/New_York  ",
        )
        assert config.provider == "google"
        assert config.calendar_id == "primary"
        assert config.timezone == "America/New_York"


class TestCalendarProviderInterface:
    """Verify the provider interface exposes required tool operations."""

    def test_provider_contract_operations(self):
        abstract_methods = CalendarProvider.__abstractmethods__
        expected = {
            "name",
            "list_events",
            "get_event",
            "create_event",
            "update_event",
            "delete_event",
            "find_conflicts",
            "shutdown",
        }
        assert expected.issubset(abstract_methods)


class TestModuleStartup:
    """Verify startup provider selection behavior."""

    async def test_startup_accepts_supported_provider(self):
        mod = CalendarModule()
        await mod.on_startup({"provider": "google", "calendar_id": "primary"}, db=None)

        # Verify provider was selected and is usable by later tools.
        provider = getattr(mod, "_provider")
        assert provider is not None
        assert provider.name == "google"

    async def test_startup_fails_clearly_on_unsupported_provider(self):
        mod = CalendarModule()
        with pytest.raises(RuntimeError, match="Unsupported calendar provider 'outlook'"):
            await mod.on_startup({"provider": "outlook", "calendar_id": "primary"}, db=None)

        with pytest.raises(RuntimeError, match="Supported providers: google"):
            await mod.on_startup({"provider": "outlook", "calendar_id": "primary"}, db=None)

    async def test_register_tools_accepts_validated_config(self):
        mod = CalendarModule()
        cfg = CalendarConfig(provider="google", calendar_id="primary")
        await mod.register_tools(mcp=object(), config=cfg, db=None)
        assert isinstance(getattr(mod, "_config"), CalendarConfig)

    async def test_register_tools_accepts_dict_config(self):
        mod = CalendarModule()
        await mod.register_tools(
            mcp=object(),
            config={"provider": "google", "calendar_id": "primary"},
            db=None,
        )
        stored = getattr(mod, "_config")
        assert isinstance(stored, CalendarConfig)
        assert stored.provider == "google"
