"""Tests for connector lifecycle endpoints: pause and run-now.

These endpoints are implemented in ingestion_connectors.py (bu-1f91v.8, now
shipped). All xfail decorators that cited bu-1f91v.8 have been removed as part
of bu-lbilo (co-resolves bu-5nqst).

Endpoints tested:
  POST /api/ingestion/connectors/{type}/{identity}/pause
    - sets connector to 'paused' state
    - emits _audit_append() with action='connector.pause'
    - returns 200 on success
    - returns 404 if connector not found (logic-driven: registry lookup → None)

  POST /api/ingestion/connectors/{type}/{identity}/run-now
    - validates connector is currently 'paused' (HTTP 409 otherwise)
    - clears pause, triggers next poll cycle
    - emits _audit_append() with action='connector.run_now' on success
    - returns 200 on success
    - returns 409 if connector is not in 'paused' state

The tests mount the ingestion router via create_app() (shared `app` fixture)
and wire the DB dependency by overriding
``butlers.api.routers.ingestion_connectors._get_db_manager``.

Because the pause/run-now handlers use ``pool.acquire()`` context-manager
semantics (not raw ``pool.fetchrow``), mocks must be built with a proper
async-context-manager chain: pool.acquire().__aenter__ → conn → conn.fetchrow.

§3.6a, §3.6b — Phase 3d (bu-1f91v.9), retargeted bu-lbilo
"""

from __future__ import annotations

import datetime as _dt
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from butlers.api.db import DatabaseManager
from butlers.api.routers.ingestion_connectors import _get_db_manager

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_row(data: dict):
    row = MagicMock()
    row.__getitem__ = lambda self, k: data[k]
    row.get = lambda k, default=None: data.get(k, default)
    return row


def _connector_row(
    *,
    connector_type: str = "gmail",
    endpoint_identity: str = "user@example.com",
    state: str = "healthy",
) -> dict:
    return {
        "connector_type": connector_type,
        "endpoint_identity": endpoint_identity,
        "state": state,
        "error_message": None,
    }


def _make_conn(*, fetchrow_results: list):
    """Build a mock asyncpg connection that returns results in sequence.

    ``fetchrow_results`` is consumed left-to-right per awaited fetchrow() call.
    Pass ``None`` to simulate a missing row (connector not found).
    """
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(side_effect=fetchrow_results)

    # transaction() must be an async context manager
    @asynccontextmanager
    async def _transaction():
        yield

    conn.transaction = _transaction
    return conn


def _make_tracking_conn(*, fetchrow_results: list, tx_state: dict):
    """Like ``_make_conn`` but records transaction nesting depth in ``tx_state``.

    ``tx_state["depth"]`` is incremented on ``transaction()`` enter and
    decremented on exit, so a caller can observe whether a later call (e.g.
    ``_audit_append``) happened while the state transaction was still open
    (``depth > 0``) or after it committed (``depth == 0``). This is what
    distinguishes the buggy in-transaction audit (would roll the state change
    back on failure) from the fixed post-commit audit.
    """
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(side_effect=fetchrow_results)

    @asynccontextmanager
    async def _transaction():
        tx_state["depth"] += 1
        try:
            yield
        finally:
            tx_state["depth"] -= 1

    conn.transaction = _transaction
    return conn


def _make_pool(conn: AsyncMock) -> AsyncMock:
    """Wrap a mock connection in a mock pool with acquire() context-manager support."""
    pool = AsyncMock()

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool.acquire = _acquire
    return pool


def _wire_db(app, pool, *, pool_available: bool = True):
    """Override _get_db_manager with a mock DatabaseManager."""
    mock_db = MagicMock(spec=DatabaseManager)
    if pool_available:
        mock_db.pool.return_value = pool
    else:
        mock_db.pool.side_effect = KeyError("switchboard pool not available")
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    return mock_db


# ---------------------------------------------------------------------------
# POST /api/ingestion/connectors/{type}/{identity}/pause
# ---------------------------------------------------------------------------


async def test_connector_pause_200_sets_state_and_audits(app):
    """POST pause on a healthy connector returns 200, sets state='paused', and audits."""
    returned_row = _make_row(_connector_row(state="paused"))
    conn = _make_conn(fetchrow_results=[returned_row])
    pool = _make_pool(conn)
    _wire_db(app, pool)

    with patch(
        "butlers.api.routers.ingestion_connectors._audit_append",
        new_callable=AsyncMock,
    ) as mock_audit:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/ingestion/connectors/gmail/user@example.com/pause")

    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["state"] == "paused"
    # Audit-action contract for pause.
    mock_audit.assert_awaited_once()
    assert mock_audit.call_args.kwargs["action"] == "connector.pause"


async def test_connector_pause_404_not_found(app):
    """POST pause on a non-existent connector returns 404 (logic-driven: registry lookup → None)."""
    # fetchrow returns None → connector not in registry → handler raises HTTPException 404
    conn = _make_conn(fetchrow_results=[None])
    pool = _make_pool(conn)
    _wire_db(app, pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/ingestion/connectors/gmail/nonexistent@example.com/pause")

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/ingestion/connectors/{type}/{identity}/run-now
# ---------------------------------------------------------------------------


async def test_connector_run_now_200_when_paused_and_audits(app):
    """POST run-now on a paused connector returns 200, clears pause, and audits."""
    # run-now does two fetchrow calls: SELECT FOR UPDATE (returns paused row), then UPDATE RETURNING
    paused_row = _make_row(_connector_row(state="paused"))
    updated_row = _make_row(_connector_row(state="unknown"))
    conn = _make_conn(fetchrow_results=[paused_row, updated_row])
    pool = _make_pool(conn)
    _wire_db(app, pool)

    with patch(
        "butlers.api.routers.ingestion_connectors._audit_append",
        new_callable=AsyncMock,
    ) as mock_audit:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/ingestion/connectors/gmail/user@example.com/run-now")

    assert resp.status_code == 200
    # Audit-action contract for run-now.
    mock_audit.assert_awaited_once()
    assert mock_audit.call_args.kwargs["action"] == "connector.run_now"


async def test_connector_run_now_409_when_not_paused(app):
    """POST run-now on a non-paused connector returns 409."""
    healthy_row = _make_row(_connector_row(state="healthy"))
    conn = _make_conn(fetchrow_results=[healthy_row])
    pool = _make_pool(conn)
    _wire_db(app, pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/ingestion/connectors/gmail/user@example.com/run-now")

    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# POST /api/ingestion/connectors/{type}/{identity}/archive  (bu-33dm2)
# POST /api/ingestion/connectors/{type}/{identity}/unarchive
# ---------------------------------------------------------------------------


async def test_connector_archive_200_sets_archived_and_audits(app):
    """POST archive returns 200, reports archived=true + timestamp, and audits."""
    archived_at = _dt.datetime(2026, 7, 10, 0, 0, 0, tzinfo=_dt.UTC)
    returned_row = _make_row(
        {
            "connector_type": "google_health",
            "endpoint_identity": "degraded",
            "archived_at": archived_at,
        }
    )
    conn = _make_conn(fetchrow_results=[returned_row])
    pool = _make_pool(conn)
    _wire_db(app, pool)

    with patch(
        "butlers.api.routers.ingestion_connectors._audit_append",
        new_callable=AsyncMock,
    ) as mock_audit:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/ingestion/connectors/google_health/degraded/archive")

    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["archived"] is True
    assert body["data"]["archived_at"] == archived_at.isoformat()
    mock_audit.assert_awaited_once()
    assert mock_audit.call_args.kwargs["action"] == "connector.archive"


async def test_connector_archive_404_not_found(app):
    """POST archive on a non-existent (or soft-deleted) connector returns 404."""
    conn = _make_conn(fetchrow_results=[None])
    pool = _make_pool(conn)
    _wire_db(app, pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/ingestion/connectors/gmail/ghost@example.com/archive")

    assert resp.status_code == 404


async def test_connector_unarchive_200_clears_archived_and_audits(app):
    """POST unarchive returns 200, reports archived=false, and audits."""
    returned_row = _make_row(
        {
            "connector_type": "google_health",
            "endpoint_identity": "degraded",
            "archived_at": None,
        }
    )
    conn = _make_conn(fetchrow_results=[returned_row])
    pool = _make_pool(conn)
    _wire_db(app, pool)

    with patch(
        "butlers.api.routers.ingestion_connectors._audit_append",
        new_callable=AsyncMock,
    ) as mock_audit:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/ingestion/connectors/google_health/degraded/unarchive")

    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["archived"] is False
    assert body["data"]["archived_at"] is None
    mock_audit.assert_awaited_once()
    assert mock_audit.call_args.kwargs["action"] == "connector.unarchive"


async def test_connector_unarchive_404_not_found(app):
    """POST unarchive on a non-existent connector returns 404."""
    conn = _make_conn(fetchrow_results=[None])
    pool = _make_pool(conn)
    _wire_db(app, pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/ingestion/connectors/gmail/ghost@example.com/unarchive")

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Audit-failure atomicity (bu-tjtp8)
#
# Regression for the silent-rollback bug: pause and run-now used to emit their
# audit_log entry INSIDE conn.transaction() with the exception swallowed. Under
# a real asyncpg connection an audit insert failure aborts the transaction, so
# the swallowed exception let the context manager COMMIT an already-aborted tx —
# Postgres turns that into a ROLLBACK, silently discarding the state change while
# the endpoint still returned 200 (false success). The fix moves audit emission
# post-commit (matching archive/unarchive/disconnect/rotate). These tests assert
# a failing audit does NOT roll the state change back and that audit is attempted
# only AFTER the state transaction has closed.
# ---------------------------------------------------------------------------


async def test_connector_pause_audit_failure_does_not_roll_back_state(app):
    """A failing pause audit insert leaves the state committed and returns true state."""
    returned_row = _make_row(_connector_row(state="paused"))
    tx_state = {"depth": 0}
    conn = _make_tracking_conn(fetchrow_results=[returned_row], tx_state=tx_state)
    pool = _make_pool(conn)
    _wire_db(app, pool)

    audit_tx_depth: list[int] = []

    async def _failing_audit(*args, **kwargs):
        audit_tx_depth.append(tx_state["depth"])
        raise RuntimeError("audit_log insert failed")

    with patch(
        "butlers.api.routers.ingestion_connectors._audit_append",
        new=AsyncMock(side_effect=_failing_audit),
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/ingestion/connectors/gmail/user@example.com/pause")

    # The state change survives the audit failure: 200 + the true mutated state.
    assert resp.status_code == 200
    assert resp.json()["data"]["state"] == "paused"
    # Audit was attempted exactly once, AFTER the state transaction committed
    # (depth 0) — so a failing audit can never roll the pause back.
    assert audit_tx_depth == [0]


async def test_connector_run_now_audit_failure_does_not_roll_back_state(app):
    """A failing run-now audit insert leaves the cleared pause committed, returns true state."""
    paused_row = _make_row(_connector_row(state="paused"))
    updated_row = _make_row(_connector_row(state="unknown"))
    tx_state = {"depth": 0}
    conn = _make_tracking_conn(fetchrow_results=[paused_row, updated_row], tx_state=tx_state)
    pool = _make_pool(conn)
    _wire_db(app, pool)

    audit_tx_depth: list[int] = []

    async def _failing_audit(*args, **kwargs):
        audit_tx_depth.append(tx_state["depth"])
        raise RuntimeError("audit_log insert failed")

    with patch(
        "butlers.api.routers.ingestion_connectors._audit_append",
        new=AsyncMock(side_effect=_failing_audit),
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/ingestion/connectors/gmail/user@example.com/run-now")

    # The cleared-pause state change survives the audit failure: 200 + true state.
    assert resp.status_code == 200
    assert resp.json()["data"]["state"] == "unknown"
    # Audit was attempted exactly once, AFTER the state transaction committed
    # (depth 0) — so a failing audit can never roll the resume back.
    assert audit_tx_depth == [0]
