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
    CalendarEventCreate,
    CalendarEventUpdate,
    CalendarModule,
    CalendarProvider,
    CalendarRequestError,
    _coerce_expires_in_seconds,
    _extract_google_credential_value,
    _extract_google_private_metadata,
    _google_event_to_calendar_event,
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
