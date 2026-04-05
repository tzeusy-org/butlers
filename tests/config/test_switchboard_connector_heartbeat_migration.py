"""Integration tests for Switchboard connector heartbeat tables migration."""

from __future__ import annotations

import asyncio
import shutil

import pytest
from sqlalchemy import create_engine, text

from alembic import command
from butlers.testing.migration import (
    create_migration_db,
    get_column_info,
    index_exists,
    migration_db_name,
    table_exists,
)

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


def _function_exists(db_url: str, function_name: str) -> bool:
    engine = create_engine(db_url)
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM pg_proc JOIN pg_namespace ON pg_proc.pronamespace = pg_namespace.oid WHERE pg_namespace.nspname = 'public' AND pg_proc.proname = :f)"
            ),
            {"f": function_name},
        )
        exists = result.scalar()
    engine.dispose()
    return bool(exists)


def _get_partition_count(db_url: str, parent_table: str) -> int:
    engine = create_engine(db_url)
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT COUNT(*) FROM pg_inherits JOIN pg_class parent ON parent.oid = pg_inherits.inhparent JOIN pg_class child ON child.oid = pg_inherits.inhrelid WHERE parent.relname = :t"
            ),
            {"t": parent_table},
        )
        count = result.scalar()
    engine.dispose()
    return int(count or 0)


def _run_core_and_switchboard(postgres_container) -> str:
    from butlers.migrations import run_migrations

    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    asyncio.run(run_migrations(db_url, chain="core"))
    asyncio.run(run_migrations(db_url, chain="switchboard"))
    return db_url


def test_connector_heartbeat_tables_schema(postgres_container):
    """Verify both tables exist, have required columns and indexes."""
    db_url = _run_core_and_switchboard(postgres_container)

    assert table_exists(db_url, "connector_registry")
    assert table_exists(db_url, "connector_heartbeat_log")

    # connector_registry key columns
    for col in ("connector_type", "endpoint_identity", "state"):
        info = get_column_info(db_url, "connector_registry", col)
        assert info is not None and info["data_type"] == "text"
    for col in (
        "counter_messages_ingested",
        "counter_messages_failed",
        "counter_checkpoint_saves",
        "counter_dedupe_accepted",
    ):
        info = get_column_info(db_url, "connector_registry", col)
        assert info is not None and info["data_type"] == "bigint"

    # connector_heartbeat_log key columns
    assert get_column_info(db_url, "connector_heartbeat_log", "id")["data_type"] == "bigint"
    for col in (
        "counter_messages_ingested",
        "counter_messages_failed",
        "counter_checkpoint_saves",
        "counter_dedupe_accepted",
    ):
        info = get_column_info(db_url, "connector_heartbeat_log", col)
        assert info is not None and info["data_type"] == "bigint"

    # Indexes
    assert index_exists(db_url, "ix_connector_registry_last_heartbeat_at")
    assert index_exists(db_url, "ix_connector_heartbeat_log_connector_type_received_at")

    # Partition management functions
    assert _function_exists(db_url, "switchboard_connector_heartbeat_log_ensure_partition")
    assert _get_partition_count(db_url, "connector_heartbeat_log") >= 1


def test_downgrade_drops_all_objects(postgres_container):
    """Verify downgrade cleanly drops all tables and functions."""
    from butlers.migrations import _build_alembic_config

    db_url = _run_core_and_switchboard(postgres_container)

    config = _build_alembic_config(db_url, chains=["switchboard"])
    command.downgrade(config, "switchboard@sw_001")

    assert not table_exists(db_url, "connector_registry")
    assert not table_exists(db_url, "connector_heartbeat_log")
    assert not _function_exists(db_url, "switchboard_connector_heartbeat_log_ensure_partition")
