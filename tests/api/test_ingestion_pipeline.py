"""Tests for §4.2 pipeline stats and §4.3 aggregates_available threading.

Extracted from test_ingestion_bulk_replay_pipeline.py when the /events/replay/bulk
endpoint was removed (bu-5vcpc).  The bulk_replay tests (§4.1, §4.8.3) were
deleted along with the dead endpoint.  Pipeline stats and aggregates_available
tests are orthogonal to replay — they cover GET /api/ingestion/pipeline,
GET /api/ingestion/connectors/summaries, and GET /api/ingestion/connectors/cross-summary.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI

from butlers.api.db import DatabaseManager
from butlers.api.routers.ingestion_connectors import (
    _get_db_manager as _connectors_get_db_manager,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_shared_pool(rows=None, fetchrow_val=None):
    """Build a mock pool wired for non-bulk_replay endpoints (no acquire needed)."""
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=rows or [])
    pool.fetchrow = AsyncMock(return_value=fetchrow_val)
    pool.execute = AsyncMock(return_value=None)
    pool.fetchval = AsyncMock(return_value=None)
    return pool


def _app_with_connectors_db(app: FastAPI, *, switchboard_pool=None):
    mock_db = MagicMock(spec=DatabaseManager)
    if switchboard_pool is None:
        switchboard_pool = _make_shared_pool()
    mock_db.pool.return_value = switchboard_pool
    app.dependency_overrides[_connectors_get_db_manager] = lambda: mock_db
    return mock_db


# ---------------------------------------------------------------------------
# §4.2 PipelineStats: degraded mode
# ---------------------------------------------------------------------------


async def test_pipeline_stats_degraded_mode_no_prometheus_url(app):
    """GET /api/ingestion/pipeline returns zeros with aggregates_available=false
    when PROMETHEUS_URL is not set."""
    # Clear any stale cache entries
    from butlers.api.routers import ingestion_pipeline as _pip_mod

    _pip_mod._pipeline_cache.clear()

    with patch.dict("os.environ", {"PROMETHEUS_URL": ""}, clear=False):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/ingestion/pipeline?window=24h")

    assert resp.status_code == 200
    body = resp.json()
    assert body["aggregates_available"] is False
    assert body["ingested"] == 0
    assert body["filtered"] == 0
    assert body["errored"] == 0
    assert body["spark24h"] == [0] * 24
    assert body["rate1h"] == 0.0
    assert body["window"] == "24h"


async def test_pipeline_stats_degraded_mode_prometheus_error(app):
    """GET /api/ingestion/pipeline returns degraded mode on Prometheus connection error."""
    from butlers.api.routers import ingestion_pipeline as _pip_mod

    _pip_mod._pipeline_cache.clear()

    with patch.dict("os.environ", {"PROMETHEUS_URL": "http://lgtm:9090"}):
        with patch(
            "butlers.api.routers.ingestion_pipeline.async_query",
            new_callable=AsyncMock,
            return_value=[{"error": "connection refused"}],
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/ingestion/pipeline?window=24h")

    assert resp.status_code == 200
    body = resp.json()
    assert body["aggregates_available"] is False
    assert body["ingested"] == 0


async def test_pipeline_stats_healthy_response(app):
    """GET /api/ingestion/pipeline returns aggregates_available=true on healthy Prometheus.

    spark24h comes from the range query when it succeeds.
    """
    from butlers.api.routers import ingestion_pipeline as _pip_mod

    _pip_mod._pipeline_cache.clear()

    # Simulate successful Prometheus responses for all queries
    def _prom_result(value: float):
        return [{"metric": {}, "value": [1234567890.0, str(value)]}]

    # 24 hourly buckets for the range query (oldest → most-recent)
    _range_buckets = [[1234560000 + i * 3600, str(i * 10)] for i in range(24)]
    _range_result = [{"metric": {}, "values": _range_buckets}]

    with patch.dict("os.environ", {"PROMETHEUS_URL": "http://lgtm:9090"}):
        with patch(
            "butlers.api.routers.ingestion_pipeline.async_query",
            new_callable=AsyncMock,
            side_effect=[
                _prom_result(100.0),  # ingested
                _prom_result(20.0),  # filtered
                _prom_result(5.0),  # errored
                [{"metric": {"butler_name": "atlas"}, "value": [0, "80"]}],  # routed
                _prom_result(2.5),  # rate1h
                _prom_result(15.0),  # filtered24h
            ],
        ):
            with patch(
                "butlers.api.routers.ingestion_pipeline.async_query_range",
                new_callable=AsyncMock,
                return_value=_range_result,
            ):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    resp = await client.get("/api/ingestion/pipeline?window=24h")

    assert resp.status_code == 200
    body = resp.json()
    assert body["aggregates_available"] is True
    assert body["ingested"] == 100
    assert body["filtered"] == 20
    assert body["errored"] == 5
    assert body["routed_by_butler"] == {"atlas": 80}
    # spark24h must be the real range buckets, not a uniform distribution
    assert body["spark24h"] == [i * 10 for i in range(24)]


async def test_pipeline_stats_ttl_cache_second_request_served_from_cache(app):
    """Second request within 60s window is served from cache without hitting Prometheus."""
    from butlers.api.routers import ingestion_pipeline as _pip_mod

    _pip_mod._pipeline_cache.clear()

    def _prom_result(value: float):
        return [{"metric": {}, "value": [1234567890.0, str(value)]}]

    call_count = 0

    async def _mock_query(url, query, **kwargs):
        nonlocal call_count
        call_count += 1
        return _prom_result(42.0)

    _range_buckets = [[1234560000 + i * 3600, "5"] for i in range(24)]

    with patch.dict("os.environ", {"PROMETHEUS_URL": "http://lgtm:9090"}):
        with patch(
            "butlers.api.routers.ingestion_pipeline.async_query",
            side_effect=_mock_query,
        ):
            with patch(
                "butlers.api.routers.ingestion_pipeline.async_query_range",
                new_callable=AsyncMock,
                return_value=[{"metric": {}, "values": _range_buckets}],
            ):
                # First request populates cache
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    resp1 = await client.get("/api/ingestion/pipeline?window=24h")
                calls_after_first = call_count

                # Second request — should use cache, no new Prometheus calls
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    resp2 = await client.get("/api/ingestion/pipeline?window=24h")

    assert resp1.status_code == 200
    assert resp2.status_code == 200
    # call_count should not have increased after the second request
    assert call_count == calls_after_first, (
        f"Prometheus was called {call_count - calls_after_first} extra time(s) on second request"
    )


async def test_pipeline_stats_invalid_window_400(app):
    """GET /api/ingestion/pipeline?window=invalid returns HTTP 422 (FastAPI Literal validation)."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/ingestion/pipeline?window=invalid")
    assert resp.status_code == 422


async def test_pipeline_stats_spark24h_from_range_query(app):
    """spark24h is populated from the Prometheus range query when it succeeds.

    The returned bucket values should match what async_query_range returns —
    not a uniform distribution of the ingested total.
    """
    from butlers.api.routers import ingestion_pipeline as _pip_mod

    _pip_mod._pipeline_cache.clear()

    def _prom_result(value: float):
        return [{"metric": {}, "value": [1234567890.0, str(value)]}]

    # Distinct per-hour values so we can verify real bucketing (not uniform).
    hourly_values = list(range(24))  # 0, 1, 2, … 23
    _range_result = [
        {
            "metric": {},
            "values": [[1234560000 + i * 3600, str(hourly_values[i])] for i in range(24)],
        }
    ]

    with patch.dict("os.environ", {"PROMETHEUS_URL": "http://lgtm:9090"}):
        with patch(
            "butlers.api.routers.ingestion_pipeline.async_query",
            new_callable=AsyncMock,
            side_effect=[
                _prom_result(276.0),  # ingested (sum of 0..23 = 276)
                _prom_result(0.0),  # filtered
                _prom_result(0.0),  # errored
                [],  # routed — empty vector
                _prom_result(0.0),  # rate1h
                _prom_result(0.0),  # filtered24h
            ],
        ):
            with patch(
                "butlers.api.routers.ingestion_pipeline.async_query_range",
                new_callable=AsyncMock,
                return_value=_range_result,
            ):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    resp = await client.get("/api/ingestion/pipeline?window=24h")

    assert resp.status_code == 200
    body = resp.json()
    assert body["aggregates_available"] is True
    assert len(body["spark24h"]) == 24
    # Must be real per-bucket values, not uniform distribution.
    assert body["spark24h"] == hourly_values


async def test_pipeline_stats_spark24h_fallback_on_range_error(app):
    """spark24h falls back to uniform distribution when the range query returns an error.

    The endpoint must still return 200 with aggregates_available=true because the
    instant queries (ingested, filtered, …) did succeed.
    """
    from butlers.api.routers import ingestion_pipeline as _pip_mod

    _pip_mod._pipeline_cache.clear()

    def _prom_result(value: float):
        return [{"metric": {}, "value": [1234567890.0, str(value)]}]

    with patch.dict("os.environ", {"PROMETHEUS_URL": "http://lgtm:9090"}):
        with patch(
            "butlers.api.routers.ingestion_pipeline.async_query",
            new_callable=AsyncMock,
            side_effect=[
                _prom_result(48.0),  # ingested
                _prom_result(0.0),  # filtered
                _prom_result(0.0),  # errored
                [],  # routed
                _prom_result(0.0),  # rate1h
                _prom_result(0.0),  # filtered24h
            ],
        ):
            with patch(
                "butlers.api.routers.ingestion_pipeline.async_query_range",
                new_callable=AsyncMock,
                return_value=[{"error": "connection refused"}],
            ):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    resp = await client.get("/api/ingestion/pipeline?window=24h")

    assert resp.status_code == 200
    body = resp.json()
    assert body["aggregates_available"] is True
    assert len(body["spark24h"]) == 24
    # Should be uniform distribution: 48 // 24 = 2 per bucket.
    assert body["spark24h"] == [2] * 24


async def test_pipeline_stats_spark24h_fallback_on_empty_matrix(app):
    """spark24h falls back to uniform distribution when the range query returns an empty matrix."""
    from butlers.api.routers import ingestion_pipeline as _pip_mod

    _pip_mod._pipeline_cache.clear()

    def _prom_result(value: float):
        return [{"metric": {}, "value": [1234567890.0, str(value)]}]

    with patch.dict("os.environ", {"PROMETHEUS_URL": "http://lgtm:9090"}):
        with patch(
            "butlers.api.routers.ingestion_pipeline.async_query",
            new_callable=AsyncMock,
            side_effect=[
                _prom_result(24.0),  # ingested
                _prom_result(0.0),  # filtered
                _prom_result(0.0),  # errored
                [],  # routed
                _prom_result(0.0),  # rate1h
                _prom_result(0.0),  # filtered24h
            ],
        ):
            with patch(
                "butlers.api.routers.ingestion_pipeline.async_query_range",
                new_callable=AsyncMock,
                return_value=[],  # empty matrix
            ):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    resp = await client.get("/api/ingestion/pipeline?window=24h")

    assert resp.status_code == 200
    body = resp.json()
    assert body["aggregates_available"] is True
    assert len(body["spark24h"]) == 24
    # Uniform distribution: 24 // 24 = 1 per bucket.
    assert body["spark24h"] == [1] * 24


async def test_pipeline_stats_spark24h_trims_25_buckets_to_24(app):
    """async_query_range can return 25 points due to boundary inclusion; we trim to 24."""
    from butlers.api.routers import ingestion_pipeline as _pip_mod

    _pip_mod._pipeline_cache.clear()

    def _prom_result(value: float):
        return [{"metric": {}, "value": [1234567890.0, str(value)]}]

    # 25 buckets: we should keep the last 24 (most-recent).
    raw_25 = [[1234560000 + i * 3600, str(i)] for i in range(25)]
    _range_result = [{"metric": {}, "values": raw_25}]

    with patch.dict("os.environ", {"PROMETHEUS_URL": "http://lgtm:9090"}):
        with patch(
            "butlers.api.routers.ingestion_pipeline.async_query",
            new_callable=AsyncMock,
            side_effect=[
                _prom_result(0.0),  # ingested
                _prom_result(0.0),  # filtered
                _prom_result(0.0),  # errored
                [],  # routed
                _prom_result(0.0),  # rate1h
                _prom_result(0.0),  # filtered24h
            ],
        ):
            with patch(
                "butlers.api.routers.ingestion_pipeline.async_query_range",
                new_callable=AsyncMock,
                return_value=_range_result,
            ):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    resp = await client.get("/api/ingestion/pipeline?window=24h")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["spark24h"]) == 24
    # We dropped the first bucket (index 0 = oldest) and kept indices 1..24.
    assert body["spark24h"] == list(range(1, 25))


# ---------------------------------------------------------------------------
# §4.3 aggregates_available threading: summaries + cross-summary
# ---------------------------------------------------------------------------


async def test_connector_summaries_includes_aggregates_available_false_no_prometheus(app):
    """GET /api/ingestion/connectors/summaries includes aggregates_available=false
    when PROMETHEUS_URL is not configured."""
    pool = _make_shared_pool(rows=[])
    _app_with_connectors_db(app, switchboard_pool=pool)

    # Clear pipeline cache so cache doesn't influence aggregates_available
    from butlers.api.routers import ingestion_pipeline as _pip_mod

    _pip_mod._pipeline_cache.clear()

    with patch.dict("os.environ", {"PROMETHEUS_URL": ""}, clear=False):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/ingestion/connectors/summaries")

    assert resp.status_code == 200
    body = resp.json()
    assert "aggregates_available" in body["data"]
    assert body["data"]["aggregates_available"] is False
    assert "connectors" in body["data"]


def _cross_summary_row(last_heartbeat_at, messages_ingested=0, messages_failed=0):
    """Build a mock asyncpg record for the cross-summary per-connector fetch."""
    row = MagicMock()
    data = {
        "last_heartbeat_at": last_heartbeat_at,
        "messages_ingested": messages_ingested,
        "messages_failed": messages_failed,
    }
    row.__getitem__ = MagicMock(side_effect=lambda k: data[k])
    return row


async def test_cross_summary_includes_aggregates_available_false_no_prometheus(app):
    """GET /api/ingestion/connectors/cross-summary includes aggregates_available=false
    when PROMETHEUS_URL is not configured."""
    import datetime as dt

    now = dt.datetime.now(dt.UTC)
    # Two connectors: one recently alive (online), one with no heartbeat (offline).
    pool = _make_shared_pool(
        rows=[
            _cross_summary_row(last_heartbeat_at=now, messages_ingested=100, messages_failed=5),
            _cross_summary_row(last_heartbeat_at=None),
        ]
    )
    _app_with_connectors_db(app, switchboard_pool=pool)

    # Clear pipeline cache so cache doesn't influence aggregates_available
    from butlers.api.routers import ingestion_pipeline as _pip_mod

    _pip_mod._pipeline_cache.clear()

    with patch.dict("os.environ", {"PROMETHEUS_URL": ""}, clear=False):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/ingestion/connectors/cross-summary")

    assert resp.status_code == 200
    body = resp.json()
    assert "aggregates_available" in body["data"]
    assert body["data"]["aggregates_available"] is False
    assert body["data"]["total_connectors"] == 2


async def test_cross_summary_aggregates_available_true_with_prometheus(app):
    """GET /api/ingestion/connectors/cross-summary sets aggregates_available=true
    when PROMETHEUS_URL is configured (even without a cache hit)."""
    import datetime as dt

    now = dt.datetime.now(dt.UTC)
    pool = _make_shared_pool(rows=[_cross_summary_row(last_heartbeat_at=now, messages_ingested=50)])
    _app_with_connectors_db(app, switchboard_pool=pool)

    # Clear pipeline cache so we rely only on PROMETHEUS_URL presence
    from butlers.api.routers import ingestion_pipeline as _pip_mod

    _pip_mod._pipeline_cache.clear()

    with patch.dict("os.environ", {"PROMETHEUS_URL": "http://lgtm:9090"}):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/ingestion/connectors/cross-summary")

    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["aggregates_available"] is True


async def test_cross_summary_counts_by_liveness_not_state(app):
    """GET /api/ingestion/connectors/cross-summary online/stale/offline counts are
    derived from heartbeat liveness, consistent with /summaries per-connector
    liveness.

    Regression: previous impl counted by connector state (healthy/degraded/error),
    causing online:16 while /summaries showed >=4 connectors with liveness:'offline'.
    bu-e0s9p.
    """
    import datetime as dt

    now = dt.datetime.now(dt.UTC)

    # Three connectors with distinct liveness outcomes:
    # 1. Online:  heartbeat 30s ago  → liveness "online"
    # 2. Stale:   heartbeat 400s ago → liveness "stale"
    # 3. Offline: no heartbeat       → liveness "offline"
    online_heartbeat = now - dt.timedelta(seconds=30)
    stale_heartbeat = now - dt.timedelta(seconds=400)

    pool = _make_shared_pool(
        rows=[
            _cross_summary_row(last_heartbeat_at=online_heartbeat, messages_ingested=10),
            _cross_summary_row(last_heartbeat_at=stale_heartbeat, messages_ingested=5),
            _cross_summary_row(last_heartbeat_at=None, messages_ingested=0),
        ]
    )
    _app_with_connectors_db(app, switchboard_pool=pool)

    from butlers.api.routers import ingestion_pipeline as _pip_mod

    _pip_mod._pipeline_cache.clear()

    with patch.dict("os.environ", {"PROMETHEUS_URL": ""}, clear=False):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/ingestion/connectors/cross-summary")

    assert resp.status_code == 200
    data = resp.json()["data"]

    assert data["total_connectors"] == 3
    # Each bucket must reflect liveness, not state.
    assert data["connectors_online"] == 1, "only the 30s-old heartbeat is online"
    assert data["connectors_stale"] == 1, "only the 400s-old heartbeat is stale"
    assert data["connectors_offline"] == 1, "the null-heartbeat connector is offline"
    # Totals must sum correctly.
    assert (
        data["connectors_online"] + data["connectors_stale"] + data["connectors_offline"]
        == data["total_connectors"]
    )
