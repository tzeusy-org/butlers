"""Adapt the conditions service's ``DependencyReporter`` to closed observations.

The service reports ``(dependency, state, reason)`` with the reason a free-text
exception class name. That text is read only to choose a closed cause and is never
stored or forwarded. A report is a claim about a moment: the collector keeps the
latest one per dependency for a bounded time, and silence afterwards is "no claim",
never "healthy".
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from butlers.capability_health.model import Observation
from butlers.core.capability_dependencies import DEPENDENCY_IDS

_PREFIX = "system.dep."
_NETWORK = frozenset(
    {
        "TimeoutError",
        "ConnectionError",
        "OSError",
        "ConnectionRefusedError",
        "ConnectionResetError",
        "ConnectionAbortedError",
        "BrokenPipeError",
    }
)
_ONLY_CAUSE = {"available": None, "held": "policy_held", "unconfigured": "not_configured"}


def observation_from_report(
    dependency: str, state: str, reason: str, now: datetime
) -> Observation | None:
    """A closed observation for a declared dependency, or ``None`` if it names none."""
    name = dependency.removeprefix(_PREFIX) if isinstance(dependency, str) else ""
    if name not in DEPENDENCY_IDS:
        return None
    if state in _ONLY_CAUSE:
        return Observation(name, state, _ONLY_CAUSE[state], now)
    if state in ("down", "degraded", "blind"):
        cause = "domain_unreachable" if reason in _NETWORK and state != "blind" else "unknown"
        return Observation(name, state, cause, now)
    return Observation(name, None, None, now)


class ReportCollector:
    """A ``DependencyReporter`` that records the latest closed report per dependency."""

    def __init__(
        self,
        *,
        max_age: timedelta = timedelta(minutes=5),
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.max_age = max_age
        self.clock = clock
        self._latest: dict[str, Observation] = {}

    async def report(self, dependency: str, state: str, reason: str) -> None:
        try:
            observation = observation_from_report(dependency, state, reason, self.clock())
        except Exception:
            return  # reporting must never fail the path it observes
        if observation is not None:
            self._latest[observation.dependency] = observation

    def observations(self, dependency: str, now: datetime) -> list[Observation]:
        latest = self._latest.get(dependency)
        if latest is None or now.astimezone(UTC) - latest.observed_at > self.max_age:
            return []
        return [latest]
