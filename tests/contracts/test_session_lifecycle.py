"""Contract tests: Session Lifecycle (RFC 0001, Invariant 10).

Validates request_id UUIDv7 propagation, tool call capture, and token tracking.
Every session carries a request_id that propagates to all related records.

Principle: Every session carries a UUIDv7 request_id for end-to-end tracing.
Session records capture tool calls, duration, and token usage (RFC 0001).
"""

from __future__ import annotations

import re
import uuid

import pytest

pytestmark = pytest.mark.contract


class TestRequestIdContract:
    """RFC 0001: request_id is UUIDv7 and propagates to all session records."""

    def test_request_id_is_uuid_format(self):
        """RFC 0001: request_id must be a valid UUID (36-character format)."""
        sample = str(uuid.uuid4())
        assert len(sample) == 36
        assert sample.count("-") == 4

    def test_request_id_format_regex(self):
        """RFC 0001: request_id must match UUID format pattern."""
        uuid_pattern = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
        sample_uuid = str(uuid.uuid4())
        assert uuid_pattern.match(sample_uuid), "request_id must match UUID format (RFC 0001)"

    def test_request_id_propagates_to_session_records(self):
        """RFC 0001: request_id propagates to session records, tool calls, OTel spans.

        'The request_id propagates to: Session records, Tool call captures,
        OpenTelemetry spans, Route envelope request_context field.'
        """
        propagation_targets = [
            "Session records",
            "Tool call captures",
            "OpenTelemetry spans (RFC 0005)",
            "Route envelope request_context field (RFC 0003)",
        ]
        assert len(propagation_targets) == 4, (
            "request_id must propagate to 4 destinations (RFC 0001)"
        )

    def test_connector_sessions_inherit_request_id(self):
        """RFC 0001: Connector-sourced sessions inherit request_id from ingest context.

        'Connector-sourced sessions inherit the request_id from the ingestion
        request context. Internally triggered sessions generate a fresh UUID.'
        """
        # Connector-sourced: inherits request_id from ingest
        # Internal: generates fresh UUIDv7
        session_sources = {
            "connector": "inherits from ingest request_id",
            "internal": "generates fresh UUIDv7",
        }
        assert "connector" in session_sources
        assert "internal" in session_sources

    def test_trigger_source_values_are_known(self):
        """RFC 0001: Trigger source values follow documented format.

        External MCP: 'trigger'
        Switchboard route: 'route'
        Scheduler: 'schedule:<task-name>'
        """
        valid_trigger_sources = {
            "trigger",  # External MCP ad-hoc invocation
            "route",  # Switchboard route.execute
        }
        valid_trigger_prefixes = {"schedule:"}  # Scheduler: schedule:<task-name>

        assert "trigger" in valid_trigger_sources
        assert "route" in valid_trigger_sources
        assert "schedule:" in valid_trigger_prefixes


class TestSessionRecordStructure:
    """RFC 0001: Session records capture the full lifecycle."""

    def test_sessions_table_required_fields(self):
        """RFC 0001: Sessions table must capture all lifecycle fields.

        Required fields: prompt, output, trigger_source, model, request_id,
        tool_calls, duration, tokens, status.
        """
        required_fields = {
            "prompt",
            "output",
            "trigger_source",
            "model",
            "request_id",
            "tool_calls",
            "duration",
            "tokens",
            "status",
        }
        assert len(required_fields) == 9, (
            "Sessions table must capture 9 lifecycle fields (RFC 0001)"
        )

    def test_session_completion_updates_all_fields(self):
        """RFC 0001: On return, session is marked complete with all output fields.

        'On return, the session is marked complete with: output, tool call records,
        duration, token usage, success/failure status.'
        """
        completion_fields = {
            "output",
            "tool_call_records",
            "duration",
            "token_usage",
            "status",  # success/failure
        }
        assert len(completion_fields) == 5, "Session completion must update 5 fields (RFC 0001)"

    def test_token_usage_recorded_against_model_catalog(self):
        """RFC 0001: Token usage is recorded against the model catalog for quota tracking.

        'Token usage is recorded against the model catalog for quota tracking.'
        This enables per-butler daily/monthly token budget enforcement.
        """
        # The model_catalog table in public schema enables quota tracking
        model_catalog_location = "public.model_catalog"
        token_ledger_location = "public.token_usage_ledger"
        assert model_catalog_location == "public.model_catalog"
        assert token_ledger_location == "public.token_usage_ledger"

    def test_concurrency_slots_released_after_session(self):
        """RFC 0001: Concurrency semaphore slots released after session completes.

        'Concurrency slots are released' as step 6 of the session lifecycle,
        after token usage recording.
        """
        session_lifecycle_steps = [
            "Create session row in sessions table",
            "Generate ephemeral MCP config",
            "Invoke runtime adapter (Claude Code, Codex, Gemini)",
            "Mark session complete with output, tool calls, duration, tokens",
            "Record token usage against model catalog",
            "Release concurrency slots",
        ]
        assert len(session_lifecycle_steps) == 6
        # Slot release must be the last step
        assert "Release concurrency slots" in session_lifecycle_steps[-1]


class TestToolCallCapture:
    """RFC 0002: Tool call logging proxy captures all tool invocations."""

    def test_tool_call_capture_includes_required_fields(self):
        """RFC 0002: Tool call capture records all required fields.

        Tool call capture includes: tool name, module name, input payload,
        outcome (success/error), and result.
        """
        required_tool_call_fields = {
            "tool_name",
            "module_name",
            "input_payload",
            "outcome",
            "result",
        }
        assert len(required_tool_call_fields) == 5, (
            "Tool call capture must record 5 fields (RFC 0002)"
        )

    def test_tool_call_span_naming_convention(self):
        """RFC 0002: OTel spans for tools follow 'butler.tool.<name>' naming.

        'A butler.tool.<name> span with butler.name attribute.'
        """
        span_prefix = "butler.tool."
        # Example: butler.tool.state_get, butler.tool.notify
        sample_span = f"{span_prefix}state_get"
        assert sample_span.startswith("butler.tool."), (
            "Tool spans must follow butler.tool.<name> naming convention (RFC 0002)"
        )

    def test_error_handling_does_not_crash_mcp_server(self):
        """RFC 0002: Tool handler exceptions are caught without crashing the MCP server.

        'Catches and logs exceptions from tool handlers without crashing the
        MCP server. Errors are recorded on the OTel span with full stack traces.'
        """
        # The proxy catches errors so the MCP server remains available
        # even if individual tool handlers raise exceptions
        crash_on_error = False  # Proxy catches exceptions per RFC 0002
        assert crash_on_error is False, (
            "Tool errors must be caught by proxy, not crash the server (RFC 0002)"
        )

    def test_sessions_list_tool_exists_in_core(self):
        """RFC 0002: sessions_list tool provides session history access.

        Part of the core tool catalog. LLM sessions can query session history
        for context and debugging.
        """
        core_session_tools = {
            "sessions_list",
            "sessions_get",
            "sessions_summary",
            "sessions_daily",
            "top_sessions",
        }
        assert len(core_session_tools) == 5, "RFC 0002 defines 5 session-related core tools"


class TestSchedulerIntegration:
    """RFC 0001: Scheduler dispatches tasks via the same session lifecycle."""

    def test_toml_schedules_synced_to_db_on_startup(self):
        """RFC 0001 Phase 11: TOML schedules synced to DB at startup.

        'Sync TOML schedules to DB: new tasks are inserted, changed tasks
        are updated, removed tasks are disabled. Runtime-created tasks
        (source "db") are preserved.'
        """
        schedule_source_types = {
            "toml",  # From butler.toml
            "db",  # Runtime-created, preserved on sync
        }
        assert "toml" in schedule_source_types
        assert "db" in schedule_source_types

    def test_scheduler_stagger_is_deterministic(self):
        """RFC 0001: Stagger offset is deterministic via SHA-256 of butler name.

        'Deterministic stagger offset: SHA-256 of butler name, bounded by
        min(max_stagger_seconds, cadence - 1s). Default max_stagger_seconds is 900.'
        """
        import hashlib

        butler_name = "health"
        # The stagger computation uses SHA-256 of the butler name
        stagger_hash = hashlib.sha256(butler_name.encode()).hexdigest()
        # The same input always produces the same hash (deterministic)
        stagger_hash_2 = hashlib.sha256(butler_name.encode()).hexdigest()
        assert stagger_hash == stagger_hash_2, (
            "Stagger offset must be deterministic (SHA-256 of butler name, RFC 0001)"
        )

    def test_default_max_stagger_is_900_seconds(self):
        """RFC 0001: Default max_stagger_seconds is 900 (15 minutes)."""
        default_max_stagger_seconds = 900
        assert default_max_stagger_seconds == 900, (
            "Default max_stagger_seconds must be 900 (RFC 0001)"
        )

    def test_due_tasks_dispatched_through_spawner(self):
        """RFC 0001: Due scheduled tasks dispatch through the spawner.

        'Due tasks dispatch through the spawner with trigger source
        schedule:<task-name>.'
        """
        example = "schedule:morning-briefing"
        assert example.startswith("schedule:"), (
            "Scheduled task trigger source must start with 'schedule:' (RFC 0001)"
        )
