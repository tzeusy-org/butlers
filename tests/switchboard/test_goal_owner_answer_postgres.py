"""The owner's reply wakes a goal waiting for it (RFC 0038 S2.1, S2.2; bu-dhloi1.7 PR C).

A waiting goal joins a reply only by the exact reply binding to the prompt its owner-answer wait
names. That reply is the wait's one wake: the ``reply_joined`` decision, the ``owner_reply`` inbox
row, the envelope's ``open -> fired``, the ``wake`` epoch and the pending wake intent commit in the
admission transaction. Every other reply to a waiting goal stays new owner intent.
"""

import asyncio
from dataclasses import replace
from uuid import uuid4

import pytest

from butlers.core.goal_admission import Evidence, SourceRef
from butlers.core.goal_waiting import WaitLedger
from tests.switchboard.goal_ledger_fixtures import admit, effect

pytest_plugins = ("tests.switchboard.goal_ledger_fixtures",)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

OWNER = Evidence("message", "owner", owner=True)


async def waiting_for_answer(owner, runtime, *, kind="owner_answer", wait="1 hour"):
    """A goal that delivered a prompt to the owner and waits on its answer."""
    goal = await admit(runtime, OWNER)
    assert goal.goal_id is not None
    entity = uuid4()
    expiry = await owner.fetchval("SELECT now() + interval '1 hour'")
    _, prompt = await effect(
        owner,
        goal.goal_id,
        delivery="prompt-1",
        entity=entity,
        channel="telegram",
        accepts=True,
        expiry=expiry,
    )
    async with owner.acquire() as conn, conn.transaction():
        waits = WaitLedger(conn)
        if kind == "owner_answer":
            until = await conn.fetchval(f"SELECT now() + interval '{wait}'")
            opened = await waits.open_owner_answer(goal.goal_id, 0, prompt, until)
        else:
            opened = await waits.open_settle(goal.goal_id, 0)
        assert opened is not None
        await conn.execute(
            "UPDATE switchboard.goals SET status = 'waiting' WHERE id = $1", goal.goal_id
        )
    reply = Evidence(
        "message",
        "owner",
        owner=True,
        reply_delivery="prompt-1",
        owner_entity=entity,
        owner_channel="telegram",
    )
    return goal.goal_id, prompt, reply


async def test_the_exact_reply_wakes_the_goal_through_one_successor_epoch(ledger):
    owner, runtime = ledger
    goal_id, prompt, reply = await waiting_for_answer(owner, runtime)
    source, attempt = SourceRef("message", "reply-1"), uuid4()

    joined = await admit(runtime, reply, source, attempt)

    assert (joined.result, joined.reason, joined.goal_id) == ("joined", "reply_joined", goal_id)
    assert (
        await owner.fetchval("SELECT status FROM switchboard.goals WHERE id = $1", goal_id)
        == "admitted"
    )
    envelope = await owner.fetchrow(
        "SELECT kind, state, consumed_wakes FROM switchboard.wait_envelopes WHERE goal_id = $1",
        goal_id,
    )
    assert (envelope["kind"], envelope["state"], envelope["consumed_wakes"]) == (
        "owner_answer",
        "fired",
        1,
    )
    inbox = await owner.fetchrow("SELECT * FROM switchboard.goal_inbox WHERE goal_id = $1", goal_id)
    assert (inbox["input_kind"], inbox["reply_effect_id"]) == ("owner_reply", prompt)
    wake = await owner.fetchrow(
        "SELECT * FROM switchboard.wake_intents WHERE goal_id = $1", goal_id
    )
    assert (wake["state"], wake["epoch"]) == ("pending", 1)
    epoch = await owner.fetchrow(
        "SELECT reason, state, input_refs FROM switchboard.goal_epochs "
        "WHERE goal_id = $1 AND epoch = 1",
        goal_id,
    )
    assert (epoch["reason"], epoch["state"]) == ("wake", "pending")
    assert f"inbox:{inbox['id']}" in str(epoch["input_refs"])
    # A replay of the same delivery returns the stored decision and wakes nothing more.
    again = await admit(runtime, reply, source, attempt)
    assert again == joined
    assert await owner.fetchval("SELECT count(*) FROM switchboard.wake_intents") == 1
    assert await owner.fetchval("SELECT count(*) FROM switchboard.goal_inbox") == 1


async def test_a_reply_to_another_prompt_does_not_wake_the_goal(ledger):
    owner, runtime = ledger
    goal_id, _, reply = await waiting_for_answer(owner, runtime)
    expiry = await owner.fetchval("SELECT now() + interval '1 hour'")
    await effect(
        owner,
        goal_id,
        delivery="prompt-2",
        entity=reply.owner_entity,
        channel="telegram",
        accepts=True,
        expiry=expiry,
    )

    other = await admit(runtime, replace(reply, reply_delivery="prompt-2"))

    assert other.result == "admitted" and other.goal_id != goal_id
    assert (
        await owner.fetchval("SELECT status FROM switchboard.goals WHERE id = $1", goal_id)
        == "waiting"
    )
    assert await owner.fetchval("SELECT count(*) FROM switchboard.wake_intents") == 0


async def test_a_reply_to_a_goal_waiting_on_something_else_is_new_intent(ledger):
    owner, runtime = ledger
    goal_id, _, reply = await waiting_for_answer(owner, runtime, kind="settle")

    other = await admit(runtime, reply)

    assert other.result == "admitted" and other.goal_id != goal_id
    assert await owner.fetchval("SELECT count(*) FROM switchboard.wake_intents") == 0


async def test_a_reply_after_the_wait_expired_is_new_intent(ledger):
    owner, runtime = ledger
    goal_id, _, reply = await waiting_for_answer(owner, runtime, wait="1 second")
    await asyncio.sleep(1.2)  # the prompt is still live but the wait's envelope ran out

    other = await admit(runtime, reply)

    assert other.result == "admitted" and other.goal_id != goal_id
    assert await owner.fetchval("SELECT count(*) FROM switchboard.wake_intents") == 0


async def test_a_reply_after_stop_wakes_nothing(ledger):
    owner, runtime = ledger
    goal_id, _, reply = await waiting_for_answer(owner, runtime)
    await owner.execute("UPDATE switchboard.goals SET status = 'stopped' WHERE id = $1", goal_id)

    other = await admit(runtime, reply)

    assert other.result == "admitted" and other.goal_id != goal_id
    assert await owner.fetchval("SELECT count(*) FROM switchboard.wake_intents") == 0
