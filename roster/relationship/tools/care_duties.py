"""Owner-declared care duties for dependents and their occurrences (bu-s11n0s.13 S1).

Openspec capability ``relationship-care-duties``; Relationship MANIFESTO "Duty of Care". The owner
declares recurring duties for dependents (a school pickup, a parent's medication, pet care) and
names who is assumed to carry each one. The dependent and the carer are ``public.entities`` rows,
never users, and nothing in this module contacts anyone: it has no notify, no connector and no
MCP call. The dashboard API is its only caller, behind the owner gate.

S1 stores what the owner says. An occurrence's state is only ever ``assumed`` or ``confirmed``
here; the coverage job that would mark one ``at_risk`` or ``uncovered`` is S2 and stays deferred
past the cutover. ``care_occurrences`` carries the full state vocabulary so S2 adds no migration,
and an owner assignment must never be overwritten by a later recompute.

Privacy: a duty's title and an occurrence's reason are owner prose about non-consenting people.
Nothing here logs them.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from dateutil.rrule import rrulestr

DutyKind = Literal["calendar_anchored", "completion_anchored"]
OwnerState = Literal["assumed", "confirmed"]

_LIVE_ENTITY = """
    SELECT 1 FROM public.entities
    WHERE id = $1
      AND (metadata->>'merged_into') IS NULL AND (metadata->>'deleted_at') IS NULL
"""

_DUTY_COLUMNS = (
    "id, subject_entity_id, title, kind, rrule, interval_days, anchor_at, window_minutes, "
    "lead_time_hours, default_carrier_entity_id, retired_at, created_at, updated_at"
)


class CareDutyError(ValueError):
    """A request the owner can correct: the API maps it to 422 (or 404 for ``not_found``)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _utc(value: datetime) -> datetime:
    return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)


def parse_rrule(rrule: str, anchor_at: datetime):
    """Return the parsed rule anchored at *anchor_at*; raise ``CareDutyError`` when invalid."""
    text = rrule.strip()
    if not text.upper().startswith("RRULE:"):
        text = f"RRULE:{text}"
    try:
        return rrulestr(text, dtstart=_utc(anchor_at), ignoretz=False)
    except (ValueError, TypeError, KeyError) as exc:
        raise CareDutyError("invalid_rrule", "rrule is not a valid RFC 5545 rule") from exc


async def _require_live_entity(pool: Any, entity_id: UUID, label: str) -> None:
    if await pool.fetchval(_LIVE_ENTITY, entity_id) is None:
        raise CareDutyError("not_found", f"{label} entity not found")


async def _require_duty(pool: Any, duty_id: UUID, *, live: bool = True) -> dict[str, Any]:
    row = await pool.fetchrow(
        f"SELECT {_DUTY_COLUMNS} FROM relationship.care_duties WHERE id = $1", duty_id
    )
    if row is None:
        raise CareDutyError("not_found", "Care duty not found")
    if live and row["retired_at"] is not None:
        raise CareDutyError("retired", "Care duty is retired")
    return dict(row)


async def create_duty(
    pool: Any,
    *,
    subject_entity_id: UUID,
    title: str,
    kind: DutyKind,
    rrule: str | None,
    interval_days: int | None,
    anchor_at: datetime,
    window_minutes: int = 0,
    lead_time_hours: int = 24,
    default_carrier_entity_id: UUID | None = None,
) -> dict[str, Any]:
    """Declare a duty. The subject and any default carrier must be live entities."""
    title = title.strip()
    if not title:
        raise CareDutyError("invalid_title", "title is required")
    if kind == "calendar_anchored":
        if not rrule or interval_days is not None:
            raise CareDutyError(
                "invalid_shape", "a calendar-anchored duty needs rrule and no interval_days"
            )
        parse_rrule(rrule, anchor_at)
    elif kind == "completion_anchored":
        if interval_days is None or rrule:
            raise CareDutyError(
                "invalid_shape", "a completion-anchored duty needs interval_days and no rrule"
            )
    else:
        raise CareDutyError("invalid_shape", "unknown duty kind")
    await _require_live_entity(pool, subject_entity_id, "Subject")
    if default_carrier_entity_id is not None:
        await _require_live_entity(pool, default_carrier_entity_id, "Carrier")
    row = await pool.fetchrow(
        f"""
        INSERT INTO relationship.care_duties
            (subject_entity_id, title, kind, rrule, interval_days, anchor_at,
             window_minutes, lead_time_hours, default_carrier_entity_id)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        RETURNING {_DUTY_COLUMNS}
        """,
        subject_entity_id,
        title,
        kind,
        rrule.strip() if rrule else None,
        interval_days,
        _utc(anchor_at),
        window_minutes,
        lead_time_hours,
        default_carrier_entity_id,
    )
    return dict(row)


async def set_default_carrier(
    pool: Any, duty_id: UUID, carrier_entity_id: UUID | None
) -> dict[str, Any]:
    """Name (or clear) who is assumed to carry a live duty by default."""
    await _require_duty(pool, duty_id)
    if carrier_entity_id is not None:
        await _require_live_entity(pool, carrier_entity_id, "Carrier")
    row = await pool.fetchrow(
        f"""
        UPDATE relationship.care_duties
        SET default_carrier_entity_id = $2, updated_at = now()
        WHERE id = $1
        RETURNING {_DUTY_COLUMNS}
        """,
        duty_id,
        carrier_entity_id,
    )
    return dict(row)


async def retire_duty(pool: Any, duty_id: UUID) -> dict[str, Any]:
    """Retire a duty without deleting its history. Retiring a retired duty is a no-op."""
    duty = await _require_duty(pool, duty_id, live=False)
    if duty["retired_at"] is not None:
        return duty
    row = await pool.fetchrow(
        f"""
        UPDATE relationship.care_duties
        SET retired_at = now(), updated_at = now()
        WHERE id = $1 AND retired_at IS NULL
        RETURNING {_DUTY_COLUMNS}
        """,
        duty_id,
    )
    return dict(row) if row is not None else await _require_duty(pool, duty_id, live=False)


async def assign_occurrence(
    pool: Any,
    duty_id: UUID,
    occurrence_start: datetime,
    *,
    carrier_entity_id: UUID,
    state: OwnerState = "confirmed",
    reason: str | None = None,
) -> dict[str, Any]:
    """Record the owner's carer for one occurrence; idempotent on (duty, occurrence_start).

    A calendar-anchored duty only takes a start that its rule actually produces; a
    completion-anchored duty takes any start at or after its anchor.
    """
    duty = await _require_duty(pool, duty_id)
    start = _utc(occurrence_start)
    if duty["kind"] == "calendar_anchored":
        rule = parse_rrule(duty["rrule"], duty["anchor_at"])
        if rule.after(start - timedelta(seconds=1), inc=True) != start:
            raise CareDutyError("not_an_occurrence", "start is not an occurrence of this duty")
    elif start < _utc(duty["anchor_at"]):
        raise CareDutyError("not_an_occurrence", "start is before the duty's anchor")
    await _require_live_entity(pool, carrier_entity_id, "Carrier")
    row = await pool.fetchrow(
        """
        INSERT INTO relationship.care_occurrences
            (duty_id, occurrence_start, carrier_entity_id, state, reason)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (duty_id, occurrence_start) DO UPDATE
            SET carrier_entity_id = EXCLUDED.carrier_entity_id,
                state = EXCLUDED.state,
                reason = EXCLUDED.reason,
                updated_at = now()
        RETURNING id, duty_id, occurrence_start, carrier_entity_id, state, reason, updated_at
        """,
        duty_id,
        start,
        carrier_entity_id,
        state,
        reason,
    )
    return dict(row)


async def list_duties(
    pool: Any, *, include_retired: bool = False, limit: int = 200
) -> list[dict[str, Any]]:
    rows = await pool.fetch(
        f"""
        SELECT {_DUTY_COLUMNS} FROM relationship.care_duties
        WHERE $1 OR retired_at IS NULL
        ORDER BY created_at, id
        LIMIT $2
        """,
        include_retired,
        limit,
    )
    return [dict(row) for row in rows]


async def list_occurrences(
    pool: Any, *, start: datetime, end: datetime, limit: int = 200
) -> list[dict[str, Any]]:
    """Recorded occurrences of live duties in ``[start, end)``, soonest first.

    S1 lists only what the owner recorded; it never expands a rule, and it never reports a
    duty as covered or uncovered. Coverage is S2.
    """
    rows = await pool.fetch(
        """
        SELECT o.id, o.duty_id, o.occurrence_start, o.carrier_entity_id, o.state, o.reason,
               d.title, d.subject_entity_id
        FROM relationship.care_occurrences o
        JOIN relationship.care_duties d ON d.id = o.duty_id
        WHERE d.retired_at IS NULL
          AND o.occurrence_start >= $1 AND o.occurrence_start < $2
        ORDER BY o.occurrence_start, o.id
        LIMIT $3
        """,
        _utc(start),
        _utc(end),
        limit,
    )
    return [dict(row) for row in rows]
