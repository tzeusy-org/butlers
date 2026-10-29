"""Bounded probing of state that offers no completion notification."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable


async def poll_until(probe: Callable[[], Awaitable[bool]], *, interval_s: float) -> None:
    """Return once ``probe()`` is true, re-probing every ``interval_s`` seconds.

    For state with no event to await, such as PostgreSQL lock tables, a peer
    socket or a stream's internal buffer. Callers bound the wait themselves,
    normally with ``asyncio.timeout``.
    """
    while True:
        if await probe():
            return
        await asyncio.sleep(interval_s)
