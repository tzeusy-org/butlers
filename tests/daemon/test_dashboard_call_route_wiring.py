"""The dashboard service-call route lives on Switchboard's service listener only (RFC 0038 D7)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastmcp import FastMCP as RuntimeFastMCP
from starlette.testclient import TestClient

from butlers.core.service_plane.dashboard_route import SERVICE_CALL_PATH
from butlers.daemon import ButlerDaemon

pytestmark = pytest.mark.unit


def _paths(app) -> set[str]:
    return {getattr(route, "path", "") for route in app._app.routes}


def _daemon(name: str, pool: object | None) -> ButlerDaemon:
    daemon = ButlerDaemon.__new__(ButlerDaemon)
    daemon.config = SimpleNamespace(name=name)
    daemon.db = SimpleNamespace(pool=pool) if pool is not None else None
    return daemon


def test_only_switchboard_with_a_pool_builds_the_route() -> None:
    assert _daemon("health", object())._build_dashboard_call_route() is None
    assert _daemon("switchboard", None)._build_dashboard_call_route() is None
    route = _daemon("switchboard", object())._build_dashboard_call_route()
    assert route is not None and route.path == SERVICE_CALL_PATH


def test_the_daemon_opts_in_to_unsigned_forwarding_until_broker_cutover(monkeypatch) -> None:
    """Remove this opt-in at cutover (bu-jnhxc5), when the broker's signer is wired."""
    from butlers.core.service_plane import dashboard_route

    seen: dict = {}

    def build(pool, **kwargs):
        seen.update(kwargs)
        return "ROUTE"

    monkeypatch.setattr(dashboard_route, "build_switchboard_dashboard_call_route", build)

    assert _daemon("switchboard", object())._build_dashboard_call_route() == "ROUTE"
    assert seen == {"unsigned_until_broker_cutover": True}


def test_the_route_is_attached_for_switchboard_and_dark_by_default(monkeypatch) -> None:
    monkeypatch.delenv("BUTLERS_SERVICE_PLANE_AUTH", raising=False)
    route = _daemon("switchboard", object())._build_dashboard_call_route()
    app = ButlerDaemon._build_mcp_http_app(
        RuntimeFastMCP("switchboard"), butler_name="switchboard", dashboard_call_route=route
    )

    assert SERVICE_CALL_PATH in _paths(app)
    assert f"/mcp{SERVICE_CALL_PATH}" not in _paths(app)
    with TestClient(app) as client:
        response = client.post(SERVICE_CALL_PATH, json={})
    assert (response.status_code, response.json()["code"]) == (404, "not_found")


def test_without_a_route_nothing_is_attached() -> None:
    app = ButlerDaemon._build_mcp_http_app(RuntimeFastMCP("health"), butler_name="health")

    assert SERVICE_CALL_PATH not in _paths(app)


async def test_the_route_is_not_an_mcp_tool() -> None:
    mcp = RuntimeFastMCP("switchboard")
    before = {tool.name for tool in await mcp.list_tools()}

    ButlerDaemon._build_mcp_http_app(
        mcp,
        butler_name="switchboard",
        dashboard_call_route=_daemon("switchboard", object())._build_dashboard_call_route(),
    )

    assert {tool.name for tool in await mcp.list_tools()} == before
