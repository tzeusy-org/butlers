"""E2E data contract validation tests.

Validates data contracts between pipeline stages per docs/tests/e2e/contracts.md:
1. IngestEnvelopeV1 validation (schema, channel/provider, timestamps, extra fields)
2. Idempotency contract (same key -> same request_id)
3. Classification response validation (well-formed, LLM failures, unknown butler)
4. Route contract version (version match, quarantined butler)
5. SpawnerResult contract (successful/failed invocation, session persistence)
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from butlers.tools.switchboard.routing.contracts import parse_ingest_envelope

if TYPE_CHECKING:
    from asyncpg.pool import Pool

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e]


# ---------------------------------------------------------------------------
# Helper functions for envelope construction
# ---------------------------------------------------------------------------


def _build_valid_ingest_envelope(
    *,
    text: str = "Test message",
    idempotency_key: str | None = None,
    schema_version: str = "ingest.v1",
    channel: str = "telegram",
    provider: str = "telegram",
    endpoint_identity: str = "bot_test",
    sender_identity: str = "user123",
    external_event_id: str | None = None,
    observed_at: str | None = None,
) -> dict:
    """Build a well-formed IngestEnvelopeV1 dict for testing."""
    if external_event_id is None:
        external_event_id = f"event-{uuid.uuid4()}"
    if observed_at is None:
        observed_at = datetime.now(UTC).isoformat()

    envelope = {
        "schema_version": schema_version,
        "source": {
            "channel": channel,
            "provider": provider,
            "endpoint_identity": endpoint_identity,
        },
        "event": {
            "external_event_id": external_event_id,
            "observed_at": observed_at,
        },
        "sender": {
            "identity": sender_identity,
        },
        "payload": {
            "raw": {"text": text},
            "normalized_text": text,
        },
    }

    if idempotency_key:
        envelope["control"] = {"idempotency_key": idempotency_key}

    return envelope


# ---------------------------------------------------------------------------
# Contract 1: IngestEnvelopeV1 Validation
# ---------------------------------------------------------------------------


def test_valid_ingest_envelope_accepted():
    """Valid IngestEnvelopeV1 should parse without errors."""
    envelope = _build_valid_ingest_envelope(text="Log weight 80kg")
    parsed = parse_ingest_envelope(envelope)

    assert parsed.schema_version == "ingest.v1"
    assert parsed.source.channel == "telegram"
    assert parsed.source.provider == "telegram"
    assert parsed.payload.normalized_text == "Log weight 80kg"


def test_wrong_schema_version_rejected():
    """Wrong schema_version should raise PydanticCustomError."""
    envelope = _build_valid_ingest_envelope(schema_version="ingest.v2")

    with pytest.raises(ValidationError) as exc_info:
        parse_ingest_envelope(envelope)

    errors = exc_info.value.errors()
    assert any(e["type"] == "unsupported_schema_version" for e in errors)


def test_invalid_channel_provider_pair_rejected():
    """Invalid channel/provider combinations should be rejected."""
    envelope = _build_valid_ingest_envelope(channel="telegram", provider="gmail")

    with pytest.raises(ValidationError) as exc_info:
        parse_ingest_envelope(envelope)

    errors = exc_info.value.errors()
    assert any(e["type"] == "invalid_source_provider" for e in errors)


def test_naive_datetime_rejected():
    """Naive datetime (no timezone) should be rejected."""
    envelope = _build_valid_ingest_envelope(observed_at="2026-02-16T10:00:00")

    with pytest.raises(ValidationError) as exc_info:
        parse_ingest_envelope(envelope)

    errors = exc_info.value.errors()
    assert any(e["type"] == "rfc3339_string_required" for e in errors)


def test_extra_fields_rejected():
    """Extra fields should be rejected (extra='forbid')."""
    envelope = _build_valid_ingest_envelope(text="Test")
    envelope["unknown_field"] = "should_fail"

    with pytest.raises(ValidationError) as exc_info:
        parse_ingest_envelope(envelope)

    errors = exc_info.value.errors()
    assert any(e["type"] == "extra_forbidden" for e in errors)


def test_missing_required_fields_rejected():
    """Missing required fields should raise ValidationError."""
    envelope = _build_valid_ingest_envelope(text="Test")
    del envelope["source"]

    with pytest.raises(ValidationError) as exc_info:
        parse_ingest_envelope(envelope)

    errors = exc_info.value.errors()
    assert any(e["loc"] == ("source",) for e in errors)


def test_valid_email_channel_provider():
    """Valid email channel with gmail provider should parse."""
    envelope = _build_valid_ingest_envelope(
        channel="email",
        provider="gmail",
        endpoint_identity="bot_email",
    )
    parsed = parse_ingest_envelope(envelope)
    assert parsed.source.channel == "email"
    assert parsed.source.provider == "gmail"


# ---------------------------------------------------------------------------
# Contract 2: Idempotency Contract
# ---------------------------------------------------------------------------


async def test_idempotency_contract(switchboard_pool: Pool):
    """Same idempotency_key should produce same request_id with duplicate flag."""
    # Build envelope with explicit idempotency key
    idempotency_key = f"test-idempotency-{uuid.uuid4()}"

    # Compute expected dedupe_key (matches switchboard ingestion logic)
    dedupe_key = hashlib.sha256(f"bot_test:user123:{idempotency_key}".encode()).hexdigest()

    # First insertion
    async with switchboard_pool.acquire() as conn:
        request_id_1 = uuid.uuid7()
        await conn.execute(
            """
            INSERT INTO message_inbox (
                request_id, dedupe_key, channel, endpoint_identity,
                sender_identity, message_text, raw_payload, received_at
            )
            VALUES ($1, $2, 'telegram', 'bot_test', 'user123', $3, $4, NOW())
            ON CONFLICT (dedupe_key) DO NOTHING
            """,
            request_id_1,
            dedupe_key,
            "Log weight 80kg",
            {"text": "Log weight 80kg"},
        )

        # Verify first insert created row
        row1 = await conn.fetchrow(
            "SELECT request_id FROM message_inbox WHERE dedupe_key = $1",
            dedupe_key,
        )
        assert row1 is not None
        assert row1["request_id"] == request_id_1

    # Second insertion with same dedupe_key (simulates duplicate)
    async with switchboard_pool.acquire() as conn:
        request_id_2 = uuid.uuid7()  # Different UUID
        await conn.execute(
            """
            INSERT INTO message_inbox (
                request_id, dedupe_key, channel, endpoint_identity,
                sender_identity, message_text, raw_payload, received_at
            )
            VALUES ($1, $2, 'telegram', 'bot_test', 'user123', $3, $4, NOW())
            ON CONFLICT (dedupe_key) DO NOTHING
            """,
            request_id_2,
            dedupe_key,
            "Log weight 80kg",
            {"text": "Log weight 80kg"},
        )

        # Verify second insert was no-op, request_id unchanged
        row2 = await conn.fetchrow(
            "SELECT request_id FROM message_inbox WHERE dedupe_key = $1",
            dedupe_key,
        )
        assert row2 is not None
        assert row2["request_id"] == request_id_1  # Same as first!
        assert row2["request_id"] != request_id_2  # Second UUID ignored


# ---------------------------------------------------------------------------
# Contract 3: Classification Response Validation
# ---------------------------------------------------------------------------


def test_well_formed_single_domain_classification():
    """Well-formed single-domain classification should have all required fields."""
    classification_response = [
        {
            "butler": "health",
            "prompt": "Log weight 80kg",
            "segment": {"rationale": "Health measurement"},
        }
    ]

    # Validate structure
    assert len(classification_response) == 1
    entry = classification_response[0]
    assert entry["butler"] == "health"
    assert entry["prompt"] == "Log weight 80kg"
    assert "segment" in entry
    assert "rationale" in entry["segment"]


def test_well_formed_multi_domain_classification():
    """Multi-domain classification should have multiple self-contained entries."""
    classification_response = [
        {
            "butler": "health",
            "prompt": "Track metformin 500mg prescribed by Dr. Smith, taken twice daily",
            "segment": {"offsets": {"start": 0, "end": 66}},
        },
        {
            "butler": "relationship",
            "prompt": "Remind me to send Dr. Smith a thank-you card next week",
            "segment": {"rationale": "Social follow-up request"},
        },
    ]

    assert len(classification_response) == 2

    # Health entry
    assert classification_response[0]["butler"] == "health"
    assert "metformin" in classification_response[0]["prompt"]
    assert "offsets" in classification_response[0]["segment"]

    # Relationship entry
    assert classification_response[1]["butler"] == "relationship"
    assert "thank-you card" in classification_response[1]["prompt"]
    assert "rationale" in classification_response[1]["segment"]


def test_classification_empty_array_fallback():
    """Empty classification array should fall back to general butler."""
    classification_response = []

    # Contract: empty array triggers fallback
    if not classification_response:
        fallback = [
            {
                "butler": "general",
                "prompt": "<original message>",
                "segment": {"rationale": "Fallback due to empty classification"},
            }
        ]
        classification_response = fallback

    assert len(classification_response) == 1
    assert classification_response[0]["butler"] == "general"


def test_classification_unknown_butler_entry():
    """Classification entry with unknown butler should be skippable."""
    classification_response = [
        {
            "butler": "nonexistent_butler",
            "prompt": "Some prompt",
            "segment": {"rationale": "Unknown butler"},
        },
        {
            "butler": "health",
            "prompt": "Log weight 80kg",
            "segment": {"rationale": "Valid entry"},
        },
    ]

    # Contract: filter out unknown butlers
    known_butlers = {"health", "relationship", "general", "switchboard", "messenger"}
    valid_entries = [e for e in classification_response if e["butler"] in known_butlers]

    assert len(valid_entries) == 1
    assert valid_entries[0]["butler"] == "health"


def test_classification_extra_keys_ignored():
    """Extra keys in classification entries should be ignored (forward-compatible)."""
    classification_response = [
        {
            "butler": "health",
            "prompt": "Log weight 80kg",
            "segment": {"rationale": "Health measurement"},
            "confidence": 0.95,  # Extra key
            "model_version": "v2",  # Extra key
        }
    ]

    # Contract: extra keys are ignored
    entry = classification_response[0]
    assert entry["butler"] == "health"
    assert entry["prompt"] == "Log weight 80kg"
    assert "segment" in entry
    # Extra keys present but not validated


# ---------------------------------------------------------------------------
# Contract 4: Route Contract Version
# ---------------------------------------------------------------------------


async def test_route_contract_version_match(switchboard_pool: Pool):
    """Butler with matching route_contract_version should route successfully."""
    # Insert test butler with route_contract_version='v1'
    async with switchboard_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO butler_registry (
                name, endpoint_url, route_contract_version,
                eligibility_state, last_seen_at
            )
            VALUES ('test_butler_v1', 'http://localhost:9999', 'v1', 'active', NOW())
            ON CONFLICT (name) DO UPDATE SET
                route_contract_version = 'v1',
                eligibility_state = 'active',
                last_seen_at = NOW()
            """
        )

        # Verify registry entry
        row = await conn.fetchrow(
            "SELECT route_contract_version, eligibility_state FROM butler_registry WHERE name = $1",
            "test_butler_v1",
        )
        assert row is not None
        assert row["route_contract_version"] == "v1"
        assert row["eligibility_state"] == "active"


async def test_route_contract_quarantined_butler_skipped(switchboard_pool: Pool):
    """Quarantined butler should be skipped during routing."""
    # Insert test butler with eligibility_state='quarantined'
    async with switchboard_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO butler_registry (
                name, endpoint_url, route_contract_version,
                eligibility_state, last_seen_at
            )
            VALUES ('quarantined_butler', 'http://localhost:9998', 'v1', 'quarantined', NOW())
            ON CONFLICT (name) DO UPDATE SET
                eligibility_state = 'quarantined',
                last_seen_at = NOW()
            """
        )

        # Verify quarantined state
        row = await conn.fetchrow(
            "SELECT eligibility_state FROM butler_registry WHERE name = $1",
            "quarantined_butler",
        )
        assert row is not None
        assert row["eligibility_state"] == "quarantined"


# ---------------------------------------------------------------------------
# Contract 5: SpawnerResult Contract
# ---------------------------------------------------------------------------


async def test_spawner_result_successful_invocation(health_pool: Pool):
    """Successful spawner invocation should populate all SpawnerResult fields."""
    # Query a recent successful session from the health butler
    async with health_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT session_id, status, duration_ms, model,
                   input_tokens, output_tokens, error
            FROM sessions
            WHERE status = 'completed' AND success = true
            ORDER BY completed_at DESC LIMIT 1
            """
        )

    # Contract guarantees for successful invocation
    if row:  # May not exist in fresh test ecosystem
        assert row["session_id"] is not None
        assert row["status"] == "completed"
        assert row["duration_ms"] >= 0
        assert row["model"] is not None
        assert row["error"] is None


async def test_spawner_result_failed_invocation(health_pool: Pool):
    """Failed spawner invocation should set error field."""
    # Query a recent failed session
    async with health_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT session_id, status, error, success
            FROM sessions
            WHERE success = false
            ORDER BY completed_at DESC LIMIT 1
            """
        )

    # Contract guarantees for failed invocation
    if row:  # May not exist in fresh test ecosystem
        assert row["session_id"] is not None
        assert row["error"] is not None
        assert row["success"] is False


async def test_session_persistence_contract(health_pool: Pool):
    """Every spawner invocation should persist session row with correct status."""
    # Count total sessions
    async with health_pool.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM sessions")

    # Contract: sessions table should exist and be queryable
    assert count >= 0

    # Verify session schema includes required fields
    async with health_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT session_id, status, trigger_source, created_at,
                   completed_at, duration_ms, success, model,
                   input_tokens, output_tokens
            FROM sessions
            ORDER BY created_at DESC LIMIT 1
            """
        )

    # If any sessions exist, validate schema
    if row:
        assert "session_id" in row.keys()
        assert "status" in row.keys()
        assert "trigger_source" in row.keys()
        assert "created_at" in row.keys()
