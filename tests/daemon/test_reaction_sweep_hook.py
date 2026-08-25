"""The scheduler loop closes out this butler's silent wakes (bu-6jv4m.8).

The correlation sweep is subscriber-local -- it reads the caller's own
``scheduled_tasks`` -- so it is hosted per-butler in the scheduler loop
rather than centrally on the Switchboard, which cannot see a sibling
schema's tasks.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from butlers import background

pytestmark = pytest.mark.unit

_real_sleep = asyncio.sleep


async def _run_one_tick(monkeypatch, *, sweep) -> None:
    """Drive exactly one full scheduler iteration, then unwind the loop."""
    sleeps = {"n": 0}

    async def _fast_sleep(_delay: float) -> None:
        sleeps["n"] += 1
        if sleeps["n"] >= 2:
            raise asyncio.CancelledError
        await _real_sleep(0)

    monkeypatch.setattr(background.asyncio, "sleep", _fast_sleep)
    monkeypatch.setattr(background, "reconcile_reaction_lifecycle", sweep)

    await background.scheduler_loop(
        pool=AsyncMock(),
        dispatch_fn=AsyncMock(),
        interval=1,
        butler_name="finance",
        tick_fn=AsyncMock(return_value=0),
        get_switchboard_client=lambda: None,
        get_db=lambda: None,
    )


async def test_each_tick_sweeps_this_butlers_own_wakes(monkeypatch) -> None:
    sweep = AsyncMock(return_value={"examined": 0, "running": 0, "unreported": 0})
    await _run_one_tick(monkeypatch, sweep=sweep)

    assert sweep.await_count >= 1
    assert sweep.await_args.kwargs["subscriber_butler"] == "finance"


async def test_a_sweep_failure_never_breaks_the_scheduler_loop(monkeypatch) -> None:
    """Positive control: the loop must keep ticking when the sweep raises."""
    sweep = AsyncMock(side_effect=RuntimeError("ledger unavailable"))
    await _run_one_tick(monkeypatch, sweep=sweep)

    assert sweep.await_count >= 1
