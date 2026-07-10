"""Real-Postgres integration tests for skip-aware GET /api/ingestion/connectors/summaries
(bu-scyro).

Mocked-pool unit tests (tests/api/test_connector_summaries_hourly.py,
tests/api/test_connector_device_liveness.py) prove the Python-side bucketing,
map-routing, and degraded-flag logic, but they stub ``pool.fetch`` and
therefore cannot catch SQL that is invalid — or silently wrong — against the
real schema. In particular:

- ``connectors.filtered_events`` is a MONTHLY-PARTITIONED table (core_007).
  The UNION ALL against unpartitioned ``public.ingestion_events`` only proves
  correct if it actually reads across the right partitions.
- The device-liveness ``sender_identity <> ''`` exclusion filter is pure SQL —
  a mocked pool cannot exercise it at all, since the mock just returns
  whatever rows the test hands it.

This repo has been burned before by SQL that passed mocked-pool tests and
broke main for ~8h (PR #2598 class). See test_ingestion_events_histogram_db.py
for the sibling precedent (same UNION ALL shape, different query).
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import asyncpg
import httpx
import pytest
from fastapi import FastAPI

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]

SUMMARIES_PATH = "/api/ingestion/connectors/summaries"
BASE_URL = "http://test"


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    """Provision the core chain — public.ingestion_events + connectors.filtered_events."""
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core"],
    )


@pytest.fixture
async def pool(postgres_container, migrated_db_url: str):
    p = await asyncpg.create_pool(
        migrated_db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
    )
    await p.execute("TRUNCATE TABLE public.ingestion_events CASCADE")
    await p.execute("TRUNCATE TABLE connectors.filtered_events CASCADE")
    # Minimal connector_registry stand-in — only the columns the summaries
    # endpoint actually queries (connector_registry itself lives in the
    # switchboard migration chain; standing it up by hand here keeps this
    # test scoped to the core-chain tables that are actually under test,
    # matching the precedent in test_pending_actions_writers_jsonb_roundtrip.py).
    await p.execute("""
        CREATE TABLE IF NOT EXISTS connector_registry (
            connector_type TEXT NOT NULL,
            endpoint_identity TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'healthy',
            error_message TEXT,
            version TEXT,
            uptime_s INTEGER,
            last_heartbeat_at TIMESTAMPTZ,
            first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            counter_messages_ingested BIGINT NOT NULL DEFAULT 0,
            counter_messages_failed BIGINT NOT NULL DEFAULT 0,
            deleted_at TIMESTAMPTZ,
            archived_at TIMESTAMPTZ,
            PRIMARY KEY (connector_type, endpoint_identity)
        )
    """)
    await p.execute("TRUNCATE TABLE connector_registry")
    yield p
    await p.close()


@pytest.fixture
def app(pool: asyncpg.Pool) -> FastAPI:
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = pool

    application = create_app()
    from butlers.api.routers import ingestion_connectors as _router_mod

    application.dependency_overrides[_router_mod._get_db_manager] = lambda: mock_db
    return application


async def _get_summaries(app: FastAPI) -> dict:
    from butlers.api.routers import ingestion_pipeline as _pip_mod

    _pip_mod._pipeline_cache.clear()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=BASE_URL
    ) as client:
        resp = await client.get(SUMMARIES_PATH)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


async def _ensure_filtered_partition(pool: asyncpg.Pool, reference_ts: datetime) -> None:
    await pool.fetchval(
        "SELECT connectors.connectors_filtered_events_ensure_partition($1)", reference_ts
    )


async def _seed_registry(
    pool: asyncpg.Pool, *, connector_type: str, endpoint_identity: str
) -> None:
    await pool.execute(
        """
        INSERT INTO connector_registry (connector_type, endpoint_identity, first_seen_at)
        VALUES ($1, $2, now())
        ON CONFLICT (connector_type, endpoint_identity) DO NOTHING
        """,
        connector_type,
        endpoint_identity,
    )


async def _seed_ingestion_event(
    pool: asyncpg.Pool,
    *,
    received_at: datetime,
    connector_type: str,
    endpoint_identity: str,
    sender: str | None = None,
    status: str = "ingested",
) -> uuid.UUID:
    event_id = uuid.uuid4()
    await pool.execute(
        """
        INSERT INTO public.ingestion_events (
            id, received_at, source_channel, source_provider,
            source_endpoint_identity, source_sender_identity, external_event_id,
            dedupe_key, dedupe_strategy, ingestion_tier, policy_tier, status
        ) VALUES ($1, $2, $3, $3, $4, $5, $6, $7, 'connector_api', 'full', 'default', $8)
        """,
        event_id,
        received_at,
        connector_type,
        endpoint_identity,
        sender,
        f"ext-{event_id}",
        f"dedupe-{event_id}",
        status,
    )
    return event_id


async def _seed_filtered_event(
    pool: asyncpg.Pool,
    *,
    received_at: datetime,
    connector_type: str,
    endpoint_identity: str,
    sender_identity: str,
    status: str = "filtered",
) -> uuid.UUID:
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
    return event_id


# ---------------------------------------------------------------------------
# Hourly 'filtered' series — real UNION ALL against the partitioned table
# ---------------------------------------------------------------------------


async def test_hourly_filtered_events_reflects_real_filtered_events_rows(
    app: FastAPI, pool: asyncpg.Pool
) -> None:
    """A 100%-skip-routed connector (zero ingestion_events rows) still surfaces
    its filtered volume via the real UNION ALL query, distinct from hourly_events.
    """
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)

    await _seed_registry(pool, connector_type="home_assistant", endpoint_identity="default")
    # Zero ingestion_events rows for home_assistant — entirely skip-routed.
    for _ in range(5):
        await _seed_filtered_event(
            pool,
            received_at=now,
            connector_type="home_assistant",
            endpoint_identity="default",
            sender_identity="binary_sensor.front_door",
        )

    data = await _get_summaries(app)
    assert data["hourly_events_available"] is True
    connector = data["connectors"][0]
    assert connector["connector_type"] == "home_assistant"
    assert sum(connector["hourly_events"]) == 0
    assert sum(connector["hourly_filtered_events"]) == 5
    assert connector["today"]["messages_ingested"] == 0


async def test_hourly_events_and_filtered_events_stay_distinct_same_hour(
    app: FastAPI, pool: asyncpg.Pool
) -> None:
    """Ingested and filtered counts in the SAME hour never bleed into each other."""
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)

    await _seed_registry(pool, connector_type="gmail", endpoint_identity="user@example.com")
    await _seed_ingestion_event(
        pool,
        received_at=now,
        connector_type="gmail",
        endpoint_identity="user@example.com",
        sender="alice@example.com",
    )
    await _seed_ingestion_event(
        pool,
        received_at=now,
        connector_type="gmail",
        endpoint_identity="user@example.com",
        sender="bob@example.com",
    )
    for _ in range(3):
        await _seed_filtered_event(
            pool,
            received_at=now,
            connector_type="gmail",
            endpoint_identity="user@example.com",
            sender_identity="spam@example.com",
        )

    data = await _get_summaries(app)
    connector = data["connectors"][0]
    assert sum(connector["hourly_events"]) == 2
    assert sum(connector["hourly_filtered_events"]) == 3
    assert connector["today"]["messages_ingested"] == 2


# ---------------------------------------------------------------------------
# Device liveness union — real SQL for the sender_identity <> '' exclusion
# ---------------------------------------------------------------------------


async def test_devices_surfaces_sender_seen_only_in_filtered_events(
    app: FastAPI, pool: asyncpg.Pool
) -> None:
    """A sender/device that NEVER appears in ingestion_events (100% skip-routed)
    still surfaces in the `devices` liveness list via the filtered_events union.
    """
    now = datetime.now(UTC)

    await _seed_registry(pool, connector_type="home_assistant", endpoint_identity="default")
    # Two distinct entities, both ONLY in filtered_events — never ingested.
    await _seed_filtered_event(
        pool,
        received_at=now,
        connector_type="home_assistant",
        endpoint_identity="default",
        sender_identity="binary_sensor.front_door",
    )
    await _seed_filtered_event(
        pool,
        received_at=now - timedelta(days=70),
        connector_type="home_assistant",
        endpoint_identity="default",
        sender_identity="binary_sensor.garage",
    )

    data = await _get_summaries(app)
    assert data["device_liveness_available"] is True
    connector = data["connectors"][0]
    devices = connector["devices"]
    assert devices is not None
    identities = {d["sender_identity"] for d in devices}
    assert identities == {"binary_sensor.front_door", "binary_sensor.garage"}
    by_identity = {d["sender_identity"]: d for d in devices}
    assert by_identity["binary_sensor.front_door"]["stale"] is False
    assert by_identity["binary_sensor.garage"]["stale"] is True


async def test_devices_excludes_empty_sender_identity_from_filtered_events(
    app: FastAPI, pool: asyncpg.Pool
) -> None:
    """An empty-string sender_identity in filtered_events (google_health/spotify/
    google_calendar's no-organizer placeholder) is excluded — it never becomes a
    fake "device" entry.
    """
    now = datetime.now(UTC)

    await _seed_registry(pool, connector_type="google_calendar", endpoint_identity="primary")
    await _seed_ingestion_event(
        pool,
        received_at=now,
        connector_type="google_calendar",
        endpoint_identity="primary",
        sender="organizer@example.com",
    )
    await _seed_filtered_event(
        pool,
        received_at=now,
        connector_type="google_calendar",
        endpoint_identity="primary",
        sender_identity="attendee@example.com",
    )
    # No-organizer placeholder — must be excluded from `devices`.
    await _seed_filtered_event(
        pool,
        received_at=now,
        connector_type="google_calendar",
        endpoint_identity="primary",
        sender_identity="",
    )

    data = await _get_summaries(app)
    connector = data["connectors"][0]
    devices = connector["devices"]
    assert devices is not None
    identities = {d["sender_identity"] for d in devices}
    assert identities == {"organizer@example.com", "attendee@example.com"}
    assert "" not in identities


async def test_device_lookback_reads_filtered_events_across_partitions(
    app: FastAPI, pool: asyncpg.Pool
) -> None:
    """The 90-day device lookback correctly reads a filtered_events row from a
    different monthly partition than the current one (core_007 partitioning).
    """
    now = datetime.now(UTC)
    two_months_ago = now - timedelta(days=60)

    await _seed_registry(pool, connector_type="home_assistant", endpoint_identity="default")
    await _seed_filtered_event(
        pool,
        received_at=now,
        connector_type="home_assistant",
        endpoint_identity="default",
        sender_identity="binary_sensor.recent",
    )
    await _seed_filtered_event(
        pool,
        received_at=two_months_ago,
        connector_type="home_assistant",
        endpoint_identity="default",
        sender_identity="binary_sensor.old",
    )

    # Sanity: the two rows really landed in different partitions.
    partitions = await pool.fetch(
        "SELECT DISTINCT tableoid::regclass::text AS partition "
        "FROM connectors.filtered_events ORDER BY partition"
    )
    assert len({r["partition"] for r in partitions}) == 2

    data = await _get_summaries(app)
    connector = data["connectors"][0]
    identities = {d["sender_identity"] for d in connector["devices"]}
    assert identities == {"binary_sensor.recent", "binary_sensor.old"}
