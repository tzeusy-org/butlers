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
from butlers.tools.switchboard.ingestion.goal_input import scoped_reply_ref
from tests.polling import poll_until
from tests.switchboard.accepted_source_fixtures import (
    ObservedSource,
    accept,
    admit_source,
    verified_owner,
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
    # Deterministic losing-attempt sequence; concurrency alone did not force
    # this ordering and could miss an unbound returned retry.
    canonical = await admit(runtime, source=source)
    assert await admit(runtime, source=source, attempt=attempt) == canonical
    assert (
        await owner.fetchval(
            "SELECT decision_id FROM switchboard.admission_attempt_bindings WHERE attempt_id=$1",
            attempt,
        )
        == canonical.decision_id
    )
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
    # Repeat the contention/retry through real ingest and retained marked input.
    async with verified_owner(owner):
        durable = await accept(owner, markers=[{"kind": "quoted", "start": 0, "end": 5}])
        reader = ObservedSource()
        service = GoalAdmission(runtime, reader, Catalogs())
        attempt = uuid4()
        first, second = await asyncio.gather(
            service.admit_submission(durable, attempt), service.admit_submission(durable, uuid4())
        )
        assert first == second and first.result == "admitted"
        assert len(reader.inputs) == 1 and reader.inputs[0].prepared_input.marked
        assert await service.admit_submission(durable, attempt) == first
        assert await owner.fetchval(
            "SELECT exposed FROM switchboard.goals WHERE id=$1", first.goal_id
        )
        assert await owner.fetchval("SELECT count(*) FROM switchboard.budgets") == 3
        with pytest.raises(AdmissionUnavailable):
            await service.admit_submission(await accept(owner), attempt)

        started = asyncio.Event()

        class PausedAccepted(ObservedSource):
            async def load(self, connection, source):
                value = await super().load(connection, source)
                started.set()
                await asyncio.Event().wait()
                return value

        cancelled_source = await accept(owner)
        cancelled_attempt = uuid4()
        task = asyncio.create_task(
            GoalAdmission(runtime, PausedAccepted(), Catalogs()).admit_submission(
                cancelled_source, cancelled_attempt
            )
        )
        try:
            await asyncio.wait_for(started.wait(), 5)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert not await owner.fetchval(
            "SELECT EXISTS(SELECT 1 FROM switchboard.admission_attempt_bindings WHERE attempt_id=$1)",
            cancelled_attempt,
        )
        assert not await owner.fetchval(
            "SELECT request_context ? '_goal_input_digest' FROM switchboard.message_inbox WHERE request_context->>'request_id'=$1",
            cancelled_source.id,
        )
        assert await owner.fetchval("SELECT count(*) FROM switchboard.budgets") == 3


@pytest.mark.parametrize("same_source", [True, False])
async def test_attempt_and_source_contention_preserves_canonical_binding(ledger, same_source):
    from butlers.core.goal_ledger import GoalLedger

    owner, runtime = ledger
    source = SourceRef("message", "contended-source")
    canonical = await admit(runtime, source=source)
    attempts = [uuid4(), uuid4()]
    sources = [source, source if same_source else SourceRef("message", "other-source")]
    if not same_source:
        attempts[1] = attempts[0]
    key = "source:" + source.key if same_source else "attempt:" + str(attempts[0])
    tasks = []
    try:
        async with owner.acquire() as holder, holder.transaction():
            await GoalLedger(holder).lock_keys([key])
            pid = await holder.fetchval("SELECT pg_backend_pid()")
            tasks = [
                asyncio.create_task(admit(runtime, source=s, attempt=a))
                for s, a in zip(sources, attempts, strict=True)
            ]

            # Observe both real SQL waiters, not merely scheduled Python tasks.
            async def both_admissions_wait_on_lock():
                return (
                    await owner.fetchval(
                        "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() "
                        "AND wait_event='advisory' AND $1=ANY(pg_blocking_pids(pid))",
                        pid,
                    )
                    >= 2
                )

            async with asyncio.timeout(5):
                await poll_until(both_admissions_wait_on_lock, interval_s=0.02)
        outcomes = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 10)
        if same_source:
            assert outcomes == [canonical, canonical]
        else:
            assert sum(isinstance(value, AdmissionUnavailable) for value in outcomes) == 1
            winner = next(value for value in outcomes if not isinstance(value, BaseException))
            binding = await owner.fetchrow(
                "SELECT source_key,decision_id FROM switchboard.admission_attempt_bindings "
                "WHERE attempt_id=$1",
                attempts[0],
            )
            assert binding["decision_id"] == winner.decision_id
            with pytest.raises(AdmissionUnavailable):
                await admit(
                    runtime, source=SourceRef("message", "third-source"), attempt=attempts[0]
                )
        assert await owner.fetchval("SELECT count(*) FROM switchboard.budgets") == (
            1 if same_source or canonical in outcomes else 2
        )
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 5)


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
    # The join's only delivery path is one goal_inbox row bound to the prompt.
    inbox = await owner.fetch("SELECT * FROM switchboard.goal_inbox")
    assert [
        (r["goal_id"], r["decision_id"], r["delivery_ref"], r["reply_effect_id"], r["consumed_at"])
        for r in inbox
    ] == [(original.goal_id, joined.decision_id, "delivery-1", effect_id, None)]
    assert await runtime.execute("DELETE FROM switchboard.goal_inbox") == "DELETE 0"
    with pytest.raises(asyncpg.PostgresError):
        await runtime.execute("UPDATE switchboard.goal_inbox SET delivery_ref='forged'")
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
    # New owner goals are not deliveries: only the one live join reached an inbox.
    assert await owner.fetchval("SELECT count(*) FROM switchboard.goal_inbox") == 1
    assert (
        await owner.fetchval(
            "SELECT delivery_ref FROM switchboard.admission_decisions WHERE id=$1",
            terminal.decision_id,
        )
        == "delivery-1"
    )
    # D11 has no settling -> running edge, so a settling goal cannot consume an
    # inbox row: a reply to its live prompt becomes a new owner goal instead.
    settling = await admit(runtime)
    await effect(
        owner,
        settling.goal_id,
        delivery="settling-1",
        entity=entity,
        channel="telegram",
        accepts=True,
        expiry=expiry,
    )
    await owner.execute(
        "UPDATE switchboard.goals SET status='settling' WHERE id=$1", settling.goal_id
    )
    after_settling = await admit(runtime, replace(facts, reply_delivery="settling-1"))
    assert after_settling.result == "admitted" and after_settling.goal_id != settling.goal_id
    new = await owner.fetchrow(
        "SELECT * FROM switchboard.goals WHERE id=$1", after_settling.goal_id
    )
    assert new["origin"] == "owner" and new["caused_by_goal"] is None
    assert (
        await owner.fetchval(
            "SELECT delivery_ref FROM switchboard.admission_decisions WHERE id=$1",
            after_settling.decision_id,
        )
        == "settling-1"
    )
    assert await owner.fetchval("SELECT count(*) FROM switchboard.goal_inbox") == 1
    # A forwarded owner reply that joins an unexposed live goal raises its
    # exposure and nothing else; later invocations must start exposed.
    quiet = await admit(runtime, Evidence("message", "owner", owner=True))
    await effect(
        owner,
        quiet.goal_id,
        delivery="forwarded-1",
        entity=entity,
        channel="telegram",
        accepts=True,
        expiry=expiry,
    )
    quiet_before = dict(
        await owner.fetchrow("SELECT * FROM switchboard.goals WHERE id=$1", quiet.goal_id)
    )
    quiet_budget = dict(
        await owner.fetchrow("SELECT * FROM switchboard.budgets WHERE goal_id=$1", quiet.goal_id)
    )
    assert quiet_before["exposed"] is False
    forwarded = await admit(runtime, replace(facts, reply_delivery="forwarded-1", forwarded=True))
    assert forwarded.result == "joined" and forwarded.goal_id == quiet.goal_id
    quiet_after = dict(
        await owner.fetchrow("SELECT * FROM switchboard.goals WHERE id=$1", quiet.goal_id)
    )
    assert quiet_after["exposed"] is True
    unchanged = ("origin", "ceiling", "caused_by_goal", "caused_by_effect", "admitted_exposed")
    assert {k: quiet_after[k] for k in unchanged} == {k: quiet_before[k] for k in unchanged}
    assert (
        dict(
            await owner.fetchrow(
                "SELECT * FROM switchboard.budgets WHERE goal_id=$1", quiet.goal_id
            )
        )
        == quiet_budget
    )
    with pytest.raises(asyncpg.PostgresError, match="exposure"):
        await owner.execute(
            "INSERT INTO switchboard.invocations (id,goal_id,depth,role,lead_epoch,status,grant_snapshot,grant_digest,max_turns,max_tokens) VALUES ($1,$2,0,'lead',1,'returned','[]','digest',1,1)",
            uuid4(),
            quiet.goal_id,
        )
    await accepted_reply_sequences(owner, runtime)


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

    async def committed_state():
        # Separate owner acquisitions see committed rows, not a runtime's
        # aborted transaction. The five legitimate refusals above stay bound.
        return {
            "goals": await owner.fetch("SELECT * FROM switchboard.goals ORDER BY id"),
            "budgets": await owner.fetch("SELECT * FROM switchboard.budgets ORDER BY goal_id"),
            "decisions": await owner.fetch(
                "SELECT * FROM switchboard.admission_decisions ORDER BY id"
            ),
            "bindings": await owner.fetch(
                "SELECT * FROM switchboard.admission_attempt_bindings ORDER BY attempt_id"
            ),
        }

    before_failures = await committed_state()
    assert len(before_failures["decisions"]) == len(before_failures["bindings"]) == 5

    async def assert_rolled_back(source, attempt):
        assert await committed_state() == before_failures
        assert (
            await owner.fetchrow(
                "SELECT * FROM switchboard.admission_attempt_bindings WHERE attempt_id=$1", attempt
            )
            is None
        )
        assert (
            await owner.fetchrow(
                "SELECT * FROM switchboard.admission_decisions WHERE attempt_id=$1", attempt
            )
            is None
        )
        assert (
            await owner.fetchrow("SELECT * FROM switchboard.goals WHERE source_key=$1", source.key)
            is None
        )
        assert await owner.fetchval("SELECT count(*) FROM switchboard.budgets") == 0

    class Broken(Sources):
        async def load(self, connection, source):
            await connection.execute("SELECT definitely_missing_goal_admission_function()")

    broken_source, broken_attempt = SourceRef("message", "broken"), uuid4()
    with pytest.raises(AdmissionUnavailable):
        await GoalAdmission(runtime, Broken(), Catalogs()).admit_submission(
            broken_source, broken_attempt
        )
    await assert_rolled_back(broken_source, broken_attempt)
    started = asyncio.Event()

    class Cancelled(Sources):
        async def load(self, connection, source):
            started.set()
            await asyncio.Event().wait()

    cancelled_source, cancelled_attempt = SourceRef("message", "cancelled"), uuid4()
    task = asyncio.create_task(
        GoalAdmission(runtime, Cancelled(), Catalogs()).admit_submission(
            cancelled_source, cancelled_attempt
        )
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await assert_rolled_back(cancelled_source, cancelled_attempt)
    assert await owner.fetchval("SELECT count(*) FROM switchboard.goals") == 0
    assert await owner.fetchval("SELECT count(*) FROM switchboard.budgets") == 0
    privileged_source, privileged_attempt = SourceRef("message", "privileged"), uuid4()
    with pytest.raises(AdmissionUnavailable):
        await admit(owner, source=privileged_source, attempt=privileged_attempt)
        # No privileged migration-login fallback.
    await assert_rolled_back(privileged_source, privileged_attempt)
    # A persistence failure after goal+budget INSERT must roll both back.
    await owner.execute("""
        CREATE FUNCTION switchboard.test_refuse_decision() RETURNS trigger LANGUAGE plpgsql AS
        $$ BEGIN RAISE EXCEPTION 'injected persistence failure'; END $$;
        CREATE TRIGGER test_refuse_decision BEFORE INSERT ON switchboard.admission_decisions
        FOR EACH ROW EXECUTE FUNCTION switchboard.test_refuse_decision()
    """)
    decision_source, decision_attempt = SourceRef("message", "decision-failure"), uuid4()
    try:
        with pytest.raises(AdmissionUnavailable):
            await admit(runtime, source=decision_source, attempt=decision_attempt)
        await assert_rolled_back(decision_source, decision_attempt)
        assert await owner.fetchval("SELECT count(*) FROM switchboard.goals") == 0
        assert await owner.fetchval("SELECT count(*) FROM switchboard.budgets") == 0
    finally:
        await owner.execute(
            "DROP TRIGGER test_refuse_decision ON switchboard.admission_decisions; DROP FUNCTION switchboard.test_refuse_decision()"
        )
    assert await committed_state() == before_failures
    # A failure at the new final binding INSERT must roll back the actual
    # earlier goal, budget and decision, not leave an apparently returned ID.
    await owner.execute("""
        CREATE FUNCTION switchboard.test_refuse_binding() RETURNS trigger LANGUAGE plpgsql AS
        $$ BEGIN RAISE EXCEPTION 'injected attempt binding failure'; END $$;
        CREATE TRIGGER test_refuse_binding BEFORE INSERT ON switchboard.admission_attempt_bindings
        FOR EACH ROW EXECUTE FUNCTION switchboard.test_refuse_binding()
    """)
    binding_source, binding_attempt = SourceRef("message", "binding-failure"), uuid4()
    try:
        with pytest.raises(AdmissionUnavailable):
            await admit(runtime, source=binding_source, attempt=binding_attempt)
        await assert_rolled_back(binding_source, binding_attempt)
        assert await owner.fetchval("SELECT count(*) FROM switchboard.goals") == 0
        assert await owner.fetchval("SELECT count(*) FROM switchboard.budgets") == 0
        assert (
            await owner.fetch("SELECT * FROM switchboard.admission_decisions ORDER BY id")
            == before_failures["decisions"]
        )
        assert (
            await owner.fetch(
                "SELECT * FROM switchboard.admission_attempt_bindings ORDER BY attempt_id"
            )
            == before_failures["bindings"]
        )
    finally:
        await owner.execute(
            "DROP TRIGGER test_refuse_binding ON switchboard.admission_attempt_bindings; "
            "DROP FUNCTION switchboard.test_refuse_binding()"
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
    # The failed acknowledgement committed its original attempt too. An alias
    # retry with another lost acknowledgement must not become retargetable.
    alias = uuid4()
    with pytest.raises(AdmissionUnavailable):
        await GoalAdmission(CommitUncertain(), Sources(), Catalogs()).admit_submission(
            uncertain_source, alias
        )
    with pytest.raises(AdmissionUnavailable):
        await admit(runtime, source=SourceRef("message", "retarget-alias"), attempt=alias)
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
        "INSERT INTO switchboard.invocations (id,goal_id,parent_invocation_id,depth,role,lead_epoch,status,grant_snapshot,grant_digest,exposed,max_turns,max_tokens) VALUES ($1,$2,$3,1,'child',0,'returned','[]','digest',true,1,1)",
        child,
        a.goal_id,
        lead,
    )
    await owner.execute("UPDATE switchboard.invocations SET status='running' WHERE id=$1", lead)
    async with owner.acquire() as conn:
        cases = [
            (
                "INSERT INTO switchboard.admission_attempt_bindings "
                "(attempt_id,source_key,decision_id) VALUES ($1,'wrong-source',$2)",
                uuid4(),
                a.decision_id,
            ),
            (
                "UPDATE switchboard.admission_attempt_bindings SET decision_id=$1 "
                "WHERE decision_id=$2",
                b.decision_id,
                a.decision_id,
            ),
            (
                "DELETE FROM switchboard.admission_attempt_bindings WHERE decision_id=$1",
                a.decision_id,
            ),
            (
                "INSERT INTO switchboard.invocations (id,goal_id,parent_invocation_id,depth,role,lead_epoch,status,grant_snapshot,grant_digest,exposed,max_turns,max_tokens) VALUES ($1,$2,$3,1,'child',0,'launching','[]','digest',true,1,1)",
                uuid4(),
                a.goal_id,
                child,
            ),
            (
                "INSERT INTO switchboard.invocations (id,goal_id,depth,role,lead_epoch,status,grant_snapshot,grant_digest,exposed,max_turns,max_tokens) VALUES ($1,$2,0,'lead',1,'launching','[]','digest',true,1,1)",
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
                "INSERT INTO switchboard.invocations (id,goal_id,parent_invocation_id,depth,role,lead_epoch,status,grant_snapshot,grant_digest,exposed,max_turns,max_tokens) VALUES ($1,$2,$3,1,'child',0,'launching','[]','digest',true,1,1)",
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
    # thread_id binds once; exposure is per invocation and only increases.
    thread = uuid4()
    await owner.execute("UPDATE switchboard.goals SET thread_id=$1 WHERE id=$2", thread, a.goal_id)
    owner_goal = await admit(runtime, Evidence("message", "owner", owner=True))
    clean_lead, _ = await effect(owner, owner_goal.goal_id)
    assert not await owner.fetchval(
        "SELECT exposed FROM switchboard.goals WHERE id=$1", owner_goal.goal_id
    )
    async with owner.acquire() as conn:
        for query, *values in [
            ("UPDATE switchboard.goals SET thread_id=$1 WHERE id=$2", uuid4(), a.goal_id),
            ("UPDATE switchboard.goals SET thread_id=NULL WHERE id=$1", a.goal_id),
            ("UPDATE switchboard.invocations SET exposed=false WHERE id=$1", lead),
            (
                "INSERT INTO switchboard.invocations (id,goal_id,depth,role,lead_epoch,status,grant_snapshot,grant_digest,max_turns,max_tokens) VALUES ($1,$2,0,'lead',1,'returned','[]','digest',1,1)",
                uuid4(),
                a.goal_id,
            ),
        ]:
            with pytest.raises(asyncpg.PostgresError):
                async with conn.transaction():
                    await conn.execute(query, *values)
    assert (
        await owner.fetchval("SELECT thread_id FROM switchboard.goals WHERE id=$1", a.goal_id)
        == thread
    )
    await runtime.execute("UPDATE switchboard.invocations SET exposed=true WHERE id=$1", clean_lead)
    assert await owner.fetchval(
        "SELECT exposed FROM switchboard.goals WHERE id=$1", owner_goal.goal_id
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


async def accepted_reply_sequences(owner, runtime):
    """Real provider references, accepted markers and observed PostgreSQL waits."""
    await configure(owner, limits(new_goals=100, per_source=100))
    async with verified_owner(owner) as entity:
        prompt_source = await accept(owner)
        prompt, _ = await admit_source(runtime, prompt_source, catalog=Catalogs())
        locator = "100:bot-prompt"
        delivery = scoped_reply_ref(
            transport="telegram",
            endpoint="telegram:bot:closed-test",
            conversation="telegram:100",
            recipient_entity=entity,
            recipient_channel="telegram_bot",
            locator=locator,
        )
        expiry = await owner.fetchval("SELECT now()+interval '1 hour'")
        _, effect_id = await effect(
            owner,
            prompt.goal_id,
            delivery=delivery,
            entity=entity,
            channel="telegram_bot",
            accepts=True,
            expiry=expiry,
        )
        before = dict(
            await owner.fetchrow("SELECT * FROM switchboard.goals WHERE id=$1", prompt.goal_id)
        )
        budget = dict(
            await owner.fetchrow(
                "SELECT * FROM switchboard.budgets WHERE goal_id=$1", prompt.goal_id
            )
        )
        source = await accept(
            owner, reference=locator, markers=[{"kind": "forwarded", "start": 0, "end": 5}]
        )
        service = GoalAdmission(runtime, ObservedSource(), Catalogs())
        a, b = await asyncio.gather(
            service.admit_submission(source, uuid4()), service.admit_submission(source, uuid4())
        )
        assert a == b and a.result == "joined" and a.goal_id == prompt.goal_id
        after = dict(
            await owner.fetchrow("SELECT * FROM switchboard.goals WHERE id=$1", prompt.goal_id)
        )
        assert {k: v for k, v in after.items() if k != "updated_at"} == {
            k: v for k, v in {**before, "exposed": True}.items() if k != "updated_at"
        }
        assert (
            dict(
                await owner.fetchrow(
                    "SELECT * FROM switchboard.budgets WHERE goal_id=$1", prompt.goal_id
                )
            )
            == budget
        )
        inbox = await owner.fetch(
            "SELECT * FROM switchboard.goal_inbox WHERE goal_id=$1", prompt.goal_id
        )
        assert len(inbox) == 1 and inbox[0]["reply_effect_id"] == effect_id
        # A reply to actual bot output inherits its producing goal's exposure,
        # while a clean bot output itself is not automatically a quote.
        plain = await accept(owner, reference=locator)
        result, reader = await admit_source(runtime, plain, catalog=Catalogs())
        assert result.result == "joined" and reader.inputs[0].outside_content
        clean, _ = await admit_source(runtime, await accept(owner), catalog=Catalogs())
        clean_ref = scoped_reply_ref(
            transport="telegram",
            endpoint="telegram:bot:closed-test",
            conversation="telegram:100",
            recipient_entity=entity,
            recipient_channel="telegram_bot",
            locator="100:clean",
        )
        await effect(
            owner,
            clean.goal_id,
            delivery=clean_ref,
            entity=entity,
            channel="telegram_bot",
            accepts=True,
            expiry=expiry,
        )
        result, reader = await admit_source(
            runtime, await accept(owner, reference="100:clean"), catalog=Catalogs()
        )
        assert result.result == "joined" and not reader.inputs[0].outside_content
        # Only exact transport/endpoint/conversation provenance can resolve.
        for overrides in (
            {"reference": "unknown"},
            {"reference": str(effect_id)},
            {"reference": locator, "endpoint": "telegram:other"},
            {"reference": locator, "conversation": "telegram:other"},
        ):
            result, reader = await admit_source(
                runtime, await accept(owner, **overrides), catalog=Catalogs()
            )
            assert result.result == "admitted" and result.goal_id != prompt.goal_id
            assert reader.inputs[0].reply_delivery is None
        # Stored inbound content with no trusted stamp never upgrades to clean.
        await accept(owner, sender="111", event_id="third-party")
        result, reader = await admit_source(
            runtime, await accept(owner, reference="100:third-party"), catalog=Catalogs()
        )
        assert result.result == "admitted" and reader.inputs[0].outside_content
        # Observe the actual goal-lock waiter. Stop and expiry are evaluated
        # after that wait, without taking the effect lock before the goal.
        for transition in ("stop", "expire"):
            target, _ = await admit_source(runtime, await accept(owner), catalog=Catalogs())
            ref = scoped_reply_ref(
                transport="telegram",
                endpoint="telegram:bot:closed-test",
                conversation="telegram:100",
                recipient_entity=entity,
                recipient_channel="telegram_bot",
                locator="100:" + transition,
            )
            _, eid = await effect(
                owner,
                target.goal_id,
                delivery=ref,
                entity=entity,
                channel="telegram_bot",
                accepts=True,
                expiry=expiry,
            )
            accepted = await accept(owner, reference="100:" + transition)
            task = None
            try:
                async with owner.acquire() as holder, holder.transaction():
                    await holder.execute(
                        "SELECT id FROM switchboard.goals WHERE id=$1 FOR UPDATE", target.goal_id
                    )
                    pid = await holder.fetchval("SELECT pg_backend_pid()")
                    task = asyncio.create_task(admit_source(runtime, accepted, catalog=Catalogs()))

                    async def waiting(pid=pid):
                        return await owner.fetchval(
                            "SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE $1=ANY(pg_blocking_pids(pid)))",
                            pid,
                        )

                    async with asyncio.timeout(5):
                        await poll_until(waiting, interval_s=0.02)
                    if transition == "stop":
                        await holder.execute(
                            "UPDATE switchboard.goals SET status='stopped',exposed=true WHERE id=$1",
                            target.goal_id,
                        )
                    else:
                        await holder.execute(
                            "UPDATE switchboard.effects SET prompt_expires_at=clock_timestamp()-interval '1 second' WHERE id=$1",
                            eid,
                        )
                result, _ = await asyncio.wait_for(task, 5)
                assert result.result == "admitted" and result.goal_id != target.goal_id
                row = await owner.fetchrow(
                    "SELECT * FROM switchboard.goals WHERE id=$1", result.goal_id
                )
                assert row["origin"] == "owner" and row["caused_by_goal"] is None
                assert row["exposed"] == (transition == "stop")
                assert (
                    await owner.fetchval(
                        "SELECT delivery_ref FROM switchboard.admission_decisions WHERE id=$1",
                        result.decision_id,
                    )
                    == ref
                )
                assert not await owner.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM switchboard.goal_inbox WHERE goal_id=$1)",
                    target.goal_id,
                )
            finally:
                if task and not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
