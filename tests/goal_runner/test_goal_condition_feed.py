"""The goal-event feed wakes a goal waiting on its own conditions (bu-dhloi1.305 PR2).

``GoalRunner.feed_conditions`` syncs the domain's decisions onto the goal's parked effects, feeds
the goal's own decision and settlement events to its condition instance, and admits the fire as
the wait's one wake. It is dark: the sweep does not call it until feed-triggered wakes are
narrowed (PR5), so these tests call it directly.
"""

from __future__ import annotations

from uuid import UUID

import pytest

from tests.goal_runner.test_goal_runner import harness
from tests.goal_runner.test_goal_wait_until import (
    decision,
    envelope,
    parked,
    settled,
    wait_args,
    wait_until,
)

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
