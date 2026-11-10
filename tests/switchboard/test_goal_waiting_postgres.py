"""sw_063 waiting records on a real migrated ledger (RFC 0038 S2, S2.1, S2.2).

Each test names the invariant a writer must not be able to break: plan chains, the immutable
envelope bounds and its one-wake-per-fire counter, the unique successor per generation, a refused
fire ending in a terminal ``not_admitted`` row, discriminated inbox kinds and one-time
consumption, and the derived epoch record under the current runner.
"""

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import asyncpg
import pytest

from butlers.core.goal_admission import Evidence
from butlers.core.goal_labels import Label
from butlers.core.goal_waiting import EnvelopeSpec, WaitLedger
from tests.switchboard.goal_ledger_fixtures import admit, effect

pytest_plugins = ("tests.switchboard.goal_ledger_fixtures",)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

OWNER = Evidence("message", "owner", owner=True)
DIGEST = hashlib.sha256(b"x").hexdigest()


async def refused(conn, query, *args):
    with pytest.raises(asyncpg.PostgresError):
        async with conn.transaction():
            await conn.execute(query, *args)


async def goal_with_lead(owner, runtime):
    goal = await admit(runtime, OWNER)
    lead, _ = await effect(owner, goal.goal_id)
    return goal.goal_id, lead


async def plan(owner, goal_id, version=1, predecessor=None, epoch=0):
    await owner.execute(
        "INSERT INTO switchboard.goal_plans (goal_id,version,predecessor_version,created_epoch,"
        "digest) VALUES ($1,$2,$3,$4,$5)",
        goal_id,
        version,
        predecessor,
        epoch,
        DIGEST,
    )


async def envelope(owner, goal_id, *, kind="conditions", max_wakes=2, deadline="1 hour", **extra):
    envelope_id = uuid4()
    await owner.execute(
        "INSERT INTO switchboard.wait_envelopes (id,goal_id,kind,original_epoch,plan_version,"
        "plan_digest,predicate,predicate_digest,source_set,permitted_sources,grant_snapshot,"
        "grant_digest,label_version,label_instr,label_data,label_sens,deadline,max_wakes,"
        'approval_effect_id,approval_digest) VALUES ($1,$2,$3,0,1,$4,\'{"tag":"owner_input"}\','
        f"$4,'{{owner}}','{{owner}}','[]',$4,1,'{{owner}}','{{owner}}','{{}}',"
        f"now() + interval '{deadline}',$5,$6,$7)",
        envelope_id,
        goal_id,
        kind,
        DIGEST,
        max_wakes,
        extra.get("approval_effect_id"),
        DIGEST if extra.get("approval_effect_id") else None,
    )
    await owner.execute(
        "INSERT INTO switchboard.wait_generations (goal_id,envelope_id,generation,"
        "condition_instance_ids) VALUES ($1,$2,0,'{c0}')",
        goal_id,
        envelope_id,
    )
    return envelope_id


async def wake_epoch(owner, goal_id, lead, epoch):
    await owner.execute(
        "INSERT INTO switchboard.goal_epochs (goal_id,epoch,predecessor_invocation_id,reason,"
        "label_version,label_instr,label_data,label_sens,state) "
        "VALUES ($1,$2,$3,'wake',1,'{owner}','{owner}','{}','pending')",
        goal_id,
        epoch,
        lead,
    )


async def intent(owner, goal_id, envelope_id, *, generation=0, epoch=1, state="pending", **extra):
    intent_id = uuid4()
    await owner.execute(
        "INSERT INTO switchboard.wake_intents (id,goal_id,envelope_id,generation,fire_key,"
        "fire_event_id,epoch,state,not_admitted_reason) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)",
        intent_id,
        goal_id,
        envelope_id,
        generation,
        extra.get("fire_key", uuid4()),
        extra.get("event", uuid4()),
        epoch,
        state,
        extra.get("reason"),
    )
    return intent_id


async def decision(owner, goal_id, *, reason, kind, source_kind, source_ref, occurrence=None):
    decision_id, key = uuid4(), f"key-{uuid4()}"
    await owner.execute(
        "INSERT INTO switchboard.admission_decisions (id,attempt_id,source_key,source_ref,"
        "source_kind,goal_id,result,reason,occurrence_key,input_kind) "
        "VALUES ($1,$2,$3,$4,$5,$6,'joined',$7,$8,$9)",
        decision_id,
        uuid4(),
        key,
        source_ref,
        source_kind,
        goal_id,
        reason,
        occurrence,
        kind,
    )
    return decision_id, key


async def test_plan_versions_form_an_append_only_chain(ledger):
    owner, runtime = ledger
    goal_id, _ = await goal_with_lead(owner, runtime)
    await plan(owner, goal_id)
    await plan(owner, goal_id, 2, 1)
    async with owner.acquire() as conn:
        # Skipping a version or naming the wrong predecessor is refused, and so is any rewrite.
        for args in ((3, 1), (4, 3), (1, None)):
            with pytest.raises(asyncpg.PostgresError):
                async with conn.transaction():
                    await plan(conn, goal_id, *args)
        await refused(conn, "UPDATE switchboard.goal_plans SET digest = $1", "0" * 64)
        await refused(conn, "DELETE FROM switchboard.goal_plans")
        # The goal binds a plan version that exists, and never moves back.
        await refused(conn, "UPDATE switchboard.goals SET plan_version = 9 WHERE id = $1", goal_id)
        await conn.execute("UPDATE switchboard.goals SET plan_version = 2 WHERE id = $1", goal_id)
        await refused(conn, "UPDATE switchboard.goals SET plan_version = 1 WHERE id = $1", goal_id)


async def test_a_lead_invocation_derives_its_epoch_and_the_epoch_only_moves_forward(ledger):
    owner, runtime = ledger
    goal_id, lead = await goal_with_lead(owner, runtime)
    epoch = await owner.fetchrow(
        "SELECT * FROM switchboard.goal_epochs WHERE goal_id = $1 AND epoch = 0", goal_id
    )
    assert (epoch["reason"], epoch["state"]) == ("initial", "finished")
    # A recovery lead links to its predecessor and follows its own status.
    second = uuid4()
    await owner.execute(
        "INSERT INTO switchboard.invocations (id,goal_id,depth,role,lead_epoch,status,"
        "grant_snapshot,grant_digest,max_turns,max_tokens) "
        "VALUES ($1,$2,0,'lead',1,'launching','[]','d',1,1)",
        second,
        goal_id,
    )
    row = await owner.fetchrow(
        "SELECT * FROM switchboard.goal_epochs WHERE goal_id = $1 AND epoch = 1", goal_id
    )
    assert (row["reason"], row["state"], row["predecessor_invocation_id"]) == (
        "recovery",
        "launching",
        lead,
    )
    await owner.execute(
        "UPDATE switchboard.invocations SET status = 'running' WHERE id = $1", second
    )
    assert (
        await owner.fetchval(
            "SELECT state FROM switchboard.goal_epochs WHERE goal_id = $1 AND epoch = 1", goal_id
        )
        == "running"
    )
    async with owner.acquire() as conn:
        await refused(
            conn,
            "UPDATE switchboard.goal_epochs SET state = 'pending' WHERE goal_id = $1 AND epoch = 1",
            goal_id,
        )
        await refused(
            conn,
            "UPDATE switchboard.goal_epochs SET reason = 'initial', predecessor_invocation_id = NULL "
            "WHERE goal_id = $1 AND epoch = 1",
            goal_id,
        )


async def test_envelope_bounds_are_immutable_and_each_fire_counts_one_wake(ledger):
    owner, runtime = ledger
    goal_id, _ = await goal_with_lead(owner, runtime)
    await plan(owner, goal_id)
    env = await envelope(owner, goal_id, max_wakes=1)
    async with owner.acquire() as conn:
        # Bounds never change, even by the owner; there is one live envelope per goal.
        for column, value in (
            ("max_wakes", "5"),
            ("deadline", "now() + interval '9 days'"),
            ("predicate", '\'{"tag":"at"}\'::jsonb'),
            ("kind", "'settle'"),
        ):
            await refused(conn, f"UPDATE switchboard.wait_envelopes SET {column} = {value}")
        with pytest.raises(asyncpg.UniqueViolationError):
            await envelope(conn, goal_id)
        # A fire is exactly one wake; the counter cannot move any other way.
        await refused(conn, "UPDATE switchboard.wait_envelopes SET consumed_wakes = 1")
        await refused(
            conn, "UPDATE switchboard.wait_envelopes SET state = 'fired', consumed_wakes = 0"
        )
        await conn.execute(
            "UPDATE switchboard.wait_envelopes SET state = 'fired', consumed_wakes = 1 WHERE id = $1",
            env,
        )
        # Rearm is one generation per fired->open, and never past the cumulative cap.
        await refused(conn, "UPDATE switchboard.wait_envelopes SET state = 'open'")
        await conn.execute(
            "UPDATE switchboard.wait_envelopes SET state = 'open', generation = 1 WHERE id = $1",
            env,
        )
        await refused(
            conn,
            "UPDATE switchboard.wait_envelopes SET state = 'fired', consumed_wakes = 2 WHERE id = $1",
            env,
        )
        await conn.execute(
            "UPDATE switchboard.wait_envelopes SET state = 'expired' WHERE id = $1", env
        )
        await refused(
            conn, "UPDATE switchboard.wait_envelopes SET state = 'open' WHERE id = $1", env
        )


async def test_envelope_creation_refuses_unbound_or_unbounded_waits(ledger):
    owner, runtime = ledger
    goal_id, _ = await goal_with_lead(owner, runtime)
    async with owner.acquire() as conn:
        # No plan version yet: the envelope has nothing to bind.
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await envelope(conn, goal_id)
    await plan(owner, goal_id)
    async with owner.acquire() as conn:
        with pytest.raises(asyncpg.PostgresError):
            await envelope(conn, goal_id, deadline="infinity")
        with pytest.raises(asyncpg.PostgresError):
            await envelope(conn, goal_id, max_wakes=0)
        # An approval wait names its approved effect.
        with pytest.raises(asyncpg.PostgresError):
            await envelope(conn, goal_id, kind="approval")
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await envelope(conn, goal_id, kind="approval", approval_effect_id=uuid4())


async def test_one_successor_per_generation_and_a_refused_fire_ends_terminal(ledger):
    owner, runtime = ledger
    goal_id, lead = await goal_with_lead(owner, runtime)
    await plan(owner, goal_id)
    env = await envelope(owner, goal_id)
    await wake_epoch(owner, goal_id, lead, 1)
    fire_key = uuid4()
    first = await intent(owner, goal_id, env, fire_key=fire_key)
    async with owner.acquire() as conn:
        # The same fire, or a second fire of the generation, cannot mint another successor.
        with pytest.raises(asyncpg.UniqueViolationError):
            await intent(conn, goal_id, env, fire_key=fire_key, epoch=2)
        await wake_epoch(conn, goal_id, lead, 2)
        with pytest.raises(asyncpg.UniqueViolationError):
            await intent(conn, goal_id, env, epoch=2)
        # A fire the ledger refused is recorded once, terminally, with its reason and no epoch.
        refused_fire = await intent(
            conn, goal_id, env, epoch=None, state="not_admitted", reason="stopped"
        )
        with pytest.raises(asyncpg.PostgresError):
            await intent(conn, goal_id, env, epoch=2, state="not_admitted", reason="stopped")
        with pytest.raises(asyncpg.PostgresError):
            await intent(conn, goal_id, env, epoch=None, state="not_admitted")
        await refused(
            conn,
            "UPDATE switchboard.wake_intents SET state = 'claimed' WHERE id = $1",
            refused_fire,
        )
        # The launch outbox: claim, fail before an invocation, retry the same intent, launch.
        claim = (
            "UPDATE switchboard.wake_intents SET state = 'claimed', claimed_by = 'w', "
            "claim_expires_at = now() + interval '1 minute', attempts = attempts + 1 WHERE id = $1"
        )
        await conn.execute(claim, first)
        await conn.execute(
            "UPDATE switchboard.wake_intents SET state = 'failed', claimed_by = NULL, "
            "claim_expires_at = NULL WHERE id = $1",
            first,
        )
        await conn.execute(claim, first)
        await conn.execute(
            "UPDATE switchboard.wake_intents SET state = 'launched', claimed_by = NULL, "
            "claim_expires_at = NULL WHERE id = $1",
            first,
        )
        await refused(conn, claim, first)
        await refused(conn, "UPDATE switchboard.wake_intents SET epoch = 2 WHERE id = $1", first)


@pytest.mark.parametrize("from_state", ["pending", "failed", "claimed"])
async def test_stop_cancels_an_unlaunched_wake_and_it_cannot_be_claimed(ledger, from_state):
    owner, runtime = ledger
    goal_id, lead = await goal_with_lead(owner, runtime)
    await plan(owner, goal_id)
    env = await envelope(owner, goal_id)
    await wake_epoch(owner, goal_id, lead, 1)
    wake = await intent(owner, goal_id, env)
    async with owner.acquire() as conn:
        claim = (
            "UPDATE switchboard.wake_intents SET state = 'claimed', claimed_by = 'w', "
            "claim_expires_at = now() + interval '1 minute', attempts = attempts + 1 WHERE id = $1"
        )
        if from_state != "pending":
            await conn.execute(claim, wake)
        if from_state == "failed":
            await conn.execute(
                "UPDATE switchboard.wake_intents SET state = 'failed', claimed_by = NULL, "
                "claim_expires_at = NULL WHERE id = $1",
                wake,
            )
        cancel = (
            "UPDATE switchboard.wake_intents SET state = 'cancelled', cancelled_reason = 'stopped', "
            "claimed_by = NULL, claim_expires_at = NULL WHERE id = $1"
        )
        # A cancellation names its reason, and only a cancellation carries one.
        await refused(
            conn,
            "UPDATE switchboard.wake_intents SET state = 'cancelled', claimed_by = NULL, "
            "claim_expires_at = NULL WHERE id = $1",
            wake,
        )
        await conn.execute(cancel, wake)
        await refused(conn, claim, wake)
        await refused(
            conn, "UPDATE switchboard.wake_intents SET state = 'pending' WHERE id = $1", wake
        )
        # Out of the open index, yet still the generation's one successor.
        still_open = await owner.fetchval(
            "SELECT count(*) FROM switchboard.wake_intents WHERE id = $1 "
            "AND state IN ('pending','claimed','failed')",
            wake,
        )
        assert still_open == 0
        await wake_epoch(owner, goal_id, lead, 2)
        with pytest.raises(asyncpg.UniqueViolationError):
            await intent(owner, goal_id, env, epoch=2)


async def test_a_launched_wake_cannot_be_cancelled(ledger):
    owner, runtime = ledger
    goal_id, lead = await goal_with_lead(owner, runtime)
    await plan(owner, goal_id)
    env = await envelope(owner, goal_id)
    await wake_epoch(owner, goal_id, lead, 1)
    wake = await intent(owner, goal_id, env, state="launched")
    async with owner.acquire() as conn:
        await refused(
            conn,
            "UPDATE switchboard.wake_intents SET state = 'cancelled', cancelled_reason = 'stopped' "
            "WHERE id = $1",
            wake,
        )


async def test_cancel_waits_closes_envelopes_and_unlaunched_wakes(ledger):
    owner, runtime = ledger
    goal_id, lead = await goal_with_lead(owner, runtime)
    await plan(owner, goal_id)
    env = await envelope(owner, goal_id)
    await wake_epoch(owner, goal_id, lead, 1)
    wake = await intent(owner, goal_id, env)
    async with owner.acquire() as conn, conn.transaction():
        await WaitLedger(conn).cancel_waits(goal_id, "stopped")
    row = await owner.fetchrow("SELECT state, cancelled_reason FROM switchboard.wake_intents")
    assert (row["state"], row["cancelled_reason"]) == ("cancelled", "stopped")
    assert await owner.fetchval("SELECT state FROM switchboard.wait_envelopes") == "cancelled"
    assert wake is not None


async def test_inbox_kinds_are_discriminated_and_consumed_once(ledger):
    owner, runtime = ledger
    goal_id, lead = await goal_with_lead(owner, runtime)
    await plan(owner, goal_id)
    env = await envelope(owner, goal_id)
    await wake_epoch(owner, goal_id, lead, 1)
    event = uuid4()
    await intent(owner, goal_id, env, event=event)

    async def inbox(conn, decision_id, key, kind, **extra):
        await conn.execute(
            "INSERT INTO switchboard.goal_inbox (id,goal_id,decision_id,source_key,source_ref,"
            "input_kind,event_id,envelope_id,envelope_generation) "
            "VALUES ($1,$2,$3,$4,'ref',$5,$6,$7,$8)",
            uuid4(),
            goal_id,
            decision_id,
            key,
            kind,
            extra.get("event"),
            extra.get("envelope"),
            extra.get("generation"),
        )

    fire = {"event": event, "envelope": env, "generation": 0}
    good, good_key = await decision(
        owner,
        goal_id,
        reason="condition_joined",
        kind="condition_fire",
        source_kind="occurrence",
        source_ref=f"condition:{env}",
        occurrence=f"gen:0:event:{event}",
    )
    async with owner.acquire() as conn:
        # A condition fire needs its event, envelope generation and a fire the evaluator recorded.
        with pytest.raises(asyncpg.PostgresError):
            await inbox(conn, good, good_key, "condition_fire")
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await inbox(conn, good, good_key, "condition_fire", **{**fire, "event": uuid4()})
        # The kind must match the joined decision, and a reply cannot ride a condition decision.
        with pytest.raises(asyncpg.PostgresError):
            await inbox(conn, good, good_key, "owner_non_reply")
        # A decision for another occurrence is not this fire's decision.
        other, other_key = await decision(
            owner,
            goal_id,
            reason="condition_joined",
            kind="condition_fire",
            source_kind="occurrence",
            source_ref=f"condition:{env}",
            occurrence=f"gen:0:event:{uuid4()}",
        )
        with pytest.raises(asyncpg.PostgresError):
            await inbox(conn, other, other_key, "condition_fire", **fire)
        await inbox(conn, good, good_key, "condition_fire", **fire)
        row = await conn.fetchrow(
            "SELECT * FROM switchboard.goal_inbox WHERE decision_id = $1", good
        )
        # Never a fake reply: no delivery or reply effect, and the envelope's label is carried.
        assert (row["delivery_ref"], row["reply_effect_id"]) == (None, None)
        assert row["label_instr"] == ["owner"]

        # A verified non-reply owner message joins with no reply effect.
        msg, msg_key = await decision(
            owner,
            goal_id,
            reason="input_joined",
            kind="owner_non_reply",
            source_kind="message",
            source_ref="m1",
        )
        await inbox(conn, msg, msg_key, "owner_non_reply")
        # Consumption is one tuple, once, bound to a real epoch of this goal.
        consume = (
            "UPDATE switchboard.goal_inbox SET consumed_at = now(), consumed_epoch = $2 "
            "WHERE decision_id = $1"
        )
        await refused(
            conn,
            "UPDATE switchboard.goal_inbox SET consumed_at = now() WHERE decision_id = $1",
            good,
        )
        await refused(conn, consume, good, 77)
        await conn.execute(consume, good, 1)
        await refused(conn, consume, good, 0)
        await refused(
            conn,
            "UPDATE switchboard.goal_inbox SET consumed_at = NULL, consumed_epoch = NULL WHERE decision_id = $1",
            good,
        )
        # Source fields never change, and the legacy marker cannot be set by a new writer.
        await refused(
            conn,
            "UPDATE switchboard.goal_inbox SET event_id = $2 WHERE decision_id = $1",
            good,
            uuid4(),
        )
        with pytest.raises(asyncpg.PostgresError):
            await conn.execute(
                "INSERT INTO switchboard.goal_inbox (id,goal_id,decision_id,source_key,source_ref,"
                "input_kind,legacy_consumed,consumed_at) VALUES ($1,$2,$3,$4,'r','owner_non_reply',"
                "true,now())",
                uuid4(),
                goal_id,
                uuid4(),
                "k",
            )


async def test_runtime_role_writes_waiting_records_but_cannot_delete_them(ledger):
    owner, runtime = ledger
    goal_id, lead = await goal_with_lead(owner, runtime)
    async with runtime.acquire() as conn:
        thread = uuid4()
        await conn.execute(
            "INSERT INTO switchboard.owner_threads (id,owner_entity_id,channel,conversation_key) "
            "VALUES ($1,$2,'telegram','chat-1')",
            thread,
            uuid4(),
        )
        # One identity per (owner, channel, conversation); the registry never rewrites a row.
        await refused(
            conn,
            "INSERT INTO switchboard.owner_threads (id,owner_entity_id,channel,conversation_key) "
            "SELECT $1, owner_entity_id, channel, conversation_key FROM switchboard.owner_threads "
            "WHERE id = $2",
            uuid4(),
            thread,
        )
        # RLS leaves no row to update, so the runtime role's rewrite changes nothing.
        assert (
            await conn.execute(
                "UPDATE switchboard.owner_threads SET channel = 'x' WHERE id = $1", thread
            )
            == "UPDATE 0"
        )
        await refused(conn, "DELETE FROM switchboard.owner_threads")
        await refused(conn, "DELETE FROM switchboard.goal_epochs")
    # Durable even to the owner: only the test reset lifts the guard.
    async with owner.acquire() as conn:
        await refused(conn, "DELETE FROM switchboard.goal_epochs")
        await refused(conn, "TRUNCATE switchboard.owner_threads CASCADE")
    assert (
        await owner.fetchval(
            "SELECT count(*) FROM switchboard.goal_epochs WHERE goal_id = $1", goal_id
        )
        == 1
    )


def _spec(goal_id, **extra):
    return EnvelopeSpec(
        kind=extra.pop("kind", "conditions"),
        goal_id=goal_id,
        original_epoch=0,
        plan_version=1,
        plan_digest=DIGEST,
        predicate={"tag": "owner_input"},
        predicate_digest=DIGEST,
        source_set=["owner"],
        permitted_sources=["owner"],
        grant_snapshot=[],
        grant_digest=DIGEST,
        label=Label(
            version=1, instr=frozenset({"owner"}), data=frozenset({"owner"}), sens=frozenset()
        ),
        deadline=extra.pop("deadline", datetime.now(UTC) + timedelta(hours=1)),
        max_wakes=extra.pop("max_wakes", 2),
        condition_instance_ids=["c0"],
        **extra,
    )


async def test_thread_registry_and_plan_versions_through_the_ledger_ops(ledger):
    owner, runtime = ledger
    goal_id, _ = await goal_with_lead(owner, runtime)
    entity = uuid4()
    async with runtime.acquire() as conn:
        waiting = WaitLedger(conn)
        first = await waiting.thread(entity, "telegram", "chat-9")
        assert await waiting.thread(entity, "telegram", "chat-9") == first
        assert await waiting.thread(entity, "telegram", "chat-10") != first
        v1 = await waiting.append_plan(goal_id, 0, ["m1"], ["e1"], ["s1"])
        # A retry of the same content is the same version; a changed premise is a new one.
        assert await waiting.append_plan(goal_id, 0, ["m1"], ["e1"], ["s1"]) == v1 == 1
        assert await waiting.append_plan(goal_id, 0, ["m2"], ["e1"], ["s1"]) == 2
    assert (
        await owner.fetchval("SELECT plan_version FROM switchboard.goals WHERE id=$1", goal_id) == 2
    )
    assert await owner.fetchval(
        "SELECT premise_refs FROM switchboard.goal_plans WHERE goal_id=$1 AND version=1",
        goal_id,
    ) == ["m1"]


async def test_rearm_reuses_one_envelope_only_inside_its_bounds(ledger):
    owner, runtime = ledger
    goal_id, lead = await goal_with_lead(owner, runtime)
    async with runtime.acquire() as conn:
        waiting = WaitLedger(conn)
        await waiting.append_plan(goal_id, 0, ["m1"], [], [])
        env = await waiting.open_envelope(_spec(goal_id, max_wakes=2))
        assert env is not None
        # An open envelope has nothing to rearm; a fired one needs its successor to have launched.
        assert await waiting.rearm(goal_id, env, ["c1"], {}) is None
    await owner.execute(
        "UPDATE switchboard.wait_envelopes SET state='fired', consumed_wakes=1 WHERE id=$1", env
    )
    async with runtime.acquire() as conn:
        waiting = WaitLedger(conn)
        assert await waiting.rearm(goal_id, env, ["c1"], {}) is None
    await wake_epoch(owner, goal_id, lead, 1)
    await intent(owner, goal_id, env, state="launched")
    async with runtime.acquire() as conn:
        waiting = WaitLedger(conn)
        # A live lead blocks the rearm; the successor must have returned to waiting first.
        await owner.execute("UPDATE switchboard.invocations SET status='running' WHERE id=$1", lead)
        assert await waiting.rearm(goal_id, env, ["c1"], {}) is None
        await owner.execute(
            "UPDATE switchboard.invocations SET status='returned' WHERE id=$1", lead
        )
        generation = await waiting.rearm(goal_id, env, ["c1"], {"decision": 4})
        assert generation == 1
        # A retry after commit returns the generation already opened and counts nothing twice.
        assert await waiting.rearm(goal_id, env, ["c9"], {}) == 1
    row = await owner.fetchrow("SELECT * FROM switchboard.wait_envelopes WHERE id=$1", env)
    assert (row["state"], row["generation"], row["consumed_wakes"]) == ("open", 1, 1)
    registered = await owner.fetchrow(
        "SELECT * FROM switchboard.wait_generations WHERE envelope_id=$1 AND generation=1", env
    )
    assert registered["condition_instance_ids"] == ["c1"]
    assert registered["predecessor_epoch"] == 1
    assert registered["registration_cursor"] == {"decision": 4}
    assert (
        await owner.fetchval("SELECT wait_generation FROM switchboard.goals WHERE id=$1", goal_id)
        == 1
    )

    # The cumulative cap, a Stopped goal and an expired deadline each forbid another generation.
    await owner.execute(
        "UPDATE switchboard.wait_envelopes SET state='fired', consumed_wakes=2 WHERE id=$1", env
    )
    await wake_epoch(owner, goal_id, lead, 2)
    await intent(owner, goal_id, env, generation=1, epoch=2, state="launched")
    async with runtime.acquire() as conn:
        assert await WaitLedger(conn).rearm(goal_id, env, ["c2"], {}) is None
    await owner.execute("UPDATE switchboard.goals SET status='stopped' WHERE id=$1", goal_id)
    async with runtime.acquire() as conn:
        assert await WaitLedger(conn).rearm(goal_id, env, ["c2"], {}) is None
        assert await WaitLedger(conn).open_envelope(_spec(goal_id)) is None


async def test_a_new_envelope_supersedes_a_fired_one_and_closing_is_idempotent(ledger):
    owner, runtime = ledger
    goal_id, _ = await goal_with_lead(owner, runtime)
    async with runtime.acquire() as conn:
        waiting = WaitLedger(conn)
        await waiting.append_plan(goal_id, 0, ["m1"], [], [])
        first = await waiting.open_envelope(_spec(goal_id))
    await owner.execute(
        "UPDATE switchboard.wait_envelopes SET state='fired', consumed_wakes=1 WHERE id=$1", first
    )
    async with runtime.acquire() as conn:
        waiting = WaitLedger(conn)
        second = await waiting.open_envelope(_spec(goal_id))
        assert second not in (None, first)
        assert await waiting.close_envelope(goal_id, second, "cancelled") is True
        assert await waiting.close_envelope(goal_id, second, "expired") is False
    states = {
        r["id"]: r["state"]
        for r in await owner.fetch("SELECT id, state FROM switchboard.wait_envelopes")
    }
    assert states == {first: "cancelled", second: "cancelled"}
