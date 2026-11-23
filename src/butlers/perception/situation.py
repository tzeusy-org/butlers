"""Typed situation reads and the timeline query (RFC 0038 S1/S3, bu-dhloi1.21).

``situation.read`` returns one of the four states (known, unknown, unmeasurable,
unconfigured) with its source, age, freshness, labels and availability reason.
A known value past its freshness reads as unknown, its last value carried
separately as stale. ``timeline.query`` returns immutable records with derived
``retracted`` and ``late``; by default it skips late and retracted assertions.

Visibility has two audiences, each bound to a database reader role:

* ``OWNER`` (``perception_owner_reader``): owner-authenticated detail.
* ``AVAILABILITY`` (``perception_availability_reader``): exposed and
  third-party goals. They see owner availability only, ``busy_until`` or
  unknown, never place, routine, title or coordinates, and have no timeline.

The SQL already enforces this by role. This layer adds three checks: the
connection must be in the audience's role, the availability projection is a
fixed allowlist of three fields whatever the database returns, and a
non-owner timeline query is refused before any database call. Any malformed
read fails closed.

Dark: nothing calls this until ``flags.SITUATION_READS_LIVE`` is turned on at
the switch. No grant is added here; at activation the runtime that serves a goal
needs membership of the reader role for that goal's audience.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from butlers.perception import flags
from butlers.perception.model import STATES
from butlers.perception.store import PerceptionStore


class Audience(StrEnum):
    OWNER = "owner"
    AVAILABILITY = "availability"


#: The database role a connection must hold for each audience.
READER_ROLE: dict[Audience, str] = {
    Audience.OWNER: "perception_owner_reader",
    Audience.AVAILABILITY: "perception_availability_reader",
}


@dataclass(frozen=True, slots=True)
class AvailabilityRead:
    """All an exposed or third-party goal may learn about the owner."""

    state: str
    busy_until: datetime | None
    availability_reason: str


AVAILABILITY_FIELDS = tuple(f.name for f in fields(AvailabilityRead))


@dataclass(frozen=True, slots=True)
class SituationRead:
    """The owner's typed four-state read."""

    kind: str
    state: str
    value: Any
    stale_last_value: Any
    source: str | None
    observed_at: datetime | None
    fresh_until: datetime | None
    age_seconds: float | None
    labels: dict[str, Any]
    availability_reason: str
    generation: int | None


@dataclass(frozen=True, slots=True)
class TimelineEntry:
    """One immutable timeline record, with its derived retraction and lateness."""

    id: UUID
    kind: str
    subject: UUID | None
    record_class: str
    operation: str
    at: datetime
    confirmed_at: datetime
    late: bool
    retracted: bool
    retraction_event_id: UUID | None
    retracts_event_id: UUID | None
    from_state: str | None
    to_state: str | None
    from_value: Any
    to_value: Any
    typed_value: Any
    labels: dict[str, Any]
    source: str


def situation_capabilities() -> tuple[str, ...]:
    """The capabilities on offer: none while dark."""
    if not flags.SITUATION_READS_LIVE:
        return ()
    return ("situation.read", "timeline.query")


async def _require_role(connection: Any, audience: Audience) -> None:
    role = await connection.fetchval("SELECT current_user")
    if role != READER_ROLE[audience]:
        raise PermissionError(f"a {audience.value} read requires the {READER_ROLE[audience]} role")


def _instant(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        result = datetime.fromisoformat(value)
    else:
        raise ValueError("instant required")
    if result.tzinfo is None:
        raise ValueError("aware instant required")
    return result


def _uuid(value: Any) -> UUID | None:
    return None if value is None else UUID(str(value))


def _availability(raw: Any) -> AvailabilityRead:
    """Only ``busy_until`` is ever taken from the database; nothing else is copied."""
    busy = raw.get("busy_until") if isinstance(raw, dict) else None
    try:
        busy_until = _instant(busy)
    except (TypeError, ValueError):
        busy_until = None
    return AvailabilityRead(
        state="unknown", busy_until=busy_until, availability_reason="detail_restricted"
    )


def _owner(kind: str, raw: Any) -> SituationRead:
    if not isinstance(raw, dict):
        raise ValueError("perception read returned no object")
    state = raw.get("state")
    if state not in STATES:
        raise ValueError("unknown perception state")
    value = raw.get("value")
    if (state == "known") != (value is not None):
        raise ValueError("a value is carried exactly when the state is known")
    labels = raw.get("labels")
    if not isinstance(labels, dict):
        raise ValueError("labels are required")
    reason = raw.get("availability_reason")
    if not isinstance(reason, str):
        raise ValueError("availability reason is required")
    age = raw.get("age_seconds")
    generation = raw.get("generation")
    return SituationRead(
        kind=raw.get("kind") or kind,
        state=state,
        value=value,
        stale_last_value=raw.get("stale_last_value"),
        source=raw.get("source"),
        observed_at=_instant(raw.get("observed_at")),
        fresh_until=_instant(raw.get("fresh_until")),
        age_seconds=None if age is None else float(age),
        labels=labels,
        availability_reason=reason,
        generation=None if generation is None else int(generation),
    )


async def situation_read(
    connection: Any,
    *,
    audience: Audience,
    kind: str,
    subject: UUID | None,
    as_of: datetime,
) -> SituationRead | AvailabilityRead:
    """Read one situation kind for ``audience`` on a connection in that audience's role."""
    await _require_role(connection, audience)
    raw = await PerceptionStore(connection).read(kind, subject, as_of=as_of)
    if audience is Audience.AVAILABILITY:
        return _availability(raw)
    return _owner(kind, raw)


def _entry(row: dict[str, Any]) -> TimelineEntry:
    at, confirmed_at = _instant(row["at"]), _instant(row["confirmed_at"])
    if at is None or confirmed_at is None:
        raise ValueError("timeline instants are required")
    labels = row.get("labels")
    if not isinstance(labels, dict):
        raise ValueError("timeline labels are required")
    return TimelineEntry(
        id=UUID(str(row["id"])),
        kind=row["kind"],
        subject=_uuid(row.get("subject_entity")),
        record_class=row["class"],
        operation=row["operation"],
        at=at,
        confirmed_at=confirmed_at,
        late=bool(row["late"]),
        retracted=bool(row["retracted"]),
        retraction_event_id=_uuid(row.get("retraction_event_id")),
        retracts_event_id=_uuid(row.get("retracts_event_id")),
        from_state=row.get("from_state"),
        to_state=row.get("to_state"),
        from_value=row.get("from_value"),
        to_value=row.get("to_value"),
        typed_value=row.get("typed_value"),
        labels=labels,
        source=row["source"],
    )


async def timeline_query(
    connection: Any,
    *,
    audience: Audience,
    since: datetime,
    until: datetime,
    kind: str | None = None,
    subject: UUID | None = None,
    before: UUID | None = None,
    limit: int = 20,
    live: bool = True,
) -> list[TimelineEntry]:
    """The owner's timeline page. ``live`` skips late and retracted assertions."""
    if audience is not Audience.OWNER:
        raise PermissionError("the timeline is owner-only")
    await _require_role(connection, audience)
    rows = await PerceptionStore(connection).timeline(
        since=since, until=until, kind=kind, subject=subject, before=before, limit=limit, live=live
    )
    return [_entry(row) for row in rows or []]


__all__ = [
    "AVAILABILITY_FIELDS",
    "READER_ROLE",
    "Audience",
    "AvailabilityRead",
    "SituationRead",
    "TimelineEntry",
    "situation_capabilities",
    "situation_read",
    "timeline_query",
]
