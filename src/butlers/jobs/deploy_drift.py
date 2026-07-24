"""Migration-drift sentinel (bu-9r3hd.1, epic bu-9r3hd "Deploy spine").

Real incident (bd bu-zhfd0): seven merged core-chain revisions (core_155..161)
sat dark in prod because the migrations one-shot exited 0 against a
pre-core_155 image, and nothing could tell. ``public.deployments`` (bu-9r3hd.2,
``butlers.core.deployments``) now records what git SHA + migration head each
boot ran with -- but a ledger alone doesn't catch drift *between* deploys
(schema migrated by hand, a chain silently un-applied, a merged migration that
never got deployed at all). This module is the active check: it compares
three revision sources every hour and turns a mismatch into a red ``/system``
clause plus a QA escalation once the drift has persisted more than 24h.

The three sources
------------------
1. **Codebase head** -- what ``alembic/versions/<chain>/`` on disk says the
   latest revision for a chain should be (:func:`butlers.migrations.get_chain_head`).
2. **Per-schema DB revision** -- what each butler schema's own
   ``alembic_version`` table actually holds, read directly (this module).
3. **Deployed SHA/migration head** -- ``public.deployments`` (out of scope for
   the comparison itself; the ledger's ``migration_head`` is a single
   representative-schema snapshot per ``butlers.core.deployments``'s own
   docstring, not a per-chain proof, so it's surfaced by the sibling
   ``/api/system/deployments`` endpoint rather than folded into this
   comparison).

Where this runs
----------------
As an ``asyncio.Task`` inside the dashboard-api FastAPI process (see
``butlers.api.app.lifespan``), not a butler daemon's scheduled-task loop.
Two reasons: (a) the dashboard-api's ``DatabaseManager`` already reads
cross-schema catalog data from a single pool for the sibling
``/api/system/database`` and ``/api/system/deployments`` endpoints -- a
butler daemon's own pool is schema-scoped and, once per-butler Postgres role
enforcement is active (``BUTLERS_POSTURE=hardened``), would not be able to
read *other* butlers' ``alembic_version`` tables at all; (b) this mirrors the
established precedent in ``butlers.jobs.secrets_lifecycle`` for "a periodic,
deterministic, zero-LLM check that doesn't belong to any single butler."

Escalation: durable condition lifecycle, not a one-shot marker
-----------------------------------------------------------------
bu-27dxl.6.3 migrated this module off its original one-shot
``public.audit_log`` "first detected"/"already escalated" debounce (marker
absence is not durable current-state authority -- a degraded or partial
comparison had no safe way to distinguish "unknown" from "recovered") onto
``butlers.core.infra_conditions.reconcile_snapshot`` (bu-27dxl.6.2), the
shared durable condition ledger and lifecycle service. Every drifted
``(schema, chain)`` pair is now its own ``deployment_drift`` condition
episode, identified by that stable pair alone (never the mutable
``expected_head``/``actual_revision`` values, which stay evidence -- see
:func:`_drift_fingerprint`). A complete successful comparison resolves any
active episode absent from the current drifted set; a degraded/failed
comparison only confirms evidence for what it did see and never resolves by
omission (AC2 of bu-27dxl.6.3 / the ``infrastructure-reliability`` spec's
"Episode lifecycle and complete-snapshot resolution").

L1 (the episode's first due escalation) still opens a QA-visible case via the
existing self-healing case-tracking primitives (:func:`create_or_join_attempt`
/ :func:`update_attempt_status` against ``public.healing_attempts``) -- the
same table and API the self-healing ``report_error`` MCP tool and QA patrol
dispatch use -- created and immediately transitioned to the terminal
``unfixable`` status with an ``error_detail`` carrying a human-action marker
(see ``core.qa.severity.failed_with_human_action`` / ``state_of_case``).
L2 and every subsequent due level (including the seven-day L3 repeat) record
a distinct re-escalation ``public.audit_log`` marker instead, and deliberately
do NOT create another ``healing_attempts`` row for the same episode (AC4).
Nothing polls ``healing_attempts`` for new ``investigating`` rows outside the
explicit ``dispatch_healing``/QA-dispatch call sites, so writing (and
immediately closing) a row here never triggers an unwanted healing-agent
spawn.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import asyncpg

from butlers.api.db import DatabaseManager
from butlers.api.deps import discover_butlers
from butlers.api.routers import audit as audit_router
from butlers.core.healing.tracking import create_or_join_attempt, update_attempt_status
from butlers.core.infra_conditions import (
    ConditionTransition,
    Observation,
    compute_fingerprint,
    get_active_condition,
    reconcile_snapshot,
)
from butlers.migrations import (
    get_all_chains,
    get_chain_head,
    get_chain_revision_ids,
    has_butler_chain,
)

logger = logging.getLogger(__name__)

# Cadence for the background loop below. Hourly, per the epic's acceptance
# criteria ("/system shows a red clause within an hour of ... drift").
DEFAULT_DRIFT_CHECK_INTERVAL_S = 3600

# How long drift may persist before this module's episode reaches its first
# (L1) escalation -- the producer-owned "initial grace" the shared lifecycle
# service requires (it has no global default; see infra_conditions.py).
DRIFT_ESCALATION_THRESHOLD_HOURS = 24

_FIRST_DETECTED_ACTION = "migration_drift_first_detected"
_ESCALATED_ACTION = "migration_drift_escalated"
_REESCALATED_ACTION = "migration_drift_reescalated"
_DRIFT_ACTOR = "migration_drift_sentinel"

# Canonical infra_conditions ledger source for this producer -- the
# deployment-and-drift spec's "QA Escalation After Sustained Drift"
# requirement names this exact string. Distinct from _DRIFT_ACTOR, which is
# only the public.audit_log actor for this module's own writes.
_DRIFT_CONDITION_SOURCE = "deployment_drift"
_DRIFT_IDENTITY_VERSION = 1


# ---------------------------------------------------------------------------
# Drift computation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChainDrift:
    """One migration chain out of sync between the codebase and a schema."""

    schema: str
    chain: str
    expected_head: str
    actual_revision: str | None  # None: this chain has never been applied to this schema


@dataclass(frozen=True)
class DriftReport:
    """Result of one drift-check pass."""

    checked_at: datetime
    drifted: tuple[ChainDrift, ...]
    #: Non-None when the check itself failed (pool unavailable, unreadable
    #: schema, etc.) -- a degraded check, never silently reported as clean.
    check_error: str | None = None

    @property
    def is_drifted(self) -> bool:
        return bool(self.drifted)

    @property
    def is_available(self) -> bool:
        return self.check_error is None


def _expected_chains_by_schema() -> dict[str, list[str]]:
    """Return ``{schema: [chain, ...]}`` for every discovered butler.

    Mirrors the chain-resolution rule ``butlers.cli._migrate_all`` uses at
    boot to decide which migration chains actually apply to a given butler:
    the core chain always, the butler's own chain if it has one, plus enabled
    module chains that carry migrations of their own.
    """
    all_chains = set(get_all_chains())
    result: dict[str, list[str]] = {}
    for butler in discover_butlers():
        schema = butler.db_schema or butler.name
        chains = ["core"]
        if has_butler_chain(butler.name):
            chains.append(butler.name)
        # get_all_chains() only lists module/butler chains that were
        # discovered with actual migration files on disk (see
        # butlers.migrations._discover_module_chains/_discover_butler_chains),
        # so membership alone is sufficient -- no extra filesystem check needed.
        chains += [m for m in sorted(butler.modules) if m != butler.name and m in all_chains]
        result[schema] = chains
    return result


async def _actual_revisions(pool: asyncpg.Pool, schema: str) -> set[str]:
    """Return every ``version_num`` currently applied to *schema*.

    A schema legitimately carries more than one row -- every chain ever
    upgraded there (core, butler-specific, module chains) shares one
    ``alembic_version`` table. An absent table (a schema that predates any
    migration run) is a legitimate empty state, not a check failure.
    """
    try:
        rows = await pool.fetch(f'SELECT version_num FROM "{schema}".alembic_version')
    except asyncpg.UndefinedTableError:
        return set()
    return {row["version_num"] for row in rows}


async def compute_drift_report(db: DatabaseManager) -> DriftReport:
    """Compare codebase migration heads against every schema's applied revisions.

    Never raises: any failure (pool unavailable, unreadable schema, disk-scan
    error) is captured into ``DriftReport.check_error`` instead of crashing
    the caller or producing a false all-clear.
    """
    checked_at = datetime.now(UTC)
    try:
        pool = db.pool("switchboard")
    except KeyError:
        return DriftReport(
            checked_at=checked_at, drifted=(), check_error="switchboard pool unavailable"
        )

    try:
        drifted: list[ChainDrift] = []
        for schema, chains in sorted(_expected_chains_by_schema().items()):
            actual = await _actual_revisions(pool, schema)
            for chain in chains:
                expected_head = get_chain_head(chain)
                applied_for_chain = actual & get_chain_revision_ids(chain)
                if expected_head in applied_for_chain:
                    continue
                actual_revision = sorted(applied_for_chain)[-1] if applied_for_chain else None
                drifted.append(
                    ChainDrift(
                        schema=schema,
                        chain=chain,
                        expected_head=expected_head,
                        actual_revision=actual_revision,
                    )
                )
        return DriftReport(checked_at=checked_at, drifted=tuple(drifted))
    except Exception as exc:
        logger.warning("migration drift sentinel: check failed", exc_info=True)
        return DriftReport(checked_at=checked_at, drifted=(), check_error=str(exc))


def _drift_fingerprint(drift: ChainDrift) -> str:
    """Stable per-``(schema, chain)`` condition fingerprint (Decision #1).

    Deliberately excludes ``expected_head``/``actual_revision`` -- the
    deployment-and-drift spec's "Mutable drift revisions remain evidence"
    scenario requires the SAME episode (and escalation clock) to continue as
    those values change during one ongoing outage, not a fresh episode every
    time a revision changes. They are still carried as the Observation's
    evidence/metadata (see :func:`reconcile_drift_conditions`), just never
    hashed into identity.
    """
    return compute_fingerprint(
        _DRIFT_CONDITION_SOURCE,
        _DRIFT_IDENTITY_VERSION,
        {"schema": drift.schema, "chain": drift.chain},
    )


def _summarize(drifted: tuple[ChainDrift, ...]) -> str:
    parts = [
        f"{d.schema}/{d.chain}: expected {d.expected_head}, DB has {d.actual_revision or 'none'}"
        for d in drifted
    ]
    return "; ".join(parts)


def _as_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


# ---------------------------------------------------------------------------
# Escalation state (read-only lookup shared by the API endpoint and the loop)
# ---------------------------------------------------------------------------


async def get_drift_escalation_state(
    pool: asyncpg.Pool, drifted: tuple[ChainDrift, ...]
) -> tuple[datetime | None, bool]:
    """Aggregate ``(first_detected_at, escalated)`` across every currently
    drifted ``(schema, chain)`` pair's active condition-lifecycle episode.

    Read-only -- safe to call from the ``/api/system/drift`` GET handler as
    well as the escalation loop below. ``first_detected_at`` is the earliest
    active episode's detection time across the current drifted set;
    ``escalated`` is True once ANY of them has moved past ``L0`` -- per the
    deployment-and-drift spec's ``GET /api/system/drift`` contract, this
    "SHALL NOT mean a permanent already-escalated latch".
    """
    first_detected_at: datetime | None = None
    escalated = False
    for drift in drifted:
        condition = await get_active_condition(
            pool, source=_DRIFT_CONDITION_SOURCE, fingerprint=_drift_fingerprint(drift)
        )
        if condition is None:
            continue
        detected = _as_aware_utc(condition["first_detected_at"])
        if first_detected_at is None or detected < first_detected_at:
            first_detected_at = detected
        if condition["escalation_level"] != "L0":
            escalated = True
    return first_detected_at, escalated


# ---------------------------------------------------------------------------
# Condition-lifecycle reconciliation (bu-27dxl.6.3)
# ---------------------------------------------------------------------------


async def _escalate_drift_l1(
    pool: asyncpg.Pool, transition: ConditionTransition, summary: str
) -> dict[str, Any]:
    """Open the terminal human-action QA case for one episode's first (L1) due transition."""
    try:
        attempt_id, _is_new = await create_or_join_attempt(
            pool,
            fingerprint=transition.fingerprint,
            butler_name="switchboard",
            severity=1,  # "high" -- see core.qa.severity.map_severity
            exception_type="MigrationDriftDetected",
            call_site="butlers.jobs.deploy_drift:migration_drift_sentinel",
            session_id=uuid.uuid4(),
            sanitized_msg=summary,
        )
        await update_attempt_status(
            pool,
            attempt_id,
            "unfixable",
            error_detail=(
                "Escalated: human action required — migration drift persisted past its "
                f"initial {DRIFT_ESCALATION_THRESHOLD_HOURS}h grace period. {summary}"
            ),
        )
        await audit_router.append(
            pool,
            _DRIFT_ACTOR,
            _ESCALATED_ACTION,
            target=transition.fingerprint,
            note=str(attempt_id),
            result="escalated",
        )
    except Exception:
        logger.exception("migration drift sentinel: QA escalation failed")
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


async def _apply_drift_transition(
    pool: asyncpg.Pool, transition: ConditionTransition, drift: ChainDrift | None
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
    summary = _summarize((drift,)) if drift is not None else transition.fingerprint

    if transition.transition in ("opened", "reopened"):
        await audit_router.append(
            pool,
            _DRIFT_ACTOR,
            _FIRST_DETECTED_ACTION,
            target=transition.fingerprint,
            note=summary,
            result="detected",
        )
        return {"fingerprint": transition.fingerprint, "transition": transition.transition}

    if transition.transition == "escalation_due" and transition.escalation_level == "L1":
        return await _escalate_drift_l1(pool, transition, summary)

    if transition.transition == "escalation_due":
        await audit_router.append(
            pool,
            _DRIFT_ACTOR,
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


async def reconcile_drift_conditions(
    pool: asyncpg.Pool, report: DriftReport
) -> list[dict[str, Any]]:
    """Reconcile this tick's drift report against the shared condition lifecycle.

    One :class:`~butlers.core.infra_conditions.Observation` per drifted
    ``(schema, chain)`` pair. A complete successful comparison
    (``report.is_available``) resolves any active drift condition absent from
    this tick's drifted set (AC1/AC3, per ``reconcile_snapshot``'s
    ``snapshot_complete``); a degraded/failed comparison only confirms
    evidence for what it did observe and never resolves by omission (AC2).
    ``compute_drift_report`` is already all-or-nothing (any schema-read
    failure aborts the whole comparison and sets ``check_error``), so
    ``snapshot_complete`` maps directly onto ``report.is_available`` -- there
    is no partial-fan-out case to fold in here the way calendar has.
    """
    by_fingerprint: dict[str, ChainDrift] = {}
    observations: list[Observation] = []
    for drift in report.drifted:
        fp = _drift_fingerprint(drift)
        by_fingerprint[fp] = drift
        observations.append(
            Observation(
                fingerprint=fp,
                summary=_summarize((drift,)),
                metadata={
                    "schema": drift.schema,
                    "chain": drift.chain,
                    "expected_head": drift.expected_head,
                    "actual_revision": drift.actual_revision,
                },
            )
        )

    transitions = await reconcile_snapshot(
        pool,
        source=_DRIFT_CONDITION_SOURCE,
        observations=observations,
        snapshot_complete=report.is_available,
        initial_grace_seconds=DRIFT_ESCALATION_THRESHOLD_HOURS * 3600,
    )
    return [
        await _apply_drift_transition(pool, t, by_fingerprint.get(t.fingerprint))
        for t in transitions
    ]


# ---------------------------------------------------------------------------
# Background loop
# ---------------------------------------------------------------------------


async def run_migration_drift_check(db: DatabaseManager) -> dict[str, Any]:
    """Run one drift-check tick: compare, then reconcile the condition lifecycle. Never raises.

    Reconciliation runs even when nothing is currently drifted -- a complete
    clean comparison is exactly what resolves a previously active drift
    condition (AC3); returning early here would leave a recovered chain's
    episode open forever.
    """
    report = await compute_drift_report(db)
    if not report.is_available:
        logger.warning("migration drift sentinel: check degraded: %s", report.check_error)
        return {"available": False, "drifted": False}

    try:
        pool = db.pool("switchboard")
    except KeyError:
        logger.warning("migration drift sentinel: switchboard pool unavailable for reconciliation")
        return {
            "available": True,
            "drifted": report.is_drifted,
            "reconciled": False,
            "reason": "no_pool",
        }

    conditions = await reconcile_drift_conditions(pool, report)
    return {"available": True, "drifted": report.is_drifted, "conditions": conditions}


async def run_migration_drift_loop(
    db: DatabaseManager,
    *,
    interval_s: float = DEFAULT_DRIFT_CHECK_INTERVAL_S,
) -> None:
    """Run :func:`run_migration_drift_check` every ``interval_s`` until cancelled.

    Sleeps first, mirroring ``run_secrets_lifecycle_loop`` -- avoids a real-DB
    burst at every process boot (dev reloads, full-lifespan tests) before the
    first check actually matters. A single tick's failure is logged and
    swallowed so one bad tick never kills the loop.
    """
    if interval_s <= 0:
        raise ValueError(f"interval_s must be a positive number, got {interval_s!r}")
    while True:
        await asyncio.sleep(interval_s)
        try:
            summary = await run_migration_drift_check(db)
            logger.info("migration_drift_check: %s", summary)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("migration_drift_check: tick failed")
