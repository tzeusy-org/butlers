"""Real-Postgres regression tests for the mind-map staleness-abandonment job.

Binding spec: ``openspec/specs/module-education-mind-map/spec.md``
  Requirement: *Mind map lifecycle — staleness abandonment*

The weekly job transitions an ``active`` mind map to ``abandoned`` once more
than 30 days have elapsed since any node activity (the maximum ``updated_at``
across the map's nodes). Maps with recent activity, ``completed`` maps, and
already-``abandoned`` maps are left untouched.

These tests run the real query (``mind_map_abandon_stale``) and the registered
deterministic job handler against a migrated Postgres, since the staleness
condition is expressed entirely in SQL and cannot be exercised by mocked pools.
"""

from __future__ import annotations

import shutil
import uuid

import asyncpg
import pytest

from butlers.scheduled_jobs import get_deterministic_schedule_job_registry
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from butlers.tools.education.mind_maps import mind_map_abandon_stale

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    """Provision core + education chains."""
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "education"],
    )


@pytest.fixture
async def pool(postgres_container, migrated_db_url: str):
    p = await asyncpg.create_pool(migrated_db_url, min_size=1, max_size=3)
    await p.execute("TRUNCATE TABLE education.mind_maps CASCADE")
    yield p
    await p.close()


async def _create_map(
    pool: asyncpg.Pool,
    *,
    title: str,
    status: str = "active",
    map_age_days: int = 0,
) -> str:
    """Insert a mind map whose created_at/updated_at are ``map_age_days`` old."""
    map_id = str(uuid.uuid4())
    await pool.execute(
        """
        INSERT INTO education.mind_maps (id, title, status, created_at, updated_at)
        VALUES ($1, $2, $3,
                now() - make_interval(days => $4),
                now() - make_interval(days => $4))
        """,
        map_id,
        title,
        status,
        map_age_days,
    )
    return map_id


async def _add_node(pool: asyncpg.Pool, map_id: str, *, age_days: int) -> None:
    """Add a node to a map whose updated_at is ``age_days`` in the past."""
    await pool.execute(
        """
        INSERT INTO education.mind_map_nodes
            (mind_map_id, label, created_at, updated_at)
        VALUES ($1, $2,
                now() - make_interval(days => $3),
                now() - make_interval(days => $3))
        """,
        map_id,
        "concept",
        age_days,
    )


async def _status(pool: asyncpg.Pool, map_id: str) -> str:
    return await pool.fetchval("SELECT status FROM education.mind_maps WHERE id = $1", map_id)


async def test_abandons_active_map_inactive_30_days(pool: asyncpg.Pool) -> None:
    """An active map whose newest node activity is >30 days old is abandoned."""
    map_id = await _create_map(pool, title="Stale Python", map_age_days=40)
    await _add_node(pool, map_id, age_days=45)

    abandoned = await mind_map_abandon_stale(pool)

    assert abandoned == [map_id]
    assert await _status(pool, map_id) == "abandoned"


async def test_recently_active_map_is_untouched(pool: asyncpg.Pool) -> None:
    """A map with at least one node updated within 30 days stays active."""
    map_id = await _create_map(pool, title="Active Calculus", map_age_days=40)
    # Old node, but one recent node => max(updated_at) is recent => not stale.
    await _add_node(pool, map_id, age_days=45)
    await _add_node(pool, map_id, age_days=2)

    abandoned = await mind_map_abandon_stale(pool)

    assert abandoned == []
    assert await _status(pool, map_id) == "active"


async def test_completed_map_not_subject_to_staleness(pool: asyncpg.Pool) -> None:
    """A completed map with old nodes is never modified by the staleness job."""
    map_id = await _create_map(pool, title="Done History", status="completed", map_age_days=80)
    await _add_node(pool, map_id, age_days=60)

    abandoned = await mind_map_abandon_stale(pool)

    assert abandoned == []
    assert await _status(pool, map_id) == "completed"


async def test_empty_active_map_uses_map_timestamp(pool: asyncpg.Pool) -> None:
    """A node-less active map falls back to its own updated_at.

    A freshly created empty map is not abandoned; an old empty one is.
    """
    fresh = await _create_map(pool, title="New Empty", map_age_days=1)
    old = await _create_map(pool, title="Old Empty", map_age_days=40)

    abandoned = await mind_map_abandon_stale(pool)

    assert abandoned == [old]
    assert await _status(pool, fresh) == "active"
    assert await _status(pool, old) == "abandoned"


async def test_registered_job_handler_transitions_stale_maps(pool: asyncpg.Pool) -> None:
    """The deterministic job registered under education runs the staleness query."""
    stale = await _create_map(pool, title="Stale Job Map", map_age_days=40)
    await _add_node(pool, stale, age_days=45)
    recent = await _create_map(pool, title="Recent Job Map", map_age_days=40)
    await _add_node(pool, recent, age_days=3)

    registry = get_deterministic_schedule_job_registry()
    handler = registry["education"]["mind_map_staleness_abandonment"]

    result = await handler(pool, None)

    assert result["abandoned_count"] == 1
    assert result["abandoned_ids"] == [stale]
    assert await _status(pool, stale) == "abandoned"
    assert await _status(pool, recent) == "active"
