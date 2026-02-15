"""Tests for messenger operations tools (validation, dry-run, health).

Tests the 6 tools from docs/roles/messenger_butler.md sections 5.1.3-5.1.4:
- messenger_validate_notify
- messenger_dry_run
- messenger_circuit_status
- messenger_rate_limit_status
- messenger_queue_depth
- messenger_delivery_stats
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from butlers.tools.messenger.operations import (
    messenger_circuit_status,
    messenger_delivery_stats,
    messenger_dry_run,
    messenger_queue_depth,
    messenger_rate_limit_status,
    messenger_validate_notify,
)
from butlers.tools.messenger.rate_limiter import RateLimitConfig, RateLimiter
from butlers.tools.messenger.reliability import (
    CircuitBreakerConfig,
    CircuitBreakerState,
    CircuitState,
)

# Skip DB tests if Docker is not available
docker_available = shutil.which("docker") is not None
db_tests_mark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


# ============================================================================
# Test messenger_validate_notify
# ============================================================================


async def test_validate_notify_valid_send():
    """Valid send request passes validation."""
    request = {
        "schema_version": "notify.v1",
        "origin_butler": "health",
        "delivery": {
            "intent": "send",
            "channel": "telegram",
            "message": "Test message",
            "recipient": "user123",
        },
    }

    result = await messenger_validate_notify(request)

    assert result["valid"] is True
    assert result["errors"] == []


async def test_validate_notify_valid_reply():
    """Valid reply request passes validation."""
    request = {
        "schema_version": "notify.v1",
        "origin_butler": "health",
        "delivery": {
            "intent": "reply",
            "channel": "telegram",
            "message": "Test reply",
        },
        "request_context": {
            "request_id": str(uuid.uuid4()),
            "source_channel": "telegram",
            "source_endpoint_identity": "bot_endpoint",
            "source_sender_identity": "user123",
        },
    }

    result = await messenger_validate_notify(request)

    assert result["valid"] is True
    assert result["errors"] == []


async def test_validate_notify_missing_schema_version():
    """Missing schema_version fails validation."""
    request = {
        "origin_butler": "health",
        "delivery": {
            "intent": "send",
            "channel": "telegram",
            "message": "Test",
        },
    }

    result = await messenger_validate_notify(request)

    assert result["valid"] is False
    assert any("schema_version" in err["field"] for err in result["errors"])


async def test_validate_notify_invalid_schema_version():
    """Invalid schema_version fails validation."""
    request = {
        "schema_version": "notify.v2",
        "origin_butler": "health",
        "delivery": {
            "intent": "send",
            "channel": "telegram",
            "message": "Test",
        },
    }

    result = await messenger_validate_notify(request)

    assert result["valid"] is False
    assert any("schema_version" in err["field"] for err in result["errors"])


async def test_validate_notify_missing_origin_butler():
    """Missing origin_butler fails validation."""
    request = {
        "schema_version": "notify.v1",
        "delivery": {
            "intent": "send",
            "channel": "telegram",
            "message": "Test",
        },
    }

    result = await messenger_validate_notify(request)

    assert result["valid"] is False
    assert any("origin_butler" in err["field"] for err in result["errors"])


async def test_validate_notify_missing_delivery_block():
    """Missing delivery block fails validation."""
    request = {
        "schema_version": "notify.v1",
        "origin_butler": "health",
    }

    result = await messenger_validate_notify(request)

    assert result["valid"] is False
    assert any("delivery" in err["field"] for err in result["errors"])


async def test_validate_notify_invalid_intent():
    """Invalid intent fails validation."""
    request = {
        "schema_version": "notify.v1",
        "origin_butler": "health",
        "delivery": {
            "intent": "broadcast",
            "channel": "telegram",
            "message": "Test",
        },
    }

    result = await messenger_validate_notify(request)

    assert result["valid"] is False
    assert any("delivery.intent" in err["field"] for err in result["errors"])


async def test_validate_notify_missing_message():
    """Missing message fails validation."""
    request = {
        "schema_version": "notify.v1",
        "origin_butler": "health",
        "delivery": {
            "intent": "send",
            "channel": "telegram",
        },
    }

    result = await messenger_validate_notify(request)

    assert result["valid"] is False
    assert any("delivery.message" in err["field"] for err in result["errors"])


async def test_validate_notify_reply_missing_request_context():
    """Reply without request_context fails validation."""
    request = {
        "schema_version": "notify.v1",
        "origin_butler": "health",
        "delivery": {
            "intent": "reply",
            "channel": "telegram",
            "message": "Test reply",
        },
    }

    result = await messenger_validate_notify(request)

    assert result["valid"] is False
    assert any("request_context" in err["field"] for err in result["errors"])


async def test_validate_notify_reply_missing_context_fields():
    """Reply with incomplete request_context fails validation."""
    request = {
        "schema_version": "notify.v1",
        "origin_butler": "health",
        "delivery": {
            "intent": "reply",
            "channel": "telegram",
            "message": "Test reply",
        },
        "request_context": {
            "request_id": str(uuid.uuid4()),
            # Missing source_channel, source_endpoint_identity, source_sender_identity
        },
    }

    result = await messenger_validate_notify(request)

    assert result["valid"] is False
    assert len(result["errors"]) >= 3  # Should have errors for missing fields


# ============================================================================
# Test messenger_dry_run
# ============================================================================


async def test_dry_run_valid_send():
    """Dry-run for valid send request."""
    request = {
        "schema_version": "notify.v1",
        "origin_butler": "health",
        "delivery": {
            "intent": "send",
            "channel": "telegram",
            "message": "Test message",
            "recipient": "user123",
        },
    }

    result = await messenger_dry_run(request)

    assert result["valid"] is True
    assert result["target_identity"] == "user123"
    assert result["channel_adapter"] == "telegram.bot"
    assert result["intent"] == "send"
    assert result["would_be_admitted"] is True


async def test_dry_run_valid_reply():
    """Dry-run for valid reply request."""
    request = {
        "schema_version": "notify.v1",
        "origin_butler": "health",
        "delivery": {
            "intent": "reply",
            "channel": "email",
            "message": "Test reply",
        },
        "request_context": {
            "request_id": str(uuid.uuid4()),
            "source_channel": "email",
            "source_endpoint_identity": "bot@example.com",
            "source_sender_identity": "user@example.com",
        },
    }

    result = await messenger_dry_run(request)

    assert result["valid"] is True
    assert result["target_identity"] == "user@example.com"
    assert result["channel_adapter"] == "email.bot"
    assert result["intent"] == "reply"


async def test_dry_run_invalid_request():
    """Dry-run for invalid request returns validation errors."""
    request = {
        "schema_version": "notify.v1",
        "origin_butler": "health",
        "delivery": {
            "intent": "send",
            "channel": "telegram",
            # Missing message
        },
    }

    result = await messenger_dry_run(request)

    assert result["valid"] is False
    assert "errors" in result
    assert len(result["errors"]) > 0


async def test_dry_run_with_rate_limiter():
    """Dry-run with rate limiter includes headroom info."""
    config = RateLimitConfig(
        global_max_per_minute=60,
        channel_limits={"telegram.bot": 30},
    )
    rate_limiter = RateLimiter(config)

    request = {
        "schema_version": "notify.v1",
        "origin_butler": "health",
        "delivery": {
            "intent": "send",
            "channel": "telegram",
            "message": "Test",
            "recipient": "user123",
        },
    }

    result = await messenger_dry_run(request, rate_limiter=rate_limiter)

    assert result["valid"] is True
    assert "rate_limit_headroom" in result
    assert result["rate_limit_headroom"]["global"] >= 0
    assert result["would_be_admitted"] is True


async def test_dry_run_send_without_recipient():
    """Dry-run for send without recipient uses policy default."""
    request = {
        "schema_version": "notify.v1",
        "origin_butler": "health",
        "delivery": {
            "intent": "send",
            "channel": "telegram",
            "message": "Test",
        },
    }

    result = await messenger_dry_run(request)

    assert result["valid"] is True
    assert result["target_identity"] == "<policy-default-for-telegram>"


# ============================================================================
# Test messenger_circuit_status


# Simple test helper to create circuit breakers with custom state
class MockCircuitBreaker:
    """Mock circuit breaker for testing."""

    def __init__(self, config, state):
        self.config = config
        self.state = state


async def test_circuit_status_empty():
    """Circuit status with no circuit breakers."""
    result = await messenger_circuit_status(circuit_breakers=None)

    assert result["circuits"] == {}


async def test_circuit_status_closed():
    """Circuit status for closed circuit."""
    config = CircuitBreakerConfig(failure_threshold=5)
    state = CircuitBreakerState(state=CircuitState.CLOSED, consecutive_failures=0)
    breaker = MockCircuitBreaker(config, state)

    circuit_breakers = {"telegram.bot": breaker}
    result = await messenger_circuit_status(circuit_breakers=circuit_breakers)

    assert "telegram.bot" in result["circuits"]
    assert result["circuits"]["telegram.bot"]["state"] == "closed"
    assert result["circuits"]["telegram.bot"]["consecutive_failures"] == 0


async def test_circuit_status_open():
    """Circuit status for open circuit."""
    config = CircuitBreakerConfig(failure_threshold=5, recovery_timeout_seconds=60.0)
    state = CircuitBreakerState(
        state=CircuitState.OPEN,
        consecutive_failures=5,
        opened_at=datetime.now(UTC),
        last_error_class="target_unavailable",
        last_error_message="Provider unavailable",
    )
    breaker = MockCircuitBreaker(config, state)

    circuit_breakers = {"email.bot": breaker}
    result = await messenger_circuit_status(circuit_breakers=circuit_breakers)

    assert "email.bot" in result["circuits"]
    circuit_info = result["circuits"]["email.bot"]
    assert circuit_info["state"] == "open"
    assert circuit_info["consecutive_failures"] == 5
    assert "trip_reason" in circuit_info
    assert "trip_timestamp" in circuit_info
    assert circuit_info["recovery_timeout_seconds"] == 60.0
    assert circuit_info["last_error_class"] == "target_unavailable"


async def test_circuit_status_half_open():
    """Circuit status for half-open circuit."""
    config = CircuitBreakerConfig(
        failure_threshold=5,
        half_open_max_attempts=3,
        half_open_success_threshold=2,
    )
    state = CircuitBreakerState(
        state=CircuitState.HALF_OPEN,
        consecutive_failures=5,
        half_open_attempts=1,
        half_open_successes=0,
    )
    breaker = MockCircuitBreaker(config, state)

    circuit_breakers = {"telegram.bot": breaker}
    result = await messenger_circuit_status(circuit_breakers=circuit_breakers)

    circuit_info = result["circuits"]["telegram.bot"]
    assert circuit_info["state"] == "half_open"
    assert circuit_info["half_open_attempts"] == 1
    assert circuit_info["half_open_successes"] == 0
    assert circuit_info["success_threshold"] == 2


async def test_circuit_status_filtered_by_channel():
    """Circuit status filtered by channel."""
    config = CircuitBreakerConfig()
    state1 = CircuitBreakerState(state=CircuitState.CLOSED)
    state2 = CircuitBreakerState(state=CircuitState.CLOSED)
    breaker1 = MockCircuitBreaker(config, state1)
    breaker2 = MockCircuitBreaker(config, state2)

    circuit_breakers = {
        "telegram.bot": breaker1,
        "email.bot": breaker2,
    }
    result = await messenger_circuit_status(
        circuit_breakers=circuit_breakers, channel="telegram.bot"
    )

    assert "telegram.bot" in result["circuits"]
    assert "email.bot" not in result["circuits"]


# Test messenger_rate_limit_status
# ============================================================================


async def test_rate_limit_status_empty():
    """Rate limit status with no rate limiter."""
    result = await messenger_rate_limit_status(rate_limiter=None)

    assert result["global"]["capacity"] == 0
    assert result["channels"] == {}


async def test_rate_limit_status_with_limiter():
    """Rate limit status with active rate limiter."""
    config = RateLimitConfig(
        global_max_per_minute=60,
        channel_limits={"telegram.bot": 30, "email.bot": 20},
    )
    rate_limiter = RateLimiter(config)

    result = await messenger_rate_limit_status(rate_limiter=rate_limiter)

    # Global bucket
    assert result["global"]["capacity"] == 60
    assert result["global"]["available"] == 60
    assert result["global"]["consumed"] == 0
    assert "refill_rate" in result["global"]

    # Channel buckets
    assert "telegram.bot" in result["channels"]
    assert result["channels"]["telegram.bot"]["capacity"] == 30
    assert "email.bot" in result["channels"]
    assert result["channels"]["email.bot"]["capacity"] == 20


async def test_rate_limit_status_after_consumption():
    """Rate limit status reflects consumed tokens."""
    config = RateLimitConfig(global_max_per_minute=60)
    rate_limiter = RateLimiter(config)

    # Consume some tokens
    rate_limiter._global_bucket.consume(15)

    result = await messenger_rate_limit_status(rate_limiter=rate_limiter)

    assert result["global"]["available"] < 60
    assert result["global"]["consumed"] > 0


async def test_rate_limit_status_filtered_by_channel():
    """Rate limit status filtered by channel."""
    config = RateLimitConfig(
        channel_limits={"telegram.bot": 30, "email.bot": 20},
    )
    rate_limiter = RateLimiter(config)

    result = await messenger_rate_limit_status(rate_limiter=rate_limiter, channel="telegram")

    assert "telegram.bot" in result["channels"]
    assert "email.bot" not in result["channels"]


async def test_rate_limit_status_filtered_by_identity_scope():
    """Rate limit status filtered by identity scope."""
    config = RateLimitConfig(
        channel_limits={"telegram.bot": 30, "telegram.user": 20},
    )
    rate_limiter = RateLimiter(config)

    result = await messenger_rate_limit_status(rate_limiter=rate_limiter, identity_scope="bot")

    assert "telegram.bot" in result["channels"]
    assert "telegram.user" not in result["channels"]


# ============================================================================
# Test messenger_queue_depth (requires DB)
# ============================================================================


@pytest.fixture
async def delivery_pool():
    """Create a PostgreSQL pool with delivery_requests table."""
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16") as postgres:
        host = postgres.get_container_host_ip()
        port = postgres.get_exposed_port(5432)
        user = postgres.username
        password = postgres.password
        database = postgres.dbname

        pool = await asyncpg.create_pool(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
            min_size=1,
            max_size=5,
        )

        # Create delivery_requests table
        await pool.execute("""
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
                        'pending', 'in_progress', 'delivered', 'failed', 'dead_lettered'
                    )),
                terminal_error_class TEXT,
                terminal_error_message TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                terminal_at TIMESTAMPTZ
            )
        """)

        yield pool

        await pool.close()


@pytest.mark.parametrize("mark", db_tests_mark)
async def test_queue_depth_empty(delivery_pool, mark):
    """Queue depth with no deliveries."""
    result = await messenger_queue_depth(delivery_pool)

    assert result["total_in_flight"] == 0
    assert result["by_status"] == {}
    assert result["by_channel"] == {}


@pytest.mark.parametrize("mark", db_tests_mark)
async def test_queue_depth_with_deliveries(delivery_pool, mark):
    """Queue depth with in-flight deliveries."""
    # Insert test deliveries
    await delivery_pool.execute("""
        INSERT INTO delivery_requests
            (idempotency_key, origin_butler, channel, intent, target_identity,
             message_content, request_envelope, status)
        VALUES
            ('key1', 'health', 'telegram', 'send', 'user1', 'msg1', '{}', 'pending'),
            ('key2', 'health', 'telegram', 'send', 'user2', 'msg2', '{}', 'pending'),
            ('key3', 'health', 'email', 'send', 'user3', 'msg3', '{}', 'in_progress'),
            ('key4', 'health', 'email', 'send', 'user4', 'msg4', '{}', 'delivered')
    """)

    result = await messenger_queue_depth(delivery_pool)

    assert result["total_in_flight"] == 3  # pending + in_progress
    assert result["by_status"]["pending"] == 2
    assert result["by_status"]["in_progress"] == 1
    assert result["by_channel"]["telegram"] == 2
    assert result["by_channel"]["email"] == 1


@pytest.mark.parametrize("mark", db_tests_mark)
async def test_queue_depth_filtered_by_channel(delivery_pool, mark):
    """Queue depth filtered by channel."""
    await delivery_pool.execute("""
        INSERT INTO delivery_requests
            (idempotency_key, origin_butler, channel, intent, target_identity,
             message_content, request_envelope, status)
        VALUES
            ('key1', 'health', 'telegram', 'send', 'user1', 'msg1', '{}', 'pending'),
            ('key2', 'health', 'email', 'send', 'user2', 'msg2', '{}', 'pending')
    """)

    result = await messenger_queue_depth(delivery_pool, channel="telegram")

    assert result["total_in_flight"] == 1
    assert result["by_status"]["pending"] == 1
    assert result["by_channel"] == {}  # Not included when filtering


# ============================================================================
# Test messenger_delivery_stats (requires DB)
# ============================================================================


@pytest.fixture
async def stats_pool():
    """Create a PostgreSQL pool with delivery tables."""
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16") as postgres:
        host = postgres.get_container_host_ip()
        port = postgres.get_exposed_port(5432)
        user = postgres.username
        password = postgres.password
        database = postgres.dbname

        pool = await asyncpg.create_pool(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
            min_size=1,
            max_size=5,
        )

        # Create delivery_requests table
        await pool.execute("""
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
                        'pending', 'in_progress', 'delivered', 'failed', 'dead_lettered'
                    )),
                terminal_error_class TEXT,
                terminal_error_message TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                terminal_at TIMESTAMPTZ
            )
        """)

        # Create delivery_attempts table
        await pool.execute("""
            CREATE TABLE delivery_attempts (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                delivery_request_id UUID NOT NULL
                    REFERENCES delivery_requests(id) ON DELETE CASCADE,
                attempt_number INTEGER NOT NULL,
                started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                completed_at TIMESTAMPTZ,
                latency_ms INTEGER,
                outcome TEXT NOT NULL CHECK (outcome IN (
                    'success', 'retryable_error', 'non_retryable_error', 'timeout', 'in_progress'
                )),
                error_class TEXT,
                error_message TEXT,
                provider_response JSONB,
                UNIQUE (delivery_request_id, attempt_number)
            )
        """)

        yield pool

        await pool.close()


@pytest.mark.parametrize("mark", db_tests_mark)
async def test_delivery_stats_empty(stats_pool, mark):
    """Delivery stats with no deliveries."""
    result = await messenger_delivery_stats(stats_pool)

    assert result["total_deliveries"] == 0
    assert result["success_count"] == 0
    assert result["success_rate"] == 0.0


@pytest.mark.parametrize("mark", db_tests_mark)
async def test_delivery_stats_with_deliveries(stats_pool, mark):
    """Delivery stats with successful and failed deliveries."""
    now = datetime.now(UTC)

    # Insert deliveries
    for i in range(10):
        status = "delivered" if i < 8 else "failed"
        row = await stats_pool.fetchrow(
            """
            INSERT INTO delivery_requests
                (idempotency_key, origin_butler, channel, intent, target_identity,
                 message_content, request_envelope, status, created_at)
            VALUES ($1, 'health', 'telegram', 'send', 'user1', 'msg', '{}', $2, $3)
            RETURNING id
        """,
            f"key{i}",
            status,
            now,
        )

        # Add attempt with latency
        await stats_pool.execute(
            """
            INSERT INTO delivery_attempts
                (delivery_request_id, attempt_number, latency_ms, outcome)
            VALUES ($1, 1, $2, $3)
        """,
            row["id"],
            100 + i * 50,
            "success" if status == "delivered" else "retryable_error",
        )

    result = await messenger_delivery_stats(stats_pool)

    assert result["total_deliveries"] == 10
    assert result["success_count"] == 8
    assert result["success_rate"] == 0.8
    assert result["failed_count"] == 2
    assert result["p50_latency_ms"] is not None
    assert result["p95_latency_ms"] is not None


@pytest.mark.parametrize("mark", db_tests_mark)
async def test_delivery_stats_with_time_window(stats_pool, mark):
    """Delivery stats filtered by time window."""
    now = datetime.now(UTC)
    one_hour_ago = now - timedelta(hours=1)
    two_hours_ago = now - timedelta(hours=2)

    # Insert old delivery
    await stats_pool.execute(
        """
        INSERT INTO delivery_requests
            (idempotency_key, origin_butler, channel, intent, target_identity,
             message_content, request_envelope, status, created_at)
        VALUES ('old', 'health', 'telegram', 'send', 'user1', 'msg', '{}', 'delivered', $1)
    """,
        two_hours_ago,
    )

    # Insert recent delivery
    await stats_pool.execute(
        """
        INSERT INTO delivery_requests
            (idempotency_key, origin_butler, channel, intent, target_identity,
             message_content, request_envelope, status, created_at)
        VALUES ('recent', 'health', 'telegram', 'send', 'user2', 'msg', '{}', 'delivered', $1)
    """,
        now,
    )

    result = await messenger_delivery_stats(
        stats_pool,
        since=one_hour_ago.isoformat(),
        until=now.isoformat(),
    )

    assert result["total_deliveries"] == 1  # Only recent one


@pytest.mark.parametrize("mark", db_tests_mark)
async def test_delivery_stats_grouped_by_channel(stats_pool, mark):
    """Delivery stats grouped by channel."""
    now = datetime.now(UTC)

    # Insert deliveries for different channels
    for channel in ["telegram", "telegram", "email"]:
        row = await stats_pool.fetchrow(
            """
            INSERT INTO delivery_requests
                (idempotency_key, origin_butler, channel, intent, target_identity,
                 message_content, request_envelope, status, created_at)
            VALUES ($1, 'health', $2, 'send', 'user1', 'msg', '{}', 'delivered', $3)
            RETURNING id
        """,
            f"key-{channel}-{uuid.uuid4()}",
            channel,
            now,
        )

        await stats_pool.execute(
            """
            INSERT INTO delivery_attempts
                (delivery_request_id, attempt_number, latency_ms, outcome)
            VALUES ($1, 1, 200, 'success')
        """,
            row["id"],
        )

    result = await messenger_delivery_stats(stats_pool, group_by="channel")

    assert result["total_deliveries"] == 3
    assert "groups" in result
    assert "telegram" in result["groups"]
    assert "email" in result["groups"]
    assert result["groups"]["telegram"]["total_deliveries"] == 2
    assert result["groups"]["email"]["total_deliveries"] == 1


@pytest.mark.parametrize("mark", db_tests_mark)
async def test_delivery_stats_invalid_group_by(stats_pool, mark):
    """Delivery stats with invalid group_by returns error."""
    result = await messenger_delivery_stats(stats_pool, group_by="invalid")

    assert "error" in result
    assert "Invalid group_by" in result["error"]


@pytest.mark.parametrize("mark", db_tests_mark)
async def test_delivery_stats_invalid_timestamp(stats_pool, mark):
    """Delivery stats with invalid timestamp returns error."""
    result = await messenger_delivery_stats(stats_pool, since="not-a-timestamp")

    assert "error" in result
    assert "Invalid since timestamp" in result["error"]
