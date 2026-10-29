"""Option D: real PostgreSQL fencing, retention, privacy and reversible DDL.

REQ-system-overview-page-008. Authored for the hosted native lane;
local collection is not a native outcome.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

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
from tests.integration.test_dashboard_stepup_restore import client

_REPO_ROOT = Path(__file__).resolve().parents[2]

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

    # Reproduce the existing shared roundtrip consumer: trusted bootstrap,
    # rather than the ordinary API login, performs the actual recreation.
    bootstrap_config = _build_alembic_config(
        migration_bootstrap_db_url(postgres_container, name), chains=["core"]
    )
    await asyncio.to_thread(command.downgrade, bootstrap_config, "core_282")
    await asyncio.to_thread(command.upgrade, bootstrap_config, "core@core_286")
    conn = await asyncpg.connect(url.replace("postgresql+psycopg2://", "postgresql://"))
    try:
        posture = await conn.fetch(
            """SELECT c.relname,pg_get_userbyid(c.relowner) AS owner,
                c.relrowsecurity,c.relforcerowsecurity,p.polroles,
                pg_get_expr(p.polqual,c.oid) AS using_expression
                FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                JOIN pg_policy p ON p.polrelid=c.oid
                WHERE n.nspname='public'
                AND c.relname=ANY($1::text[]) ORDER BY c.relname""",
            ["dashboard_job_health", "dashboard_job_transitions"],
        )
        assert len(posture) == 2
        owner_oid = await conn.fetchval("SELECT oid FROM pg_roles WHERE rolname=$1", owner)
        assert all(row["owner"] == owner for row in posture)
        assert all(row["relrowsecurity"] and row["relforcerowsecurity"] for row in posture)
        assert all(row["polroles"] == [owner_oid] for row in posture)
        assert all("CURRENT_USER" in row["using_expression"] for row in posture)
        # Metadata presence is privilege filtered. Prove ordinary DML, not
        # only superuser catalog existence or the Alembic version string.
        assert (
            await conn.fetchval(
                """SELECT count(DISTINCT table_name) FROM information_schema.columns
            WHERE table_schema='public' AND table_name=ANY($1::text[])""",
                ["dashboard_job_health", "dashboard_job_transitions"],
            )
            == 2
        )
    finally:
        await conn.close()
    await asyncio.to_thread(replay_bootstrap)
    pool = await asyncpg.create_pool(url.replace("postgresql+psycopg2://", "postgresql://"))
    try:
        store = JobHealthStore(lambda: pool)
        await store.recorder("model_verify", "periodic", 10).starting()
        await store.recorder("model_verify", "periodic", 10).tick_ok()
        assert not store.unavailable_jobs
        assert await pool.fetchval("SELECT count(*) FROM public.dashboard_job_health") == 1
        async with pool.acquire() as runtime, runtime.transaction():
            await runtime.execute("SET LOCAL ROLE butler_general_rw")
            assert await runtime.fetchval("SELECT count(*) FROM public.dashboard_job_health") == 0
            assert (
                await runtime.execute("UPDATE public.dashboard_job_health SET state='ok'")
                == "UPDATE 0"
            )
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with runtime.transaction():
                    await runtime.execute(
                        """INSERT INTO public.dashboard_job_health
                        (job_name,job_class,process_id,process_started_at,interval_s,grace_s)
                        VALUES ('forged','periodic',$1,clock_timestamp(),10,5)""",
                        store.process_id,
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


@pytest.mark.asyncio(loop_scope="session")
async def test_job_health_ordinary_backup_preserves_typed_history_and_fresh_process(
    postgres_container, tmp_path
):
    """Real ordinary backup/privileged restore; no native execution in author prep."""
    name = migration_db_name()
    url = await asyncio.to_thread(
        create_migrated_test_db,
        postgres_container,
        name,
        chains=["core"],
        revisions={"core": "core_286"},
    )
    source = make_url(url)
    root = _REPO_ROOT
    pool = await asyncpg.create_pool(url.replace("postgresql+psycopg2://", "postgresql://"))
    target = migration_db_name()
    restored_pool = None
    try:
        store = JobHealthStore(lambda: pool)
        recorder = store.recorder("model_verify", "periodic", 10)
        await recorder.starting()
        await recorder.tick_failed()
        await recorder.tick_ok()
        assert not store.unavailable_jobs
        before = {
            table: [
                row["value"]
                for row in await pool.fetch(
                    f"SELECT to_jsonb(t)::text AS value FROM public.{table} t ORDER BY 1"
                )
            ]
            for table in ("dashboard_job_health", "dashboard_job_transitions")
        }
        assert all(before.values())
        # Reuse the existing bounded, named disposable PostgreSQL client.
        backup = await asyncio.to_thread(
            client,
            postgres_container,
            'export POSTGRES_PASSWORD="$SYNTHETIC_PASSWORD"; sh /backup/pg_dump.sh',
            source.password,
            mounts=(
                f"{root / 'deploy/backup/pg_dump.sh'}:/backup/pg_dump.sh:ro",
                f"{tmp_path}:/backups",
            ),
            POSTGRES_HOST="host.docker.internal",
            POSTGRES_PORT=str(postgres_container.get_exposed_port(5432)),
            POSTGRES_USER=source.username,
            POSTGRES_DB=name,
            BACKUP_DIR="/backups",
        )
        assert backup.returncode == 0, "ordinary health-history backup must succeed"
        artifacts = list(tmp_path.glob("butlers_*.sql.gz"))
        assert len(artifacts) == 1
        admin = make_url(migration_bootstrap_db_url(postgres_container, name))
        restored = await asyncio.to_thread(
            client,
            postgres_container,
            f'export POSTGRES_PASSWORD="$SYNTHETIC_PASSWORD"; bash /restore.sh /backup.sql.gz --host host.docker.internal --port {postgres_container.get_exposed_port(5432)} --user {admin.username} --target-db {target} --drop-existing',
            admin.password,
            mounts=(
                f"{root / 'scripts/pg_restore.sh'}:/restore.sh:ro",
                f"{artifacts[0]}:/backup.sql.gz:ro",
            ),
        )
        assert restored.returncode == 0, "health-history restore must preserve owner and rows"
        target_url = source.set(database=target).render_as_string(hide_password=False)
        restored_pool = await asyncpg.create_pool(
            target_url.replace("postgresql+psycopg2://", "postgresql://")
        )
        after = {
            table: [
                row["value"]
                for row in await restored_pool.fetch(
                    f"SELECT to_jsonb(t)::text AS value FROM public.{table} t ORDER BY 1"
                )
            ]
            for table in before
        }
        assert after == before
        assert await restored_pool.fetchval(
            "SELECT relforcerowsecurity FROM pg_class WHERE oid='public.dashboard_job_health'::regclass"
        )
        assert (
            await restored_pool.fetchval(
                "SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid='public.dashboard_job_health'::regclass"
            )
            == source.username
        )
        fresh = JobHealthStore(lambda: restored_pool)
        fresh_view = {row["name"]: row for row in (await fresh.read())["jobs"]}
        assert fresh_view["model_verify"]["state"] == "unknown"
        assert fresh_view["model_verify"]["transitions"]
        await fresh.recorder("model_verify", "periodic", 10).starting()
        view = {row["name"]: row for row in (await fresh.read())["jobs"]}
        assert view["model_verify"]["state"] == "starting"
        assert view["model_verify"]["last_success_at"] is None
        async with restored_pool.acquire() as conn, conn.transaction():
            await conn.execute("SET LOCAL ROLE butler_general_rw")
            # If no post-restore bootstrap has regranted table DML, table ACL
            # refusal is also denial. After regrant, FORCE RLS still hides rows.
            if await conn.fetchval(
                "SELECT has_table_privilege(current_user,'public.dashboard_job_health','SELECT')"
            ):
                assert await conn.fetchval("SELECT count(*) FROM public.dashboard_job_health") == 0
            else:
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    async with conn.transaction():
                        await conn.fetchval("SELECT count(*) FROM public.dashboard_job_health")
        # An extra PUBLIC policy must refuse the real export before publishing,
        # never authorize a newly readable or filtered diagnostic stream.
        await pool.execute(
            "CREATE POLICY health_backup_poison ON public.dashboard_job_health FOR SELECT TO PUBLIC USING (true)"
        )
        poisoned = tmp_path / "poisoned"
        poisoned.mkdir()
        try:
            refused = await asyncio.to_thread(
                client,
                postgres_container,
                'export POSTGRES_PASSWORD="$SYNTHETIC_PASSWORD"; sh /backup/pg_dump.sh',
                source.password,
                mounts=(
                    f"{root / 'deploy/backup/pg_dump.sh'}:/backup/pg_dump.sh:ro",
                    f"{poisoned}:/backups",
                ),
                POSTGRES_HOST="host.docker.internal",
                POSTGRES_PORT=str(postgres_container.get_exposed_port(5432)),
                POSTGRES_USER=source.username,
                POSTGRES_DB=name,
                BACKUP_DIR="/backups",
            )
            assert refused.returncode != 0
            assert not list(poisoned.glob("butlers_*.sql.gz"))
        finally:
            await pool.execute("DROP POLICY health_backup_poison ON public.dashboard_job_health")
    finally:
        if restored_pool is not None:
            await restored_pool.close()
        await pool.close()
        admin_url = migration_bootstrap_db_url(postgres_container, name)
        control = await asyncpg.connect(
            admin_url.replace("postgresql+psycopg2://", "postgresql://")
        )
        try:
            await control.execute(f'DROP DATABASE IF EXISTS "{target}" WITH (FORCE)')
        finally:
            await control.close()
