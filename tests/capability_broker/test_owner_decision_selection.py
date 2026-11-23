"""The resolver's exact selection rules, without a database (bu-4c7nvp.1.3, PR-B).

``plan_outcomes`` decides what one owner decision does to each item of a set version: a ticked
item takes the decision, an unticked one is left parked (Q3 = B), a rule or schedule change is
decided only through the explicit field (Q4), and any selection that does not name the shown items
exactly refuses the whole decision.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from butlers.capability_broker.owner_decisions import (
    OWNER_DECISIONS_LIVE,
    UNTICKED_OUTCOME,
    DecisionSetResolver,
    OwnerContext,
    OwnerDecisionRefused,
    OwnerDecisions,
    plan_outcomes,
)
from butlers.core.decision_sets import DecisionSetItem


def _item(position: int, *, explicit: bool = False) -> DecisionSetItem:
    return DecisionSetItem(
        position=position,
        effect_id=uuid4(),
        capability="messenger__set_rule" if explicit else "messenger__notify",
        args_digest="a" * 64,
        recipient_entity=None,
        recipient_channel=None,
        explicit_only=explicit,
        item_digest=f"{position:x}" * 64,
    )


PLAIN, OTHER, RULE = _item(0), _item(1), _item(2, explicit=True)
ITEMS = (PLAIN, OTHER, RULE)


def _pick(*items):
    return {item.effect_id: item.item_digest for item in items}


def _reason(*args) -> str:
    with pytest.raises(OwnerDecisionRefused) as refused:
        plan_outcomes(ITEMS, *args)
    return refused.value.reason


def test_ticked_items_take_the_decision_and_unticked_ones_are_left():
    planned = plan_outcomes(ITEMS, _pick(PLAIN), {}, "approve")
    assert [(item, outcome) for item, outcome in planned] == [
        (PLAIN, "approved"),
        (OTHER, UNTICKED_OUTCOME),
        (RULE, UNTICKED_OUTCOME),
    ]
    assert UNTICKED_OUTCOME == "left"  # Q3 = B, pending owner confirmation
    rejected = plan_outcomes(ITEMS, _pick(PLAIN, OTHER), _pick(RULE), "reject")
    assert [outcome for _, outcome in rejected] == ["rejected"] * 3


def test_a_rule_change_is_decided_only_through_the_explicit_field():
    assert _reason(_pick(PLAIN, OTHER, RULE), {}, "approve") == "policy"
    assert _reason({}, _pick(PLAIN), "approve") == "policy"
    planned = plan_outcomes(ITEMS, {}, _pick(RULE), "approve")
    assert [outcome for _, outcome in planned] == ["left", "left", "approved"]


def test_a_selection_that_is_not_exactly_the_shown_items_refuses_the_whole_decision():
    assert _reason({}, {}, "approve") == "policy"
    assert _reason({uuid4(): "0" * 64}, {}, "approve") == "policy"
    assert _reason(_pick(PLAIN), _pick(PLAIN), "approve") == "policy"
    assert _reason(_pick(PLAIN), {}, "maybe") == "policy"
    assert _reason({PLAIN.effect_id: OTHER.item_digest}, {}, "approve") == "stale"
    assert _reason({PLAIN.effect_id: None}, {}, "approve") == "policy"


def test_an_owner_context_names_who_decided_and_through_what():
    assert OwnerContext("owner:entity", "telegram").decided_by == "owner:entity"
    for decided_by, via in (("", "telegram"), ("owner", ""), (None, "x")):
        with pytest.raises(ValueError):
            OwnerContext(decided_by, via)  # type: ignore[arg-type]


class _Untouchable:
    """A broker that fails the test if the dark path touches it at all."""

    def __getattr__(self, name):
        raise AssertionError(f"dark owner decision path touched broker.{name}")


async def test_every_entry_point_is_dark_by_default_and_touches_nothing():
    assert OWNER_DECISIONS_LIVE is False
    dark = OwnerDecisions(_Untouchable())
    owner = OwnerContext("owner:entity", "telegram")
    for attempt in (
        dark.approve(uuid4(), uuid4(), owner),
        dark.reject(uuid4(), uuid4(), owner),
        DecisionSetResolver(dark).decide(uuid4(), 1, {uuid4(): "a" * 64}, {}, "approve", owner),
    ):
        with pytest.raises(OwnerDecisionRefused) as refused:
            await attempt
        assert refused.value.reason == "unavailable"
    with pytest.raises(OwnerDecisionRefused):
        dark.require_live()
    assert await dark.redrive() == 0
