"""Integration tests for butler lifecycle, scheduler, switchboard routing, and tracing.

These tests use testcontainers PostgreSQL for real database interactions and
verify that core subsystems work together end-to-end.
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
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


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

# WARNING: This constant duplicates core table schemas. If you update
# the schema via migrations, you MUST update it here as well to prevent
# schema drift in tests.
CORE_TABLES_SQL = """
    CREATE TABLE IF NOT EXISTS state (
        key TEXT PRIMARY KEY,
        value JSONB NOT NULL DEFAULT '{}',
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );

    CREATE TABLE IF NOT EXISTS scheduled_tasks (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        name TEXT NOT NULL,
        cron TEXT NOT NULL,
        prompt TEXT NOT NULL,
        source TEXT NOT NULL DEFAULT 'db',
        enabled BOOLEAN NOT NULL DEFAULT true,
        next_run_at TIMESTAMPTZ,
        last_run_at TIMESTAMPTZ,
        last_result JSONB,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );

    CREATE TABLE IF NOT EXISTS sessions (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        prompt TEXT NOT NULL,
        trigger_source TEXT NOT NULL,
        result TEXT,
        tool_calls JSONB NOT NULL DEFAULT '[]',
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
    );
"""

SWITCHBOARD_TABLES_SQL = """
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
    );

    CREATE TABLE IF NOT EXISTS routing_log (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        source_butler TEXT NOT NULL,
        target_butler TEXT NOT NULL,
        tool_name TEXT NOT NULL,
        success BOOLEAN NOT NULL,
        duration_ms INTEGER,
        error TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );
"""


def _unique_db_name() -> str:
    return f"test_{uuid.uuid4().hex[:12]}"


@pytest.fixture(scope="module")
def postgres_container():
    """Start a PostgreSQL container for the test module."""
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16") as pg:
        yield pg


async def _make_pool(postgres_container, sql: str) -> asyncpg.Pool:
    """Create a fresh database, run the given SQL, and return a pool."""
    db_name = _unique_db_name()

    admin_conn = await asyncpg.connect(
        host=postgres_container.get_container_host_ip(),
        port=int(postgres_container.get_exposed_port(5432)),
        user=postgres_container.username,
        password=postgres_container.password,
        database="postgres",
    )
    try:
        safe_name = db_name.replace('"', '""')
        await admin_conn.execute(f'CREATE DATABASE "{safe_name}"')
    finally:
        await admin_conn.close()

    pool = await asyncpg.create_pool(
        host=postgres_container.get_container_host_ip(),
        port=int(postgres_container.get_exposed_port(5432)),
        user=postgres_container.username,
        password=postgres_container.password,
        database=db_name,
        min_size=1,
        max_size=3,
    )
    await pool.execute(sql)
    return pool


# ---------------------------------------------------------------------------
# 22.1: Full butler startup integration test
#
# Tests individual core components (state store, scheduler, sessions) with a
# real PostgreSQL database, verifying they all work together on the same pool.
# ---------------------------------------------------------------------------


class TestButlerStartupIntegration:
    """Integration test for the complete butler core subsystem lifecycle."""

    @pytest.fixture
    async def pool(self, postgres_container):
        """Create a fresh DB with all core tables."""
        p = await _make_pool(postgres_container, CORE_TABLES_SQL)
        yield p
        await p.close()

    async def test_core_tables_exist(self, pool):
        """Core migrations create the state, scheduled_tasks, and sessions tables."""
        tables = await pool.fetch(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
        )
        table_names = {row["tablename"] for row in tables}
        assert "state" in table_names
        assert "scheduled_tasks" in table_names
        assert "sessions" in table_names

    async def test_state_store_crud(self, pool):
        """State store get/set/delete/list work with a real database."""
        from butlers.core.state import state_delete, state_get, state_list, state_set

        # Set a value
        await state_set(pool, "butler.name", "test-butler")
        val = await state_get(pool, "butler.name")
        assert val == "test-butler"

        # Set a complex value
        await state_set(pool, "butler.config", {"port": 9100, "active": True})
        val = await state_get(pool, "butler.config")
        assert val == {"port": 9100, "active": True}

        # List with prefix (returns key strings by default)
        keys = await state_list(pool, prefix="butler.")
        assert "butler.name" in keys
        assert "butler.config" in keys

        # Delete
        await state_delete(pool, "butler.name")
        val = await state_get(pool, "butler.name")
        assert val is None

        # List after delete
        keys = await state_list(pool, prefix="butler.")
        assert "butler.name" not in keys
        assert "butler.config" in keys
        assert "butler.name" not in keys
        assert "butler.config" in keys

    async def test_scheduler_crud_and_tick(self, pool):
        """Scheduler create/list/tick work with a real database."""
        from butlers.core.scheduler import schedule_create, schedule_list, tick

        # Create a scheduled task
        task_id = await schedule_create(pool, "daily-check", "*/5 * * * *", "Run daily check")
        assert isinstance(task_id, uuid.UUID)

        # List tasks
        tasks = await schedule_list(pool)
        names = [t["name"] for t in tasks]
        assert "daily-check" in names

        # Force next_run_at to the past so tick picks it up
        await pool.execute(
            "UPDATE scheduled_tasks SET next_run_at = $2 WHERE id = $1",
            task_id,
            datetime.now(UTC) - timedelta(minutes=10),
        )

        # Create a dispatch function that records calls
        dispatch_calls: list[dict] = []

        async def dispatch_fn(**kwargs):
            dispatch_calls.append(kwargs)

        count = await tick(pool, dispatch_fn)
        assert count == 1
        assert len(dispatch_calls) == 1
        assert dispatch_calls[0]["prompt"] == "Run daily check"

    async def test_sessions_crud(self, pool):
        """Sessions create/list/get work with a real database."""
        from butlers.core.sessions import (
            session_complete,
            session_create,
            sessions_get,
            sessions_list,
        )

        # Create a session
        session_id = await session_create(pool, "Hello butler", "trigger", trace_id="abc123")
        assert isinstance(session_id, uuid.UUID)

        # List sessions
        sessions = await sessions_list(pool)
        assert len(sessions) >= 1
        ids = [s["id"] for s in sessions]
        assert session_id in ids

        # Get specific session
        session = await sessions_get(pool, session_id)
        assert session is not None
        assert session["prompt"] == "Hello butler"
        assert session["trigger_source"] == "trigger"
        assert session["trace_id"] == "abc123"
        assert session["completed_at"] is None

        # Complete the session
        await session_complete(
            pool,
            session_id,
            output="Done",
            tool_calls=[{"tool": "state_get", "args": {"key": "x"}}],
            duration_ms=150,
            success=True,
            cost={"input_tokens": 100, "output_tokens": 50},
        )

        session = await sessions_get(pool, session_id)
        assert session["result"] == "Done"
        assert session["duration_ms"] == 150
        assert session["completed_at"] is not None
        assert session["success"] is True
        assert session["error"] is None
        assert session["tool_calls"] == [{"tool": "state_get", "args": {"key": "x"}}]
        assert session["cost"] == {"input_tokens": 100, "output_tokens": 50}

    async def test_all_subsystems_share_pool(self, pool):
        """State, scheduler, and sessions all work together on the same pool."""
        from butlers.core.scheduler import schedule_create
        from butlers.core.sessions import session_create
        from butlers.core.state import state_get, state_set

        # Use all three subsystems on the same pool
        await state_set(pool, "integration.status", "running")
        task_id = await schedule_create(pool, "integ-task", "0 9 * * *", "integration prompt")
        session_id = await session_create(pool, "integration test", "trigger")

        # Verify all operations succeeded
        val = await state_get(pool, "integration.status")
        assert val == "running"

        task_row = await pool.fetchrow("SELECT name FROM scheduled_tasks WHERE id = $1", task_id)
        assert task_row["name"] == "integ-task"

        session_row = await pool.fetchrow("SELECT prompt FROM sessions WHERE id = $1", session_id)
        assert session_row["prompt"] == "integration test"

    async def test_daemon_tool_registration_logic(self, pool):
        """Verify that the daemon registers the expected set of core tools."""
        from unittest.mock import MagicMock

        from butlers.daemon import CORE_TOOL_NAMES, ButlerDaemon

        # We cannot easily call daemon.start() without extensive mocking,
        # but we can test _register_core_tools by setting up the daemon's
        # state manually.
        daemon = ButlerDaemon.__new__(ButlerDaemon)
        daemon.config = MagicMock()
        daemon.config.name = "test-butler"
        daemon.config.description = "A test butler"
        daemon.config.port = 9100
        daemon._modules = []
        daemon._started_at = 1000.0
        daemon.spawner = MagicMock()

        # Create a mock DB with a real pool reference
        daemon.db = MagicMock()
        daemon.db.pool = pool

        # Create a mock FastMCP that captures tool registrations
        registered_tools: list[str] = []
        mock_mcp = MagicMock()

        def tool_decorator(*_decorator_args, **decorator_kwargs):
            declared_name = decorator_kwargs.get("name")

            def decorator(fn):
                registered_tools.append(declared_name or fn.__name__)
                return fn

            return decorator

        mock_mcp.tool = tool_decorator
        daemon.mcp = mock_mcp

        daemon._register_core_tools()

        expected_tools = CORE_TOOL_NAMES
        assert set(registered_tools) == expected_tools


# ---------------------------------------------------------------------------
# 22.2: Scheduler tick integration test
# ---------------------------------------------------------------------------


class TestSchedulerTickIntegration:
    """Integration test for scheduler tick + dispatch + session logging."""

    @pytest.fixture
    async def pool(self, postgres_container):
        """Create a fresh DB with core tables."""
        p = await _make_pool(postgres_container, CORE_TABLES_SQL)
        yield p
        await p.close()

    async def test_tick_dispatches_due_task(self, pool):
        """tick() dispatches a task with next_run_at in the past and advances next_run_at."""
        from butlers.core.scheduler import tick

        # Insert a scheduled task with next_run_at in the past
        past = datetime.now(UTC) - timedelta(minutes=30)
        task_id = await pool.fetchval(
            """
            INSERT INTO scheduled_tasks (name, cron, prompt, source, enabled, next_run_at)
            VALUES ($1, $2, $3, 'toml', true, $4)
            RETURNING id
            """,
            "due-task",
            "*/5 * * * *",
            "Process overdue items",
            past,
        )

        # Create a dispatch function that records calls
        dispatch_calls: list[dict] = []

        async def dispatch_fn(**kwargs):
            dispatch_calls.append(kwargs)

        # Call tick
        count = await tick(pool, dispatch_fn)

        # Verify dispatch was called with the correct prompt
        assert count == 1
        assert len(dispatch_calls) == 1
        assert dispatch_calls[0]["prompt"] == "Process overdue items"
        assert dispatch_calls[0]["trigger_source"] == "schedule:due-task"

        # Verify next_run_at was advanced to the future
        row = await pool.fetchrow(
            "SELECT next_run_at, last_run_at FROM scheduled_tasks WHERE id = $1",
            task_id,
        )
        assert row["next_run_at"] > datetime.now(UTC) - timedelta(seconds=5)
        assert row["last_run_at"] is not None

    async def test_session_log_after_dispatch(self, pool):
        """After tick dispatches a task, a session log entry can be created and retrieved."""
        from butlers.core.scheduler import tick
        from butlers.core.sessions import session_complete, session_create, sessions_get

        # Insert a due task
        past = datetime.now(UTC) - timedelta(minutes=5)
        await pool.execute(
            """
            INSERT INTO scheduled_tasks (name, cron, prompt, source, enabled, next_run_at)
            VALUES ($1, $2, $3, 'runtime', true, $4)
            """,
            "log-test-task",
            "*/10 * * * *",
            "Generate report",
            past,
        )

        # Dispatch function that also creates a session
        session_ids: list[uuid.UUID] = []

        async def dispatch_fn(**kwargs):
            sid = await session_create(pool, kwargs["prompt"], kwargs["trigger_source"])
            session_ids.append(sid)

        count = await tick(pool, dispatch_fn)
        assert count == 1
        assert len(session_ids) == 1

        # Complete the session
        await session_complete(
            pool,
            session_ids[0],
            output="Report generated",
            tool_calls=[],
            duration_ms=500,
            success=True,
        )

        # Verify the session was stored correctly
        session = await sessions_get(pool, session_ids[0])
        assert session is not None
        assert session["prompt"] == "Generate report"
        assert session["trigger_source"] == "schedule:log-test-task"
        assert session["result"] == "Report generated"
        assert session["duration_ms"] == 500
        assert session["success"] is True
        assert session["error"] is None
        assert session["completed_at"] is not None

    async def test_tick_skips_future_and_disabled_tasks(self, pool):
        """tick() only dispatches enabled tasks that are due."""
        from butlers.core.scheduler import tick

        past = datetime.now(UTC) - timedelta(minutes=10)
        future = datetime.now(UTC) + timedelta(hours=1)

        # Due + enabled (should dispatch)
        await pool.execute(
            """
            INSERT INTO scheduled_tasks (name, cron, prompt, source, enabled, next_run_at)
            VALUES ($1, $2, $3, 'runtime', true, $4)
            """,
            "due-enabled",
            "*/5 * * * *",
            "dispatch me",
            past,
        )
        # Due + disabled (should NOT dispatch)
        await pool.execute(
            """
            INSERT INTO scheduled_tasks (name, cron, prompt, source, enabled, next_run_at)
            VALUES ($1, $2, $3, 'runtime', false, $4)
            """,
            "due-disabled",
            "*/5 * * * *",
            "skip me",
            past,
        )
        # Future + enabled (should NOT dispatch)
        await pool.execute(
            """
            INSERT INTO scheduled_tasks (name, cron, prompt, source, enabled, next_run_at)
            VALUES ($1, $2, $3, 'runtime', true, $4)
            """,
            "future-enabled",
            "*/5 * * * *",
            "not yet",
            future,
        )

        dispatch_calls: list[dict] = []

        async def dispatch_fn(**kwargs):
            dispatch_calls.append(kwargs)

        count = await tick(pool, dispatch_fn)

        assert count == 1
        assert len(dispatch_calls) == 1
        assert dispatch_calls[0]["prompt"] == "dispatch me"


# ---------------------------------------------------------------------------
# 22.3: Switchboard routing integration test
# ---------------------------------------------------------------------------


class TestSwitchboardRoutingIntegration:
    """Integration test for switchboard butler registry and routing with real DB."""

    @pytest.fixture(autouse=True)
    def otel_provider(self):
        """Set up an in-memory TracerProvider so route() can inject trace context."""
        _reset_otel_global_state()
        exporter = InMemorySpanExporter()
        resource = Resource.create({"service.name": "switchboard-test"})
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        yield exporter
        provider.shutdown()
        _reset_otel_global_state()

    @pytest.fixture
    async def pool(self, postgres_container):
        """Create a fresh DB with switchboard tables."""
        p = await _make_pool(postgres_container, SWITCHBOARD_TABLES_SQL)
        yield p
        await p.close()

    async def test_register_and_list_butlers(self, pool):
        """Register two butlers and verify they appear in list_butlers."""
        from butlers.tools.switchboard import list_butlers, register_butler

        await register_butler(
            pool, "health", "http://localhost:8101/sse", "Health butler", ["email"]
        )
        await register_butler(
            pool, "general", "http://localhost:8102/sse", "General butler", ["telegram"]
        )

        butlers = await list_butlers(pool)
        names = [b["name"] for b in butlers]
        assert "health" in names
        assert "general" in names
        assert len(names) == 2

    async def test_route_to_known_butler(self, pool):
        """route() calls call_fn with correct endpoint/tool/args for a known butler."""
        from butlers.tools.switchboard import register_butler, route

        await register_butler(pool, "target-butler", "http://localhost:9200/sse")

        call_log: list[dict] = []

        async def mock_call(endpoint_url, tool_name, args):
            call_log.append(
                {
                    "endpoint_url": endpoint_url,
                    "tool_name": tool_name,
                    "args": args,
                }
            )
            return {"status": "ok"}

        result = await route(
            pool,
            "target-butler",
            "state_get",
            {"key": "test"},
            call_fn=mock_call,
        )

        # Verify call_fn received correct arguments
        assert len(call_log) == 1
        assert call_log[0]["endpoint_url"] == "http://localhost:9200/sse"
        assert call_log[0]["tool_name"] == "state_get"
        assert call_log[0]["args"]["key"] == "test"
        assert "_trace_context" in call_log[0]["args"]  # Trace context injected

        # Verify result
        assert result == {"result": {"status": "ok"}}

    async def test_routing_log_created(self, pool):
        """Successful route() creates a routing_log entry."""
        from butlers.tools.switchboard import register_butler, route

        await register_butler(pool, "logged-butler", "http://localhost:9300/sse")

        async def ok_call(endpoint_url, tool_name, args):
            return "ok"

        await route(pool, "logged-butler", "ping", {}, call_fn=ok_call)

        rows = await pool.fetch("SELECT * FROM routing_log WHERE target_butler = 'logged-butler'")
        assert len(rows) == 1
        assert rows[0]["success"] is True
        assert rows[0]["tool_name"] == "ping"
        assert rows[0]["error"] is None
        assert rows[0]["source_butler"] == "switchboard"

    async def test_route_unknown_butler_returns_error(self, pool):
        """route() returns an error dict for an unregistered butler."""
        from butlers.tools.switchboard import route

        result = await route(pool, "nonexistent-butler", "some_tool", {})

        assert "error" in result
        assert "not found" in result["error"]

    async def test_route_unknown_butler_logs_failure(self, pool):
        """Routing to an unknown butler creates a failure routing_log entry."""
        from butlers.tools.switchboard import route

        await route(pool, "ghost-butler", "anything", {})

        rows = await pool.fetch("SELECT * FROM routing_log WHERE target_butler = 'ghost-butler'")
        assert len(rows) == 1
        assert rows[0]["success"] is False
        assert "not found" in rows[0]["error"].lower()

    async def test_route_failure_logs_error(self, pool):
        """When call_fn raises, route() logs the error and returns error dict."""
        from butlers.tools.switchboard import register_butler, route

        await register_butler(pool, "error-butler", "http://localhost:9400/sse")

        async def failing_call(endpoint_url, tool_name, args):
            raise ConnectionError("Connection refused")

        result = await route(pool, "error-butler", "broken_tool", {}, call_fn=failing_call)

        assert "error" in result
        assert "ConnectionError" in result["error"]

        rows = await pool.fetch("SELECT * FROM routing_log WHERE target_butler = 'error-butler'")
        assert len(rows) == 1
        assert rows[0]["success"] is False
        assert "Connection refused" in rows[0]["error"]


# ---------------------------------------------------------------------------
# 22.4: Trace context propagation test
# ---------------------------------------------------------------------------


def _reset_otel_global_state():
    """Fully reset the OpenTelemetry global tracer provider state."""
    trace._TRACER_PROVIDER_SET_ONCE = trace.Once()
    trace._TRACER_PROVIDER = None


class TestTraceContextPropagation:
    """Integration test for OpenTelemetry trace context propagation."""

    @pytest.fixture(autouse=True)
    def otel_provider(self):
        """Set up an in-memory TracerProvider for every test, then tear down."""
        _reset_otel_global_state()
        exporter = InMemorySpanExporter()
        resource = Resource.create({"service.name": "butler-integration-test"})
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        yield exporter
        provider.shutdown()
        _reset_otel_global_state()

    def test_tool_span_creates_named_span(self, otel_provider):
        """tool_span creates a span named butler.tool.<name> with butler.name attribute."""
        from butlers.core.telemetry import tool_span

        with tool_span("state_get", butler_name="integration-butler"):
            pass

        spans = otel_provider.get_finished_spans()
        assert len(spans) == 1
        assert spans[0].name == "butler.tool.state_get"
        assert spans[0].attributes["butler.name"] == "integration-butler"

    def test_inject_extract_preserves_trace_context(self, otel_provider):
        """inject_trace_context + extract_trace_context preserve parent-child relationship."""
        from butlers.core.telemetry import extract_trace_context, inject_trace_context

        tracer = trace.get_tracer("integration-test")

        # Create a parent span and inject its context
        with tracer.start_as_current_span("parent-span") as parent_span:
            parent_trace_id = parent_span.get_span_context().trace_id
            parent_span_id = parent_span.get_span_context().span_id
            ctx_dict = inject_trace_context()

        # Verify the injected context has a traceparent
        assert "traceparent" in ctx_dict
        parts = ctx_dict["traceparent"].split("-")
        assert len(parts) == 4
        assert parts[0] == "00"  # W3C version

        # Extract context and create a child span
        parent_ctx = extract_trace_context(ctx_dict)
        with tracer.start_as_current_span("child-span", context=parent_ctx) as child_span:
            child_trace_id = child_span.get_span_context().trace_id

        # Verify same trace_id (parent-child relationship)
        assert child_trace_id == parent_trace_id

        # Verify parent_span_id relationship
        spans = otel_provider.get_finished_spans()
        child = next(s for s in spans if s.name == "child-span")
        assert child.parent is not None
        assert child.parent.span_id == parent_span_id

    def test_get_traceparent_env_format(self, otel_provider):
        """get_traceparent_env returns TRACEPARENT in W3C format."""
        from butlers.core.telemetry import get_traceparent_env

        tracer = trace.get_tracer("integration-test")
        with tracer.start_as_current_span("env-span"):
            env = get_traceparent_env()

        assert isinstance(env, dict)
        assert "TRACEPARENT" in env
        traceparent = env["TRACEPARENT"]
        assert traceparent.startswith("00-")
        parts = traceparent.split("-")
        assert len(parts) == 4
        # trace_id is 32 hex chars, span_id is 16 hex chars
        assert len(parts[1]) == 32
        assert len(parts[2]) == 16

    def test_full_trace_propagation_flow(self, otel_provider):
        """End-to-end: tool_span -> inject -> extract -> child span preserves trace."""
        from butlers.core.telemetry import (
            extract_trace_context,
            get_traceparent_env,
            inject_trace_context,
            tool_span,
        )

        tracer = trace.get_tracer("integration-test")

        # Step 1: Create a span using tool_span context manager
        with tool_span("trigger", butler_name="switchboard") as parent_span:
            parent_trace_id = parent_span.get_span_context().trace_id
            parent_span_id = parent_span.get_span_context().span_id

            # Step 2: Inject trace context (simulates passing to CC spawner)
            ctx_dict = inject_trace_context()

            # Step 3: Also get env var format
            env = get_traceparent_env()

        # Step 4: Extract context (simulates CC instance receiving context)
        extracted_ctx = extract_trace_context(ctx_dict)

        # Step 5: Create a child span in the extracted context
        with tracer.start_as_current_span("cc-execution", context=extracted_ctx) as child_span:
            child_trace_id = child_span.get_span_context().trace_id

        # Verify the full chain
        assert child_trace_id == parent_trace_id

        spans = otel_provider.get_finished_spans()
        assert len(spans) == 2

        parent = next(s for s in spans if s.name == "butler.tool.trigger")
        child = next(s for s in spans if s.name == "cc-execution")

        # Parent has the correct attributes
        assert parent.attributes["butler.name"] == "switchboard"

        # Child's parent is the tool_span
        assert child.parent is not None
        assert child.parent.span_id == parent_span_id
        assert child.parent.trace_id == parent_trace_id

        # Env var format matches
        assert "TRACEPARENT" in env
        assert env["TRACEPARENT"].startswith("00-")

    def test_empty_context_without_active_span(self, otel_provider):
        """Without an active span, inject/get_traceparent_env return empty or no-op dicts."""
        from butlers.core.telemetry import get_traceparent_env, inject_trace_context

        ctx = inject_trace_context()
        assert isinstance(ctx, dict)

        env = get_traceparent_env()
        assert isinstance(env, dict)
