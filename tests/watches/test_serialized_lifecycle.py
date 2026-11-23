from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from butlers.conditions import ConditionEvaluator, State
from butlers.conditions.memory import InMemoryConditionStore
from butlers.watches import WatchConflict, WatchRefused, WatchService, WatchState
from butlers.watches.memory import InMemoryWatchStore
from tests.watches.test_watches import NOW, PREDICATE, approve, create, grant_for, snap

pytestmark = pytest.mark.asyncio


class TransitionOverlap:
    """Block only the first transition, so a correctly serialized second can wait."""

    def __init__(self):
        self.first_arrived = asyncio.Event()
        self.release_first = asyncio.Event()

    async def stall_first(self):
        if not self.first_arrived.is_set():
            self.first_arrived.set()
            await self.release_first.wait()

    async def run(self, first, second):
        second_started = asyncio.Event()

        async def begin_second():
            second_started.set()
            return await second()

        first_task = asyncio.create_task(first())
        second_task = None
        try:
            await asyncio.wait_for(self.first_arrived.wait(), timeout=3)
            second_task = asyncio.create_task(begin_second())
            await asyncio.wait_for(second_started.wait(), timeout=3)
        finally:
            self.release_first.set()
            results = await asyncio.wait_for(
                asyncio.gather(
                    *(task for task in (first_task, second_task) if task is not None),
                    return_exceptions=True,
                ),
                timeout=3,
            )
        assert all(
            not isinstance(result, BaseException)
            or isinstance(result, (WatchConflict, WatchRefused))
            for result in results
        ), results
        return results


async def test_overlapping_renewal_replays_leave_one_current_open_generation():
    overlap = TransitionOverlap()

    class GatedRenewalStore(InMemoryWatchStore):
        async def write(self, watch, *, expected_version, cap):
            if watch.generation == 2:
                await overlap.stall_first()
            return await super().write(watch, expected_version=expected_version, cap=cap)

    conditions = InMemoryConditionStore()
    service = WatchService(GatedRenewalStore(), ConditionEvaluator(conditions), grant_for)
    _, watch = await create(service)
    later = watch.expires_at - timedelta(days=7)
    card = await service.propose_renew(watch.id, now=later)

    async def renew():
        return await service.approve(card, approve(card), snap(later))

    results = await overlap.run(renew, renew)
    current = await service.store.get(watch.id)
    assert current is not None and current.generation == 2
    assert current in results
    assert conditions.instances[current.instance_id].state is State.OPEN
    assert conditions.instances[watch.instance_id].state is State.CANCELLED


async def test_overlapping_delete_and_edit_preserve_the_winning_watch_state():
    overlap = TransitionOverlap()

    class GatedDeletionStore(InMemoryWatchStore):
        async def write(self, watch, *, expected_version, cap):
            if watch.state is WatchState.DELETED:
                await overlap.stall_first()
            return await super().write(watch, expected_version=expected_version, cap=cap)

    conditions = InMemoryConditionStore()
    service = WatchService(GatedDeletionStore(), ConditionEvaluator(conditions), grant_for)
    _, watch = await create(service)
    delete = await service.propose_delete(watch.id)
    edit = await service.propose_update(watch.id, delivery="right_away")

    results = await overlap.run(
        lambda: service.approve(delete, approve(delete), snap()),
        lambda: service.approve(edit, approve(edit), snap()),
    )
    current = await service.store.get(watch.id)
    assert current is not None and current.version == watch.version + 1 and current in results
    assert sum(isinstance(result, (WatchConflict, WatchRefused)) for result in results) == 1
    instance = conditions.instances[current.instance_id]
    if current.state is WatchState.ACTIVE:
        assert current.delivery == "right_away"
        assert instance.state is State.OPEN
    else:
        assert current.state is WatchState.DELETED
        assert instance.state is State.CANCELLED


async def test_pause_overlapping_new_registration_leaves_no_open_condition(monkeypatch):
    overlap = TransitionOverlap()
    conditions = InMemoryConditionStore()
    evaluator = ConditionEvaluator(conditions)
    service = WatchService(InMemoryWatchStore(), evaluator, grant_for)
    _, watch = await create(service)
    await service.pause(watch.id, origin="owner", now=NOW)
    card = await service.propose_update(watch.id, resume=True)
    register = evaluator.register

    async def delayed_registration(**kwargs):
        await overlap.stall_first()
        return await register(**kwargs)

    monkeypatch.setattr(evaluator, "register", delayed_registration)
    results = await overlap.run(
        lambda: service.approve(card, approve(card), snap()),
        lambda: service.pause(watch.id, origin="owner", now=NOW),
    )

    current = await service.store.get(watch.id)
    assert current is not None and current.state is WatchState.PAUSED and current in results
    instance = conditions.instances.get(current.instance_id)
    assert instance is None or instance.state is State.CANCELLED


async def test_resumed_watch_can_retry_a_transient_registration_failure(monkeypatch):
    conditions = InMemoryConditionStore()
    evaluator = ConditionEvaluator(conditions)
    service = WatchService(InMemoryWatchStore(), evaluator, grant_for)
    _, watch = await create(service)
    await service.pause(watch.id, origin="owner", now=NOW)
    card = await service.propose_update(watch.id, resume=True)
    register = evaluator.register

    async def outage(**_kwargs):
        raise OSError("synthetic condition store outage")

    with monkeypatch.context() as patch:
        patch.setattr(evaluator, "register", outage)
        with pytest.raises(OSError):
            await service.approve(card, approve(card), snap())
        # Nothing committed ACTIVE on a registration that never happened.
        assert (await service.store.get(watch.id)).state is WatchState.PAUSED

    assert evaluator.register == register
    recovered = await service.approve(card, approve(card), snap())
    assert recovered.state is WatchState.ACTIVE
    assert conditions.instances[recovered.instance_id].state is State.OPEN


async def test_a_replayed_approval_repairs_a_missing_condition_and_a_live_one_after_pause():
    """After any transition: ACTIVE implies a live condition, PAUSED or DELETED implies none."""
    conditions = InMemoryConditionStore()
    service = WatchService(InMemoryWatchStore(), ConditionEvaluator(conditions), grant_for)
    _, watch = await create(service)
    delete = await service.propose_delete(watch.id)
    # The delete committed but the process died before it cancelled the condition.
    closed = await service.store.get(watch.id)
    from dataclasses import replace

    await service.store.write(
        replace(
            closed,
            state=WatchState.DELETED,
            version=closed.version + 1,
            approval_digest=delete.digest,
        ),
        expected_version=closed.version,
        cap=50,
    )
    assert conditions.instances[watch.instance_id].state is State.OPEN
    await service.approve(delete, approve(delete), snap())
    assert conditions.instances[watch.instance_id].state is State.CANCELLED


async def test_replaying_a_create_whose_registration_failed_never_revives_the_watch(monkeypatch):
    conditions = InMemoryConditionStore()
    evaluator = ConditionEvaluator(conditions)
    service = WatchService(InMemoryWatchStore(), evaluator, grant_for)
    card = service.propose_create({"predicate": PREDICATE}, now=NOW)
    register = evaluator.register

    async def outage(**_kwargs):
        raise OSError("synthetic condition store outage")

    with monkeypatch.context() as patch:
        patch.setattr(evaluator, "register", outage)
        with pytest.raises(OSError):
            await service.approve(card, approve(card), snap())
    stored = await service.store.get(card.watch_id)
    assert stored is not None and stored.state is WatchState.DELETED
    assert evaluator.register == register

    replayed = await service.approve(card, approve(card), snap())
    assert replayed.state is WatchState.DELETED
    assert not conditions.instances  # no live instance was created for a closed watch


async def test_a_replayed_renewal_cancels_the_previous_generation_a_crash_left_open():
    conditions = InMemoryConditionStore()
    service = WatchService(InMemoryWatchStore(), ConditionEvaluator(conditions), grant_for)
    _, watch = await create(service)
    later = watch.expires_at - timedelta(days=7)
    card = await service.propose_renew(watch.id, now=later)

    class Crash(RuntimeError):
        pass

    original = service._cancel

    async def crash_on_old_generation(target, now):
        if target.generation == 1:
            raise Crash  # the process died after committing N+1, before stopping N
        await original(target, now)

    service._cancel = crash_on_old_generation  # type: ignore[method-assign]
    with pytest.raises(Crash):
        await service.approve(card, approve(card), snap(later))
    current = await service.store.get(watch.id)
    assert current is not None and current.generation == 2
    assert conditions.instances[watch.instance_id].state is State.OPEN  # the leak
    service._cancel = original  # type: ignore[method-assign]

    await service.approve(card, approve(card), snap(later))
    assert conditions.instances[watch.instance_id].state is State.CANCELLED
    assert conditions.instances[current.instance_id].state is State.OPEN


async def test_idle_transition_locks_are_evicted():
    store = InMemoryWatchStore()
    service = WatchService(store, ConditionEvaluator(InMemoryConditionStore()), grant_for)
    _, watch = await create(service)
    await service.pause(watch.id, origin="owner", now=NOW)
    assert store._transition_locks == {}


async def test_a_replay_after_the_grant_changed_pauses_the_watch_never_active_without_a_condition():
    from tests.watches.test_watches import GRANT

    conditions = InMemoryConditionStore()
    service = WatchService(InMemoryWatchStore(), ConditionEvaluator(conditions), grant_for)
    card = service.propose_create({"predicate": PREDICATE}, now=NOW)
    watch = await service.approve(card, approve(card), snap())
    conditions.instances.clear()  # a crash left an ACTIVE watch with no condition
    before = dict(GRANT)
    GRANT.update(sources=frozenset({"perception:other"}))  # the live grant moved
    try:
        replayed = await service.approve(card, approve(card), snap())
    finally:
        GRANT.update(before)

    assert replayed.state is WatchState.PAUSED
    stored = await service.store.get(watch.id)
    assert stored is not None and stored.state is WatchState.PAUSED
    assert not conditions.instances  # never ACTIVE without an instance
    GRANT.update(sources=frozenset({"perception:other"}))
    try:
        assert (await service.view(watch.id)).reason == "grant_changed"
    finally:
        GRANT.update(before)
