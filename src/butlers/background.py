"""Background loop implementations for ButlerDaemon.

Each long-running async loop is a standalone coroutine that takes only the
references it needs.  ButlerDaemon stores the Task handles but delegates the
loop body to functions defined here.

Functions
---------
dispatch_scheduled_task    -- dispatch one cron-triggered task (deterministic or prompt)
scheduler_loop             -- cron-driven task dispatch loop
liveness_reporter_loop     -- periodic POST to Switchboard heartbeat endpoint
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from typing import Any

import httpx

from butlers.core.model_routing import Complexity
from butlers.scheduled_jobs import (
    _DETERMINISTIC_SCHEDULE_JOB_REGISTRY,
    _resolve_deterministic_schedule_job_name,
)

logger = logging.getLogger(__name__)

_DEFERRED_NOTIFY_TIMEOUT_S = 30


# ---------------------------------------------------------------------------
# Scheduled task dispatch
# ---------------------------------------------------------------------------


async def dispatch_scheduled_task(
    *,
    butler_name: str,
    pool: Any,
    spawner: Any,
    trigger_source: str,
    prompt: str | None = None,
    job_name: str | None = None,
    job_args: dict[str, Any] | None = None,
    complexity: Complexity = Complexity.MEDIUM,
    max_token_budget: int | None = None,
) -> Any:
    """Dispatch one scheduled task via deterministic jobs or prompt fallback.

    Deterministic schedules are resolved through an explicit per-butler
    job registry.  Prompt-mode schedules fall back to runtime/LLM dispatch.

    Parameters
    ----------
    butler_name:
        Name of the butler owning the schedule (used for registry lookup and logging).
    pool:
        Active asyncpg connection pool.  Required for deterministic dispatch.
    spawner:
        Active Spawner instance.  Required for prompt-mode dispatch.
    trigger_source:
        Cron trigger source string (e.g. ``"schedule:memory_consolidation"``).
    prompt:
        Prompt payload for prompt-mode dispatch.  Ignored for deterministic jobs.
    job_name:
        Explicit job name override.  When ``None``, resolved from ``trigger_source``.
    job_args:
        Keyword arguments forwarded to the deterministic job handler.
    complexity:
        Complexity hint forwarded to the spawner for prompt-mode dispatch.
    max_token_budget:
        Optional token budget forwarded to the spawner for prompt-mode dispatch.
    """
    resolved_job_name = _resolve_deterministic_schedule_job_name(
        butler_name=butler_name,
        trigger_source=trigger_source,
        job_name=job_name,
    )
    if resolved_job_name is not None:
        if pool is None:
            raise RuntimeError(
                "Deterministic scheduler dispatch requires an initialized DB pool "
                f"(butler={butler_name!r}, job_name={resolved_job_name!r})"
            )

        jobs_for_butler = _DETERMINISTIC_SCHEDULE_JOB_REGISTRY.get(butler_name, {})
        handler = jobs_for_butler.get(resolved_job_name)
        if handler is None:
            registered_jobs = ", ".join(sorted(jobs_for_butler)) or "<none>"
            raise RuntimeError(
                "Unknown deterministic scheduler job "
                f"(butler={butler_name!r}, job_name={resolved_job_name!r}). "
                f"Registered jobs: {registered_jobs}. "
                "Use prompt dispatch mode for LLM-backed schedules."
            )

        logger.debug(
            "Dispatching deterministic scheduled task "
            "(butler=%s, job_name=%s, trigger_source=%s, job_args=%s)",
            butler_name,
            resolved_job_name,
            trigger_source,
            job_args,
        )
        return await handler(pool, job_args)

    if spawner is None:
        raise RuntimeError("Scheduler dispatch requires an initialized spawner")
    if prompt is None or not prompt.strip():
        raise RuntimeError("Prompt-mode scheduler dispatch requires a non-empty prompt payload")
    return await spawner.trigger(
        prompt=prompt,
        trigger_source=trigger_source,
        complexity=complexity,
        max_token_budget=max_token_budget,
    )


# ---------------------------------------------------------------------------
# Scheduler loop
# ---------------------------------------------------------------------------


async def scheduler_loop(
    *,
    pool: Any,
    dispatch_fn: Callable[..., Coroutine[Any, Any, Any]],
    interval: int,
    butler_name: str,
    tick_fn: Callable[..., Coroutine[Any, Any, Any]],
    get_switchboard_client: Callable[[], Any],
    get_db: Callable[[], Any],
) -> None:
    """Periodically call tick() to dispatch due scheduled tasks.

    Runs as a background task for the lifetime of the butler.  Sleeps for
    ``interval`` seconds, then calls ``tick_fn()`` to evaluate and dispatch any
    due cron tasks.

    Exceptions from ``tick_fn()`` are logged and the loop continues — a single
    tick failure never breaks the loop.

    On cancellation (graceful shutdown):
    - If sleeping between ticks, the loop exits immediately.
    - If a tick_fn() call is in-progress, ``asyncio.shield()`` wraps the inner
      task so that the CancelledError interrupts only the await but the tick
      itself continues running; the loop then awaits the shielded task to let
      the in-progress tick() finish before exiting.

    Parameters
    ----------
    pool:
        Active asyncpg connection pool.
    dispatch_fn:
        Callable used by tick_fn to dispatch due tasks.
    interval:
        Sleep duration between ticks in seconds.
    butler_name:
        Butler name (used as stagger key and for logging).
    tick_fn:
        Tick implementation (normally ``butlers.core.scheduler.tick``).
    get_switchboard_client:
        Zero-argument callable that returns the current switchboard MCP client
        (or ``None``).  Called lazily per deferred-notification delivery so that
        reconnections are reflected without restarting the loop.
    get_db:
        Zero-argument callable that returns the current Database instance (or
        ``None``).  Same laziness rationale as ``get_switchboard_client``.
    """

    async def _scheduler_notify_fn(envelope: dict) -> None:
        """Deliver a deferred notify.v1 envelope via the standard notify pipeline."""
        _client = get_switchboard_client()
        _db = get_db()
        if _client is None and butler_name != "switchboard":
            raise RuntimeError(
                "Switchboard client not connected; cannot deliver deferred notification"
            )
        deliver_args: dict = {
            "source_butler": butler_name,
            "notify_request": envelope,
        }
        if _client is None and butler_name == "switchboard":
            if _db is None or _db.pool is None:
                raise RuntimeError("Database not available for deferred notification delivery")
            from butlers.tools.switchboard.notification.deliver import (
                deliver as _sw_deliver,
            )

            result = await _sw_deliver(
                _db.pool,
                source_butler=butler_name,
                notify_request=envelope,
            )
            if result.get("status") == "failed":
                raise RuntimeError(f"Deferred notification delivery failed: {result.get('error')}")
        else:
            result = await asyncio.wait_for(
                _client.call_tool("deliver", deliver_args),
                timeout=_DEFERRED_NOTIFY_TIMEOUT_S,
            )
            if result.is_error:
                error_text = str(result.content[0].text) if result.content else "Unknown error"
                raise RuntimeError(f"Deferred notification delivery failed: {error_text}")

    logger.info(
        "Scheduler loop started (tick_interval_seconds=%d) for butler %s",
        interval,
        butler_name,
    )

    try:
        while True:
            await asyncio.sleep(interval)
            tick_task = asyncio.create_task(
                tick_fn(
                    pool,
                    dispatch_fn,
                    stagger_key=butler_name,
                    butler_name=butler_name,
                    notify_fn=_scheduler_notify_fn,
                )
            )
            try:
                dispatched = await asyncio.shield(tick_task)
                logger.debug(
                    "Scheduler loop: tick() dispatched %d task(s) for butler %s",
                    dispatched,
                    butler_name,
                )
            except asyncio.CancelledError:
                # Cancellation arrived while tick() was running; let it finish.
                logger.debug("Scheduler loop: cancelled during tick(), waiting for tick to finish")
                try:
                    await tick_task
                except Exception:
                    logger.exception(
                        "Scheduler loop: in-progress tick() raised on cancellation for butler %s",
                        butler_name,
                    )
                raise
            except Exception:
                logger.exception(
                    "Scheduler loop: tick() raised an exception for butler %s; continuing",
                    butler_name,
                )
    except asyncio.CancelledError:
        logger.info("Scheduler loop cancelled for butler %s", butler_name)


# ---------------------------------------------------------------------------
# Liveness reporter loop
# ---------------------------------------------------------------------------


async def liveness_reporter_loop(
    *,
    butler_name: str,
    url: str,
    interval: int,
    butler_type_value: str,
) -> None:
    """Periodically POST to the Switchboard's heartbeat endpoint to signal liveness.

    Runs as a background task for the lifetime of every butler, including the
    switchboard itself (which heartbeats to its own dashboard endpoint).
    Sends an initial heartbeat within 5 seconds of startup, then repeats every
    ``interval`` seconds.

    Connection failures are logged at WARNING level — transient unavailability is
    expected (e.g., Switchboard not yet started) and does not break the loop.

    On cancellation (graceful shutdown), the loop exits cleanly.

    Parameters
    ----------
    butler_name:
        Name of the butler sending the heartbeat.
    url:
        Full URL of the Switchboard heartbeat endpoint.
    interval:
        Heartbeat repetition interval in seconds.
    butler_type_value:
        String value of the butler type enum (e.g. ``"staffer"``).
    """
    logger.info(
        "Liveness reporter started (heartbeat_interval_seconds=%d, url=%s) for butler %s",
        interval,
        url,
        butler_name,
    )

    payload = {"butler_name": butler_name, "type": butler_type_value}
    consecutive_404s = 0
    max_consecutive_404s = 3

    async def _post_heartbeat(client: Any, phase: str) -> bool:
        """POST one heartbeat and return whether loop should continue.

        A persistent 404 (3 consecutive) means the target service does not
        expose the Switchboard heartbeat endpoint (wrong host/port/path).
        In that case we stop retrying to avoid noisy, unproductive log spam.
        A single 404 during a dashboard restart is tolerated.
        """
        nonlocal consecutive_404s
        try:
            resp = await client.post(url, json=payload)
            if resp.status_code == 404:
                consecutive_404s += 1
                if consecutive_404s >= max_consecutive_404s:
                    logger.warning(
                        "Liveness reporter: %s heartbeat endpoint not found (404) "
                        "%d consecutive times for butler %s at %s; disabling reporter",
                        phase,
                        consecutive_404s,
                        butler_name,
                        url,
                    )
                    return False
                logger.warning(
                    "Liveness reporter: %s heartbeat got 404 for butler %s at %s "
                    "(%d/%d before disable)",
                    phase,
                    butler_name,
                    url,
                    consecutive_404s,
                    max_consecutive_404s,
                )
                return True
            consecutive_404s = 0
            resp.raise_for_status()
            logger.debug(
                "Liveness reporter: %s heartbeat sent for butler %s (status %d)",
                phase,
                butler_name,
                resp.status_code,
            )
            return True
        except Exception:
            logger.warning(
                "Liveness reporter: %s heartbeat failed for butler %s",
                phase,
                butler_name,
                exc_info=True,
            )
            return True

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            # Send initial heartbeat within 5 seconds of startup
            await asyncio.sleep(5)
            if not await _post_heartbeat(client, "initial"):
                return

            while True:
                await asyncio.sleep(interval)
                if not await _post_heartbeat(client, "periodic"):
                    return
        except asyncio.CancelledError:
            logger.info("Liveness reporter cancelled for butler %s", butler_name)
