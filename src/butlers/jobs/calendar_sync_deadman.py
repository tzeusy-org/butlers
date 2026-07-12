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

Escalation, no storm
---------------------
Follows the exact ``first_detected`` / ``escalated`` ``public.audit_log``
debounce shape ``butlers.jobs.deploy_drift`` established (itself the same
"once per state transition" pattern
``butlers.core.fleet_halt_attention`` / ``butlers.core.model_breaker_attention``
use for their own owner-facing pushes): the first tick that observes staleness
only records a marker; escalation to a QA case
(``public.healing_attempts``, terminal ``unfixable`` with a human-action
``error_detail`` -- the same "QA discovery" shape ``deploy_drift`` uses) only
fires once the SAME stale-source composition has persisted past
:data:`CALENDAR_DEADMAN_ESCALATION_DELAY_S` (one more check tick), and then at
most once per composition (a resolved-then-different set of stale sources gets
a fresh fingerprint and its own one-time escalation). A single blip that
clears before the next tick never reaches QA at all.
"""

from __future__ import annotations

import asyncio
import hashlib
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

# A stale-composition must persist across at least one more check tick before
# escalating to QA -- absorbs a single-tick fan-out hiccup or a source mid-way
# through its very first sync without inventing a second, harder-to-reason-
# about time constant.
CALENDAR_DEADMAN_ESCALATION_DELAY_S = DEFAULT_CALENDAR_DEADMAN_CHECK_INTERVAL_S

_FIRST_DETECTED_ACTION = "calendar_sync_deadman_first_detected"
_ESCALATED_ACTION = "calendar_sync_deadman_escalated"
_DEADMAN_ACTOR = "calendar_sync_deadman"


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

    @property
    def is_stale(self) -> bool:
        return bool(self.stale_sources)

    @property
    def is_available(self) -> bool:
        return self.check_error is None


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
        sources = await query_calendar_sources(db)
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

    return CalendarSyncDeadmanReport(checked_at=checked_at, stale_sources=tuple(stale))


def _fingerprint(stale_sources: tuple[StaleCalendarSource, ...]) -> str:
    """Stable SHA-256 fingerprint for one stale-source *composition*.

    A changing composition (a source resolves, a new one goes stale) is
    treated as a new episode with its own first-detected/escalation clock --
    the same accepted simplification ``butlers.jobs.deploy_drift`` makes for
    migration-chain drift.
    """
    canonical = "|".join(
        f"{s.db_butler}:{s.source_key}"
        for s in sorted(stale_sources, key=lambda s: (s.db_butler, s.source_key))
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _summarize(stale_sources: tuple[StaleCalendarSource, ...]) -> str:
    parts = [
        f"{s.db_butler}/{s.source_key}: last synced "
        f"{s.last_synced_at.isoformat() if s.last_synced_at else 'never'}"
        for s in stale_sources
    ]
    return "; ".join(parts)


def _as_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


# ---------------------------------------------------------------------------
# Escalation
# ---------------------------------------------------------------------------


async def get_deadman_escalation_state(
    pool: asyncpg.Pool, fingerprint: str
) -> tuple[datetime | None, bool]:
    """Return ``(first_detected_at, escalated)`` for one stale-composition fingerprint."""
    first_row = await pool.fetchrow(
        """
        SELECT ts FROM public.audit_log
        WHERE target = $1 AND action = $2
        ORDER BY ts ASC LIMIT 1
        """,
        fingerprint,
        _FIRST_DETECTED_ACTION,
    )
    if first_row is None:
        return None, False

    escalated_row = await pool.fetchrow(
        """
        SELECT 1 FROM public.audit_log
        WHERE target = $1 AND action = $2
        LIMIT 1
        """,
        fingerprint,
        _ESCALATED_ACTION,
    )
    return _as_aware_utc(first_row["ts"]), escalated_row is not None


async def maybe_escalate_calendar_sync_deadman(
    pool: asyncpg.Pool, report: CalendarSyncDeadmanReport
) -> dict[str, Any]:
    """Record first-detection and escalate to QA once staleness persists past one tick.

    Idempotent per stale-source composition: writes the first-detected marker
    at most once per fingerprint, and the escalated marker (plus the
    ``public.healing_attempts`` case) at most once per fingerprint. Never
    raises -- an escalation failure is logged and reported back in the
    returned summary rather than crashing the deadman loop.
    """
    if not report.is_stale:
        return {"escalated": False, "reason": "no_stale_sources"}

    fingerprint = _fingerprint(report.stale_sources)
    first_detected_at, already_escalated = await get_deadman_escalation_state(pool, fingerprint)

    if first_detected_at is None:
        await audit_router.append(
            pool,
            _DEADMAN_ACTOR,
            _FIRST_DETECTED_ACTION,
            target=fingerprint,
            note=_summarize(report.stale_sources),
        )
        return {
            "escalated": False,
            "reason": "newly_detected",
            "first_detected_at": report.checked_at.isoformat(),
        }

    age = report.checked_at - first_detected_at
    if age < timedelta(seconds=CALENDAR_DEADMAN_ESCALATION_DELAY_S):
        return {
            "escalated": False,
            "reason": "within_grace",
            "first_detected_at": first_detected_at.isoformat(),
        }

    if already_escalated:
        return {
            "escalated": False,
            "reason": "already_escalated",
            "first_detected_at": first_detected_at.isoformat(),
        }

    try:
        attempt_id, _is_new = await create_or_join_attempt(
            pool,
            fingerprint=fingerprint,
            butler_name="switchboard",
            severity=1,  # "high" -- see core.qa.severity.map_severity
            exception_type="CalendarSyncDeadman",
            call_site="butlers.jobs.calendar_sync_deadman:calendar_sync_deadman",
            session_id=uuid.uuid4(),
            sanitized_msg=_summarize(report.stale_sources),
        )
        await update_attempt_status(
            pool,
            attempt_id,
            "unfixable",
            error_detail=(
                "Escalated: human action required — calendar provider sync has not "
                f"stamped a cursor within {PROJECTION_STALENESS_MULTIPLIER}x the poll "
                f"interval, persisting past a second check. {_summarize(report.stale_sources)}"
            ),
        )
        await audit_router.append(
            pool, _DEADMAN_ACTOR, _ESCALATED_ACTION, target=fingerprint, note=str(attempt_id)
        )
    except Exception:
        logger.exception("calendar sync deadman: QA escalation failed")
        return {
            "escalated": False,
            "reason": "escalation_failed",
            "first_detected_at": first_detected_at.isoformat(),
        }

    return {
        "escalated": True,
        "healing_attempt_id": str(attempt_id),
        "first_detected_at": first_detected_at.isoformat(),
    }


# ---------------------------------------------------------------------------
# Background loop
# ---------------------------------------------------------------------------


async def run_calendar_sync_deadman_check(db: DatabaseManager) -> dict[str, Any]:
    """Run one deadman-check tick: compute staleness, maybe escalate. Never raises."""
    report = await compute_calendar_sync_report(db)
    if not report.is_available:
        logger.warning("calendar sync deadman: check degraded: %s", report.check_error)
        return {"available": False, "stale": False}
    if not report.is_stale:
        return {"available": True, "stale": False}

    try:
        pool = db.pool("switchboard")
    except KeyError:
        logger.warning("calendar sync deadman: switchboard pool unavailable for escalation check")
        return {"available": True, "stale": True, "escalated": False, "reason": "no_pool"}

    escalation = await maybe_escalate_calendar_sync_deadman(pool, report)
    return {"available": True, "stale": True, **escalation}


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
