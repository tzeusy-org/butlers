"""The owner's reply to a prompt is an ``owner_input`` leaf a goal can wait on (bu-dhloi1.305 PR2b).

RFC 0038 S2.2 / S4. A lead may wait on the owner's input to one of its live prompts. Admission
joins the exact reply to that prompt as an unread inbox row and leaves the goal waiting; the
goal-event feed (dark, called directly here) turns the row into the leaf's event and admits the
fire as the wait's wake. A reply already joined and unread when the lead waits fires at once; a
reply the lead already read is not new input; a reply to any other prompt is new owner intent.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from butlers.capability_broker.contracts import Reason
from butlers.core.goal_admission import Evidence, SourceRef
from butlers.core.reply_scope import scoped_reply_ref
from tests.capability_broker.test_broker_ledger import NOTIFY, OWNER, SEND, TO_OWNER
from tests.goal_runner.test_goal_runner import _grant, harness, refused
from tests.goal_runner.test_goal_wait_until import envelope, wait_args, wait_until
from tests.goal_runner.test_goal_wake_runner import PROMPT_SCOPE, owner_prompt
from tests.switchboard.goal_admission_fixtures import limits
from tests.switchboard.goal_ledger_fixtures import admit, configure, pools

pytestmark = pytest.mark.integration  # the harness module carries the shared marks
pytest_plugins = ("tests.capability_broker.test_broker_ledger",)


def owner_input(goal: UUID, prompt: UUID) -> dict:
    return {"tag": "owner_input", "goal_id": str(goal), "binding_ref": str(prompt)}


async def reply(broker_db_url, locator: str = "100:77"):
    """The owner answers the Telegram message at ``locator``; admission decides where it goes."""
    evidence = Evidence(
        "message",
        "owner",
        owner=True,
        reply_delivery=scoped_reply_ref(
            **PROMPT_SCOPE,
            recipient_entity=OWNER,
            recipient_channel="telegram_bot",
            locator=locator,
        ),
        owner_entity=OWNER,
        owner_channel="telegram_bot",
    )
    async with pools(broker_db_url) as (owner, runtime):
        await configure(owner, limits())
        return await admit(runtime, evidence, SourceRef("message", f"tg-{uuid4()}"), uuid4())


async def prompted(h):
    goal = await h.goal(_grant(SEND), _grant(NOTIFY))
    lead = await h.lead(goal)
    return goal, lead, await owner_prompt(h, goal, lead)


async def wake_count(h, goal: UUID) -> int:
    return await h.w.owner.fetchval(
        "SELECT count(*) FROM switchboard.wake_intents WHERE goal_id = $1", goal
    )


async def test_the_reply_joins_the_waiting_goal_and_the_feed_wakes_it(broker_db_url):
    async with harness(broker_db_url) as h:
        goal, lead, prompt = await prompted(h)
        args = wait_args(owner_input(goal, prompt))
        assert await wait_until(h, goal, lead.token, args) == {"status": "waiting"}
        assert (await envelope(h, goal))["source_set"] == ["goal.inbox"]

        joined = await reply(broker_db_url)

        assert (joined.result, joined.reason, joined.goal_id) == ("joined", "reply_joined", goal)
        # Joined, not yet a wake: the reply waits unread and the goal keeps waiting.
        assert await h.goal_status(goal) == "waiting"
        assert await wake_count(h, goal) == 0
        inbox = await h.w.owner.fetchrow(
            "SELECT input_kind, reply_effect_id, consumed_at FROM switchboard.goal_inbox "
            "WHERE goal_id = $1",
            goal,
        )
        assert (inbox["input_kind"], inbox["reply_effect_id"]) == ("owner_reply", prompt)
        assert inbox["consumed_at"] is None

        await h.runner.feed_conditions()

        assert await h.goal_status(goal) == "admitted"
        assert (await envelope(h, goal))["state"] == "fired"
        await h.runner.feed_conditions()  # the same row is the same event: no second wake
        assert await wake_count(h, goal) == 1
        await h.runner.supervise_once()
        woken = await h.launched(2)
        replies = (await h.call(goal, woken.token, "goal__inbox", {}))["replies"]
        assert sorted(item["kind"] for item in replies) == ["condition_fire", "owner_reply"]


async def test_an_unread_reply_when_the_lead_waits_wakes_the_goal_at_once(broker_db_url):
    async with harness(broker_db_url) as h:
        goal, lead, prompt = await prompted(h)
        joined = await reply(broker_db_url)  # the goal is running: the reply joins it
        assert (joined.reason, joined.goal_id) == ("reply_joined", goal)

        result = await wait_until(h, goal, lead.token, wait_args(owner_input(goal, prompt)))

        assert result == {"status": "woken"}
        assert await h.goal_status(goal) == "admitted"
        assert await wake_count(h, goal) == 1


async def test_a_reply_the_lead_already_read_is_not_new_input(broker_db_url):
    async with harness(broker_db_url) as h:
        goal, lead, prompt = await prompted(h)
        await reply(broker_db_url)
        (item,) = (await h.call(goal, lead.token, "goal__inbox", {}))["replies"]
        assert item["kind"] == "owner_reply"

        result = await wait_until(h, goal, lead.token, wait_args(owner_input(goal, prompt)))
        await h.runner.feed_conditions()

        assert result == {"status": "waiting"}
        assert await h.goal_status(goal) == "waiting"
        assert await wake_count(h, goal) == 0


async def test_a_reply_to_another_prompt_stays_new_intent(broker_db_url):
    async with harness(broker_db_url) as h:
        goal, lead, prompt = await prompted(h)
        await owner_prompt(h, goal, lead, locator="100:78", message="Or the later train?")
        assert await wait_until(h, goal, lead.token, wait_args(owner_input(goal, prompt))) == {
            "status": "waiting"
        }

        other = await reply(broker_db_url, locator="100:78")

        assert other.result == "admitted" and other.goal_id != goal
        await h.runner.feed_conditions()
        assert await h.goal_status(goal) == "waiting"
        assert not await h.w.owner.fetchval(
            "SELECT count(*) FROM switchboard.goal_inbox WHERE goal_id = $1", goal
        )


async def test_input_on_anything_but_this_goals_live_prompt_is_refused(broker_db_url):
    async with harness(broker_db_url) as h:
        goal, lead, prompt = await prompted(h)
        plain = await h.w.broker.call(goal, lead.token, SEND.identity, TO_OWNER)
        for predicate in (
            owner_input(goal, uuid4()),  # no such effect
            owner_input(goal, plain.effect_id),  # an effect that asked nothing
            owner_input(uuid4(), prompt),  # this prompt, named under another goal
        ):
            reason = await refused(wait_until(h, goal, lead.token, wait_args(predicate)))
            assert reason is Reason.RESOURCE, predicate
        assert await h.goal_status(goal) == "running"
        assert await envelope(h, goal) is None


async def released(h, goal: UUID) -> list[tuple[UUID, str]]:
    rows = await h.w.owner.fetch(
        "SELECT inbox_id, reason FROM switchboard.goal_inbox_releases WHERE goal_id = $1", goal
    )
    return [(row["inbox_id"], row["reason"]) for row in rows]


async def unread_reply(h, goal: UUID) -> UUID:
    return await h.w.owner.fetchval(
        "SELECT id FROM switchboard.goal_inbox WHERE goal_id = $1 AND consumed_at IS NULL", goal
    )


async def test_a_goal_ending_at_its_deadline_releases_the_unread_reply(broker_db_url):
    async with harness(broker_db_url) as h:
        goal, lead, prompt = await prompted(h)
        deadline = (datetime.now(UTC) + timedelta(seconds=2)).isoformat()
        args = wait_args(owner_input(goal, prompt), deadline=deadline)
        assert await wait_until(h, goal, lead.token, args) == {"status": "waiting"}
        joined = await reply(broker_db_url)  # joined, but the dark feed never wakes the goal
        assert joined.reason == "reply_joined"
        inbox = await unread_reply(h, goal)
        await asyncio.sleep(2.2)

        await h.runner.supervise_once()

        assert await h.goal_status(goal) == "failed"
        # Not swallowed: the reply is still there, unread, and recorded as released.
        assert await unread_reply(h, goal) == inbox
        assert await released(h, goal) == [(inbox, "deadline")]
        async with h.runner._txn() as ledger:
            assert await ledger.released_replies(goal) == 1


async def test_stop_releases_the_unread_reply(broker_db_url):
    async with harness(broker_db_url) as h:
        goal, lead, prompt = await prompted(h)
        assert await wait_until(h, goal, lead.token, wait_args(owner_input(goal, prompt))) == {
            "status": "waiting"
        }
        await reply(broker_db_url)
        inbox = await unread_reply(h, goal)

        await h.runner.stop(goal)

        assert await released(h, goal) == [(inbox, "stopped")]


async def test_a_read_reply_is_not_released(broker_db_url):
    async with harness(broker_db_url) as h:
        goal, lead, _ = await prompted(h)
        await reply(broker_db_url)
        assert len((await h.call(goal, lead.token, "goal__inbox", {}))["replies"]) == 1

        await h.runner.stop(goal)

        assert await released(h, goal) == []
        async with h.runner._txn() as ledger:
            assert await ledger.released_replies(goal) == 0
