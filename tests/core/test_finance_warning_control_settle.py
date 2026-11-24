"""settle() returns even when a finished task's done-callback has not run yet (bu-dhloi1.237).

``asyncio.shield`` of a done task returns the task itself, and ``gather`` of
futures that are all done completes without yielding to the event loop. A task
that finished in the same loop iteration settle() started in is still in
``tasks``/``children``, because its ``settled`` callback only runs on a later
iteration, so settle() re-gathered it forever. Same defect as the goal
runner's drain() (bu-dhloi1.227).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from butlers.core.finance_warning_control import FinanceWarningControlOwner

pytestmark = pytest.mark.unit


@pytest.fixture
def gather_calls(monkeypatch) -> list[int]:
    real_gather = asyncio.gather
    calls: list[int] = []

    def bounded_gather(*aws, **kwargs):
        # A spinning settle() never yields, so no timeout could fire; fail instead.
        calls.append(len(aws))
        if len(calls) > 50:
            raise AssertionError("settle() re-gathered done tasks without yielding")
        return real_gather(*aws, **kwargs)

    monkeypatch.setattr(asyncio, "gather", bounded_gather)
    return calls


def _owner() -> FinanceWarningControlOwner:
    daemon = SimpleNamespace(db=SimpleNamespace(pool=object()), config=SimpleNamespace(name="f"))
    return FinanceWarningControlOwner(daemon)


def _own(owned: set, operation, on_settled=None) -> asyncio.Task:
    # The registration _bounded() and the verifier child perform.
    task = asyncio.create_task(operation)
    owned.add(task)

    def settled(done):
        owned.discard(done)
        if on_settled is not None:
            on_settled()

    task.add_done_callback(settled)
    return task


async def _finishes_at_once() -> None:
    return None


@pytest.mark.parametrize("kind", ["tasks", "children"])
async def test_settle_returns_when_a_done_task_still_awaits_its_callback(
    gather_calls, kind
) -> None:
    owner = _owner()
    ran: list[str] = []
    task = _own(getattr(owner, kind), _finishes_at_once(), lambda: ran.append("settled"))
    await asyncio.sleep(0)  # it runs and finishes; its callback is queued behind us
    assert task.done()
    assert getattr(owner, kind) == {task}  # finished, but its callback has not run yet

    await owner.settle()

    assert not owner.tasks and not owner.children
    assert ran == ["settled"]  # readmission bookkeeping has run when settle() returns
    assert len(gather_calls) <= 2


async def test_settle_waits_for_children_owned_while_settling() -> None:
    owner = _owner()
    release = asyncio.Event()
    seen: list[str] = []

    async def child() -> None:
        seen.append("child")

    async def whole() -> None:
        await release.wait()
        _own(owner.children, child())
        seen.append("whole")

    _own(owner.tasks, whole())
    asyncio.get_running_loop().call_soon(release.set)

    await owner.settle()

    assert seen == ["whole", "child"]
    assert not owner.tasks and not owner.children
