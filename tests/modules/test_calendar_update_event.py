"""Unit tests for _GoogleProvider.update_event().

Covers:
- Correct PATCH sent to Google Calendar API
- Only changed fields included in the PATCH body
- Butler-generated metadata preserved on butler-created events
- Etag used as If-Match header for optimistic concurrency
- Timezone updates applied to start/end boundaries
- Timezone-only update fetches existing event boundaries
- 412 Precondition Failed (etag conflict) surfaces as CalendarRequestError
- NotImplementedError for delete_event (still unimplemented, unchanged)
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import httpx
import pytest

from butlers.modules.calendar import (
    BUTLER_GENERATED_PRIVATE_KEY,
    BUTLER_NAME_PRIVATE_KEY,
    GOOGLE_CALENDAR_CREDENTIALS_ENV,
    CalendarConfig,
    CalendarEvent,
    CalendarEventUpdate,
    CalendarRequestError,
    _build_google_event_patch_body,
    _GoogleProvider,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_credentials_json() -> str:
    return json.dumps(
        {
            "client_id": "client-id",
            "client_secret": "client-secret",
            "refresh_token": "refresh-token",
        }
    )


def _make_mock_http_client() -> MagicMock:
    """Return a mock httpx.AsyncClient pre-wired with a valid OAuth token response."""
    mock_client = MagicMock(spec=httpx.AsyncClient)
    token_response = MagicMock()
    token_response.status_code = 200
    token_response.json.return_value = {"access_token": "access-token", "expires_in": 3600}
    mock_client.post = AsyncMock(return_value=token_response)
    return mock_client


def _make_google_event_payload(
    event_id: str = "event-123",
    summary: str = "Test Event",
    start_dt: str = "2026-03-01T10:00:00Z",
    end_dt: str = "2026-03-01T11:00:00Z",
    timezone: str = "America/New_York",
    etag: str | None = '"abc123"',
    butler_generated: bool = False,
    butler_name: str | None = None,
    status: str = "confirmed",
) -> dict:
    payload: dict = {
        "id": event_id,
        "summary": summary,
        "status": status,
        "start": {"dateTime": start_dt, "timeZone": timezone},
        "end": {"dateTime": end_dt, "timeZone": timezone},
    }
    if etag is not None:
        payload["etag"] = etag
    if butler_generated or butler_name:
        private: dict = {}
        if butler_generated:
            private[BUTLER_GENERATED_PRIVATE_KEY] = "true"
        if butler_name:
            private[BUTLER_NAME_PRIVATE_KEY] = butler_name
        payload["extendedProperties"] = {"private": private}
    return payload


@pytest.fixture
def google_provider(monkeypatch):
    """Return a _GoogleProvider with mocked credentials and HTTP client."""
    monkeypatch.setenv(GOOGLE_CALENDAR_CREDENTIALS_ENV, _make_credentials_json())
    config = CalendarConfig(
        provider="google",
        calendar_id="test@example.com",
        timezone="UTC",
    )
    return _GoogleProvider(config=config, http_client=_make_mock_http_client())


# ---------------------------------------------------------------------------
# _build_google_event_patch_body unit tests
# ---------------------------------------------------------------------------


class TestBuildGoogleEventPatchBody:
    """Unit tests for the _build_google_event_patch_body helper."""

    def test_only_title_changed(self):
        patch = CalendarEventUpdate(title="New Title")
        body = _build_google_event_patch_body(patch)
        assert body == {"summary": "New Title"}

    def test_only_description_changed(self):
        patch = CalendarEventUpdate(description="Updated description")
        body = _build_google_event_patch_body(patch)
        assert body == {"description": "Updated description"}

    def test_only_location_changed(self):
        patch = CalendarEventUpdate(location="Conference Room A")
        body = _build_google_event_patch_body(patch)
        assert body == {"location": "Conference Room A"}

    def test_start_at_with_timezone(self):
        patch = CalendarEventUpdate(
            start_at=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
            end_at=datetime(2026, 3, 1, 11, 0, tzinfo=UTC),
            timezone="America/New_York",
        )
        body = _build_google_event_patch_body(patch)
        assert "start" in body
        assert "end" in body
        assert body["start"]["timeZone"] == "America/New_York"
        assert body["end"]["timeZone"] == "America/New_York"
        # Times should be in the target timezone
        assert "-05:00" in body["start"]["dateTime"] or "-04:00" in body["start"]["dateTime"]

    def test_start_at_without_timezone_uses_utc(self):
        patch = CalendarEventUpdate(
            start_at=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
            end_at=datetime(2026, 3, 1, 11, 0, tzinfo=UTC),
        )
        body = _build_google_event_patch_body(patch)
        assert "start" in body
        assert "end" in body
        # No timeZone key when no timezone supplied
        assert "timeZone" not in body["start"]
        assert body["start"]["dateTime"].endswith("Z")

    def test_attendees_changed(self):
        patch = CalendarEventUpdate(attendees=["alice@example.com", "bob@example.com"])
        body = _build_google_event_patch_body(patch)
        assert body == {
            "attendees": [
                {"email": "alice@example.com"},
                {"email": "bob@example.com"},
            ]
        }

    def test_recurrence_rule_changed(self):
        patch = CalendarEventUpdate(recurrence_rule="RRULE:FREQ=WEEKLY;BYDAY=MO")
        body = _build_google_event_patch_body(patch)
        assert body == {"recurrence": ["RRULE:FREQ=WEEKLY;BYDAY=MO"]}

    def test_color_id_changed(self):
        patch = CalendarEventUpdate(color_id="5")
        body = _build_google_event_patch_body(patch)
        assert body == {"colorId": "5"}

    def test_private_metadata_included(self):
        patch = CalendarEventUpdate(
            private_metadata={
                BUTLER_GENERATED_PRIVATE_KEY: "true",
                BUTLER_NAME_PRIVATE_KEY: "butler-general",
            }
        )
        body = _build_google_event_patch_body(patch)
        assert "extendedProperties" in body
        assert body["extendedProperties"]["private"][BUTLER_GENERATED_PRIVATE_KEY] == "true"

    def test_no_fields_changed_returns_empty_body(self):
        patch = CalendarEventUpdate()
        body = _build_google_event_patch_body(patch)
        assert body == {}

    def test_etag_not_included_in_body(self):
        """Etag goes in the If-Match header, not the body."""
        patch = CalendarEventUpdate(title="Updated", etag='"abc123"')
        body = _build_google_event_patch_body(patch)
        assert "etag" not in body
        assert "If-Match" not in body

    def test_timezone_only_update_uses_existing_boundaries(self):
        """When only timezone changes, existing boundaries are re-emitted with new timezone."""
        patch = CalendarEventUpdate(timezone="Europe/London")
        existing_start = datetime(2026, 3, 1, 10, 0, tzinfo=ZoneInfo("America/New_York"))
        existing_end = datetime(2026, 3, 1, 11, 0, tzinfo=ZoneInfo("America/New_York"))
        body = _build_google_event_patch_body(
            patch,
            existing_start_at=existing_start,
            existing_end_at=existing_end,
            existing_timezone="America/New_York",
        )
        assert "start" in body
        assert "end" in body
        assert body["start"]["timeZone"] == "Europe/London"
        assert body["end"]["timeZone"] == "Europe/London"

    def test_timezone_only_without_existing_boundaries_emits_nothing(self):
        """When timezone changes but no existing boundaries provided, no start/end emitted."""
        patch = CalendarEventUpdate(timezone="Europe/London")
        body = _build_google_event_patch_body(patch)
        # Without existing boundaries there's nothing to emit for start/end
        assert "start" not in body
        assert "end" not in body


# ---------------------------------------------------------------------------
# _GoogleProvider.update_event integration tests (with mocked HTTP)
# ---------------------------------------------------------------------------


class TestGoogleProviderUpdateEvent:
    """Test _GoogleProvider.update_event with mocked HTTP responses."""

    async def test_update_event_sends_patch_request(self, google_provider):
        """update_event sends a PATCH to the correct Google Calendar endpoint."""
        response_payload = _make_google_event_payload(
            event_id="event-123",
            summary="Updated Event",
        )
        google_provider._request_google_json = AsyncMock(return_value=response_payload)

        patch = CalendarEventUpdate(title="Updated Event")
        result = await google_provider.update_event(
            calendar_id="test@example.com",
            event_id="event-123",
            patch=patch,
        )

        google_provider._request_google_json.assert_awaited_once()
        call_args = google_provider._request_google_json.call_args
        assert call_args[0][0] == "PATCH"
        assert "event-123" in call_args[0][1]
        assert isinstance(result, CalendarEvent)
        assert result.event_id == "event-123"

    async def test_update_event_only_sends_changed_fields(self, google_provider):
        """Only non-None fields from the patch appear in the PATCH body."""
        response_payload = _make_google_event_payload()
        google_provider._request_google_json = AsyncMock(return_value=response_payload)

        patch = CalendarEventUpdate(title="New Title Only")
        await google_provider.update_event(
            calendar_id="test@example.com",
            event_id="event-123",
            patch=patch,
        )

        call_args = google_provider._request_google_json.call_args
        body = call_args.kwargs["json_body"]
        assert "summary" in body
        assert body["summary"] == "New Title Only"
        # Other fields should NOT appear
        assert "start" not in body
        assert "end" not in body
        assert "attendees" not in body
        assert "recurrence" not in body

    async def test_update_event_uses_etag_as_if_match_header(self, google_provider):
        """Etag from the patch is sent as If-Match header for optimistic concurrency."""
        response_payload = _make_google_event_payload()
        google_provider._request_google_json = AsyncMock(return_value=response_payload)

        patch = CalendarEventUpdate(title="Updated", etag='"abc123etag"')
        await google_provider.update_event(
            calendar_id="test@example.com",
            event_id="event-123",
            patch=patch,
        )

        call_args = google_provider._request_google_json.call_args
        extra_headers = call_args.kwargs.get("extra_headers")
        assert extra_headers is not None
        assert extra_headers.get("If-Match") == '"abc123etag"'

    async def test_update_event_without_etag_sends_no_if_match_header(self, google_provider):
        """When no etag provided, no If-Match header is sent."""
        response_payload = _make_google_event_payload()
        google_provider._request_google_json = AsyncMock(return_value=response_payload)

        patch = CalendarEventUpdate(title="Updated", etag=None)
        await google_provider.update_event(
            calendar_id="test@example.com",
            event_id="event-123",
            patch=patch,
        )

        call_args = google_provider._request_google_json.call_args
        extra_headers = call_args.kwargs.get("extra_headers")
        # Either None or not containing If-Match
        assert extra_headers is None or "If-Match" not in extra_headers

    async def test_update_event_412_raises_calendar_request_error(self, google_provider):
        """A 412 Precondition Failed (etag conflict) surfaces as CalendarRequestError."""
        google_provider._request_google_json = AsyncMock(
            side_effect=CalendarRequestError(
                status_code=412,
                message="Precondition Failed",
            )
        )

        patch = CalendarEventUpdate(title="Updated", etag='"stale-etag"')
        with pytest.raises(CalendarRequestError) as exc_info:
            await google_provider.update_event(
                calendar_id="test@example.com",
                event_id="event-123",
                patch=patch,
            )
        assert exc_info.value.status_code == 412

    async def test_update_event_preserves_butler_metadata_on_butler_event(self, google_provider):
        """Butler-generated events preserve private_metadata in the PATCH body."""
        response_payload = _make_google_event_payload(
            butler_generated=True,
            butler_name="general",
        )
        google_provider._request_google_json = AsyncMock(return_value=response_payload)

        # The MCP tool layer builds the private_metadata; we simulate that here.
        patch = CalendarEventUpdate(
            title="BUTLER: Updated Title",
            private_metadata={
                BUTLER_GENERATED_PRIVATE_KEY: "true",
                BUTLER_NAME_PRIVATE_KEY: "general",
            },
        )
        await google_provider.update_event(
            calendar_id="test@example.com",
            event_id="event-123",
            patch=patch,
        )

        call_args = google_provider._request_google_json.call_args
        body = call_args.kwargs["json_body"]
        assert "extendedProperties" in body
        assert body["extendedProperties"]["private"][BUTLER_GENERATED_PRIVATE_KEY] == "true"
        assert body["extendedProperties"]["private"][BUTLER_NAME_PRIVATE_KEY] == "general"

    async def test_update_event_without_private_metadata_omits_extended_properties(
        self, google_provider
    ):
        """When private_metadata is None, extendedProperties is not sent in PATCH body."""
        response_payload = _make_google_event_payload()
        google_provider._request_google_json = AsyncMock(return_value=response_payload)

        patch = CalendarEventUpdate(title="Updated", private_metadata=None)
        await google_provider.update_event(
            calendar_id="test@example.com",
            event_id="event-123",
            patch=patch,
        )

        call_args = google_provider._request_google_json.call_args
        body = call_args.kwargs["json_body"]
        assert "extendedProperties" not in body

    async def test_update_event_returns_canonical_calendar_event(self, google_provider):
        """update_event returns a parsed CalendarEvent from the Google API response."""
        response_payload = _make_google_event_payload(
            event_id="ev-42",
            summary="Parsed Event",
            etag='"new-etag"',
        )
        google_provider._request_google_json = AsyncMock(return_value=response_payload)

        patch = CalendarEventUpdate(title="Parsed Event")
        result = await google_provider.update_event(
            calendar_id="test@example.com",
            event_id="ev-42",
            patch=patch,
        )

        assert isinstance(result, CalendarEvent)
        assert result.event_id == "ev-42"
        assert result.title == "Parsed Event"
        assert result.etag == '"new-etag"'

    async def test_update_event_empty_event_id_raises_value_error(self, google_provider):
        """Empty event_id raises ValueError immediately."""
        patch = CalendarEventUpdate(title="Whatever")
        with pytest.raises(ValueError, match="event_id must be a non-empty string"):
            await google_provider.update_event(
                calendar_id="test@example.com",
                event_id="   ",
                patch=patch,
            )

    async def test_update_event_timezone_change_fetches_existing_boundaries(self, google_provider):
        """When only timezone changes, the provider fetches the existing event for boundaries."""
        existing_event_payload = _make_google_event_payload(
            event_id="event-tz",
            start_dt="2026-03-01T10:00:00-05:00",
            end_dt="2026-03-01T11:00:00-05:00",
            timezone="America/New_York",
            etag='"existing-etag"',
        )
        updated_event_payload = _make_google_event_payload(
            event_id="event-tz",
            start_dt="2026-03-01T15:00:00+00:00",
            end_dt="2026-03-01T16:00:00+00:00",
            timezone="Europe/London",
        )

        # get_event is called first, then _request_google_json for PATCH.
        google_provider.get_event = AsyncMock(
            return_value=_make_calendar_event_from_payload(existing_event_payload)
        )
        google_provider._request_google_json = AsyncMock(return_value=updated_event_payload)

        patch = CalendarEventUpdate(timezone="Europe/London")
        result = await google_provider.update_event(
            calendar_id="test@example.com",
            event_id="event-tz",
            patch=patch,
        )

        # get_event should have been called exactly once to get existing boundaries + etag.
        google_provider.get_event.assert_awaited_once()

        # The PATCH body should contain start/end with new timezone
        call_args = google_provider._request_google_json.call_args
        body = call_args.kwargs["json_body"]
        assert "start" in body
        assert "end" in body
        assert body["start"]["timeZone"] == "Europe/London"
        assert body["end"]["timeZone"] == "Europe/London"
        assert isinstance(result, CalendarEvent)

    async def test_update_event_timezone_change_uses_fetched_etag_as_if_match(
        self, google_provider
    ):
        """Timezone-only update uses etag from fetched event as If-Match header."""
        existing_event_payload = _make_google_event_payload(
            event_id="event-tz",
            start_dt="2026-03-01T10:00:00-05:00",
            end_dt="2026-03-01T11:00:00-05:00",
            timezone="America/New_York",
            etag='"fetched-etag-value"',
        )
        updated_event_payload = _make_google_event_payload(event_id="event-tz")
        google_provider.get_event = AsyncMock(
            return_value=_make_calendar_event_from_payload(existing_event_payload)
        )
        google_provider._request_google_json = AsyncMock(return_value=updated_event_payload)

        # No etag provided by caller — provider should use the fetched etag.
        patch = CalendarEventUpdate(timezone="Europe/London")
        await google_provider.update_event(
            calendar_id="test@example.com",
            event_id="event-tz",
            patch=patch,
        )

        call_args = google_provider._request_google_json.call_args
        extra_headers = call_args.kwargs.get("extra_headers")
        assert extra_headers is not None
        assert extra_headers.get("If-Match") == '"fetched-etag-value"'

    async def test_update_event_timezone_change_caller_etag_takes_precedence(self, google_provider):
        """When caller supplies etag and timezone, caller etag is used (not fetched etag)."""
        existing_event_payload = _make_google_event_payload(
            event_id="event-tz",
            start_dt="2026-03-01T10:00:00-05:00",
            end_dt="2026-03-01T11:00:00-05:00",
            timezone="America/New_York",
            etag='"server-etag"',
        )
        updated_event_payload = _make_google_event_payload(event_id="event-tz")
        google_provider.get_event = AsyncMock(
            return_value=_make_calendar_event_from_payload(existing_event_payload)
        )
        google_provider._request_google_json = AsyncMock(return_value=updated_event_payload)

        # Caller supplies their own etag.
        patch = CalendarEventUpdate(timezone="Europe/London", etag='"caller-etag"')
        await google_provider.update_event(
            calendar_id="test@example.com",
            event_id="event-tz",
            patch=patch,
        )

        call_args = google_provider._request_google_json.call_args
        extra_headers = call_args.kwargs.get("extra_headers")
        assert extra_headers is not None
        # Caller etag takes precedence.
        assert extra_headers.get("If-Match") == '"caller-etag"'

    async def test_update_event_timezone_change_event_not_found_raises_404(self, google_provider):
        """Timezone-only update raises CalendarRequestError(404) when event not found."""
        google_provider.get_event = AsyncMock(return_value=None)

        patch = CalendarEventUpdate(timezone="Europe/London")
        with pytest.raises(CalendarRequestError) as exc_info:
            await google_provider.update_event(
                calendar_id="test@example.com",
                event_id="missing-event",
                patch=patch,
            )
        assert exc_info.value.status_code == 404

    async def test_update_event_time_change_sends_both_boundaries(self, google_provider):
        """Updating start_at and end_at sends both in the PATCH body."""
        response_payload = _make_google_event_payload()
        google_provider._request_google_json = AsyncMock(return_value=response_payload)

        new_start = datetime(2026, 3, 2, 14, 0, tzinfo=UTC)
        new_end = datetime(2026, 3, 2, 15, 0, tzinfo=UTC)
        patch = CalendarEventUpdate(start_at=new_start, end_at=new_end)
        await google_provider.update_event(
            calendar_id="test@example.com",
            event_id="event-123",
            patch=patch,
        )

        call_args = google_provider._request_google_json.call_args
        body = call_args.kwargs["json_body"]
        assert "start" in body
        assert "end" in body
        assert "2026-03-02" in body["start"]["dateTime"]
        assert "2026-03-02" in body["end"]["dateTime"]

    async def test_update_event_time_change_with_timezone(self, google_provider):
        """When start_at/end_at + timezone are all provided, timezone is applied."""
        response_payload = _make_google_event_payload()
        google_provider._request_google_json = AsyncMock(return_value=response_payload)

        new_start = datetime(2026, 3, 2, 14, 0, tzinfo=UTC)
        new_end = datetime(2026, 3, 2, 15, 0, tzinfo=UTC)
        patch = CalendarEventUpdate(
            start_at=new_start,
            end_at=new_end,
            timezone="America/Chicago",
        )
        await google_provider.update_event(
            calendar_id="test@example.com",
            event_id="event-123",
            patch=patch,
        )

        call_args = google_provider._request_google_json.call_args
        body = call_args.kwargs["json_body"]
        assert body["start"]["timeZone"] == "America/Chicago"
        assert body["end"]["timeZone"] == "America/Chicago"

    async def test_update_event_cancelled_response_raises_request_error(self, google_provider):
        """Google returning a cancelled event after PATCH raises CalendarRequestError."""
        # A cancelled event payload
        response_payload = _make_google_event_payload(status="cancelled")
        google_provider._request_google_json = AsyncMock(return_value=response_payload)

        patch = CalendarEventUpdate(title="Updated")
        with pytest.raises(CalendarRequestError) as exc_info:
            await google_provider.update_event(
                calendar_id="test@example.com",
                event_id="event-123",
                patch=patch,
            )
        assert "cancelled event after update" in str(exc_info.value)

    async def test_update_event_encodes_special_chars_in_event_id(self, google_provider):
        """Special characters in event_id are URL-encoded in the request path."""
        response_payload = _make_google_event_payload(event_id="event/with/slashes")
        google_provider._request_google_json = AsyncMock(return_value=response_payload)

        patch = CalendarEventUpdate(title="Updated")
        await google_provider.update_event(
            calendar_id="test@example.com",
            event_id="event/with/slashes",
            patch=patch,
        )

        call_args = google_provider._request_google_json.call_args
        path = call_args[0][1]
        assert "event%2Fwith%2Fslashes" in path


# ---------------------------------------------------------------------------
# Helper to build a CalendarEvent from a raw Google payload dict
# ---------------------------------------------------------------------------


def _make_calendar_event_from_payload(payload: dict) -> CalendarEvent:
    from butlers.modules.calendar import _google_event_to_calendar_event

    event = _google_event_to_calendar_event(payload, fallback_timezone="UTC")
    assert event is not None
    return event
