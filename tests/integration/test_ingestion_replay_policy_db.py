"""Real-Postgres coverage for server-authoritative ingestion replay policy.

The replay transition must prove its policy predicate against the migrated
``switchboard.connector_registry`` table in the same statement that changes
the event status. Mocked pools cannot exercise candidate matching, row locks,
or the partitioned filtered-event table.
"""

from __future__ import annotations

import asyncio
import shutil
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import asyncpg
import pytest

from butlers.core.ingestion_events import ingestion_event_replay_request
from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]

_PUBLIC_ENDPOINT = "telegram:bot:test"
_FILTERED_ENDPOINT = "telegram:user:test"


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    """Provision the real core and switchboard migration chains."""
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "switchboard"],
        schemas={"switchboard": "switchboard"},
    )


@pytest.fixture
async def migrated_pool(migrated_db_url: str) -> AsyncIterator[asyncpg.Pool]:
    """Yield a clean pool to a single fully migrated database."""
    pool = await asyncpg.create_pool(
        migrated_db_url,
        min_size=1,
        max_size=4,
        init=register_jsonb_codec,
    )
    await pool.execute(
        "TRUNCATE TABLE connectors.filtered_events, public.ingestion_events, "
        "switchboard.connector_registry CASCADE"
    )
    yield pool
    await pool.close()


async def _seed_public_failed_event(
    pool: asyncpg.Pool,
    *,
    channel: str,
    provider: str,
) -> uuid.UUID:
    event_id = uuid.uuid4()
    await pool.execute(
        """
        INSERT INTO public.ingestion_events (
            id, received_at, source_channel, source_provider,
            source_endpoint_identity, source_sender_identity, external_event_id,
            dedupe_key, dedupe_strategy, ingestion_tier, policy_tier,
            triage_decision, triage_target, status
        ) VALUES ($1, $2, $3, $4, $5, 'owner', $6, $7,
                  'connector_api', 'full', 'default', 'route', 'general', 'failed')
        """,
        event_id,
        datetime.now(UTC),
        channel,
        provider,
        _PUBLIC_ENDPOINT,
        f"external-{event_id}",
        f"dedupe-{event_id}",
    )
    return event_id


async def _seed_filtered_event(
    pool: asyncpg.Pool,
    *,
    connector_type: str,
    source_channel: str,
) -> uuid.UUID:
    received_at = datetime.now(UTC)
    await pool.fetchval(
        "SELECT connectors.connectors_filtered_events_ensure_partition($1)", received_at
    )
    event_id = uuid.uuid4()
    await pool.execute(
        """
        INSERT INTO connectors.filtered_events (
            id, received_at, connector_type, endpoint_identity,
            external_message_id, source_channel, sender_identity,
            filter_reason, status, full_payload
        ) VALUES ($1, $2, $3, $4, $5, $6, 'owner', 'filtered-for-test',
                  'filtered', '{}'::jsonb)
        """,
        event_id,
        received_at,
        connector_type,
        _FILTERED_ENDPOINT,
        f"external-{event_id}",
        source_channel,
    )
    return event_id


async def _seed_registry(
    pool: asyncpg.Pool,
    *,
    connector_type: str,
    endpoint_identity: str,
    replay_safe: bool,
) -> None:
    await pool.execute(
        """
        INSERT INTO switchboard.connector_registry (
            connector_type, endpoint_identity, replay_safe
        ) VALUES ($1, $2, $3)
        """,
        connector_type,
        endpoint_identity,
        replay_safe,
    )


async def _seed_public_policy_shape(pool: asyncpg.Pool, policy: str) -> None:
    if policy == "email":
        await _seed_registry(
            pool,
            connector_type="email",
            endpoint_identity=_PUBLIC_ENDPOINT,
            replay_safe=True,
        )
    elif policy == "unsafe":
        await _seed_registry(
            pool,
            connector_type="telegram_bot",
            endpoint_identity=_PUBLIC_ENDPOINT,
            replay_safe=False,
        )
    elif policy == "ambiguous":
        for connector_type in ("telegram_bot", "telegram"):
            await _seed_registry(
                pool,
                connector_type=connector_type,
                endpoint_identity=_PUBLIC_ENDPOINT,
                replay_safe=True,
            )
    else:
        assert policy == "missing"


async def _seed_filtered_policy_shape(pool: asyncpg.Pool, policy: str) -> None:
    if policy == "email":
        await _seed_registry(
            pool,
            connector_type="email",
            endpoint_identity=_FILTERED_ENDPOINT,
            replay_safe=True,
        )
    elif policy == "unsafe":
        await _seed_registry(
            pool,
            connector_type="telegram",
            endpoint_identity=_FILTERED_ENDPOINT,
            replay_safe=False,
        )
    elif policy == "ambiguous":
        for connector_type in ("telegram", "telegram_user_client"):
            await _seed_registry(
                pool,
                connector_type=connector_type,
                endpoint_identity=_FILTERED_ENDPOINT,
                replay_safe=True,
            )
    else:
        assert policy == "missing"


async def _public_status(pool: asyncpg.Pool, event_id: uuid.UUID) -> str:
    status = await pool.fetchval(
        "SELECT status FROM public.ingestion_events WHERE id = $1", event_id
    )
    assert isinstance(status, str)
    return status


async def _filtered_status(pool: asyncpg.Pool, event_id: uuid.UUID) -> str:
    status = await pool.fetchval(
        "SELECT status FROM connectors.filtered_events WHERE id = $1", event_id
    )
    assert isinstance(status, str)
    return status


@asynccontextmanager
async def _locked_registry_row(
    pool: asyncpg.Pool,
    *,
    connector_type: str,
    endpoint_identity: str,
) -> AsyncIterator[asyncpg.Connection]:
    """Hold the exact policy row with a real ``FOR UPDATE`` transaction lock."""
    async with pool.acquire() as connection:
        transaction = connection.transaction()
        await transaction.start()
        try:
            row = await connection.fetchrow(
                """
                SELECT connector_type
                FROM switchboard.connector_registry
                WHERE connector_type = $1 AND endpoint_identity = $2
                FOR UPDATE
                """,
                connector_type,
                endpoint_identity,
            )
            assert row is not None
            yield connection
        except BaseException:
            await transaction.rollback()
            raise
        else:
            await transaction.commit()


async def _wait_until_replay_waits_on_registry_lock(
    pool: asyncpg.Pool,
    *,
    update_relation: str = "public.ingestion_events",
) -> None:
    """Prove the replay task is blocked on the live policy row lock for one mutation."""
    async with pool.acquire() as observer:
        async with asyncio.timeout(5):
            while not await observer.fetchval(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM pg_stat_activity
                    WHERE datname = current_database()
                      AND wait_event_type = 'Lock'
                      AND wait_event = 'transactionid'
                      AND query LIKE $1
                )
                """,
                f"%UPDATE {update_relation}%",
            ):
                await asyncio.sleep(0.01)


@pytest.mark.parametrize("policy", ["email", "missing", "unsafe", "ambiguous"])
async def test_public_replay_refuses_unsafe_policy(
    migrated_pool: asyncpg.Pool,
    policy: str,
) -> None:
    """Email, absent, unsafe, and ambiguous policies cannot transition a public row."""
    channel, provider = ("email", "gmail") if policy == "email" else ("telegram_bot", "telegram")
    event_id = await _seed_public_failed_event(
        migrated_pool,
        channel=channel,
        provider=provider,
    )
    await _seed_public_policy_shape(migrated_pool, policy)

    result = await ingestion_event_replay_request(migrated_pool, event_id)

    assert result["outcome"] == "unsafe"
    assert await _public_status(migrated_pool, event_id) == "failed"


async def test_public_channel_candidate_can_replay(migrated_pool: asyncpg.Pool) -> None:
    """A public event may match its channel-specific registry connector type."""
    event_id = await _seed_public_failed_event(
        migrated_pool,
        channel="telegram_bot",
        provider="telegram",
    )
    await _seed_registry(
        migrated_pool,
        connector_type="telegram_bot",
        endpoint_identity=_PUBLIC_ENDPOINT,
        replay_safe=True,
    )

    result = await ingestion_event_replay_request(migrated_pool, event_id)

    assert result["outcome"] == "ok"
    assert await _public_status(migrated_pool, event_id) == "ingested"


async def test_public_provider_candidate_can_replay(migrated_pool: asyncpg.Pool) -> None:
    """A public event may match its provider-specific registry connector type."""
    event_id = await _seed_public_failed_event(
        migrated_pool,
        channel="telegram_bot",
        provider="telegram",
    )
    await _seed_registry(
        migrated_pool,
        connector_type="telegram",
        endpoint_identity=_PUBLIC_ENDPOINT,
        replay_safe=True,
    )

    result = await ingestion_event_replay_request(migrated_pool, event_id)

    assert result["outcome"] == "ok"
    assert await _public_status(migrated_pool, event_id) == "ingested"


@pytest.mark.parametrize("policy", ["email", "missing", "unsafe", "ambiguous"])
async def test_filtered_replay_refuses_unsafe_policy(
    migrated_pool: asyncpg.Pool,
    policy: str,
) -> None:
    connector_type, source_channel = (
        ("gmail", "email") if policy == "email" else ("telegram", "telegram_user_client")
    )
    event_id = await _seed_filtered_event(
        migrated_pool,
        connector_type=connector_type,
        source_channel=source_channel,
    )
    await _seed_filtered_policy_shape(migrated_pool, policy)

    result = await ingestion_event_replay_request(migrated_pool, event_id)

    assert result["outcome"] == "unsafe"
    assert await _filtered_status(migrated_pool, event_id) == "filtered"


async def test_filtered_connector_type_candidate_can_replay(migrated_pool: asyncpg.Pool) -> None:
    """A filtered event may match its connector_type registry candidate."""
    event_id = await _seed_filtered_event(
        migrated_pool,
        connector_type="telegram",
        source_channel="telegram_user_client",
    )
    await _seed_registry(
        migrated_pool,
        connector_type="telegram",
        endpoint_identity=_FILTERED_ENDPOINT,
        replay_safe=True,
    )

    result = await ingestion_event_replay_request(migrated_pool, event_id)

    assert result["outcome"] == "ok"
    assert await _filtered_status(migrated_pool, event_id) == "replay_pending"


async def test_filtered_source_channel_candidate_can_replay(migrated_pool: asyncpg.Pool) -> None:
    """A filtered event may match its source_channel registry candidate."""
    event_id = await _seed_filtered_event(
        migrated_pool,
        connector_type="telegram",
        source_channel="telegram_user_client",
    )
    await _seed_registry(
        migrated_pool,
        connector_type="telegram_user_client",
        endpoint_identity=_FILTERED_ENDPOINT,
        replay_safe=True,
    )

    result = await ingestion_event_replay_request(migrated_pool, event_id)

    assert result["outcome"] == "ok"
    assert await _filtered_status(migrated_pool, event_id) == "replay_pending"


async def test_public_replay_fails_closed_when_policy_flips_while_locked(
    migrated_pool: asyncpg.Pool,
) -> None:
    """A concurrent safe-to-unsafe flip cannot permit the public transition."""
    event_id = await _seed_public_failed_event(
        migrated_pool,
        channel="telegram_bot",
        provider="telegram",
    )
    await _seed_registry(
        migrated_pool,
        connector_type="telegram_bot",
        endpoint_identity=_PUBLIC_ENDPOINT,
        replay_safe=True,
    )
    replay: asyncio.Task[dict] | None = None

    try:
        async with _locked_registry_row(
            migrated_pool,
            connector_type="telegram_bot",
            endpoint_identity=_PUBLIC_ENDPOINT,
        ) as locked_policy:
            replay = asyncio.create_task(ingestion_event_replay_request(migrated_pool, event_id))
            await _wait_until_replay_waits_on_registry_lock(migrated_pool)
            assert not replay.done()
            await locked_policy.execute(
                """
                UPDATE switchboard.connector_registry
                SET replay_safe = FALSE
                WHERE connector_type = 'telegram_bot' AND endpoint_identity = $1
                """,
                _PUBLIC_ENDPOINT,
            )

        assert replay is not None
        assert (await replay)["outcome"] == "unsafe"
        assert await _public_status(migrated_pool, event_id) == "failed"
    finally:
        if replay is not None and not replay.done():
            replay.cancel()
            await asyncio.gather(replay, return_exceptions=True)


async def test_filtered_replay_fails_closed_when_policy_flips_while_locked(
    migrated_pool: asyncpg.Pool,
) -> None:
    """A concurrent safe-to-unsafe flip cannot permit the filtered transition."""
    event_id = await _seed_filtered_event(
        migrated_pool,
        connector_type="telegram",
        source_channel="telegram_user_client",
    )
    await _seed_registry(
        migrated_pool,
        connector_type="telegram_user_client",
        endpoint_identity=_FILTERED_ENDPOINT,
        replay_safe=True,
    )
    replay: asyncio.Task[dict] | None = None

    try:
        async with _locked_registry_row(
            migrated_pool,
            connector_type="telegram_user_client",
            endpoint_identity=_FILTERED_ENDPOINT,
        ) as locked_policy:
            replay = asyncio.create_task(ingestion_event_replay_request(migrated_pool, event_id))
            await _wait_until_replay_waits_on_registry_lock(
                migrated_pool,
                update_relation="connectors.filtered_events",
            )
            assert not replay.done()
            await locked_policy.execute(
                """
                UPDATE switchboard.connector_registry
                SET replay_safe = FALSE
                WHERE connector_type = 'telegram_user_client' AND endpoint_identity = $1
                """,
                _FILTERED_ENDPOINT,
            )

        assert replay is not None
        assert (await replay)["outcome"] == "unsafe"
        assert await _filtered_status(migrated_pool, event_id) == "filtered"
    finally:
        if replay is not None and not replay.done():
            replay.cancel()
            await asyncio.gather(replay, return_exceptions=True)
