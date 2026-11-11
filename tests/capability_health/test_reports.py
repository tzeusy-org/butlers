"""The conditions service's free-text DependencyReporter becomes closed observations."""

from datetime import UTC, datetime, timedelta

import pytest

from butlers.capability_health.reports import ReportCollector, observation_from_report
from butlers.conditions.service import DEPENDENCY
from butlers.core.capability_dependencies import DEPENDENCY_IDS

pytestmark = pytest.mark.unit

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)


def test_conditions_is_a_declared_dependency_and_the_services_own_name_maps_to_it() -> None:
    assert "conditions" in DEPENDENCY_IDS
    observation = observation_from_report(DEPENDENCY, "down", "ConnectionError", NOW)
    assert observation is not None and observation.dependency == "conditions"


@pytest.mark.parametrize(
    "reason",
    [
        "TimeoutError",
        "ConnectionError",
        "OSError",
        "ConnectionRefusedError",
        "ConnectionResetError",
    ],
)
def test_network_failures_are_domain_unreachable(reason) -> None:
    observation = observation_from_report(DEPENDENCY, "down", reason, NOW)
    assert (observation.state, observation.cause) == ("down", "domain_unreachable")


def test_any_other_reason_is_unknown_and_never_copied() -> None:
    observation = observation_from_report(DEPENDENCY, "down", "ValueError: password=hunter2", NOW)
    assert (observation.state, observation.cause) == ("down", "unknown")
    assert "hunter2" not in repr(observation)


@pytest.mark.parametrize(
    "state,cause",
    [("available", None), ("held", "policy_held"), ("unconfigured", "not_configured")],
)
def test_other_states_use_their_only_cause(state, cause) -> None:
    observation = observation_from_report("conditions", state, "anything", NOW)
    assert (observation.state, observation.cause) == (state, cause)


def test_degraded_and_blind_reports_keep_a_closed_cause() -> None:
    assert (
        observation_from_report("conditions", "degraded", "TimeoutError", NOW).cause
        == "domain_unreachable"
    )
    assert observation_from_report("conditions", "blind", "x", NOW).cause == "unknown"


def test_an_unrecognised_state_is_an_unmeasured_observation() -> None:
    observation = observation_from_report("conditions", "on-fire", "x", NOW)
    assert observation is not None and observation.state is None


def test_an_undeclared_dependency_is_ignored() -> None:
    assert observation_from_report("system.dep.nope", "down", "x", NOW) is None
    assert observation_from_report("", "down", "x", NOW) is None


async def test_the_collector_keeps_the_latest_report_and_expires_it() -> None:
    clock = [NOW]
    collector = ReportCollector(max_age=timedelta(minutes=5), clock=lambda: clock[0])
    await collector.report(DEPENDENCY, "down", "OSError")
    clock[0] = NOW + timedelta(minutes=1)
    await collector.report(DEPENDENCY, "down", "ValueError")
    (only,) = collector.observations("conditions", clock[0])
    assert only.cause == "unknown"
    # A report is a claim about a moment, not a standing fact: silence is not "healthy".
    assert collector.observations("conditions", NOW + timedelta(minutes=7)) == []
    assert collector.observations("google", clock[0]) == []


async def test_the_collector_never_raises_into_the_reporting_path() -> None:
    collector = ReportCollector(clock=lambda: NOW)
    await collector.report(object(), None, 5)  # type: ignore[arg-type]
    assert collector.observations("conditions", NOW) == []
