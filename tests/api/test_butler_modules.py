"""Tests for GET /api/butlers/{name}/modules — module list with health status.

Verifies module discovery from butler config, live health probing via MCP
status() tool, and graceful fallback to status='unknown' when a butler is
unreachable.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.deps import ButlerConnectionInfo, ButlerUnreachableError, MCPClientManager
from butlers.api.models import ModuleStatus
from butlers.api.routers.butlers import _get_module_health_via_mcp

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_roster_with_modules(
    tmp_path: Path,
    name: str = "general",
    port: int = 40101,
    modules: dict[str, str] | None = None,
) -> Path:
    """Create a butler directory with modules in config.

    Parameters
    ----------
    modules:
        Mapping of module name to TOML config body (or empty string).
    """
    if modules is None:
        modules = {"telegram": 'mode = "polling"', "email": ""}

    butler_dir = tmp_path / name
    butler_dir.mkdir(parents=True, exist_ok=True)

    mod_sections = ""
    for mod_name, mod_body in modules.items():
        mod_sections += f"[modules.{mod_name}]\n"
        if mod_body:
            mod_sections += f"{mod_body}\n"

    (butler_dir / "butler.toml").write_text(
        f'[butler]\nname = "{name}"\nport = {port}\n'
        f'description = "Test butler"\n'
        f'[butler.db]\nname = "butler_{name}"\n'
        f"[runtime]\n"
        f'type = "claude-code"\n'
        f"{mod_sections}"
    )
    return butler_dir


def _make_roster_no_modules(tmp_path: Path, name: str = "bare", port: int = 40102) -> Path:
    """Create a butler directory with no modules."""
    butler_dir = tmp_path / name
    butler_dir.mkdir(parents=True, exist_ok=True)
    (butler_dir / "butler.toml").write_text(
        f'[butler]\nname = "{name}"\nport = {port}\n'
        f'description = "Bare butler"\n'
        f'[butler.db]\nname = "butler_{name}"\n'
        f"[runtime]\n"
        f'type = "claude-code"\n'
    )
    return butler_dir


def _mock_status_result(
    modules: list[str] | dict[str, dict[str, str]],
    health: str = "ok",
    *,
    canonicalize_module_list: bool = True,
) -> MagicMock:
    """Create a mock CallToolResult from the status() MCP tool."""
    modules_payload: list[str] | dict[str, dict[str, str]]
    if canonicalize_module_list and isinstance(modules, list):
        modules_payload = {name: {"status": "active"} for name in modules}
    else:
        modules_payload = modules

    data = {
        "name": "test",
        "description": "Test butler",
        "port": 40101,
        "modules": modules_payload,
        "health": health,
        "uptime_seconds": 123.4,
    }
    content_block = MagicMock()
    content_block.text = json.dumps(data)
    result = MagicMock()
    result.content = [content_block]
    result.is_error = False
    return result


def _mock_mcp_manager_with_status(
    status_result: MagicMock | None = None,
    *,
    unreachable: bool = False,
) -> MCPClientManager:
    """Create a mock MCPClientManager that returns a status() tool result."""
    mgr = MagicMock(spec=MCPClientManager)
    if unreachable:
        mgr.get_client = AsyncMock(
            side_effect=ButlerUnreachableError("test", cause=ConnectionRefusedError("refused"))
        )
    else:
        mock_client = MagicMock()
        mock_client.ping = AsyncMock(return_value=True)
        mock_client.call_tool = AsyncMock(return_value=status_result)
        mgr.get_client = AsyncMock(return_value=mock_client)
    return mgr


def _create_test_app(
    tmp_path: Path,
    configs: list[ButlerConnectionInfo],
    mcp_manager: MCPClientManager,
):
    """Create a FastAPI test app with dependency overrides."""
    from butlers.api.deps import get_butler_configs, get_mcp_manager
    from butlers.api.routers.butlers import _get_roster_dir

    app = create_app()
    app.dependency_overrides[get_butler_configs] = lambda: configs
    app.dependency_overrides[get_mcp_manager] = lambda: mcp_manager
    app.dependency_overrides[_get_roster_dir] = lambda: tmp_path
    return app


# ---------------------------------------------------------------------------
# _get_module_health_via_mcp unit tests
# ---------------------------------------------------------------------------


class TestGetModuleHealthViaMCP:
    async def test_returns_connected_when_healthy(self):
        """Modules present in status response with health='ok' are 'connected'."""
        result = _mock_status_result(["telegram", "email"], health="ok")
        mgr = _mock_mcp_manager_with_status(result)

        modules = await _get_module_health_via_mcp("test", mgr, ["telegram", "email"])

        assert len(modules) == 2
        for m in modules:
            assert m.status == "connected"
            assert m.enabled is True
            assert m.error is None

    async def test_returns_degraded_when_butler_degraded(self):
        """Modules report 'degraded' when butler health is 'degraded'."""
        result = _mock_status_result(["telegram"], health="degraded")
        mgr = _mock_mcp_manager_with_status(result)

        modules = await _get_module_health_via_mcp("test", mgr, ["telegram"])

        assert len(modules) == 1
        assert modules[0].status == "degraded"
        assert modules[0].name == "telegram"

    async def test_returns_error_for_missing_module(self):
        """Module configured but not in live response gets status='error'."""
        result = _mock_status_result(["telegram"], health="ok")
        mgr = _mock_mcp_manager_with_status(result)

        modules = await _get_module_health_via_mcp("test", mgr, ["telegram", "email"])

        by_name = {m.name: m for m in modules}
        assert by_name["telegram"].status == "connected"
        assert by_name["email"].status == "error"
        assert by_name["email"].error is not None

    async def test_returns_unknown_when_unreachable(self):
        """All modules get status='unknown' when butler is unreachable."""
        mgr = _mock_mcp_manager_with_status(unreachable=True)

        modules = await _get_module_health_via_mcp("test", mgr, ["telegram", "email"])

        assert len(modules) == 2
        for m in modules:
            assert m.status == "unknown"
            assert m.enabled is True

    async def test_returns_unknown_on_unexpected_error(self):
        """All modules get status='unknown' on unexpected exceptions."""
        mgr = MagicMock(spec=MCPClientManager)
        mgr.get_client = AsyncMock(side_effect=RuntimeError("something broke"))

        modules = await _get_module_health_via_mcp("test", mgr, ["telegram"])

        assert len(modules) == 1
        assert modules[0].status == "unknown"

    async def test_legacy_list_payload_no_longer_marks_modules_active(self):
        """Legacy list payloads should not be interpreted as active module states."""
        result = _mock_status_result(
            ["telegram", "email"],
            health="ok",
            canonicalize_module_list=False,
        )
        mgr = _mock_mcp_manager_with_status(result)

        modules = await _get_module_health_via_mcp("test", mgr, ["telegram", "email"])

        assert len(modules) == 2
        for module in modules:
            assert module.status == "error"
            assert module.error == "Module configured but not loaded by butler"


# ---------------------------------------------------------------------------
# GET /api/butlers/{name}/modules endpoint tests (ASGI transport)
# ---------------------------------------------------------------------------


class TestGetButlerModulesEndpoint:
    async def test_returns_404_for_unknown_butler(self, tmp_path: Path):
        """Unknown butler name returns 404."""
        configs = [ButlerConnectionInfo("general", 40101)]
        mgr = _mock_mcp_manager_with_status(unreachable=True)
        app = _create_test_app(tmp_path, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/nonexistent/modules")

        assert response.status_code == 404

    async def test_returns_404_when_config_not_found(self, tmp_path: Path):
        """Returns 404 when butler is in configs but has no butler.toml."""
        configs = [ButlerConnectionInfo("phantom", 8999)]
        mgr = _mock_mcp_manager_with_status(unreachable=True)
        app = _create_test_app(tmp_path, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/phantom/modules")

        assert response.status_code == 404

    async def test_returns_module_list_from_config(self, tmp_path: Path):
        """Returns modules defined in butler.toml with live health status."""
        _make_roster_with_modules(tmp_path, "general", 40101)
        configs = [ButlerConnectionInfo("general", 40101)]
        status_result = _mock_status_result(["telegram", "email"], health="ok")
        mgr = _mock_mcp_manager_with_status(status_result)
        app = _create_test_app(tmp_path, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/general/modules")

        assert response.status_code == 200
        body = response.json()
        assert "data" in body
        assert "meta" in body

        modules = body["data"]
        assert len(modules) == 2
        names = {m["name"] for m in modules}
        assert names == {"telegram", "email"}

        for m in modules:
            assert m["enabled"] is True
            assert m["status"] == "connected"

    async def test_handles_unreachable_butler(self, tmp_path: Path):
        """Returns modules with status='unknown' when butler is unreachable."""
        _make_roster_with_modules(tmp_path, "general", 40101)
        configs = [ButlerConnectionInfo("general", 40101)]
        mgr = _mock_mcp_manager_with_status(unreachable=True)
        app = _create_test_app(tmp_path, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/general/modules")

        assert response.status_code == 200
        modules = response.json()["data"]
        assert len(modules) == 2
        for m in modules:
            assert m["status"] == "unknown"
            assert m["enabled"] is True

    async def test_returns_live_module_health(self, tmp_path: Path):
        """Returns live module health when butler is reachable."""
        _make_roster_with_modules(
            tmp_path, "general", 40101, modules={"telegram": "", "email": "", "calendar": ""}
        )
        configs = [ButlerConnectionInfo("general", 40101)]
        # Only telegram and email are loaded — calendar is missing
        status_result = _mock_status_result(["telegram", "email"], health="ok")
        mgr = _mock_mcp_manager_with_status(status_result)
        app = _create_test_app(tmp_path, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/general/modules")

        assert response.status_code == 200
        modules = response.json()["data"]
        by_name = {m["name"]: m for m in modules}

        assert by_name["telegram"]["status"] == "connected"
        assert by_name["email"]["status"] == "connected"
        assert by_name["calendar"]["status"] == "error"
        assert by_name["calendar"]["error"] is not None

    async def test_empty_modules_list(self, tmp_path: Path):
        """Returns empty list when butler has no modules configured."""
        _make_roster_no_modules(tmp_path, "bare", 40102)
        configs = [ButlerConnectionInfo("bare", 40102)]
        mgr = _mock_mcp_manager_with_status(unreachable=True)
        app = _create_test_app(tmp_path, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/bare/modules")

        assert response.status_code == 200
        body = response.json()
        assert body["data"] == []

    async def test_response_shape_matches_model(self, tmp_path: Path):
        """Verify response data can be parsed as ModuleStatus."""
        _make_roster_with_modules(tmp_path, "general", 40101)
        configs = [ButlerConnectionInfo("general", 40101)]
        status_result = _mock_status_result(["telegram", "email"], health="ok")
        mgr = _mock_mcp_manager_with_status(status_result)
        app = _create_test_app(tmp_path, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/general/modules")

        assert response.status_code == 200
        body = response.json()

        for item in body["data"]:
            mod = ModuleStatus.model_validate(item)
            assert mod.enabled is True
            assert mod.status in {"connected", "degraded", "error", "unknown"}

    async def test_degraded_butler_shows_degraded_modules(self, tmp_path: Path):
        """Modules show 'degraded' when butler health is 'degraded'."""
        _make_roster_with_modules(tmp_path, "general", 40101)
        configs = [ButlerConnectionInfo("general", 40101)]
        status_result = _mock_status_result(["telegram", "email"], health="degraded")
        mgr = _mock_mcp_manager_with_status(status_result)
        app = _create_test_app(tmp_path, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/general/modules")

        assert response.status_code == 200
        modules = response.json()["data"]
        for m in modules:
            assert m["status"] == "degraded"
