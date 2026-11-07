"""A memory entity delete severs the contact bridge row, as a forget does (bu-dhloi1.124).

Before, ``DELETE /api/memory/entities/{id}`` left ``contact_entity_map`` in place, so the
contact stayed on a deleted entity and ``contact_merge`` could only refuse it. These
tests run on a real migrated database.
"""

from __future__ import annotations

import shutil
import uuid
from unittest.mock import MagicMock

import asyncpg
import pytest

from butlers.api.router_discovery import discover_butler_routers
from butlers.api.routers import memory as memory_router
from butlers.roster_imports import roster_module

contacts_mod = roster_module("relationship", "tools/contacts")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]


@pytest.fixture
async def rel_pool(db):
    """Core + memory + relationship chains, with the owner the forget route requires."""
    pool = await db("core", "memory", "relationship", min_size=2, max_size=8)
    await _entity(pool, "Owner", roles=["owner"])
    return pool


async def _entity(pool: asyncpg.Pool, tag: str, *, roles: list[str] | None = None) -> uuid.UUID:
    return await pool.fetchval(
        "INSERT INTO public.entities (canonical_name, entity_type, roles) "
        "VALUES ($1, 'person', $2) RETURNING id",
        f"{tag} {uuid.uuid4()}",
        roles or [],
    )


async def _contact(pool: asyncpg.Pool, entity: uuid.UUID) -> uuid.UUID:
    contact = uuid.uuid4()
    await pool.execute(
        "INSERT INTO contact_entity_map (contact_id, entity_id) VALUES ($1, $2)", contact, entity
    )
    return contact


def _dashboard_db(pool: object) -> MagicMock:
    db = MagicMock()
    db.butler_names = ["relationship"]
    db.pool = MagicMock(return_value=pool)
    return db


async def _bridge_rows(pool: asyncpg.Pool, contact: uuid.UUID) -> int:
    return await pool.fetchval(
        "SELECT count(*) FROM contact_entity_map WHERE contact_id = $1", contact
    )


async def test_a_memory_delete_removes_the_contact_bridge_row(rel_pool) -> None:
    entity = await _entity(rel_pool, "Deleted")
    contact = await _contact(rel_pool, entity)

    await memory_router.delete_entity(str(entity), retire_facts=True, db=_dashboard_db(rel_pool))

    assert await _bridge_rows(rel_pool, contact) == 0
    assert (
        await rel_pool.fetchval(
            "SELECT metadata->>'deleted_at' FROM public.entities WHERE id = $1", entity
        )
        is not None
    )


async def test_a_deleted_entitys_contact_reads_as_not_found(rel_pool) -> None:
    entity = await _entity(rel_pool, "Deleted")
    contact = await _contact(rel_pool, entity)

    await memory_router.delete_entity(str(entity), retire_facts=True, db=_dashboard_db(rel_pool))

    assert await contacts_mod.contact_get(rel_pool, contact, allow_missing=True) is None
    with pytest.raises(ValueError, match="not found"):
        await contacts_mod.contact_get(rel_pool, contact)


async def test_a_refused_delete_keeps_the_bridge_row(rel_pool) -> None:
    """The owner entity is refused; its contact stays linked."""
    owner = await rel_pool.fetchval("SELECT id FROM public.entities WHERE 'owner' = ANY(roles)")
    contact = await _contact(rel_pool, owner)

    with pytest.raises(Exception) as refused:
        await memory_router.delete_entity(str(owner), db=_dashboard_db(rel_pool))

    assert getattr(refused.value, "status_code", None) == 403
    assert await _bridge_rows(rel_pool, contact) == 1


async def test_other_entities_contacts_stay_linked(rel_pool) -> None:
    deleted, kept = await _entity(rel_pool, "Deleted"), await _entity(rel_pool, "Kept")
    await _contact(rel_pool, deleted)
    kept_contact = await _contact(rel_pool, kept)

    await memory_router.delete_entity(str(deleted), retire_facts=True, db=_dashboard_db(rel_pool))

    assert await _bridge_rows(rel_pool, kept_contact) == 1


async def test_forget_and_delete_share_one_unlink_path(rel_pool) -> None:
    """Both routes leave the same bridge state: no row for the entity."""
    router = next(mod for name, mod in discover_butler_routers() if name == "relationship")
    forgotten, deleted = await _entity(rel_pool, "Forgotten"), await _entity(rel_pool, "Deleted")
    forgotten_contact = await _contact(rel_pool, forgotten)
    deleted_contact = await _contact(rel_pool, deleted)

    await router.forget_entity(forgotten, db=_dashboard_db(rel_pool))
    await memory_router.delete_entity(str(deleted), retire_facts=True, db=_dashboard_db(rel_pool))

    assert await _bridge_rows(rel_pool, forgotten_contact) == 0
    assert await _bridge_rows(rel_pool, deleted_contact) == 0
