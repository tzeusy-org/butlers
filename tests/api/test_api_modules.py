"""Tests for module state management API endpoints.

Tests for:
- GET /api/butlers/{name}/module-states
- PUT /api/butlers/{name}/module-states/{module_name}/enabled

These endpoints expose the richer ModuleRuntimeState data from the daemon
via the module.states and module.set_enabled MCP tools added in butlers-949.2.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.deps import ButlerConnectionInfo, ButlerUnreachableError, MCPClientManager
from butlers.api.models.modules import ModuleRuntimeStateResponse
from butlers.api.routers.modules import _get_module_states_via_mcp, _has_module_config

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


def _make_butler_dir(
    tmp_path: Path,
    name: str = "general",
    port: int = 40101,
    modules: dict[str, str] | None = None,
) -> Path:
    """Create a butler directory with optional modules in butler.toml."""
    if modules is None:
        modules = {"telegram": "", "email": ""}

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


def _make_module_states_result(
    states: dict[str, dict],
) -> MagicMock:
    """Create a mock CallToolResult for the module.states MCP tool."""
    content_block = MagicMock()
    content_block.text = json.dumps(states)
    result = MagicMock()
    result.content = [content_block]
    result.is_error = False
    return result


def _make_set_enabled_result(status: str, *, error: str | None = None) -> MagicMock:
    """Create a mock CallToolResult for the module.set_enabled MCP tool."""
    data: dict = {"status": status}
    if error:
        data["error"] = error
    content_block = MagicMock()
    content_block.text = json.dumps(data)
    result = MagicMock()
    result.content = [content_block]
    result.is_error = False
    return result


def _mock_mcp_manager(
    *,
    states_result: MagicMock | None = None,
    set_enabled_result: MagicMock | None = None,
    unreachable: bool = False,
) -> MCPClientManager:
    """Create a mock MCPClientManager for module state tests."""
    mgr = MagicMock(spec=MCPClientManager)

    if unreachable:
        mgr.get_client = AsyncMock(
            side_effect=ButlerUnreachableError("test", cause=ConnectionRefusedError("refused"))
        )
        return mgr

    mock_client = MagicMock()
    mock_client.ping = AsyncMock(return_value=True)

    async def _call_tool(tool_name: str, args: dict) -> MagicMock:
        if tool_name == "module.states" and states_result is not None:
            return states_result
        if tool_name == "module.set_enabled" and set_enabled_result is not None:
            return set_enabled_result
        raise RuntimeError(f"Unexpected tool call: {tool_name}")

    mock_client.call_tool = AsyncMock(side_effect=_call_tool)
    mgr.get_client = AsyncMock(return_value=mock_client)
    return mgr


def _create_test_app(
    tmp_path: Path,
    configs: list[ButlerConnectionInfo],
    mcp_manager: MCPClientManager,
):
    """Create a FastAPI test app with dependency overrides for module endpoints."""
    from butlers.api.deps import get_butler_configs, get_mcp_manager
    from butlers.api.routers.modules import _get_roster_dir

    app = create_app()
    app.dependency_overrides[get_butler_configs] = lambda: configs
    app.dependency_overrides[get_mcp_manager] = lambda: mcp_manager
    app.dependency_overrides[_get_roster_dir] = lambda: tmp_path
    return app


# ---------------------------------------------------------------------------
# _has_module_config unit tests
# ---------------------------------------------------------------------------


class TestHasModuleConfig:
    def test_returns_true_when_module_in_toml(self, tmp_path: Path):
        """Returns True when butler.toml has the module section."""
        butler_dir = _make_butler_dir(tmp_path, modules={"telegram": ""})
        assert _has_module_config(butler_dir, "telegram") is True

    def test_returns_false_for_unconfigured_module(self, tmp_path: Path):
        """Returns False when module is not in butler.toml."""
        butler_dir = _make_butler_dir(tmp_path, modules={"telegram": ""})
        assert _has_module_config(butler_dir, "email") is False

    def test_returns_false_when_no_toml(self, tmp_path: Path):
        """Returns False when butler.toml does not exist."""
        butler_dir = tmp_path / "noconfig"
        butler_dir.mkdir()
        assert _has_module_config(butler_dir, "telegram") is False

    def test_returns_false_for_invalid_toml(self, tmp_path: Path):
        """Returns False when butler.toml is malformed."""
        butler_dir = tmp_path / "bad"
        butler_dir.mkdir()
        (butler_dir / "butler.toml").write_text("[[[ invalid toml")
        assert _has_module_config(butler_dir, "telegram") is False


# ---------------------------------------------------------------------------
# _get_module_states_via_mcp unit tests
# ---------------------------------------------------------------------------


class TestGetModuleStatesViaMCP:
    async def test_returns_parsed_states(self, tmp_path: Path):
        """Parses module.states MCP tool response correctly."""
        butler_dir = _make_butler_dir(tmp_path, modules={"telegram": "", "email": ""})
        raw_states = {
            "telegram": {
                "health": "active",
                "enabled": True,
                "failure_phase": None,
                "failure_error": None,
            },
            "email": {
                "health": "active",
                "enabled": False,
                "failure_phase": None,
                "failure_error": None,
            },
        }
        states_result = _make_module_states_result(raw_states)
        mgr = _mock_mcp_manager(states_result=states_result)

        states = await _get_module_states_via_mcp("general", mgr, butler_dir)

        assert len(states) == 2
        by_name = {s.name: s for s in states}
        assert by_name["telegram"].health == "active"
        assert by_name["telegram"].enabled is True
        assert by_name["telegram"].has_config is True
        assert by_name["email"].enabled is False
        assert by_name["email"].has_config is True

    async def test_has_config_false_for_unconfigured_module(self, tmp_path: Path):
        """Modules not in butler.toml have has_config=False."""
        # Only telegram is configured; runtime shows both
        butler_dir = _make_butler_dir(tmp_path, modules={"telegram": ""})
        raw_states = {
            "telegram": {
                "health": "active",
                "enabled": True,
                "failure_phase": None,
                "failure_error": None,
            },
            "calendar": {
                "health": "active",
                "enabled": True,
                "failure_phase": None,
                "failure_error": None,
            },
        }
        states_result = _make_module_states_result(raw_states)
        mgr = _mock_mcp_manager(states_result=states_result)

        states = await _get_module_states_via_mcp("general", mgr, butler_dir)
        by_name = {s.name: s for s in states}

        assert by_name["telegram"].has_config is True
        assert by_name["calendar"].has_config is False

    async def test_returns_failed_module_state(self, tmp_path: Path):
        """Failed modules are returned with health=failed and failure details."""
        butler_dir = _make_butler_dir(tmp_path, modules={"email": ""})
        raw_states = {
            "email": {
                "health": "failed",
                "enabled": False,
                "failure_phase": "on_startup",
                "failure_error": "SMTP connection refused",
            },
        }
        states_result = _make_module_states_result(raw_states)
        mgr = _mock_mcp_manager(states_result=states_result)

        states = await _get_module_states_via_mcp("general", mgr, butler_dir)

        assert len(states) == 1
        assert states[0].health == "failed"
        assert states[0].enabled is False
        assert states[0].failure_phase == "on_startup"
        assert states[0].failure_error == "SMTP connection refused"

    async def test_raises_503_when_unreachable(self, tmp_path: Path):
        """Raises HTTPException(503) when butler is unreachable."""
        from fastapi import HTTPException

        butler_dir = _make_butler_dir(tmp_path)
        mgr = _mock_mcp_manager(unreachable=True)

        with pytest.raises(HTTPException) as exc_info:
            await _get_module_states_via_mcp("general", mgr, butler_dir)

        assert exc_info.value.status_code == 503

    async def test_returns_empty_for_empty_states(self, tmp_path: Path):
        """Returns empty list when daemon returns no module states."""
        butler_dir = _make_butler_dir(tmp_path, modules={})
        states_result = _make_module_states_result({})
        mgr = _mock_mcp_manager(states_result=states_result)

        states = await _get_module_states_via_mcp("general", mgr, butler_dir)
        assert states == []


# ---------------------------------------------------------------------------
# GET /api/butlers/{name}/module-states endpoint tests
# ---------------------------------------------------------------------------


class TestGetModuleStatesEndpoint:
    async def test_returns_404_for_unknown_butler(self, tmp_path: Path):
        """Unknown butler returns 404."""
        configs = [ButlerConnectionInfo("general", 40101)]
        mgr = _mock_mcp_manager(unreachable=True)
        app = _create_test_app(tmp_path, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/nonexistent/module-states")

        assert response.status_code == 404

    async def test_returns_503_when_butler_unreachable(self, tmp_path: Path):
        """Returns 503 when butler daemon is unreachable."""
        _make_butler_dir(tmp_path, "general", 40101)
        configs = [ButlerConnectionInfo("general", 40101)]
        mgr = _mock_mcp_manager(unreachable=True)
        app = _create_test_app(tmp_path, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/general/module-states")

        assert response.status_code == 503

    async def test_returns_module_states_with_correct_schema(self, tmp_path: Path):
        """GET returns all modules with correct fields and values."""
        _make_butler_dir(tmp_path, "general", 40101, modules={"telegram": "", "email": ""})
        configs = [ButlerConnectionInfo("general", 40101)]
        raw_states = {
            "telegram": {
                "health": "active",
                "enabled": True,
                "failure_phase": None,
                "failure_error": None,
            },
            "email": {
                "health": "active",
                "enabled": False,
                "failure_phase": None,
                "failure_error": None,
            },
        }
        states_result = _make_module_states_result(raw_states)
        mgr = _mock_mcp_manager(states_result=states_result)
        app = _create_test_app(tmp_path, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/general/module-states")

        assert response.status_code == 200
        body = response.json()
        assert "data" in body
        assert "meta" in body

        modules = body["data"]
        assert len(modules) == 2
        by_name = {m["name"]: m for m in modules}

        assert by_name["telegram"]["health"] == "active"
        assert by_name["telegram"]["enabled"] is True
        assert by_name["telegram"]["has_config"] is True
        assert by_name["telegram"]["failure_phase"] is None
        assert by_name["telegram"]["failure_error"] is None

        assert by_name["email"]["enabled"] is False
        assert by_name["email"]["has_config"] is True

    async def test_response_validates_as_pydantic_model(self, tmp_path: Path):
        """Response items can be parsed as ModuleRuntimeStateResponse."""
        _make_butler_dir(tmp_path, "general", 40101)
        configs = [ButlerConnectionInfo("general", 40101)]
        raw_states = {
            "telegram": {
                "health": "active",
                "enabled": True,
                "failure_phase": None,
                "failure_error": None,
            },
        }
        states_result = _make_module_states_result(raw_states)
        mgr = _mock_mcp_manager(states_result=states_result)
        app = _create_test_app(tmp_path, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/general/module-states")

        body = response.json()
        for item in body["data"]:
            parsed = ModuleRuntimeStateResponse.model_validate(item)
            assert parsed.health in {"active", "failed", "cascade_failed"}

    async def test_returns_failed_module_info(self, tmp_path: Path):
        """Failed modules are returned with health=failed and failure details."""
        _make_butler_dir(tmp_path, "general", 40101, modules={"email": ""})
        configs = [ButlerConnectionInfo("general", 40101)]
        raw_states = {
            "email": {
                "health": "failed",
                "enabled": False,
                "failure_phase": "on_startup",
                "failure_error": "Connection refused",
            },
        }
        states_result = _make_module_states_result(raw_states)
        mgr = _mock_mcp_manager(states_result=states_result)
        app = _create_test_app(tmp_path, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/butlers/general/module-states")

        assert response.status_code == 200
        modules = response.json()["data"]
        assert len(modules) == 1
        m = modules[0]
        assert m["health"] == "failed"
        assert m["enabled"] is False
        assert m["failure_phase"] == "on_startup"
        assert m["failure_error"] == "Connection refused"


# ---------------------------------------------------------------------------
# PUT /api/butlers/{name}/module-states/{module_name}/enabled tests
# ---------------------------------------------------------------------------


class TestSetModuleEnabledEndpoint:
    async def test_returns_404_for_unknown_butler(self, tmp_path: Path):
        """Unknown butler returns 404."""
        configs = [ButlerConnectionInfo("general", 40101)]
        mgr = _mock_mcp_manager()
        app = _create_test_app(tmp_path, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.put(
                "/api/butlers/nonexistent/module-states/telegram/enabled",
                json={"enabled": True},
            )

        assert response.status_code == 404

    async def test_returns_503_when_butler_unreachable(self, tmp_path: Path):
        """Returns 503 when butler daemon is unreachable."""
        _make_butler_dir(tmp_path, "general", 40101)
        configs = [ButlerConnectionInfo("general", 40101)]
        mgr = _mock_mcp_manager(unreachable=True)
        app = _create_test_app(tmp_path, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.put(
                "/api/butlers/general/module-states/telegram/enabled",
                json={"enabled": True},
            )

        assert response.status_code == 503

    async def test_returns_404_for_unknown_module(self, tmp_path: Path):
        """Returns 404 when daemon reports module is unknown."""
        _make_butler_dir(tmp_path, "general", 40101)
        configs = [ButlerConnectionInfo("general", 40101)]
        set_result = _make_set_enabled_result("error", error="Unknown module: 'nonexistent'")
        mgr = _mock_mcp_manager(set_enabled_result=set_result)
        app = _create_test_app(tmp_path, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.put(
                "/api/butlers/general/module-states/nonexistent/enabled",
                json={"enabled": True},
            )

        assert response.status_code == 404

    async def test_returns_409_for_unavailable_module(self, tmp_path: Path):
        """Returns 409 when daemon reports module is unavailable (failed health)."""
        _make_butler_dir(tmp_path, "general", 40101, modules={"email": ""})
        configs = [ButlerConnectionInfo("general", 40101)]
        set_result = _make_set_enabled_result(
            "error",
            error="Module 'email' is unavailable (health='failed') and cannot be toggled",
        )
        mgr = _mock_mcp_manager(set_enabled_result=set_result)
        app = _create_test_app(tmp_path, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.put(
                "/api/butlers/general/module-states/email/enabled",
                json={"enabled": True},
            )

        assert response.status_code == 409

    async def test_enable_module_returns_updated_state(self, tmp_path: Path):
        """Successful enable returns the updated module state with enabled=True."""
        _make_butler_dir(tmp_path, "general", 40101, modules={"telegram": ""})
        configs = [ButlerConnectionInfo("general", 40101)]
        set_result = _make_set_enabled_result("ok")
        mgr = _mock_mcp_manager(set_enabled_result=set_result)
        app = _create_test_app(tmp_path, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.put(
                "/api/butlers/general/module-states/telegram/enabled",
                json={"enabled": True},
            )

        assert response.status_code == 200
        body = response.json()
        assert "data" in body
        data = body["data"]
        assert data["name"] == "telegram"
        assert data["enabled"] is True
        assert data["health"] == "active"
        assert data["has_config"] is True

    async def test_disable_module_returns_updated_state(self, tmp_path: Path):
        """Successful disable returns the updated module state with enabled=False."""
        _make_butler_dir(tmp_path, "general", 40101, modules={"telegram": ""})
        configs = [ButlerConnectionInfo("general", 40101)]
        set_result = _make_set_enabled_result("ok")
        mgr = _mock_mcp_manager(set_enabled_result=set_result)
        app = _create_test_app(tmp_path, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.put(
                "/api/butlers/general/module-states/telegram/enabled",
                json={"enabled": False},
            )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["enabled"] is False
        assert data["name"] == "telegram"

    async def test_response_validates_as_pydantic_model(self, tmp_path: Path):
        """Response data can be parsed as ModuleRuntimeStateResponse."""
        _make_butler_dir(tmp_path, "general", 40101, modules={"telegram": ""})
        configs = [ButlerConnectionInfo("general", 40101)]
        set_result = _make_set_enabled_result("ok")
        mgr = _mock_mcp_manager(set_enabled_result=set_result)
        app = _create_test_app(tmp_path, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.put(
                "/api/butlers/general/module-states/telegram/enabled",
                json={"enabled": True},
            )

        assert response.status_code == 200
        parsed = ModuleRuntimeStateResponse.model_validate(response.json()["data"])
        assert parsed.name == "telegram"
        assert parsed.enabled is True

    async def test_has_config_false_for_unconfigured_module(self, tmp_path: Path):
        """has_config is False when module is not in butler.toml."""
        _make_butler_dir(tmp_path, "general", 40101, modules={"telegram": ""})
        configs = [ButlerConnectionInfo("general", 40101)]
        # Runtime allows 'calendar' even though it's not in config
        set_result = _make_set_enabled_result("ok")
        mgr = _mock_mcp_manager(set_enabled_result=set_result)
        app = _create_test_app(tmp_path, configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.put(
                "/api/butlers/general/module-states/calendar/enabled",
                json={"enabled": True},
            )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["has_config"] is False
