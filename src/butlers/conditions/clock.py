"""Clock resolution: owner clock, then configured home zone, then UTC. No hidden default zone."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from butlers.conditions.model import ClockReading


def _valid(zone: str | None) -> bool:
    if not zone:
        return False
    try:
        ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


def resolve_clock(
    now: datetime,
    *,
    owner: ClockReading | None = None,
    home_zone: str | None = None,
) -> ClockReading:
    if now.tzinfo is None or now.utcoffset() != UTC.utcoffset(now):
        raise ValueError("resolve_clock requires a UTC instant")
    if owner is not None and _valid(owner.zone):
        return owner
    if home_zone is not None and _valid(home_zone):
        return ClockReading(zone=home_zone, basis="home", source="home_zone", observed_at=now)
    return ClockReading(zone="UTC", basis="utc_fallback", source="utc", observed_at=now)
