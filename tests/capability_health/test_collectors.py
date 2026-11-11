"""Collectors map closed local facts to observations; none can claim more than it measured."""

import asyncio
from datetime import UTC, datetime

import pytest

from butlers.capability_health.collectors import (
    credential_observations,
    probe_conditions,
)

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)


def observed(pairs):
    return {(o.dependency, o.state, o.cause) for o in credential_observations(pairs, NOW)}


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
    (obs,) = credential_observations([("u:google", "warn")], NOW)
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
