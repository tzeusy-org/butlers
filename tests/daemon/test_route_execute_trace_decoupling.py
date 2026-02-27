"""Tests for async route dispatch tracing continuity.

Verifies:
1. The background processing task continues the incoming distributed trace.
2. The process span carries request_id as an attribute.
3. The process span links back to the accept-phase span via SpanLink.
4. The accept-phase span carries request_id as an attribute.
5. The accept span still ends before the process span completes.
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

_REQUEST_ID = "018f6f4e-5b3b-7b2d-9c2f-7b7b6b6b6b6b"


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


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
    butler_name: str = "health",
    port: int = 9400,
) -> Path:
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
    (tmp_path / "butler.toml").write_text("\n".join(toml_lines))
    return tmp_path


def _patch_infra(butler_name: str = "health"):
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
    mock_db.db_name = f"butler_{butler_name}"

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
        "validate_module_credentials": patch(
            "butlers.daemon.validate_module_credentials_async",
            new_callable=AsyncMock,
            return_value={},
        ),
        "validate_core_credentials": patch(
            "butlers.daemon.validate_core_credentials_async",
            new_callable=AsyncMock,
        ),
        "sync_schedules": patch("butlers.daemon.sync_schedules", new_callable=AsyncMock),
        "get_adapter": patch("butlers.daemon.get_adapter", return_value=mock_adapter_cls),
        "shutil_which": patch("butlers.daemon.shutil.which", return_value="/usr/bin/claude"),
        "start_mcp_server": patch.object(ButlerDaemon, "_start_mcp_server", new_callable=AsyncMock),
        "connect_switchboard": patch.object(
            ButlerDaemon, "_connect_switchboard", new_callable=AsyncMock
        ),
        "recover_route_inbox": patch.object(
            ButlerDaemon, "_recover_route_inbox", new_callable=AsyncMock
        ),
        "mock_db": mock_db,
        "mock_pool": mock_pool,
        "mock_spawner": mock_spawner,
    }


async def _start_daemon_with_route_execute(butler_dir: Path, patches: dict, *, otel_tracer):
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
        patches["validate_module_credentials"],
        patches["validate_core_credentials"],
        # Patch init_telemetry but return the real in-memory tracer so spans are captured
        patch("butlers.daemon.init_telemetry", return_value=otel_tracer),
        patches["sync_schedules"],
        patch("butlers.daemon.FastMCP", return_value=mock_mcp),
        patch("butlers.daemon.Spawner", return_value=patches["mock_spawner"]),
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
    request_id: str = _REQUEST_ID,
) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "received_at": "2026-02-14T00:00:00Z",
        "source_channel": source_channel,
        "source_endpoint_identity": source_endpoint_identity,
        "source_sender_identity": source_sender_identity,
    }


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


# ---------------------------------------------------------------------------
# Tests: Process span is a fresh root (not a child of switchboard's trace)
# ---------------------------------------------------------------------------


class TestProcessSpanTraceContinuity:
    """The background processing task should continue switchboard trace context."""

    async def test_process_span_shares_switchboard_trace(
        self, tmp_path: Path, otel_provider: InMemorySpanExporter
    ) -> None:
        """route.process span should use the same trace as the switchboard span."""
        patches = _patch_infra("health")
        butler_dir = _make_butler_toml(tmp_path, butler_name="health")
        tracer = trace.get_tracer("butlers")
        daemon, route_execute_fn = await _start_daemon_with_route_execute(
            butler_dir, patches, otel_tracer=tracer
        )
        assert route_execute_fn is not None

        trigger_result = MagicMock()
        trigger_result.session_id = uuid.uuid4()
        daemon.spawner.trigger = AsyncMock(return_value=trigger_result)

        # Call route.execute with a parent span (simulating switchboard)
        parent_tracer = trace.get_tracer("test")
        with parent_tracer.start_as_current_span("switchboard.route") as parent_span:
            parent_trace_id = parent_span.get_span_context().trace_id
            parent_span_id = parent_span.get_span_context().span_id
            trace_context = inject_trace_context()

        result = await route_execute_fn(
            schema_version="route.v1",
            request_context=_route_request_context(),
            input={"prompt": "Run health check."},
            trace_context=trace_context,
        )
        assert result["status"] == "accepted"

        # Let the background task run
        await asyncio.sleep(0.1)
        daemon.spawner.trigger.assert_awaited_once()

        spans = otel_provider.get_finished_spans()
        process_spans = [s for s in spans if s.name == "route.process"]
        assert len(process_spans) == 1, (
            f"Expected 1 route.process span, got: {[s.name for s in spans]}"
        )
        process_span = process_spans[0]

        assert process_span.context.trace_id == parent_trace_id, (
            "route.process should share the switchboard trace_id"
        )
        assert process_span.parent is not None
        assert process_span.parent.span_id == parent_span_id, (
            "route.process should be parented to the upstream switchboard span context"
        )

    async def test_accept_span_ends_before_process_span(
        self, tmp_path: Path, otel_provider: InMemorySpanExporter
    ) -> None:
        """The accept-phase route.execute span ends before the process span completes."""
        patches = _patch_infra("health")
        butler_dir = _make_butler_toml(tmp_path, butler_name="health")
        tracer = trace.get_tracer("butlers")
        daemon, route_execute_fn = await _start_daemon_with_route_execute(
            butler_dir, patches, otel_tracer=tracer
        )
        assert route_execute_fn is not None

        trigger_started = asyncio.Event()
        trigger_allowed = asyncio.Event()

        async def slow_trigger(**kwargs):
            trigger_started.set()
            await trigger_allowed.wait()
            r = MagicMock()
            r.session_id = uuid.uuid4()
            return r

        daemon.spawner.trigger = slow_trigger

        result = await route_execute_fn(
            schema_version="route.v1",
            request_context=_route_request_context(),
            input={"prompt": "Run health check."},
        )
        assert result["status"] == "accepted"

        # At this point, the accept span has ended (route.execute returned).
        # Wait for the background task to start (but not finish).
        await trigger_started.wait()

        # Check that route.execute accept span is already finished
        spans_so_far = otel_provider.get_finished_spans()
        accept_spans = [s for s in spans_so_far if s.name == "butler.tool.route.execute"]
        assert len(accept_spans) == 1, "Accept span should be finished before trigger completes"

        # Process span should NOT be finished yet (trigger is blocked)
        process_spans = [s for s in spans_so_far if s.name == "route.process"]
        # Note: the process span may or may not be recorded yet depending on the
        # SimpleSpanProcessor export timing, but trigger hasn't completed

        # Allow the trigger to finish
        trigger_allowed.set()
        await asyncio.sleep(0.1)

        # Now process span should be done
        all_spans = otel_provider.get_finished_spans()
        process_spans = [s for s in all_spans if s.name == "route.process"]
        assert len(process_spans) == 1

        # Accept span ended_time must be before process span ended_time
        accept_span = accept_spans[0]
        process_span = process_spans[0]
        assert accept_span.end_time < process_span.end_time, (
            "Accept span must end before the process span (true decoupling)"
        )


# ---------------------------------------------------------------------------
# Tests: request_id attribute on both spans
# ---------------------------------------------------------------------------


class TestRequestIdAttribute:
    """Both accept-phase and process spans must carry request_id."""

    async def test_accept_span_has_request_id_attribute(
        self, tmp_path: Path, otel_provider: InMemorySpanExporter
    ) -> None:
        """The accept-phase route.execute span carries request_id as an attribute."""
        patches = _patch_infra("health")
        butler_dir = _make_butler_toml(tmp_path, butler_name="health")
        tracer = trace.get_tracer("butlers")
        daemon, route_execute_fn = await _start_daemon_with_route_execute(
            butler_dir, patches, otel_tracer=tracer
        )
        assert route_execute_fn is not None

        trigger_result = MagicMock()
        trigger_result.session_id = uuid.uuid4()
        daemon.spawner.trigger = AsyncMock(return_value=trigger_result)

        result = await route_execute_fn(
            schema_version="route.v1",
            request_context=_route_request_context(),
            input={"prompt": "Run health check."},
        )
        assert result["status"] == "accepted"
        await asyncio.sleep(0.05)

        spans = otel_provider.get_finished_spans()
        accept_spans = [s for s in spans if s.name == "butler.tool.route.execute"]
        assert len(accept_spans) == 1
        accept_span = accept_spans[0]

        assert "request_id" in accept_span.attributes, (
            "Accept span must have request_id attribute for cross-trace correlation"
        )
        assert accept_span.attributes["request_id"] == _REQUEST_ID

    async def test_process_span_has_request_id_attribute(
        self, tmp_path: Path, otel_provider: InMemorySpanExporter
    ) -> None:
        """The process span carries request_id as an attribute."""
        patches = _patch_infra("health")
        butler_dir = _make_butler_toml(tmp_path, butler_name="health")
        tracer = trace.get_tracer("butlers")
        daemon, route_execute_fn = await _start_daemon_with_route_execute(
            butler_dir, patches, otel_tracer=tracer
        )
        assert route_execute_fn is not None

        trigger_result = MagicMock()
        trigger_result.session_id = uuid.uuid4()
        daemon.spawner.trigger = AsyncMock(return_value=trigger_result)

        result = await route_execute_fn(
            schema_version="route.v1",
            request_context=_route_request_context(),
            input={"prompt": "Run health check."},
        )
        assert result["status"] == "accepted"
        await asyncio.sleep(0.1)

        spans = otel_provider.get_finished_spans()
        process_spans = [s for s in spans if s.name == "route.process"]
        assert len(process_spans) == 1
        process_span = process_spans[0]

        assert "request_id" in process_span.attributes, (
            "Process span must have request_id attribute for cross-trace correlation"
        )
        assert process_span.attributes["request_id"] == _REQUEST_ID


# ---------------------------------------------------------------------------
# Tests: SpanLink from process span to accept span
# ---------------------------------------------------------------------------


class TestSpanLink:
    """The process span must carry a SpanLink back to the accept-phase span."""

    async def test_process_span_has_link_to_accept_span(
        self, tmp_path: Path, otel_provider: InMemorySpanExporter
    ) -> None:
        """route.process carries a SpanLink referencing the accept-phase span's context."""
        patches = _patch_infra("health")
        butler_dir = _make_butler_toml(tmp_path, butler_name="health")
        tracer = trace.get_tracer("butlers")
        daemon, route_execute_fn = await _start_daemon_with_route_execute(
            butler_dir, patches, otel_tracer=tracer
        )
        assert route_execute_fn is not None

        trigger_result = MagicMock()
        trigger_result.session_id = uuid.uuid4()
        daemon.spawner.trigger = AsyncMock(return_value=trigger_result)

        result = await route_execute_fn(
            schema_version="route.v1",
            request_context=_route_request_context(),
            input={"prompt": "Run health check."},
        )
        assert result["status"] == "accepted"
        await asyncio.sleep(0.1)

        spans = otel_provider.get_finished_spans()
        accept_spans = [s for s in spans if s.name == "butler.tool.route.execute"]
        process_spans = [s for s in spans if s.name == "route.process"]

        assert len(accept_spans) == 1
        assert len(process_spans) == 1

        accept_span = accept_spans[0]
        process_span = process_spans[0]

        # Process span must have at least one link
        assert len(process_span.links) >= 1, (
            "route.process must have a SpanLink back to the accept-phase span"
        )

        # The link's context must reference the accept span
        link_ctx = process_span.links[0].context
        assert link_ctx.trace_id == accept_span.context.trace_id, (
            "SpanLink trace_id must match the accept-phase span's trace_id"
        )
        assert link_ctx.span_id == accept_span.context.span_id, (
            "SpanLink span_id must match the accept-phase span's span_id"
        )

    async def test_span_link_carries_request_id_attribute(
        self, tmp_path: Path, otel_provider: InMemorySpanExporter
    ) -> None:
        """The SpanLink on route.process includes request_id in its attributes."""
        patches = _patch_infra("health")
        butler_dir = _make_butler_toml(tmp_path, butler_name="health")
        tracer = trace.get_tracer("butlers")
        daemon, route_execute_fn = await _start_daemon_with_route_execute(
            butler_dir, patches, otel_tracer=tracer
        )
        assert route_execute_fn is not None

        trigger_result = MagicMock()
        trigger_result.session_id = uuid.uuid4()
        daemon.spawner.trigger = AsyncMock(return_value=trigger_result)

        result = await route_execute_fn(
            schema_version="route.v1",
            request_context=_route_request_context(),
            input={"prompt": "Run health check."},
        )
        assert result["status"] == "accepted"
        await asyncio.sleep(0.1)

        spans = otel_provider.get_finished_spans()
        process_spans = [s for s in spans if s.name == "route.process"]
        assert len(process_spans) == 1
        process_span = process_spans[0]

        assert len(process_span.links) >= 1
        link = process_span.links[0]
        assert link.attributes is not None
        assert "request_id" in link.attributes, (
            "SpanLink must carry request_id attribute for trace join lookups"
        )
        assert link.attributes["request_id"] == _REQUEST_ID

    async def test_accept_and_process_share_same_trace(
        self, tmp_path: Path, otel_provider: InMemorySpanExporter
    ) -> None:
        """Accept and process spans should be in one trace and share request_id."""
        patches = _patch_infra("health")
        butler_dir = _make_butler_toml(tmp_path, butler_name="health")
        tracer = trace.get_tracer("butlers")
        daemon, route_execute_fn = await _start_daemon_with_route_execute(
            butler_dir, patches, otel_tracer=tracer
        )
        assert route_execute_fn is not None

        trigger_result = MagicMock()
        trigger_result.session_id = uuid.uuid4()
        daemon.spawner.trigger = AsyncMock(return_value=trigger_result)

        result = await route_execute_fn(
            schema_version="route.v1",
            request_context=_route_request_context(),
            input={"prompt": "Run health check."},
        )
        assert result["status"] == "accepted"
        await asyncio.sleep(0.1)

        spans = otel_provider.get_finished_spans()
        accept_spans = [s for s in spans if s.name == "butler.tool.route.execute"]
        process_spans = [s for s in spans if s.name == "route.process"]

        assert len(accept_spans) == 1
        assert len(process_spans) == 1

        accept_span = accept_spans[0]
        process_span = process_spans[0]

        assert accept_span.context.trace_id == process_span.context.trace_id, (
            "Accept and process spans must belong to the same distributed trace"
        )

        # Both carry the same request_id for correlation and auditing.
        assert accept_span.attributes.get("request_id") == _REQUEST_ID
        assert process_span.attributes.get("request_id") == _REQUEST_ID


# ---------------------------------------------------------------------------
# Tests: Switchboard trace ends with accept span
# ---------------------------------------------------------------------------


class TestSwitchboardTraceIncludesProcess:
    """The switchboard trace should include the async processing phase."""

    async def test_switchboard_trace_includes_process_span(
        self, tmp_path: Path, otel_provider: InMemorySpanExporter
    ) -> None:
        """route.process should be emitted under the switchboard trace_id."""
        patches = _patch_infra("health")
        butler_dir = _make_butler_toml(tmp_path, butler_name="health")
        tracer = trace.get_tracer("butlers")
        daemon, route_execute_fn = await _start_daemon_with_route_execute(
            butler_dir, patches, otel_tracer=tracer
        )
        assert route_execute_fn is not None

        trigger_result = MagicMock()
        trigger_result.session_id = uuid.uuid4()
        daemon.spawner.trigger = AsyncMock(return_value=trigger_result)

        # Call with a parent span (simulating switchboard context)
        parent_tracer = trace.get_tracer("test")
        with parent_tracer.start_as_current_span("switchboard.route") as parent_span:
            switchboard_trace_id = parent_span.get_span_context().trace_id
            trace_context = inject_trace_context()

        result = await route_execute_fn(
            schema_version="route.v1",
            request_context=_route_request_context(),
            input={"prompt": "Run health check."},
            trace_context=trace_context,
        )
        assert result["status"] == "accepted"
        await asyncio.sleep(0.1)

        spans = otel_provider.get_finished_spans()

        # Both accept and process spans should be in the switchboard trace.
        switchboard_spans = [s for s in spans if s.context.trace_id == switchboard_trace_id]
        switchboard_span_names = [s.name for s in switchboard_spans]
        assert "route.process" in switchboard_span_names
        assert "butler.tool.route.execute" in switchboard_span_names, (
            "butler.tool.route.execute (accept span) must be in the switchboard's trace"
        )
