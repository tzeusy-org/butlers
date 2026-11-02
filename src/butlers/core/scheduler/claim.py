"""Claim-side admission: allowance deferral, QA policy and butler dispatch gate."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from enum import StrEnum

import asyncpg

from butlers.core.metrics import ButlerMetrics
from butlers.core.model_routing import Complexity, allowance_deferral
from butlers.core.scheduler.rows import _result_to_jsonb

logger = logging.getLogger("butlers.core.scheduler")


async def _defer_cron_for_allowance(
    pool: asyncpg.Pool,
    *,
    task_id: uuid.UUID,
    name: str,
    original_next_run_at: datetime | None,
    butler_name: str | None,
    complexity: Complexity,
    metrics: ButlerMetrics | None,
    stagger_key: str | None,
) -> bool:
    """Defer a due prompt task to the provider-allowance reset instead of dispatching it.

    True when every model this task could run on is on an exhausted provider account
    (``skipped_allowance``): ``next_run_at`` moves to the earliest reset so the task
    runs once then, ``last_result`` says why, and no runtime is invoked.  The
    optimistic ``next_run_at`` match makes a concurrent tick's deferral a no-op.
    """
    reset_at = await allowance_deferral(pool, butler_name or "", complexity)
    if reset_at is None:
        return False
    deferred = await pool.fetchrow(
        """
        UPDATE scheduled_tasks
        SET next_run_at = $2, last_result = $3, updated_at = now()
        WHERE id = $1 AND next_run_at IS NOT DISTINCT FROM $4
        RETURNING id
        """,
        task_id,
        reset_at,
        _result_to_jsonb({"outcome": "skipped_allowance", "deferred_until": reset_at.isoformat()}),
        original_next_run_at,
    )
    if deferred is not None:
        logger.info(
            "Scheduler: deferred %r to provider allowance reset at %s", name, reset_at.isoformat()
        )
        if metrics is not None:
            metrics.record_task_dispatched(
                butler=stagger_key or "unknown", task_name=name, outcome="skipped_allowance"
            )
    return True


class QaSchedulePolicy(StrEnum):
    """Closed admission categories for QA's local scheduler (REQ-staffer-qa-009).

    Only ``ALLOW`` admits new cron/deadline work. The refusals stay distinct so
    an unreadable policy is never reported as an owner stop, and each value is
    content-blind: no row, SQL, actor, reason, timestamp, or exception text.
    """

    ALLOW = "allow"
    ADMINISTRATIVE_HOLD = "administrative_hold"
    OWNER_REVIEW_HOLD = "owner_review_hold"
    LEGACY_AMBIGUOUS = "legacy_ambiguous"
    MISSING = "missing"
    MALFORMED = "malformed"
    DENIED = "denied"
    UNAVAILABLE = "unavailable"


# sw_053's role-derived projection of the caller's own control-plane policy.
_BUTLER_POLICY_QUERY = "SELECT butler_name, policy_state FROM public.butler_local_schedule_policy()"


# sw_038's fixed projection: no caller-supplied name, and it raises 42501
# unless the connection's effective role is butler_qa_rw.
_QA_POLICY_QUERY = "SELECT policy_state, policy_provenance FROM public.qa_local_schedule_policy()"


_QA_POLICY_PAIRS: dict[tuple[str, str], QaSchedulePolicy] = {
    ("active", "none"): QaSchedulePolicy.ALLOW,
    ("active", "legacy_ttl"): QaSchedulePolicy.ALLOW,
    ("active", "operator"): QaSchedulePolicy.ALLOW,
    ("paused", "legacy_operator"): QaSchedulePolicy.ADMINISTRATIVE_HOLD,
    ("paused", "operator"): QaSchedulePolicy.ADMINISTRATIVE_HOLD,
    ("quarantined", "legacy_operator"): QaSchedulePolicy.ADMINISTRATIVE_HOLD,
    ("quarantined", "operator"): QaSchedulePolicy.ADMINISTRATIVE_HOLD,
    ("review_required", "operator"): QaSchedulePolicy.OWNER_REVIEW_HOLD,
    ("review_required", "legacy_ambiguous"): QaSchedulePolicy.LEGACY_AMBIGUOUS,
}


_QA_POLICY_SQLSTATES = {
    "P0002": QaSchedulePolicy.MISSING,
    "22023": QaSchedulePolicy.MALFORMED,
    "42501": QaSchedulePolicy.DENIED,
}


async def read_qa_schedule_policy(pool: asyncpg.Pool | None) -> QaSchedulePolicy:
    """Read QA's administrative policy freshly through its own runtime-role pool.

    Never cached: every admission boundary calls this again, so an owner hold
    committed mid-tick blocks the next dispatch. Any failure maps to a refusal
    category rather than a legacy-eligibility or audit-pool fallback.
    ``asyncio.CancelledError`` is not a category; it propagates after the pool
    releases its connection and authorizes nothing.
    """
    if pool is None:
        return QaSchedulePolicy.UNAVAILABLE
    try:
        rows = await pool.fetch(_QA_POLICY_QUERY)
    except asyncpg.PostgresError as exc:
        return _QA_POLICY_SQLSTATES.get(exc.sqlstate, QaSchedulePolicy.UNAVAILABLE)
    except Exception:
        return QaSchedulePolicy.UNAVAILABLE
    if len(rows) != 1:
        return QaSchedulePolicy.MALFORMED
    try:
        pair = (rows[0]["policy_state"], rows[0]["policy_provenance"])
        return _QA_POLICY_PAIRS.get(pair, QaSchedulePolicy.MALFORMED)
    except (KeyError, IndexError, TypeError):
        return QaSchedulePolicy.MALFORMED


async def _qa_admission_refusal(pool: asyncpg.Pool) -> str | None:
    policy = await read_qa_schedule_policy(pool)
    if policy is QaSchedulePolicy.ALLOW:
        return None
    return f"QA local schedule policy {policy.value}"


async def _butler_dispatch_gated(
    eligibility_pool: asyncpg.Pool | None,
    butler_name: str | None,
) -> str | None:
    """Return a gate reason when non-QA scheduled dispatch must be suppressed.

    QA never reaches this gate: ``tick`` admits QA work only through
    :func:`read_qa_schedule_policy` on its own runtime-role pool.

    A butler under an administrative hold must not run scheduled ticks. Under
    receiver-derived routing, remote observation staleness is not an authority
    to stop a healthy local scheduler.

    With the receiver cutover enabled, the butler's own administrative policy
    (read through ``public.butler_local_schedule_policy()`` on its own runtime
    role, so *eligibility_pool* is the butler's own pool) gates local dispatch;
    a missing row or non-active policy suppresses the tick. With the flag
    disabled, the legacy route resolver also gates stale heartbeat state; that
    reads switchboard tables, so only the switchboard passes a pool then.

    Returns ``None`` when dispatch should proceed (eligible, or no gating
    context available), or a human-readable reason string when dispatch must be
    skipped.  Registry/lookup failures fail OPEN (return ``None``) so a
    transient registry hiccup never silently freezes a healthy butler's
    schedule.
    """
    if eligibility_pool is None or not butler_name:
        return None

    try:
        from butlers.tools.switchboard.registry.registry import (
            receiver_route_cutover_enabled,
            resolve_routing_target,
        )

        if receiver_route_cutover_enabled():
            # sw_053: the butler's own runtime role reads only its own policy;
            # the function derives the butler from that role, not from input.
            try:
                row = await eligibility_pool.fetchrow(_BUTLER_POLICY_QUERY)
            except asyncpg.PostgresError as exc:
                if exc.sqlstate != "P0002":  # anything but "no policy row" fails open
                    raise
                row = None
            if row is not None and row["butler_name"] != butler_name:
                raise RuntimeError("eligibility pool belongs to another butler")
            if row is None or row["policy_state"] != "active":
                return f"Butler {butler_name!r} is held by administrative policy"
            return None

        target, error = await resolve_routing_target(
            eligibility_pool,
            butler_name,
            allow_stale=False,
            allow_quarantined=False,
        )
    except Exception:
        # Registry unreachable / schema missing — fail open so scheduling keeps
        # working for healthy butlers; do not gate on infrastructure errors.
        logger.warning(
            "Scheduler eligibility check failed for butler %r; proceeding with dispatch",
            butler_name,
            exc_info=True,
        )
        return None

    if target is None:
        # error is the registry's own reason string (e.g. quarantined/stale).
        return error or f"Butler {butler_name!r} is not eligible for scheduled dispatch"
    return None
