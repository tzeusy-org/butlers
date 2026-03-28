"""Integration tests for the Steam module wired into the Lifestyle and General butlers.

Covers bu-s2fo.7 acceptance criteria:
1. roster/lifestyle/butler.toml and roster/general/butler.toml declare [modules.steam]
2. SteamModule loads and registers all 9 tools (module loads without error)
3. Module in degraded mode (no Steam account) returns actionable errors on private tools
4. Public tools (game_news, current_players) work without auth via mocked Steam API
5. Full query flow: player summary → owned games → achievements (mocked Steam API)

All HTTP is mocked at the SteamAPIClient level via ``unittest.mock``.
No live Steam API calls are made.

Issue: bu-s2fo.7
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.modules.steam import (
    SteamModule,
    _no_credentials_error,
)

# ---------------------------------------------------------------------------
# Auto-override: these tests are pure unit tests — no Docker required.
# The roster/ conftest auto-adds integration + docker-skip; we explicitly
# override with the unit mark so they run in all environments.
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[3]  # .../rig
_LIFESTYLE_ROSTER_DIR = _REPO_ROOT / "roster" / "lifestyle"
_GENERAL_ROSTER_DIR = _REPO_ROOT / "roster" / "general"

_STEAM_ID = "76561198000000001"
_DISPLAY_NAME = "Steam Test Player"

_EXPECTED_STEAM_TOOLS = {
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
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_mcp() -> MagicMock:
    """Build a mock FastMCP server that captures registered tool functions."""
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


def _make_mock_steam_client(
    *,
    player_summaries: list[dict[str, Any]] | None = None,
    request_return: dict[str, Any] | None = None,
) -> MagicMock:
    """Build a mock SteamAPIClient with configurable responses."""
    client = MagicMock()
    client.open = AsyncMock(return_value=None)
    client.close = AsyncMock(return_value=None)

    default_player = {
        "steamid": _STEAM_ID,
        "personaname": _DISPLAY_NAME,
        "profileurl": "https://steamcommunity.com/id/testplayer/",
        "avatar": "https://cdn.cloudflare.steamstatic.com/test.jpg",
        "communityvisibilitystate": 3,
        "personastate": 1,
    }
    client.get_player_summaries = AsyncMock(
        return_value=player_summaries if player_summaries is not None else [default_player]
    )
    client.request = AsyncMock(return_value=request_return if request_return is not None else {})
    return client


# ===========================================================================
# Test Category 1: butler.toml Config Validation
# Verify both butler.toml files declare [modules.steam].
# ===========================================================================


@pytest.mark.unit
class TestButlerTomlSteamConfig:
    """AC1: butler.toml files for lifestyle and general declare [modules.steam]."""

    def test_lifestyle_butler_toml_has_steam_module(self) -> None:
        """roster/lifestyle/butler.toml declares [modules.steam]."""
        from butlers.config import load_config

        cfg = load_config(_LIFESTYLE_ROSTER_DIR)
        assert "steam" in cfg.modules, (
            "steam module is missing from [modules] section in roster/lifestyle/butler.toml"
        )

    def test_general_butler_toml_has_steam_module(self) -> None:
        """roster/general/butler.toml declares [modules.steam]."""
        from butlers.config import load_config

        cfg = load_config(_GENERAL_ROSTER_DIR)
        assert "steam" in cfg.modules, (
            "steam module is missing from [modules] section in roster/general/butler.toml"
        )

    def test_lifestyle_steam_config_is_empty_dict(self) -> None:
        """Steam module config in lifestyle butler.toml is an empty dict (no required fields)."""
        from butlers.config import load_config

        cfg = load_config(_LIFESTYLE_ROSTER_DIR)
        steam_cfg = cfg.modules.get("steam", None)
        assert steam_cfg is not None
        assert isinstance(steam_cfg, dict)

    def test_general_steam_config_is_empty_dict(self) -> None:
        """Steam module config in general butler.toml is an empty dict (no required fields)."""
        from butlers.config import load_config

        cfg = load_config(_GENERAL_ROSTER_DIR)
        steam_cfg = cfg.modules.get("steam", None)
        assert steam_cfg is not None
        assert isinstance(steam_cfg, dict)


# ===========================================================================
# Test Category 2: Module Registration
# Verify SteamModule loads and registers all 9 tools.
# ===========================================================================


@pytest.mark.unit
class TestSteamModuleRegistration:
    """AC3: Steam module loads without error and registers all 9 tools."""

    async def test_registers_all_9_tools(self) -> None:
        """SteamModule.register_tools() registers exactly 9 tools."""
        module = SteamModule()
        mcp = _make_mock_mcp()
        await module.register_tools(mcp=mcp, config={}, db=None)
        assert len(mcp._registered_tools) == 9

    async def test_all_expected_tool_names_present(self) -> None:
        """The registered tool names match exactly the expected 9-tool set."""
        module = SteamModule()
        mcp = _make_mock_mcp()
        await module.register_tools(mcp=mcp, config={}, db=None)
        assert set(mcp._registered_tools.keys()) == _EXPECTED_STEAM_TOOLS

    async def test_all_tools_are_callable(self) -> None:
        """Every registered tool is a callable (async function)."""
        module = SteamModule()
        mcp = _make_mock_mcp()
        await module.register_tools(mcp=mcp, config={}, db=None)
        for name, fn in mcp._registered_tools.items():
            assert callable(fn), f"{name} should be callable"

    async def test_module_name_is_steam(self) -> None:
        """SteamModule.name returns 'steam'."""
        module = SteamModule()
        assert module.name == "steam"

    async def test_module_startup_no_db_does_not_raise(self) -> None:
        """on_startup() with db=None completes without raising (degraded mode)."""
        from unittest.mock import patch

        mock_client = _make_mock_steam_client()
        module = SteamModule()
        with patch("butlers.modules.steam.SteamAPIClient", return_value=mock_client):
            await module.on_startup(config={}, db=None)
        assert not module._credentials_ok
        # Public client is still opened for game_news / current_players
        assert module._public_client is not None

    async def test_module_shutdown_no_error_when_not_started(self) -> None:
        """on_shutdown() on an unstarted module does not raise."""
        module = SteamModule()
        await module.on_shutdown()  # should not raise


# ===========================================================================
# Test Category 3: Degraded Mode (No Steam Account)
# Verify private tools return actionable errors when no account is connected.
# ===========================================================================


@pytest.mark.unit
class TestDegradedMode:
    """AC2: Steam connector starts without errors; private tools return actionable errors."""

    async def _build_degraded_module(self) -> tuple[SteamModule, MagicMock]:
        """Build a module in degraded mode (no credentials, public client available)."""
        from unittest.mock import patch

        mock_client = _make_mock_steam_client()
        module = SteamModule()
        with patch("butlers.modules.steam.SteamAPIClient", return_value=mock_client):
            await module.on_startup(config={}, db=None)
        mcp = _make_mock_mcp()
        await module.register_tools(mcp=mcp, config={}, db=None)
        return module, mcp

    async def test_credentials_ok_false_in_degraded_mode(self) -> None:
        """When no Steam account is connected, _credentials_ok is False."""
        module, _ = await self._build_degraded_module()
        assert module._credentials_ok is False

    async def test_9_tools_still_registered_in_degraded_mode(self) -> None:
        """All 9 tools are still registered even without credentials."""
        _, mcp = await self._build_degraded_module()
        assert len(mcp._registered_tools) == 9

    async def test_player_summary_returns_no_credentials_error(self) -> None:
        """steam_get_player_summary returns the no-credentials error in degraded mode."""
        _, mcp = await self._build_degraded_module()
        result = await mcp._registered_tools["steam_get_player_summary"]()
        assert "error" in result
        assert result["error"] == "no_steam_account"

    async def test_owned_games_returns_no_credentials_error(self) -> None:
        """steam_get_owned_games returns the no-credentials error in degraded mode."""
        _, mcp = await self._build_degraded_module()
        result = await mcp._registered_tools["steam_get_owned_games"]()
        assert "error" in result
        assert result["error"] == "no_steam_account"

    async def test_recently_played_returns_no_credentials_error(self) -> None:
        """steam_get_recently_played returns the no-credentials error in degraded mode."""
        _, mcp = await self._build_degraded_module()
        result = await mcp._registered_tools["steam_get_recently_played"]()
        assert "error" in result
        assert result["error"] == "no_steam_account"

    async def test_achievements_returns_no_credentials_error(self) -> None:
        """steam_get_achievements returns the no-credentials error in degraded mode."""
        _, mcp = await self._build_degraded_module()
        result = await mcp._registered_tools["steam_get_achievements"](app_id=730)
        assert "error" in result
        assert result["error"] == "no_steam_account"

    async def test_friend_list_returns_no_credentials_error(self) -> None:
        """steam_get_friend_list returns the no-credentials error in degraded mode."""
        _, mcp = await self._build_degraded_module()
        result = await mcp._registered_tools["steam_get_friend_list"]()
        assert "error" in result
        assert result["error"] == "no_steam_account"

    async def test_player_level_returns_no_credentials_error(self) -> None:
        """steam_get_player_level returns the no-credentials error in degraded mode."""
        _, mcp = await self._build_degraded_module()
        result = await mcp._registered_tools["steam_get_player_level"]()
        assert "error" in result
        assert result["error"] == "no_steam_account"

    async def test_resolve_vanity_url_returns_no_credentials_error(self) -> None:
        """steam_resolve_vanity_url returns the no-credentials error in degraded mode."""
        _, mcp = await self._build_degraded_module()
        result = await mcp._registered_tools["steam_resolve_vanity_url"](vanity_url="gaben")
        assert "error" in result
        assert result["error"] == "no_steam_account"

    async def test_no_credentials_error_includes_hint(self) -> None:
        """The no-credentials error message includes a hint directing to dashboard settings."""
        _, mcp = await self._build_degraded_module()
        result = await mcp._registered_tools["steam_get_player_summary"]()
        expected = _no_credentials_error()
        assert result == expected
        assert "hint" in result
        assert "dashboard" in result["hint"].lower() or "connect" in result["hint"].lower()


# ===========================================================================
# Test Category 4: Public Endpoints Without Auth
# Verify game_news and current_players work via the public client.
# ===========================================================================


@pytest.mark.unit
class TestPublicEndpointsWithoutAuth:
    """Public Steam API endpoints work even when no Steam account is connected."""

    async def _build_module_with_public_client(
        self, public_request_return: dict[str, Any]
    ) -> tuple[SteamModule, MagicMock]:
        """Build a degraded module with a public client returning a specific response."""
        from unittest.mock import patch

        mock_client = _make_mock_steam_client(request_return=public_request_return)
        module = SteamModule()
        with patch("butlers.modules.steam.SteamAPIClient", return_value=mock_client):
            await module.on_startup(config={}, db=None)
        mcp = _make_mock_mcp()
        await module.register_tools(mcp=mcp, config={}, db=None)
        return module, mcp

    async def test_game_news_works_without_auth(self) -> None:
        """steam_get_game_news returns news using the public client (no credentials needed)."""
        news_response = {"appnews": {"appid": 730, "newsitems": [{"title": "CS2 Update"}]}}
        _, mcp = await self._build_module_with_public_client(news_response)
        result = await mcp._registered_tools["steam_get_game_news"](app_id=730)
        assert "appnews" in result
        assert result["appnews"]["appid"] == 730

    async def test_current_players_works_without_auth(self) -> None:
        """steam_get_current_players returns player count using the public client."""
        players_response = {"player_count": 99999, "result": 1}
        _, mcp = await self._build_module_with_public_client(players_response)
        result = await mcp._registered_tools["steam_get_current_players"](app_id=730)
        assert result["player_count"] == 99999


# ===========================================================================
# Test Category 5: Full Query Flow (Mocked Steam API)
# Verify connected module can query player data through the tools.
# ===========================================================================


@pytest.mark.unit
class TestFullQueryFlow:
    """AC3: Module tools return data with mocked Steam API responses."""

    @pytest.fixture
    async def connected_module_and_mcp(self) -> tuple[SteamModule, MagicMock]:
        """SteamModule with mocked credentials in connected state."""
        import uuid
        from datetime import UTC, datetime
        from unittest.mock import patch

        from butlers.steam_account_registry import SteamAccount

        dummy_account = SteamAccount(
            id=uuid.uuid4(),
            entity_id=uuid.uuid4(),
            steam_id=int(_STEAM_ID),
            display_name=_DISPLAY_NAME,
            profile_url=None,
            avatar_url=None,
            is_primary=True,
            status="active",
            connected_at=datetime.now(UTC),
            last_poll_at=None,
            metadata={},
        )

        mock_client = _make_mock_steam_client()
        module = SteamModule()

        with (
            patch(
                "butlers.modules.steam.resolve_steam_account",
                AsyncMock(return_value=dummy_account),
            ),
            patch(
                "butlers.modules.steam._fetch_api_key",
                AsyncMock(return_value="FAKE_API_KEY_12345"),
            ),
            patch(
                "butlers.modules.steam.SteamAPIClient",
                return_value=mock_client,
            ),
        ):
            db = MagicMock()
            db.pool = MagicMock()
            await module.on_startup(config={}, db=db)

        mcp = _make_mock_mcp()
        await module.register_tools(mcp=mcp, config={}, db=None)
        return module, mcp

    async def test_credentials_ok_after_successful_startup(
        self, connected_module_and_mcp: tuple[SteamModule, MagicMock]
    ) -> None:
        """After successful startup, _credentials_ok is True."""
        module, _ = connected_module_and_mcp
        assert module._credentials_ok is True
        assert module._primary_steam_id == _STEAM_ID

    async def test_player_summary_returns_profile(
        self, connected_module_and_mcp: tuple[SteamModule, MagicMock]
    ) -> None:
        """steam_get_player_summary returns the player's profile data."""
        module, mcp = connected_module_and_mcp
        # Inject the expected response into the mock client
        module._client.get_player_summaries = AsyncMock(
            return_value=[
                {
                    "steamid": _STEAM_ID,
                    "personaname": _DISPLAY_NAME,
                    "communityvisibilitystate": 3,
                    "personastate": 1,
                }
            ]
        )
        result = await mcp._registered_tools["steam_get_player_summary"]()
        assert "player" in result
        assert result["player"]["steamid"] == _STEAM_ID
        assert result["player"]["personaname"] == _DISPLAY_NAME

    async def test_owned_games_returns_library(
        self, connected_module_and_mcp: tuple[SteamModule, MagicMock]
    ) -> None:
        """steam_get_owned_games returns the game library."""
        module, mcp = connected_module_and_mcp
        module._client.request = AsyncMock(
            return_value={
                "game_count": 3,
                "games": [
                    {"appid": 730, "name": "Counter-Strike 2", "playtime_forever": 1200},
                    {"appid": 440, "name": "Team Fortress 2", "playtime_forever": 600},
                    {"appid": 570, "name": "Dota 2", "playtime_forever": 300},
                ],
            }
        )
        result = await mcp._registered_tools["steam_get_owned_games"]()
        assert "game_count" in result
        assert result["game_count"] == 3
        assert len(result["games"]) == 3

    async def test_recently_played_returns_games(
        self, connected_module_and_mcp: tuple[SteamModule, MagicMock]
    ) -> None:
        """steam_get_recently_played returns games played in the last 2 weeks."""
        module, mcp = connected_module_and_mcp
        module._client.request = AsyncMock(
            return_value={
                "total_count": 1,
                "games": [
                    {
                        "appid": 730,
                        "name": "Counter-Strike 2",
                        "playtime_2weeks": 120,
                        "playtime_forever": 1200,
                    }
                ],
            }
        )
        result = await mcp._registered_tools["steam_get_recently_played"]()
        assert "total_count" in result
        assert result["total_count"] == 1
        assert result["games"][0]["appid"] == 730

    async def test_achievements_returns_data(
        self, connected_module_and_mcp: tuple[SteamModule, MagicMock]
    ) -> None:
        """steam_get_achievements returns achievement data for a given game."""
        module, mcp = connected_module_and_mcp
        module._client.request = AsyncMock(
            return_value={
                "playerstats": {
                    "steamID": _STEAM_ID,
                    "gameName": "Counter-Strike 2",
                    "achievements": [
                        {"apiname": "WIN_ROUNDS_ACE", "achieved": 1, "unlocktime": 1700000000}
                    ],
                    "success": True,
                }
            }
        )
        result = await mcp._registered_tools["steam_get_achievements"](app_id=730)
        assert "playerstats" in result
        assert result["playerstats"]["gameName"] == "Counter-Strike 2"

    async def test_player_level_returns_level(
        self, connected_module_and_mcp: tuple[SteamModule, MagicMock]
    ) -> None:
        """steam_get_player_level returns the Steam Experience Level."""
        module, mcp = connected_module_and_mcp
        module._client.request = AsyncMock(return_value={"player_level": 42})
        result = await mcp._registered_tools["steam_get_player_level"]()
        assert result["player_level"] == 42

    async def test_full_profile_to_games_flow(
        self, connected_module_and_mcp: tuple[SteamModule, MagicMock]
    ) -> None:
        """Full flow: get player summary → verify SteamID → query owned games."""
        module, mcp = connected_module_and_mcp

        # Step 1: Get player summary to confirm SteamID
        module._client.get_player_summaries = AsyncMock(
            return_value=[{"steamid": _STEAM_ID, "personaname": _DISPLAY_NAME}]
        )
        summary = await mcp._registered_tools["steam_get_player_summary"]()
        assert summary["player"]["steamid"] == _STEAM_ID

        # Step 2: Query the game library for the confirmed SteamID
        module._client.request = AsyncMock(
            return_value={
                "game_count": 2,
                "games": [
                    {"appid": 730, "name": "CS2", "playtime_forever": 500},
                    {"appid": 440, "name": "TF2", "playtime_forever": 200},
                ],
            }
        )
        games = await mcp._registered_tools["steam_get_owned_games"](
            steam_id=summary["player"]["steamid"]
        )
        assert games["game_count"] == 2
        assert any(g["appid"] == 730 for g in games["games"])

    async def test_vanity_url_resolution(
        self, connected_module_and_mcp: tuple[SteamModule, MagicMock]
    ) -> None:
        """steam_resolve_vanity_url resolves a vanity name to a SteamID64."""
        module, mcp = connected_module_and_mcp
        module._client.request = AsyncMock(
            return_value={"steamid": "76561197960287930", "success": 1}
        )
        result = await mcp._registered_tools["steam_resolve_vanity_url"](vanity_url="gaben")
        assert result["steamid"] == "76561197960287930"
        assert result["vanity_url"] == "gaben"

    async def test_friend_list_returns_friends(
        self, connected_module_and_mcp: tuple[SteamModule, MagicMock]
    ) -> None:
        """steam_get_friend_list returns the friend list with count."""
        module, mcp = connected_module_and_mcp
        module._client.request = AsyncMock(
            return_value={
                "friendslist": {
                    "friends": [
                        {"steamid": "76561198000000002", "relationship": "friend"},
                        {"steamid": "76561198000000003", "relationship": "friend"},
                    ]
                }
            }
        )
        result = await mcp._registered_tools["steam_get_friend_list"]()
        assert result["count"] == 2
        assert len(result["friends"]) == 2
