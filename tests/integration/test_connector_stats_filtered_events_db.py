"""Real-Postgres integration tests for the skip-aware connector STATS endpoint
(GET /api/switchboard/connectors/{type}/{identity}/stats, bu-c48im).

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

The switchboard router loads its models dynamically, so rather than wire the
whole app + auto-discovery, these tests call ``get_connector_stats`` directly
with a fake DatabaseManager whose ``.pool()`` returns a real asyncpg pool — the
same direct-call style the mocked-pool tests use.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import asyncpg
import pytest

from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]

_ROUTER_PATH = Path(__file__).resolve().parents[2] / "roster" / "switchboard" / "api" / "router.py"


def _load_switchboard_router():
    """Load a fresh switchboard router module (mirrors the mocked-pool tests)."""
    sys.modules.pop("switchboard_api_models", None)
    spec = importlib.util.spec_from_file_location("_sw_router_stats_integration", _ROUTER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _RealPoolDB:
    """Fake DatabaseManager whose pool() returns the real asyncpg pool."""

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    def pool(self, name: str):
        return self._pool


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
    yield p
    await p.close()


# ---------------------------------------------------------------------------
# Seed helpers (mirror test_connector_summaries_filtered_events_db.py)
# ---------------------------------------------------------------------------


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
    mod = _load_switchboard_router()
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)

    for _ in range(4):
        await _seed_filtered_event(
            pool,
            received_at=now,
            connector_type="home_assistant",
            endpoint_identity="default",
        )

    result = await mod.get_connector_stats(
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
    mod = _load_switchboard_router()
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

    result = await mod.get_connector_stats(
        connector_type="gmail",
        endpoint_identity="user@example.com",
        period="24h",
        db=_RealPoolDB(pool),
    )

    # Exactly one hour bucket, with the two series kept DISTINCT.
    assert len(result.data) == 1
    bucket = result.data[0]
    assert bucket.messages_ingested == 2
    assert bucket.messages_filtered == 3


async def test_stats_matches_websocket_connector_stored_under_source_provider(
    pool: asyncpg.Pool,
) -> None:
    """A websocket connector whose type lives in source_provider (not
    source_channel) is matched via COALESCE(source_provider, source_channel)."""
    mod = _load_switchboard_router()
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)

    # _seed_ingestion_event writes both source_channel and source_provider to the
    # connector_type, so COALESCE resolves correctly regardless.
    await _seed_ingestion_event(
        pool, received_at=now, connector_type="home_assistant", endpoint_identity="ws://ha:8123"
    )

    result = await mod.get_connector_stats(
        connector_type="home_assistant",
        endpoint_identity="ws://ha:8123",
        period="24h",
        db=_RealPoolDB(pool),
    )

    assert sum(r.messages_ingested for r in result.data) == 1
