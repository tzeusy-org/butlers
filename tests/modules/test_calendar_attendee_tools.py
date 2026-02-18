"""Tests for AttendeeInfo model and calendar attendee management tools.

Covers:
- AttendeeInfo model fields and defaults (spec section 5.4)
- AttendeeResponseStatus enum values (spec section 5.5)
- SendUpdatesPolicy enum values (spec section 5.7)
- _extract_google_attendees returns structured AttendeeInfo (not plain strings)
- _attendee_info_list_to_google serialization
- CalendarEvent.attendees uses list[AttendeeInfo]
- CalendarModule._event_to_payload serializes attendees as structured dicts
- calendar_add_attendees MCP tool: dedup, send_updates, error handling
- calendar_remove_attendees MCP tool: removal by email, send_updates, error handling
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.modules.calendar import (
    AttendeeInfo,
    AttendeeResponseStatus,
    CalendarConfig,
    CalendarEvent,
    CalendarModule,
    CalendarRequestError,
    SendUpdatesPolicy,
    _attendee_info_list_to_google,
)

pytestmark = pytest.mark.unit

FAKE_CREDS = json.dumps(
    {
        "client_id": "test-client-id",
        "client_secret": "test-client-secret",
        "refresh_token": "test-refresh-token",
    }
)


# ===========================================================================
# AttendeeInfo Model Tests
# ===========================================================================


class TestAttendeeInfoModel:
    """Test AttendeeInfo model construction and defaults."""

    def test_minimal_attendee_with_email_only(self):
        a = AttendeeInfo(email="alice@example.com")
        assert a.email == "alice@example.com"
        assert a.display_name is None
        assert a.response_status == AttendeeResponseStatus.needs_action
        assert a.optional is False
        assert a.organizer is False
        assert a.self_ is False
        assert a.comment is None

    def test_full_attendee_fields(self):
        a = AttendeeInfo(
            email="bob@example.com",
            display_name="Bob Jones",
            response_status=AttendeeResponseStatus.accepted,
            optional=True,
            organizer=False,
            **{"self": True},
            comment="See you there",
        )
        assert a.email == "bob@example.com"
        assert a.display_name == "Bob Jones"
        assert a.response_status == AttendeeResponseStatus.accepted
        assert a.optional is True
        assert a.organizer is False
        assert a.self_ is True
        assert a.comment == "See you there"

    def test_attendee_self_field_alias(self):
        """self_ can be set via the 'self' alias."""
        a = AttendeeInfo(email="x@example.com", **{"self": True})
        assert a.self_ is True

    def test_attendee_self_field_direct(self):
        """self_ can be set via the Python-safe name."""
        a = AttendeeInfo(email="x@example.com", self_=True)
        assert a.self_ is True

    def test_response_status_enum_values(self):
        assert AttendeeResponseStatus.needs_action.value == "needsAction"
        assert AttendeeResponseStatus.accepted.value == "accepted"
        assert AttendeeResponseStatus.declined.value == "declined"
        assert AttendeeResponseStatus.tentative.value == "tentative"

    def test_send_updates_policy_enum_values(self):
        assert SendUpdatesPolicy.all.value == "all"
        assert SendUpdatesPolicy.external_only.value == "externalOnly"
        assert SendUpdatesPolicy.none.value == "none"


# ===========================================================================
# CalendarEvent Attendees Field
# ===========================================================================


class TestCalendarEventAttendeesField:
    """Test that CalendarEvent.attendees accepts list[AttendeeInfo]."""

    def test_calendar_event_with_attendee_info_list(self):
        event = CalendarEvent(
            event_id="evt-1",
            title="Meeting",
            start_at=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
            end_at=datetime(2026, 3, 1, 11, 0, tzinfo=UTC),
            timezone="UTC",
            attendees=[
                AttendeeInfo(email="alice@example.com"),
                AttendeeInfo(
                    email="bob@example.com",
                    response_status=AttendeeResponseStatus.accepted,
                ),
            ],
        )
        assert len(event.attendees) == 2
        assert event.attendees[0].email == "alice@example.com"
        assert event.attendees[1].response_status == AttendeeResponseStatus.accepted

    def test_calendar_event_empty_attendees_default(self):
        event = CalendarEvent(
            event_id="evt-1",
            title="Solo Event",
            start_at=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
            end_at=datetime(2026, 3, 1, 11, 0, tzinfo=UTC),
            timezone="UTC",
        )
        assert event.attendees == []


# ===========================================================================
# _attendee_info_list_to_google helper
# ===========================================================================


class TestAttendeeInfoListToGoogle:
    """Test _attendee_info_list_to_google serialization."""

    def test_simple_attendee_serialization(self):
        attendees = [AttendeeInfo(email="alice@example.com")]
        result = _attendee_info_list_to_google(attendees)
        assert result == [{"email": "alice@example.com"}]

    def test_optional_attendee_includes_optional_flag(self):
        attendees = [AttendeeInfo(email="alice@example.com", optional=True)]
        result = _attendee_info_list_to_google(attendees)
        assert result[0]["optional"] is True

    def test_non_optional_attendee_omits_optional_flag(self):
        attendees = [AttendeeInfo(email="alice@example.com", optional=False)]
        result = _attendee_info_list_to_google(attendees)
        assert "optional" not in result[0]

    def test_display_name_included_when_set(self):
        attendees = [AttendeeInfo(email="alice@example.com", display_name="Alice")]
        result = _attendee_info_list_to_google(attendees)
        assert result[0]["displayName"] == "Alice"

    def test_display_name_omitted_when_none(self):
        attendees = [AttendeeInfo(email="alice@example.com")]
        result = _attendee_info_list_to_google(attendees)
        assert "displayName" not in result[0]

    def test_response_status_not_included(self):
        """Response status is read-only, should not be sent to Google."""
        attendees = [
            AttendeeInfo(email="alice@example.com", response_status=AttendeeResponseStatus.accepted)
        ]
        result = _attendee_info_list_to_google(attendees)
        assert "responseStatus" not in result[0]

    def test_empty_list_returns_empty_list(self):
        assert _attendee_info_list_to_google([]) == []

    def test_multiple_attendees(self):
        attendees = [
            AttendeeInfo(email="alice@example.com"),
            AttendeeInfo(email="bob@example.com", display_name="Bob", optional=True),
        ]
        result = _attendee_info_list_to_google(attendees)
        assert len(result) == 2
        assert result[0] == {"email": "alice@example.com"}
        assert result[1] == {"email": "bob@example.com", "displayName": "Bob", "optional": True}


# ===========================================================================
# CalendarModule._event_to_payload attendee serialization
# ===========================================================================


class TestEventToPayloadAttendees:
    """Test that _event_to_payload serializes attendees as structured dicts."""

    def test_event_to_payload_with_attendees(self):
        event = CalendarEvent(
            event_id="evt-1",
            title="Meeting",
            start_at=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
            end_at=datetime(2026, 3, 1, 11, 0, tzinfo=UTC),
            timezone="UTC",
            attendees=[
                AttendeeInfo(
                    email="alice@example.com",
                    display_name="Alice",
                    response_status=AttendeeResponseStatus.accepted,
                    optional=False,
                    organizer=True,
                    self_=True,
                    comment="Will attend",
                )
            ],
        )
        payload = CalendarModule._event_to_payload(event)
        assert len(payload["attendees"]) == 1
        a = payload["attendees"][0]
        assert a["email"] == "alice@example.com"
        assert a["display_name"] == "Alice"
        assert a["response_status"] == "accepted"
        assert a["optional"] is False
        assert a["organizer"] is True
        assert a["self"] is True
        assert a["comment"] == "Will attend"

    def test_event_to_payload_empty_attendees(self):
        event = CalendarEvent(
            event_id="evt-1",
            title="Solo",
            start_at=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
            end_at=datetime(2026, 3, 1, 11, 0, tzinfo=UTC),
            timezone="UTC",
        )
        payload = CalendarModule._event_to_payload(event)
        assert payload["attendees"] == []

    def test_event_to_payload_needs_action_response_status(self):
        event = CalendarEvent(
            event_id="evt-1",
            title="Meeting",
            start_at=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
            end_at=datetime(2026, 3, 1, 11, 0, tzinfo=UTC),
            timezone="UTC",
            attendees=[AttendeeInfo(email="pending@example.com")],
        )
        payload = CalendarModule._event_to_payload(event)
        assert payload["attendees"][0]["response_status"] == "needsAction"


# ===========================================================================
# calendar_add_attendees MCP tool
# ===========================================================================


def _make_event(
    *,
    event_id: str = "evt-1",
    title: str = "BUTLER: Test Event",
    attendees: list[AttendeeInfo] | None = None,
) -> CalendarEvent:
    return CalendarEvent(
        event_id=event_id,
        title=title,
        start_at=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
        end_at=datetime(2026, 3, 1, 11, 0, tzinfo=UTC),
        timezone="UTC",
        attendees=attendees or [],
    )


def _make_module_with_mock_provider(
    mock_provider: MagicMock,
) -> CalendarModule:
    """Create a CalendarModule wired to a mock provider."""
    module = CalendarModule()
    module._config = CalendarConfig(provider="google", calendar_id="cal@example.com")
    module._provider = mock_provider
    return module


class TestCalendarAddAttendeesTool:
    """Test calendar_add_attendees MCP tool via register_tools."""

    @pytest.fixture
    def mock_provider(self):
        provider = MagicMock()
        provider.name = "google"
        provider.add_attendees = AsyncMock()
        return provider

    @pytest.fixture
    def module_and_tools(self, mock_provider):
        """Set up CalendarModule with registered tools."""
        module = _make_module_with_mock_provider(mock_provider)

        tools = {}

        class FakeMcp:
            def tool(self):
                def decorator(fn):
                    tools[fn.__name__] = fn
                    return fn

                return decorator

        import asyncio

        asyncio.get_event_loop().run_until_complete(
            module.register_tools(FakeMcp(), module._config, MagicMock())
        )
        return module, tools, mock_provider

    async def test_add_attendees_calls_provider(self, module_and_tools):
        module, tools, mock_provider = module_and_tools
        updated_event = _make_event(
            attendees=[
                AttendeeInfo(email="existing@example.com"),
                AttendeeInfo(email="new@example.com"),
            ]
        )
        mock_provider.add_attendees.return_value = updated_event

        result = await tools["calendar_add_attendees"](
            event_id="evt-1",
            attendees=["new@example.com"],
        )

        assert result["status"] == "updated"
        assert result["provider"] == "google"
        mock_provider.add_attendees.assert_called_once_with(
            calendar_id="cal@example.com",
            event_id="evt-1",
            attendees=["new@example.com"],
            optional=False,
            send_updates="none",
        )

    async def test_add_attendees_with_optional_flag(self, module_and_tools):
        module, tools, mock_provider = module_and_tools
        mock_provider.add_attendees.return_value = _make_event()

        await tools["calendar_add_attendees"](
            event_id="evt-1",
            attendees=["opt@example.com"],
            optional=True,
        )

        mock_provider.add_attendees.assert_called_once_with(
            calendar_id="cal@example.com",
            event_id="evt-1",
            attendees=["opt@example.com"],
            optional=True,
            send_updates="none",
        )

    async def test_add_attendees_with_send_updates_all(self, module_and_tools):
        module, tools, mock_provider = module_and_tools
        mock_provider.add_attendees.return_value = _make_event()

        await tools["calendar_add_attendees"](
            event_id="evt-1",
            attendees=["a@example.com"],
            send_updates=SendUpdatesPolicy.all,
        )

        mock_provider.add_attendees.assert_called_once_with(
            calendar_id="cal@example.com",
            event_id="evt-1",
            attendees=["a@example.com"],
            optional=False,
            send_updates="all",
        )

    async def test_add_attendees_empty_event_id_raises(self, module_and_tools):
        _, tools, _ = module_and_tools
        with pytest.raises(ValueError, match="event_id must be a non-empty string"):
            await tools["calendar_add_attendees"](
                event_id="   ",
                attendees=["a@example.com"],
            )

    async def test_add_attendees_empty_attendees_raises(self, module_and_tools):
        _, tools, _ = module_and_tools
        with pytest.raises(ValueError, match="at least one non-empty email"):
            await tools["calendar_add_attendees"](
                event_id="evt-1",
                attendees=["", "   "],
            )

    async def test_add_attendees_provider_error_returns_structured_error(self, module_and_tools):
        module, tools, mock_provider = module_and_tools
        mock_provider.add_attendees.side_effect = CalendarRequestError(
            status_code=403,
            message="Forbidden",
        )

        result = await tools["calendar_add_attendees"](
            event_id="evt-1",
            attendees=["a@example.com"],
        )

        assert result["status"] == "error"
        assert result["error_type"] == "CalendarRequestError"
        assert result["provider"] == "google"

    async def test_add_attendees_strips_whitespace_from_emails(self, module_and_tools):
        module, tools, mock_provider = module_and_tools
        mock_provider.add_attendees.return_value = _make_event()

        await tools["calendar_add_attendees"](
            event_id="evt-1",
            attendees=["  alice@example.com  ", "  bob@example.com  "],
        )

        mock_provider.add_attendees.assert_called_once_with(
            calendar_id="cal@example.com",
            event_id="evt-1",
            attendees=["alice@example.com", "bob@example.com"],
            optional=False,
            send_updates="none",
        )


# ===========================================================================
# calendar_remove_attendees MCP tool
# ===========================================================================


class TestCalendarRemoveAttendeesTool:
    """Test calendar_remove_attendees MCP tool via register_tools."""

    @pytest.fixture
    def mock_provider(self):
        provider = MagicMock()
        provider.name = "google"
        provider.remove_attendees = AsyncMock()
        return provider

    @pytest.fixture
    def module_and_tools(self, mock_provider):
        module = _make_module_with_mock_provider(mock_provider)

        tools = {}

        class FakeMcp:
            def tool(self):
                def decorator(fn):
                    tools[fn.__name__] = fn
                    return fn

                return decorator

        import asyncio

        asyncio.get_event_loop().run_until_complete(
            module.register_tools(FakeMcp(), module._config, MagicMock())
        )
        return module, tools, mock_provider

    async def test_remove_attendees_calls_provider(self, module_and_tools):
        module, tools, mock_provider = module_and_tools
        updated_event = _make_event(attendees=[AttendeeInfo(email="remaining@example.com")])
        mock_provider.remove_attendees.return_value = updated_event

        result = await tools["calendar_remove_attendees"](
            event_id="evt-1",
            attendees=["removed@example.com"],
        )

        assert result["status"] == "updated"
        assert result["provider"] == "google"
        mock_provider.remove_attendees.assert_called_once_with(
            calendar_id="cal@example.com",
            event_id="evt-1",
            attendees=["removed@example.com"],
            send_updates="none",
        )

    async def test_remove_attendees_with_send_updates_external_only(self, module_and_tools):
        module, tools, mock_provider = module_and_tools
        mock_provider.remove_attendees.return_value = _make_event()

        await tools["calendar_remove_attendees"](
            event_id="evt-1",
            attendees=["a@example.com"],
            send_updates=SendUpdatesPolicy.external_only,
        )

        mock_provider.remove_attendees.assert_called_once_with(
            calendar_id="cal@example.com",
            event_id="evt-1",
            attendees=["a@example.com"],
            send_updates="externalOnly",
        )

    async def test_remove_attendees_empty_event_id_raises(self, module_and_tools):
        _, tools, _ = module_and_tools
        with pytest.raises(ValueError, match="event_id must be a non-empty string"):
            await tools["calendar_remove_attendees"](
                event_id="",
                attendees=["a@example.com"],
            )

    async def test_remove_attendees_empty_attendees_raises(self, module_and_tools):
        _, tools, _ = module_and_tools
        with pytest.raises(ValueError, match="at least one non-empty email"):
            await tools["calendar_remove_attendees"](
                event_id="evt-1",
                attendees=[],
            )

    async def test_remove_attendees_provider_error_returns_structured_error(self, module_and_tools):
        module, tools, mock_provider = module_and_tools
        mock_provider.remove_attendees.side_effect = CalendarRequestError(
            status_code=404,
            message="Event not found",
        )

        result = await tools["calendar_remove_attendees"](
            event_id="evt-1",
            attendees=["a@example.com"],
        )

        assert result["status"] == "error"
        assert result["error_type"] == "CalendarRequestError"

    async def test_remove_attendees_strips_whitespace_from_emails(self, module_and_tools):
        module, tools, mock_provider = module_and_tools
        mock_provider.remove_attendees.return_value = _make_event()

        await tools["calendar_remove_attendees"](
            event_id="evt-1",
            attendees=["  alice@example.com  "],
        )

        mock_provider.remove_attendees.assert_called_once_with(
            calendar_id="cal@example.com",
            event_id="evt-1",
            attendees=["alice@example.com"],
            send_updates="none",
        )


# ===========================================================================
# _GoogleProvider.add_attendees / remove_attendees unit tests
# ===========================================================================


class TestGoogleProviderAddAttendees:
    """Test _GoogleProvider.add_attendees logic with a mock HTTP client."""

    @pytest.fixture
    def google_provider_and_http(self):
        from butlers.modules.calendar import _GoogleProvider

        mock_http = MagicMock()
        mock_http.aclose = AsyncMock()
        config = CalendarConfig(provider="google", calendar_id="cal@example.com")

        with patch.dict(os.environ, {"BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON": FAKE_CREDS}):
            provider = _GoogleProvider(config, http_client=mock_http)

        return provider, mock_http

    @pytest.fixture
    def mock_event_payload(self):
        return {
            "id": "evt-1",
            "summary": "BUTLER: Test Meeting",
            "start": {"dateTime": "2026-03-01T10:00:00Z", "timeZone": "UTC"},
            "end": {"dateTime": "2026-03-01T11:00:00Z", "timeZone": "UTC"},
            "attendees": [
                {"email": "existing@example.com", "responseStatus": "accepted"},
                {"email": "new@example.com", "responseStatus": "needsAction"},
            ],
        }

    async def test_add_attendees_deduplicates_existing_emails(
        self, google_provider_and_http, mock_event_payload
    ):
        """Adding an email that already exists should not duplicate it."""
        provider, mock_http = google_provider_and_http

        # Existing event has "existing@example.com"
        existing_payload = {
            "id": "evt-1",
            "summary": "Meeting",
            "start": {"dateTime": "2026-03-01T10:00:00Z", "timeZone": "UTC"},
            "end": {"dateTime": "2026-03-01T11:00:00Z", "timeZone": "UTC"},
            "attendees": [{"email": "existing@example.com", "responseStatus": "accepted"}],
        }
        # PATCH response includes both
        patch_response_payload = {
            "id": "evt-1",
            "summary": "Meeting",
            "start": {"dateTime": "2026-03-01T10:00:00Z", "timeZone": "UTC"},
            "end": {"dateTime": "2026-03-01T11:00:00Z", "timeZone": "UTC"},
            "attendees": [{"email": "existing@example.com", "responseStatus": "accepted"}],
        }

        get_resp = MagicMock()
        get_resp.status_code = 200
        get_resp.json.return_value = existing_payload

        patch_resp = MagicMock()
        patch_resp.status_code = 200
        patch_resp.json.return_value = patch_response_payload

        call_count = 0

        async def fake_request(method, url, **kwargs):
            nonlocal call_count
            call_count += 1
            if method == "GET":
                return get_resp
            return patch_resp

        mock_http.request = fake_request
        provider._oauth._access_token = "fake-token"
        provider._oauth._access_token_expires_at = datetime(2099, 1, 1, tzinfo=UTC)

        event = await provider.add_attendees(
            calendar_id="cal@example.com",
            event_id="evt-1",
            attendees=["existing@example.com"],  # already present
        )

        # PATCH should be called with only 1 attendee (no duplication)
        assert event.event_id == "evt-1"

    async def test_add_attendees_event_not_found_raises(self, google_provider_and_http):
        provider, mock_http = google_provider_and_http

        get_resp = MagicMock()
        get_resp.status_code = 404
        get_resp.json.return_value = {"error": {"message": "Not found"}}
        get_resp.text = "Not found"
        get_resp.headers = {}

        async def fake_request(method, url, **kwargs):
            return get_resp

        mock_http.request = fake_request
        provider._oauth._access_token = "fake-token"
        provider._oauth._access_token_expires_at = datetime(2099, 1, 1, tzinfo=UTC)

        with pytest.raises(CalendarRequestError, match="not found"):
            await provider.add_attendees(
                calendar_id="cal@example.com",
                event_id="evt-1",
                attendees=["new@example.com"],
            )

    async def test_add_attendees_empty_attendees_raises(self, google_provider_and_http):
        provider, mock_http = google_provider_and_http

        with pytest.raises(ValueError, match="at least one non-empty email"):
            await provider.add_attendees(
                calendar_id="cal@example.com",
                event_id="evt-1",
                attendees=["  ", ""],
            )

    async def test_add_attendees_empty_event_id_raises(self, google_provider_and_http):
        provider, mock_http = google_provider_and_http

        with pytest.raises(ValueError, match="event_id must be a non-empty string"):
            await provider.add_attendees(
                calendar_id="cal@example.com",
                event_id="",
                attendees=["a@example.com"],
            )


class TestGoogleProviderRemoveAttendees:
    """Test _GoogleProvider.remove_attendees logic with a mock HTTP client."""

    @pytest.fixture
    def google_provider(self):
        from butlers.modules.calendar import _GoogleProvider

        mock_http = MagicMock()
        mock_http.aclose = AsyncMock()
        config = CalendarConfig(provider="google", calendar_id="cal@example.com")

        with patch.dict(os.environ, {"BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON": FAKE_CREDS}):
            provider = _GoogleProvider(config, http_client=mock_http)
        provider._oauth._access_token = "fake-token"
        provider._oauth._access_token_expires_at = datetime(2099, 1, 1, tzinfo=UTC)
        return provider, mock_http

    async def test_remove_attendees_filters_by_email_case_insensitive(self, google_provider):
        provider, mock_http = google_provider

        existing_payload = {
            "id": "evt-1",
            "summary": "Meeting",
            "start": {"dateTime": "2026-03-01T10:00:00Z", "timeZone": "UTC"},
            "end": {"dateTime": "2026-03-01T11:00:00Z", "timeZone": "UTC"},
            "attendees": [
                {"email": "alice@example.com", "responseStatus": "accepted"},
                {"email": "Bob@Example.COM", "responseStatus": "needsAction"},
            ],
        }
        after_remove_payload = {
            "id": "evt-1",
            "summary": "Meeting",
            "start": {"dateTime": "2026-03-01T10:00:00Z", "timeZone": "UTC"},
            "end": {"dateTime": "2026-03-01T11:00:00Z", "timeZone": "UTC"},
            "attendees": [
                {"email": "alice@example.com", "responseStatus": "accepted"},
            ],
        }

        get_resp = MagicMock()
        get_resp.status_code = 200
        get_resp.json.return_value = existing_payload

        patch_resp = MagicMock()
        patch_resp.status_code = 200
        patch_resp.json.return_value = after_remove_payload

        call_count = 0

        async def fake_request(method, url, **kwargs):
            nonlocal call_count
            call_count += 1
            if method == "GET":
                return get_resp
            # Verify the PATCH body only contains alice
            body = kwargs.get("json", {})
            assert len(body.get("attendees", [])) == 1
            return patch_resp

        mock_http.request = fake_request

        event = await provider.remove_attendees(
            calendar_id="cal@example.com",
            event_id="evt-1",
            attendees=["bob@example.com"],  # lower-case, should match "Bob@Example.COM"
        )

        assert len(event.attendees) == 1
        assert event.attendees[0].email == "alice@example.com"

    async def test_remove_attendees_event_not_found_raises(self, google_provider):
        provider, mock_http = google_provider

        get_resp = MagicMock()
        get_resp.status_code = 404
        get_resp.json.return_value = {"error": {"message": "Not found"}}
        get_resp.text = "Not found"
        get_resp.headers = {}

        async def fake_request(method, url, **kwargs):
            return get_resp

        mock_http.request = fake_request

        with pytest.raises(CalendarRequestError, match="not found"):
            await provider.remove_attendees(
                calendar_id="cal@example.com",
                event_id="evt-1",
                attendees=["a@example.com"],
            )

    async def test_remove_attendees_empty_attendees_raises(self, google_provider):
        provider, _ = google_provider

        with pytest.raises(ValueError, match="at least one non-empty email"):
            await provider.remove_attendees(
                calendar_id="cal@example.com",
                event_id="evt-1",
                attendees=[],
            )

    async def test_remove_attendees_empty_event_id_raises(self, google_provider):
        provider, _ = google_provider

        with pytest.raises(ValueError, match="event_id must be a non-empty string"):
            await provider.remove_attendees(
                calendar_id="cal@example.com",
                event_id="   ",
                attendees=["a@example.com"],
            )
