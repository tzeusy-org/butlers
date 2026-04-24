"""Integration-style tests for MCP warmup daemon lifecycle wiring.

Covers:
- Step 14b of run_startup: _warmup_mcp_endpoints_best_effort is scheduled as a
  background task after _start_mcp_server() completes.
- Warmup failure never propagates to daemon startup (best-effort contract).
- With warmup disabled (kill-switch), warmup_mcp_endpoints is still called but
  returns [] without hitting any network.
- Spawner still succeeds with warmup disabled (normal invocation unaffected).
- Source-level: lifecycle.py calls asyncio.create_task for warmup after
  _start_mcp_server, before the scheduler loop starts.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


class TestLifecycleWarmupWiring:
    """Step 14b: warmup is scheduled as a background task in lifecycle.run_startup."""

    def test_warmup_task_scheduled_after_mcp_server_start(self) -> None:
        """Source check: asyncio.create_task for warmup appears after _start_mcp_server
        and before the scheduler loop task in run_startup."""
        from butlers.lifecycle import run_startup

        src = inspect.getsource(run_startup)

        idx_mcp_start = src.index("_start_mcp_server")
        idx_warmup = src.index("_warmup_mcp_endpoints_best_effort")
        idx_scheduler = src.index("_scheduler_loop_task")

        assert idx_mcp_start < idx_warmup, (
            "warmup task must be scheduled after _start_mcp_server in run_startup"
        )
        assert idx_warmup < idx_scheduler, (
            "warmup task must appear before scheduler loop start in run_startup"
        )

    def test_warmup_wrapped_in_create_task(self) -> None:
        """Source check: warmup is wrapped in asyncio.create_task (non-blocking)."""
        from butlers.lifecycle import run_startup

        src = inspect.getsource(run_startup)

        # The warmup must be scheduled as a task so it doesn't block startup.
        # Find the line containing the warmup call and check it's part of a create_task block.
        warmup_idx = src.index("_warmup_mcp_endpoints_best_effort")
        # Walk backward from warmup_idx to find nearest asyncio.create_task
        preceding = src[:warmup_idx]
        # The create_task call should appear within the preceding 200 chars
        assert "asyncio.create_task(" in preceding[-200:], (
            "warmup must be wrapped in asyncio.create_task for non-blocking startup"
        )

    def test_lifecycle_imports_warmup_helper(self) -> None:
        """lifecycle.py defines _warmup_mcp_endpoints_best_effort."""
        from butlers import lifecycle

        assert hasattr(lifecycle, "_warmup_mcp_endpoints_best_effort"), (
            "_warmup_mcp_endpoints_best_effort must be defined in lifecycle module"
        )


class TestWarmupBestEffortContract:
    """Warmup must never propagate failures to the daemon."""

    async def test_warmup_failure_does_not_propagate(self) -> None:
        """_warmup_mcp_endpoints_best_effort swallows all exceptions."""
        from butlers.lifecycle import _warmup_mcp_endpoints_best_effort

        daemon = MagicMock()
        daemon.config.name = "test-butler"
        daemon.config.port = 9100

        with patch(
            "butlers.core.mcp_warmup.warmup_mcp_endpoints",
            new_callable=AsyncMock,
            side_effect=RuntimeError("warmup exploded"),
        ):
            # Must not raise — best-effort contract
            await _warmup_mcp_endpoints_best_effort(daemon)

    async def test_warmup_import_error_does_not_propagate(self) -> None:
        """Import error in warmup module is swallowed."""
        from butlers.lifecycle import _warmup_mcp_endpoints_best_effort

        daemon = MagicMock()
        daemon.config.name = "test-butler"
        daemon.config.port = 9100

        with patch(
            "butlers.core.mcp_warmup.warmup_mcp_endpoints",
            new_callable=AsyncMock,
            side_effect=ImportError("httpx not installed"),
        ):
            # Must not raise
            await _warmup_mcp_endpoints_best_effort(daemon)

    async def test_warmup_success_completes_silently(self) -> None:
        """Successful warmup completes without side-effects visible outside the task."""
        from butlers.lifecycle import _warmup_mcp_endpoints_best_effort

        daemon = MagicMock()
        daemon.config.name = "warmup-test"
        daemon.config.port = 9200

        warmup_results = [
            {
                "url": "http://localhost:9200/mcp",
                "success": True,
                "latency_ms": 8,
                "tool_count": 3,
                "error": None,
            }
        ]

        with patch(
            "butlers.core.mcp_warmup.warmup_mcp_endpoints",
            new_callable=AsyncMock,
            return_value=warmup_results,
        ) as mock_warmup:
            await _warmup_mcp_endpoints_best_effort(daemon)

        mock_warmup.assert_awaited_once_with("warmup-test", butler_port=9200)


class TestWarmupDoesNotRegressionSpawner:
    """Normal spawner invocation works regardless of warmup status."""

    async def test_spawner_succeeds_with_warmup_disabled(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Spawner trigger succeeds when warmup kill-switch is active."""
        from butlers.config import ButlerConfig
        from butlers.core.runtimes.base import RuntimeAdapter
        from butlers.core.spawner import Spawner

        monkeypatch.setenv("BUTLERS_MCP_WARMUP_DISABLED", "1")

        class _OkAdapter(RuntimeAdapter):
            @property
            def binary_name(self) -> str:
                return "mock"

            async def invoke(self, **kwargs: Any) -> tuple[Any, list, Any]:
                return ("done", [], None)

            def build_config_file(self, mcp_servers: Any, tmp_dir: Path) -> Path:
                p = tmp_dir / "cfg.json"
                p.write_text("{}")
                return p

            def parse_system_prompt_file(self, config_dir: Path) -> str:
                return ""

        config = ButlerConfig(name="test", port=9100)
        spawner = Spawner(config=config, config_dir=tmp_path, runtime=_OkAdapter())
        result = await spawner.trigger(prompt="hello", trigger_source="schedule:test")

        assert result.success is True
        assert result.output == "done"

    async def test_spawner_succeeds_with_warmup_enabled(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Spawner trigger succeeds with warmup enabled (kill-switch off)."""
        from butlers.config import ButlerConfig
        from butlers.core.runtimes.base import RuntimeAdapter
        from butlers.core.spawner import Spawner

        monkeypatch.delenv("BUTLERS_MCP_WARMUP_DISABLED", raising=False)

        class _OkAdapter(RuntimeAdapter):
            @property
            def binary_name(self) -> str:
                return "mock"

            async def invoke(self, **kwargs: Any) -> tuple[Any, list, Any]:
                return ("done", [], None)

            def build_config_file(self, mcp_servers: Any, tmp_dir: Path) -> Path:
                p = tmp_dir / "cfg.json"
                p.write_text("{}")
                return p

            def parse_system_prompt_file(self, config_dir: Path) -> str:
                return ""

        config = ButlerConfig(name="test", port=9100)
        spawner = Spawner(config=config, config_dir=tmp_path, runtime=_OkAdapter())
        result = await spawner.trigger(prompt="hello", trigger_source="schedule:test")

        assert result.success is True


class TestCodexSpawnTimingInstrumentation:
    """CodexAdapter records spawn_latency_ms and mcp_server_count in last_process_info."""

    async def test_spawn_latency_ms_recorded_on_success(self) -> None:
        """spawn_latency_ms is set in last_process_info after successful invoke."""
        from butlers.core.runtimes.codex import CodexAdapter

        adapter = CodexAdapter(codex_binary="/fake/codex")

        # Stub _run_codex_subprocess to avoid real subprocess
        async def fake_run(*args: Any, **kwargs: Any) -> tuple[str, list, None]:
            adapter._last_process_info = {
                "pid": 123,
                "exit_code": 0,
                "command": "codex ...",
                "stderr": "",
                "runtime_type": "codex",
            }
            return ("result", [{"name": "my_tool", "input": {}}], None)

        with patch.object(adapter, "_run_codex_subprocess", side_effect=fake_run):
            await adapter.invoke(
                prompt="test",
                system_prompt="",
                mcp_servers={"butler": {"url": "http://localhost:9100/mcp"}},
                env={},
            )

        info = adapter.last_process_info
        assert info is not None
        assert "spawn_latency_ms" in info, "spawn_latency_ms must be recorded in last_process_info"
        assert isinstance(info["spawn_latency_ms"], int)
        assert info["spawn_latency_ms"] >= 0
        assert info.get("mcp_server_count") == 1

    async def test_mcp_server_count_zero_for_empty_servers(self) -> None:
        """mcp_server_count is 0 when no MCP servers configured."""
        from butlers.core.runtimes.codex import CodexAdapter

        adapter = CodexAdapter(codex_binary="/fake/codex")

        async def fake_run(*args: Any, **kwargs: Any) -> tuple[str, list, None]:
            adapter._last_process_info = {
                "pid": 124,
                "exit_code": 0,
                "command": "codex ...",
                "stderr": "",
                "runtime_type": "codex",
            }
            return ("ok", [], None)

        with patch.object(adapter, "_run_codex_subprocess", side_effect=fake_run):
            await adapter.invoke(
                prompt="test",
                system_prompt="",
                mcp_servers={},
                env={},
            )

        info = adapter.last_process_info
        assert info is not None
        assert info.get("mcp_server_count") == 0
