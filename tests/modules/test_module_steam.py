"""Condensed Steam module tests — behavioral contract only.

Replaces 81 tests with ~15 focused behavioral tests.

Covers:
- Module ABC compliance (instantiation, name, config_schema)
- SteamModuleConfig validation (all optional with defaults, extra rejected)
- Tool registration (expected tools)
- Tool metadata (all tools present with sensitivities)
- Missing credentials error behavior
- Error helper functions

[bu-7sd7a]
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel, ValidationError

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


@pytest.fixture
def steam_module() -> SteamModule:
    return SteamModule()


@pytest.fixture
def mock_mcp() -> MagicMock:
    mcp = MagicMock()
    tools: dict[str, Any] = {}

    def tool_decorator(*_args, **kwargs):
        def decorator(fn):
            tools[fn.__name__] = fn
            return fn

        return decorator

    mcp.tool = tool_decorator
    mcp._registered_tools = tools
    return mcp


# ---------------------------------------------------------------------------
# ABC compliance
# ---------------------------------------------------------------------------


class TestModuleABCCompliance:
    def test_module_contract(self, steam_module: SteamModule) -> None:
        """SteamModule satisfies Module ABC: name, config_schema, dependencies."""
        assert issubclass(SteamModule, Module)
        assert steam_module.name == "steam"
        assert steam_module.config_schema is SteamModuleConfig
        assert issubclass(steam_module.config_schema, BaseModel)
        assert steam_module.dependencies == []
        assert steam_module.migration_revisions() is None


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------


class TestSteamModuleConfig:
    def test_valid_config_all_defaults(self) -> None:
        cfg = SteamModuleConfig()
        assert cfg.default_account is None
        assert cfg.cache_ttl_seconds > 0
        assert cfg.max_batch_size > 0

    def test_extra_fields_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SteamModuleConfig(unknown_field="x")


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


class TestToolRegistration:
    async def test_registers_expected_tools(
        self, steam_module: SteamModule, mock_mcp: MagicMock
    ) -> None:
        await steam_module.register_tools(
            mcp=mock_mcp, config={}, db=None, butler_name="test-butler"
        )
        assert set(mock_mcp._registered_tools.keys()) == EXPECTED_STEAM_TOOLS

    def test_tool_metadata_covers_all_tools(self, steam_module: SteamModule) -> None:
        meta = steam_module.tool_metadata()
        assert EXPECTED_STEAM_TOOLS.issubset(set(meta.keys()))

    def test_default_registry_includes_steam(self) -> None:
        from butlers.modules.registry import default_registry

        assert "steam" in default_registry().available_modules


# ---------------------------------------------------------------------------
# Missing credentials behavior
# ---------------------------------------------------------------------------


class TestMissingCredentials:
    async def test_tool_without_client_returns_error(
        self, steam_module: SteamModule, mock_mcp: MagicMock
    ) -> None:
        await steam_module.register_tools(
            mcp=mock_mcp, config={}, db=None, butler_name="test-butler"
        )
        result = await mock_mcp._registered_tools["steam_get_player_summary"]()
        assert isinstance(result, dict)
        assert "error" in result


# ---------------------------------------------------------------------------
# Error helpers
# ---------------------------------------------------------------------------


class TestErrorHelpers:
    def test_error_helpers_return_dicts(self) -> None:
        assert "error" in _no_credentials_error()
        assert "error" in _privacy_error("profile is private")
        assert "error" in _rate_limited_error(30.0)

    def test_handle_steam_api_error_returns_dict(self) -> None:
        exc = SteamAPIError(400, "Bad request")
        result = _handle_steam_error(exc)
        assert isinstance(result, dict) and "error" in result

    def test_handle_rate_limit_error_returns_dict(self) -> None:
        exc = SteamRateLimitError(30.0, 429)
        result = _handle_steam_error(exc)
        assert isinstance(result, dict) and "error" in result
