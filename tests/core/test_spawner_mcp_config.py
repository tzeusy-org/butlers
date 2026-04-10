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
    async def test_only_butler_mcp_server_present_with_or_without_memory(self, tmp_path: Path):
        """MCP servers dict always contains only the butler server; memory fetch skipped when disabled."""
        # Memory enabled
        config_mem = _make_config(modules={"memory": {}})
        adapter_mem = MockAdapter()
        with patch(
            "butlers.core.spawner.fetch_memory_context",
            new_callable=AsyncMock,
            return_value=None,
        ):
            await Spawner(config=config_mem, config_dir=tmp_path, runtime=adapter_mem).trigger(
                prompt="test", trigger_source="trigger"
            )
        mcp_url = adapter_mem.calls[0]["mcp_servers"]["test-butler"]["url"]
        assert mcp_url.startswith("http://localhost:9100/mcp")
        assert "trigger_source=trigger" in mcp_url

        # Memory disabled → fetch not called, butler MCP server still present
        config_no_mem = _make_config(modules={})
        adapter_no_mem = MockAdapter()
        with patch(
            "butlers.core.spawner.fetch_memory_context",
            new_callable=AsyncMock,
        ):
            await Spawner(
                config=config_no_mem, config_dir=tmp_path, runtime=adapter_no_mem
            ).trigger(prompt="test", trigger_source="trigger")
        mcp_url_no_mem = adapter_no_mem.calls[0]["mcp_servers"]["test-butler"]["url"]
        assert mcp_url_no_mem.startswith("http://localhost:9100/mcp")
        assert "trigger_source=trigger" in mcp_url_no_mem


class TestMemoryFetchGating:
    async def test_memory_context_injected_into_system_prompt(self, tmp_path: Path):
        """Memory context appended to system prompt when enabled; not fetched when disabled."""
        (tmp_path / "CLAUDE.md").write_text("Base prompt.")

        # Memory enabled → context appended to system prompt
        adapter_sys = MockAdapter()
        with patch(
            "butlers.core.spawner.fetch_memory_context",
            new_callable=AsyncMock,
            return_value="Remembered: user likes TDD.",
        ):
            await Spawner(
                config=_make_config(modules={"memory": {}}),
                config_dir=tmp_path,
                runtime=adapter_sys,
            ).trigger(prompt="do task", trigger_source="trigger")
        assert (
            adapter_sys.calls[0]["system_prompt"] == "Base prompt.\n\nRemembered: user likes TDD."
        )

        # Memory disabled → prompt unchanged (fetch not called)
        adapter_off = MockAdapter()
        with patch(
            "butlers.core.spawner.fetch_memory_context",
            new_callable=AsyncMock,
        ):
            await Spawner(
                config=_make_config(modules={}), config_dir=tmp_path, runtime=adapter_off
            ).trigger(prompt="do task", trigger_source="trigger")
        assert adapter_off.calls[0]["system_prompt"] == "Base prompt."


class TestQaMcpGating:
    async def test_qa_trigger_receives_empty_mcp_servers(self, tmp_path: Path):
        """QA-triggered sessions pass empty mcp_servers to prevent Codex retry on bash-only runs."""
        adapter = MockAdapter()
        await Spawner(config=_make_config(), config_dir=tmp_path, runtime=adapter).trigger(
            prompt="investigate bug",
            trigger_source="qa",
        )
        assert adapter.calls[0]["mcp_servers"] == {}, (
            "QA sessions must receive empty mcp_servers to suppress MCP-discovery retry"
        )

    async def test_healing_trigger_receives_empty_mcp_servers(self, tmp_path: Path):
        """Healing-triggered sessions pass empty mcp_servers (existing behaviour, guarded)."""
        adapter = MockAdapter()
        await Spawner(config=_make_config(), config_dir=tmp_path, runtime=adapter).trigger(
            prompt="heal the branch",
            trigger_source="healing",
        )
        assert adapter.calls[0]["mcp_servers"] == {}, (
            "Healing sessions must receive empty mcp_servers"
        )

    async def test_non_qa_trigger_receives_butler_mcp_server(self, tmp_path: Path):
        """Non-QA/non-healing triggers still receive the butler MCP server."""
        adapter = MockAdapter()
        await Spawner(config=_make_config(), config_dir=tmp_path, runtime=adapter).trigger(
            prompt="do normal work",
            trigger_source="schedule:test-task",
        )
        mcp_servers = adapter.calls[0]["mcp_servers"]
        assert "test-butler" in mcp_servers, (
            "Non-QA triggers must receive the butler MCP server for tool access"
        )
