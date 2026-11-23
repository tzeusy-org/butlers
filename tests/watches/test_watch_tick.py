"""REQ-owner-watches-008: the tick is dark by default and blind without a snapshot provider."""

from __future__ import annotations

import pytest

from butlers.conditions import Snapshot
from butlers.watches import tick
from butlers.watches.approval import Providers
from butlers.watches.runner import WatchNotice
from tests.watches.test_watch_runner import NOW, Recorder, fire, make_watch


class Reporter:
    def __init__(self) -> None:
        self.reports: list[tuple[str, str, str]] = []

    async def report(self, dependency: str, state: str, reason: str) -> None:
        self.reports.append((dependency, state, reason))


def providers(service=None, snapshot=True):
    class P(Providers):
        service_for = staticmethod(lambda: service)
        snapshot_for = staticmethod(lambda now: Snapshot(now=now) if snapshot else None)

    return P()


def test_the_flag_ships_off():
    assert tick.OWNER_WATCHES_LIVE is False


async def test_a_dark_tick_touches_nothing(service):
    watch = await make_watch(service)
    await fire(service, watch)
    recorder = Recorder()
    result = await tick.run_tick(
        object(), now=NOW, providers=providers(service), deliver_for=lambda pool: recorder
    )
    assert result == {"status": "dark"} and recorder.notices == []


async def test_without_a_provider_the_tick_is_blind_and_degrades_health(service):
    reporter = Reporter()
    result = await tick.run_tick(
        object(), now=NOW, live=True, providers=providers(None), reporter=reporter
    )
    assert result == {"status": tick.BLIND}
    assert reporter.reports == [(tick.DEPENDENCY, "degraded", "no_snapshot_provider")]


async def test_without_a_snapshot_the_tick_fires_nothing_and_changes_nothing(service):
    watch = await make_watch(service)
    await fire(service, watch)
    recorder = Recorder()
    result = await tick.run_tick(
        object(),
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
        object(),
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
