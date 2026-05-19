"""Tests for §4.1 bulk replay, §4.2 pipeline stats, and §4.3 aggregates_available threading.

Wave-3 concurrency tests (§4.8) are in a separate bead (bu-1f91v.12).
This file covers:
- Bulk replay handler: max-batch-size 50, email block HTTP 409, FOR UPDATE SKIP LOCKED path
- PipelineStats: degraded mode (Prometheus unreachable → 200 + aggregates_available=false)
- PipelineStats: TTL cache (second request within window served from cache)
- aggregates_available threading: summaries, cross-summary, pipeline endpoints
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from butlers.api.db import DatabaseManager
from butlers.api.routers.ingestion_connectors import (
    _get_db_manager as _connectors_get_db_manager,
)
from butlers.api.routers.ingestion_events import _get_db_manager as _events_get_db_manager

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_shared_pool(rows=None, fetchrow_val=None):
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=rows or [])
    pool.fetchrow = AsyncMock(return_value=fetchrow_val)
    pool.execute = AsyncMock(return_value=None)
    pool.fetchval = AsyncMock(return_value=None)
    return pool


def _app_with_events_db(app: FastAPI, *, shared_pool=None, shared_pool_error=None):
    mock_db = MagicMock(spec=DatabaseManager)
    if shared_pool_error is not None:
        mock_db.credential_shared_pool.side_effect = shared_pool_error
    else:
        if shared_pool is None:
            shared_pool = _make_shared_pool()
        mock_db.credential_shared_pool.return_value = shared_pool
    mock_db.pool.side_effect = KeyError("not available")
    app.dependency_overrides[_events_get_db_manager] = lambda: mock_db
    return mock_db


def _app_with_connectors_db(app: FastAPI, *, switchboard_pool=None):
    mock_db = MagicMock(spec=DatabaseManager)
    if switchboard_pool is None:
        switchboard_pool = _make_shared_pool()
    mock_db.pool.return_value = switchboard_pool
    app.dependency_overrides[_connectors_get_db_manager] = lambda: mock_db
    return mock_db


# ---------------------------------------------------------------------------
# §4.1 Bulk replay: max-batch-size 50
# ---------------------------------------------------------------------------


async def test_bulk_replay_capped_at_50(app):
    """Requests with >50 event ids are capped; extras reported in 'capped' list."""
    event_ids = [str(uuid4()) for _ in range(60)]
    pool = _make_shared_pool(rows=[])  # No rows locked (empty filtered_events)
    _app_with_events_db(app, shared_pool=pool)

    with patch(
        "butlers.api.routers.ingestion_events._audit_append",
        new_callable=AsyncMock,
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/ingestion/events/replay/bulk",
                json={"event_ids": event_ids, "reason": "test"},
            )

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["capped"]) == 10  # 60 - 50 = 10 capped
    assert set(body["capped"]) == set(event_ids[50:])


async def test_bulk_replay_email_channel_blocked_http_409(app):
    """Email channel events are rejected with HTTP 409 and a rejection audit entry."""
    event_id = str(uuid4())

    # Simulate a locked row with source_channel='email'
    locked_row = MagicMock()
    locked_row.__getitem__ = MagicMock(
        side_effect=lambda key: {
            "id": event_id,
            "source_channel": "email",
            "replay_safe": True,
        }[key]
    )

    pool = _make_shared_pool(rows=[locked_row])
    _app_with_events_db(app, shared_pool=pool)

    with patch(
        "butlers.api.routers.ingestion_events._audit_append",
        new_callable=AsyncMock,
    ) as mock_audit:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/ingestion/events/replay/bulk",
                json={"event_ids": [event_id], "reason": "test"},
            )

    assert resp.status_code == 409
    body = resp.json()
    # Response must identify the unsafe event
    assert "unsafe_events" in body["detail"]
    assert any(e["id"] == str(event_id) for e in body["detail"]["unsafe_events"])
    # Rejection audit must be emitted
    mock_audit.assert_awaited_once()
    assert mock_audit.await_args.kwargs["action"] == "ingestion.replay.bulk_reject"


async def test_bulk_replay_replay_safe_false_blocked_http_409(app):
    """Events where connector_registry.replay_safe=FALSE are also rejected with HTTP 409."""
    event_id = str(uuid4())

    locked_row = MagicMock()
    locked_row.__getitem__ = MagicMock(
        side_effect=lambda key: {
            "id": event_id,
            "source_channel": "webhook",
            "replay_safe": False,
        }[key]
    )

    pool = _make_shared_pool(rows=[locked_row])
    _app_with_events_db(app, shared_pool=pool)

    with patch(
        "butlers.api.routers.ingestion_events._audit_append",
        new_callable=AsyncMock,
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/ingestion/events/replay/bulk",
                json={"event_ids": [event_id], "reason": "test"},
            )

    assert resp.status_code == 409
    body = resp.json()
    unsafe = body["detail"]["unsafe_events"]
    assert any("replay_safe=false" in e["reason"] for e in unsafe)


async def test_bulk_replay_safe_batch_accepted(app):
    """Safe-channel events are accepted and marked replay_pending; audit emitted."""
    event_ids = [str(uuid4()) for _ in range(3)]

    locked_rows = []
    for eid in event_ids:
        row = MagicMock()
        row.__getitem__ = MagicMock(
            side_effect=lambda key, _eid=eid: {
                "id": _eid,
                "source_channel": "telegram_bot",
                "replay_safe": True,
            }[key]
        )
        locked_rows.append(row)

    # fetch() returns locked rows; second fetch() (UPDATE RETURNING) returns updated ids
    updated_rows = []
    for eid in event_ids:
        r = MagicMock()
        r.__getitem__ = MagicMock(side_effect=lambda key, _eid=eid: {"id": _eid}[key])
        updated_rows.append(r)

    pool = AsyncMock()
    pool.fetch = AsyncMock(side_effect=[locked_rows, updated_rows])
    pool.fetchrow = AsyncMock(return_value=None)
    pool.execute = AsyncMock()
    pool.fetchval = AsyncMock(return_value=None)
    _app_with_events_db(app, shared_pool=pool)

    with patch(
        "butlers.api.routers.ingestion_events._audit_append",
        new_callable=AsyncMock,
    ) as mock_audit:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/ingestion/events/replay/bulk",
                json={"event_ids": event_ids, "reason": "re-process batch"},
            )

    assert resp.status_code == 200
    body = resp.json()
    assert set(body["accepted"]) == set(event_ids)
    assert body["capped"] == []
    # Audit entry for bulk_submit must be emitted
    mock_audit.assert_awaited_once()
    assert mock_audit.await_args.kwargs["action"] == "ingestion.replay.bulk_submit"


async def test_bulk_replay_missing_event_ids_400(app):
    """POST with missing event_ids returns HTTP 400."""
    _app_with_events_db(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/ingestion/events/replay/bulk",
            json={"reason": "oops, forgot ids"},
        )
    assert resp.status_code == 400


async def test_bulk_replay_empty_event_ids_400(app):
    """POST with empty event_ids returns HTTP 400."""
    _app_with_events_db(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/ingestion/events/replay/bulk",
            json={"event_ids": [], "reason": "empty"},
        )
    assert resp.status_code == 400


async def test_bulk_replay_503_on_db_unavailable(app):
    """POST returns 503 when the shared database pool is unavailable."""
    _app_with_events_db(app, shared_pool_error=KeyError("no shared pool"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/ingestion/events/replay/bulk",
            json={"event_ids": [str(uuid4())], "reason": "test"},
        )
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# §4.8.1 Bulk replay concurrency: FOR UPDATE SKIP LOCKED prevents double-replay
# ---------------------------------------------------------------------------


async def test_bulk_replay_skip_locked_prevents_double_replay(app):
    """FOR UPDATE SKIP LOCKED: rows locked by a concurrent caller are reported in skipped_locked.

    Simulates the race where two concurrent bulk-replay requests target the same
    event IDs.  The first caller's SELECT ... FOR UPDATE SKIP LOCKED acquires the
    row locks; the second caller's SELECT returns an empty set for those rows
    (SKIP LOCKED behaviour).  The endpoint must:
    - Return HTTP 200 (not an error)
    - Report skipped rows in the 'skipped_locked' field
    - NOT include those rows in 'accepted'
    """
    event_id_1 = str(uuid4())
    event_id_2 = str(uuid4())

    # Simulate "first caller" having acquired locks: pool.fetch returns an empty
    # list (all requested rows were skipped due to lock contention).
    # This mirrors what FOR UPDATE SKIP LOCKED returns when rows are held.
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[])  # SKIP LOCKED → zero rows returned
    pool.fetchrow = AsyncMock(return_value=None)
    pool.execute = AsyncMock()
    pool.fetchval = AsyncMock(return_value=None)

    _app_with_events_db(app, shared_pool=pool)

    with patch(
        "butlers.api.routers.ingestion_events._audit_append",
        new_callable=AsyncMock,
    ) as mock_audit:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/ingestion/events/replay/bulk",
                json={"event_ids": [event_id_1, event_id_2], "reason": "concurrency-test"},
            )

    # Endpoint returns 200 — SKIP LOCKED is not an error condition
    assert resp.status_code == 200
    body = resp.json()

    # All requested events were skipped due to lock contention
    assert body["accepted"] == []
    assert set(body["skipped_locked"]) == {event_id_1, event_id_2}
    assert body["capped"] == []

    # Audit entry for the (empty) batch is still emitted
    mock_audit.assert_awaited_once()
    assert mock_audit.await_args.kwargs["action"] == "ingestion.replay.bulk_submit"


async def test_bulk_replay_skip_locked_partial_race(app):
    """FOR UPDATE SKIP LOCKED: partial lock contention — some rows locked, some free.

    One event is held by a concurrent drain loop; the other is available.
    The endpoint accepts the free one and reports the locked one in skipped_locked.
    """
    uuid_free = uuid4()
    uuid_locked = uuid4()
    event_id_free = str(uuid_free)
    event_id_locked = str(uuid_locked)

    # Only the free row is returned by the SKIP LOCKED SELECT.
    # asyncpg returns UUID objects for uuid columns, so mock id as UUID.
    free_row = MagicMock()
    free_row.__getitem__ = MagicMock(
        side_effect=lambda key: {
            "id": uuid_free,  # UUID object, matching the endpoint's locked_ids set
            "source_channel": "telegram_bot",
            "replay_safe": True,
        }[key]
    )

    # UPDATE RETURNING returns the accepted row id as a string (endpoint calls str())
    updated_row = MagicMock()
    updated_row.__getitem__ = MagicMock(side_effect=lambda key: {"id": event_id_free}[key])

    pool = AsyncMock()
    # First fetch = SKIP LOCKED SELECT (returns only free row)
    # Second fetch = UPDATE ... RETURNING (returns accepted row)
    pool.fetch = AsyncMock(side_effect=[[free_row], [updated_row]])
    pool.fetchrow = AsyncMock(return_value=None)
    pool.execute = AsyncMock()
    pool.fetchval = AsyncMock(return_value=None)

    _app_with_events_db(app, shared_pool=pool)

    with patch(
        "butlers.api.routers.ingestion_events._audit_append",
        new_callable=AsyncMock,
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/ingestion/events/replay/bulk",
                json={
                    "event_ids": [event_id_free, event_id_locked],
                    "reason": "partial-race-test",
                },
            )

    assert resp.status_code == 200
    body = resp.json()

    # Free row was accepted; locked row was skipped due to SKIP LOCKED
    assert body["accepted"] == [event_id_free]
    assert body["skipped_locked"] == [event_id_locked]
    assert body["capped"] == []


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
    """GET /api/ingestion/pipeline returns aggregates_available=true on healthy Prometheus."""
    from butlers.api.routers import ingestion_pipeline as _pip_mod

    _pip_mod._pipeline_cache.clear()

    # Simulate successful Prometheus responses for all queries
    def _prom_result(value: float):
        return [{"metric": {}, "value": [1234567890.0, str(value)]}]

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
    assert len(body["spark24h"]) == 24


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

    with patch.dict("os.environ", {"PROMETHEUS_URL": "http://lgtm:9090"}):
        with patch(
            "butlers.api.routers.ingestion_pipeline.async_query",
            side_effect=_mock_query,
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


async def test_cross_summary_includes_aggregates_available_false_no_prometheus(app):
    """GET /api/ingestion/connectors/cross-summary includes aggregates_available=false
    when PROMETHEUS_URL is not configured."""
    pool = _make_shared_pool(
        fetchrow_val=MagicMock(
            __getitem__=MagicMock(
                side_effect=lambda k: {
                    "total_connectors": 2,
                    "online_count": 1,
                    "stale_count": 0,
                    "offline_count": 1,
                    "total_messages_ingested": 100,
                    "total_messages_failed": 5,
                }[k]
            )
        )
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
    pool = _make_shared_pool(
        fetchrow_val=MagicMock(
            __getitem__=MagicMock(
                side_effect=lambda k: {
                    "total_connectors": 1,
                    "online_count": 1,
                    "stale_count": 0,
                    "offline_count": 0,
                    "total_messages_ingested": 50,
                    "total_messages_failed": 0,
                }[k]
            )
        )
    )
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
