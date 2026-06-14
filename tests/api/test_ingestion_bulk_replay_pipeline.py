"""Tests for §4.1 bulk replay, §4.2 pipeline stats, and §4.3 aggregates_available threading.

bu-iu5k0: Updated bulk_replay tests for transaction-wrapping fix.
  The handler now runs SELECT FOR UPDATE + UPDATE + audit inside a single
  async with pool.acquire() as conn: async with conn.transaction(): block.
  Mocks must reflect this: pool.acquire() returns a context manager yielding
  a conn object, and conn.fetch()/conn.fetchval() are used (not pool.fetch()).

This file covers:
- Bulk replay handler: max-batch-size 50, email block HTTP 409, FOR UPDATE SKIP LOCKED path
- Bulk replay concurrency: SKIP LOCKED under concurrent callers, audit atomicity
- PipelineStats: degraded mode (Prometheus unreachable → 200 + aggregates_available=false)
- PipelineStats: TTL cache (second request within window served from cache)
- aggregates_available threading: summaries, cross-summary, pipeline endpoints
"""

from __future__ import annotations

from contextlib import asynccontextmanager
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


def _make_conn(fetch_side_effect=None, fetchval_return=None):
    """Build a mock asyncpg connection with transaction() context-manager support."""
    conn = AsyncMock()

    # conn.transaction() must be a synchronous context-manager factory
    # (asyncpg returns a transaction object via async with conn.transaction()).
    tx = AsyncMock()
    tx.__aenter__ = AsyncMock(return_value=None)
    tx.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=tx)

    if fetch_side_effect is not None:
        conn.fetch = AsyncMock(side_effect=fetch_side_effect)
    else:
        conn.fetch = AsyncMock(return_value=[])

    conn.fetchval = AsyncMock(return_value=fetchval_return)
    conn.fetchrow = AsyncMock(return_value=None)
    conn.execute = AsyncMock(return_value=None)
    return conn


def _make_pool_with_conn(conn):
    """Build a mock asyncpg pool whose acquire() yields the given conn."""
    pool = AsyncMock()

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool.acquire = MagicMock(side_effect=_acquire)
    # Keep pool-level fetch/fetchval stubs so non-bulk_replay paths still work.
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchrow = AsyncMock(return_value=None)
    pool.execute = AsyncMock(return_value=None)
    pool.fetchval = AsyncMock(return_value=None)
    return pool


def _make_shared_pool(rows=None, fetchrow_val=None):
    """Build a mock pool wired for non-bulk_replay endpoints (no acquire needed)."""
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
            # Default: conn returns empty fetch results
            conn = _make_conn(fetch_side_effect=None)
            shared_pool = _make_pool_with_conn(conn)
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
    # conn.fetch returns [] — no rows locked (empty filtered_events table)
    conn = _make_conn(fetch_side_effect=None)  # returns [] by default
    pool = _make_pool_with_conn(conn)
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

    # conn.fetch returns the locked row; audit is called on pool (outside tx — fail-closed)
    conn = _make_conn(fetch_side_effect=[[locked_row]])
    pool = _make_pool_with_conn(conn)
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
    # Rejection audit must be emitted (on pool, outside the transaction — fail-closed)
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

    conn = _make_conn(fetch_side_effect=[[locked_row]])
    pool = _make_pool_with_conn(conn)
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

    # conn.fetch is called twice: first for SKIP LOCKED SELECT, then for UPDATE RETURNING
    updated_rows = []
    for eid in event_ids:
        r = MagicMock()
        r.__getitem__ = MagicMock(side_effect=lambda key, _eid=eid: {"id": _eid}[key])
        updated_rows.append(r)

    conn = _make_conn(fetch_side_effect=[locked_rows, updated_rows])
    pool = _make_pool_with_conn(conn)
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
    # Audit entry for bulk_submit must be emitted (on conn, inside the transaction)
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
# §4.9 Prometheus telemetry: ingestion_bulk_replay_errors_total counter
# ---------------------------------------------------------------------------


async def test_bulk_replay_503_pool_unavailable_increments_counter(app):
    """503 from pool unavailability increments ingestion_bulk_replay_errors_total{code="503"}.

    bu-j5lx7: Without this counter, structural DB errors returned 503s for an entire
    production day before detection via a different channel.
    """
    import butlers.api.routers.ingestion_events as _mod

    # Read current counter value before the request
    before = _mod.ingestion_bulk_replay_errors_total.labels(code="503")._value.get()

    _app_with_events_db(app, shared_pool_error=KeyError("no shared pool"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/ingestion/events/replay/bulk",
            json={"event_ids": [str(uuid4())], "reason": "test"},
        )

    assert resp.status_code == 503
    after = _mod.ingestion_bulk_replay_errors_total.labels(code="503")._value.get()
    assert after == before + 1, (
        f"Counter should have incremented by 1; before={before} after={after}"
    )


async def test_bulk_replay_503_lock_failure_increments_counter(app):
    """503 from Phase 1 row-lock failure increments ingestion_bulk_replay_errors_total{code="503"}.

    Simulates the asyncpg FeatureNotSupportedError (or any DB exception) raised when
    the FOR UPDATE SKIP LOCKED query fails, e.g. due to a LEFT JOIN constraint.
    """
    import butlers.api.routers.ingestion_events as _mod

    before = _mod.ingestion_bulk_replay_errors_total.labels(code="503")._value.get()

    # conn.fetch raises an exception on the first call (Phase 1 lock attempt)
    conn = _make_conn(
        fetch_side_effect=RuntimeError("FOR UPDATE with LEFT JOIN not supported")
    )  # check-for-update-joins: ignore
    pool = _make_pool_with_conn(conn)
    _app_with_events_db(app, shared_pool=pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/ingestion/events/replay/bulk",
            json={"event_ids": [str(uuid4())], "reason": "test"},
        )

    assert resp.status_code == 503
    after = _mod.ingestion_bulk_replay_errors_total.labels(code="503")._value.get()
    assert after == before + 1, (
        f"Counter should have incremented by 1; before={before} after={after}"
    )


async def test_bulk_replay_503_update_failure_increments_counter(app):
    """503 from Phase 3 UPDATE failure increments ingestion_bulk_replay_errors_total{code="503"}.

    Simulates a failure in the UPDATE ... RETURNING step that marks rows replay_pending.
    """
    import butlers.api.routers.ingestion_events as _mod

    before = _mod.ingestion_bulk_replay_errors_total.labels(code="503")._value.get()

    event_id = str(uuid4())
    uuid_val = uuid4()

    locked_row = MagicMock()
    locked_row.__getitem__ = MagicMock(
        side_effect=lambda key: {
            "id": uuid_val,
            "source_channel": "telegram_bot",
            "replay_safe": True,
        }[key]
    )

    # First fetch (Phase 1 SELECT) succeeds, second fetch (Phase 3 UPDATE) raises
    conn = _make_conn(
        fetch_side_effect=[
            [locked_row],  # Phase 1: successful lock
            RuntimeError("UPDATE failed — simulated DB error"),  # Phase 3: update failure
        ]
    )
    pool = _make_pool_with_conn(conn)
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

    assert resp.status_code == 503
    after = _mod.ingestion_bulk_replay_errors_total.labels(code="503")._value.get()
    assert after == before + 1, (
        f"Counter should have incremented by 1; before={before} after={after}"
    )


async def test_bulk_replay_success_does_not_increment_counter(app):
    """Successful bulk replay does NOT increment ingestion_bulk_replay_errors_total."""
    import butlers.api.routers.ingestion_events as _mod

    before = _mod.ingestion_bulk_replay_errors_total.labels(code="503")._value.get()

    event_ids = [str(uuid4()) for _ in range(2)]
    uuids = [uuid4() for _ in range(2)]

    locked_rows = []
    for u, eid in zip(uuids, event_ids):
        row = MagicMock()
        row.__getitem__ = MagicMock(
            side_effect=lambda key, _u=u: {
                "id": _u,
                "source_channel": "telegram_bot",
                "replay_safe": True,
            }[key]
        )
        locked_rows.append(row)

    updated_rows = []
    for eid in event_ids:
        r = MagicMock()
        r.__getitem__ = MagicMock(side_effect=lambda key, _eid=eid: {"id": _eid}[key])
        updated_rows.append(r)

    conn = _make_conn(fetch_side_effect=[locked_rows, updated_rows])
    pool = _make_pool_with_conn(conn)
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
                json={"event_ids": event_ids, "reason": "success-test"},
            )

    assert resp.status_code == 200
    after = _mod.ingestion_bulk_replay_errors_total.labels(code="503")._value.get()
    assert after == before, (
        f"Counter should NOT have incremented on success; before={before} after={after}"
    )


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

    With the transaction fix (bu-iu5k0) the entire SELECT + UPDATE + audit sequence
    runs in a single connection.transaction() block, so the FOR UPDATE lock is held
    until commit and cannot be stolen between the SELECT and the UPDATE.
    """
    event_id_1 = str(uuid4())
    event_id_2 = str(uuid4())

    # Simulate "second caller" scenario: SKIP LOCKED SELECT returns [] because
    # the first caller still holds the row locks inside its open transaction.
    conn = _make_conn(fetch_side_effect=None)  # returns [] by default
    pool = _make_pool_with_conn(conn)

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

    # conn.fetch is called twice: SKIP LOCKED SELECT then UPDATE ... RETURNING
    conn = _make_conn(fetch_side_effect=[[free_row], [updated_row]])
    pool = _make_pool_with_conn(conn)

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
# §4.8.2 Transaction integrity: lock + update + audit run in a single connection.transaction()
# ---------------------------------------------------------------------------


async def test_bulk_replay_audit_called_with_conn_not_pool(app):
    """Audit append receives the active connection (conn), not the pool.

    When _audit_append is called with a conn inside a conn.transaction() block,
    the audit insert participates in the same SQL transaction as the UPDATE — so
    a rollback reverts both (§6.2 mandate-1 atomicity).  This test verifies that
    the first positional argument to _audit_append is the conn object returned by
    pool.acquire(), not the pool itself.
    """
    event_ids = [str(uuid4()) for _ in range(2)]
    uuids = [uuid4() for _ in range(2)]

    locked_rows = []
    for u, eid in zip(uuids, event_ids):
        row = MagicMock()
        row.__getitem__ = MagicMock(
            side_effect=lambda key, _u=u: {
                "id": _u,
                "source_channel": "telegram_bot",
                "replay_safe": True,
            }[key]
        )
        locked_rows.append(row)

    updated_rows = []
    for eid in event_ids:
        r = MagicMock()
        r.__getitem__ = MagicMock(side_effect=lambda key, _eid=eid: {"id": _eid}[key])
        updated_rows.append(r)

    conn = _make_conn(fetch_side_effect=[locked_rows, updated_rows])
    pool = _make_pool_with_conn(conn)
    _app_with_events_db(app, shared_pool=pool)

    captured_args: list = []

    async def _capture_audit(pool_or_conn, actor, action, **kwargs):
        captured_args.append(pool_or_conn)
        return 1

    with patch(
        "butlers.api.routers.ingestion_events._audit_append",
        side_effect=_capture_audit,
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/ingestion/events/replay/bulk",
                json={"event_ids": event_ids, "reason": "atomicity-test"},
            )

    assert resp.status_code == 200
    # _audit_append must have been called exactly once (bulk_submit)
    assert len(captured_args) == 1
    # The first argument must be the conn, not the pool
    assert captured_args[0] is conn, (
        f"Expected conn object, got {type(captured_args[0]).__name__}. "
        "Audit must run on the same connection as the UPDATE for atomicity."
    )


async def test_bulk_replay_reject_audit_called_with_pool_not_conn(app):
    """bulk_reject audit append receives the pool, not the conn (fail-closed auditing).

    When unsafe events are detected the handler raises HTTPException(409), which
    rolls back the open conn.transaction().  The rejection audit entry must be
    committed via pool (its own independent transaction) BEFORE the HTTPException
    is raised — so the record persists even when the surrounding transaction aborts.

    This verifies the first positional argument to _audit_append in the
    bulk_reject branch is pool, not conn.
    """
    event_id = str(uuid4())

    locked_row = MagicMock()
    locked_row.__getitem__ = MagicMock(
        side_effect=lambda key: {
            "id": event_id,
            "source_channel": "email",
            "replay_safe": True,
        }[key]
    )

    conn = _make_conn(fetch_side_effect=[[locked_row]])
    pool = _make_pool_with_conn(conn)
    _app_with_events_db(app, shared_pool=pool)

    captured_args: list = []

    async def _capture_audit(pool_or_conn, actor, action, **kwargs):
        captured_args.append(pool_or_conn)
        return 1

    with patch(
        "butlers.api.routers.ingestion_events._audit_append",
        side_effect=_capture_audit,
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/ingestion/events/replay/bulk",
                json={"event_ids": [event_id], "reason": "fail-closed-test"},
            )

    assert resp.status_code == 409
    # _audit_append must have been called once (bulk_reject)
    assert len(captured_args) == 1
    # The first argument must be the pool, NOT the conn — so the audit row is not
    # rolled back when HTTPException aborts the transaction.
    assert captured_args[0] is pool, (
        f"Expected pool object, got {type(captured_args[0]).__name__}. "
        "Rejection audit must run on pool (not conn) to survive transaction rollback."
    )


async def test_bulk_replay_transaction_context_entered(app):
    """pool.acquire() and conn.transaction() are both entered for the bulk_replay path.

    This verifies the structural fix: the handler must acquire a single connection
    and wrap all DB operations in a single explicit transaction, holding the
    FOR UPDATE locks until commit (Gemini PR #1803 line 136 finding).
    """
    event_ids = [str(uuid4())]
    uuid_val = uuid4()

    locked_row = MagicMock()
    locked_row.__getitem__ = MagicMock(
        side_effect=lambda key: {
            "id": uuid_val,
            "source_channel": "telegram_bot",
            "replay_safe": True,
        }[key]
    )
    updated_row = MagicMock()
    updated_row.__getitem__ = MagicMock(side_effect=lambda key: {"id": str(uuid_val)}[key])

    conn = _make_conn(fetch_side_effect=[[locked_row], [updated_row]])
    pool = _make_pool_with_conn(conn)
    _app_with_events_db(app, shared_pool=pool)

    with patch(
        "butlers.api.routers.ingestion_events._audit_append",
        new_callable=AsyncMock,
        return_value=1,
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/ingestion/events/replay/bulk",
                json={"event_ids": event_ids, "reason": "tx-test"},
            )

    assert resp.status_code == 200

    # Verify pool.acquire was called (context manager entered)
    pool.acquire.assert_called_once()

    # Verify conn.transaction() was called (explicit transaction opened)
    conn.transaction.assert_called_once()

    # Verify the transaction context manager was entered (__aenter__)
    tx = conn.transaction.return_value
    tx.__aenter__.assert_awaited_once()
    tx.__aexit__.assert_awaited_once()


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


# ---------------------------------------------------------------------------
# §4.8.3 Real-DB concurrency: FOR UPDATE SKIP LOCKED with two live connections
# ---------------------------------------------------------------------------

import shutil  # noqa: E402 (needed for docker_available sentinel)

_docker_available = shutil.which("docker") is not None

# DDL executed inline — the provisioned_postgres_pool fixture provisions a
# blank database (no migrations).  We create only the tables the handler
# touches so this test is self-contained and fast.
_CREATE_CONNECTORS_SCHEMA = "CREATE SCHEMA IF NOT EXISTS connectors"

_CREATE_FILTERED_EVENTS_SQL = """
    CREATE TABLE IF NOT EXISTS connectors.filtered_events (
        id                  UUID NOT NULL DEFAULT gen_random_uuid(),
        received_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
        connector_type      TEXT NOT NULL,
        endpoint_identity   TEXT NOT NULL,
        external_message_id TEXT NOT NULL,
        source_channel      TEXT NOT NULL,
        sender_identity     TEXT NOT NULL,
        subject_or_preview  TEXT,
        filter_reason       TEXT NOT NULL,
        status              TEXT NOT NULL DEFAULT 'filtered',
        full_payload        JSONB NOT NULL,
        error_detail        TEXT,
        replay_requested_at TIMESTAMPTZ,
        replay_completed_at TIMESTAMPTZ,
        created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (received_at, id),
        CONSTRAINT chk_filtered_events_status CHECK (status IN (
            'filtered', 'error', 'replay_pending', 'replay_complete', 'replay_failed'
        ))
    )
"""

# connector_registry lives in public so the unqualified LEFT JOIN in the
# handler resolves without a custom search_path.
_CREATE_CONNECTOR_REGISTRY_SQL = """
    CREATE TABLE IF NOT EXISTS public.connector_registry (
        connector_type    TEXT NOT NULL,
        endpoint_identity TEXT NOT NULL,
        replay_safe       BOOLEAN NOT NULL DEFAULT TRUE,
        PRIMARY KEY (connector_type, endpoint_identity)
    )
"""

# Mirror production: core_092 creates audit_log; core_122 adds the
# metadata/result/error columns that the unified audit writer (append())
# now INSERTs into.  Omitting them makes the handler's _audit_append raise
# UndefinedColumnError, rolling back the bulk_submit transaction.
_CREATE_AUDIT_LOG_SQL = """
    CREATE TABLE IF NOT EXISTS public.audit_log (
        id         BIGSERIAL PRIMARY KEY,
        ts         TIMESTAMPTZ NOT NULL DEFAULT now(),
        actor      TEXT NOT NULL,
        action     TEXT NOT NULL,
        target     TEXT,
        note       TEXT,
        ip         INET,
        request_id UUID,
        metadata   JSONB,
        result     TEXT,
        error      TEXT
    )
"""


_TEST_CONNECTOR_TYPE = "test_connector"
_TEST_ENDPOINT_IDENTITY = "test_endpoint"


async def _provision_bulk_replay_schema(pool) -> None:
    """Create the tables needed by bulk_replay_ingestion_events in one shot.

    Also inserts a connector_registry row so the handler's LEFT JOIN always
    finds a match.  PostgreSQL disallows FOR UPDATE on the nullable side of an  # check-for-update-joins: ignore
    outer join, which would happen if filtered_events has no matching registry
    entry.  In production each connector self-registers before writing events,
    so a matching row is always present.
    """
    await pool.execute(_CREATE_CONNECTORS_SCHEMA)
    await pool.execute(_CREATE_FILTERED_EVENTS_SQL)
    await pool.execute(_CREATE_CONNECTOR_REGISTRY_SQL)
    await pool.execute(_CREATE_AUDIT_LOG_SQL)
    # Seed the connector_registry row used by all test inserts (replay_safe=TRUE).
    await pool.execute(
        """
        INSERT INTO public.connector_registry (connector_type, endpoint_identity, replay_safe)
        VALUES ($1, $2, TRUE)
        ON CONFLICT DO NOTHING
        """,
        _TEST_CONNECTOR_TYPE,
        _TEST_ENDPOINT_IDENTITY,
    )


async def _insert_filtered_event(
    pool,
    *,
    source_channel: str = "telegram_bot",
    status: str = "filtered",
) -> str:
    """Insert one row into connectors.filtered_events and return its id as str."""
    from uuid import uuid4

    row_id = await pool.fetchval(
        """
        INSERT INTO connectors.filtered_events
            (connector_type, endpoint_identity, external_message_id,
             source_channel, sender_identity, filter_reason,
             status, full_payload)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        RETURNING id
        """,
        _TEST_CONNECTOR_TYPE,
        _TEST_ENDPOINT_IDENTITY,
        str(uuid4()),
        source_channel,
        "sender@example.com",
        "test-filter",
        status,
        "{}",
    )
    return str(row_id)


@pytest.mark.integration
@pytest.mark.skipif(not _docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_bulk_replay_skip_locked_real_db(app, provisioned_postgres_pool):
    """FOR UPDATE SKIP LOCKED with real asyncpg connections — no mocks in the race window.

    Scenario
    --------
    Two events are inserted into connectors.filtered_events.  Connection A
    opens a transaction and locks the first row with SELECT … FOR UPDATE.
    The bulk_replay handler (connection B via the test HTTP client) then
    processes both IDs.  Because connection A still holds the row lock:

    - The locked row appears in ``skipped_locked`` (SKIP LOCKED returned 0
      rows for it).
    - The unlocked row is accepted, its status mutated to ``replay_pending``,
      and a ``bulk_submit`` audit row is committed atomically alongside the
      state change.
    - The locked row's status is unchanged (still ``filtered``).

    This test is deterministic: connection A's transaction is open throughout
    the entire HTTP call so there is no timing window where the lock can be
    released before the handler's SELECT runs.
    """
    async with provisioned_postgres_pool(max_pool_size=5) as pool:
        await _provision_bulk_replay_schema(pool)

        # Insert the two test rows.
        locked_id = await _insert_filtered_event(pool, source_channel="telegram_bot")
        unlocked_id = await _insert_filtered_event(pool, source_channel="whatsapp")

        # Wire the real pool into a mock DatabaseManager.
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = pool
        app.dependency_overrides[_events_get_db_manager] = lambda: mock_db

        try:
            # Open connection A and hold a FOR UPDATE lock on locked_id.
            # The transaction stays open for the duration of the HTTP call so
            # the handler's SKIP LOCKED SELECT cannot acquire the row lock.
            # Use conn_a.transaction() so the lock is released automatically
            # (via ROLLBACK on __aexit__) even if the HTTP call raises.
            async with pool.acquire() as conn_a:
                async with conn_a.transaction():
                    await conn_a.fetchrow(
                        "SELECT id FROM connectors.filtered_events WHERE id = $1::uuid FOR UPDATE",
                        locked_id,
                    )

                    # Connection B (the handler pool) now calls bulk_replay.
                    async with httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=app), base_url="http://test"
                    ) as client:
                        resp = await client.post(
                            "/api/ingestion/events/replay/bulk",
                            json={
                                "event_ids": [locked_id, unlocked_id],
                                "reason": "real-db-concurrency-test",
                            },
                        )

                # conn_a.transaction() exits here; lock released automatically.

            # ----------------------------------------------------------------
            # Assertions on the HTTP response
            # ----------------------------------------------------------------
            assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
            body = resp.json()

            # The unlocked row was successfully replayed.
            assert body["accepted"] == [unlocked_id], (
                f"Expected only unlocked_id in accepted; got {body['accepted']}"
            )
            # The locked row was skipped by SKIP LOCKED.
            assert body["skipped_locked"] == [locked_id], (
                f"Expected locked_id in skipped_locked; got {body['skipped_locked']}"
            )
            assert body["capped"] == []

            # ----------------------------------------------------------------
            # Assertions on DB state
            # ----------------------------------------------------------------
            # The unlocked row's status must have been mutated to replay_pending.
            unlocked_status = await pool.fetchval(
                "SELECT status FROM connectors.filtered_events WHERE id = $1::uuid",
                unlocked_id,
            )
            assert unlocked_status == "replay_pending", (
                f"Expected unlocked row status='replay_pending', got {unlocked_status!r}"
            )

            # The locked row's status must be unchanged (still 'filtered').
            locked_status = await pool.fetchval(
                "SELECT status FROM connectors.filtered_events WHERE id = $1::uuid",
                locked_id,
            )
            assert locked_status == "filtered", (
                f"Expected locked row status unchanged ('filtered'), got {locked_status!r}"
            )

            # ----------------------------------------------------------------
            # Audit atomicity: bulk_submit row must be in public.audit_log.
            # It was written on the same connection + transaction as the UPDATE,
            # so it commits (or rolls back) together.
            # ----------------------------------------------------------------
            audit_count = await pool.fetchval(
                "SELECT count(*) FROM public.audit_log WHERE action = $1",
                "ingestion.replay.bulk_submit",
            )
            assert audit_count == 1, f"Expected 1 bulk_submit audit row; got {audit_count}"

            # No audit row for bulk_reject (the batch was not rejected).
            reject_count = await pool.fetchval(
                "SELECT count(*) FROM public.audit_log WHERE action = $1",
                "ingestion.replay.bulk_reject",
            )
            assert reject_count == 0, f"Expected 0 bulk_reject audit rows; got {reject_count}"

        finally:
            app.dependency_overrides.pop(_events_get_db_manager, None)
