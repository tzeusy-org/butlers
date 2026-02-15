"""Tests for daemon-side audit logging in the Spawner."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.config import ButlerConfig, RuntimeConfig
from butlers.core.runtimes.base import RuntimeAdapter
from butlers.core.spawner import Spawner

pytestmark = pytest.mark.unit


class _OkAdapter(RuntimeAdapter):
    """Adapter that always returns a successful result."""

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
        return ("ok", [{"tool": "t1"}], {"input_tokens": 10, "output_tokens": 20})

    def build_config_file(self, mcp_servers: dict[str, Any], tmp_dir: Path) -> Path:
        return tmp_dir / "cfg.json"

    def parse_system_prompt_file(self, config_dir: Path) -> str:
        return ""


class _FailAdapter(RuntimeAdapter):
    """Adapter that always raises."""

    @property
    def binary_name(self) -> str:
        return "mock"

    async def invoke(self, **kwargs: Any) -> Any:
        raise RuntimeError("adapter boom")

    def build_config_file(self, mcp_servers: dict[str, Any], tmp_dir: Path) -> Path:
        return tmp_dir / "cfg.json"

    def parse_system_prompt_file(self, config_dir: Path) -> str:
        return ""


def _make_config(name: str = "test-butler", port: int = 9100) -> ButlerConfig:
    return ButlerConfig(
        name=name,
        port=port,
        runtime=RuntimeConfig(),
        modules={},
        env_required=[],
        env_optional=[],
    )


class TestSpawnerAuditLogging:
    async def test_audit_entry_written_on_success(self, tmp_path: Path):
        audit_pool = MagicMock()
        audit_pool.execute = AsyncMock()

        spawner = Spawner(
            config=_make_config(),
            config_dir=tmp_path,
            runtime=_OkAdapter(),
            audit_pool=audit_pool,
        )

        result = await spawner.trigger(prompt="do work", trigger_source="tick")

        assert result.success is True
        audit_pool.execute.assert_awaited_once()
        args = audit_pool.execute.call_args[0]
        assert "INSERT INTO dashboard_audit_log" in args[0]
        assert args[1] == "test-butler"
        assert args[2] == "session"
        summary = json.loads(args[3])
        assert summary["trigger_source"] == "tick"
        assert summary["prompt"] == "do work"
        assert summary["tool_calls_count"] == 1
        assert summary["input_tokens"] == 10
        assert summary["output_tokens"] == 20
        # result = success
        assert args[4] == "success"
        # error = None
        assert args[5] is None

    async def test_audit_entry_written_on_error(self, tmp_path: Path):
        audit_pool = MagicMock()
        audit_pool.execute = AsyncMock()

        spawner = Spawner(
            config=_make_config(),
            config_dir=tmp_path,
            runtime=_FailAdapter(),
            audit_pool=audit_pool,
        )

        result = await spawner.trigger(prompt="do work", trigger_source="schedule:daily")

        assert result.success is False
        audit_pool.execute.assert_awaited_once()
        args = audit_pool.execute.call_args[0]
        assert args[1] == "test-butler"
        assert args[2] == "session"
        summary = json.loads(args[3])
        assert summary["trigger_source"] == "schedule:daily"
        # result = error
        assert args[4] == "error"
        # error message present
        assert "adapter boom" in args[5]

    async def test_no_audit_when_pool_is_none(self, tmp_path: Path):
        """Spawner with audit_pool=None should not raise or write."""
        spawner = Spawner(
            config=_make_config(),
            config_dir=tmp_path,
            runtime=_OkAdapter(),
            audit_pool=None,
        )

        with patch("butlers.core.spawner.write_audit_entry", new_callable=AsyncMock) as mock_write:
            result = await spawner.trigger(prompt="do work", trigger_source="tick")

        assert result.success is True
        # write_audit_entry is still called but returns immediately (pool is None)
        mock_write.assert_awaited()
        # Verify pool=None was passed
        assert mock_write.call_args[0][0] is None

    async def test_prompt_truncated_in_audit_summary(self, tmp_path: Path):
        audit_pool = MagicMock()
        audit_pool.execute = AsyncMock()

        spawner = Spawner(
            config=_make_config(),
            config_dir=tmp_path,
            runtime=_OkAdapter(),
            audit_pool=audit_pool,
        )

        long_prompt = "x" * 500
        await spawner.trigger(prompt=long_prompt, trigger_source="trigger")

        args = audit_pool.execute.call_args[0]
        summary = json.loads(args[3])
        assert len(summary["prompt"]) == 200
