"""Tests for the message classification and routing pipeline.

Tests cover:
- MessagePipeline.process() classify + route flow
- RoutingResult dataclass
- Error handling for classification and routing failures
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.modules.pipeline import MessagePipeline, RoutingResult

pytestmark = pytest.mark.unit


def _pipeline_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.name == "butlers.modules.pipeline"]


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
# MessagePipeline.process
# ---------------------------------------------------------------------------


class TestMessagePipelineProcess:
    """Verify the classify-then-route flow."""

    async def test_classifies_and_routes_successfully(self):
        """Pipeline classifies the message and routes to the classified butler."""
        mock_pool = MagicMock()

        async def mock_classify(pool, message, dispatch_fn):
            return "health"

        async def mock_route(pool, target, tool_name, args, source):
            return {"result": "handled"}

        async def mock_dispatch(**kwargs):
            pass

        pipeline = MessagePipeline(
            switchboard_pool=mock_pool,
            dispatch_fn=mock_dispatch,
            source_butler="telegram-butler",
            classify_fn=mock_classify,
            route_fn=mock_route,
        )

        result = await pipeline.process("I have a headache")

        assert result.target_butler == "health"
        assert result.route_result == {"result": "handled"}
        assert result.classification_error is None
        assert result.routing_error is None
        assert result.routed_targets == ["health"]
        assert result.acked_targets == ["health"]
        assert result.failed_targets == []

    async def test_passes_message_as_tool_arg(self):
        """Pipeline includes message text in the tool_args sent to route."""
        captured_args: dict = {}

        async def mock_classify(pool, message, dispatch_fn):
            return "general"

        async def mock_route(pool, target, tool_name, args, source):
            captured_args.update(args)
            return {"result": "ok"}

        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(),
            dispatch_fn=AsyncMock(),
            classify_fn=mock_classify,
            route_fn=mock_route,
        )

        await pipeline.process("hello world", tool_args={"extra": "data"})

        assert captured_args["message"] == "hello world"
        assert captured_args["extra"] == "data"

    async def test_enriches_route_args_with_source_metadata(self):
        """Pipeline routes include identity-aware source metadata."""
        captured_args: dict[str, Any] = {}

        async def mock_classify(pool, message, dispatch_fn):
            return "general"

        async def mock_route(pool, target, tool_name, args, source):
            captured_args.update(args)
            return {"result": "ok"}

        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(),
            dispatch_fn=AsyncMock(),
            classify_fn=mock_classify,
            route_fn=mock_route,
        )

        await pipeline.process(
            "hello",
            tool_name="bot_telegram_handle_message",
            tool_args={
                "source": "telegram",
                "source_identity": "bot",
                "source_tool": "bot_telegram_get_updates",
                "source_id": "msg-7",
            },
        )

        assert captured_args["source_channel"] == "telegram"
        assert captured_args["source_identity"] == "bot"
        assert captured_args["source_tool"] == "bot_telegram_get_updates"
        assert captured_args["source_id"] == "msg-7"
        assert captured_args["source_metadata"] == {
            "channel": "telegram",
            "identity": "bot",
            "tool_name": "bot_telegram_get_updates",
            "source_id": "msg-7",
        }

    async def test_classification_list_dispatches_multi_targets(self, monkeypatch):
        """Pipeline dispatches decomposed classifier output to multiple targets."""
        import importlib

        captured: dict[str, Any] = {}

        async def mock_classify(pool, message, dispatch_fn):
            return [
                {"butler": "health", "prompt": "Log my headache"},
                {"butler": "relationship", "prompt": "Remind me to call Mom"},
            ]

        async def mock_dispatch_decomposed(
            pool: Any,
            targets: list[dict[str, str]],
            source_channel: str = "switchboard",
            source_id: str | None = None,
            tool_name: str = "bot_switchboard_handle_message",
            source_metadata: dict[str, Any] | None = None,
            *,
            call_fn: Any | None = None,
        ) -> list[dict[str, Any]]:
            captured["targets"] = targets
            captured["source_channel"] = source_channel
            captured["source_id"] = source_id
            captured["tool_name"] = tool_name
            captured["source_metadata"] = source_metadata
            return [
                {"butler": "health", "result": "logged", "error": None},
                {"butler": "relationship", "result": "reminder set", "error": None},
            ]

        def mock_aggregate_responses(
            results: list[dict[str, Any]],
            *,
            dispatch_fn: Any | None = None,
        ) -> str:
            captured["results"] = results
            return "combined response"

        switchboard = importlib.import_module("butlers.tools.switchboard")

        monkeypatch.setattr(
            switchboard,
            "dispatch_decomposed",
            mock_dispatch_decomposed,
        )
        monkeypatch.setattr(
            switchboard,
            "aggregate_responses",
            mock_aggregate_responses,
        )

        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(),
            dispatch_fn=AsyncMock(),
            classify_fn=mock_classify,
        )

        result = await pipeline.process(
            "I have a headache and call Mom tomorrow",
            tool_args={"source": "telegram", "source_id": "msg-1"},
        )

        assert result.target_butler == "multi"
        assert result.route_result == {"result": "combined response"}
        assert result.routed_targets == ["health", "relationship"]
        assert result.acked_targets == ["health", "relationship"]
        assert result.failed_targets == []
        assert captured["targets"][0] == {"butler": "health", "prompt": "Log my headache"}
        assert captured["targets"][1] == {
            "butler": "relationship",
            "prompt": "Remind me to call Mom",
        }
        assert captured["source_channel"] == "telegram"
        assert captured["source_id"] == "msg-1"
        assert captured["tool_name"] == "bot_switchboard_handle_message"
        assert captured["source_metadata"] == {
            "channel": "telegram",
            "identity": "bot",
            "tool_name": "bot_switchboard_handle_message",
            "source_id": "msg-1",
        }

    async def test_classification_list_tracks_failed_targets(self, monkeypatch):
        """Multi-route failures are surfaced in failed target metadata."""
        import importlib

        async def mock_classify(pool, message, dispatch_fn):
            return [
                {"butler": "health", "prompt": "Log this"},
                {"butler": "general", "prompt": "Fallback"},
            ]

        async def mock_dispatch_decomposed(
            pool: Any,
            targets: list[dict[str, str]],
            source_channel: str = "switchboard",
            source_id: str | None = None,
            tool_name: str = "bot_switchboard_handle_message",
            source_metadata: dict[str, Any] | None = None,
            *,
            call_fn: Any | None = None,
        ) -> list[dict[str, Any]]:
            return [
                {"butler": "health", "result": "ok", "error": None},
                {"butler": "general", "result": None, "error": "ConnectionError: timeout"},
            ]

        def mock_aggregate_responses(
            results: list[dict[str, Any]],
            *,
            dispatch_fn: Any | None = None,
        ) -> str:
            return "partial response"

        switchboard = importlib.import_module("butlers.tools.switchboard")
        monkeypatch.setattr(switchboard, "dispatch_decomposed", mock_dispatch_decomposed)
        monkeypatch.setattr(switchboard, "aggregate_responses", mock_aggregate_responses)

        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(),
            dispatch_fn=AsyncMock(),
            classify_fn=mock_classify,
        )

        result = await pipeline.process("test fanout")

        assert result.target_butler == "multi"
        assert result.routed_targets == ["health", "general"]
        assert result.acked_targets == ["health"]
        assert result.failed_targets == ["general"]
        assert "ConnectionError" in (result.routing_error or "")

    async def test_classification_list_uses_first_entry_and_sub_prompt(self):
        """Custom route_fn uses first decomposition entry for single-target routing."""
        captured: dict[str, Any] = {}

        async def mock_classify(pool, message, dispatch_fn):
            return [
                {"butler": "health", "prompt": "Log my headache"},
                {"butler": "relationship", "prompt": "Remind me to call Mom"},
            ]

        async def mock_route(pool, target, tool_name, args, source):
            captured["target"] = target
            captured["args"] = dict(args)
            return {"result": "handled"}

        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(),
            dispatch_fn=AsyncMock(),
            classify_fn=mock_classify,
            route_fn=mock_route,
        )

        result = await pipeline.process("I have a headache and call Mom tomorrow")

        assert result.target_butler == "health"
        assert result.route_result == {"result": "handled"}
        assert result.routed_targets == ["health"]
        assert result.acked_targets == ["health"]
        assert result.failed_targets == []
        assert captured["target"] == "health"
        assert captured["args"]["prompt"] == "Log my headache"
        assert captured["args"]["message"] == "Log my headache"

    async def test_passes_tool_name_to_route(self):
        """Pipeline passes the specified tool_name to route()."""
        captured_tool_name: list[str] = []

        async def mock_classify(pool, message, dispatch_fn):
            return "general"

        async def mock_route(pool, target, tool_name, args, source):
            captured_tool_name.append(tool_name)
            return {}

        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(),
            dispatch_fn=AsyncMock(),
            classify_fn=mock_classify,
            route_fn=mock_route,
        )

        await pipeline.process("test", tool_name="custom_handler")

        assert captured_tool_name == ["custom_handler"]

    async def test_passes_source_butler_to_route(self):
        """Pipeline passes source_butler to route()."""
        captured_source: list[str] = []

        async def mock_classify(pool, message, dispatch_fn):
            return "general"

        async def mock_route(pool, target, tool_name, args, source):
            captured_source.append(source)
            return {}

        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(),
            dispatch_fn=AsyncMock(),
            source_butler="my-butler",
            classify_fn=mock_classify,
            route_fn=mock_route,
        )

        await pipeline.process("test")

        assert captured_source == ["my-butler"]

    async def test_classification_failure_returns_general(self):
        """When classification fails, pipeline defaults to 'general' with error."""

        async def failing_classify(pool, message, dispatch_fn):
            raise RuntimeError("classifier broke")

        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(),
            dispatch_fn=AsyncMock(),
            classify_fn=failing_classify,
        )

        result = await pipeline.process("test message")

        assert result.target_butler == "general"
        assert result.classification_error is not None
        assert "RuntimeError" in result.classification_error

    async def test_logs_entry_and_exit_with_structured_fields(
        self, caplog: pytest.LogCaptureFixture
    ):
        """Pipeline emits structured start/end logs with context and latency."""

        async def mock_classify(pool, message, dispatch_fn):
            return "health"

        async def mock_route(pool, target, tool_name, args, source):
            return {"result": "handled"}

        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(),
            dispatch_fn=AsyncMock(),
            classify_fn=mock_classify,
            route_fn=mock_route,
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

    async def test_classification_fallback_logs_warning_with_reason(
        self, caplog: pytest.LogCaptureFixture
    ):
        """Classification fallback emits warning with error reason."""

        async def failing_classify(pool, message, dispatch_fn):
            raise RuntimeError("classifier broke")

        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(),
            dispatch_fn=AsyncMock(),
            classify_fn=failing_classify,
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

    async def test_routing_failure_records_error(self):
        """When routing fails, pipeline records the error."""

        async def mock_classify(pool, message, dispatch_fn):
            return "health"

        async def failing_route(pool, target, tool_name, args, source):
            raise ConnectionError("butler unreachable")

        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(),
            dispatch_fn=AsyncMock(),
            classify_fn=mock_classify,
            route_fn=failing_route,
        )

        result = await pipeline.process("help me")

        assert result.target_butler == "health"
        assert result.routing_error is not None
        assert "ConnectionError" in result.routing_error
        assert result.routed_targets == ["health"]
        assert result.acked_targets == []
        assert result.failed_targets == ["health"]

    async def test_routing_returned_error_records_failure(self):
        """route() error payloads are treated as failures."""

        async def mock_classify(pool, message, dispatch_fn):
            return "health"

        async def route_with_error(pool, target, tool_name, args, source):
            return {"error": "ConnectionError: target unavailable"}

        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(),
            dispatch_fn=AsyncMock(),
            classify_fn=mock_classify,
            route_fn=route_with_error,
        )

        result = await pipeline.process("help me")

        assert result.target_butler == "health"
        assert result.routing_error == "ConnectionError: target unavailable"
        assert result.routed_targets == ["health"]
        assert result.acked_targets == []
        assert result.failed_targets == ["health"]

    async def test_default_tool_name(self):
        """Default tool_name is identity-prefixed."""
        captured: list[str] = []

        async def mock_classify(pool, message, dispatch_fn):
            return "general"

        async def mock_route(pool, target, tool_name, args, source):
            captured.append(tool_name)
            return {}

        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(),
            dispatch_fn=AsyncMock(),
            classify_fn=mock_classify,
            route_fn=mock_route,
        )

        await pipeline.process("test")

        assert captured == ["bot_switchboard_handle_message"]

    async def test_default_source_butler(self):
        """Default source_butler is 'switchboard'."""
        captured: list[str] = []

        async def mock_classify(pool, message, dispatch_fn):
            return "general"

        async def mock_route(pool, target, tool_name, args, source):
            captured.append(source)
            return {}

        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(),
            dispatch_fn=AsyncMock(),
            classify_fn=mock_classify,
            route_fn=mock_route,
        )

        await pipeline.process("test")

        assert captured == ["switchboard"]


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

    async def test_duplicate_telegram_replay_maps_to_existing_request(self, caplog):
        pool = _FakeIngressPool()

        async def mock_classify(_pool, _message, _dispatch_fn):
            return "general"

        async def mock_route(_pool, _target, _tool_name, _args, _source):
            return {"result": "ok"}

        pipeline = MessagePipeline(
            switchboard_pool=pool,
            dispatch_fn=AsyncMock(),
            classify_fn=mock_classify,
            route_fn=mock_route,
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

    async def test_duplicate_email_replay_maps_to_existing_request(self):
        pool = _FakeIngressPool()
        classify = AsyncMock(return_value="general")
        route = AsyncMock(return_value={"result": "ok"})

        pipeline = MessagePipeline(
            switchboard_pool=pool,
            dispatch_fn=AsyncMock(),
            classify_fn=classify,
            route_fn=route,
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
        assert classify.await_count == 1
        assert route.await_count == 1
