"""The runner launches a wake: one claimed intent, one successor lead at the minted epoch.

RFC 0038 S2.2 (bu-dhloi1.7 PR B2). A fired wait is admitted into a wake intent; the runner claims
that same intent, opens the lead at its epoch and marks it launched in the same transaction. A
launch that opens nothing, a lapsed claim and a crash between fire and admission all retry the
same intent; Stop cancels it; and a goal waiting for its wake is never ended as leaderless.
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest

from butlers.capability_broker.contracts import Reason
from butlers.capability_broker.domains import DeliveryReport, DomainReceipt
from butlers.conditions import FireIntent
from butlers.core.goal_admission import Evidence, Grant, SourceRef, digest, encode_grants
from butlers.core.goal_labels import Label
from butlers.core.goal_waiting import EnvelopeSpec, WaitLedger
from butlers.core.goal_wake import WakeAdmission
from butlers.core.reply_scope import scoped_reply_ref
from butlers.goal_runner.contracts import GoalEnded
from butlers.goal_runner.ledger import RunnerLedger
from tests.capability_broker.test_broker_ledger import NOTIFY, OWNER, SEND, TO_OWNER, _joined_reply
from tests.goal_runner.test_goal_runner import SEARCH, _grant, harness, refused
from tests.switchboard.goal_admission_fixtures import limits
from tests.switchboard.goal_ledger_fixtures import admit, configure, pools

pytestmark = pytest.mark.integration  # the harness module carries the shared marks
pytest_plugins = ("tests.capability_broker.test_broker_ledger",)

DIGEST = hashlib.sha256(b"x").hexdigest()


def fire_for(instance: str = "c0") -> FireIntent:
    return FireIntent(
        fire_key=uuid4(),
        instance_id=instance,
        generation=1,
        event_id=uuid4(),
        reason="fired:event",
        fired_at=datetime(2026, 11, 9, 12, tzinfo=UTC),
        snapshot_generation=1,
        cursors={"goal.inbox": 4},
        label=Label.owner(),
    )


async def waiting_on_conditions(
    h,
    goal: UUID,
    lead,
    *,
    lifetime=timedelta(hours=1),
    grants: tuple[Grant, ...] = (),
    instance: str = "c0",
) -> UUID:
    """The lead's session ended and the goal waits on one conditions envelope (``instance``).

    ``grants`` is the envelope's grant snapshot: the most its wake lead may hold.
    """
    owner_label = Label.owner()
    snapshot = encode_grants(grants)
    async with h.w.owner.acquire() as conn, conn.transaction():
        waits = WaitLedger(conn)
        version = await waits.append_plan(goal, 0, [], [], [])
        assert version is not None
        envelope = await waits.open_envelope(
            EnvelopeSpec(
                kind="conditions",
                goal_id=goal,
                original_epoch=0,
                plan_version=version,
                plan_digest=await conn.fetchval(
                    "SELECT digest FROM switchboard.goal_plans WHERE goal_id = $1", goal
                ),
                predicate={"tag": "owner_input"},
                predicate_digest=DIGEST,
                source_set=["goal.inbox"],
                permitted_sources=["goal.inbox"],
                grant_snapshot=snapshot,
                grant_digest=digest(snapshot),
                label=owner_label,
                deadline=datetime.now(UTC) + lifetime,
                max_wakes=2,
                condition_instance_ids=[instance],
            )
        )
        assert envelope is not None
        await conn.execute(
            "UPDATE switchboard.invocations SET status = 'returned', end_reason = 'completed', "
            "ended_at = now(), token_hash = NULL WHERE id = $1",
            lead.invocation_id,
        )
        await conn.execute("UPDATE switchboard.goals SET status = 'waiting' WHERE id = $1", goal)
    h.host.end(lead.invocation_id)
    return envelope


async def wake_row(h, goal: UUID):
    return await h.w.owner.fetchrow(
        "SELECT * FROM switchboard.wake_intents WHERE goal_id = $1", goal
    )


async def test_a_fire_wakes_the_goal_through_one_successor_lead_at_the_minted_epoch(broker_db_url):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(SEARCH))
        lead = await h.lead(goal)
        await waiting_on_conditions(h, goal, lead)
        fire = fire_for()

        admitted = await h.runner.admit_wake(fire)
        assert (admitted.state, admitted.epoch) == ("admitted", 1)
        assert await h.goal_status(goal) == "admitted"
        await h.runner.supervise_once()
        woken = await h.launched(2)

        row = await h.row(woken.invocation_id)
        assert (row["depth"], row["lead_epoch"], row["status"]) == (0, 1, "running")
        wake = await wake_row(h, goal)
        assert (wake["state"], wake["epoch"], wake["attempts"]) == ("launched", 1, 1)
        assert await h.goal_status(goal) == "running"
        epoch = await h.w.owner.fetchval(
            "SELECT state FROM switchboard.goal_epochs WHERE goal_id = $1 AND epoch = 1", goal
        )
        assert epoch in ("launching", "running")
        # The lead reads what woke it: a condition fire, with no reply effect to invent.
        (item,) = (await h.call(goal, woken.token, "goal__inbox", {}))["replies"]
        assert item["kind"] == "condition_fire" and item["reply_effect_id"] is None
        assert UUID(item["event_id"]) == fire.event_id
        consumed = await h.w.owner.fetchrow(
            "SELECT consumed_epoch FROM switchboard.goal_inbox WHERE goal_id = $1", goal
        )
        assert consumed["consumed_epoch"] == 1
        # A further sweep neither claims nor launches the launched intent again.
        await h.runner.supervise_once()
        assert len(h.host.launches) == 2


async def test_a_launch_that_opens_nothing_is_retried_on_the_same_intent(broker_db_url):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(SEARCH))
        lead = await h.lead(goal)
        await waiting_on_conditions(h, goal, lead)
        await h.runner.admit_wake(fire_for())
        original = RunnerLedger.open_lead
        calls = []

        async def refuse_once(self, *args, **kwargs):
            calls.append(kwargs["wake"].intent_id)
            if len(calls) == 1:
                raise GoalEnded("lead_active")
            return await original(self, *args, **kwargs)

        with patch.object(RunnerLedger, "open_lead", refuse_once):
            await h.runner.supervise_once()
            await h.runner.drain()
            assert (await wake_row(h, goal))["state"] == "failed"
            assert len(h.host.launches) == 1
            await h.runner.supervise_once()
            await h.launched(2)

        wake = await wake_row(h, goal)
        assert (wake["state"], wake["attempts"]) == ("launched", 2)
        assert len(set(calls)) == 1 and len(calls) == 2
        assert await h.w.owner.fetchval("SELECT count(*) FROM switchboard.wake_intents") == 1
        assert (
            await h.w.owner.fetchval("SELECT consumed_wakes FROM switchboard.wait_envelopes") == 1
        )


async def test_a_lapsed_claim_is_taken_again_and_never_launches_twice(broker_db_url):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(SEARCH))
        lead = await h.lead(goal)
        await waiting_on_conditions(h, goal, lead)
        await h.runner.admit_wake(fire_for())
        wake = await wake_row(h, goal)
        # A runner claimed the intent and died before opening the lead.
        async with h.runner._txn() as ledger:
            assert await ledger.claim_wake(goal, wake["id"], "dead-runner", 120) is not None
        async with h.runner._txn() as ledger:
            assert await ledger.claim_wake(goal, wake["id"], "other", 120) is None
        await h.w.owner.execute(
            "UPDATE switchboard.wake_intents SET claim_expires_at = now() - interval '1 second' "
            "WHERE id = $1",
            wake["id"],
        )

        await h.runner.supervise_once()
        await h.launched(2)
        await h.runner.supervise_once()

        after = await wake_row(h, goal)
        assert (after["state"], after["attempts"], after["claimed_by"]) == ("launched", 2, None)
        assert len(h.host.launches) == 2


async def test_stop_cancels_a_wake_that_has_not_launched(broker_db_url):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(SEARCH))
        lead = await h.lead(goal)
        await waiting_on_conditions(h, goal, lead)
        await h.runner.admit_wake(fire_for())

        await h.runner.stop(goal)
        await h.runner.supervise_once()

        wake = await wake_row(h, goal)
        assert (wake["state"], wake["cancelled_reason"]) == ("cancelled", "stopped")
        assert await h.goal_status(goal) == "stopped" and len(h.host.launches) == 1


async def test_a_goal_waiting_for_its_wake_is_not_ended_as_leaderless(broker_db_url):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(SEARCH))
        lead = await h.lead(goal)
        await waiting_on_conditions(h, goal, lead)
        await h.w.owner.execute(
            "UPDATE switchboard.invocations SET ended_at = now() - interval '1 hour' WHERE id = $1",
            lead.invocation_id,
        )
        await h.runner.admit_wake(fire_for())

        async with h.runner._txn() as ledger:
            assert await ledger.leaderless(0) == []
        # Without the pending intent the same goal would be resumed (and here, failed).
        await h.w.owner.execute(
            "UPDATE switchboard.wake_intents SET state = 'cancelled', cancelled_reason = 'stopped'"
        )
        async with h.runner._txn() as ledger:
            assert [item.goal_id for item in await ledger.leaderless(0)] == [goal]


async def test_the_sweep_admits_a_fire_the_evaluator_committed_and_nobody_admitted(broker_db_url):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(SEARCH))
        lead = await h.lead(goal)
        await waiting_on_conditions(h, goal, lead)
        fire = fire_for()

        async def missed(self, grace, limit=50):
            return [fire]

        with patch.object(WakeAdmission, "unadmitted", missed):
            await h.runner.supervise_once()
        await h.launched(2)

        wake = await wake_row(h, goal)
        assert (wake["fire_key"], wake["state"]) == (fire.fire_key, "launched")


async def test_a_poisoned_fire_does_not_stop_the_other_stages_of_the_sweep(broker_db_url):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(SEARCH))
        old = await h.lead(goal)
        await h.w.owner.execute(
            "UPDATE switchboard.invocations SET lease_expires_at = now() - interval '1 second' "
            "WHERE id = $1",
            old.invocation_id,
        )

        async def poisoned(self, grace, limit=50):
            return [fire_for("nobody")]

        async def raises(self, fire):
            raise RuntimeError("poisoned fire")

        with (
            patch.object(WakeAdmission, "unadmitted", poisoned),
            patch.object(WakeAdmission, "admit", raises),
        ):
            await h.runner.supervise_once()
        # The lapsed lease was still handled in the same sweep: a successor lead launched.
        await h.launched(2)
        assert await h.goal_status(goal) == "running"


async def test_a_fire_that_keeps_failing_is_closed_with_an_admit_error_receipt(broker_db_url):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(SEARCH))
        lead = await h.lead(goal)
        await waiting_on_conditions(h, goal, lead)
        fire = fire_for()

        async def missed(self, grace, limit=50):
            row = await self.connection.fetchval(
                "SELECT count(*) FROM switchboard.wake_intents WHERE fire_key = $1", fire.fire_key
            )
            return [] if row else [fire]

        async def raises(self, fire):
            raise RuntimeError("poisoned fire")

        with patch.object(WakeAdmission, "unadmitted", missed):
            with patch.object(WakeAdmission, "admit", raises):
                for _ in range(3):
                    await h.runner.supervise_once()
            assert await h.goal_status(goal) == "waiting"
            wake = await wake_row(h, goal)
            assert (wake["state"], wake["not_admitted_reason"]) == ("not_admitted", "admit_error")
            # Nothing is retried once the receipt exists, and the goal stays waiting.
            await h.runner.supervise_once()
        assert await h.goal_status(goal) == "waiting" and len(h.host.launches) == 1


async def test_a_lapsed_claim_past_the_envelope_deadline_is_cancelled(broker_db_url):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(SEARCH))
        lead = await h.lead(goal)
        await waiting_on_conditions(h, goal, lead, lifetime=timedelta(seconds=2))
        await h.runner.admit_wake(fire_for())
        wake = await wake_row(h, goal)
        async with h.runner._txn() as ledger:
            assert await ledger.claim_wake(goal, wake["id"], "dead-runner", 120) is not None
        await h.w.owner.execute(
            "UPDATE switchboard.wake_intents SET claim_expires_at = now() - interval '1 second'"
        )
        await asyncio.sleep(2.2)

        async with h.runner._txn() as ledger:
            assert await ledger.lapse_wakes() == 1

        after = await wake_row(h, goal)
        assert (after["state"], after["cancelled_reason"]) == ("cancelled", "expired")


async def test_a_wait_that_expires_without_firing_ends_the_goal(broker_db_url):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(SEARCH))
        lead = await h.lead(goal)
        await waiting_on_conditions(h, goal, lead, lifetime=timedelta(seconds=1))
        await h.runner.supervise_once()
        assert await h.goal_status(goal) == "waiting"  # not yet expired
        await asyncio.sleep(1.2)

        await h.runner.supervise_once()

        assert await h.goal_status(goal) == "failed"
        state = await h.w.owner.fetchval(
            "SELECT state FROM switchboard.wait_envelopes WHERE goal_id = $1", goal
        )
        assert state == "expired"
        assert len(h.host.launches) == 1


PROMPT_SCOPE = dict(
    transport="telegram", endpoint="telegram:bot:@butler", conversation="telegram:100"
)


async def owner_prompt(h, goal, lead, locator="100:77", message="Shall I book it?"):
    """A real owner prompt through the broker: notify with a window, Messenger's report back.

    The broker records one effect per goal, capability and arguments, so a second prompt on the
    same goal needs a different ``message``.
    """
    h.w.forwarder.receipt = DomainReceipt(
        "committed",
        "receipt-prompt",
        {"sent": True},
        report=DeliveryReport(
            **PROMPT_SCOPE,
            locator=locator,
            prompt_expires_at=datetime.now(UTC) + timedelta(minutes=30),
        ),
    )
    args = {
        "entity_id": str(OWNER),
        "message": message,
        "channel": "telegram",
        "awaits_reply_seconds": 1800,
    }
    sent = await h.w.broker.call(goal, lead.token, NOTIFY.identity, args)
    assert sent.status == "committed"
    return sent.effect_id


async def test_waiting_on_the_owner_ends_the_lead_and_binds_the_wait_to_the_prompt(broker_db_url):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(SEND), _grant(NOTIFY))
        lead = await h.lead(goal)
        prompt = await owner_prompt(h, goal, lead)

        result = await h.call(goal, lead.token, "goal__wait_owner", {"effect_id": str(prompt)})

        assert result == {"status": "waiting"}
        assert await h.goal_status(goal) == "waiting"
        assert (await h.row(lead.invocation_id))["status"] == "returned"
        env = await h.w.owner.fetchrow(
            "SELECT kind, state, max_wakes, predicate FROM switchboard.wait_envelopes "
            "WHERE goal_id = $1",
            goal,
        )
        assert (env["kind"], env["state"], env["max_wakes"]) == ("owner_answer", "open", 1)
        assert str(prompt) in str(env["predicate"])
        # No message but the exact reply wakes it: the sweep launches nothing.
        await h.runner.supervise_once()
        assert len(h.host.launches) == 1


async def test_waiting_on_the_owner_needs_a_live_prompt_to_the_owner(broker_db_url):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(SEND), _grant(SEARCH))
        lead = await h.lead(goal)
        plain = await h.w.broker.call(goal, lead.token, SEND.identity, TO_OWNER)  # no prompt
        for effect in (plain.effect_id, uuid4()):
            reason = await refused(
                h.call(goal, lead.token, "goal__wait_owner", {"effect_id": str(effect)})
            )
            assert reason is Reason.RESOURCE
        assert await h.goal_status(goal) == "running"


async def test_a_telegram_reply_to_a_real_prompt_wakes_the_goal_without_a_direct_insert(
    broker_db_url,
):
    """notify(awaits_reply_seconds) -> goal__wait_owner -> the owner's reply, joined by admission.

    Nothing is inserted by hand: the prompt's reply ref is the broker's, derived from the
    Messenger report and the owner recipient the broker recorded; the reply's ref is derived by
    the ingress side from the inbound message's own scope; admission matches the two.
    """
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(SEND), _grant(NOTIFY))
        lead = await h.lead(goal)
        prompt = await owner_prompt(h, goal, lead, locator="100:77")
        assert await h.call(goal, lead.token, "goal__wait_owner", {"effect_id": str(prompt)}) == {
            "status": "waiting"
        }

        inbound = scoped_reply_ref(
            **PROMPT_SCOPE,
            recipient_entity=OWNER,
            recipient_channel="telegram_bot",
            locator="100:77",
        )
        reply = Evidence(
            "message",
            "owner",
            owner=True,
            reply_delivery=inbound,
            owner_entity=OWNER,
            owner_channel="telegram_bot",
        )
        async with pools(broker_db_url) as (owner, runtime):
            await configure(owner, limits())
            wrong = Evidence(
                "message",
                "owner",
                owner=True,
                reply_delivery=scoped_reply_ref(
                    **PROMPT_SCOPE,
                    recipient_entity=OWNER,
                    recipient_channel="telegram_bot",
                    locator="100:78",  # a reply to some other message
                ),
                owner_entity=OWNER,
                owner_channel="telegram_bot",
            )
            elsewhere = await admit(runtime, wrong, SourceRef("message", "tg-other"), uuid4())
            assert elsewhere.reason != "reply_joined" and elsewhere.goal_id != goal

            joined = await admit(runtime, reply, SourceRef("message", "tg-reply-1"), uuid4())

        assert (joined.result, joined.reason, joined.goal_id) == ("joined", "reply_joined", goal)
        await h.runner.supervise_once()
        successor = await h.launched(2)
        (item,) = (await h.call(goal, successor.token, "goal__inbox", {}))["replies"]
        assert item["kind"] == "owner_reply" and UUID(item["reply_effect_id"]) == prompt


async def test_the_joined_reply_launches_a_successor_that_reads_it(broker_db_url):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(SEND), _grant(NOTIFY))
        lead = await h.lead(goal)
        prompt = await owner_prompt(h, goal, lead)
        await h.call(goal, lead.token, "goal__wait_owner", {"effect_id": str(prompt)})
        # Admission's reply join: decision + owner_reply inbox row, then the wait's wake.
        inbox_id = await _joined_reply(h.w, goal, prompt)
        decision = await h.w.owner.fetchval(
            "SELECT decision_id FROM switchboard.goal_inbox WHERE id = $1", inbox_id
        )
        async with h.runner._txn() as ledger:
            waits = WakeAdmission(ledger.connection)
            envelope = await waits.answer_envelope(goal, prompt)
            assert envelope is not None
            woken = await waits.admit_answer(envelope, decision, inbox_id)
        assert (woken.state, woken.epoch) == ("admitted", 1)

        await h.runner.supervise_once()
        successor = await h.launched(2)

        assert (await h.row(successor.invocation_id))["lead_epoch"] == 1
        (item,) = (await h.call(goal, successor.token, "goal__inbox", {}))["replies"]
        assert item["kind"] == "owner_reply" and UUID(item["reply_effect_id"]) == prompt
