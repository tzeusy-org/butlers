"""`butlers up` unwinds its daemons within one process-level shutdown budget (bu-dhloi1.153).

Daemons shut down one at a time. Each used to get its own full ``shutdown_timeout_s``, so one
slow daemon could spend the pod's whole grace period and leave the rest to be SIGKILLed with no
module shutdown or pool close. Each now gets a fair share of what the process has left.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from butlers.cli import _ShutdownPlan

pytestmark = pytest.mark.unit


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _daemon(timeout: float = 30.0):
    return SimpleNamespace(config=SimpleNamespace(shutdown_timeout_s=timeout))


def test_each_daemon_gets_an_equal_share_of_the_process_budget(monkeypatch) -> None:
    monkeypatch.setenv("BUTLERS_SHUTDOWN_BUDGET_S", "60")
    clock = _Clock()
    daemons = [_daemon(), _daemon(), _daemon(), _daemon()]
    plan = _ShutdownPlan(daemons, clock=clock)

    assert plan.next_budget(daemons[0]) == 15.0


def test_a_daemon_that_finishes_early_leaves_its_time_to_the_rest(monkeypatch) -> None:
    monkeypatch.setenv("BUTLERS_SHUTDOWN_BUDGET_S", "60")
    clock = _Clock()
    daemons = [_daemon(), _daemon(), _daemon()]
    plan = _ShutdownPlan(daemons, clock=clock)

    assert plan.next_budget(daemons[0]) == 20.0
    clock.now += 2  # the first one took 2s of its 20s
    assert plan.next_budget(daemons[1]) == 29.0  # 58s left over two daemons


def test_a_slow_daemon_cannot_starve_the_ones_after_it(monkeypatch) -> None:
    monkeypatch.setenv("BUTLERS_SHUTDOWN_BUDGET_S", "60")
    clock = _Clock()
    daemons = [_daemon(), _daemon(), _daemon()]
    plan = _ShutdownPlan(daemons, clock=clock)

    granted = [plan.next_budget(daemons[0])]
    clock.now += granted[0]  # it used everything it was given
    granted.append(plan.next_budget(daemons[1]))
    clock.now += granted[1]
    granted.append(plan.next_budget(daemons[2]))
    clock.now += granted[2]

    assert granted == [20.0, 20.0, 20.0]
    assert clock.now - 1000.0 <= 60.0


def test_a_share_never_exceeds_the_daemons_own_timeout(monkeypatch) -> None:
    monkeypatch.setenv("BUTLERS_SHUTDOWN_BUDGET_S", "600")
    plan = _ShutdownPlan([_daemon(5.0)], clock=_Clock())

    assert plan.next_budget(_daemon(5.0)) == 5.0


def test_without_a_budget_nothing_is_shared_and_each_daemon_keeps_its_own_timeout(
    monkeypatch,
) -> None:
    """A local ``butlers up`` or compose sets no budget; sharing the largest timeout across a
    dozen daemons would hand the first one a couple of seconds."""
    monkeypatch.delenv("BUTLERS_SHUTDOWN_BUDGET_S", raising=False)
    daemons = [_daemon(10.0), _daemon(45.0)]
    plan = _ShutdownPlan(daemons, clock=_Clock())

    assert [plan.next_budget(d) for d in daemons] == [None, None]


def test_a_blank_or_unreadable_budget_means_no_sharing(monkeypatch) -> None:
    daemons = [_daemon()]
    for raw in ("", "soon"):
        monkeypatch.setenv("BUTLERS_SHUTDOWN_BUDGET_S", raw)
        assert _ShutdownPlan(daemons, clock=_Clock()).next_budget(daemons[0]) is None


def test_the_budget_clock_starts_when_shutdown_begins_not_when_a_daemon_is_reached(
    monkeypatch,
) -> None:
    """The signal, not the unwind, starts the clock, so time spent before the first daemon counts."""
    monkeypatch.setenv("BUTLERS_SHUTDOWN_BUDGET_S", "60")
    clock = _Clock()
    daemons = [_daemon(), _daemon()]
    plan = _ShutdownPlan(daemons, clock=clock)
    plan.begin()
    clock.now += 40  # e.g. the approval-delivery composition unwinding first

    assert plan.next_budget(daemons[0]) == 10.0  # 20s left over two


def test_an_exhausted_budget_still_grants_zero_not_a_negative(monkeypatch) -> None:
    monkeypatch.setenv("BUTLERS_SHUTDOWN_BUDGET_S", "10")
    clock = _Clock()
    daemons = [_daemon(), _daemon()]
    plan = _ShutdownPlan(daemons, clock=clock)
    plan.begin()
    clock.now += 30

    assert plan.next_budget(daemons[0]) == 0.0


async def test_a_daemon_shuts_down_under_the_share_it_was_given(monkeypatch) -> None:
    """The share reaches the daemon before ``shutdown()`` runs, and bounds its drain steps."""
    from unittest.mock import AsyncMock

    from butlers.cli import _shutdown_in_share
    from butlers.lifecycle import _step_budget_s

    monkeypatch.setenv("BUTLERS_SHUTDOWN_BUDGET_S", "60")
    seen: list[float] = []
    daemon = _daemon()
    daemon.shutdown = AsyncMock(side_effect=lambda: seen.append(daemon._shutdown_budget_s))
    plan = _ShutdownPlan([daemon, _daemon()], clock=_Clock())

    await _shutdown_in_share(daemon, plan)

    assert seen == [30.0]
    # A drain step inside it gets what is left of the share, never the daemon's full timeout.
    import asyncio

    now = asyncio.get_running_loop().time()
    assert _step_budget_s(daemon, now + 7) <= 7
    # Shut down alone, with no share, the daemon's own timeout still applies.
    assert _step_budget_s(_daemon(12.0), now + 1) == 12.0


async def test_a_daemon_without_a_share_keeps_its_own_timeout(monkeypatch) -> None:
    from unittest.mock import AsyncMock

    from butlers.cli import _shutdown_in_share

    monkeypatch.delenv("BUTLERS_SHUTDOWN_BUDGET_S", raising=False)
    seen: list[object] = []
    daemon = _daemon()
    daemon.shutdown = AsyncMock(side_effect=lambda: seen.append(daemon._shutdown_budget_s))

    await _shutdown_in_share(daemon, _ShutdownPlan([daemon], clock=_Clock()))

    assert seen == [None]
