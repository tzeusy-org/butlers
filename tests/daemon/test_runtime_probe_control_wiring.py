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

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastmcp import FastMCP as RuntimeFastMCP
from starlette.testclient import TestClient

from butlers.core.control_plane_identity import IDENTITY_PATH
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


def test_same_port_identity_is_not_an_mcp_tool_and_refuses_until_committed_boot() -> None:
    daemon = ButlerDaemon.__new__(ButlerDaemon)
    daemon.config = SimpleNamespace(
        name="health",
        runtime_seed=SimpleNamespace(route_contract_min=1, route_contract_max=1),
    )
    daemon._boot_instance_id = uuid.UUID("0199a5b0-0000-7000-8000-000000000001")
    daemon._boot_epoch = None
    daemon._accepting_connections = False
    daemon._shutting_down = False
    daemon.spawner = SimpleNamespace(_accepting=True)
    daemon._server_task = SimpleNamespace(done=lambda: False)
    mcp = RuntimeFastMCP("health")
    app = ButlerDaemon._build_mcp_http_app(
        mcp, butler_name="health", identity_provider=daemon._identity_facts
    )

    with TestClient(app) as client:
        before = client.get(IDENTITY_PATH)
        daemon._boot_epoch = 4
        daemon._accepting_connections = True
        ready = client.get(IDENTITY_PATH)
        daemon._shutting_down = True
        stopping = client.get(IDENTITY_PATH)

    assert before.status_code == 200 and before.json()["accepting_routes"] is False
    assert before.json()["boot_epoch"] == 0
    assert ready.json()["accepting_routes"] is True
    assert ready.json()["boot_epoch"] == 4
    assert stopping.json()["accepting_routes"] is False
    assert set(ready.json()) == {
        "schema_version",
        "butler_name",
        "boot_instance_id",
        "boot_epoch",
        "route_contract",
        "accepting_routes",
    }
    assert IDENTITY_PATH in _paths(app)
    assert f"/mcp{IDENTITY_PATH}" not in _paths(app)


async def test_switchboard_seeds_only_missing_roster_rows_before_boot_registration(
    tmp_path,
) -> None:
    daemon = ButlerDaemon.__new__(ButlerDaemon)
    daemon.config = SimpleNamespace(name="switchboard")
    daemon.config_dir = tmp_path / "switchboard"
    daemon._shutting_down = False
    daemon._boot_instance_id = uuid.UUID("0199a5b0-0000-7000-8000-000000000002")
    daemon._boot_epoch = None
    order: list[str] = []

    async def register(_sql, _name, _uuid):
        order.append("register")
        return 1

    pool = SimpleNamespace(fetchval=AsyncMock(side_effect=register))
    daemon.db = SimpleNamespace(pool=pool)

    async def seed(_pool, _roster_dir):
        order.append("seed")
        return 1

    with patch(
        "butlers.tools.switchboard.registry.registry.seed_missing_roster_butlers",
        new=AsyncMock(side_effect=seed),
    ):
        assert await daemon._register_boot_epoch() is True
    assert order == ["seed", "register"]
    assert daemon._boot_epoch == 1


async def test_identity_route_does_not_expand_model_tool_enumeration() -> None:
    mcp = RuntimeFastMCP("health")
    before = {tool.name for tool in await mcp.list_tools()}
    ButlerDaemon._build_mcp_http_app(
        mcp,
        butler_name="health",
        identity_provider=lambda: {"accepting_routes": False},
    )
    assert {tool.name for tool in await mcp.list_tools()} == before
