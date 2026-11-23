"""An unreadable live grant is unknown, never "the grant changed" (bu-4c7nvp.1.5.1).

A cache expiry mid-tick must not pause an ACTIVE watch, withhold or consume a fire, or send a
notice; and a card binds only the sources its own predicate reads, so a registry that gains a
source the watch never uses does not invalidate it.
"""

from __future__ import annotations

import pytest

from butlers.conditions import ConditionEvaluator, Grant, Snapshot, State
from butlers.conditions.memory import InMemoryConditionStore
from butlers.watches import WatchRefused, WatchService, WatchState
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


class Rebinding(Live):
    def __init__(self):
        super().__init__()
        self.subjects = {None}

    def __call__(self, watch_id: str) -> Grant:
        grant = super().__call__(watch_id)
        return Grant(
            goal_id=grant.goal_id, sources=grant.sources, subjects=frozenset(self.subjects)
        )


@pytest.fixture
def moving():
    return Rebinding()


@pytest.fixture
def moving_service(moving, conditions):
    return WatchService(InMemoryWatchStore(), ConditionEvaluator(conditions), moving)


def _approval(card):
    from butlers.watches import OwnerApproval

    return OwnerApproval(card_digest=card.digest, origin="owner")


async def test_a_resume_rebinds_to_the_live_grant_and_the_card_shows_it(
    moving_service, moving, conditions
):
    service = moving_service
    watch = await make_watch(service)
    conditions.instances.clear()  # a crash left an ACTIVE watch with no condition
    moving.subjects = {None, "someone-new"}  # the live grant moved
    assert await service.repair(watch, Snapshot(now=NOW))  # no condition and a moved grant
    paused = await service.store.get(watch.id)
    assert paused is not None
    assert paused.state is WatchState.PAUSED and paused.pause_reason == "grant"
    assert (await service.view(watch.id)).reason == "grant_changed"  # true until a resume card

    card = await service.propose_update(watch.id, resume=True)
    live = service._narrow(moving(watch.id), watch.predicate)
    assert card.grant_digest == live.digest != watch.grant_digest  # the card shows the live grant
    assert card.sources == tuple(sorted(live.sources)) == watch.sources
    resumed = await service.approve(card, _approval(card), Snapshot(now=NOW))
    assert resumed.state is WatchState.ACTIVE and resumed.generation == watch.generation + 1
    assert resumed.grant_digest == live.digest and service.grant_is_current(resumed)
    assert (await service.view(watch.id)).reason is None
    stored = await service.store.get(watch.id)
    assert stored == resumed and stored.pause_reason is None


async def test_a_resume_is_refused_when_a_source_the_watch_reads_is_no_longer_granted(
    moving_service, moving
):
    service = moving_service
    watch = await make_watch(service)
    await service.pause(watch.id, origin="owner", now=NOW)
    moving.sources -= {SOURCE}
    with pytest.raises(WatchRefused, match="no longer granted"):
        await service.propose_update(watch.id, resume=True)
    stored = await service.store.get(watch.id)
    assert stored is not None and stored.state is WatchState.PAUSED


async def test_a_resume_card_goes_stale_if_the_grant_moves_again_before_approval(
    moving_service, moving
):
    service = moving_service
    watch = await make_watch(service)
    await service.pause(watch.id, origin="owner", now=NOW)
    moving.subjects = {None, "first"}
    card = await service.propose_update(watch.id, resume=True)
    moving.subjects = {None, "second"}  # moved after the owner saw the card
    with pytest.raises(WatchRefused, match="grant changed"):
        await service.approve(card, _approval(card), Snapshot(now=NOW))
    stored = await service.store.get(watch.id)
    assert stored is not None and stored.state is WatchState.PAUSED
    source_gone = await service.propose_update(watch.id, resume=True)
    moving.sources -= {SOURCE}
    with pytest.raises(WatchRefused):
        await service.approve(source_gone, _approval(source_gone), Snapshot(now=NOW))


async def test_only_a_resume_rebinds_an_update_that_changes_the_digest_is_refused(
    moving_service, moving
):
    from dataclasses import replace

    service = moving_service
    watch = await make_watch(service)
    card = await service.propose_update(watch.id, delivery="right_away")  # a plain update, active
    forged = replace(card, grant_digest="f" * 64)
    with pytest.raises(WatchRefused, match="boundary"):
        await service.approve(forged, _approval(forged), Snapshot(now=NOW))
