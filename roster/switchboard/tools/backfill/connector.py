"""Connector-facing backfill execution tools.

These tools are called by connector processes through Switchboard MCP.
Dashboard UI does not call these tools; it uses the controls in controls.py.

Tools exposed:
- backfill_poll     — claim the next pending job for a connector identity
- backfill_progress — report progress, advance cursor, and check for stop signals

See docs/connectors/email_backfill.md §6 and docs/roles/switchboard_butler.md §16.2
for the full contract.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

import asyncpg

logger = logging.getLogger(__name__)


async def backfill_poll(
    pool: asyncpg.Pool,
    *,
    connector_type: str,
    endpoint_identity: str,
) -> dict[str, Any] | None:
    """Claim the oldest pending backfill job for the given connector identity.

    Atomically transitions the job from ``pending`` to ``active`` and sets
    ``started_at`` if this is the first activation. Returns ``None`` when no
    pending job exists for this connector.

    Connectors MUST call this no more frequently than once every 60 seconds
    (see ``CONNECTOR_BACKFILL_POLL_INTERVAL_S`` in connector config).

    Args:
        pool: Database connection pool.
        connector_type: Canonical connector type (e.g. ``gmail``).
        endpoint_identity: The account identity this connector serves.

    Returns:
        On success::

            {
                "job_id": "<uuid>",
                "params": {
                    "target_categories": [...],
                    "date_from": "YYYY-MM-DD",
                    "date_to": "YYYY-MM-DD",
                    "rate_limit_per_hour": int,
                    "daily_cost_cap_cents": int,
                },
                "cursor": <JSONB payload or None>,
            }

        Returns ``None`` when no pending job is available.

    Raises:
        ValueError: If connector_type or endpoint_identity are empty.
        RuntimeError: If the database operation fails unexpectedly.
    """
    if not connector_type or not connector_type.strip():
        raise ValueError("connector_type must be a non-empty string")

    if not endpoint_identity or not endpoint_identity.strip():
        raise ValueError("endpoint_identity must be a non-empty string")

    try:
        # Atomically claim the oldest pending job for this connector identity.
        # CTE uses SELECT ... FOR UPDATE SKIP LOCKED to avoid races between
        # concurrent poll calls (rare in practice but safe under load).
        row = await pool.fetchrow(
            """
            WITH claimed AS (
                SELECT id
                FROM backfill_jobs
                WHERE connector_type = $1
                  AND endpoint_identity = $2
                  AND status = 'pending'
                ORDER BY created_at ASC
                LIMIT 1
                FOR UPDATE SKIP LOCKED
            )
            UPDATE backfill_jobs bj
            SET
                status = 'active',
                started_at = COALESCE(bj.started_at, now()),
                updated_at = now()
            FROM claimed
            WHERE bj.id = claimed.id
            RETURNING
                bj.id,
                bj.target_categories,
                bj.date_from,
                bj.date_to,
                bj.rate_limit_per_hour,
                bj.daily_cost_cap_cents,
                bj.cursor
            """,
            connector_type,
            endpoint_identity,
        )
    except Exception as exc:
        logger.error(
            "backfill.poll failed for %s/%s: %s",
            connector_type,
            endpoint_identity,
            exc,
            exc_info=True,
        )
        raise RuntimeError(f"backfill.poll database error: {exc}") from exc

    if row is None:
        logger.debug(
            "backfill.poll: no pending job for connector_type=%s endpoint_identity=%s",
            connector_type,
            endpoint_identity,
        )
        return None

    target_categories = row["target_categories"]
    if isinstance(target_categories, str):
        target_categories = json.loads(target_categories)

    cursor_raw = row["cursor"]
    if isinstance(cursor_raw, str):
        cursor_val = json.loads(cursor_raw)
    else:
        cursor_val = cursor_raw

    job_id = str(row["id"])

    logger.info(
        "backfill.poll: assigned job_id=%s to connector_type=%s endpoint_identity=%s",
        job_id,
        connector_type,
        endpoint_identity,
    )

    return {
        "job_id": job_id,
        "params": {
            "target_categories": target_categories,
            "date_from": row["date_from"].isoformat() if row["date_from"] else None,
            "date_to": row["date_to"].isoformat() if row["date_to"] else None,
            "rate_limit_per_hour": row["rate_limit_per_hour"],
            "daily_cost_cap_cents": row["daily_cost_cap_cents"],
        },
        "cursor": cursor_val,
    }


async def backfill_progress(
    pool: asyncpg.Pool,
    *,
    job_id: UUID | str,
    connector_type: str,
    endpoint_identity: str,
    rows_processed: int,
    rows_skipped: int,
    cost_spent_cents_delta: int,
    cursor: dict[str, Any] | None = None,
    status: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    """Report batch progress for an active backfill job.

    Updates cumulative counters using per-batch deltas, advances the resume
    cursor, and applies optional connector-requested status transitions.

    If the cumulative ``cost_spent_cents`` reaches or exceeds
    ``daily_cost_cap_cents``, Switchboard transitions the job to
    ``cost_capped`` regardless of the ``status`` argument.

    Connectors MUST stop processing and clean up when the returned status
    is anything other than ``active``.

    Args:
        pool: Database connection pool.
        job_id: UUID of the job being reported on.
        connector_type: Must match the job's connector_type (identity scoping).
        endpoint_identity: Must match the job's endpoint_identity (identity scoping).
        rows_processed: Number of rows processed in this batch (non-negative).
        rows_skipped: Number of rows skipped in this batch (non-negative).
        cost_spent_cents_delta: Additional cost in cents for this batch (non-negative).
        cursor: Optional updated resume cursor (opaque JSONB).
        status: Optional terminal status reported by connector
            (``completed`` or ``error``).
        error: Optional error detail (should accompany ``status="error"``).

    Returns:
        ``{status: str}`` — the authoritative job status after this update.

    Raises:
        ValueError: If the job is not found, the connector identity does not match,
            the job is not active, or the batch values are invalid.
        RuntimeError: If the database update fails unexpectedly.
    """
    job_id_val = UUID(str(job_id))

    if rows_processed < 0:
        raise ValueError("rows_processed must be >= 0")
    if rows_skipped < 0:
        raise ValueError("rows_skipped must be >= 0")
    if cost_spent_cents_delta < 0:
        raise ValueError("cost_spent_cents_delta must be >= 0")

    _VALID_CONNECTOR_TERMINAL_STATUSES = frozenset({"completed", "error"})
    if status is not None and status not in _VALID_CONNECTOR_TERMINAL_STATUSES:
        raise ValueError(
            f"Connector may only report status as one of "
            f"{sorted(_VALID_CONNECTOR_TERMINAL_STATUSES)}, got {status!r}"
        )

    # Load current job state with identity scoping check
    row = await pool.fetchrow(
        """
        SELECT
            id,
            connector_type,
            endpoint_identity,
            status,
            cost_spent_cents,
            daily_cost_cap_cents
        FROM backfill_jobs
        WHERE id = $1
        """,
        job_id_val,
    )
    if row is None:
        raise ValueError(f"Backfill job {job_id_val} not found")

    # Enforce connector identity scoping: connectors may only progress their own jobs.
    if row["connector_type"] != connector_type or row["endpoint_identity"] != endpoint_identity:
        raise ValueError(
            f"Connector identity mismatch: job {job_id_val} belongs to "
            f"({row['connector_type']!r}, {row['endpoint_identity']!r}), "
            f"not ({connector_type!r}, {endpoint_identity!r})"
        )

    current_status = row["status"]
    if current_status != "active":
        # Return authoritative status immediately — connector should stop.
        logger.info(
            "backfill.progress: job %s is not active (status=%s); "
            "returning authoritative status to connector",
            job_id_val,
            current_status,
        )
        return {"status": current_status}

    # Determine new authoritative status
    new_cost = row["cost_spent_cents"] + cost_spent_cents_delta
    cost_cap = row["daily_cost_cap_cents"]

    if new_cost >= cost_cap:
        new_status = "cost_capped"
        logger.info(
            "backfill.progress: job %s cost_capped (cumulative=%d >= cap=%d)",
            job_id_val,
            new_cost,
            cost_cap,
        )
    elif status in _VALID_CONNECTOR_TERMINAL_STATUSES:
        new_status = status
    else:
        new_status = "active"

    # Compute completed_at for terminal states
    is_terminal = new_status in {"completed", "cancelled", "error", "cost_capped"}

    cursor_json = json.dumps(cursor) if cursor is not None else None

    try:
        result = await pool.fetchrow(
            """
            UPDATE backfill_jobs
            SET
                rows_processed   = rows_processed + $2,
                rows_skipped     = rows_skipped + $3,
                cost_spent_cents = cost_spent_cents + $4,
                cursor           = CASE WHEN $5::text IS NOT NULL
                                        THEN $5::jsonb
                                        ELSE cursor END,
                status           = $6,
                error            = CASE WHEN $7::text IS NOT NULL THEN $7 ELSE error END,
                completed_at     = CASE WHEN $8
                                       THEN COALESCE(completed_at, now())
                                       ELSE completed_at END,
                updated_at       = now()
            WHERE id = $1
            RETURNING status, rows_processed, rows_skipped, cost_spent_cents
            """,
            job_id_val,
            rows_processed,
            rows_skipped,
            cost_spent_cents_delta,
            cursor_json,
            new_status,
            error,
            is_terminal,
        )
    except Exception as exc:
        logger.error("backfill.progress failed for job %s: %s", job_id_val, exc, exc_info=True)
        raise RuntimeError(f"backfill.progress database error: {exc}") from exc

    authoritative_status = result["status"]
    logger.info(
        "backfill.progress: job_id=%s status=%s rows_processed=%d rows_skipped=%d "
        "cost_spent_cents=%d",
        job_id_val,
        authoritative_status,
        result["rows_processed"],
        result["rows_skipped"],
        result["cost_spent_cents"],
    )

    return {"status": authoritative_status}
