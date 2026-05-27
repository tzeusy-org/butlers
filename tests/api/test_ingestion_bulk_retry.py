"""Tests for POST /api/ingestion/events/retry/bulk (bu-va06h).

The bulk-retry endpoint calls ingestion_event_replay_request per event
(same logic as the single-event replay endpoint) so it handles events from
both public.ingestion_events and connectors.filtered_events.

Covers:
- happy path: all events accepted → 200 with per-event status + counts
- partial failure: some events not found or in conflict state
- oversized batch rejected with 400
- empty event_ids rejected with 400
- missing event_ids rejected with 400
- invalid UUID in event_ids rejected with 400
- shared database unavailable → 503
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest

from butlers.api.db import DatabaseManager
from butlers.api.routers.ingestion_events import _get_db_manager

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_shared_pool():
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchrow = AsyncMock(return_value=None)
    pool.execute = AsyncMock(return_value=None)
    pool.fetchval = AsyncMock(return_value=None)
    return pool


def _app_with_mock_db(app, *, shared_pool=None, shared_pool_error=None):
    mock_db = MagicMock(spec=DatabaseManager)
    if shared_pool_error is not None:
        mock_db.credential_shared_pool.side_effect = shared_pool_error
    else:
        if shared_pool is None:
            shared_pool = _make_shared_pool()
        mock_db.credential_shared_pool.return_value = shared_pool
    # Switchboard pool unavailable by default (non-fatal)
    mock_db.pool.side_effect = KeyError("no switchboard pool")
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    return mock_db


# ---------------------------------------------------------------------------
# Happy path: all events accepted
# ---------------------------------------------------------------------------


async def test_bulk_retry_all_succeed(app):
    """All events replay OK → 200 with succeeded == len(event_ids), failed == 0."""
    event_ids = [str(uuid4()) for _ in range(3)]
    _app_with_mock_db(app)

    ok_result = {"outcome": "ok", "id": str(uuid4()), "source": "ingestion_events"}

    with (
        patch(
            "butlers.api.routers.ingestion_events.ingestion_event_replay_request",
            new_callable=AsyncMock,
            return_value=ok_result,
        ) as mock_replay,
        patch(
            "butlers.api.routers.ingestion_events._audit_append",
            new_callable=AsyncMock,
        ) as mock_audit,
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/ingestion/events/retry/bulk",
                json={"event_ids": event_ids},
            )

    assert resp.status_code == 200
    body = resp.json()
    assert body["succeeded"] == 3
    assert body["failed"] == 0
    assert len(body["results"]) == 3
    for item in body["results"]:
        assert item["status"] == "replay_pending"
        assert item["event_id"] in event_ids
        assert "error" not in item
    # Audit entry emitted for each accepted event
    assert mock_audit.await_count == 3
    assert mock_replay.await_count == 3


# ---------------------------------------------------------------------------
# Partial failure: some not_found, some conflict
# ---------------------------------------------------------------------------


async def test_bulk_retry_partial_failure(app):
    """Partial failure: one ok, one not_found, one conflict — all attempted."""
    id_ok = str(uuid4())
    id_not_found = str(uuid4())
    id_conflict = str(uuid4())

    _app_with_mock_db(app)

    def _replay_side_effect(pool, event_id, *, switchboard_pool=None):
        if event_id == id_ok:
            return {"outcome": "ok", "id": id_ok, "source": "ingestion_events"}
        if event_id == id_not_found:
            return {"outcome": "not_found"}
        # id_conflict
        return {"outcome": "conflict", "current_status": "replay_pending"}

    with (
        patch(
            "butlers.api.routers.ingestion_events.ingestion_event_replay_request",
            side_effect=_replay_side_effect,
        ),
        patch(
            "butlers.api.routers.ingestion_events._audit_append",
            new_callable=AsyncMock,
        ),
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/ingestion/events/retry/bulk",
                json={"event_ids": [id_ok, id_not_found, id_conflict]},
            )

    assert resp.status_code == 200
    body = resp.json()
    assert body["succeeded"] == 1
    assert body["failed"] == 2

    by_id = {r["event_id"]: r for r in body["results"]}
    assert by_id[id_ok]["status"] == "replay_pending"
    assert "error" not in by_id[id_ok]
    assert by_id[id_not_found]["status"] == "not_found"
    assert "error" in by_id[id_not_found]
    assert by_id[id_conflict]["status"] == "conflict"
    assert "replay_pending" in by_id[id_conflict]["error"]


# ---------------------------------------------------------------------------
# Oversized batch
# ---------------------------------------------------------------------------


async def test_bulk_retry_oversized_batch_400(app):
    """Batch with more than 100 event_ids is rejected with 400."""
    event_ids = [str(uuid4()) for _ in range(101)]
    _app_with_mock_db(app)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/ingestion/events/retry/bulk",
            json={"event_ids": event_ids},
        )

    assert resp.status_code == 400
    assert "101" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Exactly 100 events (boundary — must be accepted)
# ---------------------------------------------------------------------------


async def test_bulk_retry_exactly_100_events_accepted(app):
    """Exactly 100 events is the max allowed batch size (no 400)."""
    event_ids = [str(uuid4()) for _ in range(100)]
    _app_with_mock_db(app)

    ok_result = {"outcome": "ok", "id": str(uuid4()), "source": "filtered_events"}

    with (
        patch(
            "butlers.api.routers.ingestion_events.ingestion_event_replay_request",
            new_callable=AsyncMock,
            return_value=ok_result,
        ),
        patch(
            "butlers.api.routers.ingestion_events._audit_append",
            new_callable=AsyncMock,
        ),
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/ingestion/events/retry/bulk",
                json={"event_ids": event_ids},
            )

    assert resp.status_code == 200
    body = resp.json()
    assert body["succeeded"] == 100
    assert body["failed"] == 0


# ---------------------------------------------------------------------------
# Empty / missing event_ids
# ---------------------------------------------------------------------------


async def test_bulk_retry_empty_event_ids_400(app):
    """Empty event_ids list returns 400."""
    _app_with_mock_db(app)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/ingestion/events/retry/bulk",
            json={"event_ids": []},
        )

    assert resp.status_code == 400


async def test_bulk_retry_missing_event_ids_400(app):
    """Missing event_ids key returns 400."""
    _app_with_mock_db(app)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/ingestion/events/retry/bulk",
            json={"reason": "oops"},
        )

    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Invalid UUID
# ---------------------------------------------------------------------------


async def test_bulk_retry_invalid_uuid_400(app):
    """A non-UUID string in event_ids returns 400 before any DB call."""
    _app_with_mock_db(app)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/ingestion/events/retry/bulk",
            json={"event_ids": ["not-a-uuid"]},
        )

    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# DB unavailable
# ---------------------------------------------------------------------------


async def test_bulk_retry_db_unavailable_503(app):
    """Shared database pool unavailable returns 503."""
    _app_with_mock_db(app, shared_pool_error=KeyError("no shared pool"))

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/ingestion/events/retry/bulk",
            json={"event_ids": [str(uuid4())]},
        )

    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Unexpected per-event error does not abort the batch
# ---------------------------------------------------------------------------


async def test_bulk_retry_unexpected_error_continues_batch(app):
    """An unexpected exception on one event is recorded as error; batch continues."""
    id_error = str(uuid4())
    id_ok = str(uuid4())

    _app_with_mock_db(app)

    def _replay_side_effect(pool, event_id, *, switchboard_pool=None):
        if event_id == id_error:
            raise RuntimeError("Simulated DB connection drop")
        return {"outcome": "ok", "id": event_id, "source": "filtered_events"}

    with (
        patch(
            "butlers.api.routers.ingestion_events.ingestion_event_replay_request",
            side_effect=_replay_side_effect,
        ),
        patch(
            "butlers.api.routers.ingestion_events._audit_append",
            new_callable=AsyncMock,
        ),
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/ingestion/events/retry/bulk",
                json={"event_ids": [id_error, id_ok]},
            )

    assert resp.status_code == 200
    body = resp.json()
    assert body["succeeded"] == 1
    assert body["failed"] == 1

    by_id = {r["event_id"]: r for r in body["results"]}
    assert by_id[id_error]["status"] == "error"
    assert "Simulated DB connection drop" in by_id[id_error]["error"]
    assert by_id[id_ok]["status"] == "replay_pending"


# ---------------------------------------------------------------------------
# Audit is best-effort: audit failure does not abort the batch
# ---------------------------------------------------------------------------


async def test_bulk_retry_audit_failure_is_nonfatal(app):
    """Audit append failure does not abort the batch or change the HTTP response."""
    event_ids = [str(uuid4()) for _ in range(2)]
    _app_with_mock_db(app)

    ok_result = {"outcome": "ok", "id": str(uuid4()), "source": "ingestion_events"}

    with (
        patch(
            "butlers.api.routers.ingestion_events.ingestion_event_replay_request",
            new_callable=AsyncMock,
            return_value=ok_result,
        ),
        patch(
            "butlers.api.routers.ingestion_events._audit_append",
            new_callable=AsyncMock,
            side_effect=RuntimeError("audit table missing"),
        ),
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/ingestion/events/retry/bulk",
                json={"event_ids": event_ids},
            )

    # Audit failure is non-fatal; events are still accepted.
    assert resp.status_code == 200
    body = resp.json()
    assert body["succeeded"] == 2
    assert body["failed"] == 0
