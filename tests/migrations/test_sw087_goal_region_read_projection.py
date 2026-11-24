"""Real migrated-PG region reads: equivalence, lifecycle and bounded request work.

The old SQL is an intentional pre-fix control; the new statement comes from the
route. Plans run with the normal planner, never forced index settings.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import urlparse

import asyncpg
import pytest

from alembic import command
from butlers.api.routers.goals import region_summaries
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import (
    _bootstrap_migration_prerequisites,
    create_migrated_test_db,
    migration_bootstrap_db_url,
    migration_db_name,
)

pytestmark = pytest.mark.integration

_OLD_SELECT = """
    SELECT region, id, status, origin, created_at FROM (
        SELECT ge.region, g.id, g.status, g.origin, g.created_at,
               row_number() OVER (PARTITION BY ge.region
                                  ORDER BY g.created_at DESC, g.id DESC) AS rn
        FROM (SELECT DISTINCT e.goal_id, split_part(e.capability, '__', 1) AS region
              FROM switchboard.effects e) ge
        JOIN switchboard.goals g ON g.id = ge.goal_id
    ) ranked WHERE rn <= $1 ORDER BY region, created_at DESC, id DESC
"""


@pytest.fixture
def ledger_url(postgres_container):
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        ["core", "switchboard"],
        schemas={"core": "switchboard", "switchboard": "switchboard"},
        revisions={"switchboard": "sw_086"},  # pinned-revision: pre-projection backfill
    )


def _migrate(url, revision):
    config = _build_alembic_config(url, ["switchboard"], target_schema="switchboard")
    command.upgrade(config, revision)


async def _statement():
    pool = AsyncMock()
    pool.fetch.return_value = []
    db = MagicMock()
    db.pool.return_value = pool
    db.butler_names = []
    await region_summaries(db=db, goals_per_region=5, due_within_hours=72)
    sql, limit = pool.fetch.await_args.args
    assert limit == 5
    return sql


async def _seed(url, count=1000, repeats=1, sparse=False):
    conn = await asyncpg.connect(url)
    try:
        # Every five goals share a timestamp, exercising UUID tie ordering.
        await conn.execute(
            """
            INSERT INTO switchboard.goals
                (id, source_key, source_kind, source_ref, origin, admitted_exposed, exposed,
                 ceiling, ceiling_digest, catalog_digest, config_digest, chain_depth, created_at)
            SELECT md5('goal-' || n)::uuid, 'plan-' || n, 'message', n::text,
                   'owner', false, false, '[]'::jsonb,
                   repeat('0',64), repeat('0',64), repeat('0',64), 0,
                   '2026-01-01'::timestamptz + (n / 5) * interval '1 minute'
            FROM generate_series(1, $1::integer) n;
        """,
            count,
        )
        await conn.execute("""
            INSERT INTO switchboard.invocations
                (id, goal_id, depth, role, lead_epoch, status, grant_snapshot, grant_digest,
                 max_turns, max_tokens)
            SELECT md5('inv-' || source_ref)::uuid, id, 0, 'lead', 0, 'returned',
                   '[]'::jsonb, repeat('0',64), 1, 1
            FROM switchboard.goals
        """)
        await _effects(conn, repeats, sparse)
    finally:
        await conn.close()


async def _effects(conn, repeats=1, sparse=False):
    await conn.execute(
        """
        INSERT INTO switchboard.effects
            (id, goal_id, invocation_id, capability, args_digest, status)
        SELECT gen_random_uuid(), i.goal_id, i.id, r.prefix || '__synthetic',
               repeat('0',64), CASE r.ordinal WHEN 1 THEN 'requested'
                   WHEN 2 THEN 'failed' ELSE 'committed' END
        FROM switchboard.invocations i JOIN switchboard.goals g ON g.id = i.goal_id
        CROSS JOIN unnest(ARRAY['calendar','finance','home','health'])
            WITH ORDINALITY r(prefix, ordinal)
        CROSS JOIN generate_series(1, $1::integer)
        WHERE NOT $2::boolean OR r.ordinal = 1 OR g.source_ref::integer < r.ordinal
    """,
        repeats,
        sparse,
    )


def _nodes(node):
    yield node
    for child in node.get("Plans", []):
        yield from _nodes(child)


async def _plan(conn, sql):
    return json.loads(
        await conn.fetchval(
            f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {sql}",
            5,
        )
    )[0]["Plan"]


@pytest.mark.parametrize("sparse", [False, True], ids=["dense", "sparse"])
def test_region_statement_work_stays_bounded_as_history_grows(ledger_url, sparse, tmp_path):
    asyncio.run(_seed(ledger_url, sparse=sparse))
    _migrate(ledger_url, "sw_087")  # pinned-revision: owns the region read projection

    async def check():
        conn = await asyncpg.connect(ledger_url)
        try:
            sql = await _statement()
            evidence = []
            initial = await conn.fetch(sql, 5)
            for repeats in (0, 9):
                if repeats:
                    await _effects(conn, repeats, sparse)
                await conn.execute("""
                    VACUUM ANALYZE switchboard.effects;
                """)
                await conn.execute("VACUUM ANALYZE switchboard.goal_region_reads")
                await conn.execute("ANALYZE switchboard.goals")
                old, new = await _plan(conn, _OLD_SELECT), await _plan(conn, sql)
                assert await conn.fetch(sql, 5) == await conn.fetch(_OLD_SELECT, 5) == initial
                old_reads = sum(
                    n["Actual Rows"] * n["Actual Loops"]
                    for n in _nodes(old)
                    if n.get("Relation Name") == "effects"
                )
                new_reads = sum(
                    (
                        n["Actual Rows"]
                        + n.get("Rows Removed by Filter", 0)
                        + n.get("Rows Removed by Index Recheck", 0)
                    )
                    * n["Actual Loops"]
                    for n in _nodes(new)
                    if n.get("Relation Name") in {"effects", "goal_region_reads", "goals"}
                )
                effect_count = await conn.fetchval("SELECT count(*) FROM switchboard.effects")
                evidence.append(
                    dict(
                        effects=effect_count,
                        returned=len(initial),
                        old_reads=old_reads,
                        new_reads=new_reads,
                        old=old,
                        new=new,
                    )
                )
            (tmp_path / "region-plans.json").write_text(json.dumps(evidence, indent=2))
            print(
                "region plan work",
                [{k: v for k, v in e.items() if k not in {"old", "new"}} for e in evidence],
            )
            assert evidence[1]["effects"] == 10 * evidence[0]["effects"]
            assert [e["old_reads"] for e in evidence] == [e["effects"] for e in evidence]
            # Region-key seeks, capped distinct rows, and PK goal probes only.
            assert all(e["new_reads"] <= 4 * (2 * 5 + 2) for e in evidence), evidence
            assert all(n.get("Relation Name") != "effects" for n in _nodes(evidence[1]["new"]))
        finally:
            await conn.close()

    asyncio.run(check())


@pytest.mark.parametrize("direction", ["upgrade", "downgrade"])
def test_projection_migration_preserves_goal_first_writer(
    ledger_url, postgres_container, direction
):
    asyncio.run(_seed(ledger_url, count=1))
    config = _build_alembic_config(ledger_url, ["switchboard"], target_schema="switchboard")
    if direction == "downgrade":
        command.upgrade(config, "sw_087")  # pinned-revision: install before rollback
    parsed = urlparse(ledger_url)
    admin_url = migration_bootstrap_db_url(postgres_container, parsed.path[1:])
    asyncpg_admin_url = admin_url.replace("+psycopg2", "")

    def migrate():
        if direction == "upgrade":
            command.upgrade(config, "sw_087")  # pinned-revision: install alongside writer
        else:
            command.downgrade(config, "sw_086")  # pinned-revision: roll back alongside writer

    async def overlap():
        writer = await asyncpg.connect(asyncpg_admin_url)
        monitor = await asyncpg.connect(asyncpg_admin_url)
        migration = None
        try:
            await writer.execute("SET ROLE butler_switchboard_rw")
            async with writer.transaction():
                goal_id = await writer.fetchval("SELECT id FROM switchboard.goals FOR UPDATE")
                assert goal_id is not None
                migration = asyncio.create_task(asyncio.to_thread(migrate))
                async with asyncio.timeout(10):
                    while not await monitor.fetchval(  # noqa: ASYNC110 -- PostgreSQL lock barrier
                        "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                        "WHERE datname = current_database() AND $1 = ANY(pg_blocking_pids(pid)))",
                        writer.get_server_pid(),
                    ):
                        await asyncio.sleep(0.01)
                await writer.execute(
                    """
                    INSERT INTO switchboard.effects
                        (id, goal_id, invocation_id, capability, args_digest, status)
                    SELECT gen_random_uuid(), goal_id, id, 'overlap__read', repeat('0',64), 'committed'
                    FROM switchboard.invocations WHERE goal_id = $1
                    """,
                    goal_id,
                )
        finally:
            await writer.close()
            await monitor.close()
            if migration is not None:
                await migration

        conn = await asyncpg.connect(ledger_url)
        try:
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM switchboard.effects WHERE capability='overlap__read'"
                )
                == 1
            )
            if direction == "upgrade":
                assert (
                    await conn.fetchval(
                        "SELECT count(*) FROM switchboard.goal_region_reads WHERE region='overlap'"
                    )
                    == 1
                )
            else:
                assert (
                    await conn.fetchval("SELECT to_regclass('switchboard.goal_region_reads')")
                    is None
                )
        finally:
            await conn.close()

    asyncio.run(overlap())


def test_projection_backfill_runtime_atomicity_and_rebuild(ledger_url, postgres_container):
    _migrate(ledger_url, "sw_087")  # pinned-revision: empty read projection

    async def empty():
        conn = await asyncpg.connect(ledger_url)
        try:
            assert await conn.fetch(await _statement(), 5) == []
        finally:
            await conn.close()

    asyncio.run(empty())
    config = _build_alembic_config(ledger_url, ["switchboard"], target_schema="switchboard")
    command.downgrade(config, "sw_086")  # pinned-revision: install over existing history
    asyncio.run(_seed(ledger_url, 12, repeats=2))
    _migrate(ledger_url, "sw_087")  # pinned-revision: projection lifecycle
    parsed = urlparse(ledger_url)
    admin_url = migration_bootstrap_db_url(postgres_container, parsed.path[1:])
    _bootstrap_migration_prerequisites(admin_url, parsed.username)

    async def check():
        conn = await asyncpg.connect(ledger_url)
        admin = await asyncpg.connect(admin_url.replace("+psycopg2", ""))
        try:
            sql = await _statement()
            for limit in (1, 5, 25):
                assert await conn.fetch(sql, limit) == await conn.fetch(_OLD_SELECT, limit)
            assert await conn.fetchval("SELECT count(*) FROM switchboard.goal_region_reads") == 48
            await admin.execute("SET ROLE butler_switchboard_rw")
            # New regions on an OLD goal, including unregistered/empty prefixes.
            insert = """
                INSERT INTO switchboard.effects
                    (id, goal_id, invocation_id, capability, args_digest, status)
                SELECT gen_random_uuid(), goal_id, id, $1, repeat('0',64), 'committed'
                FROM switchboard.invocations WHERE id = md5('inv-' || $2::text)::uuid
            """
            await admin.execute(insert, "unregistered__read", "12")
            for capability in ("unregistered__read", "__read", "local", "calendar__second"):
                await admin.execute(insert, capability, "1")
            # Two transactions insert the same new pair. The second must wait
            # for the first's unique key and then keep one projection row.
            second = await asyncpg.connect(admin_url.replace("+psycopg2", ""))
            concurrent = None
            try:
                await second.execute("SET ROLE butler_switchboard_rw")
                async with admin.transaction():
                    await admin.execute(insert, "concurrent__read", "1")
                    concurrent = asyncio.create_task(
                        second.execute(insert, "concurrent__other", "1")
                    )
                    await admin.execute("RESET ROLE")
                    async with asyncio.timeout(5):
                        while not await admin.fetchval(  # noqa: ASYNC110 -- external PostgreSQL lock barrier
                            "SELECT wait_event_type='Lock' FROM pg_stat_activity WHERE pid=$1",
                            second.get_server_pid(),
                        ):
                            await asyncio.sleep(0.01)
                await concurrent
            finally:
                if concurrent is not None and not concurrent.done():
                    concurrent.cancel()
                    await asyncio.gather(concurrent, return_exceptions=True)
                await second.close()
            await admin.execute("SET ROLE butler_switchboard_rw")
            transaction = admin.transaction()
            await transaction.start()
            await admin.execute(insert, "rollback__read", "1")
            await transaction.rollback()
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM switchboard.goal_region_reads WHERE region='rollback'"
                )
                == 0
            )
            assert await conn.fetchval("SELECT count(*) FROM switchboard.goal_region_reads") == 53
            await admin.execute("UPDATE switchboard.goals SET status='running'")
            for limit in (1, 5, 25):
                assert await conn.fetch(sql, limit) == await conn.fetch(_OLD_SELECT, limit)
            assert {r["region"] for r in await conn.fetch(sql, 5)} == {
                "calendar",
                "finance",
                "home",
                "health",
                "unregistered",
                "",
                "local",
                "concurrent",
            }
            # Bootstrap DML regrants do not let other runtimes change the projection.
            await admin.execute("RESET ROLE")
            await admin.execute("GRANT USAGE ON SCHEMA switchboard TO butler_chronicler_rw")
            await admin.execute("""
                GRANT SELECT, INSERT, UPDATE, DELETE ON switchboard.goal_region_reads
                TO butler_chronicler_rw
            """)
            await admin.execute("SET ROLE butler_chronicler_rw")
            assert await admin.fetchval("SELECT count(*) FROM switchboard.goal_region_reads") == 0
            assert await admin.execute("DELETE FROM switchboard.goal_region_reads") == "DELETE 0"
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await admin.execute("""
                    INSERT INTO switchboard.goal_region_reads (region, goal_id, created_at)
                    VALUES ('forged', md5('goal-1')::uuid, now())
                """)
        finally:
            await admin.close()
            await conn.close()

    asyncio.run(check())
    config = _build_alembic_config(ledger_url, ["switchboard"], target_schema="switchboard")
    command.downgrade(config, "sw_086")  # pinned-revision: reversible derived read state

    async def absent_and_durable():
        conn = await asyncpg.connect(ledger_url)
        try:
            assert (
                await conn.fetchval("SELECT to_regclass('switchboard.goal_region_reads')") is None
            )
            assert await conn.fetchval("SELECT count(*) FROM switchboard.effects") == 103
            db = MagicMock()
            db.pool.return_value = conn
            db.butler_names = []
            result = await region_summaries(db=db, goals_per_region=25, due_within_hours=72)
            assert result.data.sources_degraded == ["goal_ledger"]
        finally:
            await conn.close()

    asyncio.run(absent_and_durable())
    _migrate(ledger_url, "sw_087")  # pinned-revision: rebuild from retained source

    async def rebuilt():
        conn = await asyncpg.connect(ledger_url)
        try:
            assert await conn.fetchval("SELECT count(*) FROM switchboard.goal_region_reads") == 53
            assert await conn.fetch(await _statement(), 25) == await conn.fetch(_OLD_SELECT, 25)
        finally:
            await conn.close()

    asyncio.run(rebuilt())
