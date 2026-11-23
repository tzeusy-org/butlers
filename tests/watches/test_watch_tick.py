"""REQ-owner-watches-008: the tick is dark by default and blind without a snapshot provider."""

from __future__ import annotations

import pytest

from butlers.conditions import Snapshot
from butlers.watches import tick
from butlers.watches.approval import Providers
from butlers.watches.runner import WatchNotice
from tests.watches.test_watch_runner import NOW, Recorder, fire, make_watch


class SweepPool:
    def __init__(self) -> None:
        self.deleted: list[tuple] = []

    async def execute(self, sql: str, *args):
        self.deleted.append(args)
        return "DELETE 0"


class Reporter:
    def __init__(self) -> None:
        self.reports: list[tuple[str, str, str]] = []

    async def report(self, dependency: str, state: str, reason: str) -> None:
        self.reports.append((dependency, state, reason))


def providers(service=None, snapshot=True, prepared=True):
    async def prepare(now):
        return prepared

    return Providers(
        service_for=lambda: service,
        snapshot_for=lambda now: Snapshot(now=now) if snapshot else None,
        prepare=prepare,
    )


def test_the_flag_ships_off():
    assert tick.OWNER_WATCHES_LIVE is False


async def test_a_dark_tick_touches_nothing(service):
    watch = await make_watch(service)
    await fire(service, watch)
    recorder = Recorder()
    result = await tick.run_tick(
        SweepPool(), now=NOW, providers=providers(service), deliver_for=lambda pool: recorder
    )
    assert result == {"status": "dark"} and recorder.notices == []


async def test_without_a_provider_the_tick_is_blind_and_degrades_health(service):
    reporter = Reporter()
    result = await tick.run_tick(
        SweepPool(), now=NOW, live=True, providers=providers(None), reporter=reporter
    )
    assert result == {"status": tick.BLIND}
    assert reporter.reports == [(tick.DEPENDENCY, "degraded", "no_snapshot_provider")]


async def test_without_a_snapshot_the_tick_fires_nothing_and_changes_nothing(service):
    watch = await make_watch(service)
    await fire(service, watch)
    recorder = Recorder()
    result = await tick.run_tick(
        SweepPool(),
        now=NOW,
        live=True,
        providers=providers(service, snapshot=False),
        deliver_for=lambda pool: recorder,
    )
    assert result == {"status": tick.BLIND} and recorder.notices == []
    assert (await service.store.get(watch.id)).generation == 1


async def test_a_live_tick_with_providers_delivers_once_and_reports_healthy(service):
    watch = await make_watch(service)
    await fire(service, watch)
    recorder, reporter = Recorder(), Reporter()
    result = await tick.run_tick(
        SweepPool(),
        now=NOW,
        live=True,
        providers=providers(service),
        reporter=reporter,
        deliver_for=lambda pool: recorder,
    )
    assert result["status"] == "ok" and (result["delivered"], result["rearmed"]) == (1, 1)
    assert recorder.keys == [f"owner_watch:{watch.id}:g1"]
    assert reporter.reports == [(tick.DEPENDENCY, "up", "tick_ok")]
    assert isinstance(recorder.notices[0], WatchNotice)


@pytest.fixture
def service():
    from butlers.conditions import ConditionEvaluator
    from butlers.conditions.memory import InMemoryConditionStore
    from butlers.watches import WatchService
    from butlers.watches.memory import InMemoryWatchStore
    from tests.watches.test_watch_runner import grant_for

    return WatchService(
        InMemoryWatchStore(), ConditionEvaluator(InMemoryConditionStore()), grant_for
    )


async def test_a_blind_tick_changes_nothing_and_does_not_sweep(service):
    pool = SweepPool()
    result = await tick.run_tick(pool, now=NOW, live=True, providers=providers(None))
    assert result == {"status": tick.BLIND} and pool.deleted == []


async def test_a_live_tick_with_a_snapshot_sweeps_old_card_snapshots(service):
    pool = SweepPool()
    await tick.run_tick(pool, now=NOW, live=True, providers=providers(service))
    assert len(pool.deleted) == 1 and pool.deleted[0][0] == "owner_watch:card:"


async def test_a_failing_sweep_or_reporter_never_stops_the_tick(service):
    class Broken(SweepPool):
        async def execute(self, *args, **kwargs):
            raise RuntimeError("sweep down")

    class BrokenReporter:
        async def report(self, dependency, state, reason):
            raise RuntimeError("reporter down")

    result = await tick.run_tick(
        Broken(),
        now=NOW,
        live=True,
        providers=providers(service),
        reporter=BrokenReporter(),
        deliver_for=lambda pool: Recorder(),
    )
    assert result["status"] == "ok"
    blind = await tick.run_tick(
        Broken(), now=NOW, live=True, providers=providers(None), reporter=BrokenReporter()
    )
    assert blind == {"status": tick.BLIND}


async def test_a_dark_tick_sweeps_nothing(service):
    pool = SweepPool()
    await tick.run_tick(pool, now=NOW, providers=providers(service))
    assert pool.deleted == []


async def test_an_unreadable_grant_source_is_blind_and_changes_nothing(service):
    pool = SweepPool()
    result = await tick.run_tick(
        pool, now=NOW, live=True, providers=providers(service, prepared=False)
    )
    assert result == {"status": tick.BLIND} and pool.deleted == []


async def test_the_scheduled_job_resolves_the_providers_and_stays_dark_by_default(monkeypatch):
    from butlers import scheduled_jobs
    from butlers.watches import providers as providers_module

    seen = []

    async def resolve(pool, *, live=None):
        seen.append(pool)
        return providers(None)

    monkeypatch.setattr(providers_module, "resolve_providers", resolve)
    pool = SweepPool()
    result = await scheduled_jobs._run_switchboard_owner_watch_tick_job(pool, None)
    assert result == {"status": "dark"} and seen == [pool] and pool.deleted == []


async def test_the_two_blind_causes_report_distinct_reasons(service):
    for prov, reason in (
        (providers(None), "no_snapshot_provider"),  # nothing is wired
        (providers(service, prepared=False), "sources_unreadable"),  # wired but unreadable
    ):
        reporter = Reporter()
        result = await tick.run_tick(
            SweepPool(), now=NOW, live=True, providers=prov, reporter=reporter
        )
        assert result == {"status": tick.BLIND}
        assert reporter.reports == [(tick.DEPENDENCY, "degraded", reason)]
