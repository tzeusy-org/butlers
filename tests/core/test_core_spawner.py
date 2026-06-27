"""Tests for the Spawner orchestration layer (butlers-0qp.8, butlers-f3t.7) — condensed.

Covers:
- Serial dispatch (lock prevents concurrent execution)
- Credential passthrough (only declared vars included)
- Session logging wired correctly
- SpawnerResult construction on success and error
- Parametrized orchestration tests across all runtime adapters
- Model passthrough to SDK options and session logging

Orchestration tests use a MockAdapter (runtime-agnostic). Adapter-specific
unit tests live in test_runtime_adapter.py, test_codex_adapter.py, and
test_gemini_adapter.py.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from butlers.config import ButlerConfig, RuntimeSeedConfig
from butlers.core.runtimes import DEFAULT_RUNTIME_TYPE
from butlers.core.runtimes.base import RuntimeAdapter
from butlers.core.spawner import (
    Spawner,
    SpawnerResult,
    _append_runtime_session_query,
    _build_env,
    _merge_tool_call_records,
)

pytestmark = pytest.mark.unit

# Fake catalog entry UUID used in resolve_model mock return values (4-tuple)
_FAKE_CATALOG_ID = uuid.UUID("00000000-0000-0000-0000-000000000099")


def test_append_runtime_session_query():
    """Adds param to clean URL; preserves existing query params."""
    url = _append_runtime_session_query("http://localhost:9100/mcp", "sess-123")
    assert url == "http://localhost:9100/mcp?runtime_session_id=sess-123"
    url2 = _append_runtime_session_query("http://localhost:9100/mcp?x=1", "sess-123")
    assert url2 in (
        "http://localhost:9100/mcp?x=1&runtime_session_id=sess-123",
        "http://localhost:9100/mcp?runtime_session_id=sess-123&x=1",
    )


def test_merge_tool_call_records():
    """Dedupes same name+payload; preserves failed-then-retried sequence."""
    # Dedup: executed has two calls with same name/input as parsed; result = both unique entries
    parsed = [{"name": "route_to_butler", "input": {"butler": "relationship"}}]
    executed = [
        {"name": "route_to_butler", "input": {"butler": "relationship"}},
        {"name": "route_to_butler", "input": {"butler": "health"}},
    ]
    assert _merge_tool_call_records(parsed, executed, butler_name="switchboard") == [
        {"name": "route_to_butler", "input": {"butler": "relationship"}},
        {"name": "route_to_butler", "input": {"butler": "health"}},
    ]

    # Failed-then-retried sequence preserved (different ids or anonymous)
    parsed2 = [{"name": "route_to_butler", "input": {"butler": "relationship"}}]
    executed2 = [
        {
            "name": "route_to_butler",
            "input": {"butler": "relationship"},
            "outcome": "error",
            "error": "TimeoutError: target unavailable",
        },
        {
            "name": "route_to_butler",
            "input": {"butler": "relationship"},
            "outcome": "success",
            "result": {"status": "accepted", "butler": "relationship"},
        },
    ]
    assert _merge_tool_call_records(parsed2, executed2, butler_name="switchboard") == [
        {
            "name": "route_to_butler",
            "input": {"butler": "relationship"},
            "outcome": "error",
            "error": "TimeoutError: target unavailable",
        },
        {
            "name": "route_to_butler",
            "input": {"butler": "relationship"},
            "outcome": "success",
            "result": {"status": "accepted", "butler": "relationship"},
        },
    ]


def test_merge_tool_call_records_dedup_by_id():
    """Duplicate parsed records with the same id are collapsed (last wins)."""
    # Simulates item.started + item.completed both parsed as tool calls
    parsed = [
        {
            "id": "cmd1",
            "name": "command_execution",
            "input": {
                "command": "ls",
                "status": "in_progress",
                "exit_code": None,
                "aggregated_output": "",
            },
        },
        {
            "id": "cmd1",
            "name": "command_execution",
            "input": {
                "command": "ls",
                "status": "completed",
                "exit_code": 0,
                "aggregated_output": "file.txt\n",
            },
        },
    ]
    merged = _merge_tool_call_records(parsed, [], butler_name="switchboard")
    assert len(merged) == 1
    assert merged[0]["input"]["status"] == "completed"


# ---------------------------------------------------------------------------
# MockAdapter — runtime-agnostic adapter for orchestration tests
# ---------------------------------------------------------------------------


class MockAdapter(RuntimeAdapter):
    """A fully in-process mock adapter for testing Spawner orchestration.

    Does not depend on any CLI binary or SDK. Invoke behavior is controlled
    via constructor parameters:
    - result_text / tool_calls: returned on success
    - error: if set, invoke() raises RuntimeError with this message
    - delay: if > 0, invoke() sleeps for this many seconds before returning
    - capture: if True, records all invoke() calls in .calls list
    - usage: if set, returned as the usage dict (token counts etc.)
    """

    def __init__(
        self,
        *,
        result_text: str | None = "",
        tool_calls: list[dict[str, Any]] | None = None,
        error: str | None = None,
        delay: float = 0,
        capture: bool = False,
        usage: dict[str, Any] | None = None,
    ) -> None:
        self._result_text = result_text
        self._tool_calls = tool_calls or []
        self._error = error
        self._delay = delay
        self._capture = capture
        self._usage = usage
        self.calls: list[dict[str, Any]] = []
        self._call_count = 0
        self.reset_calls = 0

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
        self._call_count += 1
        if self._capture:
            self.calls.append(
                {
                    "prompt": prompt,
                    "system_prompt": system_prompt,
                    "mcp_servers": mcp_servers,
                    "env": env,
                    "cwd": cwd,
                    "timeout": timeout,
                }
            )
        if self._delay > 0:
            await asyncio.sleep(self._delay)
        if self._error:
            raise RuntimeError(self._error)
        return self._result_text, list(self._tool_calls), self._usage

    async def reset(self) -> None:
        self.reset_calls += 1

    def build_config_file(
        self,
        mcp_servers: dict[str, Any],
        tmp_dir: Path,
    ) -> Path:
        config_path = tmp_dir / "mock_config.json"
        config_path.write_text(json.dumps({"mcpServers": mcp_servers}))
        return config_path

    def parse_system_prompt_file(self, config_dir: Path) -> str:
        return ""


class SequenceMockAdapter(MockAdapter):
    """Mock adapter that returns different results on successive calls.

    Used for tests like lock-released-on-error where the first call
    fails and the second succeeds.
    """

    def __init__(
        self,
        sequence: list[dict[str, Any]],
    ) -> None:
        super().__init__()
        self._sequence = sequence
        self._call_index = 0

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
        idx = self._call_index
        self._call_index += 1
        entry = self._sequence[idx] if idx < len(self._sequence) else self._sequence[-1]
        if entry.get("delay"):
            await asyncio.sleep(entry["delay"])
        if entry.get("error"):
            raise RuntimeError(entry["error"])
        return entry.get("result_text", ""), entry.get("tool_calls", []), entry.get("usage")


class TrackingMockAdapter(MockAdapter):
    """Mock adapter that tracks start/end of each invoke for serialization tests."""

    def __init__(self) -> None:
        super().__init__()
        self.execution_log: list[tuple[str, str]] = []

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
        self.execution_log.append(("start", prompt))
        await asyncio.sleep(0.03)
        self.execution_log.append(("end", prompt))
        return f"result-{prompt}", [], None


class WorkerFactoryMockAdapter(RuntimeAdapter):
    """Adapter that produces a fresh worker instance for each invocation."""

    def __init__(self) -> None:
        self.created_worker_ids: list[int] = []
        self.invoked_worker_ids: list[int] = []
        self._next_worker_id = 0

    @property
    def binary_name(self) -> str:
        return "worker-factory-mock"

    def create_worker(self) -> RuntimeAdapter:
        self._next_worker_id += 1
        worker_id = self._next_worker_id
        self.created_worker_ids.append(worker_id)
        return _WorkerAdapter(worker_id=worker_id, factory=self)

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
        raise AssertionError("Spawner should invoke worker adapters, not factory adapter")

    def build_config_file(
        self,
        mcp_servers: dict[str, Any],
        tmp_dir: Path,
    ) -> Path:
        config_path = tmp_dir / "mock_factory_config.json"
        config_path.write_text(json.dumps({"mcpServers": mcp_servers}))
        return config_path

    def parse_system_prompt_file(self, config_dir: Path) -> str:
        return ""


class _WorkerAdapter(RuntimeAdapter):
    """Concrete worker adapter emitted by WorkerFactoryMockAdapter."""

    def __init__(self, worker_id: int, factory: WorkerFactoryMockAdapter) -> None:
        self._worker_id = worker_id
        self._factory = factory

    @property
    def binary_name(self) -> str:
        return "worker-mock"

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
        self._factory.invoked_worker_ids.append(self._worker_id)
        return f"worker-{self._worker_id}:{prompt}", [], None

    def build_config_file(
        self,
        mcp_servers: dict[str, Any],
        tmp_dir: Path,
    ) -> Path:
        config_path = tmp_dir / "mock_worker_config.json"
        config_path.write_text(json.dumps({"mcpServers": mcp_servers}))
        return config_path

    def parse_system_prompt_file(self, config_dir: Path) -> str:
        return ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_SENTINEL = object()


def _make_config(
    name: str = "test-butler",
    port: int = 9100,
    env_required: list[str] | None = None,
    env_optional: list[str] | None = None,
    modules: dict[str, dict] | None = None,
    model: str | None | object = _SENTINEL,
    max_concurrent_sessions: int = 1,
    max_queued_sessions: int = 100,
    session_timeout_s: int = 1800,
) -> ButlerConfig:
    # ``model`` and ``session_timeout_s`` used to live on the butler config as
    # last-resort fallbacks. They are now hard-coded spawner constants —
    # the parameters remain on this factory for signature compatibility but
    # are intentionally unused; the catalog or ``timeout_override`` is the
    # only way to vary them at spawn time.
    del model, session_timeout_s
    runtime_seed = RuntimeSeedConfig(
        max_concurrent_sessions=max_concurrent_sessions,
        max_queued_sessions=max_queued_sessions,
    )
    return ButlerConfig(
        name=name,
        port=port,
        runtime_seed=runtime_seed,
        modules=modules or {},
        env_required=env_required or [],
        env_optional=env_optional or [],
    )


# ---------------------------------------------------------------------------
# 8.2: Spawner result and invocation (runtime-agnostic)
# ---------------------------------------------------------------------------


class TestSpawnerResult:
    """SpawnerResult dataclass behavior."""

    def test_result_fields(self):
        """Default values, success/error/token fields all populated correctly."""
        r = SpawnerResult()
        assert r.output is None
        assert r.tool_calls == []
        assert r.error is None
        assert r.duration_ms == 0
        assert r.model is None
        assert r.input_tokens is None
        assert r.output_tokens is None

        r2 = SpawnerResult(output="output_text", tool_calls=[{"name": "t"}], duration_ms=42)
        assert r2.output == "output_text"
        assert len(r2.tool_calls) == 1
        assert r2.error is None

        r3 = SpawnerResult(error="something broke", duration_ms=10)
        assert r3.output is None
        assert r3.error == "something broke"

        r4 = SpawnerResult(
            output="output_text",
            model="claude-opus-4-20250514",
            duration_ms=42,
            input_tokens=1500,
            output_tokens=2500,
        )
        assert r4.model == "claude-opus-4-20250514"
        assert r4.input_tokens == 1500
        assert r4.output_tokens == 2500


class TestSpawnerInvocation:
    """Tests for runtime invocation via Spawner.trigger() with MockAdapter."""

    async def test_success_result_tool_calls_and_duration(self, tmp_path: Path):
        """Success returns output; tool calls captured; duration measured."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()

        # Basic success
        adapter = MockAdapter(result_text="Hello from mock!")
        spawner = Spawner(config=config, config_dir=config_dir, runtime=adapter)
        result = await spawner.trigger("hello", "tick")
        assert result.output == "Hello from mock!"
        assert result.error is None
        assert result.duration_ms >= 0

        # Tool calls captured
        adapter2 = MockAdapter(
            result_text="Done with tools",
            tool_calls=[{"id": "tool_1", "name": "state_get", "input": {"key": "foo"}}],
        )
        result2 = await Spawner(config=config, config_dir=config_dir, runtime=adapter2).trigger(
            "use tools", "trigger_tool"
        )
        assert result2.output == "Done with tools"
        assert len(result2.tool_calls) == 1
        assert result2.tool_calls[0]["name"] == "state_get"

        # Duration measured for slow adapter
        slow_adapter = MockAdapter(result_text="slow result", delay=0.05)
        result3 = await Spawner(config=config, config_dir=config_dir, runtime=slow_adapter).trigger(
            "slow", "tick"
        )
        assert result3.duration_ms >= 40

    async def test_runtime_args_forwarded_to_runtime_invoke(self, tmp_path: Path):
        """Catalog-resolved runtime args are forwarded to adapter invoke kwargs."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()

        captured: dict[str, Any] = {}

        class RuntimeArgsCaptureAdapter(RuntimeAdapter):
            @property
            def binary_name(self) -> str:
                return "runtime-args-capture"

            async def invoke(
                self,
                prompt: str,
                system_prompt: str,
                mcp_servers: dict[str, Any],
                env: dict[str, str],
                max_turns: int = 20,
                model: str | None = None,
                runtime_args: list[str] | None = None,
                cwd: Path | None = None,
                timeout: int | None = None,
            ) -> tuple[str | None, list[dict[str, Any]], dict[str, Any] | None]:
                captured["runtime_args"] = runtime_args
                return "ok", [], None

            def build_config_file(self, mcp_servers: dict[str, Any], tmp_dir: Path) -> Path:
                config_path = tmp_dir / "capture_config.json"
                config_path.write_text(json.dumps({"mcpServers": mcp_servers}))
                return config_path

            def parse_system_prompt_file(self, config_dir: Path) -> str:
                return ""

        mock_pool = AsyncMock()
        spawner = Spawner(
            config=config,
            config_dir=config_dir,
            pool=mock_pool,
            runtime=RuntimeArgsCaptureAdapter(),
        )

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=(
                    "codex",
                    "gpt-5.4-mini",
                    ["--config", 'model_reasoning_effort="high"'],
                    7,
                    600,
                    "workhorse",
                ),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=SimpleNamespace(
                    allowed=True,
                    usage_24h=0,
                    usage_30d=0,
                    limit_24h=None,
                    limit_30d=None,
                ),
            ),
            patch("butlers.core.spawner.record_token_usage", new_callable=AsyncMock),
        ):
            mock_create.return_value = uuid.UUID("00000000-0000-0000-0000-000000000001")
            result = await spawner.trigger("hello", "tick")

        assert result.success is True
        assert captured["runtime_args"] == ["--config", 'model_reasoning_effort="high"']

    async def test_session_timeout_forwarded_to_runtime_invoke(self, tmp_path: Path):
        """Hard-coded fallback session timeout is forwarded when neither catalog nor
        override is set."""
        from butlers.core.spawner import _FALLBACK_SESSION_TIMEOUT_S

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()

        captured: dict[str, Any] = {}

        class RuntimeTimeoutCaptureAdapter(RuntimeAdapter):
            @property
            def binary_name(self) -> str:
                return "runtime-timeout-capture"

            async def invoke(
                self,
                prompt: str,
                system_prompt: str,
                mcp_servers: dict[str, Any],
                env: dict[str, str],
                max_turns: int = 20,
                model: str | None = None,
                runtime_args: list[str] | None = None,
                cwd: Path | None = None,
                timeout: int | None = None,
            ) -> tuple[str | None, list[dict[str, Any]], dict[str, Any] | None]:
                captured["timeout"] = timeout
                return "ok", [], None

            def build_config_file(self, mcp_servers: dict[str, Any], tmp_dir: Path) -> Path:
                config_path = tmp_dir / "capture_config.json"
                config_path.write_text(json.dumps({"mcpServers": mcp_servers}))
                return config_path

            def parse_system_prompt_file(self, config_dir: Path) -> str:
                return ""

        spawner = Spawner(
            config=config,
            config_dir=config_dir,
            runtime=RuntimeTimeoutCaptureAdapter(),
        )

        result = await spawner.trigger("hello", "tick")

        assert result.success is True
        assert captured["timeout"] == _FALLBACK_SESSION_TIMEOUT_S

    async def test_timeout_override_takes_precedence(self, tmp_path: Path):
        """timeout_override overrides both the catalog and the hard-coded fallback."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()

        captured: dict[str, Any] = {}

        class RuntimeTimeoutCaptureAdapter(RuntimeAdapter):
            @property
            def binary_name(self) -> str:
                return "runtime-timeout-capture"

            async def invoke(
                self,
                prompt: str,
                system_prompt: str,
                mcp_servers: dict[str, Any],
                env: dict[str, str],
                max_turns: int = 20,
                model: str | None = None,
                runtime_args: list[str] | None = None,
                cwd: Path | None = None,
                timeout: int | None = None,
            ) -> tuple[str | None, list[dict[str, Any]], dict[str, Any] | None]:
                captured["timeout"] = timeout
                return "ok", [], None

            def build_config_file(self, mcp_servers: dict[str, Any], tmp_dir: Path) -> Path:
                config_path = tmp_dir / "capture_config.json"
                config_path.write_text(json.dumps({"mcpServers": mcp_servers}))
                return config_path

            def parse_system_prompt_file(self, config_dir: Path) -> str:
                return ""

        spawner = Spawner(
            config=config,
            config_dir=config_dir,
            runtime=RuntimeTimeoutCaptureAdapter(),
        )

        result = await spawner.trigger("hello", "tick", timeout_override=1800)

        assert result.success is True
        assert captured["timeout"] == 1800

    async def test_adapter_timeout_error_is_not_masked_by_spawner_guard(self, tmp_path: Path):
        """Adapters own timeout cleanup and diagnostics; the spawner guard is only
        a backstop for runtimes that do not return after their timeout."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()

        class AdapterManagedTimeout(RuntimeAdapter):
            def __init__(self) -> None:
                self.reset_calls = 0

            @property
            def binary_name(self) -> str:
                return "adapter-managed-timeout"

            async def invoke(
                self,
                prompt: str,
                system_prompt: str,
                mcp_servers: dict[str, Any],
                env: dict[str, str],
                max_turns: int = 20,
                model: str | None = None,
                runtime_args: list[str] | None = None,
                cwd: Path | None = None,
                timeout: int | None = None,
            ) -> tuple[str | None, list[dict[str, Any]], dict[str, Any] | None]:
                assert timeout is not None
                await asyncio.sleep(timeout)
                raise TimeoutError(f"adapter timed out after {timeout} seconds")

            async def reset(self) -> None:
                self.reset_calls += 1

            def build_config_file(self, mcp_servers: dict[str, Any], tmp_dir: Path) -> Path:
                config_path = tmp_dir / "adapter_timeout_config.json"
                config_path.write_text(json.dumps({"mcpServers": mcp_servers}))
                return config_path

            def parse_system_prompt_file(self, config_dir: Path) -> str:
                return ""

        adapter = AdapterManagedTimeout()
        result = await Spawner(config=config, config_dir=config_dir, runtime=adapter).trigger(
            "slow", "tick", timeout_override=1
        )

        assert result.success is False
        assert result.error == "TimeoutError: adapter timed out after 1 seconds"
        assert adapter.reset_calls == 1

    async def test_error_wrapping_and_reset_behavior(self, tmp_path: Path):
        """Adapter error is wrapped in result with reset called; pre-invoke failure skips reset."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()

        # Adapter error wrapped in result; reset called once
        adapter = MockAdapter(error="adapter connection failed")
        result = await Spawner(config=config, config_dir=config_dir, runtime=adapter).trigger(
            "fail", "tick"
        )
        assert result.error is not None
        assert "RuntimeError" in result.error and "adapter connection failed" in result.error
        assert result.output is None and result.duration_ms >= 0
        assert adapter.reset_calls == 1

        # Pre-invoke failure (before runtime invocation) → reset not called
        adapter2 = MockAdapter()
        spawner2 = Spawner(config=config, config_dir=config_dir, runtime=adapter2)
        with patch("butlers.core.spawner.read_system_prompt", side_effect=RuntimeError("boom")):
            result2 = await spawner2.trigger("hi", "tick")
        assert result2.success is False and "RuntimeError: boom" in result2.error
        assert adapter2.calls == [] and adapter2.reset_calls == 0

    async def test_pre_spawn_mcp_warmup_runs_once_per_endpoint(self, tmp_path: Path) -> None:
        """First MCP-backed spawn warms the endpoint; later spawns reuse it."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config(port=9410)
        adapter = MockAdapter(result_text="ok", capture=True)

        warmup_results = [
            {
                "url": "http://127.0.0.1:9410/mcp",
                "success": True,
                "latency_ms": 5,
                "tool_count": 3,
                "error": None,
            }
        ]

        with patch(
            "butlers.core.mcp_warmup.warmup_mcp_urls",
            new_callable=AsyncMock,
            return_value=warmup_results,
        ) as mock_warmup:
            spawner = Spawner(config=config, config_dir=config_dir, runtime=adapter)
            first = await spawner.trigger("hello", "schedule:first")
            second = await spawner.trigger("again", "schedule:second")

        assert first.success is True
        assert second.success is True
        mock_warmup.assert_awaited_once_with("test-butler", ["http://127.0.0.1:9410/mcp"])
        assert len(adapter.calls) == 2

    async def test_pre_spawn_mcp_warmup_failure_does_not_block_session(
        self, tmp_path: Path
    ) -> None:
        """Warmup failures are best-effort; the runtime still runs."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config(port=9420)
        adapter = MockAdapter(result_text="ok", capture=True)

        with patch(
            "butlers.core.mcp_warmup.warmup_mcp_urls",
            new_callable=AsyncMock,
            side_effect=RuntimeError("warmup exploded"),
        ) as mock_warmup:
            spawner = Spawner(config=config, config_dir=config_dir, runtime=adapter)
            result = await spawner.trigger("hello", "schedule:test")

        assert result.success is True
        mock_warmup.assert_awaited_once_with("test-butler", ["http://127.0.0.1:9420/mcp"])
        assert len(adapter.calls) == 1

    async def test_terminal_codex_mcp_discovery_failure_marks_session_failed(
        self, tmp_path: Path
    ) -> None:
        """Repeated MCP-discovery failure must propagate as a failed session, not success."""
        from butlers.core.runtimes import CodexAdapter

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()
        mock_pool = AsyncMock()
        adapter = CodexAdapter(codex_binary="/usr/bin/codex")

        async def _mock_exec(*args, **kwargs):
            proc = AsyncMock()
            proc.returncode = 0
            proc.pid = 42
            proc.communicate = AsyncMock(
                return_value=(
                    (
                        json.dumps(
                            {
                                "type": "item.completed",
                                "item": {
                                    "id": "cmd1",
                                    "type": "command_execution",
                                    "command": "/bin/bash -lc true",
                                    "status": "completed",
                                    "exit_code": 0,
                                    "aggregated_output": "",
                                },
                            }
                        )
                        + "\n"
                        + json.dumps({"type": "result", "result": "MCP tools called: none."})
                    ).encode(),
                    b"MCP connection failed: connection refused",
                )
            )
            return proc

        with (
            patch(
                "butlers.core.spawner.session_create",
                new_callable=AsyncMock,
                return_value=uuid.UUID("00000000-0000-0000-0000-000000000001"),
            ),
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock) as mock_complete,
            patch("butlers.core.spawner.session_process_log_write", new_callable=AsyncMock),
            patch(
                "butlers.core.runtimes.codex.asyncio.create_subprocess_exec",
                side_effect=_mock_exec,
            ),
            patch("butlers.core.runtimes.codex._MCP_RETRY_DELAYS", (0, 0)),
        ):
            result = await Spawner(
                config=config,
                config_dir=config_dir,
                pool=mock_pool,
                runtime=adapter,
            ).trigger("route this", "tick")

        assert result.success is False
        assert result.error is not None
        assert "MCP tool discovery failed after 3 attempts" in result.error
        assert mock_complete.await_args.kwargs["success"] is False

    async def test_runtime_worker_factory_used_per_trigger(self, tmp_path: Path):
        """Spawner should invoke worker adapters returned by create_worker()."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config(max_concurrent_sessions=2)

        adapter = WorkerFactoryMockAdapter()
        spawner = Spawner(config=config, config_dir=config_dir, runtime=adapter)

        first = await spawner.trigger("first", "tick")
        second = await spawner.trigger("second", "tick")

        assert first.success is True
        assert second.success is True
        assert adapter.created_worker_ids == [1, 2]
        assert adapter.invoked_worker_ids == [1, 2]

    async def test_session_timeout_and_semaphore_release(self, tmp_path: Path):
        """Timed-out sessions return error; semaphore released so next session succeeds."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()

        # Single-shot timeout
        adapter = MockAdapter(result_text="never reached", delay=60)
        result = await Spawner(config=config, config_dir=config_dir, runtime=adapter).trigger(
            "hung prompt", "route", timeout_override=1
        )
        assert result.success is False
        assert "timed out" in result.error.lower()
        assert result.duration_ms >= 900

        # Semaphore released: second session succeeds
        seq_adapter = SequenceMockAdapter(sequence=[{"delay": 60}, {"result_text": "ok"}])
        spawner = Spawner(config=config, config_dir=config_dir, runtime=seq_adapter)
        r1 = await spawner.trigger("hung", "route", timeout_override=1)
        assert r1.success is False and "timed out" in r1.error.lower()
        r2 = await spawner.trigger("ok", "route", timeout_override=1)
        assert r2.success is True and r2.output == "ok"


# ---------------------------------------------------------------------------
# 8.2b: System-prompt resolution at spawn time (bu-dr03f.1)
# ---------------------------------------------------------------------------


class TestSpawnSystemPromptResolution:
    """The spawner resolves the live prompt edit (DB HEAD) over on-disk CLAUDE.md.

    Regression for bu-dr03f.1: the dashboard prompt editor writes
    ``public.system_prompt_history`` only. Previously nothing read it at spawn
    time, so edits were decorative. These tests prove the NEXT spawned session
    receives the edited prompt, while on-disk CLAUDE.md remains the fallback.
    """

    async def test_next_session_uses_on_disk_prompt_when_no_history(self, tmp_path: Path):
        """With no history row, the spawned session gets the on-disk CLAUDE.md."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "CLAUDE.md").write_text("# On-disk seed prompt", encoding="utf-8")
        config = _make_config()

        adapter = MockAdapter(result_text="ok", capture=True)
        # No pool -> fetch_system_prompt_override returns None -> disk fallback.
        spawner = Spawner(config=config, config_dir=config_dir, runtime=adapter)
        result = await spawner.trigger("hello", "tick")

        assert result.success is True
        assert adapter.calls[0]["system_prompt"] == "# On-disk seed prompt"

    async def test_next_session_uses_edited_prompt_from_history(self, tmp_path: Path):
        """A freshly-edited prompt (DB HEAD) is what the next session receives."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        # On-disk seed differs from the live edit to prove the edit wins.
        (config_dir / "CLAUDE.md").write_text("# Stale on-disk prompt", encoding="utf-8")
        config = _make_config()

        adapter = MockAdapter(result_text="ok", capture=True)
        spawner = Spawner(config=config, config_dir=config_dir, runtime=adapter)

        # Simulate the dashboard PUT having written the HEAD of
        # public.system_prompt_history for this butler.
        with patch(
            "butlers.core.spawner.fetch_system_prompt_override",
            new_callable=AsyncMock,
            return_value="# Freshly edited live prompt",
        ):
            result = await spawner.trigger("hello", "tick")

        assert result.success is True
        assert adapter.calls[0]["system_prompt"] == "# Freshly edited live prompt"
        assert "Stale on-disk prompt" not in adapter.calls[0]["system_prompt"]


class TestFetchSystemPromptOverride:
    """Unit tests for the DB read helper (fail-open, HEAD selection)."""

    async def test_returns_head_prompt(self):
        from butlers.core.spawner import fetch_system_prompt_override

        pool = AsyncMock()
        pool.fetchval = AsyncMock(return_value="# Edited")
        assert await fetch_system_prompt_override(pool, "mail") == "# Edited"

    async def test_none_pool_returns_none(self):
        from butlers.core.spawner import fetch_system_prompt_override

        assert await fetch_system_prompt_override(None, "mail") is None

    async def test_blank_prompt_treated_as_absent(self):
        from butlers.core.spawner import fetch_system_prompt_override

        pool = AsyncMock()
        pool.fetchval = AsyncMock(return_value="   \n ")
        assert await fetch_system_prompt_override(pool, "mail") is None

    async def test_missing_table_fails_open(self):
        from butlers.core.spawner import fetch_system_prompt_override

        pool = AsyncMock()
        pool.fetchval = AsyncMock(
            side_effect=Exception('relation "public.system_prompt_history" does not exist')
        )
        assert await fetch_system_prompt_override(pool, "mail") is None


# ---------------------------------------------------------------------------
# 8.3: Credential passthrough
# ---------------------------------------------------------------------------


class TestCredentialPassthrough:
    """Only declared env vars are passed to the runtime instance.

    Tests cover both the env-only path (no credential_store) and the
    DB-first path (with a mocked CredentialStore).
    """

    # ------------------------------------------------------------------
    # Env-only path (no credential_store — backwards compatibility)
    # ------------------------------------------------------------------

    async def test_env_path_passthrough_and_optional_exclusion(self):
        """PATH and declared required/optional vars passed; undeclared not leaked;
        optional absent → excluded; module credentials included."""
        config = _make_config(env_required=["MY_SECRET"], env_optional=["OPT_VAR"])
        with patch.dict(
            os.environ,
            {
                "PATH": "/tmp/node-bin",
                "MY_SECRET": "s3cret",
                "OPT_VAR": "opt-val",
                "UNDECLARED_SECRET": "should-not-leak",
            },
            clear=False,
        ):
            env = await _build_env(config)
            assert env["PATH"] == "/tmp/node-bin"
            assert env["MY_SECRET"] == "s3cret"
            assert env["OPT_VAR"] == "opt-val"
            assert "UNDECLARED_SECRET" not in env

        # Optional absent → excluded
        config2 = _make_config(env_optional=["MISSING_OPT"])
        os.environ.pop("MISSING_OPT", None)
        env2 = await _build_env(config2)
        assert "MISSING_OPT" not in env2

        # Module credentials included
        config3 = _make_config()
        with patch.dict(
            os.environ, {"SMTP_PASSWORD": "pw123", "IMAP_TOKEN": "tok456"}, clear=False
        ):
            env3 = await _build_env(
                config3, module_credentials_env={"email": ["SMTP_PASSWORD", "IMAP_TOKEN"]}
            )
            assert env3["SMTP_PASSWORD"] == "pw123"
            assert env3["IMAP_TOKEN"] == "tok456"

    async def test_db_credential_resolution(self):
        """DB-first path: module and butler creds resolved; missing key excluded."""
        config = _make_config(env_required=["MY_SECRET"])
        store = AsyncMock()
        resolved = {
            "SMTP_PASSWORD": "db-smtp-pw",
            "IMAP_TOKEN": "db-imap-tok",
            "MY_SECRET": "db-secret-value",
        }
        store.resolve = AsyncMock(side_effect=lambda key: resolved.get(key))
        env = await _build_env(
            config,
            module_credentials_env={"email": ["SMTP_PASSWORD", "IMAP_TOKEN"]},
            credential_store=store,
        )
        assert env["SMTP_PASSWORD"] == "db-smtp-pw"
        assert env["IMAP_TOKEN"] == "db-imap-tok"
        assert env["MY_SECRET"] == "db-secret-value"

        # Missing key excluded
        config2 = _make_config(env_required=["MISSING_KEY"])
        store2 = AsyncMock()
        store2.resolve = AsyncMock(return_value=None)
        env2 = await _build_env(config2, credential_store=store2)
        assert "MISSING_KEY" not in env2

    async def test_env_and_db_creds_passed_to_adapter(self, tmp_path: Path):
        """Env creds and DB creds both reach the adapter via trigger()."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()

        # Env path
        adapter = MockAdapter(result_text="", capture=True)
        with patch.dict(os.environ, {"BUTLER_SECRET": "s3cret"}, clear=False):
            await Spawner(
                config=_make_config(env_required=["BUTLER_SECRET"]),
                config_dir=config_dir,
                runtime=adapter,
            ).trigger("test env", "tick")
        assert adapter.calls[0]["env"]["BUTLER_SECRET"] == "s3cret"

        # DB path
        store = AsyncMock()
        store.resolve = AsyncMock(side_effect=lambda key: {"MY_API_KEY": "db-my-api-key"}.get(key))
        adapter2 = MockAdapter(result_text="", capture=True)
        await Spawner(
            config=_make_config(env_required=["MY_API_KEY"]),
            config_dir=config_dir,
            runtime=adapter2,
            credential_store=store,
        ).trigger("test db creds", "tick")
        assert adapter2.calls[0]["env"]["MY_API_KEY"] == "db-my-api-key"


# ---------------------------------------------------------------------------
# 8.5: Concurrency dispatch with asyncio semaphore (n=1 → serial)
# ---------------------------------------------------------------------------


class TestSerialDispatch:
    """asyncio.Semaphore(n) controls concurrency; n=1 gives serial dispatch."""

    async def test_concurrent_triggers_are_serialized(self, tmp_path: Path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()

        adapter = TrackingMockAdapter()
        spawner = Spawner(
            config=config,
            config_dir=config_dir,
            runtime=adapter,
        )

        # Launch 3 concurrent triggers
        results = await asyncio.gather(
            spawner.trigger("A", "tick"),
            spawner.trigger("B", "tick"),
            spawner.trigger("C", "tick"),
        )

        # All should succeed
        assert all(r.error is None for r in results)

        # Verify serial execution: each "start" must be followed by its "end"
        # before the next "start"
        execution_log = adapter.execution_log
        for i in range(0, len(execution_log), 2):
            assert execution_log[i][0] == "start"
            assert execution_log[i + 1][0] == "end"
            assert execution_log[i][1] == execution_log[i + 1][1]

    async def test_semaphore_released_on_error(self, tmp_path: Path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()

        adapter = SequenceMockAdapter(
            sequence=[
                {"error": "First call fails"},
                {"result_text": "second call works", "tool_calls": []},
            ]
        )
        spawner = Spawner(
            config=config,
            config_dir=config_dir,
            runtime=adapter,
        )

        result1 = await spawner.trigger("first", "tick")
        assert result1.error is not None

        # Semaphore slot should be released — second call should work
        result2 = await spawner.trigger("second", "tick")
        assert result2.error is None
        assert result2.output == "second call works"

    async def test_trigger_source_rejected_while_semaphore_full(self, tmp_path: Path):
        """trigger-source calls fail fast when all semaphore slots are occupied."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()

        adapter = MockAdapter(result_text="should not run", capture=True)
        spawner = Spawner(
            config=config,
            config_dir=config_dir,
            runtime=adapter,
        )

        # Acquire the sole semaphore slot to simulate a session in flight
        await spawner._session_semaphore.acquire()
        try:
            result = await spawner.trigger("nested", "trigger")
        finally:
            spawner._session_semaphore.release()

        assert result.success is False
        assert result.error is not None
        assert "cannot be called while another session is in flight" in result.error
        assert adapter.calls == []


# ---------------------------------------------------------------------------
# Semaphore concurrency pool tests
# ---------------------------------------------------------------------------


class TestSemaphoreConcurrencyPool:
    """asyncio.Semaphore(n) allows n concurrent sessions; self-trigger guard
    only rejects when all slots are occupied."""

    async def test_n3_allows_three_concurrent_sessions(self, tmp_path: Path):
        """n=3 allows 3 triggers to run concurrently (all start before any end)."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config(max_concurrent_sessions=3)

        started: list[str] = []
        finished: list[str] = []
        ready = asyncio.Event()

        class ConcurrentTrackingAdapter(MockAdapter):
            async def invoke(
                self,
                prompt,
                system_prompt,
                mcp_servers,
                env,
                max_turns=20,
                model=None,
                cwd=None,
                timeout=None,
            ):
                started.append(prompt)
                if len(started) == 3:
                    ready.set()
                # Wait until all 3 have started to prove true concurrency
                await asyncio.wait_for(ready.wait(), timeout=2.0)
                finished.append(prompt)
                return f"done-{prompt}", [], None

        adapter = ConcurrentTrackingAdapter()
        spawner = Spawner(config=config, config_dir=config_dir, runtime=adapter)

        results = await asyncio.gather(
            spawner.trigger("X", "tick"),
            spawner.trigger("Y", "tick"),
            spawner.trigger("Z", "tick"),
        )

        assert all(r.error is None for r in results)
        # All 3 started before any finished (true concurrent execution)
        assert len(started) == 3
        assert len(finished) == 3

    async def test_self_trigger_guard_with_free_and_full_n3_slots(self, tmp_path: Path):
        """With n=3, trigger allowed when slot free; rejected when all slots occupied."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config(max_concurrent_sessions=3)

        # One slot free (2 of 3 occupied) → trigger allowed
        adapter = MockAdapter(result_text="allowed", capture=True)
        spawner = Spawner(config=config, config_dir=config_dir, runtime=adapter)
        await spawner._session_semaphore.acquire()
        await spawner._session_semaphore.acquire()
        try:
            result = await spawner.trigger("self-trigger-ok", "trigger")
        finally:
            spawner._session_semaphore.release()
            spawner._session_semaphore.release()
        assert result.success is True and result.error is None

        # All 3 slots occupied → trigger rejected
        adapter2 = MockAdapter(result_text="should not run", capture=True)
        spawner2 = Spawner(config=config, config_dir=config_dir, runtime=adapter2)
        await spawner2._session_semaphore.acquire()
        await spawner2._session_semaphore.acquire()
        await spawner2._session_semaphore.acquire()
        try:
            result2 = await spawner2.trigger("nested", "trigger")
        finally:
            spawner2._session_semaphore.release()
            spawner2._session_semaphore.release()
            spawner2._session_semaphore.release()
        assert result2.success is False
        assert "cannot be called while another session is in flight" in result2.error
        assert adapter2.calls == []

    async def test_drain_handles_multiple_concurrent_sessions(self, tmp_path: Path):
        """drain() waits for all concurrent in-flight sessions to complete."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config(max_concurrent_sessions=3)

        # Adapter with a delay to keep sessions in-flight during drain
        adapter = MockAdapter(result_text="done", delay=0.05)
        spawner = Spawner(config=config, config_dir=config_dir, runtime=adapter)

        # Launch 3 concurrent sessions (don't await them yet)
        tasks = [
            asyncio.create_task(spawner.trigger("A", "tick")),
            asyncio.create_task(spawner.trigger("B", "tick")),
            asyncio.create_task(spawner.trigger("C", "tick")),
        ]

        # Give tasks a moment to start
        await asyncio.sleep(0.01)

        # Drain with generous timeout — should wait for all to complete
        spawner.stop_accepting()
        await spawner.drain(timeout=5.0)

        # All tasks should be done
        assert all(t.done() for t in tasks)
        results = [t.result() for t in tasks]
        assert all(r.error is None for r in results)
        assert spawner.in_flight_count == 0

    async def test_semaphore_slot_count_and_queue_backpressure(self, tmp_path: Path):
        """Semaphore init matches max_concurrent_sessions; queue rejects when waiters at limit."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()

        for n in (1, 3, 5):
            config = _make_config(max_concurrent_sessions=n)
            spawner = Spawner(config=config, config_dir=config_dir, runtime=MockAdapter())
            assert spawner._session_semaphore._value == n, (
                f"Expected semaphore value {n}, got {spawner._session_semaphore._value}"
            )

        # Queue backpressure test
        config = _make_config(max_concurrent_sessions=1, max_queued_sessions=1)
        adapter = MockAdapter(result_text="ok")
        spawner = Spawner(config=config, config_dir=config_dir, runtime=adapter)

        # Occupy the only active slot so the next trigger becomes a waiter.
        await spawner._session_semaphore.acquire()
        try:
            first_waiter = asyncio.create_task(spawner.trigger("queued-1", "tick"))
            for _ in range(50):
                waiters = len(getattr(spawner._session_semaphore, "_waiters", ()) or ())
                if waiters == 1:
                    break
                await asyncio.sleep(0.01)

            rejected = await spawner.trigger("queued-2", "tick")
            assert rejected.success is False
            assert rejected.error is not None
            assert "spawner queue is full" in rejected.error
        finally:
            spawner._session_semaphore.release()

        first_result = await first_waiter
        assert first_result.success is True


# ---------------------------------------------------------------------------
# 8.6: Session logging
# ---------------------------------------------------------------------------


class TestSessionLogging:
    """Session logging is wired to session_create and session_complete."""

    async def test_session_logging_on_success_and_error(self, tmp_path: Path):
        """session_create/complete called on success (with result data) and error (with error info)."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()
        mock_pool = AsyncMock()

        # Success path
        from butlers.core.spawner import _FALLBACK_MODEL_ID

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock) as mock_complete,
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            fake_session_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
            mock_create.return_value = fake_session_id
            await Spawner(
                config=config,
                config_dir=config_dir,
                pool=mock_pool,
                runtime=MockAdapter(result_text="Hello from mock!"),
            ).trigger("log me", "schedule")
            mock_create.assert_called_once()
            create_args, create_kwargs = mock_create.call_args
            assert create_args[0] is mock_pool
            assert create_args[1] == "log me"
            assert create_args[2] == "schedule"
            assert create_kwargs.get("model") == _FALLBACK_MODEL_ID
            mock_complete.assert_called_once()
            args, kwargs = mock_complete.call_args
            assert args[0] is mock_pool and args[1] == fake_session_id
            assert kwargs["output"] == "Hello from mock!"
            assert isinstance(kwargs["tool_calls"], list)
            assert kwargs["duration_ms"] >= 0 and kwargs["success"] is True

        # Error path
        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create2,
            patch(
                "butlers.core.spawner.session_complete", new_callable=AsyncMock
            ) as mock_complete2,
        ):
            fake_session_id2 = uuid.UUID("00000000-0000-0000-0000-000000000002")
            mock_create2.return_value = fake_session_id2
            result = await Spawner(
                config=config,
                config_dir=config_dir,
                pool=mock_pool,
                runtime=MockAdapter(error="adapter connection failed"),
            ).trigger("fail", "tick")
            assert result.error is not None
            mock_complete2.assert_called_once()
            args2, kwargs2 = mock_complete2.call_args
            assert args2[0] is mock_pool and args2[1] == fake_session_id2
            assert kwargs2["output"] is None and kwargs2["tool_calls"] == []
            assert kwargs2["success"] is False and "RuntimeError" in kwargs2["error"]

    async def test_session_create_failure_preserves_original_error(self, tmp_path: Path):
        """Errors before runtime invocation should not be masked by t0 handling."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()

        mock_pool = AsyncMock()

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock) as mock_complete,
        ):
            mock_create.side_effect = ValueError("Invalid trigger_source 'trigger_tool'")

            adapter = MockAdapter(result_text="unused")
            spawner = Spawner(
                config=config,
                config_dir=config_dir,
                pool=mock_pool,
                runtime=adapter,
            )

            result = await spawner.trigger("fail before invoke", "trigger_tool")

            assert result.success is False
            assert result.error is not None
            assert "ValueError" in result.error
            assert "Invalid trigger_source 'trigger_tool'" in result.error
            assert "UnboundLocalError" not in result.error
            assert result.duration_ms >= 0
            mock_complete.assert_not_called()

    async def test_no_session_logging_without_pool(self, tmp_path: Path):
        """When pool is None, no session logging occurs (no errors either)."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()

        adapter = MockAdapter(result_text="Hello from mock!")
        spawner = Spawner(
            config=config,
            config_dir=config_dir,
            pool=None,
            runtime=adapter,
        )

        # Should not raise even without a pool
        result = await spawner.trigger("no pool", "tick")
        assert result.output == "Hello from mock!"


# ---------------------------------------------------------------------------
# Model passthrough tests
# ---------------------------------------------------------------------------


class CapturingMockAdapter(MockAdapter):
    """MockAdapter that captures the model kwarg passed to invoke()."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.captured_models: list[str | None] = []

    async def invoke(self, *args, model: str | None = None, **kwargs):
        self.captured_models.append(model)
        return await super().invoke(*args, model=model, **kwargs)


class TestModelPassthrough:
    """Model string resolved by the catalog (or the static fallback) is passed
    through to ``invoke()`` kwargs and surfaced on :class:`SpawnerResult`."""

    async def test_model_passed_to_invoke_and_result(self, tmp_path: Path):
        """Without a pool, the hard-coded fallback model is forwarded to invoke();
        error results surface the same fallback model."""
        from butlers.core.spawner import _FALLBACK_MODEL_ID

        config_dir = tmp_path / "config"
        config_dir.mkdir()

        # No pool → catalog is skipped, spawner falls back to the constant
        adapter = CapturingMockAdapter(result_text="ok")
        spawner = Spawner(
            config=_make_config(),
            config_dir=config_dir,
            runtime=adapter,
        )
        result = await spawner.trigger("test default", "tick")
        assert adapter.captured_models[0] == _FALLBACK_MODEL_ID
        assert result.model == _FALLBACK_MODEL_ID

        # Error path still surfaces the fallback model
        spawner3 = Spawner(
            config=_make_config(),
            config_dir=config_dir,
            runtime=MockAdapter(error="invocation failed"),
        )
        result3 = await spawner3.trigger("fail", "tick")
        assert result3.error is not None
        assert result3.model == _FALLBACK_MODEL_ID


# ---------------------------------------------------------------------------
# Integration-style test: full flow with MockAdapter
# ---------------------------------------------------------------------------


class TestFullFlow:
    """End-to-end spawner flow with MockAdapter."""

    async def test_full_trigger_flow(self, tmp_path: Path):
        """Full flow: result, tool calls, env passthrough, system prompt, and memory context suffix."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        claude_md = config_dir / "CLAUDE.md"
        claude_md.write_text("You are the test butler.")

        config = _make_config(
            name="flow-butler",
            port=9200,
            env_required=["CUSTOM_VAR"],
            modules={"memory": {}},
        )

        adapter = MockAdapter(
            result_text="All done!",
            tool_calls=[{"id": "t1", "name": "state_set", "input": {"k": "v"}}],
            capture=True,
        )
        spawner = Spawner(config=config, config_dir=config_dir, runtime=adapter)

        # Without memory context
        with patch(
            "butlers.core.spawner.fetch_memory_context",
            new_callable=AsyncMock,
            return_value=None,
        ) as mock_fetch:
            with patch.dict(os.environ, {"CUSTOM_VAR": "cv"}, clear=False):
                result = await spawner.trigger("do the thing", "schedule")

        mock_fetch.assert_called_once_with(None, "flow-butler", "do the thing", token_budget=3000)
        assert result.output == "All done!"
        assert result.error is None
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0]["name"] == "state_set"
        call = adapter.calls[0]
        assert call["system_prompt"] == "You are the test butler."
        assert "flow-butler" in call["mcp_servers"]
        assert call["env"]["CUSTOM_VAR"] == "cv"

        # With memory context → appended to system prompt
        adapter2 = MockAdapter(result_text="All done!", capture=True)
        spawner2 = Spawner(config=config, config_dir=config_dir, runtime=adapter2)
        memory_ctx = "# Memory Context\n- user prefers concise updates"
        with patch(
            "butlers.core.spawner.fetch_memory_context",
            new_callable=AsyncMock,
            return_value=memory_ctx,
        ):
            await spawner2.trigger("do the thing", "schedule")
        assert adapter2.calls[0]["system_prompt"] == f"You are the test butler.\n\n{memory_ctx}"


# ---------------------------------------------------------------------------
# Parametrized tests: orchestration behavior across adapters
# ---------------------------------------------------------------------------


def _make_noop_adapter() -> MockAdapter:
    """Adapter that returns empty result (no error)."""
    return MockAdapter(result_text="")


def _make_result_adapter() -> MockAdapter:
    """Adapter that returns a text result."""
    return MockAdapter(result_text="Hello from adapter!")


def _make_tool_use_adapter() -> MockAdapter:
    """Adapter that returns result with tool calls."""
    return MockAdapter(
        result_text="Done with tools",
        tool_calls=[{"id": "tool_1", "name": "state_get", "input": {"key": "foo"}}],
    )


def _make_error_adapter() -> MockAdapter:
    """Adapter that raises an error."""
    return MockAdapter(error="adapter connection failed")


def _make_slow_adapter() -> MockAdapter:
    """Adapter that sleeps to simulate duration."""
    return MockAdapter(result_text="slow result", delay=0.05)


@pytest.mark.parametrize(
    "adapter_factory,expected_result,expected_error",
    [
        pytest.param(_make_noop_adapter, "", None, id="noop-adapter"),
        pytest.param(_make_result_adapter, "Hello from adapter!", None, id="result-adapter"),
        pytest.param(_make_error_adapter, None, "adapter connection failed", id="error-adapter"),
    ],
)
class TestParametrizedOrchestration:
    """Orchestration tests parametrized across different adapter behaviors."""

    async def test_trigger_returns_spawner_result(
        self, tmp_path: Path, adapter_factory, expected_result, expected_error
    ):
        """Spawner.trigger() wraps adapter output in SpawnerResult."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()

        adapter = adapter_factory()
        spawner = Spawner(config=config, config_dir=config_dir, runtime=adapter)
        result = await spawner.trigger("test", "tick")

        if expected_error:
            assert result.error is not None
            assert expected_error in result.error
            assert result.output is None
        else:
            assert result.error is None
            assert result.output == expected_result
        assert result.duration_ms >= 0


class TestToolOutcomePersistence:
    async def test_session_complete_persists_failed_then_retried_tool_outcomes(
        self, tmp_path: Path
    ):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()
        mock_pool = AsyncMock()

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock) as mock_complete,
            patch(
                "butlers.core.spawner.consume_runtime_session_tool_calls",
                return_value=[
                    {
                        "name": "route_to_butler",
                        "input": {"butler": "relationship"},
                        "outcome": "error",
                        "error": "TimeoutError: target unavailable",
                    },
                    {
                        "name": "route_to_butler",
                        "input": {"butler": "relationship"},
                        "outcome": "success",
                        "result": {"status": "accepted", "butler": "relationship"},
                    },
                ],
            ),
        ):
            fake_session_id = uuid.UUID("00000000-0000-0000-0000-000000000199")
            mock_create.return_value = fake_session_id

            adapter = MockAdapter(
                result_text="done",
                tool_calls=[
                    {
                        "id": "tool_1",
                        "name": "route_to_butler",
                        "input": {"butler": "relationship"},
                    }
                ],
            )
            spawner = Spawner(config=config, config_dir=config_dir, pool=mock_pool, runtime=adapter)

            result = await spawner.trigger("test", "tick")

            assert result.error is None
            assert [call["outcome"] for call in result.tool_calls] == ["error", "success"]

            mock_complete.assert_called_once()
            _, kwargs = mock_complete.call_args
            assert [call["outcome"] for call in kwargs["tool_calls"]] == ["error", "success"]
            assert kwargs["tool_calls"][0]["id"] == "tool_1"
            assert kwargs["tool_calls"][0]["error"] == "TimeoutError: target unavailable"
            assert kwargs["tool_calls"][1]["result"] == {
                "status": "accepted",
                "butler": "relationship",
            }


# ---------------------------------------------------------------------------
# Token usage capture from adapter
# ---------------------------------------------------------------------------


class TestTokenUsageCapture:
    """Tests for extracting input_tokens and output_tokens from adapter response."""

    async def test_token_counts_present_absent_and_partial(self, tmp_path: Path):
        """Token counts: present (100/200), absent/empty/missing-fields (all None), partial."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()

        # Present
        result = await Spawner(
            config=config,
            config_dir=config_dir,
            runtime=MockAdapter(
                result_text="Hello!", usage={"input_tokens": 100, "output_tokens": 200}
            ),
        ).trigger("test", "tick")
        assert result.input_tokens == 100
        assert result.output_tokens == 200

        # Absent (usage=None)
        result2 = await Spawner(
            config=config,
            config_dir=config_dir,
            runtime=MockAdapter(result_text="Hello!", usage=None),
        ).trigger("no tokens", "tick")
        assert result2.input_tokens is None
        assert result2.output_tokens is None

        # Empty dict / missing keys
        result3 = await Spawner(
            config=config,
            config_dir=config_dir,
            runtime=MockAdapter(result_text="Empty dict", usage={}),
        ).trigger("empty", "tick")
        assert result3.input_tokens is None
        assert result3.output_tokens is None

        # Partial: only input
        result4 = await Spawner(
            config=config,
            config_dir=config_dir,
            runtime=MockAdapter(result_text="Partial", usage={"input_tokens": 300}),
        ).trigger("partial", "tick")
        assert result4.input_tokens == 300
        assert result4.output_tokens is None

        # Partial: only output
        result5 = await Spawner(
            config=config,
            config_dir=config_dir,
            runtime=MockAdapter(result_text="Partial output only", usage={"output_tokens": 750}),
        ).trigger("partial output", "tick")
        assert result5.input_tokens is None
        assert result5.output_tokens == 750

        # Zero counts preserved
        result6 = await Spawner(
            config=config,
            config_dir=config_dir,
            runtime=MockAdapter(
                result_text="Zero tokens", usage={"input_tokens": 0, "output_tokens": 0}
            ),
        ).trigger("zero tokens", "tick")
        assert result6.input_tokens == 0
        assert result6.output_tokens == 0

    async def test_token_counts_on_error_and_sequence(self, tmp_path: Path):
        """Error: tokens None; after error, success returns tokens; tokens passed to session_complete."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()

        # Error path: tokens None
        result = await Spawner(
            config=config, config_dir=config_dir, runtime=MockAdapter(error="adapter failed")
        ).trigger("fail", "tick")
        assert result.error is not None
        assert result.input_tokens is None and result.output_tokens is None

        # Sequence: error then success recovers tokens
        adapter = SequenceMockAdapter(
            sequence=[
                {"error": "first fails"},
                {
                    "result_text": "second works",
                    "tool_calls": [],
                    "usage": {"input_tokens": 42, "output_tokens": 84},
                },
            ]
        )
        spawner = Spawner(config=config, config_dir=config_dir, runtime=adapter)
        r1 = await spawner.trigger("first", "tick")
        assert r1.error is not None and r1.input_tokens is None
        r2 = await spawner.trigger("second", "tick")
        assert r2.error is None and r2.input_tokens == 42 and r2.output_tokens == 84

    async def test_token_counts_passed_to_session_complete(self, tmp_path: Path):
        """Token counts are passed to session_complete on success."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        mock_pool = AsyncMock()

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock) as mock_complete,
        ):
            mock_create.return_value = uuid.UUID("00000000-0000-0000-0000-000000000010")
            await Spawner(
                config=_make_config(),
                config_dir=config_dir,
                pool=mock_pool,
                runtime=MockAdapter(
                    result_text="With tokens!", usage={"input_tokens": 500, "output_tokens": 1000}
                ),
            ).trigger("test", "tick")
            _, kwargs = mock_complete.call_args
            assert kwargs["input_tokens"] == 500 and kwargs["output_tokens"] == 1000


# ---------------------------------------------------------------------------
# Catalog-based model resolution (bu-afm7.2)
# ---------------------------------------------------------------------------


class TestCatalogModelResolution:
    """Tests for dynamic model selection via the model catalog.

    Covers:
    - Catalog resolution path (resolve_model returns a valid result)
    - TOML fallback when catalog has no matching entries
    - Complexity parameter propagated to resolve_model
    - extra_args merging: TOML args first, catalog args appended
    - Adapter pool lazy instantiation for new runtime types
    - Session records include model, runtime_type, complexity, resolution source
    - Graceful fallback on catalog resolution errors
    """

    async def test_catalog_and_static_fallback_model_selection(self, tmp_path: Path):
        """Catalog model used when available; static constant used when catalog returns None."""
        from butlers.core.spawner import _FALLBACK_MODEL_ID

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()

        captured: dict = {}

        class CapturingAdapter(MockAdapter):
            async def invoke(
                self,
                prompt,
                system_prompt,
                mcp_servers,
                env,
                max_turns=20,
                model=None,
                runtime_args=None,
                cwd=None,
                timeout=None,
            ):
                captured["model"] = model
                captured["runtime_args"] = runtime_args
                captured["timeout"] = timeout
                return "ok", [], None

        mock_pool = AsyncMock()

        # Catalog returns a result
        adapter = CapturingAdapter()
        spawner = Spawner(config=config, config_dir=config_dir, pool=mock_pool, runtime=adapter)
        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=(
                    DEFAULT_RUNTIME_TYPE,
                    "claude-opus-4-20250514",
                    [],
                    _FAKE_CATALOG_ID,
                    2400,
                    "workhorse",
                ),
            ),
        ):
            mock_create.return_value = uuid.UUID("00000000-0000-0000-0000-000000000001")
            result = await spawner.trigger("prompt", "tick")
        assert result.success is True
        assert captured["model"] == "claude-opus-4-20250514"
        assert captured["timeout"] == 2400
        assert result.model == "claude-opus-4-20250514"

        # Catalog returns None → static fallback constant
        captured.clear()
        adapter2 = CapturingAdapter()
        spawner2 = Spawner(config=config, config_dir=config_dir, pool=mock_pool, runtime=adapter2)
        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            mock_create.return_value = uuid.UUID("00000000-0000-0000-0000-000000000001")
            result2 = await spawner2.trigger("prompt", "tick")
        assert result2.success is True
        assert captured["model"] == _FALLBACK_MODEL_ID
        assert result2.model == _FALLBACK_MODEL_ID

    async def test_complexity_routing(self, tmp_path: Path):
        """Without pool: resolve_model not called. With pool: complexity forwarded."""
        from butlers.core.model_routing import Complexity

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()

        # No pool → resolve_model skipped
        adapter = MockAdapter(result_text="ok")
        spawner = Spawner(config=config, config_dir=config_dir, runtime=adapter)
        with patch(
            "butlers.core.spawner.resolve_model_with_effective_tier",
            new_callable=AsyncMock,
            return_value=None,
        ) as mock_resolve:
            await spawner.trigger("prompt", "tick", complexity=Complexity.REASONING)
        mock_resolve.assert_not_called()

        # With pool → complexity forwarded; default is MEDIUM
        mock_pool = AsyncMock()
        spawner2 = Spawner(
            config=config,
            config_dir=config_dir,
            pool=mock_pool,
            runtime=MockAdapter(result_text="ok"),
        )
        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=None,
            ) as mock_resolve2,
        ):
            mock_create.return_value = uuid.UUID("00000000-0000-0000-0000-000000000001")
            await spawner2.trigger("prompt", "tick", complexity=Complexity.REASONING)
        mock_resolve2.assert_called_once()
        assert mock_resolve2.call_args[0][1] == "test-butler"
        assert mock_resolve2.call_args[0][2] == Complexity.REASONING

        # Default complexity is WORKHORSE
        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=None,
            ) as mock_resolve3,
        ):
            mock_create.return_value = uuid.UUID("00000000-0000-0000-0000-000000000001")
            await spawner2.trigger("prompt", "tick")
        mock_resolve3.assert_called_once()
        assert mock_resolve3.call_args[0][2] == Complexity.WORKHORSE

    async def test_extra_args_merging(self, tmp_path: Path):
        """Catalog extra_args are forwarded verbatim; empty args omits the kwarg."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()

        captured: dict = {}

        class CapturingAdapter(MockAdapter):
            async def invoke(
                self,
                prompt,
                system_prompt,
                mcp_servers,
                env,
                max_turns=20,
                model=None,
                runtime_args=None,
                cwd=None,
                timeout=None,
            ):
                captured["runtime_args"] = runtime_args
                captured["timeout"] = timeout
                return "ok", [], None

        mock_pool = AsyncMock()
        adapter = CapturingAdapter()
        spawner = Spawner(config=config, config_dir=config_dir, pool=mock_pool, runtime=adapter)

        # Catalog-supplied args forwarded as-is
        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=(
                    DEFAULT_RUNTIME_TYPE,
                    "claude-opus-4-20250514",
                    ["--catalog-arg", "val"],
                    _FAKE_CATALOG_ID,
                    2400,
                    "workhorse",
                ),
            ),
        ):
            mock_create.return_value = uuid.UUID("00000000-0000-0000-0000-000000000001")
            await spawner.trigger("prompt", "tick")
        assert captured["runtime_args"] == ["--catalog-arg", "val"]
        assert captured["timeout"] == 2400

        # Empty args → runtime_args kwarg is None (spawner omits the kwarg)
        config2 = _make_config()
        captured2: dict = {}

        class CapturingAdapter2(MockAdapter):
            async def invoke(
                self,
                prompt,
                system_prompt,
                mcp_servers,
                env,
                max_turns=20,
                model=None,
                runtime_args=None,
                cwd=None,
                timeout=None,
            ):
                captured2["runtime_args"] = runtime_args
                captured2["timeout"] = timeout
                return "ok", [], None

        spawner2 = Spawner(
            config=config2,
            config_dir=config_dir,
            pool=mock_pool,
            runtime=CapturingAdapter2(),
        )
        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=(
                    DEFAULT_RUNTIME_TYPE,
                    "claude-opus-4-20250514",
                    [],
                    _FAKE_CATALOG_ID,
                    2400,
                    "workhorse",
                ),
            ),
        ):
            mock_create.return_value = uuid.UUID("00000000-0000-0000-0000-000000000001")
            await spawner2.trigger("prompt", "tick")
        assert captured2["runtime_args"] is None
        assert captured2["timeout"] == 2400

    async def test_catalog_error_and_unknown_runtime_fall_back_to_static(self, tmp_path: Path):
        """Both catalog errors and unknown runtime types fall back to the static default."""
        from butlers.core.spawner import _FALLBACK_MODEL_ID

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()
        mock_pool = AsyncMock()

        for side_effect, return_value in [
            (Exception("DB connection error"), None),
            (None, ("nonexistent-runtime", "some-model", [], _FAKE_CATALOG_ID, 2400, "workhorse")),
        ]:
            captured: dict = {}

            class CapturingAdapter(MockAdapter):
                async def invoke(
                    self,
                    prompt,
                    system_prompt,
                    mcp_servers,
                    env,
                    max_turns=20,
                    model=None,
                    runtime_args=None,
                    cwd=None,
                    timeout=None,
                ):
                    captured["model"] = model
                    return "ok", [], None

            adapter = CapturingAdapter()
            spawner = Spawner(config=config, config_dir=config_dir, pool=mock_pool, runtime=adapter)
            resolve_kwargs = (
                {"side_effect": side_effect} if side_effect else {"return_value": return_value}
            )
            with (
                patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
                patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
                patch(
                    "butlers.core.spawner.resolve_model_with_effective_tier",
                    new_callable=AsyncMock,
                    **resolve_kwargs,
                ),
            ):
                mock_create.return_value = uuid.UUID("00000000-0000-0000-0000-000000000001")
                result = await spawner.trigger("prompt", "tick")
            assert result.success is True
            assert captured["model"] == _FALLBACK_MODEL_ID
            assert result.model == _FALLBACK_MODEL_ID

    async def test_audit_log_resolution_metadata(self, tmp_path: Path):
        """Audit log includes model, runtime_type, complexity, resolution_source."""
        from butlers.core.model_routing import Complexity

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        mock_pool = AsyncMock()
        spawner = Spawner(
            config=_make_config(),
            config_dir=config_dir,
            pool=mock_pool,
            runtime=MockAdapter(result_text="ok"),
        )

        audit_entries: list[dict] = []

        async def fake_write_audit(pool, butler_name, event_type, data, **kwargs):
            audit_entries.append({"data": data, **kwargs})

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch("butlers.core.spawner.write_audit_entry", side_effect=fake_write_audit),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=(
                    DEFAULT_RUNTIME_TYPE,
                    "claude-opus-4-20250514",
                    [],
                    _FAKE_CATALOG_ID,
                    2400,
                    "workhorse",
                ),
            ),
        ):
            mock_create.return_value = uuid.UUID("00000000-0000-0000-0000-000000000001")
            await spawner.trigger("prompt", "tick", complexity=Complexity.REASONING)

        # Two entries: "session" + "llm_api_call" (egress)
        assert len(audit_entries) == 2
        session_entry = next(e for e in audit_entries if e["data"].get("complexity"))
        llm_entry = next(e for e in audit_entries if "provider" in e["data"])
        assert session_entry["data"]["model"] == "claude-opus-4-20250514"
        assert session_entry["data"]["runtime_type"] == DEFAULT_RUNTIME_TYPE
        assert session_entry["data"]["complexity"] == "reasoning"
        assert session_entry["data"]["resolution_source"] == "catalog"
        assert llm_entry["data"]["provider"] == "anthropic"
        assert llm_entry["data"]["model"] == "claude-opus-4-20250514"

        # Also verify the static_fallback source
        from butlers.core.spawner import _FALLBACK_MODEL_ID

        audit_entries.clear()
        spawner2 = Spawner(
            config=_make_config(),
            config_dir=config_dir,
            pool=mock_pool,
            runtime=MockAdapter(result_text="ok"),
        )
        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch("butlers.core.spawner.write_audit_entry", side_effect=fake_write_audit),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            mock_create.return_value = uuid.UUID("00000000-0000-0000-0000-000000000001")
            await spawner2.trigger("prompt", "tick")
        assert len(audit_entries) == 2
        session_entry2 = next(e for e in audit_entries if e["data"].get("resolution_source"))
        llm_entry2 = next(e for e in audit_entries if "provider" in e["data"])
        assert session_entry2["data"]["model"] == _FALLBACK_MODEL_ID
        assert session_entry2["data"]["resolution_source"] == "static_fallback"
        assert llm_entry2["data"]["provider"] == "anthropic"


# ---------------------------------------------------------------------------
# Global spawn concurrency cap (bu-s3lr.2)
# ---------------------------------------------------------------------------


from butlers.core.spawner import _reset_global_semaphore  # noqa: E402


class TestGlobalSpawnConcurrencyCap:
    """Process-wide global semaphore limits total concurrent LLM sessions.

    Tests verify:
    - Global cap (env var) is respected across multiple Spawner instances
    - Per-butler semaphore is unchanged and still applies
    - Queuing events are logged at INFO when the global cap is saturated
    - Global semaphore is released after each session (no leaks)
    - Configurable via BUTLERS_MAX_GLOBAL_SESSIONS
    """

    def setup_method(self) -> None:
        """Reset the global semaphore before each test for isolation."""
        _reset_global_semaphore()

    def teardown_method(self) -> None:
        """Reset the global semaphore after each test to avoid state leakage."""
        _reset_global_semaphore()

    async def test_global_cap_limits_concurrent_sessions_across_spawners(self, tmp_path: Path):
        """With global cap=2 and 5 spawners, at most 2 LLM sessions run simultaneously.

        This is the primary acceptance criterion: 5 butlers triggering simultaneously
        are collectively bounded by the global cap, not just their per-butler limit.
        """
        # Reset + install a fresh global semaphore with cap=2
        _reset_global_semaphore()
        with patch.dict(os.environ, {"BUTLERS_MAX_GLOBAL_SESSIONS": "2"}, clear=False):
            # Force re-init so the env var is picked up
            _reset_global_semaphore()

            max_concurrent = 0
            active_count = 0
            lock = asyncio.Lock()

            class ConcurrencyTrackingAdapter(MockAdapter):
                async def invoke(self, *args, **kwargs):
                    nonlocal max_concurrent, active_count
                    async with lock:
                        active_count += 1
                        if active_count > max_concurrent:
                            max_concurrent = active_count
                    await asyncio.sleep(0.02)
                    async with lock:
                        active_count -= 1
                    return "done", [], None

            # 5 spawners, each with max_concurrent_sessions=2 (per-butler cap)
            # but global cap=2 should be the binding constraint
            spawners = []
            for i in range(5):
                config_dir = tmp_path / f"butler-{i}"
                config_dir.mkdir()
                config = _make_config(
                    name=f"butler-{i}",
                    port=9100 + i,
                    max_concurrent_sessions=2,
                )
                spawners.append(
                    Spawner(
                        config=config,
                        config_dir=config_dir,
                        runtime=ConcurrencyTrackingAdapter(),
                    )
                )

            # Fire one trigger per spawner simultaneously
            results = await asyncio.gather(
                *[spawner.trigger(f"prompt-{i}", "tick") for i, spawner in enumerate(spawners)]
            )

        assert all(r.success for r in results), [r.error for r in results]
        # With global cap=2, never more than 2 sessions should be active at once
        assert max_concurrent <= 2, (
            f"Expected at most 2 concurrent sessions; observed {max_concurrent}"
        )

    async def test_global_cap_env_var_and_semaphore_release(self, tmp_path: Path):
        """Default cap is 3; BUTLERS_MAX_GLOBAL_SESSIONS overrides it; semaphore released after success and error."""
        import butlers.core.spawner as spawner_mod

        env_without_cap = {
            k: v for k, v in os.environ.items() if k != "BUTLERS_MAX_GLOBAL_SESSIONS"
        }
        with patch.dict(os.environ, env_without_cap, clear=True):
            _reset_global_semaphore()
            sem = spawner_mod._get_global_semaphore()
        assert sem._value == 3

        with patch.dict(os.environ, {"BUTLERS_MAX_GLOBAL_SESSIONS": "7"}, clear=False):
            _reset_global_semaphore()
            sem2 = spawner_mod._get_global_semaphore()
        assert sem2._value == 7

        # Semaphore released after session completes (success and error)
        with patch.dict(os.environ, {"BUTLERS_MAX_GLOBAL_SESSIONS": "1"}, clear=False):
            _reset_global_semaphore()

            config_dir = tmp_path / "config"
            config_dir.mkdir()
            config = _make_config(max_concurrent_sessions=1)

            result = await Spawner(
                config=config, config_dir=config_dir, runtime=MockAdapter(result_text="ok")
            ).trigger("first", "tick")
            assert result.success
            sem3 = spawner_mod._get_global_semaphore()
            assert sem3._value == 1

            result2 = await Spawner(
                config=config, config_dir=config_dir, runtime=MockAdapter(error="adapter crashed")
            ).trigger("fail", "tick")
            assert result2.error is not None
            sem4 = spawner_mod._get_global_semaphore()
            assert sem4._value == 1

    async def test_global_cap_queuing_logged_at_info(self, tmp_path: Path, caplog):
        """When the global cap is saturated, a queuing INFO message is emitted."""
        import logging

        with patch.dict(os.environ, {"BUTLERS_MAX_GLOBAL_SESSIONS": "1"}, clear=False):
            _reset_global_semaphore()

            config_dir = tmp_path / "config"
            config_dir.mkdir()
            config = _make_config(max_concurrent_sessions=2)
            adapter = MockAdapter(result_text="ok", delay=0.02)
            spawner = Spawner(config=config, config_dir=config_dir, runtime=adapter)

            with caplog.at_level(logging.INFO, logger="butlers.core.spawner"):
                results = await asyncio.gather(
                    spawner.trigger("first", "tick"),
                    spawner.trigger("second", "tick"),
                )

        assert all(r.success for r in results)
        # At least one of the two triggers should have logged the global-cap queuing message
        queued_msgs = [r.message for r in caplog.records if "global cap" in r.message.lower()]
        assert queued_msgs, (
            "Expected at least one INFO log about spawning being queued for global cap; "
            f"got caplog records: {[r.message for r in caplog.records]}"
        )

    async def test_per_butler_semaphore_still_enforced_with_global_cap(self, tmp_path: Path):
        """Per-butler max_concurrent_sessions is still respected with global cap active."""
        with patch.dict(os.environ, {"BUTLERS_MAX_GLOBAL_SESSIONS": "10"}, clear=False):
            _reset_global_semaphore()

            # Butler has per-butler cap=1 (serial dispatch)
            config_dir = tmp_path / "config"
            config_dir.mkdir()
            config = _make_config(max_concurrent_sessions=1)

            adapter = TrackingMockAdapter()
            spawner = Spawner(config=config, config_dir=config_dir, runtime=adapter)

            results = await asyncio.gather(
                spawner.trigger("A", "tick"),
                spawner.trigger("B", "tick"),
            )

        assert all(r.error is None for r in results)
        # Serial dispatch still holds: start/end pairs must not overlap
        log = adapter.execution_log
        assert len(log) == 4
        # Pattern: start-A, end-A, start-B, end-B (or B before A, but never interleaved)
        for i in range(0, len(log), 2):
            assert log[i][0] == "start"
            assert log[i + 1][0] == "end"
            assert log[i][1] == log[i + 1][1]


# ---------------------------------------------------------------------------
# cwd parameter and bypass_butler_semaphore (self-healing support)
# ---------------------------------------------------------------------------


class TestSpawnerCwdAndBypassSemaphore:
    """Tests for cwd override and bypass_butler_semaphore parameter."""

    def setup_method(self) -> None:
        _reset_global_semaphore()

    def teardown_method(self) -> None:
        _reset_global_semaphore()

    async def test_cwd_override_and_default(self, tmp_path: Path) -> None:
        """cwd overrides config_dir when provided; defaults to config_dir when absent."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        custom_cwd = tmp_path / "worktree"
        custom_cwd.mkdir()

        captured_cwd: list = []

        class CwdCaptureAdapter(MockAdapter):
            async def invoke(self, *args, **kwargs):
                captured_cwd.append(kwargs.get("cwd"))
                return "ok", [], None

        spawner = Spawner(config=_make_config(), config_dir=config_dir, runtime=CwdCaptureAdapter())

        # Override: adapter gets custom_cwd
        result = await spawner.trigger("hello", "tick", cwd=str(custom_cwd))
        assert result.success is True
        assert captured_cwd[-1] == str(custom_cwd)

        # Default: adapter gets config_dir
        result2 = await spawner.trigger("hello", "tick")
        assert result2.success is True
        assert captured_cwd[-1] == str(config_dir)

    async def test_bypass_and_normal_butler_semaphore_behavior(self, tmp_path: Path) -> None:
        """bypass_butler_semaphore=True proceeds with semaphore at 0; normal trigger waits."""
        # Bypass scenario: proceeds even when per-butler semaphore is drained
        bypass_dir = tmp_path / "bypass"
        bypass_dir.mkdir()
        config = _make_config(max_concurrent_sessions=1)
        spawner_b = Spawner(
            config=config, config_dir=bypass_dir, runtime=MockAdapter(result_text="ok")
        )
        await spawner_b._session_semaphore.acquire()
        trigger_task = asyncio.create_task(
            spawner_b.trigger("healing prompt", "healing", bypass_butler_semaphore=True)
        )
        result_b = await asyncio.wait_for(trigger_task, timeout=2.0)
        spawner_b._session_semaphore.release()
        assert result_b.success is True

        # Normal scenario: serializes when semaphore cap=1
        normal_dir = tmp_path / "normal"
        normal_dir.mkdir()
        completed: list[bool] = []

        class SlowAdapter(MockAdapter):
            async def invoke(self, *args, **kwargs):
                await asyncio.sleep(0.05)
                completed.append(True)
                return "ok", [], None

        spawner_n = Spawner(config=config, config_dir=normal_dir, runtime=SlowAdapter())
        results = await asyncio.gather(
            spawner_n.trigger("A", "tick"),
            spawner_n.trigger("B", "tick"),
        )
        assert all(r.success for r in results)
        assert len(completed) == 2


# ---------------------------------------------------------------------------
# Ingestion event propagation through trigger pipeline
# ---------------------------------------------------------------------------


class TestIngestionEventIdPropagation:
    """Spawner.trigger forwards ingestion_event_id to session_create.

    Regression for the "Conversation via unknown channel" chronicler bug:
    routed sessions need their session row to FK back into
    public.ingestion_events so chronicler contact resolution can join the
    sender's display name. Without this propagation every routed
    conversation degrades to the catch-all unknown-channel title.
    """

    @pytest.mark.parametrize(
        ("trigger_source", "ingestion_event_id", "expected"),
        [
            # route handler forwards the ingestion_event_id verbatim
            (
                "route",
                "019deb89-f3da-7a63-be86-794aa2b1de76",
                "019deb89-f3da-7a63-be86-794aa2b1de76",
            ),
            # tick / schedule callers leave it unset → session row stores NULL
            ("tick", None, None),
        ],
    )
    async def test_trigger_ingestion_event_id_forwarding(
        self, tmp_path: Path, trigger_source, ingestion_event_id, expected
    ):
        """ingestion_event_id is forwarded to session_create for route triggers and
        omitted (NULL) for internal triggers, never invented."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()
        mock_pool = AsyncMock()

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            mock_create.return_value = uuid.UUID("00000000-0000-0000-0000-000000000001")
            trigger_kwargs: dict[str, Any] = {}
            if ingestion_event_id is not None:
                trigger_kwargs["request_id"] = ingestion_event_id
                trigger_kwargs["ingestion_event_id"] = ingestion_event_id
            await Spawner(
                config=config,
                config_dir=config_dir,
                pool=mock_pool,
                runtime=MockAdapter(result_text="ok"),
            ).trigger("hello", trigger_source, **trigger_kwargs)

        mock_create.assert_called_once()
        kwargs = mock_create.call_args.kwargs
        assert kwargs.get("ingestion_event_id") == expected
        if expected is not None:
            assert kwargs.get("request_id") == expected


# ---------------------------------------------------------------------------
# emit_spend_event wiring — bu-eu38w
# ---------------------------------------------------------------------------


class TestEmitSpendEventWiring:
    """Verify that spawner calls emit_spend_event with the right payload."""

    async def test_emit_spend_event_called_with_correct_payload(self, tmp_path: Path):
        """After session closes, emit_spend_event receives kind/butler/model/tokens/session_id."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config(name="my-butler")
        mock_pool = AsyncMock()
        session_uuid = uuid.UUID("00000000-0000-0000-0000-000000000042")

        adapter = MockAdapter(
            result_text="hello",
            usage={"input_tokens": 1000, "output_tokens": 500},
        )

        captured_events: list[dict] = []

        def _fake_emit(event: dict) -> None:
            captured_events.append(event)

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=(
                    "codex",
                    "claude-sonnet-4-20250514",
                    [],
                    _FAKE_CATALOG_ID,
                    600,
                    "workhorse",
                ),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=SimpleNamespace(
                    allowed=True,
                    usage_24h=0,
                    usage_30d=0,
                    limit_24h=None,
                    limit_30d=None,
                ),
            ),
            patch("butlers.core.spawner.record_token_usage", new_callable=AsyncMock),
            patch(
                "butlers.api.routers.spend.emit_spend_event",
                side_effect=_fake_emit,
            ),
        ):
            mock_create.return_value = session_uuid
            result = await Spawner(
                config=config,
                config_dir=config_dir,
                pool=mock_pool,
                runtime=adapter,
            ).trigger("hello", "tick")

        assert result.success is True
        assert len(captured_events) == 1, "emit_spend_event must be called exactly once"
        ev = captured_events[0]
        assert ev["kind"] == "call"
        assert ev["butler"] == "my-butler"
        assert ev["model"] == "claude-sonnet-4-20250514"
        assert ev["tokens_in"] == 1000
        assert ev["tokens_out"] == 500
        assert ev["session_id"] == str(session_uuid)
        assert isinstance(ev["cost_usd"], float)
        assert "ts" in ev

    async def test_emit_spend_event_not_called_when_no_token_usage(self, tmp_path: Path):
        """When adapter reports no usage, emit_spend_event is not called."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config(name="my-butler")

        # No usage dict → _ledger_input_tokens remains None
        adapter = MockAdapter(result_text="ok", usage=None)

        captured_events: list[dict] = []

        def _fake_emit(event: dict) -> None:
            captured_events.append(event)

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock),
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=None,  # static fallback — no catalog_entry_id
            ),
            patch(
                "butlers.api.routers.spend.emit_spend_event",
                side_effect=_fake_emit,
            ),
        ):
            result = await Spawner(
                config=config,
                config_dir=config_dir,
                runtime=adapter,
            ).trigger("hello", "tick")

        assert result.success is True
        assert captured_events == [], "emit_spend_event must not be called when no token usage"

    async def test_emit_spend_event_broker_failure_does_not_break_session(self, tmp_path: Path):
        """A broker error in emit_spend_event must never propagate to the session result."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config(name="my-butler")
        mock_pool = AsyncMock()
        session_uuid = uuid.UUID("00000000-0000-0000-0000-000000000099")

        adapter = MockAdapter(
            result_text="ok",
            usage={"input_tokens": 100, "output_tokens": 50},
        )

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=(
                    "codex",
                    "claude-haiku-35-20241022",
                    [],
                    _FAKE_CATALOG_ID,
                    600,
                    "workhorse",
                ),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=SimpleNamespace(
                    allowed=True,
                    usage_24h=0,
                    usage_30d=0,
                    limit_24h=None,
                    limit_30d=None,
                ),
            ),
            patch("butlers.core.spawner.record_token_usage", new_callable=AsyncMock),
            patch(
                "butlers.api.routers.spend.emit_spend_event",
                side_effect=RuntimeError("broker exploded"),
            ),
        ):
            mock_create.return_value = session_uuid
            result = await Spawner(
                config=config,
                config_dir=config_dir,
                pool=mock_pool,
                runtime=adapter,
            ).trigger("hello", "tick")

        # Session must succeed despite broker error
        assert result.success is True
        assert result.error is None
