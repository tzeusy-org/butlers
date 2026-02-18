"""Test trace propagation through route.execute to spawner.

Verifies that:
- Trace context from route.execute is propagated to spawner
- Butler sessions spawned via routing share the same trace_id as parent
- Sessions triggered without trace context create independent traces
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from butlers.core.telemetry import inject_trace_context
from butlers.daemon import ButlerDaemon

pytestmark = pytest.mark.unit


def _reset_otel_global_state():
    """Fully reset the OpenTelemetry global tracer provider state."""
    trace._TRACER_PROVIDER_SET_ONCE = trace.Once()
    trace._TRACER_PROVIDER = None


@pytest.fixture
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


def _toml_value(v: Any) -> str:
    if isinstance(v, str):
        return f'"{v}"'
    if isinstance(v, list):
        items = ", ".join(f'"{i}"' if isinstance(i, str) else str(i) for i in v)
        return f"[{items}]"
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def _make_butler_toml(
    tmp_path: Path,
    *,
    butler_name: str = "test-butler",
    port: int = 9100,
    modules: dict[str, dict] | None = None,
) -> Path:
    modules = modules or {}
    toml_lines = [
        "[butler]",
        f'name = "{butler_name}"',
        f"port = {port}",
        'description = "A test butler"',
        "",
        "[butler.db]",
        f'name = "butler_{butler_name}"',
        "",
        "[[butler.schedule]]",
        'name = "daily-check"',
        'cron = "0 9 * * *"',
        'prompt = "Do the daily check"',
    ]
    for mod_name, mod_cfg in modules.items():
        toml_lines.append(f"\n[modules.{mod_name}]")
        for k, v in mod_cfg.items():
            toml_lines.append(f"{k} = {_toml_value(v)}")
    (tmp_path / "butler.toml").write_text("\n".join(toml_lines))
    return tmp_path


def _patch_infra():
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

    mock_spawner = MagicMock()
    mock_spawner.stop_accepting = MagicMock()
    mock_spawner.drain = AsyncMock()

    mock_adapter = MagicMock()
    mock_adapter.binary_name = "claude"
    mock_adapter_cls = MagicMock(return_value=mock_adapter)

    return {
        "db_from_env": patch("butlers.daemon.Database.from_env", return_value=mock_db),
        "run_migrations": patch("butlers.daemon.run_migrations", new_callable=AsyncMock),
        "validate_credentials": patch("butlers.daemon.validate_credentials"),
        "sync_schedules": patch("butlers.daemon.sync_schedules", new_callable=AsyncMock),
        "FastMCP": patch("butlers.daemon.FastMCP"),
        "Spawner": patch("butlers.daemon.Spawner", return_value=mock_spawner),
        "start_mcp_server": patch.object(ButlerDaemon, "_start_mcp_server", new_callable=AsyncMock),
        "connect_switchboard": patch.object(
            ButlerDaemon, "_connect_switchboard", new_callable=AsyncMock
        ),
        "recover_route_inbox": patch.object(
            ButlerDaemon, "_recover_route_inbox", new_callable=AsyncMock
        ),
        "get_adapter": patch("butlers.daemon.get_adapter", return_value=mock_adapter_cls),
        "shutil_which": patch("butlers.daemon.shutil.which", return_value="/usr/bin/claude"),
        "mock_db": mock_db,
        "mock_pool": mock_pool,
        "mock_spawner": mock_spawner,
    }


async def _start_daemon_with_route_execute(butler_dir: Path, patches: dict):
    """Boot a daemon and capture the route.execute handler function."""
    route_execute_fn = None
    mock_mcp = MagicMock()

    def tool_decorator(*_decorator_args, **decorator_kwargs):
        declared_name = decorator_kwargs.get("name")

        def decorator(fn):
            nonlocal route_execute_fn
            resolved_name = declared_name or fn.__name__
            if resolved_name == "route.execute":
                route_execute_fn = fn
            return fn

        return decorator

    mock_mcp.tool = tool_decorator

    with (
        patches["db_from_env"],
        patches["run_migrations"],
        patches["validate_credentials"],
        patch("butlers.daemon.init_telemetry", return_value=trace.get_tracer("butlers")),
        patches["sync_schedules"],
        patch("butlers.daemon.FastMCP", return_value=mock_mcp),
        patches["Spawner"],
        patches["get_adapter"],
        patches["shutil_which"],
        patches["start_mcp_server"],
        patches["connect_switchboard"],
        patches["recover_route_inbox"],
    ):
        daemon = ButlerDaemon(butler_dir)
        await daemon.start()

    return daemon, route_execute_fn


def _route_request_context(
    *,
    source_endpoint_identity: str = "switchboard",
    source_sender_identity: str = "health",
    source_channel: str = "telegram",
    source_thread_identity: str | None = "12345",
    request_id: str = "018f6f4e-5b3b-7b2d-9c2f-7b7b6b6b6b6b",
) -> dict[str, Any]:
    ctx: dict[str, Any] = {
        "request_id": request_id,
        "received_at": "2026-02-14T00:00:00Z",
        "source_channel": source_channel,
        "source_endpoint_identity": source_endpoint_identity,
        "source_sender_identity": source_sender_identity,
    }
    if source_thread_identity is not None:
        ctx["source_thread_identity"] = source_thread_identity
    return ctx


@pytest.fixture(autouse=True)
def _mock_route_inbox(monkeypatch):
    """Patch route_inbox DB calls so tests don't need a real DB pool."""
    fake_inbox_id = uuid.uuid4()
    monkeypatch.setattr(
        "butlers.daemon.route_inbox_insert",
        AsyncMock(return_value=fake_inbox_id),
    )
    monkeypatch.setattr(
        "butlers.daemon.route_inbox_mark_processing",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "butlers.daemon.route_inbox_mark_processed",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "butlers.daemon.route_inbox_mark_errored",
        AsyncMock(),
    )


class TestRouteExecuteTracePropagation:
    """Verify trace context propagates through route.execute to spawner."""

    async def test_trace_context_propagated_to_spawner(
        self, tmp_path: Path, otel_provider: InMemorySpanExporter
    ) -> None:
        """route.execute span is a child of the switchboard's trace."""
        patches = _patch_infra()
        butler_dir = _make_butler_toml(tmp_path, butler_name="health")
        daemon, route_execute_fn = await _start_daemon_with_route_execute(butler_dir, patches)
        assert route_execute_fn is not None

        mock_trigger_result = MagicMock()
        mock_trigger_result.output = "ok"
        mock_trigger_result.success = True
        mock_trigger_result.error = None
        mock_trigger_result.duration_ms = 10
        daemon.spawner.trigger = AsyncMock(return_value=mock_trigger_result)

        # Create a parent span and inject trace context
        tracer = trace.get_tracer("test")
        with tracer.start_as_current_span("switchboard.route") as parent_span:
            parent_trace_id = parent_span.get_span_context().trace_id
            parent_span_id = parent_span.get_span_context().span_id
            trace_context = inject_trace_context()

        # Call route.execute with trace_context
        result = await route_execute_fn(
            schema_version="route.v1",
            request_context=_route_request_context(),
            input={"prompt": "Run health check."},
            trace_context=trace_context,
        )

        assert result["status"] == "accepted"

        # Background task runs asynchronously; wait briefly for it to complete
        await asyncio.sleep(0.05)
        daemon.spawner.trigger.assert_awaited_once()

        # Verify route.execute created a span under the switchboard's trace
        spans = otel_provider.get_finished_spans()
        route_spans = [s for s in spans if s.name == "butler.tool.route.execute"]
        assert len(route_spans) == 1
        route_span = route_spans[0]
        assert route_span.context.trace_id == parent_trace_id
        assert route_span.parent.span_id == parent_span_id

    async def test_no_trace_context_creates_independent_trace(
        self, tmp_path: Path, otel_provider: InMemorySpanExporter
    ) -> None:
        """Sessions without trace_context create independent traces."""
        patches = _patch_infra()
        butler_dir = _make_butler_toml(tmp_path, butler_name="health")
        daemon, route_execute_fn = await _start_daemon_with_route_execute(butler_dir, patches)
        assert route_execute_fn is not None

        mock_trigger_result = MagicMock()
        mock_trigger_result.output = "ok"
        mock_trigger_result.success = True
        mock_trigger_result.error = None
        mock_trigger_result.duration_ms = 10
        daemon.spawner.trigger = AsyncMock(return_value=mock_trigger_result)

        # Call route.execute without trace_context
        result = await route_execute_fn(
            schema_version="route.v1",
            request_context=_route_request_context(),
            input={"prompt": "Run health check."},
        )

        assert result["status"] == "accepted"

        # Background task runs asynchronously; wait briefly for it to complete
        await asyncio.sleep(0.05)
        daemon.spawner.trigger.assert_awaited_once()

        # Verify parent_context is None
        call_args = daemon.spawner.trigger.call_args
        assert call_args is not None
        parent_ctx_arg = call_args.kwargs.get("parent_context")
        assert parent_ctx_arg is None
