"""Shutdown lets a running scheduler tick finish on an open Switchboard client (bu-dhloi1.133).

A job-mode job runs inside the tick, not in the spawner's in-flight set, so draining never waits
for it. The real ``scheduler_loop`` and ``disconnect_switchboard`` run here. Only the tick is a
stand-in, since what it does is the question: what it sees of the client, and when it ends.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers import background, lifecycle
from butlers.config import ButlerConfig
from butlers.daemon import ButlerDaemon

pytestmark = pytest.mark.unit


class _Client:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    async def __aexit__(self, *exc: object) -> None:
        self._events.append("client closed")


def _daemon(tmp_path: Path, events: list[str], *, budget_s: float) -> ButlerDaemon:
    (tmp_path / "butler.toml").write_text(
        '[butler]\nname = "test-butler"\nport = 9100\n\n'
        '[butler.db]\nname = "butlers"\nschema = "test_butler"\n'
    )
    daemon = ButlerDaemon(tmp_path)
    daemon.config = ButlerConfig(name="test-butler", port=9100)
    daemon.config.shutdown_timeout_s = budget_s
    daemon.spawner = MagicMock(drain=AsyncMock())
    daemon.switchboard_client = _Client(events)
    return daemon


async def _running(
    daemon: ButlerDaemon, tick: Callable[..., Coroutine[Any, Any, int]], started: asyncio.Event
) -> None:
    daemon._scheduler_loop_task = asyncio.create_task(
        background.scheduler_loop(
            pool=MagicMock(),
            dispatch_fn=AsyncMock(),
            interval=0,
            butler_name="test-butler",
            tick_fn=tick,
            get_switchboard_client=lambda: daemon.switchboard_client,
            get_db=lambda: None,
        )
    )
    async with asyncio.timeout(5):
        await started.wait()


async def test_a_running_tick_finishes_before_the_client_closes(tmp_path: Path) -> None:
    events: list[str] = []
    daemon = _daemon(tmp_path, events, budget_s=5)
    client = daemon.switchboard_client
    started, release = asyncio.Event(), asyncio.Event()
    seen: list[Any] = []

    async def tick(*args: Any, **kwargs: Any) -> int:
        started.set()
        await release.wait()
        # What a job reads at this point: the live client, the way dispatch hands it over.
        seen.append(daemon.switchboard_client)
        events.append("tick finished")
        return 0

    await _running(daemon, tick, started)
    shutdown = asyncio.create_task(lifecycle._shutdown_resources(daemon))
    await asyncio.sleep(0.05)
    assert events == []
    assert daemon.switchboard_client is client

    release.set()
    async with asyncio.timeout(5):
        await shutdown

    assert events == ["tick finished", "client closed"]
    assert seen == [client]
    assert daemon.switchboard_client is None
    assert daemon._scheduler_loop_task is None


async def test_a_tick_that_will_not_finish_is_cancelled_at_the_budget(
    tmp_path: Path, caplog
) -> None:
    events: list[str] = []
    daemon = _daemon(tmp_path, events, budget_s=0.2)
    started = asyncio.Event()

    async def tick(*args: Any, **kwargs: Any) -> int:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            events.append("tick cancelled")
            raise
        return 0

    await _running(daemon, tick, started)
    with caplog.at_level(logging.WARNING, logger=lifecycle.logger.name):
        async with asyncio.timeout(5):
            await lifecycle._shutdown_resources(daemon)

    assert events == ["tick cancelled", "client closed"]
    assert "still running at the shutdown deadline" in caplog.text


async def test_a_tick_that_ignores_its_cancel_does_not_hold_shutdown(
    tmp_path: Path, caplog
) -> None:
    events: list[str] = []
    daemon = _daemon(tmp_path, events, budget_s=0.1)
    started, release = asyncio.Event(), asyncio.Event()

    async def tick(*args: Any, **kwargs: Any) -> int:
        started.set()
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                continue
        return 0

    await _running(daemon, tick, started)
    # Waits out the real grace period: the suite does not patch the lifecycle module.
    with caplog.at_level(logging.ERROR, logger=lifecycle.logger.name):
        async with asyncio.timeout(lifecycle._TICK_CANCEL_GRACE_S + 10):
            await lifecycle._shutdown_resources(daemon)

    assert events == ["client closed"]
    assert "ignored cancellation" in caplog.text
    release.set()
    await asyncio.sleep(0)


async def test_a_sleeping_loop_stops_at_once_and_logs_nothing(tmp_path: Path, caplog) -> None:
    events: list[str] = []
    daemon = _daemon(tmp_path, events, budget_s=0)
    daemon._scheduler_loop_task = asyncio.create_task(asyncio.Event().wait())
    await asyncio.sleep(0)

    with caplog.at_level(logging.WARNING, logger=lifecycle.logger.name):
        async with asyncio.timeout(5):
            await lifecycle._shutdown_resources(daemon)

    assert events == ["client closed"]
    assert "shutdown deadline" not in caplog.text


async def test_a_loop_that_already_failed_does_not_stop_shutdown(tmp_path: Path, caplog) -> None:
    events: list[str] = []
    daemon = _daemon(tmp_path, events, budget_s=1)

    async def fail() -> None:
        raise RuntimeError("loop broke")

    daemon._scheduler_loop_task = asyncio.create_task(fail())
    await asyncio.sleep(0)

    with caplog.at_level(logging.ERROR, logger=lifecycle.logger.name):
        async with asyncio.timeout(5):
            await lifecycle._shutdown_resources(daemon)

    assert events == ["client closed"]
    assert "Scheduler loop failed" in caplog.text
