"""The goal-event feed wakes a goal waiting on its own conditions (bu-dhloi1.305 PR2).

``GoalRunner.feed_conditions`` syncs the domain's decisions onto the goal's parked effects, feeds
the goal's own decision and settlement events to its condition instance, and admits the fire as
the wait's one wake. It is dark: the sweep does not call it until feed-triggered wakes are
narrowed (PR5), so these tests call it directly.
"""

from __future__ import annotations

from unittest.mock import patch
from uuid import UUID, uuid4

import pytest

from butlers.conditions import ConditionEvaluator
from tests.goal_runner.test_goal_runner import SEARCH, _grant, harness
from tests.goal_runner.test_goal_wait_until import (
    decision,
    envelope,
    parked,
    settled,
    wait_args,
    wait_until,
)
from tests.goal_runner.test_goal_wake_runner import waiting_on_conditions

pytestmark = pytest.mark.integration  # the harness module carries the shared marks
pytest_plugins = ("tests.capability_broker.test_broker_ledger",)


async def set_status(h, effect: UUID, status: str) -> None:
    await h.w.owner.execute(
        "UPDATE switchboard.effects SET status = $2, updated_at = now() WHERE id = $1",
        effect,
        status,
    )


async def test_the_sweep_does_not_feed_conditions_while_the_feed_is_dark(broker_db_url):
    async with harness(broker_db_url) as h:
        goal, lead, effect = await parked(h)
        assert await wait_until(h, goal, lead.token, wait_args(decision(effect))) == {
            "status": "waiting"
        }
        await set_status(h, effect, "approved")

        await h.runner.supervise_once()

        assert await h.goal_status(goal) == "waiting"
        assert (await envelope(h, goal))["state"] == "open"
        assert len(h.host.launches) == 1


async def test_an_owner_approval_fed_to_the_wait_wakes_one_successor(broker_db_url):
    async with harness(broker_db_url) as h:
        goal, lead, effect = await parked(h)
        assert await wait_until(h, goal, lead.token, wait_args(decision(effect))) == {
            "status": "waiting"
        }
        await set_status(h, effect, "approved")

        await h.runner.feed_conditions()

        assert await h.goal_status(goal) == "admitted"
        env = await envelope(h, goal)
        assert (env["state"], env["consumed_wakes"]) == ("fired", 1)
        # Feeding again is the same event: no second fire, no second wake.
        await h.runner.feed_conditions()
        assert (
            await h.w.owner.fetchval(
                "SELECT count(*) FROM switchboard.wake_intents WHERE goal_id = $1", goal
            )
            == 1
        )
        await h.runner.supervise_once()
        woken = await h.launched(2)
        (item,) = (await h.call(goal, woken.token, "goal__inbox", {}))["replies"]
        assert item["kind"] == "condition_fire"


async def test_a_domain_decline_is_synced_then_fed(broker_db_url):
    async with harness(broker_db_url) as h:
        goal, lead, effect = await parked(h)
        predicate = settled(effect, "cancelled")
        assert await wait_until(h, goal, lead.token, wait_args(predicate)) == {"status": "waiting"}
        # The owner declines in the domain; only the sync moves the goal's effect.
        await h.w.domain.execute(
            "UPDATE pending_actions SET status = 'rejected' WHERE id = $1", effect
        )

        await h.runner.feed_conditions()

        assert (await h.w.effect(effect))["status"] == "rejected"
        assert await h.goal_status(goal) == "admitted"


async def test_a_wait_whose_events_do_not_hold_stays_waiting(broker_db_url):
    async with harness(broker_db_url) as h:
        goal, lead, effect = await parked(h)
        assert await wait_until(h, goal, lead.token, wait_args(decision(effect))) == {
            "status": "waiting"
        }
        await set_status(h, effect, "expired")

        await h.runner.feed_conditions()

        assert await h.goal_status(goal) == "waiting"
        assert (await envelope(h, goal))["state"] == "open"


async def owner_edits(h, goal: UUID, effect: UUID) -> None:
    """The owner approves the parked notify with a different message (D13 edit)."""
    row = await h.w.effect(effect)
    edited = {"entity_id": str(uuid4()), "message": "Owner's edit", "channel": "telegram"}
    await h.w.broker.execute_approved(goal, effect, row["approval_ref"], edited)
    assert (await h.w.effect(effect))["status"] == "rejected"


async def test_an_owner_edit_fires_neither_an_approved_nor_a_rejected_wait(broker_db_url):
    async with harness(broker_db_url) as h:
        goal, lead, effect = await parked(h)
        either = {"tag": "any", "children": [decision(effect), decision(effect, "rejected")]}
        assert await wait_until(h, goal, lead.token, wait_args(either)) == {"status": "waiting"}

        await owner_edits(h, goal, effect)
        await h.runner.feed_conditions()

        assert await h.goal_status(goal) == "waiting"
        assert (await envelope(h, goal))["state"] == "open"


async def test_an_owner_edit_still_settles_the_stored_call(broker_db_url):
    async with harness(broker_db_url) as h:
        goal, lead, effect = await parked(h)
        predicate = settled(effect, "cancelled")
        assert await wait_until(h, goal, lead.token, wait_args(predicate)) == {"status": "waiting"}

        await owner_edits(h, goal, effect)
        await h.runner.feed_conditions()

        assert await h.goal_status(goal) == "admitted"


async def test_one_goals_feed_fault_does_not_stop_the_others(broker_db_url):
    async with harness(broker_db_url) as h:
        goals = []
        for launch in (1, 2):
            goal, lead, effect = await parked(h, launch)
            assert await wait_until(h, goal, lead.token, wait_args(decision(effect))) == {
                "status": "waiting"
            }
            await set_status(h, effect, "approved")
            goals.append(goal)
        real = ConditionEvaluator.evaluate
        calls = []

        async def first_fails(self, instance_id, events, *, now):
            calls.append(instance_id)
            if len(calls) == 1:
                raise RuntimeError("boom")
            return await real(self, instance_id, events, now=now)

        with patch.object(ConditionEvaluator, "evaluate", first_fails):
            await h.runner.feed_conditions()

        assert len(calls) == 2
        assert sorted([await h.goal_status(g) for g in goals]) == ["admitted", "waiting"]


async def test_a_generation_naming_another_goals_instance_is_not_fed(broker_db_url):
    async with harness(broker_db_url) as h:
        other, other_lead, other_effect = await parked(h)
        assert await wait_until(h, other, other_lead.token, wait_args(decision(other_effect))) == {
            "status": "waiting"
        }
        (theirs,) = (await envelope(h, other))["condition_instance_ids"]
        # This goal's generation names the other goal's instance id: not its to feed.
        goal = await h.goal(_grant(SEARCH))
        lead = await h.lead(goal, 2)
        await waiting_on_conditions(h, goal, lead, instance=theirs)
        fed = []
        real = ConditionEvaluator.evaluate

        async def spy(self, instance_id, events, *, now):
            fed.append(instance_id)
            return await real(self, instance_id, events, now=now)

        with patch.object(ConditionEvaluator, "evaluate", spy):
            await h.runner.feed_conditions()

        assert fed == [theirs]  # once, for the goal that owns it
        assert await h.goal_status(goal) == "waiting"
