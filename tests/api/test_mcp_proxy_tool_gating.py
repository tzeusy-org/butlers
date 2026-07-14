"""Regression tests for dashboard MCP-proxy tool-group gating (bu-5d4je).

Core tools are gated by a butler's ``core_groups`` (the ``_core_tool(group)``
no-op decorator leaves a tool unregistered when its group is not enabled), so a
dashboard proxy that calls a structurally-absent core tool gets
``ToolError("Unknown tool: ...")`` from fastmcp. Before this fix the handlers
caught only ``ButlerUnreachableError`` and the ``ToolError`` escaped as HTTP 500
(and 502 for the module list). This asserts the graceful mapping:

- write proxies (state set/delete, module toggle) -> 409 naming the missing
  tool + core group (structural config state, not a transient fault);
- the module-list read proxy -> 200 with ``meta.module_states_unavailable``;
- a *genuine* ``ToolError`` on an ENABLED butler is NOT masked -- it still
  surfaces as a 5xx (classify-before-flagging, both directions tested).
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastmcp.exceptions import ToolError

from butlers.api.deps import (
    ButlerConnectionInfo,
    MCPClientManager,
    get_butler_configs,
    get_mcp_manager,
)
from butlers.api.routers.modules import _get_roster_dir
from butlers.api.routers.state import _get_db_manager as _state_get_db

pytestmark = pytest.mark.unit


def _tool_result(data: dict) -> MagicMock:
    item = MagicMock()
    item.text = json.dumps(data)
    result = MagicMock()
    result.content = [item]
    return result


def _mgr(behaviors: dict[str, object]) -> MCPClientManager:
    """Mock manager: maps butler name -> Exception (raised) or a call_tool result."""
    mgr = MagicMock(spec=MCPClientManager)
    clients: dict[str, MagicMock] = {}

    async def _get(name: str):
        if name not in clients:
            c = MagicMock()
            behavior = behaviors.get(name)
            if isinstance(behavior, Exception):
                c.call_tool = AsyncMock(side_effect=behavior)
            else:
                c.call_tool = AsyncMock(return_value=behavior)
            clients[name] = c
        return clients[name]

    mgr.get_client = AsyncMock(side_effect=_get)
    return mgr


def _client(app) -> httpx.AsyncClient:
    # raise_app_exceptions=False so a re-raised (genuine) ToolError renders as a
    # real 500 response instead of propagating into the test.
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    )


# ---------------------------------------------------------------------------
# state write proxies (switchboard omits the 'state' core group)
# ---------------------------------------------------------------------------


async def test_state_set_group_gated_returns_409(app):
    mgr = _mgr({"switchboard": ToolError("Unknown tool: 'state_set'")})
    app.dependency_overrides[get_mcp_manager] = lambda: mgr
    app.dependency_overrides[_state_get_db] = lambda: AsyncMock()

    async with _client(app) as client:
        resp = await client.put("/api/butlers/switchboard/state/foo", json={"value": 1})

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert "state_set" in detail
    assert "state" in detail
    assert "switchboard" in detail


async def test_state_delete_group_gated_returns_409(app):
    mgr = _mgr({"switchboard": ToolError("Unknown tool: 'state_delete'")})
    app.dependency_overrides[get_mcp_manager] = lambda: mgr
    app.dependency_overrides[_state_get_db] = lambda: AsyncMock()

    async with _client(app) as client:
        resp = await client.delete("/api/butlers/switchboard/state/foo")

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert "state_delete" in detail
    assert "state" in detail


async def test_state_set_genuine_toolerror_still_5xx(app):
    # ToolError NOT of the "Unknown tool" shape = the tool IS registered but
    # raised for its own reason. Must NOT be masked as 409.
    mgr = _mgr({"finance": ToolError("state_set failed: backing store offline")})
    app.dependency_overrides[get_mcp_manager] = lambda: mgr
    app.dependency_overrides[_state_get_db] = lambda: AsyncMock()

    async with _client(app) as client:
        resp = await client.put("/api/butlers/finance/state/foo", json={"value": 1})

    assert resp.status_code >= 500


# ---------------------------------------------------------------------------
# module toggle proxy (finance/relationship omit the 'module_mgmt' core group)
# ---------------------------------------------------------------------------


async def test_module_toggle_group_gated_returns_409(app, tmp_path):
    configs = [ButlerConnectionInfo(name="finance", port=41200)]
    mgr = _mgr({"finance": ToolError("Unknown tool: 'module.set_enabled'")})
    app.dependency_overrides[get_butler_configs] = lambda: configs
    app.dependency_overrides[get_mcp_manager] = lambda: mgr
    app.dependency_overrides[_get_roster_dir] = lambda: tmp_path

    async with _client(app) as client:
        resp = await client.put(
            "/api/butlers/finance/module-states/calendar/enabled",
            json={"enabled": False},
        )

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert "module.set_enabled" in detail
    assert "module_mgmt" in detail


async def test_module_toggle_genuine_toolerror_still_5xx(app, tmp_path):
    configs = [ButlerConnectionInfo(name="relationship", port=41300)]
    mgr = _mgr({"relationship": ToolError("module.set_enabled crashed mid-toggle")})
    app.dependency_overrides[get_butler_configs] = lambda: configs
    app.dependency_overrides[get_mcp_manager] = lambda: mgr
    app.dependency_overrides[_get_roster_dir] = lambda: tmp_path

    async with _client(app) as client:
        resp = await client.put(
            "/api/butlers/relationship/module-states/memory/enabled",
            json={"enabled": True},
        )

    assert resp.status_code >= 500


# ---------------------------------------------------------------------------
# module-list read proxy -> graceful degraded 200 (not 502)
# ---------------------------------------------------------------------------


async def test_module_states_group_gated_returns_200_degraded(app, tmp_path):
    configs = [ButlerConnectionInfo(name="relationship", port=41300)]
    mgr = _mgr({"relationship": ToolError("Unknown tool: 'module.states'")})
    app.dependency_overrides[get_butler_configs] = lambda: configs
    app.dependency_overrides[get_mcp_manager] = lambda: mgr
    app.dependency_overrides[_get_roster_dir] = lambda: tmp_path

    async with _client(app) as client:
        resp = await client.get("/api/butlers/relationship/module-states")

    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] == []
    assert body["meta"]["module_states_unavailable"] is True


async def test_module_states_genuine_toolerror_still_502(app, tmp_path):
    # A genuine tool crash on an ENABLED module.states must NOT degrade to a
    # truthful-looking empty 200 -- it stays a 5xx.
    configs = [ButlerConnectionInfo(name="finance", port=41200)]
    mgr = _mgr({"finance": ToolError("module.states raised in body")})
    app.dependency_overrides[get_butler_configs] = lambda: configs
    app.dependency_overrides[get_mcp_manager] = lambda: mgr
    app.dependency_overrides[_get_roster_dir] = lambda: tmp_path

    async with _client(app) as client:
        resp = await client.get("/api/butlers/finance/module-states")

    assert resp.status_code == 502


async def test_module_states_healthy_butler_unaffected(app, tmp_path):
    # Control: an enabled butler returns its module list with no degraded flag.
    configs = [ButlerConnectionInfo(name="finance", port=41200)]
    mgr = _mgr({"finance": _tool_result({"calendar": {"health": "active", "enabled": True}})})
    app.dependency_overrides[get_butler_configs] = lambda: configs
    app.dependency_overrides[get_mcp_manager] = lambda: mgr
    app.dependency_overrides[_get_roster_dir] = lambda: tmp_path

    async with _client(app) as client:
        resp = await client.get("/api/butlers/finance/module-states")

    assert resp.status_code == 200
    body = resp.json()
    assert [m["name"] for m in body["data"]] == ["calendar"]
    assert "module_states_unavailable" not in body["meta"]
