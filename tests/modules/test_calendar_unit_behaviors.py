"""Unit tests for core calendar module behaviors.

Covers:
- Auth credential parsing and refresh error paths
- BUTLER event tagging and metadata extraction
- Event payload normalization edge cases
- Conflict policy handling (suggest, fail, allow_overlap)
- Approval-required flow for overlap overrides
- Recurring event validation and timezone requirements
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from pydantic import ValidationError

from butlers.modules.calendar import (
    BUTLER_GENERATED_PRIVATE_KEY,
    BUTLER_NAME_PRIVATE_KEY,
    CalendarConfig,
    CalendarCredentialError,
    CalendarEventCreate,
    CalendarEventPayloadInput,
    CalendarTokenRefreshError,
    _extract_google_private_metadata,
    _GoogleOAuthClient,
    _GoogleOAuthCredentials,
    _normalize_recurrence,
    normalize_event_payload,
)

# ============================================================================
# Auth Credential Parsing Tests
# ============================================================================


class TestGoogleOAuthCredentials:
    """Test OAuth credential validation and parsing."""

    def test_valid_credentials_parse_successfully(self):
        creds = _GoogleOAuthCredentials(
            client_id="test_client_id",
            client_secret="test_secret",
            refresh_token="test_refresh",
        )
        assert creds.client_id == "test_client_id"
        assert creds.client_secret == "test_secret"
        assert creds.refresh_token == "test_refresh"

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

    def test_from_json_parses_top_level_credentials(self):
        raw = json.dumps(
            {
                "client_id": "top_id",
                "client_secret": "top_secret",
                "refresh_token": "top_token",
            }
        )
        creds = _GoogleOAuthCredentials.from_json(raw)
        assert creds.client_id == "top_id"
        assert creds.client_secret == "top_secret"
        assert creds.refresh_token == "top_token"

    def test_from_json_extracts_nested_installed_credentials(self):
        raw = json.dumps(
            {
                "installed": {
                    "client_id": "nested_id",
                    "client_secret": "nested_secret",
                },
                "refresh_token": "outer_token",
            }
        )
        creds = _GoogleOAuthCredentials.from_json(raw)
        assert creds.client_id == "nested_id"
        assert creds.client_secret == "nested_secret"
        assert creds.refresh_token == "outer_token"

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

    def test_from_json_invalid_json_raises_credential_error(self):
        with pytest.raises(CalendarCredentialError, match="must be valid JSON"):
            _GoogleOAuthCredentials.from_json("{not valid json")

    def test_from_json_non_object_raises_credential_error(self):
        with pytest.raises(CalendarCredentialError, match="must decode to a JSON object"):
            _GoogleOAuthCredentials.from_json(json.dumps(["list", "not", "object"]))

    def test_from_json_missing_client_id_raises_credential_error(self):
        raw = json.dumps(
            {
                "client_secret": "secret",
                "refresh_token": "token",
            }
        )
        with pytest.raises(CalendarCredentialError, match="missing required field.*client_id"):
            _GoogleOAuthCredentials.from_json(raw)

    def test_from_json_missing_multiple_fields_lists_all(self):
        raw = json.dumps({"client_id": "id"})
        with pytest.raises(
            CalendarCredentialError,
            match="missing required field.*client_secret.*refresh_token",
        ):
            _GoogleOAuthCredentials.from_json(raw)

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

    def test_from_json_empty_string_client_secret_raises_credential_error(self):
        raw = json.dumps(
            {
                "client_id": "id",
                "client_secret": "",
                "refresh_token": "token",
            }
        )
        with pytest.raises(
            CalendarCredentialError, match="must contain non-empty string field.*client_secret"
        ):
            _GoogleOAuthCredentials.from_json(raw)

    def test_from_env_missing_env_var_raises_credential_error(self):
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(CalendarCredentialError, match="must be set to a non-empty JSON"):
                _GoogleOAuthCredentials.from_env()

    def test_from_env_empty_env_var_raises_credential_error(self):
        with patch.dict(os.environ, {"BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON": "   "}):
            with pytest.raises(CalendarCredentialError, match="must be set to a non-empty JSON"):
                _GoogleOAuthCredentials.from_env()


# ============================================================================
# Auth Token Refresh Tests
# ============================================================================


class TestGoogleOAuthClient:
    """Test OAuth client token refresh and caching logic."""

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

    async def test_get_access_token_refreshes_when_no_cached_token(
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
        token = await client.get_access_token()

        assert token == "fresh_token"
        mock_http_client.post.assert_awaited_once()

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

        # Second call uses cache
        token2 = await client.get_access_token()
        assert token2 == "fresh_token"
        assert mock_http_client.post.await_count == 1  # No additional refresh

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
# BUTLER Event Tagging Tests
# ============================================================================


class TestButlerEventTagging:
    """Test BUTLER prefix and private metadata extraction."""

    def test_extract_butler_generated_true_from_bool(self):
        payload = {
            "private": {
                BUTLER_GENERATED_PRIVATE_KEY: True,
            }
        }
        butler_generated, butler_name = _extract_google_private_metadata(payload)
        assert butler_generated is True
        assert butler_name is None

    def test_extract_butler_generated_false_from_bool(self):
        payload = {
            "private": {
                BUTLER_GENERATED_PRIVATE_KEY: False,
            }
        }
        butler_generated, butler_name = _extract_google_private_metadata(payload)
        assert butler_generated is False

    def test_extract_butler_generated_from_string_true(self):
        payload = {
            "private": {
                BUTLER_GENERATED_PRIVATE_KEY: "true",
            }
        }
        butler_generated, _ = _extract_google_private_metadata(payload)
        assert butler_generated is True

    def test_extract_butler_generated_from_string_false(self):
        payload = {
            "private": {
                BUTLER_GENERATED_PRIVATE_KEY: "false",
            }
        }
        butler_generated, _ = _extract_google_private_metadata(payload)
        assert butler_generated is False

    def test_extract_butler_name_from_string(self):
        payload = {
            "private": {
                BUTLER_NAME_PRIVATE_KEY: "my_butler",
            }
        }
        _, butler_name = _extract_google_private_metadata(payload)
        assert butler_name == "my_butler"

    def test_extract_butler_name_strips_whitespace(self):
        payload = {
            "private": {
                BUTLER_NAME_PRIVATE_KEY: "  my_butler  ",
            }
        }
        _, butler_name = _extract_google_private_metadata(payload)
        assert butler_name == "my_butler"

    def test_extract_butler_name_empty_string_returns_none(self):
        payload = {
            "private": {
                BUTLER_NAME_PRIVATE_KEY: "   ",
            }
        }
        _, butler_name = _extract_google_private_metadata(payload)
        assert butler_name is None

    def test_extract_missing_payload_returns_defaults(self):
        butler_generated, butler_name = _extract_google_private_metadata({})
        assert butler_generated is False
        assert butler_name is None

    def test_extract_non_dict_private_returns_defaults(self):
        payload = {
            "private": "not a dict",
        }
        butler_generated, butler_name = _extract_google_private_metadata(payload)
        assert butler_generated is False
        assert butler_name is None

    def test_extract_non_dict_payload_returns_defaults(self):
        butler_generated, butler_name = _extract_google_private_metadata("not a dict")
        assert butler_generated is False
        assert butler_name is None


# ============================================================================
# Event Payload Normalization Tests
# ============================================================================


class TestEventPayloadNormalization:
    """Test normalize_event_payload edge cases and validation."""

    @pytest.fixture
    def base_config(self):
        return CalendarConfig(
            provider="google",
            calendar_id="test@example.com",
            timezone="America/New_York",
        )

    def test_normalize_timed_event_with_naive_datetimes(self, base_config):
        payload = CalendarEventPayloadInput(
            title="Test Event",
            start_at=datetime(2026, 3, 1, 10, 0),
            end_at=datetime(2026, 3, 1, 11, 0),
        )
        normalized = normalize_event_payload(payload, config=base_config)

        assert normalized.title == "Test Event"
        assert normalized.all_day is False
        assert normalized.timezone == "America/New_York"
        assert normalized.start.date_time_value is not None
        assert normalized.end.date_time_value is not None

    def test_normalize_all_day_event_requires_date_only_values(self, base_config):
        from datetime import date

        payload = CalendarEventPayloadInput(
            title="All Day Event",
            start_at=date(2026, 3, 1),
            end_at=date(2026, 3, 2),
            all_day=True,
        )
        normalized = normalize_event_payload(payload, config=base_config)

        assert normalized.all_day is True
        assert normalized.start.date_value is not None
        assert normalized.end.date_value is not None

    def test_normalize_all_day_with_datetime_values_raises_error(self, base_config):
        payload = CalendarEventPayloadInput(
            title="Invalid All Day",
            start_at=datetime(2026, 3, 1, 0, 0, 0, tzinfo=UTC),
            end_at=datetime(2026, 3, 2, 0, 0, 0, tzinfo=UTC),
            all_day=True,
        )
        with pytest.raises(ValueError, match="all_day events require date-only"):
            normalize_event_payload(payload, config=base_config)

    def test_normalize_end_before_start_raises_error(self, base_config):
        payload = CalendarEventPayloadInput(
            title="Invalid Times",
            start_at=datetime(2026, 3, 1, 15, 0, tzinfo=UTC),
            end_at=datetime(2026, 3, 1, 14, 0, tzinfo=UTC),
        )
        with pytest.raises(ValueError, match="end_at must be after start_at"):
            normalize_event_payload(payload, config=base_config)

    def test_normalize_end_equal_to_start_raises_error(self, base_config):
        same_time = datetime(2026, 3, 1, 10, 0, tzinfo=UTC)
        payload = CalendarEventPayloadInput(
            title="Same Start/End",
            start_at=same_time,
            end_at=same_time,
        )
        with pytest.raises(ValueError, match="end_at must be after start_at"):
            normalize_event_payload(payload, config=base_config)

    def test_normalize_uses_payload_timezone_over_config(self, base_config):
        payload = CalendarEventPayloadInput(
            title="Override TZ",
            start_at=datetime(2026, 3, 1, 10, 0),
            end_at=datetime(2026, 3, 1, 11, 0),
            timezone="Europe/London",
        )
        normalized = normalize_event_payload(payload, config=base_config)
        assert normalized.timezone == "Europe/London"

    def test_normalize_invalid_timezone_raises_error(self, base_config):
        # Invalid timezone is caught during CalendarEventPayloadInput validation
        with pytest.raises(ValidationError, match="must be a valid IANA timezone"):
            CalendarEventPayloadInput(
                title="Bad TZ",
                start_at=datetime(2026, 3, 1, 10, 0),
                end_at=datetime(2026, 3, 1, 11, 0),
                timezone="Invalid/Timezone",
            )


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
# ============================================================================


class TestConflictPolicyHandling:
    """Test conflict detection and policy enforcement."""

    def test_config_default_conflict_policy_is_suggest(self):
        config = CalendarConfig(
            provider="google",
            calendar_id="test@example.com",
        )
        assert config.conflicts.policy == "suggest"

    def test_config_conflict_policy_suggest(self):
        config = CalendarConfig(
            provider="google",
            calendar_id="test@example.com",
            conflicts={"policy": "suggest"},
        )
        assert config.conflicts.policy == "suggest"

    def test_config_conflict_policy_fail(self):
        config = CalendarConfig(
            provider="google",
            calendar_id="test@example.com",
            conflicts={"policy": "fail"},
        )
        assert config.conflicts.policy == "fail"

    def test_config_conflict_policy_allow_overlap(self):
        config = CalendarConfig(
            provider="google",
            calendar_id="test@example.com",
            conflicts={"policy": "allow_overlap"},
        )
        assert config.conflicts.policy == "allow_overlap"

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

    def test_config_require_approval_for_overlap_default_true(self):
        """Default is True per current implementation."""
        config = CalendarConfig(
            provider="google",
            calendar_id="test@example.com",
        )
        assert config.conflicts.require_approval_for_overlap is True

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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
