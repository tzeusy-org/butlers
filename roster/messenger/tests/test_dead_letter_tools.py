"""Tests for messenger dead letter management MCP tools."""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from butlers.tools.messenger import (
    messenger_dead_letter_discard,
    messenger_dead_letter_inspect,
    messenger_dead_letter_list,
    messenger_dead_letter_replay,
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


async def _create_test_dead_letter(
    pool: asyncpg.Pool,
    origin_butler: str = "general",
    channel: str = "telegram",
    error_class: str = "timeout",
    replay_eligible: bool = True,
    discarded: bool = False,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Helper to create a test dead letter record.

    Returns (delivery_request_id, dead_letter_id).
    """
    # Create delivery request
    delivery_id = await pool.fetchval(
        """
        INSERT INTO delivery_requests (
            idempotency_key, origin_butler, channel, intent,
            target_identity, message_content, request_envelope, status,
            terminal_error_class
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        RETURNING id
        """,
        f"test-idem-{uuid.uuid4()}",
        origin_butler,
        channel,
        "send",
        "@testuser",
        "Test message",
        json.dumps({"schema_version": "notify.v1"}),
        "dead_lettered",
        error_class,
    )

    # Create attempts
    now = datetime.now(UTC)
    for i in range(1, 4):
        await pool.execute(
            """
            INSERT INTO delivery_attempts (
                delivery_request_id, attempt_number, started_at,
                completed_at, outcome, error_class
            )
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            delivery_id,
            i,
            now - timedelta(minutes=10 - i),
            now - timedelta(minutes=10 - i) + timedelta(seconds=5),
            "retryable_error" if i < 3 else "timeout",
            error_class,
        )

    # Create dead letter
    dead_letter_id = await pool.fetchval(
        """
        INSERT INTO delivery_dead_letter (
            delivery_request_id, quarantine_reason, error_class,
            error_summary, total_attempts, first_attempt_at,
            last_attempt_at, original_request_envelope,
            all_attempt_outcomes, replay_eligible,
            discarded_at, discard_reason
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
        RETURNING id
        """,
        delivery_id,
        "exhausted retries",
        error_class,
        "Failed after 3 attempts",
        3,
        now - timedelta(minutes=10),
        now - timedelta(minutes=8),
        json.dumps({"schema_version": "notify.v1"}),
        json.dumps([{"attempt": 1}, {"attempt": 2}, {"attempt": 3}]),
        replay_eligible,
        now if discarded else None,
        "test discard reason" if discarded else None,
    )

    return delivery_id, dead_letter_id


async def test_messenger_dead_letter_list_all(delivery_pool):
    """Test messenger_dead_letter_list returns all non-discarded dead letters."""
    # Create test dead letters
    await _create_test_dead_letter(delivery_pool, origin_butler="general", channel="telegram")
    await _create_test_dead_letter(delivery_pool, origin_butler="health", channel="email")
    await _create_test_dead_letter(
        delivery_pool, origin_butler="general", channel="telegram", discarded=True
    )

    # List all (should exclude discarded)
    result = await messenger_dead_letter_list(delivery_pool)

    assert "error" not in result
    assert result["count"] == 2
    assert len(result["dead_letters"]) == 2
    assert result["include_discarded"] is False
    # Verify discarded ones are excluded
    for dl in result["dead_letters"]:
        assert dl["discarded_at"] is None


async def test_messenger_dead_letter_list_include_discarded(delivery_pool):
    """Test messenger_dead_letter_list can include discarded dead letters."""
    await _create_test_dead_letter(delivery_pool, channel="telegram")
    await _create_test_dead_letter(delivery_pool, channel="telegram", discarded=True)

    # List with include_discarded=True
    result = await messenger_dead_letter_list(delivery_pool, include_discarded=True)

    assert "error" not in result
    assert result["count"] == 2
    assert result["include_discarded"] is True


async def test_messenger_dead_letter_list_filter_by_channel(delivery_pool):
    """Test messenger_dead_letter_list filters by channel."""
    await _create_test_dead_letter(delivery_pool, channel="telegram")
    await _create_test_dead_letter(delivery_pool, channel="email")
    await _create_test_dead_letter(delivery_pool, channel="telegram")

    result = await messenger_dead_letter_list(delivery_pool, channel="telegram")

    assert "error" not in result
    assert result["count"] == 2
    for dl in result["dead_letters"]:
        assert dl["channel"] == "telegram"


async def test_messenger_dead_letter_list_filter_by_origin_butler(delivery_pool):
    """Test messenger_dead_letter_list filters by origin butler."""
    await _create_test_dead_letter(delivery_pool, origin_butler="general")
    await _create_test_dead_letter(delivery_pool, origin_butler="health")
    await _create_test_dead_letter(delivery_pool, origin_butler="general")

    result = await messenger_dead_letter_list(delivery_pool, origin_butler="health")

    assert "error" not in result
    assert result["count"] == 1
    assert result["dead_letters"][0]["origin_butler"] == "health"


async def test_messenger_dead_letter_list_filter_by_error_class(delivery_pool):
    """Test messenger_dead_letter_list filters by error class."""
    await _create_test_dead_letter(delivery_pool, error_class="timeout")
    await _create_test_dead_letter(delivery_pool, error_class="target_unavailable")
    await _create_test_dead_letter(delivery_pool, error_class="timeout")

    result = await messenger_dead_letter_list(delivery_pool, error_class="timeout")

    assert "error" not in result
    assert result["count"] == 2
    for dl in result["dead_letters"]:
        assert dl["error_class"] == "timeout"


async def test_messenger_dead_letter_list_filter_by_since(delivery_pool):
    """Test messenger_dead_letter_list filters by since timestamp."""
    now = datetime.now(UTC)

    # Create old dead letter (manually set created_at)
    delivery_id_old, _ = await _create_test_dead_letter(delivery_pool, channel="telegram")
    await delivery_pool.execute(
        """
        UPDATE delivery_dead_letter
        SET created_at = $2
        WHERE delivery_request_id = $1
        """,
        delivery_id_old,
        now - timedelta(days=7),
    )

    # Create recent dead letter
    await _create_test_dead_letter(delivery_pool, channel="email")

    # Filter by since
    since_iso = (now - timedelta(days=1)).isoformat()
    result = await messenger_dead_letter_list(delivery_pool, since=since_iso)

    assert "error" not in result
    assert result["count"] == 1
    assert result["dead_letters"][0]["channel"] == "email"


async def test_messenger_dead_letter_list_limit(delivery_pool):
    """Test messenger_dead_letter_list respects limit parameter."""
    # Create 5 dead letters
    for i in range(5):
        await _create_test_dead_letter(delivery_pool, channel="telegram")

    result = await messenger_dead_letter_list(delivery_pool, limit=2)

    assert "error" not in result
    assert result["count"] == 2
    assert result["limit"] == 2
    assert len(result["dead_letters"]) == 2


async def test_messenger_dead_letter_list_invalid_since(delivery_pool):
    """Test messenger_dead_letter_list rejects invalid since timestamp."""
    result = await messenger_dead_letter_list(delivery_pool, since="not-a-timestamp")

    assert "error" in result
    assert "invalid" in result["error"].lower()


async def test_messenger_dead_letter_inspect_success(delivery_pool):
    """Test messenger_dead_letter_inspect returns full dead letter record."""
    _, dead_letter_id = await _create_test_dead_letter(
        delivery_pool, origin_butler="general", channel="telegram"
    )

    result = await messenger_dead_letter_inspect(delivery_pool, str(dead_letter_id))

    assert "error" not in result
    assert result["id"] == dead_letter_id
    assert result["origin_butler"] == "general"
    assert result["channel"] == "telegram"
    assert result["quarantine_reason"] == "exhausted retries"
    assert result["error_class"] == "timeout"
    assert result["total_attempts"] == 3
    assert "original_request_envelope" in result
    assert "all_attempt_outcomes" in result
    assert result["replay_eligible"] is True
    assert result["replay_count"] == 0
    assert "replay_eligibility_assessment" in result
    assert result["replay_eligibility_assessment"]["eligible"] is True


async def test_messenger_dead_letter_inspect_not_eligible(delivery_pool):
    """Test messenger_dead_letter_inspect shows ineligibility assessment."""
    _, dead_letter_id = await _create_test_dead_letter(delivery_pool, replay_eligible=False)

    result = await messenger_dead_letter_inspect(delivery_pool, str(dead_letter_id))

    assert "error" not in result
    assert result["replay_eligible"] is False
    assert result["replay_eligibility_assessment"]["eligible"] is False
    assert len(result["replay_eligibility_assessment"]["reasons"]) > 0


async def test_messenger_dead_letter_inspect_discarded(delivery_pool):
    """Test messenger_dead_letter_inspect shows discard info."""
    _, dead_letter_id = await _create_test_dead_letter(delivery_pool, discarded=True)

    result = await messenger_dead_letter_inspect(delivery_pool, str(dead_letter_id))

    assert "error" not in result
    assert result["discarded_at"] is not None
    assert result["discard_reason"] == "test discard reason"
    assert result["replay_eligibility_assessment"]["eligible"] is False
    assert "discarded" in str(result["replay_eligibility_assessment"]["reasons"])


async def test_messenger_dead_letter_inspect_not_found(delivery_pool):
    """Test messenger_dead_letter_inspect returns error for non-existent dead letter."""
    fake_id = str(uuid.uuid4())
    result = await messenger_dead_letter_inspect(delivery_pool, fake_id)

    assert "error" in result
    assert "not found" in result["error"].lower()


async def test_messenger_dead_letter_inspect_invalid_uuid(delivery_pool):
    """Test messenger_dead_letter_inspect rejects invalid UUID format."""
    result = await messenger_dead_letter_inspect(delivery_pool, "not-a-uuid")

    assert "error" in result
    assert "invalid" in result["error"].lower()


async def test_messenger_dead_letter_replay_success(delivery_pool):
    """Test messenger_dead_letter_replay creates new delivery request."""
    _, dead_letter_id = await _create_test_dead_letter(delivery_pool)

    result = await messenger_dead_letter_replay(delivery_pool, str(dead_letter_id))

    assert "error" not in result
    assert result["status"] == "ok"
    assert "replayed_delivery_id" in result
    assert result["original_dead_letter_id"] == str(dead_letter_id)
    assert result["replay_number"] == 1

    # Verify new delivery was created
    new_delivery_id = uuid.UUID(result["replayed_delivery_id"])
    new_delivery = await delivery_pool.fetchrow(
        "SELECT * FROM delivery_requests WHERE id = $1",
        new_delivery_id,
    )
    assert new_delivery is not None
    assert new_delivery["status"] == "pending"
    # Verify idempotency key has replay suffix
    assert "::replay-1" in new_delivery["idempotency_key"]

    # Verify replay count was incremented
    updated_dl = await delivery_pool.fetchrow(
        "SELECT replay_count FROM delivery_dead_letter WHERE id = $1",
        dead_letter_id,
    )
    assert updated_dl["replay_count"] == 1


async def test_messenger_dead_letter_replay_preserves_lineage(delivery_pool):
    """Test messenger_dead_letter_replay preserves idempotency lineage."""
    _, dead_letter_id = await _create_test_dead_letter(delivery_pool)

    # Get original idempotency key
    original = await delivery_pool.fetchrow(
        """
        SELECT dr.idempotency_key
        FROM delivery_dead_letter ddl
        JOIN delivery_requests dr ON ddl.delivery_request_id = dr.id
        WHERE ddl.id = $1
        """,
        dead_letter_id,
    )
    original_idem_key = original["idempotency_key"]

    # First replay
    result1 = await messenger_dead_letter_replay(delivery_pool, str(dead_letter_id))
    assert result1["status"] == "ok"
    assert result1["replay_number"] == 1

    # Second replay
    result2 = await messenger_dead_letter_replay(delivery_pool, str(dead_letter_id))
    assert result2["status"] == "ok"
    assert result2["replay_number"] == 2

    # Verify both replays have correct lineage
    replay1 = await delivery_pool.fetchrow(
        "SELECT idempotency_key FROM delivery_requests WHERE id = $1",
        uuid.UUID(result1["replayed_delivery_id"]),
    )
    assert replay1["idempotency_key"] == f"{original_idem_key}::replay-1"

    replay2 = await delivery_pool.fetchrow(
        "SELECT idempotency_key FROM delivery_requests WHERE id = $1",
        uuid.UUID(result2["replayed_delivery_id"]),
    )
    assert replay2["idempotency_key"] == f"{original_idem_key}::replay-2"


async def test_messenger_dead_letter_replay_not_eligible_flag(delivery_pool):
    """Test messenger_dead_letter_replay rejects when replay_eligible is false."""
    _, dead_letter_id = await _create_test_dead_letter(delivery_pool, replay_eligible=False)

    result = await messenger_dead_letter_replay(delivery_pool, str(dead_letter_id))

    assert "error" in result
    assert "not eligible" in result["error"].lower()
    assert "replay_eligible is false" in result["reason"]


async def test_messenger_dead_letter_replay_not_eligible_discarded(delivery_pool):
    """Test messenger_dead_letter_replay rejects discarded dead letters."""
    _, dead_letter_id = await _create_test_dead_letter(delivery_pool, discarded=True)

    result = await messenger_dead_letter_replay(delivery_pool, str(dead_letter_id))

    assert "error" in result
    assert "not eligible" in result["error"].lower()
    assert "discarded" in result["reason"].lower()


async def test_messenger_dead_letter_replay_not_found(delivery_pool):
    """Test messenger_dead_letter_replay returns error for non-existent dead letter."""
    fake_id = str(uuid.uuid4())
    result = await messenger_dead_letter_replay(delivery_pool, fake_id)

    assert "error" in result
    assert "not found" in result["error"].lower()


async def test_messenger_dead_letter_replay_invalid_uuid(delivery_pool):
    """Test messenger_dead_letter_replay rejects invalid UUID format."""
    result = await messenger_dead_letter_replay(delivery_pool, "not-a-uuid")

    assert "error" in result
    assert "invalid" in result["error"].lower()


async def test_messenger_dead_letter_discard_success(delivery_pool):
    """Test messenger_dead_letter_discard marks dead letter as discarded."""
    _, dead_letter_id = await _create_test_dead_letter(delivery_pool)

    result = await messenger_dead_letter_discard(
        delivery_pool, str(dead_letter_id), "Invalid recipient address"
    )

    assert "error" not in result
    assert result["status"] == "ok"
    assert result["dead_letter_id"] == str(dead_letter_id)
    assert result["discard_reason"] == "Invalid recipient address"
    assert result["discarded_at"] is not None

    # Verify dead letter was updated
    updated = await delivery_pool.fetchrow(
        "SELECT * FROM delivery_dead_letter WHERE id = $1",
        dead_letter_id,
    )
    assert updated["discarded_at"] is not None
    assert updated["discard_reason"] == "Invalid recipient address"
    assert updated["replay_eligible"] is False


async def test_messenger_dead_letter_discard_excluded_from_list(delivery_pool):
    """Test discarded dead letters are excluded from list by default."""
    _, dead_letter_id = await _create_test_dead_letter(delivery_pool)
    await _create_test_dead_letter(delivery_pool)

    # Discard one
    await messenger_dead_letter_discard(delivery_pool, str(dead_letter_id), "test reason")

    # List without include_discarded
    result = await messenger_dead_letter_list(delivery_pool)

    assert result["count"] == 1
    assert result["dead_letters"][0]["id"] != dead_letter_id


async def test_messenger_dead_letter_discard_already_discarded(delivery_pool):
    """Test messenger_dead_letter_discard rejects already-discarded dead letters."""
    _, dead_letter_id = await _create_test_dead_letter(delivery_pool, discarded=True)

    result = await messenger_dead_letter_discard(
        delivery_pool, str(dead_letter_id), "another reason"
    )

    assert "error" in result
    assert "already discarded" in result["error"].lower()


async def test_messenger_dead_letter_discard_empty_reason(delivery_pool):
    """Test messenger_dead_letter_discard requires non-empty reason."""
    _, dead_letter_id = await _create_test_dead_letter(delivery_pool)

    result = await messenger_dead_letter_discard(delivery_pool, str(dead_letter_id), "")

    assert "error" in result
    assert "reason" in result["error"].lower()

    # Test whitespace-only reason
    result2 = await messenger_dead_letter_discard(delivery_pool, str(dead_letter_id), "   ")

    assert "error" in result2
    assert "reason" in result2["error"].lower()


async def test_messenger_dead_letter_discard_not_found(delivery_pool):
    """Test messenger_dead_letter_discard returns error for non-existent dead letter."""
    fake_id = str(uuid.uuid4())
    result = await messenger_dead_letter_discard(delivery_pool, fake_id, "test reason")

    assert "error" in result
    assert "not found" in result["error"].lower()


async def test_messenger_dead_letter_discard_invalid_uuid(delivery_pool):
    """Test messenger_dead_letter_discard rejects invalid UUID format."""
    result = await messenger_dead_letter_discard(delivery_pool, "not-a-uuid", "test reason")

    assert "error" in result
    assert "invalid" in result["error"].lower()


async def test_messenger_dead_letter_discard_is_permanent(delivery_pool):
    """Test discard operation is permanent and cannot be undone."""
    _, dead_letter_id = await _create_test_dead_letter(delivery_pool)

    # Discard
    result = await messenger_dead_letter_discard(delivery_pool, str(dead_letter_id), "first reason")
    assert result["status"] == "ok"

    # Try to discard again
    result2 = await messenger_dead_letter_discard(
        delivery_pool, str(dead_letter_id), "second reason"
    )
    assert "error" in result2
    assert "already discarded" in result2["error"].lower()

    # Verify original reason is preserved
    assert result2["discard_reason"] == "first reason"
