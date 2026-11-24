"""Concierge's migrated scheduler must reach startup before receiver health is possible.

REQ-butler-control-plane-liveness-001: these disposable PostgreSQL bodies prove
schema/startup prerequisites, never the separate live 13/13 observation window.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import asyncpg
import pytest

from alembic import command
from butlers.core.scheduler.sync import sync_schedules
from butlers.migrations import _build_alembic_config, run_migrations
from butlers.testing.migration import create_migration_db, load_migration, migration_db_name

pytestmark = [pytest.mark.db, pytest.mark.integration]

_FLEET = (
    "chronicler",
    "concierge",
    "education",
    "finance",
    "general",
    "health",
    "home",
    "lifestyle",
    "messenger",
    "qa",
    "relationship",
    "switchboard",
    "travel",
)
_MIGRATION = (
    Path(__file__).resolve().parents[2] / "roster/concierge/migrations/003_schedule_token_budget.py"
)
_COLUMN_SQL = """
    SELECT data_type, is_nullable, column_default
    FROM information_schema.columns
    WHERE table_schema='concierge' AND table_name='scheduled_tasks'
      AND column_name='max_token_budget'
"""


@pytest.fixture(params=["concierge_002", "head"])
def scheduler_database(postgres_container, request):
    """Real fleet tables are required by Concierge's existing UNION views."""
    url = create_migration_db(postgres_container, migration_db_name())
    for schema in _FLEET:
        asyncio.run(run_migrations(url, chain="core", schema=schema))
    config = _build_alembic_config(url, ["concierge"], target_schema="concierge")
    command.upgrade(config, "concierge@head" if request.param == "head" else request.param)
    return url, config, request.param


async def _owning_role(connection):
    await connection.execute('SET ROLE "butler_concierge_rw"')
    await connection.execute("SET search_path TO concierge, public")


@pytest.mark.asyncio(loop_scope="session")
async def test_migrated_concierge_runs_empty_schedule_sync_and_preserves_data(scheduler_database):
    url, config, starting_revision = scheduler_database
    admin = await asyncpg.create_pool(url, min_size=1, max_size=2)
    runtime = await asyncpg.create_pool(url, min_size=1, max_size=2, setup=_owning_role)
    try:
        general_columns = await admin.fetch("""
            SELECT column_name,data_type,is_nullable,column_default
            FROM information_schema.columns
            WHERE table_schema='general' AND table_name='scheduled_tasks'
            ORDER BY ordinal_position
        """)
        row_id = await admin.fetchval("""
            INSERT INTO concierge.scheduled_tasks(name,cron,prompt,source,next_run_at)
            VALUES ('concierge-schema-control','0 0 * * *','synthetic migration fixture',
                    'db',now()+interval '1 day') RETURNING id
        """)
        original = await admin.fetchrow(
            "SELECT id,name,cron,prompt,source FROM concierge.scheduled_tasks WHERE id=$1", row_id
        )
        if starting_revision == "concierge_002":
            assert await admin.fetchrow(_COLUMN_SQL) is None
            # Actual old query failure, including an empty schedule list: no
            # missing test symbol, fake schema, legacy fallback or live DB write.
            with pytest.raises(asyncpg.UndefinedColumnError, match="max_token_budget"):
                await sync_schedules(runtime, [])
            await asyncio.to_thread(command.upgrade, config, "concierge@head")

        column = await admin.fetchrow(_COLUMN_SQL)
        assert column is not None
        assert dict(column) == {
            "data_type": "integer",
            "is_nullable": "YES",
            "column_default": None,
        }
        await sync_schedules(runtime, [])
        assert (
            await admin.fetchrow(
                "SELECT id,name,cron,prompt,source FROM concierge.scheduled_tasks WHERE id=$1",
                row_id,
            )
            == original
        )
        assert (
            await runtime.fetchval(
                "SELECT max_token_budget FROM scheduled_tasks WHERE id=$1", row_id
            )
            is None
        )
        await runtime.execute("UPDATE scheduled_tasks SET max_token_budget=42 WHERE id=$1", row_id)

        # Replay the actual DDL as well as the normal already-at-head path.
        migration = load_migration(_MIGRATION, "_test_concierge_003")
        statements = []
        op = MagicMock()
        op.execute.side_effect = statements.append
        with patch.object(migration, "op", op):
            migration.upgrade()
        for sql in statements:
            await admin.execute(sql)
        await asyncio.to_thread(command.upgrade, config, "concierge@head")
        assert (
            await runtime.fetchval(
                "SELECT max_token_budget FROM scheduled_tasks WHERE id=$1", row_id
            )
            == 42
        )
        assert (
            await admin.fetch("""
            SELECT column_name,data_type,is_nullable,column_default
            FROM information_schema.columns
            WHERE table_schema='general' AND table_name='scheduled_tasks'
            ORDER BY ordinal_position
        """)
            == general_columns
        )
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await runtime.fetchval("SELECT count(*) FROM general.scheduled_tasks")

        await asyncio.to_thread(command.downgrade, config, "concierge_002")
        assert await admin.fetchrow(_COLUMN_SQL) is None
        assert (
            await admin.fetchrow(
                "SELECT id,name,cron,prompt,source FROM concierge.scheduled_tasks WHERE id=$1",
                row_id,
            )
            == original
        )
        await asyncio.to_thread(command.upgrade, config, "concierge@head")
        await sync_schedules(runtime, [])
        assert await admin.fetchrow(_COLUMN_SQL) is not None
    finally:
        await runtime.close()
        await admin.close()
