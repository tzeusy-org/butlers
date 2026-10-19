"""The schedules routes parse a real fastmcp ``CallToolResult`` (bu-kcgfqb C5).

The dashboard's MCP client is a fastmcp ``Client``. Its ``call_tool`` returns a
``CallToolResult`` object, not a bare list of content blocks. Route tests that patch
``_call_mcp_tool`` or mock ``call_tool`` with a list never see that shape, so these tests
drive the real ``_call_mcp_tool`` through a real ``Client`` against an in-memory server.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastmcp import Client, FastMCP
from httpx import ASGITransport, AsyncClient

from butlers.api.deps import get_mcp_manager
from butlers.api.routers import schedules as schedules_module
from butlers.api.routers.schedules import _get_db_manager
from butlers.core.tick_guard import BUSY_MESSAGE
from tests.api.auth_helpers import create_authenticated_domain_app as create_app

pytestmark = pytest.mark.unit

_HEADERS = {"X-API-Key": "synthetic-owner-key"}
_SID = uuid.uuid4()


def _server(tool_name: str, reply: dict[str, Any]) -> FastMCP:
    server = FastMCP("schedules-probe")

    if tool_name == "schedule_toggle":

        @server.tool(name="schedule_toggle")
        async def schedule_toggle(id: str, enabled: bool) -> dict:
            return reply

    else:

        @server.tool(name=tool_name)
        async def schedule_trigger(id: str) -> dict:
            return reply

    return server


async def _request(method: str, path: str, tool_name: str, reply: dict, **kwargs: Any):
    audit = AsyncMock()
    toggle_audit = AsyncMock()
    async with Client(_server(tool_name, reply)) as client:
        mgr = MagicMock()
        mgr.get_client = AsyncMock(return_value=client)
        app = create_app(api_key="synthetic-owner-key")
        app.dependency_overrides[get_mcp_manager] = lambda: mgr
        app.dependency_overrides[_get_db_manager] = lambda: MagicMock()
        with (
            patch.object(schedules_module, "log_audit_entry", audit),
            patch.object(schedules_module, "audit_append", toggle_audit),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as http:
                resp = await http.request(method, path, headers=_HEADERS, **kwargs)
    return resp, (toggle_audit if tool_name == "schedule_toggle" else audit)


async def test_a_busy_trigger_through_a_real_client_is_409_with_the_busy_message() -> None:
    reply = {"id": str(_SID), "status": "busy", "message": BUSY_MESSAGE}

    resp, audit = await _request(
        "POST", f"/api/butlers/qa/schedules/{_SID}/trigger", "schedule_trigger", reply
    )

    assert resp.status_code == 409, resp.text
    assert BUSY_MESSAGE in resp.text
    audit.assert_awaited_once()
    _args, kwargs = audit.await_args
    assert kwargs["result"] == "success"
    assert _args[3]["observed_status"] == "busy"


async def test_a_triggered_schedule_through_a_real_client_is_200() -> None:
    reply = {"id": str(_SID), "status": "triggered", "name": "insight-urgent"}

    resp, audit = await _request(
        "POST", f"/api/butlers/qa/schedules/{_SID}/trigger", "schedule_trigger", reply
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["status"] == "triggered"
    audit.assert_awaited_once()
    assert audit.await_args.kwargs["result"] == "success"


async def test_a_successful_toggle_through_a_real_client_is_2xx() -> None:
    reply = {
        "id": str(_SID),
        "name": "insight-urgent",
        "source": "db",
        "status": "updated",
        "outcome": "applied",
        "requested_enabled": False,
        "observed_enabled": False,
        "changed": True,
        "next_run_at": None,
        "audit": {"action": "schedule.toggle", "result": "success", "target": str(_SID)},
    }

    resp, audit = await _request(
        "PATCH",
        f"/api/butlers/qa/schedules/{_SID}/toggle",
        "schedule_toggle",
        reply,
        json={"enabled": False},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["observed_enabled"] is False
    audit.assert_awaited_once()
    assert audit.await_args.kwargs["result"] == "success"
