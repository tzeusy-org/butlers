"""Integration smoke tests for the Home butler auto-discovery.

Acceptance criteria (butlers-kxbo.10):
1. HomeAssistantModule appears in default_registry().available_modules
   -> Already tested in tests/modules/test_module_home_assistant.py
      (TestModuleRegistryIntegration::test_default_registry_includes_home_assistant)
      This file adds a consolidated reference test that also confirms coverage.

2. roster/home/butler.toml is discovered and parsed by the daemon config loader
   -> Tested here: TestHomeButlerConfigDiscovery

3. Dashboard route auto-discovery: roster/home/api/router.py registered at /api/home
   -> Already tested in tests/api/test_home.py
      (TestHomeRouterDiscovery::test_home_router_is_discovered)
      This file adds a consolidated reference test.

4. E2E smoke test against real/mocked HA instance
   -> Tested here: TestHomeAssistantE2ESmoke (marked @pytest.mark.e2e, skipped in CI)

Issue: butlers-kxbo.10
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# AC1: Module auto-discovery — HomeAssistantModule in default_registry
# ---------------------------------------------------------------------------


class TestHomeAssistantModuleAutoDiscovery:
    """HomeAssistantModule must appear in the default module registry."""

    def test_home_assistant_in_default_registry(self) -> None:
        """default_registry() discovers and registers HomeAssistantModule."""
        from butlers.modules.registry import default_registry

        reg = default_registry()
        assert "home_assistant" in reg.available_modules, (
            "HomeAssistantModule was not auto-discovered by default_registry(). "
            "Check that butlers/modules/home_assistant/__init__.py exports HomeAssistantModule "
            "as a concrete (non-abstract) Module subclass."
        )

    def test_home_assistant_module_name_property(self) -> None:
        """HomeAssistantModule.name returns 'home_assistant'."""
        from butlers.modules.home_assistant import HomeAssistantModule

        module = HomeAssistantModule()
        assert module.name == "home_assistant"

    def test_home_assistant_in_available_modules_list(self) -> None:
        """HomeAssistantModule name appears in sorted available_modules list."""
        from butlers.modules.registry import default_registry

        reg = default_registry()
        available = reg.available_modules
        assert isinstance(available, list)
        assert "home_assistant" in available
        # Verify list is sorted (contract of available_modules)
        assert available == sorted(available)


# ---------------------------------------------------------------------------
# AC2: Butler config discovery — roster/home/butler.toml loads without errors
# ---------------------------------------------------------------------------


class TestHomeButlerConfigDiscovery:
    """roster/home/butler.toml must be parseable by the daemon config loader."""

    def test_home_butler_toml_loads_without_error(self) -> None:
        """load_config() succeeds for roster/home/butler.toml."""
        from butlers.config import load_config

        repo_root = Path(__file__).resolve().parent.parent
        home_config_dir = repo_root / "roster" / "home"
        assert home_config_dir.is_dir(), f"roster/home/ directory not found at {home_config_dir}"

        cfg = load_config(home_config_dir)
        assert cfg.name == "home"

    def test_home_butler_config_has_expected_port(self) -> None:
        """Home butler is configured on port 40108."""
        from butlers.config import load_config

        repo_root = Path(__file__).resolve().parent.parent
        home_config_dir = repo_root / "roster" / "home"

        cfg = load_config(home_config_dir)
        assert cfg.port == 40108

    def test_home_butler_config_has_home_assistant_module(self) -> None:
        """butler.toml must declare the home_assistant module."""
        from butlers.config import load_config

        repo_root = Path(__file__).resolve().parent.parent
        home_config_dir = repo_root / "roster" / "home"

        cfg = load_config(home_config_dir)
        assert "home_assistant" in cfg.modules, (
            "home_assistant module is missing from [modules] section in roster/home/butler.toml"
        )

    def test_home_butler_config_has_runtime_type(self) -> None:
        """Home butler runtime type is present."""
        from butlers.config import load_config

        repo_root = Path(__file__).resolve().parent.parent
        home_config_dir = repo_root / "roster" / "home"

        cfg = load_config(home_config_dir)
        assert cfg.runtime is not None
        assert cfg.runtime.type is not None

    def test_home_butler_config_has_concurrent_sessions(self) -> None:
        """Home butler is configured for at least 3 concurrent sessions."""
        from butlers.config import load_config

        repo_root = Path(__file__).resolve().parent.parent
        home_config_dir = repo_root / "roster" / "home"

        cfg = load_config(home_config_dir)
        assert cfg.runtime.max_concurrent_sessions >= 3

    def test_home_butler_in_list_butlers(self) -> None:
        """list_butlers() discovers the home butler from the real roster."""
        from butlers.config import list_butlers

        repo_root = Path(__file__).resolve().parent.parent
        roster_dir = repo_root / "roster"

        all_configs = list_butlers(roster_dir)
        butler_names = [cfg.name for cfg in all_configs]
        assert "home" in butler_names, (
            f"Home butler not found in list_butlers(). Discovered butlers: {butler_names}"
        )

    def test_home_butler_config_has_scheduled_tasks(self) -> None:
        """Home butler.toml declares at least one scheduled task."""
        from butlers.config import load_config

        repo_root = Path(__file__).resolve().parent.parent
        home_config_dir = repo_root / "roster" / "home"

        cfg = load_config(home_config_dir)
        assert len(cfg.schedules) >= 1, "roster/home/butler.toml has no [[butler.schedule]] entries"

    def test_home_butler_config_has_db_schema(self) -> None:
        """Home butler configures the 'home' database schema."""
        from butlers.config import load_config

        repo_root = Path(__file__).resolve().parent.parent
        home_config_dir = repo_root / "roster" / "home"

        cfg = load_config(home_config_dir)
        assert cfg.db_schema == "home"


# ---------------------------------------------------------------------------
# AC3: Dashboard route auto-discovery — /api/home registered
# ---------------------------------------------------------------------------


class TestHomeRouterAutoDiscovery:
    """Home butler dashboard routes must be discoverable via router_discovery.

    Note: Full coverage already exists in tests/api/test_home.py
    (TestHomeRouterDiscovery). This class adds a consolidated cross-check.
    """

    def test_home_router_discovered_at_api_home_prefix(self) -> None:
        """router_discovery finds home butler and mounts it at /api/home."""
        from fastapi import APIRouter

        from butlers.api.router_discovery import discover_butler_routers

        routers = discover_butler_routers()
        butler_names = [name for name, _ in routers]
        assert "home" in butler_names, f"Home butler router not discovered. Found: {butler_names}"

        home_module = next(m for n, m in routers if n == "home")
        assert hasattr(home_module, "router")
        assert isinstance(home_module.router, APIRouter)
        assert home_module.router.prefix == "/api/home"

    def test_home_router_routes_are_non_empty(self) -> None:
        """Home router must define at least one route."""
        from butlers.api.router_discovery import discover_butler_routers

        routers = discover_butler_routers()
        home_module = next((m for n, m in routers if n == "home"), None)
        assert home_module is not None
        assert len(home_module.router.routes) > 0

    def test_home_router_wired_in_create_app(self) -> None:
        """create_app() includes the home butler router in app.state.butler_routers."""
        from butlers.api.app import create_app

        app = create_app()
        butler_names = [name for name, _ in app.state.butler_routers]
        assert "home" in butler_names, (
            f"Home butler not found in app.state.butler_routers. Found: {butler_names}"
        )


# ---------------------------------------------------------------------------
# AC4: E2E smoke test against real or mocked HA instance
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestHomeAssistantE2ESmoke:
    """E2E smoke tests for the Home Assistant integration.

    These tests are skipped by default (requires a live HA instance).
    Run with: pytest -m e2e tests/test_home_butler_smoke.py

    To run against a mocked HA for local development verification, set:
        HA_URL=http://localhost:8123
        HA_TOKEN=<long-lived access token>
    """

    @pytest.fixture
    def ha_url(self) -> str:
        """HA base URL from environment."""
        import os

        url = os.environ.get("HA_URL", "")
        if not url:
            pytest.skip("HA_URL not set — skipping live HA test")
        return url

    @pytest.fixture
    def ha_token(self) -> str:
        """HA long-lived access token from environment."""
        import os

        token = os.environ.get("HA_TOKEN", "")
        if not token:
            pytest.skip("HA_TOKEN not set — skipping live HA test")
        return token

    async def test_websocket_connection_to_ha(self, ha_url: str, ha_token: str) -> None:
        """Start home butler, verify WebSocket connects to HA and auth succeeds."""
        from butlers.modules.home_assistant import HomeAssistantConfig, HomeAssistantModule

        module = HomeAssistantModule()
        module._config = HomeAssistantConfig(url=ha_url)
        module._token = ha_token

        await module._ws_connect()

        try:
            assert module._ws_connected is True, "WebSocket did not authenticate successfully"
        finally:
            await module.on_shutdown()

    async def test_query_entity_state_via_rest(self, ha_url: str, ha_token: str) -> None:
        """Query a well-known entity state via REST and verify structure."""
        import httpx

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{ha_url.rstrip('/')}/api/states",
                headers={"Authorization": f"Bearer {ha_token}"},
                timeout=10.0,
            )

        assert resp.status_code == 200
        entities = resp.json()
        assert isinstance(entities, list), "Expected list of entity states from /api/states"
        assert len(entities) > 0, "Expected at least one entity in HA"

        # Verify entity structure
        first = entities[0]
        assert "entity_id" in first
        assert "state" in first
        assert "attributes" in first

    async def test_ha_call_service_creates_command_log_entry(
        self, ha_url: str, ha_token: str
    ) -> None:
        """Calling ha_call_service creates a command log entry in the DB.

        This is a structural smoke test — it verifies that the command log
        write path works when a service call is made. It does NOT verify
        the service was actually executed on HA (that depends on device
        availability in the real home).
        """
        from unittest.mock import AsyncMock, MagicMock, patch

        from butlers.modules.home_assistant import HomeAssistantConfig, HomeAssistantModule

        module = HomeAssistantModule()
        module._config = HomeAssistantConfig(url=ha_url)
        module._token = ha_token

        # Mock the DB pool to capture command log inserts
        mock_pool = AsyncMock()
        mock_pool.execute = AsyncMock()

        mock_mcp = MagicMock()
        tools: dict = {}

        def tool_decorator(*args, **kwargs):
            def decorator(fn):
                tools[fn.__name__] = fn
                return fn

            return decorator

        mock_mcp.tool = tool_decorator

        await module.register_tools(mock_mcp, {}, mock_pool)

        # ha_call_service must be registered
        assert "ha_call_service" in tools, (
            "ha_call_service tool was not registered by HomeAssistantModule.register_tools()"
        )

        # Call the service (mocked — does not hit real HA)
        with patch.object(module, "_http_client") as mock_http:
            mock_response = AsyncMock()
            mock_response.raise_for_status = AsyncMock()
            mock_response.json = AsyncMock(return_value=[{"context": {"id": "test_context_id"}}])
            mock_http.post = AsyncMock(return_value=mock_response)

            await tools["ha_call_service"](
                domain="light",
                service="turn_off",
                target={"entity_id": "light.test"},
                service_data={},
            )

        # Verify command log write was attempted
        mock_pool.execute.assert_called_once()
        call_args = mock_pool.execute.call_args[0]
        assert "ha_command_log" in call_args[0], (
            "Expected INSERT into ha_command_log in command log execute call"
        )
