from butlers.conditions import ConditionEvaluator, Grant, Snapshot, State
from butlers.conditions.memory import InMemoryConditionStore
from butlers.watches import WatchConflict, WatchService, WatchState
from butlers.watches.memory import InMemoryWatchStore
from butlers.watches.runner import WatchRunner
from tests.watches.test_watch_runner import NOW, Recorder, fire, grant_for, make_watch


def build():
    store = InMemoryWatchStore()
    conditions = InMemoryConditionStore()
    service = WatchService(store, ConditionEvaluator(conditions), grant_for)
    recorder = Recorder()
    return (
        service,
        conditions,
        recorder,
        WatchRunner(service, recorder, lambda now: Snapshot(now=now)),
    )


async def test_pending_fire_does_not_deliver_after_grant_removal():
    service, _, recorder, runner = build()
    watch = await make_watch(service)
    await fire(service, watch)
    service.grant_for = lambda watch_id: Grant(goal_id=watch_id, sources=frozenset())
    problem = None
    try:
        await runner.tick(NOW)
    except ValueError as exc:
        problem = str(exc)
    assert not recorder.notices, (
        f"sent {len(recorder.notices)} notice(s) before grant refusal {problem!r}"
    )
    assert problem is None, f"grant removal aborts the whole runner tick: {problem}"


async def test_paused_watch_is_not_delivered_from_a_stale_sweep_snapshot():
    service, _, recorder, runner = build()
    watch = await make_watch(service)
    await fire(service, watch)
    original_list = service.store.list_all
    sweeps = 0

    async def list_with_owner_pause():
        nonlocal sweeps
        watches = await original_list()
        sweeps += 1
        if sweeps == 2:
            paused = await service.pause(watch.id, origin="owner", now=NOW)
            assert paused.state is WatchState.PAUSED
        return watches

    service.store.list_all = list_with_owner_pause
    await runner.tick(NOW)
    assert sweeps >= 2  # the tick's own sweep, then the closing sweep
    assert (await service.store.get(watch.id)).state is WatchState.PAUSED
    assert recorder.notices == []


async def test_stale_rearm_cannot_leave_a_live_generation_after_pause():
    service, conditions, _, _ = build()
    watch = await make_watch(service)
    await fire(service, watch)
    prior = await service.instance(watch)
    await service.pause(watch.id, origin="owner", now=NOW)
    try:
        await service.rearm(watch, prior.cursors, Snapshot(now=NOW))
    except WatchConflict:
        pass
    next_instance = conditions.instances.get(f"{watch.id}:2")
    assert next_instance is None or next_instance.state is State.CANCELLED, (
        f"a stale runner left generation 2 {next_instance.state if next_instance else None}"
    )
