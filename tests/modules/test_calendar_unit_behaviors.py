"""Unit tests for core calendar module behaviors.

## Layer Ownership

This file owns **unit-level** tests for the calendar module: pure functions,
data-model validation, and edge-case logic that does not require the full
module + MCP wiring.

| Layer | Owned by |
|-------|----------|
| Pure helpers (`_extract_google_*`, `_normalize_optional_text`, etc.) | test_calendar_helpers.py |
| Data-model construction / field validation (CalendarEventCreate, Pydantic) | THIS FILE |
| OAuth credential parsing edge cases (non-object decode, invalid type, whitespace) | THIS FILE |
| OAuth token caching, force-refresh, error paths | THIS FILE |
| `normalize_event_payload` pure-function edge cases (timezone precedence) | THIS FILE |
| Conflict policy enum aliases and validation | THIS FILE |
| MCP tool orchestration / provider wiring | test_module_calendar.py |
| Error hierarchy / fail-open / fail-closed / rate-limit retry | test_calendar_error_handling.py |

Note: `TestButlerEventTagging` was removed; all `_extract_google_private_metadata`
behavior is covered by `test_calendar_helpers.py::TestButlerPrivateMetadataExtraction`.

Covers:
- Auth credential parsing edge cases (whitespace stripping, type errors, non-object JSON)
- OAuth client token caching, force-refresh, and error paths
- Event payload normalization unique cases (timezone precedence)
- Conflict policy handling (legacy aliases, validation)
- Approval-required flow for overlap overrides
- Recurring event validation and timezone requirements
- Mixed date/datetime boundary type validation (PR #173 review feedback)
- find_conflicts type coercion before comparison (PR #173 review feedback)
- MCP tool new fields: notification, status, visibility, notes, all_day (PR #173 review feedback)
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from pydantic import ValidationError

from butlers.modules.calendar import (
    CalendarConfig,
    CalendarCredentialError,
    CalendarEventCreate,
    CalendarEventPayloadInput,
    CalendarNotificationInput,
    CalendarTokenRefreshError,
    _GoogleOAuthClient,
    _GoogleOAuthCredentials,
    _GoogleProvider,
    _normalize_recurrence,
    normalize_event_payload,
)

# ============================================================================
# Auth Credential Parsing Tests
#
# Note: Basic from_json() paths (top-level, nested installed/web, invalid JSON,
# missing-fields message) are covered in test_module_calendar.py::TestGoogleCredentialParsing.
# This class owns the remaining unit-level edge cases: whitespace stripping,
# Pydantic model validation, non-object JSON decode, and invalid field types.
# ============================================================================


class TestGoogleOAuthCredentials:
    """Test OAuth credential validation and parsing edge cases."""

    def test_credentials_strip_whitespace(self):
        creds = _GoogleOAuthCredentials(
            client_id="  test_id  ",
            client_secret="  test_secret  ",
            refresh_token="  test_token  ",
        )
        assert creds.client_id == "test_id"
        assert creds.client_secret == "test_secret"
        assert creds.refresh_token == "test_token"

    def test_empty_client_id_raises_validation_error(self):
        with pytest.raises(ValidationError, match="client_id must be a non-empty string"):
            _GoogleOAuthCredentials(
                client_id="   ",
                client_secret="secret",
                refresh_token="token",
            )

    def test_empty_client_secret_raises_validation_error(self):
        with pytest.raises(ValidationError, match="client_secret must be a non-empty string"):
            _GoogleOAuthCredentials(
                client_id="id",
                client_secret="   ",
                refresh_token="token",
            )

    def test_empty_refresh_token_raises_validation_error(self):
        with pytest.raises(ValidationError, match="refresh_token must be a non-empty string"):
            _GoogleOAuthCredentials(
                client_id="id",
                client_secret="secret",
                refresh_token="   ",
            )

    def test_from_json_non_object_raises_credential_error(self):
        with pytest.raises(CalendarCredentialError, match="must decode to a JSON object"):
            _GoogleOAuthCredentials.from_json(json.dumps(["list", "not", "object"]))

    def test_from_json_invalid_type_client_id_raises_credential_error(self):
        raw = json.dumps(
            {
                "client_id": 12345,  # not a string
                "client_secret": "secret",
                "refresh_token": "token",
            }
        )
        with pytest.raises(
            CalendarCredentialError, match="must contain non-empty string field.*client_id"
        ):
            _GoogleOAuthCredentials.from_json(raw)

    def test_from_json_extracts_nested_web_credentials(self):
        raw = json.dumps(
            {
                "web": {
                    "client_id": "web_id",
                    "client_secret": "web_secret",
                },
                "refresh_token": "web_token",
            }
        )
        creds = _GoogleOAuthCredentials.from_json(raw)
        assert creds.client_id == "web_id"
        assert creds.client_secret == "web_secret"


# ============================================================================
# Auth Token Refresh Tests
#
# Note: Basic token refresh + HTTP request body wiring is covered in
# test_module_calendar.py::TestGoogleOAuthClient. This class owns
# the caching/force-refresh behavior and all error paths.
# ============================================================================


class TestGoogleOAuthClient:
    """Test OAuth client token caching, force-refresh, and error paths."""

    @pytest.fixture
    def mock_credentials(self):
        return _GoogleOAuthCredentials(
            client_id="test_id",
            client_secret="test_secret",
            refresh_token="test_token",
        )

    @pytest.fixture
    def mock_http_client(self):
        return MagicMock(spec=httpx.AsyncClient)

    async def test_get_access_token_uses_cached_token_when_fresh(
        self, mock_credentials, mock_http_client
    ):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "fresh_token",
            "expires_in": 3600,
        }
        mock_http_client.post = AsyncMock(return_value=mock_response)

        client = _GoogleOAuthClient(mock_credentials, mock_http_client)

        # First call refreshes
        token1 = await client.get_access_token()
        assert token1 == "fresh_token"
        assert mock_http_client.post.await_count == 1

        # Second call uses cache — no additional HTTP request
        token2 = await client.get_access_token()
        assert token2 == "fresh_token"
        assert mock_http_client.post.await_count == 1

    async def test_get_access_token_force_refresh_bypasses_cache(
        self, mock_credentials, mock_http_client
    ):
        mock_response1 = MagicMock()
        mock_response1.status_code = 200
        mock_response1.json.return_value = {
            "access_token": "first_token",
            "expires_in": 3600,
        }
        mock_response2 = MagicMock()
        mock_response2.status_code = 200
        mock_response2.json.return_value = {
            "access_token": "second_token",
            "expires_in": 3600,
        }
        mock_http_client.post = AsyncMock(side_effect=[mock_response1, mock_response2])

        client = _GoogleOAuthClient(mock_credentials, mock_http_client)

        token1 = await client.get_access_token()
        assert token1 == "first_token"

        token2 = await client.get_access_token(force_refresh=True)
        assert token2 == "second_token"
        assert mock_http_client.post.await_count == 2

    async def test_refresh_access_token_http_error_raises_token_refresh_error(
        self, mock_credentials, mock_http_client
    ):
        mock_http_client.post = AsyncMock(side_effect=httpx.ConnectError("Network error"))

        client = _GoogleOAuthClient(mock_credentials, mock_http_client)

        with pytest.raises(CalendarTokenRefreshError, match="token refresh request failed"):
            await client.get_access_token()

    async def test_refresh_access_token_401_status_raises_token_refresh_error(
        self, mock_credentials, mock_http_client
    ):
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.json.return_value = {"error": "invalid_grant"}
        mock_http_client.post = AsyncMock(return_value=mock_response)

        client = _GoogleOAuthClient(mock_credentials, mock_http_client)

        with pytest.raises(CalendarTokenRefreshError, match="token refresh failed.*401"):
            await client.get_access_token()

    async def test_refresh_access_token_missing_token_field_raises_error(
        self, mock_credentials, mock_http_client
    ):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"expires_in": 3600}  # Missing access_token
        mock_http_client.post = AsyncMock(return_value=mock_response)

        client = _GoogleOAuthClient(mock_credentials, mock_http_client)

        with pytest.raises(CalendarTokenRefreshError, match="access_token"):
            await client.get_access_token()


# ============================================================================
# Event Payload Normalization Tests
#
# Note: Comprehensive normalization cases (notification shorthands, color_id
# defaults, attendee dedup, parametrized validation errors, recurrence
# normalization) are covered by test_module_calendar.py::TestEventPayloadNormalization.
# This class owns the unique pure-function edge cases not exercised there.
# ============================================================================


class TestEventPayloadNormalization:
    """Test normalize_event_payload pure-function edge cases."""

    @pytest.fixture
    def base_config(self):
        return CalendarConfig(
            provider="google",
            calendar_id="test@example.com",
            timezone="America/New_York",
        )

    def test_normalize_uses_payload_timezone_over_config(self, base_config):
        """Payload timezone takes precedence over the config default."""
        payload = CalendarEventPayloadInput(
            title="Override TZ",
            start_at=datetime(2026, 3, 1, 10, 0),
            end_at=datetime(2026, 3, 1, 11, 0),
            timezone="Europe/London",
        )
        normalized = normalize_event_payload(payload, config=base_config)
        assert normalized.timezone == "Europe/London"


# ============================================================================
# Recurrence Validation Tests
# ============================================================================


class TestRecurrenceValidation:
    """Test RRULE format validation and timezone requirements."""

    def test_normalize_recurrence_valid_rrule(self):
        result = _normalize_recurrence("RRULE:FREQ=DAILY;COUNT=5")
        assert result == ["RRULE:FREQ=DAILY;COUNT=5"]

    def test_normalize_recurrence_list_of_rules(self):
        rules = [
            "RRULE:FREQ=WEEKLY;BYDAY=MO,WE,FR",
            "RRULE:FREQ=MONTHLY;BYMONTHDAY=15",
        ]
        result = _normalize_recurrence(rules)
        assert len(result) == 2

    def test_normalize_recurrence_strips_whitespace(self):
        result = _normalize_recurrence("  RRULE:FREQ=DAILY  ")
        assert result == ["RRULE:FREQ=DAILY"]

    def test_normalize_recurrence_empty_string_raises_error(self):
        with pytest.raises(ValueError, match="must be non-empty strings"):
            _normalize_recurrence("")

    def test_normalize_recurrence_whitespace_only_raises_error(self):
        with pytest.raises(ValueError, match="must be non-empty strings"):
            _normalize_recurrence("   ")

    def test_normalize_recurrence_missing_rrule_prefix_raises_error(self):
        with pytest.raises(ValueError, match="must start with 'RRULE:'"):
            _normalize_recurrence("FREQ=DAILY")

    def test_normalize_recurrence_missing_freq_raises_error(self):
        with pytest.raises(ValueError, match="must include a FREQ component"):
            _normalize_recurrence("RRULE:COUNT=5")

    def test_normalize_recurrence_contains_dtstart_raises_error(self):
        with pytest.raises(ValueError, match="must not include DTSTART/DTEND"):
            _normalize_recurrence("RRULE:FREQ=DAILY;DTSTART=20260301T100000Z")

    def test_normalize_recurrence_contains_dtend_raises_error(self):
        with pytest.raises(ValueError, match="must not include DTSTART/DTEND"):
            _normalize_recurrence("RRULE:FREQ=DAILY;DTEND=20260301T110000Z")

    def test_normalize_recurrence_newline_raises_error(self):
        with pytest.raises(ValueError, match="must not contain newline"):
            _normalize_recurrence("RRULE:FREQ=DAILY\nCOUNT=5")

    def test_normalize_recurrence_none_returns_empty_list(self):
        result = _normalize_recurrence(None)
        assert result == []

    def test_calendar_event_create_recurrence_requires_timezone_for_naive_datetime(self):
        with pytest.raises(
            ValidationError,
            match="timezone is required when recurrence_rule is set for naive datetime",
        ):
            CalendarEventCreate(
                title="Recurring Event",
                start_at=datetime(2026, 3, 1, 10, 0),  # Naive
                end_at=datetime(2026, 3, 1, 11, 0),
                recurrence_rule="RRULE:FREQ=DAILY;COUNT=5",
                # timezone=None  # Missing!
            )

    def test_calendar_event_create_recurrence_allows_timezone_with_naive_datetime(self):
        event = CalendarEventCreate(
            title="Recurring Event",
            start_at=datetime(2026, 3, 1, 10, 0),
            end_at=datetime(2026, 3, 1, 11, 0),
            recurrence_rule="RRULE:FREQ=DAILY;COUNT=5",
            timezone="America/New_York",
        )
        assert event.timezone == "America/New_York"

    def test_calendar_event_create_recurrence_with_aware_datetime_no_timezone_ok(self):
        event = CalendarEventCreate(
            title="Recurring Event",
            start_at=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
            end_at=datetime(2026, 3, 1, 11, 0, tzinfo=UTC),
            recurrence_rule="RRULE:FREQ=DAILY;COUNT=5",
        )
        assert event.recurrence_rule == "RRULE:FREQ=DAILY;COUNT=5"


# ============================================================================
# Conflict Policy Handling Tests
#
# Note: Default policy value and require_approval_for_overlap defaults are
# already covered in test_module_calendar.py::TestCalendarConfig.test_defaults.
# This class owns the legacy alias mappings, invalid policy rejection, and
# explicit bool setter tests.
# ============================================================================


class TestConflictPolicyHandling:
    """Test conflict policy enum aliases and validation (config layer)."""

    def test_config_conflict_policy_legacy_alias_allow(self):
        """Legacy 'allow' should map to 'allow_overlap'."""
        config = CalendarConfig(
            provider="google",
            calendar_id="test@example.com",
            conflicts={"policy": "allow"},
        )
        assert config.conflicts.policy == "allow_overlap"

    def test_config_conflict_policy_legacy_alias_reject(self):
        """Legacy 'reject' should map to 'fail'."""
        config = CalendarConfig(
            provider="google",
            calendar_id="test@example.com",
            conflicts={"policy": "reject"},
        )
        assert config.conflicts.policy == "fail"

    def test_config_conflict_policy_invalid_raises_error(self):
        with pytest.raises(ValidationError, match="Input should be"):
            CalendarConfig(
                provider="google",
                calendar_id="test@example.com",
                conflicts={"policy": "invalid_policy"},
            )

    def test_config_require_approval_for_overlap_can_be_false(self):
        config = CalendarConfig(
            provider="google",
            calendar_id="test@example.com",
            conflicts={"require_approval_for_overlap": False},
        )
        assert config.conflicts.require_approval_for_overlap is False

    def test_config_require_approval_for_overlap_can_be_true(self):
        config = CalendarConfig(
            provider="google",
            calendar_id="test@example.com",
            conflicts={"require_approval_for_overlap": True},
        )
        assert config.conflicts.require_approval_for_overlap is True


# ============================================================================
# Approval Required Flow
# ============================================================================
# Note: Approval-required flows for allow_overlap are tested in integration
# tests in test_module_calendar.py, specifically:
# - test_create_conflict_allow_overlap_requires_approval_when_enqueuer_set
# - test_create_conflict_allow_overlap_returns_fallback_when_approvals_disabled
#
# These behaviors require full module + approval-enqueuer integration and are
# not suitable for unit testing in isolation.


# ============================================================================
# Mixed date/datetime boundary type validation tests (PR #173 review feedback)
# ============================================================================


class TestCalendarEventCreateBoundaryTypeConsistency:
    """Test that start_at and end_at must both be date or both be datetime."""

    def test_both_date_objects_accepted(self):
        from datetime import date

        event = CalendarEventCreate(
            title="All Day",
            start_at=date(2026, 3, 1),
            end_at=date(2026, 3, 2),
        )
        assert event.start_at == date(2026, 3, 1)
        assert event.end_at == date(2026, 3, 2)

    def test_both_datetime_objects_accepted(self):
        event = CalendarEventCreate(
            title="Timed",
            start_at=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
            end_at=datetime(2026, 3, 1, 11, 0, tzinfo=UTC),
        )
        assert isinstance(event.start_at, datetime)
        assert isinstance(event.end_at, datetime)

    def test_date_start_datetime_end_raises_validation_error(self):
        from datetime import date

        with pytest.raises(
            ValidationError,
            match="start_at and end_at must be the same type",
        ):
            CalendarEventCreate(
                title="Mixed Types",
                start_at=date(2026, 3, 1),
                end_at=datetime(2026, 3, 1, 11, 0, tzinfo=UTC),
            )

    def test_datetime_start_date_end_raises_validation_error(self):
        from datetime import date

        with pytest.raises(
            ValidationError,
            match="start_at and end_at must be the same type",
        ):
            CalendarEventCreate(
                title="Mixed Types Reversed",
                start_at=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
                end_at=date(2026, 3, 2),
            )


# ============================================================================
# find_conflicts safe comparison tests (PR #173 review feedback)
# ============================================================================


def _make_mock_http_client() -> MagicMock:
    """Build a mock httpx.AsyncClient pre-wired with a valid OAuth token response."""
    mock_client = MagicMock(spec=httpx.AsyncClient)
    token_response = MagicMock()
    token_response.status_code = 200
    token_response.json.return_value = {"access_token": "access-token", "expires_in": 3600}
    mock_client.post = AsyncMock(return_value=token_response)
    return mock_client


class TestFindConflictsBoundaryCoercion:
    """Test that find_conflicts coerces types before comparing boundaries."""

    @pytest.fixture
    def google_provider(self):
        """Build a _GoogleProvider with test credentials and mocked HTTP client."""
        config = CalendarConfig(
            provider="google",
            calendar_id="test@example.com",
            timezone="America/New_York",
        )
        credentials = _GoogleOAuthCredentials(
            client_id="client-id",
            client_secret="client-secret",
            refresh_token="refresh-token",
        )
        return _GoogleProvider(
            config=config, credentials=credentials, http_client=_make_mock_http_client()
        )

    async def test_find_conflicts_date_boundaries_do_not_raise_type_error(self, google_provider):
        """date boundaries should be coerced to datetime before comparison — no TypeError."""
        candidate = CalendarEventCreate(
            title="All Day Event",
            start_at=date(2026, 3, 1),
            end_at=date(2026, 3, 2),
        )

        # Patch the HTTP request so we don't need real credentials
        google_provider._request_google_json = AsyncMock(
            return_value={
                "calendars": {
                    "test@example.com": {
                        "busy": [],
                    }
                }
            }
        )

        # Should not raise TypeError — just returns empty list
        result = await google_provider.find_conflicts(
            calendar_id="test@example.com",
            candidate=candidate,
        )
        assert result == []

    async def test_find_conflicts_end_before_start_raises_value_error(self, google_provider):
        """Reversed date-only boundaries should raise ValueError, not TypeError."""
        candidate = CalendarEventCreate(
            title="Invalid",
            start_at=date(2026, 3, 2),
            end_at=date(2026, 3, 1),
        )

        with pytest.raises(ValueError, match="candidate.end_at must be after candidate.start_at"):
            await google_provider.find_conflicts(
                calendar_id="test@example.com",
                candidate=candidate,
            )


# ============================================================================
# MCP tool new field tests (PR #173 review feedback)
# ============================================================================


class TestCalendarCreateEventToolNewFields:
    """Test that the MCP tool accepts and passes new CalendarEventCreate fields."""

    def test_calendar_event_create_accepts_notification_field(self):
        event = CalendarEventCreate(
            title="Event with Notification",
            start_at=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
            end_at=datetime(2026, 3, 1, 11, 0, tzinfo=UTC),
            notification=CalendarNotificationInput(enabled=True, minutes_before=15),
        )
        assert event.notification is not None

    def test_calendar_event_create_accepts_notification_bool(self):
        event = CalendarEventCreate(
            title="Event",
            start_at=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
            end_at=datetime(2026, 3, 1, 11, 0, tzinfo=UTC),
            notification=True,
        )
        assert event.notification is True

    def test_calendar_event_create_accepts_status_field(self):
        event = CalendarEventCreate(
            title="Tentative Event",
            start_at=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
            end_at=datetime(2026, 3, 1, 11, 0, tzinfo=UTC),
            status="tentative",
        )
        assert event.status == "tentative"

    def test_calendar_event_create_accepts_visibility_field(self):
        event = CalendarEventCreate(
            title="Private Event",
            start_at=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
            end_at=datetime(2026, 3, 1, 11, 0, tzinfo=UTC),
            visibility="private",
        )
        assert event.visibility == "private"

    def test_calendar_event_create_accepts_notes_field(self):
        event = CalendarEventCreate(
            title="Event with Notes",
            start_at=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
            end_at=datetime(2026, 3, 1, 11, 0, tzinfo=UTC),
            notes="Some internal notes",
        )
        assert event.notes == "Some internal notes"

    def test_calendar_event_create_accepts_all_day_field(self):
        event = CalendarEventCreate(
            title="All Day Event",
            start_at=date(2026, 3, 1),
            end_at=date(2026, 3, 2),
            all_day=True,
        )
        assert event.all_day is True

    def test_calendar_event_create_all_new_fields_together(self):
        event = CalendarEventCreate(
            title="Full Featured Event",
            start_at=date(2026, 3, 1),
            end_at=date(2026, 3, 2),
            all_day=True,
            notification=CalendarNotificationInput(enabled=True, minutes_before=30),
            status="confirmed",
            visibility="public",
            notes="These are my notes",
        )
        assert event.all_day is True
        assert event.status == "confirmed"
        assert event.visibility == "public"
        assert event.notes == "These are my notes"
        assert event.notification is not None
