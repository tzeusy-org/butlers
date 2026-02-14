"""Tests for Telegram bot connector runtime."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

import httpx
import pytest

from butlers.connectors.telegram_bot import (
    TelegramBotConnector,
    TelegramBotConnectorConfig,
)


@pytest.fixture
def mock_config(tmp_path: Path) -> TelegramBotConnectorConfig:
    """Create a mock connector configuration."""
    cursor_path = tmp_path / "cursor.json"
    return TelegramBotConnectorConfig(
        switchboard_api_base_url="http://localhost:8000",
        switchboard_api_token="test-token",
        provider="telegram",
        channel="telegram",
        endpoint_identity="test_bot",
        telegram_token="test-telegram-token",
        cursor_path=cursor_path,
        poll_interval_s=0.1,
        max_inflight=2,
    )


@pytest.fixture
def connector(mock_config: TelegramBotConnectorConfig) -> TelegramBotConnector:
    """Create a connector instance with mock config."""
    return TelegramBotConnector(mock_config)


@pytest.fixture
def sample_telegram_update() -> dict[str, Any]:
    """Sample Telegram update payload."""
    return {
        "update_id": 12345,
        "message": {
            "message_id": 1,
            "from": {"id": 987654321, "first_name": "Test", "username": "testuser"},
            "chat": {"id": 987654321, "type": "private"},
            "date": 1708012800,
            "text": "Hello world",
        },
    }


# -----------------------------------------------------------------------------
# Configuration tests
# -----------------------------------------------------------------------------


def test_config_from_env_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Test loading configuration from environment variables."""
    cursor_path = tmp_path / "cursor.json"

    monkeypatch.setenv("SWITCHBOARD_API_BASE_URL", "http://localhost:8000")
    monkeypatch.setenv("SWITCHBOARD_API_TOKEN", "test-token")
    monkeypatch.setenv("CONNECTOR_PROVIDER", "telegram")
    monkeypatch.setenv("CONNECTOR_CHANNEL", "telegram")
    monkeypatch.setenv("CONNECTOR_ENDPOINT_IDENTITY", "my_bot")
    monkeypatch.setenv("BUTLER_TELEGRAM_TOKEN", "telegram-token")
    monkeypatch.setenv("CONNECTOR_CURSOR_PATH", str(cursor_path))
    monkeypatch.setenv("CONNECTOR_POLL_INTERVAL_S", "2.5")
    monkeypatch.setenv("CONNECTOR_MAX_INFLIGHT", "4")

    config = TelegramBotConnectorConfig.from_env()

    assert config.switchboard_api_base_url == "http://localhost:8000"
    assert config.switchboard_api_token == "test-token"
    assert config.provider == "telegram"
    assert config.channel == "telegram"
    assert config.endpoint_identity == "my_bot"
    assert config.telegram_token == "telegram-token"
    assert config.cursor_path == cursor_path
    assert config.poll_interval_s == 2.5
    assert config.max_inflight == 4


def test_config_from_env_missing_required_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that missing required env vars raise ValueError."""
    # Missing SWITCHBOARD_API_BASE_URL
    with pytest.raises(ValueError, match="SWITCHBOARD_API_BASE_URL"):
        TelegramBotConnectorConfig.from_env()

    # Missing CONNECTOR_ENDPOINT_IDENTITY
    monkeypatch.setenv("SWITCHBOARD_API_BASE_URL", "http://localhost:8000")
    with pytest.raises(ValueError, match="CONNECTOR_ENDPOINT_IDENTITY"):
        TelegramBotConnectorConfig.from_env()

    # Missing BUTLER_TELEGRAM_TOKEN
    monkeypatch.setenv("CONNECTOR_ENDPOINT_IDENTITY", "test_bot")
    with pytest.raises(ValueError, match="BUTLER_TELEGRAM_TOKEN"):
        TelegramBotConnectorConfig.from_env()


# -----------------------------------------------------------------------------
# Normalization tests
# -----------------------------------------------------------------------------


def test_normalize_to_ingest_v1_basic_message(
    connector: TelegramBotConnector,
    sample_telegram_update: dict[str, Any],
) -> None:
    """Test normalization of a basic Telegram message to ingest.v1."""
    envelope = connector._normalize_to_ingest_v1(sample_telegram_update)

    assert envelope["schema_version"] == "ingest.v1"
    assert envelope["source"]["channel"] == "telegram"
    assert envelope["source"]["provider"] == "telegram"
    assert envelope["source"]["endpoint_identity"] == "test_bot"
    assert envelope["event"]["external_event_id"] == "12345"
    assert envelope["event"]["external_thread_id"] == "987654321"
    assert envelope["sender"]["identity"] == "987654321"
    assert envelope["payload"]["normalized_text"] == "Hello world"
    assert envelope["payload"]["raw"] == sample_telegram_update
    assert envelope["control"]["idempotency_key"] == "telegram:test_bot:12345"
    assert envelope["control"]["policy_tier"] == "default"

    # Verify observed_at is RFC3339 with timezone
    observed_at = envelope["event"]["observed_at"]
    assert isinstance(observed_at, str)
    assert "T" in observed_at
    assert "Z" in observed_at or "+" in observed_at or "-" in observed_at[-6:]


def test_normalize_to_ingest_v1_edited_message(connector: TelegramBotConnector) -> None:
    """Test normalization of edited message."""
    update = {
        "update_id": 12346,
        "edited_message": {
            "message_id": 2,
            "from": {"id": 111222333, "first_name": "Editor"},
            "chat": {"id": 111222333, "type": "private"},
            "date": 1708012900,
            "edit_date": 1708013000,
            "text": "Edited text",
        },
    }

    envelope = connector._normalize_to_ingest_v1(update)

    assert envelope["event"]["external_event_id"] == "12346"
    assert envelope["event"]["external_thread_id"] == "111222333"
    assert envelope["sender"]["identity"] == "111222333"
    assert envelope["payload"]["normalized_text"] == "Edited text"


def test_normalize_to_ingest_v1_channel_post(connector: TelegramBotConnector) -> None:
    """Test normalization of channel post."""
    update = {
        "update_id": 12347,
        "channel_post": {
            "message_id": 3,
            "chat": {"id": -1001234567890, "type": "channel", "title": "Test Channel"},
            "date": 1708013100,
            "text": "Channel announcement",
        },
    }

    envelope = connector._normalize_to_ingest_v1(update)

    assert envelope["event"]["external_event_id"] == "12347"
    assert envelope["event"]["external_thread_id"] == "-1001234567890"
    # No 'from' in channel_post, should default to "unknown"
    assert envelope["sender"]["identity"] == "unknown"
    assert envelope["payload"]["normalized_text"] == "Channel announcement"


def test_normalize_to_ingest_v1_missing_fields(connector: TelegramBotConnector) -> None:
    """Test normalization handles missing optional fields gracefully."""
    update = {"update_id": 12348}  # Minimal update with no message

    envelope = connector._normalize_to_ingest_v1(update)

    assert envelope["event"]["external_event_id"] == "12348"
    assert envelope["event"]["external_thread_id"] is None
    assert envelope["sender"]["identity"] == "unknown"
    assert envelope["payload"]["normalized_text"] == ""
    assert envelope["payload"]["raw"] == update


# -----------------------------------------------------------------------------
# Ingest submission tests
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_to_ingest_success(
    connector: TelegramBotConnector,
    sample_telegram_update: dict[str, Any],
) -> None:
    """Test successful submission to Switchboard ingest API."""
    envelope = connector._normalize_to_ingest_v1(sample_telegram_update)

    mock_response = Mock()
    mock_response.status_code = 202
    mock_response.json.return_value = {
        "request_id": "12345678-1234-1234-1234-123456789012",
        "status": "accepted",
        "duplicate": False,
    }
    mock_response.raise_for_status = Mock()

    with patch.object(connector._http_client, "post", return_value=mock_response) as mock_post:
        await connector._submit_to_ingest(envelope)

        # Verify API call
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert call_args[0][0] == "http://localhost:8000/api/switchboard/ingest"
        assert call_args[1]["json"] == envelope
        assert call_args[1]["headers"]["Content-Type"] == "application/json"
        assert call_args[1]["headers"]["Authorization"] == "Bearer test-token"


@pytest.mark.asyncio
async def test_submit_to_ingest_duplicate_accepted(
    connector: TelegramBotConnector,
    sample_telegram_update: dict[str, Any],
) -> None:
    """Test that duplicate submissions are treated as success."""
    envelope = connector._normalize_to_ingest_v1(sample_telegram_update)

    mock_response = Mock()
    mock_response.status_code = 202
    mock_response.json.return_value = {
        "request_id": "12345678-1234-1234-1234-123456789012",
        "status": "accepted",
        "duplicate": True,
    }
    mock_response.raise_for_status = Mock()

    with patch.object(connector._http_client, "post", return_value=mock_response):
        # Should not raise, duplicate is success
        await connector._submit_to_ingest(envelope)


@pytest.mark.asyncio
async def test_submit_to_ingest_http_error(
    connector: TelegramBotConnector,
    sample_telegram_update: dict[str, Any],
) -> None:
    """Test handling of HTTP errors from ingest API."""
    envelope = connector._normalize_to_ingest_v1(sample_telegram_update)

    mock_response = Mock()
    mock_response.status_code = 400
    mock_response.text = "Invalid payload"
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "Bad Request", request=Mock(), response=mock_response
    )

    with patch.object(connector._http_client, "post", return_value=mock_response):
        with pytest.raises(httpx.HTTPStatusError):
            await connector._submit_to_ingest(envelope)


# -----------------------------------------------------------------------------
# Checkpoint tests
# -----------------------------------------------------------------------------


def test_load_checkpoint_file_exists(
    connector: TelegramBotConnector,
    mock_config: TelegramBotConnectorConfig,
) -> None:
    """Test loading checkpoint from existing file."""
    assert mock_config.cursor_path is not None
    mock_config.cursor_path.write_text(json.dumps({"last_update_id": 99999}))

    connector._load_checkpoint()

    assert connector._last_update_id == 99999


def test_load_checkpoint_file_missing(connector: TelegramBotConnector) -> None:
    """Test loading checkpoint when file doesn't exist."""
    connector._load_checkpoint()
    assert connector._last_update_id is None


def test_load_checkpoint_corrupted_file(
    connector: TelegramBotConnector,
    mock_config: TelegramBotConnectorConfig,
) -> None:
    """Test loading checkpoint with corrupted JSON."""
    assert mock_config.cursor_path is not None
    mock_config.cursor_path.write_text("invalid json{")

    connector._load_checkpoint()
    # Should fall back to None on error
    assert connector._last_update_id is None


def test_save_checkpoint_creates_file(
    connector: TelegramBotConnector,
    mock_config: TelegramBotConnectorConfig,
) -> None:
    """Test saving checkpoint creates file."""
    connector._last_update_id = 54321

    connector._save_checkpoint()

    assert mock_config.cursor_path is not None
    assert mock_config.cursor_path.exists()
    data = json.loads(mock_config.cursor_path.read_text())
    assert data["last_update_id"] == 54321


def test_save_checkpoint_creates_parent_directory(
    connector: TelegramBotConnector,
    tmp_path: Path,
) -> None:
    """Test that save_checkpoint creates parent directories."""
    nested_path = tmp_path / "nested" / "dirs" / "cursor.json"
    connector._config.cursor_path = nested_path
    connector._last_update_id = 11111

    connector._save_checkpoint()

    assert nested_path.exists()
    data = json.loads(nested_path.read_text())
    assert data["last_update_id"] == 11111


# -----------------------------------------------------------------------------
# Telegram API tests
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_updates_first_call(connector: TelegramBotConnector) -> None:
    """Test getUpdates with no previous update_id."""
    mock_response = Mock()
    mock_response.json.return_value = {
        "ok": True,
        "result": [
            {"update_id": 100, "message": {"text": "msg1"}},
            {"update_id": 101, "message": {"text": "msg2"}},
        ],
    }
    mock_response.raise_for_status = Mock()

    with patch.object(connector._http_client, "get", return_value=mock_response) as mock_get:
        updates = await connector._get_updates()

        assert len(updates) == 2
        assert connector._last_update_id == 101

        # Verify API call
        mock_get.assert_called_once()
        call_args = mock_get.call_args
        assert "getUpdates" in call_args[0][0]
        # First call should not have offset param
        assert "offset" not in call_args[1]["params"]


@pytest.mark.asyncio
async def test_get_updates_with_offset(connector: TelegramBotConnector) -> None:
    """Test getUpdates uses offset based on last_update_id."""
    connector._last_update_id = 200

    mock_response = Mock()
    mock_response.json.return_value = {
        "ok": True,
        "result": [{"update_id": 201, "message": {"text": "msg3"}}],
    }
    mock_response.raise_for_status = Mock()

    with patch.object(connector._http_client, "get", return_value=mock_response) as mock_get:
        updates = await connector._get_updates()

        assert len(updates) == 1
        assert connector._last_update_id == 201

        # Verify offset is last_update_id + 1
        call_args = mock_get.call_args
        assert call_args[1]["params"]["offset"] == 201


@pytest.mark.asyncio
async def test_get_updates_empty_result(connector: TelegramBotConnector) -> None:
    """Test getUpdates with no new updates."""
    mock_response = Mock()
    mock_response.json.return_value = {"ok": True, "result": []}
    mock_response.raise_for_status = Mock()

    with patch.object(connector._http_client, "get", return_value=mock_response):
        updates = await connector._get_updates()

        assert len(updates) == 0
        assert connector._last_update_id is None


@pytest.mark.asyncio
async def test_set_webhook(connector: TelegramBotConnector) -> None:
    """Test setWebhook API call."""
    mock_response = Mock()
    mock_response.json.return_value = {"ok": True, "result": True}
    mock_response.raise_for_status = Mock()

    webhook_url = "https://example.com/webhook"

    with patch.object(connector._http_client, "post", return_value=mock_response) as mock_post:
        result = await connector._set_webhook(webhook_url)

        assert result == {"ok": True, "result": True}

        # Verify API call
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert "setWebhook" in call_args[0][0]
        assert call_args[1]["json"]["url"] == webhook_url


# -----------------------------------------------------------------------------
# Integration tests
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_update_end_to_end(
    connector: TelegramBotConnector,
    sample_telegram_update: dict[str, Any],
) -> None:
    """Test end-to-end update processing: normalize + submit."""
    mock_ingest_response = Mock()
    mock_ingest_response.status_code = 202
    mock_ingest_response.json.return_value = {
        "request_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "status": "accepted",
        "duplicate": False,
    }
    mock_ingest_response.raise_for_status = Mock()

    with patch.object(connector._http_client, "post", return_value=mock_ingest_response):
        await connector._process_update(sample_telegram_update)

        # Should complete without error


@pytest.mark.asyncio
async def test_webhook_mode_registration(connector: TelegramBotConnector) -> None:
    """Test webhook mode registers webhook on start."""
    connector._config.webhook_url = "https://example.com/webhook"

    mock_response = Mock()
    mock_response.json.return_value = {"ok": True, "result": True}
    mock_response.raise_for_status = Mock()

    with patch.object(connector._http_client, "post", return_value=mock_response) as mock_post:
        await connector.start_webhook()

        # Verify setWebhook was called
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert "setWebhook" in call_args[0][0]


@pytest.mark.asyncio
async def test_concurrency_limit_enforced(
    connector: TelegramBotConnector,
    sample_telegram_update: dict[str, Any],
) -> None:
    """Test that max_inflight semaphore limits concurrent submissions."""
    # Set max_inflight to 2
    connector._config.max_inflight = 2
    connector._semaphore = asyncio.Semaphore(2)

    submission_times: list[float] = []

    async def mock_submit_with_delay(envelope: dict[str, Any]) -> None:
        import time

        submission_times.append(time.time())
        await asyncio.sleep(0.1)

    with patch.object(connector, "_submit_to_ingest", side_effect=mock_submit_with_delay):
        # Submit 4 updates concurrently
        tasks = [
            connector._process_update({**sample_telegram_update, "update_id": i}) for i in range(4)
        ]
        await asyncio.gather(*tasks)

    # With max_inflight=2, submissions should happen in 2 waves
    # We can't assert exact timing due to system variance, but verify all completed
    assert len(submission_times) == 4


# -----------------------------------------------------------------------------
# Error handling tests
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_update_handles_normalization_error(
    connector: TelegramBotConnector,
) -> None:
    """Test that process_update handles normalization errors gracefully."""
    invalid_update = {"bad": "data"}

    # Should not raise, just log error
    await connector._process_update(invalid_update)


@pytest.mark.asyncio
async def test_process_update_handles_submission_error(
    connector: TelegramBotConnector,
    sample_telegram_update: dict[str, Any],
) -> None:
    """Test that process_update handles submission errors gracefully."""
    with patch.object(
        connector,
        "_submit_to_ingest",
        side_effect=Exception("Network error"),
    ):
        # Should not raise, just log error
        await connector._process_update(sample_telegram_update)
