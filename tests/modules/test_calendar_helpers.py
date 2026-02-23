"""Unit tests for calendar module pure helper functions.

## Layer Ownership

This file owns all tests for **pure (stateless) helper functions** in the
calendar module — functions that take plain data in and return plain data out,
with no module/MCP/provider coupling.

| Layer | Owned by |
|-------|----------|
| Pure helpers (this file) | test_calendar_helpers.py |
| Data-model validation, OAuth edge cases | test_calendar_unit_behaviors.py |
| MCP tool orchestration / provider wiring | test_module_calendar.py |
| Error hierarchy / fail-open / fail-closed | test_calendar_error_handling.py |

Covered helpers:
- `_extract_google_credential_value` — credential JSON extraction
- `_coerce_expires_in_seconds` — token expiry coercion
- `_safe_google_error_message` — API error message sanitization
- `_google_rfc3339` / `_parse_google_datetime` — datetime formatting/parsing
- `_coerce_zoneinfo` — timezone coercion with UTC fallback
- `_extract_google_attendees` — attendee list extraction
- `_extract_google_recurrence_rule` — recurrence rule extraction
- `_extract_google_private_metadata` — BUTLER event tagging extraction
- `_normalize_optional_text` — optional text field normalization
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from butlers.modules.calendar import (
    BUTLER_GENERATED_PRIVATE_KEY,
    BUTLER_NAME_PRIVATE_KEY,
    AttendeeResponseStatus,
    _coerce_expires_in_seconds,
    _coerce_zoneinfo,
    _extract_google_attendees,
    _extract_google_credential_value,
    _extract_google_private_metadata,
    _extract_google_recurrence_rule,
    _google_rfc3339,
    _normalize_optional_text,
    _parse_google_datetime,
    _safe_google_error_message,
)

pytestmark = pytest.mark.unit


class TestCredentialParsing:
    """Test auth credential extraction from various JSON shapes."""

    def test_extract_top_level_credential(self):
        payload = {"client_id": "top-level-id"}
        assert _extract_google_credential_value(payload, "client_id") == "top-level-id"

    def test_extract_nested_installed_credential(self):
        payload = {"installed": {"client_id": "installed-id"}}
        assert _extract_google_credential_value(payload, "client_id") == "installed-id"

    def test_extract_nested_web_credential(self):
        payload = {"web": {"client_secret": "web-secret"}}
        assert _extract_google_credential_value(payload, "client_secret") == "web-secret"

    def test_extract_prefers_top_level_over_nested(self):
        payload = {
            "client_id": "top-level",
            "installed": {"client_id": "nested"},
        }
        assert _extract_google_credential_value(payload, "client_id") == "top-level"

    def test_extract_returns_none_for_missing_key(self):
        payload = {"other_field": "value"}
        assert _extract_google_credential_value(payload, "client_id") is None

    def test_extract_returns_none_for_non_dict_nested_value(self):
        payload = {"installed": "not-a-dict"}
        assert _extract_google_credential_value(payload, "client_id") is None


class TestTokenRefreshHelpers:
    """Test OAuth token refresh helper behavior."""

    def test_coerce_expires_in_with_valid_int(self):
        assert _coerce_expires_in_seconds(3600) == 3600

    def test_coerce_expires_in_with_valid_float(self):
        assert _coerce_expires_in_seconds(3600.5) == 3600

    def test_coerce_expires_in_with_zero_returns_default(self):
        assert _coerce_expires_in_seconds(0) == 3600

    def test_coerce_expires_in_with_negative_returns_default(self):
        assert _coerce_expires_in_seconds(-100) == 3600

    def test_coerce_expires_in_with_bool_returns_default(self):
        assert _coerce_expires_in_seconds(True) == 3600
        assert _coerce_expires_in_seconds(False) == 3600

    def test_coerce_expires_in_with_none_returns_default(self):
        assert _coerce_expires_in_seconds(None) == 3600

    def test_coerce_expires_in_with_string_returns_default(self):
        assert _coerce_expires_in_seconds("3600") == 3600


class TestGoogleErrorMessageExtraction:
    """Test safe error message extraction from Google API responses."""

    def test_extract_from_nested_error_object(self):
        response = httpx.Response(
            status_code=403,
            json={
                "error": {
                    "code": 403,
                    "message": "Forbidden by policy",
                }
            },
            request=httpx.Request("GET", "https://example.com"),
        )
        message = _safe_google_error_message(response)
        assert message == "Forbidden by policy"

    def test_extract_from_string_error_field(self):
        response = httpx.Response(
            status_code=400,
            json={"error": "Invalid request"},
            request=httpx.Request("GET", "https://example.com"),
        )
        message = _safe_google_error_message(response)
        assert message == "Invalid request"

    def test_extract_truncates_long_messages(self):
        long_message = "Error: " + "x" * 300
        response = httpx.Response(
            status_code=500,
            json={"error": {"message": long_message}},
            request=httpx.Request("GET", "https://example.com"),
        )
        message = _safe_google_error_message(response)
        assert len(message) == 200
        assert message.startswith("Error:")

    def test_extract_normalizes_whitespace(self):
        response = httpx.Response(
            status_code=400,
            json={"error": {"message": "Multiple   spaces\nand\nnewlines"}},
            request=httpx.Request("GET", "https://example.com"),
        )
        message = _safe_google_error_message(response)
        assert message == "Multiple spaces and newlines"

    def test_fallback_to_response_text_when_no_json(self):
        response = httpx.Response(
            status_code=502,
            text="Bad Gateway",
            request=httpx.Request("GET", "https://example.com"),
        )
        message = _safe_google_error_message(response)
        assert message == "Bad Gateway"

    def test_fallback_message_when_no_error_payload(self):
        response = httpx.Response(
            status_code=500,
            text="",
            request=httpx.Request("GET", "https://example.com"),
        )
        message = _safe_google_error_message(response)
        assert message == "Request failed without an error payload"


class TestDateTimeFormatting:
    """Test Google RFC3339 datetime formatting."""

    def test_google_rfc3339_with_utc_datetime(self):
        dt = datetime(2026, 2, 15, 9, 30, 0, tzinfo=UTC)
        assert _google_rfc3339(dt) == "2026-02-15T09:30:00Z"

    def test_google_rfc3339_with_naive_datetime_assumes_utc(self):
        dt = datetime(2026, 2, 15, 9, 30, 0)
        assert _google_rfc3339(dt) == "2026-02-15T09:30:00Z"

    def test_google_rfc3339_converts_other_timezones_to_utc(self):
        from zoneinfo import ZoneInfo

        dt = datetime(2026, 2, 15, 9, 30, 0, tzinfo=ZoneInfo("America/New_York"))
        result = _google_rfc3339(dt)
        assert result.endswith("Z")
        # EST is UTC-5, so 9:30 EST = 14:30 UTC
        assert result == "2026-02-15T14:30:00Z"


class TestDateTimeParsing:
    """Test Google datetime string parsing."""

    def test_parse_google_datetime_with_z_suffix(self):
        dt = _parse_google_datetime("2026-02-15T09:30:00Z")
        assert dt.year == 2026
        assert dt.month == 2
        assert dt.day == 15
        assert dt.hour == 9
        assert dt.minute == 30
        assert dt.tzinfo is not None

    def test_parse_google_datetime_with_offset(self):
        dt = _parse_google_datetime("2026-02-15T09:30:00-05:00")
        assert dt.year == 2026
        assert dt.tzinfo is not None

    def test_parse_google_datetime_strips_whitespace(self):
        dt = _parse_google_datetime("  2026-02-15T09:30:00Z  ")
        assert dt.year == 2026

    def test_parse_google_datetime_adds_utc_for_naive(self):
        dt = _parse_google_datetime("2026-02-15T09:30:00")
        assert dt.tzinfo == UTC

    def test_parse_google_datetime_invalid_format_raises_value_error(self):
        with pytest.raises(ValueError, match="invalid dateTime"):
            _parse_google_datetime("not-a-datetime")


class TestTimezoneCoercion:
    """Test timezone handling with fallback to UTC."""

    def test_coerce_valid_timezone(self):
        from zoneinfo import ZoneInfo

        tz = _coerce_zoneinfo("America/New_York")
        assert isinstance(tz, ZoneInfo)
        assert str(tz) == "America/New_York"

    def test_coerce_invalid_timezone_returns_utc(self):
        tz = _coerce_zoneinfo("Mars/Olympus")
        assert tz == UTC


class TestGoogleAttendeesExtraction:
    """Test attendee list extraction into structured AttendeeInfo objects."""

    def test_extract_from_dict_with_email_only(self):
        payload = [
            {"email": "alice@example.com"},
            {"email": "bob@example.com"},
        ]
        attendees = _extract_google_attendees(payload)
        assert len(attendees) == 2
        assert attendees[0].email == "alice@example.com"
        assert attendees[1].email == "bob@example.com"
        # Defaults
        assert attendees[0].display_name is None
        assert attendees[0].response_status == AttendeeResponseStatus.needs_action
        assert attendees[0].optional is False
        assert attendees[0].organizer is False
        assert attendees[0].self_ is False
        assert attendees[0].comment is None

    def test_extract_rich_attendee_fields(self):
        payload = [
            {
                "email": "alice@example.com",
                "displayName": "Alice Smith",
                "responseStatus": "accepted",
                "optional": True,
                "organizer": False,
                "self": True,
                "comment": "Looking forward to it!",
            }
        ]
        attendees = _extract_google_attendees(payload)
        assert len(attendees) == 1
        a = attendees[0]
        assert a.email == "alice@example.com"
        assert a.display_name == "Alice Smith"
        assert a.response_status == AttendeeResponseStatus.accepted
        assert a.optional is True
        assert a.organizer is False
        assert a.self_ is True
        assert a.comment == "Looking forward to it!"

    def test_extract_all_response_statuses(self):
        payload = [
            {"email": "a@example.com", "responseStatus": "needsAction"},
            {"email": "b@example.com", "responseStatus": "accepted"},
            {"email": "c@example.com", "responseStatus": "declined"},
            {"email": "d@example.com", "responseStatus": "tentative"},
        ]
        attendees = _extract_google_attendees(payload)
        assert attendees[0].response_status == AttendeeResponseStatus.needs_action
        assert attendees[1].response_status == AttendeeResponseStatus.accepted
        assert attendees[2].response_status == AttendeeResponseStatus.declined
        assert attendees[3].response_status == AttendeeResponseStatus.tentative

    def test_extract_unknown_response_status_defaults_to_needs_action(self):
        payload = [{"email": "a@example.com", "responseStatus": "unknown_status"}]
        attendees = _extract_google_attendees(payload)
        assert attendees[0].response_status == AttendeeResponseStatus.needs_action

    def test_extract_from_plain_strings(self):
        payload = ["alice@example.com", "bob@example.com"]
        attendees = _extract_google_attendees(payload)
        assert len(attendees) == 2
        assert attendees[0].email == "alice@example.com"
        assert attendees[1].email == "bob@example.com"

    def test_extract_strips_whitespace(self):
        payload = [
            {"email": "  alice@example.com  "},
            "  bob@example.com  ",
        ]
        attendees = _extract_google_attendees(payload)
        assert attendees[0].email == "alice@example.com"
        assert attendees[1].email == "bob@example.com"

    def test_extract_skips_empty_emails(self):
        payload = [
            {"email": "alice@example.com"},
            {"email": ""},
            {"email": "   "},
            "",
            "   ",
        ]
        attendees = _extract_google_attendees(payload)
        assert len(attendees) == 1
        assert attendees[0].email == "alice@example.com"

    def test_extract_skips_dict_without_email(self):
        payload = [
            {"email": "alice@example.com"},
            {"name": "Bob"},
        ]
        attendees = _extract_google_attendees(payload)
        assert len(attendees) == 1
        assert attendees[0].email == "alice@example.com"

    def test_extract_returns_empty_for_non_list(self):
        assert _extract_google_attendees(None) == []
        assert _extract_google_attendees("not-a-list") == []
        assert _extract_google_attendees({"email": "alice@example.com"}) == []

    def test_extract_organizer_flag(self):
        payload = [{"email": "organizer@example.com", "organizer": True}]
        attendees = _extract_google_attendees(payload)
        assert attendees[0].organizer is True


class TestGoogleRecurrenceExtraction:
    """Test recurrence rule extraction from Google payload."""

    def test_extract_first_non_empty_string(self):
        payload = ["RRULE:FREQ=DAILY"]
        rule = _extract_google_recurrence_rule(payload)
        assert rule == "RRULE:FREQ=DAILY"

    def test_extract_strips_whitespace(self):
        payload = ["  RRULE:FREQ=WEEKLY  "]
        rule = _extract_google_recurrence_rule(payload)
        assert rule == "RRULE:FREQ=WEEKLY"

    def test_extract_skips_empty_strings(self):
        payload = ["", "  ", "RRULE:FREQ=MONTHLY"]
        rule = _extract_google_recurrence_rule(payload)
        assert rule == "RRULE:FREQ=MONTHLY"

    def test_extract_returns_none_for_empty_list(self):
        assert _extract_google_recurrence_rule([]) is None

    def test_extract_returns_none_for_non_list(self):
        assert _extract_google_recurrence_rule(None) is None
        assert _extract_google_recurrence_rule("RRULE:FREQ=DAILY") is None
        assert _extract_google_recurrence_rule({"rule": "RRULE:FREQ=DAILY"}) is None


class TestButlerPrivateMetadataExtraction:
    """Test BUTLER event tagging metadata extraction."""

    def test_extract_butler_generated_true(self):
        payload = {
            "private": {
                BUTLER_GENERATED_PRIVATE_KEY: True,
                BUTLER_NAME_PRIVATE_KEY: "general",
            }
        }
        generated, name = _extract_google_private_metadata(payload)
        assert generated is True
        assert name == "general"

    def test_extract_butler_generated_string_true(self):
        payload = {
            "private": {
                BUTLER_GENERATED_PRIVATE_KEY: "true",
                BUTLER_NAME_PRIVATE_KEY: "scheduler",
            }
        }
        generated, name = _extract_google_private_metadata(payload)
        assert generated is True
        assert name == "scheduler"

    def test_extract_butler_generated_false(self):
        payload = {
            "private": {
                BUTLER_GENERATED_PRIVATE_KEY: False,
            }
        }
        generated, name = _extract_google_private_metadata(payload)
        assert generated is False
        assert name is None

    def test_extract_butler_generated_string_false(self):
        payload = {
            "private": {
                BUTLER_GENERATED_PRIVATE_KEY: "false",
            }
        }
        generated, name = _extract_google_private_metadata(payload)
        assert generated is False

    def test_extract_butler_name_strips_whitespace(self):
        payload = {
            "private": {
                BUTLER_NAME_PRIVATE_KEY: "  scheduler  ",
            }
        }
        generated, name = _extract_google_private_metadata(payload)
        assert name == "scheduler"

    def test_extract_butler_name_empty_string_returns_none(self):
        payload = {
            "private": {
                BUTLER_NAME_PRIVATE_KEY: "   ",
            }
        }
        generated, name = _extract_google_private_metadata(payload)
        assert name is None

    def test_extract_missing_metadata_returns_defaults(self):
        payload = {"private": {}}
        generated, name = _extract_google_private_metadata(payload)
        assert generated is False
        assert name is None

    def test_extract_non_dict_private_returns_defaults(self):
        payload = {"private": "not-a-dict"}
        generated, name = _extract_google_private_metadata(payload)
        assert generated is False
        assert name is None

    def test_extract_non_dict_payload_returns_defaults(self):
        generated, name = _extract_google_private_metadata(None)
        assert generated is False
        assert name is None

        generated, name = _extract_google_private_metadata("not-a-dict")
        assert generated is False
        assert name is None


class TestOptionalTextNormalization:
    """Test optional text field normalization."""

    def test_normalize_non_empty_string(self):
        assert _normalize_optional_text("hello") == "hello"

    def test_normalize_strips_whitespace(self):
        assert _normalize_optional_text("  hello  ") == "hello"

    def test_normalize_empty_string_returns_none(self):
        assert _normalize_optional_text("") is None
        assert _normalize_optional_text("   ") is None

    def test_normalize_non_string_returns_none(self):
        assert _normalize_optional_text(None) is None
        assert _normalize_optional_text(123) is None
        assert _normalize_optional_text(["text"]) is None
        assert _normalize_optional_text({"text": "value"}) is None
