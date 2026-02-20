"""Task scheduler — cron-driven task dispatch with TOML sync.

On startup, syncs [[butler.schedule]] entries from TOML config to the
scheduled_tasks table. At each tick(), evaluates cron expressions via
croniter and dispatches due task prompts to the LLM CLI spawner serially.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
from croniter import croniter
from opentelemetry import trace

logger = logging.getLogger(__name__)

_DEFAULT_MAX_STAGGER_SECONDS = 15 * 60


def _cron_interval_seconds(cron: str, *, now: datetime | None = None) -> int:
    """Return the interval between the next two occurrences for ``cron``."""
    anchor = now or datetime.now(UTC)
    it = croniter(cron, anchor)
    first = it.get_next(datetime).replace(tzinfo=UTC)
    second = it.get_next(datetime).replace(tzinfo=UTC)
    return max(1, int((second - first).total_seconds()))


def _stagger_offset_seconds(
    cron: str,
    *,
    stagger_key: str | None = None,
    max_stagger_seconds: int = _DEFAULT_MAX_STAGGER_SECONDS,
    now: datetime | None = None,
) -> int:
    """Compute a deterministic offset that never exceeds the cron cadence."""
    if not stagger_key or max_stagger_seconds <= 0:
        return 0

    cadence_seconds = _cron_interval_seconds(cron, now=now)
    max_safe_offset = min(max_stagger_seconds, cadence_seconds - 1)
    if max_safe_offset <= 0:
        return 0

    digest = hashlib.sha256(stagger_key.encode("utf-8")).digest()
    bucket = int.from_bytes(digest[:8], byteorder="big", signed=False)
    return bucket % (max_safe_offset + 1)


def _next_run(
    cron: str,
    *,
    stagger_key: str | None = None,
    max_stagger_seconds: int = _DEFAULT_MAX_STAGGER_SECONDS,
    now: datetime | None = None,
) -> datetime:
    """Compute the next run time for a cron expression from now (UTC)."""
    anchor = now or datetime.now(UTC)
    next_run = croniter(cron, anchor).get_next(datetime).replace(tzinfo=UTC)
    offset_seconds = _stagger_offset_seconds(
        cron,
        stagger_key=stagger_key,
        max_stagger_seconds=max_stagger_seconds,
        now=anchor,
    )
    if offset_seconds:
        return next_run + timedelta(seconds=offset_seconds)
    return next_run


def _result_to_jsonb(result: Any) -> str | None:
    """Convert a dispatch result to a JSON string suitable for JSONB storage.

    Handles dataclass-like objects (SpawnerResult) by extracting their __dict__,
    plain dicts, and falls back to string representation.
    """
    if result is None:
        return None
    if hasattr(result, "__dict__") and not isinstance(result, type):
        return json.dumps(result.__dict__, default=str)
    if isinstance(result, dict):
        return json.dumps(result, default=str)
    return json.dumps({"result": str(result)}, default=str)


async def sync_schedules(
    pool: asyncpg.Pool,
    schedules: list[dict[str, str]],
    *,
    stagger_key: str | None = None,
    max_stagger_seconds: int = _DEFAULT_MAX_STAGGER_SECONDS,
) -> None:
    """Sync TOML ``[[butler.schedule]]`` entries to the ``scheduled_tasks`` DB table.

    - Insert new tasks with ``source='toml'``
    - Update changed tasks (cron or prompt changed)
    - Mark removed tasks (present in DB but not in TOML) by setting ``enabled=false``
    - Match by ``name`` field
    - Compute ``next_run_at`` via croniter for each synced task

    Args:
        pool: asyncpg connection pool.
        schedules: List of dicts with keys ``name``, ``cron``, ``prompt``.
    """
    toml_names = {s["name"] for s in schedules}

    # Fetch existing TOML-sourced tasks
    rows = await pool.fetch(
        "SELECT id, name, cron, prompt, enabled FROM scheduled_tasks WHERE source = 'toml'"
    )
    db_by_name: dict[str, asyncpg.Record] = {row["name"]: row for row in rows}

    for entry in schedules:
        name = entry["name"]
        cron = entry["cron"]
        prompt = entry["prompt"]
        next_run_at = _next_run(
            cron,
            stagger_key=stagger_key,
            max_stagger_seconds=max_stagger_seconds,
        )

        if name in db_by_name:
            existing = db_by_name[name]
            # Update if cron or prompt changed, or if task was disabled
            if existing["cron"] != cron or existing["prompt"] != prompt or not existing["enabled"]:
                await pool.execute(
                    """
                    UPDATE scheduled_tasks
                    SET cron = $2, prompt = $3, next_run_at = $4,
                        enabled = true, updated_at = now()
                    WHERE id = $1
                    """,
                    existing["id"],
                    cron,
                    prompt,
                    next_run_at,
                )
                logger.info("Updated TOML schedule: %s", name)
        else:
            # Insert new TOML task
            await pool.execute(
                """
                INSERT INTO scheduled_tasks (name, cron, prompt, source, enabled, next_run_at)
                VALUES ($1, $2, $3, 'toml', true, $4)
                """,
                name,
                cron,
                prompt,
                next_run_at,
            )
            logger.info("Inserted TOML schedule: %s", name)

    # Disable TOML tasks no longer present in config
    for name, row in db_by_name.items():
        if name not in toml_names and row["enabled"]:
            await pool.execute(
                """
                UPDATE scheduled_tasks SET enabled = false, updated_at = now()
                WHERE id = $1
                """,
                row["id"],
            )
            logger.info("Disabled removed TOML schedule: %s", name)


async def tick(
    pool: asyncpg.Pool,
    dispatch_fn,
    *,
    stagger_key: str | None = None,
    max_stagger_seconds: int = _DEFAULT_MAX_STAGGER_SECONDS,
) -> int:
    """Evaluate due tasks and dispatch them.

    Queries ``scheduled_tasks`` WHERE ``enabled=true AND next_run_at <= now()``.
    For each due task, calls ``dispatch_fn(prompt=..., trigger_source="schedule:<task-name>")``.
    After dispatch, updates ``next_run_at``, ``last_run_at``, and ``last_result``.
    If dispatch fails, logs the error and stores the error in ``last_result``,
    but continues to the next task.

    Creates a ``butler.tick`` span with attributes ``tasks_due`` (count of due tasks)
    and ``tasks_run`` (count of successfully dispatched tasks).

    Args:
        pool: asyncpg connection pool.
        dispatch_fn: Async callable matching ``Spawner.trigger`` signature.

    Returns:
        The number of tasks successfully dispatched.
    """
    tracer = trace.get_tracer("butlers")
    with tracer.start_as_current_span("butler.tick") as span:
        now = datetime.now(UTC)
        rows = await pool.fetch(
            """
            SELECT id, name, cron, prompt
            FROM scheduled_tasks
            WHERE enabled = true AND next_run_at <= $1
            ORDER BY next_run_at
            """,
            now,
        )

        tasks_due = len(rows)
        span.set_attribute("tasks_due", tasks_due)

        dispatched = 0
        for row in rows:
            task_id = row["id"]
            name = row["name"]
            prompt = row["prompt"]
            cron = row["cron"]

            result_json: str | None = None
            try:
                result = await dispatch_fn(prompt=prompt, trigger_source=f"schedule:{name}")
                result_json = _result_to_jsonb(result)
                dispatched += 1
                logger.info("Dispatched scheduled task: %s", name)
            except Exception as exc:
                logger.exception("Failed to dispatch scheduled task: %s", name)
                result_json = _result_to_jsonb({"error": str(exc)})

            # Always advance next_run_at whether dispatch succeeded or failed
            next_run_at = _next_run(
                cron,
                stagger_key=stagger_key,
                max_stagger_seconds=max_stagger_seconds,
            )
            await pool.execute(
                """
                UPDATE scheduled_tasks
                SET next_run_at = $2, last_run_at = $3, last_result = $4::jsonb,
                    updated_at = now()
                WHERE id = $1
                """,
                task_id,
                next_run_at,
                now,
                result_json,
            )

        span.set_attribute("tasks_run", dispatched)
        return dispatched


async def schedule_list(pool: asyncpg.Pool) -> list[dict[str, Any]]:
    """Return all scheduled tasks.

    Returns:
        List of task records as dicts.
    """
    rows = await pool.fetch(
        """
        SELECT id, name, cron, prompt, source, enabled,
               next_run_at, last_run_at, last_result,
               created_at, updated_at
        FROM scheduled_tasks
        ORDER BY name
        """
    )
    return [dict(row) for row in rows]


async def schedule_create(
    pool: asyncpg.Pool,
    name: str,
    cron: str,
    prompt: str,
    *,
    stagger_key: str | None = None,
    max_stagger_seconds: int = _DEFAULT_MAX_STAGGER_SECONDS,
) -> uuid.UUID:
    """Create a runtime scheduled task.

    Validates cron syntax via croniter. Sets ``source='db'``.
    Computes initial ``next_run_at``.

    Args:
        pool: asyncpg connection pool.
        name: Human-readable task name.
        cron: Cron expression (5-field).
        prompt: Prompt text for the runtime instance.

    Returns:
        The new task's UUID.

    Raises:
        ValueError: If the cron expression is invalid or if the name already exists.
    """
    if not croniter.is_valid(cron):
        raise ValueError(f"Invalid cron expression: {cron!r}")

    next_run_at = _next_run(
        cron,
        stagger_key=stagger_key,
        max_stagger_seconds=max_stagger_seconds,
    )
    try:
        task_id: uuid.UUID = await pool.fetchval(
            """
            INSERT INTO scheduled_tasks (name, cron, prompt, source, enabled, next_run_at)
            VALUES ($1, $2, $3, 'db', true, $4)
            RETURNING id
            """,
            name,
            cron,
            prompt,
            next_run_at,
        )
    except asyncpg.UniqueViolationError:
        raise ValueError(f"Task name {name!r} already exists")
    logger.info("Created runtime schedule: %s (%s)", name, task_id)
    return task_id


async def schedule_update(
    pool: asyncpg.Pool,
    task_id: uuid.UUID,
    *,
    stagger_key: str | None = None,
    max_stagger_seconds: int = _DEFAULT_MAX_STAGGER_SECONDS,
    **fields,
) -> None:
    """Update fields on a scheduled task.

    Allowed fields: ``name``, ``cron``, ``prompt``, ``enabled``.
    If ``cron`` is updated, recomputes ``next_run_at``.
    If ``enabled`` is set to ``true``, recomputes ``next_run_at``.
    If ``enabled`` is set to ``false``, sets ``next_run_at`` to ``NULL``.

    Args:
        pool: asyncpg connection pool.
        task_id: UUID of the task to update.
        **fields: Field names and new values.

    Raises:
        ValueError: If ``task_id`` is not found or if an invalid field is provided,
            or if the new cron expression is invalid.
    """
    allowed = {"name", "cron", "prompt", "enabled"}
    invalid = set(fields.keys()) - allowed
    if invalid:
        raise ValueError(f"Invalid fields: {invalid}")
    if not fields:
        return

    # Validate cron if provided
    if "cron" in fields and not croniter.is_valid(fields["cron"]):
        raise ValueError(f"Invalid cron expression: {fields['cron']!r}")

    # Check task exists and fetch current state
    existing = await pool.fetchrow(
        "SELECT id, cron, enabled FROM scheduled_tasks WHERE id = $1", task_id
    )
    if existing is None:
        raise ValueError(f"Task {task_id} not found")

    # Build dynamic UPDATE with all fields including next_run_at if cron changed
    set_clauses = []
    params: list[Any] = [task_id]
    idx = 2
    for key, value in fields.items():
        set_clauses.append(f"{key} = ${idx}")
        params.append(value)
        idx += 1

    # Handle next_run_at based on enabled toggle or cron change
    cron = fields.get("cron", existing["cron"])
    if "enabled" in fields:
        if fields["enabled"]:
            # Enabling: recompute next_run_at from current cron
            next_run_at = _next_run(
                cron,
                stagger_key=stagger_key,
                max_stagger_seconds=max_stagger_seconds,
            )
            set_clauses.append(f"next_run_at = ${idx}")
            params.append(next_run_at)
            idx += 1
        else:
            # Disabling: set next_run_at to NULL
            set_clauses.append(f"next_run_at = ${idx}")
            params.append(None)
            idx += 1
    elif "cron" in fields:
        # Cron changed (and enabled not explicitly set): recompute next_run_at
        next_run_at = _next_run(
            fields["cron"],
            stagger_key=stagger_key,
            max_stagger_seconds=max_stagger_seconds,
        )
        set_clauses.append(f"next_run_at = ${idx}")
        params.append(next_run_at)
        idx += 1

    set_clauses.append("updated_at = now()")

    # Single atomic UPDATE statement
    query = f"UPDATE scheduled_tasks SET {', '.join(set_clauses)} WHERE id = $1"
    await pool.execute(query, *params)

    logger.info("Updated schedule %s: %s", task_id, list(fields.keys()))


async def schedule_delete(pool: asyncpg.Pool, task_id: uuid.UUID) -> None:
    """Delete a runtime scheduled task.

    TOML-sourced tasks cannot be deleted — they are managed via config sync.

    Args:
        pool: asyncpg connection pool.
        task_id: UUID of the task to delete.

    Raises:
        ValueError: If the task is ``source='toml'`` or if ``task_id`` is not found.
    """
    row = await pool.fetchrow("SELECT source FROM scheduled_tasks WHERE id = $1", task_id)
    if row is None:
        raise ValueError(f"Task {task_id} not found")
    if row["source"] == "toml":
        raise ValueError(f"Cannot delete TOML-sourced task {task_id}; disable it instead")

    await pool.execute("DELETE FROM scheduled_tasks WHERE id = $1", task_id)
    logger.info("Deleted runtime schedule: %s", task_id)
