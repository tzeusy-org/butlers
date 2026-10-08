"""Real-Postgres regression tests for the mind-map staleness-abandonment job.

Binding spec: ``openspec/specs/module-education-mind-map/spec.md``
  Requirement: *Mind map lifecycle — staleness abandonment*

The weekly job transitions an ``active`` mind map to ``abandoned`` once more
than 30 days have elapsed since any node activity (the maximum ``updated_at``
across the map's nodes). Maps with recent activity, ``completed`` maps, and
already-``abandoned`` maps are left untouched.

These tests run the real sweep and registered deterministic job handler against
migrated PostgreSQL under the ordinary runtime role. They also exercise database
content guards and durable rollback/readback, which canned rows cannot prove.
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from butlers.db import register_jsonb_codec
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
        schemas={"education": "education"},
    )


@pytest.fixture
async def pool(postgres_container, migrated_db_url: str):
    owner = await asyncpg.connect(migrated_db_url)
    await owner.execute("TRUNCATE TABLE education.mind_maps CASCADE")
    await owner.execute("DELETE FROM public.state WHERE key LIKE 'flow:%'")
    await owner.close()

    async def setup(connection):
        await connection.execute("SET ROLE butler_education_rw")
        await connection.execute("SET search_path TO education, public")

    p = await asyncpg.create_pool(
        migrated_db_url, min_size=1, max_size=3, init=register_jsonb_codec, setup=setup
    )
    yield p
    await p.close()


async def _create_map(
    pool: asyncpg.Pool,
    *,
    title: str,
    status: str = "draft",
    map_age_days: int = 0,
) -> str:
    """Insert a mind map whose created_at/updated_at are ``map_age_days`` old."""
    map_id = str(uuid.uuid4())
    created_at = datetime.now(tz=UTC) - timedelta(days=map_age_days)
    await pool.execute(
        """
        INSERT INTO education.mind_maps (id, title, status, created_at, updated_at)
        VALUES ($1, $2, $3, $4, $4)
        """,
        map_id,
        title,
        status,
        created_at,
    )
    return map_id


async def _add_node(pool: asyncpg.Pool, map_id: str, *, age_days: int) -> None:
    """Add a node to a map whose updated_at is ``age_days`` in the past."""
    updated_at = datetime.now(tz=UTC) - timedelta(days=age_days)
    await pool.execute(
        """
        INSERT INTO education.mind_map_nodes
            (mind_map_id, label, created_at, updated_at)
        VALUES ($1, $2, $3, $3)
        """,
        map_id,
        "concept",
        updated_at,
    )


async def _status(pool: asyncpg.Pool, map_id: str) -> str:
    return await pool.fetchval("SELECT status FROM education.mind_maps WHERE id = $1", map_id)


async def test_abandons_active_map_inactive_30_days(pool: asyncpg.Pool) -> None:
    """An active map whose newest node activity is >30 days old is abandoned."""
    map_id = await _create_map(pool, title="Stale Python", map_age_days=40)
    await _add_node(pool, map_id, age_days=45)
    await pool.execute("UPDATE education.mind_maps SET status='active' WHERE id=$1", map_id)

    abandoned = await mind_map_abandon_stale(pool)

    assert abandoned == [map_id]
    assert await _status(pool, map_id) == "abandoned"


async def test_recently_active_map_is_untouched(pool: asyncpg.Pool) -> None:
    """A map with at least one node updated within 30 days stays active."""
    map_id = await _create_map(pool, title="Active Calculus", map_age_days=40)
    # Old node, but one recent node => max(updated_at) is recent => not stale.
    await _add_node(pool, map_id, age_days=45)
    await _add_node(pool, map_id, age_days=2)
    await pool.execute("UPDATE education.mind_maps SET status='active' WHERE id=$1", map_id)

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


async def test_empty_draft_map_uses_creation_timestamp(pool: asyncpg.Pool) -> None:
    """Empty drafts use creation age; active empty maps are now forbidden.

    A freshly created empty map is not abandoned; an old empty one is.
    """
    fresh = await _create_map(pool, title="New Empty", map_age_days=0)
    old = await _create_map(pool, title="Old Empty", map_age_days=40)

    abandoned = await mind_map_abandon_stale(pool)

    assert abandoned == [old]
    assert await _status(pool, fresh) == "draft"
    assert await _status(pool, old) == "abandoned"


async def test_registered_job_handler_transitions_stale_maps(pool: asyncpg.Pool) -> None:
    """The deterministic job registered under education runs the staleness query."""
    stale = await _create_map(pool, title="Stale Job Map", map_age_days=40)
    await _add_node(pool, stale, age_days=45)
    await pool.execute("UPDATE education.mind_maps SET status='active' WHERE id=$1", stale)
    recent = await _create_map(pool, title="Recent Job Map", map_age_days=40)
    await _add_node(pool, recent, age_days=3)
    await pool.execute("UPDATE education.mind_maps SET status='active' WHERE id=$1", recent)

    registry = get_deterministic_schedule_job_registry()
    handler = registry["education"]["mind_map_staleness_abandonment"]

    result = await handler(pool, None)

    assert result["abandoned_count"] == 1
    assert result["abandoned_ids"] == [stale]
    assert await _status(pool, stale) == "abandoned"
    assert await _status(pool, recent) == "active"


@pytest.fixture(scope="module")
def legacy_db_url(postgres_container):
    # pinned-revision: populate legacy active-zero-node rows before our forward migration.
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "education"],
        schemas={"education": "education"},
        revisions={"education": "education_005"},
    )


async def test_lifecycle_migration_orders_backfill_audit_and_durable_guards(legacy_db_url):
    """Actual predecessor chain, ordinary migration login, installer replay and rollback."""
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine

    from butlers.migrations import run_migrations

    connection = await asyncpg.connect(legacy_db_url)
    try:
        empty = await connection.fetchval(
            "INSERT INTO education.mind_maps(title) VALUES('Legacy empty') RETURNING id"
        )
        populated = await connection.fetchval(
            "INSERT INTO education.mind_maps(title) VALUES('Legacy populated') RETURNING id"
        )
        await connection.execute(
            "INSERT INTO education.mind_map_nodes(mind_map_id,label) VALUES($1,'Concept')",
            populated,
        )
    finally:
        await connection.close()
    await run_migrations(legacy_db_url, chain="education", schema="education")
    path = (
        Path(__file__).resolve().parents[1]
        / "roster/education/migrations/006_mind_map_lifecycle.py"
    )
    spec = importlib.util.spec_from_file_location("education_lifecycle_under_test", path)
    revision = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(revision)
    engine = create_engine(legacy_db_url)
    try:
        # A real SQL replay, never a mocked op. Audit remains creation-wins.
        with engine.begin() as sql:
            with Operations.context(MigrationContext.configure(sql)):
                revision.upgrade()
        connection = await asyncpg.connect(legacy_db_url)
        try:
            assert (
                await connection.fetchval(
                    "SELECT status FROM education.mind_maps WHERE id=$1", empty
                )
                == "abandoned"
            )
            assert (
                await connection.fetchval(
                    "SELECT status FROM education.mind_maps WHERE id=$1", populated
                )
                == "active"
            )
            audits = await connection.fetch(
                "SELECT actor,target,note FROM public.audit_log WHERE action='education.mind_map.legacy_empty_abandoned'"
            )
            assert len(audits) == 1
            assert dict(audits[0]) == {
                "actor": "system:education_006",
                "target": str(empty),
                "note": "legacy active map contained no concepts",
            }
            draft = await connection.fetchval(
                "INSERT INTO education.mind_maps(title) VALUES('Draft') RETURNING id"
            )
            assert (
                await connection.fetchval(
                    "SELECT status FROM education.mind_maps WHERE id=$1", draft
                )
                == "draft"
            )
        finally:
            await connection.close()
        with pytest.raises(Exception, match="Settle draft curricula"):
            with engine.begin() as sql:
                with Operations.context(MigrationContext.configure(sql)):
                    revision.downgrade()
        # Refused downgrade leaves guards/default/history intact; a settled downgrade
        # removes enforcement without reactivating abandoned rows or deleting audits.
        connection = await asyncpg.connect(legacy_db_url)
        assert (
            await connection.fetchval("SELECT status FROM education.mind_maps WHERE id=$1", draft)
            == "draft"
        )
        assert (
            await connection.fetchval(
                "SELECT count(*) FROM pg_trigger WHERE tgrelid='education.mind_maps'::regclass AND tgname='mind_maps_active_content' AND NOT tgisinternal"
            )
            == 1
        )
        with pytest.raises(asyncpg.CheckViolationError):
            await connection.execute(
                "INSERT INTO education.mind_maps(title,status) VALUES('Still protected','active')"
            )
        default_witness = await connection.fetchval(
            "INSERT INTO education.mind_maps(title) VALUES('Still draft') RETURNING status"
        )
        assert default_witness == "draft"
        await connection.execute("DELETE FROM education.mind_maps WHERE title='Still draft'")
        await connection.execute(
            "UPDATE education.mind_maps SET status='abandoned' WHERE id=$1", draft
        )
        await connection.close()
        with engine.begin() as sql:
            with Operations.context(MigrationContext.configure(sql)):
                revision.downgrade()
        connection = await asyncpg.connect(legacy_db_url)
        try:
            assert (
                await connection.fetchval(
                    "SELECT status FROM education.mind_maps WHERE id=$1", empty
                )
                == "abandoned"
            )
            assert (
                await connection.fetchval(
                    "SELECT count(*) FROM public.audit_log WHERE target=$1", str(empty)
                )
                == 1
            )
        finally:
            await connection.close()
        with engine.begin() as sql:
            with Operations.context(MigrationContext.configure(sql)):
                revision.upgrade()
    finally:
        engine.dispose()


async def test_content_guards_serialize_activation_removal_and_allow_cascade(pool):
    import asyncio

    from butlers.tools.education.mind_maps import mind_map_update_status

    assert await pool.fetchval("SELECT current_user") == "butler_education_rw"
    map_id = await _create_map(pool, title="Guarded draft")
    for query in (
        "INSERT INTO education.mind_maps(title,status) VALUES('Direct empty','active')",
        "UPDATE education.mind_maps SET status='active' WHERE id=$1",
    ):
        with pytest.raises(asyncpg.CheckViolationError):
            await pool.execute(query, *([map_id] if "$1" in query else []))
    assert await _status(pool, map_id) == "draft"
    await _add_node(pool, map_id, age_days=0)
    await mind_map_update_status(pool, map_id, "active")
    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute("DELETE FROM education.mind_map_nodes WHERE mind_map_id=$1", map_id)
    assert (
        await pool.fetchval(
            "SELECT count(*) FROM education.mind_map_nodes WHERE mind_map_id=$1", map_id
        )
        == 1
    )
    assert await _status(pool, map_id) == "active"
    receiving = await _create_map(pool, title="Other draft")
    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute(
            "UPDATE education.mind_map_nodes SET mind_map_id=$2 WHERE mind_map_id=$1",
            map_id,
            receiving,
        )
    assert (
        await pool.fetchval(
            "SELECT count(*) FROM education.mind_map_nodes WHERE mind_map_id=$1", map_id
        )
        == 1
    )
    assert (
        await pool.fetchval(
            "SELECT count(*) FROM education.mind_map_nodes WHERE mind_map_id=$1", receiving
        )
        == 0
    )
    await _add_node(pool, map_id, age_days=0)
    nodes = await pool.fetch(
        "SELECT id FROM education.mind_map_nodes WHERE mind_map_id=$1 ORDER BY id", map_id
    )
    async with pool.acquire() as first, pool.acquire() as second:
        async with first.transaction():
            await first.execute("DELETE FROM education.mind_map_nodes WHERE id=$1", nodes[0]["id"])
            deleting = asyncio.create_task(
                second.execute("DELETE FROM education.mind_map_nodes WHERE id=$1", nodes[1]["id"])
            )
            await asyncio.sleep(0.05)
            assert not deleting.done()  # The second removal must wait on the actual parent writer.
        with pytest.raises(asyncpg.CheckViolationError):
            await asyncio.wait_for(deleting, timeout=5)
    assert (
        await pool.fetchval(
            "SELECT count(*) FROM education.mind_map_nodes WHERE mind_map_id=$1", map_id
        )
        == 1
    )
    await mind_map_update_status(pool, map_id, "abandoned")
    async with pool.acquire() as first, pool.acquire() as second:
        async with first.transaction():
            await first.execute("DELETE FROM education.mind_map_nodes WHERE mind_map_id=$1", map_id)
            activating = asyncio.create_task(mind_map_update_status(second, map_id, "active"))
            await asyncio.sleep(0.05)
            assert not activating.done()
        with pytest.raises(ValueError, match="no concepts"):
            await asyncio.wait_for(activating, timeout=5)
    assert await _status(pool, map_id) == "abandoned"
    await _add_node(pool, map_id, age_days=0)
    async with pool.acquire() as first, pool.acquire() as second:
        async with first.transaction():
            await mind_map_update_status(first, map_id, "active")
            removing = asyncio.create_task(
                second.execute("DELETE FROM education.mind_map_nodes WHERE mind_map_id=$1", map_id)
            )
            await asyncio.sleep(0.05)
            assert not removing.done()
        with pytest.raises(asyncpg.CheckViolationError):
            await asyncio.wait_for(removing, timeout=5)
    assert await _status(pool, map_id) == "active"
    assert (
        await pool.fetchval(
            "SELECT count(*) FROM education.mind_map_nodes WHERE mind_map_id=$1", map_id
        )
        == 1
    )
    # Parent cascade is intentionally exempt, even for an active populated map.
    await pool.execute("DELETE FROM education.mind_maps WHERE id=$1", map_id)
    assert (
        await pool.fetchval(
            "SELECT count(*) FROM education.mind_map_nodes WHERE mind_map_id=$1", map_id
        )
        == 0
    )


async def test_atomic_flow_and_populated_curriculum_on_migrated_runtime(pool, monkeypatch):
    from butlers.tools.education import teaching_flows
    from butlers.tools.education.curriculum import curriculum_generate
    from butlers.tools.education.mind_map_nodes import mind_map_node_create

    real_state_set = teaching_flows.state_set
    real_create = teaching_flows.mind_map_create

    async def failed_state(*args):
        await real_state_set(*args)
        raise RuntimeError("controlled state refusal")

    monkeypatch.setattr(teaching_flows, "state_set", failed_state)
    with pytest.raises(RuntimeError, match="state refusal"):
        await teaching_flows.teaching_flow_start(pool, "State rollback", goal="Stored atomically")
    # Separate acquisition sees neither half of the refused commit.
    assert (
        await pool.fetchval("SELECT count(*) FROM education.mind_maps WHERE title='State rollback'")
        == 0
    )
    assert await pool.fetchval("SELECT count(*) FROM state WHERE key LIKE 'flow:%'") == 0
    monkeypatch.setattr(teaching_flows, "state_set", real_state_set)

    async def failed_map(*args):
        await real_create(*args)
        raise RuntimeError("controlled map refusal")

    monkeypatch.setattr(teaching_flows, "mind_map_create", failed_map)
    with pytest.raises(RuntimeError, match="map refusal"):
        await teaching_flows.teaching_flow_start(pool, "Map rollback")
    assert await pool.fetchval("SELECT count(*) FROM state WHERE key LIKE 'flow:%'") == 0
    assert (
        await pool.fetchval("SELECT count(*) FROM education.mind_maps WHERE title='Map rollback'")
        == 0
    )
    monkeypatch.setattr(teaching_flows, "mind_map_create", real_create)
    flow = await teaching_flows.teaching_flow_start(
        pool, "Populated curriculum", goal="Durable goal"
    )
    map_id = flow["mind_map_id"]
    assert flow["status"] == "diagnosing"
    assert await _status(pool, map_id) == "draft"
    assert (await pool.fetchval("SELECT metadata FROM education.mind_maps WHERE id=$1", map_id))[
        "goal"
    ] == "Durable goal"
    with pytest.raises(ValueError, match="no nodes"):
        await curriculum_generate(pool, map_id)
    assert await _status(pool, map_id) == "draft"
    await mind_map_node_create(pool, map_id, "Variables", depth=0, effort_minutes=10)
    summary = await curriculum_generate(pool, map_id)
    assert summary["status"] == "active" and summary["node_count"] == 1
    assert await _status(pool, map_id) == "active"
    assert (
        await pool.fetchval(
            "SELECT sequence FROM education.mind_map_nodes WHERE mind_map_id=$1", map_id
        )
        == 1
    )
    stored = await teaching_flows.teaching_flow_get(pool, map_id)
    assert stored["status"] == "diagnosing"

    # A refused state CAS after the real map write rolls back both persisted halves.
    real_cas = teaching_flows.state_compare_and_set

    async def failed_cas(*args):
        await real_cas(*args)
        raise RuntimeError("controlled CAS refusal")

    monkeypatch.setattr(teaching_flows, "state_compare_and_set", failed_cas)
    with pytest.raises(RuntimeError, match="CAS refusal"):
        await teaching_flows.teaching_flow_abandon(pool, map_id)
    assert await _status(pool, map_id) == "active"
    assert (await teaching_flows.teaching_flow_get(pool, map_id))["status"] == "diagnosing"
    monkeypatch.setattr(teaching_flows, "state_compare_and_set", real_cas)
    cleanup_attempts = []

    async def failed_cleanup(name):
        cleanup_attempts.append(name)
        raise RuntimeError("controlled schedule refusal")

    node_id = await pool.fetchval(
        "SELECT id FROM education.mind_map_nodes WHERE mind_map_id=$1", map_id
    )
    schedule = f"review-{node_id}-rep1"
    await pool.execute(
        "INSERT INTO scheduled_tasks(name,cron,prompt) VALUES($1,'0 4 * * *','Controlled review')",
        schedule,
    )
    await teaching_flows.teaching_flow_abandon(pool, map_id, schedule_delete=failed_cleanup)
    settled = await pool.fetchrow("SELECT value,version FROM state WHERE key=$1", f"flow:{map_id}")
    assert await _status(pool, map_id) == "abandoned"
    assert settled["value"]["status"] == "abandoned"
    assert cleanup_attempts == [schedule]
    assert await pool.fetchval("SELECT count(*) FROM scheduled_tasks WHERE name=$1", schedule) == 1
    await mind_map_abandon_stale(pool)
    assert await pool.fetchval("SELECT count(*) FROM scheduled_tasks WHERE name=$1", schedule) == 0
    assert dict(
        await pool.fetchrow("SELECT value,version FROM state WHERE key=$1", f"flow:{map_id}")
    ) == dict(settled)
