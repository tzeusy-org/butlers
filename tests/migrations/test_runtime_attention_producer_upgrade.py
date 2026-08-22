"""core_199 runtime-attention producer activation and rollback contracts.

REQ-model-catalog-001; REQ-runtime-attention-outbox-001;
REQ-dashboard-spend-dashboard-001; REQ-database-security-007.
"""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio
from sqlalchemy import create_engine, text

from alembic import command
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import (
    create_migrated_test_db,
    create_migration_db,
    migration_bootstrap_db_url,
    migration_db_name,
)


@pytest.fixture(scope="module")
def migrated_v2_db_url(postgres_container) -> str:
    return create_migrated_test_db(postgres_container, migration_db_name(), chains=["core"])


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def migrated_v2_pool(migrated_v2_db_url: str) -> asyncpg.Pool:
    pool = await asyncpg.create_pool(
        migrated_v2_db_url.replace("postgresql+psycopg2://", "postgresql://", 1),
        min_size=1,
        max_size=2,
    )
    yield pool
    await pool.close()


def test_core_199_is_the_versioned_runtime_attention_upgrade() -> None:
    migration_path = (
        Path(__file__).resolve().parents[2]
        / "alembic/versions/core/core_199_runtime_attention_producer_v2.py"
    )
    spec = importlib.util.spec_from_file_location(
        "core_199_runtime_attention_producer_v2", migration_path
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    assert migration.revision == "core_199"
    assert migration.down_revision == "core_198"


def test_upgrade_requires_bootstrap_installed_v2_and_revokes_upgrade_authority(
    postgres_container,
) -> None:
    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    core_config = _build_alembic_config(db_url, chains=["core"])

    command.upgrade(core_config, "core_198")
    command.upgrade(core_config, "core@head")

    engine = create_engine(db_url)
    try:
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT version_num FROM public.alembic_version")
                ).scalar_one()
                == "core_200"
            )
            assert connection.execute(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM pg_trigger
                        WHERE tgrelid = 'public.model_dispatch_attempts'::regclass
                          AND tgname = 'runtime_attention_legacy_producer_fence_trigger'
                          AND NOT tgisinternal
                    )
                    """
                )
            ).scalar_one()
            assert not connection.execute(
                text(
                    """
                    SELECT has_function_privilege(
                        current_user,
                        upgrader.oid,
                        'EXECUTE'
                    )
                    FROM pg_proc AS upgrader
                    JOIN pg_namespace AS admin_schema
                      ON admin_schema.oid = upgrader.pronamespace
                    WHERE admin_schema.nspname = 'runtime_attention_admin'
                      AND upgrader.proname = 'upgrade_producers_v2'
                      AND upgrader.pronargs = 0
                    """
                )
            ).scalar_one()
            assert not connection.execute(
                text(
                    "SELECT has_schema_privilege(current_user, 'runtime_attention_admin', 'USAGE')"
                )
            ).scalar_one()
    finally:
        engine.dispose()


@pytest.mark.asyncio(loop_scope="module")
async def test_legacy_runtime_writers_are_fenced_before_direct_helpers_can_send(
    migrated_v2_pool: asyncpg.Pool,
) -> None:
    pool = migrated_v2_pool
    async with pool.acquire() as admin_connection:
        await admin_connection.execute(
            "TRUNCATE public.audit_log, public.model_dispatch_attempts CASCADE"
        )
    try:
        entry_id = uuid.uuid4()
        await pool.execute(
            """
            INSERT INTO public.model_catalog (id, alias, runtime_type, model_id)
            VALUES ($1, 'legacy-cutover', 'codex', 'legacy-cutover-model')
            """,
            entry_id,
        )
        async with pool.acquire() as connection:
            await connection.execute('SET ROLE "butler_general_rw"')
            try:
                await connection.execute(
                    """
                    INSERT INTO public.model_dispatch_attempts (
                        catalog_entry_id, butler, outcome, failure_reason
                    ) VALUES ($1, 'general', 'runtime_failure', 'legacy writer')
                    """,
                    entry_id,
                )
                await connection.execute(
                    """
                    INSERT INTO public.model_dispatch_attempts (
                        catalog_entry_id, butler, outcome, failure_reason
                    ) VALUES (
                        $1, 'general', 'quota_skip',
                        'Monthly spend ceiling reached: legacy writer'
                    )
                    """,
                    entry_id,
                )
            finally:
                await connection.execute("RESET ROLE")

        rows = await pool.fetch(
            """
            SELECT actor, action, target, note
            FROM public.audit_log
            WHERE actor = 'runtime_attention_cutover_fence'
            ORDER BY id
            """
        )
        assert [row["action"] for row in rows] == [
            "model_breaker_open_notified",
            "ceiling_halt_notified",
        ]
        assert rows[0]["target"] == f"model_breaker:{entry_id}"
        assert rows[1]["target"] == "ceiling_halt"
    finally:
        await pool.execute("TRUNCATE public.audit_log, public.model_dispatch_attempts CASCADE")


def test_bootstrap_rollback_disables_producers_without_restoring_direct_paths(
    postgres_container,
) -> None:
    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    bootstrap_url = migration_bootstrap_db_url(postgres_container, db_name)
    command.upgrade(_build_alembic_config(db_url, chains=["core"]), "core@head")

    command.downgrade(_build_alembic_config(bootstrap_url, chains=["core"]), "core_198")

    engine = create_engine(bootstrap_url)
    try:
        with engine.begin() as connection:
            enabled = connection.execute(
                text(
                    """
                    SELECT producers_enabled
                    FROM runtime_attention_admin.bootstrap_configuration
                    WHERE singleton
                    """
                )
            ).scalar_one()
            assert enabled is False
            entry_id = uuid.uuid4()
            connection.execute(
                text(
                    """
                    INSERT INTO public.model_catalog (id, alias, runtime_type, model_id)
                    VALUES (:id, 'rollback-disabled', 'codex', 'rollback-disabled-model')
                    """
                ),
                {"id": entry_id},
            )
            trigger_id = None
            for _ in range(5):
                trigger_id = connection.execute(
                    text(
                        """
                        INSERT INTO public.model_dispatch_attempts (
                            catalog_entry_id, butler, outcome
                        ) VALUES (:id, 'general', 'runtime_failure')
                        RETURNING id
                        """
                    ),
                    {"id": entry_id},
                ).scalar_one()
            connection.execute(text('SET ROLE "butler_general_rw"'))
            try:
                episode_id = connection.execute(
                    text("SELECT public.append_runtime_attention_model_breaker(:trigger_id)"),
                    {"trigger_id": trigger_id},
                ).scalar_one_or_none()
            finally:
                connection.execute(text("RESET ROLE"))
            assert episode_id is None
            assert (
                connection.execute(
                    text("SELECT count(*) FROM public.runtime_attention_outbox")
                ).scalar_one()
                == 0
            )
            assert connection.execute(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM pg_trigger
                        WHERE tgrelid = 'public.model_dispatch_attempts'::regclass
                          AND tgname = 'runtime_attention_legacy_producer_fence_trigger'
                          AND NOT tgisinternal
                    )
                    """
                )
            ).scalar_one()
            assert connection.execute(
                text(
                    "SELECT to_regprocedure('public.append_runtime_attention_model_breaker(bigint)') IS NOT NULL"
                )
            ).scalar_one()
            assert connection.execute(
                text(
                    "SELECT to_regprocedure('public.append_runtime_attention_fleet_halt()') IS NOT NULL"
                )
            ).scalar_one()
    finally:
        engine.dispose()
