"""Tests for Discord user connector runtime.

DRAFT — v2-only WIP, not production-ready.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.connectors.discord_user import (
    GATEWAY_OPCODE_DISPATCH,
    GATEWAY_OPCODE_HEARTBEAT_ACK,
    GATEWAY_OPCODE_HELLO,
    GATEWAY_OPCODE_IDENTIFY,
    GATEWAY_OPCODE_INVALID_SESSION,
    GATEWAY_OPCODE_RECONNECT,
    GATEWAY_OPCODE_RESUME,
    DiscordUserConnector,
    DiscordUserConnectorConfig,
    _extract_normalized_text,
    run_discord_user_connector,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_config(tmp_path: Path) -> DiscordUserConnectorConfig:
    """Create a mock connector configuration."""
    cursor_path = tmp_path / "discord_cursor.json"
    return DiscordUserConnectorConfig(
        switchboard_mcp_url="http://localhost:40100/sse",
        provider="discord",
        channel="discord",
        endpoint_identity="discord:user:123456789",
        discord_bot_token="Bot test-token",
        cursor_path=cursor_path,
        max_inflight=2,
        health_port=40084,
    )


@pytest.fixture
def connector(mock_config: DiscordUserConnectorConfig) -> DiscordUserConnector:
    """Create a connector instance with mock config."""
    return DiscordUserConnector(mock_config)


@pytest.fixture
def sample_message_create_event() -> dict[str, Any]:
    """Sample Discord MESSAGE_CREATE event data."""
    return {
        "id": "1234567890123456789",
        "channel_id": "987654321098765432",
        "guild_id": "111222333444555666",
        "author": {
            "id": "777888999000111222",
            "username": "TestUser",
            "discriminator": "0001",
        },
        "content": "Hello, world!",
        "timestamp": "2024-01-01T12:00:00.000Z",
        "edited_timestamp": None,
        "attachments": [],
        "embeds": [],
    }


@pytest.fixture
def sample_dm_message_event() -> dict[str, Any]:
    """Sample Discord DM MESSAGE_CREATE event data (no guild_id)."""
    return {
        "id": "9876543210987654321",
        "channel_id": "555444333222111000",
        "author": {
            "id": "111000222333444555",
            "username": "FriendUser",
            "discriminator": "0002",
        },
        "content": "Hey there!",
        "timestamp": "2024-01-01T13:00:00.000Z",
        "attachments": [],
        "embeds": [],
    }


# ---------------------------------------------------------------------------
# _extract_normalized_text tests
# ---------------------------------------------------------------------------


class TestExtractNormalizedText:
    """Tests for _extract_normalized_text helper function."""

    def test_returns_content_for_text_message(self) -> None:
        """Text message returns content directly."""
        msg: dict[str, Any] = {"content": "Hello world"}
        assert _extract_normalized_text(msg) == "Hello world"

    def test_returns_none_for_empty_message(self) -> None:
        """Empty message with no content/attachments/embeds returns None."""
        msg: dict[str, Any] = {"content": ""}
        assert _extract_normalized_text(msg) is None

    def test_returns_attachment_descriptor(self) -> None:
        """Message with attachment returns [Attachment: filename] descriptor."""
        msg: dict[str, Any] = {
            "content": "",
            "attachments": [{"id": "1", "filename": "photo.jpg"}],
        }
        result = _extract_normalized_text(msg)
        assert result == "[Attachment: photo.jpg]"

    def test_returns_multiple_attachment_descriptors(self) -> None:
        """Message with multiple attachments returns all descriptors."""
        msg: dict[str, Any] = {
            "content": "",
            "attachments": [
                {"id": "1", "filename": "doc.pdf"},
                {"id": "2", "filename": "image.png"},
            ],
        }
        result = _extract_normalized_text(msg)
        assert result == "[Attachment: doc.pdf] [Attachment: image.png]"

    def test_content_takes_priority_over_attachments(self) -> None:
        """Content takes priority over attachment descriptors."""
        msg: dict[str, Any] = {
            "content": "Here's a file",
            "attachments": [{"id": "1", "filename": "file.txt"}],
        }
        assert _extract_normalized_text(msg) == "Here's a file"

    def test_returns_embed_descriptor(self) -> None:
        """Message with embed returns [Embed: title] descriptor."""
        msg: dict[str, Any] = {
            "content": "",
            "embeds": [{"title": "Some Article", "description": "An interesting read"}],
        }
        result = _extract_normalized_text(msg)
        assert result == "[Embed: Some Article]"

    def test_embed_falls_back_to_description(self) -> None:
        """Embed without title falls back to description."""
        msg: dict[str, Any] = {
            "content": "",
            "embeds": [{"description": "No title here"}],
        }
        result = _extract_normalized_text(msg)
        assert result == "[Embed: No title here]"

    def test_returns_sticker_descriptor(self) -> None:
        """Message with sticker returns [Sticker: name] descriptor."""
        msg: dict[str, Any] = {
            "content": "",
            "sticker_items": [{"id": "1", "name": "thumbsup"}],
        }
        result = _extract_normalized_text(msg)
        assert result == "[Sticker: thumbsup]"

    def test_no_content_no_attachments_returns_none(self) -> None:
        """Message with no content, attachments, embeds, or stickers returns None."""
        msg: dict[str, Any] = {
            "content": "",
            "attachments": [],
            "embeds": [],
            "sticker_items": [],
        }
        assert _extract_normalized_text(msg) is None

    def test_missing_keys_returns_none(self) -> None:
        """Message dict with missing keys returns None safely."""
        assert _extract_normalized_text({}) is None


# ---------------------------------------------------------------------------
# Configuration tests
# ---------------------------------------------------------------------------


class TestDiscordUserConnectorConfig:
    """Tests for DiscordUserConnectorConfig."""

    def test_from_env_success(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Test loading configuration from environment variables."""
        cursor_path = tmp_path / "cursor.json"

        monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:40100/sse")
        monkeypatch.setenv("CONNECTOR_PROVIDER", "discord")
        monkeypatch.setenv("CONNECTOR_CHANNEL", "discord")
        monkeypatch.setenv("CONNECTOR_ENDPOINT_IDENTITY", "discord:user:123")
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "my-bot-token")
        monkeypatch.setenv("CONNECTOR_CURSOR_PATH", str(cursor_path))
        monkeypatch.setenv("CONNECTOR_MAX_INFLIGHT", "4")
        monkeypatch.setenv("CONNECTOR_HEALTH_PORT", "40090")
        monkeypatch.setenv("DISCORD_GUILD_ALLOWLIST", "111,222,333")
        monkeypatch.setenv("DISCORD_CHANNEL_ALLOWLIST", "444,555")

        config = DiscordUserConnectorConfig.from_env()

        assert config.switchboard_mcp_url == "http://localhost:40100/sse"
        assert config.provider == "discord"
        assert config.channel == "discord"
        assert config.endpoint_identity == "discord:user:123"
        assert config.discord_bot_token == "my-bot-token"
        assert config.cursor_path == cursor_path
        assert config.max_inflight == 4
        assert config.health_port == 40090
        assert config.guild_allowlist == frozenset({"111", "222", "333"})
        assert config.channel_allowlist == frozenset({"444", "555"})

    def test_from_env_missing_switchboard_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Missing SWITCHBOARD_MCP_URL raises ValueError."""
        monkeypatch.delenv("SWITCHBOARD_MCP_URL", raising=False)
        with pytest.raises(ValueError, match="SWITCHBOARD_MCP_URL"):
            DiscordUserConnectorConfig.from_env()

    def test_from_env_missing_endpoint_identity(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Missing CONNECTOR_ENDPOINT_IDENTITY raises ValueError."""
        monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:40100/sse")
        monkeypatch.delenv("CONNECTOR_ENDPOINT_IDENTITY", raising=False)
        with pytest.raises(ValueError, match="CONNECTOR_ENDPOINT_IDENTITY"):
            DiscordUserConnectorConfig.from_env()

    def test_from_env_missing_bot_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Missing DISCORD_BOT_TOKEN raises ValueError."""
        monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:40100/sse")
        monkeypatch.setenv("CONNECTOR_ENDPOINT_IDENTITY", "discord:user:123")
        monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
        with pytest.raises(ValueError, match="DISCORD_BOT_TOKEN"):
            DiscordUserConnectorConfig.from_env()

    def test_from_env_empty_allowlists(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Empty allowlists mean no filtering (all guilds/channels allowed)."""
        monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:40100/sse")
        monkeypatch.setenv("CONNECTOR_ENDPOINT_IDENTITY", "discord:user:123")
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "token")
        monkeypatch.delenv("DISCORD_GUILD_ALLOWLIST", raising=False)
        monkeypatch.delenv("DISCORD_CHANNEL_ALLOWLIST", raising=False)

        config = DiscordUserConnectorConfig.from_env()

        assert config.guild_allowlist == frozenset()
        assert config.channel_allowlist == frozenset()

    def test_default_provider_channel(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Default provider and channel are 'discord'."""
        monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:40100/sse")
        monkeypatch.setenv("CONNECTOR_ENDPOINT_IDENTITY", "discord:user:123")
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "token")
        monkeypatch.delenv("CONNECTOR_PROVIDER", raising=False)
        monkeypatch.delenv("CONNECTOR_CHANNEL", raising=False)

        config = DiscordUserConnectorConfig.from_env()

        assert config.provider == "discord"
        assert config.channel == "discord"


# ---------------------------------------------------------------------------
# Normalization tests
# ---------------------------------------------------------------------------


class TestNormalizeToIngestV1:
    """Tests for _normalize_to_ingest_v1."""

    def test_basic_message_create(
        self,
        connector: DiscordUserConnector,
        sample_message_create_event: dict[str, Any],
    ) -> None:
        """Basic MESSAGE_CREATE event normalizes correctly."""
        envelope = connector._normalize_to_ingest_v1("MESSAGE_CREATE", sample_message_create_event)

        assert envelope is not None
        assert envelope["schema_version"] == "ingest.v1"
        assert envelope["source"]["channel"] == "discord"
        assert envelope["source"]["provider"] == "discord"
        assert envelope["source"]["endpoint_identity"] == "discord:user:123456789"
        assert envelope["event"]["external_event_id"] == "1234567890123456789"
        assert envelope["event"]["external_thread_id"] == "987654321098765432"
        assert envelope["sender"]["identity"] == "777888999000111222"
        assert envelope["payload"]["normalized_text"] == "Hello, world!"
        assert envelope["payload"]["raw"]["event_type"] == "MESSAGE_CREATE"
        assert envelope["payload"]["raw"]["guild_id"] == "111222333444555666"
        assert (
            envelope["control"]["idempotency_key"]
            == "discord:discord:user:123456789:1234567890123456789"
        )
        assert envelope["control"]["policy_tier"] == "default"

        # observed_at must be RFC3339
        observed_at = envelope["event"]["observed_at"]
        assert "T" in observed_at
        assert "Z" in observed_at or "+" in observed_at or "-" in observed_at[-6:]

    def test_dm_message_create(
        self,
        connector: DiscordUserConnector,
        sample_dm_message_event: dict[str, Any],
    ) -> None:
        """DM MESSAGE_CREATE (no guild_id) normalizes correctly."""
        envelope = connector._normalize_to_ingest_v1("MESSAGE_CREATE", sample_dm_message_event)

        assert envelope is not None
        assert envelope["event"]["external_thread_id"] == "555444333222111000"
        assert envelope["sender"]["identity"] == "111000222333444555"
        assert envelope["payload"]["normalized_text"] == "Hey there!"

    def test_message_update(
        self,
        connector: DiscordUserConnector,
        sample_message_create_event: dict[str, Any],
    ) -> None:
        """MESSAGE_UPDATE event normalizes correctly."""
        updated = {**sample_message_create_event, "content": "Updated content"}
        envelope = connector._normalize_to_ingest_v1("MESSAGE_UPDATE", updated)

        assert envelope is not None
        assert envelope["payload"]["normalized_text"] == "Updated content"
        assert envelope["payload"]["raw"]["event_type"] == "MESSAGE_UPDATE"

    def test_message_delete_produces_tombstone(self, connector: DiscordUserConnector) -> None:
        """MESSAGE_DELETE events produce tombstone envelope with [Message deleted]."""
        delete_event: dict[str, Any] = {
            "id": "1111111111111111111",
            "channel_id": "2222222222222222222",
            "guild_id": "3333333333333333333",
        }
        envelope = connector._normalize_to_ingest_v1("MESSAGE_DELETE", delete_event)

        assert envelope is not None
        assert envelope["payload"]["normalized_text"] == "[Message deleted]"
        assert envelope["event"]["external_event_id"] == "1111111111111111111"

    def test_event_without_id_returns_none(self, connector: DiscordUserConnector) -> None:
        """Events without a message ID return None."""
        event: dict[str, Any] = {"channel_id": "123", "content": "hello"}
        result = connector._normalize_to_ingest_v1("MESSAGE_CREATE", event)
        assert result is None

    def test_empty_content_no_attachments_returns_none(
        self, connector: DiscordUserConnector
    ) -> None:
        """Events with empty content and no media return None."""
        event: dict[str, Any] = {
            "id": "9999999999999999999",
            "channel_id": "8888888888888888888",
            "author": {"id": "7777777777777777777"},
            "content": "",
            "attachments": [],
            "embeds": [],
        }
        result = connector._normalize_to_ingest_v1("MESSAGE_CREATE", event)
        assert result is None

    def test_message_with_no_author(self, connector: DiscordUserConnector) -> None:
        """Events without author field use 'unknown' as sender identity."""
        event: dict[str, Any] = {
            "id": "1234567890000000001",
            "channel_id": "9876543210000000001",
            "content": "System message",
        }
        envelope = connector._normalize_to_ingest_v1("MESSAGE_CREATE", event)

        assert envelope is not None
        assert envelope["sender"]["identity"] == "unknown"


# ---------------------------------------------------------------------------
# Allowlist filtering tests
# ---------------------------------------------------------------------------


class TestAllowlistFiltering:
    """Tests for _is_allowed scope filtering logic."""

    def test_no_allowlists_allows_all(self, connector: DiscordUserConnector) -> None:
        """Empty allowlists allow all events."""
        assert connector._is_allowed({"guild_id": "anything", "channel_id": "anything"})

    def test_guild_allowlist_permits_matching_guild(
        self, mock_config: DiscordUserConnectorConfig, tmp_path: Path
    ) -> None:
        """Event from allowed guild passes the filter."""
        from dataclasses import replace

        config = replace(mock_config, guild_allowlist=frozenset({"111", "222"}))
        conn = DiscordUserConnector(config)

        assert conn._is_allowed({"guild_id": "111", "channel_id": "any"})
        assert conn._is_allowed({"guild_id": "222", "channel_id": "any"})

    def test_guild_allowlist_blocks_non_matching_guild(
        self, mock_config: DiscordUserConnectorConfig
    ) -> None:
        """Event from unlisted guild is blocked."""
        from dataclasses import replace

        config = replace(mock_config, guild_allowlist=frozenset({"111", "222"}))
        conn = DiscordUserConnector(config)

        assert not conn._is_allowed({"guild_id": "999", "channel_id": "any"})

    def test_channel_allowlist_permits_matching_channel(
        self, mock_config: DiscordUserConnectorConfig
    ) -> None:
        """Event from allowed channel passes the filter."""
        from dataclasses import replace

        config = replace(mock_config, channel_allowlist=frozenset({"444", "555"}))
        conn = DiscordUserConnector(config)

        assert conn._is_allowed({"guild_id": None, "channel_id": "444"})

    def test_channel_allowlist_blocks_non_matching_channel(
        self, mock_config: DiscordUserConnectorConfig
    ) -> None:
        """Event from unlisted channel is blocked."""
        from dataclasses import replace

        config = replace(mock_config, channel_allowlist=frozenset({"444", "555"}))
        conn = DiscordUserConnector(config)

        assert not conn._is_allowed({"guild_id": None, "channel_id": "999"})

    def test_both_allowlists_must_match(self, mock_config: DiscordUserConnectorConfig) -> None:
        """Both guild and channel must match when both allowlists are set."""
        from dataclasses import replace

        config = replace(
            mock_config,
            guild_allowlist=frozenset({"111"}),
            channel_allowlist=frozenset({"444"}),
        )
        conn = DiscordUserConnector(config)

        # Both match
        assert conn._is_allowed({"guild_id": "111", "channel_id": "444"})
        # Guild matches but channel doesn't
        assert not conn._is_allowed({"guild_id": "111", "channel_id": "999"})
        # Channel matches but guild doesn't
        assert not conn._is_allowed({"guild_id": "999", "channel_id": "444"})
        # Neither matches
        assert not conn._is_allowed({"guild_id": "999", "channel_id": "999"})


# ---------------------------------------------------------------------------
# Checkpoint tests
# ---------------------------------------------------------------------------


class TestCheckpoint:
    """Tests for checkpoint load/save/update."""

    def test_load_checkpoint_missing_file(self, connector: DiscordUserConnector) -> None:
        """Missing checkpoint file starts fresh without error."""
        connector._load_checkpoint()
        assert connector._channel_checkpoints == {}

    def test_load_checkpoint_from_file(
        self, connector: DiscordUserConnector, tmp_path: Path
    ) -> None:
        """Valid checkpoint file is loaded correctly."""
        checkpoint_data = {
            "channel_checkpoints": {
                "111": "1234567890000000001",
                "222": "9876543210000000001",
            }
        }
        assert connector._config.cursor_path is not None
        connector._config.cursor_path.write_text(json.dumps(checkpoint_data))

        connector._load_checkpoint()

        assert connector._channel_checkpoints == {
            "111": "1234567890000000001",
            "222": "9876543210000000001",
        }

    def test_save_checkpoint(self, connector: DiscordUserConnector) -> None:
        """Checkpoint is saved correctly to file."""
        connector._channel_checkpoints = {
            "aaa": "111",
            "bbb": "222",
        }

        connector._save_checkpoint()

        assert connector._config.cursor_path is not None
        assert connector._config.cursor_path.exists()
        data = json.loads(connector._config.cursor_path.read_text())
        assert data["channel_checkpoints"] == {"aaa": "111", "bbb": "222"}

    def test_update_checkpoint(self, connector: DiscordUserConnector) -> None:
        """_update_checkpoint updates in-memory state."""
        connector._update_checkpoint("chan1", "msg999")
        assert connector._channel_checkpoints["chan1"] == "msg999"

        # Second update to same channel overwrites
        connector._update_checkpoint("chan1", "msg1000")
        assert connector._channel_checkpoints["chan1"] == "msg1000"

    def test_save_checkpoint_atomic(self, connector: DiscordUserConnector) -> None:
        """Checkpoint is written via temp file for atomicity."""
        connector._channel_checkpoints = {"c1": "m1"}
        assert connector._config.cursor_path is not None

        connector._save_checkpoint()

        # Temp file should be gone (replaced)
        tmp_path = connector._config.cursor_path.with_suffix(".tmp")
        assert not tmp_path.exists()
        assert connector._config.cursor_path.exists()

    def test_load_checkpoint_corrupt_file(self, connector: DiscordUserConnector) -> None:
        """Corrupt checkpoint file starts fresh without crashing."""
        assert connector._config.cursor_path is not None
        connector._config.cursor_path.write_text("not-valid-json{{")

        connector._load_checkpoint()  # Should not raise

        assert connector._channel_checkpoints == {}


# ---------------------------------------------------------------------------
# Health status tests
# ---------------------------------------------------------------------------


class TestHealthStatus:
    """Tests for get_health_status."""

    async def test_initial_health_status(self, connector: DiscordUserConnector) -> None:
        """Initial status is unknown connectivity."""
        status = await connector.get_health_status()

        assert status.source_api_connectivity == "unknown"
        assert status.uptime_seconds >= 0

    async def test_connected_health_status(self, connector: DiscordUserConnector) -> None:
        """Connected Gateway shows 'connected' and 'healthy'."""
        connector._gateway_connected = True
        status = await connector.get_health_status()

        assert status.status == "healthy"
        assert status.source_api_connectivity == "connected"

    async def test_disconnected_health_status(self, connector: DiscordUserConnector) -> None:
        """Disconnected Gateway shows 'disconnected' and 'unhealthy'."""
        connector._gateway_connected = False
        status = await connector.get_health_status()

        assert status.status == "unhealthy"
        assert status.source_api_connectivity == "disconnected"

    async def test_health_status_timestamps(self, connector: DiscordUserConnector) -> None:
        """Health status includes checkpoint/ingest timestamps when set."""
        import time

        connector._last_checkpoint_save = time.time()
        connector._last_ingest_submit = time.time()

        status = await connector.get_health_status()

        assert status.last_checkpoint_save_at is not None
        assert "T" in status.last_checkpoint_save_at
        assert status.last_ingest_submit_at is not None
        assert "T" in status.last_ingest_submit_at


# ---------------------------------------------------------------------------
# Health state reporting tests
# ---------------------------------------------------------------------------


class TestGetHealthState:
    """Tests for _get_health_state (used by switchboard heartbeat)."""

    def test_healthy_state(self, connector: DiscordUserConnector) -> None:
        """No failures and connected gateway returns healthy."""
        connector._gateway_connected = True
        connector._consecutive_failures = 0

        state, msg = connector._get_health_state()

        assert state == "healthy"
        assert msg is None

    def test_error_state_on_disconnected(self, connector: DiscordUserConnector) -> None:
        """Disconnected gateway returns error state."""
        connector._gateway_connected = False
        connector._consecutive_failures = 3

        state, msg = connector._get_health_state()

        assert state == "error"
        assert msg is not None
        assert "consecutive_failures=3" in msg

    def test_degraded_state_on_consecutive_failures(self, connector: DiscordUserConnector) -> None:
        """Consecutive failures without full disconnect returns degraded."""
        connector._gateway_connected = True
        connector._consecutive_failures = 2

        state, msg = connector._get_health_state()

        assert state == "degraded"
        assert msg is not None
        assert "2" in msg


# ---------------------------------------------------------------------------
# Gateway message handler tests
# ---------------------------------------------------------------------------


class TestHandleGatewayMessage:
    """Tests for _handle_gateway_message gateway dispatch logic."""

    async def test_hello_starts_heartbeat_and_identify(
        self, connector: DiscordUserConnector
    ) -> None:
        """HELLO opcode triggers heartbeat start and IDENTIFY."""
        connector._ws = AsyncMock()
        connector._ws.closed = False
        connector._ws.send_json = AsyncMock()

        with (
            patch.object(connector, "_start_discord_heartbeat", new=AsyncMock()) as mock_heartbeat,
            patch.object(connector, "_identify_or_resume", new=AsyncMock()) as mock_identify,
        ):
            await connector._handle_gateway_message(
                {
                    "op": GATEWAY_OPCODE_HELLO,
                    "d": {"heartbeat_interval": 41250},
                }
            )

            mock_heartbeat.assert_awaited_once_with(41250)
            mock_identify.assert_awaited_once()

    async def test_heartbeat_ack_updates_timestamp(self, connector: DiscordUserConnector) -> None:
        """HEARTBEAT_ACK opcode updates last ack timestamp."""
        before = connector._last_heartbeat_ack

        await connector._handle_gateway_message({"op": GATEWAY_OPCODE_HEARTBEAT_ACK})

        assert connector._last_heartbeat_ack >= before

    async def test_ready_event_sets_session_id(self, connector: DiscordUserConnector) -> None:
        """READY dispatch event stores session_id and records metrics."""
        connector._metrics = MagicMock()

        await connector._handle_gateway_message(
            {
                "op": GATEWAY_OPCODE_DISPATCH,
                "t": "READY",
                "s": 1,
                "d": {
                    "session_id": "test-session-abc",
                    "user": {"id": "123456789"},
                },
            }
        )

        assert connector._session_id == "test-session-abc"
        assert connector._sequence == 1

    async def test_sequence_number_updated_on_dispatch(
        self, connector: DiscordUserConnector
    ) -> None:
        """DISPATCH messages update the sequence number."""
        with patch.object(connector, "_process_dispatch_event", new=AsyncMock()):
            await connector._handle_gateway_message(
                {
                    "op": GATEWAY_OPCODE_DISPATCH,
                    "t": "MESSAGE_CREATE",
                    "s": 42,
                    "d": {"id": "1", "channel_id": "2", "content": "hi"},
                }
            )

        assert connector._sequence == 42

    async def test_reconnect_closes_websocket(self, connector: DiscordUserConnector) -> None:
        """RECONNECT opcode closes the WebSocket connection."""
        mock_ws = AsyncMock()
        mock_ws.closed = False
        connector._ws = mock_ws

        await connector._handle_gateway_message({"op": GATEWAY_OPCODE_RECONNECT})

        mock_ws.close.assert_awaited_once()

    async def test_invalid_session_non_resumable_clears_state(
        self, connector: DiscordUserConnector
    ) -> None:
        """Non-resumable INVALID_SESSION clears session state."""
        connector._session_id = "old-session"
        connector._sequence = 100
        mock_ws = AsyncMock()
        mock_ws.closed = False
        connector._ws = mock_ws

        await connector._handle_gateway_message({"op": GATEWAY_OPCODE_INVALID_SESSION, "d": False})

        assert connector._session_id is None
        assert connector._sequence is None

    async def test_invalid_session_resumable_preserves_state(
        self, connector: DiscordUserConnector
    ) -> None:
        """Resumable INVALID_SESSION preserves session state for RESUME."""
        connector._session_id = "old-session"
        connector._sequence = 100
        mock_ws = AsyncMock()
        mock_ws.closed = False
        connector._ws = mock_ws

        await connector._handle_gateway_message({"op": GATEWAY_OPCODE_INVALID_SESSION, "d": True})

        assert connector._session_id == "old-session"
        assert connector._sequence == 100


# ---------------------------------------------------------------------------
# Identify / Resume tests
# ---------------------------------------------------------------------------


class TestIdentifyOrResume:
    """Tests for _identify_or_resume handshake logic."""

    async def test_sends_identify_when_no_session(self, connector: DiscordUserConnector) -> None:
        """IDENTIFY is sent when no session_id exists."""
        mock_ws = AsyncMock()
        connector._ws = mock_ws
        connector._session_id = None
        connector._sequence = None

        await connector._identify_or_resume()

        mock_ws.send_json.assert_awaited_once()
        sent = mock_ws.send_json.call_args[0][0]
        assert sent["op"] == GATEWAY_OPCODE_IDENTIFY
        assert "token" in sent["d"]
        assert "intents" in sent["d"]

    async def test_sends_resume_when_session_exists(self, connector: DiscordUserConnector) -> None:
        """RESUME is sent when session_id and sequence are available."""
        mock_ws = AsyncMock()
        connector._ws = mock_ws
        connector._session_id = "abc-session"
        connector._sequence = 50

        await connector._identify_or_resume()

        mock_ws.send_json.assert_awaited_once()
        sent = mock_ws.send_json.call_args[0][0]
        assert sent["op"] == GATEWAY_OPCODE_RESUME
        assert sent["d"]["session_id"] == "abc-session"
        assert sent["d"]["seq"] == 50

    async def test_no_op_when_ws_is_none(self, connector: DiscordUserConnector) -> None:
        """_identify_or_resume does nothing when WebSocket is None."""
        connector._ws = None
        # Should not raise
        await connector._identify_or_resume()


# ---------------------------------------------------------------------------
# Submit to ingest tests
# ---------------------------------------------------------------------------


class TestSubmitToIngest:
    """Tests for _submit_to_ingest."""

    async def test_successful_submission(self, connector: DiscordUserConnector) -> None:
        """Successful ingest submission updates last_ingest_submit."""
        envelope = {
            "schema_version": "ingest.v1",
            "source": {"channel": "discord", "provider": "discord", "endpoint_identity": "x"},
            "event": {"external_event_id": "1", "external_thread_id": "2", "observed_at": "now"},
            "sender": {"identity": "user1"},
            "payload": {"raw": {}, "normalized_text": "hi"},
            "control": {"idempotency_key": "discord:x:1", "policy_tier": "default"},
        }

        with patch.object(
            connector._mcp_client,
            "call_tool",
            new=AsyncMock(return_value={"status": "accepted", "request_id": "req-123"}),
        ):
            assert connector._last_ingest_submit is None
            await connector._submit_to_ingest(envelope)
            assert connector._last_ingest_submit is not None

    async def test_tool_error_raises(self, connector: DiscordUserConnector) -> None:
        """Ingest tool error response raises RuntimeError."""
        envelope = {
            "schema_version": "ingest.v1",
            "source": {"channel": "discord", "provider": "discord", "endpoint_identity": "x"},
            "event": {"external_event_id": "1", "external_thread_id": "2", "observed_at": "now"},
            "sender": {"identity": "user1"},
            "payload": {"raw": {}, "normalized_text": "hi"},
            "control": {"idempotency_key": "discord:x:1", "policy_tier": "default"},
        }

        with patch.object(
            connector._mcp_client,
            "call_tool",
            new=AsyncMock(return_value={"status": "error", "error": "Ingest rejected"}),
        ):
            with pytest.raises(RuntimeError, match="Ingest tool error"):
                await connector._submit_to_ingest(envelope)

    async def test_duplicate_submission_is_success(self, connector: DiscordUserConnector) -> None:
        """Duplicate submission is treated as success (not an error)."""
        envelope = {
            "schema_version": "ingest.v1",
            "source": {"channel": "discord", "provider": "discord", "endpoint_identity": "x"},
            "event": {"external_event_id": "1", "external_thread_id": "2", "observed_at": "now"},
            "sender": {"identity": "user1"},
            "payload": {"raw": {}, "normalized_text": "hi"},
            "control": {"idempotency_key": "discord:x:1", "policy_tier": "default"},
        }

        with patch.object(
            connector._mcp_client,
            "call_tool",
            new=AsyncMock(
                return_value={"status": "accepted", "duplicate": True, "request_id": "req-old"}
            ),
        ):
            # Should not raise
            await connector._submit_to_ingest(envelope)
            assert connector._last_ingest_submit is not None


# ---------------------------------------------------------------------------
# Process dispatch event integration tests
# ---------------------------------------------------------------------------


class TestProcessDispatchEvent:
    """Integration tests for _process_dispatch_event."""

    async def test_allowed_event_is_ingested(
        self,
        connector: DiscordUserConnector,
        sample_message_create_event: dict[str, Any],
    ) -> None:
        """Allowed event is normalized and submitted to Switchboard."""
        with patch.object(
            connector._mcp_client,
            "call_tool",
            new=AsyncMock(return_value={"status": "accepted", "request_id": "req-1"}),
        ):
            await connector._process_dispatch_event("MESSAGE_CREATE", sample_message_create_event)

            # Checkpoint should be updated after successful ingest
            channel_id = sample_message_create_event["channel_id"]
            msg_id = sample_message_create_event["id"]
            assert connector._channel_checkpoints.get(channel_id) == msg_id

    async def test_filtered_event_is_not_ingested(self, connector: DiscordUserConnector) -> None:
        """Event blocked by allowlist is not submitted."""
        from dataclasses import replace

        config = replace(connector._config, channel_allowlist=frozenset({"only-this-channel"}))
        conn = DiscordUserConnector(config)

        event: dict[str, Any] = {
            "id": "1",
            "channel_id": "wrong-channel",
            "content": "filtered",
        }

        with patch.object(conn._mcp_client, "call_tool", new=AsyncMock()) as mock_tool:
            await conn._process_dispatch_event("MESSAGE_CREATE", event)
            mock_tool.assert_not_awaited()

    async def test_event_with_no_content_is_not_ingested(
        self, connector: DiscordUserConnector
    ) -> None:
        """Event with no extractable text is not submitted."""
        event: dict[str, Any] = {
            "id": "2",
            "channel_id": "123",
            "content": "",
            "attachments": [],
            "embeds": [],
        }

        with patch.object(connector._mcp_client, "call_tool", new=AsyncMock()) as mock_tool:
            await connector._process_dispatch_event("MESSAGE_CREATE", event)
            mock_tool.assert_not_awaited()


# ---------------------------------------------------------------------------
# run_discord_user_connector entry point
# ---------------------------------------------------------------------------


class TestRunDiscordUserConnector:
    """Tests for the CLI entry point."""

    async def test_run_discord_user_connector_loads_config_and_runs(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """run_discord_user_connector loads config from env and starts connector."""
        cursor_path = tmp_path / "cursor.json"
        monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:40100/sse")
        monkeypatch.setenv("CONNECTOR_ENDPOINT_IDENTITY", "discord:user:999")
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
        monkeypatch.setenv("CONNECTOR_CURSOR_PATH", str(cursor_path))

        mock_connector = AsyncMock()
        mock_connector.start = AsyncMock(side_effect=KeyboardInterrupt)
        mock_connector.stop = AsyncMock()

        with (
            patch(
                "butlers.connectors.discord_user.DiscordUserConnector",
                return_value=mock_connector,
            ),
            patch("butlers.connectors.discord_user.configure_logging"),
        ):
            await run_discord_user_connector()

        mock_connector.stop.assert_awaited_once()
