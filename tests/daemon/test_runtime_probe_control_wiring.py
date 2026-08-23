"""Where the runtime-probe control route is --- and where it must not be.

REQ-dashboard-model-settings-001: "a model session, ordinary MCP client, or
unauthenticated caller ... cannot discover or invoke the runtime-probe control
command".  The route is therefore a plain ASGI route beside ``/health``, not a
FastMCP tool, and it exists only on Switchboard.

REQ-core-credentials-002 adds the phase constraint this bead lands under: with
no verifier keyring mounted --- which is every deployment today, because this
change adds no production mount --- the route answers ``503/unavailable`` and
does nothing else.  That is what "landed dark" means concretely, and it is
asserted here rather than assumed.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastmcp import FastMCP as RuntimeFastMCP
from starlette.testclient import TestClient

from butlers.core.runtime_probe_control.coordinator import ProbeResult, ProbeStatus
from butlers.core.runtime_probe_control.endpoint import CONTROL_PATH, READINESS_PATH
from butlers.daemon import ButlerDaemon, _McpSseDisconnectGuard

pytestmark = pytest.mark.unit


class _Coordinator:
    def __init__(self, result: ProbeResult) -> None:
        self._result = result
        self.calls: list[str | None] = []

    async def run(self, compact: str | None, **_kwargs: Any) -> ProbeResult:
        self.calls.append(compact)
        return self._result


def _paths(app: _McpSseDisconnectGuard) -> set[str]:
    return {getattr(route, "path", "") for route in app._app.routes}


def test_control_route_is_absent_without_a_coordinator() -> None:
    """Every butler but Switchboard has no control surface at all."""
    app = ButlerDaemon._build_mcp_http_app(RuntimeFastMCP("chronicler"), butler_name="chronicler")

    assert CONTROL_PATH not in _paths(app)

    with TestClient(app) as client:
        assert client.post(CONTROL_PATH).status_code == 404


def test_control_route_is_attached_for_switchboard() -> None:
    coordinator = _Coordinator(ProbeResult(ProbeStatus.UNAVAILABLE))
    app = ButlerDaemon._build_mcp_http_app(
        RuntimeFastMCP("switchboard"),
        butler_name="switchboard",
        runtime_probe_coordinator=coordinator,
    )

    assert CONTROL_PATH in _paths(app)

    with TestClient(app) as client:
        response = client.post(CONTROL_PATH, headers={"Authorization": "Bearer not.a.capability"})

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}


async def test_control_route_is_not_an_mcp_tool() -> None:
    """Criterion 5: the command is not in the generic tool surface.

    Enumerated through ``list_tools`` --- the same call an MCP client makes ---
    rather than by grepping for a name the tool might not have been given, so a
    tool registered under any name at all would still change this set.
    """
    mcp = RuntimeFastMCP("switchboard")
    before = {tool.name for tool in await mcp.list_tools()}

    ButlerDaemon._build_mcp_http_app(
        mcp,
        butler_name="switchboard",
        runtime_probe_coordinator=_Coordinator(ProbeResult(ProbeStatus.UNAVAILABLE)),
    )

    # Covers the readiness route too: it is attached in the same block.
    assert {tool.name for tool in await mcp.list_tools()} == before


def test_control_route_is_not_reachable_under_the_mcp_mount() -> None:
    """A path that resolves under ``/mcp`` would be reachable by an MCP client."""
    app = ButlerDaemon._build_mcp_http_app(
        RuntimeFastMCP("switchboard"),
        butler_name="switchboard",
        runtime_probe_coordinator=_Coordinator(ProbeResult(ProbeStatus.UNAVAILABLE)),
    )

    assert CONTROL_PATH in _paths(app)
    assert not CONTROL_PATH.startswith("/mcp")
    assert f"/mcp{CONTROL_PATH}" not in _paths(app)

    with TestClient(app) as client:
        assert client.post(f"/mcp{CONTROL_PATH}").status_code == 404


def test_readiness_route_is_attached_beside_the_control_route() -> None:
    """The gate is mounted only where the plane it advertises actually exists.

    A ``200/ready`` from a butler with no control route would tell the signed
    client to go and sign for a ``404``, so the two are attached together.
    """
    app = ButlerDaemon._build_mcp_http_app(
        RuntimeFastMCP("switchboard"),
        butler_name="switchboard",
        runtime_probe_coordinator=_Coordinator(ProbeResult(ProbeStatus.UNAVAILABLE)),
    )

    assert READINESS_PATH in _paths(app)
    assert not READINESS_PATH.startswith("/mcp")
    assert f"/mcp{READINESS_PATH}" not in _paths(app)

    with TestClient(app) as client:
        # No keyring is mounted in this phase, so the honest answer is "no".
        response = client.get(READINESS_PATH, params={"kid": "probe-not-mounted"})
        assert client.get(f"/mcp{READINESS_PATH}?kid=probe-not-mounted").status_code == 404

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}


def test_readiness_route_is_absent_without_a_coordinator() -> None:
    app = ButlerDaemon._build_mcp_http_app(RuntimeFastMCP("chronicler"), butler_name="chronicler")

    assert READINESS_PATH not in _paths(app)

    with TestClient(app) as client:
        assert client.get(READINESS_PATH, params={"kid": "probe-not-mounted"}).status_code == 404


def test_daemon_builds_no_coordinator_for_a_non_switchboard_butler() -> None:
    daemon = ButlerDaemon.__new__(ButlerDaemon)
    daemon.config = type("_Config", (), {"name": "chronicler"})()
    daemon.db = type("_Db", (), {"pool": object()})()
    daemon._credential_store = None

    assert daemon._build_runtime_probe_coordinator() is None


def test_daemon_builds_no_coordinator_without_a_pool() -> None:
    daemon = ButlerDaemon.__new__(ButlerDaemon)
    daemon.config = type("_Config", (), {"name": "switchboard"})()
    daemon.db = None
    daemon._credential_store = None

    assert daemon._build_runtime_probe_coordinator() is None


def test_switchboard_coordinator_uses_its_own_pool_and_credential_store() -> None:
    """Codex authority is handed over explicitly, never inferred from the pool."""
    pool = object()
    store = object()
    daemon = ButlerDaemon.__new__(ButlerDaemon)
    daemon.config = type("_Config", (), {"name": "switchboard"})()
    daemon.db = type("_Db", (), {"pool": pool})()
    daemon._credential_store = store

    coordinator = daemon._build_runtime_probe_coordinator()

    assert coordinator is not None
    assert coordinator._pool is pool
    assert coordinator._codex_authority is store
