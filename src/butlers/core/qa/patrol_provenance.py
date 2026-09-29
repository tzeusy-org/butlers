"""Patrol provenance: which ``public.qa_patrols`` rows prove current QA coverage.

REQ-staffer-qa-008 separates a genuine scheduled discovery cycle from an
operator-created synthetic row and records the enabled-source configuration a
cycle ran under. The QA module writes that evidence; the independent fleet
controller (``butlers.core.fleet_conditions``) and readiness consumers read it
through :data:`LAST_QUALIFYING_PATROL_SQL`. Both sides derive the digest from
this module so a configuration change makes every older row stop qualifying.

A row qualifies only when it is ``origin='scheduled'``, finished
``clean``/``findings_dispatched``/``suppressed``, has ``discovery_complete``
true, and carries the current configuration digest. Legacy rows keep null
provenance and never qualify.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Final, Literal

PatrolOrigin = Literal["scheduled", "operator_synthetic"]

#: Default ``[modules.qa]`` values, shared with ``butlers.modules.qa.QaConfig``
#: so the controller computes the same contract QA runs under.
DEFAULT_PATROL_INTERVAL_MINUTES: Final[int] = 10
DEFAULT_ENABLED_SOURCES: Final[tuple[str, ...]] = (
    "log_scanner",
    "session_records",
    "butler_reports",
    "tool_call_failures",
    "infra_state",
)

#: Statuses that can end a genuine, fully discovered patrol. A ``suppressed``
#: patrol filtered its novel findings by cooldown or severity after complete
#: discovery, so it is healthy coverage when its row proves that discovery.
QUALIFYING_PATROL_STATUSES: Final[tuple[str, ...]] = ("clean", "findings_dispatched", "suppressed")

#: A patrol is overdue after this many configured cadences without a
#: qualifying completion (REQ-butler-control-plane-liveness-005).
OVERDUE_CADENCE_MULTIPLIER: Final[int] = 2

LAST_QUALIFYING_PATROL_SQL: Final[str] = """
    SELECT max(completed_at)
    FROM public.qa_patrols
    WHERE origin = 'scheduled'
      AND discovery_complete IS TRUE
      AND status = ANY($1::text[])
      AND enabled_sources_config_digest = $2
      AND completed_at IS NOT NULL
"""


def enabled_sources_snapshot(sources: Iterable[str]) -> list[str]:
    """Return the canonical, order-independent enabled-source set."""
    return sorted(set(sources))


def enabled_sources_digest(sources: Iterable[str]) -> str:
    """Return the bounded stable digest of one enabled-source configuration."""
    canonical = json.dumps(enabled_sources_snapshot(sources), separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class QaPatrolContract:
    """The configured coverage a qualifying QA patrol must prove."""

    enabled_sources: tuple[str, ...]
    patrol_interval_minutes: int

    @property
    def digest(self) -> str:
        return enabled_sources_digest(self.enabled_sources)

    @property
    def overdue_after_s(self) -> float:
        return float(self.patrol_interval_minutes * 60 * OVERDUE_CADENCE_MULTIPLIER)


def contract_from_module_config(qa_config: Mapping[str, Any]) -> QaPatrolContract | None:
    """Build the contract from a roster ``[modules.qa]`` table.

    Returns ``None`` when QA patrols are disabled, since no patrol is expected.
    Invalid values raise ``ValueError`` rather than guessing a cadence.
    """
    if qa_config.get("enabled", True) is False:
        return None
    interval = qa_config.get("patrol_interval_minutes", DEFAULT_PATROL_INTERVAL_MINUTES)
    sources = qa_config.get("enabled_sources", DEFAULT_ENABLED_SOURCES)
    if type(interval) is not int or interval <= 0:
        raise ValueError("patrol_interval_minutes must be a positive integer")
    if isinstance(sources, str) or not all(isinstance(s, str) for s in sources):
        raise ValueError("enabled_sources must be a list of source names")
    return QaPatrolContract(tuple(enabled_sources_snapshot(sources)), interval)
