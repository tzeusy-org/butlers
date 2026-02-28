"""Tests for POST /api/switchboard/heartbeat endpoint.

Verifies heartbeat handling: last_seen_at updates, eligibility transitions,
stale→active logging, quarantined→active auto-recovery, and error cases.

Issue: butlers-976.2
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

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
    app,
    *,
    fetchrow_result: dict | None = None,
    pool_available: bool = True,
    execute_return: str | None = None,
):
    """Wire a FastAPI app with a mocked DatabaseManager for heartbeat tests.

    ``fetchrow_result`` controls the row returned by pool.fetchrow() when the
    heartbeat handler queries butler_registry.  If None the butler is treated
    as not found (404).

    ``execute_return`` controls the string returned by pool.execute().  For
    stale→active tests, pass ``"UPDATE 1"`` so that the compare-and-set guard
    in the handler counts one affected row and proceeds with the transition.
    """
    mock_pool = AsyncMock()
    mock_pool.fetchrow = AsyncMock(return_value=fetchrow_result)
    mock_pool.execute = AsyncMock(return_value=execute_return)

    mock_db = MagicMock(spec=DatabaseManager)
    if pool_available:
        mock_db.pool.return_value = mock_pool
    else:
        mock_db.pool.side_effect = KeyError("No pool for butler: switchboard")

    get_db_manager = _get_current_db_manager_dep()
    app.dependency_overrides[get_db_manager] = lambda: mock_db

    return app, mock_pool


# ---------------------------------------------------------------------------
# POST /api/switchboard/heartbeat
# ---------------------------------------------------------------------------


class TestReceiveHeartbeat:
    async def test_active_butler_returns_200_with_active_state(self, app):
        """Valid heartbeat for an active butler returns 200 and active state."""
        fetchrow_result = {"eligibility_state": "active", "last_seen_at": None}
        _app_with_heartbeat_mock(app, fetchrow_result=fetchrow_result)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/switchboard/heartbeat", json={"butler_name": "health"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["eligibility_state"] == "active"

    async def test_active_butler_updates_last_seen_at(self, app):
        """Heartbeat for active butler calls UPDATE on butler_registry."""
        fetchrow_result = {"eligibility_state": "active", "last_seen_at": None}
        _, mock_pool = _app_with_heartbeat_mock(app, fetchrow_result=fetchrow_result)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            await client.post("/api/switchboard/heartbeat", json={"butler_name": "health"})

        # execute should have been called once (UPDATE last_seen_at only)
        assert mock_pool.execute.call_count == 1
        sql_call = mock_pool.execute.call_args_list[0][0][0]
        assert "UPDATE butler_registry" in sql_call
        assert "last_seen_at" in sql_call

    async def test_stale_butler_transitions_to_active(self, app):
        """Stale butler receiving a heartbeat transitions to active state."""
        fetchrow_result = {"eligibility_state": "stale", "last_seen_at": None}
        _app_with_heartbeat_mock(app, fetchrow_result=fetchrow_result, execute_return="UPDATE 1")

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/switchboard/heartbeat", json={"butler_name": "health"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["eligibility_state"] == "active"

    async def test_stale_transition_logs_to_eligibility_log(self, app):
        """Stale→active transition inserts a row into butler_registry_eligibility_log."""
        fetchrow_result = {"eligibility_state": "stale", "last_seen_at": None}
        _, mock_pool = _app_with_heartbeat_mock(
            app, fetchrow_result=fetchrow_result, execute_return="UPDATE 1"
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            await client.post("/api/switchboard/heartbeat", json={"butler_name": "health"})

        # Should have two execute calls: UPDATE + INSERT into eligibility_log
        assert mock_pool.execute.call_count == 2
        sql_calls = [c[0][0] for c in mock_pool.execute.call_args_list]
        assert any("UPDATE butler_registry" in s for s in sql_calls)
        assert any("butler_registry_eligibility_log" in s for s in sql_calls)

    async def test_stale_transition_update_includes_eligibility_state(self, app):
        """Stale→active UPDATE sets eligibility_state = 'active' and eligibility_updated_at."""
        fetchrow_result = {"eligibility_state": "stale", "last_seen_at": None}
        _, mock_pool = _app_with_heartbeat_mock(
            app, fetchrow_result=fetchrow_result, execute_return="UPDATE 1"
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            await client.post("/api/switchboard/heartbeat", json={"butler_name": "health"})

        update_sql = mock_pool.execute.call_args_list[0][0][0]
        assert "eligibility_state = 'active'" in update_sql
        assert "eligibility_updated_at" in update_sql

    async def test_quarantined_butler_recovers_to_active(self, app):
        """Quarantined butler receiving heartbeat transitions to active state."""
        fetchrow_result = {"eligibility_state": "quarantined", "last_seen_at": None}
        _app_with_heartbeat_mock(app, fetchrow_result=fetchrow_result, execute_return="UPDATE 1")

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/switchboard/heartbeat", json={"butler_name": "health"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["eligibility_state"] == "active"

    async def test_quarantined_butler_recovery_logs_transition(self, app):
        """Quarantined→active recovery inserts eligibility_log row."""
        fetchrow_result = {"eligibility_state": "quarantined", "last_seen_at": None}
        _, mock_pool = _app_with_heartbeat_mock(
            app, fetchrow_result=fetchrow_result, execute_return="UPDATE 1"
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            await client.post("/api/switchboard/heartbeat", json={"butler_name": "health"})

        # Should have two execute calls: UPDATE + INSERT into eligibility_log
        assert mock_pool.execute.call_count == 2
        # Find the INSERT call
        insert_call = None
        for c in mock_pool.execute.call_args_list:
            if "INSERT INTO butler_registry_eligibility_log" in c[0][0]:
                insert_call = c
                break
        assert insert_call is not None, "Expected INSERT into butler_registry_eligibility_log"
        args = insert_call[0][1:]
        assert args[0] == "health"  # butler_name
        assert args[1] == "quarantined"  # previous_state
        assert args[2] == "active"  # new_state
        assert args[3] == "heartbeat_recovery"  # reason

    async def test_quarantined_recovery_concurrent_modification_fallback(self, app):
        """If quarantined CAS UPDATE affects 0 rows (concurrent modification),
        fall back to re-reading state and only updating last_seen_at."""
        fetchrow_result = {"eligibility_state": "quarantined", "last_seen_at": None}
        _, mock_pool = _app_with_heartbeat_mock(
            app, fetchrow_result=fetchrow_result, execute_return=None
        )
        mock_pool.fetchrow.side_effect = [
            fetchrow_result,  # initial SELECT
            {"eligibility_state": "active"},  # re-read after CAS miss
        ]

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/switchboard/heartbeat", json={"butler_name": "health"})

        assert resp.status_code == 200
        body = resp.json()
        # Should reflect the re-read state
        assert body["eligibility_state"] == "active"
        # No eligibility log INSERT should have occurred
        sql_calls = [c[0][0] for c in mock_pool.execute.call_args_list]
        assert not any("butler_registry_eligibility_log" in s for s in sql_calls)

    async def test_unknown_butler_returns_404(self, app):
        """Heartbeat for an unknown butler name returns 404."""
        _app_with_heartbeat_mock(app, fetchrow_result=None)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/switchboard/heartbeat", json={"butler_name": "unknown-butler"}
            )

        assert resp.status_code == 404

    async def test_missing_registry_row_auto_registers_from_roster(self, app):
        """If butler is missing from registry but exists in roster, heartbeat succeeds."""
        _, mock_pool = _app_with_heartbeat_mock(app, fetchrow_result=None)
        mock_pool.fetchrow = AsyncMock(
            side_effect=[
                None,
                {"eligibility_state": "active", "last_seen_at": None},
            ]
        )
        router_module = sys.modules[_MODULE_NAME]
        register_mock = AsyncMock(return_value=True)

        with patch.object(
            router_module,
            "_register_missing_butler_from_roster",
            new=register_mock,
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/switchboard/heartbeat",
                    json={"butler_name": "health"},
                )

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["eligibility_state"] == "active"
        register_mock.assert_awaited_once_with(mock_pool, "health")

    async def test_missing_butler_name_returns_422(self, app):
        """Missing butler_name field returns 422 Unprocessable Entity."""
        _app_with_heartbeat_mock(app, fetchrow_result=None)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/switchboard/heartbeat", json={})

        assert resp.status_code == 422

    async def test_malformed_body_returns_422(self, app):
        """Non-JSON or unexpected body shape returns 422."""
        _app_with_heartbeat_mock(app, fetchrow_result=None)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/switchboard/heartbeat",
                content="not-json",
                headers={"Content-Type": "application/json"},
            )

        assert resp.status_code == 422

    async def test_pool_unavailable_returns_503(self, app):
        """When the switchboard DB pool is unavailable, return 503."""
        _app_with_heartbeat_mock(app, pool_available=False)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/switchboard/heartbeat", json={"butler_name": "health"})

        assert resp.status_code == 503

    async def test_response_shape(self, app):
        """Response body must contain 'status' and 'eligibility_state' fields."""
        fetchrow_result = {"eligibility_state": "active", "last_seen_at": None}
        _app_with_heartbeat_mock(app, fetchrow_result=fetchrow_result)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/switchboard/heartbeat", json={"butler_name": "health"})

        assert resp.status_code == 200
        body = resp.json()
        assert "status" in body
        assert "eligibility_state" in body

    async def test_eligibility_log_insert_uses_correct_states(self, app):
        """Eligibility log INSERT records previous_state=stale, new_state=active."""
        fetchrow_result = {"eligibility_state": "stale", "last_seen_at": None}
        _, mock_pool = _app_with_heartbeat_mock(
            app, fetchrow_result=fetchrow_result, execute_return="UPDATE 1"
        )

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
        assert (
            args[3] == "health_restored"
        )  # canonical reason (matches registry._transition_reason)

    async def test_stale_transition_skipped_when_row_concurrently_modified(self, app):
        """If the compare-and-set UPDATE affects 0 rows (concurrent modification),
        the handler falls back to re-reading state and only updates last_seen_at."""
        fetchrow_result = {"eligibility_state": "stale", "last_seen_at": None}
        # execute_return=None simulates 0 rows affected (concurrent quarantine)
        _, mock_pool = _app_with_heartbeat_mock(
            app, fetchrow_result=fetchrow_result, execute_return=None
        )
        # After the failed UPDATE, fetchrow is called again to re-read state
        # Simulate the row being quarantined by the time we re-read it
        mock_pool.fetchrow.side_effect = [
            fetchrow_result,  # first call: initial SELECT
            {"eligibility_state": "quarantined"},  # second call: re-read after CAS miss
        ]

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/switchboard/heartbeat", json={"butler_name": "health"})

        assert resp.status_code == 200
        body = resp.json()
        # Should reflect the re-read state (quarantined), not active
        assert body["eligibility_state"] == "quarantined"
        # No eligibility log INSERT should have occurred
        sql_calls = [c[0][0] for c in mock_pool.execute.call_args_list]
        assert not any("butler_registry_eligibility_log" in s for s in sql_calls)
