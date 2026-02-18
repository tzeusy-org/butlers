"""Unit tests for _GoogleProvider.create_event().

Covers:
- Correct POST to Google Calendar API with title/summary
- BUTLER: prefix injection and extended properties
- All-day event boundaries (date objects -> "date" field)
- Timed event boundaries with timezone handling
- Attendee list serialization
- Recurrence rule passthrough
- Notification/reminder config (None, bool, int, CalendarNotificationInput)
- Color assignment
- Event status field
- Visibility field
- Notes stored in extended properties
- Provider response parsing back to CalendarEvent canonical shape
- Error handling when API returns non-2xx
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import httpx
import pytest

from butlers.modules.calendar import (
    BUTLER_GENERATED_PRIVATE_KEY,
    BUTLER_NAME_PRIVATE_KEY,
    GOOGLE_CALENDAR_API_BASE_URL,
    GOOGLE_CALENDAR_CREDENTIALS_ENV,
    GOOGLE_OAUTH_TOKEN_URL,
    CalendarConfig,
    CalendarEvent,
    CalendarEventCreate,
    CalendarNotificationInput,
    CalendarRequestError,
    _build_google_event_body,
    _GoogleProvider,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


def _make_creds_json() -> str:
    return json.dumps(
        {
            "client_id": "client-id",
            "client_secret": "client-secret",
            "refresh_token": "refresh-token",
        }
    )


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


def _make_google_event_response(
    *,
    event_id: str = "evt-created",
    summary: str = "BUTLER: Team Sync",
    start_datetime: str = "2026-02-20T14:00:00Z",
    end_datetime: str = "2026-02-20T15:00:00Z",
    timezone: str | None = None,
    description: str | None = None,
    location: str | None = None,
    attendees: list[dict] | None = None,
    recurrence: list[str] | None = None,
    color_id: str | None = None,
    extended_properties: dict | None = None,
    status: str = "confirmed",
) -> dict:
    """Build a minimal Google Calendar event API response."""
    payload: dict = {
        "id": event_id,
        "summary": summary,
        "status": status,
        "start": {"dateTime": start_datetime},
        "end": {"dateTime": end_datetime},
    }
    if timezone:
        payload["start"]["timeZone"] = timezone
        payload["end"]["timeZone"] = timezone
    if description is not None:
        payload["description"] = description
    if location is not None:
        payload["location"] = location
    if attendees is not None:
        payload["attendees"] = attendees
    if recurrence is not None:
        payload["recurrence"] = recurrence
    if color_id is not None:
        payload["colorId"] = color_id
    if extended_properties is not None:
        payload["extendedProperties"] = extended_properties
    return payload


def _make_provider(
    monkeypatch: pytest.MonkeyPatch,
    mock_client: AsyncMock,
    *,
    config: CalendarConfig | None = None,
) -> _GoogleProvider:
    monkeypatch.setenv(GOOGLE_CALENDAR_CREDENTIALS_ENV, _make_creds_json())
    mock_client.post.return_value = _mock_response(
        status_code=200,
        url=GOOGLE_OAUTH_TOKEN_URL,
        method="POST",
        json_body={"access_token": "access-token", "expires_in": 3600},
    )
    cfg = config or CalendarConfig(
        provider="google",
        calendar_id="primary",
        timezone="UTC",
    )
    return _GoogleProvider(config=cfg, http_client=mock_client)


# ---------------------------------------------------------------------------
# _build_google_event_body unit tests
# ---------------------------------------------------------------------------


class TestBuildGoogleEventBody:
    """Unit tests for the _build_google_event_body helper function."""

    def test_title_maps_to_summary(self):
        payload = CalendarEventCreate(
            title="BUTLER: Team Sync",
            start_at=datetime(2026, 2, 20, 14, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 15, 0, tzinfo=UTC),
        )
        body = _build_google_event_body(payload)
        assert body["summary"] == "BUTLER: Team Sync"

    def test_timed_event_with_timezone_uses_datetime_and_timezone(self):
        payload = CalendarEventCreate(
            title="Meeting",
            start_at=datetime(2026, 2, 20, 9, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 10, 0, tzinfo=UTC),
            timezone="America/New_York",
        )
        body = _build_google_event_body(payload)
        assert "dateTime" in body["start"]
        assert body["start"]["timeZone"] == "America/New_York"
        assert "dateTime" in body["end"]
        assert body["end"]["timeZone"] == "America/New_York"
        assert "date" not in body["start"]
        assert "date" not in body["end"]

    def test_timed_event_without_timezone_uses_rfc3339_utc(self):
        payload = CalendarEventCreate(
            title="Meeting",
            start_at=datetime(2026, 2, 20, 9, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 10, 0, tzinfo=UTC),
        )
        body = _build_google_event_body(payload)
        assert body["start"]["dateTime"] == "2026-02-20T09:00:00Z"
        assert body["end"]["dateTime"] == "2026-02-20T10:00:00Z"
        assert "timeZone" not in body["start"]
        assert "timeZone" not in body["end"]

    def test_naive_datetime_with_timezone_gets_tz_applied(self):
        payload = CalendarEventCreate(
            title="Meeting",
            start_at=datetime(2026, 2, 20, 9, 0),  # naive
            end_at=datetime(2026, 2, 20, 10, 0),  # naive
            timezone="America/New_York",
        )
        body = _build_google_event_body(payload)
        assert "dateTime" in body["start"]
        assert body["start"]["timeZone"] == "America/New_York"
        # The datetime should include the timezone offset
        assert "-05:00" in body["start"]["dateTime"] or "-04:00" in body["start"]["dateTime"]

    def test_all_day_event_uses_date_field(self):
        payload = CalendarEventCreate(
            title="Holiday",
            start_at=date(2026, 3, 1),
            end_at=date(2026, 3, 2),
            all_day=True,
        )
        body = _build_google_event_body(payload)
        assert body["start"] == {"date": "2026-03-01"}
        assert body["end"] == {"date": "2026-03-02"}
        assert "dateTime" not in body["start"]
        assert "dateTime" not in body["end"]

    def test_attendees_serialized_as_email_dicts(self):
        payload = CalendarEventCreate(
            title="Meeting",
            start_at=datetime(2026, 2, 20, 9, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 10, 0, tzinfo=UTC),
            attendees=["alice@example.com", "bob@example.com"],
        )
        body = _build_google_event_body(payload)
        assert body["attendees"] == [
            {"email": "alice@example.com"},
            {"email": "bob@example.com"},
        ]

    def test_empty_attendees_omits_field(self):
        payload = CalendarEventCreate(
            title="Meeting",
            start_at=datetime(2026, 2, 20, 9, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 10, 0, tzinfo=UTC),
        )
        body = _build_google_event_body(payload)
        assert "attendees" not in body

    def test_recurrence_rule_passed_through_as_list(self):
        payload = CalendarEventCreate(
            title="Weekly",
            start_at=datetime(2026, 2, 20, 9, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 10, 0, tzinfo=UTC),
            recurrence_rule="RRULE:FREQ=WEEKLY;BYDAY=MO",
        )
        body = _build_google_event_body(payload)
        assert body["recurrence"] == ["RRULE:FREQ=WEEKLY;BYDAY=MO"]

    def test_no_recurrence_rule_omits_field(self):
        payload = CalendarEventCreate(
            title="One-off",
            start_at=datetime(2026, 2, 20, 9, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 10, 0, tzinfo=UTC),
        )
        body = _build_google_event_body(payload)
        assert "recurrence" not in body

    def test_color_id_maps_to_color_id(self):
        payload = CalendarEventCreate(
            title="Colored",
            start_at=datetime(2026, 2, 20, 9, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 10, 0, tzinfo=UTC),
            color_id="7",
        )
        body = _build_google_event_body(payload)
        assert body["colorId"] == "7"

    def test_no_color_id_omits_field(self):
        payload = CalendarEventCreate(
            title="Plain",
            start_at=datetime(2026, 2, 20, 9, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 10, 0, tzinfo=UTC),
        )
        body = _build_google_event_body(payload)
        assert "colorId" not in body

    def test_status_confirmed_included_by_default(self):
        payload = CalendarEventCreate(
            title="Meeting",
            start_at=datetime(2026, 2, 20, 9, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 10, 0, tzinfo=UTC),
        )
        body = _build_google_event_body(payload)
        assert body["status"] == "confirmed"

    def test_status_tentative_passed_through(self):
        payload = CalendarEventCreate(
            title="Maybe",
            start_at=datetime(2026, 2, 20, 9, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 10, 0, tzinfo=UTC),
            status="tentative",
        )
        body = _build_google_event_body(payload)
        assert body["status"] == "tentative"

    def test_visibility_passed_through(self):
        payload = CalendarEventCreate(
            title="Private Meeting",
            start_at=datetime(2026, 2, 20, 9, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 10, 0, tzinfo=UTC),
            visibility="private",
        )
        body = _build_google_event_body(payload)
        assert body["visibility"] == "private"

    def test_no_visibility_omits_field(self):
        payload = CalendarEventCreate(
            title="Meeting",
            start_at=datetime(2026, 2, 20, 9, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 10, 0, tzinfo=UTC),
        )
        body = _build_google_event_body(payload)
        assert "visibility" not in body

    def test_description_included_when_set(self):
        payload = CalendarEventCreate(
            title="Meeting",
            start_at=datetime(2026, 2, 20, 9, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 10, 0, tzinfo=UTC),
            description="Agenda: review Q1 goals",
        )
        body = _build_google_event_body(payload)
        assert body["description"] == "Agenda: review Q1 goals"

    def test_location_included_when_set(self):
        payload = CalendarEventCreate(
            title="Meeting",
            start_at=datetime(2026, 2, 20, 9, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 10, 0, tzinfo=UTC),
            location="Room 5",
        )
        body = _build_google_event_body(payload)
        assert body["location"] == "Room 5"

    def test_notification_none_uses_default_reminders(self):
        payload = CalendarEventCreate(
            title="Meeting",
            start_at=datetime(2026, 2, 20, 9, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 10, 0, tzinfo=UTC),
            notification=None,
        )
        body = _build_google_event_body(payload)
        assert body["reminders"] == {"useDefault": True}

    def test_notification_false_disables_reminders(self):
        payload = CalendarEventCreate(
            title="Meeting",
            start_at=datetime(2026, 2, 20, 9, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 10, 0, tzinfo=UTC),
            notification=False,
        )
        body = _build_google_event_body(payload)
        assert body["reminders"] == {"useDefault": False, "overrides": []}

    def test_notification_true_uses_default_reminders(self):
        payload = CalendarEventCreate(
            title="Meeting",
            start_at=datetime(2026, 2, 20, 9, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 10, 0, tzinfo=UTC),
            notification=True,
        )
        body = _build_google_event_body(payload)
        assert body["reminders"] == {"useDefault": True}

    def test_notification_int_sets_popup_override(self):
        payload = CalendarEventCreate(
            title="Meeting",
            start_at=datetime(2026, 2, 20, 9, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 10, 0, tzinfo=UTC),
            notification=30,
        )
        body = _build_google_event_body(payload)
        assert body["reminders"] == {
            "useDefault": False,
            "overrides": [{"method": "popup", "minutes": 30}],
        }

    def test_notification_input_enabled_with_minutes_sets_popup_override(self):
        payload = CalendarEventCreate(
            title="Meeting",
            start_at=datetime(2026, 2, 20, 9, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 10, 0, tzinfo=UTC),
            notification=CalendarNotificationInput(enabled=True, minutes_before=10),
        )
        body = _build_google_event_body(payload)
        assert body["reminders"] == {
            "useDefault": False,
            "overrides": [{"method": "popup", "minutes": 10}],
        }

    def test_notification_input_disabled_clears_reminders(self):
        payload = CalendarEventCreate(
            title="Meeting",
            start_at=datetime(2026, 2, 20, 9, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 10, 0, tzinfo=UTC),
            notification=CalendarNotificationInput(enabled=False),
        )
        body = _build_google_event_body(payload)
        assert body["reminders"] == {"useDefault": False, "overrides": []}

    def test_private_metadata_stored_in_extended_properties(self):
        payload = CalendarEventCreate(
            title="BUTLER: Meeting",
            start_at=datetime(2026, 2, 20, 9, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 10, 0, tzinfo=UTC),
            private_metadata={
                BUTLER_GENERATED_PRIVATE_KEY: "true",
                BUTLER_NAME_PRIVATE_KEY: "general",
            },
        )
        body = _build_google_event_body(payload)
        assert "extendedProperties" in body
        private = body["extendedProperties"]["private"]
        assert private[BUTLER_GENERATED_PRIVATE_KEY] == "true"
        assert private[BUTLER_NAME_PRIVATE_KEY] == "general"

    def test_notes_stored_in_extended_properties_private(self):
        payload = CalendarEventCreate(
            title="Meeting",
            start_at=datetime(2026, 2, 20, 9, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 10, 0, tzinfo=UTC),
            notes="This is a butler-private note",
        )
        body = _build_google_event_body(payload)
        assert "extendedProperties" in body
        assert body["extendedProperties"]["private"]["notes"] == "This is a butler-private note"

    def test_notes_and_private_metadata_merged(self):
        payload = CalendarEventCreate(
            title="BUTLER: Meeting",
            start_at=datetime(2026, 2, 20, 9, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 10, 0, tzinfo=UTC),
            private_metadata={BUTLER_GENERATED_PRIVATE_KEY: "true"},
            notes="Private context",
        )
        body = _build_google_event_body(payload)
        private = body["extendedProperties"]["private"]
        assert private[BUTLER_GENERATED_PRIVATE_KEY] == "true"
        assert private["notes"] == "Private context"

    def test_no_private_metadata_or_notes_omits_extended_properties(self):
        payload = CalendarEventCreate(
            title="Meeting",
            start_at=datetime(2026, 2, 20, 9, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 10, 0, tzinfo=UTC),
        )
        body = _build_google_event_body(payload)
        assert "extendedProperties" not in body

    def test_all_day_event_inferred_from_date_boundary(self):
        """When all_day is None, date boundaries are inferred and use date fields."""
        payload = CalendarEventCreate(
            title="Vacation",
            start_at=date(2026, 7, 1),
            end_at=date(2026, 7, 5),
        )
        body = _build_google_event_body(payload)
        # all_day is None but start_at/end_at are date objects — inferred as all-day
        assert body["start"] == {"date": "2026-07-01"}
        assert body["end"] == {"date": "2026-07-05"}


# ---------------------------------------------------------------------------
# _GoogleProvider.create_event integration tests (mocked HTTP)
# ---------------------------------------------------------------------------


class TestGoogleProviderCreateEvent:
    """Test _GoogleProvider.create_event sends correct POST and parses response."""

    async def test_create_event_sends_post_to_correct_url(self, monkeypatch: pytest.MonkeyPatch):
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = _mock_response(
            status_code=200,
            url=GOOGLE_OAUTH_TOKEN_URL,
            method="POST",
            json_body={"access_token": "access-token", "expires_in": 3600},
        )
        event_response = _make_google_event_response(
            event_id="evt-1",
            summary="BUTLER: Team Sync",
            extended_properties={
                "private": {
                    BUTLER_GENERATED_PRIVATE_KEY: "true",
                    BUTLER_NAME_PRIVATE_KEY: "general",
                }
            },
        )
        mock_client.request.return_value = _mock_response(
            status_code=200,
            url=f"{GOOGLE_CALENDAR_API_BASE_URL}/calendars/primary/events",
            method="POST",
            json_body=event_response,
        )
        provider = _make_provider(monkeypatch, mock_client)

        payload = CalendarEventCreate(
            title="BUTLER: Team Sync",
            start_at=datetime(2026, 2, 20, 14, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 15, 0, tzinfo=UTC),
            private_metadata={
                BUTLER_GENERATED_PRIVATE_KEY: "true",
                BUTLER_NAME_PRIVATE_KEY: "general",
            },
        )
        await provider.create_event(calendar_id="primary", payload=payload)

        # Check request was a POST
        call_args = mock_client.request.call_args
        assert call_args.args[0] == "POST"
        assert "/calendars/primary/events" in call_args.args[1]

    async def test_create_event_returns_calendar_event_with_correct_fields(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = _mock_response(
            status_code=200,
            url=GOOGLE_OAUTH_TOKEN_URL,
            method="POST",
            json_body={"access_token": "access-token", "expires_in": 3600},
        )
        event_response = _make_google_event_response(
            event_id="evt-created",
            summary="BUTLER: Weekly Sync",
            start_datetime="2026-02-20T14:00:00Z",
            end_datetime="2026-02-20T15:00:00Z",
            description="Team planning session",
            location="Room A",
            color_id="5",
            attendees=[{"email": "alice@example.com"}],
            extended_properties={
                "private": {
                    BUTLER_GENERATED_PRIVATE_KEY: "true",
                    BUTLER_NAME_PRIVATE_KEY: "general",
                }
            },
        )
        mock_client.request.return_value = _mock_response(
            status_code=200,
            url=f"{GOOGLE_CALENDAR_API_BASE_URL}/calendars/primary/events",
            method="POST",
            json_body=event_response,
        )
        provider = _make_provider(monkeypatch, mock_client)

        payload = CalendarEventCreate(
            title="BUTLER: Weekly Sync",
            start_at=datetime(2026, 2, 20, 14, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 15, 0, tzinfo=UTC),
            description="Team planning session",
            location="Room A",
            color_id="5",
            attendees=["alice@example.com"],
            private_metadata={
                BUTLER_GENERATED_PRIVATE_KEY: "true",
                BUTLER_NAME_PRIVATE_KEY: "general",
            },
        )
        result = await provider.create_event(calendar_id="primary", payload=payload)

        assert isinstance(result, CalendarEvent)
        assert result.event_id == "evt-created"
        assert result.title == "BUTLER: Weekly Sync"
        assert result.description == "Team planning session"
        assert result.location == "Room A"
        assert result.color_id == "5"
        assert result.attendees == ["alice@example.com"]
        assert result.butler_generated is True
        assert result.butler_name == "general"

    async def test_create_event_sends_butler_extended_properties(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Verify the API request body includes butler extended properties."""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = _mock_response(
            status_code=200,
            url=GOOGLE_OAUTH_TOKEN_URL,
            method="POST",
            json_body={"access_token": "access-token", "expires_in": 3600},
        )
        event_response = _make_google_event_response(
            extended_properties={
                "private": {
                    BUTLER_GENERATED_PRIVATE_KEY: "true",
                    BUTLER_NAME_PRIVATE_KEY: "general",
                }
            }
        )
        mock_client.request.return_value = _mock_response(
            status_code=200,
            url=f"{GOOGLE_CALENDAR_API_BASE_URL}/calendars/primary/events",
            method="POST",
            json_body=event_response,
        )
        provider = _make_provider(monkeypatch, mock_client)

        payload = CalendarEventCreate(
            title="BUTLER: Team Sync",
            start_at=datetime(2026, 2, 20, 14, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 15, 0, tzinfo=UTC),
            private_metadata={
                BUTLER_GENERATED_PRIVATE_KEY: "true",
                BUTLER_NAME_PRIVATE_KEY: "general",
            },
        )
        await provider.create_event(calendar_id="primary", payload=payload)

        call_kwargs = mock_client.request.call_args.kwargs
        body = call_kwargs["json"]
        assert "extendedProperties" in body
        private = body["extendedProperties"]["private"]
        assert private[BUTLER_GENERATED_PRIVATE_KEY] == "true"
        assert private[BUTLER_NAME_PRIVATE_KEY] == "general"

    async def test_create_event_with_recurrence_rule(self, monkeypatch: pytest.MonkeyPatch):
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = _mock_response(
            status_code=200,
            url=GOOGLE_OAUTH_TOKEN_URL,
            method="POST",
            json_body={"access_token": "access-token", "expires_in": 3600},
        )
        event_response = _make_google_event_response(
            recurrence=["RRULE:FREQ=WEEKLY;BYDAY=MO"],
        )
        mock_client.request.return_value = _mock_response(
            status_code=200,
            url=f"{GOOGLE_CALENDAR_API_BASE_URL}/calendars/primary/events",
            method="POST",
            json_body=event_response,
        )
        provider = _make_provider(monkeypatch, mock_client)

        payload = CalendarEventCreate(
            title="Weekly Standup",
            start_at=datetime(2026, 2, 20, 9, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 9, 30, tzinfo=UTC),
            recurrence_rule="RRULE:FREQ=WEEKLY;BYDAY=MO",
        )
        await provider.create_event(calendar_id="primary", payload=payload)

        call_kwargs = mock_client.request.call_args.kwargs
        body = call_kwargs["json"]
        assert body["recurrence"] == ["RRULE:FREQ=WEEKLY;BYDAY=MO"]

    async def test_create_event_with_timezone_sets_datetime_and_timezone(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = _mock_response(
            status_code=200,
            url=GOOGLE_OAUTH_TOKEN_URL,
            method="POST",
            json_body={"access_token": "access-token", "expires_in": 3600},
        )
        event_response = _make_google_event_response(
            start_datetime="2026-02-20T09:00:00-05:00",
            end_datetime="2026-02-20T10:00:00-05:00",
            timezone="America/New_York",
        )
        mock_client.request.return_value = _mock_response(
            status_code=200,
            url=f"{GOOGLE_CALENDAR_API_BASE_URL}/calendars/primary/events",
            method="POST",
            json_body=event_response,
        )
        provider = _make_provider(monkeypatch, mock_client)

        payload = CalendarEventCreate(
            title="Morning Meeting",
            start_at=datetime(2026, 2, 20, 9, 0, tzinfo=ZoneInfo("America/New_York")),
            end_at=datetime(2026, 2, 20, 10, 0, tzinfo=ZoneInfo("America/New_York")),
            timezone="America/New_York",
        )
        await provider.create_event(calendar_id="primary", payload=payload)

        call_kwargs = mock_client.request.call_args.kwargs
        body = call_kwargs["json"]
        assert body["start"]["timeZone"] == "America/New_York"
        assert body["end"]["timeZone"] == "America/New_York"
        assert "dateTime" in body["start"]
        assert "dateTime" in body["end"]

    async def test_create_event_with_all_day_uses_date_fields(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = _mock_response(
            status_code=200,
            url=GOOGLE_OAUTH_TOKEN_URL,
            method="POST",
            json_body={"access_token": "access-token", "expires_in": 3600},
        )
        # All-day event response has date fields
        event_response = {
            "id": "evt-holiday",
            "summary": "Holiday",
            "status": "confirmed",
            "start": {"date": "2026-03-01"},
            "end": {"date": "2026-03-02"},
        }
        mock_client.request.return_value = _mock_response(
            status_code=200,
            url=f"{GOOGLE_CALENDAR_API_BASE_URL}/calendars/primary/events",
            method="POST",
            json_body=event_response,
        )
        provider = _make_provider(monkeypatch, mock_client)

        payload = CalendarEventCreate(
            title="Holiday",
            start_at=date(2026, 3, 1),
            end_at=date(2026, 3, 2),
            all_day=True,
        )
        result = await provider.create_event(calendar_id="primary", payload=payload)

        call_kwargs = mock_client.request.call_args.kwargs
        body = call_kwargs["json"]
        assert body["start"] == {"date": "2026-03-01"}
        assert body["end"] == {"date": "2026-03-02"}

        # Response is parsed correctly from date-format event
        assert isinstance(result, CalendarEvent)
        assert result.event_id == "evt-holiday"
        assert result.title == "Holiday"

    async def test_create_event_with_notification_int_sets_popup_reminder(
        self, monkeypatch: pytest.MonkeyPatch
    ):
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
            method="POST",
            json_body=_make_google_event_response(),
        )
        provider = _make_provider(monkeypatch, mock_client)

        payload = CalendarEventCreate(
            title="Meeting",
            start_at=datetime(2026, 2, 20, 14, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 15, 0, tzinfo=UTC),
            notification=15,
        )
        await provider.create_event(calendar_id="primary", payload=payload)

        call_kwargs = mock_client.request.call_args.kwargs
        body = call_kwargs["json"]
        assert body["reminders"] == {
            "useDefault": False,
            "overrides": [{"method": "popup", "minutes": 15}],
        }

    async def test_create_event_with_color_id(self, monkeypatch: pytest.MonkeyPatch):
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
            method="POST",
            json_body=_make_google_event_response(color_id="9"),
        )
        provider = _make_provider(monkeypatch, mock_client)

        payload = CalendarEventCreate(
            title="Colored Event",
            start_at=datetime(2026, 2, 20, 14, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 15, 0, tzinfo=UTC),
            color_id="9",
        )
        result = await provider.create_event(calendar_id="primary", payload=payload)

        call_kwargs = mock_client.request.call_args.kwargs
        body = call_kwargs["json"]
        assert body["colorId"] == "9"
        assert result.color_id == "9"

    async def test_create_event_with_status_tentative(self, monkeypatch: pytest.MonkeyPatch):
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
            method="POST",
            json_body=_make_google_event_response(status="tentative"),
        )
        provider = _make_provider(monkeypatch, mock_client)

        payload = CalendarEventCreate(
            title="Maybe Meeting",
            start_at=datetime(2026, 2, 20, 14, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 15, 0, tzinfo=UTC),
            status="tentative",
        )
        await provider.create_event(calendar_id="primary", payload=payload)

        call_kwargs = mock_client.request.call_args.kwargs
        body = call_kwargs["json"]
        assert body["status"] == "tentative"

    async def test_create_event_api_error_raises_calendar_request_error(
        self, monkeypatch: pytest.MonkeyPatch
    ):
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
            method="POST",
            json_body={"error": {"message": "Insufficient permissions"}},
        )
        provider = _make_provider(monkeypatch, mock_client)

        payload = CalendarEventCreate(
            title="Blocked Meeting",
            start_at=datetime(2026, 2, 20, 14, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 15, 0, tzinfo=UTC),
        )
        with pytest.raises(CalendarRequestError) as excinfo:
            await provider.create_event(calendar_id="primary", payload=payload)

        assert excinfo.value.status_code == 403
        assert "Insufficient permissions" in excinfo.value.message

    async def test_create_event_url_encodes_calendar_id_with_special_chars(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = _mock_response(
            status_code=200,
            url=GOOGLE_OAUTH_TOKEN_URL,
            method="POST",
            json_body={"access_token": "access-token", "expires_in": 3600},
        )
        mock_client.request.return_value = _mock_response(
            status_code=200,
            url=f"{GOOGLE_CALENDAR_API_BASE_URL}/calendars/butler%40group.calendar.google.com/events",
            method="POST",
            json_body=_make_google_event_response(),
        )
        provider = _make_provider(monkeypatch, mock_client)

        payload = CalendarEventCreate(
            title="Meeting",
            start_at=datetime(2026, 2, 20, 14, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 15, 0, tzinfo=UTC),
        )
        await provider.create_event(
            calendar_id="butler@group.calendar.google.com",
            payload=payload,
        )

        call_args = mock_client.request.call_args
        url = call_args.args[1]
        assert "butler%40group.calendar.google.com" in url

    async def test_create_event_sends_bearer_token(self, monkeypatch: pytest.MonkeyPatch):
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        # _make_provider sets access_token to "access-token"
        mock_client.post.return_value = _mock_response(
            status_code=200,
            url=GOOGLE_OAUTH_TOKEN_URL,
            method="POST",
            json_body={"access_token": "access-token", "expires_in": 3600},
        )
        mock_client.request.return_value = _mock_response(
            status_code=200,
            url=f"{GOOGLE_CALENDAR_API_BASE_URL}/calendars/primary/events",
            method="POST",
            json_body=_make_google_event_response(),
        )
        provider = _make_provider(monkeypatch, mock_client)

        payload = CalendarEventCreate(
            title="Meeting",
            start_at=datetime(2026, 2, 20, 14, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 15, 0, tzinfo=UTC),
        )
        await provider.create_event(calendar_id="primary", payload=payload)

        call_kwargs = mock_client.request.call_args.kwargs
        assert call_kwargs["headers"]["Authorization"] == "Bearer access-token"

    async def test_create_event_with_notes_stored_in_extended_properties(
        self, monkeypatch: pytest.MonkeyPatch
    ):
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
            method="POST",
            json_body=_make_google_event_response(),
        )
        provider = _make_provider(monkeypatch, mock_client)

        payload = CalendarEventCreate(
            title="Meeting",
            start_at=datetime(2026, 2, 20, 14, 0, tzinfo=UTC),
            end_at=datetime(2026, 2, 20, 15, 0, tzinfo=UTC),
            notes="Internal butler reasoning: user asked to schedule this",
        )
        await provider.create_event(calendar_id="primary", payload=payload)

        call_kwargs = mock_client.request.call_args.kwargs
        body = call_kwargs["json"]
        assert "extendedProperties" in body
        assert body["extendedProperties"]["private"]["notes"] == (
            "Internal butler reasoning: user asked to schedule this"
        )
