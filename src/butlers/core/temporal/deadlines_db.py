"""DB CRUD helpers for deadline tasks.

Deadlines reuse the ``scheduled_tasks`` table with extra nullable columns:
  task_type          TEXT   default 'cron'        ('cron' | 'deadline')
  target_date        DATE   nullable
  lead_time_days     INT    nullable
  alert_thresholds   JSONB  nullable   [{days_before, severity}]
  deadline_status    TEXT   nullable   ('pending'|'alerted'|'escalated'|'completed'|'expired')
  fired_thresholds   JSONB  nullable   [{days_before, severity}]  — tracks what has fired
  depends_on         JSONB  nullable   [task_uuid_str, ...]

Use these functions from daemon.py MCP tool handlers and from the scheduler
tick() deadline evaluation pass.

TOML-sourced deadlines (source='toml') cannot be deleted via MCP; only
runtime-created deadlines (source='db') may be deleted.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, date, datetime
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

# Sentinel that indicates a column is absent from the DB schema (pre-migration).
_DEADLINE_COLUMNS = (
    "task_type",
    "target_date",
    "lead_time_days",
    "alert_thresholds",
    "deadline_status",
    "fired_thresholds",
    "depends_on",
)


def _jsonb_encode(value: Any) -> str | None:
    """Encode a Python value to a JSON string for JSONB binding."""
    if value is None:
        return None
    return json.dumps(value, default=str)


def _jsonb_decode(value: Any) -> Any:
    """Decode a JSONB value that may come back as dict, list, or JSON string."""
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _row_to_deadline_dict(row: asyncpg.Record) -> dict[str, Any]:
    """Convert a scheduled_tasks DB row into a deadline-oriented dict."""
    d: dict[str, Any] = dict(row)
    d["id"] = str(d["id"]) if d.get("id") is not None else None
    if d.get("calendar_event_id") is not None:
        d["calendar_event_id"] = str(d["calendar_event_id"])
    # Decode JSONB fields
    for field in ("alert_thresholds", "fired_thresholds", "depends_on", "job_args"):
        if field in d:
            d[field] = _jsonb_decode(d[field])
    # Serialize dates to ISO strings for JSON transport
    if d.get("target_date") is not None:
        d["target_date"] = d["target_date"].isoformat()
    return d


async def _has_deadline_columns(pool: asyncpg.Pool) -> bool:
    """Return True if the scheduled_tasks table has the deadline columns."""
    result = await pool.fetchval(
        """
        SELECT COUNT(*) FROM information_schema.columns
        WHERE table_name = 'scheduled_tasks'
          AND column_name = 'task_type'
        """
    )
    return int(result or 0) > 0


async def deadline_create(
    pool: asyncpg.Pool,
    *,
    name: str,
    prompt: str | None = None,
    target_date: date,
    lead_time_days: int,
    alert_thresholds: list[dict[str, Any]],
    depends_on: list[str] | None = None,
    deadline_status: str = "pending",
    butler_name: str | None = None,
) -> uuid.UUID:
    """Create a new deadline task in scheduled_tasks.

    Deadlines are created with:
      - task_type = 'deadline'
      - source = 'db'
      - enabled = true
      - cron = '0 0 * * *'  (daily tick at midnight; deadline evaluation uses target_date)
      - next_run_at = now()  (eligible immediately)
      - dispatch_mode = 'prompt' (requires a non-empty prompt)

    Args:
        pool: asyncpg connection pool.
        name: Unique deadline name.
        prompt: Prompt template for deadline dispatch (required for deadline tasks).
        target_date: The due date for the deadline.
        lead_time_days: Number of days before target_date to begin alerting.
        alert_thresholds: List of {days_before: int, severity: str} dicts.
        depends_on: Optional list of task UUIDs that must be 'completed' first.
        deadline_status: Initial status (default: 'pending').
        butler_name: Optional butler name (stored in stagger_key for reference).

    Returns:
        The new task's UUID.

    Raises:
        ValueError: If the name already exists or if the prompt is missing.
    """
    if not name or not name.strip():
        raise ValueError("deadline name must be a non-empty string")
    if prompt is None or not prompt.strip():
        raise ValueError("deadline_create requires a non-empty prompt")

    # Daily cron — the deadline evaluation pass in tick() handles actual dispatch
    cron = "0 0 * * *"
    next_run_at = datetime.now(UTC)

    try:
        task_id: uuid.UUID = await pool.fetchval(
            """
            INSERT INTO scheduled_tasks (
                name,
                cron,
                dispatch_mode,
                prompt,
                source,
                enabled,
                next_run_at,
                task_type,
                target_date,
                lead_time_days,
                alert_thresholds,
                deadline_status,
                fired_thresholds,
                depends_on
            )
            VALUES (
                $1, $2, 'prompt', $3, 'db', true, $4,
                'deadline', $5, $6, $7::jsonb, $8, '[]'::jsonb, $9::jsonb
            )
            RETURNING id
            """,
            name.strip(),
            cron,
            prompt.strip(),
            next_run_at,
            target_date,
            lead_time_days,
            _jsonb_encode(alert_thresholds),
            deadline_status,
            _jsonb_encode(depends_on or []),
        )
    except asyncpg.UniqueViolationError:
        raise ValueError(f"Deadline name {name!r} already exists")

    logger.info("Created deadline task: %s (%s)", name, task_id)
    return task_id


async def deadline_update(
    pool: asyncpg.Pool,
    task_id: uuid.UUID,
    *,
    name: str | None = None,
    prompt: str | None = None,
    target_date: date | None = None,
    lead_time_days: int | None = None,
    alert_thresholds: list[dict[str, Any]] | None = None,
    depends_on: list[str] | None = None,
    deadline_status: str | None = None,
    enabled: bool | None = None,
) -> None:
    """Update fields on a deadline task.

    When target_date changes, fired_thresholds is reset to [] and deadline_status
    is reset to 'pending' (unless an explicit deadline_status is also provided).

    Args:
        pool: asyncpg connection pool.
        task_id: UUID of the deadline task to update.
        name: New name (optional).
        prompt: New prompt (optional).
        target_date: New target date (optional; resets fired_thresholds + status).
        lead_time_days: New lead time (optional).
        alert_thresholds: New threshold list (optional).
        depends_on: New dependency list (optional).
        deadline_status: Explicit new status (optional).
        enabled: Enable or disable the task (optional).

    Raises:
        ValueError: If the task is not found or is not a deadline task.
    """
    existing = await pool.fetchrow(
        "SELECT id, task_type, source FROM scheduled_tasks WHERE id = $1",
        task_id,
    )
    if existing is None:
        raise ValueError(f"Deadline task {task_id} not found")
    if existing.get("task_type") != "deadline":
        raise ValueError(f"Task {task_id} is not a deadline task")

    set_clauses: list[str] = []
    params: list[Any] = [task_id]
    idx = 2

    if name is not None:
        if not name.strip():
            raise ValueError("name must be non-empty")
        set_clauses.append(f"name = ${idx}")
        params.append(name.strip())
        idx += 1

    if prompt is not None:
        if not prompt.strip():
            raise ValueError("prompt must be non-empty")
        set_clauses.append(f"prompt = ${idx}")
        params.append(prompt.strip())
        idx += 1

    if target_date is not None:
        set_clauses.append(f"target_date = ${idx}")
        params.append(target_date)
        idx += 1
        # Reset fired thresholds on date change
        set_clauses.append(f"fired_thresholds = ${idx}::jsonb")
        params.append("[]")
        idx += 1
        # Reset status unless caller provides explicit status
        if deadline_status is None:
            set_clauses.append(f"deadline_status = ${idx}")
            params.append("pending")
            idx += 1

    if lead_time_days is not None:
        set_clauses.append(f"lead_time_days = ${idx}")
        params.append(lead_time_days)
        idx += 1

    if alert_thresholds is not None:
        set_clauses.append(f"alert_thresholds = ${idx}::jsonb")
        params.append(_jsonb_encode(alert_thresholds))
        idx += 1

    if depends_on is not None:
        set_clauses.append(f"depends_on = ${idx}::jsonb")
        params.append(_jsonb_encode(depends_on))
        idx += 1

    if deadline_status is not None:
        set_clauses.append(f"deadline_status = ${idx}")
        params.append(deadline_status)
        idx += 1

    if enabled is not None:
        set_clauses.append(f"enabled = ${idx}")
        params.append(enabled)
        idx += 1

    if not set_clauses:
        return  # nothing to update

    set_clauses.append("updated_at = now()")
    sql = f"UPDATE scheduled_tasks SET {', '.join(set_clauses)} WHERE id = $1"
    await pool.execute(sql, *params)
    logger.info("Updated deadline task: %s", task_id)


async def deadline_list(
    pool: asyncpg.Pool,
    *,
    status_filter: str | None = None,
) -> list[dict[str, Any]]:
    """Return all deadline tasks, optionally filtered by deadline_status.

    Args:
        pool: asyncpg connection pool.
        status_filter: Optional status to filter by (e.g., 'pending', 'alerted').

    Returns:
        List of deadline task dicts.
    """
    if status_filter is not None:
        rows = await pool.fetch(
            """
            SELECT id, name, prompt, dispatch_mode, source, enabled,
                   task_type, target_date, lead_time_days,
                   alert_thresholds, deadline_status, fired_thresholds, depends_on,
                   next_run_at, last_run_at, created_at, updated_at
            FROM scheduled_tasks
            WHERE task_type = 'deadline'
              AND deadline_status = $1
            ORDER BY target_date ASC NULLS LAST, name ASC
            """,
            status_filter,
        )
    else:
        rows = await pool.fetch(
            """
            SELECT id, name, prompt, dispatch_mode, source, enabled,
                   task_type, target_date, lead_time_days,
                   alert_thresholds, deadline_status, fired_thresholds, depends_on,
                   next_run_at, last_run_at, created_at, updated_at
            FROM scheduled_tasks
            WHERE task_type = 'deadline'
            ORDER BY target_date ASC NULLS LAST, name ASC
            """
        )
    return [_row_to_deadline_dict(row) for row in rows]


async def deadline_delete(
    pool: asyncpg.Pool,
    task_id: uuid.UUID,
) -> None:
    """Delete a deadline task.

    TOML-sourced deadlines (source='toml') cannot be deleted; raises ValueError.

    Args:
        pool: asyncpg connection pool.
        task_id: UUID of the deadline task.

    Raises:
        ValueError: If not found, not a deadline, or is TOML-sourced.
    """
    existing = await pool.fetchrow(
        "SELECT id, task_type, source FROM scheduled_tasks WHERE id = $1",
        task_id,
    )
    if existing is None:
        raise ValueError(f"Deadline task {task_id} not found")
    if existing.get("task_type") != "deadline":
        raise ValueError(f"Task {task_id} is not a deadline task")
    if existing.get("source") == "toml":
        raise ValueError(
            f"Deadline {task_id} is TOML-sourced and cannot be deleted via MCP. "
            "Remove it from butler.toml instead."
        )
    await pool.execute("DELETE FROM scheduled_tasks WHERE id = $1", task_id)
    logger.info("Deleted deadline task: %s", task_id)


async def get_deadline_by_id(
    pool: asyncpg.Pool,
    task_id: uuid.UUID,
) -> dict[str, Any] | None:
    """Fetch a single deadline task by UUID.

    Returns:
        Deadline task dict, or None if not found or not a deadline.
    """
    row = await pool.fetchrow(
        """
        SELECT id, name, prompt, dispatch_mode, source, enabled,
               task_type, target_date, lead_time_days,
               alert_thresholds, deadline_status, fired_thresholds, depends_on,
               next_run_at, last_run_at, created_at, updated_at
        FROM scheduled_tasks
        WHERE id = $1 AND task_type = 'deadline'
        """,
        task_id,
    )
    if row is None:
        return None
    return _row_to_deadline_dict(row)
