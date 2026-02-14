"""Integration tests for Switchboard ingest API.

These tests verify the canonical ingest boundary behavior:
- Envelope parsing and validation
- Request context assignment
- Deduplication and idempotency
- Error handling
"""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import UTC, datetime

import asyncpg
import pytest

from butlers.tools.switchboard.ingestion.ingest import (
    IngestAcceptedResponse,
    _compute_dedupe_key,
    ingest_v1,
)
from butlers.tools.switchboard.routing.contracts import (
    IngestControlV1,
    IngestEnvelopeV1,
    IngestEventV1,
    IngestPayloadV1,
    IngestSenderV1,
    IngestSourceV1,
)

# Skip all tests if Docker not available
docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


@pytest.fixture
async def pool(provisioned_postgres_pool):
    """Provision a fresh database with message_inbox table and return a pool.

    WARNING: This fixture duplicates the database schema from the `sw_008`
    migration. If you update the `message_inbox` table schema, you must
    manually update this fixture to keep it synchronized.
    """
    async with provisioned_postgres_pool() as p:
        # Create message_inbox table (partitioned, from sw_008 migration)
        await p.execute(
            """
            CREATE TABLE message_inbox (
                id UUID NOT NULL DEFAULT gen_random_uuid(),
                received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                request_context JSONB NOT NULL DEFAULT '{}'::jsonb,
                raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                normalized_text TEXT NOT NULL,
                decomposition_output JSONB,
                dispatch_outcomes JSONB,
                response_summary TEXT,
                lifecycle_state TEXT NOT NULL DEFAULT 'accepted',
                schema_version TEXT NOT NULL DEFAULT 'message_inbox.v2',
                processing_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                final_state_at TIMESTAMPTZ,
                trace_id TEXT,
                session_id UUID,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (received_at, id)
            ) PARTITION BY RANGE (received_at)
            """
        )

        await p.execute(
            """
            CREATE INDEX ix_message_inbox_recent_received_at
            ON message_inbox (received_at DESC)
            """
        )
        await p.execute(
            """
            CREATE INDEX ix_message_inbox_ctx_source_channel_received_at
            ON message_inbox ((request_context ->> 'source_channel'), received_at DESC)
            """
        )
        await p.execute(
            """
            CREATE INDEX ix_message_inbox_ctx_source_sender_received_at
            ON message_inbox ((request_context ->> 'source_sender_identity'), received_at DESC)
            """
        )
        await p.execute(
            """
            CREATE INDEX ix_message_inbox_lifecycle_received_at
            ON message_inbox (lifecycle_state, received_at DESC)
            """
        )
        await p.execute(
            """
            CREATE UNIQUE INDEX uq_message_inbox_dedupe_key_received_at
            ON message_inbox ((request_context ->> 'dedupe_key'), received_at)
            WHERE request_context ->> 'dedupe_key' IS NOT NULL
            """
        )

        # Create partition management function
        await p.execute(
            """
            CREATE OR REPLACE FUNCTION switchboard_message_inbox_ensure_partition(
                reference_ts TIMESTAMPTZ DEFAULT now()
            ) RETURNS TEXT
            LANGUAGE plpgsql
            AS $$
            DECLARE
                month_start TIMESTAMPTZ;
                month_end TIMESTAMPTZ;
                partition_name TEXT;
            BEGIN
                month_start := date_trunc('month', reference_ts);
                month_end := month_start + INTERVAL '1 month';
                partition_name := format('message_inbox_p%s', to_char(month_start, 'YYYYMM'));

                EXECUTE format(
                    'CREATE TABLE IF NOT EXISTS %I PARTITION OF message_inbox '
                    'FOR VALUES FROM (%L) TO (%L)',
                    partition_name,
                    month_start,
                    month_end
                );

                RETURN partition_name;
            END;
            $$
            """
        )

        # Create current and next month partitions
        await p.execute("SELECT switchboard_message_inbox_ensure_partition(now())")
        await p.execute(
            "SELECT switchboard_message_inbox_ensure_partition(now() + INTERVAL '1 month')"
        )

        yield p


def _make_telegram_envelope(
    *,
    update_id: str = "123456",
    bot_id: str = "telegram_bot_main",
    sender_id: str = "user_12345",
    thread_id: str | None = None,
    text: str = "Hello, world!",
    idempotency_key: str | None = None,
) -> dict:
    """Helper to build a telegram ingest.v1 envelope."""
    return {
        "schema_version": "ingest.v1",
        "source": {
            "channel": "telegram",
            "provider": "telegram",
            "endpoint_identity": bot_id,
        },
        "event": {
            "external_event_id": update_id,
            "external_thread_id": thread_id,
            "observed_at": datetime.now(UTC).isoformat(),
        },
        "sender": {
            "identity": sender_id,
        },
        "payload": {
            "raw": {"update_id": int(update_id), "message": {"text": text}},
            "normalized_text": text,
        },
        "control": {
            "idempotency_key": idempotency_key,
            "policy_tier": "default",
        },
    }


def _make_email_envelope(
    *,
    message_id: str = "<abc123@example.com>",
    mailbox: str = "inbox@example.com",
    sender: str = "alice@example.com",
    subject: str = "Test email",
    body: str = "Email body content",
    idempotency_key: str | None = None,
) -> dict:
    """Helper to build an email ingest.v1 envelope."""
    return {
        "schema_version": "ingest.v1",
        "source": {
            "channel": "email",
            "provider": "gmail",
            "endpoint_identity": mailbox,
        },
        "event": {
            "external_event_id": message_id,
            "external_thread_id": None,
            "observed_at": datetime.now(UTC).isoformat(),
        },
        "sender": {
            "identity": sender,
        },
        "payload": {
            "raw": {"subject": subject, "body": body},
            "normalized_text": f"{subject}\n{body}",
        },
        "control": {
            "idempotency_key": idempotency_key,
            "policy_tier": "default",
        },
    }


class TestIngestV1Basic:
    """Test basic ingest.v1 acceptance and persistence."""

    async def test_ingest_telegram_envelope_success(self, pool: asyncpg.Pool) -> None:
        """Test successful ingestion of a Telegram envelope."""
        envelope = _make_telegram_envelope(
            update_id="999001",
            bot_id="test_bot",
            sender_id="user_alice",
            text="Test message",
        )

        result = await ingest_v1(pool, envelope)

        assert isinstance(result, IngestAcceptedResponse)
        assert result.status == "accepted"
        assert result.duplicate is False
        assert isinstance(result.request_id, uuid.UUID)
        assert result.request_id.version == 7

        # Verify persistence in message_inbox
        row = await pool.fetchrow(
            "SELECT * FROM message_inbox WHERE id = $1",
            result.request_id,
        )
        assert row is not None
        assert row["lifecycle_state"] == "accepted"
        assert row["normalized_text"] == "Test message"
        assert json.loads(row["request_context"])["source_channel"] == "telegram"
        assert json.loads(row["request_context"])["source_endpoint_identity"] == "test_bot"
        assert json.loads(row["request_context"])["source_sender_identity"] == "user_alice"

    async def test_ingest_email_envelope_success(self, pool: asyncpg.Pool) -> None:
        """Test successful ingestion of an email envelope."""
        envelope = _make_email_envelope(
            message_id="<test123@example.com>",
            mailbox="inbox@mybutler.com",
            sender="bob@example.com",
            subject="Test",
            body="Hello",
        )

        result = await ingest_v1(pool, envelope)

        assert result.status == "accepted"
        assert result.duplicate is False
        assert result.request_id.version == 7

        # Verify persistence
        row = await pool.fetchrow(
            "SELECT * FROM message_inbox WHERE id = $1",
            result.request_id,
        )
        assert row is not None
        assert json.loads(row["request_context"])["source_channel"] == "email"
        ctx = json.loads(row["request_context"])
        assert ctx["source_endpoint_identity"] == "inbox@mybutler.com"
        assert json.loads(row["request_context"])["source_sender_identity"] == "bob@example.com"
        assert "Test\nHello" in row["normalized_text"]


class TestIngestV1Deduplication:
    """Test deduplication and idempotency behavior."""

    async def test_duplicate_submission_returns_same_request_id(self, pool: asyncpg.Pool) -> None:
        """Duplicate submissions must return the same canonical request reference."""
        envelope = _make_telegram_envelope(
            update_id="888001",
            bot_id="dup_test_bot",
            sender_id="user_charlie",
        )

        # First submission
        result1 = await ingest_v1(pool, envelope)
        assert result1.duplicate is False

        # Second submission (duplicate)
        result2 = await ingest_v1(pool, envelope)
        assert result2.duplicate is True
        assert result2.request_id == result1.request_id

        # Verify only one row in database
        count = await pool.fetchval(
            """
            SELECT COUNT(*)
            FROM message_inbox
            WHERE request_context ->> 'source_endpoint_identity' = 'dup_test_bot'
            AND request_context ->> 'source_sender_identity' = 'user_charlie'
            """
        )
        assert count == 1

    async def test_idempotency_key_dedupe(self, pool: asyncpg.Pool) -> None:
        """Submissions with same idempotency key are deduplicated."""
        idem_key = f"test-idem-{uuid.uuid4()}"

        envelope1 = _make_telegram_envelope(
            update_id="777001",
            bot_id="idem_bot",
            sender_id="user_dave",
            text="First message",
            idempotency_key=idem_key,
        )

        envelope2 = _make_telegram_envelope(
            update_id="777002",  # different update_id
            bot_id="idem_bot",
            sender_id="user_dave",
            text="Second message",  # different text
            idempotency_key=idem_key,  # same idempotency_key
        )

        result1 = await ingest_v1(pool, envelope1)
        result2 = await ingest_v1(pool, envelope2)

        # Same idempotency key → same request_id
        assert result2.duplicate is True
        assert result2.request_id == result1.request_id

    async def test_different_bot_same_update_id_not_duplicate(self, pool: asyncpg.Pool) -> None:
        """Same update_id from different bots should NOT be deduplicated."""
        envelope1 = _make_telegram_envelope(
            update_id="666001",
            bot_id="bot_alpha",
            sender_id="user_eve",
        )

        envelope2 = _make_telegram_envelope(
            update_id="666001",  # same update_id
            bot_id="bot_beta",  # different bot
            sender_id="user_eve",
        )

        result1 = await ingest_v1(pool, envelope1)
        result2 = await ingest_v1(pool, envelope2)

        # Different endpoint_identity → different requests
        assert result2.duplicate is False
        assert result2.request_id != result1.request_id


class TestIngestV1Validation:
    """Test envelope validation and error handling."""

    async def test_invalid_schema_version_rejected(self, pool: asyncpg.Pool) -> None:
        """Envelopes with wrong schema version are rejected."""
        envelope = _make_telegram_envelope()
        envelope["schema_version"] = "ingest.v2"  # unsupported version

        with pytest.raises(ValueError, match="Invalid ingest.v1 envelope"):
            await ingest_v1(pool, envelope)

    async def test_missing_required_field_rejected(self, pool: asyncpg.Pool) -> None:
        """Envelopes missing required fields are rejected."""
        envelope = _make_telegram_envelope()
        del envelope["sender"]  # remove required field

        with pytest.raises(ValueError, match="Invalid ingest.v1 envelope"):
            await ingest_v1(pool, envelope)

    async def test_invalid_channel_provider_pair_rejected(self, pool: asyncpg.Pool) -> None:
        """Invalid channel-provider combinations are rejected."""
        envelope = _make_telegram_envelope()
        envelope["source"]["channel"] = "telegram"
        envelope["source"]["provider"] = "gmail"  # mismatched provider

        with pytest.raises(ValueError, match="Invalid ingest.v1 envelope"):
            await ingest_v1(pool, envelope)

    async def test_missing_timestamp_timezone_rejected(self, pool: asyncpg.Pool) -> None:
        """Timestamps without timezone are rejected."""
        envelope = _make_telegram_envelope()
        envelope["event"]["observed_at"] = "2026-02-15T10:00:00"  # no timezone

        with pytest.raises(ValueError, match="Invalid ingest.v1 envelope"):
            await ingest_v1(pool, envelope)


class TestIngestV1DedupeKeyComputation:
    """Test dedupe key computation logic."""

    def test_dedupe_key_with_idempotency_key(self) -> None:
        """Idempotency key takes priority in dedupe key."""
        envelope = IngestEnvelopeV1(
            schema_version="ingest.v1",
            source=IngestSourceV1(
                channel="telegram",
                provider="telegram",
                endpoint_identity="bot_test",
            ),
            event=IngestEventV1(
                external_event_id="123",
                observed_at=datetime.now(UTC).isoformat(),
            ),
            sender=IngestSenderV1(identity="user_1"),
            payload=IngestPayloadV1(raw={}, normalized_text="Hello"),
            control=IngestControlV1(idempotency_key="my-key-123"),
        )

        dedupe_key = _compute_dedupe_key(envelope)
        assert dedupe_key.startswith("idem:")
        assert "telegram" in dedupe_key
        assert "bot_test" in dedupe_key
        assert "my-key-123" in dedupe_key

    def test_dedupe_key_with_external_event_id(self) -> None:
        """External event ID used when no idempotency key."""
        envelope = IngestEnvelopeV1(
            schema_version="ingest.v1",
            source=IngestSourceV1(
                channel="telegram",
                provider="telegram",
                endpoint_identity="bot_test",
            ),
            event=IngestEventV1(
                external_event_id="update_456",
                observed_at=datetime.now(UTC).isoformat(),
            ),
            sender=IngestSenderV1(identity="user_2"),
            payload=IngestPayloadV1(raw={}, normalized_text="World"),
            control=IngestControlV1(),  # no idempotency_key
        )

        dedupe_key = _compute_dedupe_key(envelope)
        assert dedupe_key.startswith("event:")
        assert "telegram" in dedupe_key
        assert "bot_test" in dedupe_key
        assert "update_456" in dedupe_key

    def test_dedupe_key_content_hash_fallback(self) -> None:
        """Content hash used as fallback when no stable event ID."""
        now_iso = datetime.now(UTC).isoformat()
        envelope = IngestEnvelopeV1(
            schema_version="ingest.v1",
            source=IngestSourceV1(
                channel="api",
                provider="internal",
                endpoint_identity="webhook_receiver",
            ),
            event=IngestEventV1(
                external_event_id="placeholder",  # Placeholder triggers content hash fallback
                observed_at=now_iso,
            ),
            sender=IngestSenderV1(identity="api_caller"),
            payload=IngestPayloadV1(raw={}, normalized_text="Test content"),
            control=IngestControlV1(),
        )

        dedupe_key = _compute_dedupe_key(envelope)
        # Placeholder values should fall through to content hash
        assert dedupe_key.startswith("hash:")
        assert "api_caller" in dedupe_key


class TestIngestV1RequestContext:
    """Test canonical request context assignment."""

    async def test_request_context_immutable_fields(self, pool: asyncpg.Pool) -> None:
        """Verify all immutable request context fields are assigned."""
        envelope = _make_telegram_envelope(
            update_id="555001",
            bot_id="ctx_bot",
            sender_id="user_frank",
            thread_id="thread_42",
        )

        result = await ingest_v1(pool, envelope)

        row = await pool.fetchrow(
            "SELECT request_context FROM message_inbox WHERE id = $1",
            result.request_id,
        )
        assert row is not None

        ctx = json.loads(row["request_context"])
        assert ctx["request_id"] == str(result.request_id)
        assert "received_at" in ctx
        assert ctx["source_channel"] == "telegram"
        assert ctx["source_endpoint_identity"] == "ctx_bot"
        assert ctx["source_sender_identity"] == "user_frank"
        assert ctx["source_thread_identity"] == "thread_42"
        assert "dedupe_key" in ctx
        assert ctx["dedupe_strategy"] == "connector_api"

    async def test_trace_context_propagation(self, pool: asyncpg.Pool) -> None:
        """Trace context from control is propagated to request context."""
        envelope = _make_telegram_envelope()
        envelope["control"]["trace_context"] = {
            "trace_id": "abc123",
            "span_id": "def456",
        }

        result = await ingest_v1(pool, envelope)

        row = await pool.fetchrow(
            "SELECT request_context FROM message_inbox WHERE id = $1",
            result.request_id,
        )
        ctx = json.loads(row["request_context"])
        assert ctx["trace_context"]["trace_id"] == "abc123"
        assert ctx["trace_context"]["span_id"] == "def456"


class TestIngestV1Partitioning:
    """Test message_inbox partition management."""

    async def test_partition_auto_created_for_received_at(self, pool: asyncpg.Pool) -> None:
        """Partition is automatically created for received_at month."""
        envelope = _make_telegram_envelope(
            update_id="444001",
            bot_id="part_bot",
            sender_id="user_george",
        )

        result = await ingest_v1(pool, envelope)
        assert result.status == "accepted"

        # Verify row is queryable (partition exists)
        row = await pool.fetchrow(
            "SELECT id FROM message_inbox WHERE id = $1",
            result.request_id,
        )
        assert row is not None
