"""Owner scheduling-availability preferences (life/meeting hours).

This module backs the owner's scheduling-availability preferences — "when may a
meeting occupy the owner's time?" — which are DISTINCT from the per-butler
notification quiet hours in ``delivery_preferences`` ("when may a butler ping
me?").  See ``openspec/changes/calendar-availability-find-time`` (design D3) for
the modeling decision.

Key differences from ``delivery_preferences``:
  - Owner-scoped: a single row, NOT keyed by ``butler_name``.
  - Stored in ``public.owner_scheduling_preferences`` so every butler that runs
    the calendar module can read the same record.
  - Governs slot ranking (``_build_suggested_slots`` / ``calendar_find_free_slots``),
    never notification delivery.

Provides:
  - ``SchedulingPreferences``      — parsed, immutable constraint object with
                                     ``allows(start, end)`` for slot filtering.
  - ``get_scheduling_preferences`` — fetch the singleton row (or None).
  - ``upsert_scheduling_preferences`` — create/update the singleton row.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

import asyncpg

from butlers.core.temporal.delivery_db import validate_timezone

# iCal-style weekday codes indexed by Python's ``datetime.weekday()`` (Mon=0).
_WEEKDAY_CODES: tuple[str, ...] = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
_VALID_WEEKDAYS = frozenset(_WEEKDAY_CODES)

_TABLE = "public.owner_scheduling_preferences"


def _is_missing_schema(exc: Exception) -> bool:
    """Return True when the optional table is absent from an older schema."""
    return isinstance(exc, (asyncpg.UndefinedTableError, asyncpg.InvalidSchemaNameError))


def _parse_time(value: Any) -> time | None:
    """Parse an ``HH:MM`` / ``HH:MM:SS`` string (or pass through a ``time``)."""
    if value is None:
        return None
    if isinstance(value, time):
        return value
    parts = str(value).split(":")
    if len(parts) < 2:
        raise ValueError(f"Invalid time {value!r}; expected 'HH:MM'")
    hour, minute = int(parts[0]), int(parts[1])
    second = int(parts[2]) if len(parts) > 2 else 0
    return time(hour, minute, second)


def _time_to_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        # Normalise any seconds component away for stable round-trips.
        return _parse_time(value).strftime("%H:%M")
    return value.strftime("%H:%M")


def _as_list(value: Any) -> list[Any]:
    """Coerce a JSONB column (Python list or raw JSON string) into a list."""
    if value is None:
        return []
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, list):
        raise ValueError(f"Expected a JSON array, got {type(value).__name__}")
    return value


def _normalise_weekdays(days: Any) -> list[str]:
    """Validate and normalise weekday codes to upper-case iCal form."""
    result: list[str] = []
    for raw in _as_list(days):
        code = str(raw).strip().upper()
        if code not in _VALID_WEEKDAYS:
            raise ValueError(f"Invalid weekday {raw!r}; expected one of {sorted(_VALID_WEEKDAYS)}")
        if code not in result:
            result.append(code)
    return result


def _normalise_blocks(blocks: Any) -> list[dict[str, str]]:
    """Validate no-meeting blocks into ``[{"start": "HH:MM", "end": "HH:MM"}]``."""
    result: list[dict[str, str]] = []
    for raw in _as_list(blocks):
        if not isinstance(raw, dict) or "start" not in raw or "end" not in raw:
            raise ValueError(
                f"Invalid no_meeting_block {raw!r}; expected {{'start': 'HH:MM', 'end': 'HH:MM'}}"
            )
        start = _parse_time(raw["start"])
        end = _parse_time(raw["end"])
        if start is None or end is None or end <= start:
            raise ValueError(f"no_meeting_block end must be after start: {raw!r}")
        result.append({"start": start.strftime("%H:%M"), "end": end.strftime("%H:%M")})
    return result


@dataclass(frozen=True)
class SchedulingPreferences:
    """Parsed owner scheduling-availability constraints used by slot ranking.

    All fields are optional; an empty instance imposes no constraints (the
    no-row / back-compat case).  Times are interpreted in ``timezone`` (the
    owner/residence timezone) when set, otherwise in the candidate slot's own
    timezone.
    """

    timezone: str | None = None
    earliest_meeting_time: time | None = None
    latest_meeting_time: time | None = None
    meeting_days: frozenset[str] | None = None
    no_meeting_blocks: tuple[tuple[time, time], ...] = field(default_factory=tuple)

    @classmethod
    def from_row(cls, row: dict[str, Any] | None) -> SchedulingPreferences | None:
        """Build from a DB row dict; returns None when there is no row."""
        if not row:
            return None
        days_codes = _normalise_weekdays(row.get("meeting_days"))
        blocks = [
            (_parse_time(b["start"]), _parse_time(b["end"]))
            for b in _normalise_blocks(row.get("no_meeting_blocks"))
        ]
        return cls(
            timezone=row.get("timezone"),
            earliest_meeting_time=_parse_time(row.get("earliest_meeting_time")),
            latest_meeting_time=_parse_time(row.get("latest_meeting_time")),
            meeting_days=frozenset(days_codes) if days_codes else None,
            no_meeting_blocks=tuple(blocks),
        )

    @property
    def has_constraints(self) -> bool:
        """True when at least one constraint would filter candidate slots."""
        return bool(
            self.earliest_meeting_time
            or self.latest_meeting_time
            or self.meeting_days
            or self.no_meeting_blocks
        )

    def _localize(self, dt: datetime) -> datetime:
        if self.timezone is None or dt.tzinfo is None:
            return dt
        return dt.astimezone(ZoneInfo(self.timezone))

    def allows(self, start_at: datetime, end_at: datetime) -> bool:
        """Return True when a candidate slot satisfies every owner constraint.

        A slot is rejected when it starts before ``earliest_meeting_time``, ends
        after ``latest_meeting_time``, falls on a weekday not in
        ``meeting_days``, or overlaps any ``no_meeting_blocks`` interval — all
        evaluated in the owner's timezone.
        """
        start = self._localize(start_at)
        end = self._localize(end_at)

        if self.meeting_days is not None:
            if _WEEKDAY_CODES[start.weekday()] not in self.meeting_days:
                return False

        if self.earliest_meeting_time is not None and start.time() < self.earliest_meeting_time:
            return False

        if self.latest_meeting_time is not None:
            # The slot must finish within the same local day, on/before latest.
            if end.date() != start.date() or end.time() > self.latest_meeting_time:
                return False

        if self.no_meeting_blocks:
            if end.date() != start.date():
                # A slot spanning local midnight cannot be cleanly checked
                # against a daily block; reject conservatively.
                return False
            slot_start, slot_end = start.time(), end.time()
            for block_start, block_end in self.no_meeting_blocks:
                if slot_start < block_end and block_start < slot_end:
                    return False

        return True


async def get_scheduling_preferences(pool: asyncpg.Pool) -> dict[str, Any] | None:
    """Fetch the owner's scheduling-availability preferences.

    Returns a dict of preference fields, or None when no record exists (no row
    ⇒ no scheduling constraints).
    """
    try:
        row = await pool.fetchrow(
            f"""
            SELECT id, earliest_meeting_time, latest_meeting_time, meeting_days,
                   timezone, no_meeting_blocks, created_at, updated_at
            FROM {_TABLE}
            WHERE id = TRUE
            """,
        )
    except asyncpg.PostgresError as exc:
        if _is_missing_schema(exc):
            return None
        raise
    if row is None:
        return None
    return _normalise_row(dict(row))


async def upsert_scheduling_preferences(
    pool: asyncpg.Pool,
    *,
    timezone: str | None = None,
    earliest_meeting_time: str | None = None,
    latest_meeting_time: str | None = None,
    meeting_days: list[str] | None = None,
    no_meeting_blocks: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Create or update the owner's singleton scheduling-availability record.

    Only provided (non-None) fields are written. The record is owner-scoped:
    there is exactly one row (``id = TRUE``), never keyed by ``butler_name``.

    Raises:
        ValueError: If the timezone is invalid, or weekday/block values are malformed.
    """
    if timezone is not None:
        validate_timezone(timezone)

    fields: dict[str, Any] = {}
    if timezone is not None:
        fields["timezone"] = timezone
    if earliest_meeting_time is not None:
        fields["earliest_meeting_time"] = _parse_time(earliest_meeting_time)
    if latest_meeting_time is not None:
        fields["latest_meeting_time"] = _parse_time(latest_meeting_time)
    if meeting_days is not None:
        fields["meeting_days"] = _normalise_weekdays(meeting_days)
    if no_meeting_blocks is not None:
        fields["no_meeting_blocks"] = _normalise_blocks(no_meeting_blocks)

    if (
        fields.get("earliest_meeting_time") is not None
        and fields.get("latest_meeting_time") is not None
        and fields["latest_meeting_time"] <= fields["earliest_meeting_time"]
    ):
        raise ValueError("latest_meeting_time must be after earliest_meeting_time")

    insert_cols = ["id"] + list(fields.keys())
    params: list[Any] = [True] + list(fields.values())
    placeholders = ", ".join(f"${i}" for i in range(1, len(params) + 1))

    update_parts = ["updated_at = now()"]
    for col in fields:
        update_parts.append(f"{col} = EXCLUDED.{col}")
    set_clause = ", ".join(update_parts)

    row = await pool.fetchrow(
        f"""
        INSERT INTO {_TABLE} ({", ".join(insert_cols)})
        VALUES ({placeholders})
        ON CONFLICT (id) DO UPDATE
            SET {set_clause}
        RETURNING id, earliest_meeting_time, latest_meeting_time, meeting_days,
                  timezone, no_meeting_blocks, created_at, updated_at
        """,
        *params,
    )
    return _normalise_row(dict(row))


def _normalise_row(result: dict[str, Any]) -> dict[str, Any]:
    """Normalise a raw DB row into JSON-friendly preference fields."""
    for col in ("earliest_meeting_time", "latest_meeting_time"):
        result[col] = _time_to_str(result.get(col))
    result["meeting_days"] = _normalise_weekdays(result.get("meeting_days"))
    result["no_meeting_blocks"] = _normalise_blocks(result.get("no_meeting_blocks"))
    for col in ("created_at", "updated_at"):
        val = result.get(col)
        if isinstance(val, datetime):
            result[col] = val.isoformat()
    return result
