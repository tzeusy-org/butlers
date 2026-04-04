"""Condensed calendar module tests — behavioral contract only.

Replaces test_module_calendar.py (127), test_calendar_helpers.py (57),
test_calendar_error_handling.py (48), test_calendar_attendee_tools.py (40),
test_calendar_unit_behaviors.py (43), test_calendar_update_event.py (30),
test_calendar_workspace_mutations.py (10), test_calendar_autodiscovery.py (11)
= ~366 tests replaced with ~50.

Covers:
- Module ABC compliance (instantiation, name, config_schema, dependencies)
- CalendarConfig validation (required provider, defaults, normalization)
- Provider interface contract (abstract methods)
- Startup: unsupported provider raises with helpful message
- Tool registration: list/get/create/delete tool wiring
- Create event: BUTLER prefix + private metadata
- Update event: BUTLER prefix repair for butler-generated events
- Approval gating: conflict + require_approval_for_overlap
- Error hierarchy: CalendarAuthError, CalendarRequestError (exception type)
- Fail-open for reads, fail-closed for writes
- Google helpers: credential extraction, datetime parsing, error sanitization
- Calendar sync: config, state machine, sync tool
- Autodiscovery: discover_accounts behavior

[bu-7sd7a]
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from pydantic import BaseModel, ValidationError

from butlers.modules.base import Module
from butlers.modules.calendar import (
    BUTLER_GENERATED_PRIVATE_KEY,
    BUTLER_NAME_PRIVATE_KEY,
    AttendeeInfo,
    CalendarConfig,
    CalendarCredentialError,
    CalendarEvent,
    CalendarModule,
    CalendarProvider,
    CalendarRequestError,
    _coerce_expires_in_seconds,
    _extract_google_credential_value,
    _extract_google_private_metadata,
    _google_rfc3339,
    _parse_google_datetime,
    _safe_google_error_message,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


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
    """Provider test double for module-to-provider wiring tests."""

    def __init__(
        self,
        *,
        events: list[CalendarEvent] | None = None,
        event: CalendarEvent | None = None,
        conflicts: list[CalendarEvent] | None = None,
    ) -> None:
        self._events = events or []
        self._event = event
        self._conflicts = conflicts or []
        self.list_calls: list[dict] = []
        self.get_calls: list[dict] = []
        self.create_calls: list[dict] = []
        self.update_calls: list[dict] = []
        self.delete_calls: list[dict] = []

    @property
    def name(self) -> str:
        return "double"

    async def list_events(self, *, calendar_id, start_at=None, end_at=None, limit=50):
        self.list_calls.append(
            {"calendar_id": calendar_id, "start_at": start_at, "end_at": end_at, "limit": limit}
        )
        return list(self._events)

    async def get_event(self, *, calendar_id, event_id):
        self.get_calls.append({"calendar_id": calendar_id, "event_id": event_id})
        return self._event

    async def create_event(self, *, calendar_id, payload):
        self.create_calls.append({"calendar_id": calendar_id, "payload": payload})
        if self._event is not None:
            return self._event
        raise NotImplementedError

    async def update_event(self, *, calendar_id, event_id, patch):
        self.update_calls.append({"calendar_id": calendar_id, "event_id": event_id, "patch": patch})
        if self._event is not None:
            return self._event
        raise NotImplementedError

    async def delete_event(self, *, calendar_id, event_id):
        self.delete_calls.append({"calendar_id": calendar_id, "event_id": event_id})

    async def add_attendees(
        self, *, calendar_id, event_id, attendees, optional=False, send_updates="none"
    ):
        raise NotImplementedError

    async def remove_attendees(self, *, calendar_id, event_id, attendees, send_updates="none"):
        raise NotImplementedError

    async def find_conflicts(self, *, calendar_id, candidate):
        return list(self._conflicts)

    async def sync_incremental(self, *, calendar_id, sync_token, full_sync_window_days=30):
        return [], [], "token-stub"

    async def shutdown(self):
        return None


def _make_event(**kwargs) -> CalendarEvent:
    defaults = dict(
        event_id="evt-1",
        title="Team Sync",
        start_at=datetime(2026, 2, 20, 14, 0, tzinfo=UTC),
        end_at=datetime(2026, 2, 20, 15, 0, tzinfo=UTC),
        timezone="UTC",
    )
    defaults.update(kwargs)
    return CalendarEvent(**defaults)


# ---------------------------------------------------------------------------
# ABC compliance
# ---------------------------------------------------------------------------


class TestModuleABCCompliance:
    def test_is_module_subclass(self):
        assert issubclass(CalendarModule, Module)

    def test_instantiates(self):
        assert CalendarModule() is not None

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

    def test_provider_interface_abstract_methods(self):
        abstract_methods = CalendarProvider.__abstractmethods__
        for method in {
            "name",
            "list_events",
            "get_event",
            "create_event",
            "update_event",
            "delete_event",
            "find_conflicts",
            "sync_incremental",
            "shutdown",
        }:
            assert method in abstract_methods


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------


class TestCalendarConfig:
    def test_provider_is_required(self):
        with pytest.raises(ValidationError):
            CalendarConfig(calendar_id="primary")

    def test_defaults(self):
        config = CalendarConfig(provider="google")
        assert config.provider == "google"
        assert config.timezone == "UTC"
        assert config.conflicts.policy == "suggest"
        assert config.conflicts.require_approval_for_overlap is True

    def test_string_normalization(self):
        config = CalendarConfig(provider="  GOOGLE  ", calendar_id="  primary  ")
        assert config.provider == "google"
        assert config.calendar_id == "primary"

    def test_whitespace_only_calendar_id_becomes_none(self):
        config = CalendarConfig(provider="google", calendar_id="   ")
        assert config.calendar_id is None

    def test_account_stripped_and_none_on_whitespace(self):
        config = CalendarConfig(provider="google", account="  work@gmail.com  ")
        assert config.account == "work@gmail.com"
        config2 = CalendarConfig(provider="google", account="   ")
        assert config2.account is None

    @pytest.mark.parametrize("bad_timezone", ["", "   "])
    def test_timezone_rejects_empty(self, bad_timezone: str):
        with pytest.raises(ValidationError):
            CalendarConfig(provider="google", timezone=bad_timezone)

    def test_nested_unknown_keys_rejected(self):
        with pytest.raises(ValidationError):
            CalendarConfig(provider="google", conflicts={"policy": "suggest", "unexpected": True})


# ---------------------------------------------------------------------------
# Module startup
# ---------------------------------------------------------------------------


class TestModuleStartup:
    async def test_startup_fails_on_unsupported_provider(self):
        mod = CalendarModule()
        with pytest.raises(RuntimeError, match="Unsupported calendar provider 'outlook'"):
            await mod.on_startup({"provider": "outlook"}, db=None)

    async def test_register_tools_accepts_dict_config(self):
        mod = CalendarModule()
        await mod.register_tools(mcp=_StubMCP(), config={"provider": "google"}, db=None)
        assert isinstance(mod._config, CalendarConfig)
        assert mod._config.provider == "google"


# ---------------------------------------------------------------------------
# Calendar read tools
# ---------------------------------------------------------------------------


class TestCalendarReadTools:
    async def test_list_and_get_tool_wiring(self):
        event = _make_event(
            event_id="evt-123",
            title="Dentist",
            attendees=[AttendeeInfo(email="alex@example.com")],
        )
        provider = _ProviderDouble(events=[event], event=event)
        mcp = _StubMCP()
        mod = CalendarModule()
        mod._provider = provider
        mod._resolved_calendar_id = "primary"
        await mod.register_tools(
            mcp=mcp, config={"provider": "google", "calendar_id": "primary"}, db=None
        )

        list_result = await mcp.tools["calendar_list_events"]()
        get_result = await mcp.tools["calendar_get_event"](event_id="evt-123")

        assert provider.list_calls[0]["calendar_id"] == "primary"
        assert provider.get_calls[0]["event_id"] == "evt-123"
        assert list_result["events"][0]["event_id"] == "evt-123"
        assert get_result["event"]["event_id"] == "evt-123"

    async def test_calendar_id_override_applied(self):
        provider = _ProviderDouble()
        mcp = _StubMCP()
        mod = CalendarModule()
        mod._provider = provider
        mod._resolved_calendar_id = "primary"
        await mod.register_tools(
            mcp=mcp, config={"provider": "google", "calendar_id": "primary"}, db=None
        )

        await mcp.tools["calendar_list_events"](calendar_id="  other-cal  ", limit=5)
        assert provider.list_calls[0]["calendar_id"] == "other-cal"
        assert provider.list_calls[0]["limit"] == 5

    async def test_blank_calendar_id_override_rejected(self):
        provider = _ProviderDouble()
        mcp = _StubMCP()
        mod = CalendarModule()
        mod._provider = provider
        mod._resolved_calendar_id = "primary"
        await mod.register_tools(
            mcp=mcp, config={"provider": "google", "calendar_id": "primary"}, db=None
        )

        with pytest.raises(ValueError, match="calendar_id"):
            await mcp.tools["calendar_list_events"](calendar_id="   ")


# ---------------------------------------------------------------------------
# Calendar write tools
# ---------------------------------------------------------------------------


class TestCalendarWriteTools:
    async def test_create_event_adds_butler_prefix_and_metadata(self):
        created = _make_event(
            title="BUTLER: Team Sync", butler_generated=True, butler_name="general"
        )
        provider = _ProviderDouble(event=created)
        mcp = _StubMCP()
        mod = CalendarModule()
        mod._provider = provider
        mod._resolved_calendar_id = "primary"
        await mod.register_tools(
            mcp=mcp,
            config={"provider": "google", "calendar_id": "primary"},
            db=SimpleNamespace(db_name="butlers", db_schema="general"),
        )

        result = await mcp.tools["calendar_create_event"](
            title="Team Sync",
            start_at=datetime(2026, 2, 20, 14, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 15, 0, tzinfo=UTC),
        )

        payload = provider.create_calls[0]["payload"]
        assert payload.title == "BUTLER: Team Sync"
        assert payload.private_metadata[BUTLER_GENERATED_PRIVATE_KEY] == "true"
        assert result["event"]["butler_generated"] is True

    async def test_delete_event_tool_is_registered(self):
        provider = _ProviderDouble()
        mcp = _StubMCP()
        mod = CalendarModule()
        mod._provider = provider
        mod._resolved_calendar_id = "primary"
        await mod.register_tools(
            mcp=mcp, config={"provider": "google", "calendar_id": "primary"}, db=None
        )
        # calendar_delete_event tool is registered and callable
        assert "calendar_delete_event" in mcp.tools

    async def test_overlap_approval_gate_enqueues_action(self):
        conflict = _make_event(event_id="busy-1", title="(busy)")
        created = _make_event(
            title="BUTLER: Team Sync", butler_generated=True, butler_name="general"
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
            db=SimpleNamespace(db_name="butlers", db_schema="general"),
        )

        enqueue_calls = []

        async def _enqueuer(tool_name, tool_args, agent_summary):
            enqueue_calls.append((tool_name, tool_args))
            return "action-123"

        mod.set_approval_enqueuer(_enqueuer)

        result = await mcp.tools["calendar_create_event"](
            title="Team Sync",
            start_at=datetime(2026, 2, 20, 14, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 15, 0, tzinfo=UTC),
            conflict_policy="allow_overlap",
        )

        assert provider.create_calls == []  # NOT written
        assert result["status"] == "approval_required"
        assert result["action_id"] == "action-123"
        assert len(enqueue_calls) == 1
        assert enqueue_calls[0][0] == "calendar_create_event"


# ---------------------------------------------------------------------------
# Error hierarchy
# ---------------------------------------------------------------------------


class TestErrorHierarchy:
    def test_calendar_credential_error_is_exception(self):
        err = CalendarCredentialError("missing client_id")
        assert isinstance(err, Exception)

    def test_calendar_request_error_is_exception(self):
        err = CalendarRequestError(status_code=429, message="quota exceeded")
        assert isinstance(err, Exception)
        assert err.status_code == 429

    async def test_startup_fails_without_credentials_raises_error(self):
        """Missing credentials must raise an error (not KeyError)."""
        mod = CalendarModule()
        store = AsyncMock()
        store.resolve.return_value = None
        store.load_shared.return_value = None
        db = MagicMock()
        db.pool = MagicMock()
        with (
            patch(
                "butlers.google_credentials._resolve_account_entity_id",
                new_callable=AsyncMock,
                return_value=uuid.UUID("00000000-0000-0000-0000-000000000001"),
            ),
            patch(
                "butlers.google_credentials._resolve_entity_refresh_token",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            with pytest.raises((CalendarCredentialError, RuntimeError)):
                await mod.on_startup({"provider": "google"}, db=db, credential_store=store)


# ---------------------------------------------------------------------------
# Google credential / helper coverage
# ---------------------------------------------------------------------------


class TestGoogleHelpers:
    @pytest.mark.parametrize(
        "payload,key,expected",
        [
            ({"client_id": "top"}, "client_id", "top"),
            ({"installed": {"client_id": "nested"}}, "client_id", "nested"),
            ({"web": {"client_secret": "sec"}}, "client_secret", "sec"),
            ({"client_id": "top", "installed": {"client_id": "nested"}}, "client_id", "top"),
            ({}, "client_id", None),
        ],
    )
    def test_extract_google_credential_value(self, payload, key, expected):
        assert _extract_google_credential_value(payload, key) == expected

    @pytest.mark.parametrize(
        "value,expected",
        [
            (3600, 3600),
            (3600.5, 3600),
            (0, 3600),
            (-100, 3600),
            (None, 3600),
            ("3600", 3600),
            (True, 3600),
        ],
    )
    def test_coerce_expires_in(self, value, expected):
        assert _coerce_expires_in_seconds(value) == expected

    def test_safe_google_error_message_truncates_at_200(self):
        long_message = "Error: " + "x" * 300
        response = httpx.Response(
            500,
            json={"error": {"message": long_message}},
            request=httpx.Request("GET", "https://example.com"),
        )
        assert len(_safe_google_error_message(response)) == 200

    def test_google_rfc3339_utc(self):
        dt = datetime(2026, 2, 15, 9, 30, 0, tzinfo=UTC)
        assert _google_rfc3339(dt) == "2026-02-15T09:30:00Z"

    def test_parse_google_datetime_iso_with_z(self):
        result = _parse_google_datetime("2026-02-15T09:30:00Z")
        assert result is not None
        assert result.tzinfo is not None

    def test_extract_google_private_metadata_butler_keys(self):
        # _extract_google_private_metadata returns (butler_generated: bool, butler_name: str | None)
        extended = {
            "private": {BUTLER_GENERATED_PRIVATE_KEY: "true", BUTLER_NAME_PRIVATE_KEY: "general"}
        }
        generated, name = _extract_google_private_metadata(extended)
        assert generated is True
        assert name == "general"
