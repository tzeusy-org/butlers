"""Dashboard-facing backfill lifecycle control tools.

These tools are called by dashboard API handlers through Switchboard MCP.
Connectors do not call these tools; they use backfill.poll and backfill.progress
instead (see connector.py).

Tools exposed:
- create_backfill_job  — create a new backfill job in pending state
- backfill_pause       — pause an active backfill job
- backfill_cancel      — cancel a job (any non-terminal state)
- backfill_resume      — re-queue a paused job
- backfill_list        — list jobs with optional filtering

See docs/connectors/gmail.md for the shared HTTP/MCP lifecycle contract.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any
from uuid import UUID

import asyncpg

logger = logging.getLogger(__name__)

# Default configuration
_DEFAULT_RATE_LIMIT_PER_HOUR = 100
_DEFAULT_DAILY_COST_CAP_CENTS = 500


async def create_backfill_job(
    pool: asyncpg.Pool,
    *,
    connector_type: str,
    endpoint_identity: str,
    target_categories: list[str],
    date_from: date | str,
    date_to: date | str,
    rate_limit_per_hour: int = _DEFAULT_RATE_LIMIT_PER_HOUR,
    daily_cost_cap_cents: int = _DEFAULT_DAILY_COST_CAP_CENTS,
) -> dict[str, Any]:
    """Create a new backfill job in pending state.

    Validates that the connector identity exists in connector_registry before
    creating the job. The job starts in ``pending`` state and will be picked
    up by the connector on the next ``backfill.poll`` call.

    Args:
        pool: Database connection pool.
        connector_type: Canonical connector type (e.g. ``gmail``, ``imap``).
        endpoint_identity: The mailbox / account identity this connector serves.
        target_categories: List of content categories to backfill
            (e.g. ``["finance", "health"]``). Empty list means all categories.
        date_from: Start of the historical window (inclusive).
        date_to: End of the historical window (inclusive).
        rate_limit_per_hour: Max messages processed per hour (default 100).
        daily_cost_cap_cents: Max spend in cents per day (default 500).

    Returns:
        ``{job_id: str, status: str}``

    Raises:
        ValueError: If connector identity is unknown or date range is invalid.
        RuntimeError: If database write fails.
    """
    # Normalise dates to strings for SQL comparison
    if isinstance(date_from, date):
        date_from_val: date = date_from
    else:
        try:
            date_from_val = date.fromisoformat(str(date_from))
        except ValueError as exc:
            raise ValueError(f"Invalid date_from format: {date_from!r}") from exc

    if isinstance(date_to, date):
        date_to_val: date = date_to
    else:
        try:
            date_to_val = date.fromisoformat(str(date_to))
        except ValueError as exc:
            raise ValueError(f"Invalid date_to format: {date_to!r}") from exc

    if date_from_val > date_to_val:
        raise ValueError(f"date_from ({date_from_val}) must not be after date_to ({date_to_val})")

    if not connector_type or not connector_type.strip():
        raise ValueError("connector_type must be a non-empty string")

    if not endpoint_identity or not endpoint_identity.strip():
        raise ValueError("endpoint_identity must be a non-empty string")

    if rate_limit_per_hour <= 0:
        raise ValueError("rate_limit_per_hour must be a positive integer")

    if daily_cost_cap_cents <= 0:
        raise ValueError("daily_cost_cap_cents must be a positive integer")

    # Verify connector is known in registry
    connector_row = await pool.fetchrow(
        """
        SELECT connector_type, endpoint_identity
        FROM switchboard.connector_registry
        WHERE connector_type = $1 AND endpoint_identity = $2
        """,
        connector_type,
        endpoint_identity,
    )
    if connector_row is None:
        raise ValueError(
            f"Connector ({connector_type!r}, {endpoint_identity!r}) not found in "
            "connector_registry. Connector must self-register via heartbeat before "
            "a backfill job can be created."
        )

    try:
        row = await pool.fetchrow(
            """
            INSERT INTO backfill_jobs (
                connector_type,
                endpoint_identity,
                target_categories,
                date_from,
                date_to,
                rate_limit_per_hour,
                daily_cost_cap_cents,
                status
            ) VALUES ($1, $2, $3::jsonb, $4, $5, $6, $7, 'pending')
            RETURNING id, status
            """,
            connector_type,
            endpoint_identity,
            json.dumps(list(target_categories)),
            date_from_val,
            date_to_val,
            rate_limit_per_hour,
            daily_cost_cap_cents,
        )
    except Exception as exc:
        logger.error(
            "Failed to create backfill job for %s/%s: %s",
            connector_type,
            endpoint_identity,
            exc,
            exc_info=True,
        )
        raise RuntimeError(f"Failed to create backfill job: {exc}") from exc

    job_id = str(row["id"])
    status = row["status"]

    logger.info(
        "Created backfill job job_id=%s connector_type=%s endpoint_identity=%s "
        "date_from=%s date_to=%s categories=%s",
        job_id,
        connector_type,
        endpoint_identity,
        date_from_val,
        date_to_val,
        target_categories,
    )

    return {"job_id": job_id, "status": status}


class BackfillJobNotFound(ValueError):
    """The requested job does not exist."""


class BackfillStateConflict(ValueError):
    """The job no longer permits this transition."""

    def __init__(self, message: str, *, http_detail: str | None = None) -> None:
        super().__init__(message)
        self.http_detail = http_detail or message


class BackfillDatabaseError(RuntimeError):
    """A database failure with the established HTTP read/write distinction."""

    def __init__(self, message: str, *, http_detail: str) -> None:
        super().__init__(message)
        self.http_detail = http_detail


_TRANSITIONS = {
    "pause": (frozenset({"pending", "active"}), "paused"),
    "cancel": (frozenset({"pending", "active", "paused", "error", "cost_capped"}), "cancelled"),
    "resume": (frozenset({"paused"}), "pending"),
}


async def _transition(pool: asyncpg.Pool, job_id: UUID | str, action: str) -> dict[str, Any]:
    job_id_val = UUID(str(job_id))
    allowed, target = _TRANSITIONS[action]
    database_operation = "fetch"
    try:
        row = await pool.fetchrow("SELECT id, status FROM backfill_jobs WHERE id = $1", job_id_val)
        if row is None:
            raise BackfillJobNotFound(f"Backfill job {job_id_val} not found")
        current = row["status"]
        # Re-pause/re-cancel are idempotent; resume from pending still refuses.
        if action != "resume" and current == target:
            return {"status": target}
        if current not in allowed:
            raise BackfillStateConflict(
                (
                    f"Backfill job {job_id_val} is in terminal state {current!r} "
                    f"and cannot be {target}"
                )
                if action != "resume"
                else f"Backfill job {job_id_val} cannot be resumed from {current!r}",
                http_detail=(
                    f"Cannot {action} job in status '{current}'. Must be one of: {sorted(allowed)}"
                ),
            )
        database_operation = action
        if action == "cancel":
            result = await pool.fetchrow(
                "UPDATE backfill_jobs SET status=$2, completed_at=now(), updated_at=now() "
                "WHERE id=$1 AND status=ANY($3::text[]) RETURNING status",
                job_id_val,
                target,
                [current],
            )
        else:
            result = await pool.fetchrow(
                "UPDATE backfill_jobs SET status=$2, updated_at=now() "
                "WHERE id=$1 AND status=ANY($3::text[]) RETURNING status",
                job_id_val,
                target,
                [current],
            )
        if result is None:
            row = await pool.fetchrow(
                "SELECT id, status FROM backfill_jobs WHERE id=$1", job_id_val
            )
            if action != "resume" and row is not None and row["status"] == target:
                return {"status": target}
            raise BackfillStateConflict(f"Backfill job {job_id_val} changed during {action}")
        return {"status": result["status"]}
    except (BackfillJobNotFound, BackfillStateConflict):
        raise
    except Exception as exc:
        logger.exception("Failed to %s backfill job %s", action, job_id_val)
        raise BackfillDatabaseError(
            f"Failed to {action} backfill job",
            http_detail=f"Failed to {database_operation} backfill job",
        ) from exc


async def backfill_pause(pool: asyncpg.Pool, *, job_id: UUID | str) -> dict[str, Any]:
    """Pause pending/active work, or return an already-paused job unchanged."""
    return await _transition(pool, job_id, "pause")


async def backfill_cancel(pool: asyncpg.Pool, *, job_id: UUID | str) -> dict[str, Any]:
    """Cancel unfinished work, including errors/capped jobs; cancellation is idempotent."""
    return await _transition(pool, job_id, "cancel")


async def backfill_resume(pool: asyncpg.Pool, *, job_id: UUID | str) -> dict[str, Any]:
    """Resume only paused work. Cost-capped work never resumes spending."""
    return await _transition(pool, job_id, "resume")


async def backfill_list(
    pool: asyncpg.Pool,
    *,
    connector_type: str | None = None,
    endpoint_identity: str | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """List backfill jobs with optional filtering.

    Args:
        pool: Database connection pool.
        connector_type: Optional filter by connector type.
        endpoint_identity: Optional filter by endpoint identity.
        status: Optional filter by job status.
        limit: Maximum number of results (default 100).

    Returns:
        List of job summary dicts, ordered by created_at descending.
        Summary fields follow the backfill contract in docs/connectors/gmail.md.
    """
    conditions: list[str] = []
    params: list[Any] = []
    param_idx = 1

    if connector_type is not None:
        conditions.append(f"connector_type = ${param_idx}")
        params.append(connector_type)
        param_idx += 1

    if endpoint_identity is not None:
        conditions.append(f"endpoint_identity = ${param_idx}")
        params.append(endpoint_identity)
        param_idx += 1

    if status is not None:
        conditions.append(f"status = ${param_idx}")
        params.append(status)
        param_idx += 1

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.append(limit)

    rows = await pool.fetch(
        f"""
        SELECT
            id,
            connector_type,
            endpoint_identity,
            target_categories,
            date_from,
            date_to,
            rate_limit_per_hour,
            daily_cost_cap_cents,
            status,
            rows_processed,
            rows_skipped,
            cost_spent_cents,
            error,
            created_at,
            started_at,
            completed_at,
            updated_at
        FROM backfill_jobs
        {where_clause}
        ORDER BY created_at DESC
        LIMIT ${param_idx}
        """,
        *params,
    )

    return [_row_to_summary(row) for row in rows]


def _row_to_summary(row: asyncpg.Record) -> dict[str, Any]:
    """Convert a backfill_jobs asyncpg record to a serialisable summary dict."""
    target_categories = row["target_categories"]
    if isinstance(target_categories, str):
        target_categories = json.loads(target_categories)

    return {
        "job_id": str(row["id"]),
        "connector_type": row["connector_type"],
        "endpoint_identity": row["endpoint_identity"],
        "target_categories": target_categories,
        "date_from": row["date_from"].isoformat() if row["date_from"] else None,
        "date_to": row["date_to"].isoformat() if row["date_to"] else None,
        "rate_limit_per_hour": row["rate_limit_per_hour"],
        "daily_cost_cap_cents": row["daily_cost_cap_cents"],
        "status": row["status"],
        "rows_processed": row["rows_processed"],
        "rows_skipped": row["rows_skipped"],
        "cost_spent_cents": row["cost_spent_cents"],
        "error": row["error"],
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        "started_at": row["started_at"].isoformat() if row["started_at"] else None,
        "completed_at": row["completed_at"].isoformat() if row["completed_at"] else None,
        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
    }
