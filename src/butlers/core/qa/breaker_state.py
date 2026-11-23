"""Derived state of the QA circuit breaker: cause, trip time and half-open probe schedule.

The breaker keeps no state of its own. It is read from the launched-execution rows dispatch
admission and the dashboard already share (``QA_BREAKER_WHERE_SQL``): open when the newest
``threshold`` executions all failed. This module only interprets those rows, as a pure function,
so dispatch, the dashboard and the briefing cannot disagree about it.

A transient outage (for example a provider authentication error) heals without the owner: once
the breaker has been open for :data:`PROBE_BACKOFF_BASE` since the newest failure, one finding is
admitted as a probe. A success breaks the failure streak and closes the breaker; a failure adds
to it, which doubles the wait up to :data:`PROBE_BACKOFF_CAP` (at most about 12 probes a day).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Final

from butlers.core.healing.constants import CIRCUIT_BREAKER_FAILURE_STATUSES
from butlers.core.qa.remediation_liveness import classify_cause

PROBE_BACKOFF_BASE: Final[timedelta] = timedelta(minutes=15)
PROBE_BACKOFF_CAP: Final[timedelta] = timedelta(hours=2)

#: Rows past the threshold that the backoff can still see: 15m doubles to the 2h cap in 3 steps.
EXTRA_ROWS_FOR_BACKOFF: Final[int] = 4


@dataclass(frozen=True)
class BreakerState:
    """What the launched-execution history says about the breaker."""

    open: bool
    streak: int
    dominant_cause: str | None = None
    cause_counts: Mapping[str, int] = field(default_factory=dict)
    opened_at: datetime | None = None
    last_failure_at: datetime | None = None
    next_probe_at: datetime | None = None


def probe_backoff(failures_since_trip: int) -> timedelta:
    """Wait before the next probe: 15 minutes, doubling per failed probe, capped at 2 hours."""
    return min(PROBE_BACKOFF_BASE * (2 ** max(failures_since_trip, 0)), PROBE_BACKOFF_CAP)


def derive_breaker_state(
    rows: Sequence[Mapping[str, Any]],
    threshold: int,
) -> BreakerState:
    """Interpret launched executions, newest first, as breaker state.

    ``rows`` carry ``status``, ``error_detail`` and ``closed_at``, ordered ``closed_at DESC`` and
    fetched with ``threshold + EXTRA_ROWS_FOR_BACKOFF`` rows. ``threshold <= 0`` disables the
    breaker.
    """
    if threshold <= 0:
        return BreakerState(open=False, streak=0)
    streak = 0
    for row in rows:
        if row["status"] not in CIRCUIT_BREAKER_FAILURE_STATUSES:
            break
        streak += 1
    if streak < threshold:
        return BreakerState(open=False, streak=streak)

    causes = [classify_cause(row.get("error_detail")) for row in rows[:threshold]]
    counts = Counter(causes)
    # One cause on every counted failure is a named cause; anything else is "mixed" and the
    # per-cause counts carry the detail.
    dominant = causes[0] if len(counts) == 1 else "mixed"
    newest = rows[0].get("closed_at")
    return BreakerState(
        open=True,
        streak=streak,
        dominant_cause=dominant,
        cause_counts=dict(counts),
        opened_at=rows[streak - threshold].get("closed_at"),
        last_failure_at=newest,
        next_probe_at=None if newest is None else newest + probe_backoff(streak - threshold),
    )


#: Plain-words causes for human-facing text. ``frontend/src/lib/qa-breaker-cause.ts`` carries the
#: same words for the /qa page; change them together.
CAUSE_LABELS: dict[str, str] = {
    "publisher_unconfigured": "no publisher configured",
    "proposal_retention_unavailable": "proposal retention unavailable",
    "execution_failure": "investigation failures",
    "mixed": "mixed causes",
}


def cause_label(cause: str | None) -> str | None:
    """Plain-words cause; an unknown code reads as its own text, never silently dropped."""
    if not cause:
        return None
    return CAUSE_LABELS.get(cause, cause.replace("_", " "))
