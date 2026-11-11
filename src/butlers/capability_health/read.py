"""Read one dependency's published health, failing safe to ``unknown``.

An unreadable, missing, stale, unmeasurable or malformed projection is ``unknown``:
``state=None`` and ``availability=unknown`` (RFC 0038 S6). That is not a healthy
state and not an exception string. Callers keep their normal authorization and
underlying call checks; health is never a grant.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

from butlers.capability_health.model import MAX_SOURCE_AGE
from butlers.core.capability_dependencies import DEPENDENCY_IDS
from butlers.perception.model import validate_value


class Availability(StrEnum):
    AVAILABLE = "available"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


_AVAILABILITY = {
    "available": Availability.AVAILABLE,
    "degraded": Availability.DEGRADED,
    "blind": Availability.DEGRADED,
    "down": Availability.UNAVAILABLE,
    "held": Availability.UNAVAILABLE,
    "unconfigured": Availability.UNAVAILABLE,
}


class HealthReader(Protocol):
    async def read(self, kind: str, subject: Any, *, as_of: datetime) -> Any: ...


@dataclass(frozen=True)
class Health:
    dependency: str
    state: str | None
    cause: str | None
    observed_at: datetime | None
    generation: int | None
    source_age_seconds: int | None
    availability: Availability


def unknown(dependency: str) -> Health:
    return Health(dependency, None, None, None, None, None, Availability.UNKNOWN)


async def read_health(store: HealthReader, dependency: str, now: datetime) -> Health:
    """Health of a declared dependency as of ``now``; never raises for storage faults."""
    if dependency not in DEPENDENCY_IDS:
        raise ValueError(f"undeclared dependency: {dependency!r}")
    try:
        row = await store.read(f"system.dep.{dependency}", None, as_of=now)
        if not isinstance(row, dict) or row.get("state") != "known":
            return unknown(dependency)
        value = row.get("value")
        validate_value("system.dep", value)
        assert isinstance(value, dict)
        generation = row["generation"]
        observed_at = datetime.fromisoformat(value["observed_at"].replace("Z", "+00:00"))
        if type(generation) is not int:
            return unknown(dependency)
        age = max(0, int((now.astimezone(UTC) - observed_at).total_seconds()))
        if age > MAX_SOURCE_AGE.total_seconds():
            # Belt and braces: a measurement past the bound is never current health.
            return unknown(dependency)
        return Health(
            dependency,
            value["state"],
            value["cause"],
            observed_at,
            generation,
            age,
            _AVAILABILITY[value["state"]],
        )
    except Exception:
        # Content-blind by design: no exception text reaches a session or a log line.
        return unknown(dependency)
