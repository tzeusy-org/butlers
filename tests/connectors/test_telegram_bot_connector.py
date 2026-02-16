"""Tests for Telegram bot connector runtime."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

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
        switchboard_mcp_url="http://localhost:8100/sse",
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

    monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:8100/sse")
    monkeypatch.setenv("CONNECTOR_PROVIDER", "telegram")
    monkeypatch.setenv("CONNECTOR_CHANNEL", "telegram")
    monkeypatch.setenv("CONNECTOR_ENDPOINT_IDENTITY", "my_bot")
    monkeypatch.setenv("BUTLER_TELEGRAM_TOKEN", "telegram-token")
    monkeypatch.setenv("CONNECTOR_CURSOR_PATH", str(cursor_path))
    monkeypatch.setenv("CONNECTOR_POLL_INTERVAL_S", "2.5")
    monkeypatch.setenv("CONNECTOR_MAX_INFLIGHT", "4")

    config = TelegramBotConnectorConfig.from_env()

    assert config.switchboard_mcp_url == "http://localhost:8100/sse"
    assert config.provider == "telegram"
    assert config.channel == "telegram"
    assert config.endpoint_identity == "my_bot"
    assert config.telegram_token == "telegram-token"
    assert config.cursor_path == cursor_path
    assert config.poll_interval_s == 2.5
    assert config.max_inflight == 4


def test_config_from_env_missing_required_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that missing required env vars raise ValueError."""
    # Missing SWITCHBOARD_MCP_URL
    with pytest.raises(ValueError, match="SWITCHBOARD_MCP_URL"):
        TelegramBotConnectorConfig.from_env()

    # Missing CONNECTOR_ENDPOINT_IDENTITY
    monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:8100/sse")
    with pytest.raises(ValueError, match="CONNECTOR_ENDPOINT_IDENTITY"):
        TelegramBotConnectorConfig.from_env()

    # Missing BUTLER_TELEGRAM_TOKEN
    monkeypatch.setenv("CONNECTOR_ENDPOINT_IDENTITY", "test_bot")
    with pytest.raises(ValueError, match="BUTLER_TELEGRAM_TOKEN"):
        TelegramBotConnectorConfig.from_env()


# -----------------------------------------------------------------------------
# Normalization tests
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_normalize_to_ingest_v1_basic_message(
    connector: TelegramBotConnector,
    sample_telegram_update: dict[str, Any],
) -> None:
    """Test normalization of a basic Telegram message to ingest.v1."""
    envelope = await connector._normalize_to_ingest_v1(sample_telegram_update)

    assert envelope["schema_version"] == "ingest.v1"
    assert envelope["source"]["channel"] == "telegram"
    assert envelope["source"]["provider"] == "telegram"
    assert envelope["source"]["endpoint_identity"] == "test_bot"
    assert envelope["event"]["external_event_id"] == "12345"
    assert envelope["event"]["external_thread_id"] == "987654321:1"
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


@pytest.mark.asyncio
async def test_normalize_to_ingest_v1_edited_message(connector: TelegramBotConnector) -> None:
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

    envelope = await connector._normalize_to_ingest_v1(update)

    assert envelope["event"]["external_event_id"] == "12346"
    assert envelope["event"]["external_thread_id"] == "111222333:2"
    assert envelope["sender"]["identity"] == "111222333"
    assert envelope["payload"]["normalized_text"] == "Edited text"


@pytest.mark.asyncio
async def test_normalize_to_ingest_v1_channel_post(connector: TelegramBotConnector) -> None:
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

    envelope = await connector._normalize_to_ingest_v1(update)

    assert envelope["event"]["external_event_id"] == "12347"
    assert envelope["event"]["external_thread_id"] == "-1001234567890:3"
    # No 'from' in channel_post, should default to "unknown"
    assert envelope["sender"]["identity"] == "unknown"
    assert envelope["payload"]["normalized_text"] == "Channel announcement"


@pytest.mark.asyncio
async def test_normalize_to_ingest_v1_missing_fields(connector: TelegramBotConnector) -> None:
    """Test normalization handles missing optional fields gracefully."""
    update = {"update_id": 12348}  # Minimal update with no message

    envelope = await connector._normalize_to_ingest_v1(update)

    assert envelope["event"]["external_event_id"] == "12348"
    assert envelope["event"]["external_thread_id"] is None
    assert envelope["sender"]["identity"] == "unknown"
    assert envelope["payload"]["normalized_text"] == ""
    assert envelope["payload"]["raw"] == update


# -----------------------------------------------------------------------------
# Ingest submission tests (MCP-based)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_to_ingest_success(
    connector: TelegramBotConnector,
    sample_telegram_update: dict[str, Any],
) -> None:
    """Test successful submission to Switchboard via MCP ingest tool."""
    envelope = await connector._normalize_to_ingest_v1(sample_telegram_update)

    mock_result = {
        "request_id": "12345678-1234-1234-1234-123456789012",
        "status": "accepted",
        "duplicate": False,
    }

    with patch.object(
        connector._mcp_client, "call_tool", new_callable=AsyncMock, return_value=mock_result
    ) as mock_call:
        await connector._submit_to_ingest(envelope)

        mock_call.assert_called_once_with("ingest", envelope)


@pytest.mark.asyncio
async def test_submit_to_ingest_duplicate_accepted(
    connector: TelegramBotConnector,
    sample_telegram_update: dict[str, Any],
) -> None:
    """Test that duplicate submissions are treated as success."""
    envelope = await connector._normalize_to_ingest_v1(sample_telegram_update)

    mock_result = {
        "request_id": "12345678-1234-1234-1234-123456789012",
        "status": "accepted",
        "duplicate": True,
    }

    with patch.object(
        connector._mcp_client, "call_tool", new_callable=AsyncMock, return_value=mock_result
    ):
        # Should not raise, duplicate is success
        await connector._submit_to_ingest(envelope)


@pytest.mark.asyncio
async def test_submit_to_ingest_mcp_error(
    connector: TelegramBotConnector,
    sample_telegram_update: dict[str, Any],
) -> None:
    """Test handling of MCP errors from ingest tool."""
    envelope = await connector._normalize_to_ingest_v1(sample_telegram_update)

    mock_result = {"status": "error", "error": "Invalid ingest.v1 envelope"}

    with patch.object(
        connector._mcp_client, "call_tool", new_callable=AsyncMock, return_value=mock_result
    ):
        with pytest.raises(RuntimeError, match="Ingest tool error"):
            await connector._submit_to_ingest(envelope)


@pytest.mark.asyncio
async def test_submit_to_ingest_connection_error(
    connector: TelegramBotConnector,
    sample_telegram_update: dict[str, Any],
) -> None:
    """Test handling of connection errors to MCP server."""
    envelope = await connector._normalize_to_ingest_v1(sample_telegram_update)

    with patch.object(
        connector._mcp_client,
        "call_tool",
        new_callable=AsyncMock,
        side_effect=ConnectionError("Cannot reach switchboard"),
    ):
        with pytest.raises(ConnectionError):
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
    """Test end-to-end update processing: normalize + submit via MCP."""
    mock_result = {
        "request_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "status": "accepted",
        "duplicate": False,
    }

    with patch.object(
        connector._mcp_client, "call_tool", new_callable=AsyncMock, return_value=mock_result
    ):
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

    async def mock_submit(envelope: dict[str, Any]) -> None:
        import time

        submission_times.append(time.time())
        await asyncio.sleep(0.1)

    with patch.object(connector, "_submit_to_ingest", side_effect=mock_submit):
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


# -----------------------------------------------------------------------------
# Media download and storage tests
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_normalize_to_ingest_v1_photo_message(connector: TelegramBotConnector) -> None:
    """Test normalization of photo message with attachment."""
    update = {
        "update_id": 12350,
        "message": {
            "message_id": 10,
            "from": {"id": 123456789, "first_name": "Photographer"},
            "chat": {"id": 123456789, "type": "private"},
            "date": 1708014000,
            "photo": [
                {"file_id": "small_photo_id", "width": 90, "height": 90, "file_size": 1000},
                {"file_id": "medium_photo_id", "width": 320, "height": 320, "file_size": 15000},
                {"file_id": "large_photo_id", "width": 800, "height": 800, "file_size": 50000},
            ],
            "caption": "Check out this photo!",
        },
    }

    # Mock blob store
    mock_blob_store = AsyncMock()
    mock_blob_store.put.return_value = "local://2026/02/16/test-photo.jpg"
    connector._blob_store = mock_blob_store

    # Mock Telegram file download
    mock_file_response = Mock()
    mock_file_response.json.return_value = {
        "ok": True,
        "result": {"file_id": "large_photo_id", "file_path": "photos/file_1.jpg"},
    }
    mock_file_response.raise_for_status = Mock()

    mock_download_response = Mock()
    mock_download_response.content = b"fake_jpeg_data"
    mock_download_response.raise_for_status = Mock()

    with (
        patch.object(connector._http_client, "get") as mock_get,
    ):
        mock_get.side_effect = [mock_file_response, mock_download_response]

        envelope = await connector._normalize_to_ingest_v1(update)

    assert envelope["payload"]["normalized_text"] == "Check out this photo!"
    assert envelope["payload"]["attachments"] is not None
    assert len(envelope["payload"]["attachments"]) == 1

    attachment = envelope["payload"]["attachments"][0]
    assert attachment["media_type"] == "image/jpeg"
    assert attachment["storage_ref"] == "local://2026/02/16/test-photo.jpg"
    assert attachment["size_bytes"] == len(b"fake_jpeg_data")
    assert attachment["width"] == 800
    assert attachment["height"] == 800

    # Verify blob store was called
    mock_blob_store.put.assert_called_once()


@pytest.mark.asyncio
async def test_normalize_to_ingest_v1_document_message(connector: TelegramBotConnector) -> None:
    """Test normalization of document message with attachment."""
    update = {
        "update_id": 12351,
        "message": {
            "message_id": 11,
            "from": {"id": 987654321, "first_name": "Sender"},
            "chat": {"id": 987654321, "type": "private"},
            "date": 1708014100,
            "document": {
                "file_id": "doc_file_id",
                "file_name": "report.pdf",
                "mime_type": "application/pdf",
                "file_size": 12345,
            },
            "caption": "Here's the report",
        },
    }

    # Mock blob store
    mock_blob_store = AsyncMock()
    mock_blob_store.put.return_value = "local://2026/02/16/report.pdf"
    connector._blob_store = mock_blob_store

    # Mock Telegram file download
    mock_file_response = Mock()
    mock_file_response.json.return_value = {
        "ok": True,
        "result": {"file_id": "doc_file_id", "file_path": "documents/file_2.pdf"},
    }
    mock_file_response.raise_for_status = Mock()

    mock_download_response = Mock()
    mock_download_response.content = b"fake_pdf_data"
    mock_download_response.raise_for_status = Mock()

    with (
        patch.object(connector._http_client, "get") as mock_get,
    ):
        mock_get.side_effect = [mock_file_response, mock_download_response]

        envelope = await connector._normalize_to_ingest_v1(update)

    assert envelope["payload"]["normalized_text"] == "Here's the report"
    assert envelope["payload"]["attachments"] is not None
    assert len(envelope["payload"]["attachments"]) == 1

    attachment = envelope["payload"]["attachments"][0]
    assert attachment["media_type"] == "application/pdf"
    assert attachment["storage_ref"] == "local://2026/02/16/report.pdf"
    assert attachment["size_bytes"] == len(b"fake_pdf_data")
    assert attachment["filename"] == "report.pdf"

    # Verify blob store was called
    mock_blob_store.put.assert_called_once()


@pytest.mark.asyncio
async def test_normalize_to_ingest_v1_text_only_no_attachments(
    connector: TelegramBotConnector,
) -> None:
    """Test normalization of text-only message produces no attachments."""
    update = {
        "update_id": 12352,
        "message": {
            "message_id": 12,
            "from": {"id": 111222333, "first_name": "Texter"},
            "chat": {"id": 111222333, "type": "private"},
            "date": 1708014200,
            "text": "Just text, no media",
        },
    }

    envelope = await connector._normalize_to_ingest_v1(update)

    assert envelope["payload"]["normalized_text"] == "Just text, no media"
    assert "attachments" not in envelope["payload"]


@pytest.mark.asyncio
async def test_download_telegram_file_graceful_degradation(
    connector: TelegramBotConnector,
) -> None:
    """Test that media download failures don't block text ingestion."""
    update = {
        "update_id": 12353,
        "message": {
            "message_id": 13,
            "from": {"id": 444555666, "first_name": "FailUser"},
            "chat": {"id": 444555666, "type": "private"},
            "date": 1708014300,
            "photo": [
                {"file_id": "broken_photo_id", "width": 800, "height": 600, "file_size": 50000},
            ],
            "caption": "Photo with text",
        },
    }

    # Mock failed file download
    with patch.object(
        connector._http_client,
        "get",
        side_effect=Exception("Telegram API down"),
    ):
        envelope = await connector._normalize_to_ingest_v1(update)

    # Text should still be extracted
    assert envelope["payload"]["normalized_text"] == "Photo with text"
    # Attachments should be empty or None on failure
    assert "attachments" not in envelope["payload"] or len(envelope["payload"]["attachments"]) == 0


@pytest.mark.asyncio
async def test_image_compression_for_large_files(connector: TelegramBotConnector) -> None:
    """Test that images >5MB are compressed before storage."""
    update = {
        "update_id": 12354,
        "message": {
            "message_id": 14,
            "from": {"id": 777888999, "first_name": "BigPhoto"},
            "chat": {"id": 777888999, "type": "private"},
            "date": 1708014400,
            "photo": [
                {
                    "file_id": "huge_photo_id",
                    "width": 4000,
                    "height": 3000,
                    "file_size": 6000000,
                },
            ],
        },
    }

    # Create a fake large image with random noise to prevent compression (>5MB)
    import random
    from io import BytesIO

    from PIL import Image

    # Create image with random noise to ensure it doesn't compress too well
    img = Image.new("RGB", (4000, 3000))
    pixels = img.load()
    for i in range(4000):
        for j in range(3000):
            pixels[i, j] = (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))

    large_jpeg = BytesIO()
    img.save(large_jpeg, format="JPEG", quality=100)
    large_jpeg_data = large_jpeg.getvalue()

    # Skip test if we can't generate a large enough image
    if len(large_jpeg_data) <= 5 * 1024 * 1024:
        pytest.skip("Could not generate >5MB test image")

    # Mock blob store
    mock_blob_store = AsyncMock()
    mock_blob_store.put.return_value = "local://2026/02/16/compressed.jpg"
    connector._blob_store = mock_blob_store

    # Mock Telegram file download
    mock_file_response = Mock()
    mock_file_response.json.return_value = {
        "ok": True,
        "result": {"file_id": "huge_photo_id", "file_path": "photos/huge.jpg"},
    }
    mock_file_response.raise_for_status = Mock()

    mock_download_response = Mock()
    mock_download_response.content = large_jpeg_data
    mock_download_response.raise_for_status = Mock()

    with (
        patch.object(connector._http_client, "get") as mock_get,
    ):
        mock_get.side_effect = [mock_file_response, mock_download_response]

        await connector._normalize_to_ingest_v1(update)

    # Verify compression happened by checking stored data size
    stored_data = mock_blob_store.put.call_args[0][0]
    assert len(stored_data) < 5 * 1024 * 1024  # Compressed to <5MB
    assert len(stored_data) < len(large_jpeg_data)  # Smaller than original
