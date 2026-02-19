"""Calendar module scaffolding with provider-agnostic contracts.

This module defines:
- ``CalendarConfig``: validated module config with sensible defaults
- ``CalendarProvider``: provider interface used by calendar tools
- ``CalendarModule``: module shell with provider selection at startup
"""

from __future__ import annotations

import abc
import asyncio
import json
import logging
import os
from collections.abc import Callable, Coroutine
from datetime import UTC, date, datetime, timedelta, tzinfo
from enum import StrEnum
from typing import Any, Literal
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

from butlers.core.state import state_get as _state_get
from butlers.core.state import state_set as _state_set
from butlers.google_credentials import (
    resolve_google_credentials,
)
from butlers.modules.base import Module

logger = logging.getLogger(__name__)

# Type alias for the approval enqueue callback.
# Receives (tool_name, tool_args, agent_summary) and returns the action_id string.
ApprovalEnqueuer = Callable[
    [str, dict[str, Any], str],
    Coroutine[Any, Any, str],
]

GOOGLE_CALENDAR_CREDENTIALS_ENV = "BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON"
GOOGLE_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_CALENDAR_API_BASE_URL = "https://www.googleapis.com/calendar/v3"
BUTLER_EVENT_TITLE_PREFIX = "BUTLER:"
BUTLER_GENERATED_PRIVATE_KEY = "butler_generated"
BUTLER_NAME_PRIVATE_KEY = "butler_name"
DEFAULT_BUTLER_NAME = "butler"
LEGACY_CONFLICT_POLICY_ALIASES = {
    "allow": "allow_overlap",
    "reject": "fail",
}
VALID_CONFLICT_POLICIES = {"suggest", "fail", "allow_overlap"}
DEFAULT_CONFLICT_SUGGESTION_COUNT = 3

# Rate-limit retry configuration (spec section 14.2, 15).
# Retry on 429 Too Many Requests and 503 Service Unavailable with exponential backoff.
RATE_LIMIT_RETRY_STATUS_CODES = {429, 503}
RATE_LIMIT_MAX_RETRIES = 3
RATE_LIMIT_BASE_BACKOFF_SECONDS = 1.0

# Sync state store key prefix (spec section 10.2).
# Format: calendar::sync::{calendar_id}
SYNC_STATE_KEY_PREFIX = "calendar::sync::"
# Default full sync window in days when no sync token exists (spec section 12.5).
DEFAULT_SYNC_WINDOW_DAYS = 30
# Default sync interval in minutes (spec section 12.5).
DEFAULT_SYNC_INTERVAL_MINUTES = 5

CalendarConflictPolicy = Literal["suggest", "fail", "allow_overlap"]


class CalendarAuthError(RuntimeError):
    """Base error raised by Google Calendar auth/request helpers."""


class CalendarCredentialError(CalendarAuthError):
    """Raised when Google credential JSON is missing or invalid."""


class CalendarTokenRefreshError(CalendarAuthError):
    """Raised when refresh-token exchange fails."""


class CalendarRequestError(CalendarAuthError):
    """Raised when Google Calendar API request fails."""

    def __init__(self, *, status_code: int, message: str) -> None:
        self.status_code = status_code
        self.message = message
        super().__init__(f"Google Calendar API request failed ({status_code}): {message}")


class CalendarSyncTokenExpiredError(CalendarAuthError):
    """Raised when a sync token is expired or invalid; caller should do a full sync."""


class _GoogleOAuthCredentials(BaseModel):
    """OAuth client credentials required for refresh-token exchange."""

    model_config = ConfigDict(extra="forbid")

    client_id: str = Field(min_length=1)
    client_secret: str = Field(min_length=1)
    refresh_token: str = Field(min_length=1)

    @field_validator("client_id", "client_secret", "refresh_token")
    @classmethod
    def _normalize_non_empty(cls, value: str, info: ValidationInfo) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError(f"{info.field_name} must be a non-empty string")
        return normalized

    @classmethod
    def from_env(cls) -> _GoogleOAuthCredentials:
        """Load credentials from the legacy BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON env var.

        .. deprecated::
            BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON is deprecated. Credentials should be
            bootstrapped via the dashboard OAuth flow and stored in the database. The
            CalendarModule now calls resolve_google_credentials(pool) at startup, which
            performs DB-first resolution with env-var fallback. This method is retained
            only for backward compatibility when no DB pool is available.
        """
        logger.warning(
            "BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON env var is deprecated. "
            "Use the dashboard OAuth flow to store credentials in the database. "
            "See docs/oauth/google/setup-guide.md for instructions."
        )
        raw_value = os.environ.get(GOOGLE_CALENDAR_CREDENTIALS_ENV, "").strip()
        if not raw_value:
            raise CalendarCredentialError(
                f"{GOOGLE_CALENDAR_CREDENTIALS_ENV} must be set to a non-empty JSON object"
            )
        return cls.from_json(raw_value)

    @classmethod
    def from_json(cls, raw_value: str) -> _GoogleOAuthCredentials:
        try:
            payload = json.loads(raw_value)
        except json.JSONDecodeError as exc:
            raise CalendarCredentialError(
                f"{GOOGLE_CALENDAR_CREDENTIALS_ENV} must be valid JSON: {exc.msg}"
            ) from exc

        if not isinstance(payload, dict):
            raise CalendarCredentialError(
                f"{GOOGLE_CALENDAR_CREDENTIALS_ENV} must decode to a JSON object"
            )

        credential_data = {
            "client_id": _extract_google_credential_value(payload, "client_id"),
            "client_secret": _extract_google_credential_value(payload, "client_secret"),
            "refresh_token": _extract_google_credential_value(payload, "refresh_token"),
        }

        missing = sorted(key for key, value in credential_data.items() if value is None)
        if missing:
            field_list = ", ".join(missing)
            raise CalendarCredentialError(
                f"{GOOGLE_CALENDAR_CREDENTIALS_ENV} is missing required field(s): {field_list}"
            )

        invalid = sorted(
            key
            for key, value in credential_data.items()
            if not isinstance(value, str) or not value.strip()
        )
        if invalid:
            field_list = ", ".join(invalid)
            raise CalendarCredentialError(
                f"{GOOGLE_CALENDAR_CREDENTIALS_ENV} must contain non-empty string "
                f"field(s): {field_list}"
            )

        return cls(
            client_id=str(credential_data["client_id"]),
            client_secret=str(credential_data["client_secret"]),
            refresh_token=str(credential_data["refresh_token"]),
        )


class _GoogleOAuthClient:
    """Refresh-token OAuth helper with lightweight access-token caching."""

    def __init__(
        self,
        credentials: _GoogleOAuthCredentials,
        http_client: httpx.AsyncClient,
    ) -> None:
        self._credentials = credentials
        self._http_client = http_client
        self._access_token: str | None = None
        self._access_token_expires_at: datetime | None = None
        self._refresh_lock = asyncio.Lock()

    async def get_access_token(self, *, force_refresh: bool = False) -> str:
        if not force_refresh and self._token_is_fresh():
            assert self._access_token is not None
            return self._access_token

        async with self._refresh_lock:
            if not force_refresh and self._token_is_fresh():
                assert self._access_token is not None
                return self._access_token

            await self._refresh_access_token()
            assert self._access_token is not None
            return self._access_token

    def _token_is_fresh(self) -> bool:
        if self._access_token is None or self._access_token_expires_at is None:
            return False
        return datetime.now(UTC) < self._access_token_expires_at

    async def _refresh_access_token(self) -> None:
        try:
            response = await self._http_client.post(
                GOOGLE_OAUTH_TOKEN_URL,
                data={
                    "client_id": self._credentials.client_id,
                    "client_secret": self._credentials.client_secret,
                    "refresh_token": self._credentials.refresh_token,
                    "grant_type": "refresh_token",
                },
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise CalendarTokenRefreshError(
                f"Google OAuth token refresh request failed: {exc}"
            ) from exc

        if response.status_code < 200 or response.status_code >= 300:
            raise CalendarTokenRefreshError(
                "Google OAuth token refresh failed "
                f"({response.status_code}): {_safe_google_error_message(response)}"
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise CalendarTokenRefreshError(
                "Google OAuth token endpoint returned invalid JSON"
            ) from exc

        access_token = payload.get("access_token") if isinstance(payload, dict) else None
        if not isinstance(access_token, str) or not access_token.strip():
            raise CalendarTokenRefreshError(
                "Google OAuth token response is missing a non-empty access_token"
            )

        expires_in_raw = payload.get("expires_in") if isinstance(payload, dict) else None
        expires_in_seconds = _coerce_expires_in_seconds(expires_in_raw)
        # Refresh early to avoid edge-of-expiration failures.
        refresh_ttl_seconds = max(expires_in_seconds - 60, 30)

        self._access_token = access_token.strip()
        self._access_token_expires_at = datetime.now(UTC) + timedelta(seconds=refresh_ttl_seconds)


def _extract_google_credential_value(payload: dict[str, Any], key: str) -> Any:
    if key in payload:
        return payload[key]

    for nested_key in ("installed", "web"):
        nested = payload.get(nested_key)
        if isinstance(nested, dict) and key in nested:
            return nested[key]
    return None


def _coerce_expires_in_seconds(value: Any) -> int:
    if isinstance(value, bool):
        return 3600
    if isinstance(value, int | float):
        return int(value) if value > 0 else 3600
    return 3600


def _safe_google_error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = None

    if isinstance(payload, dict):
        error_payload = payload.get("error")
        if isinstance(error_payload, dict):
            message = error_payload.get("message")
            if isinstance(message, str) and message.strip():
                return " ".join(message.split())[:200]
        if isinstance(error_payload, str) and error_payload.strip():
            return " ".join(error_payload.split())[:200]

    raw_text = response.text.strip()
    if raw_text:
        return " ".join(raw_text.split())[:200]
    return "Request failed without an error payload"


def _redact_credential_values(message: str) -> str:
    """Redact known credential values from an error message (spec section 15.3).

    Reads credential JSON from the environment and replaces any credential field
    values found in the message with ``[REDACTED]``.  This guards against
    transitive leakage when a credential value appears in an exception message,
    regardless of the credential length.
    """
    raw_creds = os.environ.get(GOOGLE_CALENDAR_CREDENTIALS_ENV, "")
    if not raw_creds:
        return message
    try:
        payload = json.loads(raw_creds)
    except (ValueError, TypeError):
        return message
    if not isinstance(payload, dict):
        return message

    # Collect all string values recursively (handles nested "installed"/"web" objects).
    values_to_redact: list[str] = []
    for v in payload.values():
        if isinstance(v, str) and v.strip():
            values_to_redact.append(v.strip())
        elif isinstance(v, dict):
            for nested_v in v.values():
                if isinstance(nested_v, str) and nested_v.strip():
                    values_to_redact.append(nested_v.strip())

    result = message
    for secret in values_to_redact:
        result = result.replace(secret, "[REDACTED]")
    return result


def _build_structured_error(
    exc: Exception,
    *,
    provider: str,
    calendar_id: str,
) -> dict[str, Any]:
    """Build a structured error dict per spec section 15.2.

    Sanitizes error messages to prevent credential leakage.
    """
    error_type = type(exc).__name__
    raw_message = str(exc)
    # Redact credential values before truncation (spec section 15.3).
    redacted = _redact_credential_values(raw_message)
    # Normalize whitespace and truncate to 200 chars.
    sanitized = " ".join(redacted.split())[:200]
    return {
        "status": "error",
        "error": sanitized,
        "error_type": error_type,
        "provider": provider,
        "calendar_id": calendar_id,
    }


def _google_rfc3339(value: datetime) -> str:
    normalized = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return normalized.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_google_datetime(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"Google Calendar returned an invalid dateTime: {value}") from exc
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _coerce_zoneinfo(timezone: str) -> ZoneInfo | tzinfo:
    try:
        return ZoneInfo(timezone)
    except ZoneInfoNotFoundError:
        return UTC


def _extract_google_attendees(payload: Any) -> list[AttendeeInfo]:
    """Parse a Google Calendar attendees array into structured AttendeeInfo objects."""
    if not isinstance(payload, list):
        return []

    attendees: list[AttendeeInfo] = []
    for entry in payload:
        if isinstance(entry, dict):
            email = entry.get("email")
            if not isinstance(email, str):
                continue
            normalized_email = email.strip()
            if not normalized_email:
                continue

            display_name_raw = entry.get("displayName")
            display_name = None
            if isinstance(display_name_raw, str) and (stripped := display_name_raw.strip()):
                display_name = stripped

            response_status_raw = entry.get("responseStatus")
            response_status = AttendeeResponseStatus.needs_action
            if isinstance(response_status_raw, str):
                try:
                    response_status = AttendeeResponseStatus(response_status_raw.strip())
                except ValueError:
                    pass

            optional_raw = entry.get("optional")
            optional = optional_raw is True

            organizer_raw = entry.get("organizer")
            organizer = organizer_raw is True

            self_raw = entry.get("self")
            self_ = self_raw is True

            comment_raw = entry.get("comment")
            comment = None
            if isinstance(comment_raw, str) and (stripped := comment_raw.strip()):
                comment = stripped

            attendees.append(
                AttendeeInfo(
                    email=normalized_email,
                    display_name=display_name,
                    response_status=response_status,
                    optional=optional,
                    organizer=organizer,
                    self_=self_,
                    comment=comment,
                )
            )
        elif isinstance(entry, str):
            normalized_email = entry.strip()
            if normalized_email:
                attendees.append(AttendeeInfo(email=normalized_email))
    return attendees


def _extract_google_recurrence_rule(payload: Any) -> str | None:
    if not isinstance(payload, list):
        return None
    for entry in payload:
        if isinstance(entry, str):
            normalized = entry.strip()
            if normalized:
                return normalized
    return None


def _extract_google_private_metadata(payload: Any) -> tuple[bool, str | None]:
    if not isinstance(payload, dict):
        return False, None

    private_payload = payload.get("private")
    if not isinstance(private_payload, dict):
        return False, None

    generated_raw = private_payload.get(BUTLER_GENERATED_PRIVATE_KEY)
    if isinstance(generated_raw, bool):
        butler_generated = generated_raw
    elif isinstance(generated_raw, str):
        butler_generated = generated_raw.strip().lower() == "true"
    else:
        butler_generated = False

    butler_name_raw = private_payload.get(BUTLER_NAME_PRIVATE_KEY)
    butler_name = (
        butler_name_raw.strip()
        if isinstance(butler_name_raw, str) and butler_name_raw.strip()
        else None
    )
    return butler_generated, butler_name


def _normalize_optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _parse_google_event_boundary(
    payload: dict[str, Any],
    *,
    fallback_timezone: str,
) -> tuple[datetime, str]:
    date_time = payload.get("dateTime")
    timezone_raw = payload.get("timeZone")
    timezone = (
        timezone_raw.strip()
        if isinstance(timezone_raw, str) and timezone_raw.strip()
        else fallback_timezone
    )

    if isinstance(date_time, str) and date_time.strip():
        return _parse_google_datetime(date_time), timezone

    date_value = payload.get("date")
    if isinstance(date_value, str) and date_value.strip():
        try:
            parsed_date = date.fromisoformat(date_value)
        except ValueError as exc:
            raise ValueError(
                f"Google Calendar returned an invalid date value: {date_value}"
            ) from exc

        tzinfo = _coerce_zoneinfo(timezone)
        parsed_datetime = datetime(
            parsed_date.year,
            parsed_date.month,
            parsed_date.day,
            tzinfo=tzinfo,
        )
        return parsed_datetime, timezone

    raise ValueError("Google Calendar event is missing start/end dateTime or date values")


def _parse_google_event_status(value: Any) -> EventStatus | None:
    """Parse a Google Calendar event status string into an EventStatus enum value."""
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    try:
        return EventStatus(normalized)
    except ValueError:
        return None


def _parse_google_event_visibility(value: Any) -> EventVisibility | None:
    """Parse a Google Calendar event visibility string into an EventVisibility enum value."""
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    try:
        return EventVisibility(normalized)
    except ValueError:
        return None


def _extract_google_organizer(payload: Any) -> str | None:
    """Extract the organizer email from a Google Calendar event payload."""
    if not isinstance(payload, dict):
        return None
    email = payload.get("email")
    if isinstance(email, str):
        normalized = email.strip()
        return normalized or None
    return None


def _parse_google_rfc3339_optional(value: Any) -> datetime | None:
    """Parse an optional RFC 3339 datetime string, returning None on failure."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return _parse_google_datetime(value.strip())
    except ValueError:
        return None


def _google_event_to_calendar_event(
    payload: dict[str, Any],
    *,
    fallback_timezone: str,
) -> CalendarEvent | None:
    status_raw = payload.get("status")
    if isinstance(status_raw, str) and status_raw.lower() == "cancelled":
        return None

    event_id_raw = payload.get("id")
    if not isinstance(event_id_raw, str) or not event_id_raw.strip():
        raise ValueError("Google Calendar event payload is missing a non-empty id")
    event_id = event_id_raw.strip()

    start_payload = payload.get("start")
    end_payload = payload.get("end")
    if not isinstance(start_payload, dict) or not isinstance(end_payload, dict):
        raise ValueError(f"Google Calendar event '{event_id}' is missing start/end payloads")

    start_at, start_timezone = _parse_google_event_boundary(
        start_payload,
        fallback_timezone=fallback_timezone,
    )
    end_at, end_timezone = _parse_google_event_boundary(
        end_payload,
        fallback_timezone=fallback_timezone,
    )
    timezone = start_timezone or end_timezone or fallback_timezone

    title = _normalize_optional_text(payload.get("summary")) or "(untitled)"
    butler_generated, butler_name = _extract_google_private_metadata(
        payload.get("extendedProperties")
    )

    # Parse extended fields per spec section 5.1.
    event_status = _parse_google_event_status(status_raw)
    visibility = _parse_google_event_visibility(payload.get("visibility"))
    organizer = _extract_google_organizer(payload.get("organizer"))
    etag = _normalize_optional_text(payload.get("etag"))
    created_at = _parse_google_rfc3339_optional(payload.get("created"))
    updated_at = _parse_google_rfc3339_optional(payload.get("updated"))

    return CalendarEvent(
        event_id=event_id,
        title=title,
        start_at=start_at,
        end_at=end_at,
        timezone=timezone,
        description=_normalize_optional_text(payload.get("description")),
        location=_normalize_optional_text(payload.get("location")),
        attendees=_extract_google_attendees(payload.get("attendees")),
        recurrence_rule=_extract_google_recurrence_rule(payload.get("recurrence")),
        color_id=_normalize_optional_text(payload.get("colorId")),
        butler_generated=butler_generated,
        butler_name=butler_name,
        status=event_status,
        organizer=organizer,
        visibility=visibility,
        etag=etag,
        created_at=created_at,
        updated_at=updated_at,
    )


def _attendee_info_list_to_google(attendees: list[AttendeeInfo]) -> list[dict[str, Any]]:
    """Convert a list of AttendeeInfo objects to Google Calendar API attendee dicts.

    Only writable fields are included: email, displayName, optional.
    Response status is read-only from the butler's perspective.
    """
    result: list[dict[str, Any]] = []
    for attendee in attendees:
        entry: dict[str, Any] = {"email": attendee.email}
        if attendee.display_name is not None:
            entry["displayName"] = attendee.display_name
        if attendee.optional:
            entry["optional"] = True
        result.append(entry)
    return result


def _build_google_event_body(payload: CalendarEventCreate) -> dict[str, Any]:
    """Translate a CalendarEventCreate payload into a Google Calendar API event body."""
    body: dict[str, Any] = {}

    # Title / summary
    body["summary"] = payload.title

    # Optional text fields
    if payload.description is not None:
        body["description"] = payload.description
    if payload.location is not None:
        body["location"] = payload.location

    # Event status (default confirmed)
    if payload.status is not None:
        body["status"] = payload.status
    else:
        body["status"] = "confirmed"

    # Timezone for boundary construction
    timezone = payload.timezone

    # Start/end boundaries — all-day uses "date", timed uses "dateTime"
    # Infer all-day when payload.all_day is None: use date-only boundaries as signal.
    is_all_day = payload.all_day
    if is_all_day is None:
        is_all_day = _is_date_only(payload.start_at) and _is_date_only(payload.end_at)

    if is_all_day:
        # All-day boundaries are date objects (stored as date in CalendarEventCreate)
        start_val = payload.start_at
        end_val = payload.end_at
        start_date_str = (
            start_val.date().isoformat()
            if isinstance(start_val, datetime)
            else start_val.isoformat()
        )
        end_date_str = (
            end_val.date().isoformat() if isinstance(end_val, datetime) else end_val.isoformat()
        )
        body["start"] = {"date": start_date_str}
        body["end"] = {"date": end_date_str}
    else:
        # Timed event
        start_dt = payload.start_at
        end_dt = payload.end_at
        if not isinstance(start_dt, datetime) or not isinstance(end_dt, datetime):
            raise ValueError("timed events require datetime start_at and end_at values")

        if timezone is not None:
            # Normalize to the specified timezone
            tz = ZoneInfo(timezone)
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=tz)
            else:
                start_dt = start_dt.astimezone(tz)
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=tz)
            else:
                end_dt = end_dt.astimezone(tz)
            body["start"] = {
                "dateTime": start_dt.isoformat(),
                "timeZone": timezone,
            }
            body["end"] = {
                "dateTime": end_dt.isoformat(),
                "timeZone": timezone,
            }
        else:
            # No explicit timezone: serialize as-is
            body["start"] = {"dateTime": _google_rfc3339(start_dt)}
            body["end"] = {"dateTime": _google_rfc3339(end_dt)}

    # Attendees
    if payload.attendees:
        body["attendees"] = [{"email": email} for email in payload.attendees]

    # Recurrence rules
    if payload.recurrence_rule is not None:
        body["recurrence"] = [payload.recurrence_rule]

    # Color
    if payload.color_id is not None:
        body["colorId"] = payload.color_id

    # Visibility
    if payload.visibility is not None:
        body["visibility"] = payload.visibility

    # Reminders / notifications
    notification = payload.notification
    if notification is None:
        # Use provider default (Google Calendar's own defaults)
        body["reminders"] = {"useDefault": True}
    elif isinstance(notification, bool):
        if notification:
            body["reminders"] = {"useDefault": True}
        else:
            body["reminders"] = {"useDefault": False, "overrides": []}
    elif isinstance(notification, int):
        body["reminders"] = {
            "useDefault": False,
            "overrides": [{"method": "popup", "minutes": notification}],
        }
    else:
        # CalendarNotificationInput object (or CalendarNormalizedNotification)
        notif_enabled = getattr(notification, "enabled", True)
        minutes_before = getattr(notification, "minutes_before", None)
        if not notif_enabled:
            body["reminders"] = {"useDefault": False, "overrides": []}
        elif minutes_before is not None:
            body["reminders"] = {
                "useDefault": False,
                "overrides": [{"method": "popup", "minutes": minutes_before}],
            }
        else:
            body["reminders"] = {"useDefault": True}

    # Extended properties (butler-generated metadata + custom private_metadata)
    private_props = payload.private_metadata.copy()
    if payload.notes is not None:
        private_props["notes"] = payload.notes
    if private_props:
        body["extendedProperties"] = {"private": private_props}

    return body


def _build_google_event_patch_body(
    patch: CalendarEventUpdate,
    *,
    existing_start_at: datetime | None = None,
    existing_end_at: datetime | None = None,
    existing_timezone: str | None = None,
) -> dict[str, Any]:
    """Translate a CalendarEventUpdate patch into a partial Google Calendar API event body.

    Only fields that are explicitly set (non-None) are included in the body so
    that unchanged fields are not overwritten on the server (true partial-update
    semantics as required by the PATCH endpoint).

    ``existing_start_at``, ``existing_end_at``, and ``existing_timezone`` are
    supplied by the provider when only the timezone changes (no time boundary
    update) so that the start/end can be re-emitted with the new timezone.
    """
    body: dict[str, Any] = {}

    # Title / summary
    if patch.title is not None:
        body["summary"] = patch.title

    # Optional text fields
    if patch.description is not None:
        body["description"] = patch.description
    if patch.location is not None:
        body["location"] = patch.location

    # Timezone and time boundaries — handle together so that timezone is applied
    # consistently to both start and end.
    timezone = patch.timezone

    if patch.start_at is not None or patch.end_at is not None or timezone is not None:
        # Resolve effective start/end datetimes.
        start_dt = patch.start_at if patch.start_at is not None else existing_start_at
        end_dt = patch.end_at if patch.end_at is not None else existing_end_at
        effective_tz = timezone if timezone is not None else existing_timezone

        if start_dt is not None:
            if effective_tz is not None:
                tz = ZoneInfo(effective_tz)
                if start_dt.tzinfo is None:
                    start_dt = start_dt.replace(tzinfo=tz)
                else:
                    start_dt = start_dt.astimezone(tz)
                body["start"] = {"dateTime": start_dt.isoformat(), "timeZone": effective_tz}
            else:
                body["start"] = {"dateTime": _google_rfc3339(start_dt)}

        if end_dt is not None:
            if effective_tz is not None:
                tz = ZoneInfo(effective_tz)
                if end_dt.tzinfo is None:
                    end_dt = end_dt.replace(tzinfo=tz)
                else:
                    end_dt = end_dt.astimezone(tz)
                body["end"] = {"dateTime": end_dt.isoformat(), "timeZone": effective_tz}
            else:
                body["end"] = {"dateTime": _google_rfc3339(end_dt)}

    # Attendees
    if patch.attendees is not None:
        body["attendees"] = [{"email": email} for email in patch.attendees]

    # Recurrence rules
    if patch.recurrence_rule is not None:
        body["recurrence"] = [patch.recurrence_rule]

    # Color
    if patch.color_id is not None:
        body["colorId"] = patch.color_id

    # Extended properties (butler-generated metadata + custom private_metadata).
    # Only included when private_metadata is explicitly provided.
    if patch.private_metadata is not None:
        body["extendedProperties"] = {"private": patch.private_metadata}

    return body


class CalendarConflictDefaults(BaseModel):
    """Default behavior for overlapping event handling."""

    model_config = ConfigDict(extra="forbid")

    policy: CalendarConflictPolicy = "suggest"
    require_approval_for_overlap: bool = True

    @field_validator("policy", mode="before")
    @classmethod
    def _normalize_policy(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        normalized = value.strip().lower()
        if not normalized:
            return value
        return LEGACY_CONFLICT_POLICY_ALIASES.get(normalized, normalized)


class CalendarNotificationDefaults(BaseModel):
    """Default notification and color behavior for new events."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    minutes_before: int = Field(default=15, ge=0)
    color_id: str | None = None


class CalendarSyncConfig(BaseModel):
    """Polling sync configuration (spec section 12.5)."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    interval_minutes: int = Field(default=DEFAULT_SYNC_INTERVAL_MINUTES, ge=1)
    full_sync_window_days: int = Field(default=DEFAULT_SYNC_WINDOW_DAYS, ge=1)


class CalendarSyncState(BaseModel):
    """Per-calendar sync state persisted in the KV store (spec section 10.2).

    Stored under the key ``calendar::sync::{calendar_id}``.
    """

    model_config = ConfigDict(extra="ignore")

    sync_token: str | None = None
    last_sync_at: str | None = None  # ISO-8601 UTC timestamp
    last_sync_error: str | None = None
    last_batch_change_count: int = 0


class CalendarConfig(BaseModel):
    """Configuration for the Calendar module."""

    provider: str = Field(min_length=1)
    calendar_id: str = Field(min_length=1)
    timezone: str = "UTC"
    conflicts: CalendarConflictDefaults = Field(default_factory=CalendarConflictDefaults)
    event_defaults: CalendarNotificationDefaults = Field(
        default_factory=CalendarNotificationDefaults
    )
    sync: CalendarSyncConfig = Field(default_factory=CalendarSyncConfig)

    @field_validator("provider")
    @classmethod
    def _normalize_provider(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("provider must be a non-empty string")
        return normalized

    @field_validator("calendar_id", "timezone")
    @classmethod
    def _normalize_non_empty(cls, value: str, info: ValidationInfo) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError(f"{info.field_name} must be a non-empty string")
        return normalized


class CalendarNotificationInput(BaseModel):
    """Tool input for notification configuration."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    minutes_before: int | None = Field(default=None, ge=0)


class CalendarNormalizedNotification(BaseModel):
    """Provider-neutral notification settings after defaults are applied."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool
    minutes_before: int | None = Field(default=None, ge=0)


class CalendarEventPayloadInput(BaseModel):
    """Tool input used to construct a normalized event payload."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    start_at: date | datetime
    end_at: date | datetime
    all_day: bool | None = None
    timezone: str | None = None
    description: str | None = None
    location: str | None = None
    attendees: list[str] = Field(default_factory=list)
    recurrence: str | list[str] | None = None
    notification: CalendarNotificationInput | bool | int | None = None
    color_id: str | None = None

    @field_validator("title")
    @classmethod
    def _normalize_title(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("title must be a non-empty string")
        return normalized

    @field_validator("timezone")
    @classmethod
    def _normalize_timezone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            return None
        _ensure_valid_timezone(normalized)
        return normalized

    @field_validator("description", "location", "color_id")
    @classmethod
    def _normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("attendees")
    @classmethod
    def _normalize_attendees(cls, value: list[str]) -> list[str]:
        return [attendee.strip() for attendee in value if attendee.strip()]


class CalendarNormalizedEventTime(BaseModel):
    """Normalized representation of either a date or date-time event boundary."""

    model_config = ConfigDict(extra="forbid")

    date_value: date | None = None
    date_time_value: datetime | None = None
    timezone: str | None = None

    @model_validator(mode="after")
    def _validate_shape(self) -> CalendarNormalizedEventTime:
        has_date = self.date_value is not None
        has_date_time = self.date_time_value is not None
        if has_date == has_date_time:
            raise ValueError("exactly one of date_value or date_time_value must be provided")
        if has_date and self.timezone is not None:
            raise ValueError("timezone cannot be set for date-only event boundaries")
        if has_date_time and self.timezone is None:
            raise ValueError("timezone is required for date-time event boundaries")
        return self


class CalendarNormalizedEventPayload(BaseModel):
    """Provider-neutral canonical event payload before adapter-specific mapping."""

    model_config = ConfigDict(extra="forbid")

    title: str
    all_day: bool
    start: CalendarNormalizedEventTime
    end: CalendarNormalizedEventTime
    timezone: str
    description: str | None = None
    location: str | None = None
    attendees: list[str] = Field(default_factory=list)
    recurrence: list[str] = Field(default_factory=list)
    notification: CalendarNormalizedNotification
    color_id: str | None = None


def normalize_event_payload(
    payload: CalendarEventPayloadInput | dict[str, Any],
    *,
    config: CalendarConfig,
) -> CalendarNormalizedEventPayload:
    """Normalize tool input into a provider-neutral canonical event payload."""

    tool_payload = (
        payload
        if isinstance(payload, CalendarEventPayloadInput)
        else CalendarEventPayloadInput(**payload)
    )
    timezone = tool_payload.timezone or config.timezone
    _ensure_valid_timezone(timezone)
    all_day = _resolve_all_day_flag(
        start_at=tool_payload.start_at,
        end_at=tool_payload.end_at,
        requested_all_day=tool_payload.all_day,
    )

    if all_day:
        if not _is_date_only(tool_payload.start_at) or not _is_date_only(tool_payload.end_at):
            raise ValueError("all_day events require date-only start_at and end_at values")
        start_date = tool_payload.start_at
        end_date = tool_payload.end_at
        if end_date <= start_date:
            raise ValueError("end_at must be after start_at for all_day events")
        start_time = CalendarNormalizedEventTime(date_value=start_date)
        end_time = CalendarNormalizedEventTime(date_value=end_date)
    else:
        if not isinstance(tool_payload.start_at, datetime) or not isinstance(
            tool_payload.end_at, datetime
        ):
            raise ValueError("timed events require datetime start_at and end_at values")
        start_dt = _normalize_datetime(tool_payload.start_at, timezone)
        end_dt = _normalize_datetime(tool_payload.end_at, timezone)
        if end_dt <= start_dt:
            raise ValueError("end_at must be after start_at for timed events")
        start_time = CalendarNormalizedEventTime(date_time_value=start_dt, timezone=timezone)
        end_time = CalendarNormalizedEventTime(date_time_value=end_dt, timezone=timezone)

    recurrence = _normalize_recurrence(tool_payload.recurrence)
    notification = _normalize_notification(
        tool_payload.notification,
        defaults=config.event_defaults,
    )
    color_id = tool_payload.color_id or _normalize_optional_string(config.event_defaults.color_id)

    return CalendarNormalizedEventPayload(
        title=tool_payload.title,
        all_day=all_day,
        start=start_time,
        end=end_time,
        timezone=timezone,
        description=tool_payload.description,
        location=tool_payload.location,
        attendees=tool_payload.attendees,
        recurrence=recurrence,
        notification=notification,
        color_id=color_id,
    )


def _normalize_notification(
    notification: CalendarNotificationInput | bool | int | None,
    *,
    defaults: CalendarNotificationDefaults,
) -> CalendarNormalizedNotification:
    if notification is None:
        enabled = defaults.enabled
        return CalendarNormalizedNotification(
            enabled=enabled,
            minutes_before=(defaults.minutes_before if enabled else None),
        )
    if isinstance(notification, bool):
        return CalendarNormalizedNotification(
            enabled=notification,
            minutes_before=(defaults.minutes_before if notification else None),
        )
    if isinstance(notification, int):
        if notification < 0:
            raise ValueError("notification minutes must be greater than or equal to 0")
        return CalendarNormalizedNotification(enabled=True, minutes_before=notification)

    normalized = notification
    if normalized.enabled is False and normalized.minutes_before is not None:
        raise ValueError(
            "notification.minutes_before cannot be set when notification.enabled is false"
        )

    enabled = normalized.enabled if normalized.enabled is not None else defaults.enabled
    if enabled:
        minutes_before = (
            normalized.minutes_before
            if normalized.minutes_before is not None
            else defaults.minutes_before
        )
    else:
        minutes_before = None
    return CalendarNormalizedNotification(enabled=enabled, minutes_before=minutes_before)


def _normalize_recurrence(recurrence: str | list[str] | None) -> list[str]:
    if recurrence is None:
        return []
    rules = [recurrence] if isinstance(recurrence, str) else recurrence
    normalized_rules: list[str] = []
    for raw_rule in rules:
        rule = raw_rule.strip()
        if not rule:
            raise ValueError("recurrence rules must be non-empty strings")
        if "\n" in rule or "\r" in rule:
            raise ValueError("recurrence rules must not contain newline characters")
        if not rule.startswith("RRULE:"):
            raise ValueError("recurrence rules must start with 'RRULE:'")
        upper_rule = rule.upper()
        if "FREQ=" not in upper_rule:
            raise ValueError("recurrence rules must include a FREQ component")
        if "DTSTART" in upper_rule or "DTEND" in upper_rule:
            raise ValueError("recurrence rules must not include DTSTART/DTEND components")
        normalized_rules.append(rule)
    return normalized_rules


def _normalize_recurrence_rule(recurrence_rule: str | None) -> str | None:
    if recurrence_rule is None:
        return None
    normalized_rules = _normalize_recurrence(recurrence_rule)
    return normalized_rules[0]


def _resolve_all_day_flag(
    *,
    start_at: date | datetime,
    end_at: date | datetime,
    requested_all_day: bool | None,
) -> bool:
    if requested_all_day is not None:
        return requested_all_day
    start_is_date = _is_date_only(start_at)
    end_is_date = _is_date_only(end_at)
    if start_is_date and end_is_date:
        return True
    if isinstance(start_at, datetime) and isinstance(end_at, datetime):
        return False
    raise ValueError(
        "start_at and end_at must both be date values or both datetime values "
        "when all_day is omitted"
    )


def _normalize_datetime(value: datetime, timezone: str) -> datetime:
    tz = ZoneInfo(timezone)
    if value.tzinfo is None:
        return value.replace(tzinfo=tz)
    return value.astimezone(tz)


def _is_naive_datetime(value: datetime) -> bool:
    return value.tzinfo is None or value.utcoffset() is None


def _is_date_only(value: date | datetime) -> bool:
    return isinstance(value, date) and not isinstance(value, datetime)


def _normalize_optional_string(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _ensure_valid_timezone(value: str) -> None:
    try:
        ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"timezone must be a valid IANA timezone: {value}") from exc


class EventStatus(StrEnum):
    """Event lifecycle states as tracked by the provider (spec section 5.3)."""

    confirmed = "confirmed"
    tentative = "tentative"
    cancelled = "cancelled"


class EventVisibility(StrEnum):
    """Event visibility levels (spec section 5.6)."""

    default = "default"
    public = "public"
    private = "private"
    confidential = "confidential"


class AttendeeResponseStatus(StrEnum):
    """RSVP response status for a calendar event attendee (spec section 5.5)."""

    needs_action = "needsAction"
    accepted = "accepted"
    declined = "declined"
    tentative = "tentative"


class SendUpdatesPolicy(StrEnum):
    """Controls whether attendees receive notifications for event changes (spec section 5.7)."""

    all = "all"
    external_only = "externalOnly"
    none = "none"


class AttendeeInfo(BaseModel):
    """Structured attendee representation with RSVP tracking (spec section 5.4)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    email: str
    display_name: str | None = None
    response_status: AttendeeResponseStatus = AttendeeResponseStatus.needs_action
    optional: bool = False
    organizer: bool = False
    self_: bool = Field(default=False, alias="self")
    comment: str | None = None


class CalendarEvent(BaseModel):
    """Canonical event shape shared across provider implementations."""

    event_id: str
    title: str
    start_at: datetime
    end_at: datetime
    timezone: str
    description: str | None = None
    location: str | None = None
    attendees: list[AttendeeInfo] = Field(default_factory=list)
    recurrence_rule: str | None = None
    color_id: str | None = None
    butler_generated: bool = False
    butler_name: str | None = None
    # Extended fields from spec section 5.1
    status: EventStatus | None = None
    organizer: str | None = None
    visibility: EventVisibility | None = None
    etag: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class CalendarEventCreate(BaseModel):
    """Payload for creating a calendar event."""

    title: str
    start_at: date | datetime
    end_at: date | datetime
    all_day: bool | None = None
    timezone: str | None = None
    description: str | None = None
    location: str | None = None
    attendees: list[str] = Field(default_factory=list)
    recurrence_rule: str | None = None
    notification: CalendarNotificationInput | bool | int | None = None
    color_id: str | None = None
    status: EventStatus | None = None
    visibility: EventVisibility | None = None
    notes: str | None = None
    private_metadata: dict[str, str] = Field(default_factory=dict)

    @field_validator("timezone")
    @classmethod
    def _normalize_timezone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            return None
        _ensure_valid_timezone(normalized)
        return normalized

    @field_validator("recurrence_rule")
    @classmethod
    def _normalize_recurrence_rule_field(cls, value: str | None) -> str | None:
        return _normalize_recurrence_rule(value)

    @model_validator(mode="after")
    def _validate_boundary_types_consistent(self) -> CalendarEventCreate:
        start_is_datetime = isinstance(self.start_at, datetime)
        end_is_datetime = isinstance(self.end_at, datetime)
        if start_is_datetime != end_is_datetime:
            raise ValueError(
                "start_at and end_at must be the same type: "
                "both date or both datetime (mixed date/datetime is not allowed)"
            )
        return self

    @model_validator(mode="after")
    def _validate_recurrence_timezone(self) -> CalendarEventCreate:
        if self.recurrence_rule is not None and self.timezone is None:
            if isinstance(self.start_at, datetime) and _is_naive_datetime(self.start_at):
                raise ValueError(
                    "timezone is required when recurrence_rule is set for naive datetime boundaries"
                )
            if isinstance(self.end_at, datetime) and _is_naive_datetime(self.end_at):
                raise ValueError(
                    "timezone is required when recurrence_rule is set for naive datetime boundaries"
                )
        return self


class CalendarEventUpdate(BaseModel):
    """Patch payload for updating a calendar event."""

    title: str | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    timezone: str | None = None
    description: str | None = None
    location: str | None = None
    attendees: list[str] | None = None
    recurrence_rule: str | None = None
    recurrence_scope: Literal["series"] = "series"
    color_id: str | None = None
    private_metadata: dict[str, str] | None = None
    # Etag from the existing event for optimistic concurrency (sent as If-Match header).
    etag: str | None = None

    @field_validator("timezone")
    @classmethod
    def _normalize_timezone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            return None
        _ensure_valid_timezone(normalized)
        return normalized

    @field_validator("recurrence_rule")
    @classmethod
    def _normalize_recurrence_rule_field(cls, value: str | None) -> str | None:
        return _normalize_recurrence_rule(value)

    @model_validator(mode="after")
    def _validate_recurrence_timezone(self) -> CalendarEventUpdate:
        if self.recurrence_rule is None or self.timezone is not None:
            return self
        if (self.start_at is not None and _is_naive_datetime(self.start_at)) or (
            self.end_at is not None and _is_naive_datetime(self.end_at)
        ):
            raise ValueError(
                "timezone is required when recurrence_rule is set for naive datetime boundaries"
            )
        return self


class CalendarProvider(abc.ABC):
    """Provider abstraction used by calendar tools."""

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Provider identifier (e.g., ``google``)."""
        ...

    @abc.abstractmethod
    async def list_events(
        self,
        *,
        calendar_id: str,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        limit: int = 50,
    ) -> list[CalendarEvent]:
        """Return events in a time window."""
        ...

    @abc.abstractmethod
    async def get_event(self, *, calendar_id: str, event_id: str) -> CalendarEvent | None:
        """Fetch a single event by id."""
        ...

    @abc.abstractmethod
    async def create_event(
        self,
        *,
        calendar_id: str,
        payload: CalendarEventCreate,
    ) -> CalendarEvent:
        """Create an event."""
        ...

    @abc.abstractmethod
    async def update_event(
        self,
        *,
        calendar_id: str,
        event_id: str,
        patch: CalendarEventUpdate,
    ) -> CalendarEvent:
        """Update an event."""
        ...

    @abc.abstractmethod
    async def delete_event(
        self,
        *,
        calendar_id: str,
        event_id: str,
        send_updates: str | None = None,
    ) -> None:
        """Delete (or cancel) an event.

        ``send_updates`` controls attendee notification:
        - ``None`` / ``"none"`` — no notifications (default for butler-managed events).
        - ``"all"`` — notify all attendees (sends cancellation emails).
        - ``"externalOnly"`` — notify only non-organizer-domain attendees.
        """
        ...

    @abc.abstractmethod
    async def add_attendees(
        self,
        *,
        calendar_id: str,
        event_id: str,
        attendees: list[str],
        optional: bool = False,
        send_updates: str = "none",
    ) -> CalendarEvent:
        """Add attendees to an existing event, deduplicating by email."""
        ...

    @abc.abstractmethod
    async def remove_attendees(
        self,
        *,
        calendar_id: str,
        event_id: str,
        attendees: list[str],
        send_updates: str = "none",
    ) -> CalendarEvent:
        """Remove attendees from an existing event by email."""
        ...

    @abc.abstractmethod
    async def find_conflicts(
        self,
        *,
        calendar_id: str,
        candidate: CalendarEventCreate,
    ) -> list[CalendarEvent]:
        """Find overlapping events for a candidate event."""
        ...

    @abc.abstractmethod
    async def sync_incremental(
        self,
        *,
        calendar_id: str,
        sync_token: str | None,
        full_sync_window_days: int = DEFAULT_SYNC_WINDOW_DAYS,
    ) -> tuple[list[CalendarEvent], list[str], str]:
        """Fetch incremental changes since the given sync token.

        When ``sync_token`` is ``None`` a full sync is performed over the last
        ``full_sync_window_days`` days.

        Returns:
            A 3-tuple of:
            - ``updated_events``: list of new/updated CalendarEvent objects.
            - ``cancelled_event_ids``: list of event_id strings that were cancelled.
            - ``next_sync_token``: opaque token to pass on the next call.

        Raises:
            ``CalendarSyncTokenExpiredError`` when the provider reports the
            sync token is no longer valid.  The caller should retry with
            ``sync_token=None`` to perform a full sync.
        """
        ...

    @abc.abstractmethod
    async def shutdown(self) -> None:
        """Release provider resources."""
        ...


class _GoogleProvider(CalendarProvider):
    """Google provider with OAuth refresh-token and authenticated request helpers."""

    def __init__(
        self,
        config: CalendarConfig,
        credentials: _GoogleOAuthCredentials,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._config = config
        self._owns_http_client = http_client is None
        self._http_client = http_client or httpx.AsyncClient(timeout=30.0)
        self._oauth = _GoogleOAuthClient(credentials, self._http_client)

    @property
    def name(self) -> str:
        return "google"

    async def _request_google_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        response = await self._request_with_bearer(
            method=method,
            path=path,
            params=params,
            json_body=json_body,
            extra_headers=extra_headers,
        )

        if response.status_code < 200 or response.status_code >= 300:
            raise CalendarRequestError(
                status_code=response.status_code,
                message=_safe_google_error_message(response),
            )

        if response.status_code == 204:
            return {}

        try:
            payload = response.json()
        except ValueError as exc:
            raise CalendarAuthError(
                "Google Calendar API returned invalid JSON for a successful response"
            ) from exc

        if not isinstance(payload, dict):
            raise CalendarAuthError("Google Calendar API returned an unexpected JSON payload shape")
        return payload

    async def _request_with_bearer(
        self,
        *,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        normalized_path = path if path.startswith("/") else f"/{path}"
        url = f"{GOOGLE_CALENDAR_API_BASE_URL}{normalized_path}"

        response = await self._request_once(
            method=method,
            url=url,
            params=params,
            json_body=json_body,
            extra_headers=extra_headers,
            force_refresh=False,
        )

        if response.status_code == 401:
            response = await self._request_once(
                method=method,
                url=url,
                params=params,
                json_body=json_body,
                extra_headers=extra_headers,
                force_refresh=True,
            )

        # Rate-limit retry: honour Retry-After header on 429, exponential backoff on 503
        # (spec section 14.2).
        retry = 0
        while (
            response.status_code in RATE_LIMIT_RETRY_STATUS_CODES and retry < RATE_LIMIT_MAX_RETRIES
        ):
            backoff = RATE_LIMIT_BASE_BACKOFF_SECONDS * (2**retry)
            # For 429 responses, respect the Retry-After header when present.
            if response.status_code == 429:
                retry_after_header = response.headers.get("Retry-After")
                if retry_after_header is not None:
                    try:
                        backoff = float(retry_after_header)
                    except ValueError:
                        pass
            logger.warning(
                "Calendar API rate-limited (status=%d), retrying in %.1fs (attempt %d/%d)",
                response.status_code,
                backoff,
                retry + 1,
                RATE_LIMIT_MAX_RETRIES,
            )
            await asyncio.sleep(backoff)
            response = await self._request_once(
                method=method,
                url=url,
                params=params,
                json_body=json_body,
                extra_headers=extra_headers,
                force_refresh=False,
            )
            retry += 1

        return response

    async def _request_once(
        self,
        *,
        method: str,
        url: str,
        params: dict[str, Any] | None,
        json_body: dict[str, Any] | None,
        extra_headers: dict[str, str] | None,
        force_refresh: bool,
    ) -> httpx.Response:
        access_token = await self._oauth.get_access_token(force_refresh=force_refresh)
        headers: dict[str, str] = {"Authorization": f"Bearer {access_token}"}
        if extra_headers:
            headers.update(extra_headers)
        try:
            return await self._http_client.request(
                method,
                url,
                params=params,
                json=json_body,
                headers=headers,
            )
        except httpx.HTTPError as exc:
            raise CalendarAuthError(f"Google Calendar request failed: {exc}") from exc

    async def list_events(
        self,
        *,
        calendar_id: str,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        limit: int = 50,
    ) -> list[CalendarEvent]:
        if limit < 1:
            raise ValueError("limit must be at least 1")

        normalized_calendar_id = quote(calendar_id, safe="")
        params: dict[str, Any] = {
            "singleEvents": True,
            "showDeleted": False,
            "orderBy": "startTime",
            "maxResults": min(limit, 250),
        }
        if start_at is not None:
            params["timeMin"] = _google_rfc3339(start_at)
        if end_at is not None:
            params["timeMax"] = _google_rfc3339(end_at)

        payload = await self._request_google_json(
            "GET",
            f"/calendars/{normalized_calendar_id}/events",
            params=params,
        )
        items = payload.get("items")
        if not isinstance(items, list):
            raise CalendarAuthError("Google Calendar list_events response missing items array")

        events: list[CalendarEvent] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            event = _google_event_to_calendar_event(item, fallback_timezone=self._config.timezone)
            if event is not None:
                events.append(event)
        return events

    async def get_event(self, *, calendar_id: str, event_id: str) -> CalendarEvent | None:
        normalized_event_id = event_id.strip()
        if not normalized_event_id:
            raise ValueError("event_id must be a non-empty string")

        normalized_calendar_id = quote(calendar_id, safe="")
        encoded_event_id = quote(normalized_event_id, safe="")
        response = await self._request_with_bearer(
            method="GET",
            path=f"/calendars/{normalized_calendar_id}/events/{encoded_event_id}",
        )

        if response.status_code == 404:
            return None
        if response.status_code < 200 or response.status_code >= 300:
            raise CalendarRequestError(
                status_code=response.status_code,
                message=_safe_google_error_message(response),
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise CalendarAuthError(
                "Google Calendar API returned invalid JSON for get_event"
            ) from exc

        if not isinstance(payload, dict):
            raise CalendarAuthError("Google Calendar API returned an unexpected get_event payload")
        return _google_event_to_calendar_event(payload, fallback_timezone=self._config.timezone)

    async def create_event(
        self,
        *,
        calendar_id: str,
        payload: CalendarEventCreate,
    ) -> CalendarEvent:
        normalized_calendar_id = quote(calendar_id, safe="")
        body = _build_google_event_body(payload)
        response_payload = await self._request_google_json(
            "POST",
            f"/calendars/{normalized_calendar_id}/events",
            json_body=body,
        )
        event = _google_event_to_calendar_event(
            response_payload,
            fallback_timezone=payload.timezone or self._config.timezone,
        )
        if event is None:
            raise CalendarRequestError(
                status_code=200,
                message="Google Calendar returned a cancelled event after create",
            )
        return event

    async def update_event(
        self,
        *,
        calendar_id: str,
        event_id: str,
        patch: CalendarEventUpdate,
    ) -> CalendarEvent:
        normalized_event_id = event_id.strip()
        if not normalized_event_id:
            raise ValueError("event_id must be a non-empty string")

        normalized_calendar_id = quote(calendar_id, safe="")
        encoded_event_id = quote(normalized_event_id, safe="")

        # When only the timezone changes (no time boundaries supplied), we need
        # the existing start/end datetimes to re-emit them with the new timezone.
        # Fetch the current event to obtain existing time boundaries (and the etag
        # when none was supplied by the caller) for optimistic concurrency.
        existing_start_at: datetime | None = None
        existing_end_at: datetime | None = None
        existing_timezone: str | None = None
        fetched_etag: str | None = None

        needs_existing_times = (
            patch.timezone is not None and patch.start_at is None and patch.end_at is None
        )
        if needs_existing_times:
            # Fetch the current event to get both the existing boundaries and
            # (if the caller did not supply one) the etag for optimistic
            # concurrency.  Always raise 404 consistently if the event is gone.
            existing = await self.get_event(
                calendar_id=calendar_id,
                event_id=normalized_event_id,
            )
            if existing is None:
                raise CalendarRequestError(
                    status_code=404,
                    message=f"Event '{normalized_event_id}' not found",
                )
            existing_start_at = existing.start_at
            existing_end_at = existing.end_at
            existing_timezone = existing.timezone
            fetched_etag = existing.etag

        body = _build_google_event_patch_body(
            patch,
            existing_start_at=existing_start_at,
            existing_end_at=existing_end_at,
            existing_timezone=existing_timezone,
        )

        # Build extra headers for optimistic concurrency using the etag.
        # Prefer the caller-supplied etag; fall back to the one fetched above.
        resolved_etag = patch.etag or fetched_etag
        extra_headers: dict[str, str] | None = None
        if resolved_etag is not None:
            extra_headers = {"If-Match": resolved_etag}

        response_payload = await self._request_google_json(
            "PATCH",
            f"/calendars/{normalized_calendar_id}/events/{encoded_event_id}",
            json_body=body,
            extra_headers=extra_headers,
        )

        fallback_tz = patch.timezone or self._config.timezone
        event = _google_event_to_calendar_event(
            response_payload,
            fallback_timezone=fallback_tz,
        )
        if event is None:
            raise CalendarRequestError(
                status_code=200,
                message="Google Calendar returned a cancelled event after update",
            )
        return event

    async def delete_event(
        self,
        *,
        calendar_id: str,
        event_id: str,
        send_updates: str | None = None,
    ) -> None:
        """Delete a Google Calendar event.

        Sends a DELETE request to the Google Calendar API.  A 404 response is
        treated as success (the event was already deleted).  For events with
        attendees, pass ``send_updates="all"`` to send cancellation
        notifications; the default (``None`` / ``"none"``) suppresses emails.

        For recurring events, v1 always operates on the full series: the
        ``event_id`` must be the base recurring-event ID, not an instance ID.
        """
        normalized_event_id = event_id.strip()
        if not normalized_event_id:
            raise ValueError("event_id must be a non-empty string")

        normalized_calendar_id = quote(calendar_id, safe="")
        encoded_event_id = quote(normalized_event_id, safe="")

        params: dict[str, Any] | None = None
        if send_updates is not None:
            normalized_send_updates = send_updates.strip()
            if normalized_send_updates:
                params = {"sendUpdates": normalized_send_updates}

        response = await self._request_with_bearer(
            method="DELETE",
            path=f"/calendars/{normalized_calendar_id}/events/{encoded_event_id}",
            params=params,
        )

        # 404 means the event was already deleted — treat as success.
        if response.status_code == 404:
            logger.debug(
                "delete_event: event '%s' not found (already deleted); treating as success",
                normalized_event_id,
            )
            return

        if response.status_code < 200 or response.status_code >= 300:
            raise CalendarRequestError(
                status_code=response.status_code,
                message=_safe_google_error_message(response),
            )

    async def add_attendees(
        self,
        *,
        calendar_id: str,
        event_id: str,
        attendees: list[str],
        optional: bool = False,
        send_updates: str = "none",
    ) -> CalendarEvent:
        """Add attendees to an event, deduplicating by email (case-insensitive).

        Fetches the current event, merges the new attendees with existing ones
        (preserving all existing attendee fields), then PATCHes the event.
        """
        normalized_event_id = event_id.strip()
        if not normalized_event_id:
            raise ValueError("event_id must be a non-empty string")

        emails_to_add = [e.strip() for e in attendees if e.strip()]
        if not emails_to_add:
            raise ValueError("attendees must contain at least one non-empty email address")

        existing = await self.get_event(calendar_id=calendar_id, event_id=normalized_event_id)
        if existing is None:
            raise CalendarRequestError(
                status_code=404,
                message=f"Event '{normalized_event_id}' not found",
            )

        # Deduplicate by email (case-insensitive), preserving existing attendees.
        existing_emails_lower = {a.email.lower() for a in existing.attendees}
        merged_attendees = list(existing.attendees)
        for email in emails_to_add:
            if email.lower() not in existing_emails_lower:
                merged_attendees.append(
                    AttendeeInfo(
                        email=email,
                        optional=optional,
                    )
                )
                existing_emails_lower.add(email.lower())

        google_attendees = _attendee_info_list_to_google(merged_attendees)
        normalized_calendar_id = quote(calendar_id, safe="")
        encoded_event_id = quote(normalized_event_id, safe="")
        response_payload = await self._request_google_json(
            "PATCH",
            f"/calendars/{normalized_calendar_id}/events/{encoded_event_id}",
            params={"sendUpdates": send_updates},
            json_body={"attendees": google_attendees},
        )
        event = _google_event_to_calendar_event(
            response_payload,
            fallback_timezone=self._config.timezone,
        )
        if event is None:
            raise CalendarRequestError(
                status_code=200,
                message="Google Calendar returned a cancelled event after add_attendees",
            )
        return event

    async def remove_attendees(
        self,
        *,
        calendar_id: str,
        event_id: str,
        attendees: list[str],
        send_updates: str = "none",
    ) -> CalendarEvent:
        """Remove attendees from an event by email (case-insensitive).

        Fetches the current event, filters out the specified email addresses,
        then PATCHes the event.
        """
        normalized_event_id = event_id.strip()
        if not normalized_event_id:
            raise ValueError("event_id must be a non-empty string")

        emails_to_remove_lower = {e.strip().lower() for e in attendees if e.strip()}
        if not emails_to_remove_lower:
            raise ValueError("attendees must contain at least one non-empty email address")

        existing = await self.get_event(calendar_id=calendar_id, event_id=normalized_event_id)
        if existing is None:
            raise CalendarRequestError(
                status_code=404,
                message=f"Event '{normalized_event_id}' not found",
            )

        remaining_attendees = [
            a for a in existing.attendees if a.email.lower() not in emails_to_remove_lower
        ]

        google_attendees = _attendee_info_list_to_google(remaining_attendees)
        normalized_calendar_id = quote(calendar_id, safe="")
        encoded_event_id = quote(normalized_event_id, safe="")
        response_payload = await self._request_google_json(
            "PATCH",
            f"/calendars/{normalized_calendar_id}/events/{encoded_event_id}",
            params={"sendUpdates": send_updates},
            json_body={"attendees": google_attendees},
        )
        event = _google_event_to_calendar_event(
            response_payload,
            fallback_timezone=self._config.timezone,
        )
        if event is None:
            raise CalendarRequestError(
                status_code=200,
                message="Google Calendar returned a cancelled event after remove_attendees",
            )
        return event

    async def find_conflicts(
        self,
        *,
        calendar_id: str,
        candidate: CalendarEventCreate,
    ) -> list[CalendarEvent]:
        # Coerce date/datetime boundaries to datetime for freeBusy API.
        # Do this before the guard comparison to avoid TypeError when one boundary
        # is a date and the other is a datetime (Python 3 does not allow cross-type
        # comparisons between date and datetime).
        timezone_str = candidate.timezone or self._config.timezone
        tz = _coerce_zoneinfo(timezone_str)
        start_at = candidate.start_at
        end_at = candidate.end_at
        if isinstance(start_at, date) and not isinstance(start_at, datetime):
            start_at = datetime(start_at.year, start_at.month, start_at.day, tzinfo=tz)
        if isinstance(end_at, date) and not isinstance(end_at, datetime):
            end_at = datetime(end_at.year, end_at.month, end_at.day, tzinfo=tz)

        if end_at <= start_at:
            raise ValueError("candidate.end_at must be after candidate.start_at")

        payload = await self._request_google_json(
            "POST",
            "/freeBusy",
            json_body={
                "timeMin": _google_rfc3339(start_at),
                "timeMax": _google_rfc3339(end_at),
                "timeZone": timezone_str,
                "items": [{"id": calendar_id}],
            },
        )
        calendars_payload = payload.get("calendars")
        if not isinstance(calendars_payload, dict):
            raise CalendarAuthError("Google Calendar freeBusy response missing calendars object")

        calendar_payload = calendars_payload.get(calendar_id)
        if not isinstance(calendar_payload, dict):
            if len(calendars_payload) == 1:
                calendar_payload = next(iter(calendars_payload.values()))
            else:
                raise CalendarAuthError(
                    "Google Calendar freeBusy response missing calendar entry for requested id"
                )
        if not isinstance(calendar_payload, dict):
            raise CalendarAuthError("Google Calendar freeBusy response calendar entry is invalid")

        busy_payload = calendar_payload.get("busy")
        if not isinstance(busy_payload, list):
            raise CalendarAuthError("Google Calendar freeBusy response missing busy array")

        timezone = candidate.timezone or self._config.timezone
        conflicts: list[CalendarEvent] = []
        for index, window in enumerate(busy_payload):
            if not isinstance(window, dict):
                continue

            start_raw = window.get("start")
            end_raw = window.get("end")
            if not isinstance(start_raw, str) or not isinstance(end_raw, str):
                raise CalendarAuthError(
                    "Google Calendar freeBusy busy windows must include start/end"
                )

            start_at = _parse_google_datetime(start_raw)
            end_at = _parse_google_datetime(end_raw)
            if end_at <= start_at:
                continue

            conflicts.append(
                CalendarEvent(
                    event_id=f"busy-{index + 1}",
                    title="(busy)",
                    start_at=start_at,
                    end_at=end_at,
                    timezone=timezone,
                )
            )
        return conflicts

    async def sync_incremental(
        self,
        *,
        calendar_id: str,
        sync_token: str | None,
        full_sync_window_days: int = DEFAULT_SYNC_WINDOW_DAYS,
    ) -> tuple[list[CalendarEvent], list[str], str]:
        """Fetch incremental changes using Google's syncToken / nextSyncToken flow.

        Performs a full sync when ``sync_token`` is ``None`` (first run or
        after token invalidation).  Returns updated events, cancelled IDs, and
        the token to use on the next call.

        Raises:
            CalendarSyncTokenExpiredError: When Google returns 410 Gone,
                indicating the sync token is no longer valid.
        """
        normalized_calendar_id = quote(calendar_id, safe="")
        params: dict[str, Any] = {
            "showDeleted": True,
            "singleEvents": False,
        }

        if sync_token is not None:
            params["syncToken"] = sync_token
        else:
            # Full sync: restrict to a time window to avoid fetching all history.
            now = datetime.now(UTC)
            window_start = now - timedelta(days=full_sync_window_days)
            params["timeMin"] = _google_rfc3339(window_start)

        updated_events: list[CalendarEvent] = []
        cancelled_event_ids: list[str] = []
        next_page_token: str | None = None
        next_sync_token: str | None = None

        while True:
            if next_page_token is not None:
                params["pageToken"] = next_page_token
            elif "pageToken" in params:
                del params["pageToken"]

            response = await self._request_with_bearer(
                method="GET",
                path=f"/calendars/{normalized_calendar_id}/events",
                params=params,
            )

            # 410 Gone means the sync token is expired; caller must do full re-sync.
            if response.status_code == 410:
                raise CalendarSyncTokenExpiredError(
                    f"Sync token expired for calendar '{calendar_id}'; full re-sync required"
                )

            if response.status_code < 200 or response.status_code >= 300:
                raise CalendarRequestError(
                    status_code=response.status_code,
                    message=_safe_google_error_message(response),
                )

            try:
                payload = response.json()
            except ValueError as exc:
                raise CalendarAuthError(
                    "Google Calendar sync response returned invalid JSON"
                ) from exc

            if not isinstance(payload, dict):
                raise CalendarAuthError(
                    "Google Calendar sync response has unexpected payload shape"
                )

            items = payload.get("items")
            if isinstance(items, list):
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    item_status = item.get("status")
                    event_id_raw = item.get("id")
                    if not isinstance(event_id_raw, str) or not event_id_raw.strip():
                        continue
                    if isinstance(item_status, str) and item_status.lower() == "cancelled":
                        cancelled_event_ids.append(event_id_raw.strip())
                    else:
                        event = _google_event_to_calendar_event(
                            item,
                            fallback_timezone=self._config.timezone,
                        )
                        if event is not None:
                            updated_events.append(event)

            next_page_token = payload.get("nextPageToken") if isinstance(payload, dict) else None
            candidate_sync_token = (
                payload.get("nextSyncToken") if isinstance(payload, dict) else None
            )
            if isinstance(candidate_sync_token, str) and candidate_sync_token.strip():
                next_sync_token = candidate_sync_token.strip()

            if next_page_token is None:
                break

        if next_sync_token is None:
            raise CalendarAuthError(
                f"Google Calendar sync response for '{calendar_id}' did not return nextSyncToken"
            )

        return updated_events, cancelled_event_ids, next_sync_token

    async def shutdown(self) -> None:
        if self._owns_http_client:
            await self._http_client.aclose()


class CalendarModule(Module):
    """Calendar module with provider selection and validated config."""

    _PROVIDER_CLASSES: dict[str, type[CalendarProvider]] = {
        "google": _GoogleProvider,
    }

    def __init__(self) -> None:
        self._config: CalendarConfig | None = None
        self._provider: CalendarProvider | None = None
        self._butler_name: str = DEFAULT_BUTLER_NAME
        self._approval_enqueuer: ApprovalEnqueuer | None = None
        self._db: Any = None
        self._sync_task: asyncio.Task[None] | None = None
        # In-memory sync state cache (calendar_id → CalendarSyncState).
        self._sync_states: dict[str, CalendarSyncState] = {}
        # Event set to trigger immediate sync (for calendar_force_sync tool).
        self._force_sync_event: asyncio.Event = asyncio.Event()

    @property
    def name(self) -> str:
        return "calendar"

    @property
    def config_schema(self) -> type[BaseModel]:
        return CalendarConfig

    @property
    def dependencies(self) -> list[str]:
        return []

    @property
    def credentials_env(self) -> list[str]:
        return [GOOGLE_CALENDAR_CREDENTIALS_ENV]

    def migration_revisions(self) -> str | None:
        return None

    def set_approval_enqueuer(self, enqueuer: ApprovalEnqueuer) -> None:
        """Set the callback used to enqueue overlap override requests for approval.

        The enqueuer receives (tool_name, tool_args, agent_summary) and returns
        an action_id string.  When set, overlap overrides produce
        ``status=approval_required`` instead of writing through immediately.
        """
        self._approval_enqueuer = enqueuer

    @property
    def approvals_enabled(self) -> bool:
        """Whether the approvals integration is active for this module."""
        return self._approval_enqueuer is not None

    @staticmethod
    def _coerce_config(config: Any) -> CalendarConfig:
        return config if isinstance(config, CalendarConfig) else CalendarConfig(**(config or {}))

    async def register_tools(self, mcp: Any, config: Any, db: Any) -> None:
        self._config = self._coerce_config(config)
        self._butler_name = self._resolve_butler_name(db)
        self._db = db
        module = self

        @mcp.tool()
        async def calendar_list_events(
            calendar_id: str | None = None,
            start_at: datetime | None = None,
            end_at: datetime | None = None,
            limit: int = 50,
        ) -> dict[str, Any]:
            """List calendar events using the configured provider.

            Fail-open: returns empty events list with error metadata on provider failure
            rather than raising (spec section 4.4 / 15.2).
            """
            provider = module._require_provider()
            resolved_calendar_id = module._resolve_calendar_id(calendar_id)
            try:
                events = await provider.list_events(
                    calendar_id=resolved_calendar_id,
                    start_at=start_at,
                    end_at=end_at,
                    limit=limit,
                )
            except CalendarAuthError as exc:
                logger.warning(
                    "calendar_list_events failed (calendar_id=%s): %s",
                    resolved_calendar_id,
                    exc,
                    exc_info=True,
                )
                error_dict = _build_structured_error(
                    exc,
                    provider=provider.name,
                    calendar_id=resolved_calendar_id,
                )
                error_dict["events"] = []
                return error_dict
            return {
                "provider": provider.name,
                "calendar_id": resolved_calendar_id,
                "events": [module._event_to_payload(event) for event in events],
            }

        @mcp.tool()
        async def calendar_get_event(
            event_id: str,
            calendar_id: str | None = None,
        ) -> dict[str, Any]:
            """Get a single calendar event by id using the configured provider.

            Fail-open: returns null event with error metadata on provider failure
            rather than raising (spec section 4.4 / 15.2).
            """
            normalized_event_id = event_id.strip()
            if not normalized_event_id:
                raise ValueError("event_id must be a non-empty string")

            provider = module._require_provider()
            resolved_calendar_id = module._resolve_calendar_id(calendar_id)
            try:
                event = await provider.get_event(
                    calendar_id=resolved_calendar_id,
                    event_id=normalized_event_id,
                )
            except CalendarRequestError as exc:
                if exc.status_code == 404:
                    return {
                        "status": "not_found",
                        "provider": provider.name,
                        "calendar_id": resolved_calendar_id,
                        "event": None,
                    }
                logger.warning(
                    "calendar_get_event failed (event_id=%s, calendar_id=%s): %s",
                    normalized_event_id,
                    resolved_calendar_id,
                    exc,
                    exc_info=True,
                )
                error_dict = _build_structured_error(
                    exc,
                    provider=provider.name,
                    calendar_id=resolved_calendar_id,
                )
                error_dict["event"] = None
                return error_dict
            except CalendarAuthError as exc:
                logger.warning(
                    "calendar_get_event failed (event_id=%s, calendar_id=%s): %s",
                    normalized_event_id,
                    resolved_calendar_id,
                    exc,
                    exc_info=True,
                )
                error_dict = _build_structured_error(
                    exc,
                    provider=provider.name,
                    calendar_id=resolved_calendar_id,
                )
                error_dict["event"] = None
                return error_dict
            return {
                "provider": provider.name,
                "calendar_id": resolved_calendar_id,
                "event": None if event is None else module._event_to_payload(event),
            }

        @mcp.tool()
        async def calendar_create_event(
            title: str,
            start_at: date | datetime,
            end_at: date | datetime,
            all_day: bool | None = None,
            timezone: str | None = None,
            description: str | None = None,
            location: str | None = None,
            attendees: list[str] | None = None,
            recurrence_rule: str | None = None,
            notification: CalendarNotificationInput | bool | int | None = None,
            status: EventStatus | None = None,
            visibility: EventVisibility | None = None,
            notes: str | None = None,
            color_id: str | None = None,
            calendar_id: str | None = None,
            conflict_policy: CalendarConflictPolicy | None = None,
        ) -> dict[str, Any]:
            """Create an event and mark it as Butler-generated.

            For all-day events, pass date objects (not datetime) for start_at and
            end_at, or set all_day=True with date-only values.
            Fail-closed: provider errors return a structured error dict rather than
            silently dropping the mutation (spec section 4.4 / 15.2).
            """
            provider = module._require_provider()
            resolved_calendar_id = module._resolve_calendar_id(calendar_id)
            resolved_conflict_policy = module._resolve_conflict_policy(conflict_policy)

            try:
                create_payload = CalendarEventCreate(
                    title=module._ensure_butler_title(title),
                    start_at=start_at,
                    end_at=end_at,
                    all_day=all_day,
                    timezone=timezone,
                    description=description,
                    location=location,
                    attendees=attendees or [],
                    recurrence_rule=recurrence_rule,
                    notification=notification,
                    status=status,
                    visibility=visibility,
                    notes=notes,
                    color_id=color_id,
                    private_metadata=module._build_butler_private_metadata(
                        butler_name=module._butler_name
                    ),
                )
                conflict_result = await module._check_conflicts(
                    provider=provider,
                    calendar_id=resolved_calendar_id,
                    candidate=create_payload,
                    conflict_policy=resolved_conflict_policy,
                )
            except CalendarAuthError as exc:
                logger.error(
                    "calendar_create_event failed during conflict check (calendar_id=%s): %s",
                    resolved_calendar_id,
                    exc,
                    exc_info=True,
                )
                return _build_structured_error(
                    exc,
                    provider=provider.name,
                    calendar_id=resolved_calendar_id,
                )
            if conflict_result["status"] == "conflict":
                return {
                    "status": "conflict",
                    "policy": resolved_conflict_policy,
                    "provider": provider.name,
                    "calendar_id": resolved_calendar_id,
                    "conflicts": conflict_result["conflicts"],
                    "suggested_slots": conflict_result["suggested_slots"],
                }

            # Gate overlap override with conditional approval when configured.
            if conflict_result["status"] == "allow_overlap":
                approval_result = await module._gate_overlap_approval(
                    tool_name="calendar_create_event",
                    tool_args={
                        "title": title,
                        "start_at": start_at.isoformat(),
                        "end_at": end_at.isoformat(),
                        "all_day": all_day,
                        "timezone": timezone,
                        "description": description,
                        "location": location,
                        "attendees": attendees or [],
                        "recurrence_rule": recurrence_rule,
                        "notification": notification,
                        "status": status,
                        "visibility": visibility,
                        "notes": notes,
                        "color_id": color_id,
                        "calendar_id": calendar_id,
                        "conflict_policy": resolved_conflict_policy,
                    },
                    conflicts=conflict_result["conflicts"],
                    provider_name=provider.name,
                    resolved_calendar_id=resolved_calendar_id,
                )
                if approval_result is not None:
                    return approval_result

            try:
                event = await provider.create_event(
                    calendar_id=resolved_calendar_id,
                    payload=create_payload,
                )
            except CalendarAuthError as exc:
                logger.error(
                    "calendar_create_event provider write failed (calendar_id=%s): %s",
                    resolved_calendar_id,
                    exc,
                    exc_info=True,
                )
                return _build_structured_error(
                    exc,
                    provider=provider.name,
                    calendar_id=resolved_calendar_id,
                )
            result: dict[str, Any] = {
                "status": "created",
                "provider": provider.name,
                "calendar_id": resolved_calendar_id,
                "event": module._event_to_payload(event),
            }
            if conflict_result["conflicts"]:
                result["policy"] = resolved_conflict_policy
                result["conflicts"] = conflict_result["conflicts"]
                result["suggested_slots"] = []
            return result

        @mcp.tool()
        async def calendar_update_event(
            event_id: str,
            title: str | None = None,
            start_at: datetime | None = None,
            end_at: datetime | None = None,
            timezone: str | None = None,
            description: str | None = None,
            location: str | None = None,
            attendees: list[str] | None = None,
            recurrence_rule: str | None = None,
            recurrence_scope: Literal["series"] = "series",
            color_id: str | None = None,
            calendar_id: str | None = None,
            conflict_policy: CalendarConflictPolicy | None = None,
        ) -> dict[str, Any]:
            """Update an event and preserve Butler tags for Butler-generated entries.

            Recurrence updates are series-scoped in v1.
            Fail-closed: provider errors return a structured error dict rather than
            silently dropping the mutation (spec section 4.4 / 15.2).
            """
            normalized_event_id = event_id.strip()
            if not normalized_event_id:
                raise ValueError("event_id must be a non-empty string")

            provider = module._require_provider()
            resolved_calendar_id = module._resolve_calendar_id(calendar_id)
            resolved_conflict_policy = module._resolve_conflict_policy(conflict_policy)
            try:
                existing_event = await provider.get_event(
                    calendar_id=resolved_calendar_id,
                    event_id=normalized_event_id,
                )
            except CalendarAuthError as exc:
                logger.error(
                    "calendar_update_event failed fetching existing event "
                    "(event_id=%s, calendar_id=%s): %s",
                    normalized_event_id,
                    resolved_calendar_id,
                    exc,
                    exc_info=True,
                )
                return _build_structured_error(
                    exc,
                    provider=provider.name,
                    calendar_id=resolved_calendar_id,
                )
            if existing_event is None:
                raise ValueError(f"event_id '{normalized_event_id}' was not found")

            normalized_title = title.strip() if isinstance(title, str) else None
            update_title = normalized_title
            private_metadata: dict[str, str] | None = None
            if existing_event.butler_generated:
                update_title = module._ensure_butler_title(
                    existing_event.title if update_title is None else update_title
                )
                private_metadata = module._build_butler_private_metadata(
                    butler_name=existing_event.butler_name or module._butler_name
                )

            conflict_result: dict[str, Any] = {
                "status": "clear",
                "conflicts": [],
                "suggested_slots": [],
            }
            if module._time_window_changed(
                existing_event=existing_event,
                start_at=start_at,
                end_at=end_at,
            ):
                try:
                    candidate = CalendarEventCreate(
                        title=update_title or existing_event.title,
                        start_at=start_at if start_at is not None else existing_event.start_at,
                        end_at=end_at if end_at is not None else existing_event.end_at,
                        timezone=timezone or existing_event.timezone,
                        description=(
                            description if description is not None else existing_event.description
                        ),
                        location=location if location is not None else existing_event.location,
                        attendees=(
                            attendees
                            if attendees is not None
                            else [a.email for a in existing_event.attendees]
                        ),
                        recurrence_rule=(
                            recurrence_rule
                            if recurrence_rule is not None
                            else existing_event.recurrence_rule
                        ),
                        color_id=color_id if color_id is not None else existing_event.color_id,
                    )
                    conflict_result = await module._check_conflicts(
                        provider=provider,
                        calendar_id=resolved_calendar_id,
                        candidate=candidate,
                        conflict_policy=resolved_conflict_policy,
                        ignore_start_at=existing_event.start_at,
                        ignore_end_at=existing_event.end_at,
                    )
                except CalendarAuthError as exc:
                    logger.error(
                        "calendar_update_event failed during conflict check "
                        "(event_id=%s, calendar_id=%s): %s",
                        normalized_event_id,
                        resolved_calendar_id,
                        exc,
                        exc_info=True,
                    )
                    return _build_structured_error(
                        exc,
                        provider=provider.name,
                        calendar_id=resolved_calendar_id,
                    )
                if conflict_result["status"] == "conflict":
                    return {
                        "status": "conflict",
                        "policy": resolved_conflict_policy,
                        "provider": provider.name,
                        "calendar_id": resolved_calendar_id,
                        "conflicts": conflict_result["conflicts"],
                        "suggested_slots": conflict_result["suggested_slots"],
                    }

                # Gate overlap override with conditional approval when configured.
                if conflict_result["status"] == "allow_overlap":
                    approval_result = await module._gate_overlap_approval(
                        tool_name="calendar_update_event",
                        tool_args={
                            "event_id": event_id,
                            "title": title,
                            "start_at": start_at.isoformat() if start_at else None,
                            "end_at": end_at.isoformat() if end_at else None,
                            "timezone": timezone,
                            "description": description,
                            "location": location,
                            "attendees": attendees,
                            "recurrence_rule": recurrence_rule,
                            "recurrence_scope": recurrence_scope,
                            "color_id": color_id,
                            "calendar_id": calendar_id,
                            "conflict_policy": resolved_conflict_policy,
                        },
                        conflicts=conflict_result["conflicts"],
                        provider_name=provider.name,
                        resolved_calendar_id=resolved_calendar_id,
                    )
                    if approval_result is not None:
                        return approval_result

            update_patch = CalendarEventUpdate(
                title=update_title,
                start_at=start_at,
                end_at=end_at,
                timezone=timezone,
                description=description,
                location=location,
                attendees=attendees,
                recurrence_rule=recurrence_rule,
                recurrence_scope=recurrence_scope,
                color_id=color_id,
                private_metadata=private_metadata,
                etag=existing_event.etag,
            )
            try:
                event = await provider.update_event(
                    calendar_id=resolved_calendar_id,
                    event_id=normalized_event_id,
                    patch=update_patch,
                )
            except CalendarAuthError as exc:
                logger.error(
                    "calendar_update_event provider write failed (event_id=%s, calendar_id=%s): %s",
                    normalized_event_id,
                    resolved_calendar_id,
                    exc,
                    exc_info=True,
                )
                return _build_structured_error(
                    exc,
                    provider=provider.name,
                    calendar_id=resolved_calendar_id,
                )
            result = {
                "status": "updated",
                "provider": provider.name,
                "calendar_id": resolved_calendar_id,
                "event": module._event_to_payload(event),
            }
            if conflict_result["conflicts"]:
                result["policy"] = resolved_conflict_policy
                result["conflicts"] = conflict_result["conflicts"]
                result["suggested_slots"] = []
            return result

        @mcp.tool()
        async def calendar_delete_event(
            event_id: str,
            calendar_id: str | None = None,
            recurrence_scope: Literal["series"] = "series",
            send_updates: str | None = None,
        ) -> dict[str, Any]:
            """Delete or cancel a calendar event.

            For events with attendees, pass send_updates="all" to send
            cancellation notifications.  By default (send_updates=None),
            no notification emails are sent.

            Recurring events: v1 supports series-scoped deletion only
            (recurrence_scope="series").  Pass the base recurring-event ID,
            not an individual occurrence ID.

            Returns status="deleted" on success, or status="not_found" when
            the event did not exist (already deleted — treated as success).
            """
            normalized_event_id = event_id.strip()
            if not normalized_event_id:
                raise ValueError("event_id must be a non-empty string")

            provider = module._require_provider()
            resolved_calendar_id = module._resolve_calendar_id(calendar_id)

            # Fetch the event first to confirm existence and capture metadata.
            # 404 from the provider is treated as success (idempotent delete).
            existing_event = await provider.get_event(
                calendar_id=resolved_calendar_id,
                event_id=normalized_event_id,
            )
            if existing_event is None:
                return {
                    "status": "not_found",
                    "provider": provider.name,
                    "calendar_id": resolved_calendar_id,
                    "event_id": normalized_event_id,
                }

            await provider.delete_event(
                calendar_id=resolved_calendar_id,
                event_id=normalized_event_id,
                send_updates=send_updates,
            )
            return {
                "status": "deleted",
                "provider": provider.name,
                "calendar_id": resolved_calendar_id,
                "event_id": normalized_event_id,
            }

        @mcp.tool()
        async def calendar_add_attendees(
            event_id: str,
            attendees: list[str],
            optional: bool = False,
            calendar_id: str | None = None,
            send_updates: SendUpdatesPolicy = SendUpdatesPolicy.none,
        ) -> dict[str, Any]:
            """Add attendees to an existing calendar event.

            Attendees are added by email address with deduplication (existing
            attendees are preserved). The optional flag applies to all newly
            added attendees. The send_updates policy controls whether Google
            sends notification emails to attendees.

            Fail-closed: provider errors return a structured error dict.
            """
            normalized_event_id = event_id.strip()
            if not normalized_event_id:
                raise ValueError("event_id must be a non-empty string")

            normalized_attendees = [e.strip() for e in attendees if e.strip()]
            if not normalized_attendees:
                raise ValueError("attendees must contain at least one non-empty email address")

            provider = module._require_provider()
            resolved_calendar_id = module._resolve_calendar_id(calendar_id)
            try:
                event = await provider.add_attendees(
                    calendar_id=resolved_calendar_id,
                    event_id=normalized_event_id,
                    attendees=normalized_attendees,
                    optional=optional,
                    send_updates=send_updates.value,
                )
            except CalendarAuthError as exc:
                logger.error(
                    "calendar_add_attendees failed (event_id=%s, calendar_id=%s): %s",
                    normalized_event_id,
                    resolved_calendar_id,
                    exc,
                    exc_info=True,
                )
                return _build_structured_error(
                    exc,
                    provider=provider.name,
                    calendar_id=resolved_calendar_id,
                )
            return {
                "status": "updated",
                "provider": provider.name,
                "calendar_id": resolved_calendar_id,
                "event": module._event_to_payload(event),
            }

        @mcp.tool()
        async def calendar_remove_attendees(
            event_id: str,
            attendees: list[str],
            calendar_id: str | None = None,
            send_updates: SendUpdatesPolicy = SendUpdatesPolicy.none,
        ) -> dict[str, Any]:
            """Remove attendees from an existing calendar event by email address.

            Attendees whose email addresses match (case-insensitive) are removed.
            The send_updates policy controls whether Google sends cancellation
            notifications to removed attendees.

            Fail-closed: provider errors return a structured error dict.
            """
            normalized_event_id = event_id.strip()
            if not normalized_event_id:
                raise ValueError("event_id must be a non-empty string")

            normalized_attendees = [e.strip() for e in attendees if e.strip()]
            if not normalized_attendees:
                raise ValueError("attendees must contain at least one non-empty email address")

            provider = module._require_provider()
            resolved_calendar_id = module._resolve_calendar_id(calendar_id)
            try:
                event = await provider.remove_attendees(
                    calendar_id=resolved_calendar_id,
                    event_id=normalized_event_id,
                    attendees=normalized_attendees,
                    send_updates=send_updates.value,
                )
            except CalendarAuthError as exc:
                logger.error(
                    "calendar_remove_attendees failed (event_id=%s, calendar_id=%s): %s",
                    normalized_event_id,
                    resolved_calendar_id,
                    exc,
                    exc_info=True,
                )
                return _build_structured_error(
                    exc,
                    provider=provider.name,
                    calendar_id=resolved_calendar_id,
                )
            return {
                "status": "updated",
                "provider": provider.name,
                "calendar_id": resolved_calendar_id,
                "event": module._event_to_payload(event),
            }

        @mcp.tool()
        async def calendar_sync_status(
            calendar_id: str | None = None,
        ) -> dict[str, Any]:
            """Return sync state for the configured calendar (spec section 13.5).

            Returns the last sync time, whether the sync token is valid,
            pending changes count, and any last sync error.

            Fail-open: returns state with ``sync_enabled=False`` when sync
            is not configured rather than raising.
            """
            resolved_calendar_id = module._resolve_calendar_id(calendar_id)
            cfg = module._require_config()
            provider = module._require_provider()

            if not cfg.sync.enabled:
                return {
                    "status": "ok",
                    "sync_enabled": False,
                    "provider": provider.name,
                    "calendar_id": resolved_calendar_id,
                    "last_sync_at": None,
                    "sync_token_valid": False,
                    "last_batch_change_count": 0,
                    "last_sync_error": None,
                }

            # Use cached in-memory state when available; fall back to KV store.
            sync_state = module._sync_states.get(resolved_calendar_id)
            if sync_state is None:
                try:
                    sync_state = await module._load_sync_state(resolved_calendar_id)
                except Exception as exc:
                    logger.warning(
                        "calendar_sync_status: failed to load state for '%s': %s",
                        resolved_calendar_id,
                        exc,
                    )
                    sync_state = CalendarSyncState()

            return {
                "status": "ok",
                "sync_enabled": True,
                "provider": provider.name,
                "calendar_id": resolved_calendar_id,
                "last_sync_at": sync_state.last_sync_at,
                "sync_token_valid": sync_state.sync_token is not None,
                "last_batch_change_count": sync_state.last_batch_change_count,
                "last_sync_error": sync_state.last_sync_error,
            }

        @mcp.tool()
        async def calendar_force_sync(
            calendar_id: str | None = None,
        ) -> dict[str, Any]:
            """Trigger an immediate sync outside the normal polling schedule (spec section 13.5).

            If sync is not enabled in config, performs a one-off incremental sync
            and returns the result immediately.  If the background poller is
            running, signals it to execute immediately.

            Fail-open: provider errors are recorded in last_sync_error rather than raised.
            """
            resolved_calendar_id = module._resolve_calendar_id(calendar_id)
            cfg = module._require_config()
            provider = module._require_provider()

            if cfg.sync.enabled and module._sync_task is not None:
                # Signal the background poller to sync immediately.
                module._force_sync_event.set()
                return {
                    "status": "sync_triggered",
                    "provider": provider.name,
                    "calendar_id": resolved_calendar_id,
                    "message": (
                        "Immediate sync has been triggered; check calendar_sync_status for results."
                    ),
                }

            # Sync not running as background task: run a one-off sync inline.
            # _sync_calendar swallows provider errors (fail-open, spec section 4.4).
            # Any error is captured in the sync state's last_sync_error field.
            await module._sync_calendar(resolved_calendar_id)

            sync_state = module._sync_states.get(resolved_calendar_id, CalendarSyncState())
            return {
                "status": "sync_completed",
                "provider": provider.name,
                "calendar_id": resolved_calendar_id,
                "last_sync_at": sync_state.last_sync_at,
                "last_batch_change_count": sync_state.last_batch_change_count,
                "last_sync_error": sync_state.last_sync_error,
            }

    async def on_startup(self, config: Any, db: Any) -> None:
        self._config = self._coerce_config(config)
        self._db = db

        provider_cls = self._PROVIDER_CLASSES.get(self._config.provider)
        if provider_cls is None:
            supported = ", ".join(sorted(self._PROVIDER_CLASSES))
            raise RuntimeError(
                f"Unsupported calendar provider '{self._config.provider}'. "
                f"Supported providers: {supported}"
            )

        # Resolve Google OAuth credentials: DB-first with env fallback.
        # The pool is available on db.pool when a Database instance is passed.
        pool = getattr(db, "pool", None) if db is not None else None
        if pool is not None:
            google_creds_shared = await resolve_google_credentials(pool, caller="calendar")
            credentials = _GoogleOAuthCredentials(
                client_id=google_creds_shared.client_id,
                client_secret=google_creds_shared.client_secret,
                refresh_token=google_creds_shared.refresh_token,
            )
        else:
            # No DB available (e.g. tests without a real pool) — fall back to env vars.
            credentials = _GoogleOAuthCredentials.from_env()

        self._provider = provider_cls(self._config, credentials)

        if self._config.sync.enabled:
            self._sync_task = asyncio.create_task(
                self._run_sync_poller(), name="calendar-sync-poller"
            )
            logger.info(
                "Calendar sync poller started (interval=%dm, calendar_id=%s)",
                self._config.sync.interval_minutes,
                self._config.calendar_id,
            )

    async def on_shutdown(self) -> None:
        if self._sync_task is not None and not self._sync_task.done():
            self._sync_task.cancel()
            try:
                await self._sync_task
            except asyncio.CancelledError:
                pass
        self._sync_task = None

        if self._provider is not None:
            await self._provider.shutdown()
        self._provider = None

    # ------------------------------------------------------------------
    # Sync infrastructure
    # ------------------------------------------------------------------

    def _sync_state_key(self, calendar_id: str) -> str:
        """Return the KV state store key for the given calendar's sync state."""
        return f"{SYNC_STATE_KEY_PREFIX}{calendar_id}"

    async def _load_sync_state(self, calendar_id: str) -> CalendarSyncState:
        """Load sync state from the KV store, returning a default if not found."""
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            return CalendarSyncState()
        key = self._sync_state_key(calendar_id)
        raw = await _state_get(pool, key)
        if not isinstance(raw, dict):
            return CalendarSyncState()
        return CalendarSyncState(**raw)

    async def _save_sync_state(self, calendar_id: str, state: CalendarSyncState) -> None:
        """Persist sync state to the KV store."""
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            logger.warning("Cannot persist sync state: database pool not available")
            return
        key = self._sync_state_key(calendar_id)
        await _state_set(pool, key, state.model_dump())

    async def _sync_calendar(self, calendar_id: str) -> None:
        """Run one incremental sync cycle for ``calendar_id``.

        Loads the saved sync token, calls the provider's sync endpoint, handles
        token expiration (falls back to full sync), and persists the new token.
        Errors are logged and swallowed so the poller stays alive.
        """
        provider = self._provider
        if provider is None:
            return

        config = self._require_config()
        sync_state = self._sync_states.get(calendar_id) or await self._load_sync_state(calendar_id)

        try:
            updated_events, cancelled_ids, next_token = await provider.sync_incremental(
                calendar_id=calendar_id,
                sync_token=sync_state.sync_token,
                full_sync_window_days=config.sync.full_sync_window_days,
            )
        except CalendarSyncTokenExpiredError:
            logger.warning(
                "Sync token expired for calendar '%s'; performing full re-sync", calendar_id
            )
            try:
                updated_events, cancelled_ids, next_token = await provider.sync_incremental(
                    calendar_id=calendar_id,
                    sync_token=None,
                    full_sync_window_days=config.sync.full_sync_window_days,
                )
            except CalendarAuthError as exc:
                logger.error(
                    "Full re-sync failed for calendar '%s': %s",
                    calendar_id,
                    exc,
                    exc_info=True,
                )
                sync_state.last_sync_error = str(exc)[:200]
                self._sync_states[calendar_id] = sync_state
                await self._save_sync_state(calendar_id, sync_state)
                return
        except CalendarAuthError as exc:
            logger.error(
                "Incremental sync failed for calendar '%s': %s",
                calendar_id,
                exc,
                exc_info=True,
            )
            sync_state.last_sync_error = str(exc)[:200]
            self._sync_states[calendar_id] = sync_state
            await self._save_sync_state(calendar_id, sync_state)
            return

        pending_count = len(updated_events) + len(cancelled_ids)
        now_iso = datetime.now(UTC).isoformat()
        new_state = CalendarSyncState(
            sync_token=next_token,
            last_sync_at=now_iso,
            last_sync_error=None,
            last_batch_change_count=pending_count,
        )
        self._sync_states[calendar_id] = new_state
        await self._save_sync_state(calendar_id, new_state)

        logger.info(
            "Calendar sync completed (calendar_id=%s, updated=%d, cancelled=%d)",
            calendar_id,
            len(updated_events),
            len(cancelled_ids),
        )

    async def _run_sync_poller(self) -> None:
        """Background task: poll for calendar changes at the configured interval.

        The poller also listens for a ``_force_sync_event`` to trigger
        immediate syncs (used by the ``calendar_force_sync`` MCP tool).
        """
        config = self._require_config()
        interval_seconds = config.sync.interval_minutes * 60

        logger.debug("Calendar sync poller loop started (interval=%ds)", interval_seconds)
        while True:
            try:
                # Run sync for the primary calendar.
                await self._sync_calendar(config.calendar_id)
            except Exception as exc:
                logger.error("Calendar sync poller error: %s", exc, exc_info=True)

            # Wait for the configured interval OR for an immediate-sync request.
            try:
                await asyncio.wait_for(
                    self._force_sync_event.wait(),
                    timeout=interval_seconds,
                )
                # Force sync was requested; reset the event and loop immediately.
                self._force_sync_event.clear()
                logger.debug("Calendar sync poller: immediate sync triggered via force_sync")
            except TimeoutError:
                # Normal timer expiry; loop and sync again.
                pass

    # ------------------------------------------------------------------
    # Provider / config accessors
    # ------------------------------------------------------------------

    def _require_provider(self) -> CalendarProvider:
        if self._provider is None:
            raise RuntimeError("Calendar provider is not initialized; call on_startup first")
        return self._provider

    def _require_config(self) -> CalendarConfig:
        if self._config is None:
            raise RuntimeError("Calendar config is not initialized")
        return self._config

    def _resolve_calendar_id(self, override_calendar_id: str | None) -> str:
        if override_calendar_id is None:
            return self._require_config().calendar_id

        normalized = override_calendar_id.strip()
        if not normalized:
            raise ValueError("calendar_id must be a non-empty string when provided")
        return normalized

    @staticmethod
    def _resolve_butler_name(db: Any) -> str:
        db_name = getattr(db, "db_name", None)
        if isinstance(db_name, str):
            normalized = db_name.strip()
            if normalized:
                return normalized.removeprefix("butler_") or DEFAULT_BUTLER_NAME
        return DEFAULT_BUTLER_NAME

    @staticmethod
    def _ensure_butler_title(title: str) -> str:
        normalized = title.strip()
        if not normalized:
            raise ValueError("title must be a non-empty string")

        prefix_len = len(BUTLER_EVENT_TITLE_PREFIX)
        if normalized[:prefix_len].upper() == BUTLER_EVENT_TITLE_PREFIX:
            suffix = normalized[prefix_len:].lstrip()
        else:
            suffix = normalized
        return BUTLER_EVENT_TITLE_PREFIX if not suffix else f"{BUTLER_EVENT_TITLE_PREFIX} {suffix}"

    @staticmethod
    def _build_butler_private_metadata(*, butler_name: str = DEFAULT_BUTLER_NAME) -> dict[str, str]:
        normalized_name = butler_name.strip() or DEFAULT_BUTLER_NAME
        return {
            BUTLER_GENERATED_PRIVATE_KEY: "true",
            BUTLER_NAME_PRIVATE_KEY: normalized_name,
        }

    def _resolve_conflict_policy(
        self, policy_override: CalendarConflictPolicy | str | None
    ) -> CalendarConflictPolicy:
        if policy_override is None:
            return self._require_config().conflicts.policy

        normalized = policy_override.strip().lower()
        normalized = LEGACY_CONFLICT_POLICY_ALIASES.get(normalized, normalized)
        if normalized not in VALID_CONFLICT_POLICIES:
            supported = ", ".join(sorted(VALID_CONFLICT_POLICIES))
            raise ValueError(f"conflict_policy must be one of: {supported}")
        return normalized  # type: ignore[return-value]

    async def _gate_overlap_approval(
        self,
        *,
        tool_name: str,
        tool_args: dict[str, Any],
        conflicts: list[dict[str, str]],
        provider_name: str,
        resolved_calendar_id: str,
    ) -> dict[str, Any] | None:
        """Gate an overlap override through the approval queue when configured.

        Returns a structured ``approval_required`` response when approval is
        needed, or ``None`` to let the caller proceed with the write.

        When ``require_approval_for_overlap`` is False the method always returns
        ``None`` (write-through).  When it is True and the approval enqueuer is
        not wired (approvals module disabled), returns explicit fallback
        guidance instead.
        """
        config = self._require_config()
        if not config.conflicts.require_approval_for_overlap:
            return None

        if self._approval_enqueuer is None:
            # Approvals module is not enabled -- return fallback guidance.
            return {
                "status": "approval_unavailable",
                "policy": "allow_overlap",
                "provider": provider_name,
                "calendar_id": resolved_calendar_id,
                "conflicts": conflicts,
                "message": (
                    "Overlap override requires approval but the approvals module "
                    "is not enabled on this butler. Enable the approvals module or "
                    "set require_approval_for_overlap=false in calendar config to "
                    "allow direct overlap writes."
                ),
            }

        agent_summary = (
            f"Calendar overlap override: {tool_name} with {len(conflicts)} "
            f"conflicting event(s) on calendar '{resolved_calendar_id}'"
        )
        action_id = await self._approval_enqueuer(tool_name, tool_args, agent_summary)
        logger.info(
            "Overlap override queued for approval (action=%s, tool=%s, conflicts=%d)",
            action_id,
            tool_name,
            len(conflicts),
        )
        return {
            "status": "approval_required",
            "action_id": action_id,
            "policy": "allow_overlap",
            "provider": provider_name,
            "calendar_id": resolved_calendar_id,
            "conflicts": conflicts,
            "message": (
                f"Overlap detected with {len(conflicts)} existing event(s). "
                "The write has been queued for approval before execution."
            ),
        }

    async def _check_conflicts(
        self,
        *,
        provider: CalendarProvider,
        calendar_id: str,
        candidate: CalendarEventCreate,
        conflict_policy: CalendarConflictPolicy,
        ignore_start_at: datetime | None = None,
        ignore_end_at: datetime | None = None,
    ) -> dict[str, Any]:
        if candidate.end_at <= candidate.start_at:
            raise ValueError("end_at must be after start_at")

        conflicts = await provider.find_conflicts(calendar_id=calendar_id, candidate=candidate)
        if ignore_start_at is not None and ignore_end_at is not None:
            conflicts = [
                conflict
                for conflict in conflicts
                if not (conflict.start_at == ignore_start_at and conflict.end_at == ignore_end_at)
            ]

        conflict_payload = [self._conflict_to_payload(conflict) for conflict in conflicts]
        if not conflict_payload:
            return {"status": "clear", "conflicts": [], "suggested_slots": []}

        if conflict_policy == "allow_overlap":
            return {
                "status": "allow_overlap",
                "conflicts": conflict_payload,
                "suggested_slots": [],
            }

        suggested_slots: list[dict[str, str]] = []
        if conflict_policy == "suggest":
            suggested_slots = self._build_suggested_slots(
                candidate,
                conflicts,
                count=DEFAULT_CONFLICT_SUGGESTION_COUNT,
            )
        return {
            "status": "conflict",
            "conflicts": conflict_payload,
            "suggested_slots": suggested_slots,
        }

    @staticmethod
    def _time_window_changed(
        *,
        existing_event: CalendarEvent,
        start_at: datetime | None,
        end_at: datetime | None,
    ) -> bool:
        if start_at is not None and start_at != existing_event.start_at:
            return True
        if end_at is not None and end_at != existing_event.end_at:
            return True
        return False

    @staticmethod
    def _conflict_to_payload(conflict: CalendarEvent) -> dict[str, str]:
        return {
            "event_id": conflict.event_id,
            "title": conflict.title,
            "start_at": conflict.start_at.isoformat(),
            "end_at": conflict.end_at.isoformat(),
            "timezone": conflict.timezone,
        }

    @staticmethod
    def _build_suggested_slots(
        candidate: CalendarEventCreate,
        conflicts: list[CalendarEvent],
        *,
        count: int,
    ) -> list[dict[str, str]]:
        if count < 1:
            return []
        duration = candidate.end_at - candidate.start_at
        if duration <= timedelta(0):
            return []

        last_conflict_end = max(conflict.end_at for conflict in conflicts)
        cursor = max(candidate.start_at, last_conflict_end)

        suggestions: list[dict[str, str]] = []
        for _ in range(count):
            suggestion_start = cursor
            suggestion_end = suggestion_start + duration
            suggestions.append(
                {
                    "start_at": suggestion_start.isoformat(),
                    "end_at": suggestion_end.isoformat(),
                    "timezone": candidate.timezone or "UTC",
                }
            )
            cursor = suggestion_end + timedelta(minutes=15)
        return suggestions

    @staticmethod
    def _attendee_to_payload(attendee: AttendeeInfo) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "email": attendee.email,
            "display_name": attendee.display_name,
            "response_status": attendee.response_status.value,
            "optional": attendee.optional,
            "organizer": attendee.organizer,
            "self": attendee.self_,
            "comment": attendee.comment,
        }
        return payload

    @staticmethod
    def _event_to_payload(event: CalendarEvent) -> dict[str, Any]:
        return {
            "event_id": event.event_id,
            "title": event.title,
            "start_at": event.start_at.isoformat(),
            "end_at": event.end_at.isoformat(),
            "timezone": event.timezone,
            "description": event.description,
            "location": event.location,
            "attendees": [CalendarModule._attendee_to_payload(a) for a in event.attendees],
            "recurrence_rule": event.recurrence_rule,
            "color_id": event.color_id,
            "butler_generated": event.butler_generated,
            "butler_name": event.butler_name,
            "status": event.status.value if event.status is not None else None,
            "organizer": event.organizer,
            "visibility": event.visibility.value if event.visibility is not None else None,
            "etag": event.etag,
            "created_at": event.created_at.isoformat() if event.created_at is not None else None,
            "updated_at": event.updated_at.isoformat() if event.updated_at is not None else None,
        }
