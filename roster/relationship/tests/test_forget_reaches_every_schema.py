"""A forget retracts the entity's facts, and their graph edges, in every butler schema.

Before bu-y66o00.16 the forget route retracted ``facts`` only in the Relationship schema
and deleted no memory-fact edge, so another butler's facts about a forgotten person, the
edges they project and the person's withheld counts all stayed live. Real Postgres, two
memory schemas, the dashboard's own ``DatabaseManager``.
"""

from __future__ import annotations

import asyncio
import shutil
import uuid
from typing import cast

import asyncpg
import pytest
from fastapi import HTTPException

from butlers.api.router_discovery import discover_butler_routers
from butlers.core.entity_graph_edges import (
    project_entity_graph_edge,
    withhold_entity_graph_edge,
)
from butlers.migrations import run_migrations
from butlers.modules.memory import storage as memory_storage
from butlers.testing.db_manager import db_manager

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]

_SCHEMAS = ("relationship", "general")


@pytest.fixture
def two_memory_schemas(fresh_db) -> str:
    url = fresh_db(
        "core",
        "memory",
        "relationship",
        schemas={"core": "relationship", "memory": "relationship", "relationship": "relationship"},
    )
    for chain in ("core", "memory"):
        asyncio.run(run_migrations(url, chain=chain, schema="general"))
    return url


@pytest.fixture
async def pools(db, two_memory_schemas):
    opened = {schema: await db.open(two_memory_schemas, schema=schema) for schema in _SCHEMAS}
    await _entity(opened["relationship"], "Owner", roles=["owner"])
    return opened


async def _entity(pool: asyncpg.Pool, tag: str, *, roles: list[str] | None = None) -> uuid.UUID:
    return await pool.fetchval(
        "INSERT INTO public.entities (canonical_name, entity_type, roles) "
        "VALUES ($1, 'person', $2) RETURNING id",
        f"{tag} {uuid.uuid4()}",
        roles or [],
    )


async def _fact(
    pool: asyncpg.Pool,
    entity: uuid.UUID,
    *,
    points_at: uuid.UUID | None = None,
    sensitivity: str = "normal",
) -> uuid.UUID:
    """A memory fact, with the edge or withheld stub its writer would project."""
    schema = await pool.fetchval("SELECT current_schema()")
    async with pool.acquire() as proxy, proxy.transaction():
        conn = cast(asyncpg.Connection, proxy)
        fact = await proxy.fetchval(
            "INSERT INTO facts (subject, entity_id, object_entity_id, predicate, content, "
            "sensitivity) VALUES ('opaque', $1, $2, 'knows', 'opaque', $3) RETURNING id",
            entity,
            points_at,
            sensitivity,
        )
        if sensitivity != "normal":
            await withhold_entity_graph_edge(
                conn,
                source_schema=schema,
                source_table="facts",
                source_id=fact,
                subject_entity_id=entity,
                sensitivity=sensitivity,
            )
        elif points_at is not None:
            await project_entity_graph_edge(
                conn,
                source_schema=schema,
                source_table="facts",
                source_id=fact,
                subject_entity_id=entity,
                predicate="knows",
                object_entity_id=points_at,
            )
    return fact


def _manager(pools: dict[str, object]):
    return db_manager(pools, schemas={s: s for s in pools}, memory_schemas={s: s for s in pools})


def _forget_route():
    router = next(mod for name, mod in discover_butler_routers() if name == "relationship")
    return router.forget_entity


async def _validity(pool: asyncpg.Pool, fact: uuid.UUID) -> str:
    """The fact's validity, read through the owning schema's own pool."""
    return await pool.fetchval("SELECT validity FROM facts WHERE id = $1", fact)


async def _edges(pool: asyncpg.Pool, entity: uuid.UUID) -> int:
    return await pool.fetchval(
        "SELECT count(*) FROM public.entity_graph_edges "
        "WHERE subject_entity_id = $1 OR object_entity_id = $1",
        entity,
    )


async def _catalog_invalid(pool: asyncpg.Pool, fact: uuid.UUID) -> bool:
    return await pool.fetchval(
        "SELECT invalid_at IS NOT NULL FROM public.memory_catalog "
        "WHERE source_table = 'facts' AND source_id = $1",
        fact,
    )


async def _withheld(pool: asyncpg.Pool, entity: uuid.UUID) -> int:
    return await pool.fetchval(
        "SELECT count(*) FROM public.entity_graph_withheld_counts WHERE entity_id = $1", entity
    )


async def test_a_forget_retracts_every_schema_and_its_edges_and_counts(pools) -> None:
    rel, gen = pools["relationship"], pools["general"]
    forgotten = await _entity(rel, "Forgotten")
    neighbour = await _entity(rel, "Neighbour")
    local = await _fact(rel, forgotten)
    about = await _fact(gen, forgotten, points_at=neighbour)
    pointing = await _fact(gen, neighbour, points_at=forgotten)
    withheld = await _fact(gen, forgotten, sensitivity="pii")
    fading = await gen.fetchval(
        "INSERT INTO facts (subject, entity_id, predicate, content, validity) "
        "VALUES ('opaque', $1, 'note', 'opaque', 'fading') RETURNING id",
        forgotten,
    )
    unrelated = await _fact(gen, neighbour, points_at=await _entity(rel, "Bystander"))
    assert await _withheld(rel, forgotten) == 1
    for schema, pool in pools.items():
        await memory_storage._backfill_facts_to_catalog(pool, source_schema=schema, limit=100)

    await _forget_route()(forgotten, db=_manager(pools))

    assert await _validity(rel, local) == "retracted"
    for fact in (about, pointing, withheld, fading):
        assert await _validity(gen, fact) == "retracted"
    assert await _edges(rel, forgotten) == 0
    assert await _withheld(rel, forgotten) == 0
    assert await _validity(gen, unrelated) == "active"
    assert await _edges(rel, neighbour) == 1
    # Cross-butler search stops serving them too (bu-y66o00.22).
    for fact in (local, about, pointing, fading):
        assert await _catalog_invalid(rel, fact) is True
    assert await _catalog_invalid(rel, unrelated) is False


class _Unreachable:
    def acquire(self):
        raise ConnectionRefusedError("synthetic unreachable butler")


async def test_an_unreachable_schema_fails_the_forget_and_a_retry_completes_it(pools) -> None:
    rel, gen = pools["relationship"], pools["general"]
    forgotten = await _entity(rel, "Forgotten")
    neighbour = await _entity(rel, "Neighbour")
    about = await _fact(gen, forgotten, points_at=neighbour)

    with pytest.raises(HTTPException) as refused:
        await _forget_route()(forgotten, db=_manager({**pools, "general": _Unreachable()}))
    assert refused.value.status_code == 503
    assert "general" in refused.value.detail
    # The tombstone committed first; only the unreachable schema is left to do.
    assert (
        await rel.fetchval(
            "SELECT metadata->>'tombstone' FROM public.entities WHERE id = $1", forgotten
        )
        == "true"
    )
    assert await _validity(gen, about) == "active"

    await asyncio.wait_for(_forget_route()(forgotten, db=_manager(pools)), timeout=30)
    assert await _validity(gen, about) == "retracted"
    assert await _edges(rel, forgotten) == 0
