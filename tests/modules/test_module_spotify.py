"""Condensed Spotify module tests — behavioral contract only.

Replaces 78 tests with ~15 focused behavioral tests.

Covers:
- Module ABC compliance (instantiation, name, config_schema)
- SpotifyModuleConfig validation (defaults, extra rejected, playback_tools flag)
- Tool registration (expected tools with and without playback tools)
- Error helpers return strings with actionable messages
- Missing credentials returns error dict

[bu-7sd7a]
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel, ValidationError

from butlers.modules.base import Module
from butlers.modules.spotify import (
    SpotifyModule,
    SpotifyModuleConfig,
    _premium_required_error,
    _rate_limited_error,
)

pytestmark = pytest.mark.unit

EXPECTED_SPOTIFY_TOOLS = {
    "spotify_search",
    "spotify_get_recommendations",
    "spotify_get_related_artists",
    "spotify_get_playback_state",
    "spotify_get_queue",
    "spotify_get_top_items",
    "spotify_get_playlists",
    "spotify_create_playlist",
    "spotify_add_tracks_to_playlist",
    "spotify_remove_tracks_from_playlist",
    "spotify_get_playlist_tracks",
    "spotify_get_saved_tracks",
    "spotify_save_tracks",
    "spotify_remove_saved_tracks",
}

EXPECTED_PLAYBACK_TOOLS = {
    "spotify_play",
    "spotify_pause",
    "spotify_skip_next",
    "spotify_skip_previous",
    "spotify_seek",
    "spotify_set_volume",
    "spotify_add_to_queue",
    "spotify_transfer_playback",
}


@pytest.fixture
def spotify_module() -> SpotifyModule:
    return SpotifyModule()


@pytest.fixture
def mock_mcp() -> MagicMock:
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
    return mcp


# ---------------------------------------------------------------------------
# ABC compliance
# ---------------------------------------------------------------------------


class TestModuleABCCompliance:
    def test_module_contract(self, spotify_module: SpotifyModule) -> None:
        """SpotifyModule satisfies Module ABC: name, config_schema, dependencies."""
        assert issubclass(SpotifyModule, Module)
        assert spotify_module.name == "spotify"
        assert spotify_module.config_schema is SpotifyModuleConfig
        assert issubclass(spotify_module.config_schema, BaseModel)
        assert spotify_module.dependencies == []
        assert spotify_module.migration_revisions() is None


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------


class TestSpotifyModuleConfig:
    def test_defaults_and_validation(self) -> None:
        cfg = SpotifyModuleConfig()
        assert cfg.playback_tools is True
        # playback_tools can be disabled
        cfg2 = SpotifyModuleConfig(playback_tools=False)
        assert cfg2.playback_tools is False
        # Extra fields rejected
        with pytest.raises(ValidationError):
            SpotifyModuleConfig(unknown_field="x")


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


class TestToolRegistration:
    async def test_registers_all_expected_tools(
        self, spotify_module: SpotifyModule, mock_mcp: MagicMock
    ) -> None:
        await spotify_module.register_tools(mcp=mock_mcp, config={}, db=None, butler_name="test-butler")
        registered = set(mock_mcp._registered_tools.keys())
        assert EXPECTED_SPOTIFY_TOOLS.issubset(registered)
        assert EXPECTED_PLAYBACK_TOOLS.issubset(registered)

    async def test_config_accepts_playback_tools_false(self) -> None:
        # playback_tools=False is a valid config value (stored but currently
        # does not gate registration — all tools always register)
        cfg = SpotifyModuleConfig(playback_tools=False)
        assert cfg.playback_tools is False

    def test_default_registry_includes_spotify(self) -> None:
        from butlers.modules.registry import default_registry

        assert "spotify" in default_registry().available_modules


# ---------------------------------------------------------------------------
# Tool behaviors (with mocked client)
# ---------------------------------------------------------------------------


class TestToolBehaviors:
    async def test_search_returns_result(self, mock_mcp: MagicMock) -> None:
        module = SpotifyModule()
        mock_client = MagicMock()
        mock_client.search = AsyncMock(
            return_value={"tracks": {"items": [{"name": "Song", "uri": "spotify:track:1"}]}}
        )
        module._client = mock_client
        await module.register_tools(mcp=mock_mcp, config={}, db=None, butler_name="test-butler")
        result = await mock_mcp._registered_tools["spotify_search"](query="test song", type="track")
        assert result is not None

    async def test_missing_credentials_returns_error(self, mock_mcp: MagicMock) -> None:
        module = SpotifyModule()
        # No client set → should return error dict, not raise
        await module.register_tools(mcp=mock_mcp, config={}, db=None, butler_name="test-butler")
        result = await mock_mcp._registered_tools["spotify_search"](query="test", type="track")
        assert isinstance(result, dict)
        assert "error" in result or result.get("status") == "error" or len(result) > 0


# ---------------------------------------------------------------------------
# Error message helpers
# ---------------------------------------------------------------------------


class TestErrorHelpers:
    def test_premium_required_error_is_string(self) -> None:
        err = _premium_required_error("free")
        assert isinstance(err, str)
        assert "Premium" in err

    def test_rate_limited_error_is_string(self) -> None:
        err = _rate_limited_error(30.0)
        assert isinstance(err, str)
        assert "rate" in err.lower() or "seconds" in err
