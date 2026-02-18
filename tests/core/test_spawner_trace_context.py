"""Tests for trace context propagation through the Spawner (session context lifecycle).

Verifies that the Spawner sets/clears the active session context around
runtime invocation, so that tool_span in HTTP handler tasks can parent
to the session span.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from butlers.config import ButlerConfig
from butlers.core.runtimes.base import RuntimeAdapter
from butlers.core.spawner import Spawner
from butlers.core.telemetry import clear_active_session_context, get_active_session_context

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reset_otel_global_state():
    trace._TRACER_PROVIDER_SET_ONCE = trace.Once()
    trace._TRACER_PROVIDER = None


@pytest.fixture(autouse=True)
def otel_provider():
    """In-memory OTel provider so spans are real and inspectable."""
    _reset_otel_global_state()
    exporter = InMemorySpanExporter()
    resource = Resource.create({"service.name": "butler-test"})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    yield exporter
    provider.shutdown()
    _reset_otel_global_state()


@pytest.fixture(autouse=True)
def _clean_session_context():
    clear_active_session_context()
    yield
    clear_active_session_context()


def _make_config(name: str = "test-butler", port: int = 9100) -> ButlerConfig:
    return ButlerConfig(name=name, port=port, env_required=[], env_optional=[])


class ContextCapturingAdapter(RuntimeAdapter):
    """Adapter that captures get_active_session_context() during invoke."""

    def __init__(self, *, error: str | None = None) -> None:
        self.captured_context = "NOT_CALLED"  # sentinel
        self._error = error

    @property
    def binary_name(self) -> str:
        return "context-capturing"

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
        self.captured_context = get_active_session_context()
        if self._error:
            raise RuntimeError(self._error)
        return "ok", [], None

    def build_config_file(self, mcp_servers: dict[str, Any], tmp_dir: Path) -> Path:
        p = tmp_dir / "ctx_config.json"
        p.write_text("{}")
        return p

    def parse_system_prompt_file(self, config_dir: Path) -> str:
        return ""


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSpawnerSessionContext:
    """Spawner sets/clears active session context around runtime invocation."""

    async def test_session_context_set_during_invocation(self, tmp_path: Path):
        """Adapter sees a non-None active session context during invoke."""
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        adapter = ContextCapturingAdapter()
        spawner = Spawner(
            config=_make_config(),
            config_dir=config_dir,
            runtime=adapter,
        )

        await spawner.trigger(prompt="hello", trigger_source="test")

        assert adapter.captured_context is not None
        assert adapter.captured_context != "NOT_CALLED"

    async def test_session_context_cleared_after_invocation(self, tmp_path: Path):
        """After trigger returns, the active session context is None."""
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        adapter = ContextCapturingAdapter()
        spawner = Spawner(
            config=_make_config(),
            config_dir=config_dir,
            runtime=adapter,
        )

        await spawner.trigger(prompt="hello", trigger_source="test")

        assert get_active_session_context() is None

    async def test_session_context_cleared_on_error(self, tmp_path: Path):
        """Even when the adapter raises, session context is cleaned up."""
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        adapter = ContextCapturingAdapter(error="adapter exploded")
        spawner = Spawner(
            config=_make_config(),
            config_dir=config_dir,
            runtime=adapter,
        )

        result = await spawner.trigger(prompt="hello", trigger_source="test")

        assert result.success is False
        assert get_active_session_context() is None
        # Adapter did see the context before raising
        assert adapter.captured_context is not None
        assert adapter.captured_context != "NOT_CALLED"
