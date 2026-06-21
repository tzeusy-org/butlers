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
