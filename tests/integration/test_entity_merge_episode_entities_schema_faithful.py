"""Real-Postgres proof that the entity-merge episode-entities re-point reads
``chronicler.episode_entities`` schema-qualified (bu-4rif7).

The memory module's ``_repoint_episode_entities`` (called by ``entity_merge``
when a ``chronicler_pool`` is supplied) reaches ACROSS schemas into the
chronicler domain's ``chronicler.episode_entities`` join table. It is covered
only by ``tests/modules/memory/test_tools_entities.py`` via ``AsyncMock`` today,
so an accidental un-qualification would pass unit tests but break in production
(the #2598 mocked-green / integration-red class).

This provisions the chronicler chain into a real ``chronicler`` schema and
drives the re-point through a pool scoped to ``public`` only — the chronicler
tables are NOT on the search_path, so the reads/writes resolve solely because
they are qualified ``chronicler.episode_entities``. Un-qualify them and this
test raises ``UndefinedTableError``.
"""

from __future__ import annotations

import shutil
import uuid

import asyncpg
import pytest

from butlers.db import register_jsonb_codec
from butlers.modules.memory.tools.entities import _repoint_episode_entities
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


@pytest.fixture(scope="module")
def chronicler_db_url(postgres_container) -> str:
    # Faithful topology: core into public, the chronicler domain chain into its
    # own ``chronicler`` schema (mirrors lifecycle.py provisioning chronicler).
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "chronicler"],
        schemas={"core": "public", "chronicler": "chronicler"},
    )


@pytest.fixture
async def public_pool(chronicler_db_url: str) -> asyncpg.Pool:
    """``public``-only search_path: chronicler.* resolves only via qualification."""
    p = await asyncpg.create_pool(
        chronicler_db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
        server_settings={"search_path": "public"},
    )
    yield p
    await p.close()


async def _seed_episode(pool: asyncpg.Pool, source_ref: str) -> uuid.UUID:
    # episode_entities.episode_id FKs chronicler.episodes(id); episodes.source_name
    # FKs chronicler.source_adapter_state(source_name). Seed the chain (qualified).
    await pool.execute(
        """
        INSERT INTO chronicler.source_adapter_state (source_name, chronicler_compatibility)
        VALUES ('test-adapter', 'supported')
        ON CONFLICT (source_name) DO NOTHING
        """
    )
    ep_id = uuid.uuid4()
    await pool.execute(
        """
        INSERT INTO chronicler.episodes (id, source_name, source_ref, episode_type, start_at)
        VALUES ($1, 'test-adapter', $2, 'test', now())
        """,
        ep_id,
        source_ref,
    )
    return ep_id


@pytest.mark.asyncio(loop_scope="session")
async def test_repoint_moves_rows_under_public_search_path(public_pool: asyncpg.Pool) -> None:
    src = uuid.uuid4()
    tgt = uuid.uuid4()

    # Sanity: public-only pool cannot see the table unqualified.
    with pytest.raises(asyncpg.UndefinedTableError):
        await public_pool.fetch("SELECT episode_id FROM episode_entities")

    # ep1: only source is linked -> should be re-pointed to target.
    ep1 = await _seed_episode(public_pool, "ref-1")
    await public_pool.execute(
        "INSERT INTO chronicler.episode_entities (episode_id, entity_id, role) VALUES ($1, $2, 'participant')",
        ep1,
        src,
    )
    # ep2: both linked, source is 'owner' (higher precedence) -> target promoted, source deleted.
    ep2 = await _seed_episode(public_pool, "ref-2")
    await public_pool.execute(
        "INSERT INTO chronicler.episode_entities (episode_id, entity_id, role) VALUES ($1, $2, 'owner')",
        ep2,
        src,
    )
    await public_pool.execute(
        "INSERT INTO chronicler.episode_entities (episode_id, entity_id, role) VALUES ($1, $2, 'participant')",
        ep2,
        tgt,
    )

    await _repoint_episode_entities(public_pool, src, tgt)

    # teeth: these qualified reads resolve only because they name the schema.
    # ep1 row moved to target.
    ep1_owner = await public_pool.fetchval(
        "SELECT entity_id FROM chronicler.episode_entities WHERE episode_id = $1", ep1
    )
    assert ep1_owner == tgt

    # source has no rows left.
    src_left = await public_pool.fetchval(
        "SELECT count(*) FROM chronicler.episode_entities WHERE entity_id = $1", src
    )
    assert src_left == 0

    # ep2 target row survives and was promoted to the higher-precedence 'owner' role.
    ep2_role = await public_pool.fetchval(
        "SELECT role FROM chronicler.episode_entities WHERE episode_id = $1 AND entity_id = $2",
        ep2,
        tgt,
    )
    assert ep2_role == "owner"
