"""``goal.plan`` reference lists: the closed spelling, before any ledger read (no database)."""

from __future__ import annotations

import uuid

import pytest

from butlers.capability_broker.contracts import BrokerRefused, Reason
from butlers.goal_runner.ledger import MAX_PLAN_REFS, plan_refs

pytestmark = pytest.mark.unit

ID = "6f1c2a4e-8d3b-4c5a-9e7f-0a1b2c3d4e5f"


def test_each_reference_kind_is_accepted_in_its_canonical_spelling():
    refs = [f"effect:{ID}", f"inbox:{ID}", f"child:{ID}", "plan:1", "plan:12"]
    assert plan_refs(refs) == tuple(refs)
    assert plan_refs([]) == ()
    assert len(plan_refs([f"effect:{uuid.uuid4()}" for _ in range(MAX_PLAN_REFS)])) == 32


@pytest.mark.parametrize(
    "value",
    [
        None,
        "effect:" + ID,  # a string, not a list
        ["book the 7pm table"],
        [f"outcome:{ID}"],
        [f"effect:{ID.upper()}"],
        [f"effect:{uuid.UUID(ID).hex}"],
        [f"effect:{{{ID}}}"],
        ["plan:0"],
        ["plan:01"],
        ["plan:-1"],
        ["plan:١"],  # a non-ASCII digit
        ["plan:"],
        [7],
        [f"effect:{ID}", f"effect:{ID}"],
        [f"effect:{uuid.UUID(int=n)}" for n in range(MAX_PLAN_REFS + 1)],
    ],
)
def test_anything_else_is_a_policy_refusal(value):
    with pytest.raises(BrokerRefused) as refused:
        plan_refs(value)
    assert refused.value.reason is Reason.POLICY
