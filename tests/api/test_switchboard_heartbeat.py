"""Tests for POST /api/switchboard/heartbeat endpoint.

Verifies heartbeat handling: last_seen_at updates, eligibility transitions,
stale→active logging, quarantined preservation, and error cases.

Issue: butlers-976.2
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager

_MODULE_NAME = "switchboard_api_router"
_roster_root = Path(__file__).resolve().parents[2] / "roster"
_router_path = _roster_root / "switchboard" / "api" / "router.py"

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_current_db_manager_dep() -> object:
    """Return the _get_db_manager function from whichever switchboard module
    is currently registered in sys.modules.

    This must be fetched at call time (not at import time) so that it matches
    the same module object that create_app() → discover_butler_routers() will
    use.  Re-executing the module at import time (as test_switchboard_views.py
    does unconditionally) can overwrite sys.modules and cause a mismatch
    between the dependency key used in dependency_overrides and the dependency
    registered in FastAPI's route graph.
    """
    if _MODULE_NAME in sys.modules:
        return sys.modules[_MODULE_NAME]._get_db_manager

    # Module not yet loaded — load it now
    spec = importlib.util.spec_from_file_location(_MODULE_NAME, _router_path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Could not load spec from {_router_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module._get_db_manager


def _app_with_heartbeat_mock(
    *,
    fetchrow_result: dict | None = None,
    pool_available: bool = True,
):
    """Create a FastAPI test app wired with a mocked DatabaseManager.

    ``fetchrow_result`` controls the row returned by pool.fetchrow() when the
    heartbeat handler queries butler_registry.  If None the butler is treated
    as not found (404).

    create_app() and dependency_overrides are resolved in the same call so
    that the _get_db_manager function object matches in both.
    """
    mock_pool = AsyncMock()
    mock_pool.fetchrow = AsyncMock(return_value=fetchrow_result)
    mock_pool.execute = AsyncMock(return_value=None)

    mock_db = MagicMock(spec=DatabaseManager)
    if pool_available:
        mock_db.pool.return_value = mock_pool
    else:
        mock_db.pool.side_effect = KeyError("No pool for butler: switchboard")

    # Fetch the current _get_db_manager BEFORE create_app() to ensure both
    # see the same module in sys.modules (create_app → discover_butler_routers
    # will also return the same cached module).
    get_db_manager = _get_current_db_manager_dep()
    app = create_app(cors_origins=["*"])
    app.dependency_overrides[get_db_manager] = lambda: mock_db

    return app, mock_pool


# ---------------------------------------------------------------------------
# POST /api/switchboard/heartbeat
# ---------------------------------------------------------------------------


class TestReceiveHeartbeat:
    async def test_active_butler_returns_200_with_active_state(self):
        """Valid heartbeat for an active butler returns 200 and active state."""
        fetchrow_result = {"eligibility_state": "active", "last_seen_at": None}
        app, _ = _app_with_heartbeat_mock(fetchrow_result=fetchrow_result)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/switchboard/heartbeat", json={"butler_name": "health"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["eligibility_state"] == "active"

    async def test_active_butler_updates_last_seen_at(self):
        """Heartbeat for active butler calls UPDATE on butler_registry."""
        fetchrow_result = {"eligibility_state": "active", "last_seen_at": None}
        app, mock_pool = _app_with_heartbeat_mock(fetchrow_result=fetchrow_result)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            await client.post("/api/switchboard/heartbeat", json={"butler_name": "health"})

        # execute should have been called once (UPDATE last_seen_at only)
        assert mock_pool.execute.call_count == 1
        sql_call = mock_pool.execute.call_args_list[0][0][0]
        assert "UPDATE butler_registry" in sql_call
        assert "last_seen_at" in sql_call

    async def test_stale_butler_transitions_to_active(self):
        """Stale butler receiving a heartbeat transitions to active state."""
        fetchrow_result = {"eligibility_state": "stale", "last_seen_at": None}
        app, _ = _app_with_heartbeat_mock(fetchrow_result=fetchrow_result)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/switchboard/heartbeat", json={"butler_name": "health"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["eligibility_state"] == "active"

    async def test_stale_transition_logs_to_eligibility_log(self):
        """Stale→active transition inserts a row into butler_registry_eligibility_log."""
        fetchrow_result = {"eligibility_state": "stale", "last_seen_at": None}
        app, mock_pool = _app_with_heartbeat_mock(fetchrow_result=fetchrow_result)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            await client.post("/api/switchboard/heartbeat", json={"butler_name": "health"})

        # Should have two execute calls: UPDATE + INSERT into eligibility_log
        assert mock_pool.execute.call_count == 2
        sql_calls = [c[0][0] for c in mock_pool.execute.call_args_list]
        assert any("UPDATE butler_registry" in s for s in sql_calls)
        assert any("butler_registry_eligibility_log" in s for s in sql_calls)

    async def test_stale_transition_update_includes_eligibility_state(self):
        """Stale→active UPDATE sets eligibility_state = 'active' and eligibility_updated_at."""
        fetchrow_result = {"eligibility_state": "stale", "last_seen_at": None}
        app, mock_pool = _app_with_heartbeat_mock(fetchrow_result=fetchrow_result)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            await client.post("/api/switchboard/heartbeat", json={"butler_name": "health"})

        update_sql = mock_pool.execute.call_args_list[0][0][0]
        assert "eligibility_state = 'active'" in update_sql
        assert "eligibility_updated_at" in update_sql

    async def test_quarantined_butler_stays_quarantined(self):
        """Quarantined butler receiving heartbeat keeps quarantined state."""
        fetchrow_result = {"eligibility_state": "quarantined", "last_seen_at": None}
        app, _ = _app_with_heartbeat_mock(fetchrow_result=fetchrow_result)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/switchboard/heartbeat", json={"butler_name": "health"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["eligibility_state"] == "quarantined"

    async def test_quarantined_butler_updates_last_seen_at_only(self):
        """Quarantined butler: only UPDATE last_seen_at, no eligibility log insert."""
        fetchrow_result = {"eligibility_state": "quarantined", "last_seen_at": None}
        app, mock_pool = _app_with_heartbeat_mock(fetchrow_result=fetchrow_result)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            await client.post("/api/switchboard/heartbeat", json={"butler_name": "health"})

        # Only one execute call for quarantined (no log insert)
        assert mock_pool.execute.call_count == 1
        sql_call = mock_pool.execute.call_args_list[0][0][0]
        assert "UPDATE butler_registry" in sql_call
        assert "butler_registry_eligibility_log" not in sql_call

    async def test_unknown_butler_returns_404(self):
        """Heartbeat for an unknown butler name returns 404."""
        app, _ = _app_with_heartbeat_mock(fetchrow_result=None)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/switchboard/heartbeat", json={"butler_name": "unknown-butler"}
            )

        assert resp.status_code == 404

    async def test_missing_butler_name_returns_422(self):
        """Missing butler_name field returns 422 Unprocessable Entity."""
        app, _ = _app_with_heartbeat_mock(fetchrow_result=None)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/switchboard/heartbeat", json={})

        assert resp.status_code == 422

    async def test_malformed_body_returns_422(self):
        """Non-JSON or unexpected body shape returns 422."""
        app, _ = _app_with_heartbeat_mock(fetchrow_result=None)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/switchboard/heartbeat",
                content="not-json",
                headers={"Content-Type": "application/json"},
            )

        assert resp.status_code == 422

    async def test_pool_unavailable_returns_503(self):
        """When the switchboard DB pool is unavailable, return 503."""
        app, _ = _app_with_heartbeat_mock(pool_available=False)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/switchboard/heartbeat", json={"butler_name": "health"})

        assert resp.status_code == 503

    async def test_response_shape(self):
        """Response body must contain 'status' and 'eligibility_state' fields."""
        fetchrow_result = {"eligibility_state": "active", "last_seen_at": None}
        app, _ = _app_with_heartbeat_mock(fetchrow_result=fetchrow_result)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/switchboard/heartbeat", json={"butler_name": "health"})

        assert resp.status_code == 200
        body = resp.json()
        assert "status" in body
        assert "eligibility_state" in body

    async def test_eligibility_log_insert_uses_correct_states(self):
        """Eligibility log INSERT records previous_state=stale, new_state=active."""
        fetchrow_result = {"eligibility_state": "stale", "last_seen_at": None}
        app, mock_pool = _app_with_heartbeat_mock(fetchrow_result=fetchrow_result)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            await client.post("/api/switchboard/heartbeat", json={"butler_name": "health"})

        # Find the INSERT call
        insert_call = None
        for c in mock_pool.execute.call_args_list:
            if "INSERT INTO butler_registry_eligibility_log" in c[0][0]:
                insert_call = c
                break

        assert insert_call is not None, "Expected INSERT into butler_registry_eligibility_log"
        # Args: butler_name, previous_state, new_state, reason,
        #       prev_last_seen, new_last_seen, observed_at
        args = insert_call[0][1:]  # skip the SQL string
        assert args[0] == "health"  # butler_name
        assert args[1] == "stale"  # previous_state
        assert args[2] == "active"  # new_state
        assert "heartbeat" in args[3].lower()  # reason
