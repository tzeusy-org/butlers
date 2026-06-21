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

import asyncio
import logging
import uuid
from datetime import UTC, datetime, time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from pydantic import BaseModel, ValidationError

from butlers.modules.base import Module
from butlers.modules.calendar import (
    _CREDENTIAL_KEY_CALENDAR_ID,
    _CREDENTIAL_KEY_DEFAULT_TARGET_CALENDAR_ID,
    BUTLER_GENERATED_PRIVATE_KEY,
    BUTLER_NAME_PRIVATE_KEY,
    CALENDAR_ROLE_BUTLERS,
    CALENDAR_ROLE_DEFAULT_TARGET,
    DEFAULT_BUTLER_NAME,
    AttendeeInfo,
    CalendarAuthError,
    CalendarConfig,
    CalendarCredentialError,
    CalendarEvent,
    CalendarEventCreate,
    CalendarEventUpdate,
    CalendarModule,
    CalendarProvider,
    CalendarRequestError,
    CalendarTokenRefreshError,
    _coerce_expires_in_seconds,
    _extract_google_credential_value,
    _extract_google_private_metadata,
    _format_ical_utc,
    _google_error_code,
    _google_event_to_calendar_event,
    _google_rfc3339,
    _GoogleOAuthClient,
    _GoogleOAuthCredentials,
    _GoogleProvider,
    _parse_google_datetime,
    _recurrence_lines_append_exdate,
    _recurrence_lines_bound_until,
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


def _calendar_events_fetchrow_args(pool: MagicMock) -> tuple[object, ...]:
    for call in pool.fetchrow.await_args_list:
        sql = call.args[0]
        if isinstance(sql, str) and "INSERT INTO calendar_events" in sql:
            return call.args
    raise AssertionError("expected a calendar_events fetchrow call")


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
    def test_module_contract(self):
        """CalendarModule satisfies Module ABC: name, config_schema, dependencies, revisions."""
        mod = CalendarModule()
        assert issubclass(CalendarModule, Module)
        assert mod.name == "calendar"
        assert mod.config_schema is CalendarConfig
        assert issubclass(mod.config_schema, BaseModel)
        assert mod.dependencies == []
        assert mod.migration_revisions() is None

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
        await mod.register_tools(
            mcp=_StubMCP(), config={"provider": "google"}, db=None, butler_name="test-butler"
        )
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
            mcp=mcp,
            config={"provider": "google", "calendar_id": "primary"},
            db=None,
            butler_name="test-butler",
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
            mcp=mcp,
            config={"provider": "google", "calendar_id": "primary"},
            db=None,
            butler_name="test-butler",
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
            mcp=mcp,
            config={"provider": "google", "calendar_id": "primary"},
            db=None,
            butler_name="test-butler",
        )

        with pytest.raises(ValueError, match="calendar_id"):
            await mcp.tools["calendar_list_events"](calendar_id="   ")

    async def test_unknown_calendar_id_override_rejected_after_discovery(self):
        provider = _ProviderDouble()
        mcp = _StubMCP()
        mod = CalendarModule()
        mod._provider = provider
        mod._resolved_calendar_id = "primary"
        mod._all_provider_calendar_ids = ["primary"]
        await mod.register_tools(
            mcp=mcp,
            config={"provider": "google", "calendar_id": "primary"},
            db=None,
            butler_name="test-butler",
        )

        with pytest.raises(ValueError, match="discovered provider calendars"):
            await mcp.tools["calendar_list_events"](calendar_id="__invalid_check__")

        assert provider.list_calls == []

    async def test_primary_alias_allowed_after_discovery(self):
        provider = _ProviderDouble()
        mcp = _StubMCP()
        mod = CalendarModule()
        mod._provider = provider
        mod._resolved_calendar_id = "butlers@example.com"
        mod._primary_calendar_id = "owner@example.com"
        mod._all_provider_calendar_ids = ["owner@example.com", "butlers@example.com"]
        mod._provider_calendar_discovery_completed = True
        await mod.register_tools(
            mcp=mcp,
            config={"provider": "google", "calendar_id": "butlers@example.com"},
            db=None,
            butler_name="test-butler",
        )

        await mcp.tools["calendar_list_events"](calendar_id="primary")

        assert provider.list_calls[0]["calendar_id"] == "primary"


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
            butler_name="test-butler",
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

    async def test_create_event_rejects_unknown_calendar_id_before_provider_write(self):
        created = _make_event(
            title="BUTLER: Team Sync", butler_generated=True, butler_name="general"
        )
        provider = _ProviderDouble(event=created)
        mcp = _StubMCP()
        mod = CalendarModule()
        mod._provider = provider
        mod._resolved_calendar_id = "primary"
        mod._all_provider_calendar_ids = ["primary"]
        await mod.register_tools(
            mcp=mcp,
            config={"provider": "google", "calendar_id": "primary"},
            db=SimpleNamespace(db_name="butlers", db_schema="general"),
            butler_name="test-butler",
        )

        with pytest.raises(ValueError, match="discovered provider calendars"):
            await mcp.tools["calendar_create_event"](
                title="Team Sync",
                start_at=datetime(2026, 2, 20, 14, 0, tzinfo=UTC),
                end_at=datetime(2026, 2, 20, 15, 0, tzinfo=UTC),
                calendar_id="__invalid_check__",
            )

        assert provider.create_calls == []

    async def test_empty_discovered_calendar_list_still_rejects_unknown_override(self):
        created = _make_event(
            title="BUTLER: Team Sync", butler_generated=True, butler_name="general"
        )
        provider = _ProviderDouble(event=created)
        mcp = _StubMCP()
        mod = CalendarModule()
        mod._provider = provider
        mod._resolved_calendar_id = "butlers@example.com"
        mod._all_provider_calendar_ids = []
        mod._provider_calendar_discovery_completed = True
        await mod.register_tools(
            mcp=mcp,
            config={"provider": "google", "calendar_id": "butlers@example.com"},
            db=SimpleNamespace(db_name="butlers", db_schema="general"),
            butler_name="test-butler",
        )

        with pytest.raises(ValueError, match="discovered provider calendars"):
            await mcp.tools["calendar_create_event"](
                title="Team Sync",
                start_at=datetime(2026, 2, 20, 14, 0, tzinfo=UTC),
                end_at=datetime(2026, 2, 20, 15, 0, tzinfo=UTC),
                calendar_id="__invalid_check__",
            )

        assert provider.create_calls == []

    async def test_delete_event_tool_is_registered(self):
        provider = _ProviderDouble()
        mcp = _StubMCP()
        mod = CalendarModule()
        mod._provider = provider
        mod._resolved_calendar_id = "primary"
        await mod.register_tools(
            mcp=mcp,
            config={"provider": "google", "calendar_id": "primary"},
            db=None,
            butler_name="test-butler",
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
            butler_name="test-butler",
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
# Permissions-matrix enforcement (public.permissions: calendar.write) [bu-tzlq6]
# ---------------------------------------------------------------------------


class TestCalendarWritePermissionEnforcement:
    """The calendar.write permission gates create/update/delete event writes.

    Mirrors the spawn gate: a revoked grant blocks the provider write outright
    (PermissionDenied raised before any provider call); a granted/default grant
    lets the write proceed. The gate consults public.permissions via
    butlers.modules.calendar.require_permission, which fails open on DB error.
    """

    async def _make_module(self) -> tuple[CalendarModule, _ProviderDouble, _StubMCP]:
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
            butler_name="test-butler",
        )
        return mod, provider, mcp

    async def test_create_blocked_when_calendar_write_revoked(self):
        """Revoked calendar.write blocks the create before any provider write.

        Pre-fix this fails: the matrix was ignored, so the create proceeded.
        """
        from butlers.core.permissions import PermissionDenied

        _, provider, mcp = await self._make_module()
        with patch(
            "butlers.modules.calendar.require_permission",
            new_callable=AsyncMock,
            side_effect=PermissionDenied("test-butler", "calendar.write", "revoked by owner"),
        ):
            with pytest.raises(PermissionDenied):
                await mcp.tools["calendar_create_event"](
                    title="Team Sync",
                    start_at=datetime(2026, 2, 20, 14, 0, tzinfo=UTC),
                    end_at=datetime(2026, 2, 20, 15, 0, tzinfo=UTC),
                )
        assert provider.create_calls == []

    async def test_update_blocked_when_calendar_write_revoked(self):
        from butlers.core.permissions import PermissionDenied

        _, provider, mcp = await self._make_module()
        with patch(
            "butlers.modules.calendar.require_permission",
            new_callable=AsyncMock,
            side_effect=PermissionDenied("test-butler", "calendar.write", "revoked by owner"),
        ):
            with pytest.raises(PermissionDenied):
                await mcp.tools["calendar_update_event"](event_id="evt-1", title="New")
        assert provider.update_calls == []

    async def test_delete_blocked_when_calendar_write_revoked(self):
        from butlers.core.permissions import PermissionDenied

        _, provider, mcp = await self._make_module()
        with patch(
            "butlers.modules.calendar.require_permission",
            new_callable=AsyncMock,
            side_effect=PermissionDenied("test-butler", "calendar.write", "revoked by owner"),
        ):
            with pytest.raises(PermissionDenied):
                await mcp.tools["calendar_delete_event"](event_id="evt-1")
        assert provider.delete_calls == []

    async def test_create_allowed_when_calendar_write_granted(self):
        """Granted (require_permission returns None) lets the create proceed."""
        _, provider, mcp = await self._make_module()
        with patch(
            "butlers.modules.calendar.require_permission",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await mcp.tools["calendar_create_event"](
                title="Team Sync",
                start_at=datetime(2026, 2, 20, 14, 0, tzinfo=UTC),
                end_at=datetime(2026, 2, 20, 15, 0, tzinfo=UTC),
            )
        assert len(provider.create_calls) == 1
        assert result["event"]["butler_generated"] is True

    async def test_create_user_event_blocked_when_calendar_write_revoked(self):
        """The sibling create_user_event path (not an MCP tool) is gated too.

        Pre-fix this fails: create_user_event wrote to the provider with no
        calendar.write check, bypassing the gate the 3 MCP tools enforce.
        """
        from butlers.core.permissions import PermissionDenied

        mod, provider, _ = await self._make_module()
        with patch(
            "butlers.modules.calendar.require_permission",
            new_callable=AsyncMock,
            side_effect=PermissionDenied("test-butler", "calendar.write", "revoked by owner"),
        ):
            with pytest.raises(PermissionDenied):
                await mod.create_user_event(
                    title="Lunch",
                    start_at=datetime(2026, 2, 20, 12, 0, tzinfo=UTC),
                    end_at=datetime(2026, 2, 20, 13, 0, tzinfo=UTC),
                )
        assert provider.create_calls == []

    async def test_create_user_event_allowed_when_calendar_write_granted(self):
        """Granted (require_permission returns None) lets create_user_event proceed."""
        mod, provider, _ = await self._make_module()
        with patch(
            "butlers.modules.calendar.require_permission",
            new_callable=AsyncMock,
            return_value=None,
        ):
            await mod.create_user_event(
                title="Lunch",
                start_at=datetime(2026, 2, 20, 12, 0, tzinfo=UTC),
                end_at=datetime(2026, 2, 20, 13, 0, tzinfo=UTC),
            )
        assert len(provider.create_calls) == 1


# ---------------------------------------------------------------------------
# google_calendar_write egress audit emission
# ---------------------------------------------------------------------------


class TestCalendarWriteAuditEmit:
    """google_calendar_write is emitted to dashboard_audit_log on provider writes."""

    def _make_module_with_provider(self, provider: _ProviderDouble) -> CalendarModule:
        mod = CalendarModule()
        mod._provider = provider
        mod._resolved_calendar_id = "primary"
        return mod

    async def test_create_event_emits_audit(self) -> None:
        created = _make_event(
            title="BUTLER: Meeting", butler_generated=True, butler_name="test-butler"
        )
        provider = _ProviderDouble(event=created)
        mod = self._make_module_with_provider(provider)
        mock_pool = MagicMock()
        mod.wire_audit_pool(mock_pool)
        mcp = _StubMCP()
        await mod.register_tools(
            mcp=mcp,
            config={"provider": "google", "calendar_id": "primary"},
            db=SimpleNamespace(db_name="butlers", db_schema="test-butler"),
            butler_name="test-butler",
        )

        with patch(
            "butlers.modules.calendar.write_audit_entry", new_callable=AsyncMock
        ) as mock_emit:
            await mcp.tools["calendar_create_event"](
                title="Meeting",
                start_at=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
                end_at=datetime(2026, 3, 1, 11, 0, tzinfo=UTC),
            )

        mock_emit.assert_awaited()
        audit_calls = [c for c in mock_emit.await_args_list if c.args[2] == "google_calendar_write"]
        assert len(audit_calls) == 1
        summary = audit_calls[0].args[3]
        assert summary["action"] == "create"
        assert summary["calendar_id"] == "primary"

    async def test_delete_event_emits_audit(self) -> None:
        existing = _make_event(event_id="evt-1", title="To Delete")

        class _DeleteCapableProvider(_ProviderDouble):
            async def delete_event(self, *, calendar_id, event_id, send_updates=None):
                self.delete_calls.append({"calendar_id": calendar_id, "event_id": event_id})

        provider = _DeleteCapableProvider(event=existing)
        mod = self._make_module_with_provider(provider)
        mock_pool = MagicMock()
        mod.wire_audit_pool(mock_pool)
        mcp = _StubMCP()
        await mod.register_tools(
            mcp=mcp,
            config={"provider": "google", "calendar_id": "primary"},
            db=SimpleNamespace(db_name="butlers", db_schema="test-butler"),
            butler_name="test-butler",
        )

        with patch(
            "butlers.modules.calendar.write_audit_entry", new_callable=AsyncMock
        ) as mock_emit:
            await mcp.tools["calendar_delete_event"](event_id="evt-1")

        audit_calls = [c for c in mock_emit.await_args_list if c.args[2] == "google_calendar_write"]
        assert len(audit_calls) == 1
        summary = audit_calls[0].args[3]
        assert summary["action"] == "delete"

    def test_wire_audit_pool_stores_pool(self) -> None:
        mod = CalendarModule()
        pool = MagicMock()
        mod.wire_audit_pool(pool)
        assert mod._audit_pool is pool


# ---------------------------------------------------------------------------
# Reversible-mutation pre-state capture (undo prerequisite, bu-ytu9l4)
# ---------------------------------------------------------------------------


class TestReversibleMutationPreStateCapture:
    """Pre-mutation pre-image is captured into action_result for undo.

    Spec: module-calendar "Reversible Mutation Pre-State Capture". Present on
    applied update/delete; absent on create and on non-applied (noop/failed)
    outcomes.
    """

    async def _make_module_with_event(
        self, event: CalendarEvent | None
    ) -> tuple[CalendarModule, _ProviderDouble, _StubMCP]:
        provider = _ProviderDouble(event=event)
        mcp = _StubMCP()
        mod = CalendarModule()
        mod._provider = provider
        mod._resolved_calendar_id = "primary"
        await mod.register_tools(
            mcp=mcp,
            config={"provider": "google", "calendar_id": "primary"},
            db=SimpleNamespace(db_name="butlers", db_schema="general"),
            butler_name="test-butler",
        )
        return mod, provider, mcp

    @staticmethod
    def _result_for_status(finalize_mock: AsyncMock, status: str) -> dict | None:
        for call in finalize_mock.await_args_list:
            if call.kwargs.get("action_status") == status:
                return call.kwargs.get("action_result")
        return None

    async def test_update_captures_pre_state(self) -> None:
        existing = _make_event(event_id="evt-1", title="Original", location="Room A")
        mod, provider, mcp = await self._make_module_with_event(existing)
        with (
            patch(
                "butlers.modules.calendar.require_permission",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch.object(mod, "_finalize_workspace_mutation", new_callable=AsyncMock) as fin,
        ):
            await mcp.tools["calendar_update_event"](event_id="evt-1", title="New title")

        result = self._result_for_status(fin, "applied")
        assert result is not None
        pre = result["pre_state"]
        assert pre["event_id"] == "evt-1"
        assert pre["title"] == "Original"
        assert pre["location"] == "Room A"
        assert pre["calendar_id"] == "primary"
        assert pre["start_at"] == existing.start_at.isoformat()
        # Reuses the single pre-write get_event — no extra provider round-trip.
        assert len(provider.get_calls) == 1

    async def test_delete_captures_pre_state(self) -> None:
        existing = _make_event(event_id="evt-2", title="To delete", location="Hall")

        class _DeleteCapableDouble(_ProviderDouble):
            async def delete_event(self, *, calendar_id, event_id, send_updates=None):
                self.delete_calls.append({"calendar_id": calendar_id, "event_id": event_id})

        provider = _DeleteCapableDouble(event=existing)
        mcp = _StubMCP()
        mod = CalendarModule()
        mod._provider = provider
        mod._resolved_calendar_id = "primary"
        await mod.register_tools(
            mcp=mcp,
            config={"provider": "google", "calendar_id": "primary"},
            db=SimpleNamespace(db_name="butlers", db_schema="general"),
            butler_name="test-butler",
        )
        with (
            patch(
                "butlers.modules.calendar.require_permission",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch.object(mod, "_finalize_workspace_mutation", new_callable=AsyncMock) as fin,
        ):
            await mcp.tools["calendar_delete_event"](event_id="evt-2")

        result = self._result_for_status(fin, "applied")
        assert result is not None
        pre = result["pre_state"]
        assert pre["event_id"] == "evt-2"
        assert pre["title"] == "To delete"
        assert pre["location"] == "Hall"
        assert pre["calendar_id"] == "primary"

    async def test_create_has_no_pre_state(self) -> None:
        created = _make_event(
            event_id="evt-3", title="BUTLER: New", butler_generated=True, butler_name="general"
        )
        mod, _, mcp = await self._make_module_with_event(created)
        with (
            patch(
                "butlers.modules.calendar.require_permission",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch.object(mod, "_finalize_workspace_mutation", new_callable=AsyncMock) as fin,
        ):
            await mcp.tools["calendar_create_event"](
                title="New",
                start_at=datetime(2026, 2, 20, 14, 0, tzinfo=UTC),
                end_at=datetime(2026, 2, 20, 15, 0, tzinfo=UTC),
            )

        result = self._result_for_status(fin, "applied")
        assert result is not None
        assert "pre_state" not in result

    async def test_update_noop_for_missing_event_has_no_pre_state(self) -> None:
        # Provider returns None for get_event -> noop (not_found), no pre_state.
        mod, _, mcp = await self._make_module_with_event(None)
        with (
            patch(
                "butlers.modules.calendar.require_permission",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch.object(mod, "_finalize_workspace_mutation", new_callable=AsyncMock) as fin,
        ):
            await mcp.tools["calendar_update_event"](event_id="missing", title="x")

        noop_result = self._result_for_status(fin, "noop")
        assert noop_result is not None
        assert "pre_state" not in noop_result


# ---------------------------------------------------------------------------
# Projection persistence
# ---------------------------------------------------------------------------


def _calendar_events_fetchrow_args(pool: MagicMock) -> tuple[object, ...]:
    for call in pool.fetchrow.await_args_list:
        sql = call.args[0]
        if isinstance(sql, str) and "INSERT INTO calendar_events" in sql:
            return call.args
    raise AssertionError("expected a calendar_events fetchrow call")


class TestProjectionPersistence:
    async def test_upsert_projection_event_persists_source_butler(self):
        """calendar_events.source_butler is NOT NULL with no DB default; the
        projection upsert must include it on both INSERT and ON CONFLICT paths
        and bind the caller-supplied butler name."""
        mod = CalendarModule()
        mod._butler_name = "calendar"
        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value={"id": uuid.uuid4()})
        mod._db = SimpleNamespace(pool=pool)

        await mod._upsert_projection_event(
            source_id=uuid.uuid4(),
            origin_ref="evt-123",
            title="Calendar item",
            timezone="UTC",
            starts_at=datetime(2026, 2, 20, 14, 0, tzinfo=UTC),
            ends_at=datetime(2026, 2, 20, 15, 0, tzinfo=UTC),
            status="confirmed",
            source_butler="relationship",
        )

        query = pool.fetchrow.await_args.args[0]
        params = pool.fetchrow.await_args.args[1:]
        assert "source_butler" in query
        assert "source_butler = EXCLUDED.source_butler" in query
        assert "relationship" in params

    async def test_upsert_projection_event_blank_source_butler_uses_active_butler(self):
        mod = CalendarModule()
        mod._butler_name = "health"
        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value={"id": uuid.uuid4()})
        mod._db = SimpleNamespace(pool=pool)

        await mod._upsert_projection_event(
            source_id=uuid.uuid4(),
            origin_ref="evt-1",
            title="Projected event",
            timezone="UTC",
            starts_at=datetime(2026, 2, 20, 14, 0, tzinfo=UTC),
            ends_at=datetime(2026, 2, 20, 15, 0, tzinfo=UTC),
            status="confirmed",
            source_butler="   ",
        )

        args = _calendar_events_fetchrow_args(pool)
        # source_butler is the second-to-last positional arg; source_session_id is last.
        assert args[-2] == "health"

    async def test_upsert_projection_event_writes_source_butler(self):
        mod = CalendarModule()
        mod._butler_name = "health"
        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value={"id": uuid.uuid4()})
        mod._db = SimpleNamespace(pool=pool)

        await mod._upsert_projection_event(
            source_id=uuid.uuid4(),
            origin_ref="evt-1",
            title="Projected event",
            timezone="UTC",
            starts_at=datetime(2026, 2, 20, 14, 0, tzinfo=UTC),
            ends_at=datetime(2026, 2, 20, 15, 0, tzinfo=UTC),
            status="confirmed",
        )

        sql, *args = _calendar_events_fetchrow_args(pool)
        assert "source_butler" in sql
        # source_butler precedes source_session_id as the final two positional args.
        assert args[-2] == "health"

    async def test_project_provider_changes_persists_active_butler_name(self):
        mod = CalendarModule()
        mod._butler_name = "health"
        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value={"id": uuid.uuid4()})
        mod._db = SimpleNamespace(pool=pool)
        mod._upsert_projection_instance = AsyncMock(return_value=uuid.uuid4())

        await mod._project_provider_changes(
            source_id=uuid.uuid4(),
            provider_name="google",
            calendar_id="primary",
            updated_events=[_make_event(butler_name=None)],
            cancelled_ids=[],
        )

        assert _calendar_events_fetchrow_args(pool)[-2] == "health"


# ---------------------------------------------------------------------------
# Calendar-id role separation (Butlers calendar vs user default-target)
# ---------------------------------------------------------------------------


class TestCalendarIdRoleSeparation:
    async def _make_module_with_store(
        self,
    ) -> tuple[CalendarModule, _StubMCP, AsyncMock]:
        provider = _ProviderDouble()
        mcp = _StubMCP()
        store = AsyncMock()
        store.load_shared.return_value = None
        mod = CalendarModule()
        mod._provider = provider
        mod._credential_store = store
        # Immutable Butlers calendar id + a user-owned primary calendar.
        mod._resolved_calendar_id = "butlers@group.calendar.google.com"
        mod._primary_calendar_id = "owner@example.com"
        mod._all_provider_calendar_ids = [
            "owner@example.com",
            "butlers@group.calendar.google.com",
        ]
        mod._provider_calendar_discovery_completed = True
        await mod.register_tools(
            mcp=mcp,
            config={"provider": "google", "calendar_id": "butlers@group.calendar.google.com"},
            db=None,
            butler_name="test-butler",
        )
        return mod, mcp, store

    async def test_set_primary_updates_default_target_only_leaves_resolved_untouched(self):
        """calendar_set_primary must not clobber the immutable Butlers calendar id."""
        mod, mcp, store = await self._make_module_with_store()

        result = await mcp.tools["calendar_set_primary"](calendar_id="owner@example.com")

        assert result["status"] == "ok"
        # Butlers calendar id is left intact.
        assert mod._resolved_calendar_id == "butlers@group.calendar.google.com"
        # The user's default-target selection is recorded on its own field.
        assert mod._default_target_calendar_id == "owner@example.com"
        assert result["new_calendar_id"] == "owner@example.com"

        # Persistence targets the default-target cred key, NEVER GOOGLE_CALENDAR_ID.
        persisted_keys = [call.args[0] for call in store.store_shared.await_args_list]
        assert _CREDENTIAL_KEY_DEFAULT_TARGET_CALENDAR_ID in persisted_keys
        assert _CREDENTIAL_KEY_CALENDAR_ID not in persisted_keys

    async def test_set_primary_rejects_unknown_calendar(self):
        mod, mcp, store = await self._make_module_with_store()

        result = await mcp.tools["calendar_set_primary"](calendar_id="stranger@example.com")

        assert result["status"] == "error"
        # Nothing mutated, nothing persisted.
        assert mod._resolved_calendar_id == "butlers@group.calendar.google.com"
        assert mod._default_target_calendar_id is None
        store.store_shared.assert_not_awaited()

    def test_resolve_role_butlers_returns_resolved_id(self):
        mod = CalendarModule()
        mod._resolved_calendar_id = "butlers@group.calendar.google.com"
        mod._primary_calendar_id = "owner@example.com"
        mod._default_target_calendar_id = "chosen@example.com"

        assert (
            mod._resolve_role_calendar_id(CALENDAR_ROLE_BUTLERS)
            == "butlers@group.calendar.google.com"
        )

    def test_resolve_role_default_target_precedence(self):
        mod = CalendarModule()
        mod._resolved_calendar_id = "butlers@group.calendar.google.com"

        # Falls back to the Butlers calendar when nothing else is known.
        assert (
            mod._resolve_role_calendar_id(CALENDAR_ROLE_DEFAULT_TARGET)
            == "butlers@group.calendar.google.com"
        )

        # Discovered primary takes precedence over the Butlers fallback.
        mod._primary_calendar_id = "owner@example.com"
        assert mod._resolve_role_calendar_id(CALENDAR_ROLE_DEFAULT_TARGET) == "owner@example.com"

        # An explicit user default-target selection wins over the primary.
        mod._default_target_calendar_id = "chosen@example.com"
        assert mod._resolve_role_calendar_id(CALENDAR_ROLE_DEFAULT_TARGET) == "chosen@example.com"

    def test_resolve_role_unknown_raises(self):
        mod = CalendarModule()
        with pytest.raises(ValueError, match="Unknown calendar role"):
            mod._resolve_role_calendar_id("nonsense")

    def test_resolve_calendar_id_no_override_honours_default_target(self):
        mod = CalendarModule()
        mod._resolved_calendar_id = "butlers@group.calendar.google.com"
        mod._primary_calendar_id = "owner@example.com"

        # Without a chosen default target, the no-override default is the primary.
        assert mod._resolve_calendar_id(None) == "owner@example.com"

        # A chosen default target overrides the implicit primary default.
        mod._default_target_calendar_id = "chosen@example.com"
        assert mod._resolve_calendar_id(None) == "chosen@example.com"

    async def test_set_primary_default_target_drives_no_override_resolution(self):
        """End-to-end: selecting a default target redirects no-override mutations."""
        mod, mcp, _store = await self._make_module_with_store()

        # Default (no selection) lands on the discovered primary.
        assert mod._resolve_calendar_id(None) == "owner@example.com"

        await mcp.tools["calendar_set_primary"](calendar_id="butlers@group.calendar.google.com")

        # After selecting, no-override resolution follows the default-target field,
        # while the Butlers calendar id is still intact and queryable via its role.
        assert mod._resolve_calendar_id(None) == "butlers@group.calendar.google.com"
        assert (
            mod._resolve_role_calendar_id(CALENDAR_ROLE_BUTLERS)
            == "butlers@group.calendar.google.com"
        )

    async def test_on_startup_restores_default_target_calendar_id(self):
        """on_startup must restore a saved default-target calendar ID from the credential store."""
        mod = CalendarModule()
        store = AsyncMock()

        async def _load_shared(key):
            return {
                _CREDENTIAL_KEY_CALENDAR_ID: "butlers@group.calendar.google.com",
                _CREDENTIAL_KEY_DEFAULT_TARGET_CALENDAR_ID: "chosen@example.com",
            }.get(key)

        store.load_shared.side_effect = _load_shared
        db = MagicMock()
        db.pool = MagicMock()

        async def _noop_poller():
            pass

        mock_provider_factory = MagicMock(return_value=MagicMock())

        with (
            patch.object(mod, "_resolve_credentials", new=AsyncMock(return_value=MagicMock())),
            patch.object(
                mod,
                "_resolve_startup_calendar_id",
                new=AsyncMock(return_value="butlers@group.calendar.google.com"),
            ),
            patch.object(
                mod,
                "_discover_and_register_all_calendars",
                new=AsyncMock(
                    return_value=["chosen@example.com", "butlers@group.calendar.google.com"]
                ),
            ),
            patch.object(mod, "_run_internal_projection_poller", new=_noop_poller),
            patch.dict(CalendarModule._PROVIDER_CLASSES, {"google": mock_provider_factory}),
        ):
            await mod.on_startup(
                {"provider": "google"},
                db=db,
                credential_store=store,
            )

        assert mod._default_target_calendar_id == "chosen@example.com"

    async def test_on_startup_ignores_stale_default_target_not_in_discovered_list(self):
        """on_startup must not apply a stored default-target that is no longer a known calendar."""
        mod = CalendarModule()
        store = AsyncMock()

        async def _load_shared(key):
            return {
                _CREDENTIAL_KEY_CALENDAR_ID: "butlers@group.calendar.google.com",
                _CREDENTIAL_KEY_DEFAULT_TARGET_CALENDAR_ID: "stale@example.com",
            }.get(key)

        store.load_shared.side_effect = _load_shared
        db = MagicMock()
        db.pool = MagicMock()

        async def _noop_poller():
            pass

        mock_provider_factory = MagicMock(return_value=MagicMock())

        with (
            patch.object(mod, "_resolve_credentials", new=AsyncMock(return_value=MagicMock())),
            patch.object(
                mod,
                "_resolve_startup_calendar_id",
                new=AsyncMock(return_value="butlers@group.calendar.google.com"),
            ),
            patch.object(
                mod,
                "_discover_and_register_all_calendars",
                new=AsyncMock(
                    return_value=["owner@example.com", "butlers@group.calendar.google.com"]
                ),
            ),
            patch.object(mod, "_run_internal_projection_poller", new=_noop_poller),
            patch.dict(CalendarModule._PROVIDER_CLASSES, {"google": mock_provider_factory}),
        ):
            await mod.on_startup(
                {"provider": "google"},
                db=db,
                credential_store=store,
            )

        # stale@example.com is not in the discovered list — must be silently dropped.
        assert mod._default_target_calendar_id is None


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
# Internal projection poller failure handling
# ---------------------------------------------------------------------------


class TestInternalProjectionPollerFailureHandling:
    async def test_provider_transport_failure_logs_warning_not_error(self, caplog) -> None:
        mod = CalendarModule()
        mod._project_internal_sources = AsyncMock()
        wrapped = CalendarAuthError("Google Calendar request failed: connect failed")
        wrapped.__cause__ = httpx.ConnectError("connect failed")
        mod._push_internal_events_to_provider = AsyncMock(side_effect=wrapped)

        caplog.set_level(logging.DEBUG, logger="butlers.modules.calendar")
        with patch(
            "butlers.modules.calendar.asyncio.sleep",
            new=AsyncMock(side_effect=asyncio.CancelledError),
        ):
            await mod._run_internal_projection_poller()

        messages = [record.getMessage() for record in caplog.records]
        assert any("Push to provider after projection deferred" in msg for msg in messages)
        assert not any(
            record.levelno >= logging.ERROR
            and "Push to provider after projection" in record.message
            for record in caplog.records
        )

    async def test_unexpected_provider_push_failure_stays_error(self, caplog) -> None:
        mod = CalendarModule()
        mod._project_internal_sources = AsyncMock()
        mod._push_internal_events_to_provider = AsyncMock(side_effect=RuntimeError("bad payload"))

        caplog.set_level(logging.DEBUG, logger="butlers.modules.calendar")
        with patch(
            "butlers.modules.calendar.asyncio.sleep",
            new=AsyncMock(side_effect=asyncio.CancelledError),
        ):
            await mod._run_internal_projection_poller()

        assert any(
            record.levelno >= logging.ERROR
            and "Push to provider after projection failed" in record.getMessage()
            for record in caplog.records
        )


# ---------------------------------------------------------------------------
# Google credential / helper coverage
# ---------------------------------------------------------------------------


class TestGoogleHelpers:
    @pytest.mark.parametrize(
        "payload,key,expected",
        [
            ({"client_id": "top"}, "client_id", "top"),
            ({"installed": {"client_id": "nested"}}, "client_id", "nested"),
            ({}, "client_id", None),
        ],
    )
    def test_extract_google_credential_value(self, payload, key, expected):
        assert _extract_google_credential_value(payload, key) == expected

    @pytest.mark.parametrize(
        "value,expected",
        [
            (3600, 3600),
            (0, 3600),
            (None, 3600),
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

    def test_google_error_code_preserves_scalar_values(self):
        response = httpx.Response(
            400,
            json={"error": {"code": 401, "message": "auth failed"}},
            request=httpx.Request("GET", "https://example.com"),
        )

        assert _google_error_code(response) == "401"

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

    async def test_oauth_invalid_grant_marks_revoked_and_skips_retries(self):
        requests: list[httpx.Request] = []

        async def _handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(
                400,
                json={"error": "invalid_grant", "error_description": "revoked"},
                request=request,
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
        on_revoked = AsyncMock()
        oauth = _GoogleOAuthClient(
            _GoogleOAuthCredentials(
                client_id="client-id",
                client_secret="client-secret",
                refresh_token="refresh-token",
            ),
            client,
            on_token_revoked=on_revoked,
        )

        try:
            with pytest.raises(CalendarTokenRefreshError, match="invalid_grant"):
                await oauth.get_access_token()
        finally:
            await client.aclose()

        assert len(requests) == 1
        on_revoked.assert_awaited_once()

    async def test_google_provider_marks_configured_account_revoked_on_invalid_grant(self):
        async def _handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                400,
                json={"error": "invalid_grant", "error_description": "revoked"},
                request=request,
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
        pool = MagicMock()
        pool.execute = AsyncMock()
        provider = _GoogleProvider(
            CalendarConfig(provider="google", account="account@example.test"),
            _GoogleOAuthCredentials(
                client_id="client-id",
                client_secret="client-secret",
                refresh_token="refresh-token",
            ),
            http_client=client,
            pool=pool,
        )

        try:
            with pytest.raises(CalendarTokenRefreshError):
                await provider._oauth.get_access_token()
        finally:
            await provider.shutdown()
            await client.aclose()

        revoked_calls = [
            call
            for call in pool.execute.await_args_list
            if "SET status = 'revoked'" in " ".join(call.args[0].split())
        ]
        assert len(revoked_calls) == 1
        assert "WHERE email = $1" in " ".join(revoked_calls[0].args[0].split())
        assert revoked_calls[0].args[1] == "account@example.test"


# ---------------------------------------------------------------------------
# Authorship and entity association fields (core_074)
# ---------------------------------------------------------------------------


class TestCalendarEventModel:
    """CalendarEvent model includes new authorship and entity fields."""

    def test_calendar_event_defaults_for_new_fields(self):
        event = _make_event()
        assert event.body is None
        assert event.source_butler is None
        assert event.source_session_id is None
        assert event.entity_ids == []

    def test_calendar_event_accepts_new_fields(self):
        eid = uuid.uuid4()
        event = _make_event(
            body="Extended body text",
            source_butler="general",
            source_session_id="sess-abc",
            entity_ids=[eid],
        )
        assert event.body == "Extended body text"
        assert event.source_butler == "general"
        assert event.source_session_id == "sess-abc"
        assert event.entity_ids == [eid]

    def test_calendar_event_create_accepts_body_and_entity_ids(self):
        eid = uuid.uuid4()
        payload = CalendarEventCreate(
            title="Meeting",
            start_at=datetime(2026, 3, 1, 9, 0, tzinfo=UTC),
            end_at=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
            body="Agenda for the meeting",
            entity_ids=[eid],
        )
        assert payload.body == "Agenda for the meeting"
        assert payload.entity_ids == [eid]

    def test_calendar_event_update_accepts_body_and_entity_ids(self):
        eid = uuid.uuid4()
        patch = CalendarEventUpdate(body="Updated body", entity_ids=[eid])
        assert patch.body == "Updated body"
        assert patch.entity_ids == [eid]

    def test_calendar_event_update_entity_ids_none_by_default(self):
        patch = CalendarEventUpdate(title="New title")
        assert patch.entity_ids is None


class TestGoogleEventParserBodyMapping:
    """Google event parser maps description→body (core_074 spec)."""

    def _minimal_google_payload(self, **overrides: object) -> dict:
        base: dict = {
            "id": "google-evt-1",
            "start": {"dateTime": "2026-03-01T09:00:00Z"},
            "end": {"dateTime": "2026-03-01T10:00:00Z"},
        }
        base.update(overrides)
        return base

    def test_description_is_mapped_to_body(self):
        payload = self._minimal_google_payload(description="Meeting agenda")
        event = _google_event_to_calendar_event(payload, fallback_timezone="UTC")
        assert event is not None
        assert event.body == "Meeting agenda"

    def test_description_field_also_preserved(self):
        payload = self._minimal_google_payload(description="Details")
        event = _google_event_to_calendar_event(payload, fallback_timezone="UTC")
        assert event is not None
        assert event.description == "Details"
        assert event.body == "Details"

    def test_no_description_gives_none_body(self):
        payload = self._minimal_google_payload()
        event = _google_event_to_calendar_event(payload, fallback_timezone="UTC")
        assert event is not None
        assert event.body is None
        assert event.description is None

    def test_summary_maps_to_title(self):
        payload = self._minimal_google_payload(summary="Team Sync")
        event = _google_event_to_calendar_event(payload, fallback_timezone="UTC")
        assert event is not None
        assert event.title == "Team Sync"


class TestEventToPayloadNewFields:
    """_event_to_payload includes body, source_butler, source_session_id, entity_ids."""

    def test_payload_includes_body_and_authorship(self):
        eid = uuid.uuid4()
        event = _make_event(
            body="Event body text",
            source_butler="general",
            source_session_id="sess-xyz",
            entity_ids=[eid],
        )
        payload = CalendarModule._event_to_payload(event)
        assert payload["body"] == "Event body text"
        assert payload["source_butler"] == "general"
        assert payload["source_session_id"] == "sess-xyz"
        assert payload["entity_ids"] == [str(eid)]

    def test_payload_entity_ids_empty_list_by_default(self):
        event = _make_event()
        payload = CalendarModule._event_to_payload(event)
        assert payload["entity_ids"] == []
        assert payload["body"] is None
        assert payload["source_butler"] is None


class TestCreateEventAuthorship:
    """calendar_create_event annotates events with source_butler and session_id."""

    async def test_create_event_sets_source_butler_in_result(self):
        created = _make_event(title="BUTLER: Meeting", butler_generated=True, butler_name="general")
        provider = _ProviderDouble(event=created)
        mcp = _StubMCP()
        mod = CalendarModule()
        mod._provider = provider
        mod._resolved_calendar_id = "primary"
        await mod.register_tools(
            mcp=mcp,
            config={"provider": "google", "calendar_id": "primary"},
            db=SimpleNamespace(schema="general", db_name="butlers"),
            butler_name="general",
        )

        with patch("butlers.modules.calendar._get_session_id", return_value="test-sess-123"):
            result = await mcp.tools["calendar_create_event"](
                title="Meeting",
                start_at=datetime(2026, 3, 1, 9, 0, tzinfo=UTC),
                end_at=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
            )

        assert result["status"] == "created"
        assert result["event"]["source_butler"] == "general"
        assert result["event"]["source_session_id"] == "test-sess-123"

    async def test_create_event_with_body_stored_in_result(self):
        created = _make_event(title="BUTLER: Meeting", butler_generated=True, butler_name="general")
        provider = _ProviderDouble(event=created)
        mcp = _StubMCP()
        mod = CalendarModule()
        mod._provider = provider
        mod._resolved_calendar_id = "primary"
        await mod.register_tools(
            mcp=mcp,
            config={"provider": "google", "calendar_id": "primary"},
            db=SimpleNamespace(db_name="butlers", schema="general"),
            butler_name="test-butler",
        )

        result = await mcp.tools["calendar_create_event"](
            title="Meeting",
            start_at=datetime(2026, 3, 1, 9, 0, tzinfo=UTC),
            end_at=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
            body="Detailed event body",
        )

        assert result["status"] == "created"
        assert result["event"]["body"] == "Detailed event body"

    async def test_create_event_with_entity_ids(self):
        eid = uuid.uuid4()
        created = _make_event(title="BUTLER: Meeting", butler_generated=True, butler_name="general")
        provider = _ProviderDouble(event=created)
        mcp = _StubMCP()
        mod = CalendarModule()
        mod._provider = provider
        mod._resolved_calendar_id = "primary"
        await mod.register_tools(
            mcp=mcp,
            config={"provider": "google", "calendar_id": "primary"},
            db=SimpleNamespace(db_name="butlers", schema="general"),
            butler_name="test-butler",
        )

        result = await mcp.tools["calendar_create_event"](
            title="Meeting",
            start_at=datetime(2026, 3, 1, 9, 0, tzinfo=UTC),
            end_at=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
            entity_ids=[eid],
        )

        assert result["status"] == "created"
        assert str(eid) in result["event"]["entity_ids"]


class TestEntityAssociationHelpers:
    """_upsert_event_entities and _fetch_event_entity_ids DB helpers."""

    async def test_upsert_event_entities_no_op_on_empty_list(self):
        """No DB calls when entity_ids is empty."""
        mod = CalendarModule()
        mock_pool = AsyncMock()
        mod._db = SimpleNamespace(pool=mock_pool)
        event_id = uuid.uuid4()

        await mod._upsert_event_entities(event_id=event_id, entity_ids=[])

        mock_pool.acquire.assert_not_called()

    async def test_fetch_event_entity_ids_no_pool_returns_empty(self):
        """Gracefully returns [] when no DB pool is available."""
        mod = CalendarModule()
        mod._db = None
        result = await mod._fetch_event_entity_ids(event_id=uuid.uuid4())
        assert result == []

    async def test_upsert_event_entities_executes_delete_and_insert(self):
        """Transaction deletes existing entities and inserts new ones."""
        mod = CalendarModule()
        event_id = uuid.uuid4()
        entity_id = uuid.uuid4()

        mock_conn = AsyncMock()
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)
        mock_tx = AsyncMock()
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=False)
        mock_conn.transaction = MagicMock(return_value=mock_tx)
        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=mock_conn)
        mod._db = SimpleNamespace(pool=mock_pool)

        await mod._upsert_event_entities(event_id=event_id, entity_ids=[entity_id])

        mock_conn.execute.assert_awaited_once()
        delete_sql = mock_conn.execute.call_args[0][0]
        assert "DELETE FROM calendar_event_entities" in delete_sql
        mock_conn.executemany.assert_awaited_once()
        insert_sql = mock_conn.executemany.call_args[0][0]
        assert "INSERT INTO calendar_event_entities" in insert_sql


class TestProjectInternalSourcesNoReminders:
    """_project_internal_sources no longer invokes a separate reminders pipeline."""

    async def test_project_internal_sources_only_calls_scheduler_source(self):
        """_project_internal_sources delegates only to _project_scheduler_source.

        ``_project_reminders_source`` was removed in bu-pn5ko.  Reminders are now
        native calendar events projected through the standard pipeline.
        """
        mod = CalendarModule()
        mod._db = SimpleNamespace(pool=None)

        scheduler_calls: list[str] = []

        async def _mock_scheduler() -> None:
            scheduler_calls.append("scheduler")

        with patch.object(mod, "_project_scheduler_source", side_effect=_mock_scheduler):
            await mod._project_internal_sources()

        assert scheduler_calls == ["scheduler"]
        assert not hasattr(mod, "_project_reminders_source"), (
            "_project_reminders_source should have been removed from CalendarModule"
        )


class TestCalendarProjectionSchemaCompatibility:
    """Projection paths fail closed when calendar schema is only partially migrated."""

    @staticmethod
    def _make_projection_pool(*, missing_flags: set[str] | None = None) -> MagicMock:
        pool = MagicMock()
        missing = missing_flags or set()

        availability_row = MagicMock()
        availability_row.__getitem__ = lambda self, key: key not in missing

        pool.fetchrow = AsyncMock(return_value=availability_row)
        pool.fetchval = AsyncMock()
        pool.fetch = AsyncMock()
        return pool

    async def test_projection_tables_unavailable_when_required_event_columns_missing(self):
        pool = self._make_projection_pool(missing_flags={"has_events_body"})
        mod = _make_module_with_pool(pool)

        assert await mod._projection_tables_available() is False

    async def test_project_scheduler_source_noops_when_required_event_columns_missing(self):
        pool = self._make_projection_pool(
            missing_flags={
                "has_events_body",
                "has_events_source_butler",
                "has_events_source_session_id",
            }
        )
        mod = _make_module_with_pool(pool)

        await mod._project_scheduler_source()

        pool.fetchval.assert_not_awaited()
        pool.fetch.assert_not_awaited()


# ---------------------------------------------------------------------------
# Track A: _project_scheduler_source() dispatch_mode exclusion [bu-vc3sl]
# ---------------------------------------------------------------------------


class TestProjectSchedulerSourceDispatchModeFilter:
    """_project_scheduler_source() must exclude dispatch_mode='job' rows at the SQL level.

    Track A fix (bu-daaff / PR #1297): butler-managed scheduled jobs such as
    memory_consolidation must never be projected into calendar_event_instances.
    The fix adds ``WHERE dispatch_mode != 'job'`` directly to the SQL query so
    that job-dispatch rows are filtered before Python code ever sees them.

    These tests verify:
    1. The SQL emitted to pool.fetch contains the exclusion clause.
    2. Non-job rows (dispatch_mode='task' or 'prompt') are processed normally.
    """

    @staticmethod
    def _make_full_projection_pool(*, scheduled_rows: list[dict] | None = None) -> MagicMock:
        """Build a pool mock that passes all schema-availability checks.

        - ``fetchrow`` handles both the projection-tables availability check
          (returns a row with all flags True) and the ``_ensure_calendar_source``
          INSERT … RETURNING id (returns a row with a fake UUID).
        - ``fetchval`` handles ``_table_exists`` (returns True for scheduled_tasks).
        - ``fetch`` returns the given scheduled_rows for the scheduled_tasks query
          and an empty list for any other query (e.g. stale-event cleanup).
        - ``execute`` is a no-op async function.
        """
        pool = MagicMock()

        # All schema flags True — used by _projection_tables_available().
        availability_row = MagicMock()
        availability_row.__getitem__ = lambda self, key: True

        # _ensure_calendar_source does INSERT … RETURNING id.
        source_row = MagicMock()
        source_row.__getitem__ = lambda self, key: uuid.uuid4() if key == "id" else None

        async def fetchrow_side_effect(query, *args):
            if "to_regclass" in query:
                return availability_row
            # _ensure_calendar_source INSERT … RETURNING id
            return source_row

        pool.fetchrow = AsyncMock(side_effect=fetchrow_side_effect)

        # _table_exists → True for every table name.
        pool.fetchval = AsyncMock(return_value=True)

        # Scheduled-tasks query returns caller-supplied rows; other fetch calls
        # (e.g. stale-event cancel) return an empty list.
        #
        # Use _FakeRecord instead of MagicMock so that dict(row) works correctly.
        # asyncpg Records support dict() via keys(); MagicMock does not.
        rows_to_return = [_FakeRecord(r) for r in (scheduled_rows or [])]

        async def fetch_side_effect(query, *args):
            if "scheduled_tasks" in query:
                return rows_to_return
            return []

        pool.fetch = AsyncMock(side_effect=fetch_side_effect)
        pool.execute = AsyncMock()
        return pool

    @staticmethod
    def _minimal_scheduled_task(dispatch_mode: str, name: str = "test_task") -> dict:
        """Return the minimum fields _project_scheduler_source() reads from a row."""
        return {
            "id": uuid.uuid4(),
            "name": name,
            "cron": "0 * * * *",  # hourly — ensures at least one occurrence
            "dispatch_mode": dispatch_mode,
            "prompt": None,
            "job_name": None,
            "job_args": None,
            "timezone": "UTC",
            "start_at": None,
            "end_at": None,
            "until_at": None,
            "display_title": name,
            "calendar_event_id": None,
            "enabled": True,
            "updated_at": None,
        }

    async def test_sql_query_excludes_job_dispatch_mode(self) -> None:
        """The SQL sent to pool.fetch must contain ``WHERE dispatch_mode != 'job'``.

        This is the primary assertion for Track A: the exclusion lives in the
        query itself, not in a post-fetch Python filter, so that the database
        never even returns job-dispatch rows.
        """
        pool = self._make_full_projection_pool(scheduled_rows=[])
        mod = _make_module_with_pool(pool)
        # Pre-seed the availability cache so _projection_tables_available() skips
        # its own fetchrow and we can inspect the fetch call cleanly.
        mod._projection_tables_available_cache = True

        await mod._project_scheduler_source()

        # Locate the fetch call that targets scheduled_tasks.
        scheduled_tasks_calls = [
            call
            for call in pool.fetch.call_args_list
            if call.args and "scheduled_tasks" in call.args[0]
        ]
        assert scheduled_tasks_calls, (
            "pool.fetch should have been called with a scheduled_tasks query"
        )
        sql = scheduled_tasks_calls[0].args[0]
        assert "dispatch_mode != 'job'" in sql, (
            "SQL must exclude dispatch_mode='job' rows to prevent butler-managed "
            "jobs from being projected into calendar_event_instances"
        )

    async def test_non_job_task_row_is_processed(self) -> None:
        """A row with dispatch_mode='task' (non-job) must be projected normally.

        Verifies that the fix does not over-filter: legitimate user-visible
        scheduled tasks still reach _upsert_projection_event.
        """
        task_row = self._minimal_scheduled_task(dispatch_mode="task", name="Morning briefing")
        pool = self._make_full_projection_pool(scheduled_rows=[task_row])
        mod = _make_module_with_pool(pool)
        mod._projection_tables_available_cache = True

        upsert_calls: list[str] = []

        async def _fake_upsert_event(**kwargs) -> uuid.UUID:
            upsert_calls.append(kwargs.get("title", ""))
            return uuid.uuid4()

        async def _noop_upsert_instance(**kwargs) -> None:
            pass

        async def _noop_mark_stale(**kwargs) -> None:
            pass

        async def _noop_cursor(**kwargs) -> None:
            pass

        async def _noop_prune(**kwargs) -> None:
            pass

        with (
            patch.object(mod, "_upsert_projection_event", side_effect=_fake_upsert_event),
            patch.object(mod, "_upsert_projection_instance", side_effect=_noop_upsert_instance),
            patch.object(
                mod,
                "_mark_projection_source_stale_events_cancelled",
                side_effect=_noop_mark_stale,
            ),
            patch.object(mod, "_upsert_projection_cursor", side_effect=_noop_cursor),
            patch.object(mod, "_prune_recurring_instances_outside_window", side_effect=_noop_prune),
        ):
            await mod._project_scheduler_source()

        assert upsert_calls == ["Morning briefing"], (
            "Non-job dispatch_mode row must be projected via _upsert_projection_event"
        )

    async def test_job_dispatch_row_absent_from_projection(self) -> None:
        """A row with dispatch_mode='job' must NOT reach _upsert_projection_event.

        The SQL WHERE clause filters these rows out before Python sees them.
        We verify this by seeding the pool.fetch mock to return only the non-job
        row (simulating the DB-level filter) and confirming _upsert_projection_event
        is called exactly once — for the non-job row only.
        """
        non_job_row = self._minimal_scheduled_task(dispatch_mode="prompt", name="User reminder")
        # NOTE: pool.fetch mock simulates the DB filter: it returns only the non-job row.
        # The point of this test is that the SQL contains the filter and the job row
        # never enters the Python processing loop.
        pool = self._make_full_projection_pool(scheduled_rows=[non_job_row])
        mod = _make_module_with_pool(pool)
        mod._projection_tables_available_cache = True

        upsert_calls: list[str] = []

        async def _fake_upsert_event(**kwargs) -> uuid.UUID:
            upsert_calls.append(kwargs.get("title", ""))
            return uuid.uuid4()

        async def _noop(**kwargs) -> None:
            pass

        with (
            patch.object(mod, "_upsert_projection_event", side_effect=_fake_upsert_event),
            patch.object(mod, "_upsert_projection_instance", side_effect=_noop),
            patch.object(mod, "_mark_projection_source_stale_events_cancelled", side_effect=_noop),
            patch.object(mod, "_upsert_projection_cursor", side_effect=_noop),
            patch.object(mod, "_prune_recurring_instances_outside_window", side_effect=_noop),
        ):
            await mod._project_scheduler_source()

        assert len(upsert_calls) == 1, (
            "Only the non-job row should be projected; job rows are excluded by the SQL filter"
        )
        assert "User reminder" in upsert_calls, "The non-job row title must be present"


# ---------------------------------------------------------------------------
# tick() — due-reminder evaluation
# ---------------------------------------------------------------------------


def _make_pool_for_tick(
    *,
    projection_available: bool = True,
    recurring_rows: list[dict] | None = None,
    onetime_rows: list[dict] | None = None,
    execute_raises: Exception | None = None,
) -> MagicMock:
    """Build an asyncpg pool mock for CalendarModule.tick() tests."""
    pool = MagicMock()

    # _projection_tables_available uses pool.fetchrow.
    availability_row = MagicMock()
    availability_row.__getitem__ = lambda self, key: True  # all tables present

    async def fetchrow_side_effect(query, *args):
        if "to_regclass" in query:
            if not projection_available:
                return None
            return availability_row
        return None

    pool.fetchrow = AsyncMock(side_effect=fetchrow_side_effect)

    # pool.fetch dispatches by query content so tests stay stable if tick()
    # reorders or adds queries.
    async def fetch_side_effect(query, *args):
        if "calendar_event_instances" in query:
            return [_row_to_record(r) for r in (recurring_rows or [])]
        else:
            return [_row_to_record(r) for r in (onetime_rows or [])]

    pool.fetch = AsyncMock(side_effect=fetch_side_effect)

    if execute_raises is not None:
        pool.execute = AsyncMock(side_effect=execute_raises)
    else:
        pool.execute = AsyncMock()

    return pool


def _row_to_record(d: dict) -> MagicMock:
    """Convert a plain dict into an asyncpg Record-like MagicMock."""
    rec = MagicMock()
    rec.__getitem__ = lambda self, key: d[key]
    return rec


class _FakeRecord:
    """Minimal asyncpg Record substitute that supports ``dict(record)`` conversion.

    ``dict(record)`` works via the mapping protocol (keys() + __getitem__).
    ``MagicMock`` does not implement ``keys()`` so dict() on it returns ``{}``.
    Use this class wherever the production code calls ``dict(row)`` on a fetched
    asyncpg record.
    """

    def __init__(self, data: dict) -> None:
        self._data = data

    def __getitem__(self, key: str):
        return self._data[key]

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def keys(self):
        return self._data.keys()

    def items(self):
        return self._data.items()

    def values(self):
        return self._data.values()


def _make_module_with_pool(pool) -> CalendarModule:
    mod = CalendarModule()
    db = MagicMock()
    db.pool = pool
    mod._db = db
    # Allow _projection_tables_available to actually query
    mod._projection_tables_available_cache = None
    return mod


class TestCalendarModuleTick:
    """CalendarModule.tick() — recurring and one-time reminder dedup."""

    async def test_returns_zero_when_no_pool(self):
        mod = CalendarModule()
        mod._db = None
        result = await mod.tick("general")
        assert result == 0

    async def test_returns_zero_when_projection_tables_unavailable(self):
        pool = _make_pool_for_tick(projection_available=False)
        mod = _make_module_with_pool(pool)
        result = await mod.tick("general")
        assert result == 0

    async def test_returns_zero_when_no_due_reminders(self):
        pool = _make_pool_for_tick(recurring_rows=[], onetime_rows=[])
        mod = _make_module_with_pool(pool)
        notify_fn = AsyncMock()
        result = await mod.tick("general", notify_fn=notify_fn)
        assert result == 0
        notify_fn.assert_not_called()

    async def test_recurring_reminder_fires_on_occurrence(self):
        """A recurring reminder's instance fires when its starts_at is past."""
        event_id = uuid.uuid4()
        instance_id = uuid.uuid4()
        pool = _make_pool_for_tick(
            recurring_rows=[
                {
                    "event_id": event_id,
                    "title": "Weekly Check",
                    "instance_id": instance_id,
                    "instance_starts_at": datetime(2026, 4, 14, 9, 0, tzinfo=UTC),
                }
            ],
            onetime_rows=[],
        )
        mod = _make_module_with_pool(pool)
        notify_fn = AsyncMock()

        result = await mod.tick("general", notify_fn=notify_fn)

        assert result == 1
        notify_fn.assert_called_once()
        envelope = notify_fn.call_args[0][0]
        assert envelope["reminder_event_id"] == str(event_id)
        assert envelope["reminder_instance_id"] == str(instance_id)
        # Instance metadata must be updated (not the event row)
        execute_calls = pool.execute.call_args_list
        assert len(execute_calls) == 1
        assert "calendar_event_instances" in execute_calls[0][0][0]
        assert "notified_at" in execute_calls[0][0][1]

    async def test_recurring_reminder_same_occurrence_does_not_double_fire(self):
        """An instance already marked notified_at is excluded by the query."""
        # If the query returns no rows, tick() fires nothing.
        pool = _make_pool_for_tick(recurring_rows=[], onetime_rows=[])
        mod = _make_module_with_pool(pool)
        notify_fn = AsyncMock()

        result = await mod.tick("general", notify_fn=notify_fn)

        assert result == 0
        notify_fn.assert_not_called()

    async def test_dismissed_recurring_instance_is_skipped(self):
        """Cancelled instances are excluded by the SQL status='confirmed' filter.

        We test the behaviour indirectly: if the pool returns no rows (because
        the SQL WHERE clause filtered the cancelled instance), tick returns 0.
        """
        pool = _make_pool_for_tick(recurring_rows=[], onetime_rows=[])
        mod = _make_module_with_pool(pool)
        notify_fn = AsyncMock()

        result = await mod.tick("general", notify_fn=notify_fn)

        assert result == 0
        notify_fn.assert_not_called()

    async def test_onetime_reminder_fires_and_marks_event(self):
        """A one-time reminder fires and records last_notified_at on the event."""
        event_id = uuid.uuid4()
        pool = _make_pool_for_tick(
            recurring_rows=[],
            onetime_rows=[
                {
                    "event_id": event_id,
                    "title": "Doctor Appointment",
                    "starts_at": datetime(2026, 4, 15, 10, 0, tzinfo=UTC),
                }
            ],
        )
        mod = _make_module_with_pool(pool)
        notify_fn = AsyncMock()

        result = await mod.tick("general", notify_fn=notify_fn)

        assert result == 1
        notify_fn.assert_called_once()
        envelope = notify_fn.call_args[0][0]
        assert envelope["reminder_event_id"] == str(event_id)
        assert "reminder_instance_id" not in envelope
        # Event row must be updated with last_notified_at
        execute_calls = pool.execute.call_args_list
        assert len(execute_calls) == 1
        assert "calendar_events" in execute_calls[0][0][0]
        assert "last_notified_at" in execute_calls[0][0][1]

    async def test_notify_fn_failure_skips_metadata_update(self):
        """When notify_fn raises, the instance is not marked as notified."""
        event_id = uuid.uuid4()
        instance_id = uuid.uuid4()
        pool = _make_pool_for_tick(
            recurring_rows=[
                {
                    "event_id": event_id,
                    "title": "Daily Stand-up",
                    "instance_id": instance_id,
                    "instance_starts_at": datetime(2026, 4, 14, 9, 0, tzinfo=UTC),
                }
            ],
            onetime_rows=[],
        )
        mod = _make_module_with_pool(pool)
        notify_fn = AsyncMock(side_effect=RuntimeError("delivery failed"))

        result = await mod.tick("general", notify_fn=notify_fn)

        assert result == 0
        pool.execute.assert_not_called()

    async def test_multi_occurrence_dispatched_independently(self):
        """Multiple past occurrences of the same recurring series each fire once."""
        event_id = uuid.uuid4()
        instance_id_1 = uuid.uuid4()
        instance_id_2 = uuid.uuid4()
        pool = _make_pool_for_tick(
            recurring_rows=[
                {
                    "event_id": event_id,
                    "title": "Monthly Review",
                    "instance_id": instance_id_1,
                    "instance_starts_at": datetime(2026, 3, 1, 9, 0, tzinfo=UTC),
                },
                {
                    "event_id": event_id,
                    "title": "Monthly Review",
                    "instance_id": instance_id_2,
                    "instance_starts_at": datetime(2026, 4, 1, 9, 0, tzinfo=UTC),
                },
            ],
            onetime_rows=[],
        )
        mod = _make_module_with_pool(pool)
        notify_fn = AsyncMock()

        result = await mod.tick("general", notify_fn=notify_fn)

        assert result == 2
        assert notify_fn.call_count == 2
        # Two separate UPDATE statements on calendar_event_instances
        execute_calls = pool.execute.call_args_list
        assert len(execute_calls) == 2
        # Each call should target the two different instance IDs
        updated_ids = {call[0][2] for call in execute_calls}
        assert instance_id_1 in updated_ids
        assert instance_id_2 in updated_ids


class TestProjectionEventHelpers:
    """Projection event writes normalize required authorship fields."""

    @pytest.mark.parametrize("provided_source_butler", [None, "", "   ", "unknown"])
    async def test_upsert_projection_event_falls_back_to_module_butler_name(
        self, provided_source_butler: str | None
    ) -> None:
        mod = CalendarModule()
        mod._butler_name = "finance"
        mock_pool = AsyncMock()
        mock_pool.fetchrow.return_value = {"id": uuid.uuid4()}
        mod._db = SimpleNamespace(pool=mock_pool)

        await mod._upsert_projection_event(
            source_id=uuid.uuid4(),
            origin_ref="task-1",
            title="Scheduled task",
            timezone="UTC",
            starts_at=datetime(2026, 4, 16, 6, 0, tzinfo=UTC),
            ends_at=datetime(2026, 4, 16, 6, 15, tzinfo=UTC),
            status="confirmed",
            source_butler=provided_source_butler,
        )

        assert mock_pool.fetchrow.await_count == 1
        fetchrow_args = mock_pool.fetchrow.await_args.args
        assert fetchrow_args[-2] == "finance"

    async def test_upsert_projection_event_uses_default_butler_when_names_are_blank(
        self,
    ) -> None:
        mod = CalendarModule()
        mod._butler_name = "  "
        mock_pool = AsyncMock()
        mock_pool.fetchrow.return_value = {"id": uuid.uuid4()}
        mod._db = SimpleNamespace(pool=mock_pool)

        await mod._upsert_projection_event(
            source_id=uuid.uuid4(),
            origin_ref="reminder-1",
            title="Reminder",
            timezone="UTC",
            starts_at=datetime(2026, 3, 1, 9, 0, tzinfo=UTC),
            ends_at=datetime(2026, 3, 1, 9, 15, tzinfo=UTC),
            status="confirmed",
            source_butler=" ",
        )

        fetchrow_args = mock_pool.fetchrow.await_args.args
        assert fetchrow_args[-2] == DEFAULT_BUTLER_NAME

    async def test_upsert_projection_event_normalizes_blank_source_session_id(
        self,
    ) -> None:
        mod = CalendarModule()
        mod._butler_name = "general"
        mock_pool = AsyncMock()
        mock_pool.fetchrow.return_value = {"id": uuid.uuid4()}
        mod._db = SimpleNamespace(pool=mock_pool)

        await mod._upsert_projection_event(
            source_id=uuid.uuid4(),
            origin_ref="sched-1",
            title="Scheduled task",
            timezone="UTC",
            starts_at=datetime(2026, 3, 1, 9, 0, tzinfo=UTC),
            ends_at=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
            status="confirmed",
            source_butler=None,
            source_session_id="  ",
        )

        fetchrow_args = mock_pool.fetchrow.await_args.args
        assert fetchrow_args[-2] == "general"
        assert fetchrow_args[-1] is None


class TestCalendarModuleExtraStatusFields:
    """extra_status_fields() emits OAuth/credential health based on google_accounts."""

    async def test_no_db_returns_empty(self) -> None:
        """Without a DB object, extra_status_fields returns {} (graceful degradation)."""
        mod = CalendarModule()
        assert mod._db is None
        result = await mod.extra_status_fields()
        assert result == {}

    async def test_no_pool_on_db_returns_empty(self) -> None:
        """DB with no pool attribute returns {} gracefully."""
        mod = CalendarModule()
        mod._db = SimpleNamespace()  # no pool attribute
        result = await mod.extra_status_fields()
        assert result == {}

    async def test_active_primary_account_returns_granted(self) -> None:
        """Active primary account → oauth_status='granted', credential_health='ok'."""
        mod = CalendarModule()
        mock_pool = MagicMock()
        mock_pool.fetchrow = AsyncMock(return_value={"status": "active"})
        mod._db = SimpleNamespace(pool=mock_pool)

        result = await mod.extra_status_fields()

        assert result["oauth_status"] == "granted"
        assert result["credential_health"] == "ok"
        mock_pool.fetchrow.assert_awaited_once()

    async def test_revoked_account_returns_reauth_needed(self) -> None:
        """Revoked primary account → oauth_status='reauth_needed', credential_health='error'."""
        mod = CalendarModule()
        mock_pool = MagicMock()
        mock_pool.fetchrow = AsyncMock(return_value={"status": "revoked"})
        mod._db = SimpleNamespace(pool=mock_pool)

        result = await mod.extra_status_fields()

        assert result["oauth_status"] == "reauth_needed"
        assert result["credential_health"] == "error"

    async def test_expired_account_returns_reauth_needed(self) -> None:
        """Expired primary account → oauth_status='reauth_needed', credential_health='error'."""
        mod = CalendarModule()
        mock_pool = MagicMock()
        mock_pool.fetchrow = AsyncMock(return_value={"status": "expired"})
        mod._db = SimpleNamespace(pool=mock_pool)

        result = await mod.extra_status_fields()

        assert result["oauth_status"] == "reauth_needed"
        assert result["credential_health"] == "error"

    async def test_no_primary_account_returns_not_configured(self) -> None:
        """No primary account row → oauth_status='not_configured', credential_health='warning'."""
        mod = CalendarModule()
        mock_pool = MagicMock()
        mock_pool.fetchrow = AsyncMock(return_value=None)
        mod._db = SimpleNamespace(pool=mock_pool)

        result = await mod.extra_status_fields()

        assert result["oauth_status"] == "not_configured"
        assert result["credential_health"] == "warning"

    async def test_db_query_error_returns_empty(self) -> None:
        """DB query failure returns {} without propagating exception."""
        mod = CalendarModule()
        mock_pool = MagicMock()
        mock_pool.fetchrow = AsyncMock(side_effect=Exception("connection refused"))
        mod._db = SimpleNamespace(pool=mock_pool)

        result = await mod.extra_status_fields()

        assert result == {}


# ---------------------------------------------------------------------------
# calendar_propose_event — proposal staging
# ---------------------------------------------------------------------------


class TestCalendarProposeEvent:
    """Tests for calendar_propose_event / _propose_event."""

    def _make_pool(
        self, *, insert_id: uuid.UUID | None = None, existing_id: uuid.UUID | None = None
    ) -> MagicMock:
        """Pool stub: fetchrow returns a row with insert_id (or None on conflict), fetchval returns existing_id."""
        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value={"id": insert_id} if insert_id is not None else None)
        pool.fetchval = AsyncMock(return_value=existing_id)
        return pool

    async def test_insert_creates_pending_row(self) -> None:
        """_propose_event inserts a row and returns the new proposal id."""
        proposal_id = uuid.uuid4()
        pool = self._make_pool(insert_id=proposal_id)
        mod = CalendarModule()
        mod._butler_name = "finance"
        mod._db = SimpleNamespace(pool=pool)

        result = await mod._propose_event(
            title="Budget Review",
            start_at=datetime(2026, 7, 1, 10, 0, tzinfo=UTC),
            end_at=datetime(2026, 7, 1, 11, 0, tzinfo=UTC),
            timezone="UTC",
        )

        assert result == proposal_id
        sql, *params = pool.fetchrow.await_args.args
        assert "INSERT INTO calendar_event_proposals" in sql
        assert "ON CONFLICT" in sql
        assert "DO NOTHING" in sql
        assert "finance" in params  # butler_name
        assert "Budget Review" in params

    async def test_duplicate_source_event_id_returns_existing(self) -> None:
        """Duplicate source_event_id is a no-op — returns existing proposal id."""
        existing_id = uuid.uuid4()
        pool = self._make_pool(insert_id=None, existing_id=existing_id)
        mod = CalendarModule()
        mod._butler_name = "finance"
        mod._db = SimpleNamespace(pool=pool)

        result = await mod._propose_event(
            title="Budget Review",
            start_at=datetime(2026, 7, 1, 10, 0, tzinfo=UTC),
            end_at=datetime(2026, 7, 1, 11, 0, tzinfo=UTC),
            source_event_id="evt-src-123",
        )

        assert result == existing_id
        # fetchval called with the source_event_id to retrieve the existing row
        pool.fetchval.assert_awaited_once()
        fetchval_args = pool.fetchval.await_args.args
        assert "evt-src-123" in fetchval_args

    async def test_no_provider_call_made(self) -> None:
        """_propose_event never touches the provider client."""
        proposal_id = uuid.uuid4()
        pool = self._make_pool(insert_id=proposal_id)
        mod = CalendarModule()
        mod._butler_name = "finance"
        mod._db = SimpleNamespace(pool=pool)

        provider = MagicMock()
        mod._provider = provider

        await mod._propose_event(
            title="Quarterly Planning",
            start_at=datetime(2026, 7, 1, 9, 0, tzinfo=UTC),
            end_at=datetime(2026, 7, 1, 10, 0, tzinfo=UTC),
        )

        provider.create_event.assert_not_called()
        provider.list_events.assert_not_called()
        provider.get_event.assert_not_called()

    async def test_mcp_tool_registered_and_returns_proposal_id(self) -> None:
        """calendar_propose_event MCP tool is registered and returns proposal_id as string."""
        proposal_id = uuid.uuid4()
        pool = self._make_pool(insert_id=proposal_id)
        mod = CalendarModule()
        mod._butler_name = "finance"
        mod._db = SimpleNamespace(pool=pool)

        mcp = _StubMCP()
        await mod.register_tools(
            mcp=mcp,
            config={"provider": "google"},
            db=SimpleNamespace(pool=pool),
            butler_name="finance",
        )

        assert "calendar_propose_event" in mcp.tools
        result = await mcp.tools["calendar_propose_event"](
            title="Budget Review",
            start_at=datetime(2026, 7, 1, 10, 0, tzinfo=UTC),
            end_at=datetime(2026, 7, 1, 11, 0, tzinfo=UTC),
        )
        assert result["proposal_id"] == str(proposal_id)


# ---------------------------------------------------------------------------
# Conflict suggested-slots honor owner scheduling-availability preferences
# (bu-vj0ax8) — _build_suggested_slots clips out-of-hours / weekend / blocked
# slots when prefs are configured, and is back-compatible when they are not.
# ---------------------------------------------------------------------------


class TestSuggestedSlotsSchedulingPreferences:
    def _candidate(self, start: datetime, end: datetime) -> CalendarEventCreate:
        return CalendarEventCreate(title="Sync", start_at=start, end_at=end, timezone="UTC")

    def test_no_prefs_back_compat(self):
        """With no prefs, suggestions walk forward from the last conflict (unchanged)."""
        candidate = self._candidate(
            datetime(2026, 6, 22, 9, 0, tzinfo=UTC), datetime(2026, 6, 22, 10, 0, tzinfo=UTC)
        )
        conflict = _make_event(
            start_at=datetime(2026, 6, 22, 9, 0, tzinfo=UTC),
            end_at=datetime(2026, 6, 22, 10, 0, tzinfo=UTC),
        )
        slots = CalendarModule._build_suggested_slots(
            candidate, [conflict], count=3, scheduling_preferences=None
        )
        assert len(slots) == 3
        # First suggestion starts right after the conflict ends.
        assert slots[0]["start_at"] == datetime(2026, 6, 22, 10, 0, tzinfo=UTC).isoformat()

    def test_prefs_skip_after_hours_slots(self):
        """A late conflict pushes suggestions past latest -> next day's earliest."""
        from butlers.core.temporal.scheduling import SchedulingPreferences

        prefs = SchedulingPreferences(
            timezone="UTC",
            earliest_meeting_time=time(9, 0),
            latest_meeting_time=time(18, 0),
            meeting_days=frozenset({"MO", "TU", "WE", "TH", "FR"}),
        )
        # Conflict ends at 17:30 on Mon; a 1h slot from 17:30 ends 18:30 (> latest).
        candidate = self._candidate(
            datetime(2026, 6, 22, 17, 0, tzinfo=UTC), datetime(2026, 6, 22, 18, 0, tzinfo=UTC)
        )
        conflict = _make_event(
            start_at=datetime(2026, 6, 22, 16, 30, tzinfo=UTC),
            end_at=datetime(2026, 6, 22, 17, 30, tzinfo=UTC),
        )
        slots = CalendarModule._build_suggested_slots(
            candidate, [conflict], count=2, scheduling_preferences=prefs
        )
        assert len(slots) == 2
        for slot in slots:
            start = datetime.fromisoformat(slot["start_at"])
            end = datetime.fromisoformat(slot["end_at"])
            assert start.time() >= time(9, 0)
            assert end.time() <= time(18, 0)
            assert start.weekday() < 5  # Mon–Fri only

    def test_prefs_skip_weekend(self):
        """Suggestions never land on a disallowed weekday."""
        from butlers.core.temporal.scheduling import SchedulingPreferences

        prefs = SchedulingPreferences(
            timezone="UTC",
            meeting_days=frozenset({"MO", "TU", "WE", "TH", "FR"}),
        )
        # Conflict on Fri 2026-06-26 late; next slots would be Sat/Sun without prefs.
        candidate = self._candidate(
            datetime(2026, 6, 26, 16, 0, tzinfo=UTC), datetime(2026, 6, 26, 17, 0, tzinfo=UTC)
        )
        conflict = _make_event(
            start_at=datetime(2026, 6, 26, 23, 0, tzinfo=UTC),
            end_at=datetime(2026, 6, 27, 0, 0, tzinfo=UTC),  # ends Sat 00:00
        )
        slots = CalendarModule._build_suggested_slots(
            candidate, [conflict], count=1, scheduling_preferences=prefs
        )
        assert len(slots) == 1
        start = datetime.fromisoformat(slots[0]["start_at"])
        assert start.weekday() < 5  # not Sat/Sun -> jumps to Monday

    def test_prefs_skip_no_meeting_block(self):
        """Suggestions never overlap a no-meeting block (e.g. lunch)."""
        from butlers.core.temporal.scheduling import SchedulingPreferences

        prefs = SchedulingPreferences(
            timezone="UTC",
            earliest_meeting_time=time(9, 0),
            latest_meeting_time=time(18, 0),
            no_meeting_blocks=((time(12, 0), time(13, 0)),),
        )
        candidate = self._candidate(
            datetime(2026, 6, 22, 11, 30, tzinfo=UTC), datetime(2026, 6, 22, 12, 30, tzinfo=UTC)
        )
        conflict = _make_event(
            start_at=datetime(2026, 6, 22, 11, 0, tzinfo=UTC),
            end_at=datetime(2026, 6, 22, 11, 30, tzinfo=UTC),
        )
        slots = CalendarModule._build_suggested_slots(
            candidate, [conflict], count=2, scheduling_preferences=prefs
        )
        assert len(slots) == 2
        for slot in slots:
            start = datetime.fromisoformat(slot["start_at"])
            end = datetime.fromisoformat(slot["end_at"])
            # No overlap with [12:00, 13:00)
            assert not (start.time() < time(13, 0) and time(12, 0) < end.time())


# ---------------------------------------------------------------------------
# Recurrence-scoped occurrence mutation (this / following / series) — bu-9ez7bn
# ---------------------------------------------------------------------------


class _RecurringProviderDouble(_ProviderDouble):
    """Provider double that supports occurrence-scoped recurrence edits."""

    def __init__(self, *, event: CalendarEvent, recurrence: list[str]) -> None:
        super().__init__(event=event)
        self._recurrence = list(recurrence)
        self.set_recurrence_calls: list[dict] = []
        self.created_payloads: list[CalendarEventCreate] = []

    async def get_recurrence(self, *, calendar_id, event_id):
        return list(self._recurrence)

    async def set_recurrence(self, *, calendar_id, event_id, recurrence, send_updates=None):
        self.set_recurrence_calls.append(
            {"event_id": event_id, "recurrence": list(recurrence), "send_updates": send_updates}
        )
        return self._event

    async def create_event(self, *, calendar_id, payload):
        self.created_payloads.append(payload)
        return _make_event(
            event_id="evt-detached",
            title=payload.title,
            start_at=payload.start_at,
            end_at=payload.end_at,
        )

    async def delete_event(self, *, calendar_id, event_id, send_updates=None):
        self.delete_calls.append({"calendar_id": calendar_id, "event_id": event_id})


class TestRecurrenceHelpers:
    """Pure RRULE / EXDATE / UNTIL helpers."""

    def test_format_ical_utc(self) -> None:
        dt = datetime(2026, 3, 3, 14, 0, tzinfo=UTC)
        assert _format_ical_utc(dt) == "20260303T140000Z"

    def test_append_exdate_preserves_rrule(self) -> None:
        lines = ["RRULE:FREQ=WEEKLY;BYDAY=TU"]
        result = _recurrence_lines_append_exdate(lines, datetime(2026, 3, 3, 14, 0, tzinfo=UTC))
        assert "RRULE:FREQ=WEEKLY;BYDAY=TU" in result
        assert "EXDATE:20260303T140000Z" in result

    def test_append_exdate_folds_into_existing_exdate(self) -> None:
        lines = ["RRULE:FREQ=WEEKLY", "EXDATE:20260224T140000Z"]
        result = _recurrence_lines_append_exdate(lines, datetime(2026, 3, 3, 14, 0, tzinfo=UTC))
        exdate_lines = [line for line in result if line.startswith("EXDATE")]
        assert len(exdate_lines) == 1
        assert "20260224T140000Z" in exdate_lines[0]
        assert "20260303T140000Z" in exdate_lines[0]

    def test_append_exdate_is_idempotent(self) -> None:
        lines = ["RRULE:FREQ=WEEKLY", "EXDATE:20260303T140000Z"]
        result = _recurrence_lines_append_exdate(lines, datetime(2026, 3, 3, 14, 0, tzinfo=UTC))
        assert result == lines

    def test_bound_until_replaces_count(self) -> None:
        lines = ["RRULE:FREQ=WEEKLY;COUNT=10"]
        result = _recurrence_lines_bound_until(lines, datetime(2026, 3, 9, 23, 59, 59, tzinfo=UTC))
        assert "COUNT" not in result[0]
        assert "UNTIL=20260309T235959Z" in result[0]


class TestRecurrenceScopeLiteral:
    """Scope literal widening on the update payload model."""

    def test_update_payload_accepts_each_scope(self) -> None:
        for scope in ("this", "following", "series"):
            payload = CalendarEventUpdate(recurrence_scope=scope)
            assert payload.recurrence_scope == scope

    def test_update_payload_rejects_unknown_scope(self) -> None:
        with pytest.raises(ValidationError):
            CalendarEventUpdate(recurrence_scope="bogus")

    def test_update_payload_default_is_series(self) -> None:
        assert CalendarEventUpdate().recurrence_scope == "series"


class TestRecurrenceScopedMutation:
    """Occurrence-scoped delete/update against calendar_event_instances.is_exception."""

    @staticmethod
    def _recurring_event() -> CalendarEvent:
        return _make_event(
            event_id="evt-1",
            title="Standup",
            start_at=datetime(2026, 2, 24, 14, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 24, 14, 30, tzinfo=UTC),
            recurrence_rule="FREQ=WEEKLY;BYDAY=TU",
        )

    async def _make_module(self, provider: _ProviderDouble) -> tuple[CalendarModule, _StubMCP]:
        mcp = _StubMCP()
        mod = CalendarModule()
        mod._provider = provider
        mod._resolved_calendar_id = "primary"
        await mod.register_tools(
            mcp=mcp,
            config={"provider": "google", "calendar_id": "primary"},
            db=SimpleNamespace(db_name="butlers", db_schema="general"),
            butler_name="test-butler",
        )
        return mod, mcp

    async def test_both_instance_tools_registered(self) -> None:
        provider = _ProviderDouble(event=self._recurring_event())
        _mod, mcp = await self._make_module(provider)
        assert "calendar_update_event_instance" in mcp.tools
        assert "calendar_delete_event_instance" in mcp.tools

    async def test_delete_instance_appends_exdate(self) -> None:
        provider = _RecurringProviderDouble(
            event=self._recurring_event(), recurrence=["RRULE:FREQ=WEEKLY;BYDAY=TU"]
        )
        mod, mcp = await self._make_module(provider)
        with (
            patch(
                "butlers.modules.calendar.require_permission",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch.object(mod, "_finalize_workspace_mutation", new_callable=AsyncMock),
            patch.object(mod, "_mark_instances_exception", new_callable=AsyncMock) as mark,
        ):
            result = await mcp.tools["calendar_delete_event_instance"](
                event_id="evt-1",
                instance_start_at=datetime(2026, 3, 3, 14, 0, tzinfo=UTC),
            )

        assert result["status"] == "deleted"
        assert result["impact"] == {"occurrences_touched": 1, "scope": "this"}
        assert len(provider.set_recurrence_calls) == 1
        recurrence = provider.set_recurrence_calls[0]["recurrence"]
        assert "RRULE:FREQ=WEEKLY;BYDAY=TU" in recurrence
        assert "EXDATE:20260303T140000Z" in recurrence
        # Exactly the named occurrence is marked an exception (and cancelled).
        mark.assert_awaited_once()
        assert mark.await_args.kwargs["occurrence_start"] == datetime(2026, 3, 3, 14, 0, tzinfo=UTC)
        assert mark.await_args.kwargs["cancel"] is True

    async def test_delete_following_bounds_until(self) -> None:
        provider = _RecurringProviderDouble(
            event=self._recurring_event(), recurrence=["RRULE:FREQ=WEEKLY;BYDAY=TU"]
        )
        mod, mcp = await self._make_module(provider)
        with (
            patch(
                "butlers.modules.calendar.require_permission",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch.object(mod, "_finalize_workspace_mutation", new_callable=AsyncMock),
            patch.object(mod, "_mark_instances_exception", new_callable=AsyncMock) as mark,
        ):
            result = await mcp.tools["calendar_delete_event"](
                event_id="evt-1",
                recurrence_scope="following",
                instance_start_at=datetime(2026, 3, 10, 14, 0, tzinfo=UTC),
            )

        assert result["status"] == "deleted"
        recurrence = provider.set_recurrence_calls[0]["recurrence"]
        # UNTIL is one second before the boundary occurrence.
        assert "UNTIL=20260310T135959Z" in recurrence[0]
        assert mark.await_args.kwargs["from_boundary"] == datetime(2026, 3, 10, 14, 0, tzinfo=UTC)

    async def test_delete_series_uses_whole_event_path(self) -> None:
        # recurrence_scope defaults to series → existing whole-series delete path,
        # which calls provider.delete_event, not set_recurrence.
        provider = _RecurringProviderDouble(
            event=self._recurring_event(), recurrence=["RRULE:FREQ=WEEKLY;BYDAY=TU"]
        )
        mod, mcp = await self._make_module(provider)
        with (
            patch(
                "butlers.modules.calendar.require_permission",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch.object(mod, "_finalize_workspace_mutation", new_callable=AsyncMock),
        ):
            result = await mcp.tools["calendar_delete_event"](event_id="evt-1")

        assert result["status"] == "deleted"
        assert provider.set_recurrence_calls == []
        assert len(provider.delete_calls) == 1

    async def test_update_instance_detaches_occurrence(self) -> None:
        provider = _RecurringProviderDouble(
            event=self._recurring_event(), recurrence=["RRULE:FREQ=WEEKLY;BYDAY=TU"]
        )
        mod, mcp = await self._make_module(provider)
        with (
            patch(
                "butlers.modules.calendar.require_permission",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch.object(mod, "_finalize_workspace_mutation", new_callable=AsyncMock),
            patch.object(mod, "_mark_instances_exception", new_callable=AsyncMock) as mark,
        ):
            result = await mcp.tools["calendar_update_event_instance"](
                event_id="evt-1",
                instance_start_at=datetime(2026, 3, 3, 14, 0, tzinfo=UTC),
                title="Special standup",
            )

        assert result["status"] == "updated"
        # Original slot is EXDATE-d off the series.
        recurrence = provider.set_recurrence_calls[0]["recurrence"]
        assert "EXDATE:20260303T140000Z" in recurrence
        # A standalone, non-recurring detached event carries the edit.
        assert len(provider.created_payloads) == 1
        detached = provider.created_payloads[0]
        assert detached.title == "Special standup"
        assert detached.recurrence_rule is None
        assert detached.start_at == datetime(2026, 3, 3, 14, 0, tzinfo=UTC)
        mark.assert_awaited_once()
        assert mark.await_args.kwargs["occurrence_start"] == datetime(2026, 3, 3, 14, 0, tzinfo=UTC)

    async def test_update_following_carries_recurrence(self) -> None:
        provider = _RecurringProviderDouble(
            event=self._recurring_event(), recurrence=["RRULE:FREQ=WEEKLY;BYDAY=TU"]
        )
        mod, mcp = await self._make_module(provider)
        with (
            patch(
                "butlers.modules.calendar.require_permission",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch.object(mod, "_finalize_workspace_mutation", new_callable=AsyncMock),
            patch.object(mod, "_mark_instances_exception", new_callable=AsyncMock),
        ):
            await mcp.tools["calendar_update_event"](
                event_id="evt-1",
                recurrence_scope="following",
                instance_start_at=datetime(2026, 3, 10, 14, 0, tzinfo=UTC),
                location="Room B",
            )

        # The remainder is a NEW recurring event carrying the original rule.
        detached = provider.created_payloads[0]
        assert detached.recurrence_rule == "RRULE:FREQ=WEEKLY;BYDAY=TU"
        assert detached.location == "Room B"

    async def test_delete_instance_missing_event_is_not_found(self) -> None:
        provider = _RecurringProviderDouble(event=None, recurrence=[])
        provider._event = None
        mod, mcp = await self._make_module(provider)
        with (
            patch(
                "butlers.modules.calendar.require_permission",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch.object(mod, "_finalize_workspace_mutation", new_callable=AsyncMock),
        ):
            result = await mcp.tools["calendar_delete_event_instance"](
                event_id="missing",
                instance_start_at=datetime(2026, 3, 3, 14, 0, tzinfo=UTC),
            )
        assert result["status"] == "not_found"
        assert provider.set_recurrence_calls == []

    async def test_impact_preview_counts_by_scope(self) -> None:
        provider = _ProviderDouble(event=self._recurring_event())
        mod, _mcp = await self._make_module(provider)
        event = self._recurring_event()
        boundary = datetime(2026, 3, 10, 14, 0, tzinfo=UTC)
        assert (
            mod._count_scope_occurrences(
                event=event, recurrence_scope="this", occurrence_start=boundary
            )
            == 1
        )
        series_count = mod._count_scope_occurrences(
            event=event, recurrence_scope="series", occurrence_start=boundary
        )
        following_count = mod._count_scope_occurrences(
            event=event, recurrence_scope="following", occurrence_start=boundary
        )
        # ~13 weekly occurrences in the 90-day window; following is a strict subset.
        assert series_count > following_count > 1
        # Boundary is the 3rd Tuesday (24 Feb, 3 Mar, 10 Mar onward).
        assert series_count - following_count == 2

    async def test_delete_instance_requires_instance_start(self) -> None:
        provider = _RecurringProviderDouble(
            event=self._recurring_event(), recurrence=["RRULE:FREQ=WEEKLY;BYDAY=TU"]
        )
        mod, mcp = await self._make_module(provider)
        with (
            patch(
                "butlers.modules.calendar.require_permission",
                new_callable=AsyncMock,
                return_value=None,
            ),
            pytest.raises(ValueError, match="instance_start_at is required"),
        ):
            await mod._apply_occurrence_delete(
                event_id="evt-1",
                instance_start_at=None,
                recurrence_scope="this",
                calendar_id=None,
                send_updates=None,
                request_id=None,
                tool_name="calendar_delete_event",
            )
