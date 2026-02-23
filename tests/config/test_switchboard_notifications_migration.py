"""Tests for Switchboard notifications table migration.

Note: These integration tests are currently failing due to a pre-existing issue
with duplicate revision IDs in the core migration chain (two files both use
revision "002"). This is tracked in the repository but is outside the scope of
the notifications table migration task. The unit tests in
test_switchboard_notifications_migration_unit.py verify the migration structure
is correct.
"""

from __future__ import annotations

import asyncio
import shutil

import pytest

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


def test_switchboard_notifications_migration_creates_table(postgres_container):
    """Run switchboard migrations and verify notifications table is created."""
    from butlers.migrations import run_migrations

    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)

    # Run core first, then switchboard
    asyncio.run(run_migrations(db_url, chain="core"))
    asyncio.run(run_migrations(db_url, chain="switchboard"))

    assert table_exists(db_url, "notifications"), "notifications table should exist"


def test_notifications_table_has_correct_columns(postgres_container):
    """Verify all required columns exist with correct types."""
    from butlers.migrations import run_migrations

    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)

    asyncio.run(run_migrations(db_url, chain="core"))
    asyncio.run(run_migrations(db_url, chain="switchboard"))

    # Check key columns
    id_col = get_column_info(db_url, "notifications", "id")
    assert id_col is not None, "id column should exist"
    assert id_col["data_type"] == "uuid", "id should be UUID"
    assert id_col["is_nullable"] == "NO", "id should not be nullable"

    source_butler_col = get_column_info(db_url, "notifications", "source_butler")
    assert source_butler_col is not None, "source_butler column should exist"
    assert source_butler_col["data_type"] == "text", "source_butler should be TEXT"

    channel_col = get_column_info(db_url, "notifications", "channel")
    assert channel_col is not None, "channel column should exist"
    assert channel_col["data_type"] == "text", "channel should be TEXT"

    recipient_col = get_column_info(db_url, "notifications", "recipient")
    assert recipient_col is not None, "recipient column should exist"
    assert recipient_col["data_type"] == "text", "recipient should be TEXT"

    message_col = get_column_info(db_url, "notifications", "message")
    assert message_col is not None, "message column should exist"
    assert message_col["data_type"] == "text", "message should be TEXT"

    metadata_col = get_column_info(db_url, "notifications", "metadata")
    assert metadata_col is not None, "metadata column should exist"
    assert metadata_col["data_type"] == "jsonb", "metadata should be JSONB"

    status_col = get_column_info(db_url, "notifications", "status")
    assert status_col is not None, "status column should exist"
    assert status_col["data_type"] == "text", "status should be TEXT"
    assert "sent" in status_col.get("column_default", ""), "status should default to 'sent'"

    error_col = get_column_info(db_url, "notifications", "error")
    assert error_col is not None, "error column should exist"
    assert error_col["data_type"] == "text", "error should be TEXT"

    session_id_col = get_column_info(db_url, "notifications", "session_id")
    assert session_id_col is not None, "session_id column should exist"
    assert session_id_col["data_type"] == "uuid", "session_id should be UUID"

    trace_id_col = get_column_info(db_url, "notifications", "trace_id")
    assert trace_id_col is not None, "trace_id column should exist"
    assert trace_id_col["data_type"] == "text", "trace_id should be TEXT"

    created_at_col = get_column_info(db_url, "notifications", "created_at")
    assert created_at_col is not None, "created_at column should exist"
    assert "timestamp with time zone" in created_at_col["data_type"], (
        "created_at should be TIMESTAMPTZ"
    )


def test_notifications_table_has_correct_indexes(postgres_container):
    """Verify required indexes are created."""
    from butlers.migrations import run_migrations

    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)

    asyncio.run(run_migrations(db_url, chain="core"))
    asyncio.run(run_migrations(db_url, chain="switchboard"))

    # Check indexes
    assert index_exists(db_url, "idx_notifications_source_butler_created"), (
        "source_butler+created_at index should exist"
    )
    assert index_exists(db_url, "idx_notifications_channel_created"), (
        "channel+created_at index should exist"
    )
    assert index_exists(db_url, "idx_notifications_status"), "status index should exist"


def test_notifications_migration_is_idempotent(postgres_container):
    """Running the migration twice should not raise errors."""
    from butlers.migrations import run_migrations

    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)

    asyncio.run(run_migrations(db_url, chain="core"))
    asyncio.run(run_migrations(db_url, chain="switchboard"))
    # Second run should succeed without errors
    asyncio.run(run_migrations(db_url, chain="switchboard"))

    assert table_exists(db_url, "notifications")
