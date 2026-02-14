"""Tests for calendar module config and provider interface scaffolding."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from pydantic import BaseModel, ValidationError

from butlers.modules.base import Module
from butlers.modules.calendar import (
    BUTLER_GENERATED_PRIVATE_KEY,
    BUTLER_NAME_PRIVATE_KEY,
    GOOGLE_CALENDAR_API_BASE_URL,
    GOOGLE_CALENDAR_CREDENTIALS_ENV,
    GOOGLE_OAUTH_TOKEN_URL,
    CalendarConfig,
    CalendarCredentialError,
    CalendarEvent,
    CalendarEventPayloadInput,
    CalendarModule,
    CalendarProvider,
    CalendarRequestError,
    _google_event_to_calendar_event,
    _GoogleOAuthClient,
    _GoogleOAuthCredentials,
    _GoogleProvider,
    _parse_google_datetime,
    _parse_google_event_boundary,
    normalize_event_payload,
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

    def test_credentials_env_declared(self):
        assert CalendarModule().credentials_env == [GOOGLE_CALENDAR_CREDENTIALS_ENV]


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

    def test_non_empty_errors_include_field_name(self):
        with pytest.raises(ValidationError, match="calendar_id must be a non-empty string"):
            CalendarConfig(provider="google", calendar_id="   ")

        with pytest.raises(ValidationError, match="timezone must be a non-empty string"):
            CalendarConfig(provider="google", calendar_id="primary", timezone="   ")

    def test_nested_defaults_forbid_unknown_keys(self):
        with pytest.raises(ValidationError) as conflict_error:
            CalendarConfig(
                provider="google",
                calendar_id="primary",
                conflicts={"policy": "suggest", "unexpected": True},
            )
        assert conflict_error.value.errors()[0]["loc"] == ("conflicts", "unexpected")
        assert conflict_error.value.errors()[0]["type"] == "extra_forbidden"

        with pytest.raises(ValidationError) as defaults_error:
            CalendarConfig(
                provider="google",
                calendar_id="primary",
                event_defaults={"minutes_beforee": 10},
            )
        assert defaults_error.value.errors()[0]["loc"] == ("event_defaults", "minutes_beforee")
        assert defaults_error.value.errors()[0]["type"] == "extra_forbidden"


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

    async def test_startup_accepts_supported_provider(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv(
            GOOGLE_CALENDAR_CREDENTIALS_ENV,
            json.dumps(
                {
                    "client_id": "test-client-id",
                    "client_secret": "test-client-secret",
                    "refresh_token": "test-refresh-token",
                }
            ),
        )
        mod = CalendarModule()
        await mod.on_startup({"provider": "google", "calendar_id": "primary"}, db=None)

        # Verify provider was selected and is usable by later tools.
        provider = getattr(mod, "_provider")
        assert provider is not None
        assert provider.name == "google"

    async def test_startup_fails_clearly_on_unsupported_provider(self):
        mod = CalendarModule()
        with pytest.raises(RuntimeError) as excinfo:
            await mod.on_startup({"provider": "outlook", "calendar_id": "primary"}, db=None)

        error_message = str(excinfo.value)
        assert "Unsupported calendar provider 'outlook'" in error_message
        assert "Supported providers: google" in error_message

    async def test_register_tools_accepts_validated_config(self):
        mod = CalendarModule()
        cfg = CalendarConfig(provider="google", calendar_id="primary")
        await mod.register_tools(mcp=_StubMCP(), config=cfg, db=None)
        assert isinstance(getattr(mod, "_config"), CalendarConfig)

    async def test_register_tools_accepts_dict_config(self):
        mod = CalendarModule()
        await mod.register_tools(
            mcp=_StubMCP(),
            config={"provider": "google", "calendar_id": "primary"},
            db=None,
        )
        stored = getattr(mod, "_config")
        assert isinstance(stored, CalendarConfig)
        assert stored.provider == "google"


class _StubMCP:
    """Minimal MCP stub that captures registered tools by function name."""

    def __init__(self) -> None:
        self.tools: dict[str, object] = {}

    def tool(self):
        def decorator(func):
            self.tools[func.__name__] = func
            return func

        return decorator


class _ProviderDouble(CalendarProvider):
    """Provider test double used to verify module-to-provider wiring."""

    def __init__(
        self,
        *,
        events: list[CalendarEvent] | None = None,
        event: CalendarEvent | None = None,
        create_event_result: CalendarEvent | None = None,
        update_event_result: CalendarEvent | None = None,
        conflicts: list[CalendarEvent] | None = None,
    ) -> None:
        self._events = events or []
        self._event = event
        self._create_event_result = (
            create_event_result if create_event_result is not None else event
        )
        self._update_event_result = (
            update_event_result if update_event_result is not None else event
        )
        self._conflicts = conflicts or []
        self.list_calls: list[dict[str, object]] = []
        self.get_calls: list[dict[str, object]] = []
        self.create_calls: list[dict[str, object]] = []
        self.update_calls: list[dict[str, object]] = []
        self.find_conflict_calls: list[dict[str, object]] = []

    @property
    def name(self) -> str:
        return "double"

    async def list_events(
        self,
        *,
        calendar_id: str,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        limit: int = 50,
    ) -> list[CalendarEvent]:
        self.list_calls.append(
            {
                "calendar_id": calendar_id,
                "start_at": start_at,
                "end_at": end_at,
                "limit": limit,
            }
        )
        return list(self._events)

    async def get_event(self, *, calendar_id: str, event_id: str) -> CalendarEvent | None:
        self.get_calls.append({"calendar_id": calendar_id, "event_id": event_id})
        return self._event

    async def create_event(self, *, calendar_id: str, payload):
        self.create_calls.append({"calendar_id": calendar_id, "payload": payload})
        if self._create_event_result is not None:
            return self._create_event_result
        raise NotImplementedError

    async def update_event(self, *, calendar_id: str, event_id: str, patch):
        self.update_calls.append({"calendar_id": calendar_id, "event_id": event_id, "patch": patch})
        if self._update_event_result is not None:
            return self._update_event_result
        raise NotImplementedError

    async def delete_event(self, *, calendar_id: str, event_id: str) -> None:  # pragma: no cover
        raise NotImplementedError

    async def find_conflicts(self, *, calendar_id: str, candidate):
        self.find_conflict_calls.append({"calendar_id": calendar_id, "candidate": candidate})
        return list(self._conflicts)

    async def shutdown(self) -> None:
        return None


class TestCalendarReadTools:
    """Verify list/get tools use provider abstraction and normalize payloads."""

    async def test_register_tools_wires_list_get_via_provider(self):
        event = CalendarEvent(
            event_id="evt-123",
            title="Dentist appointment",
            start_at=datetime(2026, 2, 20, 14, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 15, 0, tzinfo=UTC),
            timezone="UTC",
            description="Bring insurance card",
            location="Main Street Clinic",
            attendees=["alex@example.com"],
            recurrence_rule="RRULE:FREQ=WEEKLY",
            color_id="7",
        )
        provider = _ProviderDouble(events=[event], event=event)
        mcp = _StubMCP()
        mod = CalendarModule()
        mod._provider = provider

        await mod.register_tools(
            mcp=mcp,
            config={"provider": "google", "calendar_id": "primary"},
            db=None,
        )

        list_result = await mcp.tools["calendar_list_events"]()
        get_result = await mcp.tools["calendar_get_event"](event_id="evt-123")

        assert provider.list_calls == [
            {"calendar_id": "primary", "start_at": None, "end_at": None, "limit": 50}
        ]
        assert provider.get_calls == [{"calendar_id": "primary", "event_id": "evt-123"}]
        assert list_result == {
            "provider": "double",
            "calendar_id": "primary",
            "events": [
                {
                    "event_id": "evt-123",
                    "title": "Dentist appointment",
                    "start_at": "2026-02-20T14:00:00+00:00",
                    "end_at": "2026-02-20T15:00:00+00:00",
                    "timezone": "UTC",
                    "description": "Bring insurance card",
                    "location": "Main Street Clinic",
                    "attendees": ["alex@example.com"],
                    "recurrence_rule": "RRULE:FREQ=WEEKLY",
                    "color_id": "7",
                    "butler_generated": False,
                    "butler_name": None,
                }
            ],
        }
        assert get_result == {
            "provider": "double",
            "calendar_id": "primary",
            "event": {
                "event_id": "evt-123",
                "title": "Dentist appointment",
                "start_at": "2026-02-20T14:00:00+00:00",
                "end_at": "2026-02-20T15:00:00+00:00",
                "timezone": "UTC",
                "description": "Bring insurance card",
                "location": "Main Street Clinic",
                "attendees": ["alex@example.com"],
                "recurrence_rule": "RRULE:FREQ=WEEKLY",
                "color_id": "7",
                "butler_generated": False,
                "butler_name": None,
            },
        }

    async def test_calendar_id_override_is_applied_without_mutating_default(self):
        provider = _ProviderDouble()
        mcp = _StubMCP()
        mod = CalendarModule()
        mod._provider = provider

        await mod.register_tools(
            mcp=mcp,
            config={"provider": "google", "calendar_id": "primary"},
            db=None,
        )

        await mcp.tools["calendar_list_events"](calendar_id="  butler-subcalendar  ", limit=5)
        await mcp.tools["calendar_get_event"](
            event_id="evt-456",
            calendar_id="custom-calendar",
        )

        assert provider.list_calls[0]["calendar_id"] == "butler-subcalendar"
        assert provider.list_calls[0]["limit"] == 5
        assert provider.get_calls[0]["calendar_id"] == "custom-calendar"
        assert getattr(mod, "_config").calendar_id == "primary"

    async def test_calendar_id_override_rejects_blank_string(self):
        provider = _ProviderDouble()
        mcp = _StubMCP()
        mod = CalendarModule()
        mod._provider = provider
        await mod.register_tools(
            mcp=mcp,
            config={"provider": "google", "calendar_id": "primary"},
            db=None,
        )

        with pytest.raises(ValueError, match="calendar_id must be a non-empty string"):
            await mcp.tools["calendar_list_events"](calendar_id="   ")


class TestCalendarWriteTools:
    """Verify create/update tools enforce Butler labeling and metadata tags."""

    async def test_create_event_adds_prefix_and_private_metadata(self):
        created = CalendarEvent(
            event_id="evt-created",
            title="BUTLER: Team Sync",
            start_at=datetime(2026, 2, 20, 14, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 15, 0, tzinfo=UTC),
            timezone="UTC",
            butler_generated=True,
            butler_name="general",
        )
        provider = _ProviderDouble(event=created)
        mcp = _StubMCP()
        mod = CalendarModule()
        mod._provider = provider

        await mod.register_tools(
            mcp=mcp,
            config={"provider": "google", "calendar_id": "primary"},
            db=SimpleNamespace(db_name="butler_general"),
        )

        result = await mcp.tools["calendar_create_event"](
            title="Team Sync",
            start_at=datetime(2026, 2, 20, 14, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 15, 0, tzinfo=UTC),
        )

        payload = provider.create_calls[0]["payload"]
        assert payload.title == "BUTLER: Team Sync"
        assert payload.private_metadata == {
            BUTLER_GENERATED_PRIVATE_KEY: "true",
            BUTLER_NAME_PRIVATE_KEY: "general",
        }
        assert result["event"]["title"] == "BUTLER: Team Sync"
        assert result["event"]["butler_generated"] is True
        assert result["event"]["butler_name"] == "general"

    async def test_update_repairs_prefix_for_butler_generated_events(self):
        existing = CalendarEvent(
            event_id="evt-legacy",
            title="Legacy title without prefix",
            start_at=datetime(2026, 2, 20, 14, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 15, 0, tzinfo=UTC),
            timezone="UTC",
            butler_generated=True,
            butler_name="health",
        )
        updated = CalendarEvent(
            event_id="evt-legacy",
            title="BUTLER: Legacy title without prefix",
            start_at=datetime(2026, 2, 20, 14, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 15, 0, tzinfo=UTC),
            timezone="UTC",
            butler_generated=True,
            butler_name="health",
        )
        provider = _ProviderDouble(event=existing, update_event_result=updated)
        mcp = _StubMCP()
        mod = CalendarModule()
        mod._provider = provider

        await mod.register_tools(
            mcp=mcp,
            config={"provider": "google", "calendar_id": "primary"},
            db=SimpleNamespace(db_name="butler_general"),
        )

        await mcp.tools["calendar_update_event"](
            event_id="evt-legacy",
            location="Updated room",
        )

        patch = provider.update_calls[0]["patch"]
        assert patch.title == "BUTLER: Legacy title without prefix"
        assert patch.private_metadata == {
            BUTLER_GENERATED_PRIVATE_KEY: "true",
            BUTLER_NAME_PRIVATE_KEY: "health",
        }

    async def test_update_leaves_non_butler_event_title_unchanged(self):
        existing = CalendarEvent(
            event_id="evt-human",
            title="Discuss roadmap",
            start_at=datetime(2026, 2, 21, 10, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 21, 11, 0, tzinfo=UTC),
            timezone="UTC",
            butler_generated=False,
            butler_name=None,
        )
        provider = _ProviderDouble(event=existing)
        mcp = _StubMCP()
        mod = CalendarModule()
        mod._provider = provider

        await mod.register_tools(
            mcp=mcp,
            config={"provider": "google", "calendar_id": "primary"},
            db=None,
        )

        await mcp.tools["calendar_update_event"](
            event_id="evt-human",
            title="Discuss roadmap today",
        )

        patch = provider.update_calls[0]["patch"]
        assert patch.title == "Discuss roadmap today"
        assert patch.private_metadata is None

    async def test_create_validates_recurring_rrule_payload(self):
        created = CalendarEvent(
            event_id="evt-recurring",
            title="BUTLER: Weekly Planning",
            start_at=datetime(2026, 2, 20, 14, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 15, 0, tzinfo=UTC),
            timezone="UTC",
            recurrence_rule="RRULE:FREQ=WEEKLY;INTERVAL=1",
            butler_generated=True,
            butler_name="general",
        )
        provider = _ProviderDouble(event=created)
        mcp = _StubMCP()
        mod = CalendarModule()
        mod._provider = provider

        await mod.register_tools(
            mcp=mcp,
            config={"provider": "google", "calendar_id": "primary"},
            db=SimpleNamespace(db_name="butler_general"),
        )

        await mcp.tools["calendar_create_event"](
            title="Weekly Planning",
            start_at=datetime(2026, 2, 20, 14, 0),
            end_at=datetime(2026, 2, 20, 15, 0),
            timezone="UTC",
            recurrence_rule="  RRULE:FREQ=WEEKLY;INTERVAL=1  ",
        )

        payload = provider.create_calls[0]["payload"]
        assert payload.recurrence_rule == "RRULE:FREQ=WEEKLY;INTERVAL=1"
        assert payload.timezone == "UTC"

    async def test_create_rejects_invalid_recurrence_rule(self):
        provider = _ProviderDouble()
        mcp = _StubMCP()
        mod = CalendarModule()
        mod._provider = provider

        await mod.register_tools(
            mcp=mcp,
            config={"provider": "google", "calendar_id": "primary"},
            db=SimpleNamespace(db_name="butler_general"),
        )

        with pytest.raises(ValueError, match="must start with 'RRULE:'"):
            await mcp.tools["calendar_create_event"](
                title="Daily sync",
                start_at=datetime(2026, 2, 20, 14, 0, tzinfo=UTC),
                end_at=datetime(2026, 2, 20, 15, 0, tzinfo=UTC),
                recurrence_rule="FREQ=DAILY",
            )

    async def test_create_rejects_recurrence_with_invalid_timezone(self):
        provider = _ProviderDouble()
        mcp = _StubMCP()
        mod = CalendarModule()
        mod._provider = provider

        await mod.register_tools(
            mcp=mcp,
            config={"provider": "google", "calendar_id": "primary"},
            db=SimpleNamespace(db_name="butler_general"),
        )

        with pytest.raises(ValueError, match="timezone must be a valid IANA timezone"):
            await mcp.tools["calendar_create_event"](
                title="Daily sync",
                start_at=datetime(2026, 2, 20, 14, 0),
                end_at=datetime(2026, 2, 20, 15, 0),
                timezone="Mars/Olympus",
                recurrence_rule="RRULE:FREQ=DAILY",
            )

    async def test_create_requires_timezone_for_recurrence_on_naive_datetimes(self):
        provider = _ProviderDouble()
        mcp = _StubMCP()
        mod = CalendarModule()
        mod._provider = provider

        await mod.register_tools(
            mcp=mcp,
            config={"provider": "google", "calendar_id": "primary"},
            db=SimpleNamespace(db_name="butler_general"),
        )

        with pytest.raises(ValueError, match="timezone is required when recurrence_rule is set"):
            await mcp.tools["calendar_create_event"](
                title="Daily sync",
                start_at=datetime(2026, 2, 20, 14, 0),
                end_at=datetime(2026, 2, 20, 15, 0),
                recurrence_rule="RRULE:FREQ=DAILY",
            )

    async def test_update_recurrence_defaults_to_series_scope(self):
        existing = CalendarEvent(
            event_id="evt-recurring",
            title="BUTLER: Weekly sync",
            start_at=datetime(2026, 2, 21, 10, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 21, 11, 0, tzinfo=UTC),
            timezone="UTC",
            recurrence_rule="RRULE:FREQ=WEEKLY",
            butler_generated=True,
            butler_name="general",
        )
        provider = _ProviderDouble(event=existing, update_event_result=existing)
        mcp = _StubMCP()
        mod = CalendarModule()
        mod._provider = provider

        await mod.register_tools(
            mcp=mcp,
            config={"provider": "google", "calendar_id": "primary"},
            db=SimpleNamespace(db_name="butler_general"),
        )

        await mcp.tools["calendar_update_event"](
            event_id="evt-recurring",
            recurrence_rule="  RRULE:FREQ=MONTHLY;INTERVAL=1 ",
        )

        patch = provider.update_calls[0]["patch"]
        assert patch.recurrence_rule == "RRULE:FREQ=MONTHLY;INTERVAL=1"
        assert patch.recurrence_scope == "series"

    async def test_update_recurrence_rejects_non_series_scope(self):
        existing = CalendarEvent(
            event_id="evt-recurring",
            title="BUTLER: Weekly sync",
            start_at=datetime(2026, 2, 21, 10, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 21, 11, 0, tzinfo=UTC),
            timezone="UTC",
            recurrence_rule="RRULE:FREQ=WEEKLY",
            butler_generated=True,
            butler_name="general",
        )
        provider = _ProviderDouble(event=existing, update_event_result=existing)
        mcp = _StubMCP()
        mod = CalendarModule()
        mod._provider = provider

        await mod.register_tools(
            mcp=mcp,
            config={"provider": "google", "calendar_id": "primary"},
            db=SimpleNamespace(db_name="butler_general"),
        )

        with pytest.raises(ValidationError, match="Input should be 'series'"):
            await mcp.tools["calendar_update_event"](
                event_id="evt-recurring",
                recurrence_rule="RRULE:FREQ=MONTHLY",
                recurrence_scope="instance",
            )

    async def test_create_conflict_default_suggest_returns_conflicts_and_suggested_slots(self):
        created = CalendarEvent(
            event_id="evt-created",
            title="BUTLER: Team Sync",
            start_at=datetime(2026, 2, 20, 14, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 15, 0, tzinfo=UTC),
            timezone="UTC",
            butler_generated=True,
            butler_name="general",
        )
        conflict = CalendarEvent(
            event_id="busy-1",
            title="(busy)",
            start_at=datetime(2026, 2, 20, 14, 30, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 15, 30, tzinfo=UTC),
            timezone="UTC",
        )
        provider = _ProviderDouble(event=created, conflicts=[conflict])
        mcp = _StubMCP()
        mod = CalendarModule()
        mod._provider = provider

        await mod.register_tools(
            mcp=mcp,
            config={"provider": "google", "calendar_id": "primary"},
            db=SimpleNamespace(db_name="butler_general"),
        )

        result = await mcp.tools["calendar_create_event"](
            title="Team Sync",
            start_at=datetime(2026, 2, 20, 14, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 15, 0, tzinfo=UTC),
        )

        assert provider.find_conflict_calls
        assert provider.create_calls == []
        assert result["status"] == "conflict"
        assert result["policy"] == "suggest"
        assert result["conflicts"] == [
            {
                "event_id": "busy-1",
                "title": "(busy)",
                "start_at": "2026-02-20T14:30:00+00:00",
                "end_at": "2026-02-20T15:30:00+00:00",
                "timezone": "UTC",
            }
        ]
        assert len(result["suggested_slots"]) == 3

    async def test_create_conflict_fail_policy_returns_conflict_without_suggestions(self):
        created = CalendarEvent(
            event_id="evt-created",
            title="BUTLER: Team Sync",
            start_at=datetime(2026, 2, 20, 14, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 15, 0, tzinfo=UTC),
            timezone="UTC",
            butler_generated=True,
            butler_name="general",
        )
        conflict = CalendarEvent(
            event_id="busy-1",
            title="(busy)",
            start_at=datetime(2026, 2, 20, 14, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 15, 0, tzinfo=UTC),
            timezone="UTC",
        )
        provider = _ProviderDouble(event=created, conflicts=[conflict])
        mcp = _StubMCP()
        mod = CalendarModule()
        mod._provider = provider

        await mod.register_tools(
            mcp=mcp,
            config={"provider": "google", "calendar_id": "primary"},
            db=SimpleNamespace(db_name="butler_general"),
        )

        result = await mcp.tools["calendar_create_event"](
            title="Team Sync",
            start_at=datetime(2026, 2, 20, 14, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 15, 0, tzinfo=UTC),
            conflict_policy="fail",
        )

        assert provider.create_calls == []
        assert result["status"] == "conflict"
        assert result["policy"] == "fail"
        assert result["suggested_slots"] == []

    async def test_create_conflict_allow_overlap_policy_writes_event(self):
        created = CalendarEvent(
            event_id="evt-created",
            title="BUTLER: Team Sync",
            start_at=datetime(2026, 2, 20, 14, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 15, 0, tzinfo=UTC),
            timezone="UTC",
            butler_generated=True,
            butler_name="general",
        )
        conflict = CalendarEvent(
            event_id="busy-1",
            title="(busy)",
            start_at=datetime(2026, 2, 20, 14, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 15, 0, tzinfo=UTC),
            timezone="UTC",
        )
        provider = _ProviderDouble(event=created, conflicts=[conflict])
        mcp = _StubMCP()
        mod = CalendarModule()
        mod._provider = provider

        await mod.register_tools(
            mcp=mcp,
            config={
                "provider": "google",
                "calendar_id": "primary",
                "conflicts": {"policy": "suggest", "require_approval_for_overlap": False},
            },
            db=SimpleNamespace(db_name="butler_general"),
        )

        result = await mcp.tools["calendar_create_event"](
            title="Team Sync",
            start_at=datetime(2026, 2, 20, 14, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 15, 0, tzinfo=UTC),
            conflict_policy="allow_overlap",
        )

        assert len(provider.create_calls) == 1
        assert result["status"] == "created"
        assert result["policy"] == "allow_overlap"
        assert result["conflicts"][0]["event_id"] == "busy-1"
        assert result["suggested_slots"] == []

    async def test_create_conflict_allow_overlap_requires_approval_when_enqueuer_set(self):
        """allow_overlap + require_approval_for_overlap=True + enqueuer wired
        returns approval_required and does NOT write."""
        created = CalendarEvent(
            event_id="evt-created",
            title="BUTLER: Team Sync",
            start_at=datetime(2026, 2, 20, 14, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 15, 0, tzinfo=UTC),
            timezone="UTC",
            butler_generated=True,
            butler_name="general",
        )
        conflict = CalendarEvent(
            event_id="busy-1",
            title="(busy)",
            start_at=datetime(2026, 2, 20, 14, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 15, 0, tzinfo=UTC),
            timezone="UTC",
        )
        provider = _ProviderDouble(event=created, conflicts=[conflict])
        mcp = _StubMCP()
        mod = CalendarModule()
        mod._provider = provider

        await mod.register_tools(
            mcp=mcp,
            config={"provider": "google", "calendar_id": "primary"},
            db=SimpleNamespace(db_name="butler_general"),
        )

        enqueue_calls: list[tuple[str, dict, str]] = []

        async def _mock_enqueuer(tool_name: str, tool_args: dict, agent_summary: str) -> str:
            enqueue_calls.append((tool_name, tool_args, agent_summary))
            return "action-abc"

        mod.set_approval_enqueuer(_mock_enqueuer)

        result = await mcp.tools["calendar_create_event"](
            title="Team Sync",
            start_at=datetime(2026, 2, 20, 14, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 15, 0, tzinfo=UTC),
            conflict_policy="allow_overlap",
        )

        assert provider.create_calls == []
        assert result["status"] == "approval_required"
        assert result["action_id"] == "action-abc"
        assert result["policy"] == "allow_overlap"
        assert len(enqueue_calls) == 1
        assert enqueue_calls[0][0] == "calendar_create_event"

    async def test_create_conflict_allow_overlap_returns_fallback_when_approvals_disabled(self):
        """allow_overlap + require_approval_for_overlap=True + no enqueuer
        returns approval_unavailable guidance."""
        created = CalendarEvent(
            event_id="evt-created",
            title="BUTLER: Team Sync",
            start_at=datetime(2026, 2, 20, 14, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 15, 0, tzinfo=UTC),
            timezone="UTC",
            butler_generated=True,
            butler_name="general",
        )
        conflict = CalendarEvent(
            event_id="busy-1",
            title="(busy)",
            start_at=datetime(2026, 2, 20, 14, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 15, 0, tzinfo=UTC),
            timezone="UTC",
        )
        provider = _ProviderDouble(event=created, conflicts=[conflict])
        mcp = _StubMCP()
        mod = CalendarModule()
        mod._provider = provider

        await mod.register_tools(
            mcp=mcp,
            config={"provider": "google", "calendar_id": "primary"},
            db=SimpleNamespace(db_name="butler_general"),
        )

        result = await mcp.tools["calendar_create_event"](
            title="Team Sync",
            start_at=datetime(2026, 2, 20, 14, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 15, 0, tzinfo=UTC),
            conflict_policy="allow_overlap",
        )

        assert provider.create_calls == []
        assert result["status"] == "approval_unavailable"
        assert "approvals module is not enabled" in result["message"]

    async def test_update_conflict_allow_overlap_requires_approval_when_enqueuer_set(self):
        """Update with time change + allow_overlap + enqueuer wired returns
        approval_required and does NOT update."""
        existing = CalendarEvent(
            event_id="evt-human",
            title="Discuss roadmap",
            start_at=datetime(2026, 2, 21, 10, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 21, 11, 0, tzinfo=UTC),
            timezone="UTC",
            butler_generated=False,
            butler_name=None,
        )
        conflict = CalendarEvent(
            event_id="busy-1",
            title="(busy)",
            start_at=datetime(2026, 2, 21, 10, 30, tzinfo=UTC),
            end_at=datetime(2026, 2, 21, 11, 30, tzinfo=UTC),
            timezone="UTC",
        )
        provider = _ProviderDouble(event=existing, conflicts=[conflict])
        mcp = _StubMCP()
        mod = CalendarModule()
        mod._provider = provider

        await mod.register_tools(
            mcp=mcp,
            config={"provider": "google", "calendar_id": "primary"},
            db=None,
        )

        enqueue_calls: list[tuple[str, dict, str]] = []

        async def _mock_enqueuer(tool_name: str, tool_args: dict, agent_summary: str) -> str:
            enqueue_calls.append((tool_name, tool_args, agent_summary))
            return "action-def"

        mod.set_approval_enqueuer(_mock_enqueuer)

        result = await mcp.tools["calendar_update_event"](
            event_id="evt-human",
            start_at=datetime(2026, 2, 21, 10, 30, tzinfo=UTC),
            conflict_policy="allow_overlap",
        )

        assert provider.update_calls == []
        assert result["status"] == "approval_required"
        assert result["action_id"] == "action-def"
        assert len(enqueue_calls) == 1
        assert enqueue_calls[0][0] == "calendar_update_event"

    async def test_update_time_window_change_checks_conflicts(self):
        existing = CalendarEvent(
            event_id="evt-human",
            title="Discuss roadmap",
            start_at=datetime(2026, 2, 21, 10, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 21, 11, 0, tzinfo=UTC),
            timezone="UTC",
            butler_generated=False,
            butler_name=None,
        )
        conflict = CalendarEvent(
            event_id="busy-1",
            title="(busy)",
            start_at=datetime(2026, 2, 21, 10, 30, tzinfo=UTC),
            end_at=datetime(2026, 2, 21, 11, 30, tzinfo=UTC),
            timezone="UTC",
        )
        provider = _ProviderDouble(event=existing, conflicts=[conflict])
        mcp = _StubMCP()
        mod = CalendarModule()
        mod._provider = provider

        await mod.register_tools(
            mcp=mcp,
            config={"provider": "google", "calendar_id": "primary"},
            db=None,
        )

        result = await mcp.tools["calendar_update_event"](
            event_id="evt-human",
            start_at=datetime(2026, 2, 21, 10, 30, tzinfo=UTC),
            conflict_policy="fail",
        )

        assert len(provider.find_conflict_calls) == 1
        assert provider.update_calls == []
        assert result["status"] == "conflict"
        assert result["policy"] == "fail"
        assert result["suggested_slots"] == []

    async def test_update_without_time_change_skips_conflict_check(self):
        existing = CalendarEvent(
            event_id="evt-human",
            title="Discuss roadmap",
            start_at=datetime(2026, 2, 21, 10, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 21, 11, 0, tzinfo=UTC),
            timezone="UTC",
            butler_generated=False,
            butler_name=None,
        )
        updated = CalendarEvent(
            event_id="evt-human",
            title="Discuss roadmap",
            start_at=datetime(2026, 2, 21, 10, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 21, 11, 0, tzinfo=UTC),
            timezone="UTC",
            butler_generated=False,
            butler_name=None,
            location="Updated room",
        )
        conflict = CalendarEvent(
            event_id="busy-1",
            title="(busy)",
            start_at=datetime(2026, 2, 21, 10, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 21, 11, 0, tzinfo=UTC),
            timezone="UTC",
        )
        provider = _ProviderDouble(
            event=existing,
            update_event_result=updated,
            conflicts=[conflict],
        )
        mcp = _StubMCP()
        mod = CalendarModule()
        mod._provider = provider

        await mod.register_tools(
            mcp=mcp,
            config={"provider": "google", "calendar_id": "primary"},
            db=None,
        )

        result = await mcp.tools["calendar_update_event"](
            event_id="evt-human",
            location="Updated room",
            conflict_policy="fail",
        )

        assert provider.find_conflict_calls == []
        assert len(provider.update_calls) == 1
        assert result["status"] == "updated"


class TestGoogleCredentialParsing:
    """Verify credential JSON parsing and validation errors."""

    def test_missing_env_is_explicit(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv(GOOGLE_CALENDAR_CREDENTIALS_ENV, raising=False)

        with pytest.raises(CalendarCredentialError) as excinfo:
            _GoogleOAuthCredentials.from_env()

        assert GOOGLE_CALENDAR_CREDENTIALS_ENV in str(excinfo.value)
        assert "must be set" in str(excinfo.value)

    def test_invalid_json_is_explicit(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv(GOOGLE_CALENDAR_CREDENTIALS_ENV, "{not-valid-json")

        with pytest.raises(CalendarCredentialError) as excinfo:
            _GoogleOAuthCredentials.from_env()

        assert GOOGLE_CALENDAR_CREDENTIALS_ENV in str(excinfo.value)
        assert "must be valid JSON" in str(excinfo.value)

    def test_missing_fields_are_explicit(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv(
            GOOGLE_CALENDAR_CREDENTIALS_ENV,
            json.dumps({"client_id": "client-id-only"}),
        )

        with pytest.raises(CalendarCredentialError) as excinfo:
            _GoogleOAuthCredentials.from_env()

        message = str(excinfo.value)
        assert "missing required field(s)" in message
        assert "client_secret" in message
        assert "refresh_token" in message

    def test_supports_installed_json_shape(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv(
            GOOGLE_CALENDAR_CREDENTIALS_ENV,
            json.dumps(
                {
                    "installed": {
                        "client_id": "installed-client-id",
                        "client_secret": "installed-client-secret",
                    },
                    "refresh_token": "installed-refresh-token",
                }
            ),
        )

        creds = _GoogleOAuthCredentials.from_env()
        assert creds.client_id == "installed-client-id"
        assert creds.client_secret == "installed-client-secret"
        assert creds.refresh_token == "installed-refresh-token"


class TestGoogleProviderInitialization:
    """Verify provider init does not leak resources on credential failures."""

    def test_invalid_credentials_do_not_create_owned_http_client(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv(GOOGLE_CALENDAR_CREDENTIALS_ENV, "{not-valid-json")
        async_client_ctor = Mock()
        monkeypatch.setattr("butlers.modules.calendar.httpx.AsyncClient", async_client_ctor)

        with pytest.raises(CalendarCredentialError):
            _GoogleProvider(config=CalendarConfig(provider="google", calendar_id="primary"))

        async_client_ctor.assert_not_called()


def _mock_response(
    *,
    status_code: int,
    url: str,
    method: str = "GET",
    json_body: dict | None = None,
    text: str = "",
) -> httpx.Response:
    request = httpx.Request(method, url)
    if json_body is not None:
        return httpx.Response(status_code=status_code, json=json_body, request=request)
    return httpx.Response(status_code=status_code, text=text, request=request)


class TestGoogleOAuthClient:
    """Verify access-token refresh against Google OAuth endpoint."""

    async def test_refresh_uses_client_id_secret_and_refresh_token(self):
        credentials = _GoogleOAuthCredentials(
            client_id="client-id",
            client_secret="client-secret",
            refresh_token="refresh-token",
        )
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = _mock_response(
            status_code=200,
            url=GOOGLE_OAUTH_TOKEN_URL,
            method="POST",
            json_body={"access_token": "access-token", "expires_in": 3600},
        )

        oauth = _GoogleOAuthClient(credentials=credentials, http_client=mock_client)
        access_token = await oauth.get_access_token()
        cached_access_token = await oauth.get_access_token()

        assert access_token == "access-token"
        assert cached_access_token == "access-token"
        assert mock_client.post.call_count == 1
        mock_client.post.assert_called_once_with(
            GOOGLE_OAUTH_TOKEN_URL,
            data={
                "client_id": "client-id",
                "client_secret": "client-secret",
                "refresh_token": "refresh-token",
                "grant_type": "refresh_token",
            },
            headers={"Accept": "application/json"},
        )


class TestGoogleRequestHelper:
    """Verify bearer-token request wiring and safe non-2xx errors."""

    async def test_request_helper_injects_bearer_token(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv(
            GOOGLE_CALENDAR_CREDENTIALS_ENV,
            json.dumps(
                {
                    "client_id": "client-id",
                    "client_secret": "client-secret",
                    "refresh_token": "refresh-token",
                }
            ),
        )
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = _mock_response(
            status_code=200,
            url=GOOGLE_OAUTH_TOKEN_URL,
            method="POST",
            json_body={"access_token": "access-token", "expires_in": 3600},
        )
        mock_client.request.return_value = _mock_response(
            status_code=200,
            url=f"{GOOGLE_CALENDAR_API_BASE_URL}/calendars/primary/events",
            method="GET",
            json_body={"items": []},
        )

        provider = _GoogleProvider(
            config=CalendarConfig(provider="google", calendar_id="primary"),
            http_client=mock_client,
        )
        result = await provider._request_google_json("GET", "/calendars/primary/events")

        assert result == {"items": []}
        request_kwargs = mock_client.request.call_args.kwargs
        assert request_kwargs["headers"]["Authorization"] == "Bearer access-token"

    async def test_request_helper_surfaces_non_2xx_safely(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv(
            GOOGLE_CALENDAR_CREDENTIALS_ENV,
            json.dumps(
                {
                    "client_id": "client-id",
                    "client_secret": "client-secret",
                    "refresh_token": "refresh-token",
                }
            ),
        )
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = _mock_response(
            status_code=200,
            url=GOOGLE_OAUTH_TOKEN_URL,
            method="POST",
            json_body={"access_token": "access-token", "expires_in": 3600},
        )
        mock_client.request.return_value = _mock_response(
            status_code=403,
            url=f"{GOOGLE_CALENDAR_API_BASE_URL}/calendars/primary/events",
            method="GET",
            json_body={
                "error": {
                    "code": 403,
                    "message": "Forbidden by policy",
                    "status": "PERMISSION_DENIED",
                    "details": [{"private_token": "should-not-be-surfaced"}],
                }
            },
        )

        provider = _GoogleProvider(
            config=CalendarConfig(provider="google", calendar_id="primary"),
            http_client=mock_client,
        )

        with pytest.raises(CalendarRequestError) as excinfo:
            await provider._request_google_json("GET", "/calendars/primary/events")

        message = str(excinfo.value)
        assert excinfo.value.status_code == 403
        assert "Forbidden by policy" in message
        assert "private_token" not in message


class TestGoogleReadOperations:
    """Verify Google provider list/get read behavior."""

    async def test_list_events_maps_google_payload_to_calendar_events(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv(
            GOOGLE_CALENDAR_CREDENTIALS_ENV,
            json.dumps(
                {
                    "client_id": "client-id",
                    "client_secret": "client-secret",
                    "refresh_token": "refresh-token",
                }
            ),
        )
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = _mock_response(
            status_code=200,
            url=GOOGLE_OAUTH_TOKEN_URL,
            method="POST",
            json_body={"access_token": "access-token", "expires_in": 3600},
        )
        mock_client.request.return_value = _mock_response(
            status_code=200,
            url=f"{GOOGLE_CALENDAR_API_BASE_URL}/calendars/primary/events",
            method="GET",
            json_body={
                "items": [
                    {
                        "id": "evt-1",
                        "summary": "Team standup",
                        "description": "Daily check-in",
                        "location": "Zoom",
                        "start": {"dateTime": "2026-02-21T09:00:00-05:00"},
                        "end": {"dateTime": "2026-02-21T09:30:00-05:00"},
                        "attendees": [{"email": "alice@example.com"}],
                        "recurrence": ["RRULE:FREQ=DAILY"],
                        "colorId": "5",
                        "extendedProperties": {
                            "private": {
                                "butler_generated": "true",
                                "butler_name": "general",
                            }
                        },
                    },
                    {
                        "id": "evt-2",
                        "summary": "Holiday",
                        "start": {"date": "2026-02-22"},
                        "end": {"date": "2026-02-23"},
                    },
                ]
            },
        )

        provider = _GoogleProvider(
            config=CalendarConfig(provider="google", calendar_id="primary", timezone="UTC"),
            http_client=mock_client,
        )

        events = await provider.list_events(calendar_id="primary", limit=25)

        assert [event.event_id for event in events] == ["evt-1", "evt-2"]
        assert events[0].attendees == ["alice@example.com"]
        assert events[0].recurrence_rule == "RRULE:FREQ=DAILY"
        assert events[0].color_id == "5"
        assert events[0].butler_generated is True
        assert events[0].butler_name == "general"
        assert events[1].start_at.isoformat() == "2026-02-22T00:00:00+00:00"
        assert events[1].end_at.isoformat() == "2026-02-23T00:00:00+00:00"

        request_kwargs = mock_client.request.call_args.kwargs
        assert request_kwargs["params"]["maxResults"] == 25
        assert request_kwargs["params"]["singleEvents"] is True
        assert request_kwargs["params"]["showDeleted"] is False

    async def test_get_event_returns_none_on_404(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv(
            GOOGLE_CALENDAR_CREDENTIALS_ENV,
            json.dumps(
                {
                    "client_id": "client-id",
                    "client_secret": "client-secret",
                    "refresh_token": "refresh-token",
                }
            ),
        )
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = _mock_response(
            status_code=200,
            url=GOOGLE_OAUTH_TOKEN_URL,
            method="POST",
            json_body={"access_token": "access-token", "expires_in": 3600},
        )
        mock_client.request.return_value = _mock_response(
            status_code=404,
            url=f"{GOOGLE_CALENDAR_API_BASE_URL}/calendars/primary/events/missing",
            method="GET",
            json_body={"error": {"message": "Not found"}},
        )

        provider = _GoogleProvider(
            config=CalendarConfig(provider="google", calendar_id="primary", timezone="UTC"),
            http_client=mock_client,
        )

        result = await provider.get_event(calendar_id="primary", event_id="missing")
        assert result is None


class TestGooglePayloadValidationErrors:
    """Verify payload/data validation raises ValueError, not auth errors."""

    def test_parse_google_datetime_raises_value_error_for_invalid_datetime(self):
        with pytest.raises(ValueError, match="invalid dateTime"):
            _parse_google_datetime("not-a-datetime")

    def test_parse_google_event_boundary_raises_value_error_for_invalid_date(self):
        with pytest.raises(ValueError, match="invalid date value"):
            _parse_google_event_boundary({"date": "2026-99-99"}, fallback_timezone="UTC")

    def test_google_event_to_calendar_event_raises_value_error_for_missing_id(self):
        with pytest.raises(ValueError, match="missing a non-empty id"):
            _google_event_to_calendar_event(
                {
                    "start": {"dateTime": "2026-02-21T09:00:00Z"},
                    "end": {"dateTime": "2026-02-21T09:30:00Z"},
                },
                fallback_timezone="UTC",
            )


class TestEventPayloadNormalization:
    """Verify canonical payload normalization and validation rules."""

    def _config(self) -> CalendarConfig:
        return CalendarConfig(
            provider="google",
            calendar_id="primary",
            timezone="America/Los_Angeles",
            event_defaults={"enabled": True, "minutes_before": 30, "color_id": "7"},
        )

    def test_timed_event_uses_defaults_and_trims_text_fields(self):
        payload = CalendarEventPayloadInput(
            title="  Team Sync  ",
            start_at=datetime(2026, 2, 14, 9, 0),
            end_at=datetime(2026, 2, 14, 10, 0),
            description="  Agenda review  ",
            location="  Room 5  ",
            attendees=["  alice@example.com ", " ", "bob@example.com  "],
        )

        normalized = normalize_event_payload(payload, config=self._config())

        assert normalized.title == "Team Sync"
        assert normalized.all_day is False
        assert normalized.timezone == "America/Los_Angeles"
        assert normalized.start.date_time_value is not None
        assert normalized.end.date_time_value is not None
        assert normalized.start.date_time_value.tzinfo is not None
        assert normalized.end.date_time_value.tzinfo is not None
        assert normalized.start.timezone == "America/Los_Angeles"
        assert normalized.description == "Agenda review"
        assert normalized.location == "Room 5"
        assert normalized.attendees == ["alice@example.com", "bob@example.com"]
        assert normalized.notification.enabled is True
        assert normalized.notification.minutes_before == 30
        assert normalized.color_id == "7"

    def test_all_day_inferred_from_date_inputs(self):
        payload = CalendarEventPayloadInput(
            title="Offsite",
            start_at=date(2026, 3, 1),
            end_at=date(2026, 3, 2),
        )

        normalized = normalize_event_payload(payload, config=self._config())

        assert normalized.all_day is True
        assert normalized.start.date_value == date(2026, 3, 1)
        assert normalized.end.date_value == date(2026, 3, 2)
        assert normalized.start.date_time_value is None
        assert normalized.end.date_time_value is None
        assert normalized.timezone == "America/Los_Angeles"

    def test_notification_shorthands_map_deterministically(self):
        disabled = normalize_event_payload(
            {
                "title": "Quiet block",
                "start_at": datetime(2026, 5, 1, 12, 0),
                "end_at": datetime(2026, 5, 1, 13, 0),
                "notification": False,
            },
            config=self._config(),
        )
        custom = normalize_event_payload(
            {
                "title": "Ping me",
                "start_at": datetime(2026, 5, 2, 12, 0),
                "end_at": datetime(2026, 5, 2, 13, 0),
                "notification": 5,
            },
            config=self._config(),
        )
        explicit = normalize_event_payload(
            {
                "title": "Default reminder",
                "start_at": datetime(2026, 5, 3, 12, 0),
                "end_at": datetime(2026, 5, 3, 13, 0),
                "notification": {"enabled": True},
            },
            config=self._config(),
        )

        assert disabled.notification.enabled is False
        assert disabled.notification.minutes_before is None
        assert custom.notification.enabled is True
        assert custom.notification.minutes_before == 5
        assert explicit.notification.enabled is True
        assert explicit.notification.minutes_before == 30

    def test_color_prefers_payload_value_over_defaults(self):
        normalized = normalize_event_payload(
            {
                "title": "Colored",
                "start_at": datetime(2026, 6, 1, 9, 0),
                "end_at": datetime(2026, 6, 1, 10, 0),
                "color_id": " 11 ",
            },
            config=self._config(),
        )

        assert normalized.color_id == "11"

    @pytest.mark.parametrize(
        ("payload", "message"),
        [
            (
                {
                    "title": "Mismatch",
                    "start_at": date(2026, 4, 1),
                    "end_at": datetime(2026, 4, 1, 10, 0),
                },
                "both be date values or both datetime values",
            ),
            (
                {
                    "title": "Bad all_day",
                    "start_at": datetime(2026, 4, 1, 9, 0),
                    "end_at": datetime(2026, 4, 1, 10, 0),
                    "all_day": True,
                },
                "all_day events require date-only",
            ),
            (
                {
                    "title": "Bad timed",
                    "start_at": date(2026, 4, 1),
                    "end_at": date(2026, 4, 2),
                    "all_day": False,
                },
                "timed events require datetime",
            ),
            (
                {
                    "title": "Backwards timed",
                    "start_at": datetime(2026, 4, 1, 10, 0),
                    "end_at": datetime(2026, 4, 1, 9, 0),
                },
                "end_at must be after start_at for timed events",
            ),
            (
                {
                    "title": "Backwards all-day",
                    "start_at": date(2026, 4, 2),
                    "end_at": date(2026, 4, 1),
                    "all_day": True,
                },
                "end_at must be after start_at for all_day events",
            ),
            (
                {
                    "title": "Invalid notification",
                    "start_at": datetime(2026, 4, 1, 9, 0),
                    "end_at": datetime(2026, 4, 1, 10, 0),
                    "notification": {"enabled": False, "minutes_before": 5},
                },
                "notification.minutes_before cannot be set",
            ),
            (
                {
                    "title": "Invalid recurrence prefix",
                    "start_at": datetime(2026, 4, 1, 9, 0),
                    "end_at": datetime(2026, 4, 1, 10, 0),
                    "recurrence": "FREQ=DAILY",
                },
                "must start with 'RRULE:'",
            ),
            (
                {
                    "title": "Invalid recurrence freq",
                    "start_at": datetime(2026, 4, 1, 9, 0),
                    "end_at": datetime(2026, 4, 1, 10, 0),
                    "recurrence": "RRULE:INTERVAL=1",
                },
                "must include a FREQ component",
            ),
            (
                {
                    "title": "Invalid recurrence dtstart",
                    "start_at": datetime(2026, 4, 1, 9, 0),
                    "end_at": datetime(2026, 4, 1, 10, 0),
                    "recurrence": "RRULE:FREQ=DAILY;DTSTART=20260401T090000Z",
                },
                "must not include DTSTART/DTEND",
            ),
            (
                {
                    "title": "Invalid recurrence lowercase dtstart",
                    "start_at": datetime(2026, 4, 1, 9, 0),
                    "end_at": datetime(2026, 4, 1, 10, 0),
                    "recurrence": "RRULE:FREQ=DAILY;dtstart=20260401T090000Z",
                },
                "must not include DTSTART/DTEND",
            ),
            (
                {
                    "title": "Invalid recurrence newline injection",
                    "start_at": datetime(2026, 4, 1, 9, 0),
                    "end_at": datetime(2026, 4, 1, 10, 0),
                    "recurrence": "RRULE:FREQ=DAILY\nDTSTART:20260401T090000Z",
                },
                "must not contain newline characters",
            ),
        ],
    )
    def test_invalid_payloads_raise_clear_errors(self, payload, message):
        with pytest.raises(ValueError, match=message):
            normalize_event_payload(payload, config=self._config())

    def test_invalid_timezone_is_rejected(self):
        with pytest.raises(ValidationError, match="timezone must be a valid IANA timezone"):
            CalendarEventPayloadInput(
                title="Bad timezone",
                start_at=datetime(2026, 4, 1, 9, 0),
                end_at=datetime(2026, 4, 1, 10, 0),
                timezone="Mars/Olympus",
            )

    def test_recurrence_strings_are_trimmed_and_normalized(self):
        normalized = normalize_event_payload(
            {
                "title": "Recurring",
                "start_at": datetime(2026, 7, 1, 9, 0, tzinfo=UTC),
                "end_at": datetime(2026, 7, 1, 10, 0, tzinfo=UTC),
                "recurrence": ["  RRULE:FREQ=WEEKLY;INTERVAL=1  "],
            },
            config=self._config(),
        )

        assert normalized.recurrence == ["RRULE:FREQ=WEEKLY;INTERVAL=1"]


class TestCalendarOverlapApprovalGate:
    """Verify overlap override gating with conditional approvals."""

    async def test_create_allow_overlap_returns_approval_required_when_enqueuer_set(self):
        """allow_overlap + require_approval_for_overlap=True + enqueuer set
        should return approval_required and NOT write the event."""
        created = CalendarEvent(
            event_id="evt-created",
            title="BUTLER: Team Sync",
            start_at=datetime(2026, 2, 20, 14, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 15, 0, tzinfo=UTC),
            timezone="UTC",
            butler_generated=True,
            butler_name="general",
        )
        conflict = CalendarEvent(
            event_id="busy-1",
            title="(busy)",
            start_at=datetime(2026, 2, 20, 14, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 15, 0, tzinfo=UTC),
            timezone="UTC",
        )
        provider = _ProviderDouble(event=created, conflicts=[conflict])
        mcp = _StubMCP()
        mod = CalendarModule()
        mod._provider = provider

        await mod.register_tools(
            mcp=mcp,
            config={
                "provider": "google",
                "calendar_id": "primary",
                "conflicts": {"policy": "allow_overlap", "require_approval_for_overlap": True},
            },
            db=SimpleNamespace(db_name="butler_general"),
        )

        enqueue_calls: list[tuple[str, dict, str]] = []

        async def _mock_enqueuer(tool_name: str, tool_args: dict, agent_summary: str) -> str:
            enqueue_calls.append((tool_name, tool_args, agent_summary))
            return "action-123"

        mod.set_approval_enqueuer(_mock_enqueuer)

        result = await mcp.tools["calendar_create_event"](
            title="Team Sync",
            start_at=datetime(2026, 2, 20, 14, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 15, 0, tzinfo=UTC),
            conflict_policy="allow_overlap",
        )

        # Should NOT have written the event.
        assert provider.create_calls == []

        # Should return approval_required with action_id.
        assert result["status"] == "approval_required"
        assert result["action_id"] == "action-123"
        assert result["policy"] == "allow_overlap"
        assert result["conflicts"][0]["event_id"] == "busy-1"
        assert "queued for approval" in result["message"]

        # Should have enqueued exactly one action with correct tool name.
        assert len(enqueue_calls) == 1
        assert enqueue_calls[0][0] == "calendar_create_event"
        # tool_args should contain sufficient context to replay the call.
        tool_args = enqueue_calls[0][1]
        assert tool_args["title"] == "Team Sync"
        assert "conflict_policy" in tool_args

    async def test_create_allow_overlap_returns_fallback_when_approvals_disabled(self):
        """allow_overlap + require_approval_for_overlap=True + no enqueuer
        should return approval_unavailable guidance."""
        created = CalendarEvent(
            event_id="evt-created",
            title="BUTLER: Team Sync",
            start_at=datetime(2026, 2, 20, 14, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 15, 0, tzinfo=UTC),
            timezone="UTC",
            butler_generated=True,
            butler_name="general",
        )
        conflict = CalendarEvent(
            event_id="busy-1",
            title="(busy)",
            start_at=datetime(2026, 2, 20, 14, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 15, 0, tzinfo=UTC),
            timezone="UTC",
        )
        provider = _ProviderDouble(event=created, conflicts=[conflict])
        mcp = _StubMCP()
        mod = CalendarModule()
        mod._provider = provider

        await mod.register_tools(
            mcp=mcp,
            config={
                "provider": "google",
                "calendar_id": "primary",
                "conflicts": {"policy": "allow_overlap", "require_approval_for_overlap": True},
            },
            db=SimpleNamespace(db_name="butler_general"),
        )

        # No enqueuer set -- approvals module is disabled.
        assert not mod.approvals_enabled

        result = await mcp.tools["calendar_create_event"](
            title="Team Sync",
            start_at=datetime(2026, 2, 20, 14, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 15, 0, tzinfo=UTC),
            conflict_policy="allow_overlap",
        )

        assert provider.create_calls == []
        assert result["status"] == "approval_unavailable"
        assert result["policy"] == "allow_overlap"
        assert "approvals module is not enabled" in result["message"]
        assert result["conflicts"][0]["event_id"] == "busy-1"

    async def test_create_allow_overlap_writes_through_when_approval_not_required(self):
        """allow_overlap + require_approval_for_overlap=False should write through
        regardless of enqueuer state."""
        created = CalendarEvent(
            event_id="evt-created",
            title="BUTLER: Team Sync",
            start_at=datetime(2026, 2, 20, 14, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 15, 0, tzinfo=UTC),
            timezone="UTC",
            butler_generated=True,
            butler_name="general",
        )
        conflict = CalendarEvent(
            event_id="busy-1",
            title="(busy)",
            start_at=datetime(2026, 2, 20, 14, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 15, 0, tzinfo=UTC),
            timezone="UTC",
        )
        provider = _ProviderDouble(event=created, conflicts=[conflict])
        mcp = _StubMCP()
        mod = CalendarModule()
        mod._provider = provider

        await mod.register_tools(
            mcp=mcp,
            config={
                "provider": "google",
                "calendar_id": "primary",
                "conflicts": {"policy": "allow_overlap", "require_approval_for_overlap": False},
            },
            db=SimpleNamespace(db_name="butler_general"),
        )

        # Even with an enqueuer set, require_approval_for_overlap=False skips gating.
        async def _mock_enqueuer(tool_name: str, tool_args: dict, summary: str) -> str:
            raise AssertionError("enqueuer should not be called")

        mod.set_approval_enqueuer(_mock_enqueuer)

        result = await mcp.tools["calendar_create_event"](
            title="Team Sync",
            start_at=datetime(2026, 2, 20, 14, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 15, 0, tzinfo=UTC),
            conflict_policy="allow_overlap",
        )

        assert len(provider.create_calls) == 1
        assert result["status"] == "created"
        assert result["policy"] == "allow_overlap"

    async def test_create_no_conflicts_writes_through_even_with_approval(self):
        """When there are no conflicts, the event should be created normally
        even with require_approval_for_overlap=True."""
        created = CalendarEvent(
            event_id="evt-created",
            title="BUTLER: Team Sync",
            start_at=datetime(2026, 2, 20, 14, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 15, 0, tzinfo=UTC),
            timezone="UTC",
            butler_generated=True,
            butler_name="general",
        )
        # No conflicts.
        provider = _ProviderDouble(event=created, conflicts=[])
        mcp = _StubMCP()
        mod = CalendarModule()
        mod._provider = provider

        await mod.register_tools(
            mcp=mcp,
            config={
                "provider": "google",
                "calendar_id": "primary",
                "conflicts": {"policy": "allow_overlap", "require_approval_for_overlap": True},
            },
            db=SimpleNamespace(db_name="butler_general"),
        )

        async def _mock_enqueuer(tool_name: str, tool_args: dict, summary: str) -> str:
            raise AssertionError("enqueuer should not be called when no conflicts")

        mod.set_approval_enqueuer(_mock_enqueuer)

        result = await mcp.tools["calendar_create_event"](
            title="Team Sync",
            start_at=datetime(2026, 2, 20, 14, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 15, 0, tzinfo=UTC),
            conflict_policy="allow_overlap",
        )

        assert len(provider.create_calls) == 1
        assert result["status"] == "created"

    async def test_update_allow_overlap_returns_approval_required_when_enqueuer_set(self):
        """Update with time change + allow_overlap + approval required should
        return approval_required and NOT update the event."""
        existing = CalendarEvent(
            event_id="evt-existing",
            title="Standup",
            start_at=datetime(2026, 2, 21, 10, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 21, 11, 0, tzinfo=UTC),
            timezone="UTC",
            butler_generated=False,
            butler_name=None,
        )
        conflict = CalendarEvent(
            event_id="busy-1",
            title="(busy)",
            start_at=datetime(2026, 2, 21, 10, 30, tzinfo=UTC),
            end_at=datetime(2026, 2, 21, 11, 30, tzinfo=UTC),
            timezone="UTC",
        )
        provider = _ProviderDouble(event=existing, conflicts=[conflict])
        mcp = _StubMCP()
        mod = CalendarModule()
        mod._provider = provider

        await mod.register_tools(
            mcp=mcp,
            config={
                "provider": "google",
                "calendar_id": "primary",
                "conflicts": {"policy": "allow_overlap", "require_approval_for_overlap": True},
            },
            db=None,
        )

        enqueue_calls: list[tuple[str, dict, str]] = []

        async def _mock_enqueuer(tool_name: str, tool_args: dict, agent_summary: str) -> str:
            enqueue_calls.append((tool_name, tool_args, agent_summary))
            return "action-456"

        mod.set_approval_enqueuer(_mock_enqueuer)

        result = await mcp.tools["calendar_update_event"](
            event_id="evt-existing",
            start_at=datetime(2026, 2, 21, 10, 30, tzinfo=UTC),
            conflict_policy="allow_overlap",
        )

        # Should NOT have updated the event.
        assert provider.update_calls == []

        # Should return approval_required.
        assert result["status"] == "approval_required"
        assert result["action_id"] == "action-456"
        assert result["policy"] == "allow_overlap"
        assert result["conflicts"][0]["event_id"] == "busy-1"

        # Enqueued action should reference the update tool.
        assert len(enqueue_calls) == 1
        assert enqueue_calls[0][0] == "calendar_update_event"
        tool_args = enqueue_calls[0][1]
        assert tool_args["event_id"] == "evt-existing"

    async def test_update_allow_overlap_returns_fallback_when_approvals_disabled(self):
        """Update with time change + allow_overlap + no enqueuer should
        return approval_unavailable."""
        existing = CalendarEvent(
            event_id="evt-existing",
            title="Standup",
            start_at=datetime(2026, 2, 21, 10, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 21, 11, 0, tzinfo=UTC),
            timezone="UTC",
            butler_generated=False,
            butler_name=None,
        )
        conflict = CalendarEvent(
            event_id="busy-1",
            title="(busy)",
            start_at=datetime(2026, 2, 21, 10, 30, tzinfo=UTC),
            end_at=datetime(2026, 2, 21, 11, 30, tzinfo=UTC),
            timezone="UTC",
        )
        provider = _ProviderDouble(event=existing, conflicts=[conflict])
        mcp = _StubMCP()
        mod = CalendarModule()
        mod._provider = provider

        await mod.register_tools(
            mcp=mcp,
            config={
                "provider": "google",
                "calendar_id": "primary",
                "conflicts": {"policy": "allow_overlap", "require_approval_for_overlap": True},
            },
            db=None,
        )

        result = await mcp.tools["calendar_update_event"](
            event_id="evt-existing",
            start_at=datetime(2026, 2, 21, 10, 30, tzinfo=UTC),
            conflict_policy="allow_overlap",
        )

        assert provider.update_calls == []
        assert result["status"] == "approval_unavailable"
        assert "approvals module is not enabled" in result["message"]

    async def test_pending_action_context_contains_sufficient_replay_data(self):
        """The tool_args passed to the enqueuer must contain all parameters
        needed to replay the original create_event call."""
        created = CalendarEvent(
            event_id="evt-created",
            title="BUTLER: Planning",
            start_at=datetime(2026, 3, 1, 9, 0, tzinfo=UTC),
            end_at=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
            timezone="America/New_York",
            butler_generated=True,
            butler_name="general",
        )
        conflict = CalendarEvent(
            event_id="busy-1",
            title="(busy)",
            start_at=datetime(2026, 3, 1, 9, 30, tzinfo=UTC),
            end_at=datetime(2026, 3, 1, 10, 30, tzinfo=UTC),
            timezone="America/New_York",
        )
        provider = _ProviderDouble(event=created, conflicts=[conflict])
        mcp = _StubMCP()
        mod = CalendarModule()
        mod._provider = provider

        await mod.register_tools(
            mcp=mcp,
            config={
                "provider": "google",
                "calendar_id": "primary",
                "conflicts": {"policy": "allow_overlap", "require_approval_for_overlap": True},
            },
            db=SimpleNamespace(db_name="butler_general"),
        )

        enqueue_calls: list[tuple[str, dict, str]] = []

        async def _mock_enqueuer(tool_name: str, tool_args: dict, agent_summary: str) -> str:
            enqueue_calls.append((tool_name, tool_args, agent_summary))
            return "action-789"

        mod.set_approval_enqueuer(_mock_enqueuer)

        await mcp.tools["calendar_create_event"](
            title="Planning",
            start_at=datetime(2026, 3, 1, 9, 0, tzinfo=UTC),
            end_at=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
            timezone="America/New_York",
            description="Q1 review",
            location="Room 5",
            attendees=["alice@example.com"],
            color_id="3",
            conflict_policy="allow_overlap",
        )

        assert len(enqueue_calls) == 1
        tool_args = enqueue_calls[0][1]

        # All create_event parameters must be present in tool_args.
        assert tool_args["title"] == "Planning"
        assert "start_at" in tool_args  # ISO string
        assert "end_at" in tool_args
        assert tool_args["timezone"] == "America/New_York"
        assert tool_args["description"] == "Q1 review"
        assert tool_args["location"] == "Room 5"
        assert tool_args["attendees"] == ["alice@example.com"]
        assert tool_args["color_id"] == "3"
        assert tool_args["conflict_policy"] == "allow_overlap"

        # Agent summary should be informative.
        summary = enqueue_calls[0][2]
        assert "calendar_create_event" in summary
        assert "1" in summary  # conflict count

    async def test_approvals_enabled_property(self):
        """approvals_enabled should reflect enqueuer state."""
        mod = CalendarModule()
        assert mod.approvals_enabled is False

        async def _noop(t: str, a: dict, s: str) -> str:
            return "x"

        mod.set_approval_enqueuer(_noop)
        assert mod.approvals_enabled is True
