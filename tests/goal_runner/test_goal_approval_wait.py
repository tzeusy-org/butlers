"""A lead waits on a parked effect; its approval continues the goal, a decline ends it.

RFC 0038 S2.2 (bu-dhloi1.204, bu-dhloi1.7 PR B3). ``goal.wait_approval`` ends the lead and opens an
``approval`` envelope bound to the exact parked effect. The sweep watches that effect: once it has
settled after an approval, the envelope's one wake admits a successor summary lead at the next
epoch; once it is rejected, expired or cancelled the goal ends ``partial`` and no lead is launched.
Stop cancels the envelope with the parked effect.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from unittest.mock import patch
from uuid import uuid4

import pytest

from butlers.capability_broker.contracts import Reason
from butlers.core.goal_waiting import WaitLedger
from butlers.core.goal_wake import WakeAdmission, WakeResult
from tests.goal_runner.test_goal_runner import NOTIFY, SEARCH, _grant, harness, refused

pytestmark = pytest.mark.integration  # the harness module carries the shared marks
pytest_plugins = ("tests.capability_broker.test_broker_ledger",)


async def parked_and_waiting(h):
    goal = await h.goal(_grant(NOTIFY), _grant(SEARCH))
    lead = await h.lead(goal)
    args = {"entity_id": str(uuid4()), "message": "Running late", "channel": "telegram"}
    parked = await h.w.broker.call(goal, lead.token, NOTIFY.identity, args)
    assert parked.status == "parked"
    result = await h.call(
        goal, lead.token, "goal__wait_approval", {"effect_id": str(parked.effect_id)}
    )
    assert result == {"status": "waiting"}
    return goal, lead, parked


async def envelope(h, goal):
    return await h.w.owner.fetchrow(
        "SELECT kind, state, consumed_wakes, approval_effect_id FROM switchboard.wait_envelopes "
        "WHERE goal_id = $1",
        goal,
    )


async def test_an_approved_effect_continues_the_goal_through_a_successor_lead(broker_db_url):
    async with harness(broker_db_url) as h:
        goal, lead, parked = await parked_and_waiting(h)
        assert await h.goal_status(goal) == "waiting"
        assert (await h.row(lead.invocation_id))["status"] == "returned"
        env = await envelope(h, goal)
        assert (env["kind"], env["state"], env["approval_effect_id"]) == (
            "approval",
            "open",
            parked.effect_id,
        )
        # Still parked: the sweep leaves the wait alone.
        await h.runner.supervise_once()
        assert await h.goal_status(goal) == "waiting" and len(h.host.launches) == 1
        # The owner approves; the effect runs to its terminal state.
        for status in ("approved", "dispatched", "committed"):
            await h.w.owner.execute(
                "UPDATE switchboard.effects SET status = $2 WHERE id = $1", parked.effect_id, status
            )
        await h.runner.supervise_once()
        woken = await h.launched(2)
        row = await h.row(woken.invocation_id)
        assert (row["depth"], row["lead_epoch"]) == (0, 1)
        assert await h.goal_status(goal) == "running"
        env = await envelope(h, goal)
        assert (env["state"], env["consumed_wakes"]) == ("fired", 1)
        # Another sweep neither wakes it twice nor launches again.
        await h.runner.supervise_once()
        assert len(h.host.launches) == 2
        assert await h.w.owner.fetchval("SELECT count(*) FROM switchboard.wake_intents") == 1


async def test_a_declined_effect_ends_the_goal_without_a_successor(broker_db_url):
    async with harness(broker_db_url) as h:
        goal, _, parked = await parked_and_waiting(h)
        await h.w.domain.execute(
            "UPDATE pending_actions SET status = 'rejected' WHERE id = $1", parked.effect_id
        )
        await h.runner.supervise_once()
        assert await h.goal_status(goal) == "partial"
        assert (await envelope(h, goal))["state"] == "cancelled"
        assert len(h.host.launches) == 1
        assert not await h.w.owner.fetchval("SELECT count(*) FROM switchboard.wake_intents")
        # The decline is not a wake: nothing is counted against the envelope.
        assert (await envelope(h, goal))["consumed_wakes"] == 0


async def test_stop_while_waiting_cancels_the_parked_effect_and_the_wait(broker_db_url):
    async with harness(broker_db_url) as h:
        goal, _, parked = await parked_and_waiting(h)
        await h.runner.stop(goal)
        assert await h.goal_status(goal) == "stopped"
        assert (await h.w.effect(parked.effect_id))["status"] == "cancelled"
        assert (await envelope(h, goal))["state"] == "cancelled"
        await h.runner.supervise_once()
        assert len(h.host.launches) == 1


async def test_the_wait_must_name_an_effect_parked_for_this_goal(broker_db_url):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(NOTIFY), _grant(SEARCH))
        lead = await h.lead(goal)
        missing = await refused(
            h.call(goal, lead.token, "goal__wait_approval", {"effect_id": str(uuid4())})
        )
        assert missing is Reason.RESOURCE
        assert await h.goal_status(goal) == "running"
        assert (await h.row(lead.invocation_id))["status"] == "running"


async def test_an_approval_wait_that_expires_ends_the_goal(broker_db_url):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(NOTIFY), _grant(SEARCH))
        lead = await h.lead(goal)
        await h.w.owner.execute(
            "UPDATE switchboard.budgets SET deadline = now() + interval '1 second' "
            "WHERE goal_id = $1",
            goal,
        )
        args = {"entity_id": str(uuid4()), "message": "Running late", "channel": "telegram"}
        parked = await h.w.broker.call(goal, lead.token, NOTIFY.identity, args)
        await h.call(goal, lead.token, "goal__wait_approval", {"effect_id": str(parked.effect_id)})
        await asyncio.sleep(1.2)

        await h.runner.supervise_once()

        assert await h.goal_status(goal) == "failed"
        assert (await envelope(h, goal))["state"] == "expired"
        assert len(h.host.launches) == 1


async def test_an_approval_wait_converted_by_sw_064_continues_when_its_effect_runs(broker_db_url):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(NOTIFY), _grant(SEARCH))
        lead = await h.lead(goal)
        args = {"entity_id": str(uuid4()), "message": "Running late", "channel": "telegram"}
        parked = await h.w.broker.call(goal, lead.token, NOTIFY.identity, args)

        # sw_064 registers a converted approval wait under a legacy instance id.
        original = WaitLedger.open_envelope

        async def legacy(self, spec):
            ids = [f"legacy:awaiting_approval:{spec.goal_id}"]
            return await original(self, replace(spec, condition_instance_ids=ids))

        with patch.object(WaitLedger, "open_envelope", legacy):
            await h.call(
                goal, lead.token, "goal__wait_approval", {"effect_id": str(parked.effect_id)}
            )
        for status in ("approved", "dispatched", "committed"):
            await h.w.owner.execute(
                "UPDATE switchboard.effects SET status = $2 WHERE id = $1", parked.effect_id, status
            )

        await h.runner.supervise_once()

        woken = await h.launched(2)
        assert (await h.row(woken.invocation_id))["lead_epoch"] == 1
        assert (await envelope(h, goal))["state"] == "fired"


async def test_an_approval_wake_that_maps_to_nothing_ends_the_goal(broker_db_url):
    async with harness(broker_db_url) as h:
        goal, _, parked = await parked_and_waiting(h)
        for status in ("approved", "dispatched", "committed"):
            await h.w.owner.execute(
                "UPDATE switchboard.effects SET status = $2 WHERE id = $1", parked.effect_id, status
            )

        async def unmapped(self, fire):
            return WakeResult("unmapped")

        with patch.object(WakeAdmission, "admit", unmapped):
            await h.runner.supervise_once()

        assert await h.goal_status(goal) == "failed"
        assert len(h.host.launches) == 1


async def test_a_settle_wait_that_expires_resolves_its_outcome_partial(broker_db_url):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(NOTIFY), _grant(SEARCH))
        lead = await h.lead(goal)
        await h.w.owner.execute(
            "UPDATE switchboard.budgets SET deadline = now() + interval '1 second' "
            "WHERE goal_id = $1",
            goal,
        )
        args = {"entity_id": str(uuid4()), "message": "Running late", "channel": "telegram"}
        parked = await h.w.broker.call(goal, lead.token, NOTIFY.identity, args)
        await h.call(
            goal,
            lead.token,
            "goal__complete",
            {"outcome": "completed", "relied_effects": [str(parked.effect_id)]},
        )
        assert await h.goal_status(goal) == "waiting"
        await asyncio.sleep(1.2)

        await h.runner.supervise_once()

        assert await h.goal_status(goal) == "partial"
        final = await h.w.owner.fetchval(
            "SELECT final_status FROM switchboard.goal_outcomes WHERE goal_id = $1", goal
        )
        assert final == "partial"
        assert (await envelope(h, goal))["state"] == "expired"
