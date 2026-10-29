"""Real-Postgres: ingest stores the sender-authentication verdict (bu-s11n0s.20).

Drives the real ``ingest_v1`` against a migrated core + switchboard topology and reads the
canonical ``public.ingestion_events`` row back.
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]


@pytest.fixture
async def pool(postgres_container):
    from butlers.testing.migration import create_migrated_test_pool

    p = await create_migrated_test_pool(
        postgres_container,
        chains=["core", "switchboard"],
        schemas={"switchboard": "switchboard"},
        pool_schema="switchboard",
    )
    try:
        yield p
    finally:
        await p.close()


def _email(auth_results: str | None) -> dict:
    headers = [{"name": "Authentication-Results", "value": auth_results}] if auth_results else []
    return {
        "schema_version": "ingest.v1",
        "source": {
            "channel": "email",
            "provider": "gmail",
            "endpoint_identity": "owner@example.org",
        },
        "event": {
            "external_event_id": f"event-{uuid.uuid4()}",
            "external_thread_id": f"thread-{uuid.uuid4()}",
            "observed_at": datetime.now(UTC).isoformat(),
        },
        "sender": {"identity": "billing@vendor.example"},
        "payload": {
            "raw": {"payload": {"headers": headers}},
            "normalized_text": "Subject: Invoice\n\nhello",
        },
        "control": {"idempotency_key": f"k-{uuid.uuid4()}"},
    }


def _telegram() -> dict:
    return {
        "schema_version": "ingest.v1",
        "source": {
            "channel": "telegram_bot",
            "provider": "telegram",
            "endpoint_identity": "bot_test",
        },
        "event": {
            "external_event_id": f"event-{uuid.uuid4()}",
            "external_conversation_id": "telegram:12345",
            "reply_target_ref": "12345:1",
            "observed_at": datetime.now(UTC).isoformat(),
        },
        "sender": {"identity": "user123"},
        "payload": {"raw": {"text": "hi"}, "normalized_text": "hi"},
    }


async def _stored(pool, envelope: dict) -> str | None:
    from butlers.tools.switchboard.ingestion.ingest import ingest_v1

    response = await ingest_v1(pool, envelope)
    assert response.status == "accepted"
    return await pool.fetchval(
        "SELECT sender_authentication FROM public.ingestion_events WHERE id = $1",
        response.request_id,
    )


@pytest.mark.parametrize(
    ("auth_results", "expected"),
    [
        ("mx; dkim=pass header.d=vendor.example", "authenticated"),
        ("mx; dmarc=fail header.from=vendor.example", "failed"),
        (None, "unverified"),
    ],
)
async def test_an_email_ingest_stores_its_verdict(pool, auth_results, expected):
    assert await _stored(pool, _email(auth_results)) == expected


async def test_a_telegram_ingest_stores_null(pool):
    assert await _stored(pool, _telegram()) is None
