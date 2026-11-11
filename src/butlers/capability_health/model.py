"""Observations, the deterministic reducer and the value it publishes.

An :class:`Observation` is one collector's claim about one dependency: a closed
``(state, cause)`` pair and when the signal was measured, or ``state=None`` for
"could not measure". :func:`reduce` folds a dependency's observations into one
:class:`Reduction`. It is pure: the same observations give the same result in any
order, with no clock read and no I/O.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Any

from butlers.core.capability_dependencies import DEPENDENCY_IDS

#: The causes each state may name. ``available`` names none; every other state names
#: exactly one of its own closed causes.
ALLOWED_CAUSES = MappingProxyType(
    {
        "available": frozenset({None}),
        "degraded": frozenset(
            {"provider_unavailable", "domain_unreachable", "signal_unmeasurable", "unknown"}
        ),
        "blind": frozenset({"signal_unmeasurable", "unknown"}),
        "down": frozenset(
            {"oauth_revoked", "provider_unavailable", "domain_unreachable", "unknown"}
        ),
        "held": frozenset({"policy_held"}),
        "unconfigured": frozenset({"not_configured", "credential_missing"}),
    }
)
#: How old a measurement may be and still count. Matches the profile's freshness
#: maximum: past it, a claim is old news and is treated as unmeasured, never as a verdict.
MAX_SOURCE_AGE = timedelta(seconds=900)
# Worst first. The first entry that any observation matches decides the state.
_STATE_ORDER = ("held", "unconfigured", "down", "blind", "degraded", "available")
_CAUSE_ORDER = (
    "policy_held",
    "not_configured",
    "credential_missing",
    "oauth_revoked",
    "provider_unavailable",
    "domain_unreachable",
    "signal_unmeasurable",
    "unknown",
)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("a timezone-aware instant is required")
    return value.astimezone(UTC)


@dataclass(frozen=True)
class Observation:
    """One claim about one dependency; ``state=None`` means it could not be measured."""

    dependency: str
    state: str | None
    cause: str | None
    observed_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "observed_at", _utc(self.observed_at))
        if self.state is None:
            if self.cause is not None:
                raise ValueError("an unmeasured observation names no cause")
        elif self.state not in ALLOWED_CAUSES or self.cause not in ALLOWED_CAUSES[self.state]:
            raise ValueError("state and cause are not an allowed pair")


@dataclass(frozen=True)
class Reduction:
    """The reducer's verdict: a health value, or "no usable measurement"."""

    dependency: str
    state: str | None
    cause: str | None
    observed_at: datetime | None

    @property
    def unmeasurable(self) -> bool:
        return self.state is None

    def value(self) -> dict[str, Any]:
        """The ``system.dep`` payload; only a measurable reduction has one."""
        if self.state is None or self.observed_at is None:
            raise ValueError("an unmeasurable reduction has no value")
        return {
            "state": self.state,
            "cause": self.cause,
            "observed_at": self.observed_at.isoformat().replace("+00:00", "Z"),
        }


def reduce(
    dependency: str,
    observations: Iterable[Observation],
    now: datetime,
    *,
    max_source_age: timedelta = MAX_SOURCE_AGE,
) -> Reduction:
    """Fold a dependency's observations into one verdict.

    Worst state wins; ties inside a state go to the earlier cause in a fixed order.
    An input that could not be measured is never read as healthy: beside measured
    inputs it makes the dependency at least ``blind``, and with nothing measured the
    result is unmeasurable. The source time is the winning observation's, or the
    oldest input's for ``available`` so a stale input cannot hide behind a fresh one.
    Times after ``now`` are clamped to it. An observation older than
    ``max_source_age`` is treated as unmeasured whatever it claimed: old news is
    neither a healthy nor an unhealthy verdict, and can never publish ``available``.
    """
    if dependency not in DEPENDENCY_IDS:
        raise ValueError(f"undeclared dependency: {dependency!r}")
    now = _utc(now)
    items = list(observations)
    if any(item.dependency != dependency for item in items):
        raise ValueError("observation belongs to another dependency")
    # An unmeasured or stale input is an unmeasurable-signal claim of last resort.
    claims = []
    for item in items:
        observed = min(item.observed_at, now)
        if item.state is None or now - item.observed_at > max_source_age:
            claims.append(("blind", "signal_unmeasurable", observed, True))
        else:
            claims.append((item.state, item.cause, observed, False))
    if not claims or all(unmeasured for *_, unmeasured in claims):
        return Reduction(dependency, None, None, None)
    state = min((claim[0] for claim in claims), key=_STATE_ORDER.index)
    winners = [claim for claim in claims if claim[0] == state]
    if state == "available":
        return Reduction(dependency, state, None, min(claim[2] for claim in claims))
    cause, observed_at = min(
        ((claim[1], claim[2]) for claim in winners),
        key=lambda pair: (_CAUSE_ORDER.index(pair[0]), pair[1]),
    )
    return Reduction(dependency, state, cause, observed_at)
