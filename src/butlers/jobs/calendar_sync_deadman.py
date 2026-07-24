"""Calendar sync deadman (bu-hmdqz.10, 2026-07-12 JARVIS pursuit move 10).

Real incident (calendar auditor, live-confirmed): the provider sync poller in
every calendar-enabled butler died silently in April -- all 10
``source_freshness`` rows sat stale (``last_synced_at`` 2026-03-30..04-07 or
null, ~103 days), ``last_error`` was empty (the loop did not even record a
failure), and nothing outside the Sources tab's small degraded dots ever said
so. The week grid kept rendering a 96-day-old snapshot at full authority. This
module is the active check: an independent watcher, external to each butler's
own sync-poller task, that notices when NO source's ``calendar_sync_cursors``
stamp has landed within 2x the poll interval and escalates to QA once.

Why external to the sync poller
--------------------------------
A check embedded inside ``CalendarModule._run_sync_poller``'s own loop cannot
catch this failure mode by construction: if that loop's task dies (an
unhandled exception breaking out of its ``while True``, or an orphaned/
cancelled task), any staleness check living inside the same loop dies with it.
This mirrors ``butlers.jobs.external_deadman``'s core argument for its own
external ping -- a check must live somewhere that survives the failure it
exists to catch.

Why the dashboard-api process, not a butler daemon
----------------------------------------------------
``calendar_sync_cursors`` is per-butler-schema (every calendar-enabled butler
has its own sources + cursors, fanned out and merged for the FE's grid-level
plaque). The dashboard-api's ``DatabaseManager`` already reads across every
calendar-enabled schema for the sibling ``GET /api/calendar/workspace`` read
(:func:`butlers.api.read_models.calendar_workspace_v1.query_calendar_sources`,
reused here unchanged) -- a single butler's own pool cannot. This also mirrors
``butlers.jobs.deploy_drift``'s identical rationale for running here rather
than in a butler's scheduled-task loop.

Threshold and interval
-----------------------
The 2x-poll-interval threshold matches
``butlers.modules.calendar.PROJECTION_STALENESS_MULTIPLIER`` (already used
internally by the module for its own ``projection_freshness`` staleness
signal). The poll interval itself is
``butlers.modules.calendar.DEFAULT_SYNC_INTERVAL_MINUTES`` -- as of this
writing no roster ``butler.toml`` overrides ``[modules.calendar.sync]``, so
every calendar-enabled butler runs the code default. If a future butler ever
configures a custom interval, this constant needs to become a per-butler
lookup (parsing each ``roster/*/butler.toml``, mirroring
``butlers.api.deps.discover_butlers``) instead of the flat constant used here.

Escalation: durable condition lifecycle, not a one-shot marker
-----------------------------------------------------------------
bu-27dxl.6.3 migrated this module off its original one-shot
``public.audit_log`` "first detected"/"escalated" debounce -- keyed by a
hash of the whole *composition* of currently-stale sources, so any change to
that set (one source resolving, a new one going stale) reset the escalation
clock for every source still stale -- onto
``butlers.core.infra_conditions.reconcile_snapshot`` (bu-27dxl.6.2), the
shared durable condition ledger and lifecycle service. Every stale provider
source is now its own ``calendar_sync_deadman`` condition episode, identified
by ``(db_butler, source_key)`` alone (never the mutable ``last_synced_at``
evidence -- see :func:`_condition_fingerprint`), so one provider's outage
neither masks nor resets another's independently progressing escalation.

A complete successful check (every enabled provider source actually
observed) resolves any active episode absent from the current stale set; a
degraded check (the whole fan-out failed) or a partial one (some butler
schemas' fan-out failed while others succeeded --
:attr:`CalendarSyncDeadmanReport.failed_butlers`) only confirms evidence for
what it did observe and never resolves anything by omission (AC2). Escalation
to a QA case (``public.healing_attempts``, terminal ``unfixable`` with a
human-action ``error_detail`` -- the same "QA discovery" shape
``deploy_drift`` uses) fires once per episode at its first (L1) due
transition, after :data:`CALENDAR_DEADMAN_ESCALATION_DELAY_S` (one more check
tick) of persistence; L2+ due transitions record a distinct re-escalation
audit marker without creating another healing attempt (AC4). A single blip
that clears before the next tick never reaches QA at all.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg

from butlers.api.db import DatabaseManager
from butlers.api.read_models.calendar_workspace_v1 import (
    CalendarSourceRow,
    query_calendar_sources,
)
from butlers.api.routers import audit as audit_router
from butlers.core.healing.tracking import create_or_join_attempt, update_attempt_status
from butlers.core.infra_conditions import (
    ConditionTransition,
    Observation,
    compute_fingerprint,
    reconcile_snapshot,
)
from butlers.modules.calendar import (
    DEFAULT_SYNC_INTERVAL_MINUTES,
    PROJECTION_STALENESS_MULTIPLIER,
    SOURCE_KIND_PROVIDER,
)

logger = logging.getLogger(__name__)

# Cadence for the background loop below. 30 minutes: well under any
# staleness this check could plausibly need to react to quickly (a poller
# dying is a silent, long-lived failure, not a transient blip), while staying
# cheap (one cross-schema fan-out query per tick).
DEFAULT_CALENDAR_DEADMAN_CHECK_INTERVAL_S = 1800

# A condition must persist across at least one more check tick before
# escalating to QA (the producer-owned L1 grace the shared lifecycle service
# requires -- it has no global default; see infra_conditions.py) -- absorbs a
# single-tick fan-out hiccup or a source mid-way through its very first sync
# without inventing a second, harder-to-reason-about time constant.
CALENDAR_DEADMAN_ESCALATION_DELAY_S = DEFAULT_CALENDAR_DEADMAN_CHECK_INTERVAL_S

_FIRST_DETECTED_ACTION = "calendar_sync_deadman_first_detected"
_ESCALATED_ACTION = "calendar_sync_deadman_escalated"
_REESCALATED_ACTION = "calendar_sync_deadman_reescalated"
_DEADMAN_ACTOR = "calendar_sync_deadman"

# Canonical infra_conditions ledger source for this producer. Distinct from
# _DEADMAN_ACTOR (which happens to share the same string) in purpose: this is
# the (source, fingerprint) identity namespace key, _DEADMAN_ACTOR is only
# the public.audit_log actor for this module's own writes.
_CALENDAR_CONDITION_SOURCE = "calendar_sync_deadman"
_CALENDAR_IDENTITY_VERSION = 1


# ---------------------------------------------------------------------------
# Staleness computation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StaleCalendarSource:
    """One provider calendar source with no fresh sync cursor stamp."""

    source_key: str
    db_butler: str
    butler_name: str | None
    last_synced_at: datetime | None


@dataclass(frozen=True)
class CalendarSyncDeadmanReport:
    """Result of one deadman check pass."""

    checked_at: datetime
    stale_sources: tuple[StaleCalendarSource, ...]
    #: Non-None when the check itself failed (fan-out unavailable, etc.) -- a
    #: degraded check, never silently reported as clean.
    check_error: str | None = None
    #: Butler schemas whose fan-out query failed this tick (a PARTIAL
    #: failure -- distinct from ``check_error``, a TOTAL failure). Sources
    #: belonging to these schemas were never observed, so they must not
    #: factor into resolution-by-omission either -- see ``snapshot_complete``.
    failed_butlers: tuple[str, ...] = ()

    @property
    def is_stale(self) -> bool:
        return bool(self.stale_sources)

    @property
    def is_available(self) -> bool:
        return self.check_error is None

    @property
    def snapshot_complete(self) -> bool:
        """True only when every enabled provider source was actually observed this tick.

        Both a total check failure (``check_error``) and a partial fan-out
        failure (``failed_butlers`` non-empty) mean some provider sources
        were never observed -- reconciliation must not resolve any condition
        by omission when this is False (AC2), even though the sources that
        WERE observed still get their evidence confirmed.
        """
        return self.check_error is None and not self.failed_butlers


def _is_sync_disabled(source: CalendarSourceRow) -> bool:
    """An operator-disabled source (``sync_enabled: false``) is not a failure."""
    metadata = source.source_metadata
    return isinstance(metadata, dict) and metadata.get("sync_enabled") is False


async def compute_calendar_sync_report(db: DatabaseManager) -> CalendarSyncDeadmanReport:
    """Check every enabled provider source's sync-cursor staleness.

    Never raises: a fan-out failure is captured into
    ``CalendarSyncDeadmanReport.check_error`` instead of crashing the caller or
    producing a false all-clear. Only ``source_kind == SOURCE_KIND_PROVIDER``
    sources are considered -- internal sources (scheduled tasks, reminders)
    are locally projected, not provider-synced, so a null cursor there is
    normal, not a dead poller.
    """
    checked_at = datetime.now(UTC)
    threshold = timedelta(minutes=DEFAULT_SYNC_INTERVAL_MINUTES * PROJECTION_STALENESS_MULTIPLIER)

    try:
        # bu-27dxl.6.3: the degraded-butler list IS now consumed (below, as
        # CalendarSyncDeadmanReport.failed_butlers) -- lifecycle reconciliation
        # needs it to know a butler's sources went unobserved this tick, not
        # just whether the fan-out call raised outright.
        sources, failed_butlers = await query_calendar_sources(db)
    except Exception as exc:
        logger.warning("calendar sync deadman: source query failed", exc_info=True)
        return CalendarSyncDeadmanReport(
            checked_at=checked_at, stale_sources=(), check_error=str(exc)
        )

    stale: list[StaleCalendarSource] = []
    for source in sources:
        if source.source_kind != SOURCE_KIND_PROVIDER:
            continue
        if _is_sync_disabled(source):
            continue
        last_synced_at = source.last_synced_at
        if last_synced_at is not None and (checked_at - last_synced_at) <= threshold:
            continue
        stale.append(
            StaleCalendarSource(
                source_key=source.source_key,
                db_butler=source.db_butler,
                butler_name=source.butler_name,
                last_synced_at=last_synced_at,
            )
        )

    return CalendarSyncDeadmanReport(
        checked_at=checked_at, stale_sources=tuple(stale), failed_butlers=tuple(failed_butlers)
    )


def _condition_fingerprint(stale: StaleCalendarSource) -> str:
    """Stable per-provider-source condition fingerprint (Decision #1).

    Identity is ``(db_butler, source_key)`` alone -- deliberately excluding
    the mutable ``last_synced_at`` evidence -- so one provider's outage
    neither masks nor resets another's independently progressing escalation
    clock, and a provider's own episode survives its diagnostic text
    changing tick to tick.
    """
    return compute_fingerprint(
        _CALENDAR_CONDITION_SOURCE,
        _CALENDAR_IDENTITY_VERSION,
        {"db_butler": stale.db_butler, "source_key": stale.source_key},
    )


def _summarize(stale_sources: tuple[StaleCalendarSource, ...]) -> str:
    parts = [
        f"{s.db_butler}/{s.source_key}: last synced "
        f"{s.last_synced_at.isoformat() if s.last_synced_at else 'never'}"
        for s in stale_sources
    ]
    return "; ".join(parts)


# ---------------------------------------------------------------------------
# Condition-lifecycle reconciliation (bu-27dxl.6.3)
# ---------------------------------------------------------------------------


async def _escalate_calendar_l1(
    pool: asyncpg.Pool, transition: ConditionTransition, summary: str
) -> dict[str, Any]:
    """Open the terminal human-action QA case for one episode's first (L1) due transition."""
    try:
        attempt_id, _is_new = await create_or_join_attempt(
            pool,
            fingerprint=transition.fingerprint,
            butler_name="switchboard",
            severity=1,  # "high" -- see core.qa.severity.map_severity
            exception_type="CalendarSyncDeadman",
            call_site="butlers.jobs.calendar_sync_deadman:calendar_sync_deadman",
            session_id=uuid.uuid4(),
            sanitized_msg=summary,
        )
        await update_attempt_status(
            pool,
            attempt_id,
            "unfixable",
            error_detail=(
                "Escalated: human action required — calendar provider sync has not "
                f"stamped a cursor within {PROJECTION_STALENESS_MULTIPLIER}x the poll "
                f"interval, persisting past its initial grace period. {summary}"
            ),
        )
        await audit_router.append(
            pool,
            _DEADMAN_ACTOR,
            _ESCALATED_ACTION,
            target=transition.fingerprint,
            note=str(attempt_id),
            result="escalated",
        )
    except Exception:
        logger.exception("calendar sync deadman: QA escalation failed")
        return {
            "fingerprint": transition.fingerprint,
            "transition": transition.transition,
            "escalated": False,
            "reason": "escalation_failed",
        }
    return {
        "fingerprint": transition.fingerprint,
        "transition": transition.transition,
        "escalated": True,
        "healing_attempt_id": str(attempt_id),
    }


async def _apply_calendar_transition(
    pool: asyncpg.Pool, transition: ConditionTransition, stale: StaleCalendarSource | None
) -> dict[str, Any]:
    """Apply one episode's producer-owned audit side effect for a lifecycle transition.

    ``opened``/``reopened`` record first-detection evidence (preserving the
    direct-audit-result attribution bu-27dxl.3.2 / PR #3516 established). The
    episode's first due transition (``L1``) opens the terminal human-action QA
    case exactly once; every later due transition (``L2``, ``L3``, and the
    seven-day ``L3`` repeat) records a distinct re-escalation marker WITHOUT
    creating another healing attempt (AC4). Ordinary confirmations and
    resolutions have no side effect of their own here -- the ledger row is
    already the durable evidence for those.
    """
    summary = _summarize((stale,)) if stale is not None else transition.fingerprint

    if transition.transition in ("opened", "reopened"):
        await audit_router.append(
            pool,
            _DEADMAN_ACTOR,
            _FIRST_DETECTED_ACTION,
            target=transition.fingerprint,
            note=summary,
            result="detected",
        )
        return {"fingerprint": transition.fingerprint, "transition": transition.transition}

    if transition.transition == "escalation_due" and transition.escalation_level == "L1":
        return await _escalate_calendar_l1(pool, transition, summary)

    if transition.transition == "escalation_due":
        await audit_router.append(
            pool,
            _DEADMAN_ACTOR,
            _REESCALATED_ACTION,
            target=transition.fingerprint,
            note=f"{transition.escalation_level}: {summary}",
            result="reescalated",
        )
        return {
            "fingerprint": transition.fingerprint,
            "transition": transition.transition,
            "escalation_level": transition.escalation_level,
        }

    return {"fingerprint": transition.fingerprint, "transition": transition.transition}


async def reconcile_calendar_conditions(
    pool: asyncpg.Pool, report: CalendarSyncDeadmanReport
) -> list[dict[str, Any]]:
    """Reconcile this tick's staleness observations against the shared condition lifecycle.

    One :class:`~butlers.core.infra_conditions.Observation` per currently
    stale provider source. ``report.snapshot_complete`` folds in both a total
    check failure and a partial fan-out failure: either way, some enabled
    provider sources were never observed this tick, so resolution-by-omission
    must not fire (AC2) even though the sources that WERE observed still get
    their evidence confirmed.
    """
    by_fingerprint: dict[str, StaleCalendarSource] = {}
    observations: list[Observation] = []
    for stale in report.stale_sources:
        fp = _condition_fingerprint(stale)
        by_fingerprint[fp] = stale
        observations.append(
            Observation(
                fingerprint=fp,
                summary=_summarize((stale,)),
                metadata={
                    "db_butler": stale.db_butler,
                    "source_key": stale.source_key,
                    "last_synced_at": (
                        stale.last_synced_at.isoformat() if stale.last_synced_at else None
                    ),
                },
            )
        )

    transitions = await reconcile_snapshot(
        pool,
        source=_CALENDAR_CONDITION_SOURCE,
        observations=observations,
        snapshot_complete=report.snapshot_complete,
        initial_grace_seconds=CALENDAR_DEADMAN_ESCALATION_DELAY_S,
    )
    return [
        await _apply_calendar_transition(pool, t, by_fingerprint.get(t.fingerprint))
        for t in transitions
    ]


# ---------------------------------------------------------------------------
# Background loop
# ---------------------------------------------------------------------------


async def run_calendar_sync_deadman_check(db: DatabaseManager) -> dict[str, Any]:
    """Run one deadman-check tick: compute staleness, reconcile the condition lifecycle.

    Never raises. Reconciliation runs even when nothing is currently stale --
    a complete clean snapshot is exactly what resolves a previously active
    condition (AC3); returning early here would leave a recovered provider's
    episode open forever.
    """
    report = await compute_calendar_sync_report(db)
    if not report.is_available:
        logger.warning("calendar sync deadman: check degraded: %s", report.check_error)
        return {"available": False, "stale": False}

    try:
        pool = db.pool("switchboard")
    except KeyError:
        logger.warning("calendar sync deadman: switchboard pool unavailable for reconciliation")
        return {
            "available": True,
            "stale": report.is_stale,
            "reconciled": False,
            "reason": "no_pool",
        }

    conditions = await reconcile_calendar_conditions(pool, report)
    return {"available": True, "stale": report.is_stale, "conditions": conditions}


async def run_calendar_sync_deadman_loop(
    db: DatabaseManager,
    *,
    interval_s: float = DEFAULT_CALENDAR_DEADMAN_CHECK_INTERVAL_S,
) -> None:
    """Run :func:`run_calendar_sync_deadman_check` every ``interval_s`` until cancelled.

    Sleeps first, mirroring ``run_migration_drift_loop`` -- avoids a real fan-
    out query at every process boot before the first check actually matters. A
    single tick's failure is logged and swallowed so one bad tick never kills
    the loop.
    """
    if interval_s <= 0:
        raise ValueError(f"interval_s must be a positive number, got {interval_s!r}")
    while True:
        await asyncio.sleep(interval_s)
        try:
            summary = await run_calendar_sync_deadman_check(db)
            logger.info("calendar_sync_deadman_check: %s", summary)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("calendar_sync_deadman_check: tick failed")
