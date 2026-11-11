"""The owner is told, content-blind, how many replies a goal's end released unread (sw_071)."""

from uuid import uuid4

import pytest

from butlers.core.goal_admission import Evidence, SourceRef
from butlers.goal_runner.owner_failure import CAUSE_LINES, unread_sentence
from tests.switchboard.goal_ledger_fixtures import admit, effect, ledger, ledger_url  # noqa: F401
from tests.switchboard.test_owner_failure_postgres import (  # noqa: F401
    Sender,
    asks,
    reporter,
    world,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def goal_with_replies(owner, runtime, replies=1, origin_owner=True):
    """A running goal with ``replies`` joined, unread owner replies."""
    goal = await admit(runtime, Evidence("message", str(uuid4()), owner=origin_owner))
    entity = uuid4()
    for index in range(replies):
        delivery = f"prompt-{uuid4()}"
        expiry = await owner.fetchval("SELECT now() + interval '1 hour'")
        await effect(
            owner,
            goal.goal_id,
            delivery=delivery,
            entity=entity,
            channel="telegram",
            accepts=True,
            expiry=expiry,
        )
        reply = Evidence(
            "message",
            "owner",
            owner=True,
            reply_delivery=delivery,
            owner_entity=entity,
            owner_channel="telegram",
        )
        joined = await admit(runtime, reply, SourceRef("message", f"r-{uuid4()}"), uuid4())
        assert joined.reason == "reply_joined", index
    return goal.goal_id


async def end(owner, goal_id, status):
    await owner.execute("UPDATE switchboard.goals SET status = $2 WHERE id = $1", goal_id, status)


@pytest.mark.parametrize("status", ["completed", "partial"])
async def test_a_completed_or_partial_goal_with_unread_replies_gets_its_own_ask(world, status):  # noqa: F811
    owner, runtime, _ = world
    goal_id = await goal_with_replies(owner, runtime, replies=2)
    await end(owner, goal_id, status)
    sender = Sender()
    assert await reporter(runtime, sender).sweep() == 1
    (row,) = await asks(owner)
    assert (row["cause"], row["goal_id"]) == ("replies_unread", goal_id)
    assert sender.lines == [(unread_sentence(2), f"pending_ask:{row['id']}")]
    assert "2 replies" in sender.lines[0][0]
    # Told once.
    again = Sender()
    assert await reporter(runtime, again).sweep() == 0 and again.lines == []


@pytest.mark.parametrize("deferred", [False, True])
async def test_a_failed_goal_gets_one_ask_with_the_count_in_either_order(world, deferred):  # noqa: F811
    from butlers.goal_runner.owner_failure import OwnerFailureReporter

    owner, runtime, _ = world
    first = await admit(runtime, Evidence("message", str(uuid4()), owner=True))
    await end(owner, first.goal_id, "failed")
    goal_id = await goal_with_replies(owner, runtime, replies=1)
    await end(owner, goal_id, "failed")
    sender = Sender()
    # With batch=1 the second failure's ask is recorded one sweep late (deferred); the unread
    # pass never takes a failed goal, so it still ends with exactly one ask, with the suffix.
    sweeper = OwnerFailureReporter(runtime, sender, batch=1 if deferred else 10)
    for _ in range(3):
        await sweeper.sweep()
    mine = [r for r in await asks(owner) if r["goal_id"] == goal_id]
    assert [r["cause"] for r in mine] == ["failed"]
    assert all(r["cause"] != "replies_unread" for r in await asks(owner))
    texts = [text for text, key in sender.lines if key == f"pending_ask:{mine[0]['id']}"]
    assert texts == [f"{CAUSE_LINES['failed']} {unread_sentence(1)}"]
    assert "1 reply" in texts[0]


async def test_a_goal_with_no_released_reply_is_unchanged(world):  # noqa: F811
    owner, runtime, _ = world
    goal = await admit(runtime, Evidence("message", str(uuid4()), owner=True))
    await end(owner, goal.goal_id, "completed")
    failed = await admit(runtime, Evidence("message", str(uuid4()), owner=True))
    await end(owner, failed.goal_id, "failed")
    sender = Sender()
    await reporter(runtime, sender).sweep()
    assert [r["cause"] for r in await asks(owner)] == ["failed"]
    assert sender.lines[0][0] == CAUSE_LINES["failed"]


async def test_a_stopped_goal_needs_no_notice(world):  # noqa: F811
    owner, runtime, _ = world
    goal_id = await goal_with_replies(owner, runtime)
    await end(owner, goal_id, "stopped")
    sender = Sender()
    assert await reporter(runtime, sender).sweep() == 0
    assert await asks(owner) == [] and sender.lines == []


async def test_a_dark_rollout_writes_and_sends_nothing(world):  # noqa: F811
    owner, runtime, _ = world
    goal_id = await goal_with_replies(owner, runtime)
    await end(owner, goal_id, "completed")
    await owner.execute("UPDATE public.owner_delivery_rollout SET enabled = false")
    sender = Sender()
    assert await reporter(runtime, sender).sweep() == 0
    assert await asks(owner) == [] and sender.lines == []


async def test_an_obligation_goal_that_released_an_owner_reply_also_tells_the_owner(world):  # noqa: F811
    owner, runtime, _ = world
    goal_id = await goal_with_replies(owner, runtime, origin_owner=True)
    await owner.execute("ALTER TABLE switchboard.goals DISABLE TRIGGER USER")
    try:
        await owner.execute(
            "UPDATE switchboard.goals SET origin='third_party', status='completed' WHERE id=$1",
            goal_id,
        )
    finally:
        await owner.execute("ALTER TABLE switchboard.goals ENABLE TRIGGER USER")
    # Releases are written by the status trigger, which was off: write the record by hand.
    await owner.execute(
        "INSERT INTO switchboard.goal_inbox_releases (inbox_id, goal_id, reason) "
        "SELECT id, goal_id, 'completed' FROM switchboard.goal_inbox WHERE goal_id = $1",
        goal_id,
    )
    sender = Sender()
    assert await reporter(runtime, sender).sweep() == 1
    assert [r["cause"] for r in await asks(owner)] == ["replies_unread"]
