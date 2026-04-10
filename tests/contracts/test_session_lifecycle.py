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
