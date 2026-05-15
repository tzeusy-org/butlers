"""Integration tests for Switchboard notifications table migration."""

from __future__ import annotations

import asyncio
import shutil

import pytest

from butlers.testing.migration import (
    constraint_exists,
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


def test_notifications_table_schema_and_indexes(postgres_container):
    """Run switchboard migrations and verify notifications table schema and idempotency."""
    from butlers.migrations import run_migrations

    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)

    asyncio.run(run_migrations(db_url, chain="core"))
    asyncio.run(run_migrations(db_url, chain="switchboard"))
    # Idempotency: second run should not raise
    asyncio.run(run_migrations(db_url, chain="switchboard"))

    assert table_exists(db_url, "notifications")

    # Key column types
    assert get_column_info(db_url, "notifications", "id")["data_type"] == "uuid"
    assert get_column_info(db_url, "notifications", "source_butler")["data_type"] == "text"
    assert get_column_info(db_url, "notifications", "channel")["data_type"] == "text"
    assert get_column_info(db_url, "notifications", "metadata")["data_type"] == "jsonb"

    # Indexes
    assert index_exists(db_url, "idx_notifications_source_butler_created")
    assert index_exists(db_url, "idx_notifications_channel_created")
    assert index_exists(db_url, "idx_notifications_status")

    # CHECK constraint enumerating valid status values (sw_011)
    assert constraint_exists(db_url, "notifications", "chk_notifications_status")
