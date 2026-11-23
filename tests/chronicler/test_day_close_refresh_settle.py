"""settle() returns even when a finished entry's done-callback has not run yet (bu-dhloi1.237).

On Python 3.12, ``asyncio.gather`` of futures that are all done completes
synchronously, and ``asyncio.shield`` of a done future returns it unchanged, so
awaiting them does not yield to the event loop. A task or pool release that
finished in the same loop iteration settle() started in is still in
``pending``/``releases``, because ``_settled`` and ``releases.discard`` only
run on a later iteration. settle() then re-gathered it forever without
yielding, and those callbacks never got the chance to run. Same defect as the
goal runner's drain() (bu-dhloi1.227).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from butlers.chronicler.day_close_refresh import DayCloseRefreshService

pytestmark = pytest.mark.unit


@pytest.fixture
def gather_calls(monkeypatch) -> list[int]:
    real_gather = asyncio.gather
    calls: list[int] = []

    def bounded_gather(*aws, **kwargs):
        # A spinning settle() never yields, so no timeout could fire; fail instead.
        calls.append(len(aws))
        if len(calls) > 50:
            raise AssertionError("settle() re-gathered done entries without yielding")
        return real_gather(*aws, **kwargs)

    monkeypatch.setattr(asyncio, "gather", bounded_gather)
    return calls


def _service() -> DayCloseRefreshService:
    return DayCloseRefreshService(object(), SimpleNamespace())


def _own(service: DayCloseRefreshService, operation) -> asyncio.Task:
    # The same registration run_owned() performs.
    task = asyncio.create_task(operation)
    service.pending.add(task)
    task.add_done_callback(service._settled)
    return task


async def test_settle_returns_when_a_done_task_still_awaits_its_callback(gather_calls) -> None:
    service = _service()

    async def finishes_at_once() -> None:
        return None

    task = _own(service, finishes_at_once())
    await asyncio.sleep(0)  # it runs and finishes; _settled is queued behind us
    assert task.done()
    assert service.pending == {task}  # finished, but its done-callback has not run yet

    await service.settle()

    assert not service.has_pending
    assert len(gather_calls) <= 2


async def test_settle_returns_when_a_done_release_still_awaits_its_discard(gather_calls) -> None:
    service = _service()
    lease = asyncio.get_running_loop().create_future()
    service.releases.add(lease)
    lease.add_done_callback(service.releases.discard)
    lease.set_result(None)  # done; the discard is queued behind us

    await service.settle()

    assert not service.has_pending
    assert len(gather_calls) <= 2


async def test_settle_waits_for_tasks_owned_while_settling() -> None:
    service = _service()
    release = asyncio.Event()
    seen: list[str] = []

    async def second() -> None:
        seen.append("second")

    async def first() -> None:
        await release.wait()
        _own(service, second())
        seen.append("first")

    _own(service, first())
    asyncio.get_running_loop().call_soon(release.set)

    await service.settle()

    assert seen == ["first", "second"]
    assert not service.has_pending
