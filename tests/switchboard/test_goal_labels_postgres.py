"""RFC 0038 S1 labels on the real migrated goal ledger (sw_057, bu-dhloi1.6).

REQ-capability-authorization-019: an invocation's label starts from its actual
caller/predecessor chain, only grows, and fails closed when missing.
"""

import asyncio
from dataclasses import replace
from uuid import uuid4

import asyncpg
import pytest

from butlers.core.goal_admission import Evidence, Obligation, SourceRef
from butlers.core.goal_labels import CATEGORIES
from tests.switchboard.goal_ledger_fixtures import admit  # noqa: F401

pytest_plugins = ("tests.switchboard.goal_ledger_fixtures",)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

OWNER = Evidence("message", "owner", owner=True)


async def invocation(owner, goal_id, *, parent=None, epoch=0, status="running"):
    invocation_id = uuid4()
    await owner.execute(
        "INSERT INTO switchboard.invocations (id,goal_id,parent_invocation_id,depth,role,"
        "lead_epoch,status,grant_snapshot,grant_digest,max_turns,max_tokens) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,'[]','digest',5,500)",
        invocation_id,
        goal_id,
        parent,
        0 if parent is None else 1,
        "lead" if parent is None else "child",
        epoch,
        status,
    )
    return invocation_id


async def join(connection, invocation_id, principal="world", categories=(), cause="read"):
    """A read joining its output label, as the broker does under the goal lock."""
    await connection.execute(
        "UPDATE switchboard.invocations SET "
        "label_instr = switchboard.goal_label_union(label_instr, ARRAY[$2]::text[]), "
        "label_data = switchboard.goal_label_union(label_data, ARRAY[$2]::text[]), "
        "label_sens = switchboard.goal_label_union(label_sens, $3::text[]), "
        "exposure_cause = COALESCE(exposure_cause, CASE WHEN $2 NOT IN "
        "('owner','owner_device') THEN $4 END) WHERE id = $1",
        invocation_id,
        principal,
        list(categories),
        cause,
    )


async def labels(owner, table, **where):
    ((column, value),) = where.items()
    return await owner.fetchrow(f"SELECT * FROM switchboard.{table} WHERE {column} = $1", value)


async def test_a_clean_sibling_is_not_contaminated_by_the_goal_union(ledger):
    owner, runtime = ledger
    goal = (await admit(runtime, OWNER)).goal_id
    lead = await invocation(owner, goal)
    reader, sibling = (
        await invocation(owner, goal, parent=lead),
        await invocation(owner, goal, parent=lead),
    )
    await join(owner, reader, "external:mail-1", ["health"])

    assert (await labels(owner, "invocations", id=reader))["exposed"] is True
    assert (await labels(owner, "goals", id=goal))["exposed"] is True  # reporting union
    clean = await labels(owner, "invocations", id=sibling)
    assert (clean["exposed"], clean["label_data"], clean["label_sens"]) == (False, ["owner"], [])
    # A sibling started after the exposure starts from its caller, not the union.
    late = await labels(owner, "invocations", id=await invocation(owner, goal, parent=lead))
    assert (late["exposed"], late["exposure_cause"]) == (False, None)
    assert (await labels(owner, "invocations", id=lead))["exposed"] is False


async def test_a_child_inherits_its_caller(ledger):
    owner, runtime = ledger
    goal = (await admit(runtime, OWNER)).goal_id
    lead = await invocation(owner, goal)
    await join(owner, lead, "world", ["finance"])
    child = await labels(owner, "invocations", id=await invocation(owner, goal, parent=lead))
    assert child["exposed"] is True and child["exposure_cause"] == "child"
    assert child["label_data"] == ["owner", "world"] and child["label_sens"] == ["finance"]


async def test_a_replacement_lead_inherits_its_predecessors_and_recorded_effects(ledger):
    owner, runtime = ledger
    goal = (await admit(runtime, OWNER)).goal_id
    first = await invocation(owner, goal, status="returned")
    child = await invocation(owner, goal, parent=first)
    await join(owner, child, "external:mail-2", ["location"])
    await owner.execute(
        "INSERT INTO switchboard.effects (id,goal_id,invocation_id,capability,args_digest,status) "
        "VALUES ($1,$2,$3,'home__send','digest','parked')",
        uuid4(),
        goal,
        child,
    )
    second = await labels(
        owner, "invocations", id=await invocation(owner, goal, epoch=1, status="returned")
    )
    # The child's recorded effect is the replacement's recovered context.
    assert second["exposed"] is True and second["exposure_cause"] == "recovery"
    assert "external:mail-2" in second["label_data"] and second["label_sens"] == ["location"]

    await join(owner, second["id"], "world", ["routine"])
    third = await labels(owner, "invocations", id=await invocation(owner, goal, epoch=2))
    assert set(third["label_sens"]) == {"location", "routine"}


async def test_an_exposed_admission_starts_every_invocation_exposed(ledger):
    owner, runtime = ledger
    goal = (await admit(runtime, Evidence("message", "mail"))).goal_id  # third party
    lead = await labels(owner, "invocations", id=await invocation(owner, goal))
    assert lead["exposed"] is True and lead["exposure_cause"] == "ingress"
    assert set(lead["label_sens"]) == CATEGORIES


async def test_labels_only_grow_and_unknown_members_refuse(ledger):
    owner, runtime = ledger
    goal = (await admit(runtime, OWNER)).goal_id
    lead = await invocation(owner, goal)
    await join(owner, lead, "world", ["health"])
    effect_id = uuid4()
    await owner.execute(
        "INSERT INTO switchboard.effects (id,goal_id,invocation_id,capability,args_digest,status) "
        "VALUES ($1,$2,$3,'home__send','digest','parked')",
        effect_id,
        goal,
        lead,
    )
    async with owner.acquire() as connection:
        for query in (
            "UPDATE switchboard.invocations SET label_sens='{}' WHERE id=$1",
            "UPDATE switchboard.invocations SET label_data='{owner}' WHERE id=$1",
            "UPDATE switchboard.invocations SET exposure_cause='inbox' WHERE id=$1",
            "UPDATE switchboard.invocations SET exposed=false WHERE id=$1",
            "UPDATE switchboard.invocations SET label_version=2 WHERE id=$1",
            "UPDATE switchboard.invocations SET label_sens=label_sens||'{politics}' WHERE id=$1",
            "UPDATE switchboard.invocations SET label_data=label_data||'{admin}' WHERE id=$1",
            "UPDATE switchboard.invocations SET "
            "label_data=label_data||'{external:Mail@x}' WHERE id=$1",
        ):
            with pytest.raises(asyncpg.PostgresError):
                async with connection.transaction():
                    await connection.execute(query, lead)
        for query, value in (
            ("UPDATE switchboard.effects SET label_sens='{}' WHERE id=$1", effect_id),
            ("UPDATE switchboard.goals SET label_sens='{}' WHERE id=$1", goal),
            ("UPDATE switchboard.goals SET admitted_label_data='{world}' WHERE id=$1", goal),
        ):
            with pytest.raises(asyncpg.PostgresError):
                async with connection.transaction():
                    await connection.execute(query, value)
    row = await labels(owner, "invocations", id=lead)
    assert (row["label_sens"], row["exposure_cause"]) == (["health"], "read")


async def test_a_boolean_only_exposure_is_unknown_provenance(ledger):
    owner, runtime = ledger
    goal = (await admit(runtime, OWNER)).goal_id
    lead = await invocation(owner, goal)
    await runtime.execute("UPDATE switchboard.invocations SET exposed=true WHERE id=$1", lead)
    row = await labels(owner, "invocations", id=lead)
    assert "world" in row["label_instr"] and set(row["label_sens"]) == CATEGORIES
    assert row["exposure_cause"] == "legacy_unknown"
    other = (await admit(runtime, OWNER)).goal_id
    await runtime.execute("UPDATE switchboard.goals SET exposed=true WHERE id=$1", other)
    union = await labels(owner, "goals", id=other)
    assert "world" in union["label_data"] and set(union["label_sens"]) == CATEGORIES


async def test_an_effect_snapshots_its_requesting_label(ledger):
    owner, runtime = ledger
    goal = (await admit(runtime, OWNER)).goal_id
    lead = await invocation(owner, goal)
    await join(owner, lead, "owner", ["finance"])
    effect_id = uuid4()
    await runtime.execute(
        "INSERT INTO switchboard.effects (id,goal_id,invocation_id,capability,args_digest,status) "
        "VALUES ($1,$2,$3,'home__send','digest','requested')",
        effect_id,
        goal,
        lead,
    )
    await join(owner, lead, "world", ["health"])
    effect = await labels(owner, "effects", id=effect_id)
    assert (effect["label_data"], effect["label_sens"]) == (["owner"], ["finance"])
    # Settlement moves the status, never the requesting label.
    await runtime.execute("UPDATE switchboard.effects SET status='parked' WHERE id=$1", effect_id)
    assert (await labels(owner, "effects", id=effect_id))["label_sens"] == ["finance"]


async def recorded_effect(owner, goal_id, invocation_id, **binding):
    effect_id = uuid4()
    columns = ["id", "goal_id", "invocation_id", "capability", "args_digest", "status", *binding]
    values = [effect_id, goal_id, invocation_id, "messenger.notify", str(uuid4()), "committed"]
    values += list(binding.values())
    await owner.execute(
        f"INSERT INTO switchboard.effects ({','.join(columns)}) "
        f"VALUES ({','.join(f'${n}' for n in range(1, len(values) + 1))})",
        *values,
    )
    return effect_id


async def test_a_caused_admission_inherits_the_causing_effect_not_the_goal_union(ledger):
    owner, runtime = ledger
    cause = (await admit(runtime, OWNER)).goal_id
    lead = await invocation(owner, cause)
    reader = await invocation(owner, cause, parent=lead)
    await join(owner, reader, "external:mail-4", ["health"])
    assert (await labels(owner, "goals", id=cause))["exposed"] is True

    def caused(effect_id, tick):
        facts = Evidence(
            "occurrence",
            "schedule",
            obligation=Obligation("health", "subscription-v1", created_exposed=False),
            caused_by_goal=cause,
            caused_by_effect=effect_id,
        )
        return admit(runtime, facts, SourceRef("occurrence", "schedule-id", tick))

    clean = await caused(await recorded_effect(owner, cause, lead), "tick-clean")
    row = await labels(owner, "goals", id=clean.goal_id)
    assert (row["admitted_exposed"], row["admitted_label_data"]) == (False, ["owner"])

    tainted = await caused(await recorded_effect(owner, cause, reader), "tick-tainted")
    row = await labels(owner, "goals", id=tainted.goal_id)
    assert row["admitted_exposed"] is True
    assert (
        "external:mail-4" in row["admitted_label_data"] and "health" in row["admitted_label_sens"]
    )


async def test_a_joined_reply_exposes_only_the_invocation_that_reads_it(ledger):
    owner, runtime = ledger
    goal = (await admit(runtime, OWNER)).goal_id
    lead = await invocation(owner, goal)
    entity = uuid4()
    expiry = await owner.fetchval("SELECT now() + interval '1 hour'")
    delivery = f"labels-{uuid4()}"
    await recorded_effect(
        owner,
        goal,
        lead,
        delivery_ref=delivery,
        recipient_entity=entity,
        recipient_channel="telegram",
        prompt_accepts_input=True,
        prompt_expires_at=expiry,
    )
    reply = Evidence(
        "message",
        "owner",
        owner=True,
        forwarded=True,
        reply_delivery=delivery,
        owner_entity=entity,
        owner_channel="telegram",
    )
    joined = await admit(runtime, reply)
    assert joined.result == "joined"
    row = await labels(owner, "goal_inbox", decision_id=joined.decision_id)
    assert {"owner", "world"} <= set(row["label_data"]) and set(row["label_sens"]) == CATEGORIES
    assert (await labels(owner, "goals", id=goal))["exposed"] is True  # reporting union
    assert (await labels(owner, "invocations", id=lead))["exposed"] is False

    # A plain owner reply to a clean prompt is owner data.
    delivery = f"labels-{uuid4()}"
    await recorded_effect(
        owner,
        goal,
        lead,
        delivery_ref=delivery,
        recipient_entity=entity,
        recipient_channel="telegram",
        prompt_accepts_input=True,
        prompt_expires_at=expiry,
    )
    plain = await admit(runtime, replace(reply, forwarded=False, reply_delivery=delivery))
    row = await labels(owner, "goal_inbox", decision_id=plain.decision_id)
    assert (row["label_instr"], row["label_data"], row["label_sens"]) == (["owner"], ["owner"], [])


async def test_concurrent_joins_keep_every_principal_and_category(ledger):
    owner, runtime = ledger
    goal = (await admit(runtime, OWNER)).goal_id
    lead = await invocation(owner, goal)
    async with runtime.acquire() as first, runtime.acquire() as second:
        held = first.transaction()
        await held.start()
        await join(first, lead, "external:mail-3", ["health"])
        racing = asyncio.create_task(join(second, lead, "domain:finance", ["finance"]))
        await asyncio.sleep(0.2)
        assert not racing.done()  # blocked on the row lock, then re-reads the row
        await held.commit()
        await racing
    row = await labels(owner, "invocations", id=lead)
    assert {"external:mail-3", "domain:finance"} <= set(row["label_data"])
    assert set(row["label_sens"]) == {"health", "finance"}
    union = await labels(owner, "goals", id=goal)
    assert set(union["label_sens"]) >= {"health", "finance"}


async def test_invocation_and_effect_history_cannot_be_deleted(ledger):
    """A replacement lead's recovery context cannot be erased first (sw_057)."""
    owner, runtime = ledger
    goal = (await admit(runtime, OWNER)).goal_id
    first = await invocation(owner, goal, status="returned")
    await join(owner, first, "external:mail-5", ["health"])
    for pool in (runtime, owner):
        for statement in (
            "DELETE FROM switchboard.invocations",
            "DELETE FROM switchboard.effects",
            "TRUNCATE switchboard.effects CASCADE",
            "TRUNCATE switchboard.invocations CASCADE",
        ):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await pool.execute(statement)
    replacement = await labels(owner, "invocations", id=await invocation(owner, goal, epoch=1))
    assert (replacement["exposed"], replacement["exposure_cause"]) == (True, "recovery")
    assert "external:mail-5" in replacement["label_data"]


async def test_a_later_before_trigger_cannot_launder_a_label(ledger):
    """The runtime role holds TRIGGER (core_001); an AFTER check sees the final row."""
    owner, runtime = ledger
    goal = (await admit(runtime, OWNER)).goal_id
    lead = await invocation(owner, goal)
    await join(owner, lead, "external:mail-6", ["finance"])
    await owner.execute("GRANT TRIGGER ON switchboard.invocations TO butler_switchboard_rw")
    await owner.execute("""
        CREATE OR REPLACE FUNCTION switchboard.test_launder_label()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            NEW.label_instr := '{owner}'; NEW.label_data := '{owner}';
            NEW.label_sens := '{}'; NEW.exposed := false; NEW.exposure_cause := NULL;
            RETURN NEW;
        END $$
    """)
    try:
        # Sorts after every sw_047/sw_057 BEFORE trigger on the table.
        await runtime.execute(
            "CREATE TRIGGER zz_launder BEFORE INSERT OR UPDATE ON switchboard.invocations "
            "FOR EACH ROW EXECUTE FUNCTION switchboard.test_launder_label()"
        )
        with pytest.raises(asyncpg.RaiseError, match="immutable"):
            await runtime.execute(
                "UPDATE switchboard.invocations SET status = 'returned' WHERE id = $1", lead
            )
        with pytest.raises(asyncpg.InsufficientPrivilegeError, match="cover"):
            await invocation(owner, goal, parent=lead)  # a child of an exposed caller
    finally:
        await owner.execute("DROP TRIGGER IF EXISTS zz_launder ON switchboard.invocations")
        await owner.execute("DROP FUNCTION IF EXISTS switchboard.test_launder_label()")
        await owner.execute("REVOKE TRIGGER ON switchboard.invocations FROM butler_switchboard_rw")
    row = await labels(owner, "invocations", id=lead)
    assert row["exposed"] is True and "external:mail-6" in row["label_data"]
