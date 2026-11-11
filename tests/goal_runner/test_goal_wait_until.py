"""``goal.wait_until`` on the goal's own leaves: the lead ends and a condition wakes the goal.

RFC 0038 S2.2 / S4 (bu-dhloi1.305 PR1). A lead waits on a closed predicate over its own parked
approvals and effects; no approval is needed. The ledger derives the grant and the registration
snapshot server-side, opens a ``conditions`` envelope and registers its condition instance in the
lead's transaction, so a predicate that already holds fires once, at once, and is admitted as one
successor. Anything outside the goal's own ledger is refused before anything is recorded.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from butlers.capability_broker.contracts import Reason
from butlers.conditions import ConditionEvaluator, ConditionEvent
from butlers.conditions.sql import PostgresConditionStore, intent_from_row
from butlers.core.goal_admission import decode_grants, encode_grants, intersect
from tests.capability_broker.test_broker_ledger import NOTIFY, SEND, TO_OWNER
from tests.goal_runner.test_goal_runner import SEARCH, _grant, harness, refused

pytestmark = pytest.mark.integration  # the harness module carries the shared marks
pytest_plugins = ("tests.capability_broker.test_broker_ledger",)


def decision(effect: UUID, outcome: str = "approved") -> dict:
    return {"tag": "decision", "subject_id": str(effect), "outcome": outcome}


def settled(effect: UUID, *statuses: str) -> dict:
    return {
        "tag": "effect_settled",
        "effect_id": str(effect),
        "status_set": list(statuses or ("succeeded",)),
    }


def soon(minutes: int = 30) -> str:
    return (datetime.now(UTC) + timedelta(minutes=minutes)).isoformat()


def wait_args(predicate, deadline: str | None = None, max_wakes=1) -> dict:
    return {"predicate": predicate, "deadline": deadline or soon(), "max_wakes": max_wakes}


async def parked(h, launch: int = 1):
    """A running goal whose lead (the harness's ``launch``-th) parked one effect for approval."""
    goal = await h.goal(_grant(NOTIFY), _grant(SEARCH))
    lead = await h.lead(goal, launch)
    args = {"entity_id": str(uuid4()), "message": "Running late", "channel": "telegram"}
    effect = await h.w.broker.call(goal, lead.token, NOTIFY.identity, args)
    assert effect.status == "parked"
    return goal, lead, effect.effect_id


async def envelope(h, goal: UUID):
    return await h.w.owner.fetchrow(
        "SELECT e.*, e.grant_snapshot::text AS snapshot, e.predicate::text AS body, "
        "g.condition_instance_ids FROM switchboard.wait_envelopes e "
        "JOIN switchboard.wait_generations g ON (g.goal_id, g.envelope_id) = (e.goal_id, e.id) "
        "WHERE e.goal_id = $1",
        goal,
    )


async def nothing_recorded(h, goal: UUID, lead) -> None:
    assert await h.goal_status(goal) == "running"
    assert (await h.row(lead.invocation_id))["status"] == "running"
    assert await envelope(h, goal) is None
    assert not await h.w.owner.fetchval(
        "SELECT count(*) FROM public.condition_instances WHERE goal_id = $1", str(goal)
    )


async def test_a_predicate_that_already_holds_wakes_one_successor_at_once(broker_db_url):
    async with harness(broker_db_url) as h:
        goal, lead, effect = await parked(h)
        await h.w.owner.execute(
            "UPDATE switchboard.effects SET status = 'approved' WHERE id = $1", effect
        )

        result = await h.call(goal, lead.token, "goal__wait_until", wait_args(decision(effect)))

        assert result == {"status": "woken"}
        assert (await h.row(lead.invocation_id))["status"] == "returned"
        assert await h.goal_status(goal) == "admitted"
        env = await envelope(h, goal)
        assert (env["kind"], env["state"], env["consumed_wakes"]) == ("conditions", "fired", 1)
        (instance,) = env["condition_instance_ids"]
        fire = await h.w.owner.fetchrow(
            "SELECT fire_key, instance_id, generation, event_id, reason, fired_at, "
            "snapshot_generation, cursors::text AS cursors, claimed_by, label_version, "
            "label_instr, label_data, label_sens FROM public.condition_fire_intents "
            "WHERE instance_id = $1",
            instance,
        )
        assert fire["reason"] == "fired:registration"
        # Admitting the committed fire again changes nothing: one wake, one successor.
        replay = await h.runner.admit_wake(intent_from_row(fire))
        assert replay.replayed and replay.state == "admitted"
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


async def test_a_predicate_not_yet_true_waits_until_its_event_fires(broker_db_url):
    async with harness(broker_db_url) as h:
        goal, lead, effect = await parked(h)
        predicate = {"tag": "any", "children": [decision(effect), decision(effect, "rejected")]}

        result = await h.call(goal, lead.token, "goal__wait_until", wait_args(predicate))

        assert result == {"status": "waiting"}
        assert await h.goal_status(goal) == "waiting"
        assert (await h.row(lead.invocation_id))["status"] == "returned"
        env = await envelope(h, goal)
        assert (env["kind"], env["state"], env["max_wakes"]) == ("conditions", "open", 1)
        assert json.loads(env["body"]) == predicate
        assert env["source_set"] == ["goal.decision"]
        (instance,) = env["condition_instance_ids"]
        assert (
            await h.w.owner.fetchval(
                "SELECT state FROM public.condition_instances WHERE id = $1", instance
            )
            == "open"
        )

        now = datetime.now(UTC)
        event = ConditionEvent(
            id=str(uuid4()),
            source="goal.decision",
            position=1,
            at=now,
            tag="decision",
            data={"subject_id": str(effect), "outcome": "rejected"},
        )
        evaluator = ConditionEvaluator(PostgresConditionStore(h.w.broker.pool))
        fired = await evaluator.evaluate(instance, [event], now=now)
        assert fired.intent is not None
        admitted = await h.runner.admit_wake(fired.intent)
        assert admitted.state == "admitted"
        await h.runner.supervise_once()
        await h.launched(2)


async def test_the_envelope_is_bound_by_the_waiting_lead_and_the_goal(broker_db_url):
    async with harness(broker_db_url) as h:
        goal, lead, effect = await parked(h)
        # A later deadline than the goal's budget is held to the budget.
        args = wait_args(decision(effect), deadline=soon(60 * 24 * 10), max_wakes=6)

        assert await h.call(goal, lead.token, "goal__wait_until", args) == {"status": "waiting"}

        env = await envelope(h, goal)
        budget = await h.budget(goal)
        assert env["deadline"] == budget["deadline"] and env["max_wakes"] == 6
        row = await h.row(lead.invocation_id)
        ceiling = await h.w.owner.fetchval(
            "SELECT ceiling::text FROM switchboard.goals WHERE id = $1", goal
        )
        lead_grant = row["grant_snapshot"]
        held = decode_grants(json.loads(lead_grant) if isinstance(lead_grant, str) else lead_grant)
        expected = encode_grants(intersect(held, decode_grants(json.loads(ceiling))))
        assert json.loads(env["snapshot"]) == expected


async def test_a_leaf_outside_the_goals_own_ledger_is_refused_as_resource(broker_db_url):
    async with harness(broker_db_url) as h:
        goal, lead, _ = await parked(h)
        _, _, foreign = await parked(h, launch=2)
        for predicate in (
            decision(uuid4()),  # no such effect
            decision(foreign),  # another goal's approval
            settled(foreign),  # another goal's effect
        ):
            reason = await refused(
                h.call(goal, lead.token, "goal__wait_until", wait_args(predicate))
            )
            assert reason is Reason.RESOURCE, predicate
        await nothing_recorded(h, goal, lead)


async def test_a_decision_on_an_effect_that_needed_no_approval_is_refused(broker_db_url):
    async with harness(broker_db_url) as h:
        goal = await h.goal(_grant(SEND))
        lead = await h.lead(goal)
        sent = await h.w.broker.call(goal, lead.token, SEND.identity, TO_OWNER)
        assert sent.effect_id is not None

        reason = await refused(
            h.call(goal, lead.token, "goal__wait_until", wait_args(decision(sent.effect_id)))
        )

        assert reason is Reason.RESOURCE
        await nothing_recorded(h, goal, lead)


async def test_an_unbounded_or_foreign_shaped_wait_is_refused_as_policy(broker_db_url):
    async with harness(broker_db_url) as h:
        goal, lead, effect = await parked(h)
        naive = (datetime.now() + timedelta(minutes=30)).isoformat()
        cases = [
            wait_args({"tag": "at", "instant": "2026-11-12T09:00:00Z", "zone": "UTC"}),
            wait_args({"tag": "owner_input", "goal_id": str(goal), "binding_ref": str(effect)}),
            wait_args("when the owner approves"),
            wait_args(decision(effect), max_wakes=7),
            wait_args(decision(effect), max_wakes=0),
            wait_args(decision(effect), max_wakes=True),
            wait_args(decision(effect), deadline=soon(60 * 24 * 46)),  # past the 45-day horizon
            wait_args(decision(effect), deadline=soon(-1)),
            wait_args(decision(effect), deadline=naive),
            wait_args(decision(effect), deadline="tomorrow"),
            {"predicate": decision(effect), "max_wakes": 1},
        ]
        for args in cases:
            reason = await refused(h.call(goal, lead.token, "goal__wait_until", args))
            assert reason is Reason.POLICY, args
        await nothing_recorded(h, goal, lead)


async def test_stop_while_waiting_cancels_the_wait_and_its_fire(broker_db_url):
    async with harness(broker_db_url) as h:
        goal, lead, effect = await parked(h)
        assert await h.call(goal, lead.token, "goal__wait_until", wait_args(decision(effect))) == {
            "status": "waiting"
        }
        (instance,) = (await envelope(h, goal))["condition_instance_ids"]

        await h.runner.stop(goal)

        assert await h.goal_status(goal) == "stopped"
        assert (await envelope(h, goal))["state"] == "cancelled"
        now = datetime.now(UTC)
        event = ConditionEvent(
            id=str(uuid4()),
            source="goal.decision",
            position=1,
            at=now,
            tag="decision",
            data={"subject_id": str(effect), "outcome": "approved"},
        )
        evaluator = ConditionEvaluator(PostgresConditionStore(h.w.broker.pool))
        fired = await evaluator.evaluate(instance, [event], now=now)
        # The instance may still fire; admission records it refused and launches nothing.
        assert fired.intent is not None
        result = await h.runner.admit_wake(fired.intent)
        assert (result.state, result.reason) == ("not_admitted", "stopped")
        await h.runner.supervise_once()
        assert len(h.host.launches) == 1
