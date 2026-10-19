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
import weakref
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any


class _PoolState:
    """The lock for one daemon pool and the number of ticks running or waiting on it."""

    __slots__ = ("lock", "pending", "__weakref__")

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.pending = 0


# One state per pool; a butler daemon owns exactly one pool. Weak keys, so a closed pool's
# state is never inherited by a later object that reuses its ``id()``.
_states: weakref.WeakKeyDictionary[Any, _PoolState] = weakref.WeakKeyDictionary()
# The pools whose lock the current task already holds, so a guarded call made from inside
# :func:`run_forced_tick` does not deadlock on its own lock.
_held: ContextVar[frozenset[int]] = ContextVar("tick_guard_held", default=frozenset())


# Pools that cannot be weakly referenced (plain ``object()`` sentinels) fall back to an
# id-keyed entry. Real asyncpg pools take the weak path above.
_unweakable_states: dict[int, _PoolState] = {}


def _state_for(pool: Any) -> _PoolState:
    try:
        state = _states.get(pool)
        if state is None:
            state = _states[pool] = _PoolState()
    except TypeError:
        state = _unweakable_states.setdefault(id(pool), _PoolState())
    return state


def tick_in_progress(pool: Any) -> bool:
    """Whether a tick is running or waiting for this daemon's lock."""
    return _state_for(pool).pending > 0


def exclusive_tick[**P, R](fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
    """Serialize calls of ``fn(pool, ...)`` per pool; waiters queue in arrival order."""

    @functools.wraps(fn)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        pool = args[0] if args else kwargs["pool"]
        key = id(pool)
        if key in _held.get():
            return await fn(*args, **kwargs)
        state = _state_for(pool)
        state.pending += 1
        try:
            async with state.lock:
                token = _held.set(_held.get() | {key})
                try:
                    return await fn(*args, **kwargs)
                finally:
                    _held.reset(token)
        finally:
            state.pending -= 1

    return wrapper


async def run_forced_tick(pool: Any, run: Callable[[], Awaitable[int]]) -> dict[str, Any]:
    """Run a forced tick unless a tick is already running or waiting.

    Returns ``{"dispatched": n}`` after running, or ``{"status": "busy", "dispatched": 0,
    "message": ...}`` when another tick is running or waiting. The ran reply carries no
    ``status`` key; callers detect busy by ``status == "busy"``.
    """
    state = _state_for(pool)
    if state.pending > 0:
        return {
            "status": "busy",
            "dispatched": 0,
            "message": "a scheduler tick is already running; try again when it finishes",
        }
    # pending == 0 means the lock is free and has no waiters, so this cannot queue.
    state.pending += 1
    try:
        async with state.lock:
            token = _held.set(_held.get() | {id(pool)})
            try:
                dispatched = await run()
            finally:
                _held.reset(token)
    finally:
        state.pending -= 1
    return {"dispatched": dispatched}
