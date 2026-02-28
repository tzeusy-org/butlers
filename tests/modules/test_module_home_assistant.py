"""Tests for the Home Assistant module scaffold.

Covers:
- HomeAssistantModule ABC compliance (no TypeError on instantiation)
- HomeAssistantConfig validation (required url, extra=forbid, defaults)
- on_startup credential resolution (token from owner contact_info)
- migration_revisions() returns 'home_assistant'
- Tool registration (register_tools creates expected MCP tools)
- Lifecycle: on_shutdown cleans up client
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel, ValidationError

from butlers.modules.base import Module
from butlers.modules.home_assistant import HomeAssistantConfig, HomeAssistantModule

pytestmark = pytest.mark.unit

EXPECTED_HA_TOOLS = {
    "ha_get_entity_state",
    "ha_list_entities",
    "ha_call_service",
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ha_module() -> HomeAssistantModule:
    """Create a fresh HomeAssistantModule instance."""
    return HomeAssistantModule()


@pytest.fixture
def mock_mcp() -> MagicMock:
    """Create a mock MCP server that captures registered tools."""
    mcp = MagicMock()
    tools: dict[str, Any] = {}

    def tool_decorator(*_decorator_args, **decorator_kwargs):
        declared_name = decorator_kwargs.get("name")

        def decorator(fn):
            tools[declared_name or fn.__name__] = fn
            return fn

        return decorator

    mcp.tool = tool_decorator
    mcp._registered_tools = tools
    return mcp


@pytest.fixture
def valid_config_dict() -> dict[str, Any]:
    return {"url": "http://homeassistant.local:8123"}


# ---------------------------------------------------------------------------
# ABC compliance
# ---------------------------------------------------------------------------


class TestModuleABCCompliance:
    """HomeAssistantModule must implement all abstract members without error."""

    def test_no_type_error_on_instantiation(self) -> None:
        """Instantiating HomeAssistantModule must not raise TypeError."""
        module = HomeAssistantModule()
        assert module is not None

    def test_is_module_subclass(self) -> None:
        """HomeAssistantModule is a subclass of Module."""
        assert issubclass(HomeAssistantModule, Module)

    def test_name(self, ha_module: HomeAssistantModule) -> None:
        """Module name is 'home_assistant'."""
        assert ha_module.name == "home_assistant"

    def test_config_schema(self, ha_module: HomeAssistantModule) -> None:
        """config_schema returns HomeAssistantConfig (a BaseModel subclass)."""
        assert ha_module.config_schema is HomeAssistantConfig
        assert issubclass(ha_module.config_schema, BaseModel)

    def test_dependencies_empty(self, ha_module: HomeAssistantModule) -> None:
        """Home Assistant module declares no dependencies."""
        assert ha_module.dependencies == []

    def test_migration_revisions(self, ha_module: HomeAssistantModule) -> None:
        """migration_revisions() returns 'home_assistant'."""
        assert ha_module.migration_revisions() == "home_assistant"

    def test_tool_metadata_default_empty(self, ha_module: HomeAssistantModule) -> None:
        """Default tool_metadata() returns empty dict (no explicit declarations)."""
        assert ha_module.tool_metadata() == {}


# ---------------------------------------------------------------------------
# HomeAssistantConfig validation
# ---------------------------------------------------------------------------


class TestHomeAssistantConfig:
    """Verify HomeAssistantConfig validation and defaults."""

    def test_url_required(self) -> None:
        """url is required; omitting it raises ValidationError."""
        with pytest.raises(ValidationError):
            HomeAssistantConfig()  # type: ignore[call-arg]

    def test_url_accepted(self) -> None:
        """url is accepted and stored as-is."""
        config = HomeAssistantConfig(url="http://homeassistant.local:8123")
        assert config.url == "http://homeassistant.local:8123"

    def test_defaults(self) -> None:
        """All optional fields have correct defaults."""
        config = HomeAssistantConfig(url="http://ha.local")
        assert config.verify_ssl is False
        assert config.websocket_ping_interval == 30
        assert config.poll_interval_seconds == 60
        assert config.snapshot_interval_seconds == 300

    def test_extra_fields_rejected(self) -> None:
        """Extra fields raise ValidationError (extra='forbid')."""
        with pytest.raises(ValidationError):
            HomeAssistantConfig(url="http://ha.local", unknown_field="boom")

    def test_verify_ssl_configurable(self) -> None:
        """verify_ssl can be set to True."""
        config = HomeAssistantConfig(url="https://ha.local", verify_ssl=True)
        assert config.verify_ssl is True

    def test_intervals_configurable(self) -> None:
        """Interval fields can be overridden."""
        config = HomeAssistantConfig(
            url="http://ha.local",
            websocket_ping_interval=60,
            poll_interval_seconds=120,
            snapshot_interval_seconds=600,
        )
        assert config.websocket_ping_interval == 60
        assert config.poll_interval_seconds == 120
        assert config.snapshot_interval_seconds == 600

    def test_from_dict(self, valid_config_dict: dict[str, Any]) -> None:
        """Config can be constructed from a dict (as from butler.toml)."""
        config = HomeAssistantConfig(**valid_config_dict)
        assert config.url == "http://homeassistant.local:8123"

    def test_empty_dict_raises(self) -> None:
        """An empty dict raises because url is required."""
        with pytest.raises(ValidationError):
            HomeAssistantConfig(**{})


# ---------------------------------------------------------------------------
# on_startup — credential resolution
# ---------------------------------------------------------------------------


class TestOnStartupCredentialResolution:
    """Verify on_startup resolves token from owner contact_info."""

    async def test_startup_resolves_token_from_contact_info(
        self, ha_module: HomeAssistantModule
    ) -> None:
        """on_startup calls resolve_owner_contact_info and caches the token."""
        mock_pool = MagicMock()
        mock_db = MagicMock()
        mock_db.pool = mock_pool

        with patch(
            "butlers.credential_store.resolve_owner_contact_info",
            new=AsyncMock(return_value="test-ha-token-12345"),
        ) as mock_resolve:
            with patch("httpx.AsyncClient", return_value=MagicMock()) as _:
                await ha_module.on_startup(
                    config={"url": "http://ha.local"},
                    db=mock_db,
                )

            mock_resolve.assert_awaited_once_with(mock_pool, "home_assistant_token")

        assert ha_module._token == "test-ha-token-12345"

    async def test_startup_raises_if_token_missing(self, ha_module: HomeAssistantModule) -> None:
        """on_startup raises RuntimeError when token is not found in contact_info."""
        mock_db = MagicMock()
        mock_db.pool = MagicMock()

        with patch(
            "butlers.credential_store.resolve_owner_contact_info",
            new=AsyncMock(return_value=None),
        ):
            with pytest.raises(RuntimeError, match="home_assistant_token"):
                await ha_module.on_startup(
                    config={"url": "http://ha.local"},
                    db=mock_db,
                )

    async def test_startup_raises_if_pool_none_and_no_token(
        self, ha_module: HomeAssistantModule
    ) -> None:
        """on_startup raises RuntimeError when db has no pool (no DB available)."""
        with pytest.raises(RuntimeError, match="home_assistant_token"):
            await ha_module.on_startup(
                config={"url": "http://ha.local"},
                db=None,
            )

    async def test_startup_creates_http_client_with_auth_header(
        self, ha_module: HomeAssistantModule
    ) -> None:
        """on_startup creates httpx.AsyncClient with Authorization header."""
        mock_db = MagicMock()
        mock_db.pool = MagicMock()

        with patch(
            "butlers.credential_store.resolve_owner_contact_info",
            new=AsyncMock(return_value="my-secret-token"),
        ):
            with patch("httpx.AsyncClient", return_value=MagicMock()) as mock_client_cls:
                await ha_module.on_startup(
                    config={"url": "http://ha.local"},
                    db=mock_db,
                )
                mock_client_cls.assert_called_once()
                call_kwargs = mock_client_cls.call_args.kwargs
                assert call_kwargs["base_url"] == "http://ha.local"
                assert "Authorization" in call_kwargs["headers"]
                assert call_kwargs["headers"]["Authorization"] == "Bearer my-secret-token"
                assert call_kwargs["verify"] is False  # default verify_ssl=False

    async def test_startup_verify_ssl_passed_to_client(
        self, ha_module: HomeAssistantModule
    ) -> None:
        """on_startup passes verify_ssl to httpx.AsyncClient."""
        mock_db = MagicMock()
        mock_db.pool = MagicMock()

        with patch(
            "butlers.credential_store.resolve_owner_contact_info",
            new=AsyncMock(return_value="tok"),
        ):
            with patch("httpx.AsyncClient", return_value=MagicMock()) as mock_client_cls:
                await ha_module.on_startup(
                    config={"url": "https://ha.local", "verify_ssl": True},
                    db=mock_db,
                )
                call_kwargs = mock_client_cls.call_args.kwargs
                assert call_kwargs["verify"] is True


# ---------------------------------------------------------------------------
# on_shutdown
# ---------------------------------------------------------------------------


class TestShutdown:
    """Verify on_shutdown cleans up resources."""

    async def test_shutdown_closes_http_client(self, ha_module: HomeAssistantModule) -> None:
        """on_shutdown calls aclose() on the HTTP client and nullifies it."""
        mock_client = AsyncMock()
        ha_module._client = mock_client
        ha_module._token = "some-token"
        ha_module._config = HomeAssistantConfig(url="http://ha.local")

        await ha_module.on_shutdown()

        mock_client.aclose.assert_awaited_once()
        assert ha_module._client is None
        assert ha_module._token is None
        assert ha_module._config is None

    async def test_shutdown_idempotent_with_no_client(self, ha_module: HomeAssistantModule) -> None:
        """on_shutdown is a no-op when no client is present."""
        ha_module._client = None

        # Should not raise
        await ha_module.on_shutdown()
        await ha_module.on_shutdown()


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


class TestToolRegistration:
    """Verify register_tools creates expected MCP tools."""

    async def test_registers_expected_tools(
        self, ha_module: HomeAssistantModule, mock_mcp: MagicMock
    ) -> None:
        """register_tools creates ha_get_entity_state, ha_list_entities, ha_call_service."""
        await ha_module.register_tools(
            mcp=mock_mcp,
            config={"url": "http://ha.local"},
            db=None,
        )
        assert set(mock_mcp._registered_tools.keys()) == EXPECTED_HA_TOOLS

    async def test_registered_tools_are_callable(
        self, ha_module: HomeAssistantModule, mock_mcp: MagicMock
    ) -> None:
        """All registered HA tools are callable."""
        await ha_module.register_tools(
            mcp=mock_mcp,
            config={"url": "http://ha.local"},
            db=None,
        )
        for name in EXPECTED_HA_TOOLS:
            assert callable(mock_mcp._registered_tools[name])

    async def test_tools_have_ha_prefix(
        self, ha_module: HomeAssistantModule, mock_mcp: MagicMock
    ) -> None:
        """All registered HA tools use the ha_ prefix."""
        await ha_module.register_tools(
            mcp=mock_mcp,
            config={"url": "http://ha.local"},
            db=None,
        )
        for name in mock_mcp._registered_tools:
            assert name.startswith("ha_"), f"Tool '{name}' does not start with 'ha_'"


# ---------------------------------------------------------------------------
# Registry integration
# ---------------------------------------------------------------------------


class TestRegistryIntegration:
    """Verify HomeAssistantModule integrates with ModuleRegistry."""

    def test_register_in_registry(self) -> None:
        """HomeAssistantModule can be registered in ModuleRegistry."""
        from butlers.modules.registry import ModuleRegistry

        reg = ModuleRegistry()
        reg.register(HomeAssistantModule)
        assert "home_assistant" in reg.available_modules

    def test_load_from_config(self) -> None:
        """HomeAssistantModule can be loaded from registry with config dict."""
        from butlers.modules.registry import ModuleRegistry

        reg = ModuleRegistry()
        reg.register(HomeAssistantModule)
        modules = reg.load_from_config({"home_assistant": {"url": "http://ha.local"}})
        assert len(modules) == 1
        assert modules[0].name == "home_assistant"

    def test_default_registry_includes_home_assistant(self) -> None:
        """default_registry() auto-discovers and registers HomeAssistantModule."""
        from butlers.modules.registry import default_registry

        reg = default_registry()
        assert "home_assistant" in reg.available_modules
