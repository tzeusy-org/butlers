"""The schedule trigger route maps every outcome through a real fastmcp Client (bu-ukf1u0).

The route unit tests patch ``_call_mcp_tool``, so they never see the shape a real fastmcp
``Client`` returns (a ``CallToolResult`` object). Here the real helper reads the reply of an
in-memory FastMCP server, and the status mapping is pinned end to end: triggered 200, busy
409, not found 404, dispatch failure 502.
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
from butlers.core.tick_guard import BUSY_MESSAGE
from tests.api.auth_helpers import create_authenticated_domain_app as create_app

pytestmark = pytest.mark.unit

_SID = uuid.uuid4()
_HEADERS = {"X-API-Key": "synthetic-owner-key"}


async def _post_trigger(reply: dict):
    server = FastMCP("trigger-probe")

    @server.tool(name="schedule_trigger")
    async def schedule_trigger(id: str) -> dict:
        return reply

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
    return resp, audit


async def test_triggered_is_200() -> None:
    resp, audit = await _post_trigger({"id": str(_SID), "status": "triggered", "name": "x"})

    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["status"] == "triggered"
    audit.assert_awaited_once()
    assert audit.await_args.kwargs["result"] == "success"


async def test_busy_is_409_with_the_busy_message() -> None:
    resp, audit = await _post_trigger({"id": str(_SID), "status": "busy", "message": BUSY_MESSAGE})

    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"] == BUSY_MESSAGE
    audit.assert_awaited_once()
    assert audit.await_args.kwargs["result"] == "success"


async def test_not_found_is_404_audited_once_as_a_refusal() -> None:
    resp, audit = await _post_trigger(
        {"id": str(_SID), "status": "error", "code": "SCHEDULE_NOT_FOUND", "error": "gone"}
    )

    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "Schedule not found"
    audit.assert_awaited_once()
    assert audit.await_args.kwargs["result"] == "success"
    assert audit.await_args.args[3]["observed_status"] == "not_found"


async def test_a_dispatch_failure_is_502_with_the_reason_and_no_doubled_prefix() -> None:
    resp, audit = await _post_trigger(
        {"id": str(_SID), "status": "error", "error": "spawner exploded"}
    )

    assert resp.status_code == 502, resp.text
    assert resp.json()["detail"] == "spawner exploded"
    audit.assert_awaited_once()
    assert audit.await_args.kwargs["result"] == "error"


async def test_a_not_found_code_without_an_error_status_is_not_audited_as_a_refusal() -> None:
    # The route requires status == "error" as well as the code; the audit row must agree.
    resp, audit = await _post_trigger({"id": str(_SID), "code": "SCHEDULE_NOT_FOUND"})

    assert resp.status_code == 502, resp.text
    audit.assert_awaited_once()
    assert audit.await_args.kwargs["result"] == "error"
