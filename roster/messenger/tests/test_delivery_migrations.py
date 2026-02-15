"""Tests for messenger delivery table migrations."""

from __future__ import annotations

import asyncio
import shutil
import uuid

import pytest
from sqlalchemy import create_engine, text

# Skip all tests if Docker is not available
docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


def _unique_db_name() -> str:
    return f"test_{uuid.uuid4().hex[:12]}"


@pytest.fixture(scope="module")
def postgres_container():
    """Start a PostgreSQL container for migration tests."""
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16") as postgres:
        yield postgres


def _create_db(postgres_container, db_name: str) -> str:
    """Create a fresh database and return its SQLAlchemy URL."""
    admin_url = postgres_container.get_connection_url()
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        safe = db_name.replace('"', '""')
        conn.execute(text(f'CREATE DATABASE "{safe}"'))
    engine.dispose()

    # Build URL pointing at the new database
    host = postgres_container.get_container_host_ip()
    port = postgres_container.get_exposed_port(5432)
    user = postgres_container.username
    password = postgres_container.password
    return f"postgresql://{user}:{password}@{host}:{port}/{db_name}"


def _table_exists(db_url: str, table_name: str) -> bool:
    """Check whether a table exists in the database."""
    engine = create_engine(db_url)
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT EXISTS ("
                "  SELECT 1 FROM information_schema.tables"
                "  WHERE table_schema = 'public' AND table_name = :t"
                ")"
            ),
            {"t": table_name},
        )
        exists = result.scalar()
    engine.dispose()
    return bool(exists)


def _constraint_exists(db_url: str, table_name: str, constraint_name: str) -> bool:
    """Check whether a constraint exists on a table."""
    engine = create_engine(db_url)
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT EXISTS ("
                "  SELECT 1 FROM information_schema.table_constraints"
                "  WHERE table_name = :t AND constraint_name = :c"
                ")"
            ),
            {"t": table_name, "c": constraint_name},
        )
        exists = result.scalar()
    engine.dispose()
    return bool(exists)


def _index_exists(db_url: str, index_name: str) -> bool:
    """Check whether an index exists."""
    engine = create_engine(db_url)
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT EXISTS (  SELECT 1 FROM pg_indexes  WHERE indexname = :idx)"),
            {"idx": index_name},
        )
        exists = result.scalar()
    engine.dispose()
    return bool(exists)


def test_messenger_migrations_create_all_tables(postgres_container):
    """Run messenger migrations and verify all 4 delivery tables are created."""
    from butlers.migrations import run_migrations

    db_name = _unique_db_name()
    db_url = _create_db(postgres_container, db_name)

    asyncio.run(run_migrations(db_url, chain="messenger"))

    assert _table_exists(db_url, "delivery_requests"), "delivery_requests table should exist"
    assert _table_exists(db_url, "delivery_attempts"), "delivery_attempts table should exist"
    assert _table_exists(db_url, "delivery_receipts"), "delivery_receipts table should exist"
    assert _table_exists(db_url, "delivery_dead_letter"), "delivery_dead_letter table should exist"


def test_delivery_requests_idempotency_key_unique(postgres_container):
    """delivery_requests.idempotency_key must have a unique constraint."""
    from butlers.migrations import run_migrations

    db_name = _unique_db_name()
    db_url = _create_db(postgres_container, db_name)

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

    db_name = _unique_db_name()
    db_url = _create_db(postgres_container, db_name)

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

    db_name = _unique_db_name()
    db_url = _create_db(postgres_container, db_name)

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

    db_name = _unique_db_name()
    db_url = _create_db(postgres_container, db_name)

    asyncio.run(run_migrations(db_url, chain="messenger"))

    # delivery_requests indexes
    assert _index_exists(db_url, "idx_delivery_requests_request_id"), (
        "idx_delivery_requests_request_id should exist"
    )
    assert _index_exists(db_url, "idx_delivery_requests_origin_butler"), (
        "idx_delivery_requests_origin_butler should exist"
    )
    assert _index_exists(db_url, "idx_delivery_requests_channel_status"), (
        "idx_delivery_requests_channel_status should exist"
    )

    # delivery_attempts indexes
    assert _index_exists(db_url, "idx_delivery_attempts_request_started"), (
        "idx_delivery_attempts_request_started should exist"
    )

    # delivery_receipts indexes
    assert _index_exists(db_url, "idx_delivery_receipts_request"), (
        "idx_delivery_receipts_request should exist"
    )
    assert _index_exists(db_url, "idx_delivery_receipts_provider_id"), (
        "idx_delivery_receipts_provider_id should exist"
    )

    # delivery_dead_letter indexes
    assert _index_exists(db_url, "idx_delivery_dead_letter_replay"), (
        "idx_delivery_dead_letter_replay should exist"
    )
    assert _index_exists(db_url, "idx_delivery_dead_letter_error_class"), (
        "idx_delivery_dead_letter_error_class should exist"
    )


def test_migrations_idempotent(postgres_container):
    """Running migrations twice should not raise errors."""
    from butlers.migrations import run_migrations

    db_name = _unique_db_name()
    db_url = _create_db(postgres_container, db_name)

    asyncio.run(run_migrations(db_url, chain="messenger"))
    # Second run should succeed without errors
    asyncio.run(run_migrations(db_url, chain="messenger"))

    assert _table_exists(db_url, "delivery_requests")
    assert _table_exists(db_url, "delivery_attempts")
    assert _table_exists(db_url, "delivery_receipts")
    assert _table_exists(db_url, "delivery_dead_letter")


def test_alembic_version_tracking(postgres_container):
    """After migration, alembic_version table should have the correct entry."""
    from butlers.migrations import run_migrations

    db_name = _unique_db_name()
    db_url = _create_db(postgres_container, db_name)

    asyncio.run(run_migrations(db_url, chain="messenger"))

    assert _table_exists(db_url, "alembic_version"), "alembic_version table should exist"

    engine = create_engine(db_url)
    with engine.connect() as conn:
        result = conn.execute(text("SELECT version_num FROM alembic_version"))
        versions = [row[0] for row in result]
    engine.dispose()

    assert "msg_001" in versions, f"Expected revision 'msg_001' in {versions}"
