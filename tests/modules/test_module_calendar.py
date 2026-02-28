"""Tests for calendar module orchestration, provider wiring, and integration.

## Layer Ownership

This file owns **module-level** (orchestration/integration) tests: MCP tool
wiring, provider abstraction, conflict-policy dispatch, approval-gate
integration, and full event payload serialization.

| Layer | Owned by |
|-------|----------|
| Pure helpers (`_extract_google_*`, `_normalize_optional_text`, etc.) | test_calendar_helpers.py |
| OAuth credential edge cases (whitespace/type/non-object) | test_calendar_unit_behaviors.py |
| OAuth token caching, force-refresh, error paths | test_calendar_unit_behaviors.py |
| Conflict policy enum aliases | test_calendar_unit_behaviors.py |
| MCP tool orchestration / provider wiring | THIS FILE |
| CalendarConfig validation, defaults, unknown-key rejection | THIS FILE |
| Credential JSON parsing (from_json wiring) | THIS FILE |
| OAuth HTTP request body wiring | THIS FILE |
| Error hierarchy / fail-open / fail-closed / rate-limit retry | test_calendar_error_handling.py |
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from pydantic import BaseModel, ValidationError

from butlers.modules.base import Module
from butlers.modules.calendar import (
    BUTLER_GENERATED_PRIVATE_KEY,
    BUTLER_NAME_PRIVATE_KEY,
    GOOGLE_CALENDAR_API_BASE_URL,
    GOOGLE_OAUTH_TOKEN_URL,
    AttendeeInfo,
    CalendarConfig,
    CalendarCredentialError,
    CalendarEvent,
    CalendarEventPayloadInput,
    CalendarModule,
    CalendarProvider,
    CalendarRequestError,
    EventStatus,
    EventVisibility,
    _extract_google_organizer,
    _google_event_to_calendar_event,
    _GoogleOAuthClient,
    _GoogleOAuthCredentials,
    _GoogleProvider,
    _parse_google_datetime,
    _parse_google_event_boundary,
    _parse_google_event_status,
    _parse_google_event_visibility,
    _parse_google_rfc3339_optional,
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
        env = CalendarModule().credentials_env
        assert env == []


class TestCalendarConfig:
    """Verify config validation, required fields, and defaults."""

    def test_provider_is_required(self):
        with pytest.raises(ValidationError):
            CalendarConfig(calendar_id="primary")

    def test_calendar_id_is_optional(self):
        config = CalendarConfig(provider="google")
        assert config.calendar_id is None

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

    def test_whitespace_only_calendar_id_becomes_none(self):
        config = CalendarConfig(provider="google", calendar_id="   ")
        assert config.calendar_id is None

    def test_timezone_rejects_empty_string(self):
        with pytest.raises(ValidationError, match="timezone must be a non-empty string"):
            CalendarConfig(provider="google", timezone="   ")

    def test_nested_defaults_forbid_unknown_keys(self):
        with pytest.raises(ValidationError) as conflict_error:
            CalendarConfig(
                provider="google",
                conflicts={"policy": "suggest", "unexpected": True},
            )
        assert conflict_error.value.errors()[0]["loc"] == ("conflicts", "unexpected")
        assert conflict_error.value.errors()[0]["type"] == "extra_forbidden"

        with pytest.raises(ValidationError) as defaults_error:
            CalendarConfig(
                provider="google",
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
            "sync_incremental",
            "shutdown",
        }
        assert expected.issubset(abstract_methods)


class TestModuleStartup:
    """Verify startup provider selection behavior."""

    async def test_startup_accepts_supported_provider(self):
        store = AsyncMock()

        async def _resolve(key: str, env_fallback: bool = False):
            values = {
                "GOOGLE_OAUTH_CLIENT_ID": "test-client-id",
                "GOOGLE_OAUTH_CLIENT_SECRET": "test-client-secret",
                "GOOGLE_CALENDAR_ID": "test-calendar-id",
            }
            return values.get(key)

        store.resolve.side_effect = _resolve
        db = MagicMock()
        db.pool = MagicMock()
        mod = CalendarModule()
        with patch(
            "butlers.credential_store.resolve_owner_contact_info",
            new_callable=AsyncMock,
            return_value="test-refresh-token",
        ):
            await mod.on_startup(
                {"provider": "google"},
                db=db,
                credential_store=store,
            )

        # Verify provider was selected and is usable by later tools.
        provider = getattr(mod, "_provider")
        assert provider is not None
        assert provider.name == "google"
        assert mod._resolved_calendar_id == "test-calendar-id"

    async def test_startup_fails_clearly_on_unsupported_provider(self):
        mod = CalendarModule()
        with pytest.raises(RuntimeError) as excinfo:
            await mod.on_startup({"provider": "outlook"}, db=None)

        error_message = str(excinfo.value)
        assert "Unsupported calendar provider 'outlook'" in error_message
        assert "Supported providers: google" in error_message

    async def test_register_tools_accepts_validated_config(self):
        mod = CalendarModule()
        cfg = CalendarConfig(provider="google")
        await mod.register_tools(mcp=_StubMCP(), config=cfg, db=None)
        assert isinstance(getattr(mod, "_config"), CalendarConfig)

    async def test_register_tools_accepts_dict_config(self):
        mod = CalendarModule()
        await mod.register_tools(
            mcp=_StubMCP(),
            config={"provider": "google"},
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

    async def add_attendees(
        self,
        *,
        calendar_id: str,
        event_id: str,
        attendees: list[str],
        optional: bool = False,
        send_updates: str = "none",
    ) -> CalendarEvent:  # pragma: no cover
        raise NotImplementedError

    async def remove_attendees(
        self,
        *,
        calendar_id: str,
        event_id: str,
        attendees: list[str],
        send_updates: str = "none",
    ) -> CalendarEvent:  # pragma: no cover
        raise NotImplementedError

    async def find_conflicts(self, *, calendar_id: str, candidate):
        self.find_conflict_calls.append({"calendar_id": calendar_id, "candidate": candidate})
        return list(self._conflicts)

    async def sync_incremental(
        self,
        *,
        calendar_id: str,
        sync_token: str | None,
        full_sync_window_days: int = 30,
    ) -> tuple[list, list[str], str]:
        return [], [], "token-stub"

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
            attendees=[AttendeeInfo(email="alex@example.com")],
            recurrence_rule="RRULE:FREQ=WEEKLY",
            color_id="7",
        )
        provider = _ProviderDouble(events=[event], event=event)
        mcp = _StubMCP()
        mod = CalendarModule()
        mod._provider = provider
        mod._resolved_calendar_id = "primary"

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
        expected_attendee = {
            "email": "alex@example.com",
            "display_name": None,
            "response_status": "needsAction",
            "optional": False,
            "organizer": False,
            "self": False,
            "comment": None,
        }
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
                    "attendees": [expected_attendee],
                    "recurrence_rule": "RRULE:FREQ=WEEKLY",
                    "color_id": "7",
                    "butler_generated": False,
                    "butler_name": None,
                    "status": None,
                    "organizer": None,
                    "visibility": None,
                    "etag": None,
                    "created_at": None,
                    "updated_at": None,
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
                "attendees": [expected_attendee],
                "recurrence_rule": "RRULE:FREQ=WEEKLY",
                "color_id": "7",
                "butler_generated": False,
                "butler_name": None,
                "status": None,
                "organizer": None,
                "visibility": None,
                "etag": None,
                "created_at": None,
                "updated_at": None,
            },
        }

    async def test_calendar_id_override_is_applied_without_mutating_default(self):
        provider = _ProviderDouble()
        mcp = _StubMCP()
        mod = CalendarModule()
        mod._provider = provider
        mod._resolved_calendar_id = "primary"

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
        mod._resolved_calendar_id = "primary"
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
        mod._resolved_calendar_id = "primary"

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
        mod._resolved_calendar_id = "primary"

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
        mod._resolved_calendar_id = "primary"

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
        mod._resolved_calendar_id = "primary"

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
        mod._resolved_calendar_id = "primary"

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
        mod._resolved_calendar_id = "primary"

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
        mod._resolved_calendar_id = "primary"

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
        mod._resolved_calendar_id = "primary"

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
        mod._resolved_calendar_id = "primary"

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
        mod._resolved_calendar_id = "primary"

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
        mod._resolved_calendar_id = "primary"

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
        mod._resolved_calendar_id = "primary"

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
        mod._resolved_calendar_id = "primary"

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
        mod._resolved_calendar_id = "primary"

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
        mod._resolved_calendar_id = "primary"

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
        mod._resolved_calendar_id = "primary"

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
        mod._resolved_calendar_id = "primary"

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
    """Verify credential JSON parsing and validation wiring.

    Note: Edge cases (whitespace stripping, non-object JSON, invalid field
    types, from_env removal) are covered in
    test_calendar_unit_behaviors.py::TestGoogleOAuthCredentials.
    This class owns the integration-level from_json() paths: malformed JSON,
    missing-field error messages, and the nested 'installed' shape.
    """

    def test_invalid_json_is_explicit(self, monkeypatch: pytest.MonkeyPatch):
        """from_json() raises CalendarCredentialError for malformed JSON."""
        with pytest.raises(CalendarCredentialError) as excinfo:
            from butlers.modules.calendar import _GoogleOAuthCredentials

            _GoogleOAuthCredentials.from_json("{not-valid-json")

        assert "valid JSON" in str(excinfo.value)

    def test_missing_fields_are_explicit(self):
        with pytest.raises(CalendarCredentialError) as excinfo:
            _GoogleOAuthCredentials.from_json(json.dumps({"client_id": "client-id-only"}))

        message = str(excinfo.value)
        assert "missing" in message.lower()
        assert "client_secret" in message
        assert "refresh_token" in message

    def test_supports_installed_json_shape_via_from_json(self):
        """from_json() supports the nested 'installed' JSON shape."""
        raw_json = json.dumps(
            {
                "installed": {
                    "client_id": "installed-client-id",
                    "client_secret": "installed-client-secret",
                },
                "refresh_token": "installed-refresh-token",
            }
        )

        creds = _GoogleOAuthCredentials.from_json(raw_json)
        assert creds.client_id == "installed-client-id"
        assert creds.client_secret == "installed-client-secret"
        assert creds.refresh_token == "installed-refresh-token"


def _make_test_credentials() -> _GoogleOAuthCredentials:
    """Return a valid _GoogleOAuthCredentials instance for testing."""
    return _GoogleOAuthCredentials(
        client_id="test-client-id",
        client_secret="test-client-secret",
        refresh_token="test-refresh-token",
    )


class TestGoogleProviderInitialization:
    """Verify provider init does not leak resources on credential failures."""

    def test_valid_credentials_creates_provider(self):
        """_GoogleProvider initialises successfully when valid credentials are passed."""
        creds = _make_test_credentials()
        provider = _GoogleProvider(
            config=CalendarConfig(provider="google", calendar_id="primary"),
            credentials=creds,
        )
        assert provider is not None

    def test_invalid_credentials_raise_on_construction(self, monkeypatch: pytest.MonkeyPatch):
        """Passing invalid credential values raises a validation error."""
        with pytest.raises(Exception):
            _GoogleOAuthCredentials(client_id="", client_secret="s", refresh_token="r")


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
    """Verify access-token HTTP request wiring against Google OAuth endpoint.

    Note: Token caching, force-refresh, and error paths are covered in
    test_calendar_unit_behaviors.py::TestGoogleOAuthClient.
    This class owns the integration wiring: exact HTTP body format, endpoint
    URL, and caching at the level of the OAuth client integration.
    """

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

    async def test_request_helper_injects_bearer_token(self):
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
            credentials=_make_test_credentials(),
            http_client=mock_client,
        )
        result = await provider._request_google_json("GET", "/calendars/primary/events")

        assert result == {"items": []}
        request_kwargs = mock_client.request.call_args.kwargs
        assert request_kwargs["headers"]["Authorization"] == "Bearer access-token"

    async def test_request_helper_surfaces_non_2xx_safely(self):
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
            credentials=_make_test_credentials(),
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

    async def test_list_events_maps_google_payload_to_calendar_events(self):
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
            credentials=_make_test_credentials(),
            http_client=mock_client,
        )

        events = await provider.list_events(calendar_id="primary", limit=25)

        assert [event.event_id for event in events] == ["evt-1", "evt-2"]
        assert len(events[0].attendees) == 1
        assert events[0].attendees[0].email == "alice@example.com"
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

    async def test_get_event_returns_none_on_404(self):
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
            credentials=_make_test_credentials(),
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
        mod._resolved_calendar_id = "primary"

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
        mod._resolved_calendar_id = "primary"

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
        mod._resolved_calendar_id = "primary"

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
        mod._resolved_calendar_id = "primary"

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
        mod._resolved_calendar_id = "primary"

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
        mod._resolved_calendar_id = "primary"

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
        mod._resolved_calendar_id = "primary"

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


# ============================================================================
# CalendarEvent Extended Field Tests (spec section 5.1)
# ============================================================================


class TestCalendarEventExtendedFields:
    """Verify CalendarEvent model includes all spec section 5.1 extended fields."""

    def test_calendar_event_has_optional_extended_fields(self):
        """CalendarEvent can be created without extended fields (all default to None)."""
        event = CalendarEvent(
            event_id="evt-basic",
            title="Basic Event",
            start_at=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
            end_at=datetime(2026, 3, 1, 11, 0, tzinfo=UTC),
            timezone="UTC",
        )
        assert event.status is None
        assert event.organizer is None
        assert event.visibility is None
        assert event.etag is None
        assert event.created_at is None
        assert event.updated_at is None

    def test_calendar_event_accepts_all_extended_fields(self):
        """CalendarEvent can be constructed with all extended fields set."""
        event = CalendarEvent(
            event_id="evt-full",
            title="Full Event",
            start_at=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
            end_at=datetime(2026, 3, 1, 11, 0, tzinfo=UTC),
            timezone="UTC",
            status=EventStatus.confirmed,
            organizer="organizer@example.com",
            visibility=EventVisibility.private,
            etag='"v1234567890"',
            created_at=datetime(2026, 1, 1, 9, 0, tzinfo=UTC),
            updated_at=datetime(2026, 2, 15, 10, 0, tzinfo=UTC),
        )
        assert event.status == EventStatus.confirmed
        assert event.organizer == "organizer@example.com"
        assert event.visibility == EventVisibility.private
        assert event.etag == '"v1234567890"'
        assert event.created_at == datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
        assert event.updated_at == datetime(2026, 2, 15, 10, 0, tzinfo=UTC)

    def test_event_status_enum_values(self):
        """EventStatus enum has all spec-required values."""
        assert EventStatus.confirmed.value == "confirmed"
        assert EventStatus.tentative.value == "tentative"
        assert EventStatus.cancelled.value == "cancelled"

    def test_event_visibility_enum_values(self):
        """EventVisibility enum has all spec-required values."""
        assert EventVisibility.default.value == "default"
        assert EventVisibility.public.value == "public"
        assert EventVisibility.private.value == "private"
        assert EventVisibility.confidential.value == "confidential"


# ============================================================================
# Google Payload Parsing Helper Tests (new field parsers)
# ============================================================================


class TestGoogleExtendedFieldParsers:
    """Verify parsing helpers for the new CalendarEvent fields."""

    def test_parse_google_event_status_confirmed(self):
        assert _parse_google_event_status("confirmed") == EventStatus.confirmed

    def test_parse_google_event_status_tentative(self):
        assert _parse_google_event_status("tentative") == EventStatus.tentative

    def test_parse_google_event_status_cancelled_parses_correctly(self):
        # Cancelled events are filtered out upstream; parsing still returns the enum.
        assert _parse_google_event_status("cancelled") == EventStatus.cancelled

    def test_parse_google_event_status_is_case_insensitive(self):
        assert _parse_google_event_status("CONFIRMED") == EventStatus.confirmed
        assert _parse_google_event_status("Tentative") == EventStatus.tentative

    def test_parse_google_event_status_unknown_returns_none(self):
        assert _parse_google_event_status("unknown_status") is None

    def test_parse_google_event_status_non_string_returns_none(self):
        assert _parse_google_event_status(None) is None
        assert _parse_google_event_status(123) is None
        assert _parse_google_event_status({}) is None

    def test_parse_google_event_visibility_default(self):
        assert _parse_google_event_visibility("default") == EventVisibility.default

    def test_parse_google_event_visibility_public(self):
        assert _parse_google_event_visibility("public") == EventVisibility.public

    def test_parse_google_event_visibility_private(self):
        assert _parse_google_event_visibility("private") == EventVisibility.private

    def test_parse_google_event_visibility_confidential(self):
        assert _parse_google_event_visibility("confidential") == EventVisibility.confidential

    def test_parse_google_event_visibility_is_case_insensitive(self):
        assert _parse_google_event_visibility("PUBLIC") == EventVisibility.public

    def test_parse_google_event_visibility_unknown_returns_none(self):
        assert _parse_google_event_visibility("restricted") is None

    def test_parse_google_event_visibility_non_string_returns_none(self):
        assert _parse_google_event_visibility(None) is None
        assert _parse_google_event_visibility(42) is None

    def test_extract_google_organizer_from_dict_with_email(self):
        result = _extract_google_organizer({"email": "organizer@example.com"})
        assert result == "organizer@example.com"

    def test_extract_google_organizer_strips_whitespace(self):
        result = _extract_google_organizer({"email": "  organizer@example.com  "})
        assert result == "organizer@example.com"

    def test_extract_google_organizer_empty_email_returns_none(self):
        assert _extract_google_organizer({"email": ""}) is None
        assert _extract_google_organizer({"email": "   "}) is None

    def test_extract_google_organizer_missing_email_returns_none(self):
        assert _extract_google_organizer({"displayName": "Alice"}) is None

    def test_extract_google_organizer_non_dict_returns_none(self):
        assert _extract_google_organizer(None) is None
        assert _extract_google_organizer("organizer@example.com") is None
        assert _extract_google_organizer(123) is None

    def test_parse_google_rfc3339_optional_valid_datetime(self):
        result = _parse_google_rfc3339_optional("2026-01-15T09:00:00Z")
        assert result is not None
        assert result.year == 2026
        assert result.tzinfo is not None

    def test_parse_google_rfc3339_optional_none_returns_none(self):
        assert _parse_google_rfc3339_optional(None) is None

    def test_parse_google_rfc3339_optional_empty_string_returns_none(self):
        assert _parse_google_rfc3339_optional("") is None
        assert _parse_google_rfc3339_optional("   ") is None

    def test_parse_google_rfc3339_optional_invalid_returns_none(self):
        assert _parse_google_rfc3339_optional("not-a-date") is None

    def test_parse_google_rfc3339_optional_non_string_returns_none(self):
        assert _parse_google_rfc3339_optional(123) is None


# ============================================================================
# Google Event Payload Parsing with Extended Fields
# ============================================================================


class TestGoogleEventToCalendarEventExtendedFields:
    """Verify _google_event_to_calendar_event populates extended fields correctly."""

    def _minimal_payload(self) -> dict:
        """Return a minimal valid Google Calendar event payload."""
        return {
            "id": "evt-test",
            "summary": "Test Event",
            "start": {"dateTime": "2026-03-01T10:00:00Z"},
            "end": {"dateTime": "2026-03-01T11:00:00Z"},
        }

    def test_parses_status_confirmed(self):
        payload = {**self._minimal_payload(), "status": "confirmed"}
        event = _google_event_to_calendar_event(payload, fallback_timezone="UTC")
        assert event is not None
        assert event.status == EventStatus.confirmed

    def test_parses_status_tentative(self):
        payload = {**self._minimal_payload(), "status": "tentative"}
        event = _google_event_to_calendar_event(payload, fallback_timezone="UTC")
        assert event is not None
        assert event.status == EventStatus.tentative

    def test_cancelled_status_returns_none(self):
        """Events with status=cancelled are filtered out by the existing logic."""
        payload = {**self._minimal_payload(), "status": "cancelled"}
        result = _google_event_to_calendar_event(payload, fallback_timezone="UTC")
        assert result is None

    def test_missing_status_defaults_to_none(self):
        event = _google_event_to_calendar_event(self._minimal_payload(), fallback_timezone="UTC")
        assert event is not None
        assert event.status is None

    def test_parses_visibility_public(self):
        payload = {**self._minimal_payload(), "visibility": "public"}
        event = _google_event_to_calendar_event(payload, fallback_timezone="UTC")
        assert event is not None
        assert event.visibility == EventVisibility.public

    def test_parses_visibility_private(self):
        payload = {**self._minimal_payload(), "visibility": "private"}
        event = _google_event_to_calendar_event(payload, fallback_timezone="UTC")
        assert event is not None
        assert event.visibility == EventVisibility.private

    def test_parses_visibility_confidential(self):
        payload = {**self._minimal_payload(), "visibility": "confidential"}
        event = _google_event_to_calendar_event(payload, fallback_timezone="UTC")
        assert event is not None
        assert event.visibility == EventVisibility.confidential

    def test_parses_visibility_default(self):
        payload = {**self._minimal_payload(), "visibility": "default"}
        event = _google_event_to_calendar_event(payload, fallback_timezone="UTC")
        assert event is not None
        assert event.visibility == EventVisibility.default

    def test_missing_visibility_defaults_to_none(self):
        event = _google_event_to_calendar_event(self._minimal_payload(), fallback_timezone="UTC")
        assert event is not None
        assert event.visibility is None

    def test_parses_organizer_email(self):
        payload = {**self._minimal_payload(), "organizer": {"email": "owner@example.com"}}
        event = _google_event_to_calendar_event(payload, fallback_timezone="UTC")
        assert event is not None
        assert event.organizer == "owner@example.com"

    def test_missing_organizer_defaults_to_none(self):
        event = _google_event_to_calendar_event(self._minimal_payload(), fallback_timezone="UTC")
        assert event is not None
        assert event.organizer is None

    def test_parses_etag(self):
        payload = {**self._minimal_payload(), "etag": '"v1234567890"'}
        event = _google_event_to_calendar_event(payload, fallback_timezone="UTC")
        assert event is not None
        assert event.etag == '"v1234567890"'

    def test_missing_etag_defaults_to_none(self):
        event = _google_event_to_calendar_event(self._minimal_payload(), fallback_timezone="UTC")
        assert event is not None
        assert event.etag is None

    def test_parses_created_at(self):
        payload = {**self._minimal_payload(), "created": "2026-01-01T09:00:00Z"}
        event = _google_event_to_calendar_event(payload, fallback_timezone="UTC")
        assert event is not None
        assert event.created_at is not None
        assert event.created_at.year == 2026
        assert event.created_at.month == 1

    def test_parses_updated_at(self):
        payload = {**self._minimal_payload(), "updated": "2026-02-15T10:30:00Z"}
        event = _google_event_to_calendar_event(payload, fallback_timezone="UTC")
        assert event is not None
        assert event.updated_at is not None
        assert event.updated_at.year == 2026
        assert event.updated_at.month == 2
        assert event.updated_at.day == 15

    def test_missing_created_at_defaults_to_none(self):
        event = _google_event_to_calendar_event(self._minimal_payload(), fallback_timezone="UTC")
        assert event is not None
        assert event.created_at is None

    def test_missing_updated_at_defaults_to_none(self):
        event = _google_event_to_calendar_event(self._minimal_payload(), fallback_timezone="UTC")
        assert event is not None
        assert event.updated_at is None

    def test_all_extended_fields_populated_together(self):
        """All extended fields parsed together from a complete payload."""
        payload = {
            "id": "evt-full",
            "summary": "Full Event",
            "status": "tentative",
            "visibility": "private",
            "organizer": {"email": "boss@example.com", "displayName": "Boss"},
            "etag": '"xyz123"',
            "created": "2026-01-10T08:00:00Z",
            "updated": "2026-02-20T14:30:00Z",
            "start": {"dateTime": "2026-03-01T10:00:00Z"},
            "end": {"dateTime": "2026-03-01T11:00:00Z"},
        }
        event = _google_event_to_calendar_event(payload, fallback_timezone="UTC")
        assert event is not None
        assert event.status == EventStatus.tentative
        assert event.visibility == EventVisibility.private
        assert event.organizer == "boss@example.com"
        assert event.etag == '"xyz123"'
        assert event.created_at is not None
        assert event.created_at.day == 10
        assert event.updated_at is not None
        assert event.updated_at.day == 20


# ============================================================================
# _event_to_payload serialization with extended fields
# ============================================================================


class TestEventToPayloadExtendedFields:
    """Verify _event_to_payload serializes extended fields correctly."""

    def test_payload_includes_extended_fields_as_none_when_unset(self):
        event = CalendarEvent(
            event_id="evt-1",
            title="Basic",
            start_at=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
            end_at=datetime(2026, 3, 1, 11, 0, tzinfo=UTC),
            timezone="UTC",
        )
        payload = CalendarModule._event_to_payload(event)
        assert payload["status"] is None
        assert payload["organizer"] is None
        assert payload["visibility"] is None
        assert payload["etag"] is None
        assert payload["created_at"] is None
        assert payload["updated_at"] is None

    def test_payload_serializes_status_as_string(self):
        event = CalendarEvent(
            event_id="evt-2",
            title="Confirmed",
            start_at=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
            end_at=datetime(2026, 3, 1, 11, 0, tzinfo=UTC),
            timezone="UTC",
            status=EventStatus.tentative,
        )
        payload = CalendarModule._event_to_payload(event)
        assert payload["status"] == "tentative"

    def test_payload_serializes_visibility_as_string(self):
        event = CalendarEvent(
            event_id="evt-3",
            title="Private",
            start_at=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
            end_at=datetime(2026, 3, 1, 11, 0, tzinfo=UTC),
            timezone="UTC",
            visibility=EventVisibility.confidential,
        )
        payload = CalendarModule._event_to_payload(event)
        assert payload["visibility"] == "confidential"

    def test_payload_serializes_created_at_as_iso_string(self):
        created = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)
        event = CalendarEvent(
            event_id="evt-4",
            title="With Timestamps",
            start_at=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
            end_at=datetime(2026, 3, 1, 11, 0, tzinfo=UTC),
            timezone="UTC",
            created_at=created,
        )
        payload = CalendarModule._event_to_payload(event)
        assert payload["created_at"] == created.isoformat()

    def test_payload_serializes_updated_at_as_iso_string(self):
        updated = datetime(2026, 2, 20, 14, 30, tzinfo=UTC)
        event = CalendarEvent(
            event_id="evt-5",
            title="Updated",
            start_at=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
            end_at=datetime(2026, 3, 1, 11, 0, tzinfo=UTC),
            timezone="UTC",
            updated_at=updated,
        )
        payload = CalendarModule._event_to_payload(event)
        assert payload["updated_at"] == updated.isoformat()

    def test_payload_serializes_all_extended_fields(self):
        """All extended fields appear in the payload with correct values."""
        created = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
        updated = datetime(2026, 2, 1, 12, 0, tzinfo=UTC)
        event = CalendarEvent(
            event_id="evt-full",
            title="Full",
            start_at=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
            end_at=datetime(2026, 3, 1, 11, 0, tzinfo=UTC),
            timezone="UTC",
            status=EventStatus.confirmed,
            organizer="alice@example.com",
            visibility=EventVisibility.public,
            etag='"etag-value"',
            created_at=created,
            updated_at=updated,
        )
        payload = CalendarModule._event_to_payload(event)
        assert payload["status"] == "confirmed"
        assert payload["organizer"] == "alice@example.com"
        assert payload["visibility"] == "public"
        assert payload["etag"] == '"etag-value"'
        assert payload["created_at"] == created.isoformat()
        assert payload["updated_at"] == updated.isoformat()


# ============================================================================
# Google Provider Read Operations with Extended Fields
# ============================================================================


class TestGoogleReadOperationsExtendedFields:
    """Verify Google provider read operations parse and populate extended fields."""

    async def test_list_events_includes_extended_fields_from_google_payload(self):
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
                        "id": "evt-ext",
                        "summary": "Extended Fields Event",
                        "status": "tentative",
                        "visibility": "private",
                        "organizer": {"email": "owner@example.com"},
                        "etag": '"abc123"',
                        "created": "2026-01-15T08:00:00Z",
                        "updated": "2026-02-20T10:00:00Z",
                        "start": {"dateTime": "2026-03-01T10:00:00Z"},
                        "end": {"dateTime": "2026-03-01T11:00:00Z"},
                    }
                ]
            },
        )

        provider = _GoogleProvider(
            config=CalendarConfig(provider="google", calendar_id="primary", timezone="UTC"),
            credentials=_make_test_credentials(),
            http_client=mock_client,
        )

        events = await provider.list_events(calendar_id="primary", limit=10)

        assert len(events) == 1
        event = events[0]
        assert event.status == EventStatus.tentative
        assert event.visibility == EventVisibility.private
        assert event.organizer == "owner@example.com"
        assert event.etag == '"abc123"'
        assert event.created_at is not None
        assert event.created_at.year == 2026
        assert event.created_at.month == 1
        assert event.updated_at is not None
        assert event.updated_at.month == 2

    async def test_get_event_includes_extended_fields_from_google_payload(self):
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = _mock_response(
            status_code=200,
            url=GOOGLE_OAUTH_TOKEN_URL,
            method="POST",
            json_body={"access_token": "access-token", "expires_in": 3600},
        )
        mock_client.request.return_value = _mock_response(
            status_code=200,
            url=f"{GOOGLE_CALENDAR_API_BASE_URL}/calendars/primary/events/evt-ext",
            method="GET",
            json_body={
                "id": "evt-ext",
                "summary": "Single Event",
                "status": "confirmed",
                "visibility": "public",
                "organizer": {"email": "organizer@example.com"},
                "etag": '"etag-single"',
                "created": "2026-01-20T09:00:00Z",
                "updated": "2026-02-25T11:00:00Z",
                "start": {"dateTime": "2026-03-05T14:00:00Z"},
                "end": {"dateTime": "2026-03-05T15:00:00Z"},
            },
        )

        provider = _GoogleProvider(
            config=CalendarConfig(provider="google", calendar_id="primary", timezone="UTC"),
            credentials=_make_test_credentials(),
            http_client=mock_client,
        )

        event = await provider.get_event(calendar_id="primary", event_id="evt-ext")

        assert event is not None
        assert event.status == EventStatus.confirmed
        assert event.visibility == EventVisibility.public
        assert event.organizer == "organizer@example.com"
        assert event.etag == '"etag-single"'
        assert event.created_at is not None
        assert event.created_at.day == 20
        assert event.updated_at is not None
        assert event.updated_at.day == 25
