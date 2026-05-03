"""Condensed tests for butler management API endpoints.

Condensed from:
  test_butler_config.py (8) + test_butler_detail.py (21) + test_butler_discovery.py (37)
  + test_butler_list.py (15) + test_butler_mcp.py (7) + test_butler_modules.py (14)
  + test_butler_router.py (14) + test_butler_skills.py (10) + test_butler_tick.py (6)
  + test_butler_trigger.py (8) → ~15 tests (bu-egmz6) → 4 tests (bu-2yw2d)

Keeps: list structure, 404/503 CRUD paths (parametrized), trigger success, config 200.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.db import DatabaseManager
from butlers.api.deps import (
    ButlerConnectionInfo,
    ButlerUnreachableError,
    MCPClientManager,
    get_butler_configs,
    get_mcp_manager,
)
from butlers.api.routers.butlers import _get_db_manager

from .conftest import make_butler_dir, make_mock_mcp_manager, make_test_app

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_audit_db():
    pool = AsyncMock()
    pool.execute = AsyncMock()
    db = MagicMock(spec=DatabaseManager)
    db.pool.return_value = pool
    return db


def _mock_tool_result(data: dict) -> MagicMock:
    block = MagicMock()
    block.text = json.dumps(data)
    result = MagicMock()
    result.content = [block]
    result.is_error = False
    return result


def _wire(app, configs, mcp_manager=None, db=None):
    if mcp_manager is None:
        mcp_manager = make_mock_mcp_manager(online=True)
    if db is None:
        db = _mock_audit_db()
    app.dependency_overrides[get_butler_configs] = lambda: configs
    app.dependency_overrides[get_mcp_manager] = lambda: mcp_manager
    app.dependency_overrides[_get_db_manager] = lambda: db
    return app


def _mcp_unreachable():
    mgr = MagicMock(spec=MCPClientManager)
    mgr.get_client = AsyncMock(
        side_effect=ButlerUnreachableError("general", cause=ConnectionRefusedError("unreachable"))
    )
    return mgr


# ---------------------------------------------------------------------------
# Butler list — all butlers returned, unreachable never 500
# ---------------------------------------------------------------------------


class TestButlerList:
    async def test_list_returns_all_butlers_and_unreachable_never_500(self, app, roster_dir):
        make_butler_dir(roster_dir, "general", 41101)
        configs = [ButlerConnectionInfo("general", 41101)]
        # Online case
        _wire(app, configs, make_mock_mcp_manager(online=True))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp_ok = await client.get("/api/butlers")
        assert resp_ok.status_code == 200
        assert resp_ok.json()["data"][0]["name"] == "general"

        # Offline — still 200, never 500
        _wire(app, configs, make_mock_mcp_manager(online=False))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp_down = await client.get("/api/butlers")
        assert resp_down.status_code == 200


# ---------------------------------------------------------------------------
# Butler config — 404 unknown, 200 known
# ---------------------------------------------------------------------------


class TestButlerConfig:
    async def test_config_404_unknown_and_200_known(self, roster_dir):
        make_butler_dir(roster_dir, "general", 41101, claude_md="Be helpful.")
        configs = [ButlerConnectionInfo("general", 41101)]
        app = make_test_app(roster_dir, configs)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            r404 = await client.get("/api/butlers/nonexistent/config")
            r200 = await client.get("/api/butlers/general/config")
        assert r404.status_code == 404
        assert r200.status_code == 200
        assert "data" in r200.json()


# ---------------------------------------------------------------------------
# Butler trigger — 404 unknown, 503 unreachable, 200 success
# ---------------------------------------------------------------------------


class TestButlerTrigger:
    @pytest.mark.parametrize(
        "butler_name,expected",
        [("nonexistent", 404), ("general", 503)],
        ids=["404-unknown", "503-unreachable"],
    )
    async def test_trigger_error_paths(self, app, roster_dir, butler_name, expected):
        configs = [ButlerConnectionInfo("general", 41101)]
        _wire(app, configs, _mcp_unreachable())
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                f"/api/butlers/{butler_name}/trigger", json={"prompt": "hello"}
            )
        assert resp.status_code == expected

    async def test_trigger_success_returns_session_data(self, app, roster_dir):
        configs = [ButlerConnectionInfo("general", 41101)]
        trigger_data = {"session_id": "sess-1", "success": True, "output": "Done"}
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.call_tool = AsyncMock(return_value=_mock_tool_result(trigger_data))
        mgr = MagicMock(spec=MCPClientManager)
        mgr.get_client = AsyncMock(return_value=mock_client)
        _wire(app, configs, mgr)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/butlers/general/trigger", json={"prompt": "do something"}
            )
        assert resp.status_code == 200
        assert resp.json()["data"]["session_id"] == "sess-1"
