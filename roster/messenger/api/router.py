"""Messenger butler delivery health endpoints.

Provides four read-only endpoints that wrap the messenger butler's delivery
health MCP tools:
  - GET /api/messenger/delivery-stats
  - GET /api/messenger/circuit-status
  - GET /api/messenger/queue-depth
  - GET /api/messenger/dead-letters

All data is queried directly from the messenger butler's PostgreSQL database
via asyncpg. No butler_name SQL filter is applied — the pool is already
scoped to the messenger schema.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query

from butlers.api.db import DatabaseManager

# Dynamically load models module from the same directory
_models_path = Path(__file__).parent / "models.py"
_spec = importlib.util.spec_from_file_location("messenger_api_models", _models_path)
if _spec is not None and _spec.loader is not None:
    _models = importlib.util.module_from_spec(_spec)
    sys.modules["messenger_api_models"] = _models
    _spec.loader.exec_module(_models)

    CircuitChannelEntry = _models.CircuitChannelEntry
    CircuitStatus = _models.CircuitStatus
    DeadLetterEntry = _models.DeadLetterEntry
    DeadLetterSummary = _models.DeadLetterSummary
    DeliveryStats = _models.DeliveryStats
    QueueDepth = _models.QueueDepth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/messenger", tags=["messenger"])

BUTLER_DB = "messenger"


def _get_db_manager() -> DatabaseManager:
    """Dependency stub — overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


def _pool(db: DatabaseManager):
    """Retrieve the messenger butler's connection pool.

    Raises HTTPException 503 if the pool is not available.
    """
    try:
        return db.pool(BUTLER_DB)
    except KeyError:
        raise HTTPException(
            status_code=503,
            detail="Messenger butler database is not available",
        )


# ---------------------------------------------------------------------------
# GET /delivery-stats
# ---------------------------------------------------------------------------


@router.get("/delivery-stats", response_model=DeliveryStats)
async def get_delivery_stats(
    window_hours: int = Query(24, ge=1, le=8760, description="Time window in hours"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> DeliveryStats:
    """Return delivery counts over the requested time window.

    Wraps the ``messenger_delivery_stats`` MCP tool logic: queries the
    ``delivery_requests`` table for rows within the window and groups
    by status. The pool is butler-scoped — no butler_name filter is applied.
    """
    pool = _pool(db)

    since_dt = datetime.now(tz=UTC) - timedelta(hours=window_hours)

    rows = await pool.fetch(
        """
        SELECT
            status,
            COUNT(*) AS cnt
        FROM delivery_requests
        WHERE created_at >= $1
        GROUP BY status
        """,
        since_dt,
    )

    counts: dict[str, int] = {row["status"]: row["cnt"] for row in rows}

    # Map delivery_requests statuses to the model fields.
    # "delivered" maps to status='delivered', "failed" to status='failed',
    # "pending"/"in_progress" are the in-flight queue, "dead_lettered" to
    # the dead_letter count, and retried is derived from attempts > 1.
    delivered = counts.get("delivered", 0)
    failed = counts.get("failed", 0)
    dead_letter = counts.get("dead_lettered", 0)
    pending = counts.get("pending", 0) + counts.get("in_progress", 0)

    # Retried = deliveries that had more than one attempt
    retried_row = await pool.fetchval(
        """
        SELECT COUNT(DISTINCT delivery_request_id)
        FROM delivery_attempts
        WHERE attempt_number > 1
          AND created_at >= $1
        """,
        since_dt,
    )
    retried = int(retried_row or 0)

    # Most-recent dispatched_at
    dispatched_row = await pool.fetchval(
        "SELECT MAX(created_at) FROM delivery_requests WHERE created_at >= $1",
        since_dt,
    )
    dispatched_at = str(dispatched_row) if dispatched_row else None

    return DeliveryStats(
        window_hours=window_hours,
        delivered=delivered,
        failed=failed,
        pending=pending,
        retried=retried,
        dead_letter=dead_letter,
        dispatched_at=dispatched_at,
    )


# ---------------------------------------------------------------------------
# GET /circuit-status
# ---------------------------------------------------------------------------


@router.get("/circuit-status", response_model=CircuitStatus)
async def get_circuit_status(
    db: DatabaseManager = Depends(_get_db_manager),
) -> CircuitStatus:
    """Return current circuit-breaker state per channel.

    Wraps the ``messenger_circuit_status`` MCP tool logic. Circuit breaker
    state is derived from recent delivery attempt outcomes in the DB.

    The pool is butler-scoped — no butler_name filter is applied.

    Note: The MCP tool (``messenger_circuit_status``) operates on in-memory
    CircuitBreaker objects that are not available from the API tier. This
    endpoint instead computes an approximation from the delivery_requests
    table: channels with only failures in the last 15 minutes are considered
    "open"; channels with a mix are "half_open"; otherwise "closed".
    """
    pool = _pool(db)

    rows = await pool.fetch(
        """
        SELECT
            channel,
            COUNT(*) FILTER (WHERE status = 'failed') AS failures,
            COUNT(*) FILTER (WHERE status = 'delivered') AS successes,
            MAX(updated_at) AS last_activity
        FROM delivery_requests
        WHERE created_at >= NOW() - INTERVAL '15 minutes'
        GROUP BY channel
        """,
    )

    channels: list[CircuitChannelEntry] = []
    for row in rows:
        failures = row["failures"] or 0
        successes = row["successes"] or 0
        total = failures + successes

        if total == 0:
            state = "closed"
        elif successes == 0 and failures > 0:
            state = "open"
        elif failures > 0 and successes > 0:
            state = "half_open"
        else:
            state = "closed"

        failure_rate = round(failures / total, 4) if total > 0 else 0.0
        last_change = str(row["last_activity"]) if row["last_activity"] else None

        channels.append(
            CircuitChannelEntry(
                name=row["channel"],
                state=state,
                last_state_change=last_change,
                failure_rate_15m=failure_rate,
            )
        )

    return CircuitStatus(channels=channels)


# ---------------------------------------------------------------------------
# GET /queue-depth
# ---------------------------------------------------------------------------


@router.get("/queue-depth", response_model=QueueDepth)
async def get_queue_depth(
    db: DatabaseManager = Depends(_get_db_manager),
) -> QueueDepth:
    """Return outbound queue depth by channel and priority.

    Wraps the ``messenger_queue_depth`` MCP tool: counts in-flight deliveries
    (status IN ('pending', 'in_progress')) broken down by channel and priority.
    The pool is butler-scoped — no butler_name filter is applied.
    """
    pool = _pool(db)

    total_row = await pool.fetchval(
        "SELECT COUNT(*) FROM delivery_requests WHERE status IN ('pending', 'in_progress')",
    )
    total = int(total_row or 0)

    channel_rows = await pool.fetch(
        """
        SELECT channel, COUNT(*) AS cnt
        FROM delivery_requests
        WHERE status IN ('pending', 'in_progress')
        GROUP BY channel
        """,
    )
    by_channel: dict[str, int] = {row["channel"]: row["cnt"] for row in channel_rows}

    # Priority breakdown — fall back to empty dict if the column doesn't exist
    # (priority is optional on delivery_requests; treat missing as unknown).
    try:
        priority_rows = await pool.fetch(
            """
            SELECT
                COALESCE(priority::text, 'normal') AS priority,
                COUNT(*) AS cnt
            FROM delivery_requests
            WHERE status IN ('pending', 'in_progress')
            GROUP BY priority
            """,
        )
        by_priority: dict[str, int] = {row["priority"]: row["cnt"] for row in priority_rows}
    except Exception:
        by_priority = {}

    return QueueDepth(
        total=total,
        by_channel=by_channel,
        by_priority=by_priority,
    )


# ---------------------------------------------------------------------------
# GET /dead-letters
# ---------------------------------------------------------------------------


@router.get("/dead-letters", response_model=DeadLetterSummary)
async def get_dead_letters(
    limit: int = Query(20, ge=1, le=500, description="Maximum number of entries to return"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> DeadLetterSummary:
    """Return a list of recent dead-letter entries.

    Wraps the ``messenger_dead_letter_list`` MCP tool: queries
    ``delivery_dead_letter`` joined to ``delivery_requests`` for target
    identity, sorted newest first. Excludes discarded entries by default.
    The pool is butler-scoped — no butler_name filter is applied.
    """
    pool = _pool(db)

    rows = await pool.fetch(
        """
        SELECT
            ddl.id,
            dr.channel,
            dr.target_identity,
            ddl.error_summary,
            ddl.last_attempt_at,
            ddl.total_attempts
        FROM delivery_dead_letter ddl
        JOIN delivery_requests dr ON ddl.delivery_request_id = dr.id
        WHERE ddl.discarded_at IS NULL
        ORDER BY ddl.created_at DESC
        LIMIT $1
        """,
        limit,
    )

    letters = [
        DeadLetterEntry(
            id=str(row["id"]),
            channel=row["channel"],
            recipient_id=row["target_identity"],
            error_message=row["error_summary"],
            attempted_at=str(row["last_attempt_at"]) if row["last_attempt_at"] else None,
            retry_count=int(row["total_attempts"] or 0),
        )
        for row in rows
    ]

    return DeadLetterSummary(letters=letters)
