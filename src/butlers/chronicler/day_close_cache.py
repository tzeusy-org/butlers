"""Shared identity and request validation for Chronicler day-close cache rows."""

from __future__ import annotations

from datetime import date
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import asyncpg


class MissingDayCloseTimezoneError(ValueError):
    """Raised when a day-close API caller omits its required timezone."""


class InvalidDayCloseTimezoneError(ValueError):
    """Raised when a day-close API caller supplies an unresolvable timezone."""


def resolve_day_close_timezone(timezone: str | None) -> tuple[str, ZoneInfo]:
    """Resolve one required IANA timezone without rewriting its input identity.

    The returned string is the exact accepted request value and is deliberately
    separate from its ``ZoneInfo`` object: aliases must not silently collapse
    into a different cache, lock, or rate-limit identity.
    """
    if timezone is None:
        raise MissingDayCloseTimezoneError("tz is required")
    if not timezone:
        raise InvalidDayCloseTimezoneError("tz must be a non-empty IANA timezone")

    try:
        return timezone, ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError, KeyError) as exc:
        raise InvalidDayCloseTimezoneError(f"Unrecognized IANA timezone: {timezone!r}") from exc


def day_close_cache_key(day_date: date, timezone: str) -> str:
    """Return the cache identity for one exact local date and timezone."""
    return f"day_close:{day_date.isoformat()}:tz:{timezone}"


async def lock_day_close_cache_tuple(
    conn: asyncpg.Connection,
    day_date: date,
    timezone: str,
) -> None:
    """Hold a transaction lock for one exact day-close tuple.

    Advisory locks accept only fixed-width integer values, so a hash of an
    arbitrary exact IANA timezone can never prove collision-free isolation.
    The Chronicler-local lock registry instead has a composite primary key on
    the actual ``(local_date, timezone)`` values. ``ON CONFLICT DO UPDATE``
    locks a pre-existing row for this transaction; ``WHERE FALSE`` preserves
    the row without creating a needless update version. A newly inserted row
    is likewise locked until the surrounding transaction finishes.
    """
    await conn.execute(
        """
        INSERT INTO day_close_cache_locks (local_date, timezone)
        VALUES ($1, $2)
        ON CONFLICT (local_date, timezone) DO UPDATE
            SET timezone = EXCLUDED.timezone
            WHERE FALSE
        """,
        day_date,
        timezone,
    )
