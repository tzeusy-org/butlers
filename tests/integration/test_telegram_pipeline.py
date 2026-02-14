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
        assert captured_args["source_channel"] == "telegram"
        assert captured_args["source_identity"] == "bot"
        assert captured_args["source_tool"] == "bot_telegram_get_updates"
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
        assert tool_args["source"] == "telegram"
        assert tool_args["source_channel"] == "telegram"
        assert tool_args["source_identity"] == "bot"
        assert tool_args["source_tool"] == "bot_telegram_get_updates"
        assert tool_args["chat_id"] == "7"
        assert tool_args["source_id"] == "update:88"

    async def test_uses_database_pool_for_message_inbox_logging(self):
        """process_update logs to message_inbox via db.pool.acquire()."""
        import uuid

        mod = TelegramModule()

        pipeline = MagicMock()
        expected = {"routed": True}
        pipeline.process = AsyncMock(
            return_value=RoutingResult(
                target_butler="general",
                route_result=expected,
            )
        )
        mod.set_pipeline(pipeline)

        inbox_id = uuid.uuid4()
        conn = AsyncMock()
        conn.fetchval = AsyncMock(return_value=inbox_id)
        acquire_cm = AsyncMock()
        acquire_cm.__aenter__.return_value = conn
        acquire_cm.__aexit__.return_value = False
        pool = MagicMock()
        pool.acquire.return_value = acquire_cm

        db = MagicMock()
        db.pool = pool
        mod._db = db

        update = {"update_id": 1, "message": {"text": "hello", "chat": {"id": 7}}}
        result = await mod.process_update(update)

        assert result is not None
        pool.acquire.assert_called_once()
        conn.fetchval.assert_awaited_once()
        pipeline.process.assert_awaited_once()
        assert pipeline.process.await_args.kwargs["message_inbox_id"] == inbox_id

    async def test_duplicate_updates_both_processed_after_partition_migration(self):
        """After partition migration, telegram-level dedup is removed.

        Both updates go through the pipeline (dedup handled upstream).
        """
        import uuid

        mod = TelegramModule()

        pipeline = MagicMock()
        pipeline.process = AsyncMock(
            return_value=RoutingResult(
                target_butler="general",
                route_result={"routed": True},
            )
        )
        mod.set_pipeline(pipeline)

        conn = AsyncMock()
        conn.fetchval = AsyncMock(side_effect=[uuid.uuid4(), uuid.uuid4()])
        acquire_cm = AsyncMock()
        acquire_cm.__aenter__.return_value = conn
        acquire_cm.__aexit__.return_value = False
        pool = MagicMock()
        pool.acquire.return_value = acquire_cm

        db = MagicMock()
        db.pool = pool
        mod._db = db

        update = {"update_id": 42, "message": {"text": "hello", "chat": {"id": 7}}}
        first = await mod.process_update(update)
        second = await mod.process_update(update)

        assert first is not None
        assert second is not None
        # Both updates are processed through the pipeline (dedup handled upstream by pipeline)
        assert pipeline.process.await_count == 2


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


class TestIdentityScopedToolFlows:
    """Verify user/bot send and ingest tool behavior."""

    async def test_user_and_bot_send_reply_tools_delegate_helpers(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Both identity-scoped send/reply tools invoke shared helpers."""
        monkeypatch.setenv("BUTLER_TELEGRAM_TOKEN", "test-token")
        mod = TelegramModule()
        mcp = MagicMock()
        tools: dict[str, object] = {}

        def capture_tool():
            def decorator(fn):
                tools[fn.__name__] = fn
                return fn

            return decorator

        mcp.tool = capture_tool
        await mod.register_tools(mcp=mcp, config=None, db=None)

        send_mock = AsyncMock(return_value={"ok": True, "type": "send"})
        reply_mock = AsyncMock(return_value={"ok": True, "type": "reply"})
        mod._send_message = send_mock  # type: ignore[method-assign]
        mod._reply_to_message = reply_mock  # type: ignore[method-assign]

        user_send = await tools["user_telegram_send_message"](chat_id="1", text="hello")  # type: ignore[index]
        bot_send = await tools["bot_telegram_send_message"](chat_id="2", text="hi")  # type: ignore[index]
        user_reply = await tools["user_telegram_reply_to_message"](  # type: ignore[index]
            chat_id="3",
            message_id=11,
            text="user reply",
        )
        bot_reply = await tools["bot_telegram_reply_to_message"](  # type: ignore[index]
            chat_id="4",
            message_id=12,
            text="bot reply",
        )

        assert user_send["type"] == "send"
        assert bot_send["type"] == "send"
        assert user_reply["type"] == "reply"
        assert bot_reply["type"] == "reply"
        assert send_mock.await_args_list[0].args == ("1", "hello")
        assert send_mock.await_args_list[1].args == ("2", "hi")
        assert reply_mock.await_args_list[0].args == ("3", 11, "user reply")
        assert reply_mock.await_args_list[1].args == ("4", 12, "bot reply")

    async def test_user_and_bot_get_updates_tools_delegate_helper(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Both identity-scoped ingest tools delegate to the updates helper."""
        monkeypatch.setenv("BUTLER_TELEGRAM_TOKEN", "test-token")
        mod = TelegramModule()
        mcp = MagicMock()
        tools: dict[str, object] = {}

        def capture_tool():
            def decorator(fn):
                tools[fn.__name__] = fn
                return fn

            return decorator

        mcp.tool = capture_tool
        await mod.register_tools(mcp=mcp, config=None, db=None)

        updates = [{"update_id": 1}, {"update_id": 2}]
        get_updates_mock = AsyncMock(return_value=updates)
        mod._get_updates = get_updates_mock  # type: ignore[method-assign]

        user_updates = await tools["user_telegram_get_updates"]()  # type: ignore[index]
        bot_updates = await tools["bot_telegram_get_updates"]()  # type: ignore[index]

        assert user_updates == updates
        assert bot_updates == updates
        assert get_updates_mock.await_count == 2

    async def test_legacy_unprefixed_telegram_tool_names_are_not_callable(self):
        """Legacy unprefixed Telegram names are absent from registration surfaces."""
        mod = TelegramModule()
        mcp = MagicMock()
        tools: dict[str, object] = {}

        def capture_tool():
            def decorator(fn):
                tools[fn.__name__] = fn
                return fn

            return decorator

        mcp.tool = capture_tool
        await mod.register_tools(mcp=mcp, config=None, db=None)

        legacy_send = "send" + "_message"
        legacy_reply = "reply" + "_to_message"
        legacy_updates = "get" + "_updates"
        assert legacy_send not in tools
        assert legacy_reply not in tools
        assert legacy_updates not in tools
        with pytest.raises(KeyError):
            _ = tools[legacy_send]
