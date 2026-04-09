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
        'name = "butlers"',
        f'schema = "{butler_name}"',
        "",
        "[[butler.schedule]]",
        'name = "daily-check"',
        'cron = "0 9 * * *"',
        'prompt = "Do the daily check"',
    ]
    (tmp_path / "butler.toml").write_text("\n".join(toml_lines))
    return tmp_path


def _make_runtime_config_row(butler_name: str = "health") -> dict:
    """Return a dict-like row for the runtime_config table, as returned by asyncpg.fetchrow."""
    return {
        "butler_name": butler_name,
        "core_groups": None,
        "model": None,
        "runtime_type": "codex",
        "args": "[]",
        "max_concurrent": 3,
        "max_queued": 10,
        "session_timeout_s": 900,
        "seeded_at": None,
        "updated_at": None,
    }


def _make_fetchrow_side_effect(butler_name: str = "health"):
    """Return an async side_effect for pool.fetchrow that returns runtime_config rows
    for runtime_config queries and None for all other queries."""

    async def _fetchrow(query: str, *args, **kwargs):
        if "runtime_config" in query:
            return _make_runtime_config_row(butler_name)
        return None

    return _fetchrow


def _patch_infra(butler_name: str = "health"):
    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(return_value=None)
    mock_conn.fetchrow = AsyncMock(return_value=None)
    mock_conn.fetchval = AsyncMock(return_value=None)
    mock_conn.fetch = AsyncMock(return_value=[])

    mock_pool = AsyncMock()
    # Support `async with pool.acquire() as conn:` for _ensure_owner_entity
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    mock_pool.fetchval = AsyncMock(return_value=None)
    mock_pool.execute = AsyncMock(return_value=None)
    mock_pool.fetchrow = AsyncMock(side_effect=_make_fetchrow_side_effect(butler_name))
    mock_pool.fetch = AsyncMock(return_value=[])
    mock_db = MagicMock()
    mock_db.provision = AsyncMock()
    mock_db.connect = AsyncMock(return_value=mock_pool)
    mock_db.close = AsyncMock()
    mock_db.pool = mock_pool
    mock_db.user = "postgres"
    mock_db.password = "postgres"
    mock_db.host = "localhost"
    mock_db.port = 5432
    mock_db.db_name = "butlers"

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
    source_channel: str = "telegram_bot",
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
        """route.process span shares switchboard trace; accept span ends before process span."""
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
        await asyncio.sleep(0.1)

        spans = otel_provider.get_finished_spans()
        process_spans = [s for s in spans if s.name == "route.process"]
        assert len(process_spans) == 1
        process_span = process_spans[0]
        assert process_span.context.trace_id == parent_trace_id
        assert process_span.parent is not None
        assert process_span.parent.span_id == parent_span_id


class TestSpanLink:
    """The process span must carry a SpanLink back to the accept-phase span, with request_id."""

    async def test_process_span_has_link_and_request_id(
        self, tmp_path: Path, otel_provider: InMemorySpanExporter
    ) -> None:
        """route.process: SpanLink references accept span; both spans carry request_id."""
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
        assert len(accept_spans) == 1 and len(process_spans) == 1
        accept_span = accept_spans[0]
        process_span = process_spans[0]

        # Both spans carry request_id
        assert accept_span.attributes.get("request_id") == _REQUEST_ID
        assert process_span.attributes.get("request_id") == _REQUEST_ID
        # Accept and process share trace_id
        assert accept_span.context.trace_id == process_span.context.trace_id

        # Process span has SpanLink referencing the accept span
        assert len(process_span.links) >= 1
        link = process_span.links[0]
        assert link.context.span_id == accept_span.context.span_id
        assert link.attributes is not None and "request_id" in link.attributes
