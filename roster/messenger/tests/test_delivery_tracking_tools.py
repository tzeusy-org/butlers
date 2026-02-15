"""Tests for messenger delivery tracking MCP tools."""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from butlers.tools.messenger import (
    messenger_delivery_attempts,
    messenger_delivery_search,
    messenger_delivery_status,
    messenger_delivery_trace,
)

# Skip all tests if Docker is not available
docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


@pytest.fixture
async def delivery_pool():
    """Create a PostgreSQL pool with delivery tables."""
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16") as postgres:
        host = postgres.get_container_host_ip()
        port = postgres.get_exposed_port(5432)
        user = postgres.username
        password = postgres.password
        database = postgres.dbname

        # Create tables from migration
        pool = await asyncpg.create_pool(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
            min_size=1,
            max_size=5,
        )

        # Create delivery tables
        await pool.execute(
            """
            CREATE TABLE delivery_requests (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                idempotency_key TEXT NOT NULL UNIQUE,
                request_id UUID,
                origin_butler TEXT NOT NULL,
                channel TEXT NOT NULL,
                intent TEXT NOT NULL CHECK (intent IN ('send', 'reply')),
                target_identity TEXT NOT NULL,
                message_content TEXT NOT NULL,
                subject TEXT,
                request_envelope JSONB NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN (
                        'pending', 'in_progress', 'delivered',
                        'failed', 'dead_lettered'
                    )),
                terminal_error_class TEXT,
                terminal_error_message TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                terminal_at TIMESTAMPTZ
            )
            """
        )

        await pool.execute(
            """
            CREATE TABLE delivery_attempts (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                delivery_request_id UUID NOT NULL
                    REFERENCES delivery_requests(id) ON DELETE CASCADE,
                attempt_number INTEGER NOT NULL,
                started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                completed_at TIMESTAMPTZ,
                latency_ms INTEGER,
                outcome TEXT NOT NULL CHECK (outcome IN (
                    'success', 'retryable_error', 'non_retryable_error',
                    'timeout', 'in_progress'
                )),
                error_class TEXT,
                error_message TEXT,
                provider_response JSONB,
                UNIQUE (delivery_request_id, attempt_number)
            )
            """
        )

        await pool.execute(
            """
            CREATE TABLE delivery_receipts (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                delivery_request_id UUID NOT NULL
                    REFERENCES delivery_requests(id) ON DELETE CASCADE,
                provider_delivery_id TEXT,
                receipt_type TEXT NOT NULL CHECK (receipt_type IN (
                    'sent', 'delivered', 'read', 'webhook_confirmation'
                )),
                received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                metadata JSONB DEFAULT '{}'
            )
            """
        )

        await pool.execute(
            """
            CREATE TABLE delivery_dead_letter (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                delivery_request_id UUID NOT NULL
                    REFERENCES delivery_requests(id) ON DELETE CASCADE,
                quarantine_reason TEXT NOT NULL,
                error_class TEXT NOT NULL,
                error_summary TEXT NOT NULL,
                total_attempts INTEGER NOT NULL,
                first_attempt_at TIMESTAMPTZ NOT NULL,
                last_attempt_at TIMESTAMPTZ NOT NULL,
                original_request_envelope JSONB NOT NULL,
                all_attempt_outcomes JSONB NOT NULL DEFAULT '[]',
                replay_eligible BOOLEAN NOT NULL DEFAULT true,
                replay_count INTEGER NOT NULL DEFAULT 0,
                discarded_at TIMESTAMPTZ,
                discard_reason TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (delivery_request_id)
            )
            """
        )

        yield pool
        await pool.close()


async def test_messenger_delivery_status_success(delivery_pool):
    """Test messenger_delivery_status returns complete delivery info."""
    # Insert a delivery request
    delivery_id = await delivery_pool.fetchval(
        """
        INSERT INTO delivery_requests (
            idempotency_key, request_id, origin_butler, channel, intent,
            target_identity, message_content, request_envelope, status
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        RETURNING id
        """,
        "test-idem-1",
        uuid.uuid4(),
        "general",
        "telegram",
        "send",
        "@testuser",
        "Hello world",
        json.dumps({"schema_version": "notify.v1"}),
        "delivered",
    )

    # Insert attempt
    await delivery_pool.execute(
        """
        INSERT INTO delivery_attempts (
            delivery_request_id, attempt_number, started_at, completed_at,
            latency_ms, outcome
        )
        VALUES ($1, $2, $3, $4, $5, $6)
        """,
        delivery_id,
        1,
        datetime.now(UTC),
        datetime.now(UTC),
        150,
        "success",
    )

    # Insert receipt with provider ID
    await delivery_pool.execute(
        """
        INSERT INTO delivery_receipts (
            delivery_request_id, provider_delivery_id, receipt_type
        )
        VALUES ($1, $2, $3)
        """,
        delivery_id,
        "telegram-msg-12345",
        "sent",
    )

    # Test
    result = await messenger_delivery_status(delivery_pool, str(delivery_id))

    assert "error" not in result
    assert result["id"] == delivery_id
    assert result["status"] == "delivered"
    assert result["channel"] == "telegram"
    assert result["origin_butler"] == "general"
    assert result["latest_attempt"]["outcome"] == "success"
    assert result["latest_attempt"]["latency_ms"] == 150
    assert result["provider_delivery_id"] == "telegram-msg-12345"


async def test_messenger_delivery_status_not_found(delivery_pool):
    """Test messenger_delivery_status returns error for non-existent delivery."""
    fake_id = str(uuid.uuid4())
    result = await messenger_delivery_status(delivery_pool, fake_id)

    assert "error" in result
    assert "not found" in result["error"].lower()


async def test_messenger_delivery_status_invalid_uuid(delivery_pool):
    """Test messenger_delivery_status rejects invalid UUID format."""
    result = await messenger_delivery_status(delivery_pool, "not-a-uuid")

    assert "error" in result
    assert "invalid" in result["error"].lower()


async def test_messenger_delivery_search_all(delivery_pool):
    """Test messenger_delivery_search returns all deliveries."""
    # Insert multiple deliveries
    now = datetime.now(UTC)

    delivery_id_1 = await delivery_pool.fetchval(
        """
        INSERT INTO delivery_requests (
            idempotency_key, origin_butler, channel, intent,
            target_identity, message_content, request_envelope,
            status, created_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        RETURNING id
        """,
        "idem-search-1",
        "general",
        "telegram",
        "send",
        "@user1",
        "Message 1",
        json.dumps({}),
        "delivered",
        now - timedelta(hours=2),
    )

    delivery_id_2 = await delivery_pool.fetchval(
        """
        INSERT INTO delivery_requests (
            idempotency_key, origin_butler, channel, intent,
            target_identity, message_content, request_envelope,
            status, created_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        RETURNING id
        """,
        "idem-search-2",
        "health",
        "email",
        "reply",
        "user@example.com",
        "Message 2",
        json.dumps({}),
        "pending",
        now - timedelta(hours=1),
    )

    # Search all
    result = await messenger_delivery_search(delivery_pool)

    assert "error" not in result
    assert result["count"] == 2
    assert len(result["deliveries"]) == 2
    # Should be sorted by recency (newest first)
    assert result["deliveries"][0]["id"] == delivery_id_2
    assert result["deliveries"][1]["id"] == delivery_id_1


async def test_messenger_delivery_search_filter_by_origin_butler(delivery_pool):
    """Test messenger_delivery_search filters by origin butler."""
    # Insert deliveries from different butlers
    await delivery_pool.execute(
        """
        INSERT INTO delivery_requests (
            idempotency_key, origin_butler, channel, intent,
            target_identity, message_content, request_envelope
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        """,
        "idem-butler-1",
        "general",
        "telegram",
        "send",
        "@user1",
        "From general",
        json.dumps({}),
    )

    await delivery_pool.execute(
        """
        INSERT INTO delivery_requests (
            idempotency_key, origin_butler, channel, intent,
            target_identity, message_content, request_envelope
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        """,
        "idem-butler-2",
        "health",
        "telegram",
        "send",
        "@user2",
        "From health",
        json.dumps({}),
    )

    # Search by origin_butler
    result = await messenger_delivery_search(delivery_pool, origin_butler="health")

    assert "error" not in result
    assert result["count"] == 1
    assert result["deliveries"][0]["origin_butler"] == "health"


async def test_messenger_delivery_search_filter_by_channel_and_status(delivery_pool):
    """Test messenger_delivery_search filters by multiple criteria."""
    # Insert varied deliveries
    await delivery_pool.execute(
        """
        INSERT INTO delivery_requests (
            idempotency_key, origin_butler, channel, intent,
            target_identity, message_content, request_envelope, status
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        """,
        "idem-filter-1",
        "general",
        "telegram",
        "send",
        "@user1",
        "Telegram delivered",
        json.dumps({}),
        "delivered",
    )

    await delivery_pool.execute(
        """
        INSERT INTO delivery_requests (
            idempotency_key, origin_butler, channel, intent,
            target_identity, message_content, request_envelope, status
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        """,
        "idem-filter-2",
        "general",
        "telegram",
        "send",
        "@user2",
        "Telegram pending",
        json.dumps({}),
        "pending",
    )

    await delivery_pool.execute(
        """
        INSERT INTO delivery_requests (
            idempotency_key, origin_butler, channel, intent,
            target_identity, message_content, request_envelope, status
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        """,
        "idem-filter-3",
        "general",
        "email",
        "send",
        "user@example.com",
        "Email delivered",
        json.dumps({}),
        "delivered",
    )

    # Filter telegram + delivered
    result = await messenger_delivery_search(delivery_pool, channel="telegram", status="delivered")

    assert "error" not in result
    assert result["count"] == 1
    assert result["deliveries"][0]["channel"] == "telegram"
    assert result["deliveries"][0]["status"] == "delivered"


async def test_messenger_delivery_search_time_range(delivery_pool):
    """Test messenger_delivery_search filters by time range."""
    now = datetime.now(UTC)
    old_time = now - timedelta(days=7)
    recent_time = now - timedelta(hours=1)

    # Insert old delivery
    await delivery_pool.execute(
        """
        INSERT INTO delivery_requests (
            idempotency_key, origin_butler, channel, intent,
            target_identity, message_content, request_envelope,
            created_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        """,
        "idem-old",
        "general",
        "telegram",
        "send",
        "@user1",
        "Old message",
        json.dumps({}),
        old_time,
    )

    # Insert recent delivery
    await delivery_pool.execute(
        """
        INSERT INTO delivery_requests (
            idempotency_key, origin_butler, channel, intent,
            target_identity, message_content, request_envelope,
            created_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        """,
        "idem-recent",
        "general",
        "telegram",
        "send",
        "@user2",
        "Recent message",
        json.dumps({}),
        recent_time,
    )

    # Search since recent time
    since_iso = (now - timedelta(hours=2)).isoformat()
    result = await messenger_delivery_search(delivery_pool, since=since_iso)

    assert "error" not in result
    assert result["count"] == 1
    assert result["deliveries"][0]["idempotency_key"] == "idem-recent"


async def test_messenger_delivery_search_limit(delivery_pool):
    """Test messenger_delivery_search respects limit parameter."""
    # Insert 5 deliveries
    for i in range(5):
        await delivery_pool.execute(
            """
            INSERT INTO delivery_requests (
                idempotency_key, origin_butler, channel, intent,
                target_identity, message_content, request_envelope
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            f"idem-limit-{i}",
            "general",
            "telegram",
            "send",
            "@user",
            f"Message {i}",
            json.dumps({}),
        )

    # Search with limit=2
    result = await messenger_delivery_search(delivery_pool, limit=2)

    assert "error" not in result
    assert result["count"] == 2
    assert result["limit"] == 2
    assert len(result["deliveries"]) == 2


async def test_messenger_delivery_attempts_success(delivery_pool):
    """Test messenger_delivery_attempts returns full attempt log."""
    # Insert delivery
    delivery_id = await delivery_pool.fetchval(
        """
        INSERT INTO delivery_requests (
            idempotency_key, origin_butler, channel, intent,
            target_identity, message_content, request_envelope
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        RETURNING id
        """,
        "idem-attempts-1",
        "general",
        "telegram",
        "send",
        "@user",
        "Test message",
        json.dumps({}),
    )

    # Insert multiple attempts
    for i in range(1, 4):
        outcome = "retryable_error" if i < 3 else "success"
        await delivery_pool.execute(
            """
            INSERT INTO delivery_attempts (
                delivery_request_id, attempt_number, started_at, completed_at,
                latency_ms, outcome, error_class
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            delivery_id,
            i,
            datetime.now(UTC),
            datetime.now(UTC),
            100 + i * 50,
            outcome,
            "timeout" if i < 3 else None,
        )

    # Test
    result = await messenger_delivery_attempts(delivery_pool, str(delivery_id))

    assert "error" not in result
    assert result["total_attempts"] == 3
    assert len(result["attempts"]) == 3
    # Verify ordering by attempt number
    assert result["attempts"][0]["attempt_number"] == 1
    assert result["attempts"][1]["attempt_number"] == 2
    assert result["attempts"][2]["attempt_number"] == 3
    # Verify outcomes
    assert result["attempts"][0]["outcome"] == "retryable_error"
    assert result["attempts"][2]["outcome"] == "success"


async def test_messenger_delivery_attempts_not_found(delivery_pool):
    """Test messenger_delivery_attempts returns error for non-existent delivery."""
    fake_id = str(uuid.uuid4())
    result = await messenger_delivery_attempts(delivery_pool, fake_id)

    assert "error" in result
    assert "not found" in result["error"].lower()


async def test_messenger_delivery_trace_success(delivery_pool):
    """Test messenger_delivery_trace reconstructs full lineage."""
    request_id = uuid.uuid4()

    # Insert delivery request
    delivery_id = await delivery_pool.fetchval(
        """
        INSERT INTO delivery_requests (
            idempotency_key, request_id, origin_butler, channel, intent,
            target_identity, message_content, subject, request_envelope,
            status
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        RETURNING id
        """,
        "idem-trace-1",
        request_id,
        "general",
        "telegram",
        "send",
        "@testuser",
        "Full trace test",
        None,
        json.dumps({"schema_version": "notify.v1", "origin": "general"}),
        "delivered",
    )

    # Insert attempt
    await delivery_pool.execute(
        """
        INSERT INTO delivery_attempts (
            delivery_request_id, attempt_number, started_at, completed_at,
            latency_ms, outcome, provider_response
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        """,
        delivery_id,
        1,
        datetime.now(UTC),
        datetime.now(UTC),
        200,
        "success",
        json.dumps({"message_id": 12345}),
    )

    # Insert receipt
    await delivery_pool.execute(
        """
        INSERT INTO delivery_receipts (
            delivery_request_id, provider_delivery_id, receipt_type, metadata
        )
        VALUES ($1, $2, $3, $4)
        """,
        delivery_id,
        "telegram-msg-12345",
        "sent",
        json.dumps({"chat_id": 67890}),
    )

    # Test
    result = await messenger_delivery_trace(delivery_pool, str(request_id))

    assert "error" not in result
    assert result["request_id"] == str(request_id)
    assert result["delivery_count"] == 1
    assert len(result["deliveries"]) == 1

    delivery = result["deliveries"][0]
    assert delivery["id"] == delivery_id
    assert delivery["origin_butler"] == "general"
    assert delivery["channel"] == "telegram"
    assert delivery["status"] == "delivered"
    assert len(delivery["attempts"]) == 1
    assert delivery["attempts"][0]["outcome"] == "success"
    assert len(delivery["receipts"]) == 1
    assert delivery["receipts"][0]["provider_delivery_id"] == "telegram-msg-12345"


async def test_messenger_delivery_trace_not_found(delivery_pool):
    """Test messenger_delivery_trace returns error when no deliveries match."""
    fake_request_id = str(uuid.uuid4())
    result = await messenger_delivery_trace(delivery_pool, fake_request_id)

    assert "error" in result
    assert "no deliveries found" in result["error"].lower()


async def test_messenger_delivery_trace_invalid_uuid(delivery_pool):
    """Test messenger_delivery_trace rejects invalid UUID format."""
    result = await messenger_delivery_trace(delivery_pool, "not-a-uuid")

    assert "error" in result
    assert "invalid" in result["error"].lower()


async def test_messenger_delivery_trace_multiple_deliveries(delivery_pool):
    """Test messenger_delivery_trace handles multiple deliveries for same request."""
    request_id = uuid.uuid4()

    # Insert two deliveries for same request_id
    for i in range(2):
        delivery_id = await delivery_pool.fetchval(
            """
            INSERT INTO delivery_requests (
                idempotency_key, request_id, origin_butler, channel, intent,
                target_identity, message_content, request_envelope
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            RETURNING id
            """,
            f"idem-multi-{i}",
            request_id,
            "general",
            "telegram",
            "send",
            f"@user{i}",
            f"Message {i}",
            json.dumps({}),
        )

        # Add attempt for each
        await delivery_pool.execute(
            """
            INSERT INTO delivery_attempts (
                delivery_request_id, attempt_number, outcome
            )
            VALUES ($1, $2, $3)
            """,
            delivery_id,
            1,
            "success",
        )

    # Test
    result = await messenger_delivery_trace(delivery_pool, str(request_id))

    assert "error" not in result
    assert result["delivery_count"] == 2
    assert len(result["deliveries"]) == 2
