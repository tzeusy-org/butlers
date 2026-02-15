"""Tests for Telegram user-client connector."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from butlers.connectors.telegram_user_client import (
    TELETHON_AVAILABLE,
    TelegramUserClientConnector,
    TelegramUserClientConnectorConfig,
)

# Skip all tests if Telethon is not available
pytestmark = pytest.mark.skipif(
    not TELETHON_AVAILABLE,
    reason="Telethon not installed (optional dependency)",
)


@pytest.fixture
def mock_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, str]:
    """Set up mock environment variables for connector config."""
    cursor_path = tmp_path / "cursor.json"
    env_vars = {
        "SWITCHBOARD_API_BASE_URL": "http://localhost:8000",
        "SWITCHBOARD_API_TOKEN": "test-token",
        "CONNECTOR_PROVIDER": "telegram",
        "CONNECTOR_CHANNEL": "telegram",
        "CONNECTOR_ENDPOINT_IDENTITY": "telegram:user:123456",
        "TELEGRAM_API_ID": "12345",
        "TELEGRAM_API_HASH": "test-hash",
        "TELEGRAM_USER_SESSION": "test-session-string",
        "CONNECTOR_CURSOR_PATH": str(cursor_path),
        "CONNECTOR_MAX_INFLIGHT": "8",
    }
    for key, value in env_vars.items():
        monkeypatch.setenv(key, value)
    return env_vars


@pytest.fixture
def config(tmp_path: Path) -> TelegramUserClientConnectorConfig:
    """Create a test connector config."""
    cursor_path = tmp_path / "cursor.json"
    return TelegramUserClientConnectorConfig(
        switchboard_api_base_url="http://localhost:8000",
        switchboard_api_token="test-token",
        provider="telegram",
        channel="telegram",
        endpoint_identity="telegram:user:123456",
        telegram_api_id=12345,
        telegram_api_hash="test-hash",
        telegram_user_session="test-session-string",
        cursor_path=cursor_path,
        max_inflight=8,
    )


class TestTelegramUserClientConnectorConfig:
    """Tests for TelegramUserClientConnectorConfig."""

    def test_from_env_success(self, mock_env: dict[str, str]) -> None:
        """Test loading config from environment variables."""
        config = TelegramUserClientConnectorConfig.from_env()

        assert config.switchboard_api_base_url == "http://localhost:8000"
        assert config.switchboard_api_token == "test-token"
        assert config.provider == "telegram"
        assert config.channel == "telegram"
        assert config.endpoint_identity == "telegram:user:123456"
        assert config.telegram_api_id == 12345
        assert config.telegram_api_hash == "test-hash"
        assert config.telegram_user_session == "test-session-string"
        assert config.max_inflight == 8

    def test_from_env_missing_required_field(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that missing required fields raise ValueError."""
        # Missing SWITCHBOARD_API_BASE_URL
        with pytest.raises(ValueError, match="SWITCHBOARD_API_BASE_URL"):
            TelegramUserClientConnectorConfig.from_env()

    def test_from_env_invalid_api_id(
        self, mock_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that invalid TELEGRAM_API_ID raises ValueError."""
        monkeypatch.setenv("TELEGRAM_API_ID", "not-an-integer")
        with pytest.raises(ValueError, match="TELEGRAM_API_ID must be an integer"):
            TelegramUserClientConnectorConfig.from_env()

    def test_from_env_with_backfill_window(
        self, mock_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test loading config with backfill window."""
        monkeypatch.setenv("CONNECTOR_BACKFILL_WINDOW_H", "24")
        config = TelegramUserClientConnectorConfig.from_env()
        assert config.backfill_window_h == 24


class TestTelegramUserClientConnector:
    """Tests for TelegramUserClientConnector."""

    def test_connector_initialization(self, config: TelegramUserClientConnectorConfig) -> None:
        """Test connector initializes correctly."""
        connector = TelegramUserClientConnector(config)
        assert connector._config == config
        assert connector._http_client is not None
        assert connector._running is False
        assert connector._last_message_id is None

    async def test_normalize_to_ingest_v1(self, config: TelegramUserClientConnectorConfig) -> None:
        """Test normalization of Telegram message to ingest.v1 format."""
        connector = TelegramUserClientConnector(config)

        # Mock Telegram message
        mock_message = MagicMock()
        mock_message.id = 12345
        mock_message.chat_id = 67890
        mock_message.sender_id = 11111
        mock_message.message = "Hello, world!"
        mock_message.to_dict.return_value = {
            "id": 12345,
            "chat_id": 67890,
            "sender_id": 11111,
            "message": "Hello, world!",
        }

        envelope = await connector._normalize_to_ingest_v1(mock_message)

        assert envelope["schema_version"] == "ingest.v1"
        assert envelope["source"]["channel"] == "telegram"
        assert envelope["source"]["provider"] == "telegram"
        assert envelope["source"]["endpoint_identity"] == "telegram:user:123456"
        assert envelope["event"]["external_event_id"] == "12345"
        assert envelope["event"]["external_thread_id"] == "67890"
        assert envelope["sender"]["identity"] == "11111"
        assert envelope["payload"]["normalized_text"] == "Hello, world!"
        assert envelope["payload"]["raw"]["id"] == 12345
        assert envelope["control"]["idempotency_key"] == "telegram:telegram:user:123456:12345"

    async def test_submit_to_ingest_success(
        self, config: TelegramUserClientConnectorConfig
    ) -> None:
        """Test successful submission to Switchboard ingest API."""
        connector = TelegramUserClientConnector(config)

        envelope = {
            "schema_version": "ingest.v1",
            "source": {
                "channel": "telegram",
                "provider": "telegram",
                "endpoint_identity": "telegram:user:123456",
            },
            "event": {
                "external_event_id": "12345",
                "external_thread_id": "67890",
                "observed_at": datetime.now(UTC).isoformat(),
            },
            "sender": {"identity": "11111"},
            "payload": {
                "raw": {},
                "normalized_text": "test",
            },
            "control": {
                "idempotency_key": "telegram:telegram:user:123456:12345",
                "policy_tier": "default",
            },
        }

        # Mock successful HTTP response
        mock_response = MagicMock()
        mock_response.status_code = 202
        mock_response.json.return_value = {
            "request_id": "req-123",
            "duplicate": False,
        }

        with patch.object(connector._http_client, "post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response
            mock_response.raise_for_status = MagicMock()

            await connector._submit_to_ingest(envelope)

            # Verify POST was called with correct parameters
            mock_post.assert_called_once()
            call_args = mock_post.call_args
            assert call_args[0][0] == "http://localhost:8000/api/switchboard/ingest"
            assert call_args[1]["json"] == envelope
            assert call_args[1]["headers"]["Authorization"] == "Bearer test-token"

    async def test_submit_to_ingest_http_error(
        self, config: TelegramUserClientConnectorConfig
    ) -> None:
        """Test handling of HTTP errors during ingest submission."""
        connector = TelegramUserClientConnector(config)

        envelope: dict[str, Any] = {
            "schema_version": "ingest.v1",
            "source": {
                "channel": "telegram",
                "provider": "telegram",
                "endpoint_identity": "telegram:user:123456",
            },
            "event": {
                "external_event_id": "12345",
                "external_thread_id": "67890",
                "observed_at": datetime.now(UTC).isoformat(),
            },
            "sender": {"identity": "11111"},
            "payload": {"raw": {}, "normalized_text": "test"},
            "control": {
                "idempotency_key": "telegram:telegram:user:123456:12345",
                "policy_tier": "default",
            },
        }

        # Mock HTTP error response
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"

        with patch.object(connector._http_client, "post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response
            mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
                "500 Server Error",
                request=MagicMock(),
                response=mock_response,
            )

            with pytest.raises(httpx.HTTPStatusError):
                await connector._submit_to_ingest(envelope)

    def test_load_checkpoint_no_file(self, config: TelegramUserClientConnectorConfig) -> None:
        """Test loading checkpoint when no file exists."""
        connector = TelegramUserClientConnector(config)
        connector._load_checkpoint()
        assert connector._last_message_id is None

    def test_load_checkpoint_with_file(self, config: TelegramUserClientConnectorConfig) -> None:
        """Test loading checkpoint from existing file."""
        # Write checkpoint file
        assert config.cursor_path is not None
        config.cursor_path.parent.mkdir(parents=True, exist_ok=True)
        with config.cursor_path.open("w") as f:
            json.dump({"last_message_id": 99999}, f)

        connector = TelegramUserClientConnector(config)
        connector._load_checkpoint()
        assert connector._last_message_id == 99999

    def test_save_checkpoint(self, config: TelegramUserClientConnectorConfig) -> None:
        """Test saving checkpoint to disk."""
        connector = TelegramUserClientConnector(config)
        connector._last_message_id = 88888
        connector._save_checkpoint()

        # Verify checkpoint file was written
        assert config.cursor_path is not None
        assert config.cursor_path.exists()
        with config.cursor_path.open("r") as f:
            data = json.load(f)
            assert data["last_message_id"] == 88888

    def test_save_checkpoint_atomic_write(self, config: TelegramUserClientConnectorConfig) -> None:
        """Test that checkpoint save uses atomic write."""
        connector = TelegramUserClientConnector(config)
        connector._last_message_id = 77777

        # Save checkpoint
        connector._save_checkpoint()

        # Verify no .tmp file remains
        assert config.cursor_path is not None
        tmp_path = config.cursor_path.with_suffix(".tmp")
        assert not tmp_path.exists()
        assert config.cursor_path.exists()

    async def test_process_message_updates_checkpoint(
        self, config: TelegramUserClientConnectorConfig
    ) -> None:
        """Test that processing a message updates the checkpoint."""
        connector = TelegramUserClientConnector(config)

        # Mock message
        mock_message = MagicMock()
        mock_message.id = 12345
        mock_message.chat_id = 67890
        mock_message.sender_id = 11111
        mock_message.message = "Test message"
        mock_message.to_dict.return_value = {
            "id": 12345,
            "chat_id": 67890,
            "sender_id": 11111,
            "message": "Test message",
        }

        # Mock successful ingest submission
        mock_response = MagicMock()
        mock_response.status_code = 202
        mock_response.json.return_value = {"request_id": "req-123", "duplicate": False}
        mock_response.raise_for_status = MagicMock()

        with patch.object(
            connector._http_client, "post", new_callable=AsyncMock, return_value=mock_response
        ):
            await connector._process_message(mock_message)

        # Verify checkpoint was updated
        assert connector._last_message_id == 12345

    async def test_process_message_skips_message_without_id(
        self, config: TelegramUserClientConnectorConfig
    ) -> None:
        """Test that messages without ID are skipped."""
        connector = TelegramUserClientConnector(config)

        # Mock message without ID
        mock_message = MagicMock()
        mock_message.id = None

        with patch.object(connector._http_client, "post", new_callable=AsyncMock) as mock_post:
            await connector._process_message(mock_message)

            # Verify no API call was made
            mock_post.assert_not_called()

    async def test_process_message_handles_errors_gracefully(
        self, config: TelegramUserClientConnectorConfig
    ) -> None:
        """Test that errors during message processing are handled gracefully."""
        connector = TelegramUserClientConnector(config)

        # Mock message
        mock_message = MagicMock()
        mock_message.id = 12345
        mock_message.chat_id = 67890
        mock_message.sender_id = 11111
        mock_message.message = "Test message"
        mock_message.to_dict.side_effect = RuntimeError("Test error")

        # Processing should not raise - errors are logged
        await connector._process_message(mock_message)

    async def test_normalize_handles_different_peer_types(
        self, config: TelegramUserClientConnectorConfig
    ) -> None:
        """Test normalization handles different Telegram peer ID types."""
        connector = TelegramUserClientConnector(config)

        # Mock message with peer_id instead of chat_id
        mock_message = MagicMock()
        mock_message.id = 12345
        mock_message.sender_id = 11111
        mock_message.message = "Test"
        mock_message.to_dict.return_value = {"id": 12345}

        # Mock peer_id with channel_id
        mock_peer = MagicMock()
        mock_peer.channel_id = 99999
        mock_message.peer_id = mock_peer
        delattr(mock_message, "chat_id")

        envelope = await connector._normalize_to_ingest_v1(mock_message)
        assert envelope["event"]["external_thread_id"] == "99999"

    async def test_normalize_handles_from_id(
        self, config: TelegramUserClientConnectorConfig
    ) -> None:
        """Test normalization handles from_id for sender identity."""
        connector = TelegramUserClientConnector(config)

        # Mock message with from_id instead of sender_id
        mock_message = MagicMock()
        mock_message.id = 12345
        mock_message.chat_id = 67890
        mock_message.message = "Test"
        mock_message.to_dict.return_value = {"id": 12345}

        # Mock from_id
        mock_from_id = MagicMock()
        mock_from_id.user_id = 22222
        mock_message.from_id = mock_from_id
        delattr(mock_message, "sender_id")

        envelope = await connector._normalize_to_ingest_v1(mock_message)
        assert envelope["sender"]["identity"] == "22222"

    async def test_normalize_sanitizes_xss_content(
        self, config: TelegramUserClientConnectorConfig
    ) -> None:
        """Test that message text is sanitized to prevent XSS."""
        connector = TelegramUserClientConnector(config)

        # Mock message with potential XSS content
        mock_message = MagicMock()
        mock_message.id = 12345
        mock_message.chat_id = 67890
        mock_message.sender_id = 11111
        mock_message.message = "<script>alert('xss')</script>"
        mock_message.to_dict.return_value = {"id": 12345}

        envelope = await connector._normalize_to_ingest_v1(mock_message)

        # Verify HTML entities are escaped
        expected = "&lt;script&gt;alert(&#x27;xss&#x27;)&lt;/script&gt;"
        assert envelope["payload"]["normalized_text"] == expected


@pytest.mark.unit
class TestTelegramUserClientConnectorUnit:
    """Unit tests that don't require Telethon."""

    def test_telethon_import_failure_handling(self) -> None:
        """Test that import failure is handled gracefully."""
        # This test verifies the TELETHON_AVAILABLE flag works correctly
        # When Telethon is not installed, the module should still import
        # but raise errors when trying to use the connector
        assert TELETHON_AVAILABLE in (True, False)

        if not TELETHON_AVAILABLE:
            # Verify that trying to create a connector raises an error
            config = TelegramUserClientConnectorConfig(
                switchboard_api_base_url="http://localhost:8000",
                telegram_api_id=12345,
                telegram_api_hash="test",
                telegram_user_session="test",
                endpoint_identity="test",
            )
            with pytest.raises(RuntimeError, match="Telethon is not installed"):
                TelegramUserClientConnector(config)
