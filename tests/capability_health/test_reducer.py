"""RFC 0038 S6: one deterministic reducer turns dependency observations into one health value."""

from datetime import UTC, datetime, timedelta

import pytest

from butlers.capability_health.model import (
    ALLOWED_CAUSES,
    MAX_SOURCE_AGE,
    Observation,
    reduce,
)
from butlers.perception.model import HEALTH_CAUSES, HEALTH_STATES

pytestmark = pytest.mark.unit

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)


def at(minutes: int) -> datetime:
    return NOW - timedelta(minutes=minutes)


def obs(state, cause=None, minutes=1, dependency="google"):
    return Observation(dependency, state, cause, at(minutes))


def test_allowed_pairs_cover_the_closed_vocabularies_exactly() -> None:
    assert set(ALLOWED_CAUSES) == HEALTH_STATES
    assert ALLOWED_CAUSES["available"] == frozenset({None})
    named = set().union(*(c for c in ALLOWED_CAUSES.values())) - {None}
    assert named == HEALTH_CAUSES


@pytest.mark.parametrize(
    "state,cause",
    [
        ("available", "unknown"),
        ("down", None),
        ("down", "policy_held"),
        ("held", "oauth_revoked"),
        ("unconfigured", "oauth_revoked"),
        ("blind", "oauth_revoked"),
        ("sleeping", None),
        ("down", "bad_weather"),
    ],
)
def test_an_observation_with_an_impossible_pair_is_refused(state, cause) -> None:
    with pytest.raises(ValueError):
        obs(state, cause)


def test_an_unmeasured_observation_has_no_state_or_cause() -> None:
    assert obs(None).state is None
    with pytest.raises(ValueError):
        obs(None, "unknown")


def test_naive_observation_time_is_refused() -> None:
    with pytest.raises(ValueError):
        Observation("google", "available", None, datetime(2026, 1, 1))


def test_no_observations_is_unmeasurable_never_healthy() -> None:
    assert reduce("google", [], NOW).unmeasurable


def test_all_unmeasured_is_unmeasurable() -> None:
    assert reduce("google", [obs(None), obs(None, minutes=3)], NOW).unmeasurable


def test_all_available_reports_available_with_the_oldest_input_time() -> None:
    result = reduce("google", [obs("available", minutes=1), obs("available", minutes=7)], NOW)
    assert (result.state, result.cause, result.observed_at) == ("available", None, at(7))


def test_one_unmeasured_input_among_healthy_ones_is_blind_not_available() -> None:
    result = reduce("google", [obs("available"), obs(None, minutes=4)], NOW)
    assert (result.state, result.cause) == ("blind", "signal_unmeasurable")
    assert result.observed_at == at(4)


@pytest.mark.parametrize(
    "worse,better",
    [
        (("held", "policy_held"), ("unconfigured", "not_configured")),
        (("unconfigured", "credential_missing"), ("down", "oauth_revoked")),
        (("down", "oauth_revoked"), ("blind", "signal_unmeasurable")),
        (("blind", "signal_unmeasurable"), ("degraded", "provider_unavailable")),
        (("degraded", "provider_unavailable"), ("available", None)),
    ],
)
def test_worst_state_wins_in_either_input_order(worse, better) -> None:
    for observations in ([obs(*worse), obs(*better)], [obs(*better), obs(*worse)]):
        result = reduce("google", observations, NOW)
        assert (result.state, result.cause) == worse


def test_down_causes_are_ranked_oauth_then_provider_then_domain_then_unknown() -> None:
    causes = ["unknown", "domain_unreachable", "provider_unavailable", "oauth_revoked"]
    assert reduce("google", [obs("down", c) for c in causes], NOW).cause == "oauth_revoked"
    assert (
        reduce("google", [obs("down", c) for c in causes[:3]], NOW).cause == "provider_unavailable"
    )
    assert reduce("google", [obs("down", c) for c in causes[:2]], NOW).cause == "domain_unreachable"


def test_a_worse_state_carries_the_time_of_the_winning_observation() -> None:
    result = reduce(
        "google",
        [obs("available", minutes=9), obs("down", "oauth_revoked", minutes=2)],
        NOW,
    )
    assert (result.state, result.observed_at) == ("down", at(2))


def test_reduction_is_order_independent_and_pure() -> None:
    items = [
        obs("degraded", "unknown", 5),
        obs("down", "domain_unreachable", 3),
        obs("available", None, 8),
        obs(None, minutes=6),
    ]
    forward = reduce("google", items, NOW)
    assert forward == reduce("google", list(reversed(items)), NOW)
    assert items[0].state == "degraded"


def test_observations_for_another_dependency_are_refused() -> None:
    with pytest.raises(ValueError):
        reduce("google", [obs("available", dependency="spotify")], NOW)


def test_future_observations_are_clamped_to_now() -> None:
    result = reduce(
        "google", [Observation("google", "available", None, NOW + timedelta(hours=1))], NOW
    )
    assert result.observed_at == NOW


def test_the_reduction_value_is_a_valid_system_dep_payload() -> None:
    from butlers.perception.model import validate_value

    for items in ([obs("available")], [obs("down", "oauth_revoked")], [obs("held", "policy_held")]):
        validate_value("system.dep", reduce("google", items, NOW).value())


def test_a_stale_available_alone_is_unmeasurable() -> None:
    old = Observation("google", "available", None, NOW - timedelta(days=30))
    assert reduce("google", [old], NOW).unmeasurable


def test_a_stale_available_beside_a_fresh_one_is_blind_not_available() -> None:
    old = Observation("google", "available", None, NOW - timedelta(days=30))
    result = reduce("google", [old, obs("available")], NOW)
    assert (result.state, result.cause) == ("blind", "signal_unmeasurable")


def test_a_stale_bad_claim_is_also_only_an_unmeasured_claim() -> None:
    # Old news is not a current verdict in either direction.
    old = Observation("google", "down", "oauth_revoked", NOW - timedelta(days=30))
    assert reduce("google", [old], NOW).unmeasurable
    assert reduce("google", [old, obs("available")], NOW).state == "blind"


def test_the_age_bound_is_inclusive_and_configurable() -> None:
    edge = Observation("google", "available", None, NOW - MAX_SOURCE_AGE)
    assert reduce("google", [edge], NOW).state == "available"
    just_over = Observation(
        "google", "available", None, NOW - MAX_SOURCE_AGE - timedelta(seconds=1)
    )
    assert reduce("google", [just_over], NOW).unmeasurable
    assert (
        reduce("google", [just_over], NOW, max_source_age=timedelta(hours=1)).state == "available"
    )
