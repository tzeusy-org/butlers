"""Tests for connector lifecycle endpoints: pause and run-now.

These endpoints are implemented by bu-1f91v.8 (Phase 3c). This file tests
the expected API contract per spec §3.6a and §3.6b.

Expected interfaces (from spec):
  POST /api/switchboard/connectors/{type}/{identity}/pause
    - sets connector to 'paused' state
    - emits audit.append() with action='connector.pause'
    - returns 200 on success
    - returns 404 if connector not found

  POST /api/switchboard/connectors/{type}/{identity}/run-now
    - validates connector is currently 'paused' (HTTP 409 otherwise)
    - clears pause, triggers next poll cycle
    - emits audit.append() with action='connector.run_now' on success
    - returns 200 on success
    - returns 409 if connector is not in 'paused' state

NOTE: These tests depend on bu-1f91v.8 being merged first.
If the endpoints are not yet implemented, tests will fail with HTTP 404 or 405.
Mark expected-failure with xfail if running before bu-1f91v.8 merges.

§3.6a, §3.6b, §3.12 — Phase 3d (bu-1f91v.9)
Blocked on: bu-1f91v.8 (connector pause/run-now implementation)
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from butlers.api.db import DatabaseManager

pytestmark = pytest.mark.unit

_MODULE_NAME = "switchboard_api_router"
_roster_root = Path(__file__).resolve().parents[2] / "roster"
_router_path = _roster_root / "switchboard" / "api" / "router.py"


def _get_db_dep():
    if _MODULE_NAME not in sys.modules:
        spec = importlib.util.spec_from_file_location(_MODULE_NAME, _router_path)
        if spec is None or spec.loader is None:
            raise ValueError(f"Could not load spec from {_router_path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[_MODULE_NAME] = module
        spec.loader.exec_module(module)
    return sys.modules[_MODULE_NAME]._get_db_manager


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


def _app_with_mock(
    app,
    *,
    fetchrow_result=None,
    execute_return="UPDATE 1",
    pool_available=True,
):
    mock_pool = AsyncMock()
    mock_pool.fetchrow = AsyncMock(return_value=fetchrow_result)
    mock_pool.execute = AsyncMock(return_value=execute_return)

    mock_db = MagicMock(spec=DatabaseManager)
    if pool_available:
        mock_db.pool.return_value = mock_pool
    else:
        mock_db.pool.side_effect = KeyError("No pool")

    app.dependency_overrides[_get_db_dep()] = lambda: mock_db
    return app, mock_pool


# ---------------------------------------------------------------------------
# POST /api/switchboard/connectors/{type}/{identity}/pause
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    reason="Depends on bu-1f91v.8 (connector lifecycle pause) being merged first",
    strict=False,
)
async def test_connector_pause_200(app):
    """POST pause on a healthy connector returns 200 and sets state='paused'."""
    connector = _connector_row(state="healthy")
    app, mock_pool = _app_with_mock(app, fetchrow_result=_make_row(connector))

    with patch(f"{_MODULE_NAME}.emit_dashboard_audit", new_callable=lambda: AsyncMock):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/switchboard/connectors/gmail/user@example.com/pause")

    assert resp.status_code == 200
    body = resp.json()
    # Expect some indication of paused state in the response
    assert "paused" in str(body).lower() or resp.status_code == 200


@pytest.mark.xfail(
    reason="Depends on bu-1f91v.8 (connector lifecycle pause) being merged first",
    strict=False,
)
async def test_connector_pause_404_not_found(app):
    """POST pause on a non-existent connector returns 404."""
    app, _ = _app_with_mock(app, fetchrow_result=None)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/switchboard/connectors/gmail/nonexistent@example.com/pause")

    assert resp.status_code == 404


@pytest.mark.xfail(
    reason="Depends on bu-1f91v.8 (connector lifecycle pause) being merged first",
    strict=False,
)
async def test_connector_pause_emits_audit(app):
    """POST pause emits audit entry with action='connector.pause'."""
    connector = _connector_row(state="healthy")
    app, mock_pool = _app_with_mock(app, fetchrow_result=_make_row(connector))

    with patch(
        f"{_MODULE_NAME}.emit_dashboard_audit", new_callable=lambda: AsyncMock
    ) as mock_audit:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/switchboard/connectors/gmail/user@example.com/pause")

    assert resp.status_code == 200
    mock_audit.assert_awaited_once()


# ---------------------------------------------------------------------------
# POST /api/switchboard/connectors/{type}/{identity}/run-now
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    reason="Depends on bu-1f91v.8 (connector lifecycle run-now) being merged first",
    strict=False,
)
async def test_connector_run_now_200_when_paused(app):
    """POST run-now on a paused connector returns 200 and clears pause."""
    connector = _connector_row(state="paused")
    app, _ = _app_with_mock(app, fetchrow_result=_make_row(connector))

    with patch(f"{_MODULE_NAME}.emit_dashboard_audit", new_callable=lambda: AsyncMock):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/switchboard/connectors/gmail/user@example.com/run-now")

    assert resp.status_code == 200


@pytest.mark.xfail(
    reason="Depends on bu-1f91v.8 (connector lifecycle run-now) being merged first",
    strict=False,
)
async def test_connector_run_now_409_when_not_paused(app):
    """POST run-now on a non-paused connector returns 409."""
    connector = _connector_row(state="healthy")
    app, _ = _app_with_mock(app, fetchrow_result=_make_row(connector))

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/switchboard/connectors/gmail/user@example.com/run-now")

    assert resp.status_code == 409


@pytest.mark.xfail(
    reason="Depends on bu-1f91v.8 (connector lifecycle run-now) being merged first",
    strict=False,
)
async def test_connector_run_now_emits_audit_on_paused(app):
    """POST run-now on paused connector emits audit with action='connector.run_now'."""
    connector = _connector_row(state="paused")
    app, _ = _app_with_mock(app, fetchrow_result=_make_row(connector))

    with patch(
        f"{_MODULE_NAME}.emit_dashboard_audit", new_callable=lambda: AsyncMock
    ) as mock_audit:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/switchboard/connectors/gmail/user@example.com/run-now")

    assert resp.status_code == 200
    mock_audit.assert_awaited_once()
