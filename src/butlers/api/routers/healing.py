"""Dashboard API routes for self-healing attempt visibility and circuit breaker management.

Provides:

- ``router`` — healing routes at ``/api/healing``

Endpoints:
- GET  /api/healing/attempts                  — paginated list with optional status filter
- GET  /api/healing/attempts/{id}             — full attempt detail
- POST /api/healing/attempts/{id}/retry       — create new attempt for same fingerprint
- GET  /api/healing/circuit-breaker           — circuit breaker status
- POST /api/healing/circuit-breaker/reset     — reset circuit breaker

All reads/writes query ``shared.healing_attempts`` via the shared credential pool.
Retry rejection (HTTP 409) is enforced for non-terminal attempts.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from butlers.api.db import DatabaseManager
from butlers.api.models import PaginatedResponse, PaginationMeta
from butlers.core.healing.dispatch import _CIRCUIT_BREAKER_FAILURE_STATUSES
from butlers.core.healing.tracking import (
    TERMINAL_STATUSES,
    VALID_STATUSES,
    get_attempt,
    get_recent_terminal_statuses,
    list_attempts,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/healing", tags=["healing"])


def _get_db_manager() -> DatabaseManager:
    """Dependency stub — overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


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


class RetryResponse(BaseModel):
    """Response from a retry request."""

    attempt_id: uuid.UUID
    fingerprint: str
    status: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _shared_pool(db: DatabaseManager):
    """Return the shared credential pool, raising 503 if unavailable."""
    try:
        return db.credential_shared_pool()
    except KeyError:
        raise HTTPException(
            status_code=503,
            detail="Shared database pool is not available",
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
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        closed_at=row.get("closed_at"),
        error_detail=row.get("error_detail"),
    )


async def _count_attempts(pool, status_filter: str | None) -> int:
    """Return the total count of healing attempts, optionally filtered by status."""
    if status_filter is not None:
        result: int = await pool.fetchval(
            "SELECT COUNT(*) FROM shared.healing_attempts WHERE status = $1",
            status_filter,
        )
    else:
        result = await pool.fetchval("SELECT COUNT(*) FROM shared.healing_attempts")
    return int(result)


# ---------------------------------------------------------------------------
# GET /api/healing/attempts — paginated list
# ---------------------------------------------------------------------------


@router.get("/attempts", response_model=PaginatedResponse[HealingAttempt])
async def list_healing_attempts(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    status: str | None = Query(default=None, description="Filter by status value"),
    db: DatabaseManager = Depends(_get_db_manager),
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
    db: DatabaseManager = Depends(_get_db_manager),
) -> HealingAttempt:
    """Return the full record for a single healing attempt."""
    pool = _shared_pool(db)
    row = await get_attempt(pool, attempt_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Healing attempt not found: {attempt_id}")
    return _row_to_attempt(row)


# ---------------------------------------------------------------------------
# POST /api/healing/attempts/{attempt_id}/retry — create new attempt
# ---------------------------------------------------------------------------


@router.post(
    "/attempts/{attempt_id}/retry",
    response_model=RetryResponse,
    status_code=201,
)
async def retry_healing_attempt(
    attempt_id: uuid.UUID,
    db: DatabaseManager = Depends(_get_db_manager),
) -> RetryResponse:
    """Create a new healing attempt for the same fingerprint as an existing attempt.

    Rejects with HTTP 409 when the attempt has a non-terminal status (investigating
    or pr_open), since the original investigation is still active.

    The new attempt is inserted directly with status ``investigating`` and an empty
    session_ids array (the retry is dashboard-triggered, not linked to a failing session).
    """
    pool = _shared_pool(db)

    # Fetch the original attempt
    original = await get_attempt(pool, attempt_id)
    if original is None:
        raise HTTPException(status_code=404, detail=f"Healing attempt not found: {attempt_id}")

    # Reject retry on non-terminal attempts (still active)
    if original["status"] not in TERMINAL_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Cannot retry attempt {attempt_id}: current status is "
                f"'{original['status']}' (not a terminal state). "
                "Wait for the active investigation to complete."
            ),
        )

    fingerprint: str = original["fingerprint"]

    # Insert a fresh investigating row bypassing the partial unique index
    # by using a direct INSERT (not create_or_join_attempt which is novelty-aware).
    # The retry semantics intentionally skip the cooldown gate — it's admin-initiated.
    try:
        new_row = await pool.fetchrow(
            """
            INSERT INTO shared.healing_attempts (
                fingerprint, butler_name, status, severity,
                exception_type, call_site, sanitized_msg, session_ids
            )
            VALUES ($1, $2, 'investigating', $3, $4, $5, $6, '{}')
            RETURNING id, fingerprint, status
            """,
            fingerprint,
            original["butler_name"],
            original["severity"],
            original["exception_type"],
            original["call_site"],
            original.get("sanitized_msg"),
        )
    except Exception as exc:
        logger.error("Failed to create retry attempt for %s: %s", attempt_id, exc)
        raise HTTPException(status_code=500, detail="Failed to create retry attempt")

    if new_row is None:
        raise HTTPException(status_code=500, detail="Retry insert returned no row")

    logger.info(
        "Retry attempt created: original=%s new=%s fingerprint=%s",
        attempt_id,
        new_row["id"],
        fingerprint[:12],
    )

    return RetryResponse(
        attempt_id=new_row["id"],
        fingerprint=new_row["fingerprint"],
        status=new_row["status"],
    )


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
    db: DatabaseManager = Depends(_get_db_manager),
) -> CircuitBreakerStatus:
    """Return the current circuit breaker state.

    The circuit breaker is tripped when the last *threshold* terminal attempts
    are all failure statuses (failed, timeout, anonymization_failed).

    ``last_failure_at`` is the ``closed_at`` of the most recent failure-status attempt.
    """
    pool = _shared_pool(db)

    # Fetch more than threshold to compute consecutive_failures accurately
    recent_statuses = await get_recent_terminal_statuses(pool, limit=threshold)

    # Count how many leading entries are failures
    consecutive_failures = 0
    for s in recent_statuses:
        if s in _CIRCUIT_BREAKER_FAILURE_STATUSES:
            consecutive_failures += 1
        else:
            break

    tripped = len(recent_statuses) >= threshold and consecutive_failures >= threshold

    # Fetch the timestamp of the most recent failure-status attempt
    last_failure_at: datetime | None = None
    if consecutive_failures > 0:
        row = await pool.fetchrow(
            """
            SELECT closed_at
            FROM shared.healing_attempts
            WHERE status = ANY($1::text[])
            ORDER BY closed_at DESC
            LIMIT 1
            """,
            list(_CIRCUIT_BREAKER_FAILURE_STATUSES),
        )
        if row is not None and row["closed_at"] is not None:
            last_failure_at = row["closed_at"]

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
    db: DatabaseManager = Depends(_get_db_manager),
) -> CircuitBreakerStatus:
    """Reset the circuit breaker by inserting a synthetic success sentinel row.

    The circuit breaker is purely derived from recent terminal attempt statuses.
    There is no persistent "tripped" flag. Resetting works by inserting a
    ``pr_merged`` sentinel row with a synthetic fingerprint, which breaks the
    consecutive-failure streak so subsequent status queries return tripped=False.

    Returns the circuit breaker state after the reset.
    """
    pool = _shared_pool(db)

    # Insert a synthetic pr_merged row to break the failure streak.
    # This is the least-invasive mechanism: no schema changes needed.
    # The sentinel fingerprint is prefixed to make it identifiable in dashboards.
    sentinel_fingerprint = f"reset-sentinel-{uuid.uuid4().hex}"
    try:
        await pool.execute(
            """
            INSERT INTO shared.healing_attempts (
                fingerprint, butler_name, status, severity,
                exception_type, call_site, session_ids, closed_at
            )
            VALUES ($1, 'dashboard', 'pr_merged', 4, 'CircuitBreakerReset', 'dashboard:reset',
                    '{}', now())
            """,
            sentinel_fingerprint,
        )
        logger.info("Circuit breaker reset via dashboard (sentinel=%s)", sentinel_fingerprint[:24])
    except Exception as exc:
        logger.error("Failed to reset circuit breaker: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to reset circuit breaker")

    # Return the updated state
    recent_statuses = await get_recent_terminal_statuses(pool, limit=threshold)
    consecutive_failures = 0
    for s in recent_statuses:
        if s in _CIRCUIT_BREAKER_FAILURE_STATUSES:
            consecutive_failures += 1
        else:
            break

    tripped = len(recent_statuses) >= threshold and consecutive_failures >= threshold

    return CircuitBreakerStatus(
        tripped=tripped,
        consecutive_failures=consecutive_failures,
        threshold=threshold,
        last_failure_at=None,
    )
