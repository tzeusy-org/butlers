"""Tests for the Spawner orchestration layer (butlers-0qp.8, butlers-f3t.7).

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
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from butlers.config import ButlerConfig, RuntimeConfig
from butlers.core.runtimes.base import RuntimeAdapter
from butlers.core.spawner import (
    CCSpawner,
    Spawner,
    SpawnerResult,
    _build_env,
)

pytestmark = pytest.mark.unit

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
) -> ButlerConfig:
    if model is not _SENTINEL:
        runtime = RuntimeConfig(
            model=model,
            max_concurrent_sessions=max_concurrent_sessions,
            max_queued_sessions=max_queued_sessions,
        )
    else:
        runtime = RuntimeConfig(
            max_concurrent_sessions=max_concurrent_sessions,
            max_queued_sessions=max_queued_sessions,
        )
    return ButlerConfig(
        name=name,
        port=port,
        runtime=runtime,
        modules=modules or {},
        env_required=env_required or [],
        env_optional=env_optional or [],
    )


# ---------------------------------------------------------------------------
# 8.2: Spawner result and invocation (runtime-agnostic)
# ---------------------------------------------------------------------------


class TestSpawnerResult:
    """SpawnerResult dataclass behavior."""

    def test_default_values(self):
        r = SpawnerResult()
        assert r.output is None
        assert r.tool_calls == []
        assert r.error is None
        assert r.duration_ms == 0
        assert r.model is None
        assert r.input_tokens is None
        assert r.output_tokens is None

    def test_success_result(self):
        r = SpawnerResult(output="output_text", tool_calls=[{"name": "t"}], duration_ms=42)
        assert r.output == "output_text"
        assert len(r.tool_calls) == 1
        assert r.error is None

    def test_error_result(self):
        r = SpawnerResult(error="something broke", duration_ms=10)
        assert r.output is None
        assert r.error == "something broke"

    def test_result_with_model(self):
        r = SpawnerResult(output="output_text", model="claude-opus-4-20250514", duration_ms=42)
        assert r.model == "claude-opus-4-20250514"

    def test_result_with_token_counts(self):
        r = SpawnerResult(
            output="output_text",
            duration_ms=42,
            input_tokens=1500,
            output_tokens=2500,
        )
        assert r.input_tokens == 1500
        assert r.output_tokens == 2500


class TestSpawnerInvocation:
    """Tests for runtime invocation via Spawner.trigger() with MockAdapter."""

    async def test_success_with_result(self, tmp_path: Path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()

        adapter = MockAdapter(result_text="Hello from mock!")
        spawner = Spawner(
            config=config,
            config_dir=config_dir,
            runtime=adapter,
        )

        result = await spawner.trigger("hello", "tick")
        assert result.output == "Hello from mock!"
        assert result.error is None
        assert result.duration_ms >= 0

    async def test_tool_calls_captured(self, tmp_path: Path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()

        adapter = MockAdapter(
            result_text="Done with tools",
            tool_calls=[{"id": "tool_1", "name": "state_get", "input": {"key": "foo"}}],
        )
        spawner = Spawner(
            config=config,
            config_dir=config_dir,
            runtime=adapter,
        )

        result = await spawner.trigger("use tools", "trigger_tool")
        assert result.output == "Done with tools"
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0]["name"] == "state_get"
        assert result.tool_calls[0]["input"] == {"key": "foo"}

    async def test_error_wrapped_in_result(self, tmp_path: Path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()

        adapter = MockAdapter(error="adapter connection failed")
        spawner = Spawner(
            config=config,
            config_dir=config_dir,
            runtime=adapter,
        )

        result = await spawner.trigger("fail", "tick")
        assert result.error is not None
        assert "RuntimeError" in result.error
        assert "adapter connection failed" in result.error
        assert result.output is None
        assert result.duration_ms >= 0
        assert adapter.reset_calls == 1

    async def test_duration_measured(self, tmp_path: Path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()

        adapter = MockAdapter(result_text="slow result", delay=0.05)
        spawner = Spawner(
            config=config,
            config_dir=config_dir,
            runtime=adapter,
        )

        result = await spawner.trigger("slow", "tick")
        assert result.duration_ms >= 40  # at least ~50ms sleep

    async def test_runtime_not_reset_when_failure_before_invoke(self, tmp_path: Path):
        """reset() is skipped when the exception occurs before runtime invocation."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()

        adapter = MockAdapter()
        spawner = Spawner(config=config, config_dir=config_dir, runtime=adapter)

        with patch("butlers.core.spawner.read_system_prompt", side_effect=RuntimeError("boom")):
            result = await spawner.trigger("hi", "tick")

        assert result.success is False
        assert result.error is not None
        assert "RuntimeError: boom" in result.error
        assert adapter.calls == []
        assert adapter.reset_calls == 0

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

    async def test_anthropic_key_always_included(self):
        config = _make_config()
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test-123"}, clear=False):
            env = await _build_env(config)
            assert env["ANTHROPIC_API_KEY"] == "sk-test-123"

    async def test_openai_key_always_included(self):
        config = _make_config()
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-openai-123"}, clear=False):
            env = await _build_env(config)
            assert env["OPENAI_API_KEY"] == "sk-openai-123"

    async def test_path_baseline_included(self):
        """PATH is passed through so runtime shebangs can resolve binaries."""
        config = _make_config()
        with patch.dict(os.environ, {"PATH": "/tmp/node-bin"}, clear=True):
            env = await _build_env(config)
            assert env["PATH"] == "/tmp/node-bin"

    async def test_required_env_vars_included(self):
        config = _make_config(env_required=["MY_SECRET"])
        with patch.dict(
            os.environ,
            {"ANTHROPIC_API_KEY": "sk-key", "MY_SECRET": "s3cret"},
            clear=False,
        ):
            env = await _build_env(config)
            assert env["MY_SECRET"] == "s3cret"

    async def test_optional_env_vars_included_when_present(self):
        config = _make_config(env_optional=["OPT_VAR"])
        with patch.dict(
            os.environ,
            {"ANTHROPIC_API_KEY": "sk-key", "OPT_VAR": "opt-val"},
            clear=False,
        ):
            env = await _build_env(config)
            assert env["OPT_VAR"] == "opt-val"

    async def test_optional_env_vars_excluded_when_absent(self):
        config = _make_config(env_optional=["MISSING_OPT"])
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-key"}, clear=False):
            # Ensure the var is not set
            os.environ.pop("MISSING_OPT", None)
            env = await _build_env(config)
            assert "MISSING_OPT" not in env

    async def test_module_credentials_included(self):
        config = _make_config()
        module_creds = {"email": ["SMTP_PASSWORD", "IMAP_TOKEN"]}
        with patch.dict(
            os.environ,
            {
                "ANTHROPIC_API_KEY": "sk-key",
                "SMTP_PASSWORD": "pw123",
                "IMAP_TOKEN": "tok456",
            },
            clear=False,
        ):
            env = await _build_env(config, module_credentials_env=module_creds)
            assert env["SMTP_PASSWORD"] == "pw123"
            assert env["IMAP_TOKEN"] == "tok456"

    async def test_undeclared_vars_not_leaked(self):
        config = _make_config(env_required=["DECLARED"])
        with patch.dict(
            os.environ,
            {
                "ANTHROPIC_API_KEY": "sk-key",
                "DECLARED": "yes",
                "UNDECLARED_SECRET": "should-not-leak",
            },
            clear=False,
        ):
            env = await _build_env(config)
            assert "UNDECLARED_SECRET" not in env
            assert "DECLARED" in env

    async def test_anthropic_key_missing_not_included(self):
        config = _make_config()
        env_copy = os.environ.copy()
        env_copy.pop("ANTHROPIC_API_KEY", None)
        with patch.dict(os.environ, env_copy, clear=True):
            env = await _build_env(config)
            assert "ANTHROPIC_API_KEY" not in env

    async def test_openai_key_missing_not_included(self):
        config = _make_config()
        env_copy = os.environ.copy()
        env_copy.pop("OPENAI_API_KEY", None)
        with patch.dict(os.environ, env_copy, clear=True):
            env = await _build_env(config)
            assert "OPENAI_API_KEY" not in env

    # ------------------------------------------------------------------
    # DB-first resolution path (with mocked CredentialStore)
    # ------------------------------------------------------------------

    async def test_db_resolution_anthropic_key(self):
        """ANTHROPIC_API_KEY resolved from DB takes precedence over env."""
        config = _make_config()
        store = AsyncMock()
        store.resolve = AsyncMock(
            side_effect=lambda key: "db-sk-key" if key == "ANTHROPIC_API_KEY" else None
        )
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "env-sk-key"}, clear=False):
            env = await _build_env(config, credential_store=store)
        assert env["ANTHROPIC_API_KEY"] == "db-sk-key"

    async def test_db_resolution_openai_key(self):
        """OPENAI_API_KEY resolved from DB takes precedence over env."""
        config = _make_config()
        store = AsyncMock()
        store.resolve = AsyncMock(
            side_effect=lambda key: "db-openai-key" if key == "OPENAI_API_KEY" else None
        )
        with patch.dict(os.environ, {"OPENAI_API_KEY": "env-openai-key"}, clear=False):
            env = await _build_env(config, credential_store=store)
        assert env["OPENAI_API_KEY"] == "db-openai-key"

    async def test_db_resolution_env_fallback_when_db_miss(self):
        """When DB has no value, env var is used as fallback via CredentialStore.resolve()."""
        config = _make_config()
        store = AsyncMock()
        store.resolve = AsyncMock(return_value="env-via-store-fallback")
        env = await _build_env(config, credential_store=store)
        assert env["ANTHROPIC_API_KEY"] == "env-via-store-fallback"

    async def test_db_resolution_module_credentials(self):
        """Module credentials resolved from DB when CredentialStore is provided."""
        config = _make_config()
        module_creds = {"email": ["SMTP_PASSWORD", "IMAP_TOKEN"]}
        store = AsyncMock()
        resolved = {
            "ANTHROPIC_API_KEY": "sk-key",
            "SMTP_PASSWORD": "db-smtp-pw",
            "IMAP_TOKEN": "db-imap-tok",
        }
        store.resolve = AsyncMock(side_effect=lambda key: resolved.get(key))
        env = await _build_env(config, module_credentials_env=module_creds, credential_store=store)
        assert env["SMTP_PASSWORD"] == "db-smtp-pw"
        assert env["IMAP_TOKEN"] == "db-imap-tok"

    async def test_db_resolution_required_env_vars(self):
        """butler-level required env vars resolved from DB when CredentialStore is provided."""
        config = _make_config(env_required=["MY_SECRET"])
        resolved = {"ANTHROPIC_API_KEY": "sk-key", "MY_SECRET": "db-secret-value"}
        store = AsyncMock()
        store.resolve = AsyncMock(side_effect=lambda key: resolved.get(key))
        env = await _build_env(config, credential_store=store)
        assert env["MY_SECRET"] == "db-secret-value"

    async def test_db_resolution_missing_key_excluded(self):
        """When DB and env have no value for a key, it is excluded from env dict."""
        config = _make_config(env_required=["MISSING_KEY"])
        store = AsyncMock()
        store.resolve = AsyncMock(return_value=None)  # Nothing found anywhere
        env = await _build_env(config, credential_store=store)
        assert "MISSING_KEY" not in env

    async def test_env_passed_to_adapter(self, tmp_path: Path):
        """Verify the env dict is passed through to the adapter."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config(env_required=["BUTLER_SECRET"])

        adapter = MockAdapter(result_text="", capture=True)
        spawner = Spawner(
            config=config,
            config_dir=config_dir,
            runtime=adapter,
        )

        with patch.dict(
            os.environ,
            {"ANTHROPIC_API_KEY": "sk-key", "BUTLER_SECRET": "s3cret"},
            clear=False,
        ):
            await spawner.trigger("test env", "tick")

        assert len(adapter.calls) == 1
        passed_env = adapter.calls[0]["env"]
        assert passed_env["ANTHROPIC_API_KEY"] == "sk-key"
        assert passed_env["BUTLER_SECRET"] == "s3cret"

    async def test_spawner_with_credential_store_passes_db_values_to_adapter(self, tmp_path: Path):
        """Spawner with credential_store resolves credentials from DB for spawned instances."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config(env_required=["MY_API_KEY"])

        store = AsyncMock()
        resolved = {"ANTHROPIC_API_KEY": "db-anthropic", "MY_API_KEY": "db-my-api-key"}
        store.resolve = AsyncMock(side_effect=lambda key: resolved.get(key))

        adapter = MockAdapter(result_text="", capture=True)
        spawner = Spawner(
            config=config,
            config_dir=config_dir,
            runtime=adapter,
            credential_store=store,
        )

        await spawner.trigger("test db creds", "tick")

        assert len(adapter.calls) == 1
        passed_env = adapter.calls[0]["env"]
        assert passed_env["ANTHROPIC_API_KEY"] == "db-anthropic"
        assert passed_env["MY_API_KEY"] == "db-my-api-key"


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

    async def test_n1_produces_serial_dispatch(self, tmp_path: Path):
        """n=1 (default) serializes invocations — identical to Lock behaviour."""
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
        # Verify serial execution: start/end pairs are interleaved (non-overlapping)
        log = adapter.execution_log
        assert len(log) == 4
        assert log[0] == ("start", "A") or log[0] == ("start", "B")
        # whichever starts first must end before the other starts
        assert log[1][0] == "end"
        assert log[2][0] == "start"
        assert log[3][0] == "end"
        assert log[1][1] == log[0][1]  # same prompt started and ended
        assert log[3][1] == log[2][1]

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

    async def test_self_trigger_rejected_when_n1_semaphore_full(self, tmp_path: Path):
        """With n=1, trigger-source rejected when the single slot is taken."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config(max_concurrent_sessions=1)

        adapter = MockAdapter(result_text="should not run", capture=True)
        spawner = Spawner(config=config, config_dir=config_dir, runtime=adapter)

        await spawner._session_semaphore.acquire()
        try:
            result = await spawner.trigger("nested", "trigger")
        finally:
            spawner._session_semaphore.release()

        assert result.success is False
        assert "cannot be called while another session is in flight" in result.error
        assert adapter.calls == []

    async def test_self_trigger_allowed_when_n3_has_free_slot(self, tmp_path: Path):
        """With n=3, trigger-source is allowed when only 2 of 3 slots are taken."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config(max_concurrent_sessions=3)

        adapter = MockAdapter(result_text="allowed", capture=True)
        spawner = Spawner(config=config, config_dir=config_dir, runtime=adapter)

        # Occupy 2 of 3 slots — one slot is still free
        await spawner._session_semaphore.acquire()
        await spawner._session_semaphore.acquire()
        try:
            result = await spawner.trigger("self-trigger-ok", "trigger")
        finally:
            spawner._session_semaphore.release()
            spawner._session_semaphore.release()

        # Should succeed: one slot was free so the guard should NOT reject
        assert result.success is True
        assert result.error is None

    async def test_self_trigger_rejected_when_n3_all_slots_full(self, tmp_path: Path):
        """With n=3, trigger-source rejected when all 3 slots are occupied."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config(max_concurrent_sessions=3)

        adapter = MockAdapter(result_text="should not run", capture=True)
        spawner = Spawner(config=config, config_dir=config_dir, runtime=adapter)

        # Occupy all 3 slots
        await spawner._session_semaphore.acquire()
        await spawner._session_semaphore.acquire()
        await spawner._session_semaphore.acquire()
        try:
            result = await spawner.trigger("nested", "trigger")
        finally:
            spawner._session_semaphore.release()
            spawner._session_semaphore.release()
            spawner._session_semaphore.release()

        assert result.success is False
        assert "cannot be called while another session is in flight" in result.error
        assert adapter.calls == []

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

    async def test_semaphore_slot_count_matches_max_concurrent_sessions(self, tmp_path: Path):
        """Spawner initialises semaphore with the configured concurrency limit."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()

        for n in (1, 3, 5):
            config = _make_config(max_concurrent_sessions=n)
            spawner = Spawner(config=config, config_dir=config_dir, runtime=MockAdapter())
            assert spawner._session_semaphore._value == n, (
                f"Expected semaphore value {n}, got {spawner._session_semaphore._value}"
            )

    async def test_queue_backpressure_rejects_when_waiters_at_limit(self, tmp_path: Path):
        """New triggers are rejected once max_queued_sessions is reached."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
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

    async def test_session_created_and_completed_on_success(self, tmp_path: Path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()

        mock_pool = AsyncMock()
        mock_pool.fetchval = AsyncMock(return_value="00000000-0000-0000-0000-000000000001")

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock) as mock_complete,
        ):
            import uuid

            fake_session_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
            mock_create.return_value = fake_session_id

            adapter = MockAdapter(result_text="Hello from mock!")
            spawner = Spawner(
                config=config,
                config_dir=config_dir,
                pool=mock_pool,
                runtime=adapter,
            )

            await spawner.trigger("log me", "schedule")

            # session_create called with correct args
            mock_create.assert_called_once()
            create_args, create_kwargs = mock_create.call_args
            assert create_args[0] is mock_pool
            assert create_args[1] == "log me"
            assert create_args[2] == "schedule"
            assert create_kwargs.get("model") == "claude-haiku-4-5-20251001"

            # session_complete called with result data
            mock_complete.assert_called_once()
            args, kwargs = mock_complete.call_args
            assert args[0] is mock_pool
            assert args[1] == fake_session_id
            assert kwargs["output"] == "Hello from mock!"
            assert isinstance(kwargs["tool_calls"], list)
            assert kwargs["duration_ms"] >= 0
            assert kwargs["success"] is True

    async def test_session_completed_on_error(self, tmp_path: Path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()

        mock_pool = AsyncMock()

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock) as mock_complete,
        ):
            import uuid

            fake_session_id = uuid.UUID("00000000-0000-0000-0000-000000000002")
            mock_create.return_value = fake_session_id

            adapter = MockAdapter(error="adapter connection failed")
            spawner = Spawner(
                config=config,
                config_dir=config_dir,
                pool=mock_pool,
                runtime=adapter,
            )

            result = await spawner.trigger("fail", "tick")
            assert result.error is not None

            # session_complete called with error info
            mock_complete.assert_called_once()
            args, kwargs = mock_complete.call_args
            assert args[0] is mock_pool
            assert args[1] == fake_session_id
            assert kwargs["output"] is None
            assert kwargs["tool_calls"] == []
            assert kwargs["success"] is False
            assert "RuntimeError" in kwargs["error"]

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

    async def test_session_logging_includes_model(self, tmp_path: Path):
        """Session logging passes model through to session_create."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config(model="claude-opus-4-20250514")

        mock_pool = AsyncMock()

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
        ):
            import uuid

            fake_session_id = uuid.UUID("00000000-0000-0000-0000-000000000003")
            mock_create.return_value = fake_session_id

            spawner = CCSpawner(
                config=config,
                config_dir=config_dir,
                pool=mock_pool,
                sdk_query=_result_sdk_query,
            )

            await spawner.trigger("model test", "schedule")

            mock_create.assert_called_once()
            create_args, create_kwargs = mock_create.call_args
            assert create_args[0] is mock_pool
            assert create_args[1] == "model test"
            assert create_args[2] == "schedule"
            assert create_kwargs["model"] == "claude-opus-4-20250514"


# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# SDK query helpers for legacy compat tests
# ---------------------------------------------------------------------------


async def _result_sdk_query(*, prompt: str, options: Any):
    """Mock SDK query that returns a successful result."""
    from claude_agent_sdk import ResultMessage

    yield ResultMessage(
        subtype="result",
        duration_ms=10,
        duration_api_ms=8,
        is_error=False,
        num_turns=1,
        session_id="helper-test",
        total_cost_usd=0.0,
        usage={},
        result="Result from helper",
    )


async def _error_sdk_query(*, prompt: str, options: Any):
    """Mock SDK query that raises an error."""
    raise RuntimeError("SDK query failed")
    yield  # makes this an async generator


# Model passthrough tests
# ---------------------------------------------------------------------------


class TestModelPassthrough:
    """Model string from config is passed through to SDK options."""

    async def test_model_passed_to_sdk_options(self, tmp_path: Path):
        """When model is set in config, it appears in ClaudeAgentOptions."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config(model="claude-sonnet-4-20250514")

        captured_options: list[Any] = []

        async def capturing_sdk(*, prompt: str, options: Any):
            captured_options.append(options)
            return
            yield

        spawner = CCSpawner(
            config=config,
            config_dir=config_dir,
            sdk_query=capturing_sdk,
        )

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-key"}, clear=False):
            await spawner.trigger("test model", "tick")

        assert len(captured_options) == 1
        assert captured_options[0].model == "claude-sonnet-4-20250514"

    async def test_model_default_when_not_configured(self, tmp_path: Path):
        """When model is not set, ClaudeAgentOptions.model defaults to Haiku."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()  # model defaults to Haiku

        captured_options: list[Any] = []

        async def capturing_sdk(*, prompt: str, options: Any):
            captured_options.append(options)
            return
            yield

        spawner = CCSpawner(
            config=config,
            config_dir=config_dir,
            sdk_query=capturing_sdk,
        )

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-key"}, clear=False):
            await spawner.trigger("test default", "tick")

        assert len(captured_options) == 1
        assert captured_options[0].model == "claude-haiku-4-5-20251001"

    async def test_model_in_spawner_result_on_success(self, tmp_path: Path):
        """SpawnerResult includes the model used on successful invocation."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config(model="claude-opus-4-20250514")

        spawner = CCSpawner(
            config=config,
            config_dir=config_dir,
            sdk_query=_result_sdk_query,
        )

        result = await spawner.trigger("test", "tick")
        assert result.model == "claude-opus-4-20250514"

    async def test_model_in_spawner_result_on_error(self, tmp_path: Path):
        """SpawnerResult includes the model even on error."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config(model="claude-opus-4-20250514")

        spawner = CCSpawner(
            config=config,
            config_dir=config_dir,
            sdk_query=_error_sdk_query,
        )

        result = await spawner.trigger("fail", "tick")
        assert result.error is not None
        assert result.model == "claude-opus-4-20250514"

    async def test_model_default_in_spawner_result_when_not_configured(self, tmp_path: Path):
        """SpawnerResult.model defaults to Haiku when not explicitly configured."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()

        spawner = CCSpawner(
            config=config,
            config_dir=config_dir,
            sdk_query=_result_sdk_query,
        )

        result = await spawner.trigger("test", "tick")
        assert result.model == "claude-haiku-4-5-20251001"


# ---------------------------------------------------------------------------
# Integration-style test: full flow with MockAdapter
# ---------------------------------------------------------------------------


class TestFullFlow:
    """End-to-end spawner flow with MockAdapter."""

    async def test_full_trigger_flow(self, tmp_path: Path):
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
        spawner = Spawner(
            config=config,
            config_dir=config_dir,
            runtime=adapter,
        )

        with patch(
            "butlers.core.spawner.fetch_memory_context",
            new_callable=AsyncMock,
            return_value=None,
        ) as mock_fetch:
            with patch.dict(
                os.environ,
                {"ANTHROPIC_API_KEY": "sk-flow", "CUSTOM_VAR": "cv"},
                clear=False,
            ):
                result = await spawner.trigger("do the thing", "schedule")

        mock_fetch.assert_called_once_with(
            None,
            "flow-butler",
            "do the thing",
            token_budget=3000,
        )

        assert result.output == "All done!"
        assert result.error is None
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0]["name"] == "state_set"
        assert result.duration_ms >= 0

        # Verify adapter received correct args
        assert len(adapter.calls) == 1
        call = adapter.calls[0]
        assert call["system_prompt"] == "You are the test butler."
        assert "flow-butler" in call["mcp_servers"]
        assert call["env"]["ANTHROPIC_API_KEY"] == "sk-flow"
        assert call["env"]["CUSTOM_VAR"] == "cv"

    async def test_full_trigger_flow_appends_memory_context_suffix(self, tmp_path: Path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "CLAUDE.md").write_text("You are the test butler.")

        config = _make_config(
            name="flow-butler",
            port=9200,
            modules={"memory": {}},
        )

        adapter = MockAdapter(result_text="All done!", capture=True)
        spawner = Spawner(
            config=config,
            config_dir=config_dir,
            runtime=adapter,
        )

        memory_ctx = "# Memory Context\n- user prefers concise updates"
        with patch(
            "butlers.core.spawner.fetch_memory_context",
            new_callable=AsyncMock,
            return_value=memory_ctx,
        ):
            await spawner.trigger("do the thing", "schedule")

        assert len(adapter.calls) == 1
        call = adapter.calls[0]
        assert call["system_prompt"] == f"You are the test butler.\n\n{memory_ctx}"


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


@pytest.mark.parametrize(
    "adapter_factory,expect_error",
    [
        pytest.param(_make_result_adapter, False, id="success"),
        pytest.param(_make_error_adapter, True, id="error"),
    ],
)
class TestParametrizedSessionLogging:
    """Session logging parametrized across success and error adapters."""

    async def test_session_logged(self, tmp_path: Path, adapter_factory, expect_error):
        """Session is created and completed regardless of outcome."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()

        mock_pool = AsyncMock()

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock) as mock_complete,
        ):
            import uuid

            fake_session_id = uuid.UUID("00000000-0000-0000-0000-000000000099")
            mock_create.return_value = fake_session_id

            adapter = adapter_factory()
            spawner = Spawner(
                config=config,
                config_dir=config_dir,
                pool=mock_pool,
                runtime=adapter,
            )

            result = await spawner.trigger("test", "tick")

            mock_create.assert_called_once()
            create_args, create_kwargs = mock_create.call_args
            assert create_args[0] is mock_pool
            assert create_args[1] == "test"
            assert create_args[2] == "tick"
            mock_complete.assert_called_once()

            args, kwargs = mock_complete.call_args
            assert args[0] is mock_pool
            assert args[1] == fake_session_id

            if expect_error:
                assert result.error is not None
                assert kwargs["output"] is None
                assert kwargs["tool_calls"] == []
                assert kwargs["success"] is False
            else:
                assert result.error is None
                assert kwargs["duration_ms"] >= 0
                assert kwargs["success"] is True


# ---------------------------------------------------------------------------
# Legacy sdk_query compat test
# ---------------------------------------------------------------------------


class TestLegacySdkQueryCompat:
    """Verify backward compatibility with sdk_query parameter."""

    async def test_sdk_query_still_works(self, tmp_path: Path):
        """Spawner(sdk_query=...) wraps the callable in a ClaudeCodeAdapter."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()

        from claude_agent_sdk import ResultMessage

        async def mock_sdk(*, prompt: str, options: Any):
            yield ResultMessage(
                subtype="result",
                duration_ms=10,
                duration_api_ms=8,
                is_error=False,
                num_turns=1,
                session_id="compat-test",
                total_cost_usd=0.0,
                usage={},
                result="Hello from legacy!",
            )

        spawner = Spawner(
            config=config,
            config_dir=config_dir,
            sdk_query=mock_sdk,
        )

        result = await spawner.trigger("hello", "tick")
        assert result.output == "Hello from legacy!"
        assert result.error is None

    async def test_full_flow_with_model(self, tmp_path: Path):
        """Full flow with a model configured passes it through to SDK options."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        claude_md = config_dir / "CLAUDE.md"
        claude_md.write_text("You are the test butler with a model.")

        config = _make_config(
            name="model-butler",
            port=9201,
            model="claude-sonnet-4-20250514",
        )

        captured: dict[str, Any] = {}

        async def capturing_sdk(*, prompt: str, options: Any):
            captured["prompt"] = prompt
            captured["options"] = options
            from claude_agent_sdk import ResultMessage

            yield ResultMessage(
                subtype="result",
                duration_ms=10,
                duration_api_ms=8,
                is_error=False,
                num_turns=1,
                session_id="model-flow",
                total_cost_usd=0.005,
                usage={},
                result="Model test done!",
            )

        spawner = CCSpawner(
            config=config,
            config_dir=config_dir,
            sdk_query=capturing_sdk,
        )

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-flow"}, clear=False):
            result = await spawner.trigger("model test", "tick")

        assert result.output == "Model test done!"
        assert result.model == "claude-sonnet-4-20250514"
        assert captured["options"].model == "claude-sonnet-4-20250514"


# ---------------------------------------------------------------------------
# Token usage capture from adapter
# ---------------------------------------------------------------------------


class TestTokenUsageCapture:
    """Tests for extracting input_tokens and output_tokens from adapter response."""

    async def test_token_counts_in_spawner_result(self, tmp_path: Path):
        """SpawnerResult includes token counts when adapter returns usage."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()

        adapter = MockAdapter(
            result_text="Hello!",
            usage={"input_tokens": 100, "output_tokens": 200},
        )
        spawner = Spawner(
            config=config,
            config_dir=config_dir,
            runtime=adapter,
        )

        result = await spawner.trigger("test tokens", "tick")
        assert result.input_tokens == 100
        assert result.output_tokens == 200

    async def test_token_counts_none_when_no_usage(self, tmp_path: Path):
        """SpawnerResult has None tokens when adapter returns no usage."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()

        adapter = MockAdapter(result_text="Hello!", usage=None)
        spawner = Spawner(
            config=config,
            config_dir=config_dir,
            runtime=adapter,
        )

        result = await spawner.trigger("no tokens", "tick")
        assert result.input_tokens is None
        assert result.output_tokens is None

    async def test_token_counts_none_on_error(self, tmp_path: Path):
        """SpawnerResult has None tokens when adapter raises an error."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()

        adapter = MockAdapter(error="adapter failed")
        spawner = Spawner(
            config=config,
            config_dir=config_dir,
            runtime=adapter,
        )

        result = await spawner.trigger("fail", "tick")
        assert result.error is not None
        assert result.input_tokens is None
        assert result.output_tokens is None

    async def test_token_counts_passed_to_session_complete(self, tmp_path: Path):
        """Token counts are passed to session_complete on success."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()

        mock_pool = AsyncMock()

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock) as mock_complete,
        ):
            import uuid

            fake_session_id = uuid.UUID("00000000-0000-0000-0000-000000000010")
            mock_create.return_value = fake_session_id

            adapter = MockAdapter(
                result_text="With tokens!",
                usage={"input_tokens": 500, "output_tokens": 1000},
            )
            spawner = Spawner(
                config=config,
                config_dir=config_dir,
                pool=mock_pool,
                runtime=adapter,
            )

            await spawner.trigger("test", "tick")

            mock_complete.assert_called_once()
            _, kwargs = mock_complete.call_args
            assert kwargs["input_tokens"] == 500
            assert kwargs["output_tokens"] == 1000

    async def test_session_complete_gets_none_tokens_without_usage(self, tmp_path: Path):
        """session_complete gets None tokens when adapter returns no usage."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()

        mock_pool = AsyncMock()

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock) as mock_complete,
        ):
            import uuid

            fake_session_id = uuid.UUID("00000000-0000-0000-0000-000000000011")
            mock_create.return_value = fake_session_id

            adapter = MockAdapter(result_text="No usage")
            spawner = Spawner(
                config=config,
                config_dir=config_dir,
                pool=mock_pool,
                runtime=adapter,
            )

            await spawner.trigger("test", "tick")

            mock_complete.assert_called_once()
            _, kwargs = mock_complete.call_args
            assert kwargs["input_tokens"] is None
            assert kwargs["output_tokens"] is None

    async def test_token_counts_from_claude_agent_sdk(self, tmp_path: Path):
        """End-to-end: token counts extracted from Claude Agent SDK ResultMessage."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()

        from claude_agent_sdk import ResultMessage

        async def sdk_with_usage(*, prompt: str, options: Any):
            yield ResultMessage(
                subtype="result",
                duration_ms=50,
                duration_api_ms=40,
                is_error=False,
                num_turns=2,
                session_id="token-test",
                total_cost_usd=0.05,
                usage={"input_tokens": 1234, "output_tokens": 5678},
                result="Done with tokens!",
            )

        spawner = CCSpawner(
            config=config,
            config_dir=config_dir,
            sdk_query=sdk_with_usage,
        )

        result = await spawner.trigger("test sdk tokens", "tick")
        assert result.output == "Done with tokens!"
        assert result.input_tokens == 1234
        assert result.output_tokens == 5678

    async def test_token_counts_none_with_empty_sdk_usage(self, tmp_path: Path):
        """Token counts are None when SDK usage dict is empty."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()

        from claude_agent_sdk import ResultMessage

        async def sdk_empty_usage(*, prompt: str, options: Any):
            yield ResultMessage(
                subtype="result",
                duration_ms=10,
                duration_api_ms=8,
                is_error=False,
                num_turns=1,
                session_id="empty-usage",
                total_cost_usd=0.0,
                usage={},
                result="Empty usage",
            )

        spawner = CCSpawner(
            config=config,
            config_dir=config_dir,
            sdk_query=sdk_empty_usage,
        )

        result = await spawner.trigger("test empty usage", "tick")
        assert result.input_tokens is None
        assert result.output_tokens is None

    async def test_token_counts_none_with_none_sdk_usage(self, tmp_path: Path):
        """Token counts are None when SDK usage is None."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()

        from claude_agent_sdk import ResultMessage

        async def sdk_none_usage(*, prompt: str, options: Any):
            yield ResultMessage(
                subtype="result",
                duration_ms=10,
                duration_api_ms=8,
                is_error=False,
                num_turns=1,
                session_id="none-usage",
                total_cost_usd=0.0,
                usage=None,
                result="None usage",
            )

        spawner = CCSpawner(
            config=config,
            config_dir=config_dir,
            sdk_query=sdk_none_usage,
        )

        result = await spawner.trigger("test none usage", "tick")
        assert result.input_tokens is None
        assert result.output_tokens is None

    async def test_partial_usage_only_input_tokens(self, tmp_path: Path):
        """When usage has only input_tokens, output_tokens is None."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()

        adapter = MockAdapter(
            result_text="Partial",
            usage={"input_tokens": 300},
        )
        spawner = Spawner(
            config=config,
            config_dir=config_dir,
            runtime=adapter,
        )

        result = await spawner.trigger("partial", "tick")
        assert result.input_tokens == 300
        assert result.output_tokens is None

    async def test_partial_usage_only_output_tokens(self, tmp_path: Path):
        """When usage has only output_tokens, input_tokens is None."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()

        adapter = MockAdapter(
            result_text="Partial output only",
            usage={"output_tokens": 750},
        )
        spawner = Spawner(
            config=config,
            config_dir=config_dir,
            runtime=adapter,
        )

        result = await spawner.trigger("partial output", "tick")
        assert result.input_tokens is None
        assert result.output_tokens == 750

    async def test_usage_with_extra_keys_ignored(self, tmp_path: Path):
        """Extra keys in usage dict are ignored; only token counts extracted."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()

        adapter = MockAdapter(
            result_text="Extra keys",
            usage={
                "input_tokens": 400,
                "output_tokens": 800,
                "total_cost_usd": 0.05,
                "cache_read_tokens": 200,
            },
        )
        spawner = Spawner(
            config=config,
            config_dir=config_dir,
            runtime=adapter,
        )

        result = await spawner.trigger("extra keys", "tick")
        assert result.input_tokens == 400
        assert result.output_tokens == 800

    async def test_zero_token_counts(self, tmp_path: Path):
        """Zero token counts are preserved (not treated as None)."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()

        adapter = MockAdapter(
            result_text="Zero tokens",
            usage={"input_tokens": 0, "output_tokens": 0},
        )
        spawner = Spawner(
            config=config,
            config_dir=config_dir,
            runtime=adapter,
        )

        result = await spawner.trigger("zero tokens", "tick")
        assert result.input_tokens == 0
        assert result.output_tokens == 0

    async def test_session_complete_no_tokens_on_error(self, tmp_path: Path):
        """session_complete is not called with token kwargs on error path."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()

        mock_pool = AsyncMock()

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock) as mock_complete,
        ):
            import uuid

            fake_session_id = uuid.UUID("00000000-0000-0000-0000-000000000012")
            mock_create.return_value = fake_session_id

            adapter = MockAdapter(error="boom")
            spawner = Spawner(
                config=config,
                config_dir=config_dir,
                pool=mock_pool,
                runtime=adapter,
            )

            result = await spawner.trigger("fail with pool", "tick")
            assert result.error is not None
            assert result.input_tokens is None
            assert result.output_tokens is None

            # session_complete called on error path without token kwargs
            mock_complete.assert_called_once()
            _, kwargs = mock_complete.call_args
            assert kwargs["success"] is False
            assert "input_tokens" not in kwargs
            assert "output_tokens" not in kwargs

    async def test_empty_usage_dict(self, tmp_path: Path):
        """Empty usage dict results in None token counts."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()

        adapter = MockAdapter(
            result_text="Empty dict",
            usage={},
        )
        spawner = Spawner(
            config=config,
            config_dir=config_dir,
            runtime=adapter,
        )

        result = await spawner.trigger("empty dict", "tick")
        assert result.input_tokens is None
        assert result.output_tokens is None

    async def test_sequence_first_error_then_tokens(self, tmp_path: Path):
        """After an error (no tokens), a successful call returns tokens correctly."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()

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
        spawner = Spawner(
            config=config,
            config_dir=config_dir,
            runtime=adapter,
        )

        result1 = await spawner.trigger("first", "tick")
        assert result1.error is not None
        assert result1.input_tokens is None
        assert result1.output_tokens is None

        result2 = await spawner.trigger("second", "tick")
        assert result2.error is None
        assert result2.input_tokens == 42
        assert result2.output_tokens == 84
