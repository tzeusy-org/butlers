"""E2E tests for switchboard ingestion flows.

Tests the switchboard ingest deduplication pipeline against the live ecosystem.

Scenarios:
1. Ingest deduplication: submit same IngestEnvelopeV1 twice via ingest_v1(),
   assert second returns duplicate=True
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from butlers.tools.switchboard.ingestion.ingest import ingest_v1

if TYPE_CHECKING:
    from asyncpg.pool import Pool


pytestmark = [pytest.mark.asyncio, pytest.mark.e2e]


# ---------------------------------------------------------------------------
# Scenario 1: Ingest deduplication
# ---------------------------------------------------------------------------


async def test_ingest_deduplication(switchboard_pool: Pool) -> None:
    """Submit same IngestEnvelopeV1 twice via ingest_v1(), assert second returns duplicate=True.

    Tests the idempotent ingestion boundary with stable dedupe_key computation.
    """
    # Build canonical ingest envelope
    now = datetime.now(UTC)
    event_id = f"test-event-{uuid4().hex[:8]}"

    envelope_payload = {
        "schema_version": "ingest.v1",
        "source": {
            "channel": "telegram",
            "provider": "telegram",
            "endpoint_identity": "test-endpoint-123",
        },
        "event": {
            "external_event_id": event_id,
            "external_thread_id": "thread-456",
            "observed_at": now.isoformat(),
        },
        "sender": {
            "identity": "user-789",
        },
        "payload": {
            "raw": {"text": "Test message for deduplication"},
            "normalized_text": "Test message for deduplication",
        },
        "control": {
            "policy_tier": "default",
        },
    }

    # First submission
    response1 = await ingest_v1(switchboard_pool, envelope_payload)

    assert response1.status == "accepted", "First submission should be accepted"
    assert response1.duplicate is False, "First submission should not be marked as duplicate"
    assert response1.request_id is not None, "First submission should return request_id"
    request_id_1 = response1.request_id

    # Second submission (identical envelope)
    response2 = await ingest_v1(switchboard_pool, envelope_payload)

    assert response2.status == "accepted", "Second submission should be accepted (idempotent)"
    assert response2.duplicate is True, "Second submission should be marked as duplicate"
    assert response2.request_id == request_id_1, "Second submission should return same request_id"

    # Verify only one row in message_inbox
    count_result = await switchboard_pool.fetchval(
        """
        SELECT COUNT(*) FROM message_inbox
        WHERE (request_context ->> 'request_id')::uuid = $1
        """,
        request_id_1,
    )
    assert count_result == 1, "Should only have 1 row in message_inbox for duplicate submissions"
