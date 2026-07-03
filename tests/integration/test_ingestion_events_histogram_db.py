"""Real-Postgres integration tests for GET /api/ingestion/events/histogram (bu-4utdw.6).

Mocked-pool unit tests (tests/core/test_ingestion_events.py,
tests/api/test_ingestion_events.py) prove the Python-side validation,
guardrail arithmetic, and per-bucket aggregation logic, but they stub
``pool.fetch`` and therefore cannot catch SQL that is invalid against the
real schema — in particular:

- ``connectors.filtered_events`` is a MONTHLY-PARTITIONED table (core_007).
  A query spanning two months only proves correct if it actually reads two
  partitions. A test against a mocked pool cannot detect a query that would
  fail (or silently miss rows) against the real partitioned table.
- ``date_bin(...)`` bucketing and the ``UNION ALL`` across
  ``public.ingestion_events`` (unpartitioned) and
  ``connectors.filtered_events`` (partitioned) is real SQL that only a real
  Postgres backend can validate. This repo has been burned before by SQL
  that passed mocked-pool tests and broke main for ~8h (PR #2598 class —
  see tests/api/test_relationship_entities_search_db.py).

These tests run the real ``ingestion_events_histogram`` query (and, for one
smoke test, the full HTTP endpoint) against a migrated Postgres container.
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

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

HISTOGRAM_PATH = "/api/ingestion/events/histogram"
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
    yield p
    await p.close()


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


async def _ensure_filtered_partition(pool: asyncpg.Pool, reference_ts: datetime) -> None:
    """Create the monthly partition connectors.filtered_events needs for reference_ts.

    Mirrors what production does on connector startup — without this, an
    INSERT into a month with no partition yet raises
    ``no partition of relation "filtered_events" found for row``.
    """
    await pool.fetchval(
        "SELECT connectors.connectors_filtered_events_ensure_partition($1)", reference_ts
    )


async def _seed_ingestion_event(
    pool: asyncpg.Pool,
    *,
    received_at: datetime,
    source_channel: str = "email",
    status: str = "ingested",
    triage_decision: str | None = None,
    sender: str | None = None,
) -> uuid.UUID:
    event_id = uuid.uuid4()
    await pool.execute(
        """
        INSERT INTO public.ingestion_events (
            id, received_at, source_channel, source_provider,
            source_endpoint_identity, source_sender_identity, external_event_id,
            dedupe_key, dedupe_strategy, ingestion_tier, policy_tier,
            triage_decision, triage_target, status
        ) VALUES ($1, $2, $3, 'gmail', 'inbox@example.com', $4, $5, $6,
                  'connector_api', 'full', 'default', $7, 'atlas', $8)
        """,
        event_id,
        received_at,
        source_channel,
        sender,
        f"ext-{event_id}",
        f"dedupe-{event_id}",
        triage_decision,
        status,
    )
    return event_id


async def _seed_filtered_event(
    pool: asyncpg.Pool,
    *,
    received_at: datetime,
    source_channel: str = "email",
    status: str = "filtered",
    sender: str | None = None,
) -> uuid.UUID:
    await _ensure_filtered_partition(pool, received_at)
    event_id = uuid.uuid4()
    await pool.execute(
        """
        INSERT INTO connectors.filtered_events (
            id, received_at, connector_type, endpoint_identity,
            external_message_id, source_channel, sender_identity,
            filter_reason, status, full_payload
        ) VALUES ($1, $2, 'gmail', 'inbox@example.com', $3, $4, $5, 'noise', $6, '{}'::jsonb)
        """,
        event_id,
        received_at,
        f"ext-{event_id}",
        source_channel,
        sender or "unknown@example.com",
        status,
    )
    return event_id


# ---------------------------------------------------------------------------
# Cross-table, cross-partition stacking correctness
# ---------------------------------------------------------------------------


async def test_histogram_stacks_counts_across_both_tables_and_partitions(pool):
    """A window straddling a month boundary correctly reads BOTH partitions.

    Seeds ingestion_events (unpartitioned) and filtered_events (partitioned,
    one row in June's partition and one in July's) across a 6-minute window
    that itself straddles the June/July partition boundary, then asserts the
    per-minute stacked counts are exactly right.
    """
    from butlers.core.ingestion_events import ingestion_events_histogram

    june_30_2358 = datetime(2026, 6, 30, 23, 58, 0, tzinfo=UTC)
    june_30_2359 = datetime(2026, 6, 30, 23, 59, 0, tzinfo=UTC)
    june_30_2359_30 = datetime(2026, 6, 30, 23, 59, 30, tzinfo=UTC)
    july_1_0001 = datetime(2026, 7, 1, 0, 1, 0, tzinfo=UTC)
    july_1_0001_30 = datetime(2026, 7, 1, 0, 1, 30, tzinfo=UTC)

    # ingestion_events: one plain ingest + one skip-triaged (derived 'skipped') in June.
    await _seed_ingestion_event(pool, received_at=june_30_2359, status="ingested")
    await _seed_ingestion_event(
        pool, received_at=june_30_2359_30, status="ingested", triage_decision="skip"
    )
    # ingestion_events: one plain ingest in July.
    await _seed_ingestion_event(pool, received_at=july_1_0001, status="ingested")

    # filtered_events: one filtered row in June's partition, one error row in July's.
    await _seed_filtered_event(pool, received_at=june_30_2359_30, status="filtered")
    await _seed_filtered_event(pool, received_at=july_1_0001_30, status="error")

    # Sanity: the two rows really landed in different partitions.
    partitions = await pool.fetch(
        "SELECT tableoid::regclass::text AS partition FROM connectors.filtered_events "
        "ORDER BY partition"
    )
    assert {r["partition"] for r in partitions} == {
        "connectors.filtered_events_202606",
        "connectors.filtered_events_202607",
    }

    result = await ingestion_events_histogram(
        pool,
        from_dt=june_30_2358,
        to_dt=datetime(2026, 7, 1, 0, 3, 0, tzinfo=UTC),
        bucket="1m",
    )

    assert result["bucket"] == "1m"
    buckets_by_ts = {b["ts"]: b["counts"] for b in result["buckets"]}

    # Exactly two non-empty minutes — no zero-count buckets included.
    assert set(buckets_by_ts.keys()) == {june_30_2359, july_1_0001}

    june_bucket = buckets_by_ts[june_30_2359]
    assert june_bucket["ingested"] == 1
    assert june_bucket["skipped"] == 1
    assert june_bucket["filtered"] == 1
    assert june_bucket["error"] == 0

    july_bucket = buckets_by_ts[july_1_0001]
    assert july_bucket["ingested"] == 1
    assert july_bucket["error"] == 1
    assert july_bucket["skipped"] == 0
    assert july_bucket["filtered"] == 0


# ---------------------------------------------------------------------------
# Filters — channels, statuses, q
# ---------------------------------------------------------------------------


async def test_histogram_respects_channel_filter(pool):
    from butlers.core.ingestion_events import ingestion_events_histogram

    ts = datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)
    await _seed_ingestion_event(pool, received_at=ts, source_channel="email")
    await _seed_ingestion_event(pool, received_at=ts, source_channel="telegram")

    result = await ingestion_events_histogram(
        pool,
        from_dt=ts - timedelta(minutes=1),
        to_dt=ts + timedelta(minutes=1),
        bucket="1m",
        channels=["email"],
    )

    assert len(result["buckets"]) == 1
    assert result["buckets"][0]["counts"]["ingested"] == 1


async def test_histogram_respects_statuses_filter(pool):
    from butlers.core.ingestion_events import ingestion_events_histogram

    ts = datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)
    await _seed_ingestion_event(pool, received_at=ts, status="ingested")
    await _seed_filtered_event(pool, received_at=ts, status="error")

    result = await ingestion_events_histogram(
        pool,
        from_dt=ts - timedelta(minutes=1),
        to_dt=ts + timedelta(minutes=1),
        bucket="1m",
        statuses=["error"],
    )

    assert len(result["buckets"]) == 1
    counts = result["buckets"][0]["counts"]
    assert counts["error"] == 1
    assert counts["ingested"] == 0


async def test_histogram_respects_q_filter(pool):
    from butlers.core.ingestion_events import ingestion_events_histogram

    ts = datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)
    await _seed_ingestion_event(pool, received_at=ts, sender="alice@example.com")
    await _seed_ingestion_event(pool, received_at=ts, sender="bob@example.com")

    result = await ingestion_events_histogram(
        pool,
        from_dt=ts - timedelta(minutes=1),
        to_dt=ts + timedelta(minutes=1),
        bucket="1m",
        q="alice",
    )

    assert len(result["buckets"]) == 1
    assert result["buckets"][0]["counts"]["ingested"] == 1


async def test_histogram_respects_event_ids_filter(pool):
    """event_ids' `id = ANY($N::uuid[])` cast is valid against the real UNION ALL
    of the unpartitioned public.ingestion_events and the partitioned
    connectors.filtered_events (bu-q750c: trace-scoped hour strip).

    A mocked pool cannot prove the ``::uuid[]`` cast type-checks against both
    branches of the UNION or that the partitioned side's ``id`` column
    actually participates in the ANY() comparison — only a real backend can.
    """
    from butlers.core.ingestion_events import ingestion_events_histogram

    ts = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
    keep_id = await _seed_ingestion_event(pool, received_at=ts, status="ingested")
    await _seed_ingestion_event(pool, received_at=ts, status="ingested")
    await _seed_filtered_event(pool, received_at=ts, status="error")

    result = await ingestion_events_histogram(
        pool,
        from_dt=ts - timedelta(minutes=1),
        to_dt=ts + timedelta(minutes=1),
        bucket="1m",
        event_ids=[str(keep_id)],
    )

    assert len(result["buckets"]) == 1
    counts = result["buckets"][0]["counts"]
    assert counts["ingested"] == 1
    assert counts["error"] == 0

    # An explicit empty list restricts to zero rows (a trace that matched no
    # session), not "no filter" — no buckets at all should come back.
    empty_result = await ingestion_events_histogram(
        pool,
        from_dt=ts - timedelta(minutes=1),
        to_dt=ts + timedelta(minutes=1),
        bucket="1m",
        event_ids=[],
    )
    assert empty_result["buckets"] == []


# ---------------------------------------------------------------------------
# Guardrail — enforced against the real function signature (no seeding needed;
# the guardrail short-circuits before any query is issued).
# ---------------------------------------------------------------------------


async def test_histogram_guardrail_enforced_against_real_pool(pool):
    from butlers.core.ingestion_events import ingestion_events_histogram

    from_dt = datetime(2026, 1, 1, tzinfo=UTC)
    with pytest.raises(ValueError, match="too wide for bucket '1m'"):
        await ingestion_events_histogram(
            pool,
            from_dt=from_dt,
            to_dt=from_dt + timedelta(hours=48, minutes=1),
            bucket="1m",
        )


# ---------------------------------------------------------------------------
# HTTP-level smoke test — proves the router is wired to the real endpoint SQL,
# not just that the core function is correct in isolation.
# ---------------------------------------------------------------------------


@pytest.fixture
def histogram_app(pool: asyncpg.Pool) -> FastAPI:
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.credential_shared_pool.return_value = pool

    application = create_app()
    from butlers.api.routers import ingestion_events as ingestion_events_router_module

    application.dependency_overrides[ingestion_events_router_module._get_db_manager] = lambda: (
        mock_db
    )
    return application


async def test_histogram_endpoint_200_against_real_backend(histogram_app, pool):
    ts = datetime(2026, 4, 1, 9, 0, 0, tzinfo=UTC)
    await _seed_ingestion_event(pool, received_at=ts)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=histogram_app), base_url=BASE_URL
    ) as client:
        resp = await client.get(
            HISTOGRAM_PATH,
            params={
                "from": (ts - timedelta(minutes=1)).isoformat(),
                "to": (ts + timedelta(minutes=1)).isoformat(),
            },
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["bucket"] == "1m"
    assert len(body["buckets"]) == 1
    assert body["buckets"][0]["counts"]["ingested"] == 1


async def test_histogram_endpoint_422_on_guardrail_against_real_backend(histogram_app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=histogram_app), base_url=BASE_URL
    ) as client:
        resp = await client.get(
            HISTOGRAM_PATH,
            params={
                "from": "2026-01-01T00:00:00Z",
                "to": "2026-01-10T00:00:00Z",
                "bucket": "1m",
            },
        )

    assert resp.status_code == 422
    assert "too wide" in resp.json()["detail"]


async def test_histogram_endpoint_trace_id_filters_to_matching_events(pool):
    """?trace_id= resolves to matching event ids and filters the histogram (bu-q750c).

    Proves the router-level wiring end to end against the real backend: the
    trace_id -> event_ids resolution (mocked fan-out here, since sessions
    live in per-butler schemas outside this pool) feeds into the same
    ``id = ANY(...)`` SQL the ledger uses, so a trace-scoped hour strip
    reflects only the trace's events, not the whole window.
    """
    ts = datetime(2026, 4, 2, 9, 0, 0, tzinfo=UTC)
    traced_id = await _seed_ingestion_event(pool, received_at=ts, status="ingested")
    await _seed_ingestion_event(pool, received_at=ts, status="ingested")  # untraced, same window

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.credential_shared_pool.return_value = pool
    mock_db.fan_out = AsyncMock(return_value={"atlas": [{"request_id": str(traced_id)}]})

    application = create_app()
    from butlers.api.routers import ingestion_events as ingestion_events_router_module

    application.dependency_overrides[ingestion_events_router_module._get_db_manager] = lambda: (
        mock_db
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url=BASE_URL
    ) as client:
        resp = await client.get(
            HISTOGRAM_PATH,
            params={
                "from": (ts - timedelta(minutes=1)).isoformat(),
                "to": (ts + timedelta(minutes=1)).isoformat(),
                "trace_id": "trace-xyz",
            },
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Only the traced event counts — the untraced sibling in the same window
    # is excluded, proving trace_id narrowed the aggregate, not just the ledger.
    assert len(body["buckets"]) == 1
    assert body["buckets"][0]["counts"]["ingested"] == 1
