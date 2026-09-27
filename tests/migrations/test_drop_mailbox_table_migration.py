"""core_249 drops a leftover per-butler ``mailbox`` table and is a no-op without one."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine

from alembic import command
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import create_migration_db, migration_db_name

pytestmark = [pytest.mark.integration, pytest.mark.db]


def _has_mailbox(connection, schema: str) -> bool:
    return connection.exec_driver_sql(
        "SELECT to_regclass(%s) IS NOT NULL", (f"{schema}.mailbox",)
    ).scalar()


def test_core_249_drops_mailbox_table_when_present(postgres_container):
    db_url = create_migration_db(postgres_container, migration_db_name())
    engine = create_engine(db_url)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("CREATE SCHEMA IF NOT EXISTS general")
        config = _build_alembic_config(db_url, chains=["core"], target_schema="general")
        command.upgrade(config, "core@core_248")
        with engine.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE general.mailbox (id UUID PRIMARY KEY)")

        command.upgrade(config, "core@core_249")
        with engine.connect() as connection:
            assert not _has_mailbox(connection, "general")

        # Fresh or already-clean schemas pass through as a no-op.
        command.downgrade(config, "core@core_248")
        command.upgrade(config, "core@core_249")
        with engine.connect() as connection:
            assert not _has_mailbox(connection, "general")
    finally:
        engine.dispose()
