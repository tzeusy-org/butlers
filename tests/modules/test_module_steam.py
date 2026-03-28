"""Tests for the Steam module (9 read-only MCP tools)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel

from butlers.modules.base import Module
from butlers.modules.steam import (
    SteamModule,
    SteamModuleConfig,
    _handle_steam_error,
    _no_achievements_error,
    _no_credentials_error,
    _privacy_error,
    _rate_limited_error,
)
from butlers.steam.client import SteamAPIError, SteamRateLimitError

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Expected tool names (9 total)
# ---------------------------------------------------------------------------

EXPECTED_STEAM_TOOLS = {
    "steam_get_player_summary",
    "steam_get_owned_games",
    "steam_get_recently_played",
    "steam_get_achievements",
    "steam_get_friend_list",
    "steam_get_game_news",
    "steam_get_player_level",
    "steam_get_current_players",
    "steam_resolve_vanity_url",
}

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def steam_module() -> SteamModule:
    """Fresh SteamModule instance."""
    return SteamModule()


@pytest.fixture
def mock_mcp() -> MagicMock:
    """Mock MCP server that captures registered tools."""
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
def mock_steam_client() -> MagicMock:
    """Mock SteamAPIClient with sensible default responses."""
    client = MagicMock()
    client.open = AsyncMock(return_value=None)
    client.close = AsyncMock(return_value=None)
    client.get_player_summaries = AsyncMock(
        return_value=[
            {
                "steamid": "76561198000000001",
                "personaname": "Test Player",
                "profileurl": "https://steamcommunity.com/id/testplayer/",
                "avatar": "https://cdn.cloudflare.steamstatic.com/steamcommunity/public/images/avatars/test.jpg",
                "communityvisibilitystate": 3,
                "personastate": 1,
            }
        ]
    )
    client.request = AsyncMock(return_value={})
    return client


@pytest.fixture
async def connected_module(
    steam_module: SteamModule,
    mock_steam_client: MagicMock,
    mock_mcp: MagicMock,
) -> SteamModule:
    """SteamModule with mocked client in connected state."""
    steam_module._client = mock_steam_client
    steam_module._credentials_ok = True
    steam_module._primary_steam_id = "76561198000000001"
    await steam_module.register_tools(mcp=mock_mcp, config={}, db=None)
    return steam_module


# ---------------------------------------------------------------------------
# Module ABC compliance
# ---------------------------------------------------------------------------


class TestModuleABCCompliance:
    """Verify SteamModule implements the Module ABC correctly."""

    def test_is_module_subclass(self):
        assert issubclass(SteamModule, Module)

    def test_instantiates(self, steam_module: SteamModule):
        assert steam_module is not None

    def test_name(self, steam_module: SteamModule):
        assert steam_module.name == "steam"

    def test_config_schema(self, steam_module: SteamModule):
        assert steam_module.config_schema is SteamModuleConfig
        assert issubclass(steam_module.config_schema, BaseModel)

    def test_dependencies_empty(self, steam_module: SteamModule):
        assert steam_module.dependencies == []

    def test_migration_revisions_none(self, steam_module: SteamModule):
        assert steam_module.migration_revisions() is None

    def test_isinstance_check(self, steam_module: SteamModule):
        assert isinstance(steam_module, Module)


# ---------------------------------------------------------------------------
# SteamModuleConfig
# ---------------------------------------------------------------------------


class TestSteamModuleConfig:
    def test_defaults(self):
        config = SteamModuleConfig()
        assert config is not None

    def test_default_account_is_none(self):
        config = SteamModuleConfig()
        assert config.default_account is None

    def test_cache_ttl_seconds_default(self):
        config = SteamModuleConfig()
        assert config.cache_ttl_seconds == 300

    def test_max_batch_size_default(self):
        config = SteamModuleConfig()
        assert config.max_batch_size == 100

    def test_default_account_accepts_steam_id(self):
        config = SteamModuleConfig(default_account="76561198000000001")
        assert config.default_account == "76561198000000001"

    def test_default_account_accepts_uuid_string(self):
        import uuid

        uid = str(uuid.uuid4())
        config = SteamModuleConfig(default_account=uid)
        assert config.default_account == uid

    def test_cache_ttl_seconds_custom(self):
        config = SteamModuleConfig(cache_ttl_seconds=60)
        assert config.cache_ttl_seconds == 60

    def test_cache_ttl_seconds_zero_disables_cache(self):
        config = SteamModuleConfig(cache_ttl_seconds=0)
        assert config.cache_ttl_seconds == 0

    def test_max_batch_size_custom(self):
        config = SteamModuleConfig(max_batch_size=50)
        assert config.max_batch_size == 50

    def test_extra_fields_rejected(self):
        with pytest.raises(Exception):
            SteamModuleConfig(**{"unknown_key": "value"})

    def test_from_empty_dict(self):
        config = SteamModuleConfig(**{})
        assert isinstance(config, SteamModuleConfig)


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


class TestToolRegistration:
    """Verify register_tools registers exactly the 9 expected MCP tools."""

    async def test_registers_all_9_tools(self, steam_module: SteamModule, mock_mcp: MagicMock):
        await steam_module.register_tools(mcp=mock_mcp, config={}, db=None)
        assert set(mock_mcp._registered_tools.keys()) == EXPECTED_STEAM_TOOLS

    async def test_tool_count_is_9(self, steam_module: SteamModule, mock_mcp: MagicMock):
        await steam_module.register_tools(mcp=mock_mcp, config={}, db=None)
        assert len(mock_mcp._registered_tools) == 9

    async def test_all_tools_are_callable(self, steam_module: SteamModule, mock_mcp: MagicMock):
        await steam_module.register_tools(mcp=mock_mcp, config={}, db=None)
        for name, fn in mock_mcp._registered_tools.items():
            assert callable(fn), f"{name} should be callable"


# ---------------------------------------------------------------------------
# Tool metadata (sensitivity)
# ---------------------------------------------------------------------------


class TestToolMetadata:
    def test_all_tools_are_read_only(self, steam_module: SteamModule):
        meta = steam_module.tool_metadata()
        for tool_name in EXPECTED_STEAM_TOOLS:
            assert tool_name in meta, f"{tool_name} missing from tool_metadata"
            assert meta[tool_name].arg_sensitivities.get("_write") is False

    def test_metadata_count(self, steam_module: SteamModule):
        assert len(steam_module.tool_metadata()) == 9


# ---------------------------------------------------------------------------
# No-credentials guard (all tools)
# ---------------------------------------------------------------------------


class TestNoCredentials:
    """All tools return structured error when no startup has run (no clients at all)."""

    async def test_private_tools_return_error_when_no_credentials(
        self, steam_module: SteamModule, mock_mcp: MagicMock
    ):
        # Module has not been started up — both _client and _public_client are None.
        await steam_module.register_tools(mcp=mock_mcp, config={}, db=None)
        tools = mock_mcp._registered_tools

        # Private (account-specific) tools should return a credentials error.
        no_creds = _no_credentials_error()

        result = await tools["steam_get_player_summary"]()
        assert result == no_creds

        result = await tools["steam_get_owned_games"]()
        assert result == no_creds

        result = await tools["steam_get_recently_played"]()
        assert result == no_creds

        result = await tools["steam_get_achievements"](app_id=730)
        assert result == no_creds

        result = await tools["steam_get_friend_list"]()
        assert result == no_creds

        result = await tools["steam_get_player_level"]()
        assert result == no_creds

        result = await tools["steam_resolve_vanity_url"](vanity_url="gaben")
        assert result == no_creds

    async def test_public_tools_return_error_when_no_clients(
        self, steam_module: SteamModule, mock_mcp: MagicMock
    ):
        """Public endpoints also fail before on_startup (no public client yet)."""
        await steam_module.register_tools(mcp=mock_mcp, config={}, db=None)
        tools = mock_mcp._registered_tools
        no_creds = _no_credentials_error()

        result = await tools["steam_get_game_news"](app_id=730)
        assert result == no_creds

        result = await tools["steam_get_current_players"](app_id=730)
        assert result == no_creds


class TestPublicEndpointsWithoutCredentials:
    """Public endpoints work in degraded mode (public_client available, no credentials)."""

    async def test_game_news_works_with_public_client(
        self, steam_module: SteamModule, mock_mcp: MagicMock, mock_steam_client: MagicMock
    ):
        """steam_get_game_news uses the public client when no authenticated client is set."""
        steam_module._public_client = mock_steam_client
        mock_steam_client.request = AsyncMock(
            return_value={"appnews": {"appid": 730, "newsitems": []}}
        )
        await steam_module.register_tools(mcp=mock_mcp, config={}, db=None)
        tools = mock_mcp._registered_tools

        result = await tools["steam_get_game_news"](app_id=730)
        assert "appnews" in result

    async def test_current_players_works_with_public_client(
        self, steam_module: SteamModule, mock_mcp: MagicMock, mock_steam_client: MagicMock
    ):
        """steam_get_current_players uses the public client when no authenticated client is set."""
        steam_module._public_client = mock_steam_client
        mock_steam_client.request = AsyncMock(return_value={"player_count": 12345})
        await steam_module.register_tools(mcp=mock_mcp, config={}, db=None)
        tools = mock_mcp._registered_tools

        result = await tools["steam_get_current_players"](app_id=730)
        assert result["player_count"] == 12345

    async def test_private_tools_still_fail_without_credentials(
        self, steam_module: SteamModule, mock_mcp: MagicMock, mock_steam_client: MagicMock
    ):
        """Private tools still return no_credentials_error even if public_client is set."""
        steam_module._public_client = mock_steam_client
        await steam_module.register_tools(mcp=mock_mcp, config={}, db=None)
        tools = mock_mcp._registered_tools

        no_creds = _no_credentials_error()
        result = await tools["steam_get_player_summary"]()
        assert result == no_creds


# ---------------------------------------------------------------------------
# Startup lifecycle
# ---------------------------------------------------------------------------


class TestStartup:
    async def test_startup_no_db_sets_not_connected(
        self, steam_module: SteamModule, mock_steam_client: MagicMock
    ):
        with patch("butlers.modules.steam.SteamAPIClient", return_value=mock_steam_client):
            await steam_module.on_startup(config={}, db=None)
        assert not steam_module._credentials_ok
        # Public client is still created even without a DB.
        assert steam_module._public_client is mock_steam_client

    async def test_startup_no_pool_sets_not_connected(
        self, steam_module: SteamModule, mock_steam_client: MagicMock
    ):
        db = MagicMock()
        db.pool = None
        with patch("butlers.modules.steam.SteamAPIClient", return_value=mock_steam_client):
            await steam_module.on_startup(config={}, db=db)
        assert not steam_module._credentials_ok
        assert steam_module._public_client is mock_steam_client

    async def test_startup_missing_primary_account_graceful(
        self, steam_module: SteamModule, mock_steam_client: MagicMock
    ):
        from butlers.steam_account_registry import MissingSteamCredentialsError

        db = MagicMock()
        db.pool = MagicMock()

        with (
            patch("butlers.modules.steam.SteamAPIClient", return_value=mock_steam_client),
            patch(
                "butlers.modules.steam.resolve_steam_account",
                AsyncMock(side_effect=MissingSteamCredentialsError("no primary")),
            ),
        ):
            await steam_module.on_startup(config={}, db=db)

        assert not steam_module._credentials_ok
        assert steam_module._public_client is mock_steam_client

    async def test_startup_no_api_key_graceful(
        self, steam_module: SteamModule, mock_steam_client: MagicMock
    ):
        import uuid
        from datetime import UTC, datetime

        from butlers.steam_account_registry import SteamAccount

        dummy_account = SteamAccount(
            id=uuid.uuid4(),
            entity_id=uuid.uuid4(),
            steam_id=76561198000000001,
            display_name="Test",
            profile_url=None,
            avatar_url=None,
            is_primary=True,
            status="active",
            connected_at=datetime.now(UTC),
            last_poll_at=None,
            metadata={},
        )

        db = MagicMock()
        db.pool = MagicMock()

        with (
            patch("butlers.modules.steam.SteamAPIClient", return_value=mock_steam_client),
            patch(
                "butlers.modules.steam.resolve_steam_account",
                AsyncMock(return_value=dummy_account),
            ),
            patch(
                "butlers.modules.steam._fetch_api_key",
                AsyncMock(return_value=None),
            ),
        ):
            await steam_module.on_startup(config={}, db=db)

        assert not steam_module._credentials_ok

    async def test_startup_success(self, steam_module: SteamModule, mock_steam_client: MagicMock):
        import uuid
        from datetime import UTC, datetime

        from butlers.steam_account_registry import SteamAccount

        dummy_account = SteamAccount(
            id=uuid.uuid4(),
            entity_id=uuid.uuid4(),
            steam_id=76561198000000001,
            display_name="Test Player",
            profile_url=None,
            avatar_url=None,
            is_primary=True,
            status="active",
            connected_at=datetime.now(UTC),
            last_poll_at=None,
            metadata={},
        )

        db = MagicMock()
        db.pool = MagicMock()

        with (
            patch(
                "butlers.modules.steam.resolve_steam_account",
                AsyncMock(return_value=dummy_account),
            ),
            patch(
                "butlers.modules.steam._fetch_api_key",
                AsyncMock(return_value="FAKE_API_KEY"),
            ),
            patch(
                "butlers.modules.steam.SteamAPIClient",
                return_value=mock_steam_client,
            ),
        ):
            await steam_module.on_startup(config={}, db=db)

        assert steam_module._credentials_ok
        assert steam_module._primary_steam_id == "76561198000000001"

    async def test_shutdown_closes_client(
        self, steam_module: SteamModule, mock_steam_client: MagicMock
    ):
        steam_module._client = mock_steam_client
        steam_module._credentials_ok = True
        await steam_module.on_shutdown()
        mock_steam_client.close.assert_called_once()
        assert steam_module._client is None

    async def test_shutdown_closes_public_client(
        self, steam_module: SteamModule, mock_steam_client: MagicMock
    ):
        """on_shutdown must close the public client even when no authenticated client is set."""
        steam_module._public_client = mock_steam_client
        await steam_module.on_shutdown()
        mock_steam_client.close.assert_called_once()
        assert steam_module._public_client is None

    async def test_shutdown_noop_when_not_connected(self, steam_module: SteamModule):
        # Should not raise
        await steam_module.on_shutdown()


# ---------------------------------------------------------------------------
# Config wiring: default_account, cache_ttl_seconds, max_batch_size
# ---------------------------------------------------------------------------


class TestConfigWiring:
    """Verify that SteamModuleConfig fields are wired into SteamModule logic."""

    # --- default_account wired into _resolve_steam_id ---

    def test_resolve_steam_id_uses_explicit_id(self, steam_module: SteamModule):
        """Explicit steam_id always wins over default_account and primary."""
        steam_module._default_account = "76561198000000002"
        steam_module._primary_steam_id = "76561198000000003"
        assert steam_module._resolve_steam_id("76561198000000001") == "76561198000000001"

    def test_resolve_steam_id_falls_back_to_default_account(self, steam_module: SteamModule):
        """When no explicit steam_id, default_account is used before primary."""
        steam_module._default_account = "76561198000000002"
        steam_module._primary_steam_id = "76561198000000003"
        assert steam_module._resolve_steam_id(None) == "76561198000000002"

    def test_resolve_steam_id_falls_back_to_primary_when_no_default(
        self, steam_module: SteamModule
    ):
        """When default_account is None and no explicit id, primary is used."""
        steam_module._default_account = None
        steam_module._primary_steam_id = "76561198000000003"
        assert steam_module._resolve_steam_id(None) == "76561198000000003"

    def test_resolve_steam_id_returns_none_when_no_ids(self, steam_module: SteamModule):
        """Returns None when all sources are unset."""
        steam_module._default_account = None
        steam_module._primary_steam_id = None
        assert steam_module._resolve_steam_id(None) is None

    async def test_startup_wires_default_account(
        self, steam_module: SteamModule, mock_steam_client: MagicMock
    ):
        """on_startup extracts default_account from config and stores it on the module."""
        with patch("butlers.modules.steam.SteamAPIClient", return_value=mock_steam_client):
            await steam_module.on_startup(
                config=SteamModuleConfig(default_account="76561198000000007"),
                db=None,
            )
        assert steam_module._default_account == "76561198000000007"

    async def test_startup_default_account_from_dict_config(
        self, steam_module: SteamModule, mock_steam_client: MagicMock
    ):
        """on_startup also parses default_account when config is a plain dict."""
        with patch("butlers.modules.steam.SteamAPIClient", return_value=mock_steam_client):
            await steam_module.on_startup(
                config={"default_account": "76561198000000099"},
                db=None,
            )
        assert steam_module._default_account == "76561198000000099"

    # --- cache_ttl_seconds passed to SteamAPIClient ---

    async def test_startup_passes_cache_ttl_to_client(
        self, steam_module: SteamModule, mock_steam_client: MagicMock
    ):
        """on_startup passes cache_ttl_seconds as cache_ttl_s to SteamAPIClient."""
        with patch(
            "butlers.modules.steam.SteamAPIClient", return_value=mock_steam_client
        ) as MockClient:
            await steam_module.on_startup(
                config=SteamModuleConfig(cache_ttl_seconds=60),
                db=None,
            )
        MockClient.assert_called_once_with(api_key="", cache_ttl_s=60.0)

    async def test_startup_passes_zero_cache_ttl_to_client(
        self, steam_module: SteamModule, mock_steam_client: MagicMock
    ):
        """cache_ttl_seconds=0 disables caching — passed through as 0.0."""
        with patch(
            "butlers.modules.steam.SteamAPIClient", return_value=mock_steam_client
        ) as MockClient:
            await steam_module.on_startup(
                config=SteamModuleConfig(cache_ttl_seconds=0),
                db=None,
            )
        MockClient.assert_called_once_with(api_key="", cache_ttl_s=0.0)

    # --- max_batch_size replaces _FRIEND_ENRICH_BATCH in steam_get_friend_list ---

    async def test_startup_wires_max_batch_size(
        self, steam_module: SteamModule, mock_steam_client: MagicMock
    ):
        """on_startup stores max_batch_size from config on the module."""
        with patch("butlers.modules.steam.SteamAPIClient", return_value=mock_steam_client):
            await steam_module.on_startup(
                config=SteamModuleConfig(max_batch_size=50),
                db=None,
            )
        assert steam_module._max_batch_size == 50

    async def test_friend_list_enrich_uses_max_batch_size(
        self,
        steam_module: SteamModule,
        mock_mcp: MagicMock,
        mock_steam_client: MagicMock,
    ):
        """steam_get_friend_list respects max_batch_size when batching enrich calls."""
        # 30 friends with max_batch_size=10 → 3 batch calls
        steam_module._client = mock_steam_client
        steam_module._credentials_ok = True
        steam_module._primary_steam_id = "76561198000000001"
        steam_module._max_batch_size = 10

        friends = [
            {"steamid": f"76561198000000{i:03d}", "relationship": "friend"} for i in range(30)
        ]
        mock_steam_client.request = AsyncMock(return_value={"friendslist": {"friends": friends}})
        mock_steam_client.get_player_summaries = AsyncMock(return_value=[])

        await steam_module.register_tools(mcp=mock_mcp, config={}, db=None)
        tools = mock_mcp._registered_tools

        await tools["steam_get_friend_list"](enrich=True)
        assert mock_steam_client.get_player_summaries.call_count == 3

    async def test_friend_list_enrich_default_batch_size_100(
        self,
        steam_module: SteamModule,
        mock_mcp: MagicMock,
        mock_steam_client: MagicMock,
    ):
        """Default max_batch_size=100: 150 friends → 2 batch calls."""
        steam_module._client = mock_steam_client
        steam_module._credentials_ok = True
        steam_module._primary_steam_id = "76561198000000001"
        # default _max_batch_size is 100

        friends = [
            {"steamid": f"765611980000{i:05d}", "relationship": "friend"} for i in range(150)
        ]
        mock_steam_client.request = AsyncMock(return_value={"friendslist": {"friends": friends}})
        mock_steam_client.get_player_summaries = AsyncMock(return_value=[])

        await steam_module.register_tools(mcp=mock_mcp, config={}, db=None)
        tools = mock_mcp._registered_tools

        await tools["steam_get_friend_list"](enrich=True)
        assert mock_steam_client.get_player_summaries.call_count == 2


# ---------------------------------------------------------------------------
# Error handling helpers
# ---------------------------------------------------------------------------


class TestErrorHelpers:
    def test_no_credentials_error_structure(self):
        err = _no_credentials_error()
        assert err["error"] == "steam_not_connected"
        assert "message" in err
        assert "hint" in err

    def test_rate_limited_error_structure(self):
        err = _rate_limited_error(120.0)
        assert err["error"] == "steam_rate_limited"
        assert "120" in err["message"]
        assert "hint" in err

    def test_privacy_error_structure(self):
        err = _privacy_error("profile is private")
        assert err["error"] == "steam_privacy"
        assert "private" in err["message"]
        assert "hint" in err

    def test_no_achievements_error_structure(self):
        err = _no_achievements_error(730)
        assert err["error"] == "steam_no_achievements"
        assert "730" in err["message"]
        assert "hint" in err

    def test_handle_rate_limit_error(self):
        exc = SteamRateLimitError(retry_after_s=60.0)
        result = _handle_steam_error(exc)
        assert result["error"] == "steam_rate_limited"

    def test_handle_api_error(self):
        exc = SteamAPIError(status_code=500, body="Internal Server Error")
        result = _handle_steam_error(exc)
        assert result["error"] == "steam_api_error"
        assert "500" in result["message"]

    def test_handle_unexpected_error(self):
        exc = RuntimeError("something broke")
        result = _handle_steam_error(exc)
        assert result["error"] == "steam_unexpected_error"


# ---------------------------------------------------------------------------
# Individual tool behavior
# ---------------------------------------------------------------------------


class TestSteamGetPlayerSummary:
    async def test_returns_player_data(
        self, connected_module: SteamModule, mock_mcp: MagicMock, mock_steam_client: MagicMock
    ):
        tools = mock_mcp._registered_tools
        mock_steam_client.get_player_summaries = AsyncMock(
            return_value=[{"steamid": "76561198000000001", "personaname": "Test Player"}]
        )
        result = await tools["steam_get_player_summary"]()
        assert result["player"]["steamid"] == "76561198000000001"

    async def test_privacy_when_empty_list(
        self, connected_module: SteamModule, mock_mcp: MagicMock, mock_steam_client: MagicMock
    ):
        tools = mock_mcp._registered_tools
        mock_steam_client.get_player_summaries = AsyncMock(return_value=[])
        result = await tools["steam_get_player_summary"]()
        assert result["error"] == "steam_privacy"

    async def test_custom_steam_id(
        self, connected_module: SteamModule, mock_mcp: MagicMock, mock_steam_client: MagicMock
    ):
        tools = mock_mcp._registered_tools
        mock_steam_client.get_player_summaries = AsyncMock(
            return_value=[{"steamid": "76561198000000999", "personaname": "Other"}]
        )
        result = await tools["steam_get_player_summary"](steam_id="76561198000000999")
        mock_steam_client.get_player_summaries.assert_called_with(["76561198000000999"])
        assert result["player"]["steamid"] == "76561198000000999"

    async def test_api_error_propagated(
        self, connected_module: SteamModule, mock_mcp: MagicMock, mock_steam_client: MagicMock
    ):
        tools = mock_mcp._registered_tools
        mock_steam_client.get_player_summaries = AsyncMock(
            side_effect=SteamAPIError(500, "Internal Server Error")
        )
        result = await tools["steam_get_player_summary"]()
        assert result["error"] == "steam_api_error"


class TestSteamGetOwnedGames:
    async def test_returns_game_library(
        self, connected_module: SteamModule, mock_mcp: MagicMock, mock_steam_client: MagicMock
    ):
        tools = mock_mcp._registered_tools
        mock_steam_client.request = AsyncMock(
            return_value={"game_count": 2, "games": [{"appid": 730}, {"appid": 440}]}
        )
        result = await tools["steam_get_owned_games"]()
        assert result["game_count"] == 2

    async def test_privacy_when_empty_response(
        self, connected_module: SteamModule, mock_mcp: MagicMock, mock_steam_client: MagicMock
    ):
        tools = mock_mcp._registered_tools
        mock_steam_client.request = AsyncMock(return_value={})
        result = await tools["steam_get_owned_games"]()
        assert result["error"] == "steam_privacy"

    async def test_passes_correct_params(
        self, connected_module: SteamModule, mock_mcp: MagicMock, mock_steam_client: MagicMock
    ):
        tools = mock_mcp._registered_tools
        mock_steam_client.request = AsyncMock(return_value={"game_count": 0, "games": []})
        await tools["steam_get_owned_games"](
            steam_id="76561198000000001",
            include_appinfo=False,
            include_free_games=False,
        )
        mock_steam_client.request.assert_called_once_with(
            "IPlayerService",
            "GetOwnedGames",
            params={
                "steamid": "76561198000000001",
                "include_appinfo": 0,
                "include_played_free_games": 0,
            },
        )


class TestSteamGetRecentlyPlayed:
    async def test_returns_recent_games(
        self, connected_module: SteamModule, mock_mcp: MagicMock, mock_steam_client: MagicMock
    ):
        tools = mock_mcp._registered_tools
        mock_steam_client.request = AsyncMock(
            return_value={"total_count": 1, "games": [{"appid": 730}]}
        )
        result = await tools["steam_get_recently_played"]()
        assert result["total_count"] == 1

    async def test_count_capped_at_50(
        self, connected_module: SteamModule, mock_mcp: MagicMock, mock_steam_client: MagicMock
    ):
        tools = mock_mcp._registered_tools
        mock_steam_client.request = AsyncMock(return_value={"total_count": 0, "games": []})
        await tools["steam_get_recently_played"](count=999)
        call_params = mock_steam_client.request.call_args[1]["params"]
        assert call_params["count"] == 50


class TestSteamGetAchievements:
    async def test_returns_achievements(
        self, connected_module: SteamModule, mock_mcp: MagicMock, mock_steam_client: MagicMock
    ):
        tools = mock_mcp._registered_tools
        mock_steam_client.request = AsyncMock(
            return_value={
                "playerstats": {
                    "steamID": "76561198000000001",
                    "gameName": "Counter-Strike 2",
                    "achievements": [],
                }
            }
        )
        result = await tools["steam_get_achievements"](app_id=730)
        assert "playerstats" in result

    async def test_privacy_on_400_private_profile(
        self, connected_module: SteamModule, mock_mcp: MagicMock, mock_steam_client: MagicMock
    ):
        """400 with a privacy-related body returns steam_privacy, not steam_no_achievements."""
        tools = mock_mcp._registered_tools
        mock_steam_client.request = AsyncMock(
            side_effect=SteamAPIError(400, "Profile is not public")
        )
        result = await tools["steam_get_achievements"](app_id=730)
        assert result["error"] == "steam_privacy"
        assert "hint" in result

    async def test_no_achievements_on_400_no_stats(
        self, connected_module: SteamModule, mock_mcp: MagicMock, mock_steam_client: MagicMock
    ):
        """400 with 'Requested app has no stats' body returns steam_no_achievements."""
        tools = mock_mcp._registered_tools
        mock_steam_client.request = AsyncMock(
            side_effect=SteamAPIError(400, "Requested app has no stats")
        )
        result = await tools["steam_get_achievements"](app_id=730)
        assert result["error"] == "steam_no_achievements"
        assert "730" in result["message"]
        assert "hint" in result

    async def test_no_achievements_on_400_no_achievements_body(
        self, connected_module: SteamModule, mock_mcp: MagicMock, mock_steam_client: MagicMock
    ):
        """400 with 'no achievements' in body returns steam_no_achievements."""
        tools = mock_mcp._registered_tools
        mock_steam_client.request = AsyncMock(
            side_effect=SteamAPIError(400, "This game has no achievements")
        )
        result = await tools["steam_get_achievements"](app_id=440)
        assert result["error"] == "steam_no_achievements"
        assert "440" in result["message"]


class TestSteamGetFriendList:
    async def test_returns_friends(
        self, connected_module: SteamModule, mock_mcp: MagicMock, mock_steam_client: MagicMock
    ):
        tools = mock_mcp._registered_tools
        mock_steam_client.request = AsyncMock(
            return_value={
                "friendslist": {
                    "friends": [
                        {"steamid": "76561198000000002", "relationship": "friend"},
                        {"steamid": "76561198000000003", "relationship": "friend"},
                    ]
                }
            }
        )
        result = await tools["steam_get_friend_list"]()
        assert result["count"] == 2
        assert len(result["friends"]) == 2

    async def test_enrich_fetches_summaries(
        self, connected_module: SteamModule, mock_mcp: MagicMock, mock_steam_client: MagicMock
    ):
        tools = mock_mcp._registered_tools
        mock_steam_client.request = AsyncMock(
            return_value={
                "friendslist": {
                    "friends": [{"steamid": "76561198000000002", "relationship": "friend"}]
                }
            }
        )
        mock_steam_client.get_player_summaries = AsyncMock(
            return_value=[{"steamid": "76561198000000002", "personaname": "Friend 1"}]
        )
        result = await tools["steam_get_friend_list"](enrich=True)
        assert result["friends"][0]["summary"]["personaname"] == "Friend 1"
        mock_steam_client.get_player_summaries.assert_called_once_with(["76561198000000002"])

    async def test_enrich_batches_at_100(
        self, connected_module: SteamModule, mock_mcp: MagicMock, mock_steam_client: MagicMock
    ):
        """Friends beyond 100 should be split into 2 batch calls."""
        tools = mock_mcp._registered_tools
        # Build 150 fake friends
        friends = [
            {"steamid": f"765611980000{i:05d}", "relationship": "friend"} for i in range(150)
        ]
        mock_steam_client.request = AsyncMock(return_value={"friendslist": {"friends": friends}})
        mock_steam_client.get_player_summaries = AsyncMock(return_value=[])
        await tools["steam_get_friend_list"](enrich=True)
        assert mock_steam_client.get_player_summaries.call_count == 2

    async def test_privacy_on_401(
        self, connected_module: SteamModule, mock_mcp: MagicMock, mock_steam_client: MagicMock
    ):
        tools = mock_mcp._registered_tools
        mock_steam_client.request = AsyncMock(side_effect=SteamAPIError(401, "Unauthorized"))
        result = await tools["steam_get_friend_list"]()
        assert result["error"] == "steam_privacy"

    async def test_empty_friends_no_enrich_call(
        self, connected_module: SteamModule, mock_mcp: MagicMock, mock_steam_client: MagicMock
    ):
        """When friend list is empty, enrich should not make API calls."""
        tools = mock_mcp._registered_tools
        mock_steam_client.request = AsyncMock(return_value={"friendslist": {"friends": []}})
        mock_steam_client.get_player_summaries = AsyncMock(return_value=[])
        result = await tools["steam_get_friend_list"](enrich=True)
        mock_steam_client.get_player_summaries.assert_not_called()
        assert result["count"] == 0


class TestSteamGetGameNews:
    async def test_returns_news(
        self, connected_module: SteamModule, mock_mcp: MagicMock, mock_steam_client: MagicMock
    ):
        tools = mock_mcp._registered_tools
        mock_steam_client.request = AsyncMock(
            return_value={"appnews": {"appid": 730, "newsitems": []}}
        )
        result = await tools["steam_get_game_news"](app_id=730)
        assert "appnews" in result

    async def test_count_capped_at_20(
        self, connected_module: SteamModule, mock_mcp: MagicMock, mock_steam_client: MagicMock
    ):
        tools = mock_mcp._registered_tools
        mock_steam_client.request = AsyncMock(return_value={})
        await tools["steam_get_game_news"](app_id=730, count=999)
        call_params = mock_steam_client.request.call_args[1]["params"]
        assert call_params["count"] == 20


class TestSteamGetPlayerLevel:
    async def test_returns_level(
        self, connected_module: SteamModule, mock_mcp: MagicMock, mock_steam_client: MagicMock
    ):
        tools = mock_mcp._registered_tools
        mock_steam_client.request = AsyncMock(return_value={"player_level": 42})
        result = await tools["steam_get_player_level"]()
        assert result["player_level"] == 42

    async def test_uses_primary_steam_id(
        self, connected_module: SteamModule, mock_mcp: MagicMock, mock_steam_client: MagicMock
    ):
        tools = mock_mcp._registered_tools
        mock_steam_client.request = AsyncMock(return_value={"player_level": 10})
        await tools["steam_get_player_level"]()
        call_params = mock_steam_client.request.call_args[1]["params"]
        assert call_params["steamid"] == "76561198000000001"


class TestSteamGetCurrentPlayers:
    async def test_returns_player_count(
        self, connected_module: SteamModule, mock_mcp: MagicMock, mock_steam_client: MagicMock
    ):
        tools = mock_mcp._registered_tools
        mock_steam_client.request = AsyncMock(return_value={"player_count": 54321})
        result = await tools["steam_get_current_players"](app_id=730)
        assert result["player_count"] == 54321

    async def test_api_error_propagated(
        self, connected_module: SteamModule, mock_mcp: MagicMock, mock_steam_client: MagicMock
    ):
        tools = mock_mcp._registered_tools
        mock_steam_client.request = AsyncMock(side_effect=SteamAPIError(404, "Not Found"))
        result = await tools["steam_get_current_players"](app_id=99999)
        assert result["error"] == "steam_api_error"


class TestSteamResolveVanityUrl:
    async def test_resolves_valid_vanity(
        self, connected_module: SteamModule, mock_mcp: MagicMock, mock_steam_client: MagicMock
    ):
        tools = mock_mcp._registered_tools
        mock_steam_client.request = AsyncMock(
            return_value={"steamid": "76561197960287930", "success": 1}
        )
        result = await tools["steam_resolve_vanity_url"](vanity_url="gaben")
        assert result["steamid"] == "76561197960287930"
        assert result["vanity_url"] == "gaben"

    async def test_not_found_returns_error(
        self, connected_module: SteamModule, mock_mcp: MagicMock, mock_steam_client: MagicMock
    ):
        tools = mock_mcp._registered_tools
        mock_steam_client.request = AsyncMock(return_value={"success": 42, "message": "No match"})
        result = await tools["steam_resolve_vanity_url"](vanity_url="no_such_user_12345")
        assert result["error"] == "steam_vanity_not_found"
        assert "hint" in result

    async def test_rate_limit_error_propagated(
        self, connected_module: SteamModule, mock_mcp: MagicMock, mock_steam_client: MagicMock
    ):
        tools = mock_mcp._registered_tools
        mock_steam_client.request = AsyncMock(side_effect=SteamRateLimitError(retry_after_s=60.0))
        result = await tools["steam_resolve_vanity_url"](vanity_url="gaben")
        assert result["error"] == "steam_rate_limited"
