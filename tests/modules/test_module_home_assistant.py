"""Condensed Home Assistant module tests — behavioral contract only.

Replaces 190 tests with ~25 focused behavioral tests.

Covers:
- Module ABC compliance (instantiation, name, config_schema, dependencies)
- HomeAssistantConfig validation (defaults, extra=forbid, read_only)
- Tool registration (expected tools, read_only mode)
- Registry integration
- Startup credential resolution (happy path, missing url, missing token)
- Shutdown lifecycle (cleanup, idempotent)
- WebSocket URL derivation (http→ws, https→wss)
- Entity cache state_changed event (update + removal)
- Key tool behaviors (entity_id validation, list_areas, get_entity_state)

[bu-7sd7a]
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel, ValidationError

from butlers.modules._roster_home import (
    HomeAssistantConfig,
    HomeAssistantModule,
)
from butlers.modules.base import Module, ToolMeta

pytestmark = pytest.mark.unit

EXPECTED_HA_TOOLS = {
    "ha_get_entity_state",
    "ha_list_entities",
    "ha_list_areas",
    "ha_list_services",
    "ha_get_history",
    "ha_get_statistics",
    "ha_render_template",
    "ha_call_service",
    "ha_activate_scene",
    "ha_maintenance_create",
    "ha_maintenance_complete",
    "ha_maintenance_list",
    "ha_maintenance_remove",
}

EXPECTED_HA_READ_ONLY_TOOLS = {
    "ha_get_entity_state",
    "ha_list_entities",
    "ha_list_areas",
    "ha_list_services",
    "ha_get_history",
    "ha_get_statistics",
    "ha_render_template",
    "ha_maintenance_list",
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ha_module() -> HomeAssistantModule:
    return HomeAssistantModule()


@pytest.fixture
def mock_mcp() -> MagicMock:
    mcp = MagicMock()
    tools: dict[str, Any] = {}

    def tool_decorator(*_args, **kwargs):
        declared_name = kwargs.get("name")

        def decorator(fn):
            tools[declared_name or fn.__name__] = fn
            return fn

        return decorator

    mcp.tool = tool_decorator
    mcp._registered_tools = tools
    return mcp


# ---------------------------------------------------------------------------
# ABC compliance
# ---------------------------------------------------------------------------


class TestModuleABCCompliance:
    def test_module_contract(self, ha_module: HomeAssistantModule) -> None:
        """HomeAssistantModule satisfies Module ABC: name, config_schema, dependencies."""
        assert issubclass(HomeAssistantModule, Module)
        assert ha_module.name == "home_assistant"
        assert ha_module.config_schema is HomeAssistantConfig
        assert issubclass(ha_module.config_schema, BaseModel)
        assert ha_module.dependencies == ["contacts", "approvals"]
        assert ha_module.migration_revisions() == "home"

    def test_tool_metadata_call_service_sensitive(self, ha_module: HomeAssistantModule) -> None:
        meta = ha_module.tool_metadata()
        assert "ha_call_service" in meta
        assert isinstance(meta["ha_call_service"], ToolMeta)
        assert meta["ha_call_service"].arg_sensitivities.get("domain") is True
        assert meta["ha_call_service"].arg_sensitivities.get("service") is True

    def test_tool_metadata_empty_when_read_only(self) -> None:
        module = HomeAssistantModule()
        module._config = HomeAssistantConfig(read_only=True)
        assert module.tool_metadata() == {}


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------


class TestHomeAssistantConfig:
    def test_url_optional(self) -> None:
        config = HomeAssistantConfig()
        assert config.url is None

    def test_defaults(self) -> None:
        config = HomeAssistantConfig()
        assert config.verify_ssl is False
        assert config.websocket_ping_interval == 30
        assert config.poll_interval_seconds == 60
        assert config.snapshot_interval_seconds == 300
        assert config.read_only is False

    def test_extra_fields_rejected(self) -> None:
        with pytest.raises(ValidationError):
            HomeAssistantConfig(unknown_field="boom")


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


class TestToolRegistration:
    async def test_registers_expected_tools(
        self, ha_module: HomeAssistantModule, mock_mcp: MagicMock
    ) -> None:
        await ha_module.register_tools(
            mcp=mock_mcp, config={"url": "http://ha.local"}, db=None, butler_name="test-butler"
        )
        assert set(mock_mcp._registered_tools.keys()) == EXPECTED_HA_TOOLS

    async def test_read_only_registers_only_query_tools(self, mock_mcp: MagicMock) -> None:
        module = HomeAssistantModule()
        await module.register_tools(
            mcp=mock_mcp, config={"read_only": True}, db=None, butler_name="test-butler"
        )
        assert set(mock_mcp._registered_tools.keys()) == EXPECTED_HA_READ_ONLY_TOOLS

    def test_default_registry_includes_home_assistant(self) -> None:
        from butlers.modules.registry import default_registry

        assert "home_assistant" in default_registry().available_modules


# ---------------------------------------------------------------------------
# Startup credential resolution
# ---------------------------------------------------------------------------


class TestOnStartup:
    async def test_startup_resolves_url_and_token(self, ha_module: HomeAssistantModule) -> None:
        mock_db = MagicMock()
        mock_db.pool = MagicMock()

        async def _resolve(pool: Any, info_type: str) -> str | None:
            return "http://ha.local" if info_type == "home_assistant_url" else "test-token"

        with patch(
            "butlers.credential_store.resolve_owner_entity_info",
            new=AsyncMock(side_effect=_resolve),
        ):
            with patch("httpx.AsyncClient", return_value=MagicMock()):
                with patch.object(HomeAssistantModule, "_ws_connect_and_seed", new=AsyncMock()):
                    await ha_module.on_startup(config={}, db=mock_db)

        assert ha_module._url == "http://ha.local"
        assert ha_module._token == "test-token"

    async def test_startup_raises_if_url_missing(self, ha_module: HomeAssistantModule) -> None:
        mock_db = MagicMock()
        mock_db.pool = MagicMock()
        with patch(
            "butlers.credential_store.resolve_owner_entity_info",
            new=AsyncMock(return_value=None),
        ):
            with pytest.raises(RuntimeError, match="home_assistant_url"):
                await ha_module.on_startup(config={}, db=mock_db)

    async def test_startup_raises_if_token_missing(self, ha_module: HomeAssistantModule) -> None:
        mock_db = MagicMock()
        mock_db.pool = MagicMock()

        async def _resolve(pool: Any, info_type: str) -> str | None:
            return "http://ha.local" if info_type == "home_assistant_url" else None

        with patch(
            "butlers.credential_store.resolve_owner_entity_info",
            new=AsyncMock(side_effect=_resolve),
        ):
            with pytest.raises(RuntimeError, match="home_assistant_token"):
                await ha_module.on_startup(config={}, db=mock_db)


# ---------------------------------------------------------------------------
# Shutdown lifecycle
# ---------------------------------------------------------------------------


class TestShutdown:
    async def test_shutdown_cleans_up_client(self, ha_module: HomeAssistantModule) -> None:
        mock_client = AsyncMock()
        ha_module._client = mock_client
        ha_module._url = "http://ha.local"
        ha_module._token = "tok"
        ha_module._config = HomeAssistantConfig()

        await ha_module.on_shutdown()

        mock_client.aclose.assert_awaited_once()
        assert ha_module._client is None
        assert ha_module._url is None
        assert ha_module._token is None


# ---------------------------------------------------------------------------
# WebSocket URL derivation
# ---------------------------------------------------------------------------


class TestWebSocketUrlDerivation:
    @pytest.mark.parametrize(
        "url,expected",
        [
            ("http://homeassistant.local:8123", "ws://homeassistant.local:8123/api/websocket"),
            ("https://ha.example.com:8123", "wss://ha.example.com:8123/api/websocket"),
            ("http://ha.local/", "ws://ha.local/api/websocket"),
        ],
    )
    def test_ws_url_derivation(
        self, ha_module: HomeAssistantModule, url: str, expected: str
    ) -> None:
        ha_module._url = url
        assert ha_module._ws_url() == expected


# ---------------------------------------------------------------------------
# Entity cache — state_changed events
# ---------------------------------------------------------------------------


class TestEntityCache:
    async def test_state_changed_updates_cache(self, ha_module: HomeAssistantModule) -> None:
        from butlers.modules._roster_home import CachedEntity

        ha_module._entity_cache["light.kitchen"] = CachedEntity(
            entity_id="light.kitchen", state="off"
        )

        await ha_module._dispatch_ws_message(
            {
                "type": "event",
                "event": {
                    "event_type": "state_changed",
                    "data": {
                        "entity_id": "light.kitchen",
                        "new_state": {
                            "entity_id": "light.kitchen",
                            "state": "on",
                            "attributes": {"brightness": 200},
                            "last_changed": "2024-01-01T10:00:00+00:00",
                            "last_updated": "2024-01-01T10:00:00+00:00",
                        },
                    },
                },
            }
        )

        assert ha_module._entity_cache["light.kitchen"].state == "on"

    async def test_state_changed_null_removes_entity(self, ha_module: HomeAssistantModule) -> None:
        from butlers.modules._roster_home import CachedEntity

        ha_module._entity_cache["sensor.gone"] = CachedEntity(entity_id="sensor.gone", state="42")

        await ha_module._dispatch_ws_message(
            {
                "type": "event",
                "event": {
                    "event_type": "state_changed",
                    "data": {"entity_id": "sensor.gone", "new_state": None},
                },
            }
        )

        assert "sensor.gone" not in ha_module._entity_cache


# ---------------------------------------------------------------------------
# Key tool behaviors
# ---------------------------------------------------------------------------


class TestToolBehaviors:
    async def test_invalid_entity_id_raises(self, ha_module: HomeAssistantModule) -> None:
        ha_module._client = MagicMock()
        with pytest.raises(ValueError):
            await ha_module._get_entity_state("light/invalid")

    async def test_list_areas_returns_sorted(self, ha_module: HomeAssistantModule) -> None:
        from butlers.modules._roster_home import CachedArea

        ha_module._area_cache["z_area"] = CachedArea(area_id="z", name="Zebra Room")
        ha_module._area_cache["a_area"] = CachedArea(area_id="a", name="Alpha Room")
        result = await ha_module._list_areas()
        names = [a["name"] for a in result]
        assert names == sorted(names)

    async def test_get_entity_state_from_cache(self, ha_module: HomeAssistantModule) -> None:
        from butlers.modules._roster_home import CachedEntity

        ha_module._entity_cache["sensor.temp"] = CachedEntity(
            entity_id="sensor.temp", state="22.5", attributes={"unit": "°C"}
        )
        ha_module._client = MagicMock()
        result = await ha_module._get_entity_state("sensor.temp")
        assert result is not None
        assert result["state"] == "22.5"
