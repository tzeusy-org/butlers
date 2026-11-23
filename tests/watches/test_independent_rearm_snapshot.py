from datetime import timedelta

from butlers.conditions import ConditionEvaluator, Grant, Snapshot, State
from butlers.conditions.memory import InMemoryConditionStore
from butlers.watches import OwnerApproval, WatchService
from butlers.watches.memory import InMemoryWatchStore
from butlers.watches.runner import WatchRunner
from tests.conditions.test_conditions import DECISION, SUBJECT, decision_ev
from tests.watches.test_watch_runner import NOW, Recorder


async def test_the_same_decision_state_does_not_create_another_notice_on_rearm():
    def grant(watch_id):
        return Grant(
            goal_id=watch_id,
            sources=frozenset({"goal.decision"}),
            decisions=frozenset({SUBJECT}),
        )

    recorder = Recorder()
    service = WatchService(
        InMemoryWatchStore(), ConditionEvaluator(InMemoryConditionStore()), grant
    )
    card = service.propose_create({"predicate": DECISION}, now=NOW)
    watch = await service.approve(card, OwnerApproval(card.digest, "owner"), Snapshot(now=NOW))
    outcome = await service.evaluator.evaluate(
        watch.instance_id, [decision_ev(at=NOW, position=7)], now=NOW
    )
    assert outcome.instance.state is State.FIRED
    assert outcome.instance.cursors == {"goal.decision": 7}
    runner = WatchRunner(
        service, recorder, lambda now: Snapshot(now=now, decisions={SUBJECT: "approved"})
    )
    first = await runner.tick(NOW)
    assert (first.delivered, first.rearmed) == (1, 1)
    restarted = WatchRunner(service, recorder, runner.snapshot_for)
    second = await restarted.tick(NOW + timedelta(seconds=1))
    assert second.delivered == 0, (
        f"unchanged snapshot delivered a second generation: {recorder.keys}"
    )
    assert len(recorder.keys) == 1


def _decision_service():
    def grant(watch_id):
        return Grant(
            goal_id=watch_id,
            sources=frozenset({"goal.decision"}),
            decisions=frozenset({SUBJECT}),
        )

    return WatchService(InMemoryWatchStore(), ConditionEvaluator(InMemoryConditionStore()), grant)


async def test_generation_one_fires_on_a_snapshot_that_is_already_true():
    service = _decision_service()
    card = service.propose_create({"predicate": DECISION}, now=NOW)
    snapshot = Snapshot(now=NOW, decisions={SUBJECT: "approved"})
    watch = await service.approve(card, OwnerApproval(card.digest, "owner"), snapshot)
    instance = await service.instance(watch)
    assert instance is not None and instance.state is State.FIRED


async def test_a_later_generation_fires_only_on_an_event_past_the_cursor():
    service = _decision_service()
    recorder = Recorder()
    card = service.propose_create({"predicate": DECISION}, now=NOW)
    watch = await service.approve(card, OwnerApproval(card.digest, "owner"), Snapshot(now=NOW))
    await service.evaluator.evaluate(watch.instance_id, [decision_ev(at=NOW, position=7)], now=NOW)
    runner = WatchRunner(
        service, recorder, lambda now: Snapshot(now=now, decisions={SUBJECT: "approved"})
    )
    await runner.tick(NOW)
    assert (await runner.tick(NOW + timedelta(seconds=1))).delivered == 0  # still true, no fire
    current = await service.store.get(watch.id)
    assert current is not None and current.generation == 2
    # A replayed event at or below the floor is ignored; a new one past it fires.
    stale = await service.evaluator.evaluate(
        current.instance_id, [decision_ev(at=NOW, position=7)], now=NOW
    )
    assert stale.instance.state is State.OPEN
    fresh = await service.evaluator.evaluate(
        current.instance_id, [decision_ev(at=NOW, position=8)], now=NOW
    )
    assert fresh.instance.state is State.FIRED
