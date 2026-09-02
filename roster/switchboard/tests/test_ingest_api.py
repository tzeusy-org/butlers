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
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest

from butlers.api.conversation_envelope import build_dashboard_envelope
from butlers.tools.switchboard.ingestion.ingest import (
    IngestAcceptedResponse,
    _compute_content_hash_key,
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
    parse_ingest_envelope,
)


def _decode_jsonb(value: object) -> object:
    """Decode a JSONB column value, defensive against codec presence.

    Returns the value unchanged when it is already a Python dict/list (codec
    wired on the pool), or json.loads()'d when it is still a raw JSON string.
    """
    if isinstance(value, (str, bytes, bytearray)):
        return json.loads(value)
    return value


# Skip all tests if Docker not available
docker_available = shutil.which("docker") is not None

# All async tests in this file must share the session event loop so that the
# asyncpg pool (created in the session-scoped fixture loop per
# asyncio_default_fixture_loop_scope="session") is never used from a different
# loop.  Without this mark each test function gets a fresh function-scoped loop,
# which causes "got Future attached to a different loop" / asyncpg
# InterfaceError failures under pytest-xdist.
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]


@pytest.fixture
async def pool(provisioned_postgres_pool):
    """Provision a fresh database with message_inbox table and return a pool.

    WARNING: This fixture duplicates the database schema from the `sw_008`,
    `sw_015`, `sw_016`, and `sw_019` migrations. If you update the `message_inbox` table
    schema, you must manually update this fixture to keep it synchronized.

    Scoped to the real ``switchboard`` schema (bu-nz1wx) so the bare table DDL
    below lands in ``switchboard`` and the production code's schema-qualified
    ``switchboard.message_inbox`` reads/writes resolve — mirroring production's
    one-db/multi-schema topology.
    """
    async with provisioned_postgres_pool(schema="switchboard") as p:
        await p.execute("CREATE SCHEMA IF NOT EXISTS switchboard")
        # Create message_inbox table (partitioned, from sw_008 thru sw_019 migrations)
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
                attachments JSONB DEFAULT NULL,
                direction TEXT NOT NULL DEFAULT 'inbound',
                ingestion_tier TEXT NOT NULL DEFAULT 'full',
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

        # Create public.ingestion_events table (core_019 migration)
        await p.execute(
            """
            CREATE TABLE IF NOT EXISTS public.ingestion_events (
                id                       UUID PRIMARY KEY,
                received_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
                source_channel           TEXT NOT NULL,
                source_provider          TEXT NOT NULL,
                source_endpoint_identity TEXT NOT NULL,
                source_sender_identity   TEXT,
                source_sender_display_name TEXT,
                source_thread_identity   TEXT,
                external_event_id        TEXT NOT NULL,
                dedupe_key               TEXT NOT NULL,
                dedupe_strategy          TEXT NOT NULL,
                ingestion_tier           TEXT NOT NULL,
                policy_tier              TEXT NOT NULL,
                triage_decision          TEXT,
                triage_target            TEXT,
                CONSTRAINT uq_ingestion_events_dedupe_key UNIQUE (dedupe_key)
            )
            """
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
    reply_target_ref = thread_id or "12345:1"
    chat_id = reply_target_ref.partition(":")[0]
    return {
        "schema_version": "ingest.v1",
        "source": {
            "channel": "telegram_bot",
            "provider": "telegram",
            "endpoint_identity": bot_id,
        },
        "event": {
            "external_event_id": update_id,
            "external_conversation_id": f"telegram:{chat_id}",
            "reply_target_ref": reply_target_ref,
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
            "external_conversation_id": message_id,
            "reply_target_ref": message_id,
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


def _make_dashboard_envelope(
    *,
    conversation_id: str = "conv-001",
    message_id: str = "msg-001",
    text: str = "What's my balance?",
    pinned_target: str | None = None,
) -> dict:
    """Helper to build a dashboard ingest.v1 envelope."""
    control: dict = {"policy_tier": "interactive"}
    if pinned_target is not None:
        control["pinned_target"] = pinned_target
    return {
        "schema_version": "ingest.v1",
        "source": {
            "channel": "dashboard",
            "provider": "internal",
            "endpoint_identity": f"dashboard:web:{conversation_id}",
        },
        "event": {
            "external_event_id": message_id,
            "external_conversation_id": f"dashboard:{conversation_id}",
            "reply_target_ref": conversation_id,
            "observed_at": datetime.now(UTC).isoformat(),
        },
        "sender": {
            "identity": "dashboard:operator",
        },
        "payload": {
            "raw": {"source": "dashboard", "conversation_id": conversation_id, "message": text},
            "normalized_text": text,
        },
        "control": control,
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
        assert _decode_jsonb(row["request_context"])["source_channel"] == "telegram_bot"
        assert _decode_jsonb(row["request_context"])["source_endpoint_identity"] == "test_bot"
        assert (
            _decode_jsonb(row["request_context"])["external_conversation_id"]
            == "telegram:12345"
        )
        assert _decode_jsonb(row["request_context"])["reply_target_ref"] == "12345:1"
        assert _decode_jsonb(row["request_context"])["source_thread_identity"] == "12345:1"
        assert _decode_jsonb(row["request_context"])["source_sender_identity"] == "user_alice"

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
        assert _decode_jsonb(row["request_context"])["source_channel"] == "email"
        ctx = _decode_jsonb(row["request_context"])
        assert ctx["source_endpoint_identity"] == "inbox@mybutler.com"
        assert _decode_jsonb(row["request_context"])["source_sender_identity"] == "bob@example.com"
        assert "Test\nHello" in row["normalized_text"]

    async def test_ingest_strips_postgres_invalid_unicode_before_jsonb_insert(
        self, pool: asyncpg.Pool
    ) -> None:
        """Lone UTF-16 surrogates inside payload.raw are stripped before JSONB persistence.

        Pydantic v2 rejects lone surrogates in typed `str` fields at envelope
        validation, so the only path to PostgreSQL is the loosely-typed
        ``payload.raw`` dict. This is the production failure mode the
        ``_strip_null_bytes`` sanitizer guards against (PostgreSQL
        ``UntranslatableCharacterError`` on JSONB insert).
        """
        envelope = _make_telegram_envelope(
            update_id="999002",
            bot_id="test_bot",
            sender_id="user_alice",
            thread_id="thread_42",
            text="Hello world",
        )
        envelope["payload"]["raw"] = {
            "message": {
                "text": "Hello \ud800world",
                "meta\udfffkey": "value\ud800",
                "nested": {"in\ud800ner": "a\udfffb"},
            }
        }

        result = await ingest_v1(pool, envelope)

        row = await pool.fetchrow(
            "SELECT request_context, raw_payload, normalized_text FROM message_inbox WHERE id = $1",
            result.request_id,
        )
        assert row is not None

        raw_payload = _decode_jsonb(row["raw_payload"])
        message = raw_payload["payload"]["raw"]["message"]
        assert message["text"] == "Hello world"
        assert message["metakey"] == "value"
        assert message["nested"] == {"inner": "ab"}


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
        envelope["source"]["channel"] = "telegram_bot"
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
                channel="telegram_bot",
                provider="telegram",
                endpoint_identity="bot_test",
            ),
            event=IngestEventV1(
                external_event_id="123",
                external_conversation_id="telegram:12345",
                reply_target_ref="12345:1",
                observed_at=datetime.now(UTC).isoformat(),
            ),
            sender=IngestSenderV1(identity="user_1"),
            payload=IngestPayloadV1(raw={}, normalized_text="Hello"),
            control=IngestControlV1(idempotency_key="my-key-123"),
        )

        dedupe_key = _compute_dedupe_key(envelope)
        assert dedupe_key.startswith("idem:")
        assert "telegram_bot" in dedupe_key
        assert "bot_test" in dedupe_key
        assert "my-key-123" in dedupe_key

    def test_dedupe_key_with_external_event_id(self) -> None:
        """External event ID used when no idempotency key."""
        envelope = IngestEnvelopeV1(
            schema_version="ingest.v1",
            source=IngestSourceV1(
                channel="telegram_bot",
                provider="telegram",
                endpoint_identity="bot_test",
            ),
            event=IngestEventV1(
                external_event_id="update_456",
                external_conversation_id="telegram:12345",
                reply_target_ref="12345:2",
                observed_at=datetime.now(UTC).isoformat(),
            ),
            sender=IngestSenderV1(identity="user_2"),
            payload=IngestPayloadV1(raw={}, normalized_text="World"),
            control=IngestControlV1(),  # no idempotency_key
        )

        dedupe_key = _compute_dedupe_key(envelope)
        assert dedupe_key.startswith("event:")
        assert "telegram_bot" in dedupe_key
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

        ctx = _decode_jsonb(row["request_context"])
        assert ctx["request_id"] == str(result.request_id)
        assert "received_at" in ctx
        assert ctx["source_channel"] == "telegram_bot"
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
        ctx = _decode_jsonb(row["request_context"])
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


def _make_evaluator_with_rules(rules: list[dict]):
    """Create an IngestionPolicyEvaluator with pre-loaded rules (no DB)."""
    import time

    from butlers.ingestion_policy import IngestionPolicyEvaluator

    evaluator = IngestionPolicyEvaluator(scope="global", db_pool=None)
    evaluator._rules = rules
    evaluator._last_loaded_at = time.monotonic()
    return evaluator


class TestPinnedTargetRouting:
    """Test the ``control.pinned_target`` envelope-pin bypass (bu-qk92y).

    ``_load_available_butlers`` is patched at its import site
    (``butlers.tools.switchboard.routing.classify``) since ``ingest_v1``
    imports it lazily inside the pinned-target branch; there is no
    ``butler_registry`` table in this fixture's schema.
    """

    async def test_pinned_target_routes_deterministically(self, pool: asyncpg.Pool) -> None:
        """A valid pinned_target produces a route_to decision, no LLM classification."""
        envelope = _make_dashboard_envelope(
            conversation_id="conv-pin-001",
            message_id="msg-pin-001",
            pinned_target="finance",
        )

        with patch(
            "butlers.tools.switchboard.routing.classify._load_available_butlers",
            new=AsyncMock(return_value=[{"name": "finance"}]),
        ):
            result = await ingest_v1(pool, envelope)

        assert result.status == "accepted"
        assert result.triage_decision == "route_to"
        assert result.triage_target == "finance"

        row = await pool.fetchrow(
            "SELECT request_context FROM message_inbox WHERE id = $1",
            result.request_id,
        )
        ctx = _decode_jsonb(row["request_context"])
        assert ctx["triage_decision"] == "route_to"
        assert ctx["triage_target"] == "finance"
        assert ctx["triage_rule_type"] == "pinned_target"

        event_row = await pool.fetchrow(
            "SELECT triage_decision, triage_target FROM public.ingestion_events WHERE id = $1",
            result.request_id,
        )
        assert event_row["triage_decision"] == "route_to"
        assert event_row["triage_target"] == "finance"

    async def test_pinned_target_wins_over_matching_policy_rule(self, pool: asyncpg.Pool) -> None:
        """pinned_target takes precedence over a global rule that would otherwise match."""
        envelope = _make_dashboard_envelope(
            conversation_id="conv-pin-002",
            message_id="msg-pin-002",
            pinned_target="finance",
        )
        # A rule matching the same channel via a permissive substring condition;
        # if evaluated it would route to "travel", not "finance".
        evaluator = _make_evaluator_with_rules(
            [
                {
                    "id": "00000000-0000-0000-0000-000000000002",
                    "rule_type": "substring",
                    "condition": {"pattern": "dashboard"},
                    "action": "route_to:travel",
                    "priority": 10,
                }
            ]
        )

        with patch(
            "butlers.tools.switchboard.routing.classify._load_available_butlers",
            new=AsyncMock(return_value=[{"name": "finance"}, {"name": "travel"}]),
        ):
            result = await ingest_v1(pool, envelope, policy_evaluator=evaluator)

        assert result.triage_decision == "route_to"
        assert result.triage_target == "finance"

    async def test_pinned_target_wins_over_thread_affinity(self, pool: asyncpg.Pool) -> None:
        """pinned_target takes precedence even on an email envelope with a thread_id.

        Thread-affinity lookup is skipped entirely when pinned_target is set, so
        no thread-affinity settings/DB fixture is required for this to pass.
        """
        envelope = _make_email_envelope(
            message_id="<pin-affinity-001@example.com>",
            sender="alerts@chase.com",
        )
        envelope["event"]["external_conversation_id"] = "thread-pin-001"
        envelope["event"]["reply_target_ref"] = "thread-pin-001"
        envelope["control"]["pinned_target"] = "finance"

        with patch(
            "butlers.tools.switchboard.routing.classify._load_available_butlers",
            new=AsyncMock(return_value=[{"name": "finance"}]),
        ):
            result = await ingest_v1(pool, envelope, enable_thread_affinity=True)

        assert result.triage_decision == "route_to"
        assert result.triage_target == "finance"

    async def test_unknown_pinned_target_rejected(self, pool: asyncpg.Pool) -> None:
        """An unknown/non-routable pinned_target is rejected, not silently misrouted."""
        envelope = _make_dashboard_envelope(
            conversation_id="conv-pin-003",
            message_id="msg-pin-003",
            pinned_target="does-not-exist",
        )

        with patch(
            "butlers.tools.switchboard.routing.classify._load_available_butlers",
            new=AsyncMock(return_value=[{"name": "finance"}]),
        ):
            with pytest.raises(ValueError, match="does-not-exist"):
                await ingest_v1(pool, envelope)

        # No message_inbox / ingestion_events row was created for the rejected submission.
        row = await pool.fetchrow(
            "SELECT id FROM message_inbox WHERE request_context ->> 'source_endpoint_identity' "
            "= $1",
            "dashboard:web:conv-pin-003",
        )
        assert row is None

    async def test_unpinned_envelope_does_not_query_butler_registry(
        self, pool: asyncpg.Pool
    ) -> None:
        """When pinned_target is absent, existing behavior is unchanged: no registry lookup."""
        envelope = _make_telegram_envelope(
            update_id="444004",
            bot_id="pin_test_bot",
            sender_id="user_no_pin",
        )
        assert "pinned_target" not in envelope["control"]

        with patch(
            "butlers.tools.switchboard.routing.classify._load_available_butlers",
            new=AsyncMock(side_effect=AssertionError("should not be called without a pin")),
        ):
            result = await ingest_v1(pool, envelope)

        assert result.status == "accepted"
        assert result.triage_decision is None


class TestDashboardConversationEnvelope:
    """Integration coverage for bu-mj2k2: the real dashboard envelope reaches
    ``ingest_v1`` with the fields ``build_dashboard_envelope`` promises, and
    a client retry reuses the persisted message's stable external-event ID —
    proving the SSE offline/retry contract without relying on a content hash.
    """

    async def test_dashboard_envelope_reaches_ingest_with_expected_fields(
        self, pool: asyncpg.Pool
    ) -> None:
        """build_dashboard_envelope's pinned_target/page_context survive ingest_v1."""
        conversation_id = uuid.uuid4()
        message_id = uuid.uuid4()
        envelope = build_dashboard_envelope(
            conversation_id=conversation_id,
            message_id=message_id,
            message_text="Alice is Bob's sister",
            pinned_target="relationship",
            page_context={
                "route": "/entities/concentration",
                "query_params": {"predicate": "child-of"},
                "entity_ref": None,
            },
        )

        with patch(
            "butlers.tools.switchboard.routing.classify._load_available_butlers",
            new=AsyncMock(return_value=[{"name": "relationship"}]),
        ):
            result = await ingest_v1(pool, envelope)

        assert result.status == "accepted"
        assert result.triage_decision == "route_to"
        assert result.triage_target == "relationship"

        row = await pool.fetchrow(
            "SELECT raw_payload, request_context FROM message_inbox WHERE id = $1",
            result.request_id,
        )
        raw_payload = _decode_jsonb(row["raw_payload"])
        assert raw_payload["source"]["channel"] == "dashboard"
        assert raw_payload["payload"]["raw"]["page_context"] == {
            "route": "/entities/concentration",
            "query_params": {"predicate": "child-of"},
            "entity_ref": None,
        }
        ctx = _decode_jsonb(row["request_context"])
        assert ctx["triage_rule_type"] == "pinned_target"

    async def test_dashboard_pin_to_switchboard_itself_is_rejected(
        self, pool: asyncpg.Pool
    ) -> None:
        """Pinning to the Switchboard staffer itself is rejected (a staffer is never routable).

        Justifies why the conversations router never pins ``control.pinned_target``
        to "switchboard" — the classification-routed widget flow must leave
        pinned_target unset so Switchboard's own classify -> route pipeline runs.
        """
        envelope = build_dashboard_envelope(
            conversation_id=uuid.uuid4(),
            message_id=uuid.uuid4(),
            message_text="the concentration chart is empty",
            pinned_target="switchboard",
        )

        # Switchboard is a staffer, never a candidate in the routable butler set.
        with patch(
            "butlers.tools.switchboard.routing.classify._load_available_butlers",
            new=AsyncMock(return_value=[{"name": "relationship"}, {"name": "finance"}]),
        ):
            with pytest.raises(ValueError, match="switchboard"):
                await ingest_v1(pool, envelope)

    async def test_invalid_pinned_target_is_validated_before_duplicate_return(
        self, pool: asyncpg.Pool
    ) -> None:
        """A duplicate-looking submission cannot bypass pinned_target validation."""
        conversation_id = uuid.uuid4()
        message_id = uuid.uuid4()
        first_envelope = build_dashboard_envelope(
            conversation_id=conversation_id,
            message_id=message_id,
            message_text="Alice's birthday is March 3rd",
            pinned_target="relationship",
        )

        with patch(
            "butlers.tools.switchboard.routing.classify._load_available_butlers",
            new=AsyncMock(return_value=[{"name": "relationship"}]),
        ):
            first_result = await ingest_v1(pool, first_envelope)
        assert first_result.duplicate is False

        invalid_retry_envelope = build_dashboard_envelope(
            conversation_id=conversation_id,
            message_id=message_id,
            message_text="Alice's birthday is March 3rd",
            pinned_target="ghost",
        )

        with patch(
            "butlers.tools.switchboard.routing.classify._load_available_butlers",
            new=AsyncMock(return_value=[{"name": "relationship"}]),
        ):
            with pytest.raises(ValueError, match="ghost"):
                await ingest_v1(pool, invalid_retry_envelope)

        row_count = await pool.fetchval(
            "SELECT count(*) FROM message_inbox WHERE request_context ->> "
            "'source_endpoint_identity' = $1",
            f"dashboard:web:{conversation_id}",
        )
        assert row_count == 1

    async def test_retry_resubmission_with_stable_message_id_ignores_time_and_context(
        self, pool: asyncpg.Pool
    ) -> None:
        """A dashboard retry dedupes by message ID, not its mutable content hash."""
        conversation_id = uuid.uuid4()
        message_id = uuid.uuid4()
        first_envelope = build_dashboard_envelope(
            conversation_id=conversation_id,
            message_id=message_id,
            message_text="Alice's birthday is March 3rd",
            pinned_target="relationship",
        )
        first_envelope["event"]["observed_at"] = "2026-07-01T09:00:00+00:00"

        retry_envelope = build_dashboard_envelope(
            conversation_id=conversation_id,
            message_id=message_id,
            message_text="Alice's birthday is March 3rd",
            conversation_context=[
                {"role": "user", "content": "What birthdays do you remember?"},
                {"role": "assistant", "content": "I remember one for Alice."},
            ],
            pinned_target="relationship",
        )
        retry_envelope["event"]["observed_at"] = "2026-07-01T11:00:00+00:00"

        first_model = parse_ingest_envelope(first_envelope)
        retry_model = parse_ingest_envelope(retry_envelope)
        assert first_model.payload.normalized_text != retry_model.payload.normalized_text
        assert _compute_content_hash_key(first_model) != _compute_content_hash_key(retry_model)
        assert _compute_dedupe_key(first_model) == _compute_dedupe_key(retry_model)

        with patch(
            "butlers.tools.switchboard.routing.classify._load_available_butlers",
            new=AsyncMock(return_value=[{"name": "relationship"}]),
        ):
            first_result = await ingest_v1(pool, first_envelope)
        assert first_result.duplicate is False

        with patch(
            "butlers.tools.switchboard.routing.classify._load_available_butlers",
            new=AsyncMock(return_value=[{"name": "relationship"}]),
        ):
            retry_result = await ingest_v1(pool, retry_envelope)

        assert retry_result.duplicate is True
        assert retry_result.request_id == first_result.request_id

    async def test_distinct_dashboard_messages_still_use_content_hash_fallback(
        self, pool: asyncpg.Pool
    ) -> None:
        """Distinct IDs with same content retain the existing hash fallback.

        This is deliberately not the dashboard retry contract: normal retries
        reuse the original message ID. It guards the generic cross-connector
        content-hash behavior while the dashboard path uses its stronger event
        identity.
        """
        conversation_id = uuid.uuid4()
        first_envelope = build_dashboard_envelope(
            conversation_id=conversation_id,
            message_id=uuid.uuid4(),
            message_text="Alice's birthday is March 3rd",
            pinned_target="relationship",
        )

        with patch(
            "butlers.tools.switchboard.routing.classify._load_available_butlers",
            new=AsyncMock(return_value=[{"name": "relationship"}]),
        ):
            first_result = await ingest_v1(pool, first_envelope)
        assert first_result.duplicate is False

        retry_envelope = build_dashboard_envelope(
            conversation_id=conversation_id,
            message_id=uuid.uuid4(),
            message_text="Alice's birthday is March 3rd",
            pinned_target="relationship",
        )
        with patch(
            "butlers.tools.switchboard.routing.classify._load_available_butlers",
            new=AsyncMock(return_value=[{"name": "relationship"}]),
        ):
            retry_result = await ingest_v1(pool, retry_envelope)

        assert retry_result.duplicate is True
        assert retry_result.request_id == first_result.request_id
