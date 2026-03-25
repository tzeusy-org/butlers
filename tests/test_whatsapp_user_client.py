"""Tests for WhatsApp user-client connector runtime."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.connectors.whatsapp_user_client import (
    ChatBuffer,
    WhatsAppUserClientConnector,
    WhatsAppUserClientConnectorConfig,
    normalize_message_text,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def wa_config() -> WhatsAppUserClientConnectorConfig:
    """Create a minimal test config."""
    return WhatsAppUserClientConnectorConfig(
        switchboard_mcp_url="http://localhost:41100/sse",
        provider="whatsapp",
        channel="whatsapp_user_client",
        endpoint_identity="whatsapp:+15551234567",
        bridge_socket="/tmp/test-wa-bridge.sock",
        flush_interval_s=600,
        buffer_max_messages=50,
        health_port=40082,
    )


@pytest.fixture
def mock_cursor_pool() -> MagicMock:
    """Create a mock DB cursor pool."""
    pool = MagicMock()
    pool.acquire = MagicMock()
    return pool


@pytest.fixture
def mock_db_pool() -> MagicMock:
    """Create a mock general DB pool."""
    pool = MagicMock()
    pool.acquire = MagicMock()
    return pool


@pytest.fixture
def connector(
    wa_config: WhatsAppUserClientConnectorConfig,
    mock_cursor_pool: MagicMock,
    mock_db_pool: MagicMock,
) -> WhatsAppUserClientConnector:
    """Create a WhatsApp connector with mocked pools."""
    return WhatsAppUserClientConnector(
        wa_config,
        db_pool=mock_db_pool,
        cursor_pool=mock_cursor_pool,
    )


# ---------------------------------------------------------------------------
# Tests: normalize_message_text
# ---------------------------------------------------------------------------


class TestNormalizeMessageText:
    """Tests for the message type normalization function."""

    def test_conversation_returns_text_verbatim(self) -> None:
        event = {"type": "Conversation", "content": {"text": "Hello world"}}
        assert normalize_message_text(event) == "Hello world"

    def test_extended_text_returns_text_verbatim(self) -> None:
        event = {"type": "ExtendedTextMessage", "content": {"text": "What's up?"}}
        assert normalize_message_text(event) == "What's up?"

    def test_image_with_caption(self) -> None:
        event = {"type": "ImageMessage", "content": {"caption": "Nice view!"}}
        assert normalize_message_text(event) == "Nice view!"

    def test_image_without_caption(self) -> None:
        event = {"type": "ImageMessage", "content": {}}
        assert normalize_message_text(event) == "[image]"

    def test_video_with_caption(self) -> None:
        event = {"type": "VideoMessage", "content": {"caption": "Watch this"}}
        assert normalize_message_text(event) == "Watch this"

    def test_video_without_caption(self) -> None:
        event = {"type": "VideoMessage", "content": {}}
        assert normalize_message_text(event) == "[video]"

    def test_audio_message(self) -> None:
        event = {"type": "AudioMessage", "content": {}}
        assert normalize_message_text(event) == "[audio]"

    def test_ptt_message(self) -> None:
        event = {"type": "PTTMessage", "content": {}}
        assert normalize_message_text(event) == "[voice message]"

    def test_document_with_filename_and_caption(self) -> None:
        event = {
            "type": "DocumentMessage",
            "content": {"fileName": "report.pdf", "caption": "Q3 Report"},
        }
        result = normalize_message_text(event)
        assert "report.pdf" in result
        assert "Q3 Report" in result

    def test_document_with_only_filename(self) -> None:
        event = {"type": "DocumentMessage", "content": {"fileName": "contract.docx"}}
        assert normalize_message_text(event) == "contract.docx"

    def test_document_empty(self) -> None:
        event = {"type": "DocumentMessage", "content": {}}
        assert normalize_message_text(event) == "[document]"

    def test_sticker_message(self) -> None:
        event = {"type": "StickerMessage", "content": {}}
        assert normalize_message_text(event) == "[sticker]"

    def test_location_with_name(self) -> None:
        event = {
            "type": "LocationMessage",
            "content": {
                "degreesLatitude": 37.7749,
                "degreesLongitude": -122.4194,
                "name": "San Francisco",
            },
        }
        result = normalize_message_text(event)
        assert "37.7749" in result
        assert "-122.4194" in result
        assert "San Francisco" in result

    def test_location_without_name(self) -> None:
        event = {
            "type": "LocationMessage",
            "content": {"degreesLatitude": 0.0, "degreesLongitude": 0.0},
        }
        result = normalize_message_text(event)
        assert "[location:" in result

    def test_contact_message(self) -> None:
        event = {"type": "ContactMessage", "content": {"displayName": "Alice Smith"}}
        assert normalize_message_text(event) == "[contact: Alice Smith]"

    def test_contact_message_empty(self) -> None:
        event = {"type": "ContactMessage", "content": {}}
        assert normalize_message_text(event) == "[contact]"

    def test_reaction_with_emoji_and_target(self) -> None:
        event = {
            "type": "ReactionMessage",
            "content": {"text": "👍", "key": {"id": "msg-123"}},
        }
        result = normalize_message_text(event)
        assert "👍" in result
        assert "msg-123" in result

    def test_reaction_with_only_emoji(self) -> None:
        event = {"type": "ReactionMessage", "content": {"text": "❤️"}}
        result = normalize_message_text(event)
        assert "❤️" in result

    def test_poll_creation_message(self) -> None:
        event = {
            "type": "PollCreationMessage",
            "content": {
                "name": "What's for lunch?",
                "options": [
                    {"optionName": "Pizza"},
                    {"optionName": "Salad"},
                    {"optionName": "Tacos"},
                ],
            },
        }
        result = normalize_message_text(event)
        assert "What's for lunch?" in result
        assert "Pizza" in result
        assert "Salad" in result

    def test_protocol_message_revoke(self) -> None:
        event = {"type": "ProtocolMessage", "content": {"type": "REVOKE"}}
        assert normalize_message_text(event) == "[message deleted]"

    def test_unknown_type_falls_back_to_type_label(self) -> None:
        event = {"type": "FutureMessageType", "content": {}}
        result = normalize_message_text(event)
        assert "futuremessagetype" in result.lower()

    def test_empty_event_returns_unknown(self) -> None:
        event: dict[str, Any] = {}
        result = normalize_message_text(event)
        assert "[unknown]" in result

    def test_text_field_at_top_level_fallback(self) -> None:
        """Events with top-level text field should use it as fallback."""
        event = {"type": "UnknownType", "text": "some text", "content": {}}
        result = normalize_message_text(event)
        assert "some text" == result


# ---------------------------------------------------------------------------
# Tests: WhatsAppUserClientConnectorConfig
# ---------------------------------------------------------------------------


class TestWhatsAppUserClientConnectorConfig:
    """Tests for connector configuration."""

    def test_from_env_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test config loading from environment variables."""
        monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:41100/sse")
        monkeypatch.setenv("CONNECTOR_PROVIDER", "whatsapp")
        monkeypatch.setenv("CONNECTOR_CHANNEL", "whatsapp_user_client")
        monkeypatch.setenv("CONNECTOR_MAX_INFLIGHT", "4")
        monkeypatch.setenv("WA_FLUSH_INTERVAL_S", "300")
        monkeypatch.setenv("WA_BUFFER_MAX_MESSAGES", "25")
        monkeypatch.setenv("CONNECTOR_HEALTH_PORT", "40082")

        config = WhatsAppUserClientConnectorConfig.from_env()

        assert config.switchboard_mcp_url == "http://localhost:41100/sse"
        assert config.provider == "whatsapp"
        assert config.channel == "whatsapp_user_client"
        assert config.max_inflight == 4
        assert config.flush_interval_s == 300
        assert config.buffer_max_messages == 25
        assert config.health_port == 40082

    def test_from_env_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test config loading uses sensible defaults."""
        monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:41100/sse")

        config = WhatsAppUserClientConnectorConfig.from_env()

        assert config.provider == "whatsapp"
        assert config.channel == "whatsapp_user_client"
        assert config.max_inflight == 8
        assert config.flush_interval_s == 600
        assert config.buffer_max_messages == 50
        assert config.health_port == 40082
        assert config.bridge_socket == "/tmp/wa-bridge.sock"

    def test_from_env_missing_switchboard_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that missing SWITCHBOARD_MCP_URL raises ValueError."""
        monkeypatch.delenv("SWITCHBOARD_MCP_URL", raising=False)
        with pytest.raises(ValueError, match="SWITCHBOARD_MCP_URL"):
            WhatsAppUserClientConnectorConfig.from_env()

    def test_from_env_backfill_window(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test backfill window is parsed from env."""
        monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:41100/sse")
        monkeypatch.setenv("CONNECTOR_BACKFILL_WINDOW_H", "24")

        config = WhatsAppUserClientConnectorConfig.from_env()
        assert config.backfill_window_h == 24

    def test_from_env_no_backfill_window_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that backfill_window_h defaults to None."""
        monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:41100/sse")
        monkeypatch.delenv("CONNECTOR_BACKFILL_WINDOW_H", raising=False)

        config = WhatsAppUserClientConnectorConfig.from_env()
        assert config.backfill_window_h is None


# ---------------------------------------------------------------------------
# Tests: ChatBuffer
# ---------------------------------------------------------------------------


class TestChatBuffer:
    """Tests for the per-chat buffer data structure."""

    def test_initial_state(self) -> None:
        buf = ChatBuffer(chat_jid="test@s.whatsapp.net")
        assert buf.messages == []
        assert buf.chat_jid == "test@s.whatsapp.net"
        assert buf.lock is not None

    def test_last_flush_ts_initialized_to_monotonic(self) -> None:
        before = time.monotonic()
        buf = ChatBuffer()
        after = time.monotonic()
        assert before <= buf.last_flush_ts <= after


# ---------------------------------------------------------------------------
# Tests: Connector buffering
# ---------------------------------------------------------------------------


class TestConnectorBuffering:
    """Tests for per-chat buffering logic."""

    async def test_buffer_event_creates_chat_buffer(
        self, connector: WhatsAppUserClientConnector
    ) -> None:
        """Buffering an event creates a new ChatBuffer if none exists."""
        event = {
            "message_id": "msg-1",
            "chat_jid": "chat1@g.us",
            "type": "Conversation",
            "content": {"text": "hi"},
        }
        await connector._buffer_event(event, "chat1@g.us")

        assert "chat1@g.us" in connector._chat_buffers
        assert len(connector._chat_buffers["chat1@g.us"].messages) == 1

    async def test_buffer_event_accumulates_messages(
        self, connector: WhatsAppUserClientConnector
    ) -> None:
        """Multiple events for the same chat accumulate in the buffer."""
        jid = "chat1@g.us"
        for i in range(3):
            event = {
                "message_id": f"msg-{i}",
                "chat_jid": jid,
                "type": "Conversation",
                "content": {"text": f"message {i}"},
            }
            await connector._buffer_event(event, jid)

        assert len(connector._chat_buffers[jid].messages) == 3

    async def test_force_flush_on_cap(self, connector: WhatsAppUserClientConnector) -> None:
        """Buffer force-flushes when it reaches buffer_max_messages."""
        connector._config = WhatsAppUserClientConnectorConfig(
            switchboard_mcp_url="http://localhost:41100/sse",
            endpoint_identity="whatsapp:+15551234567",
            buffer_max_messages=3,
        )

        flush_calls = []

        async def mock_flush(jid: str) -> None:
            flush_calls.append(jid)
            # Clear the buffer as the real flush would
            if jid in connector._chat_buffers:
                connector._chat_buffers[jid].messages = []

        connector._flush_chat_buffer = mock_flush  # type: ignore[method-assign]

        jid = "chat1@g.us"
        for i in range(3):
            event = {
                "message_id": f"msg-{i}",
                "chat_jid": jid,
                "type": "Conversation",
                "content": {"text": f"msg {i}"},
            }
            await connector._buffer_event(event, jid)

        # Should have flushed when cap was reached
        assert len(flush_calls) == 1
        assert flush_calls[0] == jid

    async def test_flush_all_buffers_clears_messages(
        self, connector: WhatsAppUserClientConnector
    ) -> None:
        """_flush_all_buffers iterates all chats."""
        flushed = []

        async def mock_flush(jid: str) -> None:
            flushed.append(jid)

        connector._flush_chat_buffer = mock_flush  # type: ignore[method-assign]

        # Seed two chat buffers
        connector._chat_buffers["a@s.whatsapp.net"] = ChatBuffer(
            chat_jid="a@s.whatsapp.net",
            messages=[{"id": "1"}],
        )
        connector._chat_buffers["b@g.us"] = ChatBuffer(
            chat_jid="b@g.us",
            messages=[{"id": "2"}],
        )

        await connector._flush_all_buffers(reason="test")

        assert set(flushed) == {"a@s.whatsapp.net", "b@g.us"}


# ---------------------------------------------------------------------------
# Tests: Envelope building
# ---------------------------------------------------------------------------


class TestEnvelopeBuilding:
    """Tests for ingest.v1 envelope construction."""

    def test_single_event_normalization(self, connector: WhatsAppUserClientConnector) -> None:
        """Single-event envelope maps fields per ingest.v1 spec."""
        event = {
            "message_id": "ABCD1234",
            "chat_jid": "5551234567@s.whatsapp.net",
            "sender_jid": "5559876543@s.whatsapp.net",
            "timestamp": 1700000000,
            "type": "Conversation",
            "content": {"text": "Hello"},
        }
        envelope = connector._normalize_single_event_to_ingest_v1(event)

        assert envelope["schema_version"] == "ingest.v1"
        assert envelope["source"]["channel"] == "whatsapp_user_client"
        assert envelope["source"]["provider"] == "whatsapp"
        assert envelope["source"]["endpoint_identity"] == "whatsapp:+15551234567"
        assert envelope["event"]["external_event_id"] == "ABCD1234"
        assert envelope["event"]["external_thread_id"] == "5551234567@s.whatsapp.net"
        assert envelope["sender"]["identity"] == "5559876543@s.whatsapp.net"
        assert envelope["payload"]["normalized_text"] == "Hello"
        assert "ABCD1234" in envelope["control"]["idempotency_key"]
        assert "whatsapp:" in envelope["control"]["idempotency_key"]
        assert envelope["control"]["policy_tier"] == "default"

    def test_single_event_idempotency_key_format(
        self, connector: WhatsAppUserClientConnector
    ) -> None:
        """Idempotency key format: whatsapp:<endpoint>:<msg_id>."""
        event = {
            "message_id": "XYZ999",
            "type": "Conversation",
            "content": {"text": "test"},
        }
        envelope = connector._normalize_single_event_to_ingest_v1(event)
        expected_prefix = "whatsapp:whatsapp:+15551234567:XYZ999"
        assert envelope["control"]["idempotency_key"] == expected_prefix

    def test_batch_envelope_structure(self, connector: WhatsAppUserClientConnector) -> None:
        """Batch envelope has correct schema_version and ingest.v1 fields."""
        events = [
            {
                "message_id": f"msg-{i}",
                "chat_jid": "chat1@g.us",
                "sender_jid": f"sender{i}@s.whatsapp.net",
                "type": "Conversation",
                "content": {"text": f"Message {i}"},
            }
            for i in range(3)
        ]
        batch_event_id = "batch:chat1@g.us:msg-0-msg-2"
        envelope = connector._build_batch_envelope("chat1@g.us", events, batch_event_id)

        assert envelope["schema_version"] == "ingest.v1"
        assert envelope["source"]["channel"] == "whatsapp_user_client"
        assert envelope["event"]["external_thread_id"] == "chat1@g.us"
        assert envelope["event"]["external_event_id"] == batch_event_id
        assert envelope["sender"]["identity"] == "multiple"
        # normalized_text should contain chat JID header
        assert "chat1@g.us" in envelope["payload"]["normalized_text"]
        # Should contain message content
        assert "Message 0" in envelope["payload"]["normalized_text"]
        assert "Message 2" in envelope["payload"]["normalized_text"]

    def test_batch_envelope_empty(self, connector: WhatsAppUserClientConnector) -> None:
        """Empty batch envelope is well-formed."""
        envelope = connector._build_batch_envelope("chat@g.us", [], "batch:chat@g.us:0-0")

        assert envelope["schema_version"] == "ingest.v1"
        assert envelope["payload"]["normalized_text"] == ""

    def test_single_event_timestamp_conversion(
        self, connector: WhatsAppUserClientConnector
    ) -> None:
        """Unix timestamp is converted to RFC3339 string."""
        event = {
            "message_id": "ts-test",
            "timestamp": 1700000000,
            "type": "Conversation",
            "content": {"text": "hi"},
        }
        envelope = connector._normalize_single_event_to_ingest_v1(event)
        # Should be an ISO format string, not an int
        assert "T" in envelope["event"]["observed_at"]
        assert "2023" in envelope["event"]["observed_at"]


# ---------------------------------------------------------------------------
# Tests: Checkpoint persistence
# ---------------------------------------------------------------------------


class TestCheckpointPersistence:
    """Tests for checkpoint load/save behavior."""

    async def test_load_checkpoint_sets_last_event_id(
        self, connector: WhatsAppUserClientConnector
    ) -> None:
        """Loading an existing checkpoint sets _last_event_id."""
        checkpoint_data = json.dumps({"last_event_id": "msg-42"})

        with patch(
            "butlers.connectors.cursor_store.load_cursor",
            new_callable=AsyncMock,
            return_value=checkpoint_data,
        ):
            await connector._load_checkpoint()

        assert connector._last_event_id == "msg-42"

    async def test_load_checkpoint_handles_missing(
        self, connector: WhatsAppUserClientConnector
    ) -> None:
        """Loading a missing checkpoint leaves _last_event_id as None."""
        with patch(
            "butlers.connectors.cursor_store.load_cursor",
            new_callable=AsyncMock,
            return_value=None,
        ):
            await connector._load_checkpoint()

        assert connector._last_event_id is None

    def test_get_checkpoint_returns_none_when_no_event_id(
        self, connector: WhatsAppUserClientConnector
    ) -> None:
        """_get_checkpoint returns (None, None) when no event has been processed."""
        cursor, updated_at = connector._get_checkpoint()
        assert cursor is None
        assert updated_at is None

    def test_get_checkpoint_returns_json_cursor(
        self, connector: WhatsAppUserClientConnector
    ) -> None:
        """_get_checkpoint returns a JSON-encoded cursor string when event ID is set."""
        connector._last_event_id = "msg-100"
        connector._last_checkpoint_save = time.time()

        cursor, updated_at = connector._get_checkpoint()
        assert cursor is not None
        data = json.loads(cursor)
        assert data["last_event_id"] == "msg-100"
        assert updated_at is not None


# ---------------------------------------------------------------------------
# Tests: Discretion integration
# ---------------------------------------------------------------------------


class TestDiscretionIntegration:
    """Tests for discretion layer integration in flush pipeline."""

    async def test_flush_records_filtered_event_on_discretion_ignore(
        self, connector: WhatsAppUserClientConnector
    ) -> None:
        """When discretion returns IGNORE, event is recorded in filtered buffer."""
        from butlers.connectors.discretion import DiscretionResult

        chat_jid = "group1@g.us"
        events = [
            {
                "message_id": "msg-1",
                "chat_jid": chat_jid,
                "sender_jid": "somebody@s.whatsapp.net",
                "type": "Conversation",
                "content": {"text": "just chatting"},
            }
        ]

        connector._chat_buffers[chat_jid] = ChatBuffer(chat_jid=chat_jid, messages=list(events))

        # Provide a discretion dispatcher that returns IGNORE
        mock_dispatcher = MagicMock()
        mock_evaluator = MagicMock()
        mock_evaluator.evaluate = AsyncMock(
            return_value=DiscretionResult(verdict="IGNORE", reason="background chatter")
        )
        connector._discretion_dispatcher = mock_dispatcher
        connector._discretion_evaluators[chat_jid] = mock_evaluator

        # Mock ingest submission (should NOT be called)
        connector._submit_to_ingest = AsyncMock()  # type: ignore[method-assign]
        connector._flush_and_drain = AsyncMock()  # type: ignore[method-assign]

        # Mock ingestion policy to allow
        connector._ingestion_policy.evaluate = MagicMock(  # type: ignore[method-assign]
            return_value=MagicMock(allowed=True)
        )
        connector._global_ingestion_policy.evaluate = MagicMock(  # type: ignore[method-assign]
            return_value=MagicMock(action="pass_through")
        )

        await connector._flush_chat_buffer(chat_jid)

        # Submission should not have been called
        connector._submit_to_ingest.assert_not_called()

        # Filtered event buffer should have a record
        assert len(connector._filtered_event_buffer) == 1

    async def test_flush_submits_on_discretion_forward(
        self, connector: WhatsAppUserClientConnector
    ) -> None:
        """When discretion returns FORWARD, event is submitted to Switchboard."""
        from butlers.connectors.discretion import DiscretionResult

        chat_jid = "dm1@s.whatsapp.net"
        events = [
            {
                "message_id": "msg-2",
                "chat_jid": chat_jid,
                "sender_jid": "friend@s.whatsapp.net",
                "type": "Conversation",
                "content": {"text": "can you help me?"},
            }
        ]

        connector._chat_buffers[chat_jid] = ChatBuffer(chat_jid=chat_jid, messages=list(events))

        mock_evaluator = MagicMock()
        mock_evaluator.evaluate = AsyncMock(
            return_value=DiscretionResult(verdict="FORWARD", reason="question detected")
        )
        connector._discretion_dispatcher = MagicMock()
        connector._discretion_evaluators[chat_jid] = mock_evaluator

        connector._submit_to_ingest = AsyncMock()  # type: ignore[method-assign]
        connector._flush_and_drain = AsyncMock()  # type: ignore[method-assign]
        connector._save_checkpoint = AsyncMock()  # type: ignore[method-assign]

        connector._ingestion_policy.evaluate = MagicMock(  # type: ignore[method-assign]
            return_value=MagicMock(allowed=True)
        )
        connector._global_ingestion_policy.evaluate = MagicMock(  # type: ignore[method-assign]
            return_value=MagicMock(action="pass_through")
        )

        await connector._flush_chat_buffer(chat_jid)

        connector._submit_to_ingest.assert_awaited_once()


# ---------------------------------------------------------------------------
# Tests: Bridge reconnection
# ---------------------------------------------------------------------------


class TestBridgeReconnection:
    """Tests for SSE reconnection behavior."""

    async def test_sse_event_loop_stops_when_bridge_degraded(
        self, connector: WhatsAppUserClientConnector
    ) -> None:
        """SSE event loop exits when bridge is in degraded mode."""
        connector._running = True
        mock_bridge = MagicMock()
        mock_bridge.is_degraded = True
        mock_bridge.degraded_reason = "Session invalidated — re-pair required"
        connector._bridge_manager = mock_bridge

        # The loop should exit quickly due to degraded state
        await asyncio.wait_for(connector._sse_event_loop(), timeout=2.0)
        # If we get here, the loop exited as expected

    async def test_sse_event_loop_handles_connection_error_with_backoff(
        self, connector: WhatsAppUserClientConnector
    ) -> None:
        """SSE loop reconnects with backoff after connection failure."""
        connector._running = True
        call_count = 0

        async def failing_stream(*args: Any, **kwargs: Any):  # type: ignore[misc]
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise ConnectionRefusedError("Bridge not ready")
            # On 3rd call, stop the loop
            connector._running = False
            return
            yield  # make it a generator

        with (
            patch(
                "butlers.connectors.whatsapp_user_client._sse_event_stream",
                side_effect=failing_stream,
            ),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            await asyncio.wait_for(connector._sse_event_loop(), timeout=5.0)

        assert call_count >= 2


# ---------------------------------------------------------------------------
# Tests: Handle bridge event
# ---------------------------------------------------------------------------


class TestHandleBridgeEvent:
    """Tests for the bridge event handler."""

    async def test_handle_message_event_buffers_it(
        self, connector: WhatsAppUserClientConnector
    ) -> None:
        """Valid message events are buffered by chat JID."""
        event = {
            "event_type": "message",
            "message_id": "abc123",
            "chat_jid": "5551234567@s.whatsapp.net",
            "sender_jid": "5559876543@s.whatsapp.net",
            "type": "Conversation",
            "content": {"text": "Hello"},
        }
        await connector._handle_bridge_event(event)

        assert "5551234567@s.whatsapp.net" in connector._chat_buffers
        assert len(connector._chat_buffers["5551234567@s.whatsapp.net"].messages) == 1

    async def test_handle_event_updates_last_event_id(
        self, connector: WhatsAppUserClientConnector
    ) -> None:
        """Processing an event updates _last_event_id."""
        event = {
            "message_id": "EVENT-ID-999",
            "chat_jid": "group@g.us",
            "type": "Conversation",
            "content": {"text": "test"},
        }
        await connector._handle_bridge_event(event)
        assert connector._last_event_id == "EVENT-ID-999"

    async def test_handle_event_skips_non_message_types(
        self, connector: WhatsAppUserClientConnector
    ) -> None:
        """Non-message events (status updates, etc.) are ignored."""
        event = {
            "event_type": "presence",
            "chat_jid": "someone@s.whatsapp.net",
            "presence": "available",
        }
        await connector._handle_bridge_event(event)
        # Nothing should be buffered
        assert "someone@s.whatsapp.net" not in connector._chat_buffers

    async def test_handle_event_skips_missing_chat_jid(
        self, connector: WhatsAppUserClientConnector
    ) -> None:
        """Events without chat_jid are skipped."""
        event = {
            "event_type": "message",
            "message_id": "no-jid",
            "type": "Conversation",
            "content": {"text": "orphan"},
        }
        await connector._handle_bridge_event(event)
        assert not connector._chat_buffers


# ---------------------------------------------------------------------------
# Tests: Health state
# ---------------------------------------------------------------------------


class TestHealthState:
    """Tests for health state reporting."""

    def test_healthy_when_running(self, connector: WhatsAppUserClientConnector) -> None:
        connector._running = True
        state, error = connector._get_health_state()
        assert state == "healthy"
        assert error is None

    def test_error_when_not_running(self, connector: WhatsAppUserClientConnector) -> None:
        connector._running = False
        state, error = connector._get_health_state()
        assert state == "error"
        assert error is not None

    def test_degraded_when_bridge_degraded(self, connector: WhatsAppUserClientConnector) -> None:
        connector._running = True
        mock_bridge = MagicMock()
        mock_bridge.is_degraded = True
        mock_bridge.degraded_reason = "Session invalidated"
        connector._bridge_manager = mock_bridge

        state, error = connector._get_health_state()
        assert state == "degraded"
        assert "Session invalidated" in str(error)


# ---------------------------------------------------------------------------
# Tests: Flush scanner
# ---------------------------------------------------------------------------


class TestFlushScanner:
    """Tests for the background flush scanner."""

    async def test_scan_and_flush_skips_empty_buffers(
        self, connector: WhatsAppUserClientConnector
    ) -> None:
        """Flush scanner ignores empty buffers."""
        flushed = []

        async def mock_flush(jid: str) -> None:
            flushed.append(jid)

        connector._flush_chat_buffer = mock_flush  # type: ignore[method-assign]

        # Add an empty buffer
        connector._chat_buffers["empty@g.us"] = ChatBuffer(
            chat_jid="empty@g.us",
            messages=[],
        )

        await connector._scan_and_flush()
        assert not flushed

    async def test_scan_and_flush_flushes_expired_buffers(
        self, connector: WhatsAppUserClientConnector
    ) -> None:
        """Flush scanner flushes buffers whose interval has elapsed."""
        flushed = []

        async def mock_flush(jid: str) -> None:
            flushed.append(jid)

        connector._flush_chat_buffer = mock_flush  # type: ignore[method-assign]

        # Add an expired buffer
        buf = ChatBuffer(chat_jid="old@g.us", messages=[{"id": "1"}])
        # Set last_flush_ts far in the past
        buf.last_flush_ts = time.monotonic() - 1000.0
        connector._chat_buffers["old@g.us"] = buf

        connector._config = WhatsAppUserClientConnectorConfig(
            switchboard_mcp_url="http://localhost:41100/sse",
            endpoint_identity="whatsapp:+15551234567",
            flush_interval_s=600,
        )

        await connector._scan_and_flush()
        assert "old@g.us" in flushed

    async def test_scan_and_flush_skips_unexpired_buffers(
        self, connector: WhatsAppUserClientConnector
    ) -> None:
        """Flush scanner skips buffers that have not yet reached flush_interval_s."""
        flushed = []

        async def mock_flush(jid: str) -> None:
            flushed.append(jid)

        connector._flush_chat_buffer = mock_flush  # type: ignore[method-assign]

        # Add a fresh buffer
        buf = ChatBuffer(chat_jid="new@g.us", messages=[{"id": "1"}])
        # last_flush_ts is just now — not expired
        buf.last_flush_ts = time.monotonic()
        connector._chat_buffers["new@g.us"] = buf

        connector._config = WhatsAppUserClientConnectorConfig(
            switchboard_mcp_url="http://localhost:41100/sse",
            endpoint_identity="whatsapp:+15551234567",
            flush_interval_s=600,
        )

        await connector._scan_and_flush()
        assert "new@g.us" not in flushed
