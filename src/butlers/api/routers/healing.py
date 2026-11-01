"""Read-only historical attempt/dashboard API and explicit breaker reset.

QA owns investigation admission. The retired Retry endpoint is absent, so stale
clients cannot create attempts, enqueue work or invoke daemon MCP tools. Existing
history, dispatch-event visibility and honest reset records remain available.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from butlers.api.db import DatabaseManager
from butlers.api.deps import (
    Db,
)
from butlers.api.models import PaginatedResponse, PaginationMeta
from butlers.core.healing.constants import CIRCUIT_BREAKER_FAILURE_STATUSES
from butlers.core.healing.tracking import (
    VALID_STATUSES,
    get_attempt,
    get_recent_terminal_statuses,
    list_attempts,
    list_dispatch_events,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/healing", tags=["healing"])

# ---------------------------------------------------------------------------
# Historical attempt models
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class HealingAttempt(BaseModel):
    """Full healing attempt record for API responses."""

    id: uuid.UUID
    fingerprint: str
    butler_name: str
    status: str
    severity: int
    exception_type: str
    call_site: str
    sanitized_msg: str | None = None
    branch_name: str | None = None
    worktree_path: str | None = None
    pr_url: str | None = None
    pr_number: int | None = None
    session_ids: list[str] = Field(default_factory=list)
    healing_session_id: uuid.UUID | None = None
    current_phase: str | None = None
    workflow_deadline_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None = None
    error_detail: str | None = None


class CircuitBreakerStatus(BaseModel):
    """Circuit breaker state for the dashboard."""

    tripped: bool
    consecutive_failures: int
    threshold: int
    last_failure_at: datetime | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _shared_pool(db: DatabaseManager):
    """Return the shared credential pool, raising 503 if unavailable."""
    try:
        return db.credential_shared_pool()
    except KeyError as exc:
        raise HTTPException(
            status_code=503,
            detail="Shared database pool is not available",
        ) from exc


def _publication_lineage_held(attempt_id: uuid.UUID) -> HTTPException:
    """The structured 409 for a retry refused by an unresolved publication hold."""
    return HTTPException(
        status_code=409,
        detail={
            "code": "publication_lineage_held",
            "message": (
                f"Cannot retry attempt {attempt_id}: an unresolved publication holds this "
                "fingerprint. Reconcile it before retrying."
            ),
        },
    )


def _row_to_attempt(row: dict[str, Any]) -> HealingAttempt:
    """Convert a healing_attempts dict to a HealingAttempt model."""
    raw_session_ids = row.get("session_ids") or []
    session_ids = [str(s) for s in raw_session_ids]

    raw_healing_session_id = row.get("healing_session_id")
    healing_session_id: uuid.UUID | None = None
    if raw_healing_session_id is not None:
        try:
            healing_session_id = uuid.UUID(str(raw_healing_session_id))
        except (ValueError, AttributeError):
            pass

    return HealingAttempt(
        id=row["id"],
        fingerprint=row["fingerprint"],
        butler_name=row["butler_name"],
        status=row["status"],
        severity=row["severity"],
        exception_type=row["exception_type"],
        call_site=row["call_site"],
        sanitized_msg=row.get("sanitized_msg"),
        branch_name=row.get("branch_name"),
        worktree_path=row.get("worktree_path"),
        pr_url=row.get("pr_url"),
        pr_number=row.get("pr_number"),
        session_ids=session_ids,
        healing_session_id=healing_session_id,
        current_phase=row.get("current_phase"),
        workflow_deadline_at=row.get("workflow_deadline_at"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        closed_at=row.get("closed_at"),
        error_detail=row.get("error_detail"),
    )


async def _count_attempts(pool, status_filter: str | None) -> int:
    """Return the total count of healing attempts, optionally filtered by status."""
    if status_filter is not None:
        result: int = await pool.fetchval(
            "SELECT COUNT(*) FROM public.healing_attempts WHERE status = $1",
            status_filter,
        )
    else:
        result = await pool.fetchval("SELECT COUNT(*) FROM public.healing_attempts")
    return int(result)


async def _count_dispatch_events(
    pool, decision_filter: str | None, fingerprint_filter: str | None = None
) -> int:
    """Return the total count of dispatch events, optionally filtered by decision/fingerprint."""
    conditions: list[str] = []
    args: list[str] = []
    idx = 1
    if decision_filter is not None:
        conditions.append(f"decision = ${idx}")
        args.append(decision_filter)
        idx += 1
    if fingerprint_filter is not None:
        conditions.append(f"fingerprint = ${idx}")
        args.append(fingerprint_filter)
        idx += 1
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    result: int = await pool.fetchval(
        f"SELECT COUNT(*) FROM public.healing_dispatch_events{where}", *args
    )
    return int(result)


async def _compute_breaker_state(pool, threshold: int) -> tuple[bool, int, datetime | None]:
    """Compute circuit breaker state from the last *threshold* terminal attempts.

    Returns ``(tripped, consecutive_failures, last_failure_at)``.
    """
    recent_statuses = await get_recent_terminal_statuses(pool, limit=threshold)

    consecutive_failures = 0
    for s in recent_statuses:
        if s in CIRCUIT_BREAKER_FAILURE_STATUSES:
            consecutive_failures += 1
        else:
            break

    tripped = len(recent_statuses) >= threshold and consecutive_failures >= threshold

    last_failure_at: datetime | None = None
    if consecutive_failures > 0:
        row = await pool.fetchrow(
            """
            SELECT closed_at
            FROM public.healing_attempts
            WHERE status = ANY($1::text[])
            ORDER BY closed_at DESC
            LIMIT 1
            """,
            list(CIRCUIT_BREAKER_FAILURE_STATUSES),
        )
        if row is not None and row["closed_at"] is not None:
            last_failure_at = row["closed_at"]

    return tripped, consecutive_failures, last_failure_at


# ---------------------------------------------------------------------------
# GET /api/healing/attempts — paginated list
# ---------------------------------------------------------------------------


@router.get("/attempts", response_model=PaginatedResponse[HealingAttempt])
async def list_healing_attempts(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    status: str | None = Query(default=None, description="Filter by status value"),
    *,
    db: Db,
) -> PaginatedResponse[HealingAttempt]:
    """Return a paginated list of healing attempts, optionally filtered by status.

    Valid status values: investigating, pr_open, pr_merged, failed, unfixable,
    anonymization_failed, timeout.
    """
    if status is not None and status not in VALID_STATUSES:
        valid = ", ".join(sorted(VALID_STATUSES))
        raise HTTPException(
            status_code=422,
            detail=f"Invalid status filter '{status}'. Must be one of: {valid}",
        )

    pool = _shared_pool(db)
    rows = await list_attempts(pool, limit=limit, offset=offset, status_filter=status)
    total = await _count_attempts(pool, status)
    attempts = [_row_to_attempt(row) for row in rows]

    return PaginatedResponse[HealingAttempt](
        data=attempts,
        meta=PaginationMeta(total=total, offset=offset, limit=limit),
    )


# ---------------------------------------------------------------------------
# GET /api/healing/attempts/{attempt_id} — full detail
# ---------------------------------------------------------------------------


@router.get(
    "/attempts/{attempt_id}",
    response_model=HealingAttempt,
)
async def get_healing_attempt(
    attempt_id: uuid.UUID,
    *,
    db: Db,
) -> HealingAttempt:
    """Return the full record for a single healing attempt."""
    pool = _shared_pool(db)
    row = await get_attempt(pool, attempt_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Healing attempt not found: {attempt_id}")
    return _row_to_attempt(row)


# ---------------------------------------------------------------------------
# GET /api/healing/circuit-breaker — status
# ---------------------------------------------------------------------------


@router.get(
    "/circuit-breaker",
    response_model=CircuitBreakerStatus,
)
async def get_circuit_breaker_status(
    threshold: int = Query(
        default=5,
        ge=1,
        description="Circuit breaker threshold (number of consecutive failures to trip)",
    ),
    *,
    db: Db,
) -> CircuitBreakerStatus:
    """Return the current circuit breaker state.

    The circuit breaker is tripped when the last *threshold* terminal attempts
    are all failure statuses (failed, timeout, anonymization_failed).

    ``last_failure_at`` is the ``closed_at`` of the most recent failure-status attempt.
    """
    pool = _shared_pool(db)
    tripped, consecutive_failures, last_failure_at = await _compute_breaker_state(pool, threshold)
    return CircuitBreakerStatus(
        tripped=tripped,
        consecutive_failures=consecutive_failures,
        threshold=threshold,
        last_failure_at=last_failure_at,
    )


# ---------------------------------------------------------------------------
# POST /api/healing/circuit-breaker/reset — reset circuit breaker
# ---------------------------------------------------------------------------


@router.post(
    "/circuit-breaker/reset",
    response_model=CircuitBreakerStatus,
)
async def reset_circuit_breaker(
    threshold: int = Query(
        default=5,
        ge=1,
        description="Circuit breaker threshold to use when returning the new status",
    ),
    *,
    db: Db,
) -> CircuitBreakerStatus:
    """Reset the circuit breaker by recording an auditable reset marker.

    Writes one ``public.breaker_resets`` row (``breaker='healing'``,
    who/when/why). ``get_recent_terminal_statuses`` — consulted by both the
    dispatch-admission gate and this dashboard's breaker state — only counts
    launched attempts that closed *after* the latest reset, so this clears
    the consecutive-failure streak and re-admits dispatches WITHOUT
    fabricating a synthetic ``pr_merged`` attempt row. The real failure
    history (attempts, dispatch events) therefore stays visible and truthful.

    Returns the circuit breaker state after the reset.
    """
    pool = _shared_pool(db)

    try:
        await pool.execute(
            """
            INSERT INTO public.breaker_resets (breaker, reset_by, reason)
            VALUES ('healing', 'dashboard', 'Manual reset via healing dashboard')
            """
        )
        logger.info("Healing circuit breaker reset via dashboard (breaker_resets recorded)")
    except Exception as exc:
        logger.error("Failed to reset circuit breaker: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to reset circuit breaker") from exc

    # Return the updated state — use the same helper as get_circuit_breaker_status
    # so last_failure_at is correctly populated (previously was hard-coded None).
    tripped, consecutive_failures, last_failure_at = await _compute_breaker_state(pool, threshold)
    return CircuitBreakerStatus(
        tripped=tripped,
        consecutive_failures=consecutive_failures,
        threshold=threshold,
        last_failure_at=last_failure_at,
    )


# ---------------------------------------------------------------------------
# GET /api/healing/dispatch-events — paginated dispatch decision list
# ---------------------------------------------------------------------------


class HealingDispatchEvent(BaseModel):
    """A dispatch decision record (gate evaluation, not an execution attempt)."""

    id: uuid.UUID
    fingerprint: str
    butler_name: str
    decision: str
    reason: str | None = None
    attempt_id: uuid.UUID | None = None
    created_at: datetime


@router.get("/dispatch-events", response_model=PaginatedResponse[HealingDispatchEvent])
async def list_healing_dispatch_events(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    decision: str | None = Query(default=None, description="Filter by decision value"),
    fingerprint: str | None = Query(
        default=None,
        description=(
            "Filter by fingerprint. Combined with decision=infra_condition_open, "
            "this is the same identity public.infra_conditions uses for "
            "source=infra_state -- how the dashboard finds the QA dispatches a "
            "standing condition suppressed (bu-ep4ks.3)."
        ),
    ),
    *,
    db: Db,
) -> PaginatedResponse[HealingDispatchEvent]:
    """Return a paginated list of healing dispatch decisions.

    Dispatch events represent gate evaluations — they record why a healing
    workflow was or was not launched.  They are distinct from healing attempts,
    which record launched execution history.

    Decision values include: ``cooldown``, ``concurrency_cap``, ``circuit_breaker``,
    ``novelty_join``, ``no_model``, ``severity``, ``disabled``, ``accepted``,
    ``infra_condition_open``.
    """
    pool = _shared_pool(db)
    rows = await list_dispatch_events(
        pool,
        limit=limit,
        offset=offset,
        decision_filter=decision,
        fingerprint_filter=fingerprint,
    )
    total = await _count_dispatch_events(pool, decision, fingerprint)
    events = [
        HealingDispatchEvent(
            id=row["id"],
            fingerprint=row["fingerprint"],
            butler_name=row["butler_name"],
            decision=row["decision"],
            reason=row.get("reason"),
            attempt_id=row.get("attempt_id"),
            created_at=row["created_at"],
        )
        for row in rows
    ]
    return PaginatedResponse(
        data=events,
        meta=PaginationMeta(limit=limit, offset=offset, total=total),
    )
