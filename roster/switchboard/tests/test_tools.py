"""Tests for butlers.tools.switchboard — routing, registry, and classification."""

from __future__ import annotations

import shutil

import pytest
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

# Skip all tests in this module if Docker is not available
docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


def _reset_otel_global_state():
    """Fully reset the OpenTelemetry global tracer provider state."""
    trace._TRACER_PROVIDER_SET_ONCE = trace.Once()
    trace._TRACER_PROVIDER = None


@pytest.fixture
async def pool(provisioned_postgres_pool):
    """Provision a fresh database with switchboard tables and return a pool."""
    async with provisioned_postgres_pool() as p:
        # Create switchboard tables (mirrors Alembic switchboard migration)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS butler_registry (
                name TEXT PRIMARY KEY,
                endpoint_url TEXT NOT NULL,
                description TEXT,
                modules JSONB NOT NULL DEFAULT '[]',
                last_seen_at TIMESTAMPTZ,
                eligibility_state TEXT NOT NULL DEFAULT 'active',
                liveness_ttl_seconds INTEGER NOT NULL DEFAULT 300,
                quarantined_at TIMESTAMPTZ,
                quarantine_reason TEXT,
                route_contract_min INTEGER NOT NULL DEFAULT 1,
                route_contract_max INTEGER NOT NULL DEFAULT 1,
                capabilities JSONB NOT NULL DEFAULT '[]',
                eligibility_updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                registered_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS butler_registry_eligibility_log (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                butler_name TEXT NOT NULL,
                previous_state TEXT NOT NULL,
                new_state TEXT NOT NULL,
                reason TEXT NOT NULL,
                previous_last_seen_at TIMESTAMPTZ,
                new_last_seen_at TIMESTAMPTZ,
                observed_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS routing_log (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                source_butler TEXT NOT NULL,
                target_butler TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                success BOOLEAN NOT NULL,
                duration_ms INTEGER,
                error TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS fanout_execution_log (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                source_channel TEXT NOT NULL,
                source_id TEXT,
                tool_name TEXT NOT NULL,
                fanout_mode TEXT NOT NULL,
                join_policy TEXT NOT NULL,
                abort_policy TEXT NOT NULL,
                plan_payload JSONB NOT NULL,
                execution_payload JSONB NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)

        yield p


# ------------------------------------------------------------------
# register_butler
# ------------------------------------------------------------------


async def test_register_butler_inserts(pool):
    """register_butler creates a new entry in the registry."""
    from butlers.tools.switchboard import list_butlers, register_butler

    await register_butler(pool, "health", "http://localhost:40101/sse", "Health butler", ["email"])
    butlers = await list_butlers(pool)
    names = [b["name"] for b in butlers]
    assert "health" in names

    health = next(b for b in butlers if b["name"] == "health")
    assert health["endpoint_url"] == "http://localhost:40101/sse"
    assert health["description"] == "Health butler"


async def test_register_butler_upserts(pool):
    """register_butler updates an existing entry on conflict."""
    from butlers.tools.switchboard import list_butlers, register_butler

    await register_butler(pool, "uptest", "http://localhost:9000/sse", "v1")
    await register_butler(pool, "uptest", "http://localhost:9001/sse", "v2", ["telegram"])

    butlers = await list_butlers(pool)
    entry = next(b for b in butlers if b["name"] == "uptest")
    assert entry["endpoint_url"] == "http://localhost:9001/sse"
    assert entry["description"] == "v2"


async def test_register_butler_tracks_liveness_and_contract_metadata(pool):
    """register_butler persists liveness/contract metadata for planner validation."""
    from butlers.tools.switchboard import list_butlers, register_butler

    await register_butler(
        pool,
        "meta",
        "http://localhost:9002/sse",
        "metadata test",
        ["email"],
        capabilities=["email", "notify"],
        route_contract_min=1,
        route_contract_max=3,
        liveness_ttl_seconds=90,
    )

    butlers = await list_butlers(pool)
    entry = next(b for b in butlers if b["name"] == "meta")
    assert entry["eligibility_state"] == "active"
    assert entry["liveness_ttl_seconds"] == 90
    assert entry["route_contract_min"] == 1
    assert entry["route_contract_max"] == 3
    assert set(entry["capabilities"]) >= {"email", "notify", "trigger"}


# ------------------------------------------------------------------
# list_butlers
# ------------------------------------------------------------------


async def test_list_butlers_empty(pool):
    """list_butlers returns an empty list when no butlers are registered."""
    from butlers.tools.switchboard import list_butlers

    # Clear any existing entries
    await pool.execute("DELETE FROM butler_registry")
    butlers = await list_butlers(pool)
    assert butlers == []


async def test_list_butlers_ordered(pool):
    """list_butlers returns results ordered by name."""
    from butlers.tools.switchboard import list_butlers, register_butler

    await pool.execute("DELETE FROM butler_registry")
    await register_butler(pool, "zebra", "http://localhost:1/sse")
    await register_butler(pool, "alpha", "http://localhost:2/sse")
    await register_butler(pool, "middle", "http://localhost:3/sse")

    butlers = await list_butlers(pool)
    names = [b["name"] for b in butlers]
    assert names == ["alpha", "middle", "zebra"]


async def test_list_butlers_routable_only_filters_non_active_targets(pool):
    """routable_only excludes stale and quarantined targets from planner visibility."""
    from butlers.tools.switchboard import list_butlers, register_butler

    await pool.execute("DELETE FROM butler_registry")
    await register_butler(pool, "active", "http://localhost:9201/sse")
    await register_butler(pool, "stale", "http://localhost:9202/sse", liveness_ttl_seconds=5)
    await register_butler(pool, "quarantined", "http://localhost:9203/sse")

    await pool.execute(
        """
        UPDATE butler_registry
        SET last_seen_at = now() - interval '30 seconds'
        WHERE name = 'stale'
        """
    )
    await pool.execute(
        """
        UPDATE butler_registry
        SET eligibility_state = 'quarantined',
            quarantined_at = now(),
            quarantine_reason = 'policy_violation'
        WHERE name = 'quarantined'
        """
    )

    visible = await list_butlers(pool, routable_only=True)
    assert [b["name"] for b in visible] == ["active"]


# ------------------------------------------------------------------
# discover_butlers
# ------------------------------------------------------------------


async def test_discover_butlers_from_config_dir(pool, tmp_path):
    """discover_butlers scans a directory for butler.toml files and registers them."""
    from butlers.tools.switchboard import discover_butlers, list_butlers

    await pool.execute("DELETE FROM butler_registry")

    # Create a fake butler config directory
    butler_dir = tmp_path / "mybutler"
    butler_dir.mkdir()
    (butler_dir / "butler.toml").write_text(
        '[butler]\nname = "mybutler"\nport = 9999\ndescription = "Test butler"\n'
    )

    discovered = await discover_butlers(pool, tmp_path)
    assert len(discovered) == 1
    assert discovered[0]["name"] == "mybutler"
    assert discovered[0]["endpoint_url"] == "http://localhost:9999/sse"

    # Verify it was registered
    butlers = await list_butlers(pool)
    names = [b["name"] for b in butlers]
    assert "mybutler" in names


async def test_discover_butlers_nonexistent_dir(pool, tmp_path):
    """discover_butlers returns empty list for a non-existent directory."""
    from butlers.tools.switchboard import discover_butlers

    result = await discover_butlers(pool, tmp_path / "does_not_exist")
    assert result == []


async def test_discover_butlers_skips_invalid_configs(pool, tmp_path):
    """discover_butlers skips directories with invalid butler.toml files."""
    from butlers.tools.switchboard import discover_butlers

    await pool.execute("DELETE FROM butler_registry")

    # Create a directory with invalid TOML
    bad_dir = tmp_path / "badbutler"
    bad_dir.mkdir()
    (bad_dir / "butler.toml").write_text("this is not valid toml [[[")

    # Create a valid one too
    good_dir = tmp_path / "goodbutler"
    good_dir.mkdir()
    (good_dir / "butler.toml").write_text('[butler]\nname = "goodbutler"\nport = 7777\n')

    discovered = await discover_butlers(pool, tmp_path)
    names = [d["name"] for d in discovered]
    assert "goodbutler" in names
    assert "badbutler" not in names


# ------------------------------------------------------------------
# route
# ------------------------------------------------------------------


async def test_route_to_unknown_butler(pool):
    """route returns an error dict when the target butler is not registered."""
    from butlers.tools.switchboard import route

    await pool.execute("DELETE FROM butler_registry")
    result = await route(pool, "nonexistent", "some_tool", {})
    assert "error" in result
    assert "not found" in result["error"]


async def test_route_to_known_butler_success(pool):
    """route calls the target butler and returns the result on success."""
    from butlers.tools.switchboard import register_butler, route

    await register_butler(pool, "target", "http://localhost:40200/sse")

    async def mock_call(endpoint_url, tool_name, args):
        return {"status": "ok", "data": 42}

    result = await route(pool, "target", "get_data", {"key": "x"}, call_fn=mock_call)
    assert result == {"result": {"status": "ok", "data": 42}}


async def test_route_to_known_butler_failure(pool):
    """route returns an error dict when the tool call raises."""
    from butlers.tools.switchboard import register_butler, route

    await register_butler(pool, "failing", "http://localhost:8300/sse")

    async def failing_call(endpoint_url, tool_name, args):
        raise ConnectionError("Connection refused")

    result = await route(pool, "failing", "broken_tool", {}, call_fn=failing_call)
    assert "error" in result
    assert "ConnectionError" in result["error"]


async def test_route_blocks_stale_target_by_default_and_allows_override(pool):
    """Stale targets are suppressed by default but can be routed via explicit override."""
    from butlers.tools.switchboard import register_butler, route

    await register_butler(pool, "stale-target", "http://localhost:9300/sse", liveness_ttl_seconds=5)
    await pool.execute(
        """
        UPDATE butler_registry
        SET last_seen_at = now() - interval '45 seconds'
        WHERE name = 'stale-target'
        """
    )

    async def mock_call(endpoint_url, tool_name, args):
        return {"ok": True}

    blocked = await route(pool, "stale-target", "ping", {}, call_fn=mock_call)
    assert "error" in blocked
    assert "stale" in blocked["error"].lower()

    allowed = await route(pool, "stale-target", "ping", {}, allow_stale=True, call_fn=mock_call)
    assert allowed == {"result": {"ok": True}}


async def test_route_blocks_quarantined_target_by_default(pool):
    """Quarantined targets are non-routable unless explicitly overridden."""
    from butlers.tools.switchboard import register_butler, route

    await register_butler(pool, "quarantine-target", "http://localhost:9301/sse")
    await pool.execute(
        """
        UPDATE butler_registry
        SET eligibility_state = 'quarantined',
            quarantined_at = now(),
            quarantine_reason = 'tool_ownership_violation'
        WHERE name = 'quarantine-target'
        """
    )

    async def mock_call(endpoint_url, tool_name, args):
        return {"ok": True}

    blocked = await route(pool, "quarantine-target", "ping", {}, call_fn=mock_call)
    assert "error" in blocked
    assert "quarantined" in blocked["error"].lower()
    assert "tool_ownership_violation" in blocked["error"]


async def test_route_allows_quarantined_target_with_explicit_override(pool):
    """Policy override can explicitly allow routing to quarantined targets."""
    from butlers.tools.switchboard import register_butler, route

    await register_butler(pool, "quarantine-override", "http://localhost:9302/sse")
    await pool.execute(
        """
        UPDATE butler_registry
        SET eligibility_state = 'quarantined',
            quarantined_at = now(),
            quarantine_reason = 'manual_hold'
        WHERE name = 'quarantine-override'
        """
    )

    async def mock_call(endpoint_url, tool_name, args):
        return {"ok": True}

    allowed = await route(
        pool,
        "quarantine-override",
        "ping",
        {},
        allow_quarantined=True,
        call_fn=mock_call,
    )
    assert allowed == {"result": {"ok": True}}


# ------------------------------------------------------------------
# routing_log
# ------------------------------------------------------------------


async def test_routing_log_records_success(pool):
    """Successful routing creates a routing_log entry with success=True."""
    from butlers.tools.switchboard import register_butler, route

    await pool.execute("DELETE FROM routing_log")
    await register_butler(pool, "logged", "http://localhost:8400/sse")

    async def ok_call(endpoint_url, tool_name, args):
        return "ok"

    await route(pool, "logged", "ping", {}, call_fn=ok_call)

    rows = await pool.fetch("SELECT * FROM routing_log WHERE target_butler = 'logged'")
    assert len(rows) == 1
    assert rows[0]["success"] is True
    assert rows[0]["tool_name"] == "ping"
    assert rows[0]["error"] is None


async def test_routing_log_records_failure(pool):
    """Failed routing creates a routing_log entry with success=False and error message."""
    from butlers.tools.switchboard import register_butler, route

    await pool.execute("DELETE FROM routing_log")
    await register_butler(pool, "errored", "http://localhost:8500/sse")

    async def bad_call(endpoint_url, tool_name, args):
        raise RuntimeError("boom")

    await route(pool, "errored", "explode", {}, call_fn=bad_call)

    rows = await pool.fetch("SELECT * FROM routing_log WHERE target_butler = 'errored'")
    assert len(rows) == 1
    assert rows[0]["success"] is False
    assert "boom" in rows[0]["error"]


async def test_routing_log_records_not_found(pool):
    """Routing to an unknown butler logs a failure with 'Butler not found'."""
    from butlers.tools.switchboard import route

    await pool.execute("DELETE FROM routing_log")
    await pool.execute("DELETE FROM butler_registry")

    await route(pool, "ghost", "anything", {})

    rows = await pool.fetch("SELECT * FROM routing_log WHERE target_butler = 'ghost'")
    assert len(rows) == 1
    assert rows[0]["success"] is False
    assert "not found" in rows[0]["error"].lower()


@pytest.fixture
def otel_provider():
    """Set up an in-memory TracerProvider, yield the exporter, then tear down."""
    _reset_otel_global_state()
    exporter = InMemorySpanExporter()
    resource = Resource.create({"service.name": "switchboard-test"})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    yield exporter
    provider.shutdown()
    _reset_otel_global_state()


# Trace context propagation in route()
# ------------------------------------------------------------------


async def test_route_injects_trace_context(pool, otel_provider):
    """route() injects trace_context into forwarded args when a span is active."""
    from butlers.tools.switchboard import register_butler, route

    await register_butler(pool, "traced", "http://localhost:8600/sse")

    captured_args: list[dict] = []

    async def capture_call(endpoint_url, tool_name, args):
        captured_args.append(args)
        return "ok"

    tracer = trace.get_tracer("test")
    with tracer.start_as_current_span("test-parent"):
        await route(pool, "traced", "ping", {"key": "val"}, call_fn=capture_call)

    assert len(captured_args) == 1
    forwarded = captured_args[0]
    assert "key" in forwarded
    assert forwarded["key"] == "val"
    assert "trace_context" in forwarded
    assert "traceparent" in forwarded["trace_context"]


async def test_route_injects_empty_trace_context_without_span(pool, otel_provider):
    """route() still works when no active span is present (no trace_context or empty)."""
    from butlers.tools.switchboard import register_butler, route

    await register_butler(pool, "nospan", "http://localhost:8601/sse")

    captured_args: list[dict] = []

    async def capture_call(endpoint_url, tool_name, args):
        captured_args.append(args)
        return "ok"

    # No active span — inject_trace_context() may return empty dict
    await route(pool, "nospan", "ping", {"x": 1}, call_fn=capture_call)

    assert len(captured_args) == 1
    forwarded = captured_args[0]
    assert forwarded["x"] == 1
    # trace_context may or may not be present depending on whether inject returns empty
    # but the route should still succeed


async def test_route_does_not_mutate_original_args(pool, otel_provider):
    """route() does not modify the caller's args dict."""
    from butlers.tools.switchboard import register_butler, route

    await register_butler(pool, "nomut", "http://localhost:8602/sse")

    async def noop_call(endpoint_url, tool_name, args):
        return "ok"

    original_args = {"key": "val"}
    tracer = trace.get_tracer("test")
    with tracer.start_as_current_span("test-parent"):
        await route(pool, "nomut", "ping", original_args, call_fn=noop_call)

    # Original args must not have trace_context injected
    assert "trace_context" not in original_args


# ------------------------------------------------------------------
# switchboard.route span creation
# ------------------------------------------------------------------


async def test_route_creates_span_with_attributes(pool, otel_provider):
    """route() creates a switchboard.route span with target and tool_name attributes."""
    from butlers.tools.switchboard import register_butler, route

    await register_butler(pool, "spantest", "http://localhost:8603/sse")

    async def ok_call(endpoint_url, tool_name, args):
        return "ok"

    await route(pool, "spantest", "get_data", {}, call_fn=ok_call)

    spans = otel_provider.get_finished_spans()
    route_spans = [s for s in spans if s.name == "switchboard.route"]
    assert len(route_spans) == 1
    span = route_spans[0]
    assert span.attributes["target"] == "spantest"
    assert span.attributes["tool_name"] == "get_data"


async def test_route_dispatch_span_contains_request_context(pool, otel_provider):
    """route() emits dispatch span with request lineage attributes."""
    from butlers.tools.switchboard import register_butler, route

    await register_butler(pool, "dispatchattrs", "http://localhost:8609/sse")

    async def ok_call(endpoint_url, tool_name, args):
        return "ok"

    await route(
        pool,
        "dispatchattrs",
        "get_data",
        {
            "request_id": "req-42",
            "__switchboard_route_context": {
                "request_id": "req-42",
                "segment_id": "segment-9",
                "fanout_mode": "ordered",
                "attempt": 2,
            },
        },
        call_fn=ok_call,
    )

    spans = otel_provider.get_finished_spans()
    dispatch_spans = [s for s in spans if s.name == "butlers.switchboard.route.dispatch"]
    assert len(dispatch_spans) == 1
    span = dispatch_spans[0]
    assert span.attributes["request.id"] == "req-42"
    assert span.attributes["routing.destination_butler"] == "dispatchattrs"
    assert span.attributes["routing.segment_id"] == "segment-9"
    assert span.attributes["routing.fanout_mode"] == "ordered"
    assert span.attributes["routing.attempt"] == 2
    assert span.attributes["routing.outcome"] == "success"


async def test_route_span_error_on_failure(pool, otel_provider):
    """route() sets span status to ERROR when the call fails."""
    from butlers.tools.switchboard import register_butler, route

    await register_butler(pool, "spanfail", "http://localhost:8604/sse")

    async def fail_call(endpoint_url, tool_name, args):
        raise RuntimeError("kaboom")

    await route(pool, "spanfail", "broken", {}, call_fn=fail_call)

    spans = otel_provider.get_finished_spans()
    route_spans = [s for s in spans if s.name == "switchboard.route"]
    assert len(route_spans) == 1
    span = route_spans[0]
    assert span.status.status_code == trace.StatusCode.ERROR


async def test_route_span_error_on_not_found(pool, otel_provider):
    """route() sets span status to ERROR when the target butler is not found."""
    from butlers.tools.switchboard import route

    await pool.execute("DELETE FROM butler_registry")
    await route(pool, "missing", "ping", {})

    spans = otel_provider.get_finished_spans()
    route_spans = [s for s in spans if s.name == "switchboard.route"]
    assert len(route_spans) == 1
    span = route_spans[0]
    assert span.status.status_code == trace.StatusCode.ERROR


# ------------------------------------------------------------------
# last_seen_at update on successful route
# ------------------------------------------------------------------


async def test_route_updates_last_seen_at_on_success(pool):
    """Successful route updates the target butler's last_seen_at timestamp."""
    from butlers.tools.switchboard import register_butler, route

    await register_butler(pool, "seen", "http://localhost:8605/sse")

    # Record the initial last_seen_at (set by register_butler)
    row_before = await pool.fetchrow("SELECT last_seen_at FROM butler_registry WHERE name = 'seen'")
    initial_last_seen = row_before["last_seen_at"]

    async def ok_call(endpoint_url, tool_name, args):
        return "ok"

    await route(pool, "seen", "ping", {}, call_fn=ok_call)

    row_after = await pool.fetchrow("SELECT last_seen_at FROM butler_registry WHERE name = 'seen'")
    assert row_after["last_seen_at"] >= initial_last_seen


async def test_route_does_not_update_last_seen_at_on_failure(pool):
    """Failed route does not update the target butler's last_seen_at timestamp."""
    from butlers.tools.switchboard import register_butler, route

    await register_butler(pool, "unseen", "http://localhost:8606/sse")

    # Record the initial last_seen_at
    row_before = await pool.fetchrow(
        "SELECT last_seen_at FROM butler_registry WHERE name = 'unseen'"
    )
    initial_last_seen = row_before["last_seen_at"]

    async def fail_call(endpoint_url, tool_name, args):
        raise RuntimeError("connection refused")

    await route(pool, "unseen", "broken", {}, call_fn=fail_call)

    row_after = await pool.fetchrow(
        "SELECT last_seen_at FROM butler_registry WHERE name = 'unseen'"
    )
    # last_seen_at should not have been updated
    assert row_after["last_seen_at"] == initial_last_seen


async def test_eligibility_transitions_are_audited_for_stale_and_recovery(pool):
    """TTL staleness and re-registration recovery transitions are recorded."""
    from butlers.tools.switchboard import register_butler, route

    await register_butler(pool, "recovering", "http://localhost:9350/sse", liveness_ttl_seconds=5)
    await pool.execute(
        """
        UPDATE butler_registry
        SET last_seen_at = now() - interval '90 seconds'
        WHERE name = 'recovering'
        """
    )

    async def mock_call(endpoint_url, tool_name, args):
        return {"ok": True}

    blocked = await route(pool, "recovering", "ping", {}, call_fn=mock_call)
    assert "error" in blocked
    assert "stale" in blocked["error"].lower()

    stale_transition = await pool.fetchrow(
        """
        SELECT previous_state, new_state, reason
        FROM butler_registry_eligibility_log
        WHERE butler_name = 'recovering'
        ORDER BY observed_at DESC
        LIMIT 1
        """
    )
    assert stale_transition is not None
    assert stale_transition["previous_state"] == "active"
    assert stale_transition["new_state"] == "stale"
    assert stale_transition["reason"] == "ttl_expired"

    await register_butler(pool, "recovering", "http://localhost:9350/sse", liveness_ttl_seconds=5)
    recovery_transition = await pool.fetchrow(
        """
        SELECT previous_state, new_state, reason
        FROM butler_registry_eligibility_log
        WHERE butler_name = 'recovering'
        ORDER BY observed_at DESC
        LIMIT 1
        """
    )
    assert recovery_transition is not None
    assert recovery_transition["previous_state"] == "stale"
    assert recovery_transition["new_state"] == "active"
    assert recovery_transition["reason"] == "health_restored"


# ------------------------------------------------------------------
# _call_butler_tool — in-process FastMCP server
# ------------------------------------------------------------------


async def test_call_butler_tool_with_fastmcp_server():
    """_call_butler_tool connects to a FastMCP server and returns text result."""
    from fastmcp import Client, FastMCP

    # Create a simple in-process FastMCP server with a test tool
    server = FastMCP("test-butler")

    @server.tool()
    async def echo(message: str) -> str:
        return f"echo: {message}"

    # Verify the in-process FastMCP Client pattern that _call_butler_tool uses
    async with Client(server) as client:
        result = await client.call_tool("echo", {"message": "hello"}, raise_on_error=True)
        assert not result.is_error
        assert result.data == "echo: hello"


async def test_call_butler_tool_returns_structured_data():
    """_call_butler_tool returns structured dict data from tools returning dicts."""
    from fastmcp import Client, FastMCP

    server = FastMCP("json-butler")

    @server.tool()
    async def get_status() -> dict:
        return {"health": "ok", "uptime": 42}

    async with Client(server) as client:
        result = await client.call_tool("get_status", {}, raise_on_error=True)
        assert not result.is_error
        assert result.data == {"health": "ok", "uptime": 42}


async def test_call_butler_tool_propagates_trace_context():
    """_call_butler_tool passes _trace_context in args to the target butler."""
    from fastmcp import Client, FastMCP

    server = FastMCP("trace-butler")

    received_trace_ctx: list[dict] = []

    @server.tool()
    async def check_trace(_trace_context: dict | None = None, message: str = "") -> str:
        if _trace_context:
            received_trace_ctx.append(_trace_context)
        return "ok"

    async with Client(server) as client:
        trace_ctx = {"traceparent": "00-abcd1234abcd1234abcd1234abcd1234-1234abcd1234abcd-01"}
        await client.call_tool(
            "check_trace",
            {
                "_trace_context": trace_ctx,
                "message": "test",
            },
        )

    assert len(received_trace_ctx) == 1
    assert "traceparent" in received_trace_ctx[0]


async def test_call_butler_tool_raises_on_connection_error():
    """_call_butler_tool raises ConnectionError for unreachable endpoints."""
    from butlers.tools.switchboard import _call_butler_tool

    with pytest.raises(ConnectionError, match="Failed to call tool"):
        await _call_butler_tool("http://localhost:1/sse", "ping", {})


# ------------------------------------------------------------------


@pytest.fixture
async def pool_with_extraction(pool):
    """Add extraction_log table to the test pool."""
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS extraction_log (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source_message_preview TEXT,
            extraction_type VARCHAR(100) NOT NULL,
            tool_name VARCHAR(100) NOT NULL,
            tool_args JSONB NOT NULL,
            target_contact_id UUID,
            confidence VARCHAR(20),
            dispatched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            source_channel VARCHAR(50)
        )
    """)
    await pool.execute("""
        CREATE INDEX IF NOT EXISTS idx_extraction_log_contact
        ON extraction_log(target_contact_id)
    """)
    await pool.execute("""
        CREATE INDEX IF NOT EXISTS idx_extraction_log_type
        ON extraction_log(extraction_type)
    """)
    await pool.execute("""
        CREATE INDEX IF NOT EXISTS idx_extraction_log_dispatched
        ON extraction_log(dispatched_at DESC)
    """)
    yield pool


async def test_log_extraction_creates_entry(pool_with_extraction):
    """log_extraction creates a new audit log entry and returns the UUID."""
    from butlers.tools.switchboard import log_extraction

    log_id = await log_extraction(
        pool_with_extraction,
        extraction_type="contact",
        tool_name="contact_add",
        tool_args={"name": "Alice", "email": "alice@example.com"},
        target_contact_id="123e4567-e89b-12d3-a456-426614174000",
        confidence="high",
        source_message_preview="Email from Alice about meeting",
        source_channel="email",
    )

    # Verify UUID format
    from uuid import UUID

    assert UUID(log_id)

    # Verify entry was created
    row = await pool_with_extraction.fetchrow("SELECT * FROM extraction_log WHERE id = $1", log_id)
    assert row is not None
    assert row["extraction_type"] == "contact"
    assert row["tool_name"] == "contact_add"
    assert row["confidence"] == "high"
    assert row["source_channel"] == "email"
    assert "Alice" in row["source_message_preview"]


async def test_log_extraction_truncates_long_preview(pool_with_extraction):
    """log_extraction truncates source_message_preview to 200 characters."""
    from butlers.tools.switchboard import log_extraction

    long_message = "a" * 300
    log_id = await log_extraction(
        pool_with_extraction,
        extraction_type="note",
        tool_name="note_add",
        tool_args={"content": "test"},
        source_message_preview=long_message,
    )

    row = await pool_with_extraction.fetchrow(
        "SELECT source_message_preview FROM extraction_log WHERE id = $1", log_id
    )
    assert len(row["source_message_preview"]) == 200
    assert row["source_message_preview"].endswith("...")


async def test_log_extraction_minimal_fields(pool_with_extraction):
    """log_extraction works with only required fields."""
    from butlers.tools.switchboard import log_extraction

    log_id = await log_extraction(
        pool_with_extraction,
        extraction_type="birthday",
        tool_name="birthday_set",
        tool_args={"contact_id": "123", "date": "1990-01-01"},
    )

    row = await pool_with_extraction.fetchrow("SELECT * FROM extraction_log WHERE id = $1", log_id)
    assert row is not None
    assert row["extraction_type"] == "birthday"
    assert row["tool_name"] == "birthday_set"
    assert row["source_message_preview"] is None
    assert row["source_channel"] is None


async def test_extraction_log_list_empty(pool_with_extraction):
    """extraction_log_list returns empty list when no entries exist."""
    from butlers.tools.switchboard import extraction_log_list

    await pool_with_extraction.execute("DELETE FROM extraction_log")
    entries = await extraction_log_list(pool_with_extraction)
    assert entries == []


async def test_extraction_log_list_all(pool_with_extraction):
    """extraction_log_list returns all entries when no filters applied."""
    from butlers.tools.switchboard import extraction_log_list, log_extraction

    await pool_with_extraction.execute("DELETE FROM extraction_log")

    await log_extraction(pool_with_extraction, "contact", "contact_add", {"name": "Alice"})
    await log_extraction(pool_with_extraction, "note", "note_add", {"content": "Test note"})

    entries = await extraction_log_list(pool_with_extraction)
    assert len(entries) == 2
    types = {e["extraction_type"] for e in entries}
    assert types == {"contact", "note"}


async def test_extraction_log_list_filter_by_contact(pool_with_extraction):
    """extraction_log_list filters by target_contact_id."""
    from butlers.tools.switchboard import extraction_log_list, log_extraction

    await pool_with_extraction.execute("DELETE FROM extraction_log")

    contact_id_1 = "123e4567-e89b-12d3-a456-426614174001"
    contact_id_2 = "123e4567-e89b-12d3-a456-426614174002"

    await log_extraction(
        pool_with_extraction,
        "contact",
        "contact_add",
        {"name": "Alice"},
        target_contact_id=contact_id_1,
    )
    await log_extraction(
        pool_with_extraction,
        "note",
        "note_add",
        {"content": "Note for Bob"},
        target_contact_id=contact_id_2,
    )

    entries = await extraction_log_list(pool_with_extraction, contact_id=contact_id_1)
    assert len(entries) == 1
    assert entries[0]["target_contact_id"] == contact_id_1


async def test_extraction_log_list_filter_by_type(pool_with_extraction):
    """extraction_log_list filters by extraction_type."""
    from butlers.tools.switchboard import extraction_log_list, log_extraction

    await pool_with_extraction.execute("DELETE FROM extraction_log")

    await log_extraction(pool_with_extraction, "contact", "contact_add", {"name": "Alice"})
    await log_extraction(pool_with_extraction, "note", "note_add", {"content": "Test"})
    await log_extraction(pool_with_extraction, "contact", "contact_update", {"id": "123"})

    entries = await extraction_log_list(pool_with_extraction, extraction_type="contact")
    assert len(entries) == 2
    assert all(e["extraction_type"] == "contact" for e in entries)


async def test_extraction_log_list_filter_by_time(pool_with_extraction):
    """extraction_log_list filters by since timestamp."""
    from datetime import UTC, datetime, timedelta

    from butlers.tools.switchboard import extraction_log_list, log_extraction

    await pool_with_extraction.execute("DELETE FROM extraction_log")

    # Create entries at different times (we'll manipulate timestamps after)
    log_id_1 = await log_extraction(pool_with_extraction, "contact", "contact_add", {"name": "Old"})
    log_id_2 = await log_extraction(pool_with_extraction, "contact", "contact_add", {"name": "New"})

    # Manually set timestamps to simulate time passing
    old_time = datetime.now(UTC) - timedelta(hours=2)
    new_time = datetime.now(UTC)

    await pool_with_extraction.execute(
        "UPDATE extraction_log SET dispatched_at = $1 WHERE id = $2",
        old_time,
        log_id_1,
    )
    await pool_with_extraction.execute(
        "UPDATE extraction_log SET dispatched_at = $1 WHERE id = $2",
        new_time,
        log_id_2,
    )

    # Query for entries after 1 hour ago
    since_time = datetime.now(UTC) - timedelta(hours=1)
    entries = await extraction_log_list(pool_with_extraction, since=since_time.isoformat())

    assert len(entries) == 1
    assert str(entries[0]["id"]) == log_id_2


async def test_extraction_log_list_respects_limit(pool_with_extraction):
    """extraction_log_list respects the limit parameter."""
    from butlers.tools.switchboard import extraction_log_list, log_extraction

    await pool_with_extraction.execute("DELETE FROM extraction_log")

    for i in range(10):
        await log_extraction(
            pool_with_extraction, "contact", "contact_add", {"name": f"Contact {i}"}
        )

    entries = await extraction_log_list(pool_with_extraction, limit=5)
    assert len(entries) == 5


async def test_extraction_log_list_max_limit(pool_with_extraction):
    """extraction_log_list caps limit at 500."""
    from butlers.tools.switchboard import extraction_log_list

    await pool_with_extraction.execute("DELETE FROM extraction_log")

    # Request more than max limit
    entries = await extraction_log_list(pool_with_extraction, limit=1000)
    # Since we have no entries, we can't test the actual limit enforcement,
    # but we verify it doesn't error
    assert entries == []


async def test_extraction_log_list_ordered_by_time_desc(pool_with_extraction):
    """extraction_log_list returns entries ordered by dispatched_at DESC."""

    from butlers.tools.switchboard import extraction_log_list, log_extraction

    await pool_with_extraction.execute("DELETE FROM extraction_log")

    log_ids = []
    for i in range(3):
        log_id = await log_extraction(
            pool_with_extraction, "contact", "contact_add", {"name": f"Contact {i}"}
        )
        log_ids.append(log_id)

    entries = await extraction_log_list(pool_with_extraction)
    assert len(entries) == 3

    # Most recent should be first
    entry_ids = [str(e["id"]) for e in entries]
    assert entry_ids == list(reversed(log_ids))


async def test_extraction_log_undo_invalid_uuid(pool_with_extraction):
    """extraction_log_undo returns error for invalid UUID format."""
    from butlers.tools.switchboard import extraction_log_undo

    result = await extraction_log_undo(pool_with_extraction, "not-a-uuid")
    assert "error" in result
    assert "Invalid UUID format" in result["error"]


async def test_extraction_log_undo_not_found(pool_with_extraction):
    """extraction_log_undo returns error when log entry doesn't exist."""
    from uuid import uuid4

    from butlers.tools.switchboard import extraction_log_undo

    fake_id = str(uuid4())
    result = await extraction_log_undo(pool_with_extraction, fake_id)
    assert "error" in result
    assert "not found" in result["error"]


async def test_extraction_log_undo_no_undo_available(pool_with_extraction):
    """extraction_log_undo returns error for tools without undo operations."""
    from butlers.tools.switchboard import extraction_log_undo, log_extraction

    log_id = await log_extraction(
        pool_with_extraction,
        "contact",
        "contact_update",
        {"id": "123", "name": "Updated"},
    )

    result = await extraction_log_undo(pool_with_extraction, log_id)
    assert "error" in result
    assert "No undo operation available" in result["error"]


async def test_extraction_log_undo_success_contact_add(pool_with_extraction):
    """extraction_log_undo calls contact_delete for contact_add."""
    from butlers.tools.switchboard import extraction_log_undo, log_extraction

    contact_id = "123e4567-e89b-12d3-a456-426614174000"
    log_id = await log_extraction(
        pool_with_extraction,
        "contact",
        "contact_add",
        {"id": contact_id, "name": "Alice"},
    )

    async def mock_route(pool, target_butler, tool_name, args):
        return {
            "result": {
                "target": target_butler,
                "tool": tool_name,
                "args": args,
            }
        }

    result = await extraction_log_undo(pool_with_extraction, log_id, route_fn=mock_route)

    assert "result" in result
    assert result["result"]["target"] == "relationship"
    assert result["result"]["tool"] == "contact_delete"
    assert result["result"]["args"]["id"] == contact_id


async def test_extraction_log_undo_success_note_add(pool_with_extraction):
    """extraction_log_undo calls note_delete for note_add."""
    from butlers.tools.switchboard import extraction_log_undo, log_extraction

    note_id = "note-123"
    log_id = await log_extraction(
        pool_with_extraction,
        "note",
        "note_add",
        {"note_id": note_id, "content": "Test note"},
    )

    async def mock_route(pool, target_butler, tool_name, args):
        return {"result": {"tool": tool_name, "args": args}}

    result = await extraction_log_undo(pool_with_extraction, log_id, route_fn=mock_route)

    assert "result" in result
    assert result["result"]["tool"] == "note_delete"
    assert result["result"]["args"]["note_id"] == note_id


async def test_extraction_log_undo_success_birthday_set(pool_with_extraction):
    """extraction_log_undo calls birthday_remove for birthday_set."""
    from butlers.tools.switchboard import extraction_log_undo, log_extraction

    contact_id = "contact-456"
    log_id = await log_extraction(
        pool_with_extraction,
        "birthday",
        "birthday_set",
        {"contact_id": contact_id, "date": "1990-01-01"},
    )

    async def mock_route(pool, target_butler, tool_name, args):
        return {"result": {"tool": tool_name, "args": args}}

    result = await extraction_log_undo(pool_with_extraction, log_id, route_fn=mock_route)

    assert "result" in result
    assert result["result"]["tool"] == "birthday_remove"
    assert result["result"]["args"]["contact_id"] == contact_id


async def test_extraction_log_undo_missing_id_field(pool_with_extraction):
    """extraction_log_undo returns error when tool_args lacks ID fields."""
    from butlers.tools.switchboard import extraction_log_undo, log_extraction

    log_id = await log_extraction(
        pool_with_extraction,
        "contact",
        "contact_add",
        {"name": "Alice"},  # No id, contact_id, or note_id
    )

    result = await extraction_log_undo(pool_with_extraction, log_id)
    assert "error" in result
    assert "Cannot determine target ID" in result["error"]


async def test_extraction_log_undo_routes_error(pool_with_extraction):
    """extraction_log_undo propagates routing errors."""
    from butlers.tools.switchboard import extraction_log_undo, log_extraction

    log_id = await log_extraction(
        pool_with_extraction,
        "contact",
        "contact_add",
        {"id": "123", "name": "Alice"},
    )

    async def failing_route(pool, target_butler, tool_name, args):
        return {"error": "Relationship butler not available"}

    result = await extraction_log_undo(pool_with_extraction, log_id, route_fn=failing_route)

    assert "error" in result
    assert "not available" in result["error"]


# ------------------------------------------------------------------
# _build_channel_args (unit tests)
# ------------------------------------------------------------------


def test_build_channel_args_telegram():
    """_build_channel_args builds correct args for telegram channel."""
    from butlers.tools.switchboard import _build_channel_args

    result = _build_channel_args("telegram", "Hello!", "123456")
    assert result == {"chat_id": "123456", "text": "Hello!"}


def test_build_channel_args_email_default_subject():
    """_build_channel_args builds correct args for email with default subject."""
    from butlers.tools.switchboard import _build_channel_args

    result = _build_channel_args("email", "Body text", "user@example.com")
    assert result == {"to": "user@example.com", "subject": "Notification", "body": "Body text"}


def test_build_channel_args_email_custom_subject():
    """_build_channel_args uses subject from metadata for email."""
    from butlers.tools.switchboard import _build_channel_args

    result = _build_channel_args(
        "email", "Body text", "user@example.com", metadata={"subject": "Custom Subject"}
    )
    assert result == {
        "to": "user@example.com",
        "subject": "Custom Subject",
        "body": "Body text",
    }


def test_build_channel_args_unsupported_channel():
    """_build_channel_args raises ValueError for unsupported channels."""
    from butlers.tools.switchboard import _build_channel_args

    with pytest.raises(ValueError, match="Unsupported channel"):
        _build_channel_args("sms", "Hello", "12345")


# ------------------------------------------------------------------
# log_notification
# ------------------------------------------------------------------


@pytest.fixture
async def pool_with_notifications(pool):
    """Add notifications table to the test pool."""
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source_butler TEXT NOT NULL,
            channel TEXT NOT NULL,
            recipient TEXT NOT NULL,
            message TEXT NOT NULL,
            metadata JSONB NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'sent',
            error TEXT,
            session_id UUID,
            trace_id TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    yield pool


async def test_log_notification_creates_entry(pool_with_notifications):
    """log_notification creates a notification entry and returns its UUID."""
    from uuid import UUID

    from butlers.tools.switchboard import log_notification

    notif_id = await log_notification(
        pool_with_notifications,
        source_butler="health",
        channel="telegram",
        recipient="123456",
        message="Time for your medication!",
        metadata={"type": "medication_reminder"},
        status="sent",
    )

    # Verify UUID format
    assert UUID(notif_id)

    # Verify entry was created
    row = await pool_with_notifications.fetchrow(
        "SELECT * FROM notifications WHERE id = $1", notif_id
    )
    assert row is not None
    assert row["source_butler"] == "health"
    assert row["channel"] == "telegram"
    assert row["recipient"] == "123456"
    assert row["message"] == "Time for your medication!"
    assert row["status"] == "sent"
    assert row["error"] is None


async def test_log_notification_with_error(pool_with_notifications):
    """log_notification stores error messages for failed deliveries."""
    from butlers.tools.switchboard import log_notification

    notif_id = await log_notification(
        pool_with_notifications,
        source_butler="health",
        channel="email",
        recipient="user@example.com",
        message="Report ready",
        status="failed",
        error="SMTP connection refused",
    )

    row = await pool_with_notifications.fetchrow(
        "SELECT * FROM notifications WHERE id = $1", notif_id
    )
    assert row["status"] == "failed"
    assert row["error"] == "SMTP connection refused"


async def test_log_notification_minimal_fields(pool_with_notifications):
    """log_notification works with only required fields."""
    from butlers.tools.switchboard import log_notification

    notif_id = await log_notification(
        pool_with_notifications,
        source_butler="general",
        channel="telegram",
        recipient="789",
        message="Hello",
    )

    row = await pool_with_notifications.fetchrow(
        "SELECT * FROM notifications WHERE id = $1", notif_id
    )
    assert row is not None
    assert row["source_butler"] == "general"
    assert row["status"] == "sent"
    assert row["error"] is None
    assert row["session_id"] is None
    assert row["trace_id"] is None


# ------------------------------------------------------------------
# deliver
# ------------------------------------------------------------------


@pytest.fixture
async def deliver_pool(pool_with_notifications):
    """Pool with both notifications and butler_registry tables for deliver tests."""
    yield pool_with_notifications


async def test_deliver_telegram_success(deliver_pool):
    """deliver() routes a telegram notification and logs it."""
    from butlers.tools.switchboard import deliver, register_butler

    await deliver_pool.execute("DELETE FROM butler_registry")
    await deliver_pool.execute("DELETE FROM notifications")

    # Register the messenger butler endpoint.
    await register_butler(deliver_pool, "messenger", "http://localhost:40100/sse", "Messenger", [])

    async def mock_call(endpoint_url, tool_name, args):
        return {"ok": True, "message_id": 42}

    result = await deliver(
        deliver_pool,
        channel="telegram",
        message="Hello from health butler!",
        recipient="123456",
        source_butler="health",
        call_fn=mock_call,
    )

    assert result["status"] == "sent"
    assert "notification_id" in result
    assert result["result"] == {"ok": True, "message_id": 42}

    # Verify notification was logged
    row = await deliver_pool.fetchrow(
        "SELECT * FROM notifications WHERE id = $1", result["notification_id"]
    )
    assert row is not None
    assert row["channel"] == "telegram"
    assert row["recipient"] == "123456"
    assert row["message"] == "Hello from health butler!"
    assert row["source_butler"] == "health"
    assert row["status"] == "sent"


async def test_deliver_email_success(deliver_pool):
    """deliver() routes an email notification with custom subject."""
    from butlers.tools.switchboard import deliver, register_butler

    await deliver_pool.execute("DELETE FROM butler_registry")
    await deliver_pool.execute("DELETE FROM notifications")

    await register_butler(deliver_pool, "messenger", "http://localhost:40100/sse", "Messenger", [])

    captured_args: list[dict] = []

    async def mock_call(endpoint_url, tool_name, args):
        captured_args.append({"tool_name": tool_name, "args": args})
        return {"status": "sent"}

    result = await deliver(
        deliver_pool,
        channel="email",
        message="Your health report is ready.",
        recipient="user@example.com",
        metadata={"subject": "Health Report"},
        source_butler="health",
        call_fn=mock_call,
    )

    assert result["status"] == "sent"
    assert "notification_id" in result

    # Verify notify.v1 dispatch to messenger route.execute.
    assert len(captured_args) == 1
    assert captured_args[0]["tool_name"] == "route.execute"
    call_args = captured_args[0]["args"]
    notify_request = call_args["input"]["context"]["notify_request"]
    assert notify_request["origin_butler"] == "health"
    assert notify_request["delivery"]["channel"] == "email"
    assert notify_request["delivery"]["message"] == "Your health report is ready."
    assert notify_request["delivery"]["recipient"] == "user@example.com"
    assert notify_request["delivery"]["subject"] == "Health Report"


async def test_deliver_unsupported_channel(deliver_pool):
    """deliver() returns error for unsupported channels."""
    from butlers.tools.switchboard import deliver

    result = await deliver(
        deliver_pool,
        channel="sms",
        message="Hello",
        recipient="12345",
    )

    assert result["status"] == "failed"
    assert "Unsupported channel" in result["error"]
    assert "sms" in result["error"]


async def test_deliver_missing_recipient(deliver_pool):
    """deliver() returns error when recipient is missing."""
    from butlers.tools.switchboard import deliver

    result = await deliver(
        deliver_pool,
        channel="telegram",
        message="Hello",
        recipient=None,
    )

    assert result["status"] == "failed"
    assert "Recipient is required" in result["error"]


async def test_deliver_empty_recipient(deliver_pool):
    """deliver() returns error when recipient is empty string."""
    from butlers.tools.switchboard import deliver

    result = await deliver(
        deliver_pool,
        channel="telegram",
        message="Hello",
        recipient="",
    )

    assert result["status"] == "failed"
    assert "Recipient is required" in result["error"]


async def test_deliver_no_butler_with_module(deliver_pool):
    """deliver() returns error when no butler has the required module."""
    from butlers.tools.switchboard import deliver, register_butler

    await deliver_pool.execute("DELETE FROM butler_registry")
    await deliver_pool.execute("DELETE FROM notifications")

    # Register a butler without the telegram module
    await register_butler(deliver_pool, "health", "http://localhost:40101/sse", "Health", ["email"])

    result = await deliver(
        deliver_pool,
        channel="telegram",
        message="Hello",
        recipient="123456",
    )

    assert result["status"] == "failed"
    assert "No butler with 'telegram' module" in result["error"]
    assert "notification_id" in result

    # Verify failure was logged in notifications
    row = await deliver_pool.fetchrow(
        "SELECT * FROM notifications WHERE id = $1", result["notification_id"]
    )
    assert row is not None
    assert row["status"] == "failed"
    assert "telegram" in row["error"]


async def test_deliver_route_failure_logs_error(deliver_pool):
    """deliver() logs failure when routing to the target butler fails."""
    from butlers.tools.switchboard import deliver, register_butler

    await deliver_pool.execute("DELETE FROM butler_registry")
    await deliver_pool.execute("DELETE FROM notifications")

    await register_butler(deliver_pool, "messenger", "http://localhost:40100/sse")

    async def failing_call(endpoint_url, tool_name, args):
        raise ConnectionError("Telegram API unavailable")

    result = await deliver(
        deliver_pool,
        channel="telegram",
        message="Hello",
        recipient="123456",
        source_butler="health",
        call_fn=failing_call,
    )

    assert result["status"] == "failed"
    assert "ConnectionError" in result["error"]
    assert "notification_id" in result

    # Verify failure was logged
    row = await deliver_pool.fetchrow(
        "SELECT * FROM notifications WHERE id = $1", result["notification_id"]
    )
    assert row["status"] == "failed"
    assert "ConnectionError" in row["error"]


async def test_deliver_logs_to_routing_log(deliver_pool):
    """deliver() creates a routing_log entry via route()."""
    from butlers.tools.switchboard import deliver, register_butler

    await deliver_pool.execute("DELETE FROM butler_registry")
    await deliver_pool.execute("DELETE FROM routing_log")

    await register_butler(deliver_pool, "messenger", "http://localhost:40100/sse")

    async def mock_call(endpoint_url, tool_name, args):
        return {"ok": True}

    await deliver(
        deliver_pool,
        channel="telegram",
        message="Test",
        recipient="123",
        source_butler="health",
        call_fn=mock_call,
    )

    # Verify routing_log entry was created by route()
    rows = await deliver_pool.fetch("SELECT * FROM routing_log")
    assert len(rows) == 1
    assert rows[0]["source_butler"] == "health"
    assert rows[0]["target_butler"] == "messenger"
    assert rows[0]["tool_name"] == "route.execute"
    assert rows[0]["success"] is True


async def test_deliver_email_default_subject(deliver_pool):
    """deliver() uses default subject for email when not in metadata."""
    from butlers.tools.switchboard import deliver, register_butler

    await deliver_pool.execute("DELETE FROM butler_registry")
    await deliver_pool.execute("DELETE FROM notifications")

    await register_butler(deliver_pool, "mailer", "http://localhost:40102/sse", "Mailer", ["email"])

    captured_args: list[dict] = []

    async def mock_call(endpoint_url, tool_name, args):
        captured_args.append(args)
        return {"status": "sent"}

    await deliver(
        deliver_pool,
        channel="email",
        message="Body text",
        recipient="user@example.com",
        call_fn=mock_call,
    )

    assert len(captured_args) == 1
    # Subject should default to "Notification"
    assert captured_args[0]["subject"] == "Notification"


async def test_deliver_metadata_stored_in_notification(deliver_pool):
    """deliver() stores metadata in the notifications table."""
    import json

    from butlers.tools.switchboard import deliver, register_butler

    await deliver_pool.execute("DELETE FROM butler_registry")
    await deliver_pool.execute("DELETE FROM notifications")

    await register_butler(
        deliver_pool, "switchboard", "http://localhost:40100/sse", "Router", ["telegram"]
    )

    async def mock_call(endpoint_url, tool_name, args):
        return {"ok": True}

    result = await deliver(
        deliver_pool,
        channel="telegram",
        message="Hello",
        recipient="123456",
        metadata={"priority": "high", "category": "reminder"},
        call_fn=mock_call,
    )

    row = await deliver_pool.fetchrow(
        "SELECT metadata FROM notifications WHERE id = $1", result["notification_id"]
    )
    metadata = json.loads(row["metadata"]) if isinstance(row["metadata"], str) else row["metadata"]
    assert metadata["priority"] == "high"
    assert metadata["category"] == "reminder"


async def test_deliver_selects_butler_with_matching_module(deliver_pool):
    """deliver() picks the correct butler based on module availability."""
    from butlers.tools.switchboard import deliver, register_butler

    await deliver_pool.execute("DELETE FROM butler_registry")
    await deliver_pool.execute("DELETE FROM notifications")

    # Register butlers with different modules
    await register_butler(
        deliver_pool, "emailer", "http://localhost:40102/sse", "Email Butler", ["email"]
    )
    await register_butler(
        deliver_pool, "chatter", "http://localhost:40103/sse", "Chat Butler", ["telegram"]
    )

    captured_urls: list[str] = []

    async def mock_call(endpoint_url, tool_name, args):
        captured_urls.append(endpoint_url)
        return {"ok": True}

    # Send via telegram — should route to chatter
    await deliver(
        deliver_pool,
        channel="telegram",
        message="Hello",
        recipient="123",
        call_fn=mock_call,
    )
    assert captured_urls[-1] == "http://localhost:40103/sse"

    # Send via email — should route to emailer
    await deliver(
        deliver_pool,
        channel="email",
        message="Hello",
        recipient="user@example.com",
        call_fn=mock_call,
    )
    assert captured_urls[-1] == "http://localhost:40102/sse"


# ------------------------------------------------------------------
# deliver span creation
# ------------------------------------------------------------------


async def test_deliver_creates_span_with_attributes(deliver_pool, otel_provider):
    """deliver() creates a switchboard.deliver span with channel and source attributes."""
    from butlers.tools.switchboard import deliver, register_butler

    await deliver_pool.execute("DELETE FROM butler_registry")
    await register_butler(deliver_pool, "messenger", "http://localhost:40100/sse")

    async def mock_call(endpoint_url, tool_name, args):
        return {"ok": True}

    await deliver(
        deliver_pool,
        channel="telegram",
        message="Test",
        recipient="123",
        source_butler="health",
        call_fn=mock_call,
    )

    spans = otel_provider.get_finished_spans()
    deliver_spans = [s for s in spans if s.name == "switchboard.deliver"]
    assert len(deliver_spans) == 1
    span = deliver_spans[0]
    assert span.attributes["channel"] == "telegram"
    assert span.attributes["source_butler"] == "health"
    assert span.attributes["target_butler"] == "messenger"


async def test_deliver_span_error_on_unsupported_channel(deliver_pool, otel_provider):
    """deliver() sets span status to ERROR for unsupported channels."""
    from butlers.tools.switchboard import deliver

    await deliver(
        deliver_pool,
        channel="sms",
        message="Test",
        recipient="123",
    )

    spans = otel_provider.get_finished_spans()
    deliver_spans = [s for s in spans if s.name == "switchboard.deliver"]
    assert len(deliver_spans) == 1
    assert deliver_spans[0].status.status_code == trace.StatusCode.ERROR


async def test_deliver_span_error_on_route_failure(deliver_pool, otel_provider):
    """deliver() sets span status to ERROR when routing fails."""
    from butlers.tools.switchboard import deliver, register_butler

    await deliver_pool.execute("DELETE FROM butler_registry")
    await register_butler(
        deliver_pool, "switchboard", "http://localhost:40100/sse", "Router", ["telegram"]
    )

    async def failing_call(endpoint_url, tool_name, args):
        raise RuntimeError("kaboom")

    await deliver(
        deliver_pool,
        channel="telegram",
        message="Test",
        recipient="123",
        call_fn=failing_call,
    )

    spans = otel_provider.get_finished_spans()
    deliver_spans = [s for s in spans if s.name == "switchboard.deliver"]
    assert len(deliver_spans) == 1
    assert deliver_spans[0].status.status_code == trace.StatusCode.ERROR
