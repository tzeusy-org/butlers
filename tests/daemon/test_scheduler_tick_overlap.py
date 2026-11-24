"""Exercise the manual tool while the daemon's scheduler holds a due occurrence."""

import asyncio
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from butlers.core_tools._base import ToolContext
from butlers.core_tools._infra import register_infra_tools
from tests.core.test_scheduler_dispatch_idempotency import _ClaimTrackingPool, _make_due_task
from tests.daemon.test_scheduler_loop import _make_butler_toml, _make_daemon_with_loop

pytestmark = pytest.mark.unit


def _daemon(tmp_path, monkeypatch):
    daemon = _make_daemon_with_loop(_make_butler_toml(tmp_path), name="health", interval=1)
    daemon._build_scheduler_runtime_context = AsyncMock(
        return_value=SimpleNamespace(
            default_timezone="UTC", prompt_hooks=None, completion_hooks=None
        )
    )
    monkeypatch.setattr("butlers.background._sweep_domain_event_reactions", AsyncMock())
    return daemon


def _register_tick(daemon):
    handlers = {}

    def core_tool(_group, **_kwargs):
        def register(handler):
            handlers[handler.__name__] = handler
            return handler

        return register

    context = ToolContext(
        daemon=daemon,
        pool=daemon.db.pool,
        spawner=daemon.spawner,
        butler_name=daemon.config.name,
        butler_type=None,
        is_switchboard=False,
        is_messenger=False,
        route_metrics=None,
    )
    register_infra_tools(context, SimpleNamespace(), core_tool)
    return handlers["tick"]


async def test_forced_tick_does_not_dispatch_another_due_job_during_a_loop_tick(
    tmp_path, monkeypatch
):
    now = datetime.now(UTC)
    pool = _ClaimTrackingPool(
        tasks=[
            _make_due_task(task_id="first", name="first", next_run_at=now - timedelta(seconds=2)),
            _make_due_task(task_id="second", name="second", next_run_at=now - timedelta(seconds=1)),
        ]
    )
    daemon = _daemon(tmp_path, monkeypatch)
    daemon.db.pool = pool
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    second_started = asyncio.Event()
    dispatched = []

    async def dispatch(*, trigger_source, **_kwargs):
        dispatched.append(trigger_source)
        if trigger_source == "schedule:first":
            first_started.set()
            await release_first.wait()
        else:
            second_started.set()
        return {"status": "ok"}

    daemon._dispatch_scheduled_task = dispatch
    forced_tick = _register_tick(daemon)
    loop = asyncio.create_task(daemon._scheduler_loop())
    try:
        async with asyncio.timeout(5):
            await first_started.wait()
            reply = await forced_tick()
            assert reply.get("status") == "busy", reply
            assert reply["dispatched"] == 0
            assert dispatched == ["schedule:first"]

            release_first.set()
            await second_started.wait()
            assert dispatched == ["schedule:first", "schedule:second"]
            assert pool.claimed_ids == ["first", "second"]
    finally:
        release_first.set()
        loop.cancel()
        with suppress(asyncio.CancelledError):
            await loop


@pytest.mark.parametrize("same_daemon", [True, False], ids=["same-daemon", "separate-daemon"])
async def test_an_active_manual_tick_only_blocks_its_own_daemon(tmp_path, monkeypatch, same_daemon):
    daemon = _daemon(tmp_path, monkeypatch)
    peer = daemon if same_daemon else _daemon(tmp_path, monkeypatch)
    peer.db.pool = daemon.db.pool
    first_started, release_first = asyncio.Event(), asyncio.Event()
    tick_calls = 0

    async def tick(*_args, **_kwargs):
        nonlocal tick_calls
        tick_calls += 1
        if tick_calls == 1:
            first_started.set()
            await release_first.wait()
            return 1
        return 2

    monkeypatch.setattr("butlers.core_tools._infra._tick", tick)
    active = asyncio.create_task(_register_tick(daemon)())
    try:
        async with asyncio.timeout(5):
            await first_started.wait()
            reply = await _register_tick(peer)()
            if same_daemon:
                assert reply.get("status") == "busy", reply
                assert reply["dispatched"] == 0
                assert tick_calls == 1
            else:
                assert reply == {"dispatched": 2}
                assert tick_calls == 2
    finally:
        release_first.set()
        assert await active == {"dispatched": 1}


class _ObservedGate(asyncio.Semaphore):
    def __init__(self):
        super().__init__(1)
        self.waiting = asyncio.Event()

    async def acquire(self):
        if self.locked():
            self.waiting.set()
        return await super().acquire()


async def test_scheduled_tick_waits_behind_manual_and_keeps_the_slot(tmp_path, monkeypatch):
    daemon = _daemon(tmp_path, monkeypatch)
    gate = daemon._scheduler_tick_gate = _ObservedGate()
    manual_started, release_manual = asyncio.Event(), asyncio.Event()
    scheduled_started, release_scheduled = asyncio.Event(), asyncio.Event()

    async def manual_tick(*_args, **_kwargs):
        manual_started.set()
        await release_manual.wait()
        return 1

    async def scheduled_tick(*_args, **_kwargs):
        scheduled_started.set()
        await release_scheduled.wait()
        return 0

    monkeypatch.setattr("butlers.core_tools._infra._tick", manual_tick)
    monkeypatch.setattr("butlers.daemon._tick", scheduled_tick)
    forced_tick = _register_tick(daemon)
    manual = asyncio.create_task(forced_tick())
    loop = asyncio.create_task(daemon._scheduler_loop())
    try:
        async with asyncio.timeout(5):
            await manual_started.wait()
            await gate.waiting.wait()
            assert not scheduled_started.is_set()
            assert (await forced_tick())["status"] == "busy"

            release_manual.set()
            assert await manual == {"dispatched": 1}
            await scheduled_started.wait()
            assert (await forced_tick())["status"] == "busy"
    finally:
        release_manual.set()
        release_scheduled.set()
        loop.cancel()
        with suppress(asyncio.CancelledError):
            await loop
        await manual


@pytest.mark.parametrize("error", [RuntimeError, asyncio.CancelledError])
async def test_manual_tick_releases_its_slot_after_failure(tmp_path, monkeypatch, error):
    daemon = _daemon(tmp_path, monkeypatch)
    tick = AsyncMock(side_effect=[error, 2])
    monkeypatch.setattr("butlers.core_tools._infra._tick", tick)
    forced_tick = _register_tick(daemon)

    with pytest.raises(error):
        await forced_tick()
    assert await forced_tick() == {"dispatched": 2}
