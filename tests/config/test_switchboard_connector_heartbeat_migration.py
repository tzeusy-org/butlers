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

# Skip all tests if Docker is not available
docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


def _function_exists(db_url: str, function_name: str) -> bool:
    """Check whether a function exists in the database."""
    engine = create_engine(db_url)
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT EXISTS ("
                "  SELECT 1 FROM pg_proc"
                "  JOIN pg_namespace ON pg_proc.pronamespace = pg_namespace.oid"
                "  WHERE pg_namespace.nspname = 'public' AND pg_proc.proname = :f"
                ")"
            ),
            {"f": function_name},
        )
        exists = result.scalar()
    engine.dispose()
    return bool(exists)


def _get_partition_count(db_url: str, parent_table: str) -> int:
    """Count the number of partitions for a given parent table."""
    engine = create_engine(db_url)
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT COUNT(*) FROM pg_inherits "
                "JOIN pg_class parent ON parent.oid = pg_inherits.inhparent "
                "JOIN pg_class child ON child.oid = pg_inherits.inhrelid "
                "WHERE parent.relname = :t"
            ),
            {"t": parent_table},
        )
        count = result.scalar()
    engine.dispose()
    return int(count or 0)


def test_connector_heartbeat_migration_creates_tables(postgres_container):
    """Run switchboard migrations and verify both tables are created."""
    from butlers.migrations import run_migrations

    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)

    # Run core first, then switchboard
    asyncio.run(run_migrations(db_url, chain="core"))
    asyncio.run(run_migrations(db_url, chain="switchboard"))

    assert table_exists(db_url, "connector_registry"), "connector_registry table should exist"
    assert table_exists(db_url, "connector_heartbeat_log"), (
        "connector_heartbeat_log table should exist"
    )


def test_connector_registry_has_correct_columns(postgres_container):
    """Verify connector_registry has all required columns with correct types."""
    from butlers.migrations import run_migrations

    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)

    asyncio.run(run_migrations(db_url, chain="core"))
    asyncio.run(run_migrations(db_url, chain="switchboard"))

    # Check primary key columns
    connector_type_col = get_column_info(db_url, "connector_registry", "connector_type")
    assert connector_type_col is not None, "connector_type column should exist"
    assert connector_type_col["data_type"] == "text", "connector_type should be TEXT"
    assert connector_type_col["is_nullable"] == "NO", "connector_type should not be nullable"

    endpoint_identity_col = get_column_info(db_url, "connector_registry", "endpoint_identity")
    assert endpoint_identity_col is not None, "endpoint_identity column should exist"
    assert endpoint_identity_col["data_type"] == "text", "endpoint_identity should be TEXT"
    assert endpoint_identity_col["is_nullable"] == "NO", "endpoint_identity should not be nullable"

    # Check state and tracking columns
    state_col = get_column_info(db_url, "connector_registry", "state")
    assert state_col is not None, "state column should exist"
    assert state_col["data_type"] == "text", "state should be TEXT"
    assert state_col["is_nullable"] == "NO", "state should not be nullable"

    # Check counter columns
    counter_messages_ingested_col = get_column_info(
        db_url, "connector_registry", "counter_messages_ingested"
    )
    assert counter_messages_ingested_col is not None, (
        "counter_messages_ingested column should exist"
    )
    assert counter_messages_ingested_col["data_type"] == "bigint", (
        "counter_messages_ingested should be BIGINT"
    )

    counter_messages_failed_col = get_column_info(
        db_url, "connector_registry", "counter_messages_failed"
    )
    assert counter_messages_failed_col is not None, "counter_messages_failed column should exist"
    assert counter_messages_failed_col["data_type"] == "bigint", (
        "counter_messages_failed should be BIGINT"
    )

    counter_source_api_calls_col = get_column_info(
        db_url, "connector_registry", "counter_source_api_calls"
    )
    assert counter_source_api_calls_col is not None, "counter_source_api_calls column should exist"
    assert counter_source_api_calls_col["data_type"] == "bigint", (
        "counter_source_api_calls should be BIGINT"
    )

    counter_checkpoint_saves_col = get_column_info(
        db_url, "connector_registry", "counter_checkpoint_saves"
    )
    assert counter_checkpoint_saves_col is not None, "counter_checkpoint_saves column should exist"
    assert counter_checkpoint_saves_col["data_type"] == "bigint", (
        "counter_checkpoint_saves should be BIGINT"
    )

    counter_dedupe_accepted_col = get_column_info(
        db_url, "connector_registry", "counter_dedupe_accepted"
    )
    assert counter_dedupe_accepted_col is not None, "counter_dedupe_accepted column should exist"
    assert counter_dedupe_accepted_col["data_type"] == "bigint", (
        "counter_dedupe_accepted should be BIGINT"
    )


def test_connector_heartbeat_log_has_correct_columns(postgres_container):
    """Verify connector_heartbeat_log has all required columns including new counters."""
    from butlers.migrations import run_migrations

    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)

    asyncio.run(run_migrations(db_url, chain="core"))
    asyncio.run(run_migrations(db_url, chain="switchboard"))

    # Check primary key columns
    id_col = get_column_info(db_url, "connector_heartbeat_log", "id")
    assert id_col is not None, "id column should exist"
    assert id_col["data_type"] == "bigint", "id should be BIGINT"

    received_at_col = get_column_info(db_url, "connector_heartbeat_log", "received_at")
    assert received_at_col is not None, "received_at column should exist"
    assert "timestamp with time zone" in received_at_col["data_type"], (
        "received_at should be TIMESTAMPTZ"
    )

    # Check identifier columns
    connector_type_col = get_column_info(db_url, "connector_heartbeat_log", "connector_type")
    assert connector_type_col is not None, "connector_type column should exist"
    assert connector_type_col["data_type"] == "text", "connector_type should be TEXT"

    endpoint_identity_col = get_column_info(db_url, "connector_heartbeat_log", "endpoint_identity")
    assert endpoint_identity_col is not None, "endpoint_identity column should exist"
    assert endpoint_identity_col["data_type"] == "text", "endpoint_identity should be TEXT"

    # Check counter columns (including the two that were missing)
    counter_messages_ingested_col = get_column_info(
        db_url, "connector_heartbeat_log", "counter_messages_ingested"
    )
    assert counter_messages_ingested_col is not None, (
        "counter_messages_ingested column should exist"
    )
    assert counter_messages_ingested_col["data_type"] == "bigint", (
        "counter_messages_ingested should be BIGINT"
    )

    counter_messages_failed_col = get_column_info(
        db_url, "connector_heartbeat_log", "counter_messages_failed"
    )
    assert counter_messages_failed_col is not None, "counter_messages_failed column should exist"
    assert counter_messages_failed_col["data_type"] == "bigint", (
        "counter_messages_failed should be BIGINT"
    )

    counter_source_api_calls_col = get_column_info(
        db_url, "connector_heartbeat_log", "counter_source_api_calls"
    )
    assert counter_source_api_calls_col is not None, "counter_source_api_calls column should exist"
    assert counter_source_api_calls_col["data_type"] == "bigint", (
        "counter_source_api_calls should be BIGINT"
    )

    # Critical: verify the two missing counter columns are now present
    counter_checkpoint_saves_col = get_column_info(
        db_url, "connector_heartbeat_log", "counter_checkpoint_saves"
    )
    assert counter_checkpoint_saves_col is not None, "counter_checkpoint_saves column should exist"
    assert counter_checkpoint_saves_col["data_type"] == "bigint", (
        "counter_checkpoint_saves should be BIGINT"
    )

    counter_dedupe_accepted_col = get_column_info(
        db_url, "connector_heartbeat_log", "counter_dedupe_accepted"
    )
    assert counter_dedupe_accepted_col is not None, "counter_dedupe_accepted column should exist"
    assert counter_dedupe_accepted_col["data_type"] == "bigint", (
        "counter_dedupe_accepted should be BIGINT"
    )


def test_connector_registry_indexes_created(postgres_container):
    """Verify all indexes for connector_registry are created."""
    from butlers.migrations import run_migrations

    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)

    asyncio.run(run_migrations(db_url, chain="core"))
    asyncio.run(run_migrations(db_url, chain="switchboard"))

    assert index_exists(db_url, "ix_connector_registry_last_heartbeat_at"), (
        "last_heartbeat_at index should exist"
    )
    assert index_exists(db_url, "ix_connector_registry_state_last_heartbeat"), (
        "state+last_heartbeat index should exist"
    )
    assert index_exists(db_url, "ix_connector_registry_connector_type"), (
        "connector_type index should exist"
    )


def test_connector_heartbeat_log_indexes_created(postgres_container):
    """Verify all indexes for connector_heartbeat_log are created."""
    from butlers.migrations import run_migrations

    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)

    asyncio.run(run_migrations(db_url, chain="core"))
    asyncio.run(run_migrations(db_url, chain="switchboard"))

    assert index_exists(db_url, "ix_connector_heartbeat_log_connector_type_received_at"), (
        "connector_type+received_at index should exist"
    )
    assert index_exists(db_url, "ix_connector_heartbeat_log_endpoint_received_at"), (
        "endpoint+received_at index should exist"
    )
    assert index_exists(db_url, "ix_connector_heartbeat_log_state_received_at"), (
        "state+received_at index should exist"
    )


def test_partition_management_functions_exist(postgres_container):
    """Verify partition management functions are created."""
    from butlers.migrations import run_migrations

    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)

    asyncio.run(run_migrations(db_url, chain="core"))
    asyncio.run(run_migrations(db_url, chain="switchboard"))

    assert _function_exists(db_url, "switchboard_connector_heartbeat_log_ensure_partition"), (
        "ensure_partition function should exist"
    )
    assert _function_exists(
        db_url, "switchboard_connector_heartbeat_log_drop_expired_partitions"
    ), "drop_expired_partitions function should exist after upgrade"


def test_initial_partition_created(postgres_container):
    """Verify initial partition is created for current month."""
    from butlers.migrations import run_migrations

    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)

    asyncio.run(run_migrations(db_url, chain="core"))
    asyncio.run(run_migrations(db_url, chain="switchboard"))

    partition_count = _get_partition_count(db_url, "connector_heartbeat_log")
    assert partition_count >= 1, "At least one partition should be created"


def test_downgrade_drops_all_objects(postgres_container):
    """Verify downgrade cleanly drops all tables and functions."""
    from butlers.migrations import _build_alembic_config, run_migrations

    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)

    # Run migrations
    asyncio.run(run_migrations(db_url, chain="core"))
    asyncio.run(run_migrations(db_url, chain="switchboard"))

    # Verify objects exist
    assert table_exists(db_url, "connector_registry")
    assert table_exists(db_url, "connector_heartbeat_log")
    assert _function_exists(db_url, "switchboard_connector_heartbeat_log_ensure_partition")

    # Downgrade by one step (sw_013 -> sw_012) using Alembic command directly
    config = _build_alembic_config(db_url, chains=["switchboard"])
    command.downgrade(config, "switchboard@sw_012")

    # Verify objects are dropped
    assert not table_exists(db_url, "connector_registry"), (
        "connector_registry should be dropped after downgrade"
    )
    assert not table_exists(db_url, "connector_heartbeat_log"), (
        "connector_heartbeat_log should be dropped after downgrade"
    )
    assert not _function_exists(db_url, "switchboard_connector_heartbeat_log_ensure_partition"), (
        "ensure_partition function should be dropped after downgrade"
    )
    assert not _function_exists(
        db_url, "switchboard_connector_heartbeat_log_drop_expired_partitions"
    ), "drop_expired_partitions function should be dropped after downgrade"
