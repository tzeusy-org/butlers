"""Real-Postgres: the stored sender-authentication verdict is read back per event (bu-6u90jj)."""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime

import pytest

from butlers.core.ingestion_events import ingestion_events_sender_authentication

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


async def _insert(pool, channel: str, verdict: str | None) -> str:
    event_id = uuid.uuid4()
    await pool.execute(
        """
        INSERT INTO public.ingestion_events
            (id, received_at, source_channel, source_provider, source_endpoint_identity,
             source_sender_identity, external_event_id, dedupe_key, dedupe_strategy,
             ingestion_tier, policy_tier, triage_decision, sender_authentication)
        VALUES ($1, $2, $3, 'p', 'e', 's@vendor.example', $4, $4, 'connector_api', 'full',
                'default', 'accepted', $5)
        """,
        event_id,
        datetime.now(UTC),
        channel,
        f"ext-{event_id}",
        verdict,
    )
    return str(event_id)


async def test_each_stored_verdict_and_null_read_back(pool) -> None:
    stored = {v: await _insert(pool, "email", v) for v in ("authenticated", "unverified", "failed")}
    legacy = await _insert(pool, "email", None)
    other = await _insert(pool, "telegram_bot", None)

    got = await ingestion_events_sender_authentication(pool, [*stored.values(), legacy, other])

    assert got == {event_id: verdict for verdict, event_id in stored.items()}
    assert await ingestion_events_sender_authentication(pool, []) == {}
