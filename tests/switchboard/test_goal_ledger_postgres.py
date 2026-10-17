"""Executing admission transactions and races over real Alembic PostgreSQL."""

import asyncio
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import asyncpg
import pytest

from butlers.core.goal_admission import (
    AdmissionUnavailable,
    Evidence,
    EvidenceRefused,
    GoalAdmission,
    Grant,
    Obligation,
    SourceRef,
)
from tests.switchboard.goal_admission_fixtures import Catalogs, Sources, limits
from tests.switchboard.goal_ledger_fixtures import (  # noqa: F401
    admit,
    configure,
    effect,
)

pytest_plugins = ("tests.switchboard.goal_ledger_fixtures",)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def test_admission_retry_is_atomic(ledger):
    owner, runtime = ledger
    source, attempt = SourceRef("message", "one-envelope"), uuid4()
    a, b = await asyncio.gather(
        admit(runtime, source=source, attempt=attempt), admit(runtime, source=source)
    )
    assert a == b
    assert await owner.fetchval("SELECT count(*) FROM switchboard.goals") == 1
    assert await owner.fetchval("SELECT count(*) FROM switchboard.budgets") == 1
    # Attempt IDs cannot be retargeted onto another durable envelope.
    with pytest.raises(AdmissionUnavailable):
        await admit(runtime, source=SourceRef("message", "forged-retry"), attempt=attempt)
    await configure(owner, limits(new_goals=0))
    assert await admit(runtime, source=source) == a
    denied_source, denied_attempt = SourceRef("message", "quota-retry"), uuid4()
    denied = await admit(runtime, source=denied_source, attempt=denied_attempt)
    assert denied.result == "refused"
    await configure(owner, limits())
    assert await admit(runtime, source=denied_source, attempt=denied_attempt) == denied
    accepted = await admit(runtime, source=denied_source)
    assert accepted.result == "admitted" and accepted.goal_id != a.goal_id
    assert await owner.fetchval("SELECT count(*) FROM switchboard.budgets") == 2


async def test_feed_quota_cannot_consume_owner_lane(ledger):
    owner, runtime = ledger
    policy = limits(new_goals=1, per_source=1)
    await configure(owner, policy)
    feed = Evidence(
        "feed", "feed-a", obligation=Obligation("health", "subscription", created_exposed=False)
    )
    a, b = await asyncio.gather(
        *[admit(runtime, feed, SourceRef("feed", str(uuid4()))) for _ in range(2)]
    )
    assert sorted((a.result, b.result)) == ["admitted", "refused"]
    owner_goal = await admit(runtime, Evidence("message", "owner", owner=True))
    assert owner_goal.result == "admitted"
    # Origin capacity remains, but the same finite source may not use it.
    policy["origins"][2]["new_goals"] = 3
    await configure(owner, policy)
    mail = Evidence("message", "mail-a")
    assert (await admit(runtime, mail)).result == "admitted"
    assert (await admit(runtime, mail)).reason == "quota_exhausted"
    assert (await admit(runtime, replace(mail, source_partition="mail-b"))).result == "admitted"
    rows = await owner.fetch(
        "SELECT * FROM switchboard.admission_decisions WHERE reason='quota_exhausted'"
    )
    assert all(r["retry_after"] == r["quota_window"] + timedelta(seconds=3600) for r in rows)


async def test_cause_creator_and_occurrence_boundaries(ledger):
    owner, runtime = ledger
    parent = await admit(runtime)
    invocation, effect_id = await effect(owner, parent.goal_id)
    obligation = Obligation(
        "health",
        "created-v1",
        (Grant("health.read"), Grant("home.actuate")),
        creator_invocation=invocation,
        created_exposed=True,
    )
    facts = Evidence(
        "occurrence",
        "schedule",
        obligation=obligation,
        caused_by_goal=parent.goal_id,
        caused_by_effect=effect_id,
    )
    child = await admit(runtime, facts, SourceRef("occurrence", "schedule-id", "tick-1"))
    row = await owner.fetchrow("SELECT * FROM switchboard.goals WHERE id=$1", child.goal_id)
    assert row["exposed"] and row["chain_depth"] == 1
    assert [g["capability"] for g in row["ceiling"]] == ["health.read"]
    assert (await admit(runtime, facts, SourceRef("occurrence", "schedule-id", "tick-1"))) == child
    _, second_effect = await effect(owner, child.goal_id)
    second = await admit(
        runtime,
        replace(facts, caused_by_goal=child.goal_id, caused_by_effect=second_effect),
        SourceRef("occurrence", "schedule-id", "tick-2"),
    )
    _, third_effect = await effect(owner, second.goal_id)
    refused = await admit(
        runtime,
        replace(facts, caused_by_goal=second.goal_id, caused_by_effect=third_effect),
        SourceRef("occurrence", "schedule-id", "tick-3"),
    )
    assert refused.reason == "causal_chain_limit" and refused.goal_id is None
    cause_row = await owner.fetchrow(
        "SELECT * FROM switchboard.admission_decisions WHERE id=$1", refused.decision_id
    )
    assert cause_row["caused_by_goal"] == second.goal_id
    assert (
        await admit(
            runtime,
            replace(facts, caused_by_effect=third_effect),
            SourceRef("occurrence", "bad", "1"),
        )
    ).reason == "cause_invalid"
    assert (
        await admit(
            runtime, replace(facts, caused_by_effect=None), SourceRef("occurrence", "bad", "2")
        )
    ).reason == "cause_invalid"


async def test_reply_binding_preserves_goal_authority(ledger):
    owner, runtime = ledger
    original = await admit(runtime)
    entity = uuid4()
    expiry = await owner.fetchval("SELECT now() + interval '1 hour'")
    _, effect_id = await effect(
        owner,
        original.goal_id,
        delivery="delivery-1",
        entity=entity,
        channel="telegram",
        accepts=True,
        expiry=expiry,
    )
    before = dict(
        await owner.fetchrow("SELECT * FROM switchboard.goals WHERE id=$1", original.goal_id)
    )
    budget = dict(
        await owner.fetchrow("SELECT * FROM switchboard.budgets WHERE goal_id=$1", original.goal_id)
    )
    facts = Evidence(
        "message",
        "owner",
        owner=True,
        reply_delivery="delivery-1",
        owner_entity=entity,
        owner_channel="telegram",
    )
    joined = await admit(runtime, facts)
    assert joined.result == "joined" and joined.goal_id == original.goal_id
    assert (
        dict(await owner.fetchrow("SELECT * FROM switchboard.goals WHERE id=$1", original.goal_id))
        == before
    )
    assert (
        dict(
            await owner.fetchrow(
                "SELECT * FROM switchboard.budgets WHERE goal_id=$1", original.goal_id
            )
        )
        == budget
    )
    for bad in (
        replace(facts, owner_entity=uuid4()),
        replace(facts, owner_channel="other"),
        replace(facts, reply_delivery="unknown"),
    ):
        assert (await admit(runtime, bad)).result == "admitted"
    # Hold the goal before transitioning terminal: the waiting reply must see Stop.
    async with owner.acquire() as connection, connection.transaction():
        await connection.execute(
            "SELECT id FROM switchboard.goals WHERE id=$1 FOR UPDATE", original.goal_id
        )
        waiting = asyncio.create_task(admit(runtime, facts))
        await asyncio.sleep(0.05)
        assert not waiting.done()
        await connection.execute(
            "UPDATE switchboard.goals SET status='stopped' WHERE id=$1", original.goal_id
        )
    # Expired prompts also create an ordinary owner goal, with no cause link.
    _, _ = await effect(
        owner,
        original.goal_id,
        delivery="expired",
        entity=entity,
        channel="telegram",
        accepts=True,
        expiry=expiry - timedelta(hours=2),
    )
    expired = await admit(runtime, replace(facts, reply_delivery="expired"))
    assert expired.result == "admitted" and expired.goal_id != original.goal_id
    terminal = await waiting
    assert terminal.result == "admitted" and terminal.goal_id != original.goal_id
    new = await owner.fetchrow("SELECT * FROM switchboard.goals WHERE id=$1", terminal.goal_id)
    assert new["origin"] == "owner" and new["caused_by_goal"] is None
    assert (
        await owner.fetchval(
            "SELECT delivery_ref FROM switchboard.admission_decisions WHERE id=$1",
            terminal.decision_id,
        )
        == "delivery-1"
    )


async def test_failure_and_cancellation_have_no_partial_success(ledger):
    owner, runtime = ledger
    for invalid in (None, {}, {**limits(), "origins": []}):
        await configure(owner, invalid)
        result = await admit(runtime)
        assert result.reason == "limits_unavailable"
    await configure(owner, limits())

    class Unavailable(Sources):
        async def load(self, connection, source):
            raise EvidenceRefused("identity_lookup_unavailable")

    class MissingCatalog:
        async def load(self, connection):
            raise EvidenceRefused("catalog_unavailable")

    assert (
        await GoalAdmission(runtime, Sources(), MissingCatalog()).admit_submission(
            SourceRef("message", "missing-catalog"), uuid4()
        )
    ).reason == "source_evidence_unavailable"
    service = GoalAdmission(runtime, Unavailable(), Catalogs())
    assert (
        await service.admit_submission(SourceRef("message", "unknown"), uuid4())
    ).reason == "source_evidence_unavailable"

    class Broken(Sources):
        async def load(self, connection, source):
            await connection.execute("SELECT definitely_missing_goal_admission_function()")

    with pytest.raises(AdmissionUnavailable):
        await GoalAdmission(runtime, Broken(), Catalogs()).admit_submission(
            SourceRef("message", "broken"), uuid4()
        )
    started = asyncio.Event()

    class Cancelled(Sources):
        async def load(self, connection, source):
            started.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(
        GoalAdmission(runtime, Cancelled(), Catalogs()).admit_submission(
            SourceRef("message", "cancelled"), uuid4()
        )
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await owner.fetchval("SELECT count(*) FROM switchboard.goals") == 0
    assert await owner.fetchval("SELECT count(*) FROM switchboard.budgets") == 0
    with pytest.raises(AdmissionUnavailable):
        await admit(owner)  # No privileged migration-login fallback.
    # A persistence failure after goal+budget INSERT must roll both back.
    await owner.execute("""
        CREATE FUNCTION switchboard.test_refuse_decision() RETURNS trigger LANGUAGE plpgsql AS
        $$ BEGIN RAISE EXCEPTION 'injected persistence failure'; END $$;
        CREATE TRIGGER test_refuse_decision BEFORE INSERT ON switchboard.admission_decisions
        FOR EACH ROW EXECUTE FUNCTION switchboard.test_refuse_decision()
    """)
    try:
        with pytest.raises(AdmissionUnavailable):
            await admit(runtime)
        assert await owner.fetchval("SELECT count(*) FROM switchboard.goals") == 0
        assert await owner.fetchval("SELECT count(*) FROM switchboard.budgets") == 0
    finally:
        await owner.execute(
            "DROP TRIGGER test_refuse_decision ON switchboard.admission_decisions; DROP FUNCTION switchboard.test_refuse_decision()"
        )
    # Inject a lost commit acknowledgement around a REAL committed transaction.
    # A retry must discover the durable association, never claim the first call succeeded.
    from contextlib import asynccontextmanager

    class CommitUncertain:
        def acquire(self):
            @asynccontextmanager
            async def acquired():
                async with runtime.acquire() as real:

                    class Connection:
                        def __getattr__(self, name):
                            return getattr(real, name)

                        @asynccontextmanager
                        async def transaction(self):
                            async with real.transaction():
                                yield
                            raise OSError("injected lost commit acknowledgement")

                    yield Connection()

            return acquired()

    uncertain_source = SourceRef("message", "uncertain")
    with pytest.raises(AdmissionUnavailable):
        await GoalAdmission(CommitUncertain(), Sources(), Catalogs()).admit_submission(
            uncertain_source, uuid4()
        )
    recovered = await admit(runtime, source=uncertain_source)
    assert recovered.result == "admitted"
    assert await owner.fetchval("SELECT count(*) FROM switchboard.goals") == 1
    assert await owner.fetchval("SELECT count(*) FROM switchboard.budgets") == 1


async def test_schema_rejects_cross_goal_and_mutable_authority(ledger):
    owner, runtime = ledger
    a, b = await admit(runtime), await admit(runtime)
    lead, effect_id = await effect(owner, a.goal_id)
    child = uuid4()
    await owner.execute(
        "INSERT INTO switchboard.invocations (id,goal_id,parent_invocation_id,depth,role,lead_epoch,status,grant_snapshot,grant_digest,max_turns,max_tokens) VALUES ($1,$2,$3,1,'child',0,'returned','[]','digest',1,1)",
        child,
        a.goal_id,
        lead,
    )
    await owner.execute("UPDATE switchboard.invocations SET status='running' WHERE id=$1", lead)
    async with owner.acquire() as conn:
        cases = [
            (
                "INSERT INTO switchboard.invocations (id,goal_id,parent_invocation_id,depth,role,lead_epoch,status,grant_snapshot,grant_digest,max_turns,max_tokens) VALUES ($1,$2,$3,1,'child',0,'launching','[]','digest',1,1)",
                uuid4(),
                a.goal_id,
                child,
            ),
            (
                "INSERT INTO switchboard.invocations (id,goal_id,depth,role,lead_epoch,status,grant_snapshot,grant_digest,max_turns,max_tokens) VALUES ($1,$2,0,'lead',1,'launching','[]','digest',1,1)",
                uuid4(),
                a.goal_id,
            ),
            ("UPDATE switchboard.invocations SET used_turns=max_turns+1 WHERE id=$1", lead),
            ("UPDATE switchboard.invocations SET grant_snapshot='[]' WHERE id=$1", lead),
            (
                "UPDATE switchboard.budgets SET active_children=max_children+1 WHERE goal_id=$1",
                a.goal_id,
            ),
            (
                "UPDATE switchboard.budgets SET reserved_calls=max_calls, used_calls=1 WHERE goal_id=$1",
                a.goal_id,
            ),
            (
                "UPDATE switchboard.budgets SET reserved_spend='Infinity' WHERE goal_id=$1",
                a.goal_id,
            ),
            ("UPDATE switchboard.goals SET status='refused' WHERE id=$1", a.goal_id),
            ("UPDATE switchboard.goals SET ceiling='[]'::jsonb WHERE id=$1", a.goal_id),
            ("UPDATE switchboard.goals SET exposed=false WHERE id=$1", a.goal_id),
            (
                "UPDATE switchboard.admission_decisions SET goal_id=$1 WHERE id=$2",
                b.goal_id,
                a.decision_id,
            ),
            ("UPDATE switchboard.budgets SET used_tokens=max_tokens+1 WHERE goal_id=$1", a.goal_id),
            ("UPDATE switchboard.budgets SET max_spend='NaN' WHERE goal_id=$1", a.goal_id),
            (
                "INSERT INTO switchboard.effects (id,goal_id,invocation_id,capability,args_digest,status) VALUES ($1,$2,$3,'x','digest','requested')",
                uuid4(),
                b.goal_id,
                lead,
            ),
            (
                "INSERT INTO switchboard.invocations (id,goal_id,parent_invocation_id,depth,role,lead_epoch,status,grant_snapshot,grant_digest,max_turns,max_tokens) VALUES ($1,$2,$3,1,'child',0,'launching','[]','digest',1,1)",
                uuid4(),
                b.goal_id,
                lead,
            ),
        ]
        for query, *values in cases:
            with pytest.raises(asyncpg.PostgresError):
                async with conn.transaction():
                    await conn.execute(query, *values)
    assert (
        await owner.fetchval("SELECT goal_id FROM switchboard.effects WHERE id=$1", effect_id)
        == a.goal_id
    )


async def test_waiting_admission_charges_its_captured_window(ledger):
    owner, runtime = ledger
    await configure(owner, limits(window_seconds=1, new_goals=1))
    captured, release = asyncio.Event(), asyncio.Event()

    class Waiting(Sources):
        async def load(self, connection, source):
            self.captured_time = await connection.fetchval("SELECT clock_timestamp()")
            captured.set()
            await release.wait()
            return self.facts

    provider = Waiting()
    task = asyncio.create_task(
        GoalAdmission(runtime, provider, Catalogs()).admit_submission(
            SourceRef("message", "window-wait"), uuid4()
        )
    )
    await captured.wait()
    # Service captures its authoritative time immediately before provider.load.
    # Wait across two whole windows; never replace that key with created_at.
    await asyncio.sleep(2.05)
    release.set()
    result = await task
    row = await owner.fetchrow(
        "SELECT * FROM switchboard.admission_decisions WHERE id=$1", result.decision_id
    )
    assert row["quota_window"] <= provider.captured_time
    assert (await owner.fetchval("SELECT clock_timestamp()")) - row["quota_window"] >= timedelta(
        seconds=2
    )
    assert (await admit(runtime)).result == "admitted"
