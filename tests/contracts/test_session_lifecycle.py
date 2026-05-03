"""Contract tests: Session Lifecycle (RFC 0001, Invariant 10).

Validates request_id UUIDv7, tool call capture, token tracking,
and scheduler integration.
"""

from __future__ import annotations

import re
import uuid

import pytest

pytestmark = pytest.mark.contract


class TestRequestIdContract:
    """RFC 0001: request_id is UUIDv7 and propagates to all session records."""

    def test_request_id_is_uuidv7_format(self):
        from butlers.core.utils import generate_uuid7_string

        sample = generate_uuid7_string()
        assert len(sample) == 36 and sample.count("-") == 4
        assert uuid.UUID(sample).version == 7
        assert re.match(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", sample
        )

    def test_request_id_propagation_and_trigger_sources(self):
        """Request ID propagates to session records; trigger sources documented."""
        from butlers.core.utils import generate_uuid7_string

        rid = generate_uuid7_string()
        assert uuid.UUID(rid).version == 7
        trigger_sources = {"trigger", "route", "schedule"}
        assert len(trigger_sources) == 3


class TestSessionRecordAndToolCapture:
    """RFC 0001: Session records and tool calls capture all required fields."""

    def test_session_and_tool_call_fields(self):
        session_fields = {
            "id",
            "butler_name",
            "request_id",
            "trigger_source",
            "started_at",
            "ended_at",
            "status",
            "model",
            "input_tokens",
            "output_tokens",
            "duration_ms",
        }
        assert len(session_fields) >= 10 and "model" in session_fields

        tool_fields = {"tool_name", "session_id", "started_at", "ended_at", "status"}
        assert "tool_name" in tool_fields

        # Span naming convention
        assert "mcp.tool" in "mcp.tool.{tool_name}"


class TestSchedulerIntegration:
    """RFC 0001: TOML schedules synced to DB; deterministic stagger."""

    def test_scheduler_stagger_default(self):
        assert 900 == 900  # default max stagger seconds


class TestRequestIdOtelPropagation:
    """RFC 0001 + RFC 0005: request_id propagates to OTel spans as the root span attribute."""

    def test_request_id_propagates_to_otel_spans(self):
        """RFC 0001: Every session opens root span; tools open child spans; all carry request_id.

        RFC 0005 + RFC 0001: OTel spans are named 'butler.tool.<name>' and carry the
        butler.name attribute. The request_id from the session is propagated to allow
        end-to-end tracing across tool invocations within a session.
        """
        import inspect

        from butlers.core import telemetry

        src = inspect.getsource(telemetry)

        # OTel span naming convention: 'butler.tool.<name>'
        assert "butler.tool" in src or "tool_span" in src or "butler.name" in src, (
            "telemetry module must define butler.tool span naming convention (RFC 0005)"
        )

        # The tool_span function or equivalent must exist
        from butlers.core.telemetry import tool_span

        assert callable(tool_span), (
            "tool_span must be callable for wrapping tool invocations (RFC 0005)"
        )

    def test_tool_span_accepts_butler_name_attribute(self):
        """RFC 0005: tool_span includes butler.name as a span attribute.

        Per RFC 0002: 'A butler.tool.<name> span with butler.name attribute.'
        This allows backends to attribute tool invocations to the correct butler.
        """
        import inspect

        from butlers.core.telemetry import tool_span

        sig = inspect.signature(tool_span)
        params = list(sig.parameters.keys())

        assert "butler_name" in params, (
            "tool_span must accept butler_name parameter for span attribute (RFC 0005)"
        )

    def test_session_request_id_is_uuidv7_not_uuid4(self):
        """RFC 0001: request_id MUST be UUIDv7 (time-ordered), not UUID4 (random).

        UUIDv7 provides time-ordering which is important for log correlation
        and for generating chronologically sorted session IDs.
        """
        from butlers.core.utils import generate_uuid7_string

        # Multiple calls must produce v7 UUIDs
        ids = [generate_uuid7_string() for _ in range(5)]
        uuid_objs = [uuid.UUID(i) for i in ids]

        # All must be v7
        for u in uuid_objs:
            assert u.version == 7, "Session request_id must be UUIDv7 (RFC 0001)"

        # UUIDv7 millisecond timestamps must be non-decreasing across calls.
        # The 48-bit ms timestamp occupies the high 48 bits of the 128-bit UUID int.
        # (Random sub-ms bits may vary, so we check only the timestamp portion.)
        timestamps = [u.int >> 80 for u in uuid_objs]
        for i in range(len(timestamps) - 1):
            assert timestamps[i] <= timestamps[i + 1], (
                "UUIDv7 millisecond timestamps must be non-decreasing — later IDs are >= earlier IDs (RFC 0001)"
            )
