"""The enrichment reap against a writer or merge in flight, on real Postgres (bu-dhloi1.101).

The reap soft-deletes a placeholder only if nothing references it. A waiting UPDATE re-checks
its row but not its statement's snapshot, so a reference still uncommitted when the reap
started goes unseen.
"""

from __future__ import annotations

import asyncio
import shutil
import uuid

import asyncpg
import pytest

from butlers.roster_imports import roster_module

reap = roster_module(
    "relationship", "jobs/relationship_jobs"
).archive_rejected_identity_enrichment_orphans
merge_entity_pair = roster_module("relationship", "tools/entity_merge").merge_entity_pair

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]


# (table, column) pairs that reference an entity: the ones the reap's referenced check reads, and
# the ones it deliberately does not, each with the reason. Tables are named without a schema:
# the memory ``facts`` table is relationship.facts in production and sits in public in this
# test database, and the reap reads it only where the memory module is enabled.
_CHECKED: set[tuple[str, str]] = {
    ("facts", "entity_id"),
    ("facts", "object_entity_id"),
    # Every butler's facts, in one place the relationship role can read.
    ("memory_catalog", "entity_id"),
    ("memory_catalog", "object_entity_id"),
    ("entity_facts", "subject"),
    ("entity_facts", "object_entity_id"),
    ("contact_entity_map", "entity_id"),
    ("entity_info", "entity_id"),
    ("priority_contacts", "entity_id"),
}
_EXCUSED: set[tuple[str, str]] = {
    # Derived from facts or ledgers: they follow a fact the reap already read, or record history.
    ("entity_graph_edges", "object_entity_id"),
    ("entity_graph_edges", "subject_entity_id"),
    ("entity_graph_withheld_counts", "entity_id"),
    ("entity_rebind_log", "source_entity_id"),
    ("entity_rebind_log", "target_entity_id"),
    ("fact_coverage", "subject"),
    ("merge_reviews", "entity_a"),
    ("merge_reviews", "entity_b"),
    ("entity_view_marks", "entity_id"),
    # Name the owner or an authority, never a person an email placeholder stands for.
    ("entity_facts", "authority_entity_id"),
    ("entity_facts", "confirmed_by_entity_id"),
    ("fact_identity_decisions", "owner_entity_id"),
    ("fact_identity_decisions", "owner_original_entity_id"),
    ("entity_facts", "authority_original_entity_id"),
    ("entity_facts", "confirmed_by_original_entity_id"),
    ("facts", "authority_entity_id"),
    ("episodes", "authority_entity_id"),
    ("rules", "authority_entity_id"),
    ("custody_holds", "owner_entity_id"),
    ("break_through_clauses", "entity_id"),
    # Open questions put to the owner about an entity, derived from facts the reap already read.
    ("knowledge_gaps", "entity_id"),
    ("knowledge_gaps", "answered_authority_entity_id"),
    # Accounts and money belong to an entity the owner already identified, and live in schemas
    # a relationship role cannot read (calendar, finance, credentials).
    ("calendar_event_entities", "entity_id"),
    ("cost_claims", "counterparty_entity_id"),
    ("google_accounts", "entity_id"),
    ("steam_accounts", "entity_id"),
    ("credentials", "entity_id"),
    # The retired contacts table that this test database still carries.
    ("contacts", "entity_id"),
}


@pytest.fixture
async def rel_pool(db):
    return await db("core", "memory", "relationship", "approvals", min_size=2, max_size=8)


async def _placeholder(pool: asyncpg.Pool) -> uuid.UUID:
    return await pool.fetchval(
        "INSERT INTO public.entities (canonical_name, entity_type, metadata) "
        "VALUES ($1, 'person', $2::jsonb) RETURNING id",
        f"Placeholder {uuid.uuid4()}",
        {
            "proposed_source": "email_identity_enrichment",
            "proposed_from_address": f"{uuid.uuid4().hex}@example.test",
        },
    )


async def _entity(pool: asyncpg.Pool, tag: str) -> uuid.UUID:
    return await pool.fetchval(
        "INSERT INTO public.entities (canonical_name, entity_type) "
        "VALUES ($1, 'person') RETURNING id",
        f"{tag} {uuid.uuid4()}",
    )


_INSERT_FACT = (
    "INSERT INTO relationship.entity_facts (subject, predicate, object, object_kind, src) "
    "VALUES ($1, 'has-email', $2, 'literal', 'test') RETURNING id"
)


async def _deleted_at(pool: asyncpg.Pool, entity: uuid.UUID) -> str | None:
    return await pool.fetchval(
        "SELECT metadata->>'deleted_at' FROM public.entities WHERE id = $1", entity
    )


async def _settled_or_lock_waiting(pool: asyncpg.Pool, task: asyncio.Task) -> None:
    for _ in range(500):
        if task.done() or await pool.fetchval(
            "SELECT count(*) FROM pg_stat_activity "
            "WHERE datname = current_database() AND wait_event_type = 'Lock'"
        ):
            return
        await asyncio.sleep(0.01)
    pytest.fail("the reap neither finished nor waited on a lock")


@pytest.mark.asyncio(loop_scope="session")
async def test_the_reap_leaves_an_entity_with_a_fact_write_in_flight(rel_pool) -> None:
    """An approved proposal is writing its first fact: this entity has graduated."""
    entity = await _placeholder(rel_pool)

    async with rel_pool.acquire() as conn, conn.transaction():
        await conn.fetchval(_INSERT_FACT, entity, "in-flight@example.test")
        # A batch job must not queue behind the writer; it skips the row for the next run.
        assert await asyncio.wait_for(reap(rel_pool), timeout=5) == 0

    assert await reap(rel_pool) == 0
    assert await _deleted_at(rel_pool, entity) is None


class _CommitsAFactAfterTheCandidateScan:
    """A writer commits after the scan chose its candidates, and before the reap locks one."""

    def __init__(self, pool: asyncpg.Pool, entity: uuid.UUID) -> None:
        self._pool = pool
        self._entity = entity

    def __getattr__(self, name: str) -> object:
        return getattr(self._pool, name)

    async def fetch(self, query: str, *args: object) -> list:
        rows = await self._pool.fetch(query, *args)
        await self._pool.fetchval(_INSERT_FACT, self._entity, "late@example.test")
        return rows


@pytest.mark.asyncio(loop_scope="session")
async def test_the_reap_rechecks_under_its_lock(rel_pool) -> None:
    entity = await _placeholder(rel_pool)

    assert await reap(_CommitsAFactAfterTheCandidateScan(rel_pool, entity)) == 0

    assert await _deleted_at(rel_pool, entity) is None


@pytest.mark.asyncio(loop_scope="session")
async def test_the_reap_comes_back_for_an_entity_whose_writer_rolled_back(rel_pool) -> None:
    entity = await _placeholder(rel_pool)

    conn = await rel_pool.acquire()
    try:
        transaction = conn.transaction()
        await transaction.start()
        await conn.fetchval(_INSERT_FACT, entity, "abandoned@example.test")
        reaping = asyncio.create_task(reap(rel_pool))
        await _settled_or_lock_waiting(rel_pool, reaping)
        await transaction.rollback()
    finally:
        await rel_pool.release(conn)

    first = await asyncio.wait_for(reaping, timeout=10)
    assert first + await reap(rel_pool) == 1
    assert await _deleted_at(rel_pool, entity) is not None


@pytest.mark.asyncio(loop_scope="session")
async def test_the_reap_leaves_the_target_of_a_merge_in_flight(rel_pool) -> None:
    """The merge moves facts onto the placeholder, which the reap's snapshot cannot see."""
    target = await _placeholder(rel_pool)
    source = await _entity(rel_pool, "Source")
    fact = await rel_pool.fetchval(_INSERT_FACT, source, "moved@example.test")

    locked = asyncio.Event()
    resume = asyncio.Event()

    async def pause_under_the_locks(_conn, _pair) -> None:
        locked.set()
        await resume.wait()

    merge = asyncio.create_task(
        merge_entity_pair(
            rel_pool,
            source_entity_id=source,
            target_entity_id=target,
            locked_guard=pause_under_the_locks,
        )
    )
    await asyncio.wait_for(locked.wait(), timeout=10)
    reaping = asyncio.create_task(reap(rel_pool))
    await _settled_or_lock_waiting(rel_pool, reaping)
    resume.set()
    await asyncio.wait_for(asyncio.gather(merge, reaping), timeout=20)

    assert await reap(rel_pool) == 0
    assert await _deleted_at(rel_pool, target) is None
    row = await rel_pool.fetchrow(
        "SELECT subject, validity FROM relationship.entity_facts WHERE id = $1", fact
    )
    assert (row["subject"], row["validity"]) == (target, "active")


@pytest.mark.asyncio(loop_scope="session")
async def test_a_merge_refuses_a_target_the_reap_already_took(rel_pool) -> None:
    """The other order: the placeholder is already reaped, so a merge onto it is refused."""
    target = await _placeholder(rel_pool)
    source = await _entity(rel_pool, "Source")
    fact = await rel_pool.fetchval(_INSERT_FACT, source, "kept@example.test")

    assert await reap(rel_pool) == 1
    with pytest.raises(
        roster_module("relationship", "tools/entity_merge").TargetEntityTombstonedError
    ):
        await merge_entity_pair(rel_pool, source_entity_id=source, target_entity_id=target)

    assert (
        await rel_pool.fetchval("SELECT subject FROM relationship.entity_facts WHERE id = $1", fact)
        == source
    )


async def _predicate(pool: asyncpg.Pool, *, is_edge: bool) -> str:
    name = f"reap_{uuid.uuid4().hex}"
    await pool.execute(
        "INSERT INTO predicate_registry (name, is_edge, is_temporal, status) "
        "VALUES ($1, $2, false, 'active')",
        name,
        is_edge,
    )
    return name


async def _memory_fact(pool, subject, *, obj=None, validity="active") -> None:
    predicate = await _predicate(pool, is_edge=obj is not None)
    await pool.execute(
        "INSERT INTO facts (subject, entity_id, object_entity_id, predicate, content, validity) "
        "VALUES ('opaque', $1, $2, $3, 'opaque', $4)",
        subject,
        obj,
        predicate,
        validity,
    )


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("as_object", [False, True], ids=["subject", "object"])
async def test_the_reap_leaves_a_placeholder_a_live_memory_fact_references(
    rel_pool, as_object
) -> None:
    """bu-dhloi1.127: memory facts are references too, so the placeholder is not reaped."""
    placeholder = await _placeholder(rel_pool)
    other = await _entity(rel_pool, "Other")
    if as_object:
        await _memory_fact(rel_pool, other, obj=placeholder)
    else:
        await _memory_fact(rel_pool, placeholder)

    assert await reap(rel_pool) == 0

    assert await _deleted_at(rel_pool, placeholder) is None


async def _catalog_row(pool, *, subject=None, obj=None, invalid: bool = False) -> None:
    """A fact in another butler's schema, as the relationship role sees it: a catalog row."""
    await pool.execute(
        "INSERT INTO public.memory_catalog (source_schema, source_table, source_id, "
        "source_butler, entity_id, object_entity_id, invalid_at) "
        "VALUES ('health', 'facts', gen_random_uuid(), 'health', $1, $2, "
        "CASE WHEN $3 THEN now() END)",
        subject,
        obj,
        invalid,
    )


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("as_object", [False, True], ids=["subject", "object"])
async def test_the_reap_leaves_a_placeholder_another_butlers_fact_references(
    rel_pool, as_object
) -> None:
    """bu-dhloi1.127: facts in other butler schemas are visible only through the catalog."""
    placeholder = await _placeholder(rel_pool)
    other = await _entity(rel_pool, "Other")
    if as_object:
        await _catalog_row(rel_pool, subject=other, obj=placeholder)
    else:
        await _catalog_row(rel_pool, subject=placeholder)

    assert await reap(rel_pool) == 0

    assert await _deleted_at(rel_pool, placeholder) is None


@pytest.mark.asyncio(loop_scope="session")
async def test_an_invalidated_catalog_row_does_not_keep_a_placeholder_alive(rel_pool) -> None:
    placeholder = await _placeholder(rel_pool)
    await _catalog_row(rel_pool, subject=placeholder, invalid=True)

    assert await reap(rel_pool) >= 1

    assert await _deleted_at(rel_pool, placeholder) is not None


@pytest.mark.asyncio(loop_scope="session")
async def test_the_reap_leaves_a_placeholder_a_contact_is_linked_to(rel_pool) -> None:
    placeholder = await _placeholder(rel_pool)
    await rel_pool.execute(
        "INSERT INTO contact_entity_map (contact_id, entity_id) VALUES ($1, $2)",
        uuid.uuid4(),
        placeholder,
    )

    assert await reap(rel_pool) == 0

    assert await _deleted_at(rel_pool, placeholder) is None


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize(
    "insert",
    [
        "INSERT INTO public.entity_info (entity_id, type, value) VALUES ($1, 'email', 'a@b.test')",
        "INSERT INTO public.priority_contacts (contact_id, entity_id, added_by) "
        "VALUES ($1, $1, 'test')",
    ],
    ids=["entity_info", "priority_contacts"],
)
async def test_the_reap_leaves_a_placeholder_with_other_entity_references(rel_pool, insert) -> None:
    placeholder = await _placeholder(rel_pool)
    await rel_pool.execute(insert, placeholder)

    assert await reap(rel_pool) == 0

    assert await _deleted_at(rel_pool, placeholder) is None


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("validity", ["retracted", "superseded"])
async def test_a_dead_memory_fact_does_not_keep_a_placeholder_alive(rel_pool, validity) -> None:
    placeholder = await _placeholder(rel_pool)
    await _memory_fact(rel_pool, placeholder, validity=validity)

    assert await reap(rel_pool) >= 1

    assert await _deleted_at(rel_pool, placeholder) is not None


@pytest.mark.asyncio(loop_scope="session")
async def test_every_table_referencing_entities_is_checked_by_the_reap_or_excused(rel_pool) -> None:
    """A table that references an entity must be decided, not silently missed.

    Reach: base tables in the ``public`` and ``relationship`` schemas of the migrated test
    database, found two ways: a foreign key to ``public.entities``, and any column whose name
    ends in ``entity_id`` (which catches references with no FK, such as ``memory_catalog`` and
    ``contact_entity_map``). Other butlers' schemas are out of reach: their facts are read through
    ``public.memory_catalog``. A reference named some other way, with no FK, is also not found.
    """
    rows = await rel_pool.fetch(
        """
        SELECT DISTINCT c.relname AS tbl, a.attname AS col
        FROM pg_constraint k
        JOIN pg_class c ON c.oid = k.conrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = ANY (k.conkey)
        WHERE k.contype = 'f' AND k.confrelid = 'public.entities'::regclass
          AND n.nspname IN ('public', 'relationship')
          AND c.relname !~ '(_bak_|_dropbak_)'
        UNION
        SELECT c.table_name, c.column_name
        FROM information_schema.columns c
        JOIN information_schema.tables t
          ON t.table_schema = c.table_schema AND t.table_name = c.table_name
        WHERE t.table_type = 'BASE TABLE'
          AND c.table_schema IN ('public', 'relationship')
          AND c.column_name ~ 'entity_id$'
          AND c.table_name !~ '(_bak_|_dropbak_)'
        """
    )
    seen = {(row["tbl"], row["col"]) for row in rows}
    assert seen - (_CHECKED | _EXCUSED) == set(), "decide these in the reap or excuse them"
    assert _CHECKED <= seen  # a checked reference that no longer exists is a stale test
