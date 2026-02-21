"""Tests for Spawner MCP config and memory module gating."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from butlers.config import ButlerConfig, RuntimeConfig
from butlers.core.runtimes.base import RuntimeAdapter
from butlers.core.spawner import Spawner

pytestmark = pytest.mark.unit


class MockAdapter(RuntimeAdapter):
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    @property
    def binary_name(self) -> str:
        return "mock"

    async def invoke(
        self,
        prompt: str,
        system_prompt: str,
        mcp_servers: dict[str, Any],
        env: dict[str, str],
        max_turns: int = 20,
        model: str | None = None,
        cwd: Path | None = None,
        timeout: int | None = None,
    ) -> tuple[str | None, list[dict[str, Any]], dict[str, Any] | None]:
        self.calls.append(
            {
                "prompt": prompt,
                "system_prompt": system_prompt,
                "mcp_servers": mcp_servers,
                "env": env,
            }
        )
        return ("ok", [], None)

    def build_config_file(self, mcp_servers: dict[str, Any], tmp_dir: Path) -> Path:
        config_path = tmp_dir / "mock_config.json"
        config_path.write_text(json.dumps({"mcpServers": mcp_servers}))
        return config_path

    def parse_system_prompt_file(self, config_dir: Path) -> str:
        return ""


def _make_config(
    name: str = "test-butler",
    port: int = 9100,
    modules: dict[str, dict] | None = None,
) -> ButlerConfig:
    return ButlerConfig(
        name=name,
        port=port,
        runtime=RuntimeConfig(),
        modules=modules or {},
        env_required=[],
        env_optional=[],
    )


class TestSpawnerMcpServers:
    async def test_only_butler_mcp_server_is_present_with_memory_enabled(self, tmp_path: Path):
        config = _make_config(modules={"memory": {}})
        adapter = MockAdapter()
        spawner = Spawner(config=config, config_dir=tmp_path, runtime=adapter)

        with patch(
            "butlers.core.spawner.fetch_memory_context",
            new_callable=AsyncMock,
            return_value=None,
        ):
            await spawner.trigger(prompt="test", trigger_source="trigger")

        mcp_servers = adapter.calls[0]["mcp_servers"]
        assert mcp_servers == {"test-butler": {"url": "http://localhost:9100/mcp"}}

    async def test_only_butler_mcp_server_is_present_with_memory_disabled(self, tmp_path: Path):
        config = _make_config(modules={})
        adapter = MockAdapter()
        spawner = Spawner(config=config, config_dir=tmp_path, runtime=adapter)

        with patch(
            "butlers.core.spawner.fetch_memory_context",
            new_callable=AsyncMock,
        ) as mock_fetch:
            await spawner.trigger(prompt="test", trigger_source="trigger")
            mock_fetch.assert_not_called()

        mcp_servers = adapter.calls[0]["mcp_servers"]
        assert mcp_servers == {"test-butler": {"url": "http://localhost:9100/mcp"}}


class TestMemoryFetchGating:
    async def test_memory_context_fetched_when_module_enabled(self, tmp_path: Path):
        config = _make_config(modules={"memory": {}})
        adapter = MockAdapter()
        spawner = Spawner(config=config, config_dir=tmp_path, runtime=adapter)

        with patch(
            "butlers.core.spawner.fetch_memory_context",
            new_callable=AsyncMock,
            return_value="ctx",
        ) as mock_fetch:
            await spawner.trigger(prompt="do task", trigger_source="trigger")

        mock_fetch.assert_awaited_once_with(
            None,
            "test-butler",
            "do task",
            token_budget=3000,
        )

    async def test_memory_context_not_fetched_when_module_disabled(self, tmp_path: Path):
        config = _make_config(modules={})
        adapter = MockAdapter()
        spawner = Spawner(config=config, config_dir=tmp_path, runtime=adapter)

        with patch(
            "butlers.core.spawner.fetch_memory_context",
            new_callable=AsyncMock,
        ) as mock_fetch:
            await spawner.trigger(prompt="do task", trigger_source="trigger")

        mock_fetch.assert_not_called()

    async def test_context_budget_comes_from_modules_memory_config(self, tmp_path: Path):
        config = _make_config(
            modules={"memory": {"retrieval": {"context_token_budget": 7777}}},
        )
        adapter = MockAdapter()
        spawner = Spawner(config=config, config_dir=tmp_path, runtime=adapter)

        with patch(
            "butlers.core.spawner.fetch_memory_context",
            new_callable=AsyncMock,
            return_value=None,
        ) as mock_fetch:
            await spawner.trigger(prompt="do task", trigger_source="trigger")

        _, kwargs = mock_fetch.call_args
        assert kwargs["token_budget"] == 7777

    async def test_memory_context_appended_to_system_prompt(self, tmp_path: Path):
        (tmp_path / "CLAUDE.md").write_text("Base prompt.")
        config = _make_config(modules={"memory": {}})
        adapter = MockAdapter()
        spawner = Spawner(config=config, config_dir=tmp_path, runtime=adapter)

        with patch(
            "butlers.core.spawner.fetch_memory_context",
            new_callable=AsyncMock,
            return_value="Remembered: user likes TDD.",
        ):
            await spawner.trigger(prompt="do task", trigger_source="trigger")

        system_prompt = adapter.calls[0]["system_prompt"]
        assert system_prompt == "Base prompt.\n\nRemembered: user likes TDD."
