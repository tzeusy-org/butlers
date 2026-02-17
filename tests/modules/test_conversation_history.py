"""Tests for conversation history loading in the routing pipeline.

Tests cover:
- HistoryConfig dataclass
- HISTORY_STRATEGY mapping
- _load_realtime_history() with time and count windows
- _load_email_history() with token truncation
- _format_history_context()
- _load_conversation_history() dispatcher
- Integration with MessagePipeline.process()
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.modules.pipeline import (
    HISTORY_STRATEGY,
    HistoryConfig,
    MessagePipeline,
    _format_history_context,
    _load_conversation_history,
    _load_email_history,
    _load_realtime_history,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# HistoryConfig
# ---------------------------------------------------------------------------


class TestHistoryConfig:
    """Verify HistoryConfig dataclass."""

    def test_default_realtime_config(self):
        """HistoryConfig has correct defaults for realtime."""
        config = HistoryConfig(strategy="realtime")
        assert config.strategy == "realtime"
        assert config.max_time_window_minutes == 15
        assert config.max_message_count == 30
        assert config.max_tokens == 50000

    def test_default_email_config(self):
        """HistoryConfig has correct defaults for email."""
        config = HistoryConfig(strategy="email")
        assert config.strategy == "email"
        assert config.max_tokens == 50000


# ---------------------------------------------------------------------------
# HISTORY_STRATEGY
# ---------------------------------------------------------------------------


class TestHistoryStrategy:
    """Verify channel strategy mapping."""

    def test_realtime_channels(self):
        """Real-time messaging channels use realtime strategy."""
        assert HISTORY_STRATEGY["telegram"] == "realtime"
        assert HISTORY_STRATEGY["whatsapp"] == "realtime"
        assert HISTORY_STRATEGY["slack"] == "realtime"
        assert HISTORY_STRATEGY["discord"] == "realtime"

    def test_email_channel(self):
        """Email uses email strategy."""
        assert HISTORY_STRATEGY["email"] == "email"

    def test_none_channels(self):
        """API and MCP use none strategy."""
        assert HISTORY_STRATEGY["api"] == "none"
        assert HISTORY_STRATEGY["mcp"] == "none"


# ---------------------------------------------------------------------------
# _load_realtime_history
# ---------------------------------------------------------------------------


class TestLoadRealtimeHistory:
    """Verify real-time message history loading."""

    async def test_loads_time_window(self):
        """Loads messages within time window."""
        now = datetime.now(UTC)
        thread_id = "chat:123"

        # Mock pool with messages
        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn

        # Time window: 2 messages
        mock_conn.fetch.side_effect = [
            [
                {
                    "raw_content": "message 1",
                    "sender_id": "user1",
                    "received_at": now - timedelta(minutes=5),
                    "raw_metadata": {},
                },
                {
                    "raw_content": "message 2",
                    "sender_id": "user2",
                    "received_at": now - timedelta(minutes=3),
                    "raw_metadata": {},
                },
            ],
            [],  # Count window: empty
        ]

        messages = await _load_realtime_history(
            mock_pool, thread_id, now, max_time_window_minutes=15, max_message_count=30
        )

        assert len(messages) == 2
        assert messages[0]["raw_content"] == "message 1"
        assert messages[1]["raw_content"] == "message 2"

    async def test_loads_count_window(self):
        """Loads messages by count window."""
        now = datetime.now(UTC)
        thread_id = "chat:456"

        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn

        # Time window: empty, count window: 1 message
        mock_conn.fetch.side_effect = [
            [],  # Time window
            [
                {
                    "raw_content": "old message",
                    "sender_id": "user3",
                    "received_at": now - timedelta(hours=1),
                    "raw_metadata": {},
                },
            ],
        ]

        messages = await _load_realtime_history(
            mock_pool, thread_id, now, max_time_window_minutes=15, max_message_count=30
        )

        assert len(messages) == 1
        assert messages[0]["raw_content"] == "old message"

    async def test_deduplicates_union(self):
        """Deduplicates messages appearing in both windows."""
        now = datetime.now(UTC)
        thread_id = "chat:789"
        msg_time = now - timedelta(minutes=5)

        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn

        duplicate_msg = {
            "raw_content": "duplicate",
            "sender_id": "user1",
            "received_at": msg_time,
            "raw_metadata": {},
        }

        # Both windows return the same message
        mock_conn.fetch.side_effect = [
            [duplicate_msg],
            [duplicate_msg],
        ]

        messages = await _load_realtime_history(
            mock_pool, thread_id, now, max_time_window_minutes=15, max_message_count=30
        )

        assert len(messages) == 1
        assert messages[0]["raw_content"] == "duplicate"

    async def test_sorts_chronologically(self):
        """Messages are sorted chronologically (oldest first)."""
        now = datetime.now(UTC)
        thread_id = "chat:sorted"

        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn

        msg1_time = now - timedelta(minutes=10)
        msg2_time = now - timedelta(minutes=5)
        msg3_time = now - timedelta(minutes=2)

        mock_conn.fetch.side_effect = [
            [
                {
                    "raw_content": "msg2",
                    "sender_id": "u",
                    "received_at": msg2_time,
                    "raw_metadata": {},
                },
                {
                    "raw_content": "msg3",
                    "sender_id": "u",
                    "received_at": msg3_time,
                    "raw_metadata": {},
                },
            ],
            [
                {
                    "raw_content": "msg1",
                    "sender_id": "u",
                    "received_at": msg1_time,
                    "raw_metadata": {},
                },
            ],
        ]

        messages = await _load_realtime_history(
            mock_pool, thread_id, now, max_time_window_minutes=15, max_message_count=30
        )

        assert len(messages) == 3
        assert messages[0]["raw_content"] == "msg1"
        assert messages[1]["raw_content"] == "msg2"
        assert messages[2]["raw_content"] == "msg3"


# ---------------------------------------------------------------------------
# _load_email_history
# ---------------------------------------------------------------------------


class TestLoadEmailHistory:
    """Verify email chain history loading."""

    async def test_loads_full_chain_under_limit(self):
        """Loads full email chain if under token limit."""
        now = datetime.now(UTC)
        thread_id = "email:thread123"

        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn

        mock_conn.fetch.return_value = [
            {
                "raw_content": "First email",
                "sender_id": "alice@example.com",
                "received_at": now - timedelta(days=2),
                "raw_metadata": {},
            },
            {
                "raw_content": "Reply email",
                "sender_id": "bob@example.com",
                "received_at": now - timedelta(days=1),
                "raw_metadata": {},
            },
        ]

        messages = await _load_email_history(mock_pool, thread_id, now, max_tokens=50000)

        assert len(messages) == 2
        assert messages[0]["raw_content"] == "First email"
        assert messages[1]["raw_content"] == "Reply email"

    async def test_truncates_from_oldest_end(self):
        """Truncates email chain from oldest end when over token limit, preserving newest."""
        now = datetime.now(UTC)
        thread_id = "email:longthread"

        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn

        # Each message is ~10 chars, limit is 20 chars (5 tokens * 4)
        mock_conn.fetch.return_value = [
            {
                "raw_content": "a" * 10,
                "sender_id": "u1",
                "received_at": now - timedelta(hours=3),
                "raw_metadata": {},
            },
            {
                "raw_content": "b" * 10,
                "sender_id": "u2",
                "received_at": now - timedelta(hours=2),
                "raw_metadata": {},
            },
            {
                "raw_content": "c" * 10,
                "sender_id": "u3",
                "received_at": now - timedelta(hours=1),
                "raw_metadata": {},
            },
        ]

        messages = await _load_email_history(mock_pool, thread_id, now, max_tokens=5)

        # Should keep newest 2 messages (20 chars) and drop the oldest
        assert len(messages) == 2
        assert messages[0]["raw_content"] == "b" * 10
        assert messages[1]["raw_content"] == "c" * 10


# ---------------------------------------------------------------------------
# _format_history_context
# ---------------------------------------------------------------------------


class TestFormatHistoryContext:
    """Verify history formatting for CC prompt."""

    def test_empty_messages_returns_empty_string(self):
        """Empty message list returns empty string."""
        assert _format_history_context([]) == ""

    def test_formats_single_message(self):
        """Formats single message correctly."""
        messages = [
            {
                "sender_id": "user123",
                "received_at": datetime(2026, 2, 16, 10, 0, 0, tzinfo=UTC),
                "raw_content": "Hello world",
            }
        ]

        result = _format_history_context(messages)

        assert "## Recent Conversation History" in result
        assert "**user123** (2026-02-16T10:00:00+00:00):" in result
        assert "Hello world" in result
        assert "---" in result

    def test_formats_multiple_messages(self):
        """Formats multiple messages in order."""
        messages = [
            {
                "sender_id": "alice",
                "received_at": datetime(2026, 2, 16, 10, 0, 0, tzinfo=UTC),
                "raw_content": "First message",
            },
            {
                "sender_id": "bob",
                "received_at": datetime(2026, 2, 16, 10, 1, 0, tzinfo=UTC),
                "raw_content": "Second message",
            },
        ]

        result = _format_history_context(messages)

        assert "**alice**" in result
        assert "**bob**" in result
        assert result.index("First message") < result.index("Second message")


# ---------------------------------------------------------------------------
# _load_conversation_history
# ---------------------------------------------------------------------------


class TestLoadConversationHistory:
    """Verify conversation history dispatcher."""

    async def test_returns_empty_if_no_thread_identity(self):
        """Returns empty string if no thread identity."""
        mock_pool = MagicMock()
        result = await _load_conversation_history(mock_pool, "telegram", None, datetime.now(UTC))
        assert result == ""

    async def test_returns_empty_for_none_strategy(self):
        """Returns empty string for channels with none strategy."""
        mock_pool = MagicMock()
        result = await _load_conversation_history(mock_pool, "api", "thread123", datetime.now(UTC))
        assert result == ""

    @patch("butlers.modules.pipeline._load_realtime_history")
    async def test_calls_realtime_for_telegram(self, mock_realtime):
        """Calls realtime loader for telegram channel."""
        mock_pool = MagicMock()
        now = datetime.now(UTC)
        mock_realtime.return_value = []

        await _load_conversation_history(mock_pool, "telegram", "chat123", now)

        mock_realtime.assert_called_once()
        assert mock_realtime.call_args[0][0] == mock_pool
        assert mock_realtime.call_args[0][1] == "chat123"
        assert mock_realtime.call_args[0][2] == now

    @patch("butlers.modules.pipeline._load_email_history")
    async def test_calls_email_for_email_channel(self, mock_email):
        """Calls email loader for email channel."""
        mock_pool = MagicMock()
        now = datetime.now(UTC)
        mock_email.return_value = []

        await _load_conversation_history(mock_pool, "email", "thread456", now)

        mock_email.assert_called_once()
        assert mock_email.call_args[0][0] == mock_pool
        assert mock_email.call_args[0][1] == "thread456"

    async def test_handles_exceptions_gracefully(self):
        """Handles exceptions and returns empty string."""
        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        mock_conn.fetch.side_effect = Exception("Database error")

        result = await _load_conversation_history(
            mock_pool, "telegram", "chat789", datetime.now(UTC)
        )

        assert result == ""


# ---------------------------------------------------------------------------
# Integration with MessagePipeline.process()
# ---------------------------------------------------------------------------


class TestPipelineHistoryIntegration:
    """Verify history loading integrates with pipeline process."""

    @patch("butlers.modules.pipeline._load_conversation_history")
    @patch("butlers.tools.switchboard.routing.classify._load_available_butlers")
    @patch("butlers.modules.pipeline._build_routing_prompt")
    async def test_process_loads_history_for_telegram(
        self, mock_build_prompt, mock_load_butlers, mock_load_history
    ):
        """Pipeline process loads history for telegram messages."""
        mock_pool = MagicMock()
        mock_dispatch = AsyncMock()
        mock_dispatch.return_value = MagicMock(
            output="Routed to general",
            tool_calls=[
                {
                    "name": "route_to_butler",
                    "input": {"butler": "general"},
                    "result": {"success": True},
                }
            ],
        )

        mock_load_butlers.return_value = [{"name": "general", "description": "General butler"}]
        mock_load_history.return_value = (
            "## Recent Conversation History\n\nPrevious message\n\n---\n"
        )
        mock_build_prompt.return_value = "Route this message"

        pipeline = MessagePipeline(
            switchboard_pool=mock_pool,
            dispatch_fn=mock_dispatch,
            source_butler="switchboard",
            enable_ingress_dedupe=False,
        )

        await pipeline.process(
            message_text="Hello",
            tool_name="telegram.process_update",
            tool_args={
                "source_channel": "telegram",
                "chat_id": "123",
                "request_id": str(uuid.uuid4()),
            },
        )

        # Verify history was loaded
        mock_load_history.assert_called_once()
        call_args = mock_load_history.call_args[0]
        assert call_args[0] == mock_pool
        assert call_args[1] == "telegram"
        assert call_args[2] == "123"  # thread identity from chat_id

        # Verify history was passed to prompt builder
        mock_build_prompt.assert_called_once()
        assert (
            mock_build_prompt.call_args[0][2]
            == "## Recent Conversation History\n\nPrevious message\n\n---\n"
        )


# ---------------------------------------------------------------------------
# Direction-aware _format_history_context
# ---------------------------------------------------------------------------


class TestFormatHistoryContextDirection:
    """Verify direction-aware formatting in _format_history_context."""

    def test_inbound_message_uses_sender_id_prefix(self):
        """Inbound messages show '**sender_id** (timestamp):' prefix."""
        messages = [
            {
                "sender_id": "user123",
                "received_at": datetime(2026, 2, 16, 10, 0, 0, tzinfo=UTC),
                "raw_content": "Hello",
                "direction": "inbound",
            }
        ]
        result = _format_history_context(messages)
        assert "**user123** (2026-02-16T10:00:00+00:00):" in result
        assert "butler →" not in result

    def test_outbound_message_uses_butler_arrow_prefix(self):
        """Outbound messages show '**butler → {sender_id}** (timestamp):' prefix."""
        messages = [
            {
                "sender_id": "relationship",
                "received_at": datetime(2026, 2, 16, 10, 0, 5, tzinfo=UTC),
                "raw_content": "Got it! I've stored the address.",
                "direction": "outbound",
            }
        ]
        result = _format_history_context(messages)
        assert "**butler → relationship** (2026-02-16T10:00:05+00:00):" in result
        assert "Got it! I've stored the address." in result
        # Should not use bare sender_id prefix for outbound
        assert "**relationship** (2026-02-16T10:00:05+00:00):" not in result

    def test_mixed_inbound_outbound_messages(self):
        """Mixed conversation shows correct prefixes for each direction."""
        messages = [
            {
                "sender_id": "user42",
                "received_at": datetime(2026, 2, 16, 10, 0, 0, tzinfo=UTC),
                "raw_content": "Dua um lives in 71 nim road 804975",
                "direction": "inbound",
            },
            {
                "sender_id": "relationship",
                "received_at": datetime(2026, 2, 16, 10, 0, 5, tzinfo=UTC),
                "raw_content": "Got it! I've stored Dua um's address as 71 nim road 804975.",
                "direction": "outbound",
            },
        ]
        result = _format_history_context(messages)

        assert "**user42** (2026-02-16T10:00:00+00:00):" in result
        assert "**butler → relationship** (2026-02-16T10:00:05+00:00):" in result
        assert "Dua um lives in 71 nim road 804975" in result
        assert "Got it! I've stored Dua um's address as 71 nim road 804975." in result
        # Inbound appears before outbound
        assert result.index("user42") < result.index("butler → relationship")

    def test_missing_direction_defaults_to_inbound(self):
        """Messages without direction field default to inbound formatting."""
        messages = [
            {
                "sender_id": "legacy_user",
                "received_at": datetime(2026, 2, 16, 10, 0, 0, tzinfo=UTC),
                "raw_content": "Legacy message",
                # No 'direction' key — backwards compatibility
            }
        ]
        result = _format_history_context(messages)
        assert "**legacy_user** (2026-02-16T10:00:00+00:00):" in result
        assert "butler →" not in result

    def test_history_sql_includes_direction_in_realtime_query(self):
        """_load_realtime_history SQL includes COALESCE(direction, 'inbound') AS direction."""
        import inspect

        import butlers.modules.pipeline as pipeline_module

        source = inspect.getsource(pipeline_module._load_realtime_history)
        assert "COALESCE(direction, 'inbound') AS direction" in source

    def test_history_sql_includes_direction_in_email_query(self):
        """_load_email_history SQL includes COALESCE(direction, 'inbound') AS direction."""
        import inspect

        import butlers.modules.pipeline as pipeline_module

        source = inspect.getsource(pipeline_module._load_email_history)
        assert "COALESCE(direction, 'inbound') AS direction" in source
