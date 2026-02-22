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
import re
import uuid
from collections.abc import Callable, Coroutine, Mapping
from datetime import UTC, date, datetime, timedelta, tzinfo
from enum import StrEnum
from typing import Any, Literal
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

from butlers.core.scheduler import schedule_create as _schedule_create
from butlers.core.scheduler import schedule_delete as _schedule_delete
from butlers.core.scheduler import schedule_update as _schedule_update
from butlers.core.state import state_get as _state_get
from butlers.core.state import state_set as _state_set
from butlers.modules.base import Module

logger = logging.getLogger(__name__)

# Type alias for the approval enqueue callback.
# Receives (tool_name, tool_args, agent_summary) and returns the action_id string.
ApprovalEnqueuer = Callable[
    [str, dict[str, Any], str],
    Coroutine[Any, Any, str],
]

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
# Calendar projection sync cursor names.
SYNC_CURSOR_PROVIDER = "provider_sync"
SYNC_CURSOR_PROJECTION = "projection"
# Default full sync window in days when no sync token exists (spec section 12.5).
DEFAULT_SYNC_WINDOW_DAYS = 30
# Default sync interval in minutes (spec section 12.5).
DEFAULT_SYNC_INTERVAL_MINUTES = 5
# Projection staleness grace period multiplier over the configured sync interval.
PROJECTION_STALENESS_MULTIPLIER = 2
# Projection table source constants.
SOURCE_KIND_PROVIDER = "provider_event"
SOURCE_KIND_INTERNAL_SCHEDULER = "internal_scheduler"
SOURCE_KIND_INTERNAL_REMINDERS = "internal_reminders"
# Projection status constants surfaced to sync/status consumers.
PROJECTION_STATUS_FRESH = "fresh"
PROJECTION_STATUS_STALE = "stale"
PROJECTION_STATUS_FAILED = "failed"
MUTATION_STATUS_PENDING = "pending"
MUTATION_STATUS_APPLIED = "applied"
MUTATION_STATUS_FAILED = "failed"
MUTATION_STATUS_NOOP = "noop"
BUTLER_EVENT_SOURCE_SCHEDULED = "scheduled_task"
BUTLER_EVENT_SOURCE_REMINDER = "butler_reminder"
MUTATION_HIGH_IMPACT_ACTIONS = {
    "workspace_butler_delete",
    "workspace_butler_toggle",
}

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
    def from_json(cls, raw_value: str) -> _GoogleOAuthCredentials:
        try:
            payload = json.loads(raw_value)
        except json.JSONDecodeError as exc:
            raise CalendarCredentialError(f"Credential JSON must be valid JSON: {exc.msg}") from exc

        if not isinstance(payload, dict):
            raise CalendarCredentialError("Credential JSON must decode to a JSON object")

        credential_data = {
            "client_id": _extract_google_credential_value(payload, "client_id"),
            "client_secret": _extract_google_credential_value(payload, "client_secret"),
            "refresh_token": _extract_google_credential_value(payload, "refresh_token"),
        }

        missing = sorted(key for key, value in credential_data.items() if value is None)
        if missing:
            field_list = ", ".join(missing)
            raise CalendarCredentialError(
                f"Credential JSON is missing required field(s): {field_list}"
            )

        invalid = sorted(
            key
            for key, value in credential_data.items()
            if not isinstance(value, str) or not value.strip()
        )
        if invalid:
            field_list = ", ".join(invalid)
            raise CalendarCredentialError(
                f"Credential JSON must contain non-empty string field(s): {field_list}"
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

    Calendar credentials are DB-managed, so redaction is pattern-based rather
    than sourced from process env vars.
    """
    redacted = message
    # key=value style pairs
    redacted = re.sub(
        r"(?i)\b(client_secret|refresh_token|access_token|token)\s*=\s*([^\s,;]+)",
        r"\1=[REDACTED]",
        redacted,
    )
    # JSON/Python dict style quoted values
    redacted = re.sub(
        r"""(?i)(['"]?(?:client_secret|refresh_token|access_token|token)['"]?\s*:\s*)(['"]).*?\2""",
        r'\1"[REDACTED]"',
        redacted,
    )
    # key: value style pairs
    redacted = re.sub(
        r"(?i)\b(client_secret|refresh_token|access_token|token)\s*:\s*([^\s,;]+)",
        r"\1: [REDACTED]",
        redacted,
    )
    return redacted


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
        # Projection cache: avoids repeated table-presence checks per cycle.
        self._projection_tables_available_cache: bool | None = None

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
        return []

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
            request_id: str | None = None,
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
            normalized_request_id = module._normalize_request_id(request_id)
            action_payload = {
                "title": title,
                "start_at": start_at,
                "end_at": end_at,
                "all_day": all_day,
                "timezone": timezone,
                "description": description,
                "location": location,
                "attendees": attendees or [],
                "recurrence_rule": recurrence_rule,
                "status": status,
                "visibility": visibility,
                "notes": notes,
                "color_id": color_id,
                "calendar_id": resolved_calendar_id,
                "conflict_policy": resolved_conflict_policy,
            }
            idempotency_key, replay = await module._prepare_workspace_mutation(
                action_type="workspace_user_create",
                request_id=normalized_request_id,
                action_payload=action_payload,
            )
            if replay is not None:
                return replay
            source_id = await module._resolve_action_source_id(
                source_kind=SOURCE_KIND_PROVIDER,
                lane="user",
                calendar_id=resolved_calendar_id,
            )

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
                error_payload = _build_structured_error(
                    exc,
                    provider=provider.name,
                    calendar_id=resolved_calendar_id,
                )
                await module._finalize_workspace_mutation(
                    idempotency_key=idempotency_key,
                    action_type="workspace_user_create",
                    request_id=normalized_request_id,
                    action_status=MUTATION_STATUS_FAILED,
                    action_payload=action_payload,
                    action_result=error_payload,
                    source_id=source_id,
                    origin_ref=None,
                    error=error_payload.get("error"),
                )
                return error_payload
            if conflict_result["status"] == "conflict":
                conflict_response = {
                    "status": "conflict",
                    "policy": resolved_conflict_policy,
                    "provider": provider.name,
                    "calendar_id": resolved_calendar_id,
                    "conflicts": conflict_result["conflicts"],
                    "suggested_slots": conflict_result["suggested_slots"],
                }
                await module._finalize_workspace_mutation(
                    idempotency_key=idempotency_key,
                    action_type="workspace_user_create",
                    request_id=normalized_request_id,
                    action_status=MUTATION_STATUS_NOOP,
                    action_payload=action_payload,
                    action_result=conflict_response,
                    source_id=source_id,
                    origin_ref=None,
                )
                return conflict_response

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
                    await module._finalize_workspace_mutation(
                        idempotency_key=idempotency_key,
                        action_type="workspace_user_create",
                        request_id=normalized_request_id,
                        action_status=MUTATION_STATUS_PENDING,
                        action_payload=action_payload,
                        action_result=approval_result,
                        source_id=source_id,
                        origin_ref=None,
                    )
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
                error_payload = _build_structured_error(
                    exc,
                    provider=provider.name,
                    calendar_id=resolved_calendar_id,
                )
                await module._finalize_workspace_mutation(
                    idempotency_key=idempotency_key,
                    action_type="workspace_user_create",
                    request_id=normalized_request_id,
                    action_status=MUTATION_STATUS_FAILED,
                    action_payload=action_payload,
                    action_result=error_payload,
                    source_id=source_id,
                    origin_ref=None,
                    error=error_payload.get("error"),
                )
                return error_payload
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
            result["projection_freshness"] = await module._refresh_user_projection(
                resolved_calendar_id
            )
            await module._finalize_workspace_mutation(
                idempotency_key=idempotency_key,
                action_type="workspace_user_create",
                request_id=normalized_request_id,
                action_status=MUTATION_STATUS_APPLIED,
                action_payload=action_payload,
                action_result=result,
                source_id=source_id,
                origin_ref=event.event_id,
            )
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
            request_id: str | None = None,
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
            normalized_request_id = module._normalize_request_id(request_id)
            action_payload = {
                "event_id": normalized_event_id,
                "title": title,
                "start_at": start_at,
                "end_at": end_at,
                "timezone": timezone,
                "description": description,
                "location": location,
                "attendees": attendees,
                "recurrence_rule": recurrence_rule,
                "recurrence_scope": recurrence_scope,
                "color_id": color_id,
                "calendar_id": resolved_calendar_id,
                "conflict_policy": resolved_conflict_policy,
            }
            idempotency_key, replay = await module._prepare_workspace_mutation(
                action_type="workspace_user_update",
                request_id=normalized_request_id,
                action_payload=action_payload,
            )
            if replay is not None:
                return replay
            source_id = await module._resolve_action_source_id(
                source_kind=SOURCE_KIND_PROVIDER,
                lane="user",
                calendar_id=resolved_calendar_id,
            )
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
                error_payload = _build_structured_error(
                    exc,
                    provider=provider.name,
                    calendar_id=resolved_calendar_id,
                )
                await module._finalize_workspace_mutation(
                    idempotency_key=idempotency_key,
                    action_type="workspace_user_update",
                    request_id=normalized_request_id,
                    action_status=MUTATION_STATUS_FAILED,
                    action_payload=action_payload,
                    action_result=error_payload,
                    source_id=source_id,
                    origin_ref=normalized_event_id,
                    error=error_payload.get("error"),
                )
                return error_payload
            if existing_event is None:
                not_found_result = {
                    "status": "not_found",
                    "provider": provider.name,
                    "calendar_id": resolved_calendar_id,
                    "event_id": normalized_event_id,
                }
                await module._finalize_workspace_mutation(
                    idempotency_key=idempotency_key,
                    action_type="workspace_user_update",
                    request_id=normalized_request_id,
                    action_status=MUTATION_STATUS_NOOP,
                    action_payload=action_payload,
                    action_result=not_found_result,
                    source_id=source_id,
                    origin_ref=normalized_event_id,
                )
                return not_found_result

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
                    error_payload = _build_structured_error(
                        exc,
                        provider=provider.name,
                        calendar_id=resolved_calendar_id,
                    )
                    await module._finalize_workspace_mutation(
                        idempotency_key=idempotency_key,
                        action_type="workspace_user_update",
                        request_id=normalized_request_id,
                        action_status=MUTATION_STATUS_FAILED,
                        action_payload=action_payload,
                        action_result=error_payload,
                        source_id=source_id,
                        origin_ref=normalized_event_id,
                        error=error_payload.get("error"),
                    )
                    return error_payload
                if conflict_result["status"] == "conflict":
                    conflict_response = {
                        "status": "conflict",
                        "policy": resolved_conflict_policy,
                        "provider": provider.name,
                        "calendar_id": resolved_calendar_id,
                        "conflicts": conflict_result["conflicts"],
                        "suggested_slots": conflict_result["suggested_slots"],
                    }
                    await module._finalize_workspace_mutation(
                        idempotency_key=idempotency_key,
                        action_type="workspace_user_update",
                        request_id=normalized_request_id,
                        action_status=MUTATION_STATUS_NOOP,
                        action_payload=action_payload,
                        action_result=conflict_response,
                        source_id=source_id,
                        origin_ref=normalized_event_id,
                    )
                    return conflict_response

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
                        await module._finalize_workspace_mutation(
                            idempotency_key=idempotency_key,
                            action_type="workspace_user_update",
                            request_id=normalized_request_id,
                            action_status=MUTATION_STATUS_PENDING,
                            action_payload=action_payload,
                            action_result=approval_result,
                            source_id=source_id,
                            origin_ref=normalized_event_id,
                        )
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
                error_payload = _build_structured_error(
                    exc,
                    provider=provider.name,
                    calendar_id=resolved_calendar_id,
                )
                await module._finalize_workspace_mutation(
                    idempotency_key=idempotency_key,
                    action_type="workspace_user_update",
                    request_id=normalized_request_id,
                    action_status=MUTATION_STATUS_FAILED,
                    action_payload=action_payload,
                    action_result=error_payload,
                    source_id=source_id,
                    origin_ref=normalized_event_id,
                    error=error_payload.get("error"),
                )
                return error_payload
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
            result["projection_freshness"] = await module._refresh_user_projection(
                resolved_calendar_id
            )
            await module._finalize_workspace_mutation(
                idempotency_key=idempotency_key,
                action_type="workspace_user_update",
                request_id=normalized_request_id,
                action_status=MUTATION_STATUS_APPLIED,
                action_payload=action_payload,
                action_result=result,
                source_id=source_id,
                origin_ref=normalized_event_id,
            )
            return result

        @mcp.tool()
        async def calendar_delete_event(
            event_id: str,
            calendar_id: str | None = None,
            recurrence_scope: Literal["series"] = "series",
            send_updates: str | None = None,
            request_id: str | None = None,
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
            normalized_request_id = module._normalize_request_id(request_id)
            action_payload = {
                "event_id": normalized_event_id,
                "calendar_id": resolved_calendar_id,
                "recurrence_scope": recurrence_scope,
                "send_updates": send_updates,
            }
            idempotency_key, replay = await module._prepare_workspace_mutation(
                action_type="workspace_user_delete",
                request_id=normalized_request_id,
                action_payload=action_payload,
            )
            if replay is not None:
                return replay
            source_id = await module._resolve_action_source_id(
                source_kind=SOURCE_KIND_PROVIDER,
                lane="user",
                calendar_id=resolved_calendar_id,
            )

            # Fetch the event first to confirm existence and capture metadata.
            # 404 from the provider is treated as success (idempotent delete).
            try:
                existing_event = await provider.get_event(
                    calendar_id=resolved_calendar_id,
                    event_id=normalized_event_id,
                )
            except CalendarAuthError as exc:
                await module._finalize_workspace_mutation(
                    idempotency_key=idempotency_key,
                    action_type="workspace_user_delete",
                    request_id=normalized_request_id,
                    action_status=MUTATION_STATUS_FAILED,
                    action_payload=action_payload,
                    action_result={
                        "status": "error",
                        "error_type": type(exc).__name__,
                        "provider": provider.name,
                        "calendar_id": resolved_calendar_id,
                    },
                    source_id=source_id,
                    origin_ref=normalized_event_id,
                    error=str(exc),
                )
                raise
            if existing_event is None:
                result = {
                    "status": "not_found",
                    "provider": provider.name,
                    "calendar_id": resolved_calendar_id,
                    "event_id": normalized_event_id,
                }
                result["projection_freshness"] = await module._refresh_user_projection(
                    resolved_calendar_id
                )
                await module._finalize_workspace_mutation(
                    idempotency_key=idempotency_key,
                    action_type="workspace_user_delete",
                    request_id=normalized_request_id,
                    action_status=MUTATION_STATUS_NOOP,
                    action_payload=action_payload,
                    action_result=result,
                    source_id=source_id,
                    origin_ref=normalized_event_id,
                )
                return result

            try:
                await provider.delete_event(
                    calendar_id=resolved_calendar_id,
                    event_id=normalized_event_id,
                    send_updates=send_updates,
                )
            except CalendarAuthError as exc:
                await module._finalize_workspace_mutation(
                    idempotency_key=idempotency_key,
                    action_type="workspace_user_delete",
                    request_id=normalized_request_id,
                    action_status=MUTATION_STATUS_FAILED,
                    action_payload=action_payload,
                    action_result={
                        "status": "error",
                        "error_type": type(exc).__name__,
                        "provider": provider.name,
                        "calendar_id": resolved_calendar_id,
                    },
                    source_id=source_id,
                    origin_ref=normalized_event_id,
                    error=str(exc),
                )
                raise
            result = {
                "status": "deleted",
                "provider": provider.name,
                "calendar_id": resolved_calendar_id,
                "event_id": normalized_event_id,
            }
            result["projection_freshness"] = await module._refresh_user_projection(
                resolved_calendar_id
            )
            await module._finalize_workspace_mutation(
                idempotency_key=idempotency_key,
                action_type="workspace_user_delete",
                request_id=normalized_request_id,
                action_status=MUTATION_STATUS_APPLIED,
                action_payload=action_payload,
                action_result=result,
                source_id=source_id,
                origin_ref=normalized_event_id,
            )
            return result

        @mcp.tool()
        async def calendar_create_butler_event(
            butler_name: str,
            title: str,
            start_at: datetime,
            end_at: datetime | None = None,
            timezone: str | None = None,
            recurrence_rule: str | None = None,
            cron: str | None = None,
            until_at: datetime | None = None,
            action: str = "Run butler event",
            action_args: dict[str, Any] | None = None,
            source_hint: str | None = None,
            request_id: str | None = None,
            _approval_bypass: bool = False,
        ) -> dict[str, Any]:
            """Create a butler-view workspace event as schedule or reminder."""
            normalized_butler = butler_name.strip()
            if not normalized_butler:
                raise ValueError("butler_name must be a non-empty string")
            if normalized_butler != module._butler_name:
                raise ValueError(
                    f"butler_name '{normalized_butler}' does not match current butler "
                    f"'{module._butler_name}'"
                )
            if start_at.tzinfo is None:
                raise ValueError("start_at must be timezone-aware")
            normalized_title = title.strip()
            if not normalized_title:
                raise ValueError("title must be a non-empty string")
            effective_timezone = (timezone or module._require_config().timezone).strip()
            _ensure_valid_timezone(effective_timezone)
            effective_end = end_at or (start_at + timedelta(minutes=15))
            if effective_end.tzinfo is None:
                raise ValueError("end_at must be timezone-aware when provided")
            if effective_end <= start_at:
                raise ValueError("end_at must be after start_at")
            normalized_rule = _normalize_recurrence_rule(recurrence_rule)
            if until_at is None and normalized_rule is not None:
                until_at = module._rrule_until(normalized_rule)

            normalized_source_hint = module._normalize_butler_event_source_hint(source_hint)
            reminders_available = await module._table_exists("reminders")
            source_kind = normalized_source_hint
            if source_kind is None:
                if normalized_rule is None and cron is None and reminders_available:
                    source_kind = BUTLER_EVENT_SOURCE_REMINDER
                else:
                    source_kind = BUTLER_EVENT_SOURCE_SCHEDULED
            if source_kind == BUTLER_EVENT_SOURCE_REMINDER and not reminders_available:
                raise ValueError("Reminder source is not available on this butler")

            normalized_request_id = module._normalize_request_id(request_id)
            action_payload = {
                "butler_name": normalized_butler,
                "title": normalized_title,
                "start_at": start_at,
                "end_at": effective_end,
                "timezone": effective_timezone,
                "recurrence_rule": normalized_rule,
                "cron": cron,
                "until_at": until_at,
                "action": action,
                "action_args": action_args,
                "source_hint": source_kind,
            }
            idempotency_key, replay = await module._prepare_workspace_mutation(
                action_type="workspace_butler_create",
                request_id=normalized_request_id,
                action_payload=action_payload,
                allow_pending_replay=_approval_bypass,
            )
            if replay is not None:
                return replay

            pool = getattr(module._db, "pool", None) if module._db is not None else None
            if pool is None:
                raise RuntimeError("Database pool is not available")

            projection_source_kind = (
                SOURCE_KIND_INTERNAL_REMINDERS
                if source_kind == BUTLER_EVENT_SOURCE_REMINDER
                else SOURCE_KIND_INTERNAL_SCHEDULER
            )
            source_id = await module._resolve_action_source_id(
                source_kind=projection_source_kind,
                lane="butler",
            )

            try:
                event_link_id = uuid.uuid4()
                if source_kind == BUTLER_EVENT_SOURCE_REMINDER:
                    reminder = await module._create_reminder_event(
                        title=normalized_title,
                        start_at=start_at,
                        timezone=effective_timezone,
                        until_at=until_at,
                        recurrence_rule=normalized_rule,
                        cron=cron,
                        action=action,
                        action_args=action_args,
                        calendar_event_id=event_link_id,
                    )
                    origin_ref = str(reminder["id"])
                    result: dict[str, Any] = {
                        "status": "created",
                        "source_type": BUTLER_EVENT_SOURCE_REMINDER,
                        "butler_name": module._butler_name,
                        "event_id": str(reminder.get("calendar_event_id") or reminder["id"]),
                        "reminder_id": str(reminder["id"]),
                        "reminder": reminder,
                    }
                else:
                    effective_cron = cron
                    if effective_cron is None:
                        if normalized_rule is None:
                            raise ValueError(
                                "cron or recurrence_rule is required for scheduled_task events"
                            )
                        effective_cron = module._rrule_to_cron(start_at, normalized_rule)
                    args = action_args or {}
                    dispatch_mode = str(args.get("dispatch_mode") or "prompt").strip().lower()
                    if dispatch_mode not in {"prompt", "job"}:
                        raise ValueError("dispatch_mode must be 'prompt' or 'job'")
                    schedule_name = str(
                        args.get("name") or f"calendar-event-{uuid.uuid4().hex[:8]}"
                    )
                    if dispatch_mode == "job":
                        job_name = str(args.get("job_name") or action).strip()
                        if not job_name:
                            raise ValueError("job_name must be non-empty for job dispatch mode")
                        job_args = args.get("job_args")
                        if job_args is None:
                            job_args = {}
                        if not isinstance(job_args, dict):
                            raise ValueError("job_args must be an object when provided")
                        task_id = await _schedule_create(
                            pool,
                            schedule_name,
                            effective_cron,
                            None,
                            dispatch_mode="job",
                            job_name=job_name,
                            job_args=job_args,
                            timezone=effective_timezone,
                            start_at=start_at,
                            end_at=effective_end,
                            until_at=until_at,
                            display_title=normalized_title,
                            calendar_event_id=str(event_link_id),
                            stagger_key=module._butler_name,
                        )
                    else:
                        task_id = await _schedule_create(
                            pool,
                            schedule_name,
                            effective_cron,
                            action,
                            dispatch_mode="prompt",
                            timezone=effective_timezone,
                            start_at=start_at,
                            end_at=effective_end,
                            until_at=until_at,
                            display_title=normalized_title,
                            calendar_event_id=str(event_link_id),
                            stagger_key=module._butler_name,
                        )
                    origin_ref = str(task_id)
                    result = {
                        "status": "created",
                        "source_type": BUTLER_EVENT_SOURCE_SCHEDULED,
                        "butler_name": module._butler_name,
                        "event_id": str(event_link_id),
                        "schedule_id": str(task_id),
                        "cron": effective_cron,
                    }

                result["projection_freshness"] = await module._refresh_butler_projection()
                await module._finalize_workspace_mutation(
                    idempotency_key=idempotency_key,
                    action_type="workspace_butler_create",
                    request_id=normalized_request_id,
                    action_status=MUTATION_STATUS_APPLIED,
                    action_payload=action_payload,
                    action_result=result,
                    source_id=source_id,
                    origin_ref=origin_ref,
                )
                return result
            except Exception as exc:
                error_payload = {"status": "error", "error": str(exc)}
                await module._finalize_workspace_mutation(
                    idempotency_key=idempotency_key,
                    action_type="workspace_butler_create",
                    request_id=normalized_request_id,
                    action_status=MUTATION_STATUS_FAILED,
                    action_payload=action_payload,
                    action_result=error_payload,
                    source_id=source_id,
                    origin_ref=None,
                    error=str(exc),
                )
                return error_payload

        @mcp.tool()
        async def calendar_update_butler_event(
            event_id: str,
            title: str | None = None,
            start_at: datetime | None = None,
            end_at: datetime | None = None,
            timezone: str | None = None,
            recurrence_rule: str | None = None,
            cron: str | None = None,
            until_at: datetime | None = None,
            enabled: bool | None = None,
            source_hint: str | None = None,
            request_id: str | None = None,
            _approval_bypass: bool = False,
        ) -> dict[str, Any]:
            """Update a butler schedule/reminder event."""
            normalized_source_hint = module._normalize_butler_event_source_hint(source_hint)
            normalized_request_id = module._normalize_request_id(request_id)
            action_payload = {
                "event_id": event_id,
                "title": title,
                "start_at": start_at,
                "end_at": end_at,
                "timezone": timezone,
                "recurrence_rule": recurrence_rule,
                "cron": cron,
                "until_at": until_at,
                "enabled": enabled,
                "source_hint": normalized_source_hint,
            }
            idempotency_key, replay = await module._prepare_workspace_mutation(
                action_type="workspace_butler_update",
                request_id=normalized_request_id,
                action_payload=action_payload,
                allow_pending_replay=_approval_bypass,
            )
            if replay is not None:
                return replay

            try:
                source_type, target_id = await module._resolve_butler_event_target(
                    event_id=event_id,
                    source_hint=normalized_source_hint,
                )
                source_kind = (
                    SOURCE_KIND_INTERNAL_REMINDERS
                    if source_type == BUTLER_EVENT_SOURCE_REMINDER
                    else SOURCE_KIND_INTERNAL_SCHEDULER
                )
                source_id = await module._resolve_action_source_id(
                    source_kind=source_kind,
                    lane="butler",
                )
                pool = getattr(module._db, "pool", None) if module._db is not None else None
                if pool is None:
                    raise RuntimeError("Database pool is not available")

                if source_type == BUTLER_EVENT_SOURCE_REMINDER:
                    reminder = await module._update_reminder_event(
                        reminder_id=target_id,
                        title=title,
                        start_at=start_at,
                        timezone=timezone,
                        until_at=until_at,
                        recurrence_rule=recurrence_rule,
                        cron=cron,
                        enabled=enabled,
                    )
                    origin_ref = str(target_id)
                    result: dict[str, Any] = {
                        "status": "updated",
                        "source_type": BUTLER_EVENT_SOURCE_REMINDER,
                        "butler_name": module._butler_name,
                        "event_id": str(reminder.get("calendar_event_id") or reminder["id"]),
                        "reminder_id": str(target_id),
                        "reminder": reminder,
                    }
                else:
                    update_fields: dict[str, Any] = {}
                    if title is not None:
                        update_fields["display_title"] = title
                    if start_at is not None:
                        update_fields["start_at"] = start_at
                    if end_at is not None:
                        update_fields["end_at"] = end_at
                    if timezone is not None:
                        _ensure_valid_timezone(timezone)
                        update_fields["timezone"] = timezone
                    if until_at is not None:
                        update_fields["until_at"] = until_at
                    effective_cron = cron
                    normalized_rule = _normalize_recurrence_rule(recurrence_rule)
                    if effective_cron is None and normalized_rule is not None:
                        if start_at is None:
                            raise ValueError(
                                "start_at is required when recurrence_rule is provided without cron"
                            )
                        effective_cron = module._rrule_to_cron(start_at, normalized_rule)
                    if effective_cron is not None:
                        update_fields["cron"] = effective_cron
                    if enabled is not None:
                        update_fields["enabled"] = enabled
                    await _schedule_update(
                        pool,
                        target_id,
                        stagger_key=module._butler_name,
                        **update_fields,
                    )
                    schedule_row = await pool.fetchrow(
                        "SELECT calendar_event_id FROM scheduled_tasks WHERE id = $1",
                        target_id,
                    )
                    event_link = (
                        str(schedule_row["calendar_event_id"])
                        if schedule_row is not None
                        and schedule_row["calendar_event_id"] is not None
                        else str(target_id)
                    )
                    origin_ref = str(target_id)
                    result = {
                        "status": "updated",
                        "source_type": BUTLER_EVENT_SOURCE_SCHEDULED,
                        "butler_name": module._butler_name,
                        "event_id": event_link,
                        "schedule_id": str(target_id),
                    }

                result["projection_freshness"] = await module._refresh_butler_projection()
                await module._finalize_workspace_mutation(
                    idempotency_key=idempotency_key,
                    action_type="workspace_butler_update",
                    request_id=normalized_request_id,
                    action_status=MUTATION_STATUS_APPLIED,
                    action_payload=action_payload,
                    action_result=result,
                    source_id=source_id,
                    origin_ref=origin_ref,
                )
                return result
            except Exception as exc:
                error_payload = {"status": "error", "error": str(exc)}
                await module._finalize_workspace_mutation(
                    idempotency_key=idempotency_key,
                    action_type="workspace_butler_update",
                    request_id=normalized_request_id,
                    action_status=MUTATION_STATUS_FAILED,
                    action_payload=action_payload,
                    action_result=error_payload,
                    source_id=None,
                    origin_ref=None,
                    error=str(exc),
                )
                return error_payload

        @mcp.tool()
        async def calendar_delete_butler_event(
            event_id: str,
            scope: Literal["series"] = "series",
            source_hint: str | None = None,
            request_id: str | None = None,
            _approval_bypass: bool = False,
        ) -> dict[str, Any]:
            """Delete a butler schedule/reminder event."""
            if scope != "series":
                raise ValueError("Only scope='series' is supported in v1")
            normalized_source_hint = module._normalize_butler_event_source_hint(source_hint)
            normalized_request_id = module._normalize_request_id(request_id)
            action_payload = {
                "event_id": event_id,
                "scope": scope,
                "source_hint": normalized_source_hint,
            }
            idempotency_key, replay = await module._prepare_workspace_mutation(
                action_type="workspace_butler_delete",
                request_id=normalized_request_id,
                action_payload=action_payload,
                allow_pending_replay=_approval_bypass,
            )
            if replay is not None:
                return replay

            tentative_source_kind = (
                SOURCE_KIND_INTERNAL_REMINDERS
                if normalized_source_hint == BUTLER_EVENT_SOURCE_REMINDER
                else SOURCE_KIND_INTERNAL_SCHEDULER
            )
            if not _approval_bypass:
                approval_result = await module._gate_high_impact_mutation(
                    action_type="workspace_butler_delete",
                    tool_name="calendar_delete_butler_event",
                    tool_args={
                        "event_id": event_id,
                        "scope": scope,
                        "source_hint": source_hint,
                        "request_id": normalized_request_id,
                    },
                    request_id=normalized_request_id,
                    idempotency_key=idempotency_key,
                    action_payload=action_payload,
                    source_kind=tentative_source_kind,
                )
                if approval_result is not None:
                    return approval_result

            try:
                source_type, target_id = await module._resolve_butler_event_target(
                    event_id=event_id,
                    source_hint=normalized_source_hint,
                )
                source_kind = (
                    SOURCE_KIND_INTERNAL_REMINDERS
                    if source_type == BUTLER_EVENT_SOURCE_REMINDER
                    else SOURCE_KIND_INTERNAL_SCHEDULER
                )
                source_id = await module._resolve_action_source_id(
                    source_kind=source_kind,
                    lane="butler",
                )
                if source_type == BUTLER_EVENT_SOURCE_REMINDER:
                    deleted = await module._delete_reminder_event(target_id)
                else:
                    pool = getattr(module._db, "pool", None) if module._db is not None else None
                    if pool is None:
                        raise RuntimeError("Database pool is not available")
                    await _schedule_delete(pool, target_id)
                    deleted = True

                if deleted:
                    result: dict[str, Any] = {
                        "status": "deleted",
                        "source_type": source_type,
                        "butler_name": module._butler_name,
                        "event_id": event_id,
                    }
                    mutation_status = MUTATION_STATUS_APPLIED
                else:
                    result = {
                        "status": "not_found",
                        "source_type": source_type,
                        "butler_name": module._butler_name,
                        "event_id": event_id,
                    }
                    mutation_status = MUTATION_STATUS_NOOP
                result["projection_freshness"] = await module._refresh_butler_projection()
                await module._finalize_workspace_mutation(
                    idempotency_key=idempotency_key,
                    action_type="workspace_butler_delete",
                    request_id=normalized_request_id,
                    action_status=mutation_status,
                    action_payload=action_payload,
                    action_result=result,
                    source_id=source_id,
                    origin_ref=str(target_id),
                )
                return result
            except Exception as exc:
                error_payload = {"status": "error", "error": str(exc)}
                await module._finalize_workspace_mutation(
                    idempotency_key=idempotency_key,
                    action_type="workspace_butler_delete",
                    request_id=normalized_request_id,
                    action_status=MUTATION_STATUS_FAILED,
                    action_payload=action_payload,
                    action_result=error_payload,
                    source_id=None,
                    origin_ref=None,
                    error=str(exc),
                )
                return error_payload

        @mcp.tool()
        async def calendar_toggle_butler_event(
            event_id: str,
            enabled: bool,
            source_hint: str | None = None,
            request_id: str | None = None,
            _approval_bypass: bool = False,
        ) -> dict[str, Any]:
            """Pause/resume a butler schedule/reminder event."""
            normalized_source_hint = module._normalize_butler_event_source_hint(source_hint)
            normalized_request_id = module._normalize_request_id(request_id)
            action_payload = {
                "event_id": event_id,
                "enabled": enabled,
                "source_hint": normalized_source_hint,
            }
            idempotency_key, replay = await module._prepare_workspace_mutation(
                action_type="workspace_butler_toggle",
                request_id=normalized_request_id,
                action_payload=action_payload,
                allow_pending_replay=_approval_bypass,
            )
            if replay is not None:
                return replay

            tentative_source_kind = (
                SOURCE_KIND_INTERNAL_REMINDERS
                if normalized_source_hint == BUTLER_EVENT_SOURCE_REMINDER
                else SOURCE_KIND_INTERNAL_SCHEDULER
            )
            if not _approval_bypass:
                approval_result = await module._gate_high_impact_mutation(
                    action_type="workspace_butler_toggle",
                    tool_name="calendar_toggle_butler_event",
                    tool_args={
                        "event_id": event_id,
                        "enabled": enabled,
                        "source_hint": source_hint,
                        "request_id": normalized_request_id,
                    },
                    request_id=normalized_request_id,
                    idempotency_key=idempotency_key,
                    action_payload=action_payload,
                    source_kind=tentative_source_kind,
                )
                if approval_result is not None:
                    return approval_result

            try:
                source_type, target_id = await module._resolve_butler_event_target(
                    event_id=event_id,
                    source_hint=normalized_source_hint,
                )
                source_kind = (
                    SOURCE_KIND_INTERNAL_REMINDERS
                    if source_type == BUTLER_EVENT_SOURCE_REMINDER
                    else SOURCE_KIND_INTERNAL_SCHEDULER
                )
                source_id = await module._resolve_action_source_id(
                    source_kind=source_kind,
                    lane="butler",
                )
                if source_type == BUTLER_EVENT_SOURCE_REMINDER:
                    reminder = await module._toggle_reminder_event(target_id, enabled)
                    event_link = str(reminder.get("calendar_event_id") or reminder["id"])
                    result: dict[str, Any] = {
                        "status": "updated",
                        "source_type": source_type,
                        "butler_name": module._butler_name,
                        "event_id": event_link,
                        "reminder_id": str(target_id),
                        "enabled": enabled,
                        "reminder": reminder,
                    }
                else:
                    pool = getattr(module._db, "pool", None) if module._db is not None else None
                    if pool is None:
                        raise RuntimeError("Database pool is not available")
                    await _schedule_update(
                        pool,
                        target_id,
                        stagger_key=module._butler_name,
                        enabled=enabled,
                    )
                    row = await pool.fetchrow(
                        "SELECT calendar_event_id FROM scheduled_tasks WHERE id = $1",
                        target_id,
                    )
                    event_link = (
                        str(row["calendar_event_id"])
                        if row is not None and row["calendar_event_id"] is not None
                        else str(target_id)
                    )
                    result = {
                        "status": "updated",
                        "source_type": source_type,
                        "butler_name": module._butler_name,
                        "event_id": event_link,
                        "schedule_id": str(target_id),
                        "enabled": enabled,
                    }

                result["projection_freshness"] = await module._refresh_butler_projection()
                await module._finalize_workspace_mutation(
                    idempotency_key=idempotency_key,
                    action_type="workspace_butler_toggle",
                    request_id=normalized_request_id,
                    action_status=MUTATION_STATUS_APPLIED,
                    action_payload=action_payload,
                    action_result=result,
                    source_id=source_id,
                    origin_ref=str(target_id),
                )
                return result
            except Exception as exc:
                error_payload = {"status": "error", "error": str(exc)}
                await module._finalize_workspace_mutation(
                    idempotency_key=idempotency_key,
                    action_type="workspace_butler_toggle",
                    request_id=normalized_request_id,
                    action_status=MUTATION_STATUS_FAILED,
                    action_payload=action_payload,
                    action_result=error_payload,
                    source_id=None,
                    origin_ref=None,
                    error=str(exc),
                )
                return error_payload

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
            projection_freshness = await module._projection_freshness_metadata()

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
                    "projection_freshness": projection_freshness,
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
                "projection_freshness": projection_freshness,
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
                    "projection_freshness": await module._projection_freshness_metadata(),
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
                "projection_freshness": await module._projection_freshness_metadata(),
            }

    async def _resolve_credentials(
        self,
        *,
        db: Any,
        credential_store: Any,
    ) -> _GoogleOAuthCredentials:
        """Resolve Google OAuth credentials using canonical lookup sources.

        Resolution:
        1. CredentialStore (``butler_secrets`` table) for individual keys.

        Parameters
        ----------
        db:
            Butler database instance (used to extract ``db.pool``).
        credential_store:
            Optional CredentialStore for step 1.  When ``None``, step 1 is
            skipped.

        Returns
        -------
        _GoogleOAuthCredentials
            Resolved credentials.

        Raises
        ------
        RuntimeError
            If credentials cannot be resolved from DB-backed credential storage.
        """
        # Step 1: Try CredentialStore (butler_secrets) for individual keys.
        if credential_store is not None:
            client_id = await credential_store.resolve("GOOGLE_OAUTH_CLIENT_ID", env_fallback=False)
            client_secret = await credential_store.resolve(
                "GOOGLE_OAUTH_CLIENT_SECRET", env_fallback=False
            )
            refresh_token = await credential_store.resolve(
                "GOOGLE_REFRESH_TOKEN", env_fallback=False
            )
            if client_id and client_secret and refresh_token:
                logger.debug("CalendarModule: resolved Google credentials from CredentialStore")
                return _GoogleOAuthCredentials(
                    client_id=client_id,
                    client_secret=client_secret,
                    refresh_token=refresh_token,
                )

        raise RuntimeError(
            "CalendarModule: Google OAuth credentials are not available in butler_secrets. "
            "Store them via the dashboard OAuth flow (shared credential store)."
        )

    async def on_startup(self, config: Any, db: Any, credential_store: Any = None) -> None:
        """Initialize the calendar provider with resolved Google OAuth credentials.

        Credentials are resolved from CredentialStore (``butler_secrets``) only.

        Parameters
        ----------
        config:
            Module configuration (``CalendarConfig`` or raw dict).
        db:
            Butler database instance.
        credential_store:
            Optional :class:`~butlers.credential_store.CredentialStore`.
            When provided, individual Google credential keys are resolved from
            ``butler_secrets``.
        """
        self._config = self._coerce_config(config)
        self._db = db

        provider_cls = self._PROVIDER_CLASSES.get(self._config.provider)
        if provider_cls is None:
            supported = ", ".join(sorted(self._PROVIDER_CLASSES))
            raise RuntimeError(
                f"Unsupported calendar provider '{self._config.provider}'. "
                f"Supported providers: {supported}"
            )

        credentials = await self._resolve_credentials(db=db, credential_store=credential_store)
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

    @staticmethod
    def _normalize_json_object(value: Any) -> dict[str, Any]:
        """Normalize a DB JSON/JSONB value into a dict for deterministic access."""
        if isinstance(value, Mapping):
            return dict(value)
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return {}
            if isinstance(parsed, Mapping):
                return dict(parsed)
        return {}

    @classmethod
    def _jsonify_for_storage(cls, value: Any) -> Any:
        """Convert nested values into JSON-serializable primitives."""
        if isinstance(value, Mapping):
            return {str(key): cls._jsonify_for_storage(item) for key, item in value.items()}
        if isinstance(value, list | tuple | set):
            return [cls._jsonify_for_storage(item) for item in value]
        if isinstance(value, datetime | date):
            return value.isoformat()
        if isinstance(value, StrEnum):
            return value.value
        if isinstance(value, uuid.UUID):
            return str(value)
        return value

    @classmethod
    def _encode_jsonb(cls, value: Any) -> str:
        normalized = cls._jsonify_for_storage(value)
        return json.dumps(normalized, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _coerce_datetime(value: Any) -> datetime | None:
        if isinstance(value, datetime):
            return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        if isinstance(value, str):
            normalized = value.strip()
            if not normalized:
                return None
            if normalized.endswith("Z"):
                normalized = f"{normalized[:-1]}+00:00"
            try:
                parsed = datetime.fromisoformat(normalized)
            except ValueError:
                return None
            return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
        return None

    async def _projection_tables_available(self) -> bool:
        """Return whether unified calendar projection tables are present."""
        if self._projection_tables_available_cache is not None:
            return self._projection_tables_available_cache

        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            self._projection_tables_available_cache = False
            return False

        try:
            row = await pool.fetchrow(
                """
                SELECT
                    to_regclass('calendar_sources') IS NOT NULL AS has_sources,
                    to_regclass('calendar_events') IS NOT NULL AS has_events,
                    to_regclass('calendar_event_instances') IS NOT NULL AS has_instances,
                    to_regclass('calendar_sync_cursors') IS NOT NULL AS has_cursors,
                    to_regclass('calendar_action_log') IS NOT NULL AS has_action_log
                """
            )
        except Exception as exc:
            logger.debug("Projection table availability check failed: %s", exc, exc_info=True)
            self._projection_tables_available_cache = False
            return False

        if row is None:
            self._projection_tables_available_cache = False
            return False

        try:
            flags = [
                row["has_sources"],
                row["has_events"],
                row["has_instances"],
                row["has_cursors"],
                row["has_action_log"],
            ]
        except KeyError:
            self._projection_tables_available_cache = False
            return False

        # Use strict True checks to avoid treating mock placeholder objects as table existence.
        self._projection_tables_available_cache = all(flag is True for flag in flags)
        return self._projection_tables_available_cache

    async def _table_exists(self, table_name: str) -> bool:
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            return False
        exists = await pool.fetchval("SELECT to_regclass($1) IS NOT NULL", table_name)
        return bool(exists)

    async def _ensure_calendar_source(
        self,
        *,
        source_key: str,
        source_kind: str,
        lane: Literal["user", "butler"],
        provider: str | None = None,
        calendar_id: str | None = None,
        butler_name: str | None = None,
        display_name: str | None = None,
        writable: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> uuid.UUID | None:
        if not await self._projection_tables_available():
            return None
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            return None

        metadata_json = self._encode_jsonb(metadata or {})
        row = await pool.fetchrow(
            """
            INSERT INTO calendar_sources (
                source_key, source_kind, lane, provider, calendar_id, butler_name,
                display_name, writable, metadata
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb)
            ON CONFLICT (source_key) DO UPDATE SET
                source_kind = EXCLUDED.source_kind,
                lane = EXCLUDED.lane,
                provider = EXCLUDED.provider,
                calendar_id = EXCLUDED.calendar_id,
                butler_name = EXCLUDED.butler_name,
                display_name = EXCLUDED.display_name,
                writable = EXCLUDED.writable,
                metadata = EXCLUDED.metadata,
                updated_at = now()
            RETURNING id
            """,
            source_key,
            source_kind,
            lane,
            provider,
            calendar_id,
            butler_name,
            display_name,
            writable,
            metadata_json,
        )
        return row["id"] if row is not None else None

    async def _load_projection_cursor(
        self,
        *,
        source_id: uuid.UUID,
        cursor_name: str,
    ) -> dict[str, Any] | None:
        if not await self._projection_tables_available():
            return None
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            return None

        row = await pool.fetchrow(
            """
            SELECT sync_token, checkpoint, full_sync_required,
                   last_synced_at, last_success_at, last_error_at, last_error
            FROM calendar_sync_cursors
            WHERE source_id = $1 AND cursor_name = $2
            """,
            source_id,
            cursor_name,
        )
        if row is None:
            return None

        return {
            "sync_token": row["sync_token"],
            "checkpoint": self._normalize_json_object(row["checkpoint"]),
            "full_sync_required": bool(row["full_sync_required"]),
            "last_synced_at": row["last_synced_at"],
            "last_success_at": row["last_success_at"],
            "last_error_at": row["last_error_at"],
            "last_error": row["last_error"],
        }

    async def _upsert_projection_cursor(
        self,
        *,
        source_id: uuid.UUID | None,
        cursor_name: str,
        sync_token: str | None,
        checkpoint: dict[str, Any],
        full_sync_required: bool,
        last_synced_at: datetime | None,
        last_success_at: datetime | None,
        last_error_at: datetime | None,
        last_error: str | None,
    ) -> None:
        if source_id is None or not await self._projection_tables_available():
            return
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            return

        checkpoint_json = self._encode_jsonb(checkpoint)
        await pool.execute(
            """
            INSERT INTO calendar_sync_cursors (
                source_id, cursor_name, sync_token, checkpoint, full_sync_required,
                last_synced_at, last_success_at, last_error_at, last_error
            )
            VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7, $8, $9)
            ON CONFLICT (source_id, cursor_name) DO UPDATE SET
                sync_token = EXCLUDED.sync_token,
                checkpoint = EXCLUDED.checkpoint,
                full_sync_required = EXCLUDED.full_sync_required,
                last_synced_at = EXCLUDED.last_synced_at,
                last_success_at = EXCLUDED.last_success_at,
                last_error_at = EXCLUDED.last_error_at,
                last_error = EXCLUDED.last_error,
                updated_at = now()
            """,
            source_id,
            cursor_name,
            sync_token,
            checkpoint_json,
            full_sync_required,
            last_synced_at,
            last_success_at,
            last_error_at,
            last_error,
        )

    async def _record_projection_action(
        self,
        *,
        idempotency_key: str,
        action_type: str,
        action_status: Literal["pending", "applied", "failed", "noop"],
        source_id: uuid.UUID | None,
        origin_ref: str | None,
        action_payload: dict[str, Any],
        action_result: dict[str, Any] | None = None,
        request_id: str | None = None,
        event_id: uuid.UUID | None = None,
        instance_id: uuid.UUID | None = None,
        error: str | None = None,
    ) -> None:
        if not await self._projection_tables_available():
            return
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            return

        payload_json = self._encode_jsonb(action_payload)
        result_json = None if action_result is None else self._encode_jsonb(action_result)
        await pool.execute(
            """
            INSERT INTO calendar_action_log (
                idempotency_key, request_id, action_type, action_status,
                source_id, event_id, instance_id, origin_ref,
                action_payload, action_result, error, applied_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10::jsonb, $11, $12)
            ON CONFLICT (idempotency_key) DO UPDATE SET
                action_status = EXCLUDED.action_status,
                source_id = EXCLUDED.source_id,
                event_id = EXCLUDED.event_id,
                instance_id = EXCLUDED.instance_id,
                origin_ref = EXCLUDED.origin_ref,
                action_payload = EXCLUDED.action_payload,
                action_result = EXCLUDED.action_result,
                error = EXCLUDED.error,
                applied_at = EXCLUDED.applied_at,
                updated_at = now()
            """,
            idempotency_key,
            request_id,
            action_type,
            action_status,
            source_id,
            event_id,
            instance_id,
            origin_ref,
            payload_json,
            result_json,
            error,
            datetime.now(UTC) if action_status in {"applied", "failed", "noop"} else None,
        )

    async def _upsert_projection_event(
        self,
        *,
        source_id: uuid.UUID,
        origin_ref: str,
        title: str,
        timezone: str,
        starts_at: datetime,
        ends_at: datetime,
        status: str,
        all_day: bool = False,
        visibility: str = "default",
        recurrence_rule: str | None = None,
        description: str | None = None,
        location: str | None = None,
        etag: str | None = None,
        origin_updated_at: datetime | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> uuid.UUID:
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            raise RuntimeError("Projection writes require a database pool")

        metadata_json = self._encode_jsonb(metadata or {})
        row = await pool.fetchrow(
            """
            INSERT INTO calendar_events (
                source_id, origin_ref, title, description, location, timezone,
                starts_at, ends_at, all_day, status, visibility, recurrence_rule,
                etag, origin_updated_at, metadata
            )
            VALUES (
                $1, $2, $3, $4, $5, $6,
                $7, $8, $9, $10, $11, $12,
                $13, $14, $15::jsonb
            )
            ON CONFLICT (source_id, origin_ref) DO UPDATE SET
                title = EXCLUDED.title,
                description = EXCLUDED.description,
                location = EXCLUDED.location,
                timezone = EXCLUDED.timezone,
                starts_at = EXCLUDED.starts_at,
                ends_at = EXCLUDED.ends_at,
                all_day = EXCLUDED.all_day,
                status = EXCLUDED.status,
                visibility = EXCLUDED.visibility,
                recurrence_rule = EXCLUDED.recurrence_rule,
                etag = EXCLUDED.etag,
                origin_updated_at = EXCLUDED.origin_updated_at,
                metadata = EXCLUDED.metadata,
                updated_at = now()
            RETURNING id
            """,
            source_id,
            origin_ref,
            title,
            description,
            location,
            timezone,
            starts_at,
            ends_at,
            all_day,
            status,
            visibility,
            recurrence_rule,
            etag,
            origin_updated_at,
            metadata_json,
        )
        if row is None:
            raise RuntimeError("Projection upsert did not return calendar_events.id")
        return row["id"]

    async def _upsert_projection_instance(
        self,
        *,
        event_id: uuid.UUID,
        source_id: uuid.UUID,
        origin_instance_ref: str,
        timezone: str,
        starts_at: datetime,
        ends_at: datetime,
        status: str,
        is_exception: bool = False,
        origin_updated_at: datetime | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> uuid.UUID:
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            raise RuntimeError("Projection writes require a database pool")

        metadata_json = self._encode_jsonb(metadata or {})
        row = await pool.fetchrow(
            """
            INSERT INTO calendar_event_instances (
                event_id, source_id, origin_instance_ref, timezone, starts_at, ends_at,
                status, is_exception, origin_updated_at, metadata
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb)
            ON CONFLICT (event_id, origin_instance_ref) DO UPDATE SET
                timezone = EXCLUDED.timezone,
                starts_at = EXCLUDED.starts_at,
                ends_at = EXCLUDED.ends_at,
                status = EXCLUDED.status,
                is_exception = EXCLUDED.is_exception,
                origin_updated_at = EXCLUDED.origin_updated_at,
                metadata = EXCLUDED.metadata,
                updated_at = now()
            RETURNING id
            """,
            event_id,
            source_id,
            origin_instance_ref,
            timezone,
            starts_at,
            ends_at,
            status,
            is_exception,
            origin_updated_at,
            metadata_json,
        )
        if row is None:
            raise RuntimeError("Projection upsert did not return calendar_event_instances.id")
        return row["id"]

    async def _mark_projection_event_cancelled(
        self,
        *,
        source_id: uuid.UUID,
        origin_ref: str,
        origin_updated_at: datetime | None = None,
    ) -> uuid.UUID | None:
        if not await self._projection_tables_available():
            return None
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            return None

        row = await pool.fetchrow(
            """
            UPDATE calendar_events
            SET status = 'cancelled',
                origin_updated_at = COALESCE($3, origin_updated_at),
                updated_at = now()
            WHERE source_id = $1 AND origin_ref = $2
            RETURNING id
            """,
            source_id,
            origin_ref,
            origin_updated_at,
        )
        if row is None:
            return None

        event_id: uuid.UUID = row["id"]
        await pool.execute(
            """
            UPDATE calendar_event_instances
            SET status = 'cancelled',
                updated_at = now()
            WHERE event_id = $1
            """,
            event_id,
        )
        return event_id

    async def _mark_projection_source_stale_events_cancelled(
        self,
        *,
        source_id: uuid.UUID,
        seen_origin_refs: list[str],
    ) -> None:
        if not await self._projection_tables_available():
            return
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            return

        if seen_origin_refs:
            await pool.execute(
                """
                UPDATE calendar_events
                SET status = 'cancelled',
                    updated_at = now()
                WHERE source_id = $1
                  AND NOT (origin_ref = ANY($2::text[]))
                """,
                source_id,
                seen_origin_refs,
            )
        else:
            await pool.execute(
                """
                UPDATE calendar_events
                SET status = 'cancelled',
                    updated_at = now()
                WHERE source_id = $1
                """,
                source_id,
            )

        await pool.execute(
            """
            UPDATE calendar_event_instances AS i
            SET status = 'cancelled',
                updated_at = now()
            FROM calendar_events AS e
            WHERE i.event_id = e.id
              AND e.source_id = $1
              AND e.status = 'cancelled'
            """,
            source_id,
        )

    async def _project_provider_changes(
        self,
        *,
        source_id: uuid.UUID,
        provider_name: str,
        calendar_id: str,
        updated_events: list[CalendarEvent],
        cancelled_ids: list[str],
    ) -> None:
        for event in updated_events:
            status_value = (
                event.status.value if event.status is not None else EventStatus.confirmed.value
            )
            visibility_value = (
                event.visibility.value
                if event.visibility is not None
                else EventVisibility.default.value
            )
            metadata = {
                "source_type": SOURCE_KIND_PROVIDER,
                "provider": provider_name,
                "calendar_id": calendar_id,
                "butler_generated": event.butler_generated,
                "butler_name": event.butler_name,
                "organizer": event.organizer,
                "attendees": [self._attendee_to_payload(attendee) for attendee in event.attendees],
                "created_at": event.created_at.isoformat() if event.created_at else None,
                "updated_at": event.updated_at.isoformat() if event.updated_at else None,
            }
            event_id = await self._upsert_projection_event(
                source_id=source_id,
                origin_ref=event.event_id,
                title=event.title,
                description=event.description,
                location=event.location,
                timezone=event.timezone,
                starts_at=event.start_at,
                ends_at=event.end_at,
                all_day=False,
                status=status_value,
                visibility=visibility_value,
                recurrence_rule=event.recurrence_rule,
                etag=event.etag,
                origin_updated_at=event.updated_at,
                metadata=metadata,
            )
            await self._upsert_projection_instance(
                event_id=event_id,
                source_id=source_id,
                origin_instance_ref=f"{event.event_id}:{event.start_at.isoformat()}",
                timezone=event.timezone,
                starts_at=event.start_at,
                ends_at=event.end_at,
                status=status_value,
                is_exception=False,
                origin_updated_at=event.updated_at,
                metadata={"source_type": SOURCE_KIND_PROVIDER, "provider": provider_name},
            )

        cancelled_at = datetime.now(UTC)
        for cancelled_id in cancelled_ids:
            await self._mark_projection_event_cancelled(
                source_id=source_id,
                origin_ref=cancelled_id,
                origin_updated_at=cancelled_at,
            )

    async def _project_scheduler_source(self) -> None:
        if not await self._projection_tables_available():
            return
        if not await self._table_exists("scheduled_tasks"):
            return
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            return

        source_id = await self._ensure_calendar_source(
            source_key=f"internal_scheduler:{self._butler_name}",
            source_kind=SOURCE_KIND_INTERNAL_SCHEDULER,
            lane="butler",
            provider="internal",
            butler_name=self._butler_name,
            display_name=f"{self._butler_name} schedules",
            writable=True,
            metadata={"projection": "scheduler"},
        )
        if source_id is None:
            return

        rows = await pool.fetch(
            """
            SELECT id, name, cron, dispatch_mode, prompt, job_name, job_args,
                   timezone, start_at, end_at, until_at, display_title,
                   calendar_event_id, enabled, updated_at
            FROM scheduled_tasks
            WHERE start_at IS NOT NULL AND end_at IS NOT NULL
            """
        )

        seen_origin_refs: list[str] = []
        now = datetime.now(UTC)
        for row in rows:
            record = dict(row)
            start_at = self._coerce_datetime(record.get("start_at"))
            end_at = self._coerce_datetime(record.get("end_at"))
            if start_at is None or end_at is None:
                continue

            origin_ref = str(record["id"])
            seen_origin_refs.append(origin_ref)
            timezone = str(record.get("timezone") or "UTC")
            title = str(record.get("display_title") or record.get("name") or "Scheduled task")
            status = (
                EventStatus.confirmed.value
                if record.get("enabled", True)
                else EventStatus.cancelled.value
            )
            metadata = {
                "source_type": SOURCE_KIND_INTERNAL_SCHEDULER,
                "name": record.get("name"),
                "cron": record.get("cron"),
                "dispatch_mode": record.get("dispatch_mode"),
                "prompt": record.get("prompt"),
                "job_name": record.get("job_name"),
                "job_args": self._normalize_json_object(record.get("job_args")),
                "until_at": record.get("until_at"),
                "calendar_event_id": record.get("calendar_event_id"),
            }
            event_id = await self._upsert_projection_event(
                source_id=source_id,
                origin_ref=origin_ref,
                title=title,
                description=None,
                location=None,
                timezone=timezone,
                starts_at=start_at,
                ends_at=end_at,
                all_day=False,
                status=status,
                visibility=EventVisibility.default.value,
                recurrence_rule=str(record.get("cron")) if record.get("cron") else None,
                etag=None,
                origin_updated_at=self._coerce_datetime(record.get("updated_at")),
                metadata=metadata,
            )
            await self._upsert_projection_instance(
                event_id=event_id,
                source_id=source_id,
                origin_instance_ref=f"{origin_ref}:schedule",
                timezone=timezone,
                starts_at=start_at,
                ends_at=end_at,
                status=status,
                is_exception=False,
                origin_updated_at=self._coerce_datetime(record.get("updated_at")),
                metadata={"source_type": SOURCE_KIND_INTERNAL_SCHEDULER},
            )

        await self._mark_projection_source_stale_events_cancelled(
            source_id=source_id,
            seen_origin_refs=seen_origin_refs,
        )
        await self._upsert_projection_cursor(
            source_id=source_id,
            cursor_name=SYNC_CURSOR_PROJECTION,
            sync_token=None,
            checkpoint={
                "projected_rows": len(seen_origin_refs),
                "source": SOURCE_KIND_INTERNAL_SCHEDULER,
            },
            full_sync_required=False,
            last_synced_at=now,
            last_success_at=now,
            last_error_at=None,
            last_error=None,
        )

    async def _project_reminders_source(self) -> None:
        if not await self._projection_tables_available():
            return
        if not await self._table_exists("reminders"):
            return
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            return

        source_id = await self._ensure_calendar_source(
            source_key=f"internal_reminders:{self._butler_name}",
            source_kind=SOURCE_KIND_INTERNAL_REMINDERS,
            lane="butler",
            provider="internal",
            butler_name=self._butler_name,
            display_name=f"{self._butler_name} reminders",
            writable=True,
            metadata={"projection": "reminders"},
        )
        if source_id is None:
            return

        rows = await pool.fetch("SELECT * FROM reminders")
        seen_origin_refs: list[str] = []
        now = datetime.now(UTC)

        for row in rows:
            record = dict(row)
            origin_ref = str(record["id"])
            seen_origin_refs.append(origin_ref)
            updated_at = self._coerce_datetime(record.get("updated_at"))
            timezone = str(record.get("timezone") or "UTC")
            next_trigger_at = self._coerce_datetime(record.get("next_trigger_at"))
            if next_trigger_at is None:
                next_trigger_at = self._coerce_datetime(record.get("due_at"))

            if next_trigger_at is None:
                await self._mark_projection_event_cancelled(
                    source_id=source_id,
                    origin_ref=origin_ref,
                    origin_updated_at=updated_at,
                )
                continue
            dismissed = bool(record.get("dismissed"))

            title = str(record.get("label") or record.get("message") or "Reminder")
            starts_at = next_trigger_at
            ends_at = next_trigger_at + timedelta(minutes=15)
            status = EventStatus.cancelled.value if dismissed else EventStatus.confirmed.value
            recurrence_rule_raw = record.get("recurrence_rule")
            recurrence_rule = (
                str(recurrence_rule_raw).strip()
                if isinstance(recurrence_rule_raw, str) and recurrence_rule_raw.strip()
                else None
            )
            metadata = {
                "source_type": SOURCE_KIND_INTERNAL_REMINDERS,
                "label": record.get("label"),
                "message": record.get("message"),
                "type": record.get("type"),
                "reminder_type": record.get("reminder_type"),
                "contact_id": record.get("contact_id"),
                "until_at": record.get("until_at"),
                "calendar_event_id": record.get("calendar_event_id"),
                "dismissed": dismissed,
            }
            event_id = await self._upsert_projection_event(
                source_id=source_id,
                origin_ref=origin_ref,
                title=title,
                description=str(record.get("message") or "").strip() or None,
                location=None,
                timezone=timezone,
                starts_at=starts_at,
                ends_at=ends_at,
                all_day=False,
                status=status,
                visibility=EventVisibility.default.value,
                recurrence_rule=recurrence_rule,
                etag=None,
                origin_updated_at=updated_at,
                metadata=metadata,
            )
            await self._upsert_projection_instance(
                event_id=event_id,
                source_id=source_id,
                origin_instance_ref=f"{origin_ref}:{starts_at.isoformat()}",
                timezone=timezone,
                starts_at=starts_at,
                ends_at=ends_at,
                status=status,
                is_exception=False,
                origin_updated_at=updated_at,
                metadata={"source_type": SOURCE_KIND_INTERNAL_REMINDERS},
            )

        await self._mark_projection_source_stale_events_cancelled(
            source_id=source_id,
            seen_origin_refs=seen_origin_refs,
        )
        await self._upsert_projection_cursor(
            source_id=source_id,
            cursor_name=SYNC_CURSOR_PROJECTION,
            sync_token=None,
            checkpoint={
                "projected_rows": len(seen_origin_refs),
                "source": SOURCE_KIND_INTERNAL_REMINDERS,
            },
            full_sync_required=False,
            last_synced_at=now,
            last_success_at=now,
            last_error_at=None,
            last_error=None,
        )

    async def _project_internal_sources(self) -> None:
        """Refresh non-provider butler projection sources (scheduler/reminders)."""
        try:
            await self._project_scheduler_source()
            await self._project_reminders_source()
        except Exception as exc:
            logger.error("Internal calendar projection refresh failed: %s", exc, exc_info=True)

    async def _projection_freshness_metadata(self) -> dict[str, Any]:
        if not await self._projection_tables_available():
            return {
                "available": False,
                "last_refreshed_at": None,
                "staleness_ms": None,
                "sources": [],
            }
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            return {
                "available": False,
                "last_refreshed_at": None,
                "staleness_ms": None,
                "sources": [],
            }

        rows = await pool.fetch(
            """
            SELECT
                s.id,
                s.source_key,
                s.source_kind,
                s.lane,
                s.provider,
                s.calendar_id,
                s.butler_name,
                c.cursor_name,
                c.last_synced_at,
                c.last_success_at,
                c.last_error_at,
                c.last_error,
                c.full_sync_required
            FROM calendar_sources AS s
            LEFT JOIN LATERAL (
                SELECT cursor_name, last_synced_at, last_success_at, last_error_at, last_error,
                       full_sync_required, updated_at
                FROM calendar_sync_cursors
                WHERE source_id = s.id
                ORDER BY updated_at DESC
                LIMIT 1
            ) AS c ON TRUE
            ORDER BY s.lane, s.source_kind, s.source_key
            """
        )

        now = datetime.now(UTC)
        cfg = self._config
        interval_minutes = (
            cfg.sync.interval_minutes if cfg is not None else DEFAULT_SYNC_INTERVAL_MINUTES
        )
        stale_threshold_ms = max(
            interval_minutes * 60 * 1000 * PROJECTION_STALENESS_MULTIPLIER,
            300_000,
        )

        source_payloads: list[dict[str, Any]] = []
        freshest_at: datetime | None = None
        for row in rows:
            last_synced_at = self._coerce_datetime(row["last_synced_at"])
            last_success_at = self._coerce_datetime(row["last_success_at"])
            last_error_at = self._coerce_datetime(row["last_error_at"])
            last_error = row["last_error"]
            full_sync_required = (
                bool(row["full_sync_required"]) if row["full_sync_required"] is not None else False
            )

            staleness_ms = None
            if last_synced_at is not None:
                staleness_ms = max(int((now - last_synced_at).total_seconds() * 1000), 0)
                if freshest_at is None or last_synced_at > freshest_at:
                    freshest_at = last_synced_at

            if last_error and (
                last_success_at is None or (last_error_at and last_error_at >= last_success_at)
            ):
                sync_state = PROJECTION_STATUS_FAILED
            elif last_synced_at is None:
                sync_state = PROJECTION_STATUS_STALE
            elif full_sync_required:
                sync_state = PROJECTION_STATUS_STALE
            elif staleness_ms is not None and staleness_ms > stale_threshold_ms:
                sync_state = PROJECTION_STATUS_STALE
            else:
                sync_state = PROJECTION_STATUS_FRESH

            source_payloads.append(
                {
                    "source_key": row["source_key"],
                    "source_kind": row["source_kind"],
                    "lane": row["lane"],
                    "provider": row["provider"],
                    "calendar_id": row["calendar_id"],
                    "butler_name": row["butler_name"],
                    "cursor_name": row["cursor_name"],
                    "last_synced_at": last_synced_at.isoformat() if last_synced_at else None,
                    "last_success_at": last_success_at.isoformat() if last_success_at else None,
                    "last_error_at": last_error_at.isoformat() if last_error_at else None,
                    "last_error": last_error,
                    "full_sync_required": full_sync_required,
                    "sync_state": sync_state,
                    "staleness_ms": staleness_ms,
                }
            )

        overall_staleness_ms = None
        if freshest_at is not None:
            overall_staleness_ms = max(int((now - freshest_at).total_seconds() * 1000), 0)
        return {
            "available": True,
            "last_refreshed_at": freshest_at.isoformat() if freshest_at else None,
            "staleness_ms": overall_staleness_ms,
            "sources": source_payloads,
        }

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
        now = datetime.now(UTC)

        source_id: uuid.UUID | None = None
        source_key = f"provider:{provider.name}:{calendar_id}"
        try:
            source_id = await self._ensure_calendar_source(
                source_key=source_key,
                source_kind=SOURCE_KIND_PROVIDER,
                lane="user",
                provider=provider.name,
                calendar_id=calendar_id,
                display_name=calendar_id,
                writable=True,
                metadata={"projection": "provider_sync"},
            )
        except Exception as exc:
            logger.debug("Failed to ensure provider calendar source '%s': %s", source_key, exc)

        effective_sync_token = sync_state.sync_token
        cursor_checkpoint: dict[str, Any] = {}
        if source_id is not None:
            try:
                cursor_row = await self._load_projection_cursor(
                    source_id=source_id,
                    cursor_name=SYNC_CURSOR_PROVIDER,
                )
            except Exception as exc:
                logger.debug("Failed loading projection cursor for '%s': %s", source_key, exc)
                cursor_row = None
            if cursor_row is not None:
                cursor_token = cursor_row.get("sync_token")
                if isinstance(cursor_token, str):
                    effective_sync_token = cursor_token
                cursor_checkpoint = self._normalize_json_object(cursor_row.get("checkpoint"))

        performed_full_resync = False
        error_message: str | None = None

        try:
            updated_events, cancelled_ids, next_token = await provider.sync_incremental(
                calendar_id=calendar_id,
                sync_token=effective_sync_token,
                full_sync_window_days=config.sync.full_sync_window_days,
            )
        except CalendarSyncTokenExpiredError:
            logger.warning(
                "Sync token expired for calendar '%s'; performing full re-sync", calendar_id
            )
            performed_full_resync = True
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
                error_message = str(exc)[:200]
                sync_state.last_sync_error = error_message
                self._sync_states[calendar_id] = sync_state
                await self._save_sync_state(calendar_id, sync_state)
                await self._upsert_projection_cursor(
                    source_id=source_id,
                    cursor_name=SYNC_CURSOR_PROVIDER,
                    sync_token=effective_sync_token,
                    checkpoint={
                        **cursor_checkpoint,
                        "provider": provider.name,
                        "calendar_id": calendar_id,
                        "error": error_message,
                    },
                    full_sync_required=True,
                    last_synced_at=now,
                    last_success_at=self._coerce_datetime(cursor_checkpoint.get("last_success_at")),
                    last_error_at=now,
                    last_error=error_message,
                )
                await self._record_projection_action(
                    idempotency_key=f"calendar-sync:{source_key}:error:{int(now.timestamp())}",
                    action_type="projection_sync_provider",
                    action_status="failed",
                    source_id=source_id,
                    origin_ref=None,
                    action_payload={
                        "calendar_id": calendar_id,
                        "provider": provider.name,
                        "sync_token": effective_sync_token,
                    },
                    action_result={"status": "failed"},
                    error=error_message,
                )
                await self._project_internal_sources()
                return
        except CalendarAuthError as exc:
            logger.error(
                "Incremental sync failed for calendar '%s': %s",
                calendar_id,
                exc,
                exc_info=True,
            )
            error_message = str(exc)[:200]
            sync_state.last_sync_error = error_message
            self._sync_states[calendar_id] = sync_state
            await self._save_sync_state(calendar_id, sync_state)
            await self._upsert_projection_cursor(
                source_id=source_id,
                cursor_name=SYNC_CURSOR_PROVIDER,
                sync_token=effective_sync_token,
                checkpoint={
                    **cursor_checkpoint,
                    "provider": provider.name,
                    "calendar_id": calendar_id,
                    "error": error_message,
                },
                full_sync_required=False,
                last_synced_at=now,
                last_success_at=self._coerce_datetime(cursor_checkpoint.get("last_success_at")),
                last_error_at=now,
                last_error=error_message,
            )
            await self._record_projection_action(
                idempotency_key=f"calendar-sync:{source_key}:error:{int(now.timestamp())}",
                action_type="projection_sync_provider",
                action_status="failed",
                source_id=source_id,
                origin_ref=None,
                action_payload={
                    "calendar_id": calendar_id,
                    "provider": provider.name,
                    "sync_token": effective_sync_token,
                },
                action_result={"status": "failed"},
                error=error_message,
            )
            await self._project_internal_sources()
            return

        if source_id is not None:
            try:
                await self._project_provider_changes(
                    source_id=source_id,
                    provider_name=provider.name,
                    calendar_id=calendar_id,
                    updated_events=updated_events,
                    cancelled_ids=cancelled_ids,
                )
            except Exception as exc:
                logger.error(
                    "Provider projection write failed for calendar '%s': %s",
                    calendar_id,
                    exc,
                    exc_info=True,
                )
                error_message = str(exc)[:200]
                await self._upsert_projection_cursor(
                    source_id=source_id,
                    cursor_name=SYNC_CURSOR_PROVIDER,
                    sync_token=effective_sync_token,
                    checkpoint={
                        "provider": provider.name,
                        "calendar_id": calendar_id,
                        "updated_events": len(updated_events),
                        "cancelled_events": len(cancelled_ids),
                    },
                    full_sync_required=False,
                    last_synced_at=now,
                    last_success_at=None,
                    last_error_at=now,
                    last_error=error_message,
                )
                await self._record_projection_action(
                    idempotency_key=f"calendar-sync:{source_key}:projection-error:{int(now.timestamp())}",
                    action_type="projection_sync_provider",
                    action_status="failed",
                    source_id=source_id,
                    origin_ref=None,
                    action_payload={
                        "calendar_id": calendar_id,
                        "provider": provider.name,
                        "updated_events": len(updated_events),
                        "cancelled_events": len(cancelled_ids),
                    },
                    action_result={"status": "failed"},
                    error=error_message,
                )
            else:
                checkpoint = {
                    "provider": provider.name,
                    "calendar_id": calendar_id,
                    "updated_events": len(updated_events),
                    "cancelled_events": len(cancelled_ids),
                    "full_resync": performed_full_resync,
                }
                await self._upsert_projection_cursor(
                    source_id=source_id,
                    cursor_name=SYNC_CURSOR_PROVIDER,
                    sync_token=next_token,
                    checkpoint=checkpoint,
                    full_sync_required=False,
                    last_synced_at=now,
                    last_success_at=now,
                    last_error_at=None,
                    last_error=None,
                )
                await self._record_projection_action(
                    idempotency_key=f"calendar-sync:{source_key}:{next_token}",
                    action_type="projection_sync_provider",
                    action_status="applied",
                    source_id=source_id,
                    origin_ref=None,
                    action_payload={
                        "calendar_id": calendar_id,
                        "provider": provider.name,
                        "sync_token_before": effective_sync_token,
                    },
                    action_result={
                        "next_sync_token": next_token,
                        "updated_events": len(updated_events),
                        "cancelled_events": len(cancelled_ids),
                        "full_resync": performed_full_resync,
                    },
                )

        pending_count = len(updated_events) + len(cancelled_ids)
        now_iso = now.isoformat()
        new_state = CalendarSyncState(
            sync_token=next_token,
            last_sync_at=now_iso,
            last_sync_error=error_message,
            last_batch_change_count=pending_count,
        )
        self._sync_states[calendar_id] = new_state
        await self._save_sync_state(calendar_id, new_state)
        await self._project_internal_sources()

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
    def _normalize_request_id(request_id: str | None) -> str | None:
        if request_id is None:
            return None
        normalized = request_id.strip()
        return normalized or None

    @staticmethod
    def _mutation_idempotency_key(action_type: str, request_id: str | None) -> str:
        normalized_request_id = CalendarModule._normalize_request_id(request_id)
        if normalized_request_id is not None:
            return f"{action_type}:request:{normalized_request_id}"
        return f"{action_type}:generated:{uuid.uuid4()}"

    async def _table_columns(self, table_name: str) -> set[str]:
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            return set()
        rows = await pool.fetch(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = current_schema() AND table_name = $1
            """,
            table_name,
        )
        return {str(row["column_name"]) for row in rows}

    async def _load_projection_action(
        self, idempotency_key: str
    ) -> tuple[str, dict[str, Any] | None, str | None] | None:
        if not await self._projection_tables_available():
            return None
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            return None
        row = await pool.fetchrow(
            """
            SELECT action_status, action_result, error
            FROM calendar_action_log
            WHERE idempotency_key = $1
            """,
            idempotency_key,
        )
        if row is None:
            return None
        status = str(row["action_status"])
        result = None
        if row["action_result"] is not None:
            normalized = self._normalize_json_object(row["action_result"])
            if normalized:
                result = normalized
        error = row["error"]
        return status, result, None if error is None else str(error)

    async def _prepare_workspace_mutation(
        self,
        *,
        action_type: str,
        request_id: str | None,
        action_payload: dict[str, Any],
        allow_pending_replay: bool = False,
    ) -> tuple[str, dict[str, Any] | None]:
        idempotency_key = self._mutation_idempotency_key(action_type, request_id)
        existing = await self._load_projection_action(idempotency_key)
        if existing is not None:
            status, existing_result, error = existing
            if (
                status in {MUTATION_STATUS_APPLIED, MUTATION_STATUS_NOOP}
                and existing_result is not None
            ):
                replay_result = dict(existing_result)
                replay_result["idempotent_replay"] = True
                return idempotency_key, replay_result
            if status == MUTATION_STATUS_FAILED:
                return idempotency_key, {
                    "status": "error",
                    "error": error or "Mutation failed",
                    "idempotent_replay": True,
                }
            if status == MUTATION_STATUS_PENDING and not allow_pending_replay:
                return idempotency_key, {
                    "status": "pending",
                    "message": "Mutation already queued for processing",
                    "idempotent_replay": True,
                }

        await self._record_projection_action(
            idempotency_key=idempotency_key,
            action_type=action_type,
            action_status=MUTATION_STATUS_PENDING,
            source_id=None,
            origin_ref=None,
            action_payload=action_payload,
            request_id=self._normalize_request_id(request_id),
        )
        return idempotency_key, None

    async def _finalize_workspace_mutation(
        self,
        *,
        idempotency_key: str,
        action_type: str,
        request_id: str | None,
        action_status: Literal["pending", "applied", "failed", "noop"],
        action_payload: dict[str, Any],
        action_result: dict[str, Any] | None,
        source_id: uuid.UUID | None = None,
        origin_ref: str | None = None,
        error: str | None = None,
    ) -> None:
        await self._record_projection_action(
            idempotency_key=idempotency_key,
            action_type=action_type,
            action_status=action_status,
            source_id=source_id,
            origin_ref=origin_ref,
            action_payload=action_payload,
            action_result=action_result,
            request_id=self._normalize_request_id(request_id),
            error=error,
        )

    async def _refresh_user_projection(self, calendar_id: str) -> dict[str, Any]:
        if not await self._projection_tables_available():
            return await self._projection_freshness_metadata()
        await self._sync_calendar(calendar_id)
        return await self._projection_freshness_metadata()

    async def _refresh_butler_projection(self) -> dict[str, Any]:
        await self._project_internal_sources()
        return await self._projection_freshness_metadata()

    async def _resolve_action_source_id(
        self,
        *,
        source_kind: str,
        lane: Literal["user", "butler"],
        calendar_id: str | None = None,
    ) -> uuid.UUID | None:
        if source_kind == SOURCE_KIND_PROVIDER:
            provider = self._require_provider()
            resolved_calendar = calendar_id or self._require_config().calendar_id
            return await self._ensure_calendar_source(
                source_key=f"provider:{provider.name}:{resolved_calendar}",
                source_kind=SOURCE_KIND_PROVIDER,
                lane="user",
                provider=provider.name,
                calendar_id=resolved_calendar,
                display_name=resolved_calendar,
                writable=True,
                metadata={"projection": "provider_sync"},
            )
        if source_kind == SOURCE_KIND_INTERNAL_SCHEDULER:
            return await self._ensure_calendar_source(
                source_key=f"internal_scheduler:{self._butler_name}",
                source_kind=SOURCE_KIND_INTERNAL_SCHEDULER,
                lane="butler",
                provider="internal",
                butler_name=self._butler_name,
                display_name=f"{self._butler_name} schedules",
                writable=True,
                metadata={"projection": "scheduler"},
            )
        if source_kind == SOURCE_KIND_INTERNAL_REMINDERS:
            return await self._ensure_calendar_source(
                source_key=f"internal_reminders:{self._butler_name}",
                source_kind=SOURCE_KIND_INTERNAL_REMINDERS,
                lane="butler",
                provider="internal",
                butler_name=self._butler_name,
                display_name=f"{self._butler_name} reminders",
                writable=True,
                metadata={"projection": "reminders"},
            )
        return None

    async def _gate_high_impact_mutation(
        self,
        *,
        action_type: str,
        tool_name: str,
        tool_args: dict[str, Any],
        request_id: str | None,
        idempotency_key: str,
        action_payload: dict[str, Any],
        source_kind: str,
    ) -> dict[str, Any] | None:
        if action_type not in MUTATION_HIGH_IMPACT_ACTIONS:
            return None
        if self._approval_enqueuer is None:
            return None

        gated_tool_args = dict(tool_args)
        gated_tool_args["_approval_bypass"] = True
        agent_summary = (
            f"Calendar workspace high-impact action requested: {tool_name} "
            f"(butler={self._butler_name})"
        )
        action_id = await self._approval_enqueuer(tool_name, gated_tool_args, agent_summary)
        response = {
            "status": "approval_required",
            "action_id": action_id,
            "message": "This workspace mutation has been queued for approval.",
        }
        source_id = await self._resolve_action_source_id(source_kind=source_kind, lane="butler")
        await self._finalize_workspace_mutation(
            idempotency_key=idempotency_key,
            action_type=action_type,
            request_id=request_id,
            action_status=MUTATION_STATUS_PENDING,
            action_payload=action_payload,
            action_result=response,
            source_id=source_id,
            origin_ref=None,
            error=None,
        )
        return response

    @staticmethod
    def _rrule_components(recurrence_rule: str) -> dict[str, str]:
        normalized = recurrence_rule.removeprefix("RRULE:")
        values: dict[str, str] = {}
        for part in normalized.split(";"):
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            values[key.strip().upper()] = value.strip()
        return values

    @staticmethod
    def _rrule_until(recurrence_rule: str) -> datetime | None:
        components = CalendarModule._rrule_components(recurrence_rule)
        until_raw = components.get("UNTIL")
        if not until_raw:
            return None
        if until_raw.endswith("Z"):
            until_raw = f"{until_raw[:-1]}+00:00"
        for pattern in ("%Y%m%dT%H%M%S%z", "%Y%m%d"):
            try:
                parsed = datetime.strptime(until_raw, pattern)
            except ValueError:
                continue
            if pattern == "%Y%m%d":
                return datetime(parsed.year, parsed.month, parsed.day, tzinfo=UTC)
            return parsed.astimezone(UTC)
        return None

    @staticmethod
    def _rrule_to_cron(start_at: datetime, recurrence_rule: str) -> str:
        components = CalendarModule._rrule_components(recurrence_rule)
        freq = components.get("FREQ")
        if freq is None:
            raise ValueError("recurrence_rule must include FREQ")

        minute = start_at.minute
        hour = start_at.hour
        if freq == "DAILY":
            return f"{minute} {hour} * * *"
        if freq == "WEEKLY":
            byday = components.get("BYDAY")
            day_lookup = {
                "SU": "0",
                "MO": "1",
                "TU": "2",
                "WE": "3",
                "TH": "4",
                "FR": "5",
                "SA": "6",
            }
            if byday:
                parts = [day_lookup[item] for item in byday.split(",") if item in day_lookup]
                if not parts:
                    raise ValueError("recurrence_rule BYDAY must contain at least one valid day")
                day_of_week = ",".join(parts)
            else:
                day_of_week = str((start_at.weekday() + 1) % 7)
            return f"{minute} {hour} * * {day_of_week}"
        if freq == "MONTHLY":
            day_of_month = components.get("BYMONTHDAY") or str(start_at.day)
            return f"{minute} {hour} {day_of_month} * *"
        if freq == "YEARLY":
            return f"{minute} {hour} {start_at.day} {start_at.month} *"
        raise ValueError("Unsupported recurrence_rule frequency for scheduler projection")

    @staticmethod
    def _normalize_butler_event_source_hint(
        source_hint: str | None,
    ) -> Literal["scheduled_task", "butler_reminder"] | None:
        if source_hint is None:
            return None
        normalized = source_hint.strip().lower()
        if normalized in {"scheduled_task", "schedule", "scheduler"}:
            return BUTLER_EVENT_SOURCE_SCHEDULED
        if normalized in {"butler_reminder", "reminder", "reminders"}:
            return BUTLER_EVENT_SOURCE_REMINDER
        raise ValueError("source_hint must be one of: scheduled_task | butler_reminder")

    @staticmethod
    def _normalize_reminder_row(row: Mapping[str, Any]) -> dict[str, Any]:
        result = dict(row)
        if "label" not in result and "message" in result:
            result["label"] = result["message"]
        if "message" not in result and "label" in result:
            result["message"] = result["label"]
        if "next_trigger_at" not in result and "due_at" in result:
            result["next_trigger_at"] = result["due_at"]
        if "due_at" not in result and "next_trigger_at" in result:
            result["due_at"] = result["next_trigger_at"]
        return result

    async def _find_scheduled_task_target(self, event_uuid: uuid.UUID) -> uuid.UUID | None:
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None or not await self._table_exists("scheduled_tasks"):
            return None

        columns = await self._table_columns("scheduled_tasks")
        if "calendar_event_id" in columns:
            row = await pool.fetchrow(
                """
                SELECT id
                FROM scheduled_tasks
                WHERE id = $1 OR calendar_event_id = $1
                LIMIT 1
                """,
                event_uuid,
            )
        else:
            row = await pool.fetchrow(
                "SELECT id FROM scheduled_tasks WHERE id = $1 LIMIT 1",
                event_uuid,
            )
        if row is None:
            return None
        return row["id"]

    async def _find_reminder_target(self, event_uuid: uuid.UUID) -> uuid.UUID | None:
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None or not await self._table_exists("reminders"):
            return None

        columns = await self._table_columns("reminders")
        if "calendar_event_id" in columns:
            row = await pool.fetchrow(
                """
                SELECT id
                FROM reminders
                WHERE id = $1 OR calendar_event_id = $1
                LIMIT 1
                """,
                event_uuid,
            )
        else:
            row = await pool.fetchrow(
                "SELECT id FROM reminders WHERE id = $1 LIMIT 1",
                event_uuid,
            )
        if row is None:
            return None
        return row["id"]

    async def _resolve_butler_event_target(
        self,
        *,
        event_id: str,
        source_hint: Literal["scheduled_task", "butler_reminder"] | None,
    ) -> tuple[Literal["scheduled_task", "butler_reminder"], uuid.UUID]:
        normalized = event_id.strip()
        if not normalized:
            raise ValueError("event_id must be a non-empty string")

        event_uuid = uuid.UUID(normalized)
        if source_hint == BUTLER_EVENT_SOURCE_SCHEDULED:
            schedule_id = await self._find_scheduled_task_target(event_uuid)
            if schedule_id is None:
                raise ValueError(f"No scheduled task found for event_id '{event_id}'")
            return BUTLER_EVENT_SOURCE_SCHEDULED, schedule_id
        if source_hint == BUTLER_EVENT_SOURCE_REMINDER:
            reminder_id = await self._find_reminder_target(event_uuid)
            if reminder_id is None:
                raise ValueError(f"No reminder found for event_id '{event_id}'")
            return BUTLER_EVENT_SOURCE_REMINDER, reminder_id

        schedule_id = await self._find_scheduled_task_target(event_uuid)
        if schedule_id is not None:
            return BUTLER_EVENT_SOURCE_SCHEDULED, schedule_id
        reminder_id = await self._find_reminder_target(event_uuid)
        if reminder_id is not None:
            return BUTLER_EVENT_SOURCE_REMINDER, reminder_id
        raise ValueError(f"No butler event found for event_id '{event_id}'")

    async def _create_reminder_event(
        self,
        *,
        title: str,
        start_at: datetime,
        timezone: str,
        until_at: datetime | None,
        recurrence_rule: str | None,
        cron: str | None,
        action: str,
        action_args: dict[str, Any] | None,
        calendar_event_id: uuid.UUID,
    ) -> dict[str, Any]:
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            raise RuntimeError("Database pool is not available")
        if not await self._table_exists("reminders"):
            raise ValueError("Reminder-backed butler events are not available on this butler")

        columns = await self._table_columns("reminders")
        args = dict(action_args or {})
        insert_columns: list[str] = []
        insert_values: list[Any] = []

        def add(column: str, value: Any) -> None:
            insert_columns.append(column)
            insert_values.append(value)

        reminder_type = "one_time"
        reminder_legacy_type = "one_time"
        normalized_rule = _normalize_recurrence_rule(recurrence_rule)
        if normalized_rule is not None or cron is not None:
            reminder_type = "recurring"
            reminder_legacy_type = "recurring_monthly"
            if normalized_rule and "FREQ=YEARLY" in normalized_rule.upper():
                reminder_legacy_type = "recurring_yearly"

        if "label" in columns:
            add("label", title)
        if "message" in columns:
            add("message", action)
        if "type" in columns:
            add("type", reminder_legacy_type)
        if "reminder_type" in columns:
            add("reminder_type", reminder_type)
        if "next_trigger_at" in columns:
            add("next_trigger_at", start_at)
        if "due_at" in columns:
            add("due_at", start_at)
        if "timezone" in columns:
            add("timezone", timezone)
        if "until_at" in columns:
            add("until_at", until_at)
        if "recurrence_rule" in columns:
            add("recurrence_rule", normalized_rule)
        if "cron" in columns:
            add("cron", cron)
        if "dismissed" in columns:
            add("dismissed", False)
        if "calendar_event_id" in columns:
            add("calendar_event_id", calendar_event_id)
        if "updated_at" in columns:
            add("updated_at", datetime.now(UTC))
        if "contact_id" in columns and "contact_id" in args:
            contact_id_value = args.get("contact_id")
            if contact_id_value is None:
                add("contact_id", None)
            else:
                add("contact_id", uuid.UUID(str(contact_id_value)))

        placeholders = [f"${idx}" for idx in range(1, len(insert_values) + 1)]
        row = await pool.fetchrow(
            f"""
            INSERT INTO reminders ({", ".join(insert_columns)})
            VALUES ({", ".join(placeholders)})
            RETURNING *
            """,
            *insert_values,
        )
        if row is None:
            raise RuntimeError("Failed to create reminder-backed event")
        return self._normalize_reminder_row(dict(row))

    async def _update_reminder_event(
        self,
        *,
        reminder_id: uuid.UUID,
        title: str | None,
        start_at: datetime | None,
        timezone: str | None,
        until_at: datetime | None,
        recurrence_rule: str | None,
        cron: str | None,
        enabled: bool | None,
    ) -> dict[str, Any]:
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            raise RuntimeError("Database pool is not available")

        row = await pool.fetchrow("SELECT * FROM reminders WHERE id = $1", reminder_id)
        if row is None:
            raise ValueError(f"Reminder {reminder_id} not found")
        existing = self._normalize_reminder_row(dict(row))
        columns = await self._table_columns("reminders")

        updates: list[str] = []
        params: list[Any] = [reminder_id]
        idx = 2

        def add(column: str, value: Any) -> None:
            nonlocal idx
            updates.append(f"{column} = ${idx}")
            params.append(value)
            idx += 1

        normalized_rule = _normalize_recurrence_rule(recurrence_rule) if recurrence_rule else None
        effective_trigger = start_at if start_at is not None else existing.get("next_trigger_at")
        if title is not None:
            if "label" in columns:
                add("label", title)
            if "message" in columns:
                add("message", title)
        if effective_trigger is not None:
            if "next_trigger_at" in columns:
                add("next_trigger_at", effective_trigger)
            if "due_at" in columns:
                add("due_at", effective_trigger)
        if timezone is not None and "timezone" in columns:
            add("timezone", timezone)
        if "until_at" in columns:
            add("until_at", until_at)
        if recurrence_rule is not None and "recurrence_rule" in columns:
            add("recurrence_rule", normalized_rule)
        if cron is not None and "cron" in columns:
            add("cron", cron)
        if enabled is not None:
            if "dismissed" in columns:
                add("dismissed", not enabled)
            if "next_trigger_at" in columns and not enabled:
                add("next_trigger_at", None)
        if "updated_at" in columns:
            add("updated_at", datetime.now(UTC))

        if not updates:
            return existing

        query = f"UPDATE reminders SET {', '.join(updates)} WHERE id = $1 RETURNING *"
        updated = await pool.fetchrow(query, *params)
        if updated is None:
            raise ValueError(f"Reminder {reminder_id} not found")
        return self._normalize_reminder_row(dict(updated))

    async def _delete_reminder_event(self, reminder_id: uuid.UUID) -> bool:
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            raise RuntimeError("Database pool is not available")
        deleted = await pool.fetchval(
            "DELETE FROM reminders WHERE id = $1 RETURNING id",
            reminder_id,
        )
        return deleted is not None

    async def _toggle_reminder_event(self, reminder_id: uuid.UUID, enabled: bool) -> dict[str, Any]:
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            raise RuntimeError("Database pool is not available")
        row = await pool.fetchrow("SELECT * FROM reminders WHERE id = $1", reminder_id)
        if row is None:
            raise ValueError(f"Reminder {reminder_id} not found")
        existing = self._normalize_reminder_row(dict(row))
        columns = await self._table_columns("reminders")
        updates: list[str] = []
        params: list[Any] = [reminder_id]
        idx = 2

        def add(column: str, value: Any) -> None:
            nonlocal idx
            updates.append(f"{column} = ${idx}")
            params.append(value)
            idx += 1

        if "dismissed" in columns:
            add("dismissed", not enabled)
        if "next_trigger_at" in columns:
            if enabled:
                next_trigger = existing.get("next_trigger_at") or existing.get("due_at")
                add("next_trigger_at", next_trigger)
            else:
                add("next_trigger_at", None)
        if "updated_at" in columns:
            add("updated_at", datetime.now(UTC))

        query = f"UPDATE reminders SET {', '.join(updates)} WHERE id = $1 RETURNING *"
        updated = await pool.fetchrow(query, *params)
        if updated is None:
            raise ValueError(f"Reminder {reminder_id} not found")
        return self._normalize_reminder_row(dict(updated))

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
