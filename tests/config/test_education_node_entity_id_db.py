"""Real-Postgres regression test for education.mind_map_nodes.entity_id (bu-prtk1).

``mind_map_node_create`` (roster/education/tools/mind_map_nodes.py) runs::

    UPDATE education.mind_map_nodes SET entity_id = $1 WHERE id = $2

The original 001 migration's ``mind_map_nodes`` table had no ``entity_id``
column, so against a real backend this raised::

    asyncpg.exceptions.UndefinedColumnError: column "entity_id" does not exist

…masked because the mocked-pool unit tests never bind the SQL to Postgres.
Migration ``education_003`` adds the column (+ conditional FK + backfill).

This test runs the *actual* ``mind_map_node_create`` write path against a
migrated Postgres (core + education chains via testcontainers/Docker). It fails
against the missing column and passes once the migration is applied.
"""

from __future__ import annotations

import shutil

import asyncpg
import pytest

from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from butlers.tools.education.mind_map_nodes import mind_map_node_create, mind_map_node_get

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    """Provision core (public.entities) + education (education schema) chains."""
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "education"],
        schemas={"education": "education"},
    )


@pytest.fixture
async def pool(postgres_container, migrated_db_url: str):
    p = await asyncpg.create_pool(
        migrated_db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
    )
    await p.execute("TRUNCATE TABLE education.mind_maps CASCADE")
    await p.execute("TRUNCATE TABLE public.entities CASCADE")
    yield p
    await p.close()


async def test_entity_id_column_exists_after_migration(pool: asyncpg.Pool) -> None:
    """The migration must add the entity_id column to education.mind_map_nodes."""
    col = await pool.fetchval(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'education'
          AND table_name = 'mind_map_nodes'
          AND column_name = 'entity_id'
        """
    )
    assert col == "entity_id"


async def test_node_create_writes_entity_id_real_pool(pool: asyncpg.Pool) -> None:
    """The real mind_map_node_create write path must succeed and link the entity."""
    map_id = str(
        await pool.fetchval(
            "INSERT INTO education.mind_maps (title) VALUES ($1) RETURNING id",
            "Python",
        )
    )

    result = await mind_map_node_create(pool, map_id, "List Comprehensions")

    assert result["node_id"]
    assert result["entity_id"]

    # The node row must carry the linked entity_id (this is the UPDATE that
    # previously failed at runtime).
    stored_entity_id = await pool.fetchval(
        "SELECT entity_id FROM education.mind_map_nodes WHERE id = $1",
        result["node_id"],
    )
    assert str(stored_entity_id) == result["entity_id"]

    # The shared entity must exist with the canonical name pattern.
    canonical = await pool.fetchval(
        "SELECT canonical_name FROM public.entities WHERE id = $1",
        result["entity_id"],
    )
    assert canonical == "Python > List Comprehensions"

    # Read path must surface entity_id.
    node = await mind_map_node_get(pool, result["node_id"])
    assert node is not None
    assert str(node["entity_id"]) == result["entity_id"]


async def test_migration_backfills_legacy_nodes(pool: asyncpg.Pool) -> None:
    """A node inserted without entity_id (legacy path) is backfilled on re-run.

    Simulates a pre-migration node by nulling entity_id, then asserts the
    backfill DO-block (re-applied here inline mirrors education_003) links it.
    """
    map_id = str(
        await pool.fetchval(
            "INSERT INTO education.mind_maps (title) VALUES ($1) RETURNING id",
            "Calculus",
        )
    )
    legacy_node_id = str(
        await pool.fetchval(
            """
            INSERT INTO education.mind_map_nodes (mind_map_id, label)
            VALUES ($1, $2) RETURNING id
            """,
            map_id,
            "Limits",
        )
    )
    # Legacy node has no entity link.
    assert (
        await pool.fetchval(
            "SELECT entity_id FROM education.mind_map_nodes WHERE id = $1",
            legacy_node_id,
        )
        is None
    )

    # Run the same backfill the migration performs.
    await pool.execute(
        """
        DO $$
        DECLARE
            n RECORD;
            eid UUID;
            cname TEXT;
        BEGIN
            FOR n IN
                SELECT mmn.id AS node_id, mmn.mind_map_id, mmn.label, mm.title
                FROM education.mind_map_nodes mmn
                JOIN education.mind_maps mm ON mm.id = mmn.mind_map_id
                WHERE mmn.entity_id IS NULL
            LOOP
                cname := n.title || ' > ' || n.label;
                INSERT INTO public.entities (canonical_name, entity_type, aliases, metadata)
                VALUES (cname, 'other', '{}',
                    jsonb_build_object('source_butler', 'education',
                                       'source_scope', 'education',
                                       'mind_map_id', n.mind_map_id::text))
                ON CONFLICT DO NOTHING;
                SELECT id INTO eid FROM public.entities
                WHERE canonical_name = cname AND entity_type = 'other'
                  AND (metadata->>'merged_into') IS NULL LIMIT 1;
                IF eid IS NOT NULL THEN
                    UPDATE education.mind_map_nodes SET entity_id = eid WHERE id = n.node_id;
                END IF;
            END LOOP;
        END $$;
        """
    )

    backfilled = await pool.fetchval(
        "SELECT entity_id FROM education.mind_map_nodes WHERE id = $1",
        legacy_node_id,
    )
    assert backfilled is not None
    canonical = await pool.fetchval(
        "SELECT canonical_name FROM public.entities WHERE id = $1",
        backfilled,
    )
    assert canonical == "Calculus > Limits"
