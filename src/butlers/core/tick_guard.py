"""At most one scheduler ``tick()`` per daemon at a time (bu-jlrqju).

The daemon's scheduler loop and the forced-tick paths (the core ``tick`` MCP tool and
``POST /api/butlers/{name}/tick``) all call ``butlers.core.scheduler.tick``. The occurrence
claim inside ``tick`` stops one *occurrence* running twice, but not two different due jobs
running concurrently, for example the daily and the urgent insight delivery cycles, which
would both send the same candidate.

Every ``tick`` call takes one lock per daemon pool. The loop waits for a forced tick to
finish. A forced tick does not queue behind a running tick: :func:`run_forced_tick` reports
``busy`` and runs nothing, because a tick can take minutes and the API times out first.
"""

from __future__ import annotations

import asyncio
import functools
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any

# One lock per pool; a butler daemon owns exactly one pool.
_locks: dict[int, asyncio.Lock] = {}
# The pool id whose lock the current task already holds, so a guarded call made from inside
# :func:`run_forced_tick` does not deadlock on its own lock.
_held: ContextVar[frozenset[int]] = ContextVar("tick_guard_held", default=frozenset())


def _lock_for(pool: Any) -> asyncio.Lock:
    return _locks.setdefault(id(pool), asyncio.Lock())


def tick_in_progress(pool: Any) -> bool:
    """Whether a tick currently holds this daemon's lock."""
    return _lock_for(pool).locked()


def exclusive_tick[**P, R](fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
    """Serialize calls of ``fn(pool, ...)`` per pool; waiters queue in arrival order."""

    @functools.wraps(fn)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        pool = args[0] if args else kwargs["pool"]
        key = id(pool)
        if key in _held.get():
            return await fn(*args, **kwargs)
        async with _lock_for(pool):
            token = _held.set(_held.get() | {key})
            try:
                return await fn(*args, **kwargs)
            finally:
                _held.reset(token)

    return wrapper


async def run_forced_tick(pool: Any, run: Callable[[], Awaitable[int]]) -> dict[str, Any]:
    """Run a forced tick unless one is already running.

    Returns ``{"status": "ran", "dispatched": n}``, or ``{"status": "busy",
    "dispatched": 0, "message": ...}`` when a tick already holds the lock.
    """
    lock = _lock_for(pool)
    if lock.locked():
        return {
            "status": "busy",
            "dispatched": 0,
            "message": "a scheduler tick is already running; try again when it finishes",
        }
    async with lock:
        token = _held.set(_held.get() | {id(pool)})
        try:
            dispatched = await run()
        finally:
            _held.reset(token)
    return {"status": "ran", "dispatched": dispatched}
