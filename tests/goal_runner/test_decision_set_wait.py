"""A lead waits on the owner's decision of one decision set (RFC 0038 D13, S2.2; bu-4c7nvp.1.3 B2).

``goal.wait_decision_set`` ends the lead and opens a ``decision_set`` envelope bound to the exact
open version of the set, proposed at the lead's epoch under the goal's current plan. The sweep
watches that version. Once the owner approved it and every ticked item has settled, the
envelope's one wake admits a successor summary lead; an item left unticked stays parked and holds
nothing. A rejected set ends the goal ``partial``. A set made stale while the goal waits (an item
decided on its own, N3) is dead: its version is cancelled, so the owner can no longer decide it,
and the successor wakes once the decided item settled. Stop cancels the wait and the set. The
goal tool stays dark with ``goal.decide_together``; the runner path is exercised directly.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch
from uuid import uuid4

import pytest

from butlers.capability_broker.contracts import BrokerRefused, Reason
from butlers.capability_broker.owner_decisions import (
    DecisionSetResolver,
    OwnerContext,
    OwnerDecisionRefused,
    OwnerDecisions,
)
from butlers.core.goal_waiting import WaitLedger
from butlers.core.goal_wake import WakeAdmission, WakeResult
from tests.goal_runner.test_goal_runner import NOTIFY, SEARCH, _grant, harness, refused

pytestmark = pytest.mark.integration  # the harness module carries the shared marks
pytest_plugins = ("tests.capability_broker.test_broker_ledger",)

OWNER = OwnerContext(decided_by="owner:test", via="test")


async def _parked(h, goal, token, text: str):
    args = {"entity_id": str(uuid4()), "message": text, "channel": "telegram"}
    parked = await h.w.broker.call(goal, token, NOTIFY.identity, args)
    assert parked.status == "parked"
    return parked.effect_id


async def waiting_on_a_set(h, items: int = 2):
    goal = await h.goal(_grant(NOTIFY), _grant(SEARCH))
    lead = await h.lead(goal)
    effects = [await _parked(h, goal, lead.token, f"note {i}") for i in range(items)]
    proposed = await h.runner.decide_together(goal, lead.token, [str(e) for e in effects])
    status = await h.runner.wait_decision_set(goal, lead.token, str(proposed.set_id))
    assert status == "waiting"
    return goal, lead, proposed


async def envelope(h, goal):
    return await h.w.owner.fetchrow(
        "SELECT kind, state, consumed_wakes, decision_set_id, decision_set_version "
        "FROM switchboard.wait_envelopes WHERE goal_id = $1",
        goal,
    )


async def set_state(h, proposed):
    return await h.w.owner.fetchval(
        "SELECT state FROM switchboard.decision_set_versions WHERE set_id = $1 AND version = $2",
        proposed.set_id,
        proposed.version,
    )


async def decide(h, proposed, decision="approve", skip=()):
    picks = {i.effect_id: i.item_digest for i in proposed.items if i.effect_id not in skip}
    resolver = DecisionSetResolver(OwnerDecisions(h.w.broker, live=True))
    return await resolver.decide(proposed.set_id, proposed.version, picks, {}, decision, OWNER)


async def test_a_decided_set_continues_the_goal_through_one_successor(broker_db_url):
    async with harness(broker_db_url) as h:
        goal, lead, proposed = await waiting_on_a_set(h)
        assert await h.goal_status(goal) == "waiting"
        assert (await h.row(lead.invocation_id))["status"] == "returned"
        env = await envelope(h, goal)
        assert tuple(env) == ("decision_set", "open", 0, proposed.set_id, proposed.version)
        # Undecided: the sweep leaves the wait alone.
        await h.runner.supervise_once()
        assert await h.goal_status(goal) == "waiting" and len(h.host.launches) == 1

        decided = await decide(h, proposed)
        assert [o.status for o in decided.outcomes] == ["committed", "committed"]
        await h.runner.supervise_once()

        woken = await h.launched(2)
        row = await h.row(woken.invocation_id)
        assert (row["depth"], row["lead_epoch"]) == (0, 1)
        assert await h.goal_status(goal) == "running"
        assert (await envelope(h, goal))[1:3] == ("fired", 1)
        # The successor's epoch leaves the decided version decided.
        assert await set_state(h, proposed) == "decided"
        await h.runner.supervise_once()
        assert len(h.host.launches) == 2
        assert await h.w.owner.fetchval("SELECT count(*) FROM switchboard.wake_intents") == 1


async def test_an_item_left_unticked_does_not_hold_the_wake(broker_db_url):
    async with harness(broker_db_url) as h:
        goal, _, proposed = await waiting_on_a_set(h, items=3)
        left = proposed.items[2].effect_id
        await decide(h, proposed, skip=(left,))
        await h.runner.supervise_once()
        await h.launched(2)
        assert (await h.w.effect(left))["status"] == "parked"


async def test_a_left_item_approved_on_its_own_later_does_not_hold_the_wake(broker_db_url):
    async with harness(broker_db_url) as h:
        goal, _, proposed = await waiting_on_a_set(h, items=3)
        left = proposed.items[2].effect_id
        await decide(h, proposed, skip=(left,))
        real = h.w.broker.execute_approved

        async def left_unavailable(goal_id, effect_id, approval_ref, arguments):
            if effect_id == left:
                raise BrokerRefused(Reason.UNAVAILABLE)
            return await real(goal_id, effect_id, approval_ref, arguments)

        # The owner later approves the left item on its own; its dispatch is still pending.
        h.w.broker.execute_approved = left_unavailable
        await OwnerDecisions(h.w.broker, live=True).approve(goal, left, OWNER)
        assert (await h.w.effect(left))["status"] == "approved"
        await h.runner.supervise_once()
        await h.launched(2)


async def test_two_concurrent_sweeps_wake_a_decided_set_once(broker_db_url):
    async with harness(broker_db_url) as h:
        goal, _, proposed = await waiting_on_a_set(h)
        await decide(h, proposed)
        await asyncio.gather(h.runner.supervise_once(), h.runner.supervise_once())
        await h.launched(2)
        assert await h.w.owner.fetchval("SELECT count(*) FROM switchboard.wake_intents") == 1
        assert (await envelope(h, goal))[1:3] == ("fired", 1)
        await h.runner.supervise_once()
        assert len(h.host.launches) == 2


async def test_a_ticked_item_still_running_holds_the_wake(broker_db_url):
    async with harness(broker_db_url) as h:
        goal, _, proposed = await waiting_on_a_set(h)
        first, second = (item.effect_id for item in proposed.items)
        real = h.w.broker.execute_approved

        async def second_unavailable(goal_id, effect_id, approval_ref, arguments):
            if effect_id == second:
                raise BrokerRefused(Reason.UNAVAILABLE)
            return await real(goal_id, effect_id, approval_ref, arguments)

        h.w.broker.execute_approved = second_unavailable
        await decide(h, proposed)
        # A transient refusal leaves the second item approved, for the re-drive.
        assert (await h.w.effect(first))["status"] == "committed"
        assert (await h.w.effect(second))["status"] == "approved"
        await h.runner.supervise_once()
        assert len(h.host.launches) == 1 and await h.goal_status(goal) == "waiting"

        h.w.broker.execute_approved = real
        assert await OwnerDecisions(h.w.broker, live=True).redrive() == 1
        await h.runner.supervise_once()
        await h.launched(2)


async def test_a_rejected_set_ends_the_goal_without_a_successor(broker_db_url):
    async with harness(broker_db_url) as h:
        goal, _, proposed = await waiting_on_a_set(h)
        await decide(h, proposed, decision="reject")
        await h.runner.supervise_once()
        assert await h.goal_status(goal) == "partial"
        assert (await envelope(h, goal))[1:3] == ("cancelled", 0)
        assert len(h.host.launches) == 1
        assert not await h.w.owner.fetchval("SELECT count(*) FROM switchboard.wake_intents")


async def test_a_set_made_stale_by_a_single_item_decision_is_dead_to_the_wake(broker_db_url):
    async with harness(broker_db_url) as h:
        goal, _, proposed = await waiting_on_a_set(h)
        first, second = (item.effect_id for item in proposed.items)
        # The owner decides one item on its own while the goal waits on the set (N3).
        single = await OwnerDecisions(h.w.broker, live=True).approve(goal, first, OWNER)
        assert single.status == "committed"

        await h.runner.supervise_once()

        # The set can never be decided now: its version is cancelled, and the successor wakes
        # to see what changed. The other item stays parked for its own decision.
        assert await set_state(h, proposed) == "cancelled"
        woken = await h.launched(2)
        assert (await h.row(woken.invocation_id))["lead_epoch"] == 1
        assert (await envelope(h, goal))[1:3] == ("fired", 1)
        assert (await h.w.effect(second))["status"] == "parked"
        with pytest.raises(OwnerDecisionRefused) as late:
            await decide(h, proposed, skip=(first,))
        assert late.value.reason == "stale"
        await h.runner.supervise_once()
        assert len(h.host.launches) == 2


async def test_stop_while_waiting_cancels_the_set_and_the_wait(broker_db_url):
    async with harness(broker_db_url) as h:
        goal, _, proposed = await waiting_on_a_set(h)
        await h.runner.stop(goal)
        assert await h.goal_status(goal) == "stopped"
        assert await set_state(h, proposed) == "cancelled"
        assert (await envelope(h, goal))[1] == "cancelled"
        for item in proposed.items:
            assert (await h.w.effect(item.effect_id))["status"] == "cancelled"
        await h.runner.supervise_once()
        assert len(h.host.launches) == 1


async def test_only_this_leads_fresh_open_set_can_be_waited_on(broker_db_url):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(NOTIFY), _grant(SEARCH))
        lead = await h.lead(goal)
        a, b, c = [await _parked(h, goal, lead.token, text) for text in ("a", "b", "c")]

        async def wait(set_id, token=lead.token):
            return await refused(h.runner.wait_decision_set(goal, token, set_id))

        assert await wait(str(uuid4())) is Reason.RESOURCE
        assert await wait("not-a-uuid") is Reason.POLICY
        # A plan changed after the set was proposed has superseded it (sw_074).
        stale = await h.runner.decide_together(goal, lead.token, [str(a), str(b)])
        async with h.w.owner.acquire() as connection, connection.transaction():
            await WaitLedger(connection).append_plan(goal, 0, ["moved"], [], [])
        assert await set_state(h, stale) == "superseded"
        assert await wait(str(stale.set_id)) is Reason.RESOURCE
        # An item decided on its own since the set was proposed: the set is stale (N3).
        decided = await h.runner.decide_together(goal, lead.token, [str(a), str(b)])
        await OwnerDecisions(h.w.broker, live=True).reject(goal, a, OWNER)
        assert await wait(str(decided.set_id)) is Reason.RESOURCE
        # Another goal's set is not this lead's to wait on.
        other = await h.goal(_grant(NOTIFY), _grant(SEARCH))
        other_lead = await h.lead(other, 2)
        x = await _parked(h, other, other_lead.token, "x")
        y = await _parked(h, other, other_lead.token, "y")
        foreign = await h.runner.decide_together(other, other_lead.token, [str(x), str(y)])
        assert await wait(str(foreign.set_id)) is Reason.RESOURCE
        # A child never waits; the lead's goal stays running with its lead live.
        fresh = await h.runner.decide_together(goal, lead.token, [str(b), str(c)])
        _, child_token = await h.w.invocation(
            goal, _grant(NOTIFY), depth=1, parent=lead.invocation_id
        )
        assert await wait(str(fresh.set_id), child_token) is Reason.CAPABILITY
        assert await h.goal_status(goal) == "running"
        assert (await h.row(lead.invocation_id))["status"] == "running"
        assert not await h.w.owner.fetchval(
            "SELECT count(*) FROM switchboard.wait_envelopes WHERE goal_id = $1", goal
        )


async def test_a_set_wake_that_maps_to_nothing_ends_the_goal(broker_db_url):
    async with harness(broker_db_url) as h:
        goal, _, proposed = await waiting_on_a_set(h)
        await decide(h, proposed)

        async def unmapped(self, fire):
            return WakeResult("unmapped")

        with patch.object(WakeAdmission, "admit", unmapped):
            await h.runner.supervise_once()

        assert await h.goal_status(goal) == "failed"
        assert len(h.host.launches) == 1


async def test_the_goal_tool_stays_dark_with_decide_together(broker_db_url):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(NOTIFY), _grant(SEARCH))
        lead = await h.lead(goal)
        assert "goal__wait_decision_set" not in {tool.name for tool in h.tools.listed(0)}
        dark = await refused(
            h.call(goal, lead.token, "goal__wait_decision_set", {"set_id": str(uuid4())})
        )
        assert dark is Reason.CAPABILITY
