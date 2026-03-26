"""Tests for the Spotify module."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel

from butlers.modules.base import Module, ToolMeta
from butlers.modules.spotify import (
    _AUTH_EXPIRED_ERROR,
    _NO_CREDENTIALS_ERROR,
    _RECOMMENDATIONS_UNAVAILABLE_ERROR,
    SpotifyModule,
    SpotifyModuleConfig,
    _premium_required_error,
    _rate_limited_error,
)

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Expected tool names (22 total, 6 groups)
# ---------------------------------------------------------------------------

EXPECTED_SPOTIFY_TOOLS = {
    # Group 1: Search
    "spotify_search",
    # Group 2: Discovery
    "spotify_get_recommendations",
    "spotify_get_related_artists",
    # Group 3: Playback state
    "spotify_get_playback_state",
    "spotify_get_queue",
    "spotify_get_top_items",
    # Group 4: Playback control
    "spotify_play",
    "spotify_pause",
    "spotify_skip_next",
    "spotify_skip_previous",
    "spotify_seek",
    "spotify_set_volume",
    "spotify_add_to_queue",
    "spotify_transfer_playback",
    # Group 5: Playlist management
    "spotify_get_playlists",
    "spotify_create_playlist",
    "spotify_add_tracks_to_playlist",
    "spotify_remove_tracks_from_playlist",
    "spotify_get_playlist_tracks",
    # Group 6: Library management
    "spotify_get_saved_tracks",
    "spotify_save_tracks",
    "spotify_remove_saved_tracks",
}

WRITE_TOOLS = {
    "spotify_play",
    "spotify_pause",
    "spotify_skip_next",
    "spotify_skip_previous",
    "spotify_seek",
    "spotify_set_volume",
    "spotify_add_to_queue",
    "spotify_transfer_playback",
    "spotify_create_playlist",
    "spotify_add_tracks_to_playlist",
    "spotify_remove_tracks_from_playlist",
    "spotify_save_tracks",
    "spotify_remove_saved_tracks",
}

READ_TOOLS = {
    "spotify_search",
    "spotify_get_recommendations",
    "spotify_get_related_artists",
    "spotify_get_playback_state",
    "spotify_get_queue",
    "spotify_get_top_items",
    "spotify_get_playlists",
    "spotify_get_playlist_tracks",
    "spotify_get_saved_tracks",
}

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def spotify_module() -> SpotifyModule:
    """Create a fresh SpotifyModule instance."""
    return SpotifyModule()


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
def mock_spotify_client() -> MagicMock:
    """Create a mock SpotifyClient."""
    client = MagicMock()
    client.get_me = AsyncMock(
        return_value={"id": "testuser", "display_name": "Test User", "product": "premium"}
    )
    client.search = AsyncMock(return_value={"tracks": {"items": []}})
    client.get_recommendations = AsyncMock(return_value={"tracks": []})
    client.get_playback_state = AsyncMock(return_value={"is_playing": True})
    client.get_queue = AsyncMock(return_value={"currently_playing": None, "queue": []})
    client.get_top_items = AsyncMock(return_value={"items": []})
    client.play = AsyncMock(return_value=None)
    client.pause = AsyncMock(return_value=None)
    client.skip_to_next = AsyncMock(return_value=None)
    client.skip_to_previous = AsyncMock(return_value=None)
    client.seek_to_position = AsyncMock(return_value=None)
    client.set_volume = AsyncMock(return_value=None)
    client.add_to_queue = AsyncMock(return_value=None)
    client.get_user_playlists = AsyncMock(return_value={"items": []})
    client.create_playlist = AsyncMock(
        return_value={"id": "pl123", "uri": "spotify:playlist:pl123"}
    )
    client.add_tracks_to_playlist = AsyncMock(return_value={"snapshot_id": "snap1"})
    client.remove_tracks_from_playlist = AsyncMock(return_value={"snapshot_id": "snap2"})
    client.get_saved_tracks = AsyncMock(return_value={"items": []})
    client.save_tracks = AsyncMock(return_value=None)
    client.remove_saved_tracks = AsyncMock(return_value=None)
    client._get = AsyncMock(return_value={"artists": []})
    client._put = AsyncMock(return_value=None)
    client.open = AsyncMock(return_value=None)
    client.close = AsyncMock(return_value=None)
    return client


@pytest.fixture
async def connected_module(
    spotify_module: SpotifyModule,
    mock_spotify_client: MagicMock,
    mock_mcp: MagicMock,
) -> SpotifyModule:
    """SpotifyModule with mocked client in connected state."""
    spotify_module._client = mock_spotify_client
    spotify_module._credentials_ok = True
    spotify_module._user_profile = {
        "id": "testuser",
        "display_name": "Test User",
        "product": "premium",
    }
    await spotify_module.register_tools(mcp=mock_mcp, config={}, db=None)
    spotify_module._mcp = mock_mcp
    return spotify_module


# ---------------------------------------------------------------------------
# Module ABC compliance
# ---------------------------------------------------------------------------


class TestModuleABCCompliance:
    """Verify SpotifyModule implements the Module ABC correctly."""

    def test_is_module_subclass(self):
        assert issubclass(SpotifyModule, Module)

    def test_instantiates(self, spotify_module: SpotifyModule):
        assert spotify_module is not None

    def test_name(self, spotify_module: SpotifyModule):
        assert spotify_module.name == "spotify"

    def test_config_schema(self, spotify_module: SpotifyModule):
        assert spotify_module.config_schema is SpotifyModuleConfig
        assert issubclass(spotify_module.config_schema, BaseModel)

    def test_dependencies_empty(self, spotify_module: SpotifyModule):
        assert spotify_module.dependencies == []

    def test_migration_revisions_none(self, spotify_module: SpotifyModule):
        assert spotify_module.migration_revisions() is None

    def test_isinstance_check(self, spotify_module: SpotifyModule):
        """isinstance(module, Module) returns True."""
        assert isinstance(spotify_module, Module)


# ---------------------------------------------------------------------------
# SpotifyModuleConfig
# ---------------------------------------------------------------------------


class TestSpotifyModuleConfig:
    """Verify SpotifyModuleConfig validation and defaults."""

    def test_defaults(self):
        config = SpotifyModuleConfig()
        assert config.playback_tools is True

    def test_playback_tools_false(self):
        config = SpotifyModuleConfig(playback_tools=False)
        assert config.playback_tools is False

    def test_from_empty_dict(self):
        config = SpotifyModuleConfig(**{})
        assert config.playback_tools is True

    def test_extra_fields_rejected(self):
        with pytest.raises(Exception):
            SpotifyModuleConfig(**{"unknown_key": "value"})


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


class TestToolRegistration:
    """Verify register_tools registers all 22 expected MCP tools."""

    async def test_registers_all_22_tools(self, spotify_module: SpotifyModule, mock_mcp: MagicMock):
        await spotify_module.register_tools(mcp=mock_mcp, config={}, db=None)
        assert set(mock_mcp._registered_tools.keys()) == EXPECTED_SPOTIFY_TOOLS

    async def test_tool_count_is_22(self, spotify_module: SpotifyModule, mock_mcp: MagicMock):
        await spotify_module.register_tools(mcp=mock_mcp, config={}, db=None)
        assert len(mock_mcp._registered_tools) == 22

    async def test_all_tools_are_callable(self, spotify_module: SpotifyModule, mock_mcp: MagicMock):
        await spotify_module.register_tools(mcp=mock_mcp, config={}, db=None)
        for name, fn in mock_mcp._registered_tools.items():
            assert callable(fn), f"{name} should be callable"

    async def test_all_tools_start_with_spotify_prefix(
        self, spotify_module: SpotifyModule, mock_mcp: MagicMock
    ):
        await spotify_module.register_tools(mcp=mock_mcp, config={}, db=None)
        for name in mock_mcp._registered_tools:
            assert name.startswith("spotify_"), f"{name} should start with 'spotify_'"


# ---------------------------------------------------------------------------
# Sensitivity metadata
# ---------------------------------------------------------------------------


class TestToolMetadata:
    """Verify tool_metadata returns correct sensitivity for all tools."""

    def test_returns_dict(self, spotify_module: SpotifyModule):
        meta = spotify_module.tool_metadata()
        assert isinstance(meta, dict)

    def test_write_tools_marked_write(self, spotify_module: SpotifyModule):
        meta = spotify_module.tool_metadata()
        for name in WRITE_TOOLS:
            assert name in meta, f"{name} missing from tool_metadata"
            assert isinstance(meta[name], ToolMeta)
            assert meta[name].arg_sensitivities.get("_write") is True, (
                f"{name} should be marked write"
            )

    def test_read_tools_marked_read(self, spotify_module: SpotifyModule):
        meta = spotify_module.tool_metadata()
        for name in READ_TOOLS:
            assert name in meta, f"{name} missing from tool_metadata"
            assert isinstance(meta[name], ToolMeta)
            assert meta[name].arg_sensitivities.get("_write") is False, (
                f"{name} should be marked read"
            )

    def test_all_22_tools_have_metadata(self, spotify_module: SpotifyModule):
        meta = spotify_module.tool_metadata()
        assert set(meta.keys()) == EXPECTED_SPOTIFY_TOOLS


# ---------------------------------------------------------------------------
# Lifecycle: on_startup
# ---------------------------------------------------------------------------


class TestOnStartup:
    """Test on_startup lifecycle method."""

    async def test_no_credential_store_marks_unconfigured(self, spotify_module: SpotifyModule):
        """Module without credential_store logs warning and stays unconfigured."""
        await spotify_module.on_startup(config={}, db=None, credential_store=None)
        assert spotify_module._credentials_ok is False
        assert spotify_module._client is None

    async def test_missing_access_token_marks_unconfigured(self, spotify_module: SpotifyModule):
        """Module with no SPOTIFY_ACCESS_TOKEN logs warning and stays unconfigured."""
        store = AsyncMock()
        store.resolve = AsyncMock(return_value=None)  # both tokens missing
        await spotify_module.on_startup(config={}, db=None, credential_store=store)
        assert spotify_module._credentials_ok is False
        assert spotify_module._client is None

    async def test_successful_startup_sets_credentials_ok(
        self, spotify_module: SpotifyModule, mock_spotify_client: MagicMock
    ):
        """Successful startup sets _credentials_ok and caches user profile."""
        store = AsyncMock()
        store.resolve = AsyncMock(return_value="fake-token")

        with patch("butlers.modules.spotify.SpotifyClient", return_value=mock_spotify_client):
            await spotify_module.on_startup(config={}, db=None, credential_store=store)

        assert spotify_module._credentials_ok is True
        assert spotify_module._user_profile is not None
        assert spotify_module._user_profile["id"] == "testuser"

    async def test_auth_error_at_startup_marks_unconfigured(self, spotify_module: SpotifyModule):
        """SpotifyAuthError during get_me() marks module unconfigured (no exception raised)."""
        from butlers.connectors.spotify_client import SpotifyAuthError

        store = AsyncMock()
        store.resolve = AsyncMock(return_value="fake-token")

        mock_client = MagicMock()
        mock_client.open = AsyncMock()
        mock_client.get_me = AsyncMock(side_effect=SpotifyAuthError("bad token"))

        with patch("butlers.modules.spotify.SpotifyClient", return_value=mock_client):
            await spotify_module.on_startup(config={}, db=None, credential_store=store)

        assert spotify_module._credentials_ok is False

    async def test_config_applied_at_startup(self, spotify_module: SpotifyModule):
        """Config dict is parsed into SpotifyModuleConfig."""
        store = AsyncMock()
        store.resolve = AsyncMock(return_value=None)
        await spotify_module.on_startup(
            config={"playback_tools": False}, db=None, credential_store=store
        )
        assert spotify_module._config.playback_tools is False


# ---------------------------------------------------------------------------
# Lifecycle: on_shutdown
# ---------------------------------------------------------------------------


class TestOnShutdown:
    """Test on_shutdown lifecycle method."""

    async def test_closes_client(
        self, spotify_module: SpotifyModule, mock_spotify_client: MagicMock
    ):
        """on_shutdown closes the SpotifyClient."""
        spotify_module._client = mock_spotify_client
        await spotify_module.on_shutdown()
        mock_spotify_client.close.assert_called_once()

    async def test_shutdown_without_client_is_safe(self, spotify_module: SpotifyModule):
        """on_shutdown without a client doesn't raise."""
        spotify_module._client = None
        await spotify_module.on_shutdown()  # Should not raise

    async def test_client_set_none_after_shutdown(
        self, spotify_module: SpotifyModule, mock_spotify_client: MagicMock
    ):
        """Client is set to None after shutdown."""
        spotify_module._client = mock_spotify_client
        await spotify_module.on_shutdown()
        assert spotify_module._client is None


# ---------------------------------------------------------------------------
# Missing credentials: all tools return actionable error
# ---------------------------------------------------------------------------


class TestMissingCredentials:
    """All tools return _NO_CREDENTIALS_ERROR when not connected."""

    async def _get_tools(self, module: SpotifyModule) -> dict[str, Any]:
        mcp = MagicMock()
        tools: dict[str, Any] = {}

        def tool_decorator(*_args, **kwargs):
            name = kwargs.get("name")

            def decorator(fn):
                tools[name or fn.__name__] = fn
                return fn

            return decorator

        mcp.tool = tool_decorator
        mcp._registered_tools = tools
        await module.register_tools(mcp=mcp, config={}, db=None)
        return tools

    async def test_search_no_credentials(self, spotify_module: SpotifyModule):
        tools = await self._get_tools(spotify_module)
        result = await tools["spotify_search"](query="test")
        assert result == {"error": _NO_CREDENTIALS_ERROR}

    async def test_get_recommendations_no_credentials(self, spotify_module: SpotifyModule):
        tools = await self._get_tools(spotify_module)
        result = await tools["spotify_get_recommendations"]()
        assert result == {"error": _NO_CREDENTIALS_ERROR}

    async def test_get_related_artists_no_credentials(self, spotify_module: SpotifyModule):
        tools = await self._get_tools(spotify_module)
        result = await tools["spotify_get_related_artists"](artist_id="abc123")
        assert result == {"error": _NO_CREDENTIALS_ERROR}

    async def test_playback_state_no_credentials(self, spotify_module: SpotifyModule):
        tools = await self._get_tools(spotify_module)
        result = await tools["spotify_get_playback_state"]()
        assert result == {"error": _NO_CREDENTIALS_ERROR}

    async def test_play_no_credentials(self, spotify_module: SpotifyModule):
        tools = await self._get_tools(spotify_module)
        result = await tools["spotify_play"]()
        assert result == {"error": _NO_CREDENTIALS_ERROR}

    async def test_pause_no_credentials(self, spotify_module: SpotifyModule):
        tools = await self._get_tools(spotify_module)
        result = await tools["spotify_pause"]()
        assert result == {"error": _NO_CREDENTIALS_ERROR}

    async def test_skip_next_no_credentials(self, spotify_module: SpotifyModule):
        tools = await self._get_tools(spotify_module)
        result = await tools["spotify_skip_next"]()
        assert result == {"error": _NO_CREDENTIALS_ERROR}

    async def test_get_playlists_no_credentials(self, spotify_module: SpotifyModule):
        tools = await self._get_tools(spotify_module)
        result = await tools["spotify_get_playlists"]()
        assert result == {"error": _NO_CREDENTIALS_ERROR}

    async def test_get_saved_tracks_no_credentials(self, spotify_module: SpotifyModule):
        tools = await self._get_tools(spotify_module)
        result = await tools["spotify_get_saved_tracks"]()
        assert result == {"error": _NO_CREDENTIALS_ERROR}


# ---------------------------------------------------------------------------
# Group 1: Search tools
# ---------------------------------------------------------------------------


class TestSearchTools:
    """Test spotify_search tool."""

    async def test_search_calls_client(self, connected_module: SpotifyModule, mock_mcp: MagicMock):
        tools = mock_mcp._registered_tools
        await tools["spotify_search"](query="radiohead", type="artist", limit=5)
        connected_module._client.search.assert_called_once_with(
            "radiohead", types=["artist"], limit=5
        )

    async def test_search_returns_results(
        self, connected_module: SpotifyModule, mock_mcp: MagicMock
    ):
        connected_module._client.search.return_value = {
            "artists": {"items": [{"name": "Radiohead"}]}
        }
        tools = mock_mcp._registered_tools
        result = await tools["spotify_search"](query="radiohead", type="artist")
        assert "artists" in result

    async def test_search_default_type_is_track(
        self, connected_module: SpotifyModule, mock_mcp: MagicMock
    ):
        tools = mock_mcp._registered_tools
        await tools["spotify_search"](query="ok computer")
        connected_module._client.search.assert_called_once_with(
            "ok computer", types=["track"], limit=10
        )

    async def test_search_caps_limit_at_50(
        self, connected_module: SpotifyModule, mock_mcp: MagicMock
    ):
        tools = mock_mcp._registered_tools
        await tools["spotify_search"](query="test", limit=100)
        call_kwargs = connected_module._client.search.call_args
        assert call_kwargs[1]["limit"] == 50


# ---------------------------------------------------------------------------
# Group 2: Discovery tools
# ---------------------------------------------------------------------------


class TestDiscoveryTools:
    """Test spotify_get_recommendations and spotify_get_related_artists."""

    async def test_get_recommendations_calls_client(
        self, connected_module: SpotifyModule, mock_mcp: MagicMock
    ):
        connected_module._client.get_recommendations.return_value = {
            "tracks": [{"name": "Karma Police"}]
        }
        tools = mock_mcp._registered_tools
        result = await tools["spotify_get_recommendations"](
            seed_artists=["abc"], seed_tracks=None, seed_genres=None, limit=5
        )
        connected_module._client.get_recommendations.assert_called_once()
        assert "tracks" in result

    async def test_get_recommendations_unavailable_returns_error(
        self, connected_module: SpotifyModule, mock_mcp: MagicMock
    ):
        """Empty tracks result triggers the unavailable error."""
        connected_module._client.get_recommendations.return_value = {"tracks": []}
        tools = mock_mcp._registered_tools
        result = await tools["spotify_get_recommendations"]()
        assert result == {"error": _RECOMMENDATIONS_UNAVAILABLE_ERROR}

    async def test_get_related_artists_calls_client(
        self, connected_module: SpotifyModule, mock_mcp: MagicMock
    ):
        connected_module._client._get.return_value = {"artists": [{"name": "Portishead"}]}
        tools = mock_mcp._registered_tools
        result = await tools["spotify_get_related_artists"](artist_id="artist123")
        connected_module._client._get.assert_called_with("/artists/artist123/related-artists")
        assert "artists" in result

    async def test_get_related_artists_none_response(
        self, connected_module: SpotifyModule, mock_mcp: MagicMock
    ):
        connected_module._client._get.return_value = None
        tools = mock_mcp._registered_tools
        result = await tools["spotify_get_related_artists"](artist_id="artist123")
        assert result == {"artists": []}


# ---------------------------------------------------------------------------
# Group 3: Playback state tools
# ---------------------------------------------------------------------------


class TestPlaybackStateTools:
    """Test spotify_get_playback_state, spotify_get_queue, spotify_get_top_items."""

    async def test_get_playback_state_returns_state(
        self, connected_module: SpotifyModule, mock_mcp: MagicMock
    ):
        connected_module._client.get_playback_state.return_value = {
            "is_playing": True,
            "item": {"name": "Creep"},
        }
        tools = mock_mcp._registered_tools
        result = await tools["spotify_get_playback_state"]()
        assert result["is_playing"] is True

    async def test_get_playback_state_none_returns_null_dict(
        self, connected_module: SpotifyModule, mock_mcp: MagicMock
    ):
        connected_module._client.get_playback_state.return_value = None
        tools = mock_mcp._registered_tools
        result = await tools["spotify_get_playback_state"]()
        assert result == {"playback": None}

    async def test_get_queue_returns_queue(
        self, connected_module: SpotifyModule, mock_mcp: MagicMock
    ):
        connected_module._client.get_queue.return_value = {
            "currently_playing": {"name": "Creep"},
            "queue": [],
        }
        tools = mock_mcp._registered_tools
        result = await tools["spotify_get_queue"]()
        assert "currently_playing" in result

    async def test_get_top_items_calls_client(
        self, connected_module: SpotifyModule, mock_mcp: MagicMock
    ):
        tools = mock_mcp._registered_tools
        await tools["spotify_get_top_items"](type="artists", time_range="short_term", limit=5)
        connected_module._client.get_top_items.assert_called_once_with(
            "artists", time_range="short_term", limit=5
        )

    async def test_get_top_items_caps_limit_at_50(
        self, connected_module: SpotifyModule, mock_mcp: MagicMock
    ):
        tools = mock_mcp._registered_tools
        await tools["spotify_get_top_items"](limit=100)
        call_args = connected_module._client.get_top_items.call_args
        assert call_args[1]["limit"] == 50


# ---------------------------------------------------------------------------
# Group 4: Playback control tools
# ---------------------------------------------------------------------------


class TestPlaybackControlTools:
    """Test playback control tools (Premium-required)."""

    async def test_play_returns_playing_status(
        self, connected_module: SpotifyModule, mock_mcp: MagicMock
    ):
        tools = mock_mcp._registered_tools
        result = await tools["spotify_play"]()
        assert result == {"status": "playing"}

    async def test_play_passes_context_uri(
        self, connected_module: SpotifyModule, mock_mcp: MagicMock
    ):
        tools = mock_mcp._registered_tools
        await tools["spotify_play"](context_uri="spotify:album:xxx")
        connected_module._client.play.assert_called_once_with(
            context_uri="spotify:album:xxx", uris=None, device_id=None
        )

    async def test_pause_returns_paused_status(
        self, connected_module: SpotifyModule, mock_mcp: MagicMock
    ):
        tools = mock_mcp._registered_tools
        result = await tools["spotify_pause"]()
        assert result == {"status": "paused"}

    async def test_skip_next_returns_status(
        self, connected_module: SpotifyModule, mock_mcp: MagicMock
    ):
        tools = mock_mcp._registered_tools
        result = await tools["spotify_skip_next"]()
        assert result == {"status": "skipped_next"}

    async def test_skip_previous_returns_status(
        self, connected_module: SpotifyModule, mock_mcp: MagicMock
    ):
        tools = mock_mcp._registered_tools
        result = await tools["spotify_skip_previous"]()
        assert result == {"status": "skipped_previous"}

    async def test_seek_calls_client(self, connected_module: SpotifyModule, mock_mcp: MagicMock):
        tools = mock_mcp._registered_tools
        result = await tools["spotify_seek"](position_ms=30000)
        connected_module._client.seek_to_position.assert_called_once_with(30000, device_id=None)
        assert result == {"status": "seeked", "position_ms": 30000}

    async def test_set_volume_calls_client(
        self, connected_module: SpotifyModule, mock_mcp: MagicMock
    ):
        tools = mock_mcp._registered_tools
        result = await tools["spotify_set_volume"](volume_percent=70)
        connected_module._client.set_volume.assert_called_once_with(70, device_id=None)
        assert result == {"status": "volume_set", "volume_percent": 70}

    async def test_add_to_queue_calls_client(
        self, connected_module: SpotifyModule, mock_mcp: MagicMock
    ):
        tools = mock_mcp._registered_tools
        result = await tools["spotify_add_to_queue"](uri="spotify:track:abc")
        connected_module._client.add_to_queue.assert_called_once_with(
            "spotify:track:abc", device_id=None
        )
        assert result == {"status": "added_to_queue", "uri": "spotify:track:abc"}

    async def test_transfer_playback_calls_put(
        self, connected_module: SpotifyModule, mock_mcp: MagicMock
    ):
        tools = mock_mcp._registered_tools
        result = await tools["spotify_transfer_playback"](device_id="device123")
        connected_module._client._put.assert_called_once_with(
            "/me/player",
            json={"device_ids": ["device123"], "play": True},
        )
        assert result["status"] == "transferred"

    async def test_premium_required_error_returned(
        self, connected_module: SpotifyModule, mock_mcp: MagicMock
    ):
        """HTTP 403 with 'premium' in body returns actionable error."""
        from butlers.connectors.spotify_client import SpotifyAPIError

        connected_module._client.play.side_effect = SpotifyAPIError(
            403, "Player command failed: Premium required"
        )
        tools = mock_mcp._registered_tools
        result = await tools["spotify_play"]()
        assert "error" in result
        assert "Premium" in result["error"]
        assert "premium" in result["error"]  # account tier from profile


# ---------------------------------------------------------------------------
# Group 5: Playlist management tools
# ---------------------------------------------------------------------------


class TestPlaylistTools:
    """Test playlist management tools."""

    async def test_get_playlists_calls_client(
        self, connected_module: SpotifyModule, mock_mcp: MagicMock
    ):
        connected_module._client.get_user_playlists.return_value = {
            "items": [{"id": "pl1", "name": "My Playlist"}]
        }
        tools = mock_mcp._registered_tools
        result = await tools["spotify_get_playlists"](limit=10)
        connected_module._client.get_user_playlists.assert_called_once_with(limit=10, offset=0)
        assert "items" in result

    async def test_get_playlists_caps_limit_at_50(
        self, connected_module: SpotifyModule, mock_mcp: MagicMock
    ):
        tools = mock_mcp._registered_tools
        await tools["spotify_get_playlists"](limit=100)
        call_args = connected_module._client.get_user_playlists.call_args
        assert call_args[1]["limit"] == 50

    async def test_create_playlist_uses_cached_user_id(
        self, connected_module: SpotifyModule, mock_mcp: MagicMock
    ):
        tools = mock_mcp._registered_tools
        result = await tools["spotify_create_playlist"](
            name="My New Playlist", description="A test", public=False
        )
        connected_module._client.create_playlist.assert_called_once_with(
            "testuser", "My New Playlist", public=False, description="A test"
        )
        assert result["id"] == "pl123"

    async def test_create_playlist_no_user_profile(
        self, spotify_module: SpotifyModule, mock_mcp: MagicMock, mock_spotify_client: MagicMock
    ):
        """Returns error when user profile is missing."""
        spotify_module._client = mock_spotify_client
        spotify_module._credentials_ok = True
        spotify_module._user_profile = None
        await spotify_module.register_tools(mcp=mock_mcp, config={}, db=None)
        result = await mock_mcp._registered_tools["spotify_create_playlist"](name="Test")
        assert "error" in result

    async def test_add_tracks_to_playlist_calls_client(
        self, connected_module: SpotifyModule, mock_mcp: MagicMock
    ):
        tools = mock_mcp._registered_tools
        result = await tools["spotify_add_tracks_to_playlist"](
            playlist_id="pl123", uris=["spotify:track:t1"]
        )
        connected_module._client.add_tracks_to_playlist.assert_called_once_with(
            "pl123", ["spotify:track:t1"]
        )
        assert result["snapshot_id"] == "snap1"

    async def test_remove_tracks_from_playlist_calls_client(
        self, connected_module: SpotifyModule, mock_mcp: MagicMock
    ):
        tools = mock_mcp._registered_tools
        result = await tools["spotify_remove_tracks_from_playlist"](
            playlist_id="pl123", uris=["spotify:track:t1"]
        )
        connected_module._client.remove_tracks_from_playlist.assert_called_once_with(
            "pl123", ["spotify:track:t1"]
        )
        assert result["snapshot_id"] == "snap2"

    async def test_get_playlist_tracks_calls_client(
        self, connected_module: SpotifyModule, mock_mcp: MagicMock
    ):
        connected_module._client._get.return_value = {"items": [{"track": {"name": "Creep"}}]}
        tools = mock_mcp._registered_tools
        result = await tools["spotify_get_playlist_tracks"](playlist_id="pl123", limit=25, offset=0)
        connected_module._client._get.assert_called_with(
            "/playlists/pl123/tracks", params={"limit": 25, "offset": 0}
        )
        assert "items" in result

    async def test_get_playlist_tracks_caps_limit_at_100(
        self, connected_module: SpotifyModule, mock_mcp: MagicMock
    ):
        connected_module._client._get.return_value = {"items": []}
        tools = mock_mcp._registered_tools
        await tools["spotify_get_playlist_tracks"](playlist_id="pl123", limit=200)
        call_args = connected_module._client._get.call_args
        assert call_args[1]["params"]["limit"] == 100


# ---------------------------------------------------------------------------
# Group 6: Library management tools
# ---------------------------------------------------------------------------


class TestLibraryTools:
    """Test library management tools."""

    async def test_get_saved_tracks_calls_client(
        self, connected_module: SpotifyModule, mock_mcp: MagicMock
    ):
        connected_module._client.get_saved_tracks.return_value = {
            "items": [{"track": {"name": "Creep"}}]
        }
        tools = mock_mcp._registered_tools
        result = await tools["spotify_get_saved_tracks"](limit=10)
        connected_module._client.get_saved_tracks.assert_called_once_with(limit=10, offset=0)
        assert "items" in result

    async def test_get_saved_tracks_caps_limit_at_50(
        self, connected_module: SpotifyModule, mock_mcp: MagicMock
    ):
        tools = mock_mcp._registered_tools
        await tools["spotify_get_saved_tracks"](limit=100)
        call_args = connected_module._client.get_saved_tracks.call_args
        assert call_args[1]["limit"] == 50

    async def test_save_tracks_calls_client(
        self, connected_module: SpotifyModule, mock_mcp: MagicMock
    ):
        tools = mock_mcp._registered_tools
        result = await tools["spotify_save_tracks"](ids=["track1", "track2"])
        connected_module._client.save_tracks.assert_called_once_with(["track1", "track2"])
        assert result == {"status": "saved", "count": 2}

    async def test_remove_saved_tracks_calls_client(
        self, connected_module: SpotifyModule, mock_mcp: MagicMock
    ):
        tools = mock_mcp._registered_tools
        result = await tools["spotify_remove_saved_tracks"](ids=["track1"])
        connected_module._client.remove_saved_tracks.assert_called_once_with(["track1"])
        assert result == {"status": "removed", "count": 1}


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    """Test error handling for auth, rate limit, and API errors."""

    async def _tools(self, module: SpotifyModule, mock_mcp: MagicMock) -> dict[str, Any]:
        return mock_mcp._registered_tools

    async def test_auth_error_returns_actionable_message(
        self, connected_module: SpotifyModule, mock_mcp: MagicMock
    ):
        from butlers.connectors.spotify_client import SpotifyAuthError

        connected_module._client.search.side_effect = SpotifyAuthError("expired")
        tools = mock_mcp._registered_tools
        result = await tools["spotify_search"](query="test")
        assert result == {"error": _AUTH_EXPIRED_ERROR}

    async def test_rate_limit_error_returns_retry_message(
        self, connected_module: SpotifyModule, mock_mcp: MagicMock
    ):
        from butlers.connectors.spotify_client import SpotifyRateLimitError

        connected_module._client.get_queue.side_effect = SpotifyRateLimitError(30.0)
        tools = mock_mcp._registered_tools
        result = await tools["spotify_get_queue"]()
        assert "error" in result
        assert "30" in result["error"]
        assert "rate limited" in result["error"].lower()

    async def test_api_error_returns_status_and_body(
        self, connected_module: SpotifyModule, mock_mcp: MagicMock
    ):
        from butlers.connectors.spotify_client import SpotifyAPIError

        connected_module._client.get_saved_tracks.side_effect = SpotifyAPIError(
            500, "Internal Server Error"
        )
        tools = mock_mcp._registered_tools
        result = await tools["spotify_get_saved_tracks"]()
        assert "error" in result
        assert "500" in result["error"]

    async def test_premium_required_includes_product_tier(
        self, connected_module: SpotifyModule, mock_mcp: MagicMock
    ):
        from butlers.connectors.spotify_client import SpotifyAPIError

        connected_module._client.pause.side_effect = SpotifyAPIError(
            403, "Player command failed: premium required"
        )
        tools = mock_mcp._registered_tools
        result = await tools["spotify_pause"]()
        assert "error" in result
        # Should include account tier from cached profile
        assert "premium" in result["error"]


# ---------------------------------------------------------------------------
# Error message helpers
# ---------------------------------------------------------------------------


class TestErrorMessageHelpers:
    """Unit tests for error message helper functions."""

    def test_premium_required_error_includes_product(self):
        msg = _premium_required_error("free")
        assert "free" in msg
        assert "Premium" in msg

    def test_premium_required_error_unknown_product(self):
        msg = _premium_required_error(None)
        assert "unknown" in msg

    def test_rate_limited_error_includes_seconds(self):
        msg = _rate_limited_error(45.0)
        assert "45" in msg
        assert "rate limited" in msg.lower()
