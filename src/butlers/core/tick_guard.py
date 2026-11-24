"""The shared scheduler tick slot for a daemon."""

from __future__ import annotations

import asyncio
from typing import Any


def scheduler_tick_gate(daemon: Any) -> asyncio.Semaphore:
    """Return the same single-slot semaphore to both tick entry points."""
    gate = vars(daemon).get("_scheduler_tick_gate")
    if gate is None:
        gate = daemon._scheduler_tick_gate = asyncio.Semaphore(1)
    return gate
