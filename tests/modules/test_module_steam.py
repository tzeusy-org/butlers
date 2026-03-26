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
    """All tools return structured error when not connected."""

    async def test_all_tools_return_error_when_no_credentials(
        self, steam_module: SteamModule, mock_mcp: MagicMock
    ):
        # Module is not connected by default
        await steam_module.register_tools(mcp=mock_mcp, config={}, db=None)
        tools = mock_mcp._registered_tools

        # Each tool should return a credentials error
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

        result = await tools["steam_get_game_news"](app_id=730)
        assert result == no_creds

        result = await tools["steam_get_player_level"]()
        assert result == no_creds

        result = await tools["steam_get_current_players"](app_id=730)
        assert result == no_creds

        result = await tools["steam_resolve_vanity_url"](vanity_url="gaben")
        assert result == no_creds


# ---------------------------------------------------------------------------
# Startup lifecycle
# ---------------------------------------------------------------------------


class TestStartup:
    async def test_startup_no_db_sets_not_connected(self, steam_module: SteamModule):
        await steam_module.on_startup(config={}, db=None)
        assert not steam_module._credentials_ok

    async def test_startup_no_pool_sets_not_connected(self, steam_module: SteamModule):
        db = MagicMock()
        db.pool = None
        await steam_module.on_startup(config={}, db=db)
        assert not steam_module._credentials_ok

    async def test_startup_missing_primary_account_graceful(self, steam_module: SteamModule):
        from butlers.steam_account_registry import MissingSteamCredentialsError

        db = MagicMock()
        db.pool = MagicMock()

        with patch(
            "butlers.modules.steam.resolve_steam_account",
            AsyncMock(side_effect=MissingSteamCredentialsError("no primary")),
        ):
            await steam_module.on_startup(config={}, db=db)

        assert not steam_module._credentials_ok

    async def test_startup_no_api_key_graceful(self, steam_module: SteamModule):
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

    async def test_shutdown_noop_when_not_connected(self, steam_module: SteamModule):
        # Should not raise
        await steam_module.on_shutdown()


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

    async def test_privacy_on_400(
        self, connected_module: SteamModule, mock_mcp: MagicMock, mock_steam_client: MagicMock
    ):
        tools = mock_mcp._registered_tools
        mock_steam_client.request = AsyncMock(
            side_effect=SteamAPIError(400, "Profile is not public")
        )
        result = await tools["steam_get_achievements"](app_id=730)
        assert result["error"] == "steam_privacy"


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
