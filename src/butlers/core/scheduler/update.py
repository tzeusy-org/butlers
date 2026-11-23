"""Runtime schedule update and toggle."""

from __future__ import annotations

import logging
import uuid
from typing import Any

import asyncpg
from croniter import croniter

from butlers.core.scheduler.rows import (
    _DISPATCH_MODE_JOB,
    _DISPATCH_MODE_PROMPT,
    _dict_to_jsonb,
    _jsonb_to_dict,
    _normalize_complexity,
    _normalize_dispatch_mode,
    _normalize_schedule_dispatch,
    _normalize_schedule_projection_fields,
)
from butlers.core.scheduler.timing import _DEFAULT_MAX_STAGGER_SECONDS, _next_run

logger = logging.getLogger("butlers.core.scheduler")


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
    ``job_name``, ``job_args``, ``complexity``, ``enabled``, ``timezone``,
    ``start_at``, ``end_at``, ``until_at``, ``display_title``, ``description``,
    ``location``, and ``calendar_event_id``.
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
        "complexity",
        "enabled",
        "timezone",
        "start_at",
        "end_at",
        "until_at",
        "display_title",
        "description",
        "location",
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
    if "complexity" in fields:
        fields["complexity"] = _normalize_complexity(
            fields["complexity"],
            context="schedule_update",
        )
    # Check task exists and fetch current state
    existing = await pool.fetchrow(
        """
        SELECT id, cron, enabled, dispatch_mode, prompt, job_name, job_args,
               complexity, timezone, start_at, end_at, until_at, display_title,
               calendar_event_id
        FROM scheduled_tasks
        WHERE id = $1
        """,
        task_id,
    )
    if existing is None:
        raise ValueError(f"Task {task_id} not found")

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
            timezone=fields.get("timezone", existing["timezone"]),
            start_at=fields.get("start_at", existing["start_at"]),
            end_at=fields.get("end_at", existing["end_at"]),
            until_at=fields.get("until_at", existing["until_at"]),
            display_title=fields.get("display_title", existing["display_title"]),
            calendar_event_id=fields.get("calendar_event_id", existing["calendar_event_id"]),
            context="schedule_update",
        )
        normalized_projection = {
            "timezone": timezone,
            "start_at": start_at,
            "end_at": end_at,
            "until_at": until_at,
            "display_title": display_title,
            "calendar_event_id": calendar_event_id,
        }
        for key in projection_fields:
            if key in fields:
                fields[key] = normalized_projection[key]

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
        dispatch_mode, prompt, job_name, job_args, _dispatch_complexity = (
            _normalize_schedule_dispatch(
                dispatch_mode=merged["dispatch_mode"],
                prompt=merged["prompt"],
                job_name=merged["job_name"],
                job_args=merged["job_args"],
                context="schedule_update",
            )
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
    # Effective cron timezone: an updated timezone wins, else the stored one.
    effective_timezone = normalized_fields.get("timezone", existing["timezone"])
    if "enabled" in normalized_fields:
        if normalized_fields["enabled"]:
            # Enabling: recompute next_run_at from current cron
            next_run_at = _next_run(
                cron,
                timezone=effective_timezone,
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
            timezone=effective_timezone,
            stagger_key=stagger_key,
            max_stagger_seconds=max_stagger_seconds,
        )
        set_clauses.append(f"next_run_at = ${idx}")
        params.append(next_run_at)
        idx += 1

    set_clauses.append("updated_at = now()")

    # Single atomic UPDATE statement
    query = (
        f"UPDATE scheduled_tasks SET {', '.join(set_clauses)} WHERE id = $1 "
        "RETURNING dashboard_attention.final_claim(current_schema(), id, NULL)"
    )
    await pool.execute(query, *params)

    logger.info("Updated schedule %s: %s", task_id, list(normalized_fields.keys()))


def _schedule_toggle_error(
    task_id: uuid.UUID,
    code: str,
    message: str,
    *,
    source: str | None = None,
) -> dict[str, Any]:
    """Build a bounded, content-free schedule-toggle refusal."""
    result: dict[str, Any] = {
        "id": str(task_id),
        "status": "error",
        "code": code,
        "message": message,
        "error": message,
    }
    if source is not None:
        result["source"] = source
    return result


async def schedule_toggle(
    pool: asyncpg.Pool,
    task_id: uuid.UUID,
    *,
    enabled: bool,
    stagger_key: str | None = None,
    max_stagger_seconds: int = _DEFAULT_MAX_STAGGER_SECONDS,
) -> dict[str, Any]:
    """Set one runtime schedule to the requested enabled state.

    ``enabled`` is the required, retry-safe request: repeating the same
    request returns an unchanged receipt instead of flipping the row again.

    TOML and other non-DB rows are configuration- or subsystem-managed and
    cannot be changed through this interactive action.  Every refusal is a
    bounded result rather than a false success.
    """
    if not isinstance(enabled, bool):
        return _schedule_toggle_error(
            task_id,
            "SCHEDULE_TOGGLE_INVALID",
            "enabled must be a boolean",
        )

    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT id, name, cron, timezone, source, enabled, next_run_at
                FROM scheduled_tasks
                WHERE id = $1
                FOR UPDATE
                """,
                task_id,
            )
            if row is None:
                return _schedule_toggle_error(
                    task_id,
                    "SCHEDULE_NOT_FOUND",
                    f"Schedule {task_id} not found",
                )

            source = str(row["source"])
            if source == "toml":
                return _schedule_toggle_error(
                    task_id,
                    "SCHEDULE_TOML_MANAGED",
                    "TOML-managed schedules can only be changed in butler.toml",
                    source=source,
                )
            if source != "db":
                return _schedule_toggle_error(
                    task_id,
                    "SCHEDULE_MANAGED",
                    f"Schedule source {source!r} is managed by its owning subsystem",
                    source=source,
                )

            current_enabled = bool(row["enabled"])
            requested_enabled = enabled
            if current_enabled == requested_enabled:
                if not requested_enabled:
                    await conn.fetchval(
                        "SELECT dashboard_attention.final_claim(current_schema(), $1, NULL)",
                        task_id,
                    )
                observed_next_run_at = row["next_run_at"]
                changed = False
            else:
                observed_next_run_at = (
                    _next_run(
                        row["cron"],
                        timezone=row["timezone"],
                        stagger_key=stagger_key,
                        max_stagger_seconds=max_stagger_seconds,
                    )
                    if requested_enabled
                    else None
                )
                observed_next_run_at = await conn.fetchval(
                    """
                    UPDATE scheduled_tasks
                    SET enabled = $2, next_run_at = $3, updated_at = now()
                    WHERE id = $1
                    RETURNING next_run_at, dashboard_attention.final_claim(
                        current_schema(), id, NULL
                    ) AS attention_claim
                    """,
                    task_id,
                    requested_enabled,
                    observed_next_run_at,
                )
                changed = True

            return {
                "id": str(task_id),
                "name": row["name"],
                "source": source,
                "status": "updated" if changed else "unchanged",
                "outcome": "applied" if changed else "already_requested",
                "requested_enabled": requested_enabled,
                "observed_enabled": requested_enabled,
                "changed": changed,
                "next_run_at": (
                    observed_next_run_at.isoformat() if observed_next_run_at is not None else None
                ),
                "audit": {
                    "action": "schedule.toggle",
                    "result": "success",
                    "target": f"schedule:{task_id}",
                },
            }
