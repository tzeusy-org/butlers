"""Tests for span instrumentation on MCP tool handlers in ButlerDaemon.

Verifies that core tool handlers and module tools are wrapped with
``butler.tool.<name>`` spans carrying the ``butler.name`` attribute.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import BaseModel

from butlers.daemon import _SpanWrappingMCP
from butlers.modules.base import Module
from butlers.testing.fake_pool import fake_pool
from butlers.testing.tool_host import host_tools

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# OpenTelemetry test fixtures
# ---------------------------------------------------------------------------


def _reset_otel_global_state():
    """Fully reset the OpenTelemetry global tracer provider state."""
    trace._TRACER_PROVIDER_SET_ONCE = trace.Once()
    trace._TRACER_PROVIDER = None


@pytest.fixture(autouse=True)
def otel_provider():
    """Set up an in-memory TracerProvider for every test, then tear down."""
    _reset_otel_global_state()
    exporter = InMemorySpanExporter()
    resource = Resource.create({"service.name": "butler-test"})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    yield exporter
    provider.shutdown()
    _reset_otel_global_state()


# ---------------------------------------------------------------------------
# Stub module for testing module tool span wrapping
# ---------------------------------------------------------------------------


class StubSpanConfig(BaseModel):
    """Config schema for StubSpanModule."""


class StubSpanModule(Module):
    """Stub module that registers tools for span testing."""

    def __init__(self) -> None:
        self.started = False
        self.shutdown_called = False
        self.tools_registered = False

    name = "stub_span"

    @property
    def config_schema(self) -> type[BaseModel]:
        return StubSpanConfig

    @property
    def dependencies(self) -> list[str]:
        return []

    async def register_tools(self, mcp: Any, config: Any, db: Any, butler_name: str) -> None:
        @mcp.tool()
        async def stub_action(x: int) -> dict:
            """A stub action for testing span wrapping."""
            return {"result": x * 2}

        self.tools_registered = True

    def migration_revisions(self) -> str | None:
        return None

    async def on_startup(
        self, config: Any, db: Any, credential_store: Any = None, blob_store: Any = None
    ) -> None:
        self.started = True

    async def on_shutdown(self) -> None:
        self.shutdown_called = True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_butler_toml(tmp_path: Path, modules: dict | None = None) -> Path:
    """Write a minimal butler.toml and return the directory."""
    modules = modules or {}
    toml_lines = [
        "[butler]",
        'name = "test-butler"',
        "port = 9100",
        'description = "A test butler"',
        "",
        "[butler.db]",
        'name = "butlers"',
        'schema = "test_butler"',
        "",
        "[[butler.schedule]]",
        'name = "daily-check"',
        'cron = "0 9 * * *"',
        'prompt = "Do the daily check"',
    ]
    for mod_name, mod_cfg in modules.items():
        toml_lines.append(f"\n[modules.{mod_name}]")
        for k, v in mod_cfg.items():
            if isinstance(v, str):
                toml_lines.append(f'{k} = "{v}"')
            else:
                toml_lines.append(f"{k} = {v}")
    (tmp_path / "butler.toml").write_text("\n".join(toml_lines))
    return tmp_path


# ---------------------------------------------------------------------------
# Tests: _SpanWrappingMCP
# ---------------------------------------------------------------------------


class TestSpanWrappingMCP:
    """Verify _SpanWrappingMCP wraps module tool handlers with spans."""

    @staticmethod
    def _make_wrapper(
        butler_name: str = "test-butler", module_name: str | None = None
    ) -> tuple[_SpanWrappingMCP, dict]:
        """Return a wrapper backed by a mock MCP and a dict of registered tool fns."""
        mock_mcp = MagicMock()
        registered_fns: dict[str, Any] = {}

        def tool_decorator(*_decorator_args, **decorator_kwargs):
            declared_name = decorator_kwargs.get("name")

            def decorator(fn):
                registered_fns[declared_name or fn.__name__] = fn
                return fn

            return decorator

        mock_mcp.tool = tool_decorator
        kwargs = {"module_name": module_name} if module_name else {}
        return _SpanWrappingMCP(mock_mcp, butler_name, **kwargs), registered_fns

    async def test_span_wrapping_and_error_recording(self, otel_provider):
        """Wrapped tool produces span named butler.tool.<name> with butler.name attribute;
        when tool raises, span records error status."""
        wrapper, fns = self._make_wrapper()

        @wrapper.tool()
        async def my_tool(x: int) -> dict:
            return {"result": x}

        result = await fns["my_tool"](x=42)
        assert result == {"result": 42}
        spans = otel_provider.get_finished_spans()
        assert len(spans) == 1
        assert spans[0].name == "butler.tool.my_tool"
        assert spans[0].attributes["butler.name"] == "test-butler"

        # Exception recorded as ERROR status
        wrapper2, fns2 = self._make_wrapper()

        @wrapper2.tool()
        async def failing_tool() -> dict:
            raise ValueError("module error")

        with pytest.raises(ValueError, match="module error"):
            await fns2["failing_tool"]()
        error_spans = [
            s for s in otel_provider.get_finished_spans() if s.name == "butler.tool.failing_tool"
        ]
        assert len(error_spans) == 1
        assert error_spans[0].status.status_code == trace.StatusCode.ERROR

    async def test_non_tool_attributes_forwarded(self):
        """Non-tool attributes are forwarded to the underlying FastMCP."""
        mock_mcp = MagicMock()
        mock_mcp.some_attr = "hello"
        wrapper = _SpanWrappingMCP(mock_mcp, "test-butler")
        assert wrapper.some_attr == "hello"

    async def test_module_tool_creates_span_via_daemon(self, tmp_path, otel_provider):
        """A module tool the daemon registers produces a span with the right name.

        The subject is the daemon's module-tool wrapping, not the boot: the
        daemon's own ``_register_module_tools`` runs on a DB-free host (bu-rvgzmf.2).
        """
        butler_dir = _make_butler_toml(tmp_path, modules={"stub_span": {}})
        host = await host_tools(butler_dir, pool=fake_pool())
        module = StubSpanModule()
        host.daemon._modules = [module]
        await host.daemon._register_module_tools()

        assert module.tools_registered
        result = await host.call("stub_action", x=21)
        assert result == {"result": 42}

        spans = otel_provider.get_finished_spans()
        assert len(spans) == 1
        assert spans[0].name == "butler.tool.stub_action"
        assert spans[0].attributes["butler.name"] == "test-butler"
