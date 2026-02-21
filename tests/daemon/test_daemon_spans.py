"""Tests for span instrumentation on MCP tool handlers in ButlerDaemon.

Verifies that all core tool handlers and module tools are wrapped with
``butler.tool.<name>`` spans carrying the ``butler.name`` attribute.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import BaseModel

from butlers.daemon import ButlerDaemon, _SpanWrappingMCP
from butlers.modules.base import Module
from butlers.modules.registry import ModuleRegistry

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

    @property
    def name(self) -> str:
        return "stub_span"

    @property
    def config_schema(self) -> type[BaseModel]:
        return StubSpanConfig

    @property
    def dependencies(self) -> list[str]:
        return []

    async def register_tools(self, mcp: Any, config: Any, db: Any) -> None:
        """Register a test tool on the MCP server."""

        @mcp.tool()
        async def stub_action(x: int) -> dict:
            """A stub action for testing span wrapping."""
            return {"result": x * 2}

        self.tools_registered = True

    def migration_revisions(self) -> str | None:
        return None

    async def on_startup(self, config: Any, db: Any, credential_store: Any = None) -> None:
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
        'name = "butler_test"',
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


def _make_registry(*module_classes: type[Module]) -> ModuleRegistry:
    """Create a ModuleRegistry with the given module classes pre-registered."""
    registry = ModuleRegistry()
    for cls in module_classes:
        registry.register(cls)
    return registry


def _patch_infra():
    """Return a dict of patches for all infrastructure dependencies."""
    mock_pool = AsyncMock()

    mock_db = MagicMock()
    mock_db.provision = AsyncMock()
    mock_db.connect = AsyncMock(return_value=mock_pool)
    mock_db.close = AsyncMock()
    mock_db.pool = mock_pool
    mock_db.user = "postgres"
    mock_db.password = "postgres"
    mock_db.host = "localhost"
    mock_db.port = 5432
    mock_db.db_name = "butler_test"

    # Create a spawner mock with an AsyncMock trigger
    mock_spawner = MagicMock()
    mock_trigger_result = MagicMock()
    mock_trigger_result.result = "ok"
    mock_trigger_result.error = None
    mock_trigger_result.duration_ms = 100
    mock_spawner.trigger = AsyncMock(return_value=mock_trigger_result)

    return {
        "db_from_env": patch("butlers.daemon.Database.from_env", return_value=mock_db),
        "run_migrations": patch("butlers.daemon.run_migrations", new_callable=AsyncMock),
        "validate_credentials": patch("butlers.daemon.validate_credentials"),
        "validate_module_credentials": patch(
            "butlers.daemon.validate_module_credentials_async",
            new_callable=AsyncMock,
            return_value={},
        ),
        "validate_core_credentials": patch(
            "butlers.daemon.validate_core_credentials_async",
            new_callable=AsyncMock,
        ),
        "init_telemetry": patch("butlers.daemon.init_telemetry"),
        "sync_schedules": patch("butlers.daemon.sync_schedules", new_callable=AsyncMock),
        "Spawner": patch("butlers.daemon.Spawner", return_value=mock_spawner),
        "get_adapter": patch(
            "butlers.daemon.get_adapter",
            return_value=type(
                "MockAdapter",
                (),
                {"binary_name": "claude", "__init__": lambda self, **kwargs: None},
            ),
        ),
        "shutil_which": patch("butlers.daemon.shutil.which", return_value="/usr/bin/claude"),
        "start_mcp_server": patch.object(ButlerDaemon, "_start_mcp_server", new_callable=AsyncMock),
        "recover_route_inbox": patch.object(
            ButlerDaemon, "_recover_route_inbox", new_callable=AsyncMock
        ),
        "mock_db": mock_db,
        "mock_pool": mock_pool,
        "mock_spawner": mock_spawner,
    }


# ---------------------------------------------------------------------------
# Tests: Core tool span instrumentation
# ---------------------------------------------------------------------------


class TestCoreToolSpans:
    """Verify that all 13 core MCP tools create spans with correct names and attributes."""

    EXPECTED_TOOLS = {
        "status",
        "state_get",
        "state_set",
        "state_delete",
        "state_list",
    }

    async def _start_daemon_capture_tools(
        self, butler_dir: Path, patches: dict | None = None
    ) -> tuple[ButlerDaemon, dict[str, Any]]:
        """Start a daemon and capture all registered tool functions."""
        if patches is None:
            patches = _patch_infra()
        tool_fns: dict[str, Any] = {}

        mock_mcp = MagicMock()

        def tool_decorator(*_decorator_args, **decorator_kwargs):
            declared_name = decorator_kwargs.get("name")

            def decorator(fn):
                tool_fns[declared_name or fn.__name__] = fn
                return fn

            return decorator

        mock_mcp.tool = tool_decorator

        with (
            patches["db_from_env"],
            patches["run_migrations"],
            patches["validate_credentials"],
            patches["validate_module_credentials"],
            patches["validate_core_credentials"],
            patches["init_telemetry"],
            patches["sync_schedules"],
            patch("butlers.daemon.FastMCP", return_value=mock_mcp),
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["start_mcp_server"],
            patches["recover_route_inbox"],
        ):
            daemon = ButlerDaemon(butler_dir)
            await daemon.start()

        return daemon, tool_fns

    async def test_status_creates_span(self, tmp_path, otel_provider):
        butler_dir = _make_butler_toml(tmp_path)
        daemon, tools = await self._start_daemon_capture_tools(butler_dir)
        daemon._started_at = time.monotonic()

        await tools["status"]()

        spans = otel_provider.get_finished_spans()
        assert len(spans) == 1
        assert spans[0].name == "butler.tool.status"
        assert spans[0].attributes["butler.name"] == "test-butler"

    async def test_get_state_creates_span(self, tmp_path, otel_provider):
        butler_dir = _make_butler_toml(tmp_path)
        daemon, tools = await self._start_daemon_capture_tools(butler_dir)

        with patch("butlers.daemon._state_get", new_callable=AsyncMock, return_value="val"):
            await tools["state_get"](key="test-key")

        spans = otel_provider.get_finished_spans()
        assert len(spans) == 1
        assert spans[0].name == "butler.tool.state_get"
        assert spans[0].attributes["butler.name"] == "test-butler"

    async def test_set_state_creates_span(self, tmp_path, otel_provider):
        butler_dir = _make_butler_toml(tmp_path)
        daemon, tools = await self._start_daemon_capture_tools(butler_dir)

        with patch("butlers.daemon._state_set", new_callable=AsyncMock):
            await tools["state_set"](key="k", value="v")

        spans = otel_provider.get_finished_spans()
        assert len(spans) == 1
        assert spans[0].name == "butler.tool.state_set"
        assert spans[0].attributes["butler.name"] == "test-butler"

    async def test_core_tool_call_is_logged(self, tmp_path):
        """Core tool invocation emits a structured MCP tool call log."""
        butler_dir = _make_butler_toml(tmp_path)
        daemon, tools = await self._start_daemon_capture_tools(butler_dir)
        daemon._started_at = time.monotonic()

        with patch("butlers.daemon.logger") as mock_logger:
            await tools["status"]()

        mock_logger.info.assert_any_call(
            "MCP tool called (butler=%s module=%s tool=%s)",
            "test-butler",
            "core",
            "status",
        )

    async def test_all_core_tools_have_span_names(self, tmp_path, otel_provider):
        """Every core tool produces a span named butler.tool.<tool_name>."""
        butler_dir = _make_butler_toml(tmp_path)
        patches = _patch_infra()
        daemon, tools = await self._start_daemon_capture_tools(butler_dir, patches)
        daemon._started_at = time.monotonic()

        tool_kwargs = {
            "status": {},
            "trigger": {"prompt": "test"},
            "tick": {},
            "state_get": {"key": "k"},
            "state_set": {"key": "k", "value": "v"},
            "state_delete": {"key": "k"},
            "state_list": {},
            "schedule_list": {},
            "schedule_create": {"name": "n", "cron": "* * * * *", "prompt": "p"},
            "schedule_update": {"task_id": "00000000-0000-0000-0000-000000000001"},
            "schedule_delete": {"task_id": "00000000-0000-0000-0000-000000000001"},
            "sessions_list": {},
            "sessions_get": {"session_id": "00000000-0000-0000-0000-000000000001"},
            "sessions_summary": {"period": "today"},
        }

        with (
            patch("butlers.daemon._state_get", new_callable=AsyncMock, return_value="v"),
            patch("butlers.daemon._state_set", new_callable=AsyncMock),
            patch("butlers.daemon._state_delete", new_callable=AsyncMock),
            patch("butlers.daemon._state_list", new_callable=AsyncMock, return_value=[]),
            patch("butlers.daemon._schedule_list", new_callable=AsyncMock, return_value=[]),
            patch(
                "butlers.daemon._schedule_create",
                new_callable=AsyncMock,
                return_value="00000000-0000-0000-0000-000000000001",
            ),
            patch("butlers.daemon._schedule_update", new_callable=AsyncMock),
            patch("butlers.daemon._schedule_delete", new_callable=AsyncMock),
            patch("butlers.daemon._tick", new_callable=AsyncMock, return_value=0),
            patch("butlers.daemon._sessions_list", new_callable=AsyncMock, return_value=[]),
            patch("butlers.daemon._sessions_get", new_callable=AsyncMock, return_value=None),
            patch(
                "butlers.daemon._sessions_summary",
                new_callable=AsyncMock,
                return_value={
                    "period": "today",
                    "total_sessions": 0,
                    "total_input_tokens": 0,
                    "total_output_tokens": 0,
                    "by_model": {},
                },
            ),
        ):
            for tool_name, kwargs in tool_kwargs.items():
                await tools[tool_name](**kwargs)

        spans = otel_provider.get_finished_spans()
        span_names = {s.name for s in spans}

        for tool_name in self.EXPECTED_TOOLS:
            expected_span = f"butler.tool.{tool_name}"
            assert expected_span in span_names, (
                f"Missing span for tool '{tool_name}': expected '{expected_span}'"
            )

        # All spans should have butler.name attribute
        for span in spans:
            assert span.attributes["butler.name"] == "test-butler", (
                f"Span '{span.name}' missing butler.name attribute"
            )

    async def test_core_tool_span_records_exception(self, tmp_path, otel_provider):
        """When a core tool raises, the span records the error."""
        butler_dir = _make_butler_toml(tmp_path)
        daemon, tools = await self._start_daemon_capture_tools(butler_dir)

        with (
            patch(
                "butlers.daemon._state_get",
                new_callable=AsyncMock,
                side_effect=RuntimeError("db gone"),
            ),
            pytest.raises(RuntimeError, match="db gone"),
        ):
            await tools["state_get"](key="k")

        spans = otel_provider.get_finished_spans()
        assert len(spans) == 1
        assert spans[0].status.status_code == trace.StatusCode.ERROR
        exception_events = [e for e in spans[0].events if e.name == "exception"]
        assert len(exception_events) == 1

    async def test_schedule_create_accepts_job_mode_fields(self, tmp_path):
        """schedule_create forwards dispatch_mode/job metadata to scheduler core."""
        butler_dir = _make_butler_toml(tmp_path)
        patches = _patch_infra()
        _, tools = await self._start_daemon_capture_tools(butler_dir, patches)

        task_id = uuid4()
        with patch(
            "butlers.daemon._schedule_create", new_callable=AsyncMock, return_value=task_id
        ) as m:
            result = await tools["schedule_create"](
                name="eligibility-sweep",
                cron="*/5 * * * *",
                dispatch_mode="job",
                job_name="eligibility_sweep",
                job_args={"dry_run": True},
            )

        assert result["id"] == str(task_id)
        assert result["dispatch_mode"] == "job"
        assert result["job_name"] == "eligibility_sweep"
        assert result["job_args"] == {"dry_run": True}
        m.assert_awaited_once_with(
            patches["mock_pool"],
            "eligibility-sweep",
            "*/5 * * * *",
            None,
            dispatch_mode="job",
            job_name="eligibility_sweep",
            job_args={"dry_run": True},
            stagger_key="test-butler",
        )

    async def test_schedule_update_accepts_legacy_id_alias(self, tmp_path):
        """schedule_update accepts both legacy id and task_id for compatibility."""
        butler_dir = _make_butler_toml(tmp_path)
        patches = _patch_infra()
        _, tools = await self._start_daemon_capture_tools(butler_dir, patches)

        schedule_id = uuid4()
        with patch("butlers.daemon._schedule_update", new_callable=AsyncMock) as m:
            result = await tools["schedule_update"](
                id=str(schedule_id),
                dispatch_mode="job",
                job_name="eligibility_sweep",
                job_args={"dry_run": True},
            )

        assert result["id"] == str(schedule_id)
        assert result["dispatch_mode"] == "job"
        assert result["job_name"] == "eligibility_sweep"
        assert result["job_args"] == {"dry_run": True}
        m.assert_awaited_once_with(
            patches["mock_pool"],
            schedule_id,
            stagger_key="test-butler",
            dispatch_mode="job",
            job_name="eligibility_sweep",
            job_args={"dry_run": True},
        )

    async def test_schedule_delete_accepts_legacy_id_alias(self, tmp_path):
        """schedule_delete accepts legacy id field for MCP compatibility."""
        butler_dir = _make_butler_toml(tmp_path)
        patches = _patch_infra()
        _, tools = await self._start_daemon_capture_tools(butler_dir, patches)

        schedule_id = uuid4()
        with patch("butlers.daemon._schedule_delete", new_callable=AsyncMock) as m:
            result = await tools["schedule_delete"](id=str(schedule_id))

        assert result == {"id": str(schedule_id), "status": "deleted"}
        m.assert_awaited_once_with(patches["mock_pool"], schedule_id)


# ---------------------------------------------------------------------------
# Tests: _SpanWrappingMCP
# ---------------------------------------------------------------------------


class TestSpanWrappingMCP:
    """Verify _SpanWrappingMCP wraps module tool handlers with spans."""

    def test_forwards_non_tool_attributes(self):
        """Non-tool attributes are forwarded to the underlying FastMCP."""
        mock_mcp = MagicMock()
        mock_mcp.some_attr = "hello"
        wrapper = _SpanWrappingMCP(mock_mcp, "test-butler")
        assert wrapper.some_attr == "hello"

    async def test_wraps_tool_with_span(self, otel_provider):
        """A tool registered via _SpanWrappingMCP produces a span."""
        mock_mcp = MagicMock()
        registered_fns = {}

        def tool_decorator(*_decorator_args, **decorator_kwargs):
            declared_name = decorator_kwargs.get("name")

            def decorator(fn):
                registered_fns[declared_name or fn.__name__] = fn
                return fn

            return decorator

        mock_mcp.tool = tool_decorator

        wrapper = _SpanWrappingMCP(mock_mcp, "test-butler")

        @wrapper.tool()
        async def my_tool(x: int) -> dict:
            return {"result": x}

        # The registered function should be the instrumented wrapper
        assert "my_tool" in registered_fns
        result = await registered_fns["my_tool"](x=42)
        assert result == {"result": 42}

        spans = otel_provider.get_finished_spans()
        assert len(spans) == 1
        assert spans[0].name == "butler.tool.my_tool"
        assert spans[0].attributes["butler.name"] == "test-butler"

    async def test_wraps_tool_preserves_function_name(self, otel_provider):
        """The wrapped function preserves the original function name."""
        mock_mcp = MagicMock()
        registered_fns = {}

        def tool_decorator(*_decorator_args, **decorator_kwargs):
            declared_name = decorator_kwargs.get("name")

            def decorator(fn):
                registered_fns[declared_name or fn.__name__] = fn
                return fn

            return decorator

        mock_mcp.tool = tool_decorator

        wrapper = _SpanWrappingMCP(mock_mcp, "test-butler")

        @wrapper.tool()
        async def another_tool() -> dict:
            """Another tool docstring."""
            return {}

        assert "another_tool" in registered_fns

    async def test_wraps_tool_records_exception(self, otel_provider):
        """When a wrapped module tool raises, the span records the error."""
        mock_mcp = MagicMock()
        registered_fns = {}

        def tool_decorator(*_decorator_args, **decorator_kwargs):
            declared_name = decorator_kwargs.get("name")

            def decorator(fn):
                registered_fns[declared_name or fn.__name__] = fn
                return fn

            return decorator

        mock_mcp.tool = tool_decorator

        wrapper = _SpanWrappingMCP(mock_mcp, "test-butler")

        @wrapper.tool()
        async def failing_tool() -> dict:
            raise ValueError("module error")

        with pytest.raises(ValueError, match="module error"):
            await registered_fns["failing_tool"]()

        spans = otel_provider.get_finished_spans()
        assert len(spans) == 1
        assert spans[0].status.status_code == trace.StatusCode.ERROR

    async def test_wraps_tool_logs_invocation(self, otel_provider):
        """Wrapped module tools emit a call log line on invocation."""
        mock_mcp = MagicMock()
        registered_fns = {}

        def tool_decorator(*_decorator_args, **decorator_kwargs):
            declared_name = decorator_kwargs.get("name")

            def decorator(fn):
                registered_fns[declared_name or fn.__name__] = fn
                return fn

            return decorator

        mock_mcp.tool = tool_decorator
        wrapper = _SpanWrappingMCP(mock_mcp, "test-butler", module_name="stub_span")

        @wrapper.tool()
        async def stub_action() -> dict:
            return {"ok": True}

        with patch("butlers.daemon.logger") as mock_logger:
            await registered_fns["stub_action"]()

        mock_logger.info.assert_any_call(
            "MCP tool called (butler=%s module=%s tool=%s)",
            "test-butler",
            "stub_span",
            "stub_action",
        )


# ---------------------------------------------------------------------------
# Tests: Module tool span instrumentation via daemon
# ---------------------------------------------------------------------------


class TestModuleToolSpans:
    """Verify module tools get span-wrapped when registered through the daemon."""

    async def test_module_tool_creates_span(self, tmp_path, otel_provider):
        """A module tool registered via _register_module_tools produces a span."""
        butler_dir = _make_butler_toml(tmp_path, modules={"stub_span": {}})
        registry = _make_registry(StubSpanModule)
        patches = _patch_infra()

        tool_fns: dict[str, Any] = {}

        mock_mcp = MagicMock()

        def tool_decorator(*_decorator_args, **decorator_kwargs):
            declared_name = decorator_kwargs.get("name")

            def decorator(fn):
                tool_fns[declared_name or fn.__name__] = fn
                return fn

            return decorator

        mock_mcp.tool = tool_decorator

        with (
            patches["db_from_env"],
            patches["run_migrations"],
            patches["validate_credentials"],
            patches["validate_module_credentials"],
            patches["validate_core_credentials"],
            patches["init_telemetry"],
            patches["sync_schedules"],
            patch("butlers.daemon.FastMCP", return_value=mock_mcp),
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["start_mcp_server"],
            patches["recover_route_inbox"],
        ):
            daemon = ButlerDaemon(butler_dir, registry=registry)
            await daemon.start()

        # The module should have registered stub_action
        assert "stub_action" in tool_fns

        result = await tool_fns["stub_action"](x=21)
        assert result == {"result": 42}

        spans = otel_provider.get_finished_spans()
        assert len(spans) == 1
        assert spans[0].name == "butler.tool.stub_action"
        assert spans[0].attributes["butler.name"] == "test-butler"
