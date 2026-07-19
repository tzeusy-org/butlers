"""Owner-timezone timestamp-bound helpers shared by dashboard read routes.

Dashboard date inputs send a bare ``YYYY-MM-DD`` key. A database timestamptz
comparison needs the entire owner-local calendar day rather than UTC midnight,
while programmatic callers still need full ISO timestamps to pass through
unchanged. Keeping that distinction here prevents individually correct routes
from drifting apart over time.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from butlers.core.general_settings import resolve_general_timezone

logger = logging.getLogger(__name__)

_DAY_KEY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


async def owner_zoneinfo(pool: Any) -> ZoneInfo:
    """Resolve the owner's configured timezone, falling back safely to UTC.

    Any butler pool can read the shared ``public`` general settings. ``pool``
    may be ``None`` for an empty fleet; the resolver already fails open for
    that topology.
    """
    tz_name = await resolve_general_timezone(pool)
    try:
        return ZoneInfo(tz_name)
    except Exception:
        logger.warning("Could not build ZoneInfo for owner timezone %r; using UTC", tz_name)
        return ZoneInfo("UTC")


def resolve_owner_time_bound(value: str, owner_tz: ZoneInfo, *, upper: bool) -> datetime:
    """Resolve a calendar-day key or full ISO timestamp into a query bound.

    A lower bare day becomes owner-local midnight; an upper bare day becomes
    the final owner-local microsecond so an inclusive ``<=`` predicate includes
    all rows on that day. Full ISO timestamps retain their existing semantics,
    including a naive timestamp remaining naive for asyncpg's historical UTC
    encoding behavior.
    """
    if _DAY_KEY_RE.match(value):
        year, month, day = (int(part) for part in value.split("-"))
        try:
            if upper:
                return datetime(year, month, day, 23, 59, 59, 999999, tzinfo=owner_tz)
            return datetime(year, month, day, tzinfo=owner_tz)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid date or timestamp: {value!r}",
            ) from exc
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid date or timestamp: {value!r}",
        ) from exc
