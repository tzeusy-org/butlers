"""Tests for idempotency and deduplication engine."""

from __future__ import annotations

import json

# Import from local module using relative path since roster/ isn't a package
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from testcontainers.postgres import PostgresContainer

# Add roster to path for importing messenger tools
roster_path = Path(__file__).parent.parent.parent
if str(roster_path) not in sys.path:
    sys.path.insert(0, str(roster_path))

from messenger.tools.idempotency import (  # noqa: E402
    IdempotencyEngine,
    IdempotencyKey,
)


@pytest.fixture(scope="module")
def postgres_container():
    """Start a PostgreSQL container for tests."""
    with PostgresContainer("postgres:16") as postgres:
        yield postgres


@pytest.fixture(scope="function")
async def db_pool(postgres_container):
    """Create a database pool with delivery tables."""
    import asyncpg

    pool = await asyncpg.create_pool(
        host=postgres_container.get_container_host_ip(),
        port=postgres_container.get_exposed_port(5432),
        user=postgres_container.username,
        password=postgres_container.password,
        database=postgres_container.dbname,
    )

    # Create tables directly (extracted from msg_001 migration)
    async with pool.acquire() as conn:
        # delivery_requests table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS delivery_requests (
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

        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_delivery_requests_request_id
                ON delivery_requests (request_id)
                WHERE request_id IS NOT NULL
        """)

        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_delivery_requests_origin_butler
                ON delivery_requests (origin_butler, created_at DESC)
        """)

        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_delivery_requests_channel_status
                ON delivery_requests (channel, status, created_at DESC)
        """)

        # delivery_attempts table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS delivery_attempts (
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
        """)

        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_delivery_attempts_request_started
                ON delivery_attempts (delivery_request_id, started_at)
        """)

        # delivery_receipts table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS delivery_receipts (
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
        """)

        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_delivery_receipts_request
                ON delivery_receipts (delivery_request_id, received_at)
        """)

        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_delivery_receipts_provider_id
                ON delivery_receipts (provider_delivery_id)
                WHERE provider_delivery_id IS NOT NULL
        """)

        # delivery_dead_letter table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS delivery_dead_letter (
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
        """)

        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_delivery_dead_letter_replay
                ON delivery_dead_letter (replay_eligible, created_at)
                WHERE replay_eligible = true AND discarded_at IS NULL
        """)

        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_delivery_dead_letter_error_class
                ON delivery_dead_letter (error_class, created_at DESC)
        """)

    try:
        yield pool
    finally:
        await pool.close()


class TestIdempotencyKeyDerivation:
    """Test canonical idempotency key derivation."""

    def test_send_intent_with_request_id(self):
        """Test key derivation for send intent with request_id."""
        request_id = str(uuid4())
        key = IdempotencyEngine.derive_idempotency_key(
            request_id=request_id,
            origin_butler="health",
            intent="send",
            channel="telegram",
            recipient="user123",
            message="Test message",
            subject=None,
            request_context=None,
        )

        assert isinstance(key, IdempotencyKey)
        assert key.key.startswith(f"request_id:{request_id}")
        assert "origin:health" in key.key
        assert "intent:send" in key.key
        assert "channel:telegram" in key.key
        assert "target:user123" in key.key
        assert "content:" in key.key

        assert key.components["request_id"] == request_id
        assert key.components["origin_butler"] == "health"
        assert key.components["intent"] == "send"
        assert key.components["channel"] == "telegram"
        assert key.components["target"] == "user123"
        assert "content_hash" in key.components

    def test_send_intent_without_request_id(self):
        """Test key derivation for send without request_id."""
        key = IdempotencyEngine.derive_idempotency_key(
            request_id=None,
            origin_butler="health",
            intent="send",
            channel="email",
            recipient="user@example.com",
            message="Test message",
            subject="Test subject",
            request_context=None,
        )

        # Should not include request_id component
        assert "request_id:" not in key.key
        assert "origin:health" in key.key
        assert "target:user@example.com" in key.key

    def test_reply_intent_with_thread(self):
        """Test key derivation for reply intent with thread identity."""
        request_id = str(uuid4())
        request_context = {
            "request_id": request_id,
            "source_sender_identity": "sender123",
            "source_thread_identity": "thread456",
        }

        key = IdempotencyEngine.derive_idempotency_key(
            request_id=request_id,
            origin_butler="relationship",
            intent="reply",
            channel="telegram",
            recipient=None,  # Not needed for reply
            message="Reply message",
            subject=None,
            request_context=request_context,
        )

        # Target should include both sender and thread
        assert "target:sender123:thread456" in key.key

    def test_reply_intent_without_thread(self):
        """Test key derivation for reply without thread identity."""
        request_context = {
            "source_sender_identity": "sender123",
        }

        key = IdempotencyEngine.derive_idempotency_key(
            request_id=None,
            origin_butler="general",
            intent="reply",
            channel="email",
            recipient=None,
            message="Reply",
            subject=None,
            request_context=request_context,
        )

        # Target should be just sender
        assert "target:sender123" in key.key

    def test_content_hash_includes_subject(self):
        """Test that content hash includes subject when present."""
        key_with_subject = IdempotencyEngine.derive_idempotency_key(
            request_id=None,
            origin_butler="health",
            intent="send",
            channel="email",
            recipient="user@example.com",
            message="Message body",
            subject="Important",
            request_context=None,
        )

        key_without_subject = IdempotencyEngine.derive_idempotency_key(
            request_id=None,
            origin_butler="health",
            intent="send",
            channel="email",
            recipient="user@example.com",
            message="Message body",
            subject=None,
            request_context=None,
        )

        # Content hashes should differ
        assert (
            key_with_subject.components["content_hash"]
            != key_without_subject.components["content_hash"]
        )

    def test_deterministic_key_derivation(self):
        """Test that identical inputs produce identical keys."""
        request_id = str(uuid4())

        key1 = IdempotencyEngine.derive_idempotency_key(
            request_id=request_id,
            origin_butler="health",
            intent="send",
            channel="telegram",
            recipient="user123",
            message="Test",
            subject=None,
            request_context=None,
        )

        key2 = IdempotencyEngine.derive_idempotency_key(
            request_id=request_id,
            origin_butler="health",
            intent="send",
            channel="telegram",
            recipient="user123",
            message="Test",
            subject=None,
            request_context=None,
        )

        assert key1.key == key2.key
        assert key1.components == key2.components

    def test_case_normalization(self):
        """Test that keys are case-normalized."""
        key1 = IdempotencyEngine.derive_idempotency_key(
            request_id=None,
            origin_butler="Health",
            intent="SEND",
            channel="Telegram",
            recipient="User123",
            message="Test",
            subject=None,
            request_context=None,
        )

        key2 = IdempotencyEngine.derive_idempotency_key(
            request_id=None,
            origin_butler="health",
            intent="send",
            channel="telegram",
            recipient="user123",
            message="Test",
            subject=None,
            request_context=None,
        )

        # Should produce identical keys
        assert key1.key == key2.key

    def test_missing_origin_butler(self):
        """Test that missing origin_butler raises ValueError."""
        with pytest.raises(ValueError, match="origin_butler is required"):
            IdempotencyEngine.derive_idempotency_key(
                request_id=None,
                origin_butler="",
                intent="send",
                channel="telegram",
                recipient="user123",
                message="Test",
                subject=None,
                request_context=None,
            )

    def test_send_missing_recipient(self):
        """Test that send without recipient raises ValueError."""
        with pytest.raises(ValueError, match="Send intent requires explicit recipient"):
            IdempotencyEngine.derive_idempotency_key(
                request_id=None,
                origin_butler="health",
                intent="send",
                channel="telegram",
                recipient=None,
                message="Test",
                subject=None,
                request_context=None,
            )

    def test_reply_missing_request_context(self):
        """Test that reply without request_context raises ValueError."""
        with pytest.raises(ValueError, match="Reply intent requires request_context"):
            IdempotencyEngine.derive_idempotency_key(
                request_id=None,
                origin_butler="health",
                intent="reply",
                channel="telegram",
                recipient=None,
                message="Test",
                subject=None,
                request_context=None,
            )

    def test_reply_missing_sender_identity(self):
        """Test that reply without source_sender_identity raises ValueError."""
        # Empty request_context should fail
        with pytest.raises(ValueError, match="Reply requires source_sender_identity"):
            IdempotencyEngine.derive_idempotency_key(
                request_id=None,
                origin_butler="health",
                intent="reply",
                channel="telegram",
                recipient=None,
                message="Test",
                subject=None,
                request_context={"other_field": "value"},
            )


class TestDuplicateDetection:
    """Test duplicate delivery detection."""

    async def test_check_duplicate_not_found(self, db_pool):
        """Test that check_duplicate returns None for new request."""
        engine = IdempotencyEngine(db_pool)
        status = await engine.check_duplicate("nonexistent:key")
        assert status is None

    async def test_check_duplicate_pending(self, db_pool):
        """Test duplicate detection for pending delivery."""
        engine = IdempotencyEngine(db_pool)

        # Create a pending delivery
        request_id = str(uuid4())
        key = IdempotencyEngine.derive_idempotency_key(
            request_id=request_id,
            origin_butler="health",
            intent="send",
            channel="telegram",
            recipient="user123",
            message="Test",
            subject=None,
            request_context=None,
        )

        delivery_id = await engine.create_delivery_request(
            idempotency_key=key.key,
            request_id=request_id,
            origin_butler="health",
            channel="telegram",
            intent="send",
            target_identity="user123",
            message_content="Test",
            subject=None,
            request_envelope={"test": "data"},
        )

        # Check duplicate
        status = await engine.check_duplicate(key.key)
        assert status is not None
        assert status.delivery_id == delivery_id
        assert status.status == "pending"
        assert status.is_terminal is False
        assert status.terminal_result is None

    async def test_check_duplicate_delivered(self, db_pool):
        """Test duplicate detection for delivered message."""
        engine = IdempotencyEngine(db_pool)

        # Create delivery
        request_id = str(uuid4())
        key = IdempotencyEngine.derive_idempotency_key(
            request_id=request_id,
            origin_butler="health",
            intent="send",
            channel="telegram",
            recipient="user123",
            message="Test",
            subject=None,
            request_context=None,
        )

        delivery_id = await engine.create_delivery_request(
            idempotency_key=key.key,
            request_id=request_id,
            origin_butler="health",
            channel="telegram",
            intent="send",
            target_identity="user123",
            message_content="Test",
            subject=None,
            request_envelope={"test": "data"},
        )

        # Mark as delivered
        await engine.update_delivery_status(
            delivery_request_id=delivery_id,
            status="delivered",
        )

        # Record provider ID
        await engine.record_provider_delivery_id(
            delivery_request_id=delivery_id,
            provider_delivery_id="provider123",
        )

        # Check duplicate
        status = await engine.check_duplicate(key.key)
        assert status is not None
        assert status.is_terminal is True
        assert status.status == "delivered"
        assert status.terminal_result is not None
        assert status.terminal_result["status"] == "ok"
        assert status.terminal_result["delivery"]["delivery_id"] == str(delivery_id)
        assert status.terminal_result["delivery"]["provider_delivery_id"] == "provider123"

    async def test_check_duplicate_failed(self, db_pool):
        """Test duplicate detection for failed delivery."""
        engine = IdempotencyEngine(db_pool)

        # Create delivery
        key = IdempotencyEngine.derive_idempotency_key(
            request_id=None,
            origin_butler="health",
            intent="send",
            channel="telegram",
            recipient="user123",
            message="Test",
            subject=None,
            request_context=None,
        )

        delivery_id = await engine.create_delivery_request(
            idempotency_key=key.key,
            request_id=None,
            origin_butler="health",
            channel="telegram",
            intent="send",
            target_identity="user123",
            message_content="Test",
            subject=None,
            request_envelope={"test": "data"},
        )

        # Mark as failed
        await engine.update_delivery_status(
            delivery_request_id=delivery_id,
            status="failed",
            error_class="validation_error",
            error_message="Invalid recipient",
        )

        # Check duplicate
        status = await engine.check_duplicate(key.key)
        assert status is not None
        assert status.is_terminal is True
        assert status.status == "failed"
        assert status.terminal_result is not None
        assert status.terminal_result["status"] == "error"
        assert status.terminal_result["error"]["class"] == "validation_error"
        assert status.terminal_result["error"]["message"] == "Invalid recipient"
        assert status.terminal_result["error"]["retryable"] is False

    async def test_in_flight_coalescing(self, db_pool):
        """Test that concurrent duplicate requests coalesce to same delivery."""
        engine = IdempotencyEngine(db_pool)

        request_id = str(uuid4())
        key = IdempotencyEngine.derive_idempotency_key(
            request_id=request_id,
            origin_butler="health",
            intent="send",
            channel="telegram",
            recipient="user123",
            message="Test",
            subject=None,
            request_context=None,
        )

        # First request creates the delivery
        delivery_id = await engine.create_delivery_request(
            idempotency_key=key.key,
            request_id=request_id,
            origin_butler="health",
            channel="telegram",
            intent="send",
            target_identity="user123",
            message_content="Test",
            subject=None,
            request_envelope={"test": "data"},
        )

        # Second concurrent request should detect in-flight
        with pytest.raises(ValueError, match="already exists"):
            await engine.create_delivery_request(
                idempotency_key=key.key,
                request_id=request_id,
                origin_butler="health",
                channel="telegram",
                intent="send",
                target_identity="user123",
                message_content="Test",
                subject=None,
                request_envelope={"test": "data"},
            )

        # Should be able to check status instead
        status = await engine.check_duplicate(key.key)
        assert status is not None
        assert status.delivery_id == delivery_id


class TestProviderKeyPropagation:
    """Test provider delivery ID recording and retrieval."""

    async def test_record_provider_delivery_id(self, db_pool):
        """Test recording provider delivery ID."""
        engine = IdempotencyEngine(db_pool)

        # Create delivery with unique recipient
        key = IdempotencyEngine.derive_idempotency_key(
            request_id=None,
            origin_butler="health",
            intent="send",
            channel="telegram",
            recipient="user_provider_test",
            message="Test provider ID",
            subject=None,
            request_context=None,
        )

        delivery_id = await engine.create_delivery_request(
            idempotency_key=key.key,
            request_id=None,
            origin_butler="health",
            channel="telegram",
            intent="send",
            target_identity="user_provider_test",
            message_content="Test provider ID",
            subject=None,
            request_envelope={"test": "data"},
        )

        # Record provider ID
        await engine.record_provider_delivery_id(
            delivery_request_id=delivery_id,
            provider_delivery_id="telegram_msg_123",
            metadata={"raw_response": {"ok": True}},
        )

        # Verify recorded
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT provider_delivery_id, receipt_type, metadata
                FROM delivery_receipts
                WHERE delivery_request_id = $1
                """,
                delivery_id,
            )

        assert row is not None
        assert row["provider_delivery_id"] == "telegram_msg_123"
        assert row["receipt_type"] == "sent"
        metadata = json.loads(row["metadata"])
        assert metadata["raw_response"]["ok"] is True

    async def test_multiple_receipts(self, db_pool):
        """Test recording multiple receipt types for same delivery."""
        engine = IdempotencyEngine(db_pool)

        # Create delivery with unique recipient
        key = IdempotencyEngine.derive_idempotency_key(
            request_id=None,
            origin_butler="health",
            intent="send",
            channel="email",
            recipient="receipts_test@example.com",
            message="Test multiple receipts",
            subject=None,
            request_context=None,
        )

        delivery_id = await engine.create_delivery_request(
            idempotency_key=key.key,
            request_id=None,
            origin_butler="health",
            channel="email",
            intent="send",
            target_identity="receipts_test@example.com",
            message_content="Test multiple receipts",
            subject=None,
            request_envelope={"test": "data"},
        )

        # Record sent receipt
        await engine.record_provider_delivery_id(
            delivery_request_id=delivery_id,
            provider_delivery_id="email_123",
            receipt_type="sent",
        )

        # Record delivered receipt
        await engine.record_provider_delivery_id(
            delivery_request_id=delivery_id,
            provider_delivery_id="email_123",
            receipt_type="delivered",
        )

        # Verify both recorded
        async with db_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT receipt_type
                FROM delivery_receipts
                WHERE delivery_request_id = $1
                ORDER BY received_at
                """,
                delivery_id,
            )

        assert len(rows) == 2
        assert rows[0]["receipt_type"] == "sent"
        assert rows[1]["receipt_type"] == "delivered"


class TestStatusTransitions:
    """Test delivery status transitions."""

    async def test_status_update_to_in_progress(self, db_pool):
        """Test updating status to in_progress."""
        engine = IdempotencyEngine(db_pool)

        # Create delivery with unique message
        key = IdempotencyEngine.derive_idempotency_key(
            request_id=None,
            origin_butler="health",
            intent="send",
            channel="telegram",
            recipient="user_status_in_progress",
            message="Test status in_progress",
            subject=None,
            request_context=None,
        )

        delivery_id = await engine.create_delivery_request(
            idempotency_key=key.key,
            request_id=None,
            origin_butler="health",
            channel="telegram",
            intent="send",
            target_identity="user_status_in_progress",
            message_content="Test status in_progress",
            subject=None,
            request_envelope={"test": "data"},
        )

        # Update to in_progress
        await engine.update_delivery_status(
            delivery_request_id=delivery_id,
            status="in_progress",
        )

        # Verify
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT status, terminal_at FROM delivery_requests WHERE id = $1",
                delivery_id,
            )

        assert row["status"] == "in_progress"
        assert row["terminal_at"] is None  # Not terminal

    async def test_status_update_to_delivered(self, db_pool):
        """Test updating status to delivered (terminal)."""
        engine = IdempotencyEngine(db_pool)

        # Create delivery with unique message
        key = IdempotencyEngine.derive_idempotency_key(
            request_id=None,
            origin_butler="health",
            intent="send",
            channel="telegram",
            recipient="user_status_delivered",
            message="Test status delivered",
            subject=None,
            request_context=None,
        )

        delivery_id = await engine.create_delivery_request(
            idempotency_key=key.key,
            request_id=None,
            origin_butler="health",
            channel="telegram",
            intent="send",
            target_identity="user_status_delivered",
            message_content="Test status delivered",
            subject=None,
            request_envelope={"test": "data"},
        )

        # Update to delivered
        await engine.update_delivery_status(
            delivery_request_id=delivery_id,
            status="delivered",
        )

        # Verify terminal fields set
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT status, terminal_at, terminal_error_class, terminal_error_message
                FROM delivery_requests
                WHERE id = $1
                """,
                delivery_id,
            )

        assert row["status"] == "delivered"
        assert row["terminal_at"] is not None
        assert row["terminal_error_class"] is None
        assert row["terminal_error_message"] is None

    async def test_status_update_to_failed_with_error(self, db_pool):
        """Test updating status to failed with error details."""
        engine = IdempotencyEngine(db_pool)

        # Create delivery with unique message
        key = IdempotencyEngine.derive_idempotency_key(
            request_id=None,
            origin_butler="health",
            intent="send",
            channel="telegram",
            recipient="user_status_failed",
            message="Test status failed",
            subject=None,
            request_context=None,
        )

        delivery_id = await engine.create_delivery_request(
            idempotency_key=key.key,
            request_id=None,
            origin_butler="health",
            channel="telegram",
            intent="send",
            target_identity="user_status_failed",
            message_content="Test status failed",
            subject=None,
            request_envelope={"test": "data"},
        )

        # Update to failed
        await engine.update_delivery_status(
            delivery_request_id=delivery_id,
            status="failed",
            error_class="target_unavailable",
            error_message="User blocked bot",
        )

        # Verify
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT status, terminal_at, terminal_error_class, terminal_error_message
                FROM delivery_requests
                WHERE id = $1
                """,
                delivery_id,
            )

        assert row["status"] == "failed"
        assert row["terminal_at"] is not None
        assert row["terminal_error_class"] == "target_unavailable"
        assert row["terminal_error_message"] == "User blocked bot"
