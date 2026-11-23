"""The typed ``capability_unavailable`` result (RFC 0038 S6).

Only a *known* unavailable dependency (down, unconfigured or held) produces it,
immediately and without waiting on a timeout. Degraded, blind and unknown health
never does: they annotate, and unknown leaves every normal check in force.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from butlers.capability_health.read import Availability, Health

CODE = "capability_unavailable"
# Causes that can clear without an owner act: a retry later may succeed.
_RETRYABLE = frozenset({"provider_unavailable", "domain_unreachable", "unknown"})


@dataclass(frozen=True)
class CapabilityUnavailable:
    dependency: str
    state: str
    cause: str
    since: datetime
    source_age_seconds: int
    retryable: bool
    generation: int

    def as_result(self) -> dict[str, Any]:
        """The content-blind wire form: closed words, instants and counts only."""
        return {
            "code": CODE,
            "dependency": self.dependency,
            "state": self.state,
            "cause": self.cause,
            "since": self.since.isoformat().replace("+00:00", "Z"),
            "source_age_seconds": self.source_age_seconds,
            "retryable": self.retryable,
            "generation": self.generation,
        }


def first_unavailable(healths: Iterable[Health]) -> CapabilityUnavailable | None:
    """The first known-unavailable dependency in the given order, else ``None``."""
    for item in healths:
        if item.availability is not Availability.UNAVAILABLE:
            continue
        if (
            item.state is None
            or item.cause is None
            or item.observed_at is None
            or item.source_age_seconds is None
            or item.generation is None
        ):
            continue
        return CapabilityUnavailable(
            dependency=item.dependency,
            state=item.state,
            cause=item.cause,
            since=item.observed_at,
            source_age_seconds=item.source_age_seconds,
            retryable=item.cause in _RETRYABLE,
            generation=item.generation,
        )
    return None
