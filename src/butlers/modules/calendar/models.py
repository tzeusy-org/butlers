"""Calendar models implementation."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    ValidationInfo,
    field_validator,
    model_validator,
)

from butlers.modules.base import ToolGroupMixin

from .constants import (
    DEFAULT_SYNC_INTERVAL_MINUTES,
    DEFAULT_SYNC_WINDOW_DAYS,
    CalendarConflictPolicy,
)


class CalendarConflictDefaults(BaseModel):
    """Default behavior for overlapping event handling."""

    model_config = ConfigDict(extra="forbid")

    policy: CalendarConflictPolicy = "suggest"
    require_approval_for_overlap: bool = True


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


class CalendarConfig(ToolGroupMixin, BaseModel):
    """Configuration for the Calendar module.

    Tool groups
    -----------
    core : list_events, get_event, create_event, update_event, delete_event,
           sync_status, force_sync, set_primary
    response : approval-bound self invitation response (requires response_enabled)
    butler_events : create_butler_event, update_butler_event,
                    delete_butler_event, toggle_butler_event
    """

    provider: str = Field(min_length=1)
    account: str | None = None
    response_enabled: bool = False
    calendar_id: str | None = None
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

    @field_validator("account", mode="before")
    @classmethod
    def _normalize_account(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized if normalized else None

    @field_validator("calendar_id", mode="before")
    @classmethod
    def _normalize_calendar_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized if normalized else None

    @field_validator("timezone")
    @classmethod
    def _normalize_timezone(cls, value: str, info: ValidationInfo) -> str:
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
    # Provider parsing provenance, deliberately absent from tool/model payloads.
    # Manually constructed/defaulted attendees cannot certify provider status.
    _response_status_explicit: bool = PrivateAttr(default=False)


class CalendarEvent(BaseModel):
    """Canonical event shape shared across provider implementations."""

    event_id: str
    title: str
    start_at: datetime
    end_at: datetime
    timezone: str
    all_day: bool = False
    description: str | None = None
    body: str | None = None
    location: str | None = None
    attendees: list[AttendeeInfo] = Field(default_factory=list)
    recurrence_rule: str | None = None
    color_id: str | None = None
    butler_generated: bool = False
    butler_name: str | None = None
    # Authorship fields populated from DB projection columns (core_074).
    source_butler: str | None = None
    source_session_id: str | None = None
    # Entity associations from calendar_event_entities junction table.
    entity_ids: list[uuid.UUID] = Field(default_factory=list)
    # Extended fields from spec section 5.1
    status: EventStatus | None = None
    organizer: str | None = None
    visibility: EventVisibility | None = None
    # Google ``transparency``: "opaque" (busy) or "transparent" (free); None if unknown.
    transparency: str | None = None
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
    body: str | None = None
    location: str | None = None
    attendees: list[str] = Field(default_factory=list)
    recurrence_rule: str | None = None
    notification: CalendarNotificationInput | bool | int | None = None
    color_id: str | None = None
    status: EventStatus | None = None
    visibility: EventVisibility | None = None
    notes: str | None = None
    private_metadata: dict[str, str] = Field(default_factory=dict)
    entity_ids: list[uuid.UUID] = Field(default_factory=list)

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
    all_day: bool | None = None
    timezone: str | None = None
    description: str | None = None
    body: str | None = None
    location: str | None = None
    attendees: list[str] | None = None
    recurrence_rule: str | None = None
    recurrence_scope: Literal["this", "following", "series"] = "series"
    color_id: str | None = None
    private_metadata: dict[str, str] | None = None
    entity_ids: list[uuid.UUID] | None = None
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


@dataclass(frozen=True)
class BusyWindow:
    """A single merged busy interval returned by ``get_free_busy``.

    Bounds are timezone-aware datetimes. Windows returned together are sorted by
    ``start_at`` and non-overlapping (overlapping/touching inputs are merged).
    """

    start_at: datetime
    end_at: datetime
