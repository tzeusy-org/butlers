"""Runtime schedule create, list and delete, plus module-default schedules."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

import asyncpg
from croniter import croniter

from butlers.core.owner_creation import owner_creation_transaction
from butlers.core.scheduler.rows import (
    _DEFAULT_COMPLEXITY,
    _DISPATCH_MODE_PROMPT,
    _dict_to_jsonb,
    _jsonb_to_dict,
    _normalize_schedule_dispatch,
    _normalize_schedule_projection_fields,
    _parse_complexity_from_db_row,
)
from butlers.core.scheduler.timing import _DEFAULT_MAX_STAGGER_SECONDS, _next_run

logger = logging.getLogger("butlers.core.scheduler")


async def ensure_module_default_schedule(
    pool: asyncpg.Pool,
    *,
    name: str,
    cron: str,
    job_name: str,
    job_args: dict[str, Any] | None = None,
    complexity: str = _DEFAULT_COMPLEXITY,
    owner_butler: str | None = None,
    owner_schema: str | None = None,
) -> None:
    """Idempotently ensure a module-default deterministic job schedule exists.

    Intended for modules (e.g. the memory module) that self-register default
    maintenance schedules from ``on_startup`` after :func:`sync_schedules`
    establishes which TOML entries are currently active. Safe to call on every
    daemon boot:

    - **First boot** (no row named *name* exists): inserts a new
      ``source='db'`` row with a freshly computed ``next_run_at`` in UTC —
      the schedule becomes live on the very next tick. (``tick()``
      recomputes ``next_run_at`` in the owner's effective timezone after the
      first dispatch, so a UTC bootstrap value is only ever used once.)
    - **Later boots** (row already exists): a no-op unless the row is an
      eligible disabled TOML orphan (see below) — cron, job_args, enabled, and
      next_run_at are left untouched so this never clobbers an operator's
      DB-level cadence customization (e.g. via the ``schedule_update`` tool)
      or a cadence already computed by an earlier startup.
    - **Reclaiming a removed TOML block**: only a disabled
      ``source='toml'`` row is reclaimed. The conditional transition and its
      canonical audit entry share one transaction. A DB-owned row remains
      operator-owned even when disabled. ``memory_episode_cleanup`` is reclaimed
      like any other module default: its ``run_episode_cleanup`` handler deletes
      only *reapable* expired episodes (never an un-consolidated observation
      inside the grace window) in bounded batches, so recovering it can no
      longer trigger a destructive unbounded catch-up delete.

    Raises ``ValueError`` for an invalid cron expression. Does not validate
    or touch the cron of a pre-existing row — cron validation only applies
    to the fresh-insert path.
    """
    if not croniter.is_valid(cron):
        raise ValueError(f"ensure_module_default_schedule: invalid cron {cron!r} for {name!r}")

    normalized_owner_butler = (owner_butler or "").strip()
    normalized_owner_schema = (owner_schema or "").strip()
    next_run_at = _next_run(cron)

    async with pool.acquire() as connection:
        async with connection.transaction():
            inserted = await connection.fetchrow(
                """
                INSERT INTO scheduled_tasks (
                    name, cron, dispatch_mode, job_name, job_args, complexity,
                    source, enabled, next_run_at
                )
                VALUES ($1, $2, 'job', $3, $4, $5, 'db', true, $6)
                ON CONFLICT (name) DO NOTHING
                RETURNING id
                """,
                name,
                cron,
                job_name,
                _dict_to_jsonb(job_args),
                complexity,
                next_run_at,
            )
            if inserted is not None:
                return

            recovered = await connection.fetchrow(
                """
                UPDATE scheduled_tasks
                SET source = 'db', enabled = true, updated_at = now()
                WHERE name = $1
                  AND source = 'toml'
                  AND enabled = false
                RETURNING id
                """,
                name,
            )
            if recovered is None:
                return

            if not normalized_owner_butler or not normalized_owner_schema:
                raise ValueError(
                    "ensure_module_default_schedule requires non-empty owner_butler and "
                    "owner_schema to recover a TOML-owned schedule"
                )

            from butlers.core.audit import append as append_audit

            await append_audit(
                connection,  # pyright: ignore[reportArgumentType]
                actor=normalized_owner_butler,
                action="scheduler.module_default_recovered",
                target=f"schedule:{name}",
                metadata={
                    "prior_source": "toml",
                    "prior_enabled": False,
                    "registered_default_name": name,
                    "owner_butler": normalized_owner_butler,
                    "owner_schema": normalized_owner_schema,
                },
            )


async def schedule_list(pool: asyncpg.Pool) -> list[dict[str, Any]]:
    """Return all scheduled tasks.

    Returns:
        List of task records as dicts.
    """
    rows = await pool.fetch(
        """
        SELECT id, name, cron, dispatch_mode, prompt, job_name, job_args,
               complexity,
               timezone, start_at, end_at, until_at, display_title, calendar_event_id,
               source, enabled,
               next_run_at, last_run_at, last_result,
               created_at, updated_at
        FROM scheduled_tasks
        ORDER BY name
        """
    )
    tasks: list[dict[str, Any]] = []
    for row in rows:
        task = dict(row)
        task["job_args"] = _jsonb_to_dict(
            task.get("job_args"),
            context=f"scheduled_tasks[{task['name']}]",
        )
        # Normalize null/legacy complexity (e.g. the retired "medium"/"high"
        # tiers) to a canonical Complexity before surfacing it to MCP tool
        # callers, using the same shared parser the dispatch loop relies on
        # (_parse_complexity_from_db_row -> model_routing.coerce_complexity_tier).
        task["complexity"] = _parse_complexity_from_db_row(row).value
        tasks.append(task)
    return tasks


@owner_creation_transaction
async def schedule_create(
    pool: asyncpg.Pool,
    name: str,
    cron: str | None = None,
    prompt: str | None = None,
    *,
    task_type: str = "cron",
    dispatch_mode: str = _DISPATCH_MODE_PROMPT,
    job_name: str | None = None,
    job_args: dict[str, Any] | None = None,
    complexity: str | None = None,
    timezone: str | None = None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    until_at: datetime | None = None,
    display_title: str | None = None,
    description: str | None = None,
    location: str | None = None,
    calendar_event_id: str | None = None,
    stagger_key: str | None = None,
    max_stagger_seconds: int = _DEFAULT_MAX_STAGGER_SECONDS,
    # deadline-specific fields (required when task_type='deadline')
    target_date: Any | None = None,
    lead_time_days: int | None = None,
    alert_thresholds: list[dict[str, Any]] | None = None,
    depends_on: list[str] | None = None,
) -> uuid.UUID:
    """Create a runtime scheduled task.

    Validates cron syntax via croniter. Sets ``source='db'``.
    Computes initial ``next_run_at``.

    When ``task_type='deadline'``, deadline-specific fields (``target_date``,
    ``lead_time_days``, ``alert_thresholds``) are required and the call is
    delegated to the deadline_create helper. A cron expression is not required
    for deadline tasks (the deadline evaluation pass drives dispatch).

    Args:
        pool: asyncpg connection pool.
        name: Human-readable task name.
        cron: Cron expression (5-field). Required for cron tasks; ignored for
            deadline tasks (a default daily cron is used internally).
        prompt: Prompt text for prompt-mode schedules.
        task_type: ``'cron'`` (default) or ``'deadline'``.
        target_date: Due date (``datetime.date`` or ISO string). Required when
            ``task_type='deadline'``.
        lead_time_days: Days before target to begin alerting. Required when
            ``task_type='deadline'``.
        alert_thresholds: List of ``{days_before, severity}`` dicts. Required
            when ``task_type='deadline'``.
        depends_on: Optional list of deadline task UUID strings that must reach
            ``'completed'`` before this deadline's thresholds are evaluated.

    Returns:
        The new task's UUID.

    Raises:
        ValueError: If the cron expression is invalid or if the name already exists.
    """
    if task_type not in ("cron", "deadline"):
        raise ValueError(f"task_type must be 'cron' or 'deadline' (got {task_type!r})")

    # --- Deadline branch: delegate to deadline_create -----------------------
    if task_type == "deadline":
        from datetime import date as _date

        from butlers.core.temporal.deadlines import validate_deadline_input
        from butlers.core.temporal.deadlines_db import deadline_create as _dl_create

        if target_date is None:
            raise ValueError("target_date is required when task_type='deadline'")
        if lead_time_days is None:
            raise ValueError("lead_time_days is required when task_type='deadline'")
        if alert_thresholds is None:
            raise ValueError("alert_thresholds is required when task_type='deadline'")

        parsed_date = (
            _date.fromisoformat(target_date) if isinstance(target_date, str) else target_date
        )
        validate_deadline_input(
            target_date=parsed_date,
            lead_time_days=lead_time_days,
            alert_thresholds=alert_thresholds,
        )
        return await _dl_create(
            pool,
            name=name,
            prompt=prompt,
            target_date=parsed_date,
            lead_time_days=lead_time_days,
            alert_thresholds=alert_thresholds,
            depends_on=depends_on,
        )

    # --- Cron branch --------------------------------------------------------
    if cron is None:
        raise ValueError("cron is required when task_type='cron'")
    if not croniter.is_valid(cron):
        raise ValueError(f"Invalid cron expression: {cron!r}")
    dispatch_mode, prompt, job_name, job_args, complexity = _normalize_schedule_dispatch(
        dispatch_mode=dispatch_mode,
        prompt=prompt,
        job_name=job_name,
        job_args=job_args,
        complexity=complexity,
        context="schedule_create",
    )
    (
        timezone,
        start_at,
        end_at,
        until_at,
        display_title,
        calendar_event_id,
    ) = _normalize_schedule_projection_fields(
        timezone=timezone,
        start_at=start_at,
        end_at=end_at,
        until_at=until_at,
        display_title=display_title,
        calendar_event_id=calendar_event_id,
        context="schedule_create",
    )
    if timezone is None:
        timezone = "UTC"

    next_run_at = _next_run(
        cron,
        timezone=timezone,
        stagger_key=stagger_key,
        max_stagger_seconds=max_stagger_seconds,
    )
    try:
        task_id: uuid.UUID = await pool.fetchval(
            """
            INSERT INTO scheduled_tasks (
                name,
                cron,
                dispatch_mode,
                prompt,
                job_name,
                job_args,
                complexity,
                timezone,
                start_at,
                end_at,
                until_at,
                display_title,
                description,
                location,
                calendar_event_id,
                source,
                enabled,
                next_run_at
            )
            VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15,
                'db', true, $16
            )
            RETURNING id
            """,
            name,
            cron,
            dispatch_mode,
            prompt,
            job_name,
            _dict_to_jsonb(job_args),
            complexity,
            timezone,
            start_at,
            end_at,
            until_at,
            display_title,
            description,
            location,
            calendar_event_id,
            next_run_at,
        )
    except asyncpg.UniqueViolationError as exc:
        raise ValueError(f"Task name {name!r} already exists") from exc
    logger.info("Created runtime schedule: %s (%s)", name, task_id)
    return task_id


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
