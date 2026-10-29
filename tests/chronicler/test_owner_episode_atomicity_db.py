"""Real-PostgreSQL proof that an episode and its owner link commit or roll back together.

bu-qp92dv.  A trigger makes the owner ``episode_entities`` INSERT fail.  From a separate
connection, no new episode may exist and an existing episode may not have been updated.
CI only (needs Docker).
"""

from __future__ import annotations

import importlib.util
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

from butlers.chronicler.adapters.google_health import GoogleHealthSleepAdapter
from butlers.chronicler.adapters.sessions import CoreSessionsAdapter
from butlers.chronicler.contracts import INITIAL_SOURCES, seed_source_registry
from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]

_DDL = Path(__file__).resolve().parents[2] / "roster" / "chronicler" / "tests" / "_inline_ddl.py"
_spec = importlib.util.spec_from_file_location("_inline_ddl_atomicity", _DDL)
assert _spec is not None and _spec.loader is not None
_inline_ddl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_inline_ddl)

_FAIL_LINK = """
CREATE OR REPLACE FUNCTION fail_owner_link() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'owner link refused for test';
END
$$;
CREATE TRIGGER fail_owner_link BEFORE INSERT ON episode_entities
    FOR EACH ROW EXECUTE FUNCTION fail_owner_link();
"""
_RESTORE_LINK = "DROP TRIGGER IF EXISTS fail_owner_link ON episode_entities"


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    return create_migrated_test_db(
        postgres_container, migration_db_name(), chains=["core", "chronicler"]
    )


@pytest.fixture
async def pool(migrated_db_url: str):
    p = await asyncpg.create_pool(
        migrated_db_url, min_size=2, max_size=5, init=register_jsonb_codec
    )
    await p.execute(_RESTORE_LINK)
    await p.execute("TRUNCATE TABLE episodes, point_events, projection_checkpoints CASCADE")
    await seed_source_registry(p, sources=INITIAL_SOURCES)
    yield p
    await p.execute(_RESTORE_LINK)
    await p.close()


async def _owner(pool: asyncpg.Pool):
    existing = await pool.fetchval("SELECT id FROM public.entities WHERE 'owner' = ANY(roles)")
    if existing is not None:
        return existing
    return await pool.fetchval(
        "INSERT INTO public.entities (canonical_name, entity_type, roles) "
        "VALUES ('Owner', 'person', ARRAY['owner']) RETURNING id"
    )


def _sleep_row(*, efficiency: float) -> dict:
    start = datetime(2026, 10, 1, 22, 0, tzinfo=UTC)
    return {
        "id": uuid4(),
        "idempotency_key": "atomicity-sleep-1",
        "valid_at": start,
        "metadata": {
            "duration_ms": 8 * 3_600_000,
            "efficiency": efficiency,
            "end_at": (start + timedelta(hours=8)).isoformat(),
        },
    }


async def _episode_state(pool: asyncpg.Pool, source: str) -> list[tuple]:
    # A separate acquisition, so only committed state is visible.
    rows = await pool.fetch(
        "SELECT source_ref, payload FROM episodes WHERE source_name = $1 ORDER BY source_ref",
        source,
    )
    return [(r["source_ref"], r["payload"]) for r in rows]


async def test_google_health_sleep_owner_link_failure_leaves_no_new_or_updated_episode(
    pool: asyncpg.Pool,
) -> None:
    owner = await _owner(pool)
    adapter = GoogleHealthSleepAdapter()

    # A failing link must not leave a new episode behind.
    await pool.execute(_FAIL_LINK)
    with pytest.raises(asyncpg.PostgresError, match="owner link refused"):
        await adapter._project_row(pool, _sleep_row(efficiency=0.80), entity_id=owner)
    assert await _episode_state(pool, adapter.source_name) == []

    # An existing episode must not be updated when the link write fails.
    await pool.execute(_RESTORE_LINK)
    await adapter._project_row(pool, _sleep_row(efficiency=0.80), entity_id=owner)
    before = await _episode_state(pool, adapter.source_name)
    assert len(before) == 1
    assert await pool.fetchval("SELECT count(*) FROM episode_entities") == 1

    await pool.execute("DELETE FROM episode_entities")
    await pool.execute(_FAIL_LINK)
    with pytest.raises(asyncpg.PostgresError, match="owner link refused"):
        await adapter._project_row(pool, _sleep_row(efficiency=0.55), entity_id=owner)
    assert await _episode_state(pool, adapter.source_name) == before


async def test_sessions_adapter_nests_and_rolls_back_the_pair(pool: asyncpg.Pool) -> None:
    """Sessions already opens its own transaction; the helper nests as a savepoint."""
    await _owner(pool)
    schema = "atomicity_butler"
    now = datetime.now(UTC)
    await pool.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
    await pool.execute(_inline_ddl.make_sessions_table_ddl(schema))
    await pool.execute(
        f"""
        INSERT INTO "{schema}".sessions (
            id, prompt, trigger_source, request_id, started_at, completed_at
        ) VALUES ($1, 'closed', 'external', 'r-atomic', $2, $3)
        """,
        uuid4(),
        now - timedelta(minutes=30),
        now - timedelta(minutes=25),
    )
    adapter = CoreSessionsAdapter(butler_schemas=(schema,))

    await pool.execute(_FAIL_LINK)
    await adapter.run(pool=pool, chronicler_pool=pool)
    assert await _episode_state(pool, "core.sessions") == []

    await pool.execute(_RESTORE_LINK)
    result = await adapter.run(pool=pool, chronicler_pool=pool)
    assert result.success
    assert len(await _episode_state(pool, "core.sessions")) == 1
    assert await pool.fetchval("SELECT count(*) FROM episode_entities") == 1
