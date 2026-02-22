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
_DISPATCH_MODE_PROMPT = "prompt"
_DISPATCH_MODE_JOB = "job"
_ALLOWED_DISPATCH_MODES = {_DISPATCH_MODE_PROMPT, _DISPATCH_MODE_JOB}


def _normalize_schedule_projection_fields(
    *,
    timezone: Any,
    start_at: Any,
    end_at: Any,
    until_at: Any,
    display_title: Any,
    calendar_event_id: Any,
    context: str,
) -> tuple[
    str | None, datetime | None, datetime | None, datetime | None, str | None, uuid.UUID | None
]:
    """Validate optional calendar-projection fields used by scheduler rows."""
    normalized_timezone: str | None = None
    if timezone is not None:
        if not isinstance(timezone, str):
            raise ValueError(f"{context}.timezone must be a string when set")
        stripped = timezone.strip()
        if not stripped:
            raise ValueError(f"{context}.timezone must be non-empty when set")
        normalized_timezone = stripped

    normalized_start_at: datetime | None = None
    if start_at is not None:
        if not isinstance(start_at, datetime):
            raise ValueError(f"{context}.start_at must be a datetime when set")
        if start_at.tzinfo is None:
            raise ValueError(f"{context}.start_at must be timezone-aware")
        normalized_start_at = start_at

    normalized_end_at: datetime | None = None
    if end_at is not None:
        if not isinstance(end_at, datetime):
            raise ValueError(f"{context}.end_at must be a datetime when set")
        if end_at.tzinfo is None:
            raise ValueError(f"{context}.end_at must be timezone-aware")
        normalized_end_at = end_at

    normalized_until_at: datetime | None = None
    if until_at is not None:
        if not isinstance(until_at, datetime):
            raise ValueError(f"{context}.until_at must be a datetime when set")
        if until_at.tzinfo is None:
            raise ValueError(f"{context}.until_at must be timezone-aware")
        normalized_until_at = until_at

    if (
        normalized_start_at is not None
        and normalized_end_at is not None
        and normalized_end_at <= normalized_start_at
    ):
        raise ValueError(f"{context}.end_at must be after start_at")
    if (
        normalized_start_at is not None
        and normalized_until_at is not None
        and normalized_until_at < normalized_start_at
    ):
        raise ValueError(f"{context}.until_at must be on/after start_at")

    normalized_display_title: str | None = None
    if display_title is not None:
        if not isinstance(display_title, str):
            raise ValueError(f"{context}.display_title must be a string when set")
        stripped = display_title.strip()
        if not stripped:
            raise ValueError(f"{context}.display_title must be non-empty when set")
        normalized_display_title = stripped

    normalized_calendar_event_id: uuid.UUID | None = None
    if calendar_event_id is not None:
        if isinstance(calendar_event_id, uuid.UUID):
            normalized_calendar_event_id = calendar_event_id
        elif isinstance(calendar_event_id, str):
            normalized_calendar_event_id = uuid.UUID(calendar_event_id)
        else:
            raise ValueError(f"{context}.calendar_event_id must be UUID or UUID string when set")

    return (
        normalized_timezone,
        normalized_start_at,
        normalized_end_at,
        normalized_until_at,
        normalized_display_title,
        normalized_calendar_event_id,
    )


def _normalize_dispatch_mode(value: Any, *, context: str) -> str:
    """Normalize and validate a schedule dispatch mode value."""
    if not isinstance(value, str):
        raise ValueError(f"{context}.dispatch_mode must be a string")
    normalized = value.strip().lower()
    if normalized not in _ALLOWED_DISPATCH_MODES:
        raise ValueError(
            f"Invalid {context}.dispatch_mode: {value!r}. "
            f"Expected one of {sorted(_ALLOWED_DISPATCH_MODES)!r}."
        )
    return normalized


def _normalize_schedule_dispatch(
    *,
    dispatch_mode: Any,
    prompt: Any,
    job_name: Any,
    job_args: Any,
    context: str,
) -> tuple[str, str | None, str | None, dict[str, Any] | None]:
    """Validate mode-specific dispatch fields and return normalized values."""
    mode = _normalize_dispatch_mode(dispatch_mode, context=context)

    if prompt is not None and not isinstance(prompt, str):
        raise ValueError(f"{context}.prompt must be a string when set")
    if job_name is not None and not isinstance(job_name, str):
        raise ValueError(f"{context}.job_name must be a string when set")
    if job_args is not None and not isinstance(job_args, dict):
        raise ValueError(f"{context}.job_args must be a dict/object when set")

    if mode == _DISPATCH_MODE_PROMPT:
        if prompt is None or not prompt.strip():
            raise ValueError(
                f"{context} with dispatch_mode={_DISPATCH_MODE_PROMPT!r} requires non-empty prompt"
            )
        if job_name is not None:
            raise ValueError(
                f"{context}.job_name is only valid when dispatch_mode={_DISPATCH_MODE_JOB!r}"
            )
        if job_args is not None:
            raise ValueError(
                f"{context}.job_args is only valid when dispatch_mode={_DISPATCH_MODE_JOB!r}"
            )
        return mode, prompt, None, None

    if prompt is not None:
        raise ValueError(
            f"{context}.prompt is not allowed when dispatch_mode={_DISPATCH_MODE_JOB!r}"
        )
    if job_name is None or not job_name.strip():
        raise ValueError(
            f"{context} with dispatch_mode={_DISPATCH_MODE_JOB!r} requires non-empty job_name"
        )

    return mode, None, job_name.strip(), dict(job_args) if job_args is not None else None


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


def _dict_to_jsonb(value: dict[str, Any] | None) -> str | None:
    """Convert a dict payload to a JSON string suitable for JSONB binding."""
    if value is None:
        return None
    return json.dumps(value, default=str)


def _jsonb_to_dict(value: Any, *, context: str) -> dict[str, Any] | None:
    """Normalize JSONB payloads that may come back as dicts or JSON strings."""
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{context}.job_args contains invalid JSON") from exc
        if isinstance(decoded, dict):
            return decoded
    raise ValueError(f"{context}.job_args must decode to an object")


async def sync_schedules(
    pool: asyncpg.Pool,
    schedules: list[dict[str, Any]],
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
        schedules: List of dicts with schedule fields.
    """
    normalized_schedules: list[dict[str, Any]] = []
    for i, schedule in enumerate(schedules):
        schedule_path = f"schedules[{i}]"
        if not isinstance(schedule, dict):
            raise ValueError(f"{schedule_path} must be a dict/object")

        name = schedule.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"{schedule_path}.name must be a non-empty string")

        cron = schedule.get("cron")
        if not isinstance(cron, str) or not cron.strip():
            raise ValueError(f"{schedule_path}.cron must be a non-empty string")
        if not croniter.is_valid(cron):
            raise ValueError(f"Invalid {schedule_path}.cron: {cron!r}")

        dispatch_mode, prompt, job_name, job_args = _normalize_schedule_dispatch(
            dispatch_mode=schedule.get("dispatch_mode", _DISPATCH_MODE_PROMPT),
            prompt=schedule.get("prompt"),
            job_name=schedule.get("job_name"),
            job_args=schedule.get("job_args"),
            context=schedule_path,
        )
        normalized_schedules.append(
            {
                "name": name,
                "cron": cron,
                "dispatch_mode": dispatch_mode,
                "prompt": prompt,
                "job_name": job_name,
                "job_args": job_args,
            }
        )

    toml_names = {s["name"] for s in normalized_schedules}

    # Fetch existing TOML-sourced tasks
    rows = await pool.fetch(
        """
        SELECT id, name, cron, prompt, dispatch_mode, job_name, job_args, enabled
        FROM scheduled_tasks
        WHERE source = 'toml'
        """
    )
    db_by_name: dict[str, asyncpg.Record] = {row["name"]: row for row in rows}

    for entry in normalized_schedules:
        name = entry["name"]
        cron = entry["cron"]
        prompt = entry["prompt"]
        dispatch_mode = entry["dispatch_mode"]
        job_name = entry["job_name"]
        job_args = entry["job_args"]
        next_run_at = _next_run(
            cron,
            stagger_key=stagger_key,
            max_stagger_seconds=max_stagger_seconds,
        )

        if name in db_by_name:
            existing = db_by_name[name]
            existing_job_args = _jsonb_to_dict(
                existing["job_args"],
                context=f"scheduled_tasks[{name}]",
            )
            # Update if schedule payload changed, or if task was disabled.
            if (
                existing["cron"] != cron
                or existing["dispatch_mode"] != dispatch_mode
                or existing["prompt"] != prompt
                or existing["job_name"] != job_name
                or existing_job_args != job_args
                or not existing["enabled"]
            ):
                await pool.execute(
                    """
                    UPDATE scheduled_tasks
                    SET cron = $2,
                        dispatch_mode = $3,
                        prompt = $4,
                        job_name = $5,
                        job_args = $6,
                        next_run_at = $7,
                        enabled = true,
                        updated_at = now()
                    WHERE id = $1
                    """,
                    existing["id"],
                    cron,
                    dispatch_mode,
                    prompt,
                    job_name,
                    _dict_to_jsonb(job_args),
                    next_run_at,
                )
                logger.info("Updated TOML schedule: %s", name)
        else:
            # Insert new TOML task
            await pool.execute(
                """
                INSERT INTO scheduled_tasks (
                    name,
                    cron,
                    dispatch_mode,
                    prompt,
                    job_name,
                    job_args,
                    source,
                    enabled,
                    next_run_at
                )
                VALUES ($1, $2, $3, $4, $5, $6, 'toml', true, $7)
                """,
                name,
                cron,
                dispatch_mode,
                prompt,
                job_name,
                _dict_to_jsonb(job_args),
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
            SELECT id, name, cron, dispatch_mode, prompt, job_name, job_args
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
            dispatch_mode = row["dispatch_mode"]
            job_name = row["job_name"]
            job_args = _jsonb_to_dict(row["job_args"], context=f"scheduled_tasks[{name}]")

            result_json: str | None = None
            try:
                if dispatch_mode == _DISPATCH_MODE_PROMPT:
                    result = await dispatch_fn(prompt=prompt, trigger_source=f"schedule:{name}")
                elif dispatch_mode == _DISPATCH_MODE_JOB:
                    result = await dispatch_fn(
                        job_name=job_name,
                        job_args=job_args,
                        trigger_source=f"schedule:{name}",
                    )
                else:
                    raise RuntimeError(
                        f"Unsupported dispatch_mode {dispatch_mode!r} for scheduled task {name!r}"
                    )
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
        SELECT id, name, cron, dispatch_mode, prompt, job_name, job_args,
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
        tasks.append(task)
    return tasks


async def schedule_create(
    pool: asyncpg.Pool,
    name: str,
    cron: str,
    prompt: str | None = None,
    *,
    dispatch_mode: str = _DISPATCH_MODE_PROMPT,
    job_name: str | None = None,
    job_args: dict[str, Any] | None = None,
    timezone: str | None = None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    until_at: datetime | None = None,
    display_title: str | None = None,
    calendar_event_id: uuid.UUID | str | None = None,
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
        prompt: Prompt text for prompt-mode schedules.

    Returns:
        The new task's UUID.

    Raises:
        ValueError: If the cron expression is invalid or if the name already exists.
    """
    if not croniter.is_valid(cron):
        raise ValueError(f"Invalid cron expression: {cron!r}")
    dispatch_mode, prompt, job_name, job_args = _normalize_schedule_dispatch(
        dispatch_mode=dispatch_mode,
        prompt=prompt,
        job_name=job_name,
        job_args=job_args,
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
                timezone,
                start_at,
                end_at,
                until_at,
                display_title,
                calendar_event_id,
                source,
                enabled,
                next_run_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, 'db', true, $13)
            RETURNING id
            """,
            name,
            cron,
            dispatch_mode,
            prompt,
            job_name,
            _dict_to_jsonb(job_args),
            timezone,
            start_at,
            end_at,
            until_at,
            display_title,
            calendar_event_id,
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

    Allowed fields: ``name``, ``cron``, ``dispatch_mode``, ``prompt``,
    ``job_name``, ``job_args``, ``enabled``, ``timezone``, ``start_at``,
    ``end_at``, ``until_at``, ``display_title``, and ``calendar_event_id``.
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
    allowed = {
        "name",
        "cron",
        "dispatch_mode",
        "prompt",
        "job_name",
        "job_args",
        "enabled",
        "timezone",
        "start_at",
        "end_at",
        "until_at",
        "display_title",
        "calendar_event_id",
    }
    invalid = set(fields.keys()) - allowed
    if invalid:
        raise ValueError(f"Invalid fields: {invalid}")
    if not fields:
        return

    # Validate cron if provided
    if "cron" in fields and not croniter.is_valid(fields["cron"]):
        raise ValueError(f"Invalid cron expression: {fields['cron']!r}")
    if "dispatch_mode" in fields:
        fields["dispatch_mode"] = _normalize_dispatch_mode(
            fields["dispatch_mode"],
            context="schedule_update",
        )
    projection_fields = {
        "timezone",
        "start_at",
        "end_at",
        "until_at",
        "display_title",
        "calendar_event_id",
    }
    if projection_fields & fields.keys():
        if "timezone" in fields and fields["timezone"] is None:
            raise ValueError("schedule_update.timezone cannot be null")
        (
            timezone,
            start_at,
            end_at,
            until_at,
            display_title,
            calendar_event_id,
        ) = _normalize_schedule_projection_fields(
            timezone=fields.get("timezone"),
            start_at=fields.get("start_at"),
            end_at=fields.get("end_at"),
            until_at=fields.get("until_at"),
            display_title=fields.get("display_title"),
            calendar_event_id=fields.get("calendar_event_id"),
            context="schedule_update",
        )
        if "timezone" in fields:
            fields["timezone"] = timezone
        if "start_at" in fields:
            fields["start_at"] = start_at
        if "end_at" in fields:
            fields["end_at"] = end_at
        if "until_at" in fields:
            fields["until_at"] = until_at
        if "display_title" in fields:
            fields["display_title"] = display_title
        if "calendar_event_id" in fields:
            fields["calendar_event_id"] = calendar_event_id

    # Check task exists and fetch current state
    existing = await pool.fetchrow(
        """
        SELECT id, cron, enabled, dispatch_mode, prompt, job_name, job_args
        FROM scheduled_tasks
        WHERE id = $1
        """,
        task_id,
    )
    if existing is None:
        raise ValueError(f"Task {task_id} not found")
    existing_job_args = _jsonb_to_dict(existing["job_args"], context=f"scheduled_tasks[{task_id}]")

    normalized_fields: dict[str, Any] = dict(fields)
    dispatch_related = bool(
        {"dispatch_mode", "prompt", "job_name", "job_args"} & normalized_fields.keys()
    )

    if dispatch_related:
        requested_mode = normalized_fields.get("dispatch_mode")
        if requested_mode == _DISPATCH_MODE_PROMPT:
            normalized_fields.setdefault("job_name", None)
            normalized_fields.setdefault("job_args", None)
        elif requested_mode == _DISPATCH_MODE_JOB:
            normalized_fields.setdefault("prompt", None)

        merged = {
            "dispatch_mode": normalized_fields.get("dispatch_mode", existing["dispatch_mode"]),
            "prompt": normalized_fields.get("prompt", existing["prompt"]),
            "job_name": normalized_fields.get("job_name", existing["job_name"]),
            "job_args": normalized_fields.get("job_args", existing_job_args),
        }
        dispatch_mode, prompt, job_name, job_args = _normalize_schedule_dispatch(
            dispatch_mode=merged["dispatch_mode"],
            prompt=merged["prompt"],
            job_name=merged["job_name"],
            job_args=merged["job_args"],
            context="schedule_update",
        )
        normalized_fields["dispatch_mode"] = dispatch_mode
        normalized_fields["prompt"] = prompt
        normalized_fields["job_name"] = job_name
        normalized_fields["job_args"] = job_args

    # Build dynamic UPDATE with all fields including next_run_at if cron changed
    set_clauses = []
    params: list[Any] = [task_id]
    idx = 2
    for key, value in normalized_fields.items():
        if key == "job_args":
            value = _dict_to_jsonb(value)
        set_clauses.append(f"{key} = ${idx}")
        params.append(value)
        idx += 1

    # Handle next_run_at based on enabled toggle or cron change
    cron = normalized_fields.get("cron", existing["cron"])
    if "enabled" in normalized_fields:
        if normalized_fields["enabled"]:
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
    elif "cron" in normalized_fields:
        # Cron changed (and enabled not explicitly set): recompute next_run_at
        next_run_at = _next_run(
            normalized_fields["cron"],
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

    logger.info("Updated schedule %s: %s", task_id, list(normalized_fields.keys()))


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
