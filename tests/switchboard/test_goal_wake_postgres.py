"""Wake admission on a real migrated ledger (RFC 0038 S2.1 / S2.2, bu-dhloi1.7 PR B2).

A condition fire becomes exactly one successor epoch, or a terminal ``not_admitted`` receipt with
its reason. Every case runs the second committed step on the Switchboard runtime role, and the
crash case leaves a fire intent committed with no wake intent for the sweeper to complete.
"""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import asyncpg
import pytest

from butlers.conditions import ConditionEvaluator, FireIntent
from butlers.conditions.sql import PostgresConditionStore
from butlers.core.goal_labels import Label
from butlers.core.goal_wake import WakeAdmission
from tests.conditions.test_conditions import DECISION, decision_ev, register
from tests.conditions.test_conditions import NOW as CONDITION_NOW
from tests.switchboard.goal_ledger_fixtures import admit
from tests.switchboard.test_goal_waiting_postgres import (
    decision,
    envelope,
    goal_with_lead,
    plan,
)

pytest_plugins = ("tests.switchboard.goal_ledger_fixtures",)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

LABEL = Label(frozenset({"owner"}), frozenset({"owner"}), frozenset())


def fire_for(instance="c0", generation=1, **extra) -> FireIntent:
    return FireIntent(
        fire_key=extra.get("fire_key", uuid4()),
        instance_id=instance,
        generation=generation,
        event_id=extra.get("event_id", uuid4()),
        reason="fired:event",
        fired_at=datetime(2026, 11, 9, 12, tzinfo=UTC),
        snapshot_generation=1,
        cursors={"goal.inbox": 4},
        label=LABEL,
    )


async def waiting_goal(owner, runtime, **envelope_args):
    goal_id, lead = await goal_with_lead(owner, runtime)
    await plan(owner, goal_id)
    env = await envelope(owner, goal_id, **envelope_args)
    await owner.execute("UPDATE switchboard.goals SET status = 'waiting' WHERE id = $1", goal_id)
    return goal_id, lead, env


async def admit_fire(runtime, fire):
    async with runtime.acquire() as conn, conn.transaction():
        return await WakeAdmission(conn).admit(fire)


async def counts(owner, goal_id):
    return {
        "wakes": await owner.fetchval(
            "SELECT count(*) FROM switchboard.wake_intents WHERE goal_id = $1", goal_id
        ),
        "inbox": await owner.fetchval(
            "SELECT count(*) FROM switchboard.goal_inbox WHERE goal_id = $1", goal_id
        ),
        "epochs": await owner.fetchval(
            "SELECT count(*) FROM switchboard.goal_epochs WHERE goal_id = $1", goal_id
        ),
        "decisions": await owner.fetchval(
            "SELECT count(*) FROM switchboard.admission_decisions WHERE goal_id = $1 "
            "AND reason = 'condition_joined'",
            goal_id,
        ),
    }


async def test_a_fire_admits_one_successor_and_a_replay_changes_nothing(ledger):
    owner, runtime = ledger
    goal_id, lead, env = await waiting_goal(owner, runtime)
    fire = fire_for()

    result = await admit_fire(runtime, fire)

    assert (result.state, result.reason, result.goal_id) == ("admitted", None, goal_id)
    assert result.epoch == 1
    goal = await owner.fetchrow("SELECT status FROM switchboard.goals WHERE id = $1", goal_id)
    assert goal["status"] == "admitted"
    envelope_row = await owner.fetchrow(
        "SELECT * FROM switchboard.wait_envelopes WHERE id = $1", env
    )
    assert (envelope_row["state"], envelope_row["consumed_wakes"]) == ("fired", 1)
    wake = await owner.fetchrow(
        "SELECT * FROM switchboard.wake_intents WHERE goal_id = $1", goal_id
    )
    assert (wake["state"], wake["epoch"], wake["fire_key"]) == ("pending", 1, fire.fire_key)
    epoch = await owner.fetchrow(
        "SELECT * FROM switchboard.goal_epochs WHERE goal_id = $1 AND epoch = 1", goal_id
    )
    assert (epoch["reason"], epoch["state"], epoch["predecessor_invocation_id"]) == (
        "wake",
        "pending",
        lead,
    )
    inbox = await owner.fetchrow("SELECT * FROM switchboard.goal_inbox WHERE goal_id = $1", goal_id)
    assert (inbox["input_kind"], inbox["event_id"], inbox["envelope_id"]) == (
        "condition_fire",
        fire.event_id,
        env,
    )
    assert inbox["consumed_at"] is None and inbox["reply_effect_id"] is None
    decision = await owner.fetchrow(
        "SELECT reason, input_kind, result, occurrence_key FROM switchboard.admission_decisions "
        "WHERE id = $1",
        inbox["decision_id"],
    )
    assert dict(decision) == {
        "reason": "condition_joined",
        "input_kind": "condition_fire",
        "result": "joined",
        "occurrence_key": f"gen:0:event:{fire.event_id}",
    }
    before = await counts(owner, goal_id)

    again = await admit_fire(runtime, fire)

    assert (again.state, again.replayed, again.epoch) == ("admitted", True, 1)
    assert await counts(owner, goal_id) == before


@pytest.mark.parametrize(
    ("setup", "reason"),
    [
        ("stopped", "stopped"),
        ("admitted", "goal_not_waiting"),
        ("cancelled", "envelope_not_open"),
        ("expired", "expired"),
    ],
)
async def test_a_refused_fire_ends_as_a_terminal_receipt_with_its_reason(ledger, setup, reason):
    owner, runtime = ledger
    deadline = "-1 second" if setup == "expired" else "1 hour"
    goal_id, _, env = await waiting_goal(owner, runtime, deadline=deadline)
    if setup in ("stopped", "admitted"):
        await owner.execute(
            "UPDATE switchboard.goals SET status = $2 WHERE id = $1", goal_id, setup
        )
    if setup == "cancelled":
        await owner.execute(
            "UPDATE switchboard.wait_envelopes SET state = 'cancelled' WHERE id = $1", env
        )
    fire = fire_for()

    result = await admit_fire(runtime, fire)

    assert (result.state, result.reason) == ("not_admitted", reason)
    wake = await owner.fetchrow(
        "SELECT * FROM switchboard.wake_intents WHERE goal_id = $1", goal_id
    )
    assert (wake["state"], wake["not_admitted_reason"], wake["epoch"]) == (
        "not_admitted",
        reason,
        None,
    )
    assert wake["fire_key"] == fire.fire_key
    assert (await counts(owner, goal_id)) == {"wakes": 1, "inbox": 0, "epochs": 1, "decisions": 0}
    state = await owner.fetchval("SELECT state FROM switchboard.wait_envelopes WHERE id = $1", env)
    assert state == {"expired": "expired", "cancelled": "cancelled"}.get(setup, "open")
    # The receipt is final: replaying the fire reports it and writes nothing.
    again = await admit_fire(runtime, fire)
    assert (again.state, again.reason, again.replayed) == ("not_admitted", reason, True)


async def test_two_admitters_racing_for_one_fire_mint_one_successor(ledger):
    owner, runtime = ledger
    goal_id, _, _ = await waiting_goal(owner, runtime)
    fire = fire_for()

    results = await asyncio.gather(admit_fire(runtime, fire), admit_fire(runtime, fire))

    assert sorted(r.state for r in results) == ["admitted", "admitted"]
    assert {r.epoch for r in results} == {1}
    counted = await counts(owner, goal_id)
    assert counted["wakes"] == counted["inbox"] == counted["decisions"] == 1
    assert (await owner.fetchval("SELECT consumed_wakes FROM switchboard.wait_envelopes")) == 1


async def test_a_second_fire_of_a_spent_generation_is_refused(ledger):
    owner, runtime = ledger
    goal_id, _, _ = await waiting_goal(owner, runtime)
    first, second = fire_for(), fire_for()

    assert (await admit_fire(runtime, first)).state == "admitted"
    # The goal is admitted again and the envelope fired: nothing else can wake it.
    result = await admit_fire(runtime, second)

    assert (result.state, result.reason) == ("not_admitted", "goal_not_waiting")
    assert (await counts(owner, goal_id))["wakes"] == 2


async def test_a_fire_that_is_not_a_goals_wait_is_not_recorded(ledger):
    owner, runtime = ledger
    await waiting_goal(owner, runtime)

    result = await admit_fire(runtime, fire_for(instance="someone-elses"))

    assert result.state == "unmapped"
    assert await owner.fetchval("SELECT count(*) FROM switchboard.wake_intents") == 0


async def test_a_crash_between_the_fire_commit_and_admission_is_completed_by_the_sweeper(ledger):
    owner, runtime = ledger
    goal_id, _, _ = await waiting_goal(owner, runtime)
    evaluator = ConditionEvaluator(PostgresConditionStore(runtime))
    await register(evaluator, DECISION, instance_id="c0")
    fired = await evaluator.evaluate("c0", [decision_ev()], now=CONDITION_NOW)
    assert fired.intent is not None
    # The evaluator's step committed; the process died before admission wrote anything.
    assert await owner.fetchval("SELECT count(*) FROM switchboard.wake_intents") == 0

    async with runtime.acquire() as conn, conn.transaction():
        pending = await WakeAdmission(conn).unadmitted(0)
    assert [p.fire_key for p in pending] == [fired.intent.fire_key]

    results = [await admit_fire(runtime, p) for p in pending]

    assert [r.state for r in results] == ["admitted"]
    async with runtime.acquire() as conn, conn.transaction():
        assert await WakeAdmission(conn).unadmitted(0) == []
    assert (await counts(owner, goal_id))["wakes"] == 1
    # A sweeper that runs again, or races the original admitter, mints nothing more.
    assert (await admit_fire(runtime, pending[0])).replayed is True
    assert (await counts(owner, goal_id))["wakes"] == 1


async def test_a_refused_fire_leaves_nothing_for_the_sweeper_to_retry(ledger):
    owner, runtime = ledger
    goal_id, _, _ = await waiting_goal(owner, runtime)
    await owner.execute("UPDATE switchboard.goals SET status = 'stopped' WHERE id = $1", goal_id)
    evaluator = ConditionEvaluator(PostgresConditionStore(runtime))
    await register(evaluator, DECISION, instance_id="c0")
    fired = await evaluator.evaluate("c0", [decision_ev()], now=CONDITION_NOW)
    assert fired.intent is not None

    async with runtime.acquire() as conn, conn.transaction():
        (pending,) = await WakeAdmission(conn).unadmitted(0)
    assert (await admit_fire(runtime, pending)).reason == "stopped"

    async with runtime.acquire() as conn, conn.transaction():
        assert await WakeAdmission(conn).unadmitted(0) == []


async def test_every_refusal_reason_has_its_own_precedence():
    refusal = WakeAdmission._refusal

    def env(**kw):
        base = {
            "generation": 0,
            "kind": "conditions",
            "state": "open",
            "past_deadline": False,
            "consumed_wakes": 0,
            "max_wakes": 1,
        }
        return base | kw

    assert refusal({"status": "stopped"}, env(), 0) == "stopped"
    assert refusal({"status": "completed"}, env(), 0) == "goal_not_waiting"
    assert refusal({"status": "waiting"}, env(generation=1), 0) == "stale_generation"
    assert refusal({"status": "waiting"}, env(state="fired"), 0) == "envelope_not_open"
    assert refusal({"status": "waiting"}, env(kind="settle"), 0) == "envelope_not_open"
    assert refusal({"status": "waiting"}, env(past_deadline=True), 0) == "expired"
    assert refusal({"status": "waiting"}, env(consumed_wakes=1), 0) == "cap_reached"
    assert refusal({"status": "waiting"}, env(), 0) is None
    assert admit is not None  # fixture import keeps the shared helpers' registration


async def test_an_inbox_row_cannot_bind_a_refused_fire(ledger):
    owner, runtime = ledger
    goal_id, _, env = await waiting_goal(owner, runtime)
    await owner.execute("UPDATE switchboard.goals SET status = 'stopped' WHERE id = $1", goal_id)
    fire = fire_for()
    assert (await admit_fire(runtime, fire)).state == "not_admitted"
    decision_id, key = await decision(
        owner,
        goal_id,
        reason="condition_joined",
        kind="condition_fire",
        source_kind="occurrence",
        source_ref=f"condition:{env}",
        occurrence=f"gen:0:event:{fire.event_id}",
    )

    with pytest.raises(asyncpg.PostgresError, match="admitted wake intent"):
        await owner.execute(
            "INSERT INTO switchboard.goal_inbox (id,goal_id,decision_id,source_key,source_ref,"
            "input_kind,event_id,envelope_id,envelope_generation) "
            "VALUES ($1,$2,$3,$4,'ref','condition_fire',$5,$6,0)",
            uuid4(),
            goal_id,
            decision_id,
            key,
            fire.event_id,
            env,
        )


async def test_one_event_fired_by_two_instances_is_not_swept_forever(ledger):
    owner, runtime = ledger
    goal_id, _, _ = await waiting_goal(owner, runtime, instances=["c0", "c1"])
    evaluator = ConditionEvaluator(PostgresConditionStore(runtime))
    await register(evaluator, DECISION, instance_id="c0")
    await register(evaluator, DECISION, instance_id="c1")
    # The wait registered two conditions in its generation; one event fires both.
    first = await evaluator.evaluate("c0", [decision_ev()], now=CONDITION_NOW)
    second = await evaluator.evaluate("c1", [decision_ev()], now=CONDITION_NOW)
    assert first.intent is not None and second.intent is not None
    assert first.intent.event_id == second.intent.event_id
    assert (await admit_fire(runtime, first.intent)).state == "admitted"
    # The second fire is the same event under another key: refused without a receipt of its own.
    await admit_fire(runtime, second.intent)

    async with runtime.acquire() as conn, conn.transaction():
        assert await WakeAdmission(conn).unadmitted(0) == []
    assert (await counts(owner, goal_id))["wakes"] == 1


async def test_a_fire_that_keeps_failing_is_closed_as_admit_error(ledger):
    owner, runtime = ledger
    goal_id, _, _ = await waiting_goal(owner, runtime)
    evaluator = ConditionEvaluator(PostgresConditionStore(runtime))
    await register(evaluator, DECISION, instance_id="c0")
    fired = await evaluator.evaluate("c0", [decision_ev()], now=CONDITION_NOW)
    assert fired.intent is not None

    async with runtime.acquire() as conn, conn.transaction():
        result = await WakeAdmission(conn).abandon(fired.intent)

    assert (result.state, result.reason) == ("not_admitted", "admit_error")
    async with runtime.acquire() as conn, conn.transaction():
        assert await WakeAdmission(conn).unadmitted(0) == []
    assert (await counts(owner, goal_id))["wakes"] == 1  # the receipt; no successor epoch
    assert (await counts(owner, goal_id))["epochs"] == 1  # the lead's own epoch only
