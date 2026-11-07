"""Tick pass 1: deadline threshold firing."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

import asyncpg

from butlers.core.metrics import ButlerMetrics
from butlers.core.model_routing import allowance_deferral
from butlers.core.scheduler.cancel import raise_if_task_cancelled
from butlers.core.scheduler.prompt import _prepend_seasonal_context
from butlers.core.scheduler.rows import (
    _DISPATCH_MODE_JOB,
    _jsonb_to_dict,
    _list_to_jsonb,
    _parse_complexity_from_db_row,
)
from butlers.core.scheduler.timing import _DEFAULT_MAX_STAGGER_SECONDS

logger = logging.getLogger("butlers.core.scheduler")


async def _tick_deadline_pass(
    pool: asyncpg.Pool,
    dispatch_fn,
    *,
    now: datetime,
    stagger_key: str | None = None,
    max_stagger_seconds: int = _DEFAULT_MAX_STAGGER_SECONDS,
    metrics: ButlerMetrics | None = None,
    active_seasons: list[dict[str, Any]] | None = None,
    admission_refusal: Callable[[], Awaitable[str | None]] | None = None,
    butler_name: str | None = None,
) -> tuple[int, int]:
    """Evaluate deadline tasks: fire due thresholds and handle expiry.

    Runs before the cron dispatch pass.  For each enabled deadline task:
      1. If target_date < today → transition to 'expired', disable the task.
      2. Otherwise → compute days_remaining, check for unfired thresholds,
         dispatch a prompt if a threshold fires, record it.

    Args:
        admission_refusal: Optional fresh policy check awaited immediately
            before each dispatch; a refusal stops the pass with the threshold
            left unfired.

    Returns:
        (deadlines_evaluated, deadlines_dispatched) counts.
    """
    from butlers.core.temporal.deadlines import (
        DeadlineDispatchDeferred,
        build_deadline_prompt_context,
        compute_days_remaining,
        compute_expiry_transition,
        find_unfired_threshold,
        is_deadline_blocked,
    )

    deadline_rows = await pool.fetch(
        """
        SELECT id, name, prompt, dispatch_mode, job_name, job_args, task_type,
               target_date, lead_time_days, alert_thresholds,
               deadline_status, fired_thresholds, depends_on, complexity
        FROM scheduled_tasks
        WHERE enabled = true AND task_type = 'deadline'
        ORDER BY target_date
        """,
    )

    evaluated = 0
    dispatched = 0

    for row in deadline_rows:
        evaluated += 1
        task_id = row["id"]
        name = row["name"]
        target_date = row["target_date"]
        current_status = row["deadline_status"] or "pending"

        # Parse JSONB fields (asyncpg returns them as python objects already)
        raw_alert = row["alert_thresholds"]
        alert_thresholds: list[dict[str, Any]] = (
            raw_alert if isinstance(raw_alert, list) else json.loads(raw_alert or "[]")
        )
        raw_fired = row["fired_thresholds"]
        fired_thresholds: list[dict[str, Any]] = (
            raw_fired if isinstance(raw_fired, list) else json.loads(raw_fired or "[]")
        )
        raw_depends = row["depends_on"]
        depends_on: list[str] = (
            raw_depends if isinstance(raw_depends, list) else json.loads(raw_depends or "[]")
        )

        # Step 1: check expiry
        new_status, should_disable = compute_expiry_transition(
            current_status=current_status,
            target_date=target_date,
        )
        if should_disable:
            await pool.execute(
                """
                UPDATE scheduled_tasks
                SET deadline_status = $2, enabled = false, updated_at = now()
                WHERE id = $1
                """,
                task_id,
                new_status,
            )
            logger.info("Deadline task %s expired (target_date=%s); disabled", name, target_date)
            continue

        # Step 2: skip if blocked by incomplete dependencies
        if depends_on:
            # Fetch dependency statuses
            dep_rows = await pool.fetch(
                """
                SELECT id::text, deadline_status FROM scheduled_tasks
                WHERE id::text = ANY($1::text[])
                """,
                depends_on,
            )
            dep_statuses = {r["id"]: r["deadline_status"] or "pending" for r in dep_rows}
            if is_deadline_blocked(depends_on=depends_on, dependency_statuses=dep_statuses):
                logger.debug("Deadline task %s blocked by incomplete dependencies; skipping", name)
                continue

        # Step 3: compute days remaining and look for unfired threshold
        days_remaining = compute_days_remaining(target_date=target_date)
        threshold = find_unfired_threshold(
            days_remaining=days_remaining,
            alert_thresholds=alert_thresholds,
            fired_thresholds=fired_thresholds,
        )

        if threshold is None:
            continue

        # Step 4a: a job-mode deadline (e.g. a document deadline) runs its named
        # deterministic job with the threshold context and starts no session.
        if row["dispatch_mode"] == _DISPATCH_MODE_JOB:
            if admission_refusal is not None:
                refusal = await admission_refusal()
                if refusal is not None:
                    logger.info(
                        "Scheduler: stopping deadline admission before %r — %s", name, refusal
                    )
                    break
            try:
                job_args = _jsonb_to_dict(row["job_args"], context=f"deadline {name!r}")
                await dispatch_fn(
                    job_name=row["job_name"],
                    job_args={
                        **(job_args or {}),
                        "deadline": {
                            "target_date": target_date.isoformat(),
                            "days_remaining": days_remaining,
                            "fired_threshold": threshold,
                            "deadline_status": current_status,
                            "all_thresholds": alert_thresholds,
                        },
                    },
                    trigger_source=f"deadline:{name}",
                )
            except DeadlineDispatchDeferred as exc:
                # The job declined to act yet (e.g. owner quiet hours): retry next tick.
                logger.info("Deadline job %s deferred: %s", name, exc)
                if metrics is not None:
                    metrics.record_task_dispatched(
                        butler=stagger_key or "unknown", task_name=name, outcome="deferred"
                    )
                continue
            except (Exception, asyncio.CancelledError) as exc:
                raise_if_task_cancelled(exc)
                logger.exception("Failed to dispatch deadline job: %s", name)
                if metrics is not None:
                    metrics.record_task_dispatched(
                        butler=stagger_key or "unknown", task_name=name, outcome="failure"
                    )
                continue
            dispatched += 1
            if metrics is not None:
                metrics.record_task_dispatched(
                    butler=stagger_key or "unknown", task_name=name, outcome="success"
                )
            logger.info("Dispatched deadline job: %s (days_remaining=%d)", name, days_remaining)
            await _record_deadline_threshold(
                pool,
                task_id=task_id,
                current_status=current_status,
                fired_thresholds=fired_thresholds,
                threshold=threshold,
                now=now,
            )
            continue

        # Step 4: dispatch the deadline prompt
        prompt = row["prompt"] or f"Deadline approaching: {name}"
        task_complexity = _parse_complexity_from_db_row(row)
        augmented_prompt = build_deadline_prompt_context(
            original_prompt=prompt,
            target_date=target_date,
            days_remaining=days_remaining,
            fired_threshold=threshold,
            deadline_status=current_status,
            all_thresholds=alert_thresholds,
        )
        # Prepend seasonal context when active seasons exist (mirrors cron pass behaviour).
        augmented_prompt = _prepend_seasonal_context(augmented_prompt, active_seasons)
        if admission_refusal is not None:
            refusal = await admission_refusal()
            if refusal is not None:
                logger.info("Scheduler: stopping deadline admission before %r — %s", name, refusal)
                break
        # Provider allowance exhausted for every model this task could use: leave the
        # threshold unfired so the next tick after the reset dispatches it.
        if await allowance_deferral(pool, butler_name or "", task_complexity) is not None:
            logger.info("Deadline task %s deferred: provider allowance exhausted", name)
            if metrics is not None:
                metrics.record_task_dispatched(
                    butler=stagger_key or "unknown", task_name=name, outcome="skipped_allowance"
                )
            continue
        try:
            await dispatch_fn(
                prompt=augmented_prompt,
                trigger_source=f"deadline:{name}",
                complexity=task_complexity,
            )
            dispatched += 1
            if metrics is not None:
                metrics.record_task_dispatched(
                    butler=stagger_key or "unknown",
                    task_name=name,
                    outcome="success",
                )
            logger.info("Dispatched deadline task: %s (days_remaining=%d)", name, days_remaining)
        except (Exception, asyncio.CancelledError) as exc:
            raise_if_task_cancelled(exc)
            logger.exception("Failed to dispatch deadline task: %s", name)
            if metrics is not None:
                metrics.record_task_dispatched(
                    butler=stagger_key or "unknown",
                    task_name=name,
                    outcome="failure",
                )
            # Skip threshold recording on dispatch failure to preserve retry semantics
            continue

        # Step 5: record the fired threshold and update status
        await _record_deadline_threshold(
            pool,
            task_id=task_id,
            current_status=current_status,
            fired_thresholds=fired_thresholds,
            threshold=threshold,
            now=now,
        )

    return evaluated, dispatched


async def _record_deadline_threshold(
    pool: asyncpg.Pool,
    *,
    task_id: Any,
    current_status: str,
    fired_thresholds: list[dict[str, Any]],
    threshold: dict[str, Any],
    now: datetime,
) -> None:
    """Mark ``threshold`` fired and advance the deadline status after a dispatch."""
    from butlers.core.temporal.deadlines import compute_next_deadline_status

    new_status = compute_next_deadline_status(
        current_status=current_status,
        fired_threshold=threshold,
    )
    # Bind fired_thresholds as a plain list, NOT a json.dumps() string: see
    # _list_to_jsonb's docstring (bu-xfcpf). No ::jsonb cast in the SQL
    # either -- Postgres infers the parameter's jsonb type from the target
    # column.
    await pool.execute(
        """
        UPDATE scheduled_tasks
        SET deadline_status = $2, fired_thresholds = $3,
            last_run_at = $4, updated_at = now()
        WHERE id = $1
        """,
        task_id,
        new_status,
        _list_to_jsonb([*fired_thresholds, threshold]),
        now,
    )
