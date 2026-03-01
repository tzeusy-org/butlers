"""Tests for the message classification and routing pipeline.

Tests cover:
- MessagePipeline.process() tool-based routing flow
- RoutingResult dataclass
- _extract_routed_butlers() helper
- _build_routing_prompt() helper
- Routing context lifecycle (set/clear)
- Fallback to general when CC fails or calls no tools
- Error handling
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.modules.pipeline import (
    MessagePipeline,
    PipelineConfig,
    PipelineModule,
    RoutingResult,
    _build_routing_prompt,
    _extract_routed_butlers,
    _routing_ctx_var,
)

pytestmark = pytest.mark.unit


def _pipeline_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.name == "butlers.modules.pipeline"]


# ---------------------------------------------------------------------------
# Fake SpawnerResult for mocking dispatch_fn
# ---------------------------------------------------------------------------


@dataclass
class FakeSpawnerResult:
    """Mimics SpawnerResult from butlers.core.spawner."""

    output: str | None = None
    success: bool = True
    tool_calls: list[dict] = field(default_factory=list)
    error: str | None = None


# ---------------------------------------------------------------------------
# RoutingResult
# ---------------------------------------------------------------------------


class TestRoutingResult:
    """Verify RoutingResult dataclass basics."""

    def test_default_fields(self):
        """RoutingResult has sensible defaults."""
        result = RoutingResult(target_butler="general")
        assert result.target_butler == "general"
        assert result.route_result == {}
        assert result.classification_error is None
        assert result.routing_error is None
        assert result.routed_targets == []
        assert result.acked_targets == []
        assert result.failed_targets == []

    def test_with_all_fields(self):
        """RoutingResult can be constructed with all fields."""
        result = RoutingResult(
            target_butler="health",
            route_result={"result": "ok"},
            classification_error=None,
            routing_error=None,
            routed_targets=["health"],
            acked_targets=["health"],
            failed_targets=[],
        )
        assert result.target_butler == "health"
        assert result.route_result == {"result": "ok"}
        assert result.routed_targets == ["health"]
        assert result.acked_targets == ["health"]
        assert result.failed_targets == []


# ---------------------------------------------------------------------------
# _extract_routed_butlers
# ---------------------------------------------------------------------------


class TestExtractRoutedButlers:
    """Verify tool_calls parsing logic."""

    def test_extracts_successful_route(self):
        tool_calls = [
            {
                "name": "route_to_butler",
                "args": {"butler": "health", "prompt": "track meds"},
                "result": {"status": "ok", "butler": "health"},
            }
        ]
        routed, acked, failed = _extract_routed_butlers(tool_calls)
        assert routed == ["health"]
        assert acked == ["health"]
        assert failed == []

    def test_extracts_failed_route(self):
        tool_calls = [
            {
                "name": "route_to_butler",
                "args": {"butler": "health", "prompt": "track meds"},
                "result": {"status": "error", "butler": "health", "error": "not found"},
            }
        ]
        routed, acked, failed = _extract_routed_butlers(tool_calls)
        assert routed == ["health"]
        assert acked == []
        assert failed == ["health"]

    def test_multi_target_routing(self):
        tool_calls = [
            {
                "name": "route_to_butler",
                "args": {"butler": "health", "prompt": "track meds"},
                "result": {"status": "ok", "butler": "health"},
            },
            {
                "name": "route_to_butler",
                "args": {"butler": "relationship", "prompt": "call Mom"},
                "result": {"status": "ok", "butler": "relationship"},
            },
        ]
        routed, acked, failed = _extract_routed_butlers(tool_calls)
        assert routed == ["health", "relationship"]
        assert acked == ["health", "relationship"]
        assert failed == []

    def test_ignores_non_route_tool_calls(self):
        tool_calls = [
            {
                "name": "state_get",
                "args": {"key": "foo"},
                "result": {"key": "foo", "value": "bar"},
            },
            {
                "name": "route_to_butler",
                "args": {"butler": "general", "prompt": "hello"},
                "result": {"status": "ok", "butler": "general"},
            },
        ]
        routed, acked, failed = _extract_routed_butlers(tool_calls)
        assert routed == ["general"]
        assert acked == ["general"]
        assert failed == []

    def test_empty_tool_calls(self):
        routed, acked, failed = _extract_routed_butlers([])
        assert routed == []
        assert acked == []
        assert failed == []

    def test_string_result_json_ok(self):
        tool_calls = [
            {
                "name": "route_to_butler",
                "args": {"butler": "health", "prompt": "x"},
                "result": '{"status": "ok", "butler": "health"}',
            }
        ]
        routed, acked, failed = _extract_routed_butlers(tool_calls)
        assert acked == ["health"]

    def test_string_result_json_error(self):
        tool_calls = [
            {
                "name": "route_to_butler",
                "args": {"butler": "health", "prompt": "x"},
                "result": '{"status": "error", "error": "fail"}',
            }
        ]
        routed, acked, failed = _extract_routed_butlers(tool_calls)
        assert failed == ["health"]

    def test_no_result_assumes_success(self):
        """If tool_call has no result key, assume success."""
        tool_calls = [
            {
                "name": "route_to_butler",
                "args": {"butler": "health", "prompt": "x"},
            }
        ]
        routed, acked, failed = _extract_routed_butlers(tool_calls)
        assert acked == ["health"]
        assert failed == []

    def test_mcp_namespaced_tool_name(self):
        """CC SDK returns MCP-namespaced names like mcp__switchboard__route_to_butler."""
        tool_calls = [
            {
                "name": "mcp__switchboard__route_to_butler",
                "input": {"butler": "health", "prompt": "track meds"},
            }
        ]
        routed, acked, failed = _extract_routed_butlers(tool_calls)
        assert routed == ["health"]
        assert acked == ["health"]
        assert failed == []

    def test_dotted_tool_name_and_string_arguments(self):
        """Dotted MCP tool names and stringified arguments are parsed."""
        tool_calls = [
            {
                "name": "switchboard.route_to_butler",
                "arguments": '{"target_butler":"relationship","prompt":"Store birthday"}',
            }
        ]
        routed, acked, failed = _extract_routed_butlers(tool_calls)
        assert routed == ["relationship"]
        assert acked == ["relationship"]
        assert failed == []

    def test_slashed_tool_name_and_params_key(self):
        """Slashed tool names and params payloads are parsed."""
        tool_calls = [
            {
                "name": "mcp/switchboard/route_to_butler",
                "params": {"butler_name": "health", "prompt": "Track meal"},
            }
        ]
        routed, acked, failed = _extract_routed_butlers(tool_calls)
        assert routed == ["health"]
        assert acked == ["health"]
        assert failed == []

    def test_input_key_instead_of_args(self):
        """CC SDK uses 'input' key; older code used 'args'."""
        tool_calls = [
            {
                "name": "route_to_butler",
                "input": {"butler": "general", "prompt": "hello"},
                "result": {"status": "ok", "butler": "general"},
            }
        ]
        routed, acked, failed = _extract_routed_butlers(tool_calls)
        assert routed == ["general"]
        assert acked == ["general"]
        assert failed == []

    def test_mcp_namespaced_with_result(self):
        """MCP-namespaced name with a result dict."""
        tool_calls = [
            {
                "name": "mcp__switchboard__route_to_butler",
                "input": {"butler": "health", "prompt": "x"},
                "result": {"status": "error", "butler": "health", "error": "fail"},
            }
        ]
        routed, acked, failed = _extract_routed_butlers(tool_calls)
        assert routed == ["health"]
        assert failed == ["health"]

    def test_mixed_success_and_failure(self):
        tool_calls = [
            {
                "name": "route_to_butler",
                "args": {"butler": "health", "prompt": "track"},
                "result": {"status": "ok", "butler": "health"},
            },
            {
                "name": "route_to_butler",
                "args": {"butler": "general", "prompt": "fallback"},
                "result": {"status": "error", "butler": "general", "error": "timeout"},
            },
        ]
        routed, acked, failed = _extract_routed_butlers(tool_calls)
        assert routed == ["health", "general"]
        assert acked == ["health"]
        assert failed == ["general"]

    def test_accepted_status_is_acked(self):
        """'accepted' status must be treated as a success, not a failure."""
        tool_calls = [
            {
                "name": "route_to_butler",
                "args": {"butler": "health", "prompt": "track meds"},
                "result": {"status": "accepted", "butler": "health"},
            }
        ]
        routed, acked, failed = _extract_routed_butlers(tool_calls)
        assert routed == ["health"]
        assert acked == ["health"]
        assert failed == []

    def test_accepted_status_string_json_is_acked(self):
        """'accepted' status in JSON-encoded string result is treated as success."""
        tool_calls = [
            {
                "name": "route_to_butler",
                "args": {"butler": "health", "prompt": "x"},
                "result": '{"status": "accepted", "butler": "health"}',
            }
        ]
        routed, acked, failed = _extract_routed_butlers(tool_calls)
        assert acked == ["health"]
        assert failed == []

    def test_mixed_ok_and_accepted_both_acked(self):
        """Both 'ok' and 'accepted' status values count as successful routes."""
        tool_calls = [
            {
                "name": "route_to_butler",
                "args": {"butler": "health", "prompt": "track"},
                "result": {"status": "ok", "butler": "health"},
            },
            {
                "name": "route_to_butler",
                "args": {"butler": "relationship", "prompt": "remind"},
                "result": {"status": "accepted", "butler": "relationship"},
            },
        ]
        routed, acked, failed = _extract_routed_butlers(tool_calls)
        assert routed == ["health", "relationship"]
        assert acked == ["health", "relationship"]
        assert failed == []

    def test_accepted_mcp_namespaced(self):
        """'accepted' status works with MCP-namespaced route_to_butler tool name."""
        tool_calls = [
            {
                "name": "mcp__switchboard__route_to_butler",
                "input": {"butler": "health", "prompt": "x"},
                "result": {"status": "accepted", "butler": "health"},
            }
        ]
        routed, acked, failed = _extract_routed_butlers(tool_calls)
        assert routed == ["health"]
        assert acked == ["health"]
        assert failed == []


# ---------------------------------------------------------------------------
# _build_routing_prompt
# ---------------------------------------------------------------------------


class TestBuildRoutingPrompt:
    """Verify routing prompt construction."""

    def test_includes_butler_names(self):
        butlers = [
            {"name": "health", "description": "Health tracking", "modules": ["health"]},
            {"name": "general", "description": "General assistant", "modules": []},
        ]
        prompt = _build_routing_prompt("I have a headache", butlers)
        assert "health" in prompt
        assert "general" in prompt
        assert "route_to_butler" in prompt

    def test_json_encodes_user_message(self):
        butlers = [{"name": "general", "description": "General", "modules": []}]
        prompt = _build_routing_prompt("test message", butlers)
        assert json.dumps({"message": "test message"}) in prompt

    def test_includes_routing_guidance(self):
        butlers = [
            {"name": "health", "description": "Health", "modules": ["health"]},
            {"name": "general", "description": "General", "modules": []},
        ]
        prompt = _build_routing_prompt("I ate chicken", butlers)
        assert "food" in prompt.lower() or "health" in prompt.lower()


# ---------------------------------------------------------------------------
# Routing context lifecycle
# ---------------------------------------------------------------------------


class TestRoutingContextLifecycle:
    """Verify per-task routing context (ContextVar) set/clear behavior."""

    def test_set_populates_context_var(self):
        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(),
            dispatch_fn=AsyncMock(),
        )
        pipeline._set_routing_context(
            source_metadata={"channel": "telegram"},
            request_context={"request_id": "abc"},
            request_id="req-1",
        )
        ctx = _routing_ctx_var.get()
        assert ctx is not None
        assert ctx["source_metadata"] == {"channel": "telegram"}
        assert ctx["request_context"] == {"request_id": "abc"}
        assert ctx["request_id"] == "req-1"
        # Cleanup
        _routing_ctx_var.set(None)

    def test_clear_sets_context_var_to_none(self):
        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(),
            dispatch_fn=AsyncMock(),
        )
        _routing_ctx_var.set({"source_metadata": {"channel": "telegram"}})
        pipeline._clear_routing_context()
        assert _routing_ctx_var.get() is None

    def test_set_does_not_raise(self):
        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(),
            dispatch_fn=AsyncMock(),
        )
        # Should not raise
        pipeline._set_routing_context(
            source_metadata={"channel": "telegram"},
            request_id="req-1",
        )
        # Cleanup
        _routing_ctx_var.set(None)

    def test_clear_does_not_raise_when_already_none(self):
        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(),
            dispatch_fn=AsyncMock(),
        )
        _routing_ctx_var.set(None)
        # Should not raise
        pipeline._clear_routing_context()

    def test_set_stores_conversation_history(self):
        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(),
            dispatch_fn=AsyncMock(),
        )
        history = "**user** (2026-02-16T10:00:00Z):\nHello"
        pipeline._set_routing_context(
            source_metadata={"channel": "telegram"},
            request_id="req-1",
            conversation_history=history,
        )
        ctx = _routing_ctx_var.get()
        assert ctx is not None
        assert ctx["conversation_history"] == history
        # Cleanup
        _routing_ctx_var.set(None)

    def test_set_stores_none_conversation_history_when_absent(self):
        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(),
            dispatch_fn=AsyncMock(),
        )
        pipeline._set_routing_context(
            source_metadata={"channel": "telegram"},
            request_id="req-1",
        )
        ctx = _routing_ctx_var.get()
        assert ctx is not None
        assert ctx["conversation_history"] is None
        # Cleanup
        _routing_ctx_var.set(None)


class TestRequestIdCoercion:
    """Verify request_id normalization for route.v1 UUID7 constraints."""

    def test_coerce_request_id_generates_uuid7_for_invalid_value(self):
        request_id = MessagePipeline._coerce_request_id("unknown")
        parsed = uuid.UUID(request_id)
        assert parsed.version == 7

    def test_coerce_request_id_converts_non_v7_to_uuid7(self):
        request_id = MessagePipeline._coerce_request_id("123e4567-e89b-42d3-a456-426614174000")
        parsed = uuid.UUID(request_id)
        assert parsed.version == 7

    def test_coerce_request_id_preserves_uuid7(self):
        request_id = MessagePipeline._coerce_request_id("018f52f3-9d8a-7ef2-8f2d-9fb6b32f12aa")
        assert request_id == "018f52f3-9d8a-7ef2-8f2d-9fb6b32f12aa"


# ---------------------------------------------------------------------------
# MessagePipeline.process — tool-based routing
# ---------------------------------------------------------------------------

_MOCK_BUTLERS = [
    {"name": "health", "description": "Health tracking", "modules": ["health"]},
    {"name": "relationship", "description": "Relationships", "modules": ["relationship"]},
    {"name": "general", "description": "General assistant", "modules": []},
]


class TestMessagePipelineProcess:
    """Verify the tool-based routing flow."""

    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    async def test_single_target_routing(self, mock_load):
        """Pipeline routes to a single butler via tool call."""
        mock_pool = MagicMock()

        async def mock_dispatch(**kwargs):
            return FakeSpawnerResult(
                output="Routed to health butler for medication tracking.",
                tool_calls=[
                    {
                        "name": "route_to_butler",
                        "args": {"butler": "health", "prompt": "Track my headache"},
                        "result": {"status": "ok", "butler": "health"},
                    }
                ],
            )

        pipeline = MessagePipeline(
            switchboard_pool=mock_pool,
            dispatch_fn=mock_dispatch,
            source_butler="switchboard",
        )

        result = await pipeline.process("I have a headache")

        assert result.target_butler == "health"
        assert result.classification_error is None
        assert result.routing_error is None
        assert result.routed_targets == ["health"]
        assert result.acked_targets == ["health"]
        assert result.failed_targets == []
        assert "Routed to health" in result.route_result["cc_summary"]

    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    async def test_multi_target_routing(self, mock_load):
        """Pipeline routes to multiple butlers via tool calls."""

        async def mock_dispatch(**kwargs):
            return FakeSpawnerResult(
                output="Routed medication to health, reminder to relationship.",
                tool_calls=[
                    {
                        "name": "route_to_butler",
                        "args": {"butler": "health", "prompt": "Track metformin"},
                        "result": {"status": "ok", "butler": "health"},
                    },
                    {
                        "name": "route_to_butler",
                        "args": {"butler": "relationship", "prompt": "Send thank-you card"},
                        "result": {"status": "ok", "butler": "relationship"},
                    },
                ],
            )

        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(),
            dispatch_fn=mock_dispatch,
        )

        result = await pipeline.process("Got prescribed metformin. Send Dr. Smith a card.")

        assert result.target_butler == "multi"
        assert result.routed_targets == ["health", "relationship"]
        assert result.acked_targets == ["health", "relationship"]
        assert result.failed_targets == []
        assert result.routing_error is None

    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    async def test_partial_failure_in_multi_route(self, mock_load):
        """Failed tool calls are tracked in failed_targets."""

        async def mock_dispatch(**kwargs):
            return FakeSpawnerResult(
                output="Routed to health and general.",
                tool_calls=[
                    {
                        "name": "route_to_butler",
                        "args": {"butler": "health", "prompt": "Track this"},
                        "result": {"status": "ok", "butler": "health"},
                    },
                    {
                        "name": "route_to_butler",
                        "args": {"butler": "general", "prompt": "Handle this"},
                        "result": {
                            "status": "error",
                            "butler": "general",
                            "error": "timeout",
                        },
                    },
                ],
            )

        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(),
            dispatch_fn=mock_dispatch,
        )

        result = await pipeline.process("test fanout")

        assert result.target_butler == "multi"
        assert result.routed_targets == ["health", "general"]
        assert result.acked_targets == ["health"]
        assert result.failed_targets == ["general"]
        assert result.routing_error is not None

    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    @patch("butlers.tools.switchboard.routing.route.route", new_callable=AsyncMock)
    async def test_fallback_to_general_when_no_tool_calls(self, mock_route, mock_load):
        """When CC calls no tools, pipeline falls back to routing to general."""
        mock_route.return_value = {"result": "handled by general"}

        async def mock_dispatch(**kwargs):
            return FakeSpawnerResult(
                output="I'm not sure where to route this.",
                tool_calls=[],
            )

        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(),
            dispatch_fn=mock_dispatch,
        )

        result = await pipeline.process("What's the weather?")

        assert result.routed_targets == ["general"]
        assert result.acked_targets == ["general"]
        assert result.failed_targets == []
        mock_route.assert_awaited_once()

    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    @patch("butlers.tools.switchboard.routing.route.route", new_callable=AsyncMock)
    async def test_fallback_uses_cc_indicated_target_when_unambiguous(self, mock_route, mock_load):
        """No-tool fallback uses explicit 'routed to <butler>' summary when present."""
        mock_route.return_value = {"result": "handled by relationship"}

        async def mock_dispatch(**kwargs):
            return FakeSpawnerResult(
                output="Routed to `relationship` for contact details.",
                tool_calls=[],
            )

        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(),
            dispatch_fn=mock_dispatch,
        )

        result = await pipeline.process("Phoebe was born on 1997-01-22")

        assert result.routed_targets == ["relationship"]
        assert result.acked_targets == ["relationship"]
        assert result.failed_targets == []
        assert mock_route.await_args.kwargs["target_butler"] == "relationship"

    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    async def test_cc_spawn_failure_returns_general(self, mock_load):
        """When runtime spawn fails entirely, pipeline returns general with error."""

        async def failing_dispatch(**kwargs):
            raise RuntimeError("spawner broke")

        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(),
            dispatch_fn=failing_dispatch,
        )

        result = await pipeline.process("test message")

        assert result.target_butler == "general"
        assert result.classification_error is not None
        assert "RuntimeError" in result.classification_error

    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    async def test_routing_context_set_before_and_cleared_after_spawn(self, mock_load):
        """Routing context is populated in ContextVar before runtime spawn and cleared after."""
        captured_ctx_during_spawn: dict[str, Any] | None = None

        async def mock_dispatch(**kwargs):
            nonlocal captured_ctx_during_spawn
            captured_ctx_during_spawn = _routing_ctx_var.get()
            return FakeSpawnerResult(
                output="Routed to general.",
                tool_calls=[
                    {
                        "name": "route_to_butler",
                        "args": {"butler": "general", "prompt": "hello"},
                        "result": {"status": "ok", "butler": "general"},
                    }
                ],
            )

        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(),
            dispatch_fn=mock_dispatch,
        )

        await pipeline.process("hello")

        # Context was populated during spawn
        assert captured_ctx_during_spawn is not None
        assert "source_metadata" in captured_ctx_during_spawn
        assert "request_id" in captured_ctx_during_spawn
        # Context is cleared after spawn
        assert _routing_ctx_var.get() is None

    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    async def test_routing_context_cleared_even_on_error(self, mock_load):
        """Routing context ContextVar is reset even when runtime spawn fails."""

        async def failing_dispatch(**kwargs):
            raise RuntimeError("spawn failed")

        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(),
            dispatch_fn=failing_dispatch,
        )

        await pipeline.process("test")

        assert _routing_ctx_var.get() is None

    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    async def test_concurrent_sessions_get_isolated_routing_context(self, mock_load):
        """Concurrent pipeline.process() calls each see their own isolated request_id."""
        # Two sessions with different request_ids running concurrently.
        # Each session captures its own ContextVar snapshot during dispatch.
        # Acceptance criteria: each session reads its own request_id, not the other's.

        captured: dict[str, str] = {}
        barrier = asyncio.Event()

        async def dispatch_session_a(**kwargs):
            # Wait until session B has also set its context, then read ours
            barrier.set()
            await asyncio.sleep(0)  # yield so B can run
            captured["a"] = (_routing_ctx_var.get() or {}).get("request_id", "")
            return FakeSpawnerResult(
                output="ok",
                tool_calls=[
                    {
                        "name": "route_to_butler",
                        "args": {"butler": "health", "prompt": "session-a"},
                        "result": {"status": "ok", "butler": "health"},
                    }
                ],
            )

        async def dispatch_session_b(**kwargs):
            await barrier.wait()  # wait until A has started
            captured["b"] = (_routing_ctx_var.get() or {}).get("request_id", "")
            return FakeSpawnerResult(
                output="ok",
                tool_calls=[
                    {
                        "name": "route_to_butler",
                        "args": {"butler": "relationship", "prompt": "session-b"},
                        "result": {"status": "ok", "butler": "relationship"},
                    }
                ],
            )

        pipeline_a = MessagePipeline(
            switchboard_pool=MagicMock(),
            dispatch_fn=dispatch_session_a,
        )
        pipeline_b = MessagePipeline(
            switchboard_pool=MagicMock(),
            dispatch_fn=dispatch_session_b,
        )

        task_a = asyncio.create_task(
            pipeline_a.process("session-a message", tool_args={"request_id": "req-aaa"})
        )
        task_b = asyncio.create_task(
            pipeline_b.process("session-b message", tool_args={"request_id": "req-bbb"})
        )
        await asyncio.gather(task_a, task_b)

        # Each task's ContextVar copy was independent — no cross-contamination
        assert captured["a"] != captured["b"], (
            f"Context leaked between tasks: a={captured['a']!r}, b={captured['b']!r}"
        )
        # ContextVar is cleared after each session
        assert _routing_ctx_var.get() is None

    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    async def test_logs_entry_and_exit_with_structured_fields(
        self, mock_load, caplog: pytest.LogCaptureFixture
    ):
        """Pipeline emits structured start/end logs with context and latency."""

        async def mock_dispatch(**kwargs):
            return FakeSpawnerResult(
                output="Routed to health.",
                tool_calls=[
                    {
                        "name": "route_to_butler",
                        "args": {"butler": "health", "prompt": "headache"},
                        "result": {"status": "ok", "butler": "health"},
                    }
                ],
            )

        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(),
            dispatch_fn=mock_dispatch,
        )

        message = "I have a headache and feel dizzy."
        with caplog.at_level(logging.INFO, logger="butlers.modules.pipeline"):
            await pipeline.process(message, tool_args={"source": "telegram", "chat_id": "42"})

        records = _pipeline_records(caplog)
        assert records
        for record in records:
            assert hasattr(record, "source")
            assert hasattr(record, "chat_id")
            assert hasattr(record, "target_butler")
            assert hasattr(record, "latency_ms")

        start = next(r for r in records if r.getMessage() == "Pipeline processing message")
        end = next(r for r in records if r.getMessage() == "Pipeline routed message")
        assert start.source == "telegram"
        assert start.chat_id == "42"
        assert start.message_length == len(message)
        assert start.message_preview == message
        assert end.target_butler == "health"
        assert end.latency_ms >= 0
        assert end.classification_latency_ms >= 0
        assert end.routing_latency_ms >= 0

    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    async def test_classification_fallback_logs_warning_with_reason(
        self, mock_load, caplog: pytest.LogCaptureFixture
    ):
        """Classification fallback emits warning with error reason."""

        async def failing_dispatch(**kwargs):
            raise RuntimeError("classifier broke")

        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(),
            dispatch_fn=failing_dispatch,
        )

        with caplog.at_level(logging.INFO, logger="butlers.modules.pipeline"):
            result = await pipeline.process(
                "test message",
                tool_args={"source": "telegram", "chat_id": 7},
            )

        assert result.target_butler == "general"

        warning = next(
            r
            for r in _pipeline_records(caplog)
            if r.levelno == logging.WARNING
            and r.getMessage() == "Classification failed; falling back to general"
        )
        assert warning.source == "telegram"
        assert warning.chat_id == "7"
        assert warning.target_butler == "general"
        assert warning.latency_ms >= 0
        assert "RuntimeError: classifier broke" in warning.classification_error

    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    async def test_cc_returns_none(self, mock_load):
        """Pipeline handles CC returning None gracefully (falls back to general)."""

        async def mock_dispatch(**kwargs):
            return None

        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(),
            dispatch_fn=mock_dispatch,
        )

        with patch(
            "butlers.tools.switchboard.routing.route.route",
            new_callable=AsyncMock,
            return_value={"result": "ok"},
        ):
            result = await pipeline.process("hello")

        assert result.routed_targets == ["general"]
        assert result.acked_targets == ["general"]


# ---------------------------------------------------------------------------
# Ingress deduplication (unchanged — tests dedupe logic, not routing flow)
# ---------------------------------------------------------------------------


class _FakeAcquire:
    def __init__(self, conn: _FakeIngressConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeIngressConn:
        return self._conn

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class _FakeIngressConn:
    def __init__(self) -> None:
        self.request_by_key: dict[str, uuid.UUID] = {}
        self.dedupe_keys_seen: list[str] = []

    async def fetchrow(self, _query: str, *params: Any) -> dict[str, Any]:
        # params[1] is the request_context JSON string ($2 in INSERT query).
        # The dedupe_key is embedded there as request_context['dedupe_key'].
        request_context = json.loads(params[1])
        dedupe_key = str(request_context["dedupe_key"])
        self.dedupe_keys_seen.append(dedupe_key)
        if dedupe_key in self.request_by_key:
            return {
                "request_id": self.request_by_key[dedupe_key],
                "inserted": False,
            }

        request_id = uuid.uuid4()
        self.request_by_key[dedupe_key] = request_id
        return {"request_id": request_id, "inserted": True}

    async def execute(self, _query: str, *args: Any) -> str:
        return "UPDATE 1"


class _FakeIngressPool:
    def __init__(self) -> None:
        self.conn = _FakeIngressConn()

    def acquire(self) -> _FakeAcquire:
        return _FakeAcquire(self.conn)


class TestIngressDeduplication:
    def test_telegram_key_uses_update_id_and_endpoint_identity(self):
        dedupe_key, strategy, idempotency_key = MessagePipeline._build_dedupe_record(
            args={
                "source_endpoint_identity": "telegram:bot-main",
                "external_event_id": "4321",
            },
            source_metadata={"channel": "telegram", "identity": "bot", "tool_name": "tool"},
            message_text="hello",
            received_at=datetime(2026, 2, 13, 12, 0, tzinfo=UTC),
        )

        assert dedupe_key == "telegram:bot-main:update:4321"
        assert strategy == "telegram_update_id_endpoint"
        assert idempotency_key is None

    def test_email_key_uses_message_id_and_mailbox_identity(self):
        dedupe_key, strategy, idempotency_key = MessagePipeline._build_dedupe_record(
            args={
                "source_endpoint_identity": "bot:inbox@example.com",
                "external_event_id": "<abc123@example.com>",
            },
            source_metadata={"channel": "email", "identity": "bot", "tool_name": "tool"},
            message_text="hello",
            received_at=datetime(2026, 2, 13, 12, 0, tzinfo=UTC),
        )

        assert dedupe_key == "email:bot:inbox@example.com:message_id:<abc123@example.com>"
        assert strategy == "email_message_id_endpoint"
        assert idempotency_key is None

    def test_api_key_prefers_caller_idempotency_key(self):
        dedupe_key, strategy, idempotency_key = MessagePipeline._build_dedupe_record(
            args={
                "source_endpoint_identity": "api:client-a",
                "idempotency_key": "idem-123",
            },
            source_metadata={"channel": "api", "identity": "client", "tool_name": "tool"},
            message_text="hello",
            received_at=datetime(2026, 2, 13, 12, 0, tzinfo=UTC),
        )

        assert dedupe_key == "api:client-a:idempotency:idem-123"
        assert strategy == "api_idempotency_key_endpoint"
        assert idempotency_key == "idem-123"

    def test_mcp_key_falls_back_to_payload_hash_plus_bounded_window(self):
        dedupe_key, strategy, idempotency_key = MessagePipeline._build_dedupe_record(
            args={
                "source_endpoint_identity": "mcp:caller-a",
                "sender_identity": "caller-user",
            },
            source_metadata={"channel": "mcp", "identity": "caller", "tool_name": "tool"},
            message_text="hello",
            received_at=datetime(2026, 2, 13, 12, 7, tzinfo=UTC),
        )

        assert dedupe_key.startswith("mcp:caller-a:payload_hash:")
        assert dedupe_key.endswith(":window:2026-02-13T12:05:00+00:00")
        assert strategy == "mcp_payload_hash_endpoint_window"
        assert idempotency_key is None

    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    async def test_duplicate_telegram_replay_maps_to_existing_request(self, mock_load, caplog):
        pool = _FakeIngressPool()

        async def mock_dispatch(**kwargs):
            return FakeSpawnerResult(
                output="Routed to general.",
                tool_calls=[
                    {
                        "name": "route_to_butler",
                        "args": {"butler": "general", "prompt": "hello"},
                        "result": {"status": "ok", "butler": "general"},
                    }
                ],
            )

        pipeline = MessagePipeline(
            switchboard_pool=pool,
            dispatch_fn=mock_dispatch,
            enable_ingress_dedupe=True,
        )

        tool_args = {
            "source": "telegram",
            "source_channel": "telegram",
            "source_endpoint_identity": "telegram:bot-main",
            "external_event_id": "99",
            "chat_id": "7",
            "source_id": "update:99",
        }

        with caplog.at_level(logging.INFO, logger="butlers.modules.pipeline"):
            first = await pipeline.process("hello", tool_args=tool_args)
            second = await pipeline.process("hello", tool_args=tool_args)

        assert first.target_butler == "general"
        assert second.target_butler == "deduped"
        assert second.route_result["ingress_decision"] == "deduped"

        dedupe_key = "telegram:bot-main:update:99"
        request_id = pool.conn.request_by_key[dedupe_key]
        assert second.route_result["request_id"] == str(request_id)

        decisions = [
            getattr(record, "ingress_decision", None)
            for record in _pipeline_records(caplog)
            if record.getMessage() == "Ingress dedupe decision"
        ]
        assert decisions == ["accepted", "deduped"]

    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    async def test_duplicate_email_replay_maps_to_existing_request(self, mock_load):
        pool = _FakeIngressPool()

        dispatch_call_count = 0

        async def mock_dispatch(**kwargs):
            nonlocal dispatch_call_count
            dispatch_call_count += 1
            return FakeSpawnerResult(
                output="Routed to general.",
                tool_calls=[
                    {
                        "name": "route_to_butler",
                        "args": {"butler": "general", "prompt": "Subject: hello"},
                        "result": {"status": "ok", "butler": "general"},
                    }
                ],
            )

        pipeline = MessagePipeline(
            switchboard_pool=pool,
            dispatch_fn=mock_dispatch,
            enable_ingress_dedupe=True,
        )

        tool_args = {
            "source": "email",
            "source_channel": "email",
            "source_endpoint_identity": "bot:inbox@example.com",
            "external_event_id": "<msg-42@example.com>",
            "from": "sender@example.com",
            "source_id": "<msg-42@example.com>",
        }

        first = await pipeline.process("Subject: hello", tool_args=tool_args)
        second = await pipeline.process("Subject: hello", tool_args=tool_args)

        assert first.target_butler == "general"
        assert second.target_butler == "deduped"
        assert second.route_result["ingress_decision"] == "deduped"
        assert dispatch_call_count == 1


"""Test attachment context in routing prompts."""


def test_build_routing_prompt_without_attachments():
    """Test that routing prompt works without attachments (backward compatibility)."""
    butlers = [
        {"name": "health", "description": "Health tracking", "capabilities": []},
        {"name": "general", "description": "General assistant", "capabilities": []},
    ]

    prompt = _build_routing_prompt(
        message="Track my medication",
        butlers=butlers,
        conversation_history="",
        attachments=None,
    )

    assert "Track my medication" in prompt
    assert "## Attachments" not in prompt
    assert "get_attachment" not in prompt


def test_build_routing_prompt_with_single_attachment():
    """Test that routing prompt includes attachment context for single attachment."""
    butlers = [
        {"name": "health", "description": "Health tracking", "capabilities": []},
        {"name": "general", "description": "General assistant", "capabilities": []},
    ]

    attachments = [
        {
            "media_type": "image/jpeg",
            "storage_ref": "local://2026/02/16/test-photo.jpg",
            "size_bytes": 245760,  # 240 KB
            "filename": "blood-test.jpg",
        }
    ]

    prompt = _build_routing_prompt(
        message="Here are my blood test results",
        butlers=butlers,
        conversation_history="",
        attachments=attachments,
    )

    assert "Here are my blood test results" in prompt
    assert "## Attachments" in prompt
    assert "This message includes 1 attachment(s):" in prompt
    assert "blood-test.jpg" in prompt
    assert "image/jpeg" in prompt
    assert "240.0KB" in prompt
    assert "local://2026/02/16/test-photo.jpg" in prompt
    assert "get_attachment(storage_ref)" in prompt


def test_build_routing_prompt_with_multiple_attachments():
    """Test that routing prompt includes all attachments."""
    butlers = [
        {"name": "health", "description": "Health tracking", "capabilities": []},
    ]

    attachments = [
        {
            "media_type": "image/jpeg",
            "storage_ref": "local://2026/02/16/photo1.jpg",
            "size_bytes": 100000,
            "filename": "xray.jpg",
        },
        {
            "media_type": "application/pdf",
            "storage_ref": "local://2026/02/16/report.pdf",
            "size_bytes": 500000,
            "filename": "lab-report.pdf",
        },
    ]

    prompt = _build_routing_prompt(
        message="Medical records",
        butlers=butlers,
        conversation_history="",
        attachments=attachments,
    )

    assert "## Attachments" in prompt
    assert "This message includes 2 attachment(s):" in prompt
    assert "xray.jpg" in prompt
    assert "lab-report.pdf" in prompt
    assert "image/jpeg" in prompt
    assert "application/pdf" in prompt


def test_build_routing_prompt_with_attachment_no_filename():
    """Test attachment context when filename is not provided."""
    butlers = [{"name": "general", "description": "General assistant", "capabilities": []}]

    attachments = [
        {
            "media_type": "image/png",
            "storage_ref": "local://2026/02/16/unnamed.png",
            "size_bytes": 50000,
        }
    ]

    prompt = _build_routing_prompt(
        message="Check this image",
        butlers=butlers,
        conversation_history="",
        attachments=attachments,
    )

    assert "## Attachments" in prompt
    assert "image/png" in prompt
    assert "48.8KB" in prompt  # 50000 / 1024
    assert "local://2026/02/16/unnamed.png" in prompt


def test_build_routing_prompt_empty_attachments_list():
    """Test that empty attachments list is treated as no attachments."""
    butlers = [{"name": "general", "description": "General assistant", "capabilities": []}]

    prompt = _build_routing_prompt(
        message="Hello",
        butlers=butlers,
        conversation_history="",
        attachments=[],
    )

    # Empty list should not show attachment section
    assert "## Attachments" not in prompt


# ---------------------------------------------------------------------------
# PipelineModule — Module ABC implementation tests
# ---------------------------------------------------------------------------


class TestPipelineModule:
    """Unit tests for the PipelineModule Module ABC implementation."""

    def test_name_property(self):
        """PipelineModule.name returns 'pipeline'."""
        mod = PipelineModule()
        assert mod.name == "pipeline"

    def test_config_schema_property(self):
        """PipelineModule.config_schema returns PipelineConfig."""
        mod = PipelineModule()
        assert mod.config_schema is PipelineConfig

    def test_dependencies_property(self):
        """PipelineModule declares no dependencies."""
        mod = PipelineModule()
        assert mod.dependencies == []

    def test_migration_revisions_returns_none(self):
        """PipelineModule has no module-specific migrations."""
        mod = PipelineModule()
        assert mod.migration_revisions() is None

    async def test_on_startup_accepts_none_db(self):
        """on_startup does not raise when db is None."""
        mod = PipelineModule()
        await mod.on_startup(config=None, db=None)
        assert mod._pool is None

    async def test_on_startup_accepts_pydantic_config(self):
        """on_startup accepts a PipelineConfig instance directly."""
        mod = PipelineModule()
        cfg = PipelineConfig(enable_ingress_dedupe=False)
        await mod.on_startup(config=cfg, db=None)
        assert mod._config.enable_ingress_dedupe is False

    async def test_on_startup_accepts_dict_config(self):
        """on_startup accepts a raw dict and coerces it to PipelineConfig."""
        mod = PipelineModule()
        await mod.on_startup(config={"enable_ingress_dedupe": False}, db=None)
        assert mod._config.enable_ingress_dedupe is False

    async def test_on_startup_caches_pool(self):
        """on_startup caches db.pool for later use."""
        mod = PipelineModule()
        fake_pool = object()
        fake_db = MagicMock()
        fake_db.pool = fake_pool
        await mod.on_startup(config=None, db=fake_db)
        assert mod._pool is fake_pool

    async def test_on_shutdown_clears_pipeline_and_pool(self):
        """on_shutdown releases pipeline and pool references."""
        mod = PipelineModule()
        mod._pipeline = MagicMock()
        mod._pool = MagicMock()
        await mod.on_shutdown()
        assert mod._pipeline is None
        assert mod._pool is None

    def test_set_pipeline_stores_instance(self):
        """set_pipeline stores the MessagePipeline reference."""
        mod = PipelineModule()
        fake_pipeline = MagicMock(spec=MessagePipeline)
        mod.set_pipeline(fake_pipeline)
        assert mod._pipeline is fake_pipeline

    async def test_register_tools_registers_pipeline_process(self):
        """register_tools registers the 'pipeline.process' MCP tool."""
        mod = PipelineModule()
        registered: dict[str, Any] = {}

        class FakeMCP:
            def tool(self, name: str):
                def decorator(fn):
                    registered[name] = fn
                    return fn

                return decorator

        await mod.register_tools(mcp=FakeMCP(), config=None, db=None)
        assert "pipeline.process" in registered

    async def test_pipeline_process_tool_returns_error_when_no_pipeline(self):
        """pipeline.process tool returns an error dict when no pipeline is set."""
        mod = PipelineModule()
        registered: dict[str, Any] = {}

        class FakeMCP:
            def tool(self, name: str):
                def decorator(fn):
                    registered[name] = fn
                    return fn

                return decorator

        await mod.register_tools(mcp=FakeMCP(), config=None, db=None)
        tool_fn = registered["pipeline.process"]
        result = await tool_fn(message_text="hello")
        assert result["error"] == "pipeline_not_configured"
        assert "message" in result

    async def test_pipeline_process_tool_delegates_to_pipeline(self):
        """pipeline.process tool calls pipeline.process() and returns a serialised result."""
        mod = PipelineModule()
        registered: dict[str, Any] = {}

        class FakeMCP:
            def tool(self, name: str):
                def decorator(fn):
                    registered[name] = fn
                    return fn

                return decorator

        await mod.register_tools(mcp=FakeMCP(), config=None, db=None)

        fake_result = RoutingResult(
            target_butler="health",
            route_result={"cc_summary": "Routed to health."},
            routed_targets=["health"],
            acked_targets=["health"],
            failed_targets=[],
        )
        fake_pipeline = AsyncMock(spec=MessagePipeline)
        fake_pipeline.process.return_value = fake_result
        mod.set_pipeline(fake_pipeline)

        tool_fn = registered["pipeline.process"]
        result = await tool_fn(
            message_text="I need to track my medication",
            source_channel="mcp",
            source_identity="caller-1",
            request_id="",
        )

        assert result["target_butler"] == "health"
        assert result["routed_targets"] == ["health"]
        assert result["acked_targets"] == ["health"]
        assert result["failed_targets"] == []
        assert result["classification_error"] is None
        assert result["routing_error"] is None

        fake_pipeline.process.assert_awaited_once()
        call_kwargs = fake_pipeline.process.call_args
        assert call_kwargs.kwargs["message_text"] == "I need to track my medication"

    async def test_pipeline_process_tool_passes_source_channel(self):
        """pipeline.process tool passes source_channel to the underlying pipeline."""
        mod = PipelineModule()
        registered: dict[str, Any] = {}

        class FakeMCP:
            def tool(self, name: str):
                def decorator(fn):
                    registered[name] = fn
                    return fn

                return decorator

        await mod.register_tools(mcp=FakeMCP(), config=None, db=None)

        fake_result = RoutingResult(target_butler="general")
        fake_pipeline = AsyncMock(spec=MessagePipeline)
        fake_pipeline.process.return_value = fake_result
        mod.set_pipeline(fake_pipeline)

        tool_fn = registered["pipeline.process"]
        await tool_fn(
            message_text="hello",
            source_channel="telegram",
            source_identity="bot-main",
        )

        _, call_kwargs = fake_pipeline.process.call_args
        tool_args = call_kwargs.get("tool_args", {})
        assert tool_args.get("source_channel") == "telegram"
        assert tool_args.get("source_identity") == "bot-main"

    async def test_register_tools_accepts_dict_config(self):
        """register_tools coerces a raw dict config to PipelineConfig."""
        mod = PipelineModule()

        class FakeMCP:
            def tool(self, name: str):
                def decorator(fn):
                    return fn

                return decorator

        await mod.register_tools(mcp=FakeMCP(), config={"enable_ingress_dedupe": False}, db=None)
        assert mod._config.enable_ingress_dedupe is False


class TestPipelineConfig:
    """Unit tests for PipelineConfig defaults and validation."""

    def test_default_config(self):
        """PipelineConfig has sensible defaults."""
        cfg = PipelineConfig()
        assert cfg.enable_ingress_dedupe is True

    def test_custom_config(self):
        """PipelineConfig can be overridden."""
        cfg = PipelineConfig(enable_ingress_dedupe=False)
        assert cfg.enable_ingress_dedupe is False

    def test_from_dict(self):
        """PipelineConfig can be constructed from a dict."""
        cfg = PipelineConfig(**{"enable_ingress_dedupe": False})
        assert cfg.enable_ingress_dedupe is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
