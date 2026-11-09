"""D9 delegation shape and child grant, and the D16 ladders (no database)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from butlers.capability_broker.contracts import BrokerRefused, Catalog, Reason
from butlers.core.goal_admission import Grant
from butlers.goal_runner.delegation import child_grant, parse_delegation
from butlers.goal_runner.models import ModelLadders, next_tier
from tests.capability_broker.helpers import capability

pytestmark = pytest.mark.unit

SCHEMA = {
    "type": "object",
    "properties": {"found": {"type": "boolean"}},
    "required": ["found"],
    "additionalProperties": False,
}
SEARCH = capability("search", "read", resource_params=("account",))
SEND = capability("send", "communicate")
RULES = capability("set_rule", "administer")
PAY = capability("pay", "transact")
CATALOG = Catalog("c1", (SEARCH, SEND, RULES, PAY))


def request(**overrides):
    value = {
        "brief": {"objective": "Find the receipt", "result_schema": SCHEMA},
        "capabilities": [{"capability": SEARCH.identity}],
        "model_tier": "cheap",
        "budget_share": {"calls": 3, "tokens": 100, "wall_seconds": 60},
    }
    value.update(overrides)
    return value


def refused(call) -> Reason:
    with pytest.raises(BrokerRefused) as error:
        call()
    return error.value.reason


def test_a_well_formed_request_parses_into_brief_grants_tier_and_share() -> None:
    parsed = parse_delegation(
        request(capabilities=[{"capability": SEARCH.identity, "resources": {"account": ["a1"]}}])
    )
    assert parsed.brief.objective == "Find the receipt"
    assert parsed.capabilities == (Grant(SEARCH.identity, (("account", ("a1",)),)),)
    assert (parsed.share.calls, parsed.share.tokens, parsed.share.wall_seconds) == (3, 100, 60)
    assert parsed.result_schema.document == SCHEMA


@pytest.mark.parametrize("asserted", ["depth", "parent", "root", "parent_invocation_id", "goal_id"])
def test_a_request_cannot_assert_lineage(asserted) -> None:
    assert refused(lambda: parse_delegation(request(**{asserted: 0}))) is Reason.POLICY


@pytest.mark.parametrize(
    "overrides",
    [
        {"brief": {"objective": "x"}},  # no result schema
        {"brief": {"objective": "x", "result_schema": {"type": "string"}}},
        {"brief": {"objective": "", "result_schema": SCHEMA}},
        {"budget_share": {"calls": 0, "tokens": 1, "wall_seconds": 1}},
        {"budget_share": {"calls": 1, "tokens": 1}},
        {"budget_share": {"calls": True, "tokens": 1, "wall_seconds": 1}},
        {"capabilities": [{"capability": "general__search"}, {"capability": "general__search"}]},
        {"capabilities": [{"capability": "general__s*"}]},
        {"capabilities": [{"capability": "general__search", "resources": {"account": "a1"}}]},
        {"model_tier": 3},
    ],
)
def test_a_malformed_request_refuses_whole(overrides) -> None:
    assert refused(lambda: parse_delegation(request(**overrides))) is Reason.POLICY


def test_child_grant_is_the_request_within_the_leads_grant() -> None:
    lead = (Grant(SEARCH.identity, (("account", ("a1", "a2")),)), Grant(SEND.identity))
    asked = (Grant(SEARCH.identity, (("account", ("a1",)),)),)
    assert child_grant(asked, lead, CATALOG) == asked


def test_park_only_travels_down_from_the_lead() -> None:
    lead = (Grant(SEND.identity, park_only=True),)
    assert child_grant((Grant(SEND.identity),), lead, CATALOG) == (
        Grant(SEND.identity, park_only=True),
    )


@pytest.mark.parametrize(
    "asked",
    [
        (),  # empty
        (Grant(SEND.identity),),  # the lead lacks it
        (Grant(SEARCH.identity),),  # wider than the lead's account scope
        (Grant(SEARCH.identity, (("account", ("a3",)),)),),  # outside the scope
        (Grant("general__unpublished"),),
    ],
)
def test_an_empty_or_non_subset_request_is_refused_not_narrowed(asked) -> None:
    lead = (
        Grant(SEARCH.identity, (("account", ("a1",)),)),
        Grant("general__unpublished"),
    )
    assert refused(lambda: child_grant(asked, lead, CATALOG)) is Reason.CAPABILITY


def test_administer_is_removed_and_a_grant_of_only_administer_is_empty() -> None:
    lead = (Grant(SEARCH.identity), Grant(RULES.identity), Grant(PAY.identity))
    asked = (Grant(SEARCH.identity), Grant(RULES.identity), Grant(PAY.identity))
    assert child_grant(asked, lead, CATALOG) == (Grant(SEARCH.identity),)
    only = (Grant(RULES.identity),)
    assert refused(lambda: child_grant(only, lead, CATALOG)) is Reason.CAPABILITY


def test_ladders_are_finite_and_children_never_escalate() -> None:
    ladders = ModelLadders()
    assert ladders.above("lead", "workhorse") == ("reasoning",)
    assert ladders.above("lead", "reasoning") == ()
    assert ladders.above("child", "cheap") == ()
    assert ladders.above("lead", "legacy") == ()
    with pytest.raises(ValueError):
        ModelLadders(lead=("workhorse", "workhorse"))
    with pytest.raises(ValueError):
        ladders.first("child")


async def test_next_tier_climbs_only_to_a_tier_that_resolves_exactly() -> None:
    ladders = ModelLadders(monitor=("cheap", "workhorse", "reasoning"))

    async def resolve(pool, role, tier, *, allow_tier_fallthrough):
        assert allow_tier_fallthrough is False and role == "monitor"
        # workhorse is empty: a fallthrough resolver would hand back cheap.
        return None if tier == "workhorse" else ("rt", f"m-{tier}", [], None, 30, tier)

    with patch(
        "butlers.goal_runner.models.resolve_model_with_effective_tier",
        AsyncMock(side_effect=resolve),
    ):
        assert await next_tier(None, ladders, "monitor", "cheap") == "reasoning"
        assert await next_tier(None, ladders, "monitor", "reasoning") is None
