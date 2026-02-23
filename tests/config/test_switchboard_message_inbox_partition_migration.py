"""Integration tests for switchboard message_inbox partition migration."""

from __future__ import annotations

import asyncio
import json
import shutil
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, text

from alembic import command
from butlers.testing.migration import (
    create_migration_db,
    index_exists,
    migration_db_name,
    table_exists,
)

# Skip all tests if Docker is not available.
docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


def _table_relkind(db_url: str, table_name: str) -> str | None:
    engine = create_engine(db_url)
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT c.relkind "
                "FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relname = :t"
            ),
            {"t": table_name},
        )
        row = result.fetchone()
    engine.dispose()
    return str(row[0]) if row else None


def _function_exists(db_url: str, function_name: str) -> bool:
    engine = create_engine(db_url)
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT EXISTS ("
                "  SELECT 1 "
                "  FROM pg_proc p "
                "  JOIN pg_namespace n ON n.oid = p.pronamespace "
                "  WHERE n.nspname = 'public' AND p.proname = :name"
                ")"
            ),
            {"name": function_name},
        )
        exists = result.scalar()
    engine.dispose()
    return bool(exists)


def _column_exists(db_url: str, table_name: str, column_name: str) -> bool:
    engine = create_engine(db_url)
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT EXISTS ("
                "  SELECT 1 FROM information_schema.columns"
                "  WHERE table_schema = 'public' AND table_name = :t AND column_name = :c"
                ")"
            ),
            {"t": table_name, "c": column_name},
        )
        exists = result.scalar()
    engine.dispose()
    return bool(exists)


def test_partition_migration_builds_partitioned_table_and_indexes(postgres_container):
    """Switchboard migration creates a partitioned message_inbox lifecycle table."""
    from butlers.migrations import run_migrations

    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)

    asyncio.run(run_migrations(db_url, chain="core"))
    asyncio.run(run_migrations(db_url, chain="switchboard"))

    assert table_exists(db_url, "message_inbox")
    assert _table_relkind(db_url, "message_inbox") == "p"

    assert index_exists(db_url, "ix_message_inbox_recent_received_at")
    assert index_exists(db_url, "ix_message_inbox_ctx_source_channel_received_at")
    assert index_exists(db_url, "ix_message_inbox_ctx_source_sender_received_at")

    assert _function_exists(db_url, "switchboard_message_inbox_ensure_partition")
    assert _function_exists(db_url, "switchboard_message_inbox_drop_expired_partitions")


def test_partition_maintenance_and_downgrade_round_trip(postgres_container):
    """Maintenance functions and downgrade path preserve operability."""
    from butlers.migrations import _build_alembic_config, run_migrations

    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)

    asyncio.run(run_migrations(db_url, chain="core"))
    asyncio.run(run_migrations(db_url, chain="switchboard"))

    engine = create_engine(db_url)
    with engine.begin() as conn:
        result = conn.execute(
            text("SELECT switchboard_message_inbox_ensure_partition(:ts)"),
            {"ts": "2024-01-15T00:00:00+00:00"},
        )
        partition_name = result.scalar()
        assert partition_name == "message_inbox_p202401"

        relation_result = conn.execute(text("SELECT to_regclass('public.message_inbox_p202401')"))
        assert relation_result.scalar() == "message_inbox_p202401"

        drop_result = conn.execute(
            text(
                "SELECT switchboard_message_inbox_drop_expired_partitions("
                "CAST(:retention AS interval), CAST(:ref AS timestamptz)"
                ")"
            ),
            {
                "retention": "1 month",
                "ref": "2024-03-20T00:00:00+00:00",
            },
        )
        assert int(drop_result.scalar() or 0) >= 1

        conn.execute(
            text(
                """
                INSERT INTO message_inbox (
                    request_context,
                    raw_payload,
                    normalized_text,
                    received_at,
                    lifecycle_state,
                    schema_version,
                    processing_metadata,
                    response_summary
                )
                VALUES (
                    CAST(:request_context AS jsonb),
                    CAST(:raw_payload AS jsonb),
                    :normalized_text,
                    :received_at,
                    :lifecycle_state,
                    :schema_version,
                    CAST(:processing_metadata AS jsonb),
                    :response_summary
                )
                """
            ),
            {
                "request_context": json.dumps(
                    {
                        "request_id": "migration-test-request",
                        "received_at": "2026-02-14T00:00:00+00:00",
                        "source_channel": "telegram",
                        "source_endpoint_identity": "telegram:bot",
                        "source_sender_identity": "user-123",
                    }
                ),
                "raw_payload": json.dumps({"content": "hello", "metadata": {"test": True}}),
                "normalized_text": "hello",
                "received_at": datetime.now(UTC),
                "lifecycle_state": "completed",
                "schema_version": "message_inbox.v2",
                "processing_metadata": json.dumps({"classification_duration_ms": 5}),
                "response_summary": "ok",
            },
        )
    engine.dispose()

    assert not table_exists(db_url, "message_inbox_p202401")

    config = _build_alembic_config(db_url, chains=["switchboard"])
    command.downgrade(config, "switchboard@sw_007")

    assert table_exists(db_url, "message_inbox")
    assert _table_relkind(db_url, "message_inbox") == "r"
    assert _column_exists(db_url, "message_inbox", "raw_content")
    assert _column_exists(db_url, "message_inbox", "routing_results")
    assert _column_exists(db_url, "message_inbox", "source_endpoint_identity")
    assert _column_exists(db_url, "message_inbox", "dedupe_key")
    assert _column_exists(db_url, "message_inbox", "dedupe_strategy")

    assert not _function_exists(db_url, "switchboard_message_inbox_ensure_partition")
    assert not _function_exists(db_url, "switchboard_message_inbox_drop_expired_partitions")
