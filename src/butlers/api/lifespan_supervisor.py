"""Named supervision for dashboard app-lifespan background loops.

The dashboard API lifespan (``butlers.api.app.lifespan``) starts several
expected-infinite background loops via ``asyncio.create_task()`` -- secrets
lifecycle scanning, model verification, the fleet-events NOTIFY bridge, and
so on. Each of those loops already owns its own per-tick fault isolation
(catch a single bad iteration, log, keep looping); this module is the outer
safety net for the case that isolation doesn't cover: the task's coroutine
itself unexpectedly *returns* (breaking the "runs forever" invariant) or
raises past its own internal handling.

Governing intent: an expected-infinite loop returning or crashing is an
OPERATIONAL EVENT that must be logged and recovered from, not a task that
silently vanishes from ``asyncio.all_tasks()``. Shutdown cancellation is the
one and only normal, non-restarting termination -- everything else restarts
with bounded backoff.

This module intentionally does NOT add a generic heartbeat, a scheduler, or
a task-health status table. It supervises restart/shutdown semantics only;
it has no opinion on what the wrapped loop actually does.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

# Mirrors the bridge_manager.py restart-backoff shape (initial/max/multiplier)
# used for the WhatsApp bridge subprocess -- same "don't busy-loop a crashing
# background loop" problem, one level up (asyncio.Task instead of subprocess).
DEFAULT_INITIAL_BACKOFF_S = 5.0
DEFAULT_MAX_BACKOFF_S = 300.0
DEFAULT_BACKOFF_MULTIPLIER = 2.0

LoopFactory = Callable[[], Awaitable[None]]
SleepFn = Callable[[float], Awaitable[None]]


async def _supervise(
    name: str,
    coro_factory: LoopFactory,
    *,
    initial_backoff_s: float,
    max_backoff_s: float,
    backoff_multiplier: float,
    sleep_fn: SleepFn,
) -> None:
    """Run ``coro_factory()`` repeatedly, restarting on unexpected return or
    exception with bounded exponential backoff.

    Cancellation (shutdown) is the sole normal termination: it propagates
    immediately without logging a restart and without sleeping, so
    ``task.cancel(); await task`` in the lifespan shutdown path returns
    promptly.
    """
    backoff = initial_backoff_s
    while True:
        try:
            await coro_factory()
        except asyncio.CancelledError:
            logger.info("lifespan loop %r cancelled (shutdown); not restarting", name)
            raise
        except Exception:
            logger.exception(
                "lifespan loop %r crashed unexpectedly; restarting in %.1fs", name, backoff
            )
        else:
            logger.error(
                "lifespan loop %r returned unexpectedly (expected to run forever); "
                "restarting in %.1fs",
                name,
                backoff,
            )

        try:
            await sleep_fn(backoff)
        except asyncio.CancelledError:
            logger.info(
                "lifespan loop %r cancelled during restart backoff (shutdown); not restarting",
                name,
            )
            raise

        backoff = min(backoff * backoff_multiplier, max_backoff_s)


def supervise_lifespan_loop(
    name: str,
    coro_factory: LoopFactory,
    *,
    initial_backoff_s: float = DEFAULT_INITIAL_BACKOFF_S,
    max_backoff_s: float = DEFAULT_MAX_BACKOFF_S,
    backoff_multiplier: float = DEFAULT_BACKOFF_MULTIPLIER,
    sleep_fn: SleepFn = asyncio.sleep,
) -> asyncio.Task:
    """Wrap a lifespan background loop in named restart supervision.

    ``coro_factory`` must be a zero-argument callable that returns a *fresh*
    coroutine on every call -- a coroutine object can only be awaited once,
    so a restart needs a new one. Callers pass a closure, e.g.::

        supervise_lifespan_loop(
            "secrets_lifecycle",
            lambda: run_secrets_lifecycle_loop(get_db_manager(), interval_s=scan_interval_s),
        )

    Returns the single supervising ``asyncio.Task`` (named ``name`` via the
    ``asyncio.Task(name=...)`` constructor argument for observability). The
    caller owns this task exactly like any other lifespan task: keep a
    strong reference to it and cancel + await it during shutdown.

    ``sleep_fn`` is a test seam only -- production callers never pass it. It
    defaults to ``asyncio.sleep`` so restart backoff actually waits; tests
    can substitute a fake to control backoff deterministically without
    racing wall-clock time.
    """
    return asyncio.create_task(
        _supervise(
            name,
            coro_factory,
            initial_backoff_s=initial_backoff_s,
            max_backoff_s=max_backoff_s,
            backoff_multiplier=backoff_multiplier,
            sleep_fn=sleep_fn,
        ),
        name=name,
    )
