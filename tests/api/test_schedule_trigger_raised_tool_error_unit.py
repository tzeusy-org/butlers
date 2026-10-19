"""A schedule_trigger tool that raises is a failed MCP call, so the route answers 503 (bu-zbafyz).

REQ-dashboard-api-073 says a failed MCP call (a raised tool error or an unreachable butler) is
not a reply and stays 503. The unreachable half is pinned by the audit DB test; this pins the
raised-tool half through a real fastmcp Client against an in-memory server, so the
``except Exception`` branch of ``_call_mcp_tool`` cannot drift to 502 or to an error reply.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastmcp import Client, FastMCP
from httpx import ASGITransport, AsyncClient

from butlers.api.deps import get_mcp_manager
from butlers.api.routers import schedules as schedules_module
from butlers.api.routers.schedules import _get_db_manager
from tests.api.auth_helpers import create_authenticated_domain_app as create_app

pytestmark = pytest.mark.unit

_SID = uuid.uuid4()
_HEADERS = {"X-API-Key": "synthetic-owner-key"}


async def test_a_raising_tool_is_503_and_audited_once_as_unavailable() -> None:
    server = FastMCP("trigger-raises")

    @server.tool(name="schedule_trigger")
    async def schedule_trigger(id: str) -> dict:
        raise RuntimeError("tool-raised")

    audit = AsyncMock()
    async with Client(server) as client:
        mgr = MagicMock()
        mgr.get_client = AsyncMock(return_value=client)
        app = create_app(api_key="synthetic-owner-key")
        app.dependency_overrides[get_mcp_manager] = lambda: mgr
        app.dependency_overrides[_get_db_manager] = lambda: MagicMock()
        with patch.object(schedules_module, "log_audit_entry", audit):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as http:
                resp = await http.post(
                    f"/api/butlers/qa/schedules/{_SID}/trigger", headers=_HEADERS
                )

    assert resp.status_code == 503, resp.text
    audit.assert_awaited_once()
    assert audit.await_args.kwargs["result"] == "error"
    assert audit.await_args.args[3]["observed_status"] == "unavailable"
