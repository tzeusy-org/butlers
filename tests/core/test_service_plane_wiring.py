"""Switchboard serves the relays on its service listener and dispatches via route() (bu-2c1g7v)."""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

import httpx
from starlette.applications import Starlette

import butlers.core.service_plane.wiring as wiring
from butlers.core.service_plane.registry import QA_REPORT_FINDING
from butlers.core.service_plane.workload_identity import DAEMON_HEADER, TOKEN_HEADER
from tests.core.test_service_plane_relay_endpoint import _FINDING, _up_token
from tests.core.test_service_plane_workload_identity import _verifier

_ROSTER = Path(__file__).resolve().parents[2] / "roster"


async def _post(monkeypatch, *, enabled: bool) -> tuple[httpx.Response, list[dict[str, Any]]]:
    if enabled:
        monkeypatch.setenv("BUTLERS_SERVICE_PLANE_AUTH", "1")
    else:
        monkeypatch.delenv("BUTLERS_SERVICE_PLANE_AUTH", raising=False)
    monkeypatch.setattr(wiring, "cached_verifier", lambda audience: _verifier(audience))
    routed: list[dict[str, Any]] = []

    async def fake_route(pool: Any, butler: str, tool: str, args: dict[str, Any], **kw: Any):
        routed.append({"pool": pool, "butler": butler, "tool": tool, "args": args, **kw})
        return {"result": {"accepted": True}}

    # The package re-exports route() under the module's own name, so patch the module object.
    route_module = importlib.import_module("butlers.tools.switchboard.routing.route")
    monkeypatch.setattr(route_module, "route", fake_route)
    app = Starlette(routes=[wiring.build_switchboard_relay_route("POOL", _ROSTER)])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as client:
        response = await client.post(
            f"http://sb/internal/service-plane/relay/{QA_REPORT_FINDING}",
            json={"args": _FINDING},
            headers={TOKEN_HEADER: _up_token(), DAEMON_HEADER: "travel"},
        )
    return response, routed


async def test_on_the_qa_relay_reaches_qa_through_switchboard_route(monkeypatch) -> None:
    response, routed = await _post(monkeypatch, enabled=True)

    assert response.status_code == 200
    assert routed == [
        {
            "pool": "POOL",
            "butler": "qa",
            "tool": "report_finding",
            "args": {**_FINDING, "source_butler": "travel"},
            "source_butler": f"relay:{QA_REPORT_FINDING}",
            "allow_stale": True,
        }
    ]


async def test_switchboard_serves_relays_on_the_service_listener_and_never_the_session_one(
    monkeypatch,
) -> None:
    from fastmcp import FastMCP

    from butlers.control import SessionRefusalListener
    from butlers.daemon import ButlerDaemon

    monkeypatch.setenv("BUTLERS_SERVICE_PLANE_AUTH", "1")
    monkeypatch.setattr(wiring, "cached_verifier", lambda audience: _verifier(audience))
    path = f"/internal/service-plane/relay/{QA_REPORT_FINDING}"
    service = ButlerDaemon._build_mcp_http_app(
        FastMCP("switchboard"),
        butler_name="switchboard",
        service_relay_route=wiring.build_switchboard_relay_route("POOL", _ROSTER),
    )
    listener = SessionRefusalListener(host="127.0.0.1", port=0)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=service), base_url="http://service"
    ) as current:
        # Unauthenticated: the relay answers with its own typed refusal.
        refused = await current.post(path, json={"args": _FINDING})
        assert refused.status_code == 403
        assert refused.json() == {"status": "refused", "code": "caller_unauthenticated"}
        await listener.start()
        try:
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{listener.port}") as session:
                assert (await session.post(path, json={"args": _FINDING})).status_code == 404
        finally:
            await listener.close()


async def test_off_the_route_refuses_and_routes_nothing(monkeypatch) -> None:
    response, routed = await _post(monkeypatch, enabled=False)

    assert response.status_code == 404
    assert routed == []
