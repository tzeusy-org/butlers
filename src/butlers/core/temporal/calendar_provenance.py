"""Pure calendar provenance rules shared by context and conflict analysis.

Projection rows are provider truth and remain visible to the workspace. These
helpers answer the narrower question of whether a row is eligible to represent
a human meeting in deterministic analysis.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _metadata_object(metadata: Any) -> Mapping[str, Any] | None:
    """Return a JSON-object-shaped metadata value without raising on bad input."""
    if isinstance(metadata, Mapping):
        return metadata
    if not isinstance(metadata, str):
        return None
    try:
        decoded = json.loads(metadata)
    except (TypeError, ValueError):
        return None
    return decoded if isinstance(decoded, Mapping) else None


def is_explicit_butler_generated(metadata: Any) -> bool:
    """Whether metadata carries the sole generated-event exclusion marker.

    Google private properties historically arrive as strings, while projected
    JSONB normally contains a boolean. Both canonical true forms count; source
    names, title prefixes, and malformed data never infer authorship.
    """
    metadata_object = _metadata_object(metadata)
    if metadata_object is None:
        return False
    value = metadata_object.get("butler_generated")
    return value is True or (isinstance(value, str) and value.strip().lower() == "true")


def is_owner_attending(metadata: Any) -> bool:
    """Whether the owner still intends to attend, per their own RSVP.

    Only the ``self`` attendee's ``declined`` answer excludes an event; a guest
    declining, a tentative answer, no answer, or unparseable metadata all keep it
    (fail toward showing provider truth). Projected metadata spells the field
    ``response_status`` while raw Google payloads use ``responseStatus``; both count.
    """
    metadata_object = _metadata_object(metadata)
    if metadata_object is None:
        return True
    attendees = metadata_object.get("attendees")
    if not isinstance(attendees, list):
        return True
    for attendee in attendees:
        if not isinstance(attendee, Mapping) or attendee.get("self") is not True:
            continue
        raw = attendee.get("response_status", attendee.get("responseStatus"))
        if isinstance(raw, str) and raw.strip().lower() == "declined":
            return False
    return True


def is_transparent(metadata: Any) -> bool:
    """Whether the event is marked free (Google ``transparency: transparent``)."""
    metadata_object = _metadata_object(metadata)
    if metadata_object is None:
        return False
    value = metadata_object.get("transparency")
    return isinstance(value, str) and value.strip().lower() == "transparent"


def counts_toward_owner_load(metadata: Any) -> bool:
    """Whether an event is busy time the owner will attend (radar and day load)."""
    return is_owner_attending(metadata) and not is_transparent(metadata)


def is_legacy_all_day_like(
    *,
    all_day: bool,
    starts_at: datetime,
    ends_at: datetime,
    timezone: str | None,
) -> bool:
    """Recognize a pre-provenance all-day row without changing stored truth.

    The heuristic is intentionally narrow: explicit all-day rows are handled by
    the caller, while a legacy row must span at least 24 hours and both
    boundaries must be local midnight in a valid IANA timezone. Parse failures
    retain ordinary timed-event behavior.
    """
    if all_day is True:
        return False
    if not isinstance(starts_at, datetime) or not isinstance(ends_at, datetime):
        return False
    if starts_at.tzinfo is None or ends_at.tzinfo is None:
        return False
    if not isinstance(timezone, str) or not timezone.strip():
        return False

    try:
        if ends_at - starts_at < timedelta(hours=24):
            return False
        zone = ZoneInfo(timezone.strip())
        starts_local = starts_at.astimezone(zone)
        ends_local = ends_at.astimezone(zone)
    except (OverflowError, TypeError, ValueError, ZoneInfoNotFoundError):
        return False

    return (
        starts_local.hour == 0
        and starts_local.minute == 0
        and starts_local.second == 0
        and starts_local.microsecond == 0
        and ends_local.hour == 0
        and ends_local.minute == 0
        and ends_local.second == 0
        and ends_local.microsecond == 0
    )


def is_calendar_analysis_candidate(
    *,
    metadata: Any,
    all_day: bool,
    starts_at: datetime,
    ends_at: datetime,
    timezone: str | None,
) -> bool:
    """Return whether a projection row can represent a human timed meeting."""
    if all_day is True:
        return False
    if is_explicit_butler_generated(metadata):
        return False
    return not is_legacy_all_day_like(
        all_day=all_day,
        starts_at=starts_at,
        ends_at=ends_at,
        timezone=timezone,
    )
