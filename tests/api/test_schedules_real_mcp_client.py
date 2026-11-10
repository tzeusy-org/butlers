"""Schedule trigger and toggle through the real FastMCP Client (bu-16ssys).

The other schedules tests hand the router mocked results. These run the locked
FastMCP ``Client`` against an in-memory FastMCP server, so the router parses
exactly what the SDK returns over a real MCP session, and the mutation audit
is classified from that parsed result, never from a stringified SDK object.

The busy case (a trigger refused while the tick guard is held, 409) waits on
bu-kcgfqb, which adds that refusal to ``schedule_trigger``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from butlers.api.db import DatabaseManager
from butlers.api.deps import MCPClientManager, get_db, get_mcp_manager
from butlers.api.routers import schedules

pytestmark = pytest.mark.unit


def _server(*, trigger: Any, toggle: Any) -> FastMCP:
    server = FastMCP("atlas")

    @server.tool
    async def schedule_trigger(id: str) -> dict:
        return trigger(id)

    @server.tool
    async def schedule_toggle(id: str, enabled: bool) -> dict:
        return toggle(id, enabled)

    return server


def _toggled(id: str, enabled: bool) -> dict:
    return {
        "id": id,
        "name": "daily_digest",
        "source": "db",
        "status": "updated",
        "outcome": "applied",
        "requested_enabled": enabled,
        "observed_enabled": enabled,
        "changed": True,
        "next_run_at": None,
        "audit": {"action": "schedule.toggle", "result": "success", "target": f"schedule:{id}"},
    }


def _fail(*_args: Any) -> dict:
    raise ToolError("synthetic tool failure")


class _Wired:
    def __init__(self, app, monkeypatch, server: FastMCP) -> None:
        self.app = app
        self.server = server
        self.mutation_audits: list[dict[str, Any]] = []
        self.toggle_audits: list[dict[str, Any]] = []

        async def log_audit_entry(db, actor, operation, summary, *, result, error=None):
            self.mutation_audits.append(
                {"operation": operation, "summary": summary, "result": result, "error": error}
            )

        async def audit_append(pool, actor, action, *, target, metadata, result, error=None):
            self.toggle_audits.append(
                {"action": action, "metadata": metadata, "result": result, "error": error}
            )

        monkeypatch.setattr(schedules, "log_audit_entry", log_audit_entry)
        monkeypatch.setattr(schedules, "audit_append", audit_append)
        db = MagicMock(spec=DatabaseManager)
        db.pool.return_value = AsyncMock()
        db.butler_names = ["atlas"]
        app.dependency_overrides[get_db] = lambda: db

    async def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        async with Client(self.server) as mcp:
            manager = AsyncMock(spec=MCPClientManager)
            manager.get_client = AsyncMock(return_value=mcp)
            self.app.dependency_overrides[get_mcp_manager] = lambda: manager
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=self.app), base_url="http://test"
            ) as http:
                return await http.request(method, path, **kwargs)


async def test_an_accepted_trigger_is_200_and_audited_as_success(app, monkeypatch) -> None:
    sid = str(uuid4())
    accepted = {"id": sid, "status": "triggered", "result": "dispatched"}
    wired = _Wired(app, monkeypatch, _server(trigger=lambda id: accepted, toggle=_toggled))

    response = await wired.request("POST", f"/api/butlers/atlas/schedules/{sid}/trigger")

    assert response.status_code == 200
    assert response.json()["data"] == accepted
    (audit,) = wired.mutation_audits
    assert audit["operation"] == "schedule.trigger"
    assert (audit["result"], audit["error"]) == ("success", None)
    assert audit["summary"]["observed_status"] == "triggered"


async def test_a_refused_trigger_is_audited_from_the_parsed_refusal(app, monkeypatch) -> None:
    sid = str(uuid4())
    refused = {"id": sid, "status": "error", "error": "Schedule not found"}
    wired = _Wired(app, monkeypatch, _server(trigger=lambda id: refused, toggle=_toggled))

    response = await wired.request("POST", f"/api/butlers/atlas/schedules/{sid}/trigger")

    assert response.status_code == 200
    (audit,) = wired.mutation_audits
    assert (audit["result"], audit["error"]) == ("error", "MCP_REFUSED")
    assert audit["summary"]["observed_status"] == "error"


async def test_a_trigger_the_tool_fails_is_503_and_audited_as_error(app, monkeypatch) -> None:
    sid = str(uuid4())
    wired = _Wired(app, monkeypatch, _server(trigger=_fail, toggle=_toggled))

    response = await wired.request("POST", f"/api/butlers/atlas/schedules/{sid}/trigger")

    assert response.status_code == 503
    assert "synthetic tool failure" not in str(wired.mutation_audits)
    (audit,) = wired.mutation_audits
    assert (audit["result"], audit["error"]) == ("error", "MCP call failed")
    assert audit["summary"]["observed_status"] == "unavailable"


async def test_a_successful_toggle_is_2xx_and_audited_as_success(app, monkeypatch) -> None:
    sid = str(uuid4())
    wired = _Wired(app, monkeypatch, _server(trigger=lambda id: {}, toggle=_toggled))

    response = await wired.request(
        "PATCH", f"/api/butlers/atlas/schedules/{sid}/toggle", json={"enabled": False}
    )

    assert response.status_code == 200
    assert response.json()["data"]["observed_enabled"] is False
    (audit,) = wired.toggle_audits
    assert (audit["action"], audit["result"], audit["error"]) == (
        "schedule.toggle",
        "success",
        None,
    )
    assert audit["metadata"]["observed_enabled"] is False
    assert audit["metadata"]["outcome"] == "applied"


async def test_a_typed_toggle_refusal_keeps_its_status_and_audit(app, monkeypatch) -> None:
    sid = str(uuid4())
    refusal = {"id": sid, "status": "error", "code": "SCHEDULE_TOML_MANAGED", "message": "m"}
    wired = _Wired(
        app, monkeypatch, _server(trigger=lambda id: {}, toggle=lambda id, enabled: refusal)
    )

    response = await wired.request(
        "PATCH", f"/api/butlers/atlas/schedules/{sid}/toggle", json={"enabled": True}
    )

    assert response.status_code == 409
    (audit,) = wired.toggle_audits
    assert (audit["result"], audit["error"]) == ("error", "SCHEDULE_TOML_MANAGED")
