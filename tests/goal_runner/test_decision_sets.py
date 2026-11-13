"""A lead groups exact parked calls into one decision set (RFC 0038 D13, M7, bu-4c7nvp.1.3 PR-A).

The set is a grouping of effects already parked for the owner, never authority of its own: each
item binds one parked effect and a digest over its capability, stored-arguments digest,
recipient and selection class. A changed membership is a new version of the same set; the old
version is kept, superseded, exactly as it was. The read contract is what the card consumer
(bu-jv1moa.27) renders. Goal tool, broker, approvals domain and ledger are real.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from butlers.capability_broker.contracts import Reason
from butlers.core.decision_sets import MAX_SET_ITEMS, DecisionSetLedger, item_digest, set_digest
from tests.goal_runner.test_goal_runner import NOTIFY, RULES, SEARCH, _grant, harness, refused

pytestmark = pytest.mark.integration  # the harness module carries the shared marks
pytest_plugins = ("tests.capability_broker.test_broker_ledger",)


async def _parked(h, goal, token, text: str):
    args = {"entity_id": str(uuid4()), "message": text, "channel": "telegram"}
    parked = await h.w.broker.call(goal, token, NOTIFY.identity, args)
    assert parked.status == "parked"
    return parked.effect_id


async def _read(h, set_id, version=None):
    async with h.w.owner.acquire() as connection:
        return await DecisionSetLedger(connection).read(set_id, version)


async def test_a_lead_groups_parked_calls_into_one_exact_set(broker_db_url):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(NOTIFY), _grant(RULES), _grant(SEARCH))
        lead = await h.lead(goal)
        first = await _parked(h, goal, lead.token, "Running late")
        second = await _parked(h, goal, lead.token, "Table for two")
        rule = await h.w.broker.call(goal, lead.token, RULES.identity, {"rule": "x"})
        assert rule.status == "parked"

        proposed = await h.runner.decide_together(
            goal, lead.token, [str(first), str(rule.effect_id), str(second)]
        )

        decision_set = await _read(h, proposed.set_id)
        assert decision_set == proposed
        assert (decision_set.version, decision_set.state) == (1, "open")
        assert decision_set.goal_id == goal
        lead_row = await h.row(lead.invocation_id)
        assert decision_set.epoch == lead_row["lead_epoch"]
        goal_row = await h.w.owner.fetchrow(
            "SELECT plan_version FROM switchboard.goals WHERE id = $1", goal
        )
        assert decision_set.plan_version == goal_row["plan_version"] == 1
        assert decision_set.expires_at == (await h.budget(goal))["deadline"]
        # Items keep the lead's order; an administer call must be ticked on its own.
        assert [(i.position, i.effect_id, i.explicit_only) for i in decision_set.items] == [
            (0, first, False),
            (1, rule.effect_id, True),
            (2, second, False),
        ]
        for item in decision_set.items:
            effect = await h.w.effect(item.effect_id)
            assert (item.capability, item.args_digest) == (
                effect["capability"],
                effect["args_digest"],
            )
            assert (item.recipient_entity, item.recipient_channel) == (
                effect["recipient_entity"],
                effect["recipient_channel"],
            )
            assert item.item_digest == item_digest(
                item.effect_id,
                item.capability,
                item.args_digest,
                item.recipient_entity,
                item.recipient_channel,
                item.explicit_only,
            )
        assert decision_set.digest == set_digest(
            decision_set.set_id,
            decision_set.version,
            goal,
            decision_set.epoch,
            decision_set.plan_version,
            [item.item_digest for item in decision_set.items],
        )
        # Grouping changes nothing about the calls: each stays parked under its own approval.
        for item in decision_set.items:
            assert (await h.w.effect(item.effect_id))["status"] == "parked"


async def test_a_changed_membership_is_a_new_version_and_the_old_one_is_kept(broker_db_url):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(NOTIFY), _grant(SEARCH))
        lead = await h.lead(goal)
        a, b, c = [await _parked(h, goal, lead.token, text) for text in ("a", "b", "c")]

        v1 = await h.runner.decide_together(goal, lead.token, [str(a), str(b)])
        retry = await h.runner.decide_together(goal, lead.token, [str(a), str(b)])
        assert retry == v1  # the same membership is the same version, not a new one
        v2 = await h.runner.decide_together(goal, lead.token, [str(a), str(c)])

        assert (v2.set_id, v2.version, v2.state) == (v1.set_id, 2, "open")
        old = await _read(h, v1.set_id, 1)
        assert old.state == "superseded"
        assert old.items == v1.items and old.digest == v1.digest
        assert (await _read(h, v1.set_id)).version == 2  # no version: the latest
        assert (
            await h.w.owner.fetchval(
                "SELECT count(*) FROM switchboard.decision_set_versions WHERE goal_id = $1", goal
            )
            == 2
        )


async def test_only_this_goals_parked_calls_can_be_grouped(broker_db_url):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(NOTIFY), _grant(SEARCH))
        lead = await h.lead(goal)
        a = await _parked(h, goal, lead.token, "a")
        b = await _parked(h, goal, lead.token, "b")
        other = await h.goal(_grant(NOTIFY), _grant(SEARCH))
        other_lead = await h.lead(other, 2)
        foreign = await _parked(h, other, other_lead.token, "x")

        cases = {
            Reason.RESOURCE: [
                [str(a), str(foreign)],  # another goal's effect
                [str(a), str(uuid4())],  # no such effect
            ],
            Reason.POLICY: [
                [str(a)],  # a set has at least two items
                [str(a), str(a)],  # each effect once
                [str(a), "not-a-uuid"],
                [str(uuid4()) for _ in range(MAX_SET_ITEMS + 1)],
            ],
        }
        for reason, effect_ids in ((r, ids) for r, many in cases.items() for ids in many):
            assert await refused(h.runner.decide_together(goal, lead.token, effect_ids)) is reason

        # A call that is no longer parked cannot be grouped.
        await h.w.owner.execute(
            "UPDATE switchboard.effects SET status = 'rejected' WHERE id = $1", b
        )
        assert (
            await refused(h.runner.decide_together(goal, lead.token, [str(a), str(b)]))
            is Reason.RESOURCE
        )
        assert not await h.w.owner.fetchval("SELECT count(*) FROM switchboard.decision_sets")


async def test_a_child_cannot_group_calls_and_a_stopped_goal_cannot_either(broker_db_url):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(NOTIFY), _grant(SEARCH))
        lead = await h.lead(goal)
        a = await _parked(h, goal, lead.token, "a")
        b = await _parked(h, goal, lead.token, "b")
        child, child_token = await h.w.invocation(
            goal, _grant(NOTIFY), depth=1, parent=lead.invocation_id
        )
        assert child
        assert (
            await refused(h.runner.decide_together(goal, child_token, [str(a), str(b)]))
            is Reason.CAPABILITY
        )
        await h.runner.stop(goal)
        stopped = await refused(h.runner.decide_together(goal, lead.token, [str(a), str(b)]))
        assert stopped in (Reason.GOAL, Reason.AUTHENTICATION)
        assert not await h.w.owner.fetchval("SELECT count(*) FROM switchboard.decision_sets")


async def test_the_goal_tool_stays_dark_until_the_resolver_lands(broker_db_url):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(NOTIFY), _grant(SEARCH))
        lead = await h.lead(goal)
        assert "goal__decide_together" not in {tool.name for tool in h.tools.listed(0)}
        dark = await refused(h.call(goal, lead.token, "goal__decide_together", {"effect_ids": []}))
        assert dark is Reason.CAPABILITY
