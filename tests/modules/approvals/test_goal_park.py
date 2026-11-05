"""Park-time standing-rule admission and Stop scoping for goal effects (bu-cadcr1).

A goal effect may be auto-approved at park time only by a rule the approval
gate itself would admit, and an exposed lineage further needs a rule whose own
recipient is the owner or a template-pinned rule (REQ-capability-authorization-006).
These drive ``park_goal_effect`` over an in-memory connection; the real-Postgres
path is covered by ``tests/capability_broker/test_broker_ledger.py``.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from butlers.capability_broker import domains
from butlers.modules.approvals import goal_park
from butlers.modules.approvals.goal_park import GoalLineage, park_goal_effect

OWNER = str(uuid4())


class _Connection:
    def __init__(self, rules):
        self.rules, self.claimed = rules, []

    async def fetchrow(self, sql, *args):
        if sql.startswith("SELECT id, approval_rule_id"):
            return None
        if sql.startswith("WITH claimed"):
            self.claimed.append(args[2])
            return {"id": args[0]}
        raise AssertionError(sql)

    async def fetch(self, sql, *args):
        return [rule for rule in self.rules if rule["tool_name"] == args[0]]

    async def execute(self, sql, *args):
        return "UPDATE 1"

    @asynccontextmanager
    async def transaction(self):
        yield


class _Pool:
    def __init__(self, connection):
        self.connection = connection

    @asynccontextmanager
    async def acquire(self):
        yield self.connection


def _rule(tool_name, constraints):
    return {
        "id": uuid4(),
        "tool_name": tool_name,
        "arg_constraints": constraints,
        "description": "rule",
        "created_from": None,
        "created_at": datetime.now(UTC),
        "expires_at": None,
        "max_uses": None,
        "use_count": 0,
        "active": True,
    }


def _exact(value):
    return {"type": "exact", "value": value}


@pytest.fixture(autouse=True)
def _no_domain_writes(monkeypatch):
    async def park(*args, **kwargs):
        return None

    async def event(*args, **kwargs):
        return None

    monkeypatch.setattr(goal_park, "park_pending_action", park)
    monkeypatch.setattr(goal_park, "record_approval_event", event)


async def _park(tool_name, args, constraints, *, exposed, recipient_is_owner, **kwargs):
    connection = _Connection([_rule(tool_name, constraints)])
    outcome = await park_goal_effect(
        _Pool(connection),
        lineage=GoalLineage(uuid4(), uuid4(), 0, exposed, "owner", uuid4()),
        tool_name=tool_name,
        tool_args=args,
        allow_standing_rule=True,
        recipient_is_owner=recipient_is_owner,
        origin_butler="messenger",
        **kwargs,
    )
    return outcome.rule_id is not None


@pytest.mark.parametrize(
    ("case", "args", "constraints", "recipient_is_owner", "approved"),
    [
        # The call's recipient being the owner never stands in for the rule's.
        ("wildcard rule, owner call", {"to": OWNER, "text": "x"}, {}, True, False),
        ("free-text body", {"to": OWNER, "text": "x"}, {"text": {"type": "any"}}, True, False),
        # all() over no arguments is not a template pin.
        ("empty args, empty rule", {}, {}, False, False),
        ("owner-recipient rule", {"to": OWNER, "text": "x"}, {"to": _exact(OWNER)}, True, True),
        (
            "owner-pinned rule, non-owner call",
            {"to": "someone", "text": "x"},
            {"to": _exact("someone")},
            False,
            False,
        ),
        (
            "template pinned",
            {"to": "someone", "text": "x"},
            {"to": _exact("someone"), "text": _exact("x")},
            False,
            True,
        ),
    ],
)
async def test_exposed_rule_eligibility_keys_on_the_rule(
    case, args, constraints, recipient_is_owner, approved
):
    assert (
        await _park(
            "send",
            args,
            constraints,
            exposed=True,
            recipient_is_owner=recipient_is_owner,
            recipient_args=("to",),
        )
        is approved
    ), case


async def test_fact_assertion_never_rule_approves_at_goal_park():
    """The gate admits a fact rule only after its owner-permission check and arg freeze."""
    args = {"subject": str(uuid4()), "predicate": "likes", "object": "tea"}
    assert not await _park(
        "relationship_assert_fact", args, {}, exposed=False, recipient_is_owner=False
    )


async def test_unpinned_safety_argument_parks_like_the_gate():
    args = {"connector_type": "gmail", "endpoint_identity": "a@example.com"}
    constraints = {"connector_type": _exact("gmail")}
    assert not await _park(
        "connector_disconnect",
        args,
        constraints,
        exposed=False,
        recipient_is_owner=False,
        safety_args=("connector_type", "endpoint_identity"),
    )


async def test_cancel_is_scoped_to_the_goals_own_approval_refs(monkeypatch):
    calls = []

    async def cancel(pool, goal_id, action_ids):
        calls.append((pool, goal_id, action_ids))
        return action_ids

    monkeypatch.setattr(domains, "cancel_goal_effects", cancel)
    goal, ref = uuid4(), uuid4()
    await domains.ApprovalsParking({"messenger": "pool"}).cancel("messenger", goal, [str(ref)])
    assert calls == [("pool", goal, [ref])]
