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


async def test_pipeline_stats_backlog_counts_default_unavailable(app):
    """GET /api/ingestion/pipeline reports backlog_available=false with None
    counts when no DatabaseManager has been wired (default test app state) —
    never a fabricated 0 that would misreport an outage as "no backlog"."""
    from butlers.api.routers import ingestion_pipeline as _pip_mod

    _pip_mod._pipeline_cache.clear()

    with patch.dict("os.environ", {"PROMETHEUS_URL": ""}, clear=False):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/ingestion/pipeline?window=24h")

    assert resp.status_code == 200
    body = resp.json()
    assert body["backlog_available"] is False
    assert body["failed_total"] is None
    assert body["replay_pending_total"] is None
    assert body["written_off_total"] is None


def _backlog_row(status: str, cnt: int, is_written_off: bool = False):
    """Build a mock asyncpg record for the backlog-count grouped fetch."""
    row = MagicMock()
    data = {"status": status, "cnt": cnt, "is_written_off": is_written_off}
    row.__getitem__ = MagicMock(side_effect=lambda k: data[k])
    return row


async def test_pipeline_stats_backlog_counts_healthy(app):
    """GET /api/ingestion/pipeline surfaces failed/replay_pending totals from
    public.ingestion_events (DB truth) independently of Prometheus, splitting
    out written-off rows so they don't masquerade as pending losses (bu-g4oiu:
    a reviewed write-off of confirmed-recoverable-but-never-triaged events must
    stay visibly distinct from a genuine unresolved failure)."""
    from butlers.api.routers import ingestion_pipeline as _pip_mod

    _pip_mod._pipeline_cache.clear()

    rows = [
        _backlog_row("failed", 24, is_written_off=False),
        _backlog_row("failed", 99, is_written_off=True),
        _backlog_row("replay_pending", 2),
    ]
    pool = _make_shared_pool(rows=rows)
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.credential_shared_pool.return_value = pool
    app.dependency_overrides[_pip_mod._get_db_manager_optional] = lambda: mock_db

    with patch.dict("os.environ", {"PROMETHEUS_URL": ""}, clear=False):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/ingestion/pipeline?window=24h")

    assert resp.status_code == 200
    body = resp.json()
    assert body["backlog_available"] is True
    assert body["failed_total"] == 24
    assert body["written_off_total"] == 99
    assert body["replay_pending_total"] == 2
    # Independent of the (degraded, no-Prometheus) funnel stats.
    assert body["aggregates_available"] is False


async def test_pipeline_stats_backlog_counts_query_error(app):
    """A backlog-count query failure degrades only the backlog fields, never
    a 500 — mirrors the Prometheus degraded-mode contract."""
    from butlers.api.routers import ingestion_pipeline as _pip_mod

    _pip_mod._pipeline_cache.clear()

    pool = AsyncMock()
    pool.fetch = AsyncMock(side_effect=RuntimeError("connection lost"))
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.credential_shared_pool.return_value = pool
    app.dependency_overrides[_pip_mod._get_db_manager_optional] = lambda: mock_db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/ingestion/pipeline?window=24h")

    assert resp.status_code == 200
    body = resp.json()
    assert body["backlog_available"] is False
    assert body["failed_total"] is None
    assert body["written_off_total"] is None
    assert body["replay_pending_total"] is None


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


async def test_pipeline_stats_spark24h_range_error_degrades(app):
    """A range-query error lowers aggregates_available instead of inventing a shape.

    Before bu-0m31b the handler filled the sparkline uniformly from the
    ingested total and left aggregates_available=true, so the dashboard drew a
    flat 24-bucket line that Prometheus never reported.  An unreadable
    sparkline is unknown, not flat.
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
    assert body["aggregates_available"] is False, (
        "an unreadable sparkline matrix must lower the flag, not fill uniformly"
    )
    assert body["spark24h"] == [0] * 24
    assert body["ingested"] == 0


async def test_pipeline_stats_spark24h_unusable_matrix_shape_degrades(app):
    """A matrix element with no ``values`` key is unreadable, not zero."""
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
                return_value=[{"metric": {}}],  # matrix element without "values"
            ):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    resp = await client.get("/api/ingestion/pipeline?window=24h")

    assert resp.status_code == 200
    body = resp.json()
    assert body["aggregates_available"] is False
    assert body["spark24h"] == [0] * 24


async def test_pipeline_stats_spark24h_unparseable_bucket_degrades(app):
    """A bucket value Prometheus reports as NaN is unknown, not zero."""
    from butlers.api.routers import ingestion_pipeline as _pip_mod

    _pip_mod._pipeline_cache.clear()

    def _prom_result(value: float):
        return [{"metric": {}, "value": [1234567890.0, str(value)]}]

    buckets = [[1234560000 + i * 3600, "1"] for i in range(24)]
    buckets[7] = [1234560000 + 7 * 3600, "NaN"]

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
                return_value=[{"metric": {}, "values": buckets}],
            ):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    resp = await client.get("/api/ingestion/pipeline?window=24h")

    assert resp.status_code == 200
    body = resp.json()
    assert body["aggregates_available"] is False
    assert body["spark24h"] == [0] * 24


async def test_pipeline_stats_spark24h_empty_matrix_reads_as_zero(app):
    """An empty result set is a real Prometheus answer: no series, so no events.

    This is the one case that legitimately produces zeros with the flag still
    true — Prometheus was reached, answered, and the answer was "nothing".
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
                [],  # ingested — empty vector, a truthful zero
                [],  # filtered
                [],  # errored
                [],  # routed
                [],  # rate1h
                [],  # filtered24h
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
    assert body["ingested"] == 0
    assert body["spark24h"] == [0] * 24


async def test_pipeline_stats_unparseable_scalar_degrades(app):
    """A well-formed response carrying an unparseable scalar lowers the flag.

    Before bu-0m31b ``_extract_scalar`` swallowed the parse failure and
    returned ``0.0``, so "we could not read Prometheus" and "Prometheus said
    zero" arrived on the wire as the same zero (bu-0m31b).
    """
    from butlers.api.routers import ingestion_pipeline as _pip_mod

    _pip_mod._pipeline_cache.clear()

    def _prom_result(value: str):
        return [{"metric": {}, "value": [1234567890.0, value]}]

    with patch.dict("os.environ", {"PROMETHEUS_URL": "http://lgtm:9090"}):
        with patch(
            "butlers.api.routers.ingestion_pipeline.async_query",
            new_callable=AsyncMock,
            side_effect=[
                _prom_result("not-a-number"),  # ingested — unparseable
                _prom_result("0"),  # filtered
                _prom_result("0"),  # errored
                [],  # routed
                _prom_result("0"),  # rate1h
                _prom_result("0"),  # filtered24h
            ],
        ):
            with patch(
                "butlers.api.routers.ingestion_pipeline.async_query_range",
                new_callable=AsyncMock,
                return_value=[{"metric": {}, "values": [[0, "0"]] * 24}],
            ):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    resp = await client.get("/api/ingestion/pipeline?window=24h")

    assert resp.status_code == 200
    body = resp.json()
    assert body["aggregates_available"] is False, (
        "an unparseable scalar must lower the flag, not resolve to 0"
    )
    assert body["ingested"] == 0


async def test_pipeline_stats_malformed_scalar_shape_degrades(app):
    """A vector element with no ``value`` key is unreadable, not zero."""
    from butlers.api.routers import ingestion_pipeline as _pip_mod

    _pip_mod._pipeline_cache.clear()

    def _prom_result(value: str):
        return [{"metric": {}, "value": [1234567890.0, value]}]

    with patch.dict("os.environ", {"PROMETHEUS_URL": "http://lgtm:9090"}):
        with patch(
            "butlers.api.routers.ingestion_pipeline.async_query",
            new_callable=AsyncMock,
            side_effect=[
                _prom_result("10"),  # ingested
                [{"metric": {}}],  # filtered — no "value" key
                _prom_result("0"),  # errored
                [],  # routed
                _prom_result("0"),  # rate1h
                _prom_result("0"),  # filtered24h
            ],
        ):
            with patch(
                "butlers.api.routers.ingestion_pipeline.async_query_range",
                new_callable=AsyncMock,
                return_value=[{"metric": {}, "values": [[0, "0"]] * 24}],
            ):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    resp = await client.get("/api/ingestion/pipeline?window=24h")

    assert resp.status_code == 200
    assert resp.json()["aggregates_available"] is False


async def test_pipeline_stats_nan_scalar_degrades(app):
    """``NaN`` parses as a float but is not an observation — it must degrade."""
    from butlers.api.routers import ingestion_pipeline as _pip_mod

    _pip_mod._pipeline_cache.clear()

    def _prom_result(value: str):
        return [{"metric": {}, "value": [1234567890.0, value]}]

    with patch.dict("os.environ", {"PROMETHEUS_URL": "http://lgtm:9090"}):
        with patch(
            "butlers.api.routers.ingestion_pipeline.async_query",
            new_callable=AsyncMock,
            side_effect=[
                _prom_result("10"),  # ingested
                _prom_result("0"),  # filtered
                _prom_result("0"),  # errored
                [],  # routed
                _prom_result("NaN"),  # rate1h — no samples in the range
                _prom_result("0"),  # filtered24h
            ],
        ):
            with patch(
                "butlers.api.routers.ingestion_pipeline.async_query_range",
                new_callable=AsyncMock,
                return_value=[{"metric": {}, "values": [[0, "0"]] * 24}],
            ):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    resp = await client.get("/api/ingestion/pipeline?window=24h")

    assert resp.status_code == 200
    body = resp.json()
    assert body["aggregates_available"] is False
    assert body["rate1h"] == 0.0


async def test_pipeline_stats_rate1h_query_error_degrades(app):
    """A Prometheus error on the rate1h query must not publish rate1h = 0.0.

    Same shape as the ingested/filtered/errored queries, which already
    degraded; rate1h, filtered24h and the routed breakdown did not (bu-0m31b).
    """
    from butlers.api.routers import ingestion_pipeline as _pip_mod

    _pip_mod._pipeline_cache.clear()

    def _prom_result(value: str):
        return [{"metric": {}, "value": [1234567890.0, value]}]

    with patch.dict("os.environ", {"PROMETHEUS_URL": "http://lgtm:9090"}):
        with patch(
            "butlers.api.routers.ingestion_pipeline.async_query",
            new_callable=AsyncMock,
            side_effect=[
                _prom_result("100"),  # ingested
                _prom_result("0"),  # filtered
                _prom_result("0"),  # errored
                [],  # routed
                [{"error": "query timed out"}],  # rate1h
                _prom_result("0"),  # filtered24h
            ],
        ):
            with patch(
                "butlers.api.routers.ingestion_pipeline.async_query_range",
                new_callable=AsyncMock,
                return_value=[{"metric": {}, "values": [[0, "0"]] * 24}],
            ):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    resp = await client.get("/api/ingestion/pipeline?window=24h")

    assert resp.status_code == 200
    body = resp.json()
    assert body["aggregates_available"] is False
    assert body["rate1h"] == 0.0


async def test_pipeline_stats_routed_query_error_degrades(app):
    """A failed routed breakdown must not publish routed_pct = 0.0.

    routed_pct is derived from routed_by_butler; an empty breakdown caused by a
    query error made a fully-routed funnel read as 0% routed.
    """
    from butlers.api.routers import ingestion_pipeline as _pip_mod

    _pip_mod._pipeline_cache.clear()

    def _prom_result(value: str):
        return [{"metric": {}, "value": [1234567890.0, value]}]

    with patch.dict("os.environ", {"PROMETHEUS_URL": "http://lgtm:9090"}):
        with patch(
            "butlers.api.routers.ingestion_pipeline.async_query",
            new_callable=AsyncMock,
            side_effect=[
                _prom_result("100"),  # ingested
                _prom_result("0"),  # filtered
                _prom_result("0"),  # errored
                [{"error": "connection refused"}],  # routed
                _prom_result("0"),  # rate1h
                _prom_result("0"),  # filtered24h
            ],
        ):
            with patch(
                "butlers.api.routers.ingestion_pipeline.async_query_range",
                new_callable=AsyncMock,
                return_value=[{"metric": {}, "values": [[0, "0"]] * 24}],
            ):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    resp = await client.get("/api/ingestion/pipeline?window=24h")

    assert resp.status_code == 200
    body = resp.json()
    assert body["aggregates_available"] is False
    assert body["routed_pct"] == 0.0
    assert body["routed_by_butler"] == {}


async def test_pipeline_stats_unparseable_routed_series_degrades(app):
    """One unreadable butler series must not silently shrink routed_pct."""
    from butlers.api.routers import ingestion_pipeline as _pip_mod

    _pip_mod._pipeline_cache.clear()

    def _prom_result(value: str):
        return [{"metric": {}, "value": [1234567890.0, value]}]

    with patch.dict("os.environ", {"PROMETHEUS_URL": "http://lgtm:9090"}):
        with patch(
            "butlers.api.routers.ingestion_pipeline.async_query",
            new_callable=AsyncMock,
            side_effect=[
                _prom_result("100"),  # ingested
                _prom_result("0"),  # filtered
                _prom_result("0"),  # errored
                [
                    {"metric": {"butler_name": "atlas"}, "value": [0, "60"]},
                    {"metric": {"butler_name": "scribe"}},  # unreadable series
                ],
                _prom_result("0"),  # rate1h
                _prom_result("0"),  # filtered24h
            ],
        ):
            with patch(
                "butlers.api.routers.ingestion_pipeline.async_query_range",
                new_callable=AsyncMock,
                return_value=[{"metric": {}, "values": [[0, "0"]] * 24}],
            ):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    resp = await client.get("/api/ingestion/pipeline?window=24h")

    assert resp.status_code == 200
    body = resp.json()
    assert body["aggregates_available"] is False
    assert body["routed_by_butler"] == {}


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
# §4.3 aggregates_available threading: cross-summary
#
# Note: the /summaries endpoint has NO aggregates_available flag — every field
# it returns is DB-sourced with no Prometheus dependency (bu-hv639). Only
# cross-summary threads the flag.
# ---------------------------------------------------------------------------


def _cross_summary_row(
    last_heartbeat_at,
    messages_ingested=0,
    messages_failed=0,
    operational_role="runtime_instance",
):
    """Build a mock asyncpg record for the cross-summary per-connector fetch.

    ``operational_role`` defaults to ``runtime_instance`` because these tests are
    about liveness bucketing, and only runtime instances have liveness to bucket
    (sw_031, bu-6jv4m.11).
    """
    row = MagicMock()
    data = {
        "last_heartbeat_at": last_heartbeat_at,
        "messages_ingested": messages_ingested,
        "messages_failed": messages_failed,
        "operational_role": operational_role,
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


def _healthy_prom_side_effect():
    """Instant-query results for one full healthy funnel fetch, in query order."""

    def _vector(value: float):
        return [{"metric": {}, "value": [1234567890.0, str(value)]}]

    return [
        _vector(100.0),  # ingested
        _vector(20.0),  # filtered
        _vector(5.0),  # errored
        [{"metric": {"butler_name": "atlas"}, "value": [0, "80"]}],  # routed
        _vector(2.5),  # rate1h
        _vector(15.0),  # filtered24h
    ]


def _healthy_prom_range_result():
    """A readable 24-bucket matrix for the sparkline range query."""
    return [{"metric": {}, "values": [[1234560000 + i * 3600, str(i * 10)] for i in range(24)]}]


async def _cross_summary_flag(app, *, env, patches) -> bool:
    """Call /cross-summary with a cold pipeline cache and return aggregates_available."""
    import datetime as dt
    from contextlib import ExitStack

    now = dt.datetime.now(dt.UTC)
    pool = _make_shared_pool(rows=[_cross_summary_row(last_heartbeat_at=now, messages_ingested=50)])
    _app_with_connectors_db(app, switchboard_pool=pool)

    from butlers.api.routers import ingestion_pipeline as _pip_mod

    _pip_mod._pipeline_cache.clear()

    with ExitStack() as stack:
        stack.enter_context(patch.dict("os.environ", env))
        for ctx in patches:
            stack.enter_context(ctx)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/ingestion/connectors/cross-summary")

    assert resp.status_code == 200
    return resp.json()["data"]["aggregates_available"]


async def test_cross_summary_aggregates_available_true_when_prometheus_answers(app):
    """cross-summary reports aggregates_available=true once Prometheus has answered.

    The flag is a claim about the funnel aggregates the console renders, so it
    is earned by the same queries that back them — not by a configured URL.
    """
    available = await _cross_summary_flag(
        app,
        env={"PROMETHEUS_URL": "http://lgtm:9090"},
        patches=[
            patch(
                "butlers.api.routers.ingestion_pipeline.async_query",
                new_callable=AsyncMock,
                side_effect=_healthy_prom_side_effect(),
            ),
            patch(
                "butlers.api.routers.ingestion_pipeline.async_query_range",
                new_callable=AsyncMock,
                return_value=_healthy_prom_range_result(),
            ),
        ],
    )

    assert available is True


async def test_cross_summary_aggregates_unavailable_when_configured_prometheus_is_unreachable(app):
    """A configured-but-unreachable Prometheus SHALL NOT read as available (bu-avkvr).

    The handler used to set the flag from ``PROMETHEUS_URL`` being non-empty
    whenever the pipeline cache was cold, so a Prometheus that was down
    produced exactly the same ``true`` as one that answered.
    """
    available = await _cross_summary_flag(
        app,
        env={"PROMETHEUS_URL": "http://lgtm:9090"},
        patches=[
            patch(
                "butlers.api.routers.ingestion_pipeline.async_query",
                new_callable=AsyncMock,
                return_value=[{"error": "connection refused"}],
            ),
        ],
    )

    assert available is False


async def test_cross_summary_aggregates_unavailable_when_prometheus_answers_are_unreadable(app):
    """A well-formed response whose scalar will not parse is not an observation.

    Same discipline as the pipeline endpoint (bu-0m31b): ``NaN`` is how
    Prometheus renders "no samples in range", and ``float()`` accepts it.
    """
    available = await _cross_summary_flag(
        app,
        env={"PROMETHEUS_URL": "http://lgtm:9090"},
        patches=[
            patch(
                "butlers.api.routers.ingestion_pipeline.async_query",
                new_callable=AsyncMock,
                return_value=[{"metric": {}, "value": [1234567890.0, "NaN"]}],
            ),
        ],
    )

    assert available is False


async def test_cross_summary_survives_an_aggregate_probe_that_raises(app):
    """The probe is best-effort: a raising availability check degrades the flag, not the response."""
    available = await _cross_summary_flag(
        app,
        env={"PROMETHEUS_URL": "http://lgtm:9090"},
        patches=[
            patch(
                "butlers.api.routers.ingestion_connectors.prometheus_aggregates_available",
                new_callable=AsyncMock,
                side_effect=RuntimeError("event loop went away"),
            ),
        ],
    )

    assert available is False


async def test_cross_summary_reuses_the_pipeline_cache_without_requerying(app):
    """A warm pipeline cache answers the flag; cross-summary issues no query of its own."""
    import datetime as dt
    import time as _time

    from butlers.api.routers import ingestion_pipeline as _pip_mod

    now = dt.datetime.now(dt.UTC)
    pool = _make_shared_pool(rows=[_cross_summary_row(last_heartbeat_at=now, messages_ingested=50)])
    _app_with_connectors_db(app, switchboard_pool=pool)

    _pip_mod._pipeline_cache.clear()
    _pip_mod._pipeline_cache["24h"] = (
        _time.monotonic(),
        {"window": "24h", "aggregates_available": True, "ingested": 7},
    )

    query = AsyncMock(side_effect=AssertionError("a warm cache must not be re-queried"))
    with patch.dict("os.environ", {"PROMETHEUS_URL": "http://lgtm:9090"}):
        with patch("butlers.api.routers.ingestion_pipeline.async_query", query):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/ingestion/connectors/cross-summary")

    assert resp.status_code == 200
    assert resp.json()["data"]["aggregates_available"] is True
    query.assert_not_awaited()


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
