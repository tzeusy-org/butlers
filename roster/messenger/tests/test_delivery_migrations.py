"""Tests for messenger delivery table migrations."""

from __future__ import annotations

import asyncio
import shutil

import pytest
from sqlalchemy import create_engine, text

from butlers.testing.migration import (
    create_migration_db,
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


def test_messenger_migrations_create_all_tables(postgres_container):
    """Run messenger migrations and verify all 4 delivery tables are created."""
    from butlers.migrations import run_migrations

    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)

    asyncio.run(run_migrations(db_url, chain="messenger"))

    assert table_exists(db_url, "delivery_requests"), "delivery_requests table should exist"
    assert table_exists(db_url, "delivery_attempts"), "delivery_attempts table should exist"
    assert table_exists(db_url, "delivery_receipts"), "delivery_receipts table should exist"
    assert table_exists(db_url, "delivery_dead_letter"), "delivery_dead_letter table should exist"


def test_delivery_requests_idempotency_key_unique(postgres_container):
    """delivery_requests.idempotency_key must have a unique constraint."""
    from butlers.migrations import run_migrations

    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)

    asyncio.run(run_migrations(db_url, chain="messenger"))

    # Verify unique constraint on idempotency_key
    engine = create_engine(db_url)
    with engine.connect() as conn:
        # Insert first row
        conn.execute(
            text(
                """
                INSERT INTO delivery_requests
                (idempotency_key, origin_butler, channel, intent, target_identity, message_content, request_envelope)
                VALUES ('test-key-1', 'health', 'telegram', 'send', 'user123', 'Hello', '{}')
                """
            )
        )
        conn.commit()

        # Attempt to insert duplicate idempotency_key should fail
        with pytest.raises(Exception) as exc_info:
            conn.execute(
                text(
                    """
                    INSERT INTO delivery_requests
                    (idempotency_key, origin_butler, channel, intent, target_identity, message_content, request_envelope)
                    VALUES ('test-key-1', 'general', 'email', 'send', 'user456', 'Hi', '{}')
                    """
                )
            )
            conn.commit()

        assert "unique" in str(exc_info.value).lower(), (
            "Should fail with unique constraint violation"
        )
    engine.dispose()


def test_delivery_attempts_composite_unique(postgres_container):
    """delivery_attempts must have unique constraint on (delivery_request_id, attempt_number)."""
    from butlers.migrations import run_migrations

    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)

    asyncio.run(run_migrations(db_url, chain="messenger"))

    engine = create_engine(db_url)
    with engine.connect() as conn:
        # Create a delivery request
        result = conn.execute(
            text(
                """
                INSERT INTO delivery_requests
                (idempotency_key, origin_butler, channel, intent, target_identity, message_content, request_envelope)
                VALUES ('test-key-2', 'health', 'telegram', 'send', 'user123', 'Hello', '{}')
                RETURNING id
                """
            )
        )
        delivery_id = result.scalar()
        conn.commit()

        # Insert first attempt
        conn.execute(
            text(
                """
                INSERT INTO delivery_attempts
                (delivery_request_id, attempt_number, outcome)
                VALUES (:id, 1, 'in_progress')
                """
            ),
            {"id": delivery_id},
        )
        conn.commit()

        # Attempt to insert duplicate (delivery_request_id, attempt_number) should fail
        with pytest.raises(Exception) as exc_info:
            conn.execute(
                text(
                    """
                    INSERT INTO delivery_attempts
                    (delivery_request_id, attempt_number, outcome)
                    VALUES (:id, 1, 'success')
                    """
                ),
                {"id": delivery_id},
            )
            conn.commit()

        assert "unique" in str(exc_info.value).lower(), (
            "Should fail with unique constraint violation"
        )
    engine.dispose()


def test_delivery_dead_letter_unique_request(postgres_container):
    """delivery_dead_letter must have unique constraint on delivery_request_id."""
    from butlers.migrations import run_migrations

    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)

    asyncio.run(run_migrations(db_url, chain="messenger"))

    engine = create_engine(db_url)
    with engine.connect() as conn:
        # Create a delivery request
        result = conn.execute(
            text(
                """
                INSERT INTO delivery_requests
                (idempotency_key, origin_butler, channel, intent, target_identity, message_content, request_envelope)
                VALUES ('test-key-3', 'health', 'telegram', 'send', 'user123', 'Hello', '{}')
                RETURNING id
                """
            )
        )
        delivery_id = result.scalar()
        conn.commit()

        # Insert first dead letter entry
        conn.execute(
            text(
                """
                INSERT INTO delivery_dead_letter
                (delivery_request_id, quarantine_reason, error_class, error_summary, total_attempts, first_attempt_at, last_attempt_at, original_request_envelope, all_attempt_outcomes)
                VALUES (:id, 'exhausted retries', 'timeout', 'All attempts timed out', 3, now(), now(), '{}', '[]')
                """
            ),
            {"id": delivery_id},
        )
        conn.commit()

        # Attempt to insert duplicate dead letter for same delivery_request_id should fail
        with pytest.raises(Exception) as exc_info:
            conn.execute(
                text(
                    """
                    INSERT INTO delivery_dead_letter
                    (delivery_request_id, quarantine_reason, error_class, error_summary, total_attempts, first_attempt_at, last_attempt_at, original_request_envelope, all_attempt_outcomes)
                    VALUES (:id, 'manual quarantine', 'validation_error', 'Invalid target', 1, now(), now(), '{}', '[]')
                    """
                ),
                {"id": delivery_id},
            )
            conn.commit()

        assert "unique" in str(exc_info.value).lower(), (
            "Should fail with unique constraint violation"
        )
    engine.dispose()


def test_required_indexes_exist(postgres_container):
    """Verify that all required indexes are created."""
    from butlers.migrations import run_migrations

    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)

    asyncio.run(run_migrations(db_url, chain="messenger"))

    # delivery_requests indexes
    assert index_exists(db_url, "idx_delivery_requests_request_id"), (
        "idx_delivery_requests_request_id should exist"
    )
    assert index_exists(db_url, "idx_delivery_requests_origin_butler"), (
        "idx_delivery_requests_origin_butler should exist"
    )
    assert index_exists(db_url, "idx_delivery_requests_channel_status"), (
        "idx_delivery_requests_channel_status should exist"
    )

    # delivery_attempts indexes
    assert index_exists(db_url, "idx_delivery_attempts_request_started"), (
        "idx_delivery_attempts_request_started should exist"
    )

    # delivery_receipts indexes
    assert index_exists(db_url, "idx_delivery_receipts_request"), (
        "idx_delivery_receipts_request should exist"
    )
    assert index_exists(db_url, "idx_delivery_receipts_provider_id"), (
        "idx_delivery_receipts_provider_id should exist"
    )

    # delivery_dead_letter indexes
    assert index_exists(db_url, "idx_delivery_dead_letter_replay"), (
        "idx_delivery_dead_letter_replay should exist"
    )
    assert index_exists(db_url, "idx_delivery_dead_letter_error_class"), (
        "idx_delivery_dead_letter_error_class should exist"
    )


def test_migrations_idempotent(postgres_container):
    """Running migrations twice should not raise errors."""
    from butlers.migrations import run_migrations

    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)

    asyncio.run(run_migrations(db_url, chain="messenger"))
    # Second run should succeed without errors
    asyncio.run(run_migrations(db_url, chain="messenger"))

    assert table_exists(db_url, "delivery_requests")
    assert table_exists(db_url, "delivery_attempts")
    assert table_exists(db_url, "delivery_receipts")
    assert table_exists(db_url, "delivery_dead_letter")


def test_alembic_version_tracking(postgres_container):
    """After migration, alembic_version table should have the correct entry."""
    from butlers.migrations import run_migrations

    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)

    asyncio.run(run_migrations(db_url, chain="messenger"))

    assert table_exists(db_url, "alembic_version"), "alembic_version table should exist"

    engine = create_engine(db_url)
    with engine.connect() as conn:
        result = conn.execute(text("SELECT version_num FROM alembic_version"))
        versions = [row[0] for row in result]
    engine.dispose()

    assert "msg_001" in versions, f"Expected revision 'msg_001' in {versions}"
