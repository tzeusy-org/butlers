"""A contact merge and its entity merge commit together, or not at all (bu-dhloi1.40).

``contact_merge`` used to run the entity merge after its own commit and swallow any
failure, which left facts moved with no ``merged_into`` on the source: nothing could
then tell that the source's identity had moved, and an owner term on it stopped
following its facts. These tests run on a real migrated database.
"""

from __future__ import annotations

import shutil
import uuid

import asyncpg
import pytest

from butlers.roster_imports import roster_module

contacts_mod = roster_module("relationship", "tools/contacts")
entity_merge_mod = roster_module("relationship", "tools/entity_merge")
SourceEntityTombstonedError = entity_merge_mod.SourceEntityTombstonedError
TargetEntityTombstonedError = entity_merge_mod.TargetEntityTombstonedError

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
async def test_a_refused_entity_merge_refuses_the_contact_merge(rel_pool, tombstoned, error):
    """The merge authority's own refusal, with no fault injected."""
    source_entity, target_entity, fact, source, target = await _pair(rel_pool)
    other = await _entity(rel_pool, "Elsewhere")
    await rel_pool.execute(
        "UPDATE public.entities SET metadata = metadata || $2::jsonb WHERE id = $1",
        source_entity if tombstoned == "source" else target_entity,
        {"merged_into": str(other)},
    )
    with pytest.raises(error):
        await contacts_mod.contact_merge(rel_pool, source, target)
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
