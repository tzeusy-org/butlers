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

Escalation reuses existing primitives, no new table
----------------------------------------------------
"First detected" / "already escalated" debounce state is persisted in
``public.audit_log`` (no new migration) -- the exact pattern
``secrets_lifecycle.py`` already established for its own state-transition
debounce. When drift has persisted more than
:data:`DRIFT_ESCALATION_THRESHOLD_HOURS`, this module escalates to QA by
writing directly to ``public.healing_attempts`` via the existing
``core.healing.tracking`` primitives (:func:`create_or_join_attempt` /
:func:`update_attempt_status`) -- the same table and API the self-healing
``report_error`` MCP tool and QA patrol dispatch use. The row is created and
immediately transitioned to the terminal ``unfixable`` status with an
``error_detail`` carrying a human-action marker (see
``core.qa.severity.failed_with_human_action`` / ``state_of_case``), which is
exactly how this codebase already distinguishes "needs a human, not a code
fix" cases from ones the self-healing dispatcher should attempt a PR against.
Nothing polls ``healing_attempts`` for new ``investigating`` rows outside the
explicit ``dispatch_healing``/QA-dispatch call sites, so writing (and
immediately closing) a row here never triggers an unwanted healing-agent
spawn.
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
from butlers.api.deps import discover_butlers
from butlers.api.routers import audit as audit_router
from butlers.core.healing.tracking import create_or_join_attempt, update_attempt_status
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

# How long drift may persist before this module escalates to QA.
DRIFT_ESCALATION_THRESHOLD_HOURS = 24

_FIRST_DETECTED_ACTION = "migration_drift_first_detected"
_ESCALATED_ACTION = "migration_drift_escalated"
_DRIFT_ACTOR = "migration_drift_sentinel"


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


def drift_fingerprint(drifted: tuple[ChainDrift, ...]) -> str:
    """Stable SHA-256 fingerprint for one drift *composition*.

    Changing which schemas/chains are drifted (partial resolution, a new
    chain drifting) yields a new fingerprint, which resets the "first
    detected" clock for that new composition -- an accepted simplification: a
    partially-changing drift episode is treated as a new episode rather than
    tracked continuously.
    """
    canonical = "|".join(
        f"{d.schema}:{d.chain}:{d.expected_head}:{d.actual_revision or ''}"
        for d in sorted(drifted, key=lambda d: (d.schema, d.chain))
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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
# Escalation state (read-only lookups shared by the API endpoint and the loop)
# ---------------------------------------------------------------------------


async def get_drift_escalation_state(
    pool: asyncpg.Pool, fingerprint: str
) -> tuple[datetime | None, bool]:
    """Return ``(first_detected_at, escalated)`` for one drift fingerprint.

    Read-only -- safe to call from the ``/api/system/drift`` GET handler as
    well as the escalation loop below. Both markers live in
    ``public.audit_log``, keyed by ``target=<fingerprint>``.
    """
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


async def maybe_escalate_drift(pool: asyncpg.Pool, report: DriftReport) -> dict[str, Any]:
    """Record first-detection and escalate to QA once drift has persisted >24h.

    Idempotent per drift composition: writes the ``migration_drift_first_detected``
    marker at most once per fingerprint, and the ``migration_drift_escalated``
    marker (plus the ``public.healing_attempts`` case) at most once per
    fingerprint. Never raises -- an escalation failure is logged and reported
    back in the returned summary rather than crashing the sentinel loop.
    """
    if not report.is_drifted:
        return {"escalated": False, "reason": "no_drift"}

    fingerprint = drift_fingerprint(report.drifted)
    first_detected_at, already_escalated = await get_drift_escalation_state(pool, fingerprint)

    if first_detected_at is None:
        await audit_router.append(
            pool,
            _DRIFT_ACTOR,
            _FIRST_DETECTED_ACTION,
            target=fingerprint,
            note=_summarize(report.drifted),
        )
        return {
            "escalated": False,
            "reason": "newly_detected",
            "first_detected_at": report.checked_at.isoformat(),
        }

    age = report.checked_at - first_detected_at
    if age < timedelta(hours=DRIFT_ESCALATION_THRESHOLD_HOURS):
        return {
            "escalated": False,
            "reason": "within_threshold",
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
            exception_type="MigrationDriftDetected",
            call_site="butlers.jobs.deploy_drift:migration_drift_sentinel",
            session_id=uuid.uuid4(),
            sanitized_msg=_summarize(report.drifted),
        )
        await update_attempt_status(
            pool,
            attempt_id,
            "unfixable",
            error_detail=(
                "Escalated: human action required — migration drift persisted more than "
                f"{DRIFT_ESCALATION_THRESHOLD_HOURS}h without resolution. "
                f"{_summarize(report.drifted)}"
            ),
        )
        await audit_router.append(
            pool, _DRIFT_ACTOR, _ESCALATED_ACTION, target=fingerprint, note=str(attempt_id)
        )
    except Exception:
        logger.exception("migration drift sentinel: QA escalation failed")
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


async def run_migration_drift_check(db: DatabaseManager) -> dict[str, Any]:
    """Run one drift-check tick: compute drift, maybe escalate. Never raises."""
    report = await compute_drift_report(db)
    if not report.is_available:
        logger.warning("migration drift sentinel: check degraded: %s", report.check_error)
        return {"available": False, "drifted": False}
    if not report.is_drifted:
        return {"available": True, "drifted": False}

    try:
        pool = db.pool("switchboard")
    except KeyError:
        logger.warning(
            "migration drift sentinel: switchboard pool unavailable for escalation check"
        )
        return {"available": True, "drifted": True, "escalated": False, "reason": "no_pool"}

    escalation = await maybe_escalate_drift(pool, report)
    return {"available": True, "drifted": True, **escalation}


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
