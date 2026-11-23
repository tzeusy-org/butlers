"""An unreadable live grant is unknown, never "the grant changed" (bu-4c7nvp.1.5.1).

A cache expiry mid-tick must not pause an ACTIVE watch, withhold or consume a fire, or send a
notice; and a card binds only the sources its own predicate reads, so a registry that gains a
source the watch never uses does not invalidate it.
"""

from __future__ import annotations

import pytest

from butlers.conditions import ConditionEvaluator, Grant, Snapshot, State
from butlers.conditions.memory import InMemoryConditionStore
from butlers.watches import WatchService, WatchState
from butlers.watches.memory import InMemoryWatchStore
from butlers.watches.model import GrantUnavailable
from butlers.watches.runner import WatchRunner
from tests.watches.test_watch_runner import NOW, SOURCE, Recorder, fire, make_watch


class Live:
    """A grant source that can gain a source, lose one, or become unreadable."""

    def __init__(self):
        self.sources = {SOURCE, "perception:owner.place"}
        self.readable = True

    def __call__(self, watch_id: str) -> Grant:
        if not self.readable:
            raise GrantUnavailable("the live grant cannot be read now")
        return Grant(goal_id=watch_id, sources=frozenset(self.sources))


@pytest.fixture
def live():
    return Live()


@pytest.fixture
def conditions():
    return InMemoryConditionStore()


@pytest.fixture
def service(live, conditions):
    return WatchService(InMemoryWatchStore(), ConditionEvaluator(conditions), live)


async def test_a_cache_expiry_mid_tick_pauses_nothing_and_keeps_the_fire(
    service, live, conditions, caplog
):
    watch = await make_watch(service)
    await fire(service, watch)
    recorder = Recorder()
    runner = WatchRunner(service, recorder, lambda now: Snapshot(now=now))
    live.readable = False  # the 60 s cache expired between the prepare and the watch
    report = await runner.tick(NOW)
    assert report.failed == 1 and report.delivered == 0 and recorder.keys == []
    assert "owner_watch_grant_unavailable" in caplog.text  # unknown, not "grant changed"
    assert "owner_watch_grant_changed" not in caplog.text
    stored = await service.store.get(watch.id)
    assert stored is not None and stored.state is WatchState.ACTIVE and stored.version == 1
    assert conditions.instances[watch.instance_id].state is State.FIRED  # not consumed
    live.readable = True
    again = await runner.tick(NOW)
    assert (again.delivered, again.rearmed) == (1, 1)  # the next tick delivers it once


async def test_an_unknown_grant_never_pauses_a_watch_that_lost_its_condition(
    service, live, conditions
):
    watch = await make_watch(service)
    conditions.instances.clear()  # a crash left an ACTIVE watch with no condition
    live.readable = False
    with pytest.raises(GrantUnavailable):
        await service.repair(watch, Snapshot(now=NOW))
    stored = await service.store.get(watch.id)
    assert stored is not None and stored.state is WatchState.ACTIVE  # unknown is not "changed"


async def test_the_view_shows_no_grant_changed_while_the_grant_is_unknown(service, live):
    watch = await make_watch(service)
    live.readable = False
    view = await service.view(watch.id)
    assert view.reason is None


async def test_a_new_source_the_watch_never_reads_does_not_invalidate_it(service, live):
    watch = await make_watch(service)
    assert service.grant_is_current(watch)
    live.sources |= {"perception:owner.mode", "perception:home.sensing"}  # the registry grew
    assert service.grant_is_current(watch)


async def test_losing_a_source_the_watch_reads_is_a_changed_grant(service, live):
    watch = await make_watch(service)
    live.sources -= {SOURCE}
    assert service.grant_is_current(watch) is False
    assert watch.sources == (SOURCE,)  # the card binds only the predicate's own read set
