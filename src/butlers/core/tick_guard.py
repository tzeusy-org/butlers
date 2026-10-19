"""At most one scheduler ``tick()`` per daemon at a time (bu-jlrqju).

The daemon's scheduler loop and the forced-tick paths (the core ``tick`` MCP tool and
``POST /api/butlers/{name}/tick``) all call ``butlers.core.scheduler.tick``. The occurrence
claim inside ``tick`` stops one *occurrence* running twice, but not two different due jobs
running concurrently, for example the daily and the urgent insight delivery cycles, which
would both send the same candidate.

Every ``tick`` call takes one lock per daemon pool. The loop waits for a forced tick to
finish. A forced tick does not queue behind a running tick: :func:`run_forced_tick` reports
``busy`` and runs nothing, because a tick can take minutes and the API times out first.

"Running" includes ticks *waiting* for the lock. ``asyncio.Lock.locked()`` is False in the
hand-off window between one holder's release and the next waiter's resumption, and a new
acquirer queues behind that waiter, so ``locked()`` alone would let a forced tick queue
instead of reporting busy (and, when the calling session holds the only spawner slot, wait
on a loop tick that is itself waiting on that session). Each pool therefore counts its
running plus waiting ticks.
"""

from __future__ import annotations

import asyncio
import functools
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any


class _PoolState:
    """The lock for one daemon pool and the number of ticks running or waiting on it."""

    __slots__ = ("lock", "pending")

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.pending = 0


# One entry per pool id, present only while a tick is running or waiting on that pool. Every
# running or waiting tick holds a reference to its pool, so the id cannot be reused while the
# entry exists, and the entry is deleted when the count returns to 0 (the lock is then free
# with no waiters), so nothing is inherited by a later pool and the dict cannot grow. The id
# key works for any pool object: asyncpg pools cannot be weakly referenced.
_states: dict[int, _PoolState] = {}
# The pools whose lock the current task already holds, so a guarded call made from inside
# :func:`run_forced_tick` does not deadlock on its own lock.
_held: ContextVar[frozenset[int]] = ContextVar("tick_guard_held", default=frozenset())


def _enter(pool: Any) -> _PoolState:
    state = _states.get(id(pool))
    if state is None:
        state = _states[id(pool)] = _PoolState()
    state.pending += 1
    return state


def _leave(pool: Any, state: _PoolState) -> None:
    state.pending -= 1
    if state.pending == 0:
        _states.pop(id(pool), None)


def tick_in_progress(pool: Any) -> bool:
    """Whether a tick is running or waiting for this daemon's lock."""
    return id(pool) in _states


def exclusive_tick[**P, R](fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
    """Serialize calls of ``fn(pool, ...)`` per pool; waiters queue in arrival order."""

    @functools.wraps(fn)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        pool = args[0] if args else kwargs["pool"]
        key = id(pool)
        if key in _held.get():
            return await fn(*args, **kwargs)
        state = _enter(pool)
        try:
            async with state.lock:
                token = _held.set(_held.get() | {key})
                try:
                    return await fn(*args, **kwargs)
                finally:
                    _held.reset(token)
        finally:
            _leave(pool, state)

    return wrapper


BUSY_MESSAGE = "scheduled work is already running on this butler; try again when it finishes"


async def _run_if_idle[T](pool: Any, run: Callable[[], Awaitable[T]]) -> tuple[bool, T | None]:
    """Run ``run`` holding this pool's lock unless a tick is running or waiting.

    Returns ``(True, result)`` after running, or ``(False, None)`` without running.
    """
    if tick_in_progress(pool):
        return False, None
    # No entry means the lock is free and has no waiters, so this cannot queue.
    state = _enter(pool)
    try:
        async with state.lock:
            token = _held.set(_held.get() | {id(pool)})
            try:
                return True, await run()
            finally:
                _held.reset(token)
    finally:
        _leave(pool, state)


async def run_forced_tick(pool: Any, run: Callable[[], Awaitable[int]]) -> dict[str, Any]:
    """Run a forced tick unless the guard is held by a tick or a manual dispatch.

    Returns ``{"dispatched": n}`` after running, or ``{"status": "busy", "dispatched": 0,
    "message": ...}`` when another tick is running or waiting, or a manual schedule
    trigger or Chronicler refresh holds the guard. The ran reply carries no
    ``status`` key; callers detect busy by ``status == "busy"``.
    """
    ran, dispatched = await _run_if_idle(pool, run)
    if not ran:
        return {"status": "busy", "dispatched": 0, "message": BUSY_MESSAGE}
    return {"dispatched": dispatched}


def exclusive_manual_dispatch[**P, R](
    pool_of: Callable[P, Any], busy: Callable[P, R]
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """Guard a manual path that dispatches scheduled work outside ``tick()``.

    The decorated coroutine runs holding the pool's tick lock, so it cannot overlap a
    tick (the double send the guard exists to prevent). If a tick is running or waiting it
    is not run at all and ``busy(*args, **kwargs)`` is returned: waiting could deadlock
    against the one-slot spawner semaphore, the same hazard as a forced tick.
    """

    def decorate(fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        @functools.wraps(fn)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            pool = pool_of(*args, **kwargs)
            ran, result = await _run_if_idle(pool, lambda: fn(*args, **kwargs))
            return result if ran else busy(*args, **kwargs)  # type: ignore[return-value]

        return wrapper

    return decorate
