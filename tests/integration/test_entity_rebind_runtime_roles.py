"""A late entity rebind under each schema's real runtime role (bu-dhloi1.52).

``FOR KEY SHARE`` on ``public.entities`` needs UPDATE, and ``core_065`` does not
list ``butler_chronicler_rw``, so its grant comes from ``scripts/init-db.sql``
alone. A refusal would leave every receipt on that schema ``failed``. This lives
outside ``roster/relationship`` because it reads Chronicler's tables directly.
"""

from __future__ import annotations

import asyncio
import shutil
from uuid import UUID, uuid4

import asyncpg
import pytest

from butlers.db import register_jsonb_codec
from butlers.entity_rebind import process_pending_entity_rebinds

pytestmark = [
    pytest.mark.integration,
    pytest.mark.db,
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]

_CHAINS = ("core", "memory", "relationship", "approvals", "chronicler")
_SCHEMAS = {
    "memory": "relationship",
    "relationship": "relationship",
    "approvals": "relationship",
    "chronicler": "chronicler",
}


@pytest.fixture
async def rebind_pools(fresh_db):
    """An owner pool for seeding, plus each schema's pool under its bootstrap runtime role."""
    from tests.chronicler._private_memory_setup import prepare_chronicler_current_head_sync

    db_url = await asyncio.to_thread(
        fresh_db,
        *_CHAINS,
        schemas=_SCHEMAS,
        revisions={"chronicler": "chronicler_029"},
    )
    await asyncio.to_thread(prepare_chronicler_current_head_sync, db_url)

    pools: list = []

    async def open_pool(search_path: str, role: str | None = None):
        async def set_role(conn) -> None:
            await conn.execute(f"SET ROLE {role}")

        pool = await asyncpg.create_pool(
            db_url,
            min_size=1,
            max_size=2,
            init=register_jsonb_codec,
            setup=set_role if role else None,
            server_settings={"search_path": search_path},
        )
        pools.append(pool)
        return pool

    try:
        yield (
            await open_pool("relationship, public"),
            {
                "chronicler": await open_pool("chronicler, public", "butler_chronicler_rw"),
                "relationship": await open_pool("relationship, public", "butler_relationship_rw"),
            },
        )
    finally:
        for pool in pools:
            try:
                await asyncio.wait_for(pool.close(), timeout=10)
            finally:
                pool.terminate()


async def _entity(pool) -> UUID:
    return await pool.fetchval(
        "INSERT INTO public.entities (canonical_name) VALUES ('rebind ' || gen_random_uuid()) "
        "RETURNING id"
    )


async def _seed_chronicler_reference(owner, source: UUID) -> str:
    """A real ``episode_entities`` row on ``source``; returns the query that reads it back."""
    await owner.execute(
        "INSERT INTO chronicler.source_adapter_state (source_name, chronicler_compatibility) "
        "VALUES ('test-adapter', 'supported') ON CONFLICT (source_name) DO NOTHING"
    )
    episode = await owner.fetchval(
        "INSERT INTO chronicler.episodes (id, source_name, source_ref, episode_type, start_at) "
        "VALUES (gen_random_uuid(), 'test-adapter', 'ref', 'test', now()) RETURNING id"
    )
    await owner.execute(
        "INSERT INTO chronicler.episode_entities (episode_id, entity_id, role) "
        "VALUES ($1, $2, 'participant')",
        episode,
        source,
    )
    return "SELECT entity_id FROM chronicler.episode_entities"


async def _seed_relationship_reference(owner, source: UUID) -> str:
    """A real memory ``facts`` row on ``source``; returns the query that reads it back."""
    await owner.execute(
        "INSERT INTO relationship.facts (subject, entity_id, predicate, content) "
        "VALUES ('opaque-subject', $1, 'note', 'opaque')",
        source,
    )
    return "SELECT entity_id FROM relationship.facts"


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize(
    ("schema", "role", "seed_reference"),
    [
        ("chronicler", "butler_chronicler_rw", _seed_chronicler_reference),
        ("relationship", "butler_relationship_rw", _seed_relationship_reference),
    ],
    ids=["chronicler", "relationship"],
)
async def test_a_late_rebind_runs_under_each_schemas_bootstrap_role(
    rebind_pools, schema, role, seed_reference
) -> None:
    owner, runtime_pools = rebind_pools
    pool = runtime_pools[schema]
    assert await pool.fetchval("SELECT current_user") == role
    source = await _entity(owner)
    target = await _entity(owner)
    survivor = await _entity(owner)
    await owner.execute(
        "UPDATE public.entities SET metadata = metadata || jsonb_build_object("
        "'merged_into', $2::text) WHERE id = $1",
        target,
        str(survivor),
    )
    read_reference = await seed_reference(owner, source)
    await owner.execute(
        """
        INSERT INTO public.entity_rebind_log (
            rebind_id, source_entity_id, target_entity_id, target_schema,
            status, error_class, completed_at, updated_at
        ) VALUES ($1, $2, $3, $4, 'failed', 'RuntimeError', now(), now() - interval '1 day')
        """,
        uuid4(),
        source,
        target,
        schema,
    )

    receipts = await process_pending_entity_rebinds(pool, target_schema=schema)

    assert [(r.status, r.error_class, r.references_rebound) for r in receipts] == [
        ("active", None, 1)
    ]
    assert await owner.fetchval(read_reference) == survivor
