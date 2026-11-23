"""REQ-owner-watches-004: the watch loop notifies once per fire and arms the next generation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid5

import pytest

from butlers.conditions import ConditionEvaluator, ConditionEvent, Grant, Snapshot, State
from butlers.conditions.memory import InMemoryConditionStore
from butlers.watches import OwnerApproval, WatchService, WatchState
from butlers.watches.delivery import MESSAGE, PRIORITY, InsightDelivery
from butlers.watches.memory import InMemoryWatchStore
from butlers.watches.runner import WatchRunner

NS = UUID("33333333-3333-3333-3333-333333333333")
NOW = datetime(2026, 11, 23, 9, 0, tzinfo=UTC)
SOURCE = "bundle:delay-commitment"
PREDICATE = {"tag": "bundle", "bundle_id": "delay-commitment"}


def grant_for(watch_id: str) -> Grant:
    return Grant(goal_id=watch_id, sources=frozenset({SOURCE}))


def bundle_event(position: int, at: datetime = NOW) -> ConditionEvent:
    return ConditionEvent(
        id=str(uuid5(NS, f"bundle:{position}")),
        source=SOURCE,
        position=position,
        at=at,
        tag="bundle",
        data={"bundle_id": "delay-commitment"},
    )


class Recorder:
    """A delivery double that is idempotent by dedup key, like the broker adapter."""

    def __init__(self) -> None:
        self.keys: list[str] = []
        self.notices = []
        self.fail = 0

    async def __call__(self, notice) -> None:
        if self.fail:
            self.fail -= 1
            raise RuntimeError("broker down")
        self.notices.append(notice)
        if notice.dedup_key not in self.keys:
            self.keys.append(notice.dedup_key)


@pytest.fixture
def conditions():
    return InMemoryConditionStore()


@pytest.fixture
def service(conditions):
    return WatchService(InMemoryWatchStore(), ConditionEvaluator(conditions), grant_for)


@pytest.fixture
def recorder():
    return Recorder()


@pytest.fixture
def runner(service, recorder):
    return WatchRunner(service, recorder, lambda now: Snapshot(now=now))


async def make_watch(service, **spec):
    card = service.propose_create({"predicate": PREDICATE, **spec}, now=NOW)
    return await service.approve(
        card, OwnerApproval(card_digest=card.digest, origin="owner"), Snapshot(now=NOW)
    )


async def fire(service, watch, position=1, at=NOW):
    await service.evaluator.evaluate(watch.instance_id, [bundle_event(position, at)], now=at)


class TestNotifyAndRearm:
    async def test_a_fire_notifies_once_and_arms_the_next_generation(
        self, service, runner, recorder, conditions
    ):
        watch = await make_watch(service)
        await fire(service, watch)
        report = await runner.tick(NOW)
        assert (report.delivered, report.rearmed) == (1, 1)
        assert recorder.keys == [f"owner_watch:{watch.id}:g1"]
        now_watch = await service.store.get(watch.id)
        assert now_watch.generation == 2 and now_watch.version == 2
        assert conditions.instances[f"{watch.id}:2"].state is State.OPEN

    async def test_the_next_generation_ignores_the_event_that_fired_the_last(
        self, service, runner, recorder
    ):
        watch = await make_watch(service)
        await fire(service, watch, position=1)
        await runner.tick(NOW)
        nxt = await service.store.get(watch.id)
        await service.evaluator.evaluate(nxt.instance_id, [bundle_event(1)], now=NOW)
        assert (await runner.tick(NOW)).delivered == 0  # replayed, below the floor
        await service.evaluator.evaluate(nxt.instance_id, [bundle_event(2)], now=NOW)
        assert (await runner.tick(NOW)).delivered == 1
        assert recorder.keys == [f"owner_watch:{watch.id}:g1", f"owner_watch:{watch.id}:g2"]

    async def test_a_second_tick_without_a_new_fire_does_nothing(self, service, runner, recorder):
        watch = await make_watch(service)
        await fire(service, watch)
        await runner.tick(NOW)
        report = await runner.tick(NOW)
        assert (report.delivered, report.rearmed) == (0, 0) and len(recorder.keys) == 1

    async def test_the_notice_carries_ids_and_the_delivery_mode_only(
        self, service, runner, recorder
    ):
        watch = await make_watch(service, delivery="right_away")
        await fire(service, watch)
        await runner.tick(NOW)
        notice = recorder.notices[0]
        assert (notice.watch_id, notice.generation, notice.delivery) == (watch.id, 1, "right_away")
        assert set(vars(notice)) == {"watch_id", "generation", "delivery", "dedup_key", "fired_at"}


class TestFailureAndRecovery:
    async def test_a_failed_delivery_does_not_rearm_and_is_retried(
        self, service, runner, recorder, conditions
    ):
        watch = await make_watch(service)
        await fire(service, watch)
        recorder.fail = 1
        failed = await runner.tick(NOW)
        assert (failed.failed, failed.delivered, failed.rearmed) == (1, 0, 0)
        assert (await service.store.get(watch.id)).generation == 1
        assert f"{watch.id}:2" not in conditions.instances
        retried = await runner.tick(NOW)
        assert (retried.delivered, retried.rearmed) == (1, 1)
        assert recorder.keys == [f"owner_watch:{watch.id}:g1"]

    async def test_a_crash_between_delivery_and_rearm_replays_to_one_notice_key(
        self, service, runner, recorder, conditions
    ):
        watch = await make_watch(service)
        await fire(service, watch)
        real = service.rearm

        async def crash(*args, **kwargs):
            raise ConnectionError("process died")

        service.rearm = crash
        report = await runner.tick(NOW)
        assert report.failed == 1 and (await service.store.get(watch.id)).generation == 1
        service.rearm = real
        await runner.tick(NOW)
        assert recorder.keys == [f"owner_watch:{watch.id}:g1"]  # delivered twice, keyed once
        assert (await service.store.get(watch.id)).generation == 2
        assert len([i for i in conditions.instances if i.startswith(watch.id)]) == 2

    async def test_a_racing_runner_arms_one_next_generation(self, service, recorder, conditions):
        watch = await make_watch(service)
        await fire(service, watch)
        first = WatchRunner(service, recorder, lambda now: Snapshot(now=now), consumer="a")
        await first.tick(NOW)
        stale = await service.store.get(watch.id)
        # A second runner still holding the old row loses the CAS and changes nothing.
        fired = conditions.instances[f"{watch.id}:1"]
        old_row = type(stale)(**{**stale.__dict__, "version": 1, "generation": 1})
        assert await service.rearm(old_row, fired.cursors, Snapshot(now=NOW)) is None
        assert (await service.store.get(watch.id)).generation == 2

    async def test_an_active_watch_with_no_instance_is_repaired(self, service, runner, conditions):
        watch = await make_watch(service)
        del conditions.instances[watch.instance_id]
        report = await runner.tick(NOW)
        assert report.repaired == 1 and watch.instance_id in conditions.instances


class TestStates:
    async def test_a_paused_watch_is_not_advanced_or_notified(self, service, runner, recorder):
        watch = await make_watch(service)
        await fire(service, watch)
        await service.pause(watch.id, origin="owner", now=NOW)
        report = await runner.tick(NOW)
        assert report.delivered == 0 and not recorder.keys

    async def test_a_fire_after_expiry_closes_the_watch_instead_of_arming(
        self, service, runner, recorder
    ):
        watch = await make_watch(service)
        late = watch.expires_at - timedelta(seconds=1)
        await fire(service, watch, at=late)
        report = await runner.tick(watch.expires_at)
        assert report.expired == 1 and report.rearmed == 0
        assert (await service.store.get(watch.id)).state is WatchState.EXPIRED

    async def test_a_renewed_watch_keeps_notifying(self, service, runner, recorder):
        watch = await make_watch(service)
        later = watch.expires_at - timedelta(days=7)
        card = await service.propose_renew(watch.id, now=later)
        await service.approve(
            card, OwnerApproval(card_digest=card.digest, origin="owner"), Snapshot(now=later)
        )
        renewed = await service.store.get(watch.id)
        await fire(service, renewed, position=5, at=later)
        report = await runner.tick(later)
        assert (report.delivered, report.rearmed) == (1, 1)


class _Held:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, *exc):
        return False


class FakePool:
    def __init__(self, existing=None):
        self.existing = existing
        self.queries = []

    def acquire(self):
        return _Held(self)

    async def execute(self, query, *args):
        self.queries.append((query, args))

    async def fetchval(self, query, *args):
        self.queries.append((query, args))
        return self.existing


class FakeBroker:
    def __init__(self, status="accepted"):
        self.status = status
        self.calls = []

    async def propose_insight_candidate(self, pool, **kwargs):
        self.calls.append(kwargs)
        return {"status": self.status, "reason": "x"}


class TestInsightDelivery:
    def notice(self, service, delivery="digest"):
        from butlers.watches.runner import WatchNotice

        return WatchNotice("w1", 3, delivery, "owner_watch:w1:g3", NOW)

    @pytest.fixture
    def broker(self, monkeypatch):
        broker = FakeBroker()
        monkeypatch.setattr("butlers.watches.delivery._broker", lambda: broker)
        return broker

    @pytest.mark.parametrize("mode", ["digest", "right_away"])
    async def test_one_fixed_owner_line_per_notice_with_the_mode_priority(
        self, service, broker, mode
    ):
        deliver = InsightDelivery(FakePool(), now=lambda: NOW)
        await deliver(self.notice(service, mode))
        (call,) = broker.calls
        assert call["priority"] == PRIORITY[mode] and call["message"] == MESSAGE
        assert call["origin_butler"] == "switchboard" and call["category"] == "owner_watch"
        assert call["dedup_key"] == "owner_watch:w1:g3"
        assert call["metadata"] == {"watch_id": "w1", "generation": 3}
        assert call["expires_at"] > NOW and "lane" not in call  # never the timed/break-through lane

    async def test_right_away_outranks_digest_but_never_the_break_through_priority(self):
        assert PRIORITY["digest"] < PRIORITY["right_away"] < 95

    async def test_a_replayed_notice_proposes_nothing(self, service, broker):
        await InsightDelivery(FakePool(existing="c1"), now=lambda: NOW)(self.notice(service))
        assert broker.calls == []

    @pytest.mark.parametrize("status", ["filtered", "duplicate"])
    async def test_a_final_broker_answer_is_not_an_error(self, service, broker, status):
        broker.status = status
        await InsightDelivery(FakePool(), now=lambda: NOW)(self.notice(service))

    async def test_a_broker_error_raises_so_the_watch_retries(self, service, broker):
        broker.status = "error"
        with pytest.raises(RuntimeError):
            await InsightDelivery(FakePool(), now=lambda: NOW)(self.notice(service))


class TestPendingNoticesAreWithdrawnWhenAWatchCloses:
    async def _watch_with_withdraw(self, conditions, closed):
        async def withdraw(watch):
            closed.append((watch.id, watch.state))

        service = WatchService(
            InMemoryWatchStore(), ConditionEvaluator(conditions), grant_for, withdraw=withdraw
        )
        return service, await make_watch(service)

    async def test_pause_withdraws_the_pending_notice(self, conditions):
        closed = []
        service, watch = await self._watch_with_withdraw(conditions, closed)
        await service.pause(watch.id, origin="owner", now=NOW)
        assert closed == [(watch.id, WatchState.ACTIVE)]

    async def test_expiry_withdraws_the_pending_notice(self, conditions):
        closed = []
        service, watch = await self._watch_with_withdraw(conditions, closed)
        await service.expire_due(watch.expires_at + timedelta(seconds=1))
        assert [c[0] for c in closed] == [watch.id]

    async def test_a_replayed_pause_finishes_an_interrupted_withdrawal(self, conditions):
        closed = []
        service, watch = await self._watch_with_withdraw(conditions, closed)
        real = service.withdraw

        async def crash(_watch):
            raise ConnectionError("died after the commit")

        service.withdraw = crash
        with pytest.raises(ConnectionError):
            await service.pause(watch.id, origin="owner", now=NOW)
        service.withdraw = real
        await service.pause(watch.id, origin="owner", now=NOW)
        assert closed == [(watch.id, WatchState.PAUSED)]


class TestSweepAndEdgeTriggering:
    async def test_a_sweep_withdraws_for_every_closed_watch_and_skips_active_ones(self, conditions):
        swept = []

        async def withdraw(watch):
            swept.append((watch.id, watch.state))

        service = WatchService(
            InMemoryWatchStore(), ConditionEvaluator(conditions), grant_for, withdraw=withdraw
        )
        active = await make_watch(service)
        paused = await make_watch(service, delivery="right_away")
        await service.pause(paused.id, origin="owner", now=NOW)
        swept.clear()
        assert await service.sweep_closed() == 1
        assert swept == [(paused.id, WatchState.PAUSED)]
        assert active.id not in [w[0] for w in swept]

    async def test_every_tick_sweeps(self, conditions, recorder):
        swept = []

        async def withdraw(watch):
            swept.append(watch.id)

        service = WatchService(
            InMemoryWatchStore(), ConditionEvaluator(conditions), grant_for, withdraw=withdraw
        )
        watch = await make_watch(service)
        await service.pause(watch.id, origin="owner", now=NOW)
        swept.clear()
        runner = WatchRunner(service, recorder, lambda now: Snapshot(now=now))
        assert (await runner.tick(NOW)).swept == 1 and swept == [watch.id]

    async def test_a_resume_is_edge_triggered_from_the_last_fire_cursor(self, service, conditions):
        watch = await make_watch(service)
        await fire(service, watch, position=3)  # fires: last fire cursor is 3
        fired = conditions.instances[watch.instance_id]
        runner = WatchRunner(service, Recorder(), lambda now: Snapshot(now=now))
        await runner.tick(NOW)  # delivers and re-arms generation 2
        await service.pause(watch.id, origin="owner", now=NOW)
        card = await service.propose_update(watch.id, resume=True)
        resumed = await service.approve(
            card, OwnerApproval(card_digest=card.digest, origin="owner"), Snapshot(now=NOW)
        )
        instance = conditions.instances[resumed.instance_id]
        assert resumed.generation == 3 and instance.floor == fired.cursors
        assert instance.state is State.OPEN  # not seeded: state already true does not fire
        stale = await service.evaluator.evaluate(resumed.instance_id, [bundle_event(3)], now=NOW)
        assert stale.instance.state is State.OPEN  # at the floor: ignored
        past = await service.evaluator.evaluate(resumed.instance_id, [bundle_event(4)], now=NOW)
        assert past.instance.state is State.FIRED  # past the last fire cursor: counts

    async def test_a_renewal_is_edge_triggered_too(self, service, conditions):
        watch = await make_watch(service)
        await fire(service, watch, position=3)
        runner = WatchRunner(service, Recorder(), lambda now: Snapshot(now=now))
        await runner.tick(NOW)
        later = (await service.store.get(watch.id)).expires_at - timedelta(days=7)
        card = await service.propose_renew(watch.id, now=later)
        renewed = await service.approve(
            card, OwnerApproval(card_digest=card.digest, origin="owner"), Snapshot(now=later)
        )
        instance = conditions.instances[renewed.instance_id]
        assert renewed.generation == 3 and instance.floor == {SOURCE: 3}
        assert instance.state is State.OPEN


class TestNeverFiredFloor:
    async def test_a_renewal_of_a_never_fired_watch_floors_at_the_previous_registration(
        self, service, conditions
    ):
        watch = await make_watch(service)
        later = watch.expires_at - timedelta(days=7)
        first = await service.propose_renew(watch.id, now=later)
        renewed = await service.approve(
            first, OwnerApproval(card_digest=first.digest, origin="owner"), Snapshot(now=later)
        )
        assert conditions.instances[renewed.instance_id].floor == {}  # gen 1 floor was empty
        assert conditions.instances[renewed.instance_id].state is State.OPEN
