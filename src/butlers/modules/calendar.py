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
import os
from datetime import UTC, date, datetime, timedelta, tzinfo
from typing import Any, Literal
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

from butlers.modules.base import Module

GOOGLE_CALENDAR_CREDENTIALS_ENV = "BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON"
GOOGLE_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_CALENDAR_API_BASE_URL = "https://www.googleapis.com/calendar/v3"


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


def _extract_google_attendees(payload: Any) -> list[str]:
    if not isinstance(payload, list):
        return []

    attendees: list[str] = []
    for entry in payload:
        if isinstance(entry, dict):
            email = entry.get("email")
            if isinstance(email, str):
                normalized = email.strip()
                if normalized:
                    attendees.append(normalized)
        elif isinstance(entry, str):
            normalized = entry.strip()
            if normalized:
                attendees.append(normalized)
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


def _google_event_to_calendar_event(
    payload: dict[str, Any],
    *,
    fallback_timezone: str,
) -> CalendarEvent | None:
    status = payload.get("status")
    if isinstance(status, str) and status.lower() == "cancelled":
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
    )


class CalendarConflictDefaults(BaseModel):
    """Default behavior for overlapping event handling."""

    model_config = ConfigDict(extra="forbid")

    policy: Literal["suggest", "allow", "reject"] = "suggest"
    require_approval_for_overlap: bool = True


class CalendarNotificationDefaults(BaseModel):
    """Default notification and color behavior for new events."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    minutes_before: int = Field(default=15, ge=0)
    color_id: str | None = None


class CalendarConfig(BaseModel):
    """Configuration for the Calendar module."""

    provider: str = Field(min_length=1)
    calendar_id: str = Field(min_length=1)
    timezone: str = "UTC"
    conflicts: CalendarConflictDefaults = Field(default_factory=CalendarConflictDefaults)
    event_defaults: CalendarNotificationDefaults = Field(
        default_factory=CalendarNotificationDefaults
    )

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


class CalendarEvent(BaseModel):
    """Canonical event shape shared across provider implementations."""

    event_id: str
    title: str
    start_at: datetime
    end_at: datetime
    timezone: str
    description: str | None = None
    location: str | None = None
    attendees: list[str] = Field(default_factory=list)
    recurrence_rule: str | None = None
    color_id: str | None = None


class CalendarEventCreate(BaseModel):
    """Payload for creating a calendar event."""

    title: str
    start_at: datetime
    end_at: datetime
    timezone: str | None = None
    description: str | None = None
    location: str | None = None
    attendees: list[str] = Field(default_factory=list)
    recurrence_rule: str | None = None
    color_id: str | None = None


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
    color_id: str | None = None


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
    async def delete_event(self, *, calendar_id: str, event_id: str) -> None:
        """Delete (or cancel) an event."""
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
    async def shutdown(self) -> None:
        """Release provider resources."""
        ...


class _GoogleProvider(CalendarProvider):
    """Google provider with OAuth refresh-token and authenticated request helpers."""

    def __init__(
        self,
        config: CalendarConfig,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._config = config
        self._owns_http_client = http_client is None
        credentials = _GoogleOAuthCredentials.from_env()
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
    ) -> dict[str, Any]:
        response = await self._request_with_bearer(
            method=method,
            path=path,
            params=params,
            json_body=json_body,
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
    ) -> httpx.Response:
        normalized_path = path if path.startswith("/") else f"/{path}"
        url = f"{GOOGLE_CALENDAR_API_BASE_URL}{normalized_path}"

        response = await self._request_once(
            method=method,
            url=url,
            params=params,
            json_body=json_body,
            force_refresh=False,
        )

        if response.status_code == 401:
            response = await self._request_once(
                method=method,
                url=url,
                params=params,
                json_body=json_body,
                force_refresh=True,
            )
        return response

    async def _request_once(
        self,
        *,
        method: str,
        url: str,
        params: dict[str, Any] | None,
        json_body: dict[str, Any] | None,
        force_refresh: bool,
    ) -> httpx.Response:
        access_token = await self._oauth.get_access_token(force_refresh=force_refresh)
        headers = {"Authorization": f"Bearer {access_token}"}
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
        raise NotImplementedError("Google calendar provider is not implemented yet")

    async def update_event(
        self,
        *,
        calendar_id: str,
        event_id: str,
        patch: CalendarEventUpdate,
    ) -> CalendarEvent:
        raise NotImplementedError("Google calendar provider is not implemented yet")

    async def delete_event(self, *, calendar_id: str, event_id: str) -> None:
        raise NotImplementedError("Google calendar provider is not implemented yet")

    async def find_conflicts(
        self,
        *,
        calendar_id: str,
        candidate: CalendarEventCreate,
    ) -> list[CalendarEvent]:
        raise NotImplementedError("Google calendar provider is not implemented yet")

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

    @staticmethod
    def _coerce_config(config: Any) -> CalendarConfig:
        return config if isinstance(config, CalendarConfig) else CalendarConfig(**(config or {}))

    async def register_tools(self, mcp: Any, config: Any, db: Any) -> None:
        self._config = self._coerce_config(config)
        module = self

        @mcp.tool()
        async def calendar_list_events(
            calendar_id: str | None = None,
            start_at: datetime | None = None,
            end_at: datetime | None = None,
            limit: int = 50,
        ) -> dict[str, Any]:
            """List calendar events using the configured provider."""
            provider = module._require_provider()
            resolved_calendar_id = module._resolve_calendar_id(calendar_id)
            events = await provider.list_events(
                calendar_id=resolved_calendar_id,
                start_at=start_at,
                end_at=end_at,
                limit=limit,
            )
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
            """Get a single calendar event by id using the configured provider."""
            normalized_event_id = event_id.strip()
            if not normalized_event_id:
                raise ValueError("event_id must be a non-empty string")

            provider = module._require_provider()
            resolved_calendar_id = module._resolve_calendar_id(calendar_id)
            event = await provider.get_event(
                calendar_id=resolved_calendar_id,
                event_id=normalized_event_id,
            )
            return {
                "provider": provider.name,
                "calendar_id": resolved_calendar_id,
                "event": None if event is None else module._event_to_payload(event),
            }

    async def on_startup(self, config: Any, db: Any) -> None:
        self._config = self._coerce_config(config)

        provider_cls = self._PROVIDER_CLASSES.get(self._config.provider)
        if provider_cls is None:
            supported = ", ".join(sorted(self._PROVIDER_CLASSES))
            raise RuntimeError(
                f"Unsupported calendar provider '{self._config.provider}'. "
                f"Supported providers: {supported}"
            )

        self._provider = provider_cls(self._config)

    async def on_shutdown(self) -> None:
        if self._provider is not None:
            await self._provider.shutdown()
        self._provider = None

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
    def _event_to_payload(event: CalendarEvent) -> dict[str, Any]:
        return {
            "event_id": event.event_id,
            "title": event.title,
            "start_at": event.start_at.isoformat(),
            "end_at": event.end_at.isoformat(),
            "timezone": event.timezone,
            "description": event.description,
            "location": event.location,
            "attendees": list(event.attendees),
            "recurrence_rule": event.recurrence_rule,
            "color_id": event.color_id,
        }
