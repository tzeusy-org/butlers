"""Education butler — SM-2 spaced repetition engine.

Implements the SM-2 algorithm for scheduling knowledge reviews:
- Interval progression: 6h → 12h → 1d → 6d → last * ease_factor
- Ease factor floor at 1.3
- Failed recall (quality < 3) resets repetitions to 0 but still adjusts ease factor
- Creates one-shot cron schedules for next review, enforcing a per-map batch cap
"""

from __future__ import annotations

import logging
import uuid
import warnings
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_EASE_FACTOR_MIN = 1.3
_INITIAL_EASE_FACTOR = 2.5
_BATCH_CAP = 20  # max pending review schedules per mind map
_REVIEW_SCHEDULE_PREFIX = "review-"

# ---------------------------------------------------------------------------
# Schedule stubs (core infrastructure — mocked in tests)
# ---------------------------------------------------------------------------

ScheduleCreateFn = Callable[..., Awaitable[str]]
ScheduleDeleteFn = Callable[..., Awaitable[None]]


async def _default_schedule_create(**kwargs: Any) -> str:
    """Stub: replaced by the real core schedule_create at runtime."""
    # pragma: no cover
    raise NotImplementedError("schedule_create must be provided by core infrastructure")


async def _default_schedule_delete(name: str) -> None:
    """Stub: replaced by the real core schedule_delete at runtime."""
    # pragma: no cover
    raise NotImplementedError("schedule_delete must be provided by core infrastructure")


# ---------------------------------------------------------------------------
# SM-2 pure computation
# ---------------------------------------------------------------------------


def sm2_update(
    ease_factor: float,
    repetitions: int,
    quality: int,
    last_interval: float | None = None,
) -> dict[str, Any]:
    """Compute the next SM-2 state for a node.

    This is a pure function — no database access.

    Parameters
    ----------
    ease_factor:
        Current ease factor (EF). Must be >= 1.3.
    repetitions:
        Number of successful repetitions so far (0-based).
    quality:
        Response quality 0–5 (0=blackout, 5=perfect recall).
    last_interval:
        Days since the previous review. Used for rep ≥ 4 interval computation.
        Ignored for rep 0–3 (fixed intervals apply).
        When None and rep >= 4, falls back to 6.0 days.

    Returns
    -------
    dict with:
        new_ease_factor (float): updated ease factor (≥ 1.3)
        new_repetitions (int): 0 on failure, incremented on success
        interval_days (float): days until next review
    """
    # Ease factor update (applied regardless of success/failure)
    ef_delta = 0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02)
    new_ef = max(_EASE_FACTOR_MIN, ease_factor + ef_delta)

    if quality >= 3:
        # Successful recall — increment repetitions and compute interval
        new_reps = repetitions + 1
        if repetitions == 0:
            interval = 0.25  # 6 hours
        elif repetitions == 1:
            interval = 0.5  # 12 hours
        elif repetitions == 2:
            interval = 1.0  # 1 day
        elif repetitions == 3:
            interval = 6.0  # 6 days
        else:
            base = last_interval if last_interval is not None else 6.0
            interval = base * new_ef
    else:
        # Failed recall — reset repetitions, interval back to 6 hours
        new_reps = 0
        interval = 0.25

    return {
        "new_ease_factor": new_ef,
        "new_repetitions": new_reps,
        "interval_days": interval,
    }


# ---------------------------------------------------------------------------
# Cron helper
# ---------------------------------------------------------------------------


def _datetime_to_cron(dt: datetime) -> str:
    """Convert a datetime to a one-shot cron expression: 'M H D Mo *'.

    Only the minute, hour, day, and month are encoded; the weekday is a
    wildcard so the expression fires exactly once when all four match.
    """
    return f"{dt.minute} {dt.hour} {dt.day} {dt.month} *"


# ---------------------------------------------------------------------------
# Main DB functions
# ---------------------------------------------------------------------------


async def spaced_repetition_record_response(
    pool: asyncpg.Pool,
    node_id: str,
    mind_map_id: str,
    quality: int,
    *,
    schedule_create: ScheduleCreateFn = _default_schedule_create,
    schedule_delete: ScheduleDeleteFn = _default_schedule_delete,
) -> dict[str, Any]:
    """Record a spaced-repetition review response and schedule the next review.

    Runs the SM-2 algorithm, updates the node, creates a one-shot schedule
    (or a batch schedule if the map already has ≥ 20 pending reviews).
    All DB mutations happen inside a single transaction.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    node_id:
        UUID of the node being reviewed.
    mind_map_id:
        UUID of the mind map containing the node.
    quality:
        SM-2 response quality 0–5.
    schedule_create:
        Async callable matching the core schedule_create signature.
        Defaults to an error stub; inject a real implementation at runtime.
    schedule_delete:
        Async callable matching the core schedule_delete signature.
        Defaults to an error stub; inject a real implementation at runtime.

    Returns
    -------
    dict with keys:
        interval_days, ease_factor, repetitions, next_review_at (ISO string)

    Raises
    ------
    ValueError
        If quality is outside [0, 5] or if the node is not found.
    """
    if not (0 <= quality <= 5):
        raise ValueError(f"quality must be between 0 and 5, got {quality!r}")

    async with pool.acquire() as conn:
        async with conn.transaction():
            # 1. Fetch current node state
            node_row = await conn.fetchrow(
                """
                SELECT id, label, ease_factor, repetitions,
                       next_review_at, last_reviewed_at, mastery_status
                FROM education.mind_map_nodes
                WHERE id = $1 AND mind_map_id = $2
                """,
                node_id,
                mind_map_id,
            )
            if node_row is None:
                raise ValueError(f"Node {node_id!r} not found in mind map {mind_map_id!r}")

            label = str(node_row["label"])
            ease_factor = float(node_row["ease_factor"])
            repetitions = int(node_row["repetitions"])
            current_status = str(node_row["mastery_status"])

            # Compute last interval from timestamps (if available)
            last_interval: float | None = None
            next_review_at_db = node_row["next_review_at"]
            last_reviewed_at_db = node_row["last_reviewed_at"]
            if next_review_at_db is not None and last_reviewed_at_db is not None:
                delta = next_review_at_db - last_reviewed_at_db
                last_interval = delta.total_seconds() / 86400.0

            # 2. Run SM-2
            sm2 = sm2_update(
                ease_factor=ease_factor,
                repetitions=repetitions,
                quality=quality,
                last_interval=last_interval,
            )
            new_ef = sm2["new_ease_factor"]
            new_reps = sm2["new_repetitions"]
            interval_days = sm2["interval_days"]

            # 3. Compute next_review_at
            now = datetime.now(tz=UTC)
            next_review_at = now + timedelta(days=interval_days)

            # 4. Determine mastery_status transition (if any)
            new_status = _determine_sr_status(current_status, quality)

            # 5. Update node
            set_parts = [
                "ease_factor = $1",
                "repetitions = $2",
                "next_review_at = $3",
                "last_reviewed_at = $4",
                "updated_at = now()",
            ]
            values: list[Any] = [new_ef, new_reps, next_review_at, now]
            param_idx = 5

            if new_status is not None:
                set_parts.append(f"mastery_status = ${param_idx}")
                values.append(new_status)
                param_idx += 1

            values.append(node_id)
            sql = f"""
                UPDATE education.mind_map_nodes
                SET {", ".join(set_parts)}
                WHERE id = ${param_idx}
            """
            await conn.execute(sql, *values)

            # 6. Count pending review schedules for the map (for batch-cap decision).
            # We query the schedule names via the scheduler table; if that table
            # does not exist in tests, we fall back to 0 (see schedule_count below).
            schedule_count = await _count_pending_review_schedules(conn, mind_map_id)

        # Transaction committed — now manage schedules outside the transaction.

    # 7. Delete prior schedule for this node (any rep number)
    await _delete_node_schedules(pool, node_id, schedule_delete)

    # 8. Create new schedule (individual or batch)
    if schedule_count >= _BATCH_CAP:
        # Batch schedule: single review for the whole map
        batch_name = f"{_REVIEW_SCHEDULE_PREFIX}{mind_map_id}-batch"
        cron = _datetime_to_cron(next_review_at)
        until_at = next_review_at + timedelta(hours=24)
        await schedule_create(
            name=batch_name,
            cron=cron,
            dispatch_mode="prompt",
            prompt=(
                f"Batch spaced-repetition review for mind map {mind_map_id}. "
                f"There are {schedule_count + 1} pending reviews. "
                f"Call spaced_repetition_pending_reviews(mind_map_id='{mind_map_id}') "
                "to get all due nodes and review each one."
            ),
            until_at=until_at,
        )
    else:
        # Individual schedule for this specific node
        schedule_name = f"{_REVIEW_SCHEDULE_PREFIX}{node_id}-rep{new_reps}"
        cron = _datetime_to_cron(next_review_at)
        until_at = next_review_at + timedelta(hours=24)
        await schedule_create(
            name=schedule_name,
            cron=cron,
            dispatch_mode="prompt",
            prompt=(
                f"Spaced repetition review for node '{label}' "
                f"(node_id={node_id}, mind_map_id={mind_map_id}). "
                f"Repetition #{new_reps}, ease_factor={new_ef:.2f}. "
                "Ask the user a focused recall question for this concept."
            ),
            until_at=until_at,
        )

    return {
        "interval_days": interval_days,
        "ease_factor": new_ef,
        "repetitions": new_reps,
        "next_review_at": next_review_at.isoformat(),
    }


async def spaced_repetition_pending_reviews(
    pool: asyncpg.Pool,
    mind_map_id: str,
) -> list[dict[str, Any]]:
    """Return nodes due for spaced-repetition review (next_review_at <= now).

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    mind_map_id:
        UUID of the mind map to query.

    Returns
    -------
    list of dict, each with:
        node_id, label, ease_factor, repetitions,
        next_review_at (ISO string), mastery_status
    """
    rows = await pool.fetch(
        """
        SELECT id AS node_id, label, ease_factor, repetitions,
               next_review_at, mastery_status
        FROM education.mind_map_nodes
        WHERE mind_map_id = $1
          AND next_review_at IS NOT NULL
          AND next_review_at <= now()
        ORDER BY next_review_at ASC
        """,
        mind_map_id,
    )

    result = []
    for row in rows:
        d = dict(row)
        # Serialize UUID and datetime fields
        for key, val in list(d.items()):
            if isinstance(val, uuid.UUID):
                d[key] = str(val)
            elif isinstance(val, datetime):
                d[key] = val.isoformat()
        result.append(d)

    return result


async def spaced_repetition_schedule_cleanup(
    pool: asyncpg.Pool,
    mind_map_id: str,
    *,
    schedule_delete: ScheduleDeleteFn = _default_schedule_delete,
) -> int:
    """Remove all pending review schedules for a terminal mind map.

    Only acts on maps with status 'completed' or 'abandoned'. Returns 0
    and emits a warning for active maps (idempotent no-op).

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    mind_map_id:
        UUID of the mind map to clean up.
    schedule_delete:
        Async callable matching the core schedule_delete signature.

    Returns
    -------
    int
        Count of deleted schedules (0 if map is active or no schedules exist).
    """
    # Check map status
    map_row = await pool.fetchrow(
        "SELECT status FROM education.mind_maps WHERE id = $1",
        mind_map_id,
    )
    if map_row is None:
        warnings.warn(f"Mind map not found: {mind_map_id}", stacklevel=2)
        return 0

    status = str(map_row["status"])
    if status not in ("completed", "abandoned"):
        warnings.warn(
            f"Mind map {mind_map_id!r} is active — skipping schedule cleanup",
            stacklevel=2,
        )
        return 0

    # Fetch all node IDs in this map
    node_rows = await pool.fetch(
        "SELECT id FROM education.mind_map_nodes WHERE mind_map_id = $1",
        mind_map_id,
    )

    deleted = 0

    # Delete per-node schedules: review-{node_id}-rep* patterns
    for row in node_rows:
        node_id = str(row["id"])
        # We delete by listing pending schedules; since schedule_delete accepts
        # a name and we don't know all rep numbers, we use a naming pattern.
        # In practice, we call schedule_delete for the expected pattern names;
        # the implementation should handle non-existent schedule names gracefully.
        schedule_names = await _list_node_schedule_names(pool, node_id)
        for name in schedule_names:
            await schedule_delete(name)
            deleted += 1

    # Delete the batch schedule for the map (if any)
    batch_names = await _list_batch_schedule_names(pool, mind_map_id)
    for name in batch_names:
        await schedule_delete(name)
        deleted += 1

    return deleted


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _determine_sr_status(current_status: str, quality: int) -> str | None:
    """Determine mastery_status transition after a spaced-repetition response.

    Only applies to nodes in 'reviewing' or 'mastered' status:
    - reviewing + quality < 3 → learning (regression)
    - mastered + quality < 3 → reviewing (regression)
    - Others: no change.

    Returns the new status string, or None if no change.
    """
    if current_status == "reviewing" and quality < 3:
        return "learning"
    if current_status == "mastered" and quality < 3:
        return "reviewing"
    return None


async def _count_pending_review_schedules(conn: asyncpg.Connection, mind_map_id: str) -> int:
    """Count pending review schedules for a mind map via the scheduler table.

    Queries all per-node schedules (review-{node_id}-rep*) for nodes belonging
    to this map, plus the batch schedule (review-{map_id}-batch).

    Falls back to 0 if the scheduled_tasks table does not exist (test environments).
    """
    try:
        # Fetch node IDs for this map so we can match their schedule names.
        node_rows = await conn.fetch(
            "SELECT id FROM education.mind_map_nodes WHERE mind_map_id = $1",
            mind_map_id,
        )
        node_ids = [str(row["id"]) for row in node_rows]

        # Count per-node schedules (review-{node_id}-rep*)
        node_count = 0
        for node_id in node_ids:
            n = await conn.fetchval(
                """
                SELECT COUNT(*) FROM scheduled_tasks
                WHERE name LIKE $1 AND enabled = true
                """,
                f"{_REVIEW_SCHEDULE_PREFIX}{node_id}-rep%",
            )
            node_count += int(n or 0)

        # Count batch schedule for the map
        batch_name = f"{_REVIEW_SCHEDULE_PREFIX}{mind_map_id}-batch"
        batch_count = await conn.fetchval(
            "SELECT COUNT(*) FROM scheduled_tasks WHERE name = $1 AND enabled = true",
            batch_name,
        )

        return node_count + int(batch_count or 0)
    except Exception:
        # Scheduler table not available in this environment
        return 0


async def _delete_node_schedules(
    pool: asyncpg.Pool,
    node_id: str,
    schedule_delete: ScheduleDeleteFn,
) -> None:
    """Best-effort deletion of any existing schedule for a node.

    Queries the scheduler table for all schedules matching this node, then
    deletes them. Falls back to a no-op if the table is not available.
    Silently ignores errors (e.g. schedule not found).
    """
    schedule_names = await _list_node_schedule_names(pool, node_id)
    for name in schedule_names:
        try:
            await schedule_delete(name)
        except Exception:
            # Schedule not found or already deleted — that's fine
            pass


async def _list_node_schedule_names(pool: asyncpg.Pool, node_id: str) -> list[str]:
    """Return all known schedule names for a node from the core scheduler.

    Falls back to empty list if the scheduler table is not available.
    """
    try:
        rows = await pool.fetch(
            """
            SELECT name FROM scheduled_tasks
            WHERE name LIKE $1
            """,
            f"{_REVIEW_SCHEDULE_PREFIX}{node_id}-rep%",
        )
        return [str(row["name"]) for row in rows]
    except Exception:
        return []


async def _list_batch_schedule_names(pool: asyncpg.Pool, mind_map_id: str) -> list[str]:
    """Return the batch schedule name for a map if it exists in the core scheduler.

    Falls back to a single candidate name (so we attempt deletion) if the
    scheduler table is not available.
    """
    batch_name = f"{_REVIEW_SCHEDULE_PREFIX}{mind_map_id}-batch"
    try:
        rows = await pool.fetch(
            "SELECT name FROM scheduled_tasks WHERE name = $1",
            batch_name,
        )
        return [str(row["name"]) for row in rows]
    except Exception:
        # Attempt deletion of the canonical name anyway
        return [batch_name]
