"""Calendar google mapping implementation."""

from __future__ import annotations

from datetime import UTC, date, datetime, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .constants import BUTLER_GENERATED_PRIVATE_KEY, BUTLER_NAME_PRIVATE_KEY
from .models import (
    AttendeeInfo,
    AttendeeResponseStatus,
    CalendarEvent,
    CalendarEventCreate,
    CalendarEventUpdate,
    EventStatus,
    EventVisibility,
    _is_date_only,
)


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
            response_status_explicit = False
            if isinstance(response_status_raw, str):
                try:
                    response_status = AttendeeResponseStatus(response_status_raw.strip())
                    response_status_explicit = True
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

            attendee = AttendeeInfo(
                email=normalized_email,
                display_name=display_name,
                response_status=response_status,
                optional=optional,
                organizer=organizer,
                self_=self_,
                comment=comment,
            )
            attendee._response_status_explicit = response_status_explicit
            attendees.append(attendee)
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


def _parse_google_event_transparency(value: Any) -> str | None:
    """Normalize Google ``transparency`` to ``opaque`` (busy) / ``transparent`` (free)."""
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    return normalized if normalized in ("opaque", "transparent") else None


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
    all_day = (
        _normalize_optional_text(start_payload.get("date")) is not None
        and _normalize_optional_text(end_payload.get("date")) is not None
        and _normalize_optional_text(start_payload.get("dateTime")) is None
        and _normalize_optional_text(end_payload.get("dateTime")) is None
    )

    title = _normalize_optional_text(payload.get("summary")) or "(untitled)"
    butler_generated, butler_name = _extract_google_private_metadata(
        payload.get("extendedProperties")
    )

    # Parse extended fields per spec section 5.1.
    event_status = _parse_google_event_status(status_raw)
    visibility = _parse_google_event_visibility(payload.get("visibility"))
    organizer = _extract_google_organizer(payload.get("organizer"))
    transparency = _parse_google_event_transparency(payload.get("transparency"))
    etag = _normalize_optional_text(payload.get("etag"))
    created_at = _parse_google_rfc3339_optional(payload.get("created"))
    updated_at = _parse_google_rfc3339_optional(payload.get("updated"))

    description_text = _normalize_optional_text(payload.get("description"))
    return CalendarEvent(
        event_id=event_id,
        title=title,
        start_at=start_at,
        end_at=end_at,
        timezone=timezone,
        all_day=all_day,
        description=description_text,
        body=description_text,
        location=_normalize_optional_text(payload.get("location")),
        attendees=_extract_google_attendees(payload.get("attendees")),
        recurrence_rule=_extract_google_recurrence_rule(payload.get("recurrence")),
        color_id=_normalize_optional_text(payload.get("colorId")),
        butler_generated=butler_generated,
        butler_name=butler_name,
        status=event_status,
        organizer=organizer,
        visibility=visibility,
        transparency=transparency,
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
    # `body` takes precedence over `description` as the richer content field;
    # both map to Google's single `description` property.
    google_description = payload.body if payload.body is not None else payload.description
    if google_description is not None:
        body["description"] = google_description
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
    existing_all_day: bool | None = None,
) -> dict[str, Any]:
    """Translate a CalendarEventUpdate patch into a partial Google Calendar API event body.

    Only fields that are explicitly set (non-None) are included in the body so
    that unchanged fields are not overwritten on the server (true partial-update
    semantics as required by the PATCH endpoint).

    ``existing_start_at``, ``existing_end_at``, ``existing_timezone``, and
    ``existing_all_day`` are supplied by the provider when a partial patch needs
    the existing event boundaries. That preserves date-only Google boundaries
    when restoring or updating an all-day event.
    """
    body: dict[str, Any] = {}

    # Title / summary
    if patch.title is not None:
        body["summary"] = patch.title

    # Optional text fields
    # `body` takes precedence over `description` as the richer content field;
    # both map to Google's single `description` property.
    google_description = patch.body if patch.body is not None else patch.description
    if google_description is not None:
        body["description"] = google_description
    if patch.location is not None:
        body["location"] = patch.location

    # Timezone and time boundaries — handle together so that timezone is applied
    # consistently to both start and end.
    timezone = patch.timezone

    if (
        patch.start_at is not None
        or patch.end_at is not None
        or timezone is not None
        or patch.all_day is not None
    ):
        # Resolve effective start/end datetimes.
        start_dt = patch.start_at if patch.start_at is not None else existing_start_at
        end_dt = patch.end_at if patch.end_at is not None else existing_end_at
        effective_tz = timezone if timezone is not None else existing_timezone
        is_all_day = patch.all_day if patch.all_day is not None else existing_all_day

        if is_all_day:
            if start_dt is not None:
                body["start"] = {"date": start_dt.date().isoformat()}
            if end_dt is not None:
                body["end"] = {"date": end_dt.date().isoformat()}
        else:
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
