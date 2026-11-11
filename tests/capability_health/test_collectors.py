"""Collectors map closed local facts to observations; none can claim more than it measured."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from butlers.capability_health.collectors import (
    credential_observations,
    probe_conditions,
)
from butlers.capability_health.model import reduce

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)


FRESH = NOW - timedelta(minutes=2)


def observed(pairs):
    triples = [(k, s, FRESH) for k, s in pairs]
    return {(o.dependency, o.state, o.cause) for o in credential_observations(triples, NOW)}


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("ok", ("available", None)),
        ("expiring", ("available", None)),
        ("never_set", ("unconfigured", "credential_missing")),
        ("expired", ("down", "oauth_revoked")),
        ("failing", ("down", "unknown")),
        ("warn", (None, None)),
        ("something-new", (None, None)),
    ],
)
def test_credential_states_map_to_closed_observations(state, expected) -> None:
    assert observed([("u:google", state)]) == {("google", *expected)}


def test_credential_keys_name_their_dependency_and_unknown_keys_name_none() -> None:
    pairs = [
        ("u:google", "ok"),
        ("u:steam", "ok"),
        ("u:homeassistant", "ok"),
        ("u:owntracks", "ok"),
        ("u:spotify", "ok"),
        ("s:BUTLER_TELEGRAM_TOKEN", "ok"),
        ("s:SOMETHING_ELSE", "failing"),
        ("c:claude", "failing"),
        ("u:github", "failing"),
    ]
    assert {dep for dep, *_ in observed(pairs)} == {
        "google",
        "steam",
        "home_assistant",
        "owntracks",
        "spotify",
        "telegram",
    }


def test_an_unmeasured_credential_carries_no_state_and_is_stamped_at_collection() -> None:
    (obs,) = credential_observations([("u:google", "warn", None)], NOW)
    assert obs.state is None and obs.cause is None and obs.observed_at == NOW


class Pool:
    def __init__(self, behaviour) -> None:
        self.behaviour = behaviour
        self.calls = 0

    async def fetchval(self, query, *args, timeout=None):  # noqa: ASYNC109
        self.calls += 1
        return await self.behaviour(query)


async def ok(_query):
    return 1


async def test_a_reachable_conditions_store_is_available_at_probe_time() -> None:
    stamps = iter([NOW])
    obs = await probe_conditions(Pool(ok), clock=lambda: next(stamps))
    assert (obs.dependency, obs.state, obs.cause, obs.observed_at) == (
        "conditions",
        "available",
        None,
        NOW,
    )


async def test_a_slow_conditions_store_is_down_domain_unreachable() -> None:
    async def hang(_query):
        await asyncio.sleep(10)

    obs = await probe_conditions(Pool(hang), deadline_s=0.01, clock=lambda: NOW)
    assert (obs.state, obs.cause) == ("down", "domain_unreachable")


@pytest.mark.parametrize("error", [ConnectionError, OSError, TimeoutError])
async def test_a_connection_failure_is_down_domain_unreachable(error) -> None:
    async def fail(_query):
        raise error("host=secret.internal password=hunter2")

    obs = await probe_conditions(Pool(fail), clock=lambda: NOW)
    assert (obs.state, obs.cause) == ("down", "domain_unreachable")
    assert "hunter2" not in repr(obs)


@pytest.mark.parametrize("error", [ValueError, PermissionError, RuntimeError, KeyError])
async def test_any_other_failure_is_unmeasured_never_available(error) -> None:
    async def fail(_query):
        raise error("relation public.condition_instances does not exist")

    obs = await probe_conditions(Pool(fail), clock=lambda: NOW)
    assert obs.state is None and obs.cause is None and obs.observed_at == NOW


async def test_cancellation_is_not_swallowed() -> None:
    async def cancelled(_query):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await probe_conditions(Pool(cancelled), clock=lambda: NOW)


def test_a_probe_backed_claim_is_observed_when_probed_not_when_read() -> None:
    probed = NOW - timedelta(days=30)
    (obs,) = credential_observations([("u:google", "ok", probed)], NOW)
    assert (obs.state, obs.observed_at) == ("available", probed)
    # The reducer then refuses to treat a month-old success as current.
    reduction = reduce("google", [obs], NOW)
    assert reduction.unmeasurable


@pytest.mark.parametrize("state", ["ok", "expiring", "failing"])
@pytest.mark.parametrize("probed_at", [None, datetime(2026, 11, 11, 11, 59)])
def test_a_probe_backed_claim_without_a_usable_probe_time_is_unmeasured(state, probed_at) -> None:
    (obs,) = credential_observations([("u:google", state, probed_at)], NOW)
    assert obs.state is None and obs.cause is None and obs.observed_at == NOW
    assert reduce("google", [obs], NOW).unmeasurable


@pytest.mark.parametrize(
    ("state", "expected"), [("never_set", "unconfigured"), ("expired", "down")]
)
def test_row_facts_need_no_probe_and_are_observed_at_read(state, expected) -> None:
    (obs,) = credential_observations([("u:google", state, None)], NOW)
    assert (obs.state, obs.observed_at) == (expected, NOW)


def test_a_failing_probe_that_is_old_does_not_stay_down_forever() -> None:
    (obs,) = credential_observations([("u:google", "failing", NOW - timedelta(hours=2))], NOW)
    assert reduce("google", [obs], NOW).unmeasurable
