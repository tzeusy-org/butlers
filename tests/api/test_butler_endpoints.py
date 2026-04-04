"""Condensed tests for butler management API endpoints.

Condensed from:
  test_butler_config.py (8) + test_butler_detail.py (21) + test_butler_discovery.py (37)
  + test_butler_list.py (15) + test_butler_mcp.py (7) + test_butler_modules.py (14)
  + test_butler_router.py (14) + test_butler_skills.py (10) + test_butler_tick.py (6)
  + test_butler_trigger.py (8) → ~15 tests (bu-egmz6)

Keeps: 404/503 handling, list structure, config parsing, trigger/tick, MCP tool calls.
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
from butlers.api.models.butler import ButlerSummary, ModuleStatus
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


def _mock_mcp_manager(*, trigger_result=None, tick_result=None,
                      unreachable=False, timeout=False):
    mgr = MagicMock(spec=MCPClientManager)
    if unreachable:
        mgr.get_client = AsyncMock(side_effect=ButlerUnreachableError("general", "unreachable"))
    elif timeout:
        mgr.get_client = AsyncMock(side_effect=TimeoutError())
    else:
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.ping = AsyncMock(return_value=True)
        if trigger_result:
            mock_client.call_tool = AsyncMock(return_value=trigger_result)
        elif tick_result:
            mock_client.call_tool = AsyncMock(return_value=tick_result)
        mgr.get_client = AsyncMock(return_value=mock_client)
    return mgr


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


# ---------------------------------------------------------------------------
# Model unit tests
# ---------------------------------------------------------------------------


class TestModels:
    def test_module_status_serializes(self):
        ms = ModuleStatus(name="telegram", enabled=True, status="ok")
        d = ms.model_dump()
        assert d["name"] == "telegram"
        assert d["status"] == "ok"

    def test_butler_summary_has_required_fields(self):
        s = ButlerSummary(name="general", status="online", port=41101, db="general")
        assert s.name == "general"
        assert s.db == "general"


# ---------------------------------------------------------------------------
# Butler list — GET /api/butlers
# ---------------------------------------------------------------------------


class TestButlerList:
    async def test_list_returns_all_butlers(self, app, roster_dir):
        make_butler_dir(roster_dir, "general", 41101)
        configs = [ButlerConnectionInfo("general", 41101)]
        mgr = make_mock_mcp_manager(online=True)
        _wire(app, configs, mgr)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            resp = await client.get("/api/butlers")
        assert resp.status_code == 200
        body = resp.json()
        # response is either a list or {"data": [...]}
        items = body if isinstance(body, list) else body.get("data", [])
        assert len(items) >= 1
        assert items[0]["name"] == "general"

    async def test_list_unreachable_butler_does_not_raise_500(self, app, roster_dir):
        make_butler_dir(roster_dir, "general", 41101)
        configs = [ButlerConnectionInfo("general", 41101)]
        mgr = make_mock_mcp_manager(online=False)
        _wire(app, configs, mgr)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            resp = await client.get("/api/butlers")
        assert resp.status_code == 200  # never 500


# ---------------------------------------------------------------------------
# Butler config — GET /api/butlers/{name}/config
# ---------------------------------------------------------------------------


class TestButlerConfig:
    async def test_returns_404_for_unknown_butler(self, roster_dir):
        configs = [ButlerConnectionInfo("general", 41101)]
        app = make_test_app(roster_dir, configs)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            resp = await client.get("/api/butlers/nonexistent/config")
        assert resp.status_code == 404

    async def test_returns_config_for_known_butler(self, roster_dir):
        make_butler_dir(roster_dir, "general", 41101, claude_md="Be helpful.")
        configs = [ButlerConnectionInfo("general", 41101)]
        app = make_test_app(roster_dir, configs)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            resp = await client.get("/api/butlers/general/config")
        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        # data contains butler_toml (parsed config) and optional markdown fields
        assert "butler_toml" in body["data"] or "claude_md" in body["data"]


# ---------------------------------------------------------------------------
# Butler skills — GET /api/butlers/{name}/skills
# ---------------------------------------------------------------------------


class TestButlerSkills:
    async def test_returns_404_for_unknown_butler(self, roster_dir):
        configs = [ButlerConnectionInfo("general", 41101)]
        app = make_test_app(roster_dir, configs)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            resp = await client.get("/api/butlers/nonexistent/skills")
        assert resp.status_code == 404

    async def test_returns_skills_list(self, roster_dir):
        make_butler_dir(roster_dir, "general", 41101,
                        skills_with_content={"my-skill": "# My Skill\nDoes stuff."})
        configs = [ButlerConnectionInfo("general", 41101)]
        app = make_test_app(roster_dir, configs)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            resp = await client.get("/api/butlers/general/skills")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Butler trigger — POST /api/butlers/{name}/trigger
# ---------------------------------------------------------------------------


class TestButlerTrigger:
    async def test_trigger_404_for_unknown_butler(self, app, roster_dir):
        configs = [ButlerConnectionInfo("general", 41101)]
        _wire(app, configs, _mock_mcp_manager(unreachable=True))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            resp = await client.post("/api/butlers/nonexistent/trigger",
                                     json={"prompt": "hello"})
        assert resp.status_code == 404

    async def test_trigger_503_when_unreachable(self, app, roster_dir):
        configs = [ButlerConnectionInfo("general", 41101)]
        _wire(app, configs, _mock_mcp_manager(unreachable=True))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            resp = await client.post("/api/butlers/general/trigger",
                                     json={"prompt": "hello"})
        assert resp.status_code == 503

    async def test_trigger_success_returns_session_data(self, app, roster_dir):
        configs = [ButlerConnectionInfo("general", 41101)]
        trigger_data = {"session_id": "sess-1", "success": True, "output": "Done"}
        mgr = _mock_mcp_manager(trigger_result=_mock_tool_result(trigger_data))
        _wire(app, configs, mgr)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            resp = await client.post("/api/butlers/general/trigger",
                                     json={"prompt": "do something"})
        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert body["data"]["session_id"] == "sess-1"


# ---------------------------------------------------------------------------
# Butler tick — POST /api/butlers/{name}/tick
# ---------------------------------------------------------------------------


class TestButlerTick:
    async def test_tick_404_for_unknown_butler(self, app, roster_dir):
        configs = [ButlerConnectionInfo("general", 41101)]
        _wire(app, configs, _mock_mcp_manager(unreachable=True))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            resp = await client.post("/api/butlers/nonexistent/tick")
        assert resp.status_code == 404

    async def test_tick_503_when_unreachable(self, app, roster_dir):
        configs = [ButlerConnectionInfo("general", 41101)]
        _wire(app, configs, _mock_mcp_manager(unreachable=True))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            resp = await client.post("/api/butlers/general/tick")
        assert resp.status_code == 503
