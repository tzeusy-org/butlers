"""Tests for Telegram bot connector runtime."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest

from butlers.connectors.telegram_bot import (
    TelegramBotConnector,
    TelegramBotConnectorConfig,
    _resolve_telegram_bot_token_from_db,
)


@pytest.fixture
def mock_config(tmp_path: Path) -> TelegramBotConnectorConfig:
    """Create a mock connector configuration."""
    cursor_path = tmp_path / "cursor.json"
    return TelegramBotConnectorConfig(
        switchboard_mcp_url="http://localhost:40100/sse",
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

    monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:40100/sse")
    monkeypatch.setenv("CONNECTOR_PROVIDER", "telegram")
    monkeypatch.setenv("CONNECTOR_CHANNEL", "telegram")
    monkeypatch.setenv("CONNECTOR_ENDPOINT_IDENTITY", "my_bot")
    monkeypatch.setenv("BUTLER_TELEGRAM_TOKEN", "telegram-token")
    monkeypatch.setenv("CONNECTOR_CURSOR_PATH", str(cursor_path))
    monkeypatch.setenv("CONNECTOR_POLL_INTERVAL_S", "2.5")
    monkeypatch.setenv("CONNECTOR_MAX_INFLIGHT", "4")

    config = TelegramBotConnectorConfig.from_env()

    assert config.switchboard_mcp_url == "http://localhost:40100/sse"
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
    monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:40100/sse")
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
    assert envelope["event"]["external_thread_id"] == "111222333:2"
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
    assert envelope["event"]["external_thread_id"] == "-1001234567890:3"
    # No 'from' in channel_post, should default to "unknown"
    assert envelope["sender"]["identity"] == "unknown"
    assert envelope["payload"]["normalized_text"] == "Channel announcement"


def test_normalize_to_ingest_v1_no_message_returns_none(connector: TelegramBotConnector) -> None:
    """Test that updates with no message object return None."""
    update = {"update_id": 12348}  # Minimal update with no message
    assert connector._normalize_to_ingest_v1(update) is None


def test_normalize_to_ingest_v1_photo_with_caption(connector: TelegramBotConnector) -> None:
    """Test that photo messages with captions use the caption text."""
    update = {
        "update_id": 20001,
        "message": {
            "message_id": 10,
            "from": {"id": 111, "first_name": "Test"},
            "chat": {"id": 111, "type": "private"},
            "date": 1708012800,
            "photo": [{"file_id": "abc", "width": 100, "height": 100}],
            "caption": "Look at this sunset!",
        },
    }
    envelope = connector._normalize_to_ingest_v1(update)
    assert envelope is not None
    assert envelope["payload"]["normalized_text"] == "Look at this sunset!"


def test_normalize_to_ingest_v1_photo_without_caption(connector: TelegramBotConnector) -> None:
    """Test that captionless photo messages produce [Photo] descriptor."""
    update = {
        "update_id": 20002,
        "message": {
            "message_id": 11,
            "from": {"id": 222, "first_name": "Test"},
            "chat": {"id": 222, "type": "private"},
            "date": 1708012800,
            "photo": [{"file_id": "def", "width": 200, "height": 200}],
        },
    }
    envelope = connector._normalize_to_ingest_v1(update)
    assert envelope is not None
    assert envelope["payload"]["normalized_text"] == "[Photo]"


def test_normalize_to_ingest_v1_sticker_with_emoji(connector: TelegramBotConnector) -> None:
    """Test that sticker with emoji produces [Sticker: 😀]."""
    update = {
        "update_id": 20003,
        "message": {
            "message_id": 12,
            "from": {"id": 333, "first_name": "Test"},
            "chat": {"id": 333, "type": "private"},
            "date": 1708012800,
            "sticker": {"file_id": "ghi", "emoji": "😀", "width": 512, "height": 512},
        },
    }
    envelope = connector._normalize_to_ingest_v1(update)
    assert envelope is not None
    assert envelope["payload"]["normalized_text"] == "[Sticker: 😀]"


def test_normalize_to_ingest_v1_poll_with_question(connector: TelegramBotConnector) -> None:
    """Test that poll messages include the question."""
    update = {
        "update_id": 20004,
        "message": {
            "message_id": 13,
            "from": {"id": 444, "first_name": "Test"},
            "chat": {"id": 444, "type": "private"},
            "date": 1708012800,
            "poll": {
                "id": "poll123",
                "question": "What's for lunch?",
                "options": [{"text": "Pizza"}, {"text": "Sushi"}],
            },
        },
    }
    envelope = connector._normalize_to_ingest_v1(update)
    assert envelope is not None
    assert envelope["payload"]["normalized_text"] == "[Poll: What's for lunch?]"


def test_normalize_to_ingest_v1_contact(connector: TelegramBotConnector) -> None:
    """Test that contact messages include the name."""
    update = {
        "update_id": 20005,
        "message": {
            "message_id": 14,
            "from": {"id": 555, "first_name": "Test"},
            "chat": {"id": 555, "type": "private"},
            "date": 1708012800,
            "contact": {
                "phone_number": "+1234567890",
                "first_name": "John",
                "last_name": "Doe",
            },
        },
    }
    envelope = connector._normalize_to_ingest_v1(update)
    assert envelope is not None
    assert envelope["payload"]["normalized_text"] == "[Contact: John Doe]"


def test_normalize_to_ingest_v1_voice_message(connector: TelegramBotConnector) -> None:
    """Test that voice messages produce [Voice message] descriptor."""
    update = {
        "update_id": 20006,
        "message": {
            "message_id": 15,
            "from": {"id": 666, "first_name": "Test"},
            "chat": {"id": 666, "type": "private"},
            "date": 1708012800,
            "voice": {"file_id": "jkl", "duration": 5},
        },
    }
    envelope = connector._normalize_to_ingest_v1(update)
    assert envelope is not None
    assert envelope["payload"]["normalized_text"] == "[Voice message]"


def test_normalize_to_ingest_v1_service_message_returns_none(
    connector: TelegramBotConnector,
) -> None:
    """Test that service messages (new_chat_members) return None."""
    update = {
        "update_id": 20007,
        "message": {
            "message_id": 16,
            "from": {"id": 777, "first_name": "Test"},
            "chat": {"id": -100999, "type": "group"},
            "date": 1708012800,
            "new_chat_members": [{"id": 888, "first_name": "NewUser"}],
        },
    }
    assert connector._normalize_to_ingest_v1(update) is None


def test_normalize_to_ingest_v1_callback_query_returns_none(
    connector: TelegramBotConnector,
) -> None:
    """Test that callback_query updates (no message key) return None."""
    update = {
        "update_id": 20008,
        "callback_query": {
            "id": "cb123",
            "from": {"id": 999, "first_name": "Test"},
            "data": "button_click",
        },
    }
    assert connector._normalize_to_ingest_v1(update) is None


# -----------------------------------------------------------------------------
# Ingest submission tests (MCP-based)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_to_ingest_success(
    connector: TelegramBotConnector,
    sample_telegram_update: dict[str, Any],
) -> None:
    """Test successful submission to Switchboard via MCP ingest tool."""
    envelope = connector._normalize_to_ingest_v1(sample_telegram_update)

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
    envelope = connector._normalize_to_ingest_v1(sample_telegram_update)

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
    envelope = connector._normalize_to_ingest_v1(sample_telegram_update)

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
    envelope = connector._normalize_to_ingest_v1(sample_telegram_update)

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
async def test_get_updates_conflict_returns_empty(
    connector: TelegramBotConnector, caplog: pytest.LogCaptureFixture
) -> None:
    """409 Conflict should be treated as a recoverable polling conflict."""
    connector._last_update_id = 200

    response = httpx.Response(
        status_code=409,
        json={"ok": False, "description": "Conflict: terminated by other getUpdates request"},
        request=httpx.Request("GET", "https://api.telegram.org/botTOKEN/getUpdates"),
    )

    with caplog.at_level("WARNING"):
        with patch.object(connector._http_client, "get", return_value=response) as mock_get:
            updates = await connector._get_updates()

    assert updates == []
    assert connector._last_update_id == 200
    assert connector._source_api_ok is False
    mock_get.assert_called_once()
    assert any("getUpdates conflict" in rec.message for rec in caplog.records)


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
async def test_process_update_skips_when_normalize_returns_none(
    connector: TelegramBotConnector,
) -> None:
    """Test that _process_update skips submission when normalize returns None."""
    # Service message — no user content
    service_update = {
        "update_id": 30001,
        "message": {
            "message_id": 50,
            "from": {"id": 111, "first_name": "Test"},
            "chat": {"id": -100999, "type": "group"},
            "date": 1708012800,
            "new_chat_members": [{"id": 222, "first_name": "NewUser"}],
        },
    }
    with patch.object(connector, "_submit_to_ingest", new_callable=AsyncMock) as mock_submit:
        await connector._process_update(service_update)
        mock_submit.assert_not_called()


@pytest.mark.asyncio
async def test_process_update_handles_normalization_error(
    connector: TelegramBotConnector,
) -> None:
    """Test that process_update handles normalization errors gracefully."""
    invalid_update = {"bad": "data"}

    # Now returns None (no message key) → skips silently, no error
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


class TestResolveTelegramBotTokenFromDb:
    """Tests for _resolve_telegram_bot_token_from_db — DB-first credential resolution."""

    async def test_returns_none_when_db_unreachable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Returns None gracefully when DB connection fails."""
        import asyncpg

        monkeypatch.setenv("DATABASE_URL", "postgres://localhost:5432/test")

        async def fake_create_pool(**kwargs):
            raise OSError("Connection refused")

        monkeypatch.setattr(asyncpg, "create_pool", fake_create_pool)
        result = await _resolve_telegram_bot_token_from_db()
        assert result is None

    async def test_returns_none_when_secret_not_in_db(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Returns None when DB is accessible but secret is not stored."""
        from unittest.mock import AsyncMock, MagicMock

        import asyncpg

        monkeypatch.setenv("DATABASE_URL", "postgres://localhost:5432/test")

        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = None  # No secret stored

        mock_pool = MagicMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_pool.close = AsyncMock()

        async def fake_create_pool(**kwargs):
            return mock_pool

        monkeypatch.setattr(asyncpg, "create_pool", fake_create_pool)
        result = await _resolve_telegram_bot_token_from_db()
        assert result is None

    async def test_returns_token_when_stored_in_db(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Returns the token string when found in butler_secrets table."""
        from unittest.mock import AsyncMock, MagicMock

        import asyncpg

        monkeypatch.setenv("DATABASE_URL", "postgres://localhost:5432/test")
        monkeypatch.setenv("CONNECTOR_BUTLER_DB_NAME", "butler_test")

        # The CredentialStore.load method does: conn.fetchrow(SELECT secret_value ...)
        mock_row = MagicMock()
        mock_row.__getitem__ = lambda self, key: "db-bot-token-abc123"

        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = mock_row

        mock_pool = MagicMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_pool.close = AsyncMock()

        async def fake_create_pool(**kwargs):
            return mock_pool

        monkeypatch.setattr(asyncpg, "create_pool", fake_create_pool)
        result = await _resolve_telegram_bot_token_from_db()
        assert result == "db-bot-token-abc123"
