"""Owner failure cause line and pending-ask identity over real migrated PostgreSQL."""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest

from butlers.core import owner_delivery as od
from butlers.core.goal_admission import Evidence, Grant, Obligation, SourceRef
from butlers.goal_runner.owner_failure import CAUSE_LINES, OwnerFailureReporter
from tests.switchboard.goal_ledger_fixtures import (  # noqa: F401
    admit,
    effect,
    ledger,
    ledger_url,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


class Sender:
    def __init__(self, outcome="sent", quiet=False) -> None:
        self.outcome = outcome
        self.quiet = quiet
        self.lines: list[tuple[str, str]] = []

    async def suppressed(self) -> bool:
        return self.quiet

    async def send(self, text, *, dedup_key):
        self.lines.append((text, dedup_key))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


@pytest.fixture
async def world(ledger):  # noqa: F811
    owner, runtime = ledger
    await owner.execute("DELETE FROM public.entities WHERE 'owner' = ANY(roles)")
    owner_id = await owner.fetchval(
        "INSERT INTO public.entities (canonical_name, roles) VALUES ('owner-failure-test', "
        "ARRAY['owner']) RETURNING id"
    )
    await owner.execute("UPDATE public.owner_delivery_rollout SET enabled = true")
    yield owner, runtime, owner_id
    await owner.execute("UPDATE public.owner_delivery_rollout SET enabled = false")
    await owner.execute("DELETE FROM public.entities WHERE id = $1", owner_id)


async def owner_goal(runtime, owner, status="failed"):
    goal = await admit(runtime, Evidence("message", str(uuid4()), owner=True))
    await owner.execute(
        "UPDATE switchboard.goals SET status = $2 WHERE id = $1", goal.goal_id, status
    )
    return goal.goal_id


def reporter(runtime, sender):
    return OwnerFailureReporter(runtime, sender)


async def asks(owner):
    return await owner.fetch("SELECT * FROM switchboard.pending_asks ORDER BY created_at")


async def test_a_failed_owner_goal_gets_one_ask_and_one_line_however_often_it_is_swept(world):
    owner, runtime, _ = world
    goal_id = await owner_goal(runtime, owner)
    sender = Sender()
    assert await reporter(runtime, sender).sweep() == 1
    assert await reporter(runtime, sender).sweep() == 0
    rows = await asks(owner)
    assert len(rows) == 1 and rows[0]["goal_id"] == goal_id
    assert (rows[0]["cause"], rows[0]["state"]) == ("failed", "open")
    assert rows[0]["source_key"] == await owner.fetchval(
        "SELECT source_key FROM switchboard.goals WHERE id = $1", goal_id
    )
    assert sender.lines == [(CAUSE_LINES["failed"], f"pending_ask:{rows[0]['id']}")]
    subject = await owner.fetchrow(
        "SELECT kind, source_ref FROM public.owner_delivery_subjects WHERE id = $1",
        rows[0]["subject_id"],
    )
    assert (subject["kind"], subject["source_ref"]) == ("pending_ask", str(rows[0]["id"]))


async def test_budget_exhaustion_and_a_capability_refusal_name_their_own_cause(world):
    owner, runtime, _ = world
    await owner_goal(runtime, owner, "budget_exhausted")
    blocked = await owner_goal(runtime, owner)
    invocation, _ = await effect(owner, blocked, status="refused")
    await owner.execute(
        "INSERT INTO switchboard.broker_refusals (goal_id, invocation_id, capability, reason) "
        "VALUES ($1, $2, 'messenger__send', 'capability_unavailable')",
        blocked,
        invocation,
    )
    sender = Sender()
    assert await reporter(runtime, sender).sweep() == 2
    assert {row["cause"] for row in await asks(owner)} == {
        "budget_exhausted",
        "capability_unavailable",
    }
    assert {text for text, _ in sender.lines} == {
        CAUSE_LINES["budget_exhausted"],
        CAUSE_LINES["capability_unavailable"],
    }


async def test_an_owner_delivery_in_the_goals_own_lineage_means_the_owner_was_told(world):
    owner, runtime, owner_id = world
    direct = await owner_goal(runtime, owner)
    await effect(owner, direct, entity=owner_id, channel="telegram_bot", status="committed")
    assert await reporter(runtime, Sender()).sweep() == 0
    assert await asks(owner) == []


async def test_a_delivery_by_a_goal_it_caused_counts_as_telling_the_owner(world):
    owner, runtime, owner_id = world
    parent = await owner_goal(runtime, owner)
    invocation, caused = await effect(owner, parent, status="committed")
    obligation = Obligation(
        "health", "created-v1", (Grant("health.read"),), creator_invocation=invocation
    )
    facts = Evidence(
        "occurrence",
        "schedule",
        obligation=obligation,
        caused_by_goal=parent,
        caused_by_effect=caused,
    )
    child = await admit(runtime, facts, SourceRef("occurrence", "schedule-id", "tick-1"))
    await effect(owner, child.goal_id, entity=owner_id, channel="telegram_bot", status="committed")
    assert await reporter(runtime, Sender()).sweep() == 0
    assert await asks(owner) == []


@pytest.mark.parametrize("status", ["parked", "requested", "failed", "refused"])
async def test_an_owner_effect_that_cannot_have_reached_the_owner_does_not_count(world, status):
    owner, runtime, owner_id = world
    goal_id = await owner_goal(runtime, owner)
    await effect(owner, goal_id, entity=owner_id, channel="telegram_bot", status=status)
    assert await reporter(runtime, Sender()).sweep() == 1


@pytest.mark.parametrize("status", ["unknown", "dispatched"])
async def test_an_owner_effect_that_may_have_reached_the_owner_counts(world, status):
    owner, runtime, owner_id = world
    goal_id = await owner_goal(runtime, owner)
    await effect(owner, goal_id, entity=owner_id, channel="telegram_bot", status=status)
    assert await reporter(runtime, Sender()).sweep() == 0


async def test_an_unrelated_goals_owner_delivery_does_not_silence_this_failure(world):
    owner, runtime, owner_id = world
    failed = await owner_goal(runtime, owner)
    elsewhere = await owner_goal(runtime, owner, "completed")
    await effect(owner, elsewhere, entity=owner_id, channel="telegram_bot", status="committed")
    sender = Sender()
    assert await reporter(runtime, sender).sweep() == 1
    assert [row["goal_id"] for row in await asks(owner)] == [failed]


async def test_only_terminal_owner_message_goals_are_reported(world):
    owner, runtime, _ = world
    await owner_goal(runtime, owner, "completed")
    await owner_goal(runtime, owner, "stopped")
    await owner_goal(runtime, owner, "running")
    third_party = await admit(runtime, Evidence("message", str(uuid4()), owner=False))
    await owner.execute(
        "UPDATE switchboard.goals SET status = 'failed' WHERE id = $1", third_party.goal_id
    )
    assert await reporter(runtime, Sender()).sweep() == 0
    assert await asks(owner) == []


async def test_a_dark_rollout_or_a_missing_owner_writes_and_sends_nothing(world):
    owner, runtime, owner_id = world
    await owner_goal(runtime, owner)
    await owner.execute("UPDATE public.owner_delivery_rollout SET enabled = false")
    sender = Sender()
    assert await reporter(runtime, sender).sweep() == 0
    await owner.execute("UPDATE public.owner_delivery_rollout SET enabled = true")
    await owner.execute(
        "UPDATE public.entities SET roles = ARRAY[]::text[] WHERE id = $1", owner_id
    )
    assert await reporter(runtime, sender).sweep() == 0
    assert await asks(owner) == [] and sender.lines == []


async def test_concurrent_sweeps_make_one_ask_and_send_one_line(world):
    owner, runtime, _ = world
    await owner_goal(runtime, owner)
    sender = Sender()
    await asyncio.gather(*(reporter(runtime, sender).sweep() for _ in range(4)))
    assert len(await asks(owner)) == 1 and len(sender.lines) == 1


async def test_a_proven_non_send_is_presented_again_and_sent_exactly_once(world):
    owner, runtime, _ = world
    await owner_goal(runtime, owner)
    refusing = Sender("failed")
    assert await reporter(runtime, refusing).sweep() == 0
    retry = Sender()
    assert await reporter(runtime, retry).sweep() == 1
    again = Sender()
    assert await reporter(runtime, again).sweep() == 0 and again.lines == []
    assert len(refusing.lines) == 1 and len(retry.lines) == 1
    states = await owner.fetch(
        "SELECT state FROM public.owner_delivery_presentations "
        "WHERE subject_id IN (SELECT subject_id FROM switchboard.pending_asks)"
    )
    assert [row["state"] for row in states] == ["sent"]


async def test_an_ambiguous_send_is_never_sent_again(world):
    owner, runtime, _ = world
    await owner_goal(runtime, owner)
    assert await reporter(runtime, Sender("unknown")).sweep() == 0
    again = Sender()
    assert await reporter(runtime, again).sweep() == 0 and again.lines == []
    states = await owner.fetch(
        "SELECT state FROM public.owner_delivery_presentations "
        "WHERE subject_id IN (SELECT subject_id FROM switchboard.pending_asks)"
    )
    assert [row["state"] for row in states] == ["unknown"]


async def test_an_awaits_reply_prompt_is_a_question_not_a_failure_report(world):
    owner, runtime, owner_id = world
    goal_id = await owner_goal(runtime, owner)
    await effect(
        owner,
        goal_id,
        entity=owner_id,
        channel="telegram_bot",
        status="committed",
        delivery="chat:1",
        accepts=True,
        expiry=datetime.now(UTC) + timedelta(hours=1),
    )
    assert await reporter(runtime, Sender()).sweep() == 1
    assert [row["goal_id"] for row in await asks(owner)] == [goal_id]


async def test_one_asks_failure_does_not_stop_the_others(world):
    owner, runtime, _ = world
    first = await owner_goal(runtime, owner)
    second = await owner_goal(runtime, owner)
    real = od.present
    calls = []

    async def flaky(conn, subject_id, **kwargs):
        calls.append(subject_id)
        if len(calls) == 1:
            raise RuntimeError("one ask breaks")
        return await real(conn, subject_id, **kwargs)

    sender = Sender()
    with patch.object(od, "present", flaky):
        assert await reporter(runtime, sender).sweep() == 1
    assert len(await asks(owner)) == 2 and len(sender.lines) == 1
    assert {first, second} == {row["goal_id"] for row in await asks(owner)}


async def test_a_sender_that_raises_leaves_an_unknown_presentation_not_a_retry(world):
    owner, runtime, _ = world
    await owner_goal(runtime, owner)
    assert await reporter(runtime, Sender(RuntimeError("boom"))).sweep() == 0
    again = Sender()
    assert await reporter(runtime, again).sweep() == 0 and again.lines == []
    states = await owner.fetch(
        "SELECT state FROM public.owner_delivery_presentations "
        "WHERE subject_id IN (SELECT subject_id FROM switchboard.pending_asks)"
    )
    assert [row["state"] for row in states] == ["unknown"]


async def test_a_suppressed_owner_is_asked_again_on_the_next_sweep(world):
    owner, runtime, _ = world
    await owner_goal(runtime, owner)
    quiet = Sender(quiet=True)
    assert await reporter(runtime, quiet).sweep() == 0 and quiet.lines == []
    assert len(await asks(owner)) == 1
    awake = Sender()
    assert await reporter(runtime, awake).sweep() == 1


async def test_the_cause_line_is_built_from_closed_words_only(world):
    owner, runtime, _ = world
    await owner_goal(runtime, owner)
    sender = Sender()
    await reporter(runtime, sender).sweep()
    (text, key) = sender.lines[0]
    assert text in CAUSE_LINES.values()
    assert "od1:" not in text and "http" not in text
    assert key.startswith("pending_ask:")


async def test_every_closed_cause_has_exactly_one_line():
    assert set(CAUSE_LINES) == {
        "failed",
        "budget_exhausted",
        "capability_unavailable",
        "admission_refused",
    }
    assert len(set(CAUSE_LINES.values())) == len(CAUSE_LINES)
