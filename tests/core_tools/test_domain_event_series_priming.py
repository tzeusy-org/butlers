"""Startup priming of the domain-event failed_permanent series against the real roster.

An alert reads the counter through ``increase()``, which misses a route's first
failure unless the series already exists at zero. These tests pin the startup wiring
(lifecycle step 8d4) and the sweep-job probe to the real roster and job registry, so a
renamed job or a dropped step fails here instead of silently hiding first failures.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from opentelemetry import metrics
from opentelemetry.metrics import _internal as _metrics_internal
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.util._once import Once

from butlers import lifecycle
from butlers.config import list_butlers
from butlers.core.domain_event_contracts import get_contract_registry
from butlers.core_tools import _domain_events
from butlers.scheduled_jobs import get_deterministic_schedule_job_registry
from butlers.testing.fake_pool import fake_pool

pytestmark = pytest.mark.unit

_METRIC = "butlers.domain_event.delivery_failed_permanent_total"
_REASONS = {"non_retryable", "attempts_exhausted"}


@pytest.fixture
def reader():
    _metrics_internal._METER_PROVIDER_SET_ONCE = Once()
    _metrics_internal._METER_PROVIDER = None
    in_memory = InMemoryMetricReader()
    metrics.set_meter_provider(MeterProvider(metric_readers=[in_memory]))
    yield in_memory
    _metrics_internal._METER_PROVIDER_SET_ONCE = Once()
    _metrics_internal._METER_PROVIDER = None


def _series(reader: InMemoryMetricReader) -> dict[tuple[str, str, str, str], int]:
    data = reader.get_metrics_data()
    if data is None:
        return {}
    return {
        (
            point.attributes["emitter_butler"],
            point.attributes["source_butler"],
            point.attributes["destination_butler"],
            point.attributes["reason"],
        ): point.value
        for resource in data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
        if metric.name == _METRIC
        for point in metric.data.data_points
    }


def _declared_routes() -> set[tuple[str, str]]:
    return {
        (contract.publisher, subscriber)
        for contract in get_contract_registry().contracts()
        for subscriber in contract.permitted_subscribers
        if subscriber != contract.publisher
    }


def test_sweep_probe_matches_roster_schedule_and_job_registry() -> None:
    """The job_name probe finds exactly the daemons whose registry runs the sweep."""
    configs = list_butlers()
    registry = get_deterministic_schedule_job_registry()
    probed = {
        config.name
        for config in configs
        if _domain_events.schedules_reconciliation_sweep(config.schedules)
    }
    registered = {
        name
        for name, handlers in registry.items()
        if _domain_events.RECONCILIATION_SWEEP_JOB_NAME in handlers
    }
    # A renamed constant, roster schedule, or registry key empties or splits these sets.
    assert probed, "no roster daemon schedules the reconciliation sweep under the probed name"
    assert probed == registered
    assert probed == {_domain_events._SWEEP_EMITTER_BUTLER}


async def test_startup_primes_publisher_and_sweep_routes_at_zero(reader) -> None:
    """Step 8d4 primes own routes per publisher, and every route under the sweep emitter.

    The whole shared-bootstrap phase runs, nothing patched (bu-rvgzmf.2), on a
    database before migrations: no table exists, so the owner, catalogue and
    contract steps before 8d4 no-op.
    """
    configs = {config.name: config for config in list_butlers()}
    routes = _declared_routes()
    sweep_daemon = next(
        config
        for config in configs.values()
        if _domain_events.schedules_reconciliation_sweep(config.schedules)
    )
    publisher = next(source for source, _ in sorted(routes) if source != sweep_daemon.name)

    for name in (publisher, sweep_daemon.name):
        await lifecycle._startup_shared_bootstrap(
            SimpleNamespace(config=configs[name]),
            lifecycle.StartupState(pool=fake_pool(fetchval=False)),
        )

    def expected(emitter: str, emitted_routes: set[tuple[str, str]]) -> set[tuple]:
        return {(emitter, s, d, r) for s, d in emitted_routes for r in _REASONS}

    own = {route for route in routes if route[0] == publisher}
    sweep_own = {route for route in routes if route[0] == sweep_daemon.name}
    observed = _series(reader)
    assert own, f"{publisher} declares no routes to prime"
    assert set(observed) == (
        expected(publisher, own)
        | expected(sweep_daemon.name, sweep_own)
        | expected(_domain_events._SWEEP_EMITTER_BUTLER, routes)
    )
    assert set(observed.values()) == {0}
