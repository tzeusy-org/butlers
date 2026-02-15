"""Tests for butlers.tools.heartbeat — tick cycle for all registered butlers."""

from __future__ import annotations

import json
import shutil
import uuid

import pytest

# Skip all tests in this module if Docker is not available
docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


def _unique_db_name() -> str:
    return f"test_{uuid.uuid4().hex[:12]}"


@pytest.fixture(scope="module")
def postgres_container():
    """Start a PostgreSQL container for the test module."""
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16") as pg:
        yield pg


@pytest.fixture
async def pool(postgres_container):
    """Provision a fresh database and return a pool."""
    from butlers.db import Database

    db = Database(
        db_name=_unique_db_name(),
        host=postgres_container.get_container_host_ip(),
        port=int(postgres_container.get_exposed_port(5432)),
        user=postgres_container.username,
        password=postgres_container.password,
        min_pool_size=1,
        max_pool_size=3,
    )
    await db.provision()
    p = await db.connect()

    yield p
    await db.close()


@pytest.fixture
async def pool_with_sessions(pool):
    """Return a pool with the sessions table created.

    WARNING: This fixture duplicates the 'sessions' table schema. If you update
    the schema via migrations, you MUST update it here as well to prevent
    schema drift in tests.
    """
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            prompt TEXT NOT NULL,
            trigger_source TEXT NOT NULL,
            result TEXT,
            tool_calls JSONB,
            duration_ms INTEGER,
            trace_id TEXT,
            model TEXT,
            cost JSONB,
            success BOOLEAN,
            error TEXT,
            input_tokens INTEGER,
            output_tokens INTEGER,
            parent_session_id UUID,
            request_id TEXT,
            started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            completed_at TIMESTAMPTZ
        )
    """)
    return pool


# ------------------------------------------------------------------
# tick_all_butlers — core functionality
# ------------------------------------------------------------------


async def test_tick_all_butlers_ticks_all_except_heartbeat(pool):
    """tick_all_butlers calls tick_fn for each butler except heartbeat."""
    from butlers.tools.heartbeat import tick_all_butlers

    # Mock butler registry
    butlers = [
        {"name": "general", "endpoint_url": "http://localhost:8101/sse"},
        {"name": "health", "endpoint_url": "http://localhost:8102/sse"},
        {"name": "heartbeat", "endpoint_url": "http://localhost:8199/sse"},
        {"name": "relationship", "endpoint_url": "http://localhost:8103/sse"},
    ]

    async def mock_list_butlers():
        return butlers

    ticked_names = []

    async def mock_tick_fn(name: str):
        ticked_names.append(name)

    result = await tick_all_butlers(pool, mock_list_butlers, mock_tick_fn, log_session=False)

    # Should tick all except heartbeat
    assert set(ticked_names) == {"general", "health", "relationship"}
    assert result["total"] == 3
    assert set(result["successful"]) == {"general", "health", "relationship"}
    assert result["failed"] == []


async def test_tick_all_butlers_self_exclusion(pool):
    """tick_all_butlers excludes heartbeat from the tick targets."""
    from butlers.tools.heartbeat import tick_all_butlers

    butlers = [
        {"name": "heartbeat", "endpoint_url": "http://localhost:8199/sse"},
        {"name": "general", "endpoint_url": "http://localhost:8101/sse"},
    ]

    async def mock_list_butlers():
        return butlers

    ticked_names = []

    async def mock_tick_fn(name: str):
        ticked_names.append(name)

    result = await tick_all_butlers(pool, mock_list_butlers, mock_tick_fn, log_session=False)

    # heartbeat should NOT be in the ticked list
    assert "heartbeat" not in ticked_names
    assert ticked_names == ["general"]
    assert result["total"] == 1
    assert result["successful"] == ["general"]


async def test_tick_all_butlers_error_resilience(pool):
    """tick_all_butlers continues ticking even if one butler fails."""
    from butlers.tools.heartbeat import tick_all_butlers

    butlers = [
        {"name": "alpha", "endpoint_url": "http://localhost:8101/sse"},
        {"name": "beta", "endpoint_url": "http://localhost:8102/sse"},
        {"name": "gamma", "endpoint_url": "http://localhost:8103/sse"},
    ]

    async def mock_list_butlers():
        return butlers

    async def failing_tick_fn(name: str):
        if name == "beta":
            raise ConnectionError("beta is down")
        # alpha and gamma succeed

    result = await tick_all_butlers(pool, mock_list_butlers, failing_tick_fn, log_session=False)

    assert result["total"] == 3
    assert set(result["successful"]) == {"alpha", "gamma"}
    assert len(result["failed"]) == 1
    assert result["failed"][0]["name"] == "beta"
    assert "ConnectionError" in result["failed"][0]["error"]
    assert "beta is down" in result["failed"][0]["error"]


async def test_tick_all_butlers_multiple_failures(pool):
    """tick_all_butlers handles multiple butler failures gracefully."""
    from butlers.tools.heartbeat import tick_all_butlers

    butlers = [
        {"name": "a", "endpoint_url": "http://localhost:8101/sse"},
        {"name": "b", "endpoint_url": "http://localhost:8102/sse"},
        {"name": "c", "endpoint_url": "http://localhost:8103/sse"},
        {"name": "d", "endpoint_url": "http://localhost:8104/sse"},
    ]

    async def mock_list_butlers():
        return butlers

    async def multi_fail_tick_fn(name: str):
        if name in {"a", "c"}:
            raise RuntimeError(f"{name} failed")

    result = await tick_all_butlers(pool, mock_list_butlers, multi_fail_tick_fn, log_session=False)

    assert result["total"] == 4
    assert set(result["successful"]) == {"b", "d"}
    assert len(result["failed"]) == 2
    failed_names = {f["name"] for f in result["failed"]}
    assert failed_names == {"a", "c"}


async def test_tick_all_butlers_all_failures(pool):
    """tick_all_butlers completes normally even when all butlers fail."""
    from butlers.tools.heartbeat import tick_all_butlers

    butlers = [
        {"name": "x", "endpoint_url": "http://localhost:8101/sse"},
        {"name": "y", "endpoint_url": "http://localhost:8102/sse"},
    ]

    async def mock_list_butlers():
        return butlers

    async def all_fail_tick_fn(name: str):
        raise TimeoutError(f"{name} timed out")

    result = await tick_all_butlers(pool, mock_list_butlers, all_fail_tick_fn, log_session=False)

    assert result["total"] == 2
    assert result["successful"] == []
    assert len(result["failed"]) == 2
    for f in result["failed"]:
        assert "TimeoutError" in f["error"]


async def test_tick_all_butlers_list_butlers_fails(pool):
    """tick_all_butlers returns error summary when list_butlers_fn fails."""
    from butlers.tools.heartbeat import tick_all_butlers

    async def broken_list_butlers():
        raise RuntimeError("Registry unavailable")

    async def mock_tick_fn(name: str):
        pass

    result = await tick_all_butlers(pool, broken_list_butlers, mock_tick_fn, log_session=False)

    assert result["total"] == 0
    assert result["successful"] == []
    assert len(result["failed"]) == 1
    assert result["failed"][0]["name"] == "list_butlers"
    assert "RuntimeError" in result["failed"][0]["error"]
    assert "Registry unavailable" in result["failed"][0]["error"]


async def test_tick_all_butlers_empty_registry(pool):
    """tick_all_butlers handles an empty butler registry."""
    from butlers.tools.heartbeat import tick_all_butlers

    async def empty_list_butlers():
        return []

    tick_called = False

    async def mock_tick_fn(name: str):
        nonlocal tick_called
        tick_called = True

    result = await tick_all_butlers(pool, empty_list_butlers, mock_tick_fn, log_session=False)

    assert result["total"] == 0
    assert result["successful"] == []
    assert result["failed"] == []
    assert not tick_called


async def test_tick_all_butlers_only_heartbeat_in_registry(pool):
    """tick_all_butlers does nothing when only heartbeat is registered."""
    from butlers.tools.heartbeat import tick_all_butlers

    butlers = [{"name": "heartbeat", "endpoint_url": "http://localhost:8199/sse"}]

    async def mock_list_butlers():
        return butlers

    tick_called = False

    async def mock_tick_fn(name: str):
        nonlocal tick_called
        tick_called = True

    result = await tick_all_butlers(pool, mock_list_butlers, mock_tick_fn, log_session=False)

    assert result["total"] == 0
    assert result["successful"] == []
    assert result["failed"] == []
    assert not tick_called


# ------------------------------------------------------------------
# Session logging
# ------------------------------------------------------------------


async def test_tick_all_butlers_logs_session_on_success(pool_with_sessions):
    """tick_all_butlers logs a session entry on successful cycle."""
    from butlers.tools.heartbeat import tick_all_butlers

    pool = pool_with_sessions
    await pool.execute("DELETE FROM sessions")

    butlers = [
        {"name": "general", "endpoint_url": "http://localhost:8101/sse"},
        {"name": "health", "endpoint_url": "http://localhost:8102/sse"},
    ]

    async def mock_list_butlers():
        return butlers

    async def mock_tick_fn(name: str):
        pass

    await tick_all_butlers(pool, mock_list_butlers, mock_tick_fn, log_session=True)

    # Verify session was logged
    rows = await pool.fetch("SELECT * FROM sessions WHERE trigger_source = 'heartbeat'")
    assert len(rows) == 1
    session = dict(rows[0])
    assert session["trigger_source"] == "heartbeat"
    assert "2/2" in session["result"]
    assert "successfully" in session["result"]
    assert session["duration_ms"] is not None
    assert session["completed_at"] is not None

    # Verify tool_calls JSONB
    tool_calls = session["tool_calls"]
    if isinstance(tool_calls, str):
        tool_calls = json.loads(tool_calls)
    assert len(tool_calls) == 2
    assert all(tc["success"] is True for tc in tool_calls)
    butler_names = {tc["butler"] for tc in tool_calls}
    assert butler_names == {"general", "health"}


async def test_tick_all_butlers_logs_session_on_partial_failure(pool_with_sessions):
    """tick_all_butlers logs a session entry with failure details."""
    from butlers.tools.heartbeat import tick_all_butlers

    pool = pool_with_sessions
    await pool.execute("DELETE FROM sessions")

    butlers = [
        {"name": "alpha", "endpoint_url": "http://localhost:8101/sse"},
        {"name": "beta", "endpoint_url": "http://localhost:8102/sse"},
        {"name": "gamma", "endpoint_url": "http://localhost:8103/sse"},
    ]

    async def mock_list_butlers():
        return butlers

    async def failing_tick_fn(name: str):
        if name == "beta":
            raise ConnectionError("beta unreachable")

    await tick_all_butlers(pool, mock_list_butlers, failing_tick_fn, log_session=True)

    rows = await pool.fetch("SELECT * FROM sessions WHERE trigger_source = 'heartbeat'")
    assert len(rows) == 1
    session = dict(rows[0])
    assert "2/3 succeeded" in session["result"]
    assert "1 failed" in session["result"]
    assert "beta" in session["result"]

    tool_calls = session["tool_calls"]
    if isinstance(tool_calls, str):
        tool_calls = json.loads(tool_calls)
    assert len(tool_calls) == 3
    failed_calls = [tc for tc in tool_calls if not tc["success"]]
    assert len(failed_calls) == 1
    assert failed_calls[0]["butler"] == "beta"
    assert "ConnectionError" in failed_calls[0]["error"]


async def test_tick_all_butlers_logs_session_on_registry_failure(pool_with_sessions):
    """tick_all_butlers logs a session entry even when registry query fails."""
    from butlers.tools.heartbeat import tick_all_butlers

    pool = pool_with_sessions
    await pool.execute("DELETE FROM sessions")

    async def broken_list_butlers():
        raise RuntimeError("Registry down")

    async def mock_tick_fn(name: str):
        pass

    await tick_all_butlers(pool, broken_list_butlers, mock_tick_fn, log_session=True)

    rows = await pool.fetch("SELECT * FROM sessions WHERE trigger_source = 'heartbeat'")
    assert len(rows) == 1
    session = dict(rows[0])
    assert session["trigger_source"] == "heartbeat"
    assert "0/0" in session["result"] or "list_butlers" in str(session["tool_calls"])


async def test_tick_all_butlers_no_session_log_when_disabled(pool_with_sessions):
    """tick_all_butlers does not log session when log_session=False."""
    from butlers.tools.heartbeat import tick_all_butlers

    pool = pool_with_sessions
    await pool.execute("DELETE FROM sessions")

    butlers = [{"name": "general", "endpoint_url": "http://localhost:8101/sse"}]

    async def mock_list_butlers():
        return butlers

    async def mock_tick_fn(name: str):
        pass

    await tick_all_butlers(pool, mock_list_butlers, mock_tick_fn, log_session=False)

    rows = await pool.fetch("SELECT * FROM sessions WHERE trigger_source = 'heartbeat'")
    assert len(rows) == 0


async def test_tick_all_butlers_session_log_graceful_without_table(pool):
    """tick_all_butlers does not crash when sessions table doesn't exist."""
    from butlers.tools.heartbeat import tick_all_butlers

    # pool does NOT have sessions table

    butlers = [{"name": "general", "endpoint_url": "http://localhost:8101/sse"}]

    async def mock_list_butlers():
        return butlers

    async def mock_tick_fn(name: str):
        pass

    # Should not raise even with log_session=True
    result = await tick_all_butlers(pool, mock_list_butlers, mock_tick_fn, log_session=True)
    assert result["total"] == 1
    assert result["successful"] == ["general"]


# ------------------------------------------------------------------
# Summary formatting
# ------------------------------------------------------------------


def test_format_summary_all_success():
    """_format_summary reports all successes correctly."""
    from butlers.tools.heartbeat import _format_summary

    summary = {"total": 3, "successful": ["a", "b", "c"], "failed": []}
    result = _format_summary(summary)
    assert "3/3" in result
    assert "successfully" in result


def test_format_summary_with_failures():
    """_format_summary includes failure names."""
    from butlers.tools.heartbeat import _format_summary

    summary = {
        "total": 3,
        "successful": ["a"],
        "failed": [
            {"name": "b", "error": "ConnectionError: down"},
            {"name": "c", "error": "TimeoutError: slow"},
        ],
    }
    result = _format_summary(summary)
    assert "1/3 succeeded" in result
    assert "2 failed" in result
    assert "b" in result
    assert "c" in result


def test_format_summary_zero_total():
    """_format_summary handles zero total."""
    from butlers.tools.heartbeat import _format_summary

    summary = {"total": 0, "successful": [], "failed": []}
    result = _format_summary(summary)
    assert "0/0" in result


# ------------------------------------------------------------------
# Tool call building
# ------------------------------------------------------------------


def test_build_tool_calls_success_only():
    """_build_tool_calls creates entries for successful ticks."""
    from butlers.tools.heartbeat import _build_tool_calls

    summary = {"total": 2, "successful": ["a", "b"], "failed": []}
    calls = _build_tool_calls(summary)
    assert len(calls) == 2
    assert all(c["success"] is True for c in calls)
    assert all(c["tool"] == "tick" for c in calls)


def test_build_tool_calls_mixed():
    """_build_tool_calls includes both successes and failures."""
    from butlers.tools.heartbeat import _build_tool_calls

    summary = {
        "total": 3,
        "successful": ["a"],
        "failed": [
            {"name": "b", "error": "ConnectionError: down"},
            {"name": "c", "error": "TimeoutError: slow"},
        ],
    }
    calls = _build_tool_calls(summary)
    assert len(calls) == 3

    success_calls = [c for c in calls if c["success"]]
    fail_calls = [c for c in calls if not c["success"]]
    assert len(success_calls) == 1
    assert len(fail_calls) == 2
    assert fail_calls[0]["error"] == "ConnectionError: down"


# ------------------------------------------------------------------
# Integration with switchboard tools
# ------------------------------------------------------------------


async def test_tick_all_with_real_switchboard_list_butlers(pool):
    """tick_all_butlers integrates with switchboard.list_butlers."""
    from butlers.tools.heartbeat import tick_all_butlers
    from butlers.tools.switchboard import list_butlers, register_butler

    # Create switchboard tables
    await pool.execute("""
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

    # Register some butlers
    await pool.execute("DELETE FROM butler_registry")
    await register_butler(pool, "general", "http://localhost:8101/sse")
    await register_butler(pool, "health", "http://localhost:8102/sse")
    await register_butler(pool, "heartbeat", "http://localhost:8199/sse")

    ticked_names = []

    async def mock_tick_fn(name: str):
        ticked_names.append(name)

    async def wrapped_list_butlers():
        return await list_butlers(pool)

    result = await tick_all_butlers(pool, wrapped_list_butlers, mock_tick_fn, log_session=False)

    # Should tick all except heartbeat
    assert set(ticked_names) == {"general", "health"}
    assert result["total"] == 2
    assert set(result["successful"]) == {"general", "health"}
    assert result["failed"] == []


async def test_tick_all_with_switchboard_route_simulation(pool):
    """tick_all_butlers can use switchboard.route as the tick_fn."""
    from butlers.tools.heartbeat import tick_all_butlers
    from butlers.tools.switchboard import register_butler, route

    # Create switchboard tables
    await pool.execute("""
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
    await pool.execute("""
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

    await pool.execute("DELETE FROM butler_registry")
    await pool.execute("DELETE FROM routing_log")
    await register_butler(pool, "general", "http://localhost:8101/sse")
    await register_butler(pool, "heartbeat", "http://localhost:8199/sse")

    # Mock the route call with a custom call_fn
    async def mock_call_fn(endpoint_url, tool_name, args):
        return {"status": "ok", "ticked": True}

    async def wrapped_list_butlers():
        butlers = await pool.fetch("SELECT * FROM butler_registry ORDER BY name")
        return [dict(row) for row in butlers]

    async def tick_via_route(name: str):
        await route(pool, name, "tick", {}, source_butler="heartbeat", call_fn=mock_call_fn)

    result = await tick_all_butlers(pool, wrapped_list_butlers, tick_via_route, log_session=False)

    # Should have ticked general (not heartbeat)
    assert result["total"] == 1
    assert result["successful"] == ["general"]
    assert result["failed"] == []

    # Verify routing was logged
    logs = await pool.fetch(
        "SELECT * FROM routing_log WHERE source_butler = 'heartbeat' ORDER BY created_at"
    )
    assert len(logs) == 1
    assert logs[0]["target_butler"] == "general"
    assert logs[0]["tool_name"] == "tick"
    assert logs[0]["success"] is True


# ------------------------------------------------------------------
# Telemetry — heartbeat.cycle span
# ------------------------------------------------------------------


def _reset_otel_global_state():
    """Fully reset the OpenTelemetry global tracer provider state."""
    from opentelemetry import trace

    trace._TRACER_PROVIDER_SET_ONCE = trace.Once()
    trace._TRACER_PROVIDER = None


@pytest.fixture
def otel_provider():
    """Set up an in-memory TracerProvider for heartbeat span tests, then tear down."""
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    _reset_otel_global_state()
    exporter = InMemorySpanExporter()
    resource = Resource.create({"service.name": "butler-test"})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    yield exporter
    provider.shutdown()
    _reset_otel_global_state()


async def test_tick_all_butlers_creates_heartbeat_cycle_span(pool, otel_provider):
    """tick_all_butlers creates heartbeat.cycle span with butlers_ticked and failures."""
    from butlers.tools.heartbeat import tick_all_butlers

    butlers = [
        {"name": "general", "endpoint_url": "http://localhost:8101/sse"},
        {"name": "health", "endpoint_url": "http://localhost:8102/sse"},
        {"name": "heartbeat", "endpoint_url": "http://localhost:8199/sse"},
        {"name": "relationship", "endpoint_url": "http://localhost:8103/sse"},
    ]

    async def mock_list_butlers():
        return butlers

    async def mock_tick_fn(name: str):
        pass

    await tick_all_butlers(pool, mock_list_butlers, mock_tick_fn)

    spans = otel_provider.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.name == "heartbeat.cycle"
    assert span.attributes["butlers_ticked"] == 3  # all except heartbeat
    assert span.attributes["failures"] == 0


async def test_heartbeat_cycle_span_with_failures(pool, otel_provider):
    """heartbeat.cycle span records failures count."""
    from butlers.tools.heartbeat import tick_all_butlers

    butlers = [
        {"name": "alpha", "endpoint_url": "http://localhost:8101/sse"},
        {"name": "beta", "endpoint_url": "http://localhost:8102/sse"},
        {"name": "gamma", "endpoint_url": "http://localhost:8103/sse"},
        {"name": "delta", "endpoint_url": "http://localhost:8104/sse"},
    ]

    async def mock_list_butlers():
        return butlers

    async def failing_tick_fn(name: str):
        if name == "beta":
            raise RuntimeError("beta failed")

    await tick_all_butlers(pool, mock_list_butlers, failing_tick_fn)

    spans = otel_provider.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.name == "heartbeat.cycle"
    assert span.attributes["butlers_ticked"] == 4
    assert span.attributes["failures"] == 1


async def test_heartbeat_cycle_span_with_list_butlers_failure(pool, otel_provider):
    """heartbeat.cycle span handles list_butlers failure."""
    from butlers.tools.heartbeat import tick_all_butlers

    async def broken_list_butlers():
        raise RuntimeError("Registry unavailable")

    async def mock_tick_fn(name: str):
        pass

    await tick_all_butlers(pool, broken_list_butlers, mock_tick_fn)

    spans = otel_provider.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.name == "heartbeat.cycle"
    assert span.attributes["butlers_ticked"] == 0
    assert span.attributes["failures"] == 1


async def test_heartbeat_cycle_span_with_empty_registry(pool, otel_provider):
    """heartbeat.cycle span with empty registry has zero butlers_ticked."""
    from butlers.tools.heartbeat import tick_all_butlers

    async def empty_list_butlers():
        return []

    async def mock_tick_fn(name: str):
        pass

    await tick_all_butlers(pool, empty_list_butlers, mock_tick_fn)

    spans = otel_provider.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.name == "heartbeat.cycle"
    assert span.attributes["butlers_ticked"] == 0
    assert span.attributes["failures"] == 0


async def test_tick_all_with_route_and_session_logging(pool_with_sessions):
    """Full integration: tick via route with session logging enabled."""
    from butlers.tools.heartbeat import tick_all_butlers

    pool = pool_with_sessions

    # Create switchboard tables
    await pool.execute("""
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

    await pool.execute("DELETE FROM butler_registry")
    await pool.execute("DELETE FROM sessions")

    # Register butlers directly
    await pool.execute(
        "INSERT INTO butler_registry (name, endpoint_url) VALUES ('general', 'http://localhost:8101/sse')"
    )
    await pool.execute(
        "INSERT INTO butler_registry (name, endpoint_url) VALUES ('health', 'http://localhost:8102/sse')"
    )
    await pool.execute(
        "INSERT INTO butler_registry (name, endpoint_url) VALUES ('heartbeat', 'http://localhost:8199/sse')"
    )

    async def list_fn():
        rows = await pool.fetch("SELECT * FROM butler_registry ORDER BY name")
        return [dict(r) for r in rows]

    ticked = []

    async def tick_fn(name: str):
        ticked.append(name)

    result = await tick_all_butlers(pool, list_fn, tick_fn, log_session=True)

    # Verify ticking
    assert set(ticked) == {"general", "health"}
    assert result["total"] == 2
    assert set(result["successful"]) == {"general", "health"}

    # Verify session log
    sessions = await pool.fetch("SELECT * FROM sessions WHERE trigger_source = 'heartbeat'")
    assert len(sessions) == 1
    s = dict(sessions[0])
    assert "2/2" in s["result"]
    assert s["duration_ms"] >= 0


# ------------------------------------------------------------------
# Butler config validation
# ------------------------------------------------------------------


def test_heartbeat_butler_toml_loads_correctly():
    """The heartbeat butler.toml loads with the correct schedule format."""
    from pathlib import Path

    from butlers.config import load_config

    # Use the actual heartbeat butler config dir
    heartbeat_dir = Path(__file__).parent.parent / "butlers" / "heartbeat"
    if not heartbeat_dir.exists():
        pytest.skip("Heartbeat butler config directory not available in test environment")

    config = load_config(heartbeat_dir)
    assert config.name == "heartbeat"
    assert config.port == 8199
    assert config.db_name == "butler_heartbeat"
    assert len(config.schedules) == 1
    assert config.schedules[0].name == "heartbeat-cycle"
    assert config.schedules[0].cron == "*/10 * * * *"
    assert "list_butlers" in config.schedules[0].prompt
    assert config.modules == {}
