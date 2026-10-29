"""Option D: real PostgreSQL fencing, retention, privacy and reversible DDL.

REQ-system-overview-page-008. Authored for the hosted native lane;
local collection is not a native outcome.
"""

from __future__ import annotations

import asyncio
import shutil

import asyncpg
import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from alembic import command
from butlers.core.job_health import JOB_SPECS, REASONS, JobHealthStore
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import (
    create_migrated_test_db,
    init_db_sql_for_dbapi,
    migration_bootstrap_db_url,
    migration_db_name,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]


@pytest.mark.asyncio(loop_scope="session")
async def test_job_health_migration_retention_fencing_privacy_and_roundtrip(postgres_container):
    name = migration_db_name()
    url = await asyncio.to_thread(
        create_migrated_test_db,
        postgres_container,
        name,
        chains=["core"],
        revisions={"core": "core_286"},
    )
    owner = make_url(url).username
    assert owner is not None

    # Genuine bootstrap replay re-widens public DML. Forced RLS must survive it.
    def replay_bootstrap():
        admin = create_engine(
            migration_bootstrap_db_url(postgres_container, name), isolation_level="AUTOCOMMIT"
        )
        try:
            raw = admin.raw_connection()
            try:
                dbapi = raw.driver_connection
                dbapi.autocommit = True
                with dbapi.cursor() as cursor:
                    cursor.execute(
                        "SELECT set_config('butlers.connecting_user', %s, false)", (owner,)
                    )
                    cursor.execute(init_db_sql_for_dbapi())
            finally:
                raw.close()
        finally:
            admin.dispose()

    await asyncio.to_thread(replay_bootstrap)

    pool = await asyncpg.create_pool(
        url.replace("postgresql+psycopg2://", "postgresql://"), min_size=1, max_size=2
    )
    try:
        old = JobHealthStore(lambda: pool)
        recorder = old.recorder("model_verify", "periodic", 10)
        await recorder.starting()
        await recorder.tick_failed("https://private-token-sentinel/traceback")
        await recorder.tick_ok()
        row = await pool.fetchrow(
            "SELECT * FROM public.dashboard_job_health WHERE job_name='model_verify'"
        )
        assert row["state"] == "ok"
        assert row["error_count"] == 1 and row["consecutive_failures"] == 0
        assert row["last_success_at"] is not None
        assert not old.unavailable_jobs
        # Overflow both bounds, including a disabled job which has no own ticks.
        await old.recorder("external_deadman", "periodic", 600).disabled()
        await pool.execute(
            """INSERT INTO public.dashboard_job_transitions
            (job_name,process_id,state,reason,occurred_at)
            SELECT 'external_deadman',$1,'ok','tick_ok',clock_timestamp()-interval '31 days'
            FROM generate_series(1,2)""",
            old.process_id,
        )
        await pool.execute(
            """INSERT INTO public.dashboard_job_transitions
            (job_name,process_id,state,reason)
            SELECT 'model_verify',$1,'ok','tick_ok' FROM generate_series(1,105)""",
            old.process_id,
        )
        await recorder.tick_ok()
        assert (
            await pool.fetchval(
                "SELECT count(*) FROM public.dashboard_job_transitions WHERE job_name='model_verify'"
            )
            == 100
        )
        assert (
            await pool.fetchval(
                "SELECT count(*) FROM public.dashboard_job_transitions WHERE occurred_at<clock_timestamp()-interval '30 days'"
            )
            == 0
        )
        # Bootstrap grants all public sequence rights to runtimes. UUID IDs
        # deliberately introduce no mutable counter outside row security.
        assert (
            await pool.fetchval(
                "SELECT pg_get_serial_sequence('public.dashboard_job_transitions','id')"
            )
            is None
        )
        assert (
            await pool.fetchval(
                "SELECT pg_typeof(id)::text FROM public.dashboard_job_transitions LIMIT 1"
            )
            == "uuid"
        )
        reasons = await pool.fetch("SELECT reason FROM public.dashboard_job_transitions")
        assert {row["reason"] for row in reasons} <= REASONS
        assert "private-token-sentinel" not in repr(reasons)
        with pytest.raises(asyncpg.CheckViolationError):
            await pool.execute(
                "UPDATE public.dashboard_job_health SET reason='private-token-sentinel' WHERE job_name='model_verify'"
            )
        # A new boot replaces old success with starting; old writers stay fenced.
        new = JobHealthStore(lambda: pool)
        await new.recorder("model_verify", "periodic", 10).starting()
        before = await pool.fetchrow(
            "SELECT * FROM public.dashboard_job_health WHERE job_name='model_verify'"
        )
        await recorder.tick_failed()
        after = await pool.fetchrow(
            "SELECT * FROM public.dashboard_job_health WHERE job_name='model_verify'"
        )
        assert dict(after) == dict(before)
        assert after["process_id"] == new.process_id and after["last_success_at"] is None
        old_view = {x["name"]: x for x in (await old.read())["jobs"]}
        assert set(old_view) == set(JOB_SPECS)
        assert old_view["model_verify"]["state"] == "unknown"
        new_view = {x["name"]: x for x in (await new.read())["jobs"]}
        assert new_view["model_verify"]["state"] == "starting"
        assert new_view["model_verify"]["last_success_at"] is None
        # Runtime/public regrants cannot expose or alter API-owned health.
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute("SET LOCAL ROLE butler_general_rw")
            assert await conn.fetchval("SELECT count(*) FROM public.dashboard_job_health") == 0
            assert await conn.fetchval("SELECT count(*) FROM public.dashboard_job_transitions") == 0
            assert (
                await conn.execute("UPDATE public.dashboard_job_health SET state='ok'")
                == "UPDATE 0"
            )
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with conn.transaction():
                    await conn.execute(
                        """INSERT INTO public.dashboard_job_health
                        (job_name,job_class,process_id,process_started_at,interval_s,grace_s)
                        VALUES ('forged','periodic',$1,clock_timestamp(),10,5)""",
                        old.process_id,
                    )
    finally:
        await pool.close()

    # Real Alembic edges and stamp readbacks, not captured SQL substituted for
    # the migration runner. All runtime connections are closed before DDL.
    config = _build_alembic_config(url, chains=["core"])
    for direction, target, revision in (
        ("downgrade", "core_282", "core_282"),
        ("upgrade", "core@core_286", "core_286"),
    ):
        await asyncio.to_thread(getattr(command, direction), config, target)
        conn = await asyncpg.connect(url.replace("postgresql+psycopg2://", "postgresql://"))
        try:
            exists = await conn.fetchval(
                "SELECT to_regclass('public.dashboard_job_health') IS NOT NULL"
            )
            assert exists is (direction == "upgrade")
            history = await conn.fetchval(
                "SELECT to_regclass('public.dashboard_job_transitions') IS NOT NULL"
            )
            assert history is (direction == "upgrade")
            assert {
                row["version_num"]
                for row in await conn.fetch("SELECT version_num FROM public.alembic_version")
            } == {revision}
        finally:
            await conn.close()
