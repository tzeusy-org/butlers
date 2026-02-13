"""Tests for Telegram module integration with the classification pipeline.

Verifies that:
- TelegramModule.process_update() classifies and routes messages
- The polling loop forwards updates through the pipeline
- Updates without text are skipped
- Pipeline errors are handled gracefully
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.modules.pipeline import MessagePipeline, RoutingResult
from butlers.modules.telegram import TelegramModule, _extract_chat_id, _extract_text

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helper: create a pipeline with mock classify/route
# ---------------------------------------------------------------------------


def _make_pipeline(
    classify_result: str = "general",
    route_result: dict | None = None,
    classify_error: Exception | None = None,
    route_error: Exception | None = None,
) -> MessagePipeline:
    """Build a MessagePipeline with mock classify/route functions."""

    async def mock_classify(pool, message, dispatch_fn):
        if classify_error:
            raise classify_error
        return classify_result

    async def mock_route(pool, target, tool_name, args, source):
        if route_error:
            raise route_error
        return route_result or {"result": "ok"}

    return MessagePipeline(
        switchboard_pool=MagicMock(),
        dispatch_fn=AsyncMock(),
        source_butler="test-butler",
        classify_fn=mock_classify,
        route_fn=mock_route,
    )


# ---------------------------------------------------------------------------
# _extract_text helper
# ---------------------------------------------------------------------------


class TestExtractText:
    """Test the _extract_text helper for various Telegram update formats."""

    def test_regular_message(self):
        update = {"update_id": 1, "message": {"text": "hello", "chat": {"id": 123}}}
        assert _extract_text(update) == "hello"

    def test_edited_message(self):
        update = {"update_id": 2, "edited_message": {"text": "edited", "chat": {"id": 123}}}
        assert _extract_text(update) == "edited"

    def test_channel_post(self):
        update = {"update_id": 3, "channel_post": {"text": "channel msg", "chat": {"id": -100}}}
        assert _extract_text(update) == "channel msg"

    def test_no_text(self):
        update = {"update_id": 4, "message": {"photo": [{}], "chat": {"id": 123}}}
        assert _extract_text(update) is None

    def test_empty_update(self):
        update = {"update_id": 5}
        assert _extract_text(update) is None

    def test_priority_message_over_edited(self):
        """Regular message takes priority if both are present (unlikely but safe)."""
        update = {
            "update_id": 6,
            "message": {"text": "original", "chat": {"id": 1}},
            "edited_message": {"text": "edited", "chat": {"id": 1}},
        }
        assert _extract_text(update) == "original"


# ---------------------------------------------------------------------------
# _extract_chat_id helper
# ---------------------------------------------------------------------------


class TestExtractChatId:
    """Test the _extract_chat_id helper."""

    def test_regular_message(self):
        update = {"update_id": 1, "message": {"text": "hi", "chat": {"id": 12345}}}
        assert _extract_chat_id(update) == "12345"

    def test_no_chat(self):
        update = {"update_id": 2, "message": {"text": "hi"}}
        assert _extract_chat_id(update) is None

    def test_empty_update(self):
        update = {"update_id": 3}
        assert _extract_chat_id(update) is None


# ---------------------------------------------------------------------------
# set_pipeline
# ---------------------------------------------------------------------------


class TestSetPipeline:
    """Test pipeline attachment."""

    def test_set_pipeline(self):
        mod = TelegramModule()
        assert mod._pipeline is None

        pipeline = _make_pipeline()
        mod.set_pipeline(pipeline)
        assert mod._pipeline is pipeline

    def test_replace_pipeline(self):
        mod = TelegramModule()
        p1 = _make_pipeline()
        p2 = _make_pipeline(classify_result="health")

        mod.set_pipeline(p1)
        mod.set_pipeline(p2)
        assert mod._pipeline is p2


# ---------------------------------------------------------------------------
# process_update
# ---------------------------------------------------------------------------


class TestProcessUpdate:
    """Test TelegramModule.process_update()."""

    async def test_routes_message_to_classified_butler(self):
        """process_update classifies and routes the message text."""
        mod = TelegramModule()
        mod.set_pipeline(_make_pipeline(classify_result="health"))

        update = {"update_id": 1, "message": {"text": "I feel sick", "chat": {"id": 42}}}
        result = await mod.process_update(update)

        assert result is not None
        assert result.target_butler == "health"
        assert result.route_result == {"result": "ok"}

    async def test_returns_none_without_pipeline(self):
        """process_update returns None if no pipeline is set."""
        mod = TelegramModule()
        update = {"update_id": 1, "message": {"text": "hello", "chat": {"id": 1}}}
        result = await mod.process_update(update)
        assert result is None

    async def test_returns_none_without_pipeline_logs_warning(
        self, caplog: pytest.LogCaptureFixture
    ):
        """process_update logs warning when no pipeline is set."""
        mod = TelegramModule()
        update = {"update_id": 11, "message": {"text": "hello", "chat": {"id": 1}}}
        with caplog.at_level(logging.WARNING, logger="butlers.modules.telegram"):
            result = await mod.process_update(update)
        assert result is None

        warning = next(
            r
            for r in caplog.records
            if r.name == "butlers.modules.telegram"
            and r.levelno == logging.WARNING
            and r.getMessage()
            == "Skipping Telegram update because no classification pipeline is configured"
        )
        assert warning.source == "telegram"
        assert warning.chat_id == "1"
        assert warning.target_butler is None
        assert warning.latency_ms is None
        assert warning.update_id == 11

    async def test_returns_none_for_no_text(self):
        """process_update returns None when the update has no text."""
        mod = TelegramModule()
        mod.set_pipeline(_make_pipeline())
        update = {"update_id": 1, "message": {"photo": [{}], "chat": {"id": 1}}}
        result = await mod.process_update(update)
        assert result is None

    async def test_includes_source_and_chat_id_in_tool_args(self):
        """process_update includes source=telegram and chat_id in the route args."""
        captured_args: dict = {}

        async def capture_route(pool, target, tool_name, args, source):
            captured_args.update(args)
            return {"result": "ok"}

        async def mock_classify(pool, message, dispatch_fn):
            return "general"

        pipeline = MessagePipeline(
            switchboard_pool=MagicMock(),
            dispatch_fn=AsyncMock(),
            classify_fn=mock_classify,
            route_fn=capture_route,
        )

        mod = TelegramModule()
        mod.set_pipeline(pipeline)

        update = {"update_id": 1, "message": {"text": "test", "chat": {"id": 999}}}
        await mod.process_update(update)

        assert captured_args["source"] == "telegram"
        assert captured_args["chat_id"] == "999"
        assert captured_args["message"] == "test"

    async def test_records_routed_messages(self):
        """process_update appends the result to _routed_messages."""
        mod = TelegramModule()
        mod.set_pipeline(_make_pipeline(classify_result="general"))

        update = {"update_id": 1, "message": {"text": "hi", "chat": {"id": 1}}}
        await mod.process_update(update)
        await mod.process_update(update)

        assert len(mod._routed_messages) == 2
        assert all(r.target_butler == "general" for r in mod._routed_messages)

    async def test_handles_classification_error(self):
        """process_update falls back to 'general' on classification error."""
        mod = TelegramModule()
        mod.set_pipeline(_make_pipeline(classify_error=RuntimeError("AI broke")))

        update = {"update_id": 1, "message": {"text": "help", "chat": {"id": 1}}}
        result = await mod.process_update(update)

        assert result is not None
        assert result.target_butler == "general"
        assert result.classification_error is not None

    async def test_handles_routing_error(self):
        """process_update records routing error."""
        mod = TelegramModule()
        mod.set_pipeline(
            _make_pipeline(
                classify_result="health",
                route_error=ConnectionError("unreachable"),
            )
        )

        update = {"update_id": 1, "message": {"text": "help", "chat": {"id": 1}}}
        result = await mod.process_update(update)

        assert result is not None
        assert result.target_butler == "health"
        assert result.routing_error is not None

    async def test_edited_message_routed(self):
        """Edited messages are also classified and routed."""
        mod = TelegramModule()
        mod.set_pipeline(_make_pipeline(classify_result="general"))

        update = {"update_id": 1, "edited_message": {"text": "fixed", "chat": {"id": 1}}}
        result = await mod.process_update(update)

        assert result is not None
        assert result.target_butler == "general"

    async def test_channel_post_routed(self):
        """Channel posts are also classified and routed."""
        mod = TelegramModule()
        mod.set_pipeline(_make_pipeline(classify_result="general"))

        update = {"update_id": 1, "channel_post": {"text": "announcement", "chat": {"id": -100}}}
        result = await mod.process_update(update)

        assert result is not None
        assert result.target_butler == "general"

    async def test_forwards_ingress_event_identity_metadata(self):
        """process_update forwards channel-specific ingress identity metadata."""
        mod = TelegramModule()

        pipeline = MagicMock()
        pipeline.process = AsyncMock(
            return_value=RoutingResult(
                target_butler="general",
                route_result={"routed": True},
            )
        )
        mod.set_pipeline(pipeline)

        update = {
            "update_id": 88,
            "message": {
                "text": "hello",
                "chat": {"id": 7},
                "from": {"id": 222},
            },
        }
        result = await mod.process_update(update)

        assert result is not None
        pipeline.process.assert_awaited_once()
        tool_args = pipeline.process.await_args.kwargs["tool_args"]
        assert tool_args["source_endpoint_identity"] == "telegram:bot"
        assert tool_args["sender_identity"] == "222"
        assert tool_args["external_event_id"] == "88"
        assert tool_args["external_thread_id"] == "7"
        assert tool_args["raw_metadata"] == update


# ---------------------------------------------------------------------------
# Poll loop integration
# ---------------------------------------------------------------------------


class TestPollLoopPipeline:
    """Verify that the poll loop routes updates through the pipeline."""

    async def test_poll_loop_routes_updates(self):
        """Poll loop calls process_update for each received update."""
        mod = TelegramModule()
        mod.set_pipeline(_make_pipeline(classify_result="health"))

        call_count = 0
        original_process = mod.process_update

        async def counting_process(update):
            nonlocal call_count
            call_count += 1
            return await original_process(update)

        mod.process_update = counting_process  # type: ignore[method-assign]

        # Mock _get_updates to return 2 updates then cancel
        iteration = 0

        async def mock_get_updates():
            nonlocal iteration
            iteration += 1
            if iteration == 1:
                return [
                    {"update_id": 1, "message": {"text": "msg1", "chat": {"id": 1}}},
                    {"update_id": 2, "message": {"text": "msg2", "chat": {"id": 2}}},
                ]
            raise asyncio.CancelledError

        mod._get_updates = mock_get_updates  # type: ignore[method-assign]
        mod._config.poll_interval = 0.01

        with pytest.raises(asyncio.CancelledError):
            await mod._poll_loop()

        # Both updates should have been processed
        assert call_count == 2

    async def test_poll_loop_without_pipeline_still_buffers(self):
        """Without a pipeline, poll loop still buffers updates (legacy behavior)."""
        mod = TelegramModule()
        # No pipeline set

        iteration = 0

        async def mock_get_updates():
            nonlocal iteration
            iteration += 1
            if iteration == 1:
                return [{"update_id": 1, "message": {"text": "msg", "chat": {"id": 1}}}]
            raise asyncio.CancelledError

        mod._get_updates = mock_get_updates  # type: ignore[method-assign]
        mod._config.poll_interval = 0.01

        with pytest.raises(asyncio.CancelledError):
            await mod._poll_loop()

        assert len(mod._updates_buffer) == 1
        assert mod._routed_messages == []

    async def test_poll_loop_logs_update_count_at_info(self, caplog: pytest.LogCaptureFixture):
        """Poll loop emits INFO log with update count."""
        mod = TelegramModule()
        mod.set_pipeline(_make_pipeline(classify_result="general"))

        iteration = 0

        async def mock_get_updates():
            nonlocal iteration
            iteration += 1
            if iteration == 1:
                return [{"update_id": 1, "message": {"text": "msg", "chat": {"id": 1}}}]
            raise asyncio.CancelledError

        mod._get_updates = mock_get_updates  # type: ignore[method-assign]
        mod._config.poll_interval = 0.01

        with caplog.at_level(logging.INFO, logger="butlers.modules.telegram"):
            with pytest.raises(asyncio.CancelledError):
                await mod._poll_loop()

        poll_log = next(
            r
            for r in caplog.records
            if r.name == "butlers.modules.telegram"
            and r.levelno == logging.INFO
            and r.getMessage() == "Polled Telegram updates"
        )
        assert poll_log.source == "telegram"
        assert poll_log.update_count == 1
