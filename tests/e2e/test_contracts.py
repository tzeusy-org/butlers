"""E2E data contract validation tests.

Validates data contracts between pipeline stages per docs/tests/e2e/contracts.md:
1. Idempotency contract (same key -> same request_id)

Note: IngestEnvelopeV1 schema validation, classification response validation,
route contract version, and SpawnerResult structure tests have been removed.
These are unit/integration-level tests covered by tests/contracts/ and
tests/core/. Only tests that require the full e2e ecosystem are retained here.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import TYPE_CHECKING

import pytest

from butlers.core.utils import generate_uuid7_string

if TYPE_CHECKING:
    from asyncpg.pool import Pool

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e]


# ---------------------------------------------------------------------------
# Contract 1: Idempotency Contract
# ---------------------------------------------------------------------------


async def test_idempotency_contract(switchboard_pool: Pool):
    """Same idempotency_key should produce same request_id with duplicate flag."""
    # Build envelope with explicit idempotency key
    idempotency_key = f"test-idempotency-{uuid.uuid4()}"

    # Compute expected dedupe_key (matches switchboard ingestion logic)
    dedupe_key = hashlib.sha256(f"bot_test:user123:{idempotency_key}".encode()).hexdigest()

    # First insertion
    async with switchboard_pool.acquire() as conn:
        request_id_1 = uuid.UUID(generate_uuid7_string())
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
        request_id_2 = uuid.UUID(generate_uuid7_string())  # Different UUID
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
