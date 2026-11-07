"""A CancelledError nobody asked for must not end a loop that nothing restarts (bu-dhloi1.73).

The scheduler's outage (bu-dhloi1.59) had this shape, so each loop here is run with the real
thing: awaiting an already-cancelled future raises ``CancelledError`` with no ``.cancel()``
on the running task. Each also proves a real cancel still ends it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.api.lifespan_supervisor import supervise_lifespan_loop
from butlers.config import BufferConfig
from butlers.core.approval_delivery_worker import ApprovalDeliveryWorker
from butlers.core.buffer import DurableBuffer
from butlers.core.confirm_authority import run_confirm_worker
from butlers.roster_imports import roster_module

pytestmark = pytest.mark.unit


async def _raise_stray() -> None:
    future = asyncio.get_running_loop().create_future()
    future.cancel()
    await future


async def _until(predicate: Callable[[], bool]) -> None:
    async with asyncio.timeout(5):
        while not predicate():  # noqa: ASYNC110 - the predicates read plain counters
            await asyncio.sleep(0)


async def _cancelled(task: asyncio.Task) -> bool:
    task.cancel()
    try:
        async with asyncio.timeout(5):
            await task
    except asyncio.CancelledError:
        return True
    return False


def _worker() -> ApprovalDeliveryWorker:
    return ApprovalDeliveryWorker(MagicMock(), MagicMock(), MagicMock(), poll_interval_s=0.001)


async def test_a_stray_cancel_does_not_stop_the_approval_delivery_worker(caplog) -> None:
    worker = _worker()
    calls = 0
    stop = asyncio.Event()

    async def process_one() -> bool:
        nonlocal calls
        calls += 1
        if calls == 1:
            await _raise_stray()
        stop.set()
        return False

    worker.process_one = process_one  # type: ignore[method-assign]

    async with asyncio.timeout(5):
        await worker.run(stop)

    assert calls == 2
    assert "recovery scan failed (CancelledError)" in caplog.text


async def test_a_real_cancel_still_stops_the_approval_delivery_worker() -> None:
    worker = _worker()
    entered = asyncio.Event()

    async def process_one() -> bool:
        entered.set()
        await asyncio.Event().wait()
        return False

    worker.process_one = process_one  # type: ignore[method-assign]
    task = asyncio.create_task(worker.run(asyncio.Event()))
    await asyncio.wait_for(entered.wait(), 5)

    assert await _cancelled(task)


async def test_a_stray_cancel_is_a_crash_the_lifespan_supervisor_restarts(caplog) -> None:
    starts = 0
    health = AsyncMock()

    async def loop() -> None:
        nonlocal starts
        starts += 1
        if starts == 1:
            await _raise_stray()
        await asyncio.Event().wait()

    task = supervise_lifespan_loop(
        "probe", loop, sleep_fn=AsyncMock(return_value=None), health=health
    )
    await _until(lambda: starts == 2)

    assert not task.done()
    health.tick_failed.assert_awaited_once_with("loop_crashed")
    health.stopped.assert_not_awaited()
    assert "crashed unexpectedly" in caplog.text

    assert await _cancelled(task)
    health.stopped.assert_awaited_once()


async def test_a_real_cancel_still_stops_a_supervised_loop_at_once() -> None:
    entered = asyncio.Event()
    sleep_fn = AsyncMock(return_value=None)

    async def loop() -> None:
        entered.set()
        await asyncio.Event().wait()

    task = supervise_lifespan_loop("probe", loop, sleep_fn=sleep_fn)
    await asyncio.wait_for(entered.wait(), 5)

    assert await _cancelled(task)
    sleep_fn.assert_not_awaited()


async def test_a_stray_cancel_does_not_stop_the_confirm_worker() -> None:
    calls = 0
    stop = asyncio.Event()

    async def cycle() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            await _raise_stray()
        stop.set()

    delivery = MagicMock(cycle=cycle)

    async with asyncio.timeout(5):
        # The first cycle's stray must be survived; the pause between cycles is 5 seconds.
        task = asyncio.create_task(run_confirm_worker(delivery, stop))
        await _until(lambda: calls == 1)
        await asyncio.sleep(0)
        assert not task.done()
        assert await _cancelled(task)


async def test_a_real_cancel_during_a_cycle_still_stops_the_confirm_worker() -> None:
    entered = asyncio.Event()

    async def cycle() -> None:
        entered.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(run_confirm_worker(MagicMock(cycle=cycle), asyncio.Event()))
    await asyncio.wait_for(entered.wait(), 5)

    assert await _cancelled(task)


def _enqueue(buffer: DurableBuffer, request_id: str) -> None:
    assert buffer.enqueue(
        request_id=request_id,
        message_inbox_id=request_id,
        message_text="hello",
        source={"channel": "telegram"},
        event={},
        sender={"identity": "u1"},
    )


async def test_a_stray_cancel_costs_one_message_not_a_buffer_worker(caplog) -> None:
    processed: list[str] = []

    async def process(ref) -> None:
        processed.append(ref.request_id)
        if ref.request_id == "r1":
            await _raise_stray()

    buffer = DurableBuffer(config=BufferConfig(worker_count=1), pool=None, process_fn=process)
    await buffer.start()
    try:
        _enqueue(buffer, "r1")
        await _until(lambda: processed == ["r1"])
        await asyncio.sleep(0)
        _enqueue(buffer, "r2")
        await _until(lambda: processed == ["r1", "r2"])
    finally:
        await buffer.stop(drain_timeout_s=1)

    assert "processing failed for request_id=r1" in caplog.text


async def test_a_real_cancel_still_stops_a_buffer_worker_mid_message() -> None:
    entered = asyncio.Event()

    async def process(_ref) -> None:
        entered.set()
        await asyncio.Event().wait()

    buffer = DurableBuffer(config=BufferConfig(worker_count=1), pool=None, process_fn=process)
    _enqueue(buffer, "r1")
    task = asyncio.create_task(buffer._worker_loop(worker_id=0))
    await asyncio.wait_for(entered.wait(), 5)

    assert await _cancelled(task)


@pytest.fixture
def switchboard_modules(monkeypatch):
    module = roster_module("switchboard", "modules/__init__")
    monkeypatch.setattr(module, "_RUNTIME_ATTENTION_POLL_INTERVAL_SECONDS", 0)
    return module


async def test_a_stray_cancel_does_not_stop_owner_attention_delivery(
    switchboard_modules, caplog
) -> None:
    calls = 0

    async def run_once() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            await _raise_stray()

    run = switchboard_modules.SwitchboardModule._run_runtime_attention_worker
    task = asyncio.create_task(run(MagicMock(), MagicMock(run_once=run_once)))
    await _until(lambda: calls >= 2)

    assert not task.done()
    assert "delivery worker pass failed" in caplog.text
    task.cancel()
    await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 5)
    assert task.done()


async def test_a_real_cancel_still_stops_owner_attention_delivery(switchboard_modules) -> None:
    entered = asyncio.Event()

    async def run_once() -> None:
        entered.set()
        await asyncio.Event().wait()

    run = switchboard_modules.SwitchboardModule._run_runtime_attention_worker
    task = asyncio.create_task(run(MagicMock(), MagicMock(run_once=run_once)))
    await asyncio.wait_for(entered.wait(), 5)

    assert await _cancelled(task)
