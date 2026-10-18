"""At most one scheduler tick per daemon at a time (bu-jlrqju)."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock

import pytest

from butlers.api.routers.butlers import _tick_reported_busy
from butlers.core import scheduler
from butlers.core.tick_guard import exclusive_tick, run_forced_tick, tick_in_progress

pytestmark = pytest.mark.unit


class _Probe:
    """A tick double that records how many ticks run at once."""

    def __init__(self) -> None:
        self.running = 0
        self.max_running = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def tick(self, pool: object, *, block: bool = False) -> int:
        self.calls += 1
        self.running += 1
        self.max_running = max(self.max_running, self.running)
        self.started.set()
        try:
            if block:
                await self.release.wait()
            return 1
        finally:
            self.running -= 1


async def test_a_forced_tick_during_a_running_tick_reports_busy_and_runs_nothing() -> None:
    pool, probe = MagicMock(), _Probe()
    guarded = exclusive_tick(probe.tick)
    loop_tick = asyncio.create_task(guarded(pool, block=True))
    await probe.started.wait()

    forced_ran = 0

    async def forced() -> int:
        nonlocal forced_ran
        forced_ran += 1
        return await guarded(pool)

    outcome = await asyncio.wait_for(run_forced_tick(pool, forced), timeout=2)

    assert outcome["status"] == "busy"
    assert outcome["dispatched"] == 0
    assert forced_ran == 0
    assert probe.calls == 1
    probe.release.set()
    await loop_tick


async def test_guarded_ticks_never_run_concurrently() -> None:
    pool, probe = MagicMock(), _Probe()
    guarded = exclusive_tick(probe.tick)
    first = asyncio.create_task(guarded(pool, block=True))
    await probe.started.wait()
    second = asyncio.create_task(guarded(pool))
    await asyncio.sleep(0)
    assert probe.calls == 1  # the second tick waits instead of starting
    probe.release.set()
    await asyncio.wait_for(asyncio.gather(first, second), timeout=2)

    assert probe.max_running == 1
    assert probe.calls == 2


async def test_an_idle_forced_tick_runs_and_the_guarded_call_inside_does_not_deadlock() -> None:
    pool, probe = MagicMock(), _Probe()
    guarded = exclusive_tick(probe.tick)

    outcome = await asyncio.wait_for(run_forced_tick(pool, lambda: guarded(pool)), timeout=2)

    assert outcome == {"status": "ran", "dispatched": 1}
    assert not tick_in_progress(pool)


async def test_a_loop_tick_waits_for_a_running_forced_tick() -> None:
    pool, probe = MagicMock(), _Probe()
    guarded = exclusive_tick(probe.tick)
    forced = asyncio.create_task(run_forced_tick(pool, lambda: guarded(pool, block=True)))
    await asyncio.wait_for(probe.started.wait(), timeout=2)

    loop_tick = asyncio.create_task(guarded(pool))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert probe.calls == 1  # the loop tick has not started beside the forced one

    probe.release.set()
    await asyncio.wait_for(asyncio.gather(forced, loop_tick), timeout=2)
    assert probe.max_running == 1
    assert probe.calls == 2


async def test_the_lock_is_per_daemon_pool() -> None:
    probe = _Probe()
    guarded = exclusive_tick(probe.tick)
    pool_a, pool_b = MagicMock(), MagicMock()
    running = asyncio.create_task(guarded(pool_a, block=True))
    await probe.started.wait()

    outcome = await asyncio.wait_for(run_forced_tick(pool_b, lambda: guarded(pool_b)), timeout=2)

    assert outcome["status"] == "ran"
    probe.release.set()
    await running


async def test_a_failing_tick_releases_the_lock() -> None:
    pool = MagicMock()

    async def boom(pool: object) -> int:
        raise RuntimeError("tick failed")

    with pytest.raises(RuntimeError):
        await exclusive_tick(boom)(pool)

    assert not tick_in_progress(pool)


def test_the_scheduler_tick_entrypoint_is_guarded() -> None:
    # The loop and the forced paths all call scheduler.tick; it must carry the guard.
    assert hasattr(scheduler.tick, "__wrapped__")


@pytest.mark.parametrize(
    ("message", "busy"),
    [
        (json.dumps({"status": "busy", "dispatched": 0}), True),
        (json.dumps({"status": "ran", "dispatched": 2}), False),
        ("not json", False),
        (None, False),
    ],
)
def test_the_force_tick_api_marks_a_busy_reply_unsuccessful(
    message: str | None, busy: bool
) -> None:
    assert _tick_reported_busy(message) is busy
