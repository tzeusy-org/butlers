"""RFC 0038 S6: a known-unavailable dependency refuses a call before it reserves anything.

Health can only refuse. It never grants a tool, never hides an authorization
refusal, and an unreadable or unknown projection leaves every normal check in force.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from butlers.capability_broker.contracts import BrokerRefused, Catalog, Reason
from butlers.capability_broker.server import refusal_data
from butlers.capability_health.read import Availability, Health
from tests.capability_broker.helpers import capability
from tests.capability_broker.test_broker_ledger import (
    DOMAIN,
    SEARCH,
    StaticCatalog,
    _grant,
    _refused,
    world,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
MAIL = capability("read_mail_headers", "read", DOMAIN, depends_on=("google",))
PLAIN = capability("plain_lookup", "read", DOMAIN)
DESK = Catalog("catalog-v1", (SEARCH, MAIL, PLAIN))


def health(state, cause, availability, dependency="google"):
    return Health(dependency, state, cause, NOW - timedelta(seconds=45), 7, 45, availability)


DOWN = health("down", "oauth_revoked", Availability.UNAVAILABLE)


class Stub:
    def __init__(self, *items, error=None, delay=0.0):
        self.items, self.error, self.delay = items, error, delay
        self.asked = []

    async def current(self, dependencies):
        self.asked.append(tuple(dependencies))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return [h for h in self.items if h.dependency in dependencies]


async def used_calls(w, goal):
    return await w.owner.fetchval(
        "SELECT used_calls + reserved_calls FROM switchboard.budgets WHERE goal_id = $1", goal
    )


async def test_known_down_dependency_refuses_before_any_reservation(broker_db_url):
    async with world(broker_db_url, catalog=StaticCatalog(DESK)) as w:
        w.broker.health = Stub(DOWN)
        goal = await w.goal(_grant(MAIL))
        _, token = await w.invocation(goal, _grant(MAIL))
        refused = await _refused(w.broker.call(goal, token, MAIL.identity, {}))
        assert refused.reason is Reason.CAPABILITY_UNAVAILABLE
        assert refused.unavailable.as_result() == {
            "code": "capability_unavailable",
            "dependency": "google",
            "state": "down",
            "cause": "oauth_revoked",
            "since": "2026-11-11T11:59:15Z",
            "source_age_seconds": 45,
            "retryable": False,
            "generation": 7,
        }
        assert w.forwarder.calls == []
        assert await w.owner.fetchval("SELECT count(*) FROM switchboard.effects") == 0
        assert await used_calls(w, goal) == 0
        reasons = await w.owner.fetch(
            "SELECT reason FROM switchboard.broker_refusals WHERE goal_id = $1", goal
        )
        assert [r["reason"] for r in reasons] == ["capability_unavailable"]


@pytest.mark.parametrize(
    "stub",
    [
        Stub(health("available", None, Availability.AVAILABLE)),
        Stub(health("degraded", "unknown", Availability.DEGRADED)),
        Stub(health("blind", "signal_unmeasurable", Availability.DEGRADED)),
        Stub(Health("google", None, None, None, None, None, Availability.UNKNOWN)),
        Stub(),
        Stub(error=RuntimeError("perception down")),
        Stub(DOWN, delay=5),
    ],
    ids=["available", "degraded", "blind", "unknown", "no-row", "read-error", "read-timeout"],
)
async def test_anything_but_known_unavailable_leaves_the_call_to_normal_checks(broker_db_url, stub):
    async with world(broker_db_url, catalog=StaticCatalog(DESK)) as w:
        w.broker.health, w.broker.health_timeout = stub, 0.05
        goal = await w.goal(_grant(MAIL))
        _, token = await w.invocation(goal, _grant(MAIL))
        assert (await w.broker.call(goal, token, MAIL.identity, {})).status == "returned"
        assert stub.asked == [("google",)]


async def test_health_never_hides_or_grants(broker_db_url):
    async with world(broker_db_url, catalog=StaticCatalog(DESK)) as w:
        stub = w.broker.health = Stub(DOWN)
        goal = await w.goal(_grant(SEARCH))
        _, token = await w.invocation(goal, _grant(SEARCH))
        # Not granted: the authorization refusal wins and health is not even consulted.
        refused = await _refused(w.broker.call(goal, token, MAIL.identity, {}))
        assert refused.reason is Reason.CAPABILITY and refused.unavailable is None
        assert stub.asked == []


async def test_a_capability_without_dependencies_never_consults_health(broker_db_url):
    async with world(broker_db_url, catalog=StaticCatalog(DESK)) as w:
        stub = w.broker.health = Stub(DOWN)
        goal = await w.goal(_grant(PLAIN))
        _, token = await w.invocation(goal, _grant(PLAIN))
        assert (await w.broker.call(goal, token, PLAIN.identity, {})).status == "returned"
        assert stub.asked == []


async def test_without_a_health_source_nothing_changes(broker_db_url):
    async with world(broker_db_url, catalog=StaticCatalog(DESK)) as w:
        assert w.broker.health is None
        goal = await w.goal(_grant(MAIL))
        _, token = await w.invocation(goal, _grant(MAIL))
        assert (await w.broker.call(goal, token, MAIL.identity, {})).status == "returned"


async def test_recovery_is_immediate_once_health_publishes_available(broker_db_url):
    async with world(broker_db_url, catalog=StaticCatalog(DESK)) as w:
        stub = w.broker.health = Stub(DOWN)
        goal = await w.goal(_grant(MAIL))
        _, token = await w.invocation(goal, _grant(MAIL))
        await _refused(w.broker.call(goal, token, MAIL.identity, {}))
        stub.items = (health("available", None, Availability.AVAILABLE),)
        assert (await w.broker.call(goal, token, MAIL.identity, {})).status == "returned"


def test_the_session_sees_closed_words_and_counts_only() -> None:
    from butlers.capability_health.unavailable import first_unavailable

    unavailable = first_unavailable([DOWN])
    data = refusal_data(BrokerRefused(Reason.CAPABILITY_UNAVAILABLE, unavailable=unavailable))
    assert data == {
        "status": "refused",
        "reason": "capability_unavailable",
        "dependency": "google",
        "state": "down",
        "cause": "oauth_revoked",
        "since": "2026-11-11T11:59:15Z",
        "source_age_seconds": 45,
        "retryable": False,
        "generation": 7,
    }
    plain = refusal_data(BrokerRefused(Reason.POLICY))
    assert plain == {"status": "refused", "reason": "policy_refused"}
