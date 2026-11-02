"""``tick()``: the scheduler's serial claim-and-dispatch orchestrator."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import asyncpg
from opentelemetry import trace

from butlers.core.metrics import ButlerMetrics
from butlers.core.scheduler.chain_pass import _tick_event_chain_pass
from butlers.core.scheduler.claim import (
    _butler_dispatch_gated,
    _defer_cron_for_allowance,
    _qa_admission_refusal,
)
from butlers.core.scheduler.deadline_pass import _tick_deadline_pass
from butlers.core.scheduler.deferred_pass import _tick_deferred_notification_pass
from butlers.core.scheduler.prompt import (
    _continuity_block_for_task,
    _prepare_scheduled_prompt,
    _prepend_seasonal_context,
    _run_completion_hook,
)
from butlers.core.scheduler.rows import (
    _DISPATCH_MODE_JOB,
    _DISPATCH_MODE_PROMPT,
    _jsonb_to_dict,
    _parse_complexity_from_db_row,
    _result_to_jsonb,
)
from butlers.core.scheduler.timing import (
    _DEFAULT_MAX_STAGGER_SECONDS,
    _DEFAULT_SCHEDULE_TIMEZONE,
    _effective_schedule_timezone,
    _next_run,
)

logger = logging.getLogger("butlers.core.scheduler")

# Kept beside tick(): the reviewed host-policy selector scan resolves this alias per module.
_QA_BUTLER_NAME = "qa"


async def tick(
    pool: asyncpg.Pool,
    dispatch_fn,
    *,
    stagger_key: str | None = None,
    max_stagger_seconds: int = _DEFAULT_MAX_STAGGER_SECONDS,
    metrics: ButlerMetrics | None = None,
    butler_name: str | None = None,
    notify_fn=None,
    prompt_hooks: dict[str, Any] | None = None,
    completion_hooks: dict[str, Any] | None = None,
    eligibility_pool: asyncpg.Pool | None = None,
    default_timezone: str = _DEFAULT_SCHEDULE_TIMEZONE,
) -> int:
    """Evaluate due tasks and dispatch them.

    Passes executed in order:
      1. Deadline evaluation pass — fire due threshold alerts, expire past-target tasks.
      2. Cron dispatch pass — standard cron-scheduled tasks.
      3. Event chain trigger detection pass — fire chains whose triggers have occurred.
      4. Deferred notification flush pass — deliver/expire deferred notifications.

    Queries ``scheduled_tasks`` WHERE ``enabled=true AND next_run_at <= now()``
    for cron tasks.  Deadline tasks are handled separately.
    For each due cron task, optionally prepares the prompt through a
    task-specific deterministic hook, then calls
    ``dispatch_fn(prompt=..., trigger_source="schedule:<task-name>")``.
    After dispatch, updates ``next_run_at``, ``last_run_at``, and ``last_result``.
    If dispatch fails, logs the error and stores the error in ``last_result``,
    but continues to the next task.

    Creates a ``butler.tick`` span with attributes:
      - ``tasks_due`` — count of due cron tasks
      - ``tasks_run`` — count of successfully dispatched cron tasks
      - ``deadlines_evaluated`` — count of deadline tasks evaluated
      - ``chains_fired`` — count of event chains fired
      - ``deferred_flushed`` — count of deferred notifications delivered/expired

    When *butler_name* is provided, ``get_active_seasons()`` is queried once
    per tick.  If active seasonal periods exist, their names are prepended to
    the prompt text as a ``[Seasonal context: ...]`` prefix for every
    prompt-mode dispatch.  This allows butlers to adjust behaviour based on
    which seasonal periods are currently active without requiring any change to
    the ``dispatch_fn`` signature.  Job-mode dispatches are unaffected (prompt
    is not used for job dispatch).

    Args:
        pool: asyncpg connection pool.
        dispatch_fn: Async callable matching ``Spawner.trigger`` signature.
        butler_name: Butler instance name used to query ``seasonal_periods``.
            When ``None`` (default), seasonal context injection is skipped.
        notify_fn: Optional async callable used by the deferred notification
            flush pass to deliver stored ``notify.v1`` envelopes via the
            standard notify pipeline.  Signature::

                async def notify_fn(envelope: dict) -> None: ...

            When ``None``, due notifications are left ``pending`` and retried
            on the next tick.  The scheduler loop in the daemon wires this to
            the butler's own notify delivery path.
        prompt_hooks: Optional mapping of ``task_name → callable`` invoked
            immediately before a prompt-mode task dispatches.  Signature::

                def hook(
                    *, task_name: str, prompt: str, run_at: datetime, timezone: str
                ) -> str: ...

            Hooks receive the same tick timestamp and effective schedule
            timezone later supplied to completion hooks. Hook failures are
            handled as dispatch failures, so no unbound prompt reaches the LLM.
        completion_hooks: Optional mapping of ``task_name → async callable``
            invoked after a prompt-mode task dispatches (regardless of
            success/failure).  Signature::

                async def hook(
                    *, task_name: str, result: Any, run_at: datetime, timezone: str
                ) -> None: ...

            Hooks are called with the task name, the dispatch result (a
            SpawnerResult or None), the wall-clock time the tick fired, and the
            same effective timezone used to prepare the prompt. Existing hooks
            with the original three-keyword signature remain supported; the
            scheduler passes ``timezone`` only when the callable accepts it.
            Hook exceptions are logged and swallowed — a hook failure never
            blocks task progression or advances to the next tick.
            Job-mode tasks do NOT trigger completion hooks.
        eligibility_pool: Optional asyncpg pool scoped to the Switchboard schema
            (``butler_registry``).  When provided alongside *butler_name*, the
            butler's eligibility is checked via the canonical
            :func:`resolve_routing_target` accessor before any dispatch.  If the
            butler is paused/quarantined (or stale), the deadline- and
            cron-dispatch passes are SKIPPED — due ticks are dropped, not
            queued, so a paused butler does not stampede catch-up dispatches on
            resume.  ``next_run_at`` is left untouched while gated, so the
            schedule resumes naturally on the next eligible tick.  When ``None``
            (e.g. unit tests, or a context without a registry), no gating is
            applied and all passes run as before.  Ignored for QA, which reads
            ``public.qa_local_schedule_policy()`` through *pool* (its own
            runtime-role pool) before the passes and again immediately before
            every deadline or cron dispatch; any refusal fails closed.

    Returns:
        The number of tasks successfully dispatched (cron + deadline).
    """
    from butlers.core.seasonal import get_active_seasons as _get_active_seasons

    tracer = trace.get_tracer("butlers")
    with tracer.start_as_current_span("butler.tick") as span:
        now = datetime.now(UTC)

        # ------------------------------------------------------------------
        # Seasonal context — queried once per tick, injected into ALL dispatches
        # (both deadline pass and cron pass).  Must be queried before Pass 1 so
        # that deadline dispatches receive the same seasonal prefix as cron tasks.
        # ------------------------------------------------------------------
        active_seasons: list[dict[str, Any]] = []
        if butler_name:
            try:
                active_seasons = await _get_active_seasons(pool, butler_name)
            except Exception:
                logger.warning(
                    "Failed to query active seasons for butler %r; skipping seasonal context",
                    butler_name,
                    exc_info=True,
                )

        # ------------------------------------------------------------------
        # Eligibility gate — a paused/quarantined (or stale) butler must NOT
        # fire its scheduled deadline/cron ticks.  We consult the canonical
        # Switchboard registry accessor (resolve_routing_target) so this matches
        # the routing path's notion of "eligible".  When gated, both dispatch
        # passes are SKIPPED outright (no catch-up queue): next_run_at is left
        # untouched so the schedule resumes naturally once the butler is
        # un-paused.  Event-chain bookkeeping (Pass 3) and deferred-notification
        # flush (Pass 4) still run — neither spawns the gated butler.
        #
        # QA instead reads its own policy projection through its runtime-role
        # pool, never the eligibility pool, and re-reads it before each
        # dispatch so an owner hold committed mid-tick stops later admissions.
        # ------------------------------------------------------------------
        admission_refusal: Callable[[], Awaitable[str | None]] | None = None
        if butler_name == _QA_BUTLER_NAME:

            async def admission_refusal() -> str | None:
                return await _qa_admission_refusal(pool)

            dispatch_gate_reason = await admission_refusal()
        else:
            dispatch_gate_reason = await _butler_dispatch_gated(eligibility_pool, butler_name)
        span.set_attribute("dispatch_gated", dispatch_gate_reason is not None)

        if dispatch_gate_reason is not None:
            logger.info(
                "Scheduler: skipping deadline/cron dispatch for butler %r — %s",
                butler_name,
                dispatch_gate_reason,
            )
            deadlines_evaluated = 0
            deadline_dispatched = 0
            span.set_attribute("deadlines_evaluated", 0)
            span.set_attribute("deadline_dispatched", 0)
        else:
            # --- Pass 1: Deadline evaluation (before cron dispatch) ---
            deadlines_evaluated, deadline_dispatched = await _tick_deadline_pass(
                pool,
                dispatch_fn,
                now=now,
                stagger_key=stagger_key,
                max_stagger_seconds=max_stagger_seconds,
                metrics=metrics,
                active_seasons=active_seasons,
                admission_refusal=admission_refusal,
                butler_name=butler_name,
            )
            span.set_attribute("deadlines_evaluated", deadlines_evaluated)
            span.set_attribute("deadline_dispatched", deadline_dispatched)

        # --- Pass 2: Cron dispatch ---
        # Deadline tasks were handled in pass 1; select only cron tasks.
        # When the butler is gated (paused/quarantined/stale), skip the cron
        # fetch entirely so no rows are dispatched and next_run_at is preserved.
        rows: list[asyncpg.Record] = []
        if dispatch_gate_reason is None:
            cron_filter = "AND COALESCE(task_type, 'cron') = 'cron'"
            _budget_col_select = ", max_token_budget"
            _until_at_select = ", until_at"
            _continuity_col_select = ", continuity"
            rows = await pool.fetch(
                f"""
                SELECT id, name, cron, dispatch_mode, prompt, job_name, job_args,
                       complexity, timezone, next_run_at{_until_at_select}{_budget_col_select}
                       {_continuity_col_select}
                FROM scheduled_tasks
                WHERE enabled = true
                  {cron_filter}
                  AND next_run_at <= $1
                ORDER BY next_run_at
                """,
                now,
            )

        tasks_due = len(rows)
        span.set_attribute("tasks_due", tasks_due)

        dispatched = 0
        for row in rows:
            task_id = row["id"]
            original_next_run_at = row["next_run_at"]
            name = row["name"]
            prompt = row["prompt"]
            cron = row["cron"]
            dispatch_mode = row["dispatch_mode"]
            job_name = row["job_name"]
            job_args = _jsonb_to_dict(row["job_args"], context=f"scheduled_tasks[{name}]")
            task_complexity = _parse_complexity_from_db_row(row)
            max_token_budget: int | None = row["max_token_budget"]
            continuity_enabled: bool = bool(row["continuity"])

            # Effective cron timezone: a non-UTC per-row value overrides; the
            # default 'UTC'/NULL sentinel follows the owner's general timezone.
            # A NULL/UTC value retains the owner-timezone sentinel behavior.
            task_timezone = _effective_schedule_timezone(row.get("timezone"), default_timezone)

            until_at = row["until_at"]

            # --- Compute next_run_at before claiming ---
            # We need it in the claim UPDATE so the DB transition is atomic.
            next_run_at = _next_run(
                cron,
                timezone=task_timezone,
                stagger_key=stagger_key,
                max_stagger_seconds=max_stagger_seconds,
            )
            should_auto_disable = until_at is not None and next_run_at > until_at

            # Recheck before the claim so a refused task keeps its due
            # next_run_at and resumes exactly once after the hold lifts.
            if admission_refusal is not None:
                refusal = await admission_refusal()
                if refusal is not None:
                    logger.info("Scheduler: stopping cron admission before %r — %s", name, refusal)
                    break

            # Provider allowance exhausted for every model this task could run on:
            # defer to the reset instead of burning a claim on a doomed dispatch.
            if (
                dispatch_mode == _DISPATCH_MODE_PROMPT
                and not should_auto_disable
                and await _defer_cron_for_allowance(
                    pool,
                    task_id=task_id,
                    name=name,
                    original_next_run_at=original_next_run_at,
                    butler_name=butler_name,
                    complexity=task_complexity,
                    metrics=metrics,
                    stagger_key=stagger_key,
                )
            ):
                continue

            # --- Claim this occurrence atomically (idempotency guard) ---
            # Use next_run_at as an optimistic version field.  The UPDATE succeeds
            # only when next_run_at still matches the value we read, meaning no
            # concurrent tick (MCP tick tool, force-tick API, or a rapid second
            # loop iteration) has already claimed this occurrence.
            # If the claim returns nothing, skip silently — the occurrence was
            # handled elsewhere.
            if should_auto_disable:
                claim_row = await pool.fetchrow(
                    """
                    UPDATE scheduled_tasks
                    SET enabled = false, next_run_at = NULL, updated_at = now()
                    WHERE id = $1 AND enabled AND next_run_at IS NOT DISTINCT FROM $2
                    RETURNING id, dashboard_attention.final_claim(
                        current_schema(), id, $2
                    ) AS attention_claim
                    """,
                    task_id,
                    original_next_run_at,
                )
            else:
                claim_row = await pool.fetchrow(
                    """
                    UPDATE scheduled_tasks
                    SET next_run_at = $2, updated_at = now()
                    WHERE id = $1 AND next_run_at IS NOT DISTINCT FROM $3
                    RETURNING id
                    """,
                    task_id,
                    next_run_at,
                    original_next_run_at,
                )

            if claim_row is None:
                logger.debug(
                    "Scheduled task %r already claimed by a concurrent tick; skipping",
                    name,
                )
                continue

            if should_auto_disable:
                logger.info(
                    "Scheduled task %s has passed until_at (%s); auto-disabling", name, until_at
                )

            from butlers.core.attention_urgency import AttentionOrigin, _current_origin

            attention_token = _current_origin.set(
                AttentionOrigin(
                    namespace=butler_name or stagger_key,
                    task_id=task_id,
                    original_next_run_at=original_next_run_at,
                )
            )
            result_json: str | None = None
            dispatch_result: Any = None
            try:
                if dispatch_mode == _DISPATCH_MODE_PROMPT:
                    # Prepend seasonal context to the prompt when active seasons exist.
                    # We prepend as a prompt prefix rather than passing a separate kwarg
                    # so that dispatch_fn (Spawner.trigger / _dispatch_scheduled_task)
                    # does not need to be aware of seasonal periods.
                    dispatched_prompt = _prepend_seasonal_context(prompt, active_seasons)
                    dispatched_prompt = _prepare_scheduled_prompt(
                        prompt_hooks,
                        task_name=name,
                        prompt=dispatched_prompt,
                        run_at=now,
                        timezone=task_timezone,
                    )
                    if continuity_enabled:
                        continuity_block = await _continuity_block_for_task(
                            pool,
                            butler_name=butler_name,
                            task_name=name,
                            now=now,
                        )
                        if continuity_block:
                            dispatched_prompt = f"{dispatched_prompt}\n\n{continuity_block}"
                    dispatch_kwargs: dict[str, Any] = {
                        "prompt": dispatched_prompt,
                        "trigger_source": f"schedule:{name}",
                        "complexity": task_complexity,
                    }
                    if max_token_budget is not None:
                        dispatch_kwargs["max_token_budget"] = max_token_budget
                    dispatch_result = await dispatch_fn(**dispatch_kwargs)
                    result = dispatch_result
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
                if metrics is not None:
                    metrics.record_task_dispatched(
                        butler=stagger_key or "unknown",
                        task_name=name,
                        outcome="success",
                    )
                logger.info("Dispatched scheduled task: %s", name)
            except Exception as exc:
                logger.exception("Failed to dispatch scheduled task: %s", name)
                result_json = _result_to_jsonb({"error": str(exc)})
                if metrics is not None:
                    metrics.record_task_dispatched(
                        butler=stagger_key or "unknown",
                        task_name=name,
                        outcome="failure",
                    )

            finally:
                _current_origin.reset(attention_token)

            # Fire completion hook for prompt-mode tasks (after success or failure).
            # Hooks are butler-specific post-processing (e.g. cache writes).
            # A hook exception is logged and swallowed — it never blocks task progression.
            if dispatch_mode == _DISPATCH_MODE_PROMPT:
                await _run_completion_hook(
                    completion_hooks,
                    task_name=name,
                    result=dispatch_result,
                    run_at=now,
                    timezone=task_timezone,
                )

            # Record the dispatch outcome.  next_run_at was already advanced in the
            # claim step above; only last_run_at and last_result need updating here.
            await pool.execute(
                """
                UPDATE scheduled_tasks
                SET last_run_at = $2, last_result = $3, updated_at = now()
                WHERE id = $1
                """,
                task_id,
                now,
                result_json,
            )

        span.set_attribute("tasks_run", dispatched)

        # --- Pass 3: Event chain trigger detection (after cron/deadline dispatch) ---
        chains_fired = await _tick_event_chain_pass(pool, dispatch_fn, now)
        span.set_attribute("chains_fired", chains_fired)

        # --- Pass 4: Deferred notification flush (after chain detection) ---
        deferred_flushed = await _tick_deferred_notification_pass(pool, now, notify_fn=notify_fn)
        span.set_attribute("deferred_flushed", deferred_flushed)

        return dispatched + deadline_dispatched
