"""Tests for the Home Assistant module.

Covers:
- HomeAssistantModule ABC compliance (no TypeError on instantiation)
- HomeAssistantConfig validation (required url, extra=forbid, defaults)
- on_startup credential resolution (token from owner contact_info)
- migration_revisions() returns 'home_assistant'
- Tool registration (register_tools creates expected MCP tools)
- tool_metadata() returns sensitivity for ha_call_service
- Lifecycle: on_shutdown cleans up client and WebSocket
- WebSocket URL derivation (http → ws, https → wss)
- WebSocket authentication flow (auth_required → auth → auth_ok)
- WebSocket message dispatch (event, result, pong)
- Entity cache population from REST and state_changed events
- Entity cache removal on null new_state
- Area and entity registry caches
- _list_entities_from_cache with domain and area filtering
- WebSocket command helper (auto-incrementing ID, response correlation)
- Auto-reconnect scheduling and backoff
- REST polling fallback start/stop
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel, ValidationError

from butlers.modules.base import Module, ToolMeta
from butlers.modules.home_assistant import (
    CachedArea,
    CachedEntity,
    CachedEntityRegistryEntry,
    HomeAssistantConfig,
    HomeAssistantModule,
)

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

    def test_tool_metadata_ha_call_service_sensitive(self, ha_module: HomeAssistantModule) -> None:
        """tool_metadata() returns sensitivity metadata for ha_call_service."""
        meta = ha_module.tool_metadata()
        assert "ha_call_service" in meta
        assert isinstance(meta["ha_call_service"], ToolMeta)
        assert meta["ha_call_service"].arg_sensitivities.get("domain") is True
        assert meta["ha_call_service"].arg_sensitivities.get("service") is True

    def test_tool_metadata_query_tools_not_listed(self, ha_module: HomeAssistantModule) -> None:
        """Query tools do not appear in tool_metadata (no explicit sensitivity)."""
        meta = ha_module.tool_metadata()
        assert "ha_get_entity_state" not in meta
        assert "ha_list_entities" not in meta


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


def _patch_startup(token: str = "test-ha-token-12345") -> Any:
    """Context manager stack that patches on_startup's external dependencies.

    Patches:
    - ``resolve_owner_contact_info`` to return ``token``
    - ``httpx.AsyncClient`` to a MagicMock
    - ``HomeAssistantModule._ws_connect_and_seed`` to a no-op async
    """
    from contextlib import AsyncExitStack, nullcontext

    class _Stack:
        """Helper that sequences three patches without nested with-blocks."""

        def __init__(self) -> None:
            self.mock_resolve: AsyncMock | None = None
            self.mock_client_cls: MagicMock | None = None

        async def __aenter__(self) -> _Stack:
            self._p1 = patch(
                "butlers.credential_store.resolve_owner_contact_info",
                new=AsyncMock(return_value=token),
            )
            self._p2 = patch("httpx.AsyncClient", return_value=MagicMock())
            self._p3 = patch.object(HomeAssistantModule, "_ws_connect_and_seed", new=AsyncMock())
            self.mock_resolve = self._p1.start()
            self.mock_client_cls = self._p2.start()
            self._p3.start()
            return self

        async def __aexit__(self, *args: Any) -> None:
            self._p1.stop()
            self._p2.stop()
            self._p3.stop()

    _ = nullcontext, AsyncExitStack  # suppress unused import warnings
    return _Stack()


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
            with patch("httpx.AsyncClient", return_value=MagicMock()):
                with patch.object(HomeAssistantModule, "_ws_connect_and_seed", new=AsyncMock()):
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
                with patch.object(HomeAssistantModule, "_ws_connect_and_seed", new=AsyncMock()):
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
                with patch.object(HomeAssistantModule, "_ws_connect_and_seed", new=AsyncMock()):
                    await ha_module.on_startup(
                        config={"url": "https://ha.local", "verify_ssl": True},
                        db=mock_db,
                    )
                call_kwargs = mock_client_cls.call_args.kwargs
                assert call_kwargs["verify"] is True

    async def test_startup_calls_ws_connect_and_seed(self, ha_module: HomeAssistantModule) -> None:
        """on_startup invokes _ws_connect_and_seed to establish WebSocket connection."""
        mock_db = MagicMock()
        mock_db.pool = MagicMock()

        with patch(
            "butlers.credential_store.resolve_owner_contact_info",
            new=AsyncMock(return_value="tok"),
        ):
            with patch("httpx.AsyncClient", return_value=MagicMock()):
                with patch.object(
                    HomeAssistantModule,
                    "_ws_connect_and_seed",
                    new=AsyncMock(),
                ) as mock_seed:
                    await ha_module.on_startup(
                        config={"url": "http://ha.local"},
                        db=mock_db,
                    )
                    mock_seed.assert_awaited_once()


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

    async def test_shutdown_closes_ws_session(self, ha_module: HomeAssistantModule) -> None:
        """on_shutdown closes the aiohttp WebSocket session."""
        mock_ws_session = AsyncMock()
        mock_ws_session.closed = False
        ha_module._ws_session = mock_ws_session
        ha_module._client = AsyncMock()
        ha_module._config = HomeAssistantConfig(url="http://ha.local")

        await ha_module.on_shutdown()

        mock_ws_session.close.assert_awaited_once()
        assert ha_module._ws_session is None

    async def test_shutdown_cancels_background_tasks(self, ha_module: HomeAssistantModule) -> None:
        """on_shutdown cancels all running background asyncio tasks."""

        # Create real tasks that just sleep forever
        async def _forever() -> None:
            await asyncio.sleep(9999)

        ha_module._ws_loop_task = asyncio.ensure_future(_forever())
        ha_module._ws_ping_task = asyncio.ensure_future(_forever())
        ha_module._client = AsyncMock()
        ha_module._config = HomeAssistantConfig(url="http://ha.local")

        await ha_module.on_shutdown()

        assert ha_module._ws_loop_task is None
        assert ha_module._ws_ping_task is None

    async def test_shutdown_cancels_pending_ws_futures(
        self, ha_module: HomeAssistantModule
    ) -> None:
        """on_shutdown cancels any pending WebSocket command futures."""
        loop = asyncio.get_event_loop()
        fut: asyncio.Future[dict] = loop.create_future()
        ha_module._ws_pending[42] = fut
        ha_module._client = AsyncMock()
        ha_module._config = HomeAssistantConfig(url="http://ha.local")

        await ha_module.on_shutdown()

        assert fut.cancelled()
        assert len(ha_module._ws_pending) == 0


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


class TestToolRegistration:
    """Verify register_tools creates expected MCP tools."""

    async def test_registers_expected_tools(
        self, ha_module: HomeAssistantModule, mock_mcp: MagicMock
    ) -> None:
        """register_tools creates all 8 expected HA query and control tools."""
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


# ---------------------------------------------------------------------------
# WebSocket URL derivation
# ---------------------------------------------------------------------------


class TestWebSocketUrlDerivation:
    """Verify _ws_url() derives the correct WebSocket URL from the HA base URL."""

    def test_http_becomes_ws(self, ha_module: HomeAssistantModule) -> None:
        """http:// base URL produces ws:// WebSocket URL."""
        ha_module._config = HomeAssistantConfig(url="http://homeassistant.local:8123")
        assert ha_module._ws_url() == "ws://homeassistant.local:8123/api/websocket"

    def test_https_becomes_wss(self, ha_module: HomeAssistantModule) -> None:
        """https:// base URL produces wss:// WebSocket URL."""
        ha_module._config = HomeAssistantConfig(url="https://ha.example.com:8123")
        assert ha_module._ws_url() == "wss://ha.example.com:8123/api/websocket"

    def test_trailing_slash_stripped(self, ha_module: HomeAssistantModule) -> None:
        """Trailing slash in URL is stripped before appending /api/websocket."""
        ha_module._config = HomeAssistantConfig(url="http://ha.local/")
        assert ha_module._ws_url() == "ws://ha.local/api/websocket"

    def test_tailscale_url(self, ha_module: HomeAssistantModule) -> None:
        """Tailscale URLs work correctly."""
        ha_module._config = HomeAssistantConfig(url="http://homeassistant.tail1234.ts.net:8123")
        url = ha_module._ws_url()
        assert url.startswith("ws://")
        assert url.endswith("/api/websocket")


# ---------------------------------------------------------------------------
# WebSocket authentication flow
# ---------------------------------------------------------------------------


def _make_ws_mock(*messages: dict[str, Any]) -> MagicMock:
    """Build a mock aiohttp WebSocket connection that returns ``messages`` in sequence."""
    ws = AsyncMock()
    ws.closed = False
    ws.receive_json = AsyncMock(side_effect=list(messages))
    ws.send_json = AsyncMock()
    ws.close = AsyncMock()
    return ws


def _make_aiohttp_session_mock(ws_mock: MagicMock) -> MagicMock:
    """Build a mock aiohttp.ClientSession that returns ``ws_mock`` from ws_connect."""
    session = AsyncMock()
    session.closed = False
    session.ws_connect = AsyncMock(return_value=ws_mock)
    session.close = AsyncMock()
    return session


class TestWebSocketAuthentication:
    """Verify the WebSocket auth flow: auth_required → auth → auth_ok."""

    def _inject_session(self, ha_module: HomeAssistantModule, ws: MagicMock) -> MagicMock:
        """Inject a pre-built mock session so _ws_connect skips aiohttp creation."""
        session = _make_aiohttp_session_mock(ws)
        # Pre-set the session so the 'if self._ws_session is None' branch is skipped
        ha_module._ws_session = session
        return session

    async def test_auth_ok_sets_connected(self, ha_module: HomeAssistantModule) -> None:
        """Successful auth sets _ws_connected = True."""
        ha_module._config = HomeAssistantConfig(url="http://ha.local")
        ha_module._token = "secret-token"

        ws = _make_ws_mock(
            {"type": "auth_required", "ha_version": "2024.1.0"},
            {"type": "auth_ok", "ha_version": "2024.1.0"},
        )
        self._inject_session(ha_module, ws)

        await ha_module._ws_connect()

        assert ha_module._ws_connected is True

    async def test_auth_sends_access_token(self, ha_module: HomeAssistantModule) -> None:
        """Auth message sent to HA contains the resolved access token."""
        ha_module._config = HomeAssistantConfig(url="http://ha.local")
        ha_module._token = "my-llat-token"

        ws = _make_ws_mock(
            {"type": "auth_required"},
            {"type": "auth_ok"},
        )
        self._inject_session(ha_module, ws)

        await ha_module._ws_connect()

        # First send_json call is the auth message
        auth_call = ws.send_json.call_args_list[0]
        sent = auth_call.args[0]
        assert sent["type"] == "auth"
        assert sent["access_token"] == "my-llat-token"

    async def test_auth_sends_supported_features(self, ha_module: HomeAssistantModule) -> None:
        """After auth_ok, supported_features with coalesce_messages: 1 is sent."""
        ha_module._config = HomeAssistantConfig(url="http://ha.local")
        ha_module._token = "tok"

        ws = _make_ws_mock(
            {"type": "auth_required"},
            {"type": "auth_ok"},
        )
        self._inject_session(ha_module, ws)

        await ha_module._ws_connect()

        # Second send_json call should be supported_features
        assert ws.send_json.call_count == 2
        features_call = ws.send_json.call_args_list[1]
        sent = features_call.args[0]
        assert sent["type"] == "supported_features"
        assert sent["features"]["coalesce_messages"] == 1

    async def test_auth_invalid_raises(self, ha_module: HomeAssistantModule) -> None:
        """auth_invalid response raises RuntimeError."""
        ha_module._config = HomeAssistantConfig(url="http://ha.local")
        ha_module._token = "bad-token"

        ws = _make_ws_mock(
            {"type": "auth_required"},
            {"type": "auth_invalid", "message": "Invalid access token"},
        )
        self._inject_session(ha_module, ws)

        with pytest.raises(RuntimeError, match="auth_invalid"):
            await ha_module._ws_connect()

        assert ha_module._ws_connected is False

    async def test_unexpected_first_message_raises(self, ha_module: HomeAssistantModule) -> None:
        """If the first WS message is not auth_required, RuntimeError is raised."""
        ha_module._config = HomeAssistantConfig(url="http://ha.local")
        ha_module._token = "tok"

        ws = _make_ws_mock({"type": "event", "data": {}})
        self._inject_session(ha_module, ws)

        with pytest.raises(RuntimeError, match="auth_required"):
            await ha_module._ws_connect()


# ---------------------------------------------------------------------------
# WebSocket message dispatch
# ---------------------------------------------------------------------------


class TestWebSocketMessageDispatch:
    """Verify _dispatch_ws_message routes by type correctly."""

    async def test_pong_updates_last_pong_time(self, ha_module: HomeAssistantModule) -> None:
        """Receiving a pong updates _last_pong_time."""
        ha_module._last_pong_time = 0.0
        before = asyncio.get_event_loop().time()

        await ha_module._dispatch_ws_message({"type": "pong"})

        assert ha_module._last_pong_time >= before

    async def test_result_resolves_pending_future(self, ha_module: HomeAssistantModule) -> None:
        """A result message resolves the matching pending WS command future."""
        loop = asyncio.get_event_loop()
        fut: asyncio.Future[dict] = loop.create_future()
        ha_module._ws_pending[7] = fut

        await ha_module._dispatch_ws_message(
            {"type": "result", "id": 7, "success": True, "result": {"answer": 42}}
        )

        assert fut.done()
        assert fut.result() == {"answer": 42}

    async def test_result_error_sets_exception(self, ha_module: HomeAssistantModule) -> None:
        """A failed result message sets an exception on the matching future."""
        loop = asyncio.get_event_loop()
        fut: asyncio.Future[dict] = loop.create_future()
        ha_module._ws_pending[3] = fut

        await ha_module._dispatch_ws_message(
            {
                "type": "result",
                "id": 3,
                "success": False,
                "error": {"code": "unknown_command", "message": "oops"},
            }
        )

        assert fut.done()
        with pytest.raises(RuntimeError, match="unknown_command"):
            fut.result()

    async def test_result_unknown_id_is_ignored(self, ha_module: HomeAssistantModule) -> None:
        """A result with an unknown ID is silently ignored (no KeyError)."""
        await ha_module._dispatch_ws_message(
            {"type": "result", "id": 99999, "success": True, "result": {}}
        )

    async def test_state_changed_event_updates_cache(self, ha_module: HomeAssistantModule) -> None:
        """state_changed event updates the entity cache."""
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
        assert ha_module._entity_cache["light.kitchen"].attributes["brightness"] == 200

    async def test_state_changed_null_new_state_removes_entity(
        self, ha_module: HomeAssistantModule
    ) -> None:
        """state_changed with null new_state removes the entity from the cache."""
        ha_module._entity_cache["sensor.gone"] = CachedEntity(entity_id="sensor.gone", state="42")

        await ha_module._dispatch_ws_message(
            {
                "type": "event",
                "event": {
                    "event_type": "state_changed",
                    "data": {
                        "entity_id": "sensor.gone",
                        "new_state": None,
                    },
                },
            }
        )

        assert "sensor.gone" not in ha_module._entity_cache

    async def test_area_registry_updated_triggers_refresh(
        self, ha_module: HomeAssistantModule
    ) -> None:
        """area_registry_updated event triggers _fetch_area_registry."""
        ha_module._ws_connected = True
        ha_module._ws_connection = AsyncMock()

        with patch.object(ha_module, "_fetch_area_registry", new=AsyncMock()) as mock_fetch:
            await ha_module._dispatch_ws_message(
                {
                    "type": "event",
                    "event": {"event_type": "area_registry_updated", "data": {}},
                }
            )
            mock_fetch.assert_awaited_once()

    async def test_entity_registry_updated_triggers_refresh(
        self, ha_module: HomeAssistantModule
    ) -> None:
        """entity_registry_updated event triggers _fetch_entity_registry."""
        ha_module._ws_connected = True
        ha_module._ws_connection = AsyncMock()

        with patch.object(ha_module, "_fetch_entity_registry", new=AsyncMock()) as mock_fetch:
            await ha_module._dispatch_ws_message(
                {
                    "type": "event",
                    "event": {"event_type": "entity_registry_updated", "data": {}},
                }
            )
            mock_fetch.assert_awaited_once()


# ---------------------------------------------------------------------------
# Entity cache — seeding from REST
# ---------------------------------------------------------------------------


class TestEntityCacheSeeding:
    """Verify _seed_entity_cache_from_rest populates the entity cache."""

    async def test_seed_populates_cache(self, ha_module: HomeAssistantModule) -> None:
        """_seed_entity_cache_from_rest fills the cache from the REST states response."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = [
            {
                "entity_id": "light.living_room",
                "state": "on",
                "attributes": {"friendly_name": "Living Room Light", "brightness": 255},
                "last_changed": "2024-01-01T08:00:00+00:00",
                "last_updated": "2024-01-01T08:00:00+00:00",
            },
            {
                "entity_id": "sensor.temp",
                "state": "21.5",
                "attributes": {},
                "last_changed": "2024-01-01T07:00:00+00:00",
                "last_updated": "2024-01-01T07:00:00+00:00",
            },
        ]
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        ha_module._client = mock_client

        await ha_module._seed_entity_cache_from_rest()

        assert "light.living_room" in ha_module._entity_cache
        assert ha_module._entity_cache["light.living_room"].state == "on"
        assert "sensor.temp" in ha_module._entity_cache

    async def test_seed_replaces_existing_cache(self, ha_module: HomeAssistantModule) -> None:
        """_seed_entity_cache_from_rest replaces the full cache (not merge)."""
        # Pre-populate with a stale entity
        ha_module._entity_cache["stale.entity"] = CachedEntity(
            entity_id="stale.entity", state="old"
        )

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = [
            {
                "entity_id": "sensor.new",
                "state": "fresh",
                "attributes": {},
                "last_changed": "",
                "last_updated": "",
            }
        ]
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        ha_module._client = mock_client

        await ha_module._seed_entity_cache_from_rest()

        assert "stale.entity" not in ha_module._entity_cache
        assert "sensor.new" in ha_module._entity_cache

    async def test_seed_no_op_without_client(self, ha_module: HomeAssistantModule) -> None:
        """_seed_entity_cache_from_rest is a no-op when client is None."""
        ha_module._client = None
        # Should not raise
        await ha_module._seed_entity_cache_from_rest()
        assert ha_module._entity_cache == {}

    async def test_seed_populates_area_id_from_entity_area_map(
        self, ha_module: HomeAssistantModule
    ) -> None:
        """Seeded entities inherit area_id from the entity_area_map."""
        ha_module._entity_area_map["light.bed"] = "bedroom_area"

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = [
            {
                "entity_id": "light.bed",
                "state": "off",
                "attributes": {},
                "last_changed": "",
                "last_updated": "",
            }
        ]
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        ha_module._client = mock_client

        await ha_module._seed_entity_cache_from_rest()

        assert ha_module._entity_cache["light.bed"].area_id == "bedroom_area"


# ---------------------------------------------------------------------------
# Registry caches — area and entity registries
# ---------------------------------------------------------------------------


class TestRegistryCaches:
    """Verify area and entity registry fetching via WebSocket commands."""

    async def test_fetch_area_registry_populates_cache(
        self, ha_module: HomeAssistantModule
    ) -> None:
        """_fetch_area_registry populates _area_cache from WS response."""
        ha_module._ws_connected = True
        with patch.object(
            ha_module,
            "_ws_command",
            new=AsyncMock(
                return_value=[
                    {"area_id": "living_room", "name": "Living Room"},
                    {"area_id": "bedroom", "name": "Bedroom"},
                ]
            ),
        ):
            await ha_module._fetch_area_registry()

        assert "living_room" in ha_module._area_cache
        assert ha_module._area_cache["living_room"].name == "Living Room"
        assert "bedroom" in ha_module._area_cache

    async def test_fetch_area_registry_noop_when_disconnected(
        self, ha_module: HomeAssistantModule
    ) -> None:
        """_fetch_area_registry is a no-op when WebSocket is not connected."""
        ha_module._ws_connected = False
        with patch.object(ha_module, "_ws_command", new=AsyncMock()) as mock_cmd:
            await ha_module._fetch_area_registry()
            mock_cmd.assert_not_awaited()

    async def test_fetch_entity_registry_populates_area_map(
        self, ha_module: HomeAssistantModule
    ) -> None:
        """_fetch_entity_registry builds _entity_area_map from WS response."""
        ha_module._ws_connected = True
        with patch.object(
            ha_module,
            "_ws_command",
            new=AsyncMock(
                return_value=[
                    {"entity_id": "light.kitchen", "area_id": "kitchen"},
                    {"entity_id": "sensor.bedroom_temp", "area_id": "bedroom"},
                    {"entity_id": "switch.no_area"},  # no area_id key
                ]
            ),
        ):
            await ha_module._fetch_entity_registry()

        assert ha_module._entity_area_map["light.kitchen"] == "kitchen"
        assert ha_module._entity_area_map["sensor.bedroom_temp"] == "bedroom"
        assert "switch.no_area" not in ha_module._entity_area_map

    async def test_fetch_entity_registry_backfills_cached_entities(
        self, ha_module: HomeAssistantModule
    ) -> None:
        """_fetch_entity_registry updates area_id on already-cached entities."""
        ha_module._entity_cache["light.hall"] = CachedEntity(
            entity_id="light.hall", state="off", area_id=None
        )
        ha_module._ws_connected = True

        with patch.object(
            ha_module,
            "_ws_command",
            new=AsyncMock(
                return_value=[
                    {"entity_id": "light.hall", "area_id": "hallway"},
                ]
            ),
        ):
            await ha_module._fetch_entity_registry()

        assert ha_module._entity_cache["light.hall"].area_id == "hallway"


# ---------------------------------------------------------------------------
# WebSocket command helper
# ---------------------------------------------------------------------------


class TestWebSocketCommandHelper:
    """Verify _ws_command sends with auto-incrementing ID and awaits response."""

    async def test_command_increments_id(self, ha_module: HomeAssistantModule) -> None:
        """Each _ws_command call uses the next auto-incrementing ID."""
        ha_module._ws_connected = True
        ws = AsyncMock()
        ha_module._ws_connection = ws

        # Simulate the message loop resolving the future after send_json
        async def side_effect(msg: dict[str, Any]) -> None:
            cmd_id = msg.get("id")
            if cmd_id in ha_module._ws_pending:
                ha_module._ws_pending[cmd_id].set_result({"ok": True})

        ws.send_json = AsyncMock(side_effect=side_effect)

        initial_id = ha_module._ws_cmd_id
        await ha_module._ws_command({"type": "get_states"})
        assert ha_module._ws_cmd_id == initial_id + 1

        await ha_module._ws_command({"type": "get_services"})
        assert ha_module._ws_cmd_id == initial_id + 2

    async def test_command_raises_when_not_connected(self, ha_module: HomeAssistantModule) -> None:
        """_ws_command raises RuntimeError when WebSocket is not connected."""
        ha_module._ws_connected = False
        ha_module._ws_connection = None

        with pytest.raises(RuntimeError, match="not connected"):
            await ha_module._ws_command({"type": "ping"})

    async def test_command_returns_result(self, ha_module: HomeAssistantModule) -> None:
        """_ws_command returns the result payload from the correlated response."""
        ha_module._ws_connected = True
        ws = AsyncMock()
        ha_module._ws_connection = ws

        expected_result = [{"area_id": "kitchen", "name": "Kitchen"}]

        async def side_effect(msg: dict[str, Any]) -> None:
            cmd_id = msg.get("id")
            if cmd_id in ha_module._ws_pending:
                ha_module._ws_pending[cmd_id].set_result(expected_result)

        ws.send_json = AsyncMock(side_effect=side_effect)

        result = await ha_module._ws_command({"type": "config/area_registry/list"})
        assert result == expected_result

    async def test_command_timeout_raises(self, ha_module: HomeAssistantModule) -> None:
        """_ws_command raises asyncio.TimeoutError when no response arrives."""
        ha_module._ws_connected = True
        ws = AsyncMock()
        ws.send_json = AsyncMock()  # does NOT resolve the future
        ha_module._ws_connection = ws

        with pytest.raises((asyncio.TimeoutError, TimeoutError)):
            await ha_module._ws_command({"type": "ping"}, timeout=0.05)


# ---------------------------------------------------------------------------
# _list_entities_from_cache — domain and area filtering
# ---------------------------------------------------------------------------


class TestListEntitiesFromCache:
    """Verify _list_entities_from_cache with various filter combinations."""

    @pytest.fixture
    def populated_module(self, ha_module: HomeAssistantModule) -> HomeAssistantModule:
        """Module with entity and area caches pre-populated."""
        ha_module._area_cache = {
            "kitchen": CachedArea(area_id="kitchen", name="Kitchen"),
            "bedroom": CachedArea(area_id="bedroom", name="Bedroom"),
        }
        ha_module._entity_area_map = {
            "light.kitchen_ceiling": "kitchen",
            "sensor.bedroom_temp": "bedroom",
        }
        ha_module._entity_cache = {
            "light.kitchen_ceiling": CachedEntity(
                entity_id="light.kitchen_ceiling",
                state="on",
                attributes={"friendly_name": "Kitchen Ceiling"},
                area_id="kitchen",
            ),
            "sensor.bedroom_temp": CachedEntity(
                entity_id="sensor.bedroom_temp",
                state="21.5",
                attributes={"friendly_name": "Bedroom Temperature"},
                area_id="bedroom",
            ),
            "switch.garage_door": CachedEntity(
                entity_id="switch.garage_door",
                state="off",
                attributes={},
                area_id=None,
            ),
        }
        return ha_module

    def test_no_filters_returns_all(self, populated_module: HomeAssistantModule) -> None:
        """No filters returns all entities sorted by entity_id."""
        results = populated_module._list_entities_from_cache()
        ids = [r["entity_id"] for r in results]
        assert ids == sorted(ids)
        assert len(results) == 3

    def test_domain_filter(self, populated_module: HomeAssistantModule) -> None:
        """Domain filter returns only entities with matching prefix."""
        results = populated_module._list_entities_from_cache(domain="light")
        assert len(results) == 1
        assert results[0]["entity_id"] == "light.kitchen_ceiling"

    def test_area_filter_by_name(self, populated_module: HomeAssistantModule) -> None:
        """Area filter by name returns entities in that area."""
        results = populated_module._list_entities_from_cache(area="Kitchen")
        assert len(results) == 1
        assert results[0]["entity_id"] == "light.kitchen_ceiling"

    def test_area_filter_by_id(self, populated_module: HomeAssistantModule) -> None:
        """Area filter by area_id returns entities in that area."""
        results = populated_module._list_entities_from_cache(area="bedroom")
        assert len(results) == 1
        assert results[0]["entity_id"] == "sensor.bedroom_temp"

    def test_area_filter_case_insensitive(self, populated_module: HomeAssistantModule) -> None:
        """Area filter by name is case-insensitive."""
        results = populated_module._list_entities_from_cache(area="KITCHEN")
        assert len(results) == 1
        assert results[0]["entity_id"] == "light.kitchen_ceiling"

    def test_area_filter_unknown_returns_empty(self, populated_module: HomeAssistantModule) -> None:
        """Unknown area name returns empty list."""
        results = populated_module._list_entities_from_cache(area="Attic")
        assert results == []

    def test_combined_domain_and_area_filter(self, populated_module: HomeAssistantModule) -> None:
        """Combined domain + area filter applies both."""
        results = populated_module._list_entities_from_cache(domain="light", area="kitchen")
        assert len(results) == 1
        results_mismatch = populated_module._list_entities_from_cache(
            domain="sensor", area="kitchen"
        )
        assert results_mismatch == []

    def test_summary_includes_area_name(self, populated_module: HomeAssistantModule) -> None:
        """Entity summaries include area_name from the area registry."""
        results = populated_module._list_entities_from_cache(domain="light")
        assert results[0]["area_name"] == "Kitchen"

    def test_summary_includes_domain(self, populated_module: HomeAssistantModule) -> None:
        """Entity summaries include the domain extracted from entity_id."""
        results = populated_module._list_entities_from_cache(domain="switch")
        assert results[0]["domain"] == "switch"


# ---------------------------------------------------------------------------
# _get_entity_state — cache-first, REST fallback
# ---------------------------------------------------------------------------


class TestGetEntityState:
    """Verify _get_entity_state serves from cache and falls back to REST."""

    async def test_serves_from_cache_when_populated(self, ha_module: HomeAssistantModule) -> None:
        """_get_entity_state returns cached data when entity is in cache."""
        ha_module._entity_cache["light.hall"] = CachedEntity(
            entity_id="light.hall",
            state="on",
            attributes={"brightness": 150},
            last_changed="2024-01-01T10:00:00+00:00",
            last_updated="2024-01-01T10:00:01+00:00",
        )
        ha_module._area_cache["hall_area"] = CachedArea(area_id="hall_area", name="Hall")
        ha_module._entity_cache["light.hall"].area_id = "hall_area"

        result = await ha_module._get_entity_state("light.hall")

        assert result is not None
        assert result["entity_id"] == "light.hall"
        assert result["state"] == "on"
        assert result["attributes"]["brightness"] == 150
        assert result["area_name"] == "Hall"

    async def test_falls_back_to_rest_on_cache_miss(self, ha_module: HomeAssistantModule) -> None:
        """_get_entity_state falls back to REST when entity not in cache."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "entity_id": "sensor.outside_temp",
            "state": "15.0",
        }
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        ha_module._client = mock_client

        result = await ha_module._get_entity_state("sensor.outside_temp")

        assert result is not None
        assert result["state"] == "15.0"
        mock_client.get.assert_awaited_once()

    async def test_returns_none_for_404(self, ha_module: HomeAssistantModule) -> None:
        """_get_entity_state returns None for REST 404 response."""
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        ha_module._client = mock_client

        result = await ha_module._get_entity_state("sensor.nonexistent")

        assert result is None


# ---------------------------------------------------------------------------
# Reconnect scheduling
# ---------------------------------------------------------------------------


class TestReconnectScheduling:
    """Verify auto-reconnect scheduling and polling fallback."""

    def test_schedule_reconnect_creates_task(self, ha_module: HomeAssistantModule) -> None:
        """_schedule_reconnect creates a background asyncio task."""
        ha_module._ws_reconnect_task = None
        ha_module._shutdown = False

        ha_module._schedule_reconnect(delay=1.0)

        assert ha_module._ws_reconnect_task is not None
        assert not ha_module._ws_reconnect_task.done()
        # Cleanup
        ha_module._ws_reconnect_task.cancel()

    def test_schedule_reconnect_noop_when_shutdown(self, ha_module: HomeAssistantModule) -> None:
        """_schedule_reconnect is a no-op when _shutdown is True."""
        ha_module._shutdown = True

        ha_module._schedule_reconnect(delay=1.0)

        assert ha_module._ws_reconnect_task is None

    def test_schedule_reconnect_noop_when_task_running(
        self, ha_module: HomeAssistantModule
    ) -> None:
        """_schedule_reconnect does not create a second task if one is already running."""

        async def _long() -> None:
            await asyncio.sleep(9999)

        ha_module._shutdown = False
        task = asyncio.ensure_future(_long())
        ha_module._ws_reconnect_task = task

        ha_module._schedule_reconnect(delay=0.1)

        # Same task object — no new task created
        assert ha_module._ws_reconnect_task is task
        task.cancel()

    def test_start_poll_fallback_creates_task(self, ha_module: HomeAssistantModule) -> None:
        """_start_poll_fallback creates a background polling task."""
        ha_module._poll_task = None
        ha_module._config = HomeAssistantConfig(url="http://ha.local")
        ha_module._shutdown = False

        ha_module._start_poll_fallback()

        assert ha_module._poll_task is not None
        assert not ha_module._poll_task.done()
        ha_module._poll_task.cancel()

    def test_stop_poll_fallback_cancels_task(self, ha_module: HomeAssistantModule) -> None:
        """_stop_poll_fallback cancels and clears the polling task."""

        async def _long() -> None:
            await asyncio.sleep(9999)

        ha_module._poll_task = asyncio.ensure_future(_long())

        ha_module._stop_poll_fallback()

        assert ha_module._poll_task is None


# ---------------------------------------------------------------------------
# CachedEntityRegistryEntry dataclass
# ---------------------------------------------------------------------------


class TestCachedEntityRegistryEntry:
    """Verify the CachedEntityRegistryEntry dataclass structure."""

    def test_entity_registry_entry_fields(self) -> None:
        """CachedEntityRegistryEntry has entity_id, area_id, device_id, platform."""
        entry = CachedEntityRegistryEntry(
            entity_id="light.kitchen_ceiling",
            area_id="kitchen",
            device_id="abc123",
            platform="zha",
        )
        assert entry.entity_id == "light.kitchen_ceiling"
        assert entry.area_id == "kitchen"
        assert entry.device_id == "abc123"
        assert entry.platform == "zha"

    def test_entity_registry_entry_optional_fields_default_to_none(self) -> None:
        """area_id, device_id, platform default to None."""
        entry = CachedEntityRegistryEntry(entity_id="sensor.temp")
        assert entry.area_id is None
        assert entry.device_id is None
        assert entry.platform is None

    def test_entity_registry_entry_partial_fields(self) -> None:
        """CachedEntityRegistryEntry can be created with only some optional fields."""
        entry = CachedEntityRegistryEntry(
            entity_id="light.hall",
            platform="hue",
        )
        assert entry.entity_id == "light.hall"
        assert entry.area_id is None
        assert entry.device_id is None
        assert entry.platform == "hue"


# ---------------------------------------------------------------------------
# Entity registry cache — device_id and platform
# ---------------------------------------------------------------------------


class TestEntityRegistryCacheExtended:
    """Verify _fetch_entity_registry populates _entity_registry with device_id and platform."""

    async def test_fetch_entity_registry_populates_registry_with_all_fields(
        self, ha_module: HomeAssistantModule
    ) -> None:
        """_fetch_entity_registry stores entity_id, area_id, device_id, platform."""
        ha_module._ws_connected = True
        with patch.object(
            ha_module,
            "_ws_command",
            new=AsyncMock(
                return_value=[
                    {
                        "entity_id": "light.kitchen_ceiling",
                        "area_id": "kitchen",
                        "device_id": "device_abc",
                        "platform": "zha",
                    },
                    {
                        "entity_id": "sensor.bedroom_temp",
                        "area_id": "bedroom",
                        "device_id": "device_def",
                        "platform": "mqtt",
                    },
                ]
            ),
        ):
            await ha_module._fetch_entity_registry()

        assert "light.kitchen_ceiling" in ha_module._entity_registry
        entry = ha_module._entity_registry["light.kitchen_ceiling"]
        assert entry.entity_id == "light.kitchen_ceiling"
        assert entry.area_id == "kitchen"
        assert entry.device_id == "device_abc"
        assert entry.platform == "zha"

        assert "sensor.bedroom_temp" in ha_module._entity_registry
        entry2 = ha_module._entity_registry["sensor.bedroom_temp"]
        assert entry2.device_id == "device_def"
        assert entry2.platform == "mqtt"

    async def test_fetch_entity_registry_handles_missing_optional_fields(
        self, ha_module: HomeAssistantModule
    ) -> None:
        """Entities without device_id or platform get None values in registry."""
        ha_module._ws_connected = True
        with patch.object(
            ha_module,
            "_ws_command",
            new=AsyncMock(
                return_value=[
                    {
                        "entity_id": "virtual.helper",
                        "area_id": "office",
                        # no device_id, no platform
                    }
                ]
            ),
        ):
            await ha_module._fetch_entity_registry()

        entry = ha_module._entity_registry["virtual.helper"]
        assert entry.device_id is None
        assert entry.platform is None
        assert entry.area_id == "office"

    async def test_fetch_entity_registry_empty_string_area_id_stored_as_none(
        self, ha_module: HomeAssistantModule
    ) -> None:
        """Empty string area_id (unassigned in HA) is stored as None."""
        ha_module._ws_connected = True
        with patch.object(
            ha_module,
            "_ws_command",
            new=AsyncMock(
                return_value=[
                    {
                        "entity_id": "switch.unassigned",
                        "area_id": "",  # HA returns empty string for no area
                        "device_id": "dev1",
                        "platform": "hue",
                    }
                ]
            ),
        ):
            await ha_module._fetch_entity_registry()

        entry = ha_module._entity_registry["switch.unassigned"]
        assert entry.area_id is None
        # Entity without area should NOT appear in _entity_area_map
        assert "switch.unassigned" not in ha_module._entity_area_map

    async def test_fetch_entity_registry_skips_entries_without_entity_id(
        self, ha_module: HomeAssistantModule
    ) -> None:
        """Registry entries without entity_id are silently skipped."""
        ha_module._ws_connected = True
        with patch.object(
            ha_module,
            "_ws_command",
            new=AsyncMock(
                return_value=[
                    {"area_id": "kitchen", "platform": "zha"},  # missing entity_id
                    {"entity_id": "light.valid", "area_id": "kitchen", "platform": "zha"},
                ]
            ),
        ):
            await ha_module._fetch_entity_registry()

        assert len(ha_module._entity_registry) == 1
        assert "light.valid" in ha_module._entity_registry

    async def test_fetch_entity_registry_populates_area_map_from_registry(
        self, ha_module: HomeAssistantModule
    ) -> None:
        """_entity_area_map is derived from _entity_registry entries with area_id."""
        ha_module._ws_connected = True
        with patch.object(
            ha_module,
            "_ws_command",
            new=AsyncMock(
                return_value=[
                    {"entity_id": "light.a", "area_id": "kitchen", "platform": "zha"},
                    {"entity_id": "light.b", "platform": "zha"},  # no area
                ]
            ),
        ):
            await ha_module._fetch_entity_registry()

        assert ha_module._entity_area_map == {"light.a": "kitchen"}
        assert "light.b" not in ha_module._entity_area_map

    async def test_fetch_entity_registry_noop_when_disconnected(
        self, ha_module: HomeAssistantModule
    ) -> None:
        """_fetch_entity_registry is a no-op when WebSocket is disconnected."""
        ha_module._ws_connected = False
        with patch.object(ha_module, "_ws_command", new=AsyncMock()) as mock_cmd:
            await ha_module._fetch_entity_registry()
            mock_cmd.assert_not_awaited()
        assert ha_module._entity_registry == {}

    async def test_fetch_entity_registry_error_is_logged_not_raised(
        self, ha_module: HomeAssistantModule
    ) -> None:
        """_fetch_entity_registry swallows exceptions and logs a warning."""
        ha_module._ws_connected = True
        with patch.object(
            ha_module,
            "_ws_command",
            new=AsyncMock(side_effect=RuntimeError("WS timeout")),
        ):
            # Must not raise
            await ha_module._fetch_entity_registry()
        # Registry remains unchanged
        assert ha_module._entity_registry == {}


# ---------------------------------------------------------------------------
# WebSocket event subscriptions
# ---------------------------------------------------------------------------


class TestWebSocketSubscriptions:
    """Verify _ws_subscribe_events sends the correct subscription commands."""

    async def test_subscribe_events_sends_three_subscriptions(
        self, ha_module: HomeAssistantModule
    ) -> None:
        """_ws_subscribe_events sends subscribe_events for the three required event types."""
        ha_module._ws_connected = True
        sent_commands: list[dict[str, Any]] = []

        async def capture_command(cmd: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
            sent_commands.append(cmd)
            return {}

        with patch.object(ha_module, "_ws_command", side_effect=capture_command):
            await ha_module._ws_subscribe_events()

        event_types_subscribed = {
            cmd["event_type"] for cmd in sent_commands if cmd.get("type") == "subscribe_events"
        }
        assert "state_changed" in event_types_subscribed
        assert "area_registry_updated" in event_types_subscribed
        assert "entity_registry_updated" in event_types_subscribed
        assert len(sent_commands) == 3

    async def test_subscribe_events_noop_when_disconnected(
        self, ha_module: HomeAssistantModule
    ) -> None:
        """_ws_subscribe_events is a no-op when WebSocket is not connected."""
        ha_module._ws_connected = False
        with patch.object(ha_module, "_ws_command", new=AsyncMock()) as mock_cmd:
            await ha_module._ws_subscribe_events()
            mock_cmd.assert_not_awaited()

    async def test_subscribe_events_continues_on_partial_failure(
        self, ha_module: HomeAssistantModule
    ) -> None:
        """_ws_subscribe_events subscribes remaining events even if one fails."""
        ha_module._ws_connected = True
        call_count = 0

        async def failing_then_ok(cmd: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("subscription error")
            return {}

        with patch.object(ha_module, "_ws_command", side_effect=failing_then_ok):
            # Must not raise despite first subscription failing
            await ha_module._ws_subscribe_events()

        # All 3 were attempted despite the first failure
        assert call_count == 3


# ---------------------------------------------------------------------------
# _ws_connect_and_seed — integration
# ---------------------------------------------------------------------------


class TestWsConnectAndSeed:
    """Verify _ws_connect_and_seed orchestrates connect, seed, and subscribe correctly."""

    async def test_connect_and_seed_success_flow(self, ha_module: HomeAssistantModule) -> None:
        """On successful WS connect, seed entity cache, fetch registries, subscribe."""
        ha_module._config = HomeAssistantConfig(url="http://ha.local")
        ha_module._token = "tok"

        connect_called = []
        seed_called = []
        area_called = []
        entity_called = []
        subscribe_called = []
        message_loop_called = []
        ping_called = []

        async def mock_connect() -> None:
            connect_called.append(True)
            ha_module._ws_connected = True

        async def mock_seed() -> None:
            seed_called.append(True)

        async def mock_area() -> None:
            area_called.append(True)

        async def mock_entity() -> None:
            entity_called.append(True)

        async def mock_subscribe() -> None:
            subscribe_called.append(True)

        def mock_message_loop() -> None:
            message_loop_called.append(True)

        def mock_ping() -> None:
            ping_called.append(True)

        with (
            patch.object(ha_module, "_ws_connect", side_effect=mock_connect),
            patch.object(ha_module, "_seed_entity_cache_from_rest", side_effect=mock_seed),
            patch.object(ha_module, "_fetch_area_registry", side_effect=mock_area),
            patch.object(ha_module, "_fetch_entity_registry", side_effect=mock_entity),
            patch.object(ha_module, "_ws_subscribe_events", side_effect=mock_subscribe),
            patch.object(ha_module, "_start_ws_message_loop", side_effect=mock_message_loop),
            patch.object(ha_module, "_start_ws_ping_task", side_effect=mock_ping),
        ):
            await ha_module._ws_connect_and_seed()

        assert connect_called, "_ws_connect was not called"
        assert seed_called, "_seed_entity_cache_from_rest was not called"
        assert area_called, "_fetch_area_registry was not called"
        assert entity_called, "_fetch_entity_registry was not called"
        assert subscribe_called, "_ws_subscribe_events was not called"
        assert message_loop_called, "_start_ws_message_loop was not called"
        assert ping_called, "_start_ws_ping_task was not called"

    async def test_connect_and_seed_falls_back_on_ws_failure(
        self, ha_module: HomeAssistantModule
    ) -> None:
        """When WS connect fails, poll fallback and reconnect are scheduled."""
        ha_module._config = HomeAssistantConfig(url="http://ha.local")
        ha_module._token = "tok"
        ha_module._shutdown = False

        poll_started = []
        reconnect_scheduled = []

        def mock_poll() -> None:
            poll_started.append(True)

        def mock_reconnect(delay: float) -> None:
            reconnect_scheduled.append(delay)

        with (
            patch.object(ha_module, "_ws_connect", side_effect=ConnectionError("refused")),
            patch.object(ha_module, "_start_poll_fallback", side_effect=mock_poll),
            patch.object(ha_module, "_schedule_reconnect", side_effect=mock_reconnect),
        ):
            await ha_module._ws_connect_and_seed()

        assert ha_module._ws_connected is False
        assert poll_started, "_start_poll_fallback was not called on WS failure"
        assert reconnect_scheduled, "_schedule_reconnect was not called on WS failure"


# ---------------------------------------------------------------------------
# REST polling fallback — _poll_loop behavior
# ---------------------------------------------------------------------------


class TestPollLoopBehavior:
    """Verify _poll_loop replaces entity cache and stops when WS reconnects."""

    async def test_poll_loop_replaces_cache_on_each_cycle(
        self, ha_module: HomeAssistantModule
    ) -> None:
        """_poll_loop calls _seed_entity_cache_from_rest once per cycle."""
        ha_module._config = HomeAssistantConfig(url="http://ha.local", poll_interval_seconds=0)
        ha_module._ws_connected = False
        ha_module._shutdown = False

        seed_calls = []

        async def mock_seed() -> None:
            seed_calls.append(True)
            # Stop the loop after first cycle by marking WS as connected
            ha_module._ws_connected = True

        with patch.object(ha_module, "_seed_entity_cache_from_rest", side_effect=mock_seed):
            await ha_module._poll_loop()

        assert len(seed_calls) == 1, "Expected exactly one poll cycle before WS reconnected"

    async def test_poll_loop_stops_when_ws_reconnects(self, ha_module: HomeAssistantModule) -> None:
        """_poll_loop exits when _ws_connected becomes True."""
        ha_module._config = HomeAssistantConfig(url="http://ha.local", poll_interval_seconds=0)
        ha_module._ws_connected = False
        ha_module._shutdown = False
        call_count = 0

        async def mock_seed() -> None:
            nonlocal call_count
            call_count += 1
            ha_module._ws_connected = True  # simulates WS reconnect

        with patch.object(ha_module, "_seed_entity_cache_from_rest", side_effect=mock_seed):
            await ha_module._poll_loop()

        # Loop should have exited after WS reconnected — not called more than once
        assert call_count <= 1

    async def test_poll_loop_stops_on_shutdown(self, ha_module: HomeAssistantModule) -> None:
        """_poll_loop exits immediately when _shutdown is True."""
        ha_module._config = HomeAssistantConfig(url="http://ha.local", poll_interval_seconds=0)
        ha_module._ws_connected = False
        ha_module._shutdown = True

        seed_calls = []

        async def mock_seed() -> None:
            seed_calls.append(True)

        with patch.object(ha_module, "_seed_entity_cache_from_rest", side_effect=mock_seed):
            await ha_module._poll_loop()

        # Shutdown flag set before sleep — loop should not call seed at all
        assert len(seed_calls) == 0

    async def test_poll_loop_continues_after_seed_error(
        self, ha_module: HomeAssistantModule
    ) -> None:
        """_poll_loop catches seed errors and continues polling."""
        ha_module._config = HomeAssistantConfig(url="http://ha.local", poll_interval_seconds=0)
        ha_module._ws_connected = False
        ha_module._shutdown = False
        call_count = 0

        async def mock_seed_with_error() -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("HA REST unavailable")
            # Second call: stop the loop
            ha_module._ws_connected = True

        with patch.object(
            ha_module, "_seed_entity_cache_from_rest", side_effect=mock_seed_with_error
        ):
            await ha_module._poll_loop()

        assert call_count == 2, "Poll loop should have retried after error"


# ---------------------------------------------------------------------------
# _list_areas — area registry query tool
# ---------------------------------------------------------------------------


class TestListAreas:
    """Verify _list_areas returns sorted area list from the cache."""

    async def test_list_areas_sorted_by_name(self, ha_module: HomeAssistantModule) -> None:
        """_list_areas returns areas sorted alphabetically by name."""
        ha_module._area_cache = {
            "kitchen": CachedArea(area_id="kitchen", name="Kitchen"),
            "attic": CachedArea(area_id="attic", name="Attic"),
            "bedroom": CachedArea(area_id="bedroom", name="Bedroom"),
        }

        result = await ha_module._list_areas()

        names = [a["name"] for a in result]
        assert names == sorted(names)
        assert names == ["Attic", "Bedroom", "Kitchen"]

    async def test_list_areas_empty_cache_returns_empty(
        self, ha_module: HomeAssistantModule
    ) -> None:
        """_list_areas returns an empty list when the area cache is empty."""
        ha_module._area_cache = {}

        result = await ha_module._list_areas()

        assert result == []

    async def test_list_areas_includes_area_id_and_name(
        self, ha_module: HomeAssistantModule
    ) -> None:
        """Each area in the result has area_id and name keys."""
        ha_module._area_cache = {
            "living_room": CachedArea(area_id="living_room", name="Living Room"),
        }

        result = await ha_module._list_areas()

        assert len(result) == 1
        assert result[0]["area_id"] == "living_room"
        assert result[0]["name"] == "Living Room"


# ---------------------------------------------------------------------------
# _list_services — service catalog tool
# ---------------------------------------------------------------------------


class TestListServices:
    """Verify _list_services queries REST and applies optional domain filter."""

    async def test_list_services_uses_rest_client(self, ha_module: HomeAssistantModule) -> None:
        """_list_services calls GET /api/services via the REST client."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = [
            {"domain": "light", "services": {"turn_on": {}, "turn_off": {}}},
            {"domain": "switch", "services": {"turn_on": {}, "turn_off": {}}},
        ]
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        ha_module._client = mock_client

        result = await ha_module._list_services()

        mock_client.get.assert_awaited_once_with("/api/services")
        assert len(result) == 2

    async def test_list_services_domain_filter(self, ha_module: HomeAssistantModule) -> None:
        """_list_services filters by domain when domain param is provided."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = [
            {"domain": "light", "services": {"turn_on": {}}},
            {"domain": "switch", "services": {"turn_on": {}}},
        ]
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        ha_module._client = mock_client

        result = await ha_module._list_services(domain="light")

        assert len(result) == 1
        assert result[0]["domain"] == "light"

    async def test_list_services_falls_back_to_ws(self, ha_module: HomeAssistantModule) -> None:
        """_list_services uses WebSocket get_services when REST client is unavailable."""
        ha_module._client = None
        ha_module._ws_connected = True

        ws_result = {
            "light": {"turn_on": {}, "turn_off": {}},
            "switch": {"turn_on": {}},
        }
        with patch.object(
            ha_module, "_ws_command", new=AsyncMock(return_value=ws_result)
        ) as mock_cmd:
            result = await ha_module._list_services()

        mock_cmd.assert_awaited_once()
        sent_cmd = mock_cmd.call_args.args[0]
        assert sent_cmd["type"] == "get_services"
        assert len(result) == 2
        domains = {entry["domain"] for entry in result}
        assert domains == {"light", "switch"}

    async def test_list_services_raises_when_no_client_and_no_ws(
        self, ha_module: HomeAssistantModule
    ) -> None:
        """_list_services raises RuntimeError when neither REST nor WebSocket is available."""
        ha_module._client = None
        ha_module._ws_connected = False

        with pytest.raises(RuntimeError, match="cannot list services"):
            await ha_module._list_services()

    async def test_list_services_domain_filter_with_ws_fallback(
        self, ha_module: HomeAssistantModule
    ) -> None:
        """Domain filter still applies when using the WebSocket fallback path."""
        ha_module._client = None
        ha_module._ws_connected = True

        ws_result = {
            "light": {"turn_on": {}},
            "switch": {"turn_on": {}},
        }
        with patch.object(ha_module, "_ws_command", new=AsyncMock(return_value=ws_result)):
            result = await ha_module._list_services(domain="switch")

        assert len(result) == 1
        assert result[0]["domain"] == "switch"


# ---------------------------------------------------------------------------
# _get_history — history API tool
# ---------------------------------------------------------------------------


class TestGetHistory:
    """Verify _get_history constructs the correct REST request."""

    async def test_get_history_calls_rest(self, ha_module: HomeAssistantModule) -> None:
        """_get_history calls GET /api/history/period/<start> with correct params."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = [[{"state": "on", "last_changed": "2026-02-01T00:00:00Z"}]]
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        ha_module._client = mock_client

        result = await ha_module._get_history(
            entity_ids=["light.kitchen"],
            start="2026-02-01T00:00:00Z",
        )

        mock_client.get.assert_awaited_once()
        call_args = mock_client.get.call_args
        assert "/api/history/period/2026-02-01T00:00:00Z" in call_args.args[0]
        params = call_args.kwargs.get("params", {})
        assert "light.kitchen" in params["filter_entity_id"]
        assert "minimal_response" in params
        assert "significant_changes_only" in params
        assert result == [[{"state": "on", "last_changed": "2026-02-01T00:00:00Z"}]]

    async def test_get_history_with_end_time(self, ha_module: HomeAssistantModule) -> None:
        """_get_history includes end_time parameter when end is provided."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = []
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        ha_module._client = mock_client

        await ha_module._get_history(
            entity_ids=["sensor.temp"],
            start="2026-02-01T00:00:00Z",
            end="2026-02-02T00:00:00Z",
        )

        call_args = mock_client.get.call_args
        params = call_args.kwargs.get("params", {})
        assert params.get("end_time") == "2026-02-02T00:00:00Z"

    async def test_get_history_raises_for_empty_entity_ids(
        self, ha_module: HomeAssistantModule
    ) -> None:
        """_get_history raises ValueError when entity_ids is empty."""
        ha_module._client = AsyncMock()

        with pytest.raises(ValueError, match="at least one entity_id"):
            await ha_module._get_history(entity_ids=[], start="2026-02-01T00:00:00Z")

    async def test_get_history_multiple_entity_ids(self, ha_module: HomeAssistantModule) -> None:
        """_get_history includes all entity IDs comma-separated in filter_entity_id."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = [[], []]
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        ha_module._client = mock_client

        await ha_module._get_history(
            entity_ids=["sensor.a", "sensor.b"],
            start="2026-02-01T00:00:00Z",
        )

        params = mock_client.get.call_args.kwargs.get("params", {})
        assert params["filter_entity_id"] == "sensor.a,sensor.b"

    async def test_get_history_rejects_path_traversal_start(
        self, ha_module: HomeAssistantModule
    ) -> None:
        """_get_history raises ValueError for start values containing path traversal."""
        ha_module._client = AsyncMock()

        with pytest.raises(ValueError, match="ISO 8601"):
            await ha_module._get_history(
                entity_ids=["sensor.temp"],
                start="../../../api/config",
            )

    async def test_get_history_rejects_invalid_iso8601_start(
        self, ha_module: HomeAssistantModule
    ) -> None:
        """_get_history raises ValueError for start values that are not ISO 8601."""
        ha_module._client = AsyncMock()

        with pytest.raises(ValueError, match="ISO 8601"):
            await ha_module._get_history(
                entity_ids=["sensor.temp"],
                start="not-a-timestamp",
            )

    async def test_get_history_url_encodes_plus_timezone(
        self, ha_module: HomeAssistantModule
    ) -> None:
        """_get_history percent-encodes + in timezone offsets so the URL path is valid."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = []
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        ha_module._client = mock_client

        await ha_module._get_history(
            entity_ids=["sensor.temp"],
            start="2026-02-01T00:00:00+01:00",
        )

        call_args = mock_client.get.call_args
        url_path = call_args.args[0]
        # + must be percent-encoded as %2B in the URL path segment
        assert "%2B" in url_path, f"Expected %2B in URL path, got: {url_path}"
        assert "+" not in url_path, f"Unencoded + found in URL path: {url_path}"


# ---------------------------------------------------------------------------
# _get_statistics — recorder statistics tool
# ---------------------------------------------------------------------------


_VALID_PERIODS = ["5minute", "hour", "day", "week", "month"]


class TestGetStatistics:
    """Verify _get_statistics sends the correct WebSocket command."""

    async def test_get_statistics_sends_ws_command(self, ha_module: HomeAssistantModule) -> None:
        """_get_statistics sends recorder/get_statistics_during_period WS command."""
        expected_result = {"sensor.energy": [{"mean": 1.5, "sum": 36.0}]}
        ha_module._ws_connected = True

        with patch.object(
            ha_module, "_ws_command", new=AsyncMock(return_value=expected_result)
        ) as mock_cmd:
            result = await ha_module._get_statistics(
                statistic_ids=["sensor.energy"],
                start="2026-02-01T00:00:00Z",
                end="2026-02-28T00:00:00Z",
                period="day",
            )

        mock_cmd.assert_awaited_once()
        sent = mock_cmd.call_args.args[0]
        assert sent["type"] == "recorder/get_statistics_during_period"
        assert sent["statistic_ids"] == ["sensor.energy"]
        assert sent["start_time"] == "2026-02-01T00:00:00Z"
        assert sent["end_time"] == "2026-02-28T00:00:00Z"
        assert sent["period"] == "day"
        assert result == expected_result

    async def test_get_statistics_default_period_is_hour(
        self, ha_module: HomeAssistantModule
    ) -> None:
        """Default period is 'hour' when not specified."""
        ha_module._ws_connected = True

        with patch.object(ha_module, "_ws_command", new=AsyncMock(return_value={})) as mock_cmd:
            await ha_module._get_statistics(
                statistic_ids=["sensor.temp"],
                start="2026-02-01T00:00:00Z",
                end="2026-02-02T00:00:00Z",
            )

        sent = mock_cmd.call_args.args[0]
        assert sent["period"] == "hour"

    @pytest.mark.parametrize("period", _VALID_PERIODS)
    async def test_get_statistics_valid_periods(
        self, ha_module: HomeAssistantModule, period: str
    ) -> None:
        """_get_statistics accepts all valid period values without raising."""
        ha_module._ws_connected = True

        with patch.object(ha_module, "_ws_command", new=AsyncMock(return_value={})):
            # Must not raise
            await ha_module._get_statistics(
                statistic_ids=["sensor.temp"],
                start="2026-02-01T00:00:00Z",
                end="2026-02-02T00:00:00Z",
                period=period,
            )

    async def test_get_statistics_invalid_period_raises(
        self, ha_module: HomeAssistantModule
    ) -> None:
        """_get_statistics raises ValueError for invalid period values."""
        with pytest.raises(ValueError, match="Invalid period"):
            await ha_module._get_statistics(
                statistic_ids=["sensor.temp"],
                start="2026-02-01T00:00:00Z",
                end="2026-02-02T00:00:00Z",
                period="yearly",
            )

    async def test_get_statistics_includes_types(self, ha_module: HomeAssistantModule) -> None:
        """_get_statistics command includes the expected types list."""
        ha_module._ws_connected = True

        with patch.object(ha_module, "_ws_command", new=AsyncMock(return_value={})) as mock_cmd:
            await ha_module._get_statistics(
                statistic_ids=["sensor.energy"],
                start="2026-02-01T00:00:00Z",
                end="2026-02-02T00:00:00Z",
                period="hour",
            )

        sent = mock_cmd.call_args.args[0]
        assert set(sent["types"]) == {"mean", "min", "max", "sum", "state"}


# ---------------------------------------------------------------------------
# _render_template — template rendering tool
# ---------------------------------------------------------------------------


class TestRenderTemplate:
    """Verify _render_template calls POST /api/template and returns rendered text."""

    async def test_render_template_calls_rest(self, ha_module: HomeAssistantModule) -> None:
        """_render_template calls POST /api/template with the template body."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.text = "23.5 °C"
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        ha_module._client = mock_client

        result = await ha_module._render_template(template="{{ states('sensor.temperature') }} °C")

        mock_client.post.assert_awaited_once()
        call_args = mock_client.post.call_args
        assert call_args.args[0] == "/api/template"
        body = call_args.kwargs.get("json", {})
        assert body["template"] == "{{ states('sensor.temperature') }} °C"
        assert result == "23.5 °C"

    async def test_render_template_returns_string(self, ha_module: HomeAssistantModule) -> None:
        """_render_template returns the response text as a plain string."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.text = "Hello World"
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        ha_module._client = mock_client

        result = await ha_module._render_template(template="Hello World")

        assert isinstance(result, str)
        assert result == "Hello World"

    async def test_render_template_raises_without_client(
        self, ha_module: HomeAssistantModule
    ) -> None:
        """_render_template raises RuntimeError when the HTTP client is not initialized."""
        ha_module._client = None

        with pytest.raises(RuntimeError, match="not initialised"):
            await ha_module._render_template(template="{{ now() }}")


# ---------------------------------------------------------------------------
# Query tools registered via MCP — integration smoke tests
# ---------------------------------------------------------------------------


class TestQueryToolsMcpRegistration:
    """Verify query tools are callable through the MCP registration layer."""

    async def test_ha_list_areas_callable_via_mcp(
        self, ha_module: HomeAssistantModule, mock_mcp: MagicMock
    ) -> None:
        """ha_list_areas registered tool delegates to _list_areas."""
        await ha_module.register_tools(mcp=mock_mcp, config={"url": "http://ha.local"}, db=None)
        ha_module._area_cache = {
            "kitchen": CachedArea(area_id="kitchen", name="Kitchen"),
        }

        tool_fn = mock_mcp._registered_tools["ha_list_areas"]
        result = await tool_fn()

        assert len(result) == 1
        assert result[0]["area_id"] == "kitchen"

    async def test_ha_list_services_callable_via_mcp(
        self, ha_module: HomeAssistantModule, mock_mcp: MagicMock
    ) -> None:
        """ha_list_services registered tool delegates to _list_services."""
        await ha_module.register_tools(mcp=mock_mcp, config={"url": "http://ha.local"}, db=None)
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = [{"domain": "light", "services": {}}]
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        ha_module._client = mock_client

        tool_fn = mock_mcp._registered_tools["ha_list_services"]
        result = await tool_fn()

        assert len(result) == 1
        assert result[0]["domain"] == "light"

    async def test_ha_get_history_callable_via_mcp(
        self, ha_module: HomeAssistantModule, mock_mcp: MagicMock
    ) -> None:
        """ha_get_history registered tool delegates to _get_history."""
        await ha_module.register_tools(mcp=mock_mcp, config={"url": "http://ha.local"}, db=None)
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = [[{"state": "on"}]]
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        ha_module._client = mock_client

        tool_fn = mock_mcp._registered_tools["ha_get_history"]
        result = await tool_fn(entity_ids=["light.kitchen"], start="2026-02-01T00:00:00Z")

        assert result == [[{"state": "on"}]]

    async def test_ha_get_statistics_callable_via_mcp(
        self, ha_module: HomeAssistantModule, mock_mcp: MagicMock
    ) -> None:
        """ha_get_statistics registered tool delegates to _get_statistics."""
        await ha_module.register_tools(mcp=mock_mcp, config={"url": "http://ha.local"}, db=None)
        ha_module._ws_connected = True
        expected = {"sensor.energy": []}

        with patch.object(ha_module, "_ws_command", new=AsyncMock(return_value=expected)):
            tool_fn = mock_mcp._registered_tools["ha_get_statistics"]
            result = await tool_fn(
                statistic_ids=["sensor.energy"],
                start="2026-02-01T00:00:00Z",
                end="2026-02-28T00:00:00Z",
            )

        assert result == expected

    async def test_ha_render_template_callable_via_mcp(
        self, ha_module: HomeAssistantModule, mock_mcp: MagicMock
    ) -> None:
        """ha_render_template registered tool delegates to _render_template."""
        await ha_module.register_tools(mcp=mock_mcp, config={"url": "http://ha.local"}, db=None)
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.text = "rendered"
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        ha_module._client = mock_client

        tool_fn = mock_mcp._registered_tools["ha_render_template"]
        result = await tool_fn(template="{{ now() }}")

        assert result == "rendered"
