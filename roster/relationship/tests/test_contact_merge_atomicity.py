"""A contact merge and its entity merge commit together, or not at all (bu-dhloi1.40).

``contact_merge`` used to run the entity merge after its own commit and swallow any
failure, which left facts moved with no ``merged_into`` on the source: nothing could
then tell that the source's identity had moved, and an owner term on it stopped
following its facts. These tests run on a real migrated database.
"""

from __future__ import annotations

import asyncio
import contextlib
import shutil
import uuid
from typing import Any

import asyncpg
import pytest

from butlers.roster_imports import roster_module

contacts_mod = roster_module("relationship", "tools/contacts")
entity_merge_mod = roster_module("relationship", "tools/entity_merge")
identity_slots_mod = roster_module("relationship", "tools/identity_slots")
SourceEntityTombstonedError = entity_merge_mod.SourceEntityTombstonedError
TargetEntityTombstonedError = entity_merge_mod.TargetEntityTombstonedError

# Contact columns a merge deliberately does not re-point.
_NOT_REPOINTED = {
    ("contact_entity_map", "contact_id"),  # the source's bridge row is deleted instead
    ("priority_contacts", "contact_id"),  # not moved: flagged in bu-dhloi1.123's PR
}

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]


@pytest.fixture
async def rel_pool(db):
    return await db("core", "relationship", min_size=2, max_size=8)


async def _entity(pool: asyncpg.Pool, name: str) -> uuid.UUID:
    return await pool.fetchval(
        "INSERT INTO public.entities (canonical_name, entity_type) VALUES ($1, 'person') "
        "RETURNING id",
        f"{name} {uuid.uuid4()}",
    )


async def _contact(pool: asyncpg.Pool, entity: uuid.UUID) -> uuid.UUID:
    contact = uuid.uuid4()
    await pool.execute(
        "INSERT INTO contact_entity_map (contact_id, entity_id) VALUES ($1, $2)", contact, entity
    )
    return contact


async def _fact(pool: asyncpg.Pool, subject: uuid.UUID, value: str) -> uuid.UUID:
    return await pool.fetchval(
        "INSERT INTO relationship.entity_facts (subject, predicate, object, object_kind, src) "
        "VALUES ($1, 'has-email', $2, 'literal', 'test') RETURNING id",
        subject,
        value,
    )


async def _pair(pool: asyncpg.Pool):
    source_entity, target_entity = await _entity(pool, "Source"), await _entity(pool, "Target")
    await pool.execute(
        "UPDATE public.entities SET metadata = metadata || $2::jsonb WHERE id = $1",
        source_entity,
        {"profile": {"nickname": "from-source"}},
    )
    fact = await _fact(pool, source_entity, f"{uuid.uuid4().hex}@source.test")
    source, target = await _contact(pool, source_entity), await _contact(pool, target_entity)
    return source_entity, target_entity, fact, source, target


async def _assert_nothing_moved(pool, source_entity, target_entity, fact, source) -> None:
    assert (
        await pool.fetchval("SELECT subject FROM relationship.entity_facts WHERE id = $1", fact)
        == source_entity
    )
    assert (
        await pool.fetchval(
            "SELECT entity_id FROM contact_entity_map WHERE contact_id = $1", source
        )
        == source_entity
    )
    source_metadata = await pool.fetchval(
        "SELECT metadata FROM public.entities WHERE id = $1", source_entity
    )
    assert "merged_into" not in source_metadata
    target_metadata = await pool.fetchval(
        "SELECT metadata FROM public.entities WHERE id = $1", target_entity
    )
    assert "profile" not in target_metadata  # the profile reconcile rolled back too
    assert not await pool.fetchval(
        "SELECT count(*) FROM public.entity_rebind_log WHERE source_entity_id = $1", source_entity
    )


async def test_a_failed_entity_merge_rolls_the_whole_contact_merge_back(rel_pool, monkeypatch):
    source_entity, target_entity, fact, source, target = await _pair(rel_pool)

    async def boom(*_args, **_kwargs):
        raise RuntimeError("entity merge failed")

    # Late inside the merge's own transaction: after it tombstoned and moved rows.
    monkeypatch.setattr(entity_merge_mod, "write_merge_review", boom)
    with pytest.raises(RuntimeError, match="entity merge failed"):
        await contacts_mod.contact_merge(rel_pool, source, target)
    await _assert_nothing_moved(rel_pool, source_entity, target_entity, fact, source)


@pytest.mark.parametrize(
    ("tombstoned", "error"),
    [("target", TargetEntityTombstonedError), ("source", SourceEntityTombstonedError)],
)
@pytest.mark.parametrize("marker", ["merged_into", "tombstone", "deleted_at"])
async def test_a_refused_entity_merge_refuses_the_contact_merge(
    rel_pool, tombstoned, error, marker
):
    """The merge authority's own refusal, with no fault injected."""
    source_entity, target_entity, fact, source, target = await _pair(rel_pool)
    other = await _entity(rel_pool, "Elsewhere")
    await rel_pool.execute(
        "UPDATE public.entities SET metadata = metadata || $2::jsonb WHERE id = $1",
        source_entity if tombstoned == "source" else target_entity,
        {
            "merged_into": {"merged_into": str(other)},
            "tombstone": {"tombstone": True},
            "deleted_at": {"deleted_at": "2026-11-06T00:00:00Z"},
        }[marker],
    )
    with pytest.raises(ValueError, match="its entity has been removed") as refused:
        await contacts_mod.contact_merge(rel_pool, source, target)
    assert isinstance(refused.value.__cause__, error)
    assert str(source if tombstoned == "source" else target) in str(refused.value)
    assert (
        await rel_pool.fetchval("SELECT subject FROM relationship.entity_facts WHERE id = $1", fact)
        == source_entity
    )
    assert (
        await rel_pool.fetchval(
            "SELECT entity_id FROM contact_entity_map WHERE contact_id = $1", source
        )
        == source_entity
    )


async def test_a_committed_contact_merge_has_tombstoned_its_source(rel_pool):
    source_entity, target_entity, fact, source, target = await _pair(rel_pool)
    result = await contacts_mod.contact_merge(rel_pool, source, target)
    assert result["id"] == target
    assert await rel_pool.fetchval(
        "SELECT metadata->>'merged_into' FROM public.entities WHERE id = $1", source_entity
    ) == str(target_entity)
    assert (
        await rel_pool.fetchval("SELECT subject FROM relationship.entity_facts WHERE id = $1", fact)
        == target_entity
    )
    assert not await rel_pool.fetchval(
        "SELECT count(*) FROM contact_entity_map WHERE contact_id = $1", source
    )
    # Each merge-bearing schema has its recovery receipt from the same transaction.
    assert await rel_pool.fetchval(
        "SELECT count(*) FROM public.entity_rebind_log WHERE source_entity_id = $1", source_entity
    )


async def test_a_contact_merge_carries_projected_graph_edges_with_its_facts(rel_pool):
    """Edges follow their moved facts, and a superseded fact loses its edge (bu-dhloi1.51)."""
    source_entity, target_entity, _fact_id, source, target = await _pair(rel_pool)
    other, third = await _entity(rel_pool, "Other"), await _entity(rel_pool, "Third")

    async def knows(subject: uuid.UUID, obj: uuid.UUID) -> uuid.UUID:
        fact = await rel_pool.fetchval(
            "INSERT INTO relationship.entity_facts (subject, predicate, object, object_kind, src) "
            "VALUES ($1, 'knows', $2, 'entity', 'test') RETURNING id",
            subject,
            str(obj),
        )
        await rel_pool.execute(
            "INSERT INTO public.entity_graph_edges (source_schema, source_table, source_id, "
            "subject_entity_id, predicate, object_entity_id) "
            "VALUES ('relationship', 'entity_facts', $1, $2, 'knows', $3)",
            fact,
            subject,
            obj,
        )
        return fact

    moved_subject = await knows(source_entity, other)
    moved_object = await knows(other, source_entity)
    loser = await knows(source_entity, third)
    winner = await knows(target_entity, third)

    await contacts_mod.contact_merge(rel_pool, source, target)

    edges = {
        row["source_id"]: (row["subject_entity_id"], row["object_entity_id"])
        for row in await rel_pool.fetch(
            "SELECT source_id, subject_entity_id, object_entity_id FROM public.entity_graph_edges"
        )
    }
    assert edges == {
        moved_subject: (target_entity, other),
        moved_object: (other, target_entity),
        winner: (target_entity, third),
    }
    assert (
        await rel_pool.fetchval(
            "SELECT validity FROM relationship.entity_facts WHERE id = $1", loser
        )
        == "superseded"
    )


async def test_a_settling_failure_after_commit_keeps_the_merge(rel_pool, monkeypatch, caplog):
    """After commit the identity is durable; the rebind ledger recovers the rest."""
    source_entity, target_entity, fact, source, target = await _pair(rel_pool)

    async def boom(*_args, **_kwargs):
        raise RuntimeError("fleet event failed")

    monkeypatch.setattr(entity_merge_mod, "publish_fleet_event", boom)
    result = await contacts_mod.contact_merge(rel_pool, source, target)
    assert result["id"] == target
    assert await rel_pool.fetchval(
        "SELECT metadata->>'merged_into' FROM public.entities WHERE id = $1", source_entity
    ) == str(target_entity)
    assert (
        await rel_pool.fetchval("SELECT subject FROM relationship.entity_facts WHERE id = $1", fact)
        == target_entity
    )
    assert await rel_pool.fetchval(
        "SELECT count(*) FROM public.entity_rebind_log WHERE source_entity_id = $1", source_entity
    )
    assert "settling" in caplog.text


async def test_a_contact_merge_and_an_entity_merge_cannot_deadlock(rel_pool, monkeypatch):
    """Two merges sharing a fact and a slot finish, each with the right result.

    Both run the authority's one lock order, so no reordering inside the authority can
    invert them. ``test_a_contact_merge_takes_no_lock_before_the_authority`` pins what
    keeps it that way. Here ``contact_merge(A->B)`` holds its facts while
    ``merge_entity_pair(C->D)`` goes for an identity slot with a candidate on both A
    and C, with a fact on C naming A. The entity merge queues, so the contact merge's
    hook stops waiting and lets it through.
    """
    a, b = await _entity(rel_pool, "A"), await _entity(rel_pool, "B")
    c, d = await _entity(rel_pool, "C"), await _entity(rel_pool, "D")
    source, target = await _contact(rel_pool, a), await _contact(rel_pool, b)
    linking_fact = await rel_pool.fetchval(
        "INSERT INTO relationship.entity_facts (subject, predicate, object, object_kind, src) "
        "VALUES ($1, 'knows', $2, 'entity', 'test') RETURNING id",
        c,
        str(a),
    )
    shared_address = f"{uuid.uuid4().hex}@slot.test"
    for subject in (a, c):
        await rel_pool.execute(
            "INSERT INTO relationship.entity_facts "
            "(subject, predicate, object, object_kind, src, validity) "
            "VALUES ($1, 'has-email', $2, 'literal', 'test', 'candidate')",
            subject,
            shared_address,
        )

    contact_merge_locked, entity_merge_locked = asyncio.Event(), asyncio.Event()
    real_plan_facts = entity_merge_mod._lock_and_plan_facts
    real_candidates = entity_merge_mod._lock_candidate_reports

    async def plan_facts_then_wait(conn, merged, survivor):
        await real_plan_facts(conn, merged, survivor)
        if merged == a:
            contact_merge_locked.set()
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(2):
                    await entity_merge_locked.wait()

    async def wait_then_candidates(conn, merged, survivor):
        if merged == c:
            await contact_merge_locked.wait()
        await real_candidates(conn, merged, survivor)
        if merged == c:
            entity_merge_locked.set()

    monkeypatch.setattr(entity_merge_mod, "_lock_and_plan_facts", plan_facts_then_wait)
    monkeypatch.setattr(entity_merge_mod, "_lock_candidate_reports", wait_then_candidates)

    results = await asyncio.gather(
        contacts_mod.contact_merge(rel_pool, source, target),
        entity_merge_mod.merge_entity_pair(rel_pool, source_entity_id=c, target_entity_id=d),
        return_exceptions=True,
    )

    assert [r for r in results if isinstance(r, BaseException)] == []
    survivors = await rel_pool.fetch(
        "SELECT id, metadata ->> 'merged_into' AS merged_into FROM public.entities "
        "WHERE id = ANY($1::uuid[])",
        [a, c],
    )
    assert {row["id"]: row["merged_into"] for row in survivors} == {a: str(b), c: str(d)}
    moved = await rel_pool.fetchrow(
        "SELECT subject, object FROM relationship.entity_facts WHERE id = $1", linking_fact
    )
    assert (moved["subject"], moved["object"]) == (d, str(b))


async def test_a_failed_local_rebind_is_named_in_the_contact_merge_log(
    rel_pool, monkeypatch, caplog
):
    """The merge stands, and the schema left failed is not discarded silently (bu-dhloi1.52)."""
    source_entity, target_entity, _fact_id, source, target = await _pair(rel_pool)

    async def boom(*_args, **_kwargs):
        raise ValueError("local rebind failed")

    monkeypatch.setattr("butlers.entity_rebind._repoint_facts", boom)
    result = await contacts_mod.contact_merge(rel_pool, source, target)

    assert result["id"] == target
    assert (
        await rel_pool.fetchval(
            "SELECT status FROM public.entity_rebind_log "
            "WHERE source_entity_id = $1 AND target_schema = 'relationship'",
            source_entity,
        )
        == "failed"
    )
    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any("rebind failed" in m and "relationship" in m for m in warnings), warnings
    assert any(str(target_entity) in m for m in warnings), warnings


async def _held_before_authority(conn) -> dict[str, Any]:
    """What this transaction holds when the authority starts."""
    return {
        "xid": await conn.fetchval("SELECT pg_current_xact_id_if_assigned()::text"),
        # pg_advisory_xact_lock assigns no transaction id, so the id alone cannot see a slot
        # lock; pg_locks is the only place one shows.
        "advisory": await conn.fetchval(
            "SELECT count(*) FROM pg_catalog.pg_locks "
            "WHERE locktype = 'advisory' AND pid = pg_backend_pid() AND granted"
        ),
    }


async def test_the_probe_sees_an_advisory_lock_taken_before_the_authority(rel_pool):
    """Positive control: the probe is not blind to the lock it guards against."""
    async with rel_pool.acquire() as conn, conn.transaction():
        assert (await _held_before_authority(conn))["advisory"] == 0
        await identity_slots_mod.lock_identity_slot(conn, "has-email", "probe@control.test")
        held = await _held_before_authority(conn)
    assert held["xid"] is None  # an advisory lock alone assigns no id: why the id is not enough
    assert held["advisory"] >= 1


async def test_a_contact_merge_takes_no_lock_before_the_authority(rel_pool, monkeypatch):
    """Its transaction holds no row lock, write or advisory lock when the authority starts.

    Any of those would be a lock outside the authority's one order, which is how the
    deadlock bu-dhloi1.50 fixed could return (bu-dhloi1.51). A row lock or write assigns the
    transaction an id; an identity-slot lock assigns none, so it is read from ``pg_locks``.
    """
    _source_entity, _target_entity, _fact_id, source, target = await _pair(rel_pool)
    real_authority = entity_merge_mod.merge_entity_pair_in_transaction
    held = []

    async def authority(conn, **kwargs):
        held.append(await _held_before_authority(conn))
        return await real_authority(conn, **kwargs)

    monkeypatch.setattr(entity_merge_mod, "merge_entity_pair_in_transaction", authority)

    await contacts_mod.contact_merge(rel_pool, source, target)

    assert held == [{"xid": None, "advisory": 0}]


@pytest.mark.parametrize("reordered", [False, True], ids=["authority_order", "reorder_mutant"])
async def test_the_authority_locks_slots_before_facts_against_a_slot_first_writer(
    rel_pool, monkeypatch, reordered
):
    """A writer that takes an identity slot, then a fact row, queues behind the merge.

    The central writer's order is slot, then row. The merge takes the slots of its
    candidate reports before it locks any fact, so a writer holding a slot and wanting a
    fact never waits on a merge that holds that fact. The mutant locks the facts first;
    there the writer and the merge each hold what the other wants, and PostgreSQL aborts
    one of them as a deadlock. Both runs are real: the mutant is the proof this guard fails
    on a reorder.
    """
    source_entity, target_entity, fact, _source, _target = await _pair(rel_pool)
    address = f"{uuid.uuid4().hex}@slot-first.test"
    await rel_pool.execute(
        "INSERT INTO relationship.entity_facts "
        "(subject, predicate, object, object_kind, src, validity) "
        "VALUES ($1, 'has-email', $2, 'literal', 'test', 'candidate')",
        source_entity,
        address,
    )
    if reordered:
        real_plan_facts = entity_merge_mod._lock_and_plan_facts
        real_candidates = entity_merge_mod._lock_candidate_reports

        async def no_candidates_yet(conn, source, target):
            return None

        async def facts_then_candidates(conn, source, target):
            await real_plan_facts(conn, source, target)
            await real_candidates(conn, source, target)

        monkeypatch.setattr(entity_merge_mod, "_lock_candidate_reports", no_candidates_yet)
        monkeypatch.setattr(entity_merge_mod, "_lock_and_plan_facts", facts_then_candidates)

    slot_held = asyncio.Event()

    async def writer() -> None:
        async with rel_pool.acquire() as conn, conn.transaction():
            await identity_slots_mod.lock_identity_slot(conn, "has-email", address, source_entity)
            writer_pid = await conn.fetchval("SELECT pg_backend_pid()")
            slot_held.set()
            # Go for the fact only once the merge is queued on something this writer holds.
            for _ in range(500):
                if await rel_pool.fetchval(
                    "SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_stat_activity "
                    "WHERE datname = current_database() AND pid <> $1 "
                    "AND wait_event_type = 'Lock')",
                    writer_pid,
                ):
                    break
                await asyncio.sleep(0.02)
            else:
                raise AssertionError("the merge never queued behind the writer's slot")
            await conn.fetchval(
                "SELECT id FROM relationship.entity_facts WHERE id = $1 FOR UPDATE", fact
            )

    async def merge() -> object:
        await slot_held.wait()
        return await entity_merge_mod.merge_entity_pair(
            rel_pool, source_entity_id=source_entity, target_entity_id=target_entity
        )

    results = await asyncio.wait_for(asyncio.gather(writer(), merge(), return_exceptions=True), 30)

    failures = [r for r in results if isinstance(r, BaseException)]
    if reordered:
        assert any(isinstance(r, asyncpg.exceptions.DeadlockDetectedError) for r in failures), (
            results
        )
    else:
        assert failures == []
        assert await rel_pool.fetchval(
            "SELECT metadata ->> 'merged_into' FROM public.entities WHERE id = $1", source_entity
        ) == str(target_entity)


async def test_two_contacts_of_one_entity_merge_without_touching_its_facts(rel_pool):
    entity = await _entity(rel_pool, "Same person")
    fact = await _fact(rel_pool, entity, f"{uuid.uuid4().hex}@same.test")
    source, target = await _contact(rel_pool, entity), await _contact(rel_pool, entity)

    result = await contacts_mod.contact_merge(rel_pool, source, target)

    assert result["id"] == target
    row = await rel_pool.fetchrow(
        "SELECT subject, validity FROM relationship.entity_facts WHERE id = $1", fact
    )
    assert (row["subject"], row["validity"]) == (entity, "active")
    metadata = await rel_pool.fetchval("SELECT metadata FROM public.entities WHERE id = $1", entity)
    assert "merged_into" not in metadata
    assert not await rel_pool.fetchval(
        "SELECT count(*) FROM contact_entity_map WHERE contact_id = $1", source
    )


async def test_a_contacts_important_dates_follow_the_merge(rel_pool):
    _source_entity, _target_entity, _fact_id, source, target = await _pair(rel_pool)
    row = await rel_pool.fetchval(
        "INSERT INTO important_dates (contact_id, label, month, day) "
        "VALUES ($1, 'birthday', 3, 9) RETURNING id",
        source,
    )

    await contacts_mod.contact_merge(rel_pool, source, target)

    assert (
        await rel_pool.fetchval("SELECT contact_id FROM important_dates WHERE id = $1", row)
        == target
    )


@pytest.mark.parametrize(
    ("target_year", "source_year", "merged_years"),
    [
        (1990, 1990, [1990]),
        (None, None, [None]),
        (None, 1990, [1990]),
        (1990, None, [1990]),
        (1990, 1991, [1990, 1991]),
    ],
    ids=["same-year", "no-years", "target-lacks-year", "source-lacks-year", "years-conflict"],
)
async def test_a_date_both_contacts_hold_merges_to_one_row_keeping_any_known_year(
    rel_pool, target_year, source_year, merged_years
):
    """Readers list every row, so a shared birthday would reach the owner twice."""
    _source_entity, _target_entity, _fact_id, source, target = await _pair(rel_pool)
    await rel_pool.execute(
        "INSERT INTO important_dates (contact_id, label, month, day, year) "
        "VALUES ($1, 'birthday', 3, 9, $2::int)",
        target,
        target_year,
    )
    await rel_pool.execute(
        "INSERT INTO important_dates (contact_id, label, month, day, year) VALUES "
        "($1, 'birthday', 3, 9, $2::int), ($1, 'anniversary', 3, 9, NULL), "
        "($1, 'birthday', 3, 10, NULL), ($1, 'birthday', 4, 9, NULL)",
        source,
        source_year,
    )

    await contacts_mod.contact_merge(rel_pool, source, target)

    rows = await rel_pool.fetch(
        "SELECT contact_id, label, month, day, year FROM important_dates "
        "WHERE contact_id = ANY($1::uuid[]) ORDER BY label, month, day, year",
        [source, target],
    )
    assert [tuple(row) for row in rows] == [
        (target, "anniversary", 3, 9, None),
        *[(target, "birthday", 3, 9, year) for year in merged_years],
        (target, "birthday", 3, 10, None),
        (target, "birthday", 4, 9, None),
    ]


@pytest.mark.parametrize(
    ("add_parent", "add_members", "read_members"),
    [
        (
            "INSERT INTO groups (name) VALUES ($1) RETURNING id",
            "INSERT INTO group_members (group_id, contact_id) VALUES ($1, $2), ($1, $3), ($4, $2)",
            "SELECT group_id AS k, contact_id FROM group_members",
        ),
        (
            "INSERT INTO labels (name) VALUES ($1) RETURNING id",
            "INSERT INTO contact_labels (label_id, contact_id) VALUES ($1, $2), ($1, $3), ($4, $2)",
            "SELECT label_id AS k, contact_id FROM contact_labels",
        ),
    ],
    ids=["group_members", "contact_labels"],
)
async def test_two_contacts_sharing_a_group_or_label_can_merge(
    rel_pool, add_parent, add_members, read_members
):
    """Each has a unique (key, contact_id), so a plain re-point refused the whole merge."""
    _source_entity, _target_entity, _fact_id, source, target = await _pair(rel_pool)
    shared = await rel_pool.fetchval(add_parent, f"shared {uuid.uuid4()}")
    only_source = await rel_pool.fetchval(add_parent, f"source {uuid.uuid4()}")
    await rel_pool.execute(add_members, shared, source, target, only_source)

    await contacts_mod.contact_merge(rel_pool, source, target)

    rows = await rel_pool.fetch(read_members)
    assert {(row["k"], row["contact_id"]) for row in rows} == {
        (shared, target),
        (only_source, target),
    }


async def test_every_contact_child_table_exists_and_every_contact_column_is_accounted_for(rel_pool):
    """A wrong name is skipped silently at runtime, so a merge would leave those rows behind."""
    columns = await rel_pool.fetch(
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND column_name IN ('contact_id', 'contact_a', 'contact_b')"
    )
    at_head = {
        (row["table_name"], row["column_name"])
        for row in columns
        if "_bak_" not in row["table_name"] and "_dropbak_" not in row["table_name"]
    }

    assert set(contacts_mod._CONTACT_CHILD_TABLES) | _NOT_REPOINTED == at_head
