"""Real-Postgres integration tests for canonical connector stats and settings.

The mocked-pool unit tests in
roster/switchboard/tests/test_connector_stats_prometheus.py prove the
Python-side mapping and SQL shape, but they stub ``pool.fetch`` and so cannot
catch SQL that is invalid — or silently wrong — against the real schema. This
matters here because:

- ``connectors.filtered_events`` is MONTHLY-PARTITIONED (core_007); the UNION ALL
  against unpartitioned ``public.ingestion_events`` only proves correct if it
  actually reads the right partition.
- The DISTINCT ``messages_filtered`` series must never bleed into
  ``messages_ingested`` (folding skip volume into ingestion would fabricate
  ingestion that never happened).

This repo has been burned before by SQL that passed mocked-pool tests and broke
main for ~8h (PR #2598 class). See test_connector_summaries_filtered_events_db.py
for the sibling overview precedent (same UNION ALL shape, different endpoint).

The SQL-series cases call the canonical stats helper directly with a fake
``DatabaseManager`` whose ``.pool()`` returns a real asyncpg pool. The lock
case invokes the route function against the same migrated schema, and the
settings case exercises the ASGI route, so both public contracts retain real
database coverage.
"""

from __future__ import annotations

import asyncio
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import httpx
import pytest
from fastapi import FastAPI

from butlers.api.db import DatabaseManager
from butlers.api.routers import ingestion_connectors
from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from butlers.tools.switchboard.connector.lifecycle import connector_disconnect
from tests.api.auth_helpers import create_authenticated_domain_app as create_app

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]


class _RealPoolDB:
    """Fake DatabaseManager whose pool() returns the real asyncpg pool."""

    def __init__(self, pool: Any):
        self._pool = pool

    def pool(self, name: str) -> Any:
        return self._pool


class _GatedStatsConnection:
    """A real connection with a deterministic gate before its history query."""

    def __init__(
        self,
        connection: asyncpg.Connection,
        history_started: asyncio.Event,
        release_history: asyncio.Event,
    ) -> None:
        self._connection = connection
        self._history_started = history_started
        self._release_history = release_history

    def transaction(self, **kwargs):
        return self._connection.transaction(**kwargs)

    async def execute(self, *args: object):
        return await self._connection.execute(*args)

    async def fetchval(self, *args: object):
        return await self._connection.fetchval(*args)

    async def fetchrow(self, *args: object):
        return await self._connection.fetchrow(*args)

    async def fetch(self, *args: object):
        self._history_started.set()
        await self._release_history.wait()
        return await self._connection.fetch(*args)


class _GatedStatsAcquire:
    """Async context manager that retains the real pool acquisition lifecycle."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        history_started: asyncio.Event,
        release_history: asyncio.Event,
    ) -> None:
        self._acquire = pool.acquire()
        self._history_started = history_started
        self._release_history = release_history

    async def __aenter__(self) -> _GatedStatsConnection:
        connection = await self._acquire.__aenter__()
        return _GatedStatsConnection(
            connection,
            self._history_started,
            self._release_history,
        )

    async def __aexit__(self, *exc_info: object) -> bool | None:
        return await self._acquire.__aexit__(*exc_info)


class _GatedStatsPool:
    """Wrap the real pool only long enough to coordinate the lock interleaving."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        history_started: asyncio.Event,
        release_history: asyncio.Event,
    ) -> None:
        self._pool = pool
        self._history_started = history_started
        self._release_history = release_history

    def acquire(self) -> _GatedStatsAcquire:
        return _GatedStatsAcquire(self._pool, self._history_started, self._release_history)


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    """Provision the real core + Switchboard chains for canonical route SQL."""
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "switchboard"],
        schemas={"switchboard": "switchboard"},
    )


@pytest.fixture
async def pool(postgres_container, migrated_db_url: str):
    p = await asyncpg.create_pool(
        migrated_db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
        server_settings={"search_path": "switchboard,public"},
    )
    await p.execute("TRUNCATE TABLE public.ingestion_events CASCADE")
    await p.execute("TRUNCATE TABLE connectors.filtered_events CASCADE")
    await p.execute("TRUNCATE TABLE switchboard.connector_registry")
    yield p
    await p.close()


@pytest.fixture
def app(pool: asyncpg.Pool) -> FastAPI:
    """Wire the canonical connector routes to the migrated Switchboard pool."""
    db = MagicMock(spec=DatabaseManager)
    db.pool.return_value = pool
    application = create_app()
    application.dependency_overrides[ingestion_connectors._get_db_manager] = lambda: db
    return application


# ---------------------------------------------------------------------------
# Seed helpers (mirror test_connector_summaries_filtered_events_db.py)
# ---------------------------------------------------------------------------


async def _seed_registry(
    pool: asyncpg.Pool,
    *,
    connector_type: str,
    endpoint_identity: str,
    settings: dict[str, object] | None = None,
) -> None:
    """Create a live runtime row through the real Switchboard migration chain."""
    await pool.execute(
        """
        INSERT INTO connector_registry (
            connector_type,
            endpoint_identity,
            state,
            operational_role,
            settings
        ) VALUES ($1, $2, 'healthy', 'runtime_instance', $3::jsonb)
        """,
        connector_type,
        endpoint_identity,
        settings,
    )


async def _ensure_filtered_partition(pool: asyncpg.Pool, reference_ts: datetime) -> None:
    await pool.fetchval(
        "SELECT connectors.connectors_filtered_events_ensure_partition($1)", reference_ts
    )


async def _seed_ingestion_event(
    pool: asyncpg.Pool,
    *,
    received_at: datetime,
    connector_type: str,
    endpoint_identity: str,
    status: str = "ingested",
) -> None:
    event_id = uuid.uuid4()
    await pool.execute(
        """
        INSERT INTO public.ingestion_events (
            id, received_at, source_channel, source_provider,
            source_endpoint_identity, source_sender_identity, external_event_id,
            dedupe_key, dedupe_strategy, ingestion_tier, policy_tier, status
        ) VALUES ($1, $2, $3, $3, $4, NULL, $5, $6, 'connector_api', 'full', 'default', $7)
        """,
        event_id,
        received_at,
        connector_type,
        endpoint_identity,
        f"ext-{event_id}",
        f"dedupe-{event_id}",
        status,
    )


async def _seed_filtered_event(
    pool: asyncpg.Pool,
    *,
    received_at: datetime,
    connector_type: str,
    endpoint_identity: str,
    sender_identity: str = "sensor.x",
    status: str = "filtered",
) -> None:
    await _ensure_filtered_partition(pool, received_at)
    event_id = uuid.uuid4()
    await pool.execute(
        """
        INSERT INTO connectors.filtered_events (
            id, received_at, connector_type, endpoint_identity,
            external_message_id, source_channel, sender_identity,
            filter_reason, status, full_payload
        ) VALUES ($1, $2, $3, $4, $5, $3, $6, 'global_rule:skip:noise', $7, '{}'::jsonb)
        """,
        event_id,
        received_at,
        connector_type,
        endpoint_identity,
        f"ext-{event_id}",
        sender_identity,
        status,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_stats_surfaces_filtered_volume_for_fully_skip_routed_connector(
    pool: asyncpg.Pool,
) -> None:
    """A 100%-skip-routed connector (zero ingestion_events rows) still surfaces
    its filtered volume via the real UNION ALL query, distinct from ingested."""
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)

    for _ in range(4):
        await _seed_filtered_event(
            pool,
            received_at=now,
            connector_type="home_assistant",
            endpoint_identity="default",
        )

    result = await ingestion_connectors._connector_stats_from_db(
        connector_type="home_assistant",
        endpoint_identity="default",
        period="24h",
        db=_RealPoolDB(pool),
    )

    assert result.meta.hourly_events_available is True
    total_ingested = sum(r.messages_ingested for r in result.data)
    total_filtered = sum(r.messages_filtered for r in result.data)
    assert total_ingested == 0
    assert total_filtered == 4


async def test_stats_ingested_and_filtered_stay_distinct_same_hour(
    pool: asyncpg.Pool,
) -> None:
    """Ingested and filtered counts in the SAME hour never bleed into each other."""
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)

    await _seed_ingestion_event(
        pool, received_at=now, connector_type="gmail", endpoint_identity="user@example.com"
    )
    await _seed_ingestion_event(
        pool, received_at=now, connector_type="gmail", endpoint_identity="user@example.com"
    )
    for _ in range(3):
        await _seed_filtered_event(
            pool, received_at=now, connector_type="gmail", endpoint_identity="user@example.com"
        )

    result = await ingestion_connectors._connector_stats_from_db(
        connector_type="gmail",
        endpoint_identity="user@example.com",
        period="24h",
        db=_RealPoolDB(pool),
    )

    # Exactly one hour bucket, with the two series kept DISTINCT.
    assert len(result.data) == 24
    bucket = next(bucket for bucket in result.data if bucket.messages_ingested == 2)
    assert bucket.messages_ingested == 2
    assert bucket.messages_filtered == 3

    # SYNTHETIC CHRONOLOGY CONFORMANCE ONLY: the trusted disposable fixture
    # plants receiver authority while guards are disabled, then restores the
    # complete real catalog before the unmocked SQL read. This is not elapsed
    # recording/admission proof; that separate owning-role node uses real clocks.
    endpoint = "recording-conformance"
    anchor = await pool.fetchval("SELECT clock_timestamp()")
    await pool.execute(
        "SELECT switchboard.switchboard_connector_heartbeat_log_ensure_partition($1)", anchor
    )
    await pool.execute(
        "SELECT switchboard.switchboard_connector_heartbeat_log_ensure_partition($1)",
        anchor - timedelta(days=2),
    )
    catalog = await pool.fetchval("SELECT switchboard.heartbeat_recording_catalog()")
    await pool.execute("ALTER TABLE switchboard.connector_registry DISABLE TRIGGER USER")
    await pool.execute("ALTER TABLE switchboard.connector_heartbeat_log DISABLE TRIGGER USER")
    try:
        await pool.execute(
            "INSERT INTO connector_registry (connector_type,endpoint_identity,state, "
            "operational_role,first_seen_at,heartbeat_history_coverage) "
            "VALUES ('telegram_bot',$1,'healthy','runtime_instance',$2,$3)",
            endpoint,
            anchor - timedelta(days=2),
            {
                "version": 1,
                "coverage_start": (anchor - timedelta(hours=30)).isoformat(),
                "partitions": catalog,
            },
        )
        for index in range(24):
            if index in (5, 6, 7):
                continue
            await pool.execute(
                "INSERT INTO connector_heartbeat_log (connector_type, endpoint_identity, "
                "state, received_at) VALUES ('telegram_bot',$1,$2,$3)",
                endpoint,
                "error" if index == 20 else "healthy",
                anchor - timedelta(hours=24 - index) + timedelta(minutes=30),
            )
    finally:
        await pool.execute("ALTER TABLE switchboard.connector_registry ENABLE TRIGGER USER")
        await pool.execute("ALTER TABLE switchboard.connector_heartbeat_log ENABLE TRIGGER USER")
    for index, count in ((2, 2), (20, 20)):
        for _ in range(count):
            await _seed_ingestion_event(
                pool,
                received_at=anchor - timedelta(hours=24 - index) + timedelta(minutes=30),
                connector_type="telegram_bot",
                endpoint_identity=endpoint,
            )
    conformance = await ingestion_connectors._connector_stats_from_db(
        "telegram_bot", endpoint, "24h", _RealPoolDB(pool)
    )
    assert len(conformance.data) == 24
    assert conformance.data[2].messages_ingested == 2
    assert conformance.data[20].messages_ingested == 20
    # Roster and detail use one actual SQL reader and the same captured source
    # window, rather than comparing independently moving clocks.
    from butlers.api.connector_buckets import read_buckets

    async with pool.acquire() as connection:
        async with connection.transaction():
            captured = datetime.fromisoformat(conformance.meta.as_of)
            batch, _ = await read_buckets(
                connection, [("telegram_bot", endpoint)], "24h", catalog=catalog, as_of=captured
            )
            detail = await ingestion_connectors._connector_stats_from_db(
                "telegram_bot",
                endpoint,
                "24h",
                _RealPoolDB(pool),
                connection=connection,
                catalog=catalog,
                as_of=captured,
            )
    assert [item.model_dump(exclude={"hour"}) for item in detail.data] == batch[
        ("telegram_bot", endpoint)
    ]
    assert [item["messages_ingested"] for item in batch[("telegram_bot", endpoint)]] == [
        item.messages_ingested for item in conformance.data
    ]
    assert [i for i, item in enumerate(conformance.data) if item.listening == "deaf"] == [5, 6, 7]
    assert all(
        item.listening == "live" for i, item in enumerate(conformance.data) if i not in (5, 6, 7)
    )
    assert all(
        datetime.fromisoformat(item.bucket_end) <= datetime.fromisoformat(conformance.meta.as_of)
        for item in conformance.data
    )
    # Old best-effort history and a positive count never supply empty-bucket authority.
    await pool.execute(
        "UPDATE connector_registry SET last_heartbeat_at=clock_timestamp() "
        "WHERE connector_type='telegram_bot' AND endpoint_identity=$1",
        endpoint,
    )
    legacy = await ingestion_connectors._connector_stats_from_db(
        "telegram_bot", endpoint, "24h", _RealPoolDB(pool)
    )
    assert [legacy.data[i].listening for i in (5, 6, 7)] == ["unknown"] * 3
    assert legacy.data[2].messages_ingested == 2
    assert legacy.data[20].listening == "live"

    # Genuine board SQL positive: a canonical disposable session producer
    # survives the new rolling origin; every source interval is closed/real.
    from butlers.api.routers.butlers import _fetch_board_hourly_stripe
    from butlers.core.sessions import session_create
    from butlers.core.utils import generate_uuid7_string

    await session_create(
        pool, "synthetic count control", "trigger", request_id=generate_uuid7_string()
    )
    before = await pool.fetchval("SELECT clock_timestamp()")
    board, total, unavailable = await _fetch_board_hourly_stripe(pool)
    after = await pool.fetchval("SELECT clock_timestamp()")
    assert not unavailable and len(board) == 24 and total >= 1
    assert before - timedelta(hours=24) <= datetime.fromisoformat(board[0]["hour_start"])
    assert datetime.fromisoformat(board[-1]["hour_start"]) + timedelta(hours=1) <= after
    assert any(item["sessions_count"] > 0 for item in board)

    # Genuine SQL/savepoint failures preserve the independent source and the
    # outer transaction. These fixture DDL mutations never change privileges.
    from butlers.api.connector_buckets import read_buckets, recording_catalog

    await pool.execute(
        "ALTER TABLE connector_registry RENAME COLUMN "
        "heartbeat_history_coverage TO fixture_unavailable_coverage"
    )
    try:
        async with pool.acquire() as connection:
            async with connection.transaction():
                captured = await connection.fetchval("SELECT clock_timestamp()")
                values, meta = await read_buckets(
                    connection, [("telegram_bot", endpoint)], "24h", catalog=None, as_of=captured
                )
                assert meta["hourly_events_available"] and not meta["heartbeat_history_available"]
                assert (
                    sum(item["messages_ingested"] for item in values[("telegram_bot", endpoint)])
                    == 22
                )
                assert all(
                    item["listening"] == "unknown" for item in values[("telegram_bot", endpoint)]
                )
                assert await connection.fetchval("SELECT 1") == 1
    finally:
        await pool.execute(
            "ALTER TABLE connector_registry RENAME COLUMN "
            "fixture_unavailable_coverage TO heartbeat_history_coverage"
        )
    # Separate acquisition sees retained count rows, not a caught aborted tx.
    assert (
        await pool.fetchval(
            "SELECT count(*) FROM public.ingestion_events WHERE source_endpoint_identity=$1",
            endpoint,
        )
        == 22
    )
    await pool.execute(
        "ALTER TABLE public.ingestion_events RENAME COLUMN "
        "source_provider TO fixture_unavailable_provider"
    )
    try:
        async with pool.acquire() as connection:
            async with connection.transaction():
                catalog = await recording_catalog(connection)
                captured = await connection.fetchval("SELECT clock_timestamp()")
                values, meta = await read_buckets(
                    connection, [("telegram_bot", endpoint)], "24h", catalog=catalog, as_of=captured
                )
                assert not meta["hourly_events_available"] and meta["heartbeat_history_available"]
                assert all(
                    item["messages_ingested"] is None for item in values[("telegram_bot", endpoint)]
                )
                assert values[("telegram_bot", endpoint)][20]["listening"] == "live"
                assert await connection.fetchval("SELECT 1") == 1
    finally:
        await pool.execute(
            "ALTER TABLE public.ingestion_events RENAME COLUMN "
            "fixture_unavailable_provider TO source_provider"
        )
    restored = await ingestion_connectors._connector_stats_from_db(
        "telegram_bot", endpoint, "24h", _RealPoolDB(pool)
    )
    assert restored.meta.hourly_events_available and restored.meta.heartbeat_history_available
    assert restored.data[2].messages_ingested == 2 and restored.data[20].messages_ingested == 20
    # Closed daily eligibility is distinct from physically retained monthly data.
    daily = await ingestion_connectors._connector_stats_from_db(
        "telegram_bot", endpoint, "30d", _RealPoolDB(pool)
    )
    assert len(daily.data) == 31
    assert all(item.listening == "unknown" for item in daily.data[:-7])
    assert daily.data[-1].counts_partial and daily.data[-1].listening in ("unknown", "live")


async def test_stats_matches_websocket_connector_stored_under_source_provider(
    pool: asyncpg.Pool,
) -> None:
    """A websocket connector whose type lives in source_provider (not
    source_channel) is matched via COALESCE(source_provider, source_channel)."""
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)

    # Use an actually different source channel so only provider precedence
    # can find this event (the prior equal-column fixture was non-causal).
    await _seed_ingestion_event(
        pool, received_at=now, connector_type="home_assistant", endpoint_identity="ws://ha:8123"
    )
    await pool.execute(
        "UPDATE public.ingestion_events SET source_channel='websocket' "
        "WHERE source_endpoint_identity='ws://ha:8123'"
    )

    result = await ingestion_connectors._connector_stats_from_db(
        connector_type="home_assistant",
        endpoint_identity="ws://ha:8123",
        period="24h",
        db=_RealPoolDB(pool),
    )

    assert sum(r.messages_ingested for r in result.data) == 1


async def test_stats_row_lock_prevents_disconnect_until_history_read_completes(
    pool: asyncpg.Pool,
) -> None:
    """A real soft-delete cannot interleave after the live-row check.

    ``get_connector_stats`` locks the actual migrated registry row before it
    reads history.  A second real connection is deliberately given a short
    PostgreSQL lock timeout: it must time out while the stats history query is
    paused, then the normal disconnect succeeds after the reader releases it.
    """
    connector_type = "gmail"
    endpoint_identity = "lock-test@example.invalid"
    await _seed_registry(
        pool,
        connector_type=connector_type,
        endpoint_identity=endpoint_identity,
    )

    history_started = asyncio.Event()
    release_history = asyncio.Event()
    gated_pool = _GatedStatsPool(pool, history_started, release_history)
    stats_task = asyncio.create_task(
        ingestion_connectors.get_connector_stats(
            connector_type,
            endpoint_identity,
            period="24h",
            db=_RealPoolDB(gated_pool),
        )
    )

    try:
        await asyncio.wait_for(history_started.wait(), timeout=2)
        async with pool.acquire() as disconnect_connection:
            await disconnect_connection.execute("SET lock_timeout = '250ms'")
            try:
                with pytest.raises(asyncpg.exceptions.LockNotAvailableError):
                    await connector_disconnect(
                        disconnect_connection,
                        connector_type,
                        endpoint_identity,
                    )
            finally:
                await disconnect_connection.execute("RESET lock_timeout")
    finally:
        release_history.set()
        stats = await stats_task

    assert stats.meta.hourly_events_available is True
    disconnect = await connector_disconnect(pool, connector_type, endpoint_identity)
    assert disconnect["status"] == "disconnected"
    deleted_at = await pool.fetchval(
        "SELECT deleted_at FROM connector_registry "
        "WHERE connector_type = $1 AND endpoint_identity = $2",
        connector_type,
        endpoint_identity,
    )
    assert deleted_at is not None


async def test_settings_patch_shallow_merges_real_registry_jsonb(
    app: FastAPI,
    pool: asyncpg.Pool,
) -> None:
    """The canonical PATCH retains unrelated persisted settings on the real row."""
    connector_type = "telegram_user_client"
    endpoint_identity = "settings-test"
    existing_settings = {
        "unrelated_setting": "preserved",
        "nested": {"retained": True},
    }
    await _seed_registry(
        pool,
        connector_type=connector_type,
        endpoint_identity=endpoint_identity,
        settings=existing_settings,
    )

    with patch(
        "butlers.api.routers.ingestion_connectors.emit_dashboard_audit",
        new_callable=AsyncMock,
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.patch(
                f"/api/ingestion/connectors/{connector_type}/{endpoint_identity}/settings",
                json={"settings": {"flush_interval_s": 300}},
            )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["settings"] == {
        **existing_settings,
        "flush_interval_s": 300,
    }
    row = await pool.fetchrow(
        "SELECT settings FROM connector_registry "
        "WHERE connector_type = $1 AND endpoint_identity = $2",
        connector_type,
        endpoint_identity,
    )
    assert row is not None
    assert row["settings"] == {
        **existing_settings,
        "flush_interval_s": 300,
    }
