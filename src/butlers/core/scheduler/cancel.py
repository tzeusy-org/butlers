"""Telling a real cancel of the running task from a stray ``CancelledError``."""

from __future__ import annotations

import asyncio


def raise_if_task_cancelled(exc: BaseException) -> None:
    """Re-raise ``exc`` if it is a cancel that was requested on the running task.

    A job can raise ``CancelledError`` with no such request, for example from a cancelled
    child task it awaited, or from asyncpg's race between its own timeout and an
    ``asyncio.timeout``. That is a failure of the job, and the caller handles it as one.
    ``Task.cancelling()`` counts only the ``.cancel()`` calls made on this task.
    """
    task = asyncio.current_task()
    if isinstance(exc, asyncio.CancelledError) and task is not None and task.cancelling() > 0:
        raise exc
