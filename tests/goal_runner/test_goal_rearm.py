"""A woken lead waits again on the same conditions: rearm opens the next generation (bu-dhloi1.305).

RFC 0038 S2.2. ``goal.wait_again`` ends the woken lead and re-arms the goal's fired ``conditions``
envelope on its unchanged bounds (predicate, deadline, cumulative ``max_wakes``, grant snapshot).
The next generation registers a fresh condition instance against a snapshot of the goal's
own ledger with no replay floor, so only a new fact can wake it: an old decision, settlement or
reply fed again changes no leaf. Rearm is refused while an owner reply sits unread (the lead
reads what woke it first), while the snapshot alone already holds the predicate, once the
wake count is spent, and before anything has fired. The feed is dark and called directly here.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from butlers.capability_broker.contracts import Reason
from butlers.conditions.sql import intent_from_row
from tests.capability_broker.test_broker_ledger import NOTIFY
from tests.goal_runner.test_goal_condition_feed import set_status
from tests.goal_runner.test_goal_owner_input_wait import owner_input, prompted, reply, wake_count
from tests.goal_runner.test_goal_runner import harness, refused
from tests.goal_runner.test_goal_wait_until import decision, wait_args, wait_until

pytestmark = pytest.mark.integration  # the harness module carries the shared marks
pytest_plugins = ("tests.capability_broker.test_broker_ledger",)


async def wait_again(h, goal: UUID, token: str) -> str:
    """The runner's ``goal.wait_again`` path; the tool is dark to leads (below)."""
    return await h.runner.wait_again(goal, token)


async def live_envelope(h, goal: UUID):
    return await h.w.owner.fetchrow(
        "SELECT id, state, generation, consumed_wakes, max_wakes FROM switchboard.wait_envelopes "
        "WHERE goal_id = $1 AND kind = 'conditions'",
        goal,
    )


async def generation_instance(h, goal: UUID, generation: int) -> str:
    return await h.w.owner.fetchval(
        "SELECT condition_instance_ids[1] FROM switchboard.wait_generations "
        "WHERE goal_id = $1 AND generation = $2",
        goal,
        generation,
    )


async def woken_by_reply(h, broker_db_url, *, max_wakes: int = 2):
    """A goal woken once by the owner's reply: its successor lead running, the envelope fired."""
    goal, lead, prompt = await prompted(h)
    args = wait_args(owner_input(goal, prompt), max_wakes=max_wakes)
    assert await wait_until(h, goal, lead.token, args) == {"status": "waiting"}
    await reply(broker_db_url)
    await h.runner.feed_conditions()
    assert await h.goal_status(goal) == "admitted"
    await h.runner.supervise_once()
    return goal, prompt, await h.launched(2)


async def read_inbox(h, goal: UUID, lead) -> list[str]:
    replies = (await h.call(goal, lead.token, "goal__inbox", {}))["replies"]
    return sorted(item["kind"] for item in replies)


async def parked_call(h, goal: UUID, lead) -> UUID:
    args = {"entity_id": str(uuid4()), "message": "Running late", "channel": "telegram"}
    effect = await h.w.broker.call(goal, lead.token, NOTIFY.identity, args)
    assert effect.status == "parked"
    return effect.effect_id


async def test_a_woken_lead_waits_again_and_only_a_new_reply_wakes_it(broker_db_url):
    async with harness(broker_db_url) as h:
        goal, _, successor = await woken_by_reply(h, broker_db_url)
        assert await read_inbox(h, goal, successor) == ["condition_fire", "owner_reply"]

        assert await wait_again(h, goal, successor.token) == "waiting"

        assert await h.goal_status(goal) == "waiting"
        assert (await h.row(successor.invocation_id))["status"] == "returned"
        env = await live_envelope(h, goal)
        assert (env["state"], env["generation"], env["consumed_wakes"]) == ("open", 1, 1)
        fresh = await generation_instance(h, goal, 1)
        assert fresh != await generation_instance(h, goal, 0)
        # Nothing new: feeding the goal's current state again wakes nothing.
        await h.runner.feed_conditions()
        assert await h.goal_status(goal) == "waiting"
        assert await wake_count(h, goal) == 1

        await reply(broker_db_url)
        await h.runner.feed_conditions()

        assert await h.goal_status(goal) == "admitted"
        env = await live_envelope(h, goal)
        assert (env["state"], env["generation"], env["consumed_wakes"]) == ("fired", 1, 2)
        assert await wake_count(h, goal) == 2
        await h.runner.supervise_once()
        third = await h.launched(3)
        assert await read_inbox(h, goal, third) == ["condition_fire", "owner_reply"]


async def test_the_wake_count_is_cumulative_and_caps_the_loop(broker_db_url):
    async with harness(broker_db_url) as h:
        goal, _, successor = await woken_by_reply(h, broker_db_url, max_wakes=2)
        await read_inbox(h, goal, successor)
        assert await wait_again(h, goal, successor.token) == "waiting"
        await reply(broker_db_url)
        await h.runner.feed_conditions()
        await h.runner.supervise_once()
        third = await h.launched(3)
        await read_inbox(h, goal, third)

        reason = await refused(wait_again(h, goal, third.token))

        assert reason is Reason.POLICY
        # Refused before anything is recorded: the lead runs on and the count never resets.
        assert await h.goal_status(goal) == "running"
        assert (await h.row(third.invocation_id))["status"] == "running"
        env = await live_envelope(h, goal)
        assert (env["state"], env["generation"], env["consumed_wakes"]) == ("fired", 1, 2)


async def test_an_unread_reply_refuses_the_rearm_until_the_lead_reads_it(broker_db_url):
    async with harness(broker_db_url) as h:
        goal, _, successor = await woken_by_reply(h, broker_db_url)

        # The reply that woke the goal is still unread: rearming would wake on it again.
        reason = await refused(wait_again(h, goal, successor.token))

        assert reason is Reason.POLICY
        assert await h.goal_status(goal) == "running"
        assert (await live_envelope(h, goal))["state"] == "fired"
        assert await read_inbox(h, goal, successor) == ["condition_fire", "owner_reply"]
        assert await wait_again(h, goal, successor.token) == "waiting"
        await h.runner.feed_conditions()
        assert await h.goal_status(goal) == "waiting"
        assert await wake_count(h, goal) == 1


async def test_an_old_decision_contributes_but_never_wakes_the_next_generation(broker_db_url):
    async with harness(broker_db_url) as h:
        goal, lead, prompt = await prompted(h)
        effect = await parked_call(h, goal, lead)
        await set_status(h, effect, "approved")
        predicate = {"tag": "all", "children": [owner_input(goal, prompt), decision(effect)]}
        assert await wait_until(h, goal, lead.token, wait_args(predicate, max_wakes=2)) == {
            "status": "waiting"
        }
        await reply(broker_db_url)
        await h.runner.feed_conditions()
        await h.runner.supervise_once()
        successor = await h.launched(2)
        await read_inbox(h, goal, successor)
        assert await wait_again(h, goal, successor.token) == "waiting"

        # The approval's row changes again (its time moves past any earlier position): the same
        # decision, fed again, is not a new fact and wakes nothing.
        await h.w.owner.execute(
            "UPDATE switchboard.effects SET updated_at = now() + interval '1 minute' WHERE id = $1",
            effect,
        )
        await h.runner.feed_conditions()
        assert await h.goal_status(goal) == "waiting"
        assert await wake_count(h, goal) == 1

        # A new reply completes the predicate with the old approval still contributing.
        await reply(broker_db_url)
        await h.runner.feed_conditions()
        assert await h.goal_status(goal) == "admitted"
        assert await wake_count(h, goal) == 2


async def test_a_predicate_the_old_facts_already_hold_is_refused(broker_db_url):
    async with harness(broker_db_url) as h:
        goal, lead, prompt = await prompted(h)
        effect = await parked_call(h, goal, lead)
        predicate = {"tag": "any", "children": [owner_input(goal, prompt), decision(effect)]}
        assert await wait_until(h, goal, lead.token, wait_args(predicate, max_wakes=2)) == {
            "status": "waiting"
        }
        await set_status(h, effect, "approved")
        await h.runner.feed_conditions()
        await h.runner.supervise_once()
        successor = await h.launched(2)
        await read_inbox(h, goal, successor)

        reason = await refused(wait_again(h, goal, successor.token))

        assert reason is Reason.POLICY
        assert await h.goal_status(goal) == "running"
        env = await live_envelope(h, goal)
        assert (env["state"], env["generation"]) == ("fired", 0)
        assert not await h.w.owner.fetchval(
            "SELECT count(*) FROM switchboard.wait_generations WHERE goal_id = $1 "
            "AND generation = 1",
            goal,
        )


async def test_an_earlier_generations_fire_never_wakes_the_next(broker_db_url):
    async with harness(broker_db_url) as h:
        goal, _, successor = await woken_by_reply(h, broker_db_url)
        await read_inbox(h, goal, successor)
        old = await generation_instance(h, goal, 0)
        assert await wait_again(h, goal, successor.token) == "waiting"
        fire = await h.w.owner.fetchrow(
            "SELECT fire_key, instance_id, generation, event_id, reason, fired_at, "
            "snapshot_generation, cursors::text AS cursors, claimed_by, label_version, "
            "label_instr, label_data, label_sens FROM public.condition_fire_intents "
            "WHERE instance_id = $1",
            old,
        )

        replay = await h.runner.admit_wake(intent_from_row(fire))

        # The settled gen-0 fire replays its stored outcome; the open generation is untouched.
        assert replay.replayed
        assert await h.goal_status(goal) == "waiting"
        assert (await live_envelope(h, goal))["state"] == "open"
        assert await wake_count(h, goal) == 1


async def test_stop_cancels_the_rearmed_generation(broker_db_url):
    async with harness(broker_db_url) as h:
        goal, _, successor = await woken_by_reply(h, broker_db_url)
        await read_inbox(h, goal, successor)
        assert await wait_again(h, goal, successor.token) == "waiting"
        fresh = await generation_instance(h, goal, 1)

        await h.runner.stop(goal)

        assert (await live_envelope(h, goal))["state"] == "cancelled"
        assert (
            await h.w.owner.fetchval(
                "SELECT state FROM public.condition_instances WHERE id = $1", fresh
            )
            == "cancelled"
        )


async def test_nothing_to_rearm_before_a_conditions_wait_fired(broker_db_url):
    async with harness(broker_db_url) as h:
        goal, lead, _ = await prompted(h)

        reason = await refused(wait_again(h, goal, lead.token))

        assert reason is Reason.RESOURCE
        assert await h.goal_status(goal) == "running"


async def test_the_tool_is_dark_to_leads(broker_db_url):
    async with harness(broker_db_url) as h:
        goal, lead, _ = await prompted(h)

        assert "goal__wait_again" not in {tool.name for tool in h.tools.listed(0)}
        reason = await refused(h.call(goal, lead.token, "goal__wait_again", {}))

        assert reason is Reason.CAPABILITY
