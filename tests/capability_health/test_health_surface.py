"""Reader fail modes, the typed unavailable result and the publish policy (RFC 0038 S6)."""

from datetime import UTC, datetime, timedelta

import pytest

from butlers.capability_health.model import Reduction
from butlers.capability_health.publish import (
    HEARTBEAT,
    HealthPublisher,
    PublishState,
    should_publish,
)
from butlers.capability_health.read import Availability, Health, read_health
from butlers.capability_health.unavailable import CapabilityUnavailable, first_unavailable
from butlers.core.capability_dependencies import dependency_subject
from butlers.perception.model import EventRequest

pytestmark = pytest.mark.unit

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)


def iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


class FakeStore:
    def __init__(self, *, row=None, error=None, writes=()):
        self.row, self.error = row, error
        self.scripted = list(writes)
        self.requests = []

    async def read(self, kind, subject, *, as_of):
        self.read_args = (kind, subject, as_of)
        if self.error is not None:
            raise self.error
        return self.row

    async def write(self, request):
        self.requests.append(request.payload())
        if isinstance(self.scripted[0], Exception):
            raise self.scripted.pop(0)
        return self.scripted.pop(0)


def known(state="down", cause="oauth_revoked", observed=NOW - timedelta(seconds=90), generation=4):
    return {
        "state": "known",
        "value": {"state": state, "cause": cause, "observed_at": iso(observed)},
        "generation": generation,
    }


async def test_a_known_value_is_read_through_the_dependency_alias() -> None:
    store = FakeStore(row=known())
    health = await read_health(store, "google", NOW)
    assert store.read_args == ("system.dep.google", None, NOW)
    assert health == Health(
        "google",
        "down",
        "oauth_revoked",
        NOW - timedelta(seconds=90),
        4,
        90,
        Availability.UNAVAILABLE,
    )


@pytest.mark.parametrize(
    "state,availability",
    [
        ("available", Availability.AVAILABLE),
        ("degraded", Availability.DEGRADED),
        ("blind", Availability.DEGRADED),
        ("down", Availability.UNAVAILABLE),
        ("held", Availability.UNAVAILABLE),
        ("unconfigured", Availability.UNAVAILABLE),
    ],
)
async def test_availability_is_derived_from_the_state(state, availability) -> None:
    cause = (
        None
        if state == "available"
        else {
            "degraded": "unknown",
            "blind": "unknown",
            "down": "unknown",
            "held": "policy_held",
            "unconfigured": "not_configured",
        }[state]
    )
    assert (
        await read_health(FakeStore(row=known(state, cause)), "google", NOW)
    ).availability is availability


@pytest.mark.parametrize(
    "row",
    [
        {"state": "unknown", "value": None, "generation": 2},
        {"state": "unmeasurable", "value": None, "generation": 2},
        {"state": "unconfigured", "value": None, "generation": 0},
        {"state": "stale", "value": None, "stale_last_value": known()["value"], "generation": 2},
        {"state": "known", "value": "available", "generation": 2},
        {"state": "known", "value": {"state": "down"}, "generation": 2},
        {"state": "known", "value": known("down", "bad")["value"], "generation": 2},
        {"state": "known", "generation": 2},
        None,
        [],
    ],
    ids=[
        "unknown",
        "unmeasurable",
        "unconfigured",
        "stale",
        "legacy-string",
        "partial",
        "bad-cause",
        "no-value",
        "none",
        "not-a-row",
    ],
)
async def test_anything_but_a_fresh_valid_value_is_unknown(row) -> None:
    health = await read_health(FakeStore(row=row), "google", NOW)
    assert health == Health("google", None, None, None, None, None, Availability.UNKNOWN)


async def test_an_unreadable_projection_is_unknown_and_leaks_nothing() -> None:
    health = await read_health(FakeStore(error=RuntimeError("password=hunter2")), "google", NOW)
    assert health.availability is Availability.UNKNOWN and health.state is None
    assert "hunter2" not in repr(health)


async def test_cancellation_is_not_swallowed() -> None:
    import asyncio

    with pytest.raises(asyncio.CancelledError):
        await read_health(FakeStore(error=asyncio.CancelledError()), "google", NOW)


async def test_an_undeclared_dependency_is_refused_not_read() -> None:
    with pytest.raises(ValueError):
        await read_health(FakeStore(row=known()), "nope", NOW)


async def test_a_value_older_than_the_source_age_bound_is_unknown() -> None:
    from butlers.capability_health.model import MAX_SOURCE_AGE

    stale = known(observed=NOW - MAX_SOURCE_AGE - timedelta(seconds=1))
    assert (
        await read_health(FakeStore(row=stale), "google", NOW)
    ).availability is Availability.UNKNOWN
    edge = known(observed=NOW - MAX_SOURCE_AGE)
    assert (
        await read_health(FakeStore(row=edge), "google", NOW)
    ).availability is Availability.UNAVAILABLE


async def test_a_future_source_time_has_zero_age() -> None:
    row = known(observed=NOW + timedelta(minutes=5))
    assert (await read_health(FakeStore(row=row), "google", NOW)).source_age_seconds == 0


def health(state, cause, availability, dependency="google"):
    return Health(dependency, state, cause, NOW - timedelta(seconds=30), 3, 30, availability)


def test_only_unavailable_health_yields_the_typed_result() -> None:
    down = health("down", "oauth_revoked", Availability.UNAVAILABLE)
    result = first_unavailable([health("available", None, Availability.AVAILABLE), down])
    assert result == CapabilityUnavailable(
        dependency="google",
        state="down",
        cause="oauth_revoked",
        since=NOW - timedelta(seconds=30),
        source_age_seconds=30,
        retryable=False,
        generation=3,
    )
    for quiet in (
        health("degraded", "unknown", Availability.DEGRADED),
        health("blind", "signal_unmeasurable", Availability.DEGRADED),
        Health("google", None, None, None, None, None, Availability.UNKNOWN),
    ):
        assert first_unavailable([quiet]) is None
    assert first_unavailable([]) is None


@pytest.mark.parametrize(
    "state,cause,retryable",
    [
        ("down", "oauth_revoked", False),
        ("down", "provider_unavailable", True),
        ("down", "domain_unreachable", True),
        ("down", "unknown", True),
        ("unconfigured", "credential_missing", False),
        ("unconfigured", "not_configured", False),
        ("held", "policy_held", False),
    ],
)
def test_retryability_follows_the_cause(state, cause, retryable) -> None:
    result = first_unavailable([health(state, cause, Availability.UNAVAILABLE)])
    assert result is not None and result.retryable is retryable


def test_the_result_is_content_blind_and_stable() -> None:
    result = first_unavailable([health("down", "oauth_revoked", Availability.UNAVAILABLE)])
    assert result is not None and result.as_result() == {
        "code": "capability_unavailable",
        "dependency": "google",
        "state": "down",
        "cause": "oauth_revoked",
        "since": iso(NOW - timedelta(seconds=30)),
        "source_age_seconds": 30,
        "retryable": False,
        "generation": 3,
    }


def test_the_first_unavailable_dependency_wins_in_given_order() -> None:
    first = health("down", "oauth_revoked", Availability.UNAVAILABLE, "google")
    second = health("held", "policy_held", Availability.UNAVAILABLE, "spotify")
    result = first_unavailable([second, first])
    assert result is not None and result.dependency == "spotify"


def reduction(state="down", cause="oauth_revoked", observed=NOW - timedelta(seconds=30)):
    return Reduction("google", state, cause, observed)


def test_publish_policy_writes_first_changes_and_heartbeats_only() -> None:
    same = PublishState("down", "oauth_revoked", NOW - timedelta(seconds=10))
    assert should_publish(None, reduction(), NOW)
    assert not should_publish(same, reduction(), NOW)
    assert should_publish(same, reduction("down", "provider_unavailable"), NOW)
    assert should_publish(same, reduction("available", None), NOW)
    old = PublishState("down", "oauth_revoked", NOW - HEARTBEAT)
    assert should_publish(old, reduction(), NOW)
    assert should_publish(PublishState(None, None, NOW - timedelta(seconds=1)), reduction(), NOW)
    unmeasurable = Reduction("google", None, None, None)
    assert should_publish(same, unmeasurable, NOW)
    assert not should_publish(
        PublishState(None, None, NOW - timedelta(seconds=1)), unmeasurable, NOW
    )


async def test_publish_builds_a_valid_closed_request_for_the_dependency() -> None:
    store = FakeStore(writes=[{"status": "committed", "generation": 1}])
    result = await HealthPublisher(store).publish(reduction(), NOW)
    assert result.published and result.generation == 1
    body = store.requests[0]
    EventRequest(body)
    assert body["subject"] == str(dependency_subject("google"))
    assert body["source"] == "health.dependencies" and body["kind"] == "system.dep"
    assert body["expected_generation"] == 0 and body["state"] == "known"
    assert body["typed_value_or_ref"] == reduction().value()
    assert body["observed_at"] == body["at"] == body["confirmed_at"] == iso(NOW)
    assert [e["source"] for e in body["evidence_refs"]] == ["capability_health"]


async def test_publish_retries_once_with_the_generation_from_the_conflict() -> None:
    store = FakeStore(
        writes=[{"status": "conflict", "generation": 6}, {"status": "committed", "generation": 7}]
    )
    result = await HealthPublisher(store).publish(reduction(), NOW)
    assert result.published and result.generation == 7
    assert [r["expected_generation"] for r in store.requests] == [0, 6]
    assert store.requests[0]["event_key"] != store.requests[1]["event_key"]


async def test_a_second_conflict_is_reported_not_raised() -> None:
    store = FakeStore(
        writes=[{"status": "conflict", "generation": 6}, {"status": "conflict", "generation": 7}]
    )
    result = await HealthPublisher(store).publish(reduction(), NOW)
    assert not result.published and result.reason == "conflict" and len(store.requests) == 2


async def test_the_same_attempt_has_the_same_event_key() -> None:
    first, second = (FakeStore(writes=[{"status": "committed", "generation": 1}]) for _ in range(2))
    await HealthPublisher(first).publish(reduction(), NOW)
    await HealthPublisher(second).publish(reduction(), NOW)
    assert first.requests == second.requests


async def test_a_storage_failure_is_reported_without_its_message() -> None:
    store = FakeStore(writes=[RuntimeError("secret connection string")])
    result = await HealthPublisher(store).publish(reduction(), NOW)
    assert not result.published and result.reason == "unavailable"
    assert "secret" not in repr(result)


async def test_an_unmeasurable_reduction_publishes_an_unmeasurable_slot() -> None:
    store = FakeStore(writes=[{"status": "committed", "generation": 1}])
    result = await HealthPublisher(store).publish(Reduction("google", None, None, None), NOW)
    assert result.published
    assert store.requests[0]["state"] == "unmeasurable"
    assert store.requests[0]["typed_value_or_ref"] is None
