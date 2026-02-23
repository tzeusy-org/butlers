"""Integration tests for ingestion tier handling in Switchboard ingest API.

Covers the Tier 1/2 behavior from docs/connectors/email_ingestion_policy.md:
- Tier 1 ("full"): Standard ingest pipeline, lifecycle_state="accepted".
- Tier 2 ("metadata"): Bypass LLM, lifecycle_state="metadata_ref".
- Backward compatibility: Envelopes without ingestion_tier behave as Tier 1.
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime

import asyncpg
import pytest

from butlers.tools.switchboard.ingestion.ingest import (
    IngestAcceptedResponse,
    ingest_v1,
)

# Skip all tests if Docker not available
docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


@pytest.fixture
async def pool(provisioned_postgres_pool):
    """Provision a fresh database with message_inbox table (including ingestion_tier).

    WARNING: This fixture duplicates the database schema from sw_008 + sw_019 migrations.
    If you update the message_inbox schema, you must manually update this fixture.
    """
    async with provisioned_postgres_pool() as p:
        # Base message_inbox table (from sw_008 / test_ingest_api pattern)
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

        # Dedupe unique index (from sw_010)
        await p.execute(
            """
            CREATE UNIQUE INDEX uq_message_inbox_dedupe_key_received_at
            ON message_inbox ((request_context ->> 'dedupe_key'), received_at)
            WHERE request_context ->> 'dedupe_key' IS NOT NULL
            """
        )

        # ingestion_tier index (from sw_019)
        await p.execute(
            """
            CREATE INDEX ix_message_inbox_ingestion_tier_received_at
            ON message_inbox (ingestion_tier, received_at DESC)
            """
        )

        # Partition management function
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
                    partition_name, month_start, month_end
                );
                RETURN partition_name;
            END;
            $$
            """
        )

        # Create partitions
        await p.execute("SELECT switchboard_message_inbox_ensure_partition(now())")
        await p.execute(
            "SELECT switchboard_message_inbox_ensure_partition(now() + INTERVAL '1 month')"
        )

        yield p


def _make_tier1_email_envelope(
    *,
    message_id: str = "<t1_001@example.com>",
    mailbox: str = "gmail:user:alice@gmail.com",
    sender: str = "important@example.com",
    subject: str = "Finance Statement",
    body: str = "Your balance is $1,234.56",
) -> dict:
    """Build a Tier 1 (full) email ingest.v1 envelope."""
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
        "sender": {"identity": sender},
        "payload": {
            "raw": {"subject": subject, "body": body},
            "normalized_text": f"{subject}\n{body}",
        },
        "control": {
            "idempotency_key": f"gmail:{mailbox}:{message_id}",
            "ingestion_tier": "full",
            "policy_tier": "default",
        },
    }


def _make_tier2_email_envelope(
    *,
    message_id: str = "<t2_001@example.com>",
    mailbox: str = "gmail:user:alice@gmail.com",
    sender: str = "newsletter@example.com",
    subject: str = "Weekly Newsletter",
) -> dict:
    """Build a Tier 2 (metadata-only) email ingest.v1 envelope.

    Per policy doc section 5.2: payload.raw must be null,
    normalized_text contains subject-only text.
    """
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
        "sender": {"identity": sender},
        "payload": {
            "raw": None,
            "normalized_text": f"Subject: {subject}",
        },
        "control": {
            "idempotency_key": f"gmail:{mailbox}:{message_id}",
            "ingestion_tier": "metadata",
            "policy_tier": "default",
        },
    }


class TestTier1FullIngestion:
    """Verify Tier 1 (full) ingest behavior."""

    async def test_tier1_envelope_accepted(self, pool: asyncpg.Pool) -> None:
        """Tier 1 envelope is accepted and persisted with lifecycle_state='accepted'."""
        envelope = _make_tier1_email_envelope(message_id="<tier1_001@ex.com>")
        result = await ingest_v1(pool, envelope)

        assert isinstance(result, IngestAcceptedResponse)
        assert result.status == "accepted"
        assert result.duplicate is False

        row = await pool.fetchrow(
            "SELECT lifecycle_state, request_context, raw_payload FROM message_inbox WHERE id = $1",
            result.request_id,
        )
        assert row is not None
        assert row["lifecycle_state"] == "accepted"
        ctx = json.loads(row["request_context"])
        assert ctx["ingestion_tier"] == "full"
        raw = json.loads(row["raw_payload"])
        assert raw["control"]["ingestion_tier"] == "full"

    async def test_tier1_raw_payload_persisted(self, pool: asyncpg.Pool) -> None:
        """Tier 1 raw payload dict is persisted in raw_payload column."""
        envelope = _make_tier1_email_envelope(
            message_id="<tier1_002@ex.com>",
            subject="Salary credit",
            body="$5000 credited",
        )
        result = await ingest_v1(pool, envelope)

        row = await pool.fetchrow(
            "SELECT raw_payload FROM message_inbox WHERE id = $1",
            result.request_id,
        )
        raw = json.loads(row["raw_payload"])
        assert raw["payload"]["raw"] == {"subject": "Salary credit", "body": "$5000 credited"}
        assert raw["payload"]["raw"] is not None


class TestTier2MetadataIngestion:
    """Verify Tier 2 (metadata-only) ingest behavior."""

    async def test_tier2_envelope_accepted(self, pool: asyncpg.Pool) -> None:
        """Tier 2 (metadata) envelope is accepted and persisted."""
        envelope = _make_tier2_email_envelope(message_id="<tier2_001@ex.com>")
        result = await ingest_v1(pool, envelope)

        assert isinstance(result, IngestAcceptedResponse)
        assert result.status == "accepted"
        assert result.duplicate is False

    async def test_tier2_lifecycle_state_is_metadata_ref(self, pool: asyncpg.Pool) -> None:
        """Tier 2 envelopes must use lifecycle_state='metadata_ref' to signal bypass."""
        envelope = _make_tier2_email_envelope(message_id="<tier2_002@ex.com>")
        result = await ingest_v1(pool, envelope)

        row = await pool.fetchrow(
            "SELECT lifecycle_state FROM message_inbox WHERE id = $1",
            result.request_id,
        )
        assert row is not None
        assert row["lifecycle_state"] == "metadata_ref"

    async def test_tier2_ingestion_tier_annotated_in_request_context(
        self, pool: asyncpg.Pool
    ) -> None:
        """Tier 2 request_context must include ingestion_tier='metadata'."""
        envelope = _make_tier2_email_envelope(message_id="<tier2_003@ex.com>")
        result = await ingest_v1(pool, envelope)

        row = await pool.fetchrow(
            "SELECT request_context FROM message_inbox WHERE id = $1",
            result.request_id,
        )
        ctx = json.loads(row["request_context"])
        assert ctx["ingestion_tier"] == "metadata"

    async def test_tier2_raw_payload_stored_as_null(self, pool: asyncpg.Pool) -> None:
        """Tier 2 raw_payload.payload.raw must be null (no full body stored)."""
        envelope = _make_tier2_email_envelope(message_id="<tier2_004@ex.com>")
        result = await ingest_v1(pool, envelope)

        row = await pool.fetchrow(
            "SELECT raw_payload FROM message_inbox WHERE id = $1",
            result.request_id,
        )
        raw = json.loads(row["raw_payload"])
        assert raw["payload"]["raw"] is None

    async def test_tier2_normalized_text_contains_subject(self, pool: asyncpg.Pool) -> None:
        """Tier 2 normalized_text contains subject-only text."""
        envelope = _make_tier2_email_envelope(
            message_id="<tier2_005@ex.com>",
            subject="Weekly Tech Digest",
        )
        result = await ingest_v1(pool, envelope)

        row = await pool.fetchrow(
            "SELECT normalized_text FROM message_inbox WHERE id = $1",
            result.request_id,
        )
        assert "Subject: Weekly Tech Digest" in row["normalized_text"]

    async def test_tier2_deduplication_works(self, pool: asyncpg.Pool) -> None:
        """Tier 2 envelopes are deduplicated by the same strategy as Tier 1."""
        envelope = _make_tier2_email_envelope(message_id="<tier2_006@ex.com>")

        result1 = await ingest_v1(pool, envelope)
        result2 = await ingest_v1(pool, envelope)  # duplicate

        assert result1.duplicate is False
        assert result2.duplicate is True
        assert result2.request_id == result1.request_id


class TestIngestionTierBackwardCompatibility:
    """Verify backward compatibility: envelopes without ingestion_tier behave as Tier 1."""

    async def test_no_control_field_defaults_to_tier1(self, pool: asyncpg.Pool) -> None:
        """Envelopes with no control field at all are treated as Tier 1."""
        envelope = {
            "schema_version": "ingest.v1",
            "source": {
                "channel": "telegram",
                "provider": "telegram",
                "endpoint_identity": "legacy_bot",
            },
            "event": {
                "external_event_id": "upd_legacy_001",
                "observed_at": datetime.now(UTC).isoformat(),
            },
            "sender": {"identity": "user_legacy_001"},
            "payload": {
                "raw": {"text": "old message"},
                "normalized_text": "old message",
            },
        }

        result = await ingest_v1(pool, envelope)
        assert result.status == "accepted"

        row = await pool.fetchrow(
            "SELECT lifecycle_state, request_context FROM message_inbox WHERE id = $1",
            result.request_id,
        )
        assert row["lifecycle_state"] == "accepted"
        ctx = json.loads(row["request_context"])
        assert ctx["ingestion_tier"] == "full"

    async def test_control_without_ingestion_tier_defaults_to_tier1(
        self, pool: asyncpg.Pool
    ) -> None:
        """Envelopes with control but no ingestion_tier field default to Tier 1."""
        envelope = {
            "schema_version": "ingest.v1",
            "source": {
                "channel": "email",
                "provider": "gmail",
                "endpoint_identity": "legacy_mailbox@example.com",
            },
            "event": {
                "external_event_id": "<legacy_001@ex.com>",
                "observed_at": datetime.now(UTC).isoformat(),
            },
            "sender": {"identity": "alice@example.com"},
            "payload": {
                "raw": {"subject": "Hi", "body": "Body"},
                "normalized_text": "Hi\nBody",
            },
            "control": {
                "policy_tier": "interactive",
                # no ingestion_tier field
            },
        }

        result = await ingest_v1(pool, envelope)

        row = await pool.fetchrow(
            "SELECT lifecycle_state, request_context FROM message_inbox WHERE id = $1",
            result.request_id,
        )
        assert row["lifecycle_state"] == "accepted"
        ctx = json.loads(row["request_context"])
        assert ctx["ingestion_tier"] == "full"
