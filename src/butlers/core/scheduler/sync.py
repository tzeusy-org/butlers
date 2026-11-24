"""Config-to-DB synchronization of ``[[butler.schedule]]`` entries."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg

from butlers.config import ScheduleSpec
from butlers.core.scheduler.rows import (
    _DEFAULT_COMPLEXITY,
    _DISPATCH_MODE_PROMPT,
    _check_notify_reference,
    _dict_to_jsonb,
    _jsonb_to_dict,
    _references_notify,
)
from butlers.core.scheduler.timing import (
    _DEFAULT_MAX_STAGGER_SECONDS,
    _DEFAULT_SCHEDULE_TIMEZONE,
    _effective_schedule_timezone,
    _next_run,
    schedule_always_in_quiet_window,
)

logger = logging.getLogger("butlers.core.scheduler")


async def sync_schedules(
    pool: asyncpg.Pool,
    schedules: list[ScheduleSpec],
    *,
    stagger_key: str | None = None,
    max_stagger_seconds: int = _DEFAULT_MAX_STAGGER_SECONDS,
    skills_dir: Path | None = None,
    default_timezone: str = _DEFAULT_SCHEDULE_TIMEZONE,
    quiet_policy: dict[str, Any] | None = None,
) -> None:
    """Sync TOML ``[[butler.schedule]]`` entries to the ``scheduled_tasks`` DB table.

    - Insert new tasks with ``source='toml'``
    - Update changed tasks (cron or prompt changed)
    - Mark removed tasks (present in DB but not in TOML) by setting ``enabled=false``
    - Match by ``name`` field
    - Compute ``next_run_at`` via croniter for each synced task

    For ``dispatch_mode=prompt`` tasks, emits a WARNING if neither the prompt
    text nor any skill SKILL.md referenced by the prompt contains ``notify``
    (case-insensitive).  Pass *skills_dir* (``roster/{butler}/.agents/skills/``)
    to enable skill-content scanning.

    Args:
        pool: asyncpg connection pool.
        schedules: Validated ``ScheduleSpec`` entries (see ``butlers.config``); all
            schedule validation happens when the spec is constructed, not here.
        skills_dir: Optional path to the butler's skills directory for notify
            reference checking.  When provided, SKILL.md files for any
            kebab-case skill names found in the prompt are also inspected.
        default_timezone: IANA timezone used to interpret cron fields for TOML
            schedules (which have no per-row timezone).  Defaults to UTC; the
            daemon passes the owner's configured general timezone so hour-pinned
            crons fire at the intended local time. A spec may carry its own
            ``timezone`` (IANA name) which overrides this; the literal ``"UTC"`` is the
            owner-timezone sentinel, so pin real UTC with ``"Etc/UTC"``.
        quiet_policy: Owner quiet-window policy (``quiet_start_hour``, ``quiet_end_hour``,
            ``timezone``) from ``get_approvals_policy_quiet_hours``. Only used for the
            warn-only lint below; ``None`` disables it.

    Emits one WARNING per prompt-mode schedule that notifies the owner but whose every
    firing in the next week falls inside the owner quiet window (the lint never rejects,
    and uncertain cases do not warn).
    """
    # The current migrated schema is required.

    toml_names = {spec.name for spec in schedules}

    # Fetch temporal columns so the
    # needs_update check can detect changes to deadline-specific fields.
    _temporal_select = ", task_type, target_date, lead_time_days, alert_thresholds"

    _budget_select = ", max_token_budget"

    _continuity_select = ", continuity"

    _trailing_select = _continuity_select + (", timezone")

    # Fetch existing tasks whose names match any TOML schedule (regardless of source).
    # A runtime-created task (source='db') may share a name with a TOML schedule;
    # TOML takes ownership on next startup to avoid unique-constraint violations.
    rows = await pool.fetch(
        f"""
        SELECT id, name, source, cron, prompt, dispatch_mode, job_name, job_args,
               complexity, enabled{_temporal_select}{_budget_select}{_trailing_select}
        FROM scheduled_tasks
        WHERE name = ANY($1::text[])
        """,
        list(toml_names),
    )
    # Also fetch all remaining toml-sourced tasks (for the disable-removed-tasks pass below).
    toml_only_rows = await pool.fetch(
        f"""
        SELECT id, name, cron, prompt, dispatch_mode, job_name, job_args,
               complexity, enabled{_temporal_select}{_budget_select}{_trailing_select}
        FROM scheduled_tasks
        WHERE source = 'toml' AND name != ALL($1::text[])
        """,
        list(toml_names),
    )
    db_by_name: dict[str, asyncpg.Record] = {row["name"]: row for row in rows}
    # Include leftover toml-sourced tasks so the disable pass can find them.
    for row in toml_only_rows:
        db_by_name.setdefault(row["name"], row)

    for spec in schedules:
        name = spec.name
        cron = spec.cron
        task_type = spec.task_type
        prompt = spec.prompt
        dispatch_mode = spec.dispatch_mode.value
        job_name = spec.job_name
        job_args = spec.job_args
        complexity = spec.complexity or _DEFAULT_COMPLEXITY
        max_token_budget = spec.max_token_budget
        continuity = spec.continuity
        target_date = spec.target_date
        lead_time_days = spec.lead_time_days
        alert_thresholds = spec.alert_thresholds
        # Warn if a prompt-mode task omits notify() — task results will be silent otherwise.
        if dispatch_mode == _DISPATCH_MODE_PROMPT and prompt is not None:
            _check_notify_reference(task_name=name, prompt=prompt, skills_dir=skills_dir)
        # An explicit per-schedule zone wins; otherwise the owner's zone (the stored
        # "UTC" column default is the follow-the-owner sentinel, so it is stored for unset).
        stored_timezone = spec.timezone or "UTC"
        effective_timezone = _effective_schedule_timezone(spec.timezone, default_timezone)
        next_run_at = _next_run(
            cron,
            timezone=effective_timezone,
            stagger_key=stagger_key,
            max_stagger_seconds=max_stagger_seconds,
        )
        if (
            task_type == "cron"
            and dispatch_mode == _DISPATCH_MODE_PROMPT
            and prompt is not None
            and _references_notify(prompt, skills_dir)
            and schedule_always_in_quiet_window(
                cron,
                timezone=effective_timezone,
                quiet_policy=quiet_policy,
                now=datetime.now(UTC),
                max_stagger_seconds=max_stagger_seconds,
            )
        ):
            logger.warning(
                "Scheduled task %r (cron %r, zone %s) only fires inside the owner quiet window; "
                "its notifications will always be held or dropped",
                name,
                cron,
                effective_timezone,
            )

        if name in db_by_name:
            existing = db_by_name[name]
            existing_job_args = _jsonb_to_dict(
                existing["job_args"],
                context=f"scheduled_tasks[{name}]",
            )
            existing_complexity = existing.get("complexity") or _DEFAULT_COMPLEXITY
            # Reclaim from 'db' source, or update if schedule payload changed / disabled.
            needs_update = (
                existing.get("source", "toml") != "toml"
                or existing["cron"] != cron
                or existing["dispatch_mode"] != dispatch_mode
                or existing["prompt"] != prompt
                or existing["job_name"] != job_name
                or existing_job_args != job_args
                or existing_complexity != complexity
                or not existing["enabled"]
            )
            # Also detect changes to max_token_budget in the migrated schema.
            if not needs_update:
                needs_update = existing.get("max_token_budget") != max_token_budget
            # Also detect changes to continuity in the migrated schema.
            if not needs_update:
                needs_update = bool(existing.get("continuity")) != continuity
            # Also detect a changed per-schedule timezone in the migrated schema.
            if not needs_update:
                needs_update = (existing.get("timezone") or "UTC") != stored_timezone
            # Also detect changes to deadline-specific fields in the migrated schema.
            # Restrict to deadline tasks to avoid spurious updates on cron tasks that
            # may carry stale deadline-column values from a prior task_type migration.
            if not needs_update and task_type == "deadline":
                existing_alert_thresholds = existing.get("alert_thresholds")
                if isinstance(existing_alert_thresholds, str):
                    existing_alert_thresholds = json.loads(existing_alert_thresholds)
                needs_update = (
                    existing.get("target_date") != target_date
                    or existing.get("lead_time_days") != lead_time_days
                    or existing_alert_thresholds != alert_thresholds
                )
            if needs_update:
                _budget_set = ", max_token_budget = $13"
                if task_type == "deadline":
                    base_args = [
                        existing["id"],
                        cron,
                        dispatch_mode,
                        prompt,
                        job_name,
                        _dict_to_jsonb(job_args),
                        complexity,
                        next_run_at,
                        task_type,
                        target_date,
                        lead_time_days,
                        alert_thresholds,
                    ]
                    base_args.append(max_token_budget)
                    await pool.execute(
                        f"""
                        UPDATE scheduled_tasks
                        SET cron = $2,
                            dispatch_mode = $3,
                            prompt = $4,
                            job_name = $5,
                            job_args = $6,
                            complexity = $7,
                            next_run_at = $8,
                            source = 'toml',
                            enabled = true,
                            task_type = $9,
                            target_date = $10,
                            lead_time_days = $11,
                            alert_thresholds = $12{_budget_set},
                            updated_at = now()
                        WHERE id = $1
                        """,
                        *base_args,
                    )
                    await pool.execute(
                        "UPDATE scheduled_tasks SET continuity = $2 WHERE id = $1",
                        existing["id"],
                        continuity,
                    )
                else:
                    _budget_set_cron = ", max_token_budget = $9"
                    base_args = [
                        existing["id"],
                        cron,
                        dispatch_mode,
                        prompt,
                        job_name,
                        _dict_to_jsonb(job_args),
                        complexity,
                        next_run_at,
                    ]
                    base_args.append(max_token_budget)
                    await pool.execute(
                        f"""
                        UPDATE scheduled_tasks
                        SET cron = $2,
                            dispatch_mode = $3,
                            prompt = $4,
                            job_name = $5,
                            job_args = $6,
                            complexity = $7,
                            next_run_at = $8,
                            source = 'toml',
                            enabled = true{_budget_set_cron},
                            updated_at = now()
                        WHERE id = $1
                        """,
                        *base_args,
                    )
                    await pool.execute(
                        "UPDATE scheduled_tasks SET continuity = $2 WHERE id = $1",
                        existing["id"],
                        continuity,
                    )
                await pool.execute(
                    "UPDATE scheduled_tasks SET timezone = $2 WHERE id = $1",
                    existing["id"],
                    stored_timezone,
                )
                logger.info("Updated TOML schedule: %s", name)
        else:
            # Insert new TOML task
            _budget_col = ", max_token_budget"
            if task_type == "deadline":
                _budget_val = ", $13"
                base_args = [
                    name,
                    cron,
                    dispatch_mode,
                    prompt,
                    job_name,
                    _dict_to_jsonb(job_args),
                    complexity,
                    next_run_at,
                    task_type,
                    target_date,
                    lead_time_days,
                    alert_thresholds,
                ]
                base_args.append(max_token_budget)
                new_id = await pool.fetchval(
                    f"""
                    INSERT INTO scheduled_tasks (
                        name,
                        cron,
                        dispatch_mode,
                        prompt,
                        job_name,
                        job_args,
                        complexity,
                        source,
                        enabled,
                        next_run_at,
                        task_type,
                        target_date,
                        lead_time_days,
                        alert_thresholds{_budget_col}
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, 'toml', true, $8,
                            $9, $10, $11, $12{_budget_val})
                    RETURNING id
                    """,
                    *base_args,
                )
                await pool.execute(
                    "UPDATE scheduled_tasks SET continuity = $2 WHERE id = $1",
                    new_id,
                    continuity,
                )
            else:
                _budget_val_cron = ", $9"
                base_args = [
                    name,
                    cron,
                    dispatch_mode,
                    prompt,
                    job_name,
                    _dict_to_jsonb(job_args),
                    complexity,
                    next_run_at,
                ]
                base_args.append(max_token_budget)
                new_id = await pool.fetchval(
                    f"""
                    INSERT INTO scheduled_tasks (
                        name,
                        cron,
                        dispatch_mode,
                        prompt,
                        job_name,
                        job_args,
                        complexity,
                        source,
                        enabled,
                        next_run_at{_budget_col}
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, 'toml', true, $8{_budget_val_cron})
                    RETURNING id
                    """,
                    *base_args,
                )
                await pool.execute(
                    "UPDATE scheduled_tasks SET continuity = $2 WHERE id = $1",
                    new_id,
                    continuity,
                )
            await pool.execute(
                "UPDATE scheduled_tasks SET timezone = $2 WHERE id = $1",
                new_id,
                stored_timezone,
            )
            logger.info("Inserted TOML schedule: %s", name)

    # Disable TOML tasks no longer present in config
    for name, row in db_by_name.items():
        if name not in toml_names and row["enabled"]:
            await pool.execute(
                """
                UPDATE scheduled_tasks SET enabled = false, updated_at = now()
                WHERE id = $1
                  AND source = 'toml'
                  AND enabled = true
                """,
                row["id"],
            )
            logger.info("Disabled removed TOML schedule: %s", name)
