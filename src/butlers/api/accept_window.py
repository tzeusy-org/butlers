"""Run a slow butler call past the request that started it, answering 202 when it outlasts one.

A butler tool that runs scheduled work (``schedule_trigger``, ``tick``) awaits whole LLM sessions,
which can outlast the dashboard's 15 s request budget. Holding the HTTP request open would make a
call that did start read as a failed one. ``run_within_window`` runs the call as its own task and
waits only a bounded window: a call that finishes inside it is mapped to a status as usual, and one
that does not keeps running with a strong reference here while the caller answers 202.

The coroutine passed in owns its audit trail, so an outcome that arrives after the request ended is
still recorded exactly once.
"""

from __future__ import annotations

import asyncio
import functools
import logging
from collections.abc import Coroutine
from typing import Any

from fastapi import HTTPException

logger = logging.getLogger(__name__)

#: Strong references so a call that outlives its request is not garbage collected.
TASKS: set[asyncio.Task] = set()

#: Calls whose request already answered 202: only these log "after its request ended".
_DETACHED: set[asyncio.Task] = set()


def _task_done(log: logging.Logger, task: asyncio.Task) -> None:
    TASKS.discard(task)
    detached = task in _DETACHED
    _DETACHED.discard(task)
    label = task.get_name()
    if task.cancelled():
        # Loop teardown cancels a pending call; it never reached its audit write.
        log.warning("Background call %s was cancelled; its outcome was not audited", label)
        return
    exc = task.exception()
    if exc is None:
        return
    if isinstance(exc, HTTPException):
        # The coroutine already audited it. A request still waiting re-raises it to the caller;
        # only one that answered 202 has nobody left to tell.
        if detached:
            log.info("Background call %s finished with %r after its request ended", label, exc)
        return
    log.error("Background call %s failed unexpectedly", label, exc_info=exc)


async def run_within_window(
    call: Coroutine[Any, Any, Any],
    window_s: float,
    *,
    name: str,
    log: logging.Logger = logger,
) -> tuple[asyncio.Task, bool]:
    """Start ``call`` as a task and wait up to ``window_s``; return ``(task, finished)``.

    ``name`` labels the task for the log lines about its end (put the butler and, for a schedule,
    its id in it); ``log`` is the caller's logger. ``task.result()`` re-raises the call's
    exception when ``finished``. When not finished the task keeps running in the background and
    is marked detached, so only then does the log say its request ended.
    """
    task = asyncio.create_task(call, name=name)
    TASKS.add(task)
    task.add_done_callback(functools.partial(_task_done, log))
    done, _pending = await asyncio.wait({task}, timeout=window_s)
    if not done:
        _DETACHED.add(task)
    return task, bool(done)
