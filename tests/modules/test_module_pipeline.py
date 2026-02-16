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
    RoutingResult,
    _build_routing_prompt,
    _extract_routed_butlers,
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
    """Verify shared routing context set/clear behavior."""

    def test_set_populates_dict(self):
        ctx: dict[str, Any] = {}
        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(),
            dispatch_fn=AsyncMock(),
            routing_session_ctx=ctx,
        )
        pipeline._set_routing_context(
            source_metadata={"channel": "telegram"},
            request_context={"request_id": "abc"},
            request_id="req-1",
        )
        assert ctx["source_metadata"] == {"channel": "telegram"}
        assert ctx["request_context"] == {"request_id": "abc"}
        assert ctx["request_id"] == "req-1"

    def test_clear_empties_dict(self):
        ctx: dict[str, Any] = {"source_metadata": {"channel": "telegram"}}
        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(),
            dispatch_fn=AsyncMock(),
            routing_session_ctx=ctx,
        )
        pipeline._clear_routing_context()
        assert ctx == {}

    def test_set_noop_when_no_ctx(self):
        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(),
            dispatch_fn=AsyncMock(),
        )
        # Should not raise
        pipeline._set_routing_context(
            source_metadata={"channel": "telegram"},
            request_id="req-1",
        )

    def test_clear_noop_when_no_ctx(self):
        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(),
            dispatch_fn=AsyncMock(),
        )
        # Should not raise
        pipeline._clear_routing_context()


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
    async def test_cc_spawn_failure_returns_general(self, mock_load):
        """When CC spawn fails entirely, pipeline returns general with error."""

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
        """Routing context is populated before CC spawn and cleared after."""
        ctx: dict[str, Any] = {}
        captured_ctx_during_spawn: dict[str, Any] = {}

        async def mock_dispatch(**kwargs):
            captured_ctx_during_spawn.update(ctx)
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
            routing_session_ctx=ctx,
        )

        await pipeline.process("hello")

        # Context was populated during spawn
        assert "source_metadata" in captured_ctx_during_spawn
        assert "request_id" in captured_ctx_during_spawn
        # Context is cleared after spawn
        assert ctx == {}

    @patch(
        "butlers.tools.switchboard.routing.classify._load_available_butlers",
        new_callable=AsyncMock,
        return_value=_MOCK_BUTLERS,
    )
    async def test_routing_context_cleared_even_on_error(self, mock_load):
        """Routing context is cleared even when CC spawn fails."""
        ctx: dict[str, Any] = {}

        async def failing_dispatch(**kwargs):
            raise RuntimeError("spawn failed")

        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(),
            dispatch_fn=failing_dispatch,
            routing_session_ctx=ctx,
        )

        await pipeline.process("test")

        assert ctx == {}

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
        dedupe_key = str(params[9])
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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
