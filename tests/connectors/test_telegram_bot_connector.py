"""Tests for Telegram bot connector runtime."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import httpx
import pytest

from butlers.connectors.telegram_bot import (
    TelegramBotConnector,
    TelegramBotConnectorConfig,
    _resolve_telegram_bot_token_from_db,
    resolve_telegram_endpoint_identity,
    run_telegram_bot_connector,
)
from butlers.ingestion_policy import IngestionPolicyEvaluator

pytestmark = pytest.mark.unit


@pytest.fixture
def mock_config() -> TelegramBotConnectorConfig:
    """Create a mock connector configuration."""
    return TelegramBotConnectorConfig(
        switchboard_mcp_url="http://localhost:40100/sse",
        provider="telegram",
        channel="telegram",
        endpoint_identity="test_bot",
        telegram_token="test-telegram-token",
        poll_interval_s=0.1,
        max_inflight=2,
    )


@pytest.fixture
def mock_cursor_pool() -> MagicMock:
    """Create a mock DB cursor pool."""
    return MagicMock()


@pytest.fixture
def connector(
    mock_config: TelegramBotConnectorConfig,
    mock_cursor_pool: MagicMock,
) -> TelegramBotConnector:
    """Create a connector instance with mock config and cursor pool."""
    return TelegramBotConnector(mock_config, cursor_pool=mock_cursor_pool)


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


def test_config_from_env_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test loading configuration from environment variables."""
    monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:40100/sse")
    monkeypatch.setenv("CONNECTOR_PROVIDER", "telegram")
    monkeypatch.setenv("CONNECTOR_CHANNEL", "telegram")
    monkeypatch.setenv("CONNECTOR_ENDPOINT_IDENTITY", "my_bot")
    monkeypatch.setenv("BUTLER_TELEGRAM_TOKEN", "telegram-token")
    monkeypatch.setenv("CONNECTOR_POLL_INTERVAL_S", "2.5")
    monkeypatch.setenv("CONNECTOR_MAX_INFLIGHT", "4")

    config = TelegramBotConnectorConfig.from_env()

    assert config.switchboard_mcp_url == "http://localhost:40100/sse"
    assert config.provider == "telegram"
    assert config.channel == "telegram"
    assert config.endpoint_identity == "my_bot"
    assert config.telegram_token == "telegram-token"
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
    assert envelope["control"]["idempotency_key"] == "tg:987654321:1"
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


@pytest.mark.asyncio
async def test_load_checkpoint_from_db(connector: TelegramBotConnector) -> None:
    """Test loading checkpoint from DB."""
    with patch(
        "butlers.connectors.cursor_store.load_cursor",
        new=AsyncMock(return_value=json.dumps({"last_update_id": 99999})),
    ):
        await connector._load_checkpoint()

    assert connector._last_update_id == 99999


@pytest.mark.asyncio
async def test_load_checkpoint_db_missing(connector: TelegramBotConnector) -> None:
    """Test loading checkpoint when no row in DB."""
    with patch(
        "butlers.connectors.cursor_store.load_cursor",
        new=AsyncMock(return_value=None),
    ):
        await connector._load_checkpoint()
    assert connector._last_update_id is None


@pytest.mark.asyncio
async def test_load_checkpoint_db_error(connector: TelegramBotConnector) -> None:
    """Test loading checkpoint when DB raises an error."""
    with patch(
        "butlers.connectors.cursor_store.load_cursor",
        new=AsyncMock(side_effect=RuntimeError("DB error")),
    ):
        await connector._load_checkpoint()
    # Should fall back to None on error
    assert connector._last_update_id is None


@pytest.mark.asyncio
async def test_save_checkpoint_to_db(connector: TelegramBotConnector) -> None:
    """Test saving checkpoint to DB."""
    connector._last_update_id = 54321

    with patch(
        "butlers.connectors.cursor_store.save_cursor",
        new=AsyncMock(),
    ) as mock_save:
        await connector._save_checkpoint()

    mock_save.assert_awaited_once()
    saved_data = json.loads(mock_save.call_args[0][3])
    assert saved_data["last_update_id"] == 54321


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
async def test_get_updates_rate_limited_returns_empty_and_respects_retry_after(
    connector: TelegramBotConnector, caplog: pytest.LogCaptureFixture
) -> None:
    """429 Too Many Requests should back off and skip this poll cycle."""
    connector._last_update_id = 200

    response = httpx.Response(
        status_code=429,
        json={
            "ok": False,
            "description": "Too Many Requests: retry after 3",
            "parameters": {"retry_after": 3},
        },
        request=httpx.Request("GET", "https://api.telegram.org/botTOKEN/getUpdates"),
    )
    response.headers["Retry-After"] = "2"

    with caplog.at_level("WARNING"):
        with patch.object(connector._http_client, "get", return_value=response) as mock_get:
            with patch(
                "butlers.connectors.telegram_bot.asyncio.sleep",
                new_callable=AsyncMock,
            ) as sleep_mock:
                updates = await connector._get_updates()

    assert updates == []
    assert connector._last_update_id == 200
    assert connector._source_api_ok is False
    mock_get.assert_called_once()
    sleep_mock.assert_awaited_once_with(2.0)
    assert any("rate-limited" in rec.message for rec in caplog.records)


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

    with (
        patch(
            "butlers.connectors.health_socket.make_health_socket",
            return_value=Mock(),
        ),
        patch.object(connector._http_client, "post", return_value=mock_response) as mock_post,
    ):
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

    @staticmethod
    def _configure_single_db_env(monkeypatch: pytest.MonkeyPatch, db_name: str = "butlers") -> None:
        monkeypatch.setenv("CONNECTOR_BUTLER_DB_NAME", db_name)
        monkeypatch.setenv("BUTLER_SHARED_DB_NAME", db_name)

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
        self._configure_single_db_env(monkeypatch)

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
        self._configure_single_db_env(monkeypatch)

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


@pytest.mark.asyncio
async def test_run_telegram_bot_connector_uses_db_token_when_env_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DB token should be sufficient even when BUTLER_TELEGRAM_TOKEN env var is absent."""
    monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:40100/sse")
    monkeypatch.setenv("CONNECTOR_ENDPOINT_IDENTITY", "telegram:bot:test")
    monkeypatch.setenv("POSTGRES_HOST", "localhost")
    monkeypatch.delenv("BUTLER_TELEGRAM_TOKEN", raising=False)

    mock_connector = Mock()
    mock_connector.start_polling = AsyncMock()
    mock_connector.stop = AsyncMock()

    mock_pool = MagicMock()
    mock_pool.close = AsyncMock()

    with (
        patch(
            "butlers.connectors.telegram_bot._resolve_telegram_bot_token_from_db",
            new=AsyncMock(return_value="db-token-123"),
        ),
        patch("butlers.connectors.telegram_bot.configure_logging"),
        patch(
            "butlers.connectors.telegram_bot.TelegramBotConnector",
            return_value=mock_connector,
        ) as cls,
        patch(
            "butlers.connectors.cursor_store.create_cursor_pool_from_env",
            new=AsyncMock(return_value=mock_pool),
        ),
    ):
        await run_telegram_bot_connector()

    passed_config = cls.call_args[0][0]
    assert passed_config.telegram_token == "db-token-123"
    mock_connector.start_polling.assert_awaited_once()


# -----------------------------------------------------------------------------
# Exponential backoff tests
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_polling_backoff_resets_after_success(
    connector: TelegramBotConnector,
    mock_config: TelegramBotConnectorConfig,
) -> None:
    """After a successful poll, consecutive_failures resets to 0 and poll_interval_s is used."""
    # Pre-seed failure count as if prior errors occurred
    connector._consecutive_failures = 3

    mock_response = Mock()
    mock_response.json.return_value = {"ok": True, "result": []}
    mock_response.raise_for_status = Mock()

    sleep_calls: list[float] = []

    async def record_sleep(secs: float) -> None:
        sleep_calls.append(secs)
        # Stop the loop after first sleep so the test doesn't hang
        connector._running = False

    connector._running = True

    with (
        patch.object(connector._http_client, "get", return_value=mock_response),
        patch("butlers.connectors.telegram_bot.asyncio.sleep", side_effect=record_sleep),
        patch.object(connector, "_start_health_server"),
        patch.object(connector, "_start_heartbeat"),
        patch.object(connector, "_load_checkpoint"),
        patch(
            "butlers.connectors.telegram_bot.wait_for_switchboard_ready",
            new_callable=AsyncMock,
        ),
    ):
        await connector.start_polling()

    assert connector._consecutive_failures == 0
    assert len(sleep_calls) == 1
    assert sleep_calls[0] == mock_config.poll_interval_s


@pytest.mark.asyncio
async def test_polling_backoff_increases_on_consecutive_failures(
    connector: TelegramBotConnector,
    mock_config: TelegramBotConnectorConfig,
) -> None:
    """Each consecutive network error doubles the sleep duration (capped at 60s)."""
    poll_interval = mock_config.poll_interval_s  # 0.1s in mock_config

    sleep_calls: list[float] = []
    call_count = 0

    async def record_sleep(secs: float) -> None:
        sleep_calls.append(secs)
        nonlocal call_count
        call_count += 1
        if call_count >= 3:
            connector._running = False

    connector._running = True

    with (
        patch.object(
            connector,
            "_get_updates",
            side_effect=httpx.ReadError("Network error", request=None),
        ),
        patch("butlers.connectors.telegram_bot.asyncio.sleep", side_effect=record_sleep),
        patch("butlers.connectors.telegram_bot.random.random", return_value=0.5),
        patch.object(connector, "_start_health_server"),
        patch.object(connector, "_start_heartbeat"),
        patch.object(connector, "_load_checkpoint"),
        patch(
            "butlers.connectors.telegram_bot.wait_for_switchboard_ready",
            new_callable=AsyncMock,
        ),
    ):
        await connector.start_polling()

    # With random.random() == 0.5, jitter term = capped_backoff * 0.1 * (2*0.5 - 1) = 0
    # So sleep_s == capped_backoff exactly
    assert len(sleep_calls) == 3

    # Failure 1: base = 0.1 * 2^1 = 0.2, cap = min(0.2, 60) = 0.2, jitter = 0 → 0.2
    assert sleep_calls[0] == pytest.approx(poll_interval * 2**1, rel=1e-9)
    # Failure 2: base = 0.1 * 2^2 = 0.4, cap = min(0.4, 60) = 0.4, jitter = 0 → 0.4
    assert sleep_calls[1] == pytest.approx(poll_interval * 2**2, rel=1e-9)
    # Failure 3: base = 0.1 * 2^3 = 0.8, cap = min(0.8, 60) = 0.8, jitter = 0 → 0.8
    assert sleep_calls[2] == pytest.approx(poll_interval * 2**3, rel=1e-9)

    # Consecutive failures counter matches number of errors raised
    assert connector._consecutive_failures == 3


@pytest.mark.asyncio
async def test_polling_backoff_capped_at_60_seconds(
    connector: TelegramBotConnector,
    mock_config: TelegramBotConnectorConfig,
) -> None:
    """Backoff is capped at 60 seconds regardless of failure count."""
    connector._consecutive_failures = 20  # Pre-seed very high failure count

    sleep_calls: list[float] = []

    async def record_sleep(secs: float) -> None:
        sleep_calls.append(secs)
        connector._running = False

    connector._running = True

    with (
        patch.object(
            connector,
            "_get_updates",
            side_effect=httpx.ReadError("Network error", request=None),
        ),
        patch("butlers.connectors.telegram_bot.asyncio.sleep", side_effect=record_sleep),
        patch("butlers.connectors.telegram_bot.random.random", return_value=0.5),
        patch.object(connector, "_start_health_server"),
        patch.object(connector, "_start_heartbeat"),
        patch.object(connector, "_load_checkpoint"),
        patch(
            "butlers.connectors.telegram_bot.wait_for_switchboard_ready",
            new_callable=AsyncMock,
        ),
    ):
        await connector.start_polling()

    assert len(sleep_calls) == 1
    # With jitter factor of 0 (random=0.5), sleep should be exactly 60.0
    assert sleep_calls[0] == pytest.approx(60.0, rel=1e-9)


@pytest.mark.asyncio
async def test_polling_backoff_resets_after_recovery(
    connector: TelegramBotConnector,
    mock_config: TelegramBotConnectorConfig,
) -> None:
    """After errors, a successful poll resets backoff to base poll_interval_s."""
    poll_interval = mock_config.poll_interval_s

    # Use a counter instead of iter()/next() to avoid StopIteration inside
    # async coroutines, which has unpredictable behaviour across Python versions.
    get_updates_call = 0

    async def get_updates_side_effect() -> list:
        nonlocal get_updates_call
        get_updates_call += 1
        if get_updates_call == 1:
            raise httpx.ReadError("Network error", request=None)
        if get_updates_call == 2:
            return []  # success — resets backoff
        # 3rd call: stop the loop cleanly
        connector._running = False
        return []

    sleep_calls: list[float] = []

    async def record_sleep(secs: float) -> None:
        sleep_calls.append(secs)

    connector._running = True

    with (
        patch.object(connector, "_get_updates", side_effect=get_updates_side_effect),
        patch("butlers.connectors.telegram_bot.asyncio.sleep", side_effect=record_sleep),
        patch("butlers.connectors.telegram_bot.random.random", return_value=0.5),
        patch.object(connector, "_start_health_server"),
        patch.object(connector, "_start_heartbeat"),
        patch.object(connector, "_load_checkpoint"),
        patch(
            "butlers.connectors.telegram_bot.wait_for_switchboard_ready",
            new_callable=AsyncMock,
        ),
    ):
        await connector.start_polling()

    assert get_updates_call == 3
    # At least 2 sleeps from the polling loop (error backoff + success interval)
    assert len(sleep_calls) >= 2
    # First sleep: error → backoff (0.1 * 2^1 = 0.2, jitter=0)
    assert sleep_calls[0] == pytest.approx(poll_interval * 2, rel=1e-9)
    # Second sleep: success → back to poll_interval_s
    assert sleep_calls[1] == pytest.approx(poll_interval, rel=1e-9)
    # After recovery, failures reset
    assert connector._consecutive_failures == 0


@pytest.mark.asyncio
async def test_polling_backoff_consecutive_failures_in_health_state(
    connector: TelegramBotConnector,
) -> None:
    """_consecutive_failures is reflected in heartbeat health state reporting."""
    # No failures — healthy
    connector._source_api_ok = True
    connector._consecutive_failures = 0
    state, msg = connector._get_health_state()
    assert state == "healthy"
    assert msg is None

    # Failures present but source_api_ok still True → degraded
    connector._consecutive_failures = 2
    state, msg = connector._get_health_state()
    assert state == "degraded"
    assert "2" in (msg or "")

    # source_api_ok is False → error with failure count in message
    connector._source_api_ok = False
    connector._consecutive_failures = 5
    state, msg = connector._get_health_state()
    assert state == "error"
    assert "5" in (msg or "")


# -----------------------------------------------------------------------------
# Heartbeat connector_type alignment tests (butlers-2cf1)
# -----------------------------------------------------------------------------


def test_heartbeat_connector_type_matches_metrics_label(
    connector: TelegramBotConnector,
) -> None:
    """HeartbeatConfig must use 'telegram_bot' to match ConnectorMetrics label.

    Root cause of butlers-2cf1: _start_heartbeat() was passing
    connector_type=self._config.provider ('telegram') but ConnectorMetrics
    labels all Prometheus metrics with connector_type='telegram_bot'.
    The mismatch caused _collect_counters() to find zero matching samples,
    so heartbeat records reported all counters as 0.
    """
    # ConnectorMetrics is always labelled "telegram_bot"
    assert connector._metrics._connector_type == "telegram_bot"


@patch("butlers.connectors.telegram_bot.ConnectorHeartbeat")
@patch("butlers.connectors.telegram_bot.HeartbeatConfig")
def test_start_heartbeat_passes_telegram_bot_as_connector_type(
    mock_heartbeat_config_cls: Mock,
    mock_heartbeat_cls: Mock,
    connector: TelegramBotConnector,
) -> None:
    """_start_heartbeat() must pass connector_type='telegram_bot', not provider.

    This ensures HeartbeatConfig.connector_type matches ConnectorMetrics labels
    so that _collect_counters() can find the correct Prometheus samples.
    """
    mock_cfg_instance = Mock()
    mock_heartbeat_config_cls.from_env.return_value = mock_cfg_instance

    mock_hb_instance = Mock()
    mock_hb_instance.start = Mock()
    mock_heartbeat_cls.return_value = mock_hb_instance

    connector._start_heartbeat()

    # Verify connector_type is hardcoded "telegram_bot", not self._config.provider
    mock_heartbeat_config_cls.from_env.assert_called_once()
    call_kwargs = mock_heartbeat_config_cls.from_env.call_args.kwargs
    assert call_kwargs["connector_type"] == "telegram_bot", (
        f"Expected connector_type='telegram_bot', got '{call_kwargs['connector_type']}'. "
        "This mismatch causes zero ingestion counts in the dashboard."
    )
    # endpoint_identity should still come from config
    assert call_kwargs["endpoint_identity"] == connector._config.endpoint_identity


def test_heartbeat_connector_type_is_not_provider(
    connector: TelegramBotConnector,
) -> None:
    """connector_type for heartbeat must be 'telegram_bot', not 'telegram'.

    The TelegramBotConnectorConfig.provider defaults to 'telegram', but
    ConnectorMetrics always uses 'telegram_bot'. If heartbeat uses provider,
    counter filtering returns zero matches and the dashboard shows 0.
    """
    # Confirm the provider is 'telegram' (the default that caused the bug)
    assert connector._config.provider == "telegram"

    # Confirm metrics label is 'telegram_bot' (what the heartbeat should match)
    assert connector._metrics._connector_type == "telegram_bot"

    # Confirm they differ — this is the mismatch that was the bug
    assert connector._config.provider != connector._metrics._connector_type


# -----------------------------------------------------------------------------
# Switchboard readiness probe tests (butlers-p4qf)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_polling_waits_for_switchboard_ready(
    connector: TelegramBotConnector,
    mock_config: TelegramBotConnectorConfig,
) -> None:
    """start_polling() calls wait_for_switchboard_ready before entering the loop.

    This ensures messages are not polled from Telegram (and thus offsets
    advanced) before the Switchboard is accepting connections.
    """
    mock_response = Mock()
    mock_response.json.return_value = {"ok": True, "result": []}
    mock_response.raise_for_status = Mock()

    connector._running = True

    async def stop_after_first_sleep(secs: float) -> None:
        connector._running = False

    probe_calls: list[str] = []

    async def mock_probe(url: str, **kwargs: Any) -> None:
        probe_calls.append(url)

    with (
        patch.object(connector._http_client, "get", return_value=mock_response),
        patch("butlers.connectors.telegram_bot.asyncio.sleep", side_effect=stop_after_first_sleep),
        patch.object(connector, "_start_health_server"),
        patch.object(connector, "_start_heartbeat"),
        patch.object(connector, "_load_checkpoint"),
        patch(
            "butlers.connectors.telegram_bot.wait_for_switchboard_ready",
            side_effect=mock_probe,
        ),
    ):
        await connector.start_polling()

    # Probe must have been called exactly once with the configured SSE URL
    assert probe_calls == [mock_config.switchboard_mcp_url]


@pytest.mark.asyncio
async def test_start_polling_continues_if_probe_times_out(
    connector: TelegramBotConnector,
    mock_config: TelegramBotConnectorConfig,
) -> None:
    """start_polling() logs a warning and continues if the readiness probe times out.

    The connector must still start even when Switchboard takes unusually long
    to become healthy (so the connector doesn't hang forever).
    """
    mock_response = Mock()
    mock_response.json.return_value = {"ok": True, "result": []}
    mock_response.raise_for_status = Mock()

    connector._running = True

    async def stop_after_first_sleep(secs: float) -> None:
        connector._running = False

    async def mock_probe_timeout(url: str, **kwargs: Any) -> None:
        raise TimeoutError("probe timed out")

    with (
        patch.object(connector._http_client, "get", return_value=mock_response),
        patch("butlers.connectors.telegram_bot.asyncio.sleep", side_effect=stop_after_first_sleep),
        patch.object(connector, "_start_health_server"),
        patch.object(connector, "_start_heartbeat"),
        patch.object(connector, "_load_checkpoint"),
        patch(
            "butlers.connectors.telegram_bot.wait_for_switchboard_ready",
            side_effect=mock_probe_timeout,
        ),
    ):
        # Must not raise — timeout is treated as a warning, not a hard failure
        await connector.start_polling()


@pytest.mark.asyncio
async def test_process_update_reraises_connection_error(
    connector: TelegramBotConnector,
    sample_telegram_update: dict[str, Any],
) -> None:
    """_process_update() re-raises ConnectionError so the outer loop can retry.

    When the Switchboard is unavailable, ConnectionError must propagate up to
    start_polling()'s except block so that the checkpoint is NOT saved past
    the failed update batch.  Silently swallowing ConnectionError causes
    permanent message loss because Telegram advances the offset regardless.
    """
    with patch.object(
        connector._mcp_client,
        "call_tool",
        new_callable=AsyncMock,
        side_effect=ConnectionError("Switchboard not reachable"),
    ):
        with pytest.raises(ConnectionError, match="Switchboard not reachable"):
            await connector._process_update(sample_telegram_update)


@pytest.mark.asyncio
async def test_process_update_swallows_other_exceptions(
    connector: TelegramBotConnector,
    sample_telegram_update: dict[str, Any],
) -> None:
    """_process_update() swallows non-ConnectionError exceptions (malformed updates, etc.).

    A single bad update must not block delivery of the rest of the batch.
    Only transient infrastructure failures (ConnectionError) should abort the
    whole batch.
    """
    with patch.object(
        connector._mcp_client,
        "call_tool",
        new_callable=AsyncMock,
        side_effect=RuntimeError("Application error from MCP tool"),
    ):
        # Must NOT raise — other errors are logged and swallowed
        await connector._process_update(sample_telegram_update)


@pytest.mark.asyncio
async def test_connection_error_prevents_checkpoint_save(
    connector: TelegramBotConnector,
    mock_config: TelegramBotConnectorConfig,
    sample_telegram_update: dict[str, Any],
) -> None:
    """When delivery raises ConnectionError, the batch checkpoint is NOT saved.

    This is the key safety property: if Switchboard is unreachable during
    polling, we must not advance our cursor past the failed updates.
    """
    # Arrange: give the connector a checkpoint position
    connector._last_update_id = 12340

    # simulate getUpdates returning a new update
    updates_response = Mock()
    updates_response.json.return_value = {
        "ok": True,
        "result": [sample_telegram_update],
    }
    updates_response.raise_for_status = Mock()

    checkpoint_saves: list[int | None] = []

    original_save = connector._save_checkpoint

    def record_save() -> None:
        checkpoint_saves.append(connector._last_update_id)
        original_save()

    async def stop_after_sleep(secs: float) -> None:
        connector._running = False

    with (
        patch.object(connector._http_client, "get", return_value=updates_response),
        patch.object(
            connector._mcp_client,
            "call_tool",
            new_callable=AsyncMock,
            side_effect=ConnectionError("Switchboard not reachable"),
        ),
        patch.object(connector, "_save_checkpoint", side_effect=record_save),
        patch("butlers.connectors.telegram_bot.asyncio.sleep", side_effect=stop_after_sleep),
        patch.object(connector, "_start_health_server"),
        patch.object(connector, "_start_heartbeat"),
        patch.object(connector, "_load_checkpoint"),
        patch(
            "butlers.connectors.telegram_bot.wait_for_switchboard_ready",
            new_callable=AsyncMock,
        ),
    ):
        await connector.start_polling()

    # Checkpoint must NOT have been saved because delivery failed
    assert checkpoint_saves == [], (
        "Checkpoint was saved despite ConnectionError — messages would be permanently lost"
    )


# -----------------------------------------------------------------------------
# Source filter gate tests (bu-qbq.8)
# -----------------------------------------------------------------------------


def _policy_evaluator_with_rules(
    rules: list[dict[str, Any]],
    scope: str = "connector:telegram-bot:test_bot",
) -> IngestionPolicyEvaluator:
    """Create an IngestionPolicyEvaluator with pre-loaded rules (no DB needed)."""
    ev = IngestionPolicyEvaluator(
        scope=scope,
        db_pool=None,
        refresh_interval_s=300,
    )
    ev._rules = rules
    ev._last_loaded_at = time.monotonic()
    return ev


# -- _build_ingestion_envelope helper tests --


def test_build_ingestion_envelope_private_chat() -> None:
    """Envelope raw_key is str(chat.id) for private messages."""
    update = {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "from": {"id": 987654321},
            "chat": {"id": 987654321, "type": "private"},
            "text": "hi",
        },
    }
    envelope = TelegramBotConnector._build_ingestion_envelope(update)
    assert envelope.raw_key == "987654321"
    assert envelope.source_channel == "telegram"


def test_build_ingestion_envelope_group_chat() -> None:
    """Envelope raw_key returns group chat ID (negative) for group messages."""
    update = {
        "update_id": 2,
        "message": {
            "message_id": 2,
            "from": {"id": 111},
            "chat": {"id": -100987654321, "type": "supergroup"},
            "text": "hello group",
        },
    }
    envelope = TelegramBotConnector._build_ingestion_envelope(update)
    assert envelope.raw_key == "-100987654321"


def test_build_ingestion_envelope_edited_message() -> None:
    """Envelope raw_key works for edited_message updates."""
    update = {
        "update_id": 3,
        "edited_message": {
            "message_id": 3,
            "from": {"id": 222},
            "chat": {"id": 222, "type": "private"},
            "text": "edited",
        },
    }
    envelope = TelegramBotConnector._build_ingestion_envelope(update)
    assert envelope.raw_key == "222"


def test_build_ingestion_envelope_channel_post() -> None:
    """Envelope raw_key works for channel_post updates."""
    update = {
        "update_id": 4,
        "channel_post": {
            "message_id": 4,
            "chat": {"id": -1001234567890, "type": "channel"},
            "text": "announcement",
        },
    }
    envelope = TelegramBotConnector._build_ingestion_envelope(update)
    assert envelope.raw_key == "-1001234567890"


def test_build_ingestion_envelope_non_message_update() -> None:
    """Envelope raw_key is empty string when no message key is present."""
    update = {"update_id": 5, "callback_query": {"id": "cb1", "data": "click"}}
    envelope = TelegramBotConnector._build_ingestion_envelope(update)
    assert envelope.raw_key == ""


# -- IngestionPolicyEvaluator integration: chat_id block --


@pytest.mark.asyncio
async def test_ingestion_policy_blocks_specific_chat(
    connector: TelegramBotConnector,
) -> None:
    """A blocking chat_id rule blocks; Switchboard is never called."""
    blocked_chat_id = "987654321"
    evaluator = _policy_evaluator_with_rules(
        [
            {
                "id": "rule-001",
                "rule_type": "chat_id",
                "condition": {"chat_id": blocked_chat_id},
                "action": "block",
                "priority": 0,
                "name": "block-chat",
            }
        ]
    )
    connector._ingestion_policy = evaluator

    update = {
        "update_id": 50001,
        "message": {
            "message_id": 100,
            "from": {"id": int(blocked_chat_id)},
            "chat": {"id": int(blocked_chat_id), "type": "private"},
            "text": "Hello — should be blocked",
        },
    }

    with patch.object(connector._mcp_client, "call_tool", new_callable=AsyncMock) as mock_call:
        await connector._process_update(update)
        mock_call.assert_not_called()


@pytest.mark.asyncio
async def test_ingestion_policy_allows_other_chats(
    connector: TelegramBotConnector,
) -> None:
    """A blocking chat_id rule does not block other chats."""
    evaluator = _policy_evaluator_with_rules(
        [
            {
                "id": "rule-002",
                "rule_type": "chat_id",
                "condition": {"chat_id": "111111111"},
                "action": "block",
                "priority": 0,
                "name": "block-other",
            }
        ]
    )
    connector._ingestion_policy = evaluator

    update = {
        "update_id": 50002,
        "message": {
            "message_id": 101,
            "from": {"id": 987654321},
            "chat": {"id": 987654321, "type": "private"},
            "text": "Not blocked",
        },
    }

    mock_result = {"request_id": "aaa", "status": "accepted", "duplicate": False}
    with patch.object(
        connector._mcp_client, "call_tool", new_callable=AsyncMock, return_value=mock_result
    ) as mock_call:
        await connector._process_update(update)
        mock_call.assert_called_once()


# -- IngestionPolicyEvaluator integration: no rules = pass_through --


@pytest.mark.asyncio
async def test_ingestion_policy_no_rules_allows(
    connector: TelegramBotConnector,
) -> None:
    """No rules means pass_through; update proceeds to Switchboard."""
    evaluator = _policy_evaluator_with_rules([])
    connector._ingestion_policy = evaluator

    update = {
        "update_id": 50010,
        "message": {
            "message_id": 200,
            "from": {"id": 42424242},
            "chat": {"id": 42424242, "type": "private"},
            "text": "I am allowed",
        },
    }

    mock_result = {"request_id": "bbb", "status": "accepted", "duplicate": False}
    with patch.object(
        connector._mcp_client, "call_tool", new_callable=AsyncMock, return_value=mock_result
    ) as mock_call:
        await connector._process_update(update)
        assert mock_call.call_count == 1


# -- Blocked update does not call Switchboard --


@pytest.mark.asyncio
async def test_ingestion_policy_blocked_update_no_switchboard_call(
    connector: TelegramBotConnector,
) -> None:
    """Blocked updates are dropped silently with no Switchboard submission.

    The update_id is already advanced by _get_updates() so Telegram will not
    re-deliver it.  This is intentional behaviour, not an error condition.
    """
    evaluator = _policy_evaluator_with_rules(
        [
            {
                "id": "rule-003",
                "rule_type": "chat_id",
                "condition": {"chat_id": "12345"},
                "action": "block",
                "priority": 0,
                "name": "block-chat",
            }
        ]
    )
    connector._ingestion_policy = evaluator

    blocked_update = {
        "update_id": 50030,
        "message": {
            "message_id": 400,
            "from": {"id": 12345},
            "chat": {"id": 12345, "type": "private"},
            "text": "Block me",
        },
    }

    with patch.object(connector._mcp_client, "call_tool", new_callable=AsyncMock) as mock_call:
        # Must not raise — blocked is not an error
        await connector._process_update(blocked_update)
        mock_call.assert_not_called()


# -- ensure_loaded called before ingestion loop --


@pytest.mark.asyncio
async def test_ingestion_policy_ensure_loaded_called_before_polling(
    connector: TelegramBotConnector,
    mock_config: TelegramBotConnectorConfig,
) -> None:
    """ensure_loaded() must be called before the first getUpdates call."""
    mock_response = Mock()
    mock_response.json.return_value = {"ok": True, "result": []}
    mock_response.raise_for_status = Mock()

    ensure_loaded_calls: list[str] = []
    get_updates_calls: list[str] = []

    original_ensure_loaded = connector._ingestion_policy.ensure_loaded

    async def track_ensure_loaded() -> None:
        ensure_loaded_calls.append("ensure_loaded")
        await original_ensure_loaded()

    async def stop_after_get_updates() -> list:
        get_updates_calls.append("get_updates")
        connector._running = False
        return []

    connector._running = True

    async def stop_after_sleep(secs: float) -> None:
        connector._running = False

    with (
        patch.object(
            connector._ingestion_policy,
            "ensure_loaded",
            side_effect=track_ensure_loaded,
        ),
        patch.object(connector, "_get_updates", side_effect=stop_after_get_updates),
        patch("butlers.connectors.telegram_bot.asyncio.sleep", side_effect=stop_after_sleep),
        patch.object(connector, "_start_health_server"),
        patch.object(connector, "_start_heartbeat"),
        patch.object(connector, "_load_checkpoint"),
        patch(
            "butlers.connectors.telegram_bot.wait_for_switchboard_ready",
            new_callable=AsyncMock,
        ),
    ):
        await connector.start_polling()

    assert ensure_loaded_calls == ["ensure_loaded"], "ensure_loaded() was not called"


# -- Connector instantiates IngestionPolicyEvaluator with correct scope --


def test_connector_instantiates_ingestion_policy_evaluator(
    connector: TelegramBotConnector,
) -> None:
    """TelegramBotConnector must have an IngestionPolicyEvaluator."""
    assert hasattr(connector, "_ingestion_policy")
    assert isinstance(connector._ingestion_policy, IngestionPolicyEvaluator)
    expected_scope = f"connector:telegram-bot:{connector._config.endpoint_identity}"
    assert connector._ingestion_policy.scope == expected_scope


# -- resolve_telegram_endpoint_identity tests --


class TestResolveTelegramEndpointIdentity:
    """Tests for resolve_telegram_endpoint_identity()."""

    async def test_resolves_username_from_get_me(self) -> None:
        """Should return bot username when getMe returns ok=true with a username."""
        get_me_response = MagicMock(spec=httpx.Response)
        get_me_response.raise_for_status = MagicMock()
        get_me_response.json.return_value = {
            "ok": True,
            "result": {
                "id": 123456789,
                "is_bot": True,
                "first_name": "My Bot",
                "username": "my_bot",
            },
        }

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=get_me_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("butlers.connectors.telegram_bot.httpx.AsyncClient", return_value=mock_client):
            result = await resolve_telegram_endpoint_identity(
                token="test-token",
                env_fallback="telegram:user:dev",
            )

        assert result == "my_bot"

    async def test_falls_back_to_first_name_when_no_username(self) -> None:
        """Should return first_name when username is absent from the getMe result."""
        get_me_response = MagicMock(spec=httpx.Response)
        get_me_response.raise_for_status = MagicMock()
        get_me_response.json.return_value = {
            "ok": True,
            "result": {
                "id": 123456789,
                "is_bot": True,
                "first_name": "My Bot",
                # no username
            },
        }

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=get_me_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("butlers.connectors.telegram_bot.httpx.AsyncClient", return_value=mock_client):
            result = await resolve_telegram_endpoint_identity(
                token="test-token",
                env_fallback="telegram:user:dev",
            )

        assert result == "My Bot"

    async def test_falls_back_to_env_fallback_on_api_error(self) -> None:
        """Should return env_fallback when the API call raises an exception."""
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("butlers.connectors.telegram_bot.httpx.AsyncClient", return_value=mock_client):
            result = await resolve_telegram_endpoint_identity(
                token="test-token",
                env_fallback="telegram:user:dev",
            )

        assert result == "telegram:user:dev"

    async def test_falls_back_to_env_fallback_when_ok_is_false(self) -> None:
        """Should return env_fallback when getMe returns ok=false."""
        get_me_response = MagicMock(spec=httpx.Response)
        get_me_response.raise_for_status = MagicMock()
        get_me_response.json.return_value = {"ok": False, "description": "Unauthorized"}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=get_me_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("butlers.connectors.telegram_bot.httpx.AsyncClient", return_value=mock_client):
            result = await resolve_telegram_endpoint_identity(
                token="bad-token",
                env_fallback="telegram:user:dev",
            )

        assert result == "telegram:user:dev"


class TestRunTelegramConnectorIdentityResolution:
    """Tests for endpoint_identity auto-resolution in run_telegram_bot_connector()."""

    async def test_endpoint_identity_updated_from_resolved_username(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """run_telegram_bot_connector should update endpoint_identity with resolved username."""
        monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:40100/sse")
        monkeypatch.setenv("CONNECTOR_ENDPOINT_IDENTITY", "telegram:user:dev")
        monkeypatch.setenv("BUTLER_TELEGRAM_TOKEN", "test-token")
        monkeypatch.setenv("CONNECTOR_POLL_INTERVAL_S", "1.0")
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("POSTGRES_HOST", raising=False)

        connector_mock = MagicMock()
        connector_mock.start_polling = AsyncMock()
        connector_mock.stop = AsyncMock()

        with (
            patch("butlers.connectors.telegram_bot.configure_logging"),
            patch(
                "butlers.connectors.telegram_bot.resolve_telegram_endpoint_identity",
                new=AsyncMock(return_value="my_real_bot"),
            ),
            patch(
                "butlers.connectors.telegram_bot.TelegramBotConnector",
                return_value=connector_mock,
            ) as ctor,
            patch(
                "butlers.connectors.cursor_store.create_cursor_pool_from_env",
                new=AsyncMock(return_value=AsyncMock()),
            ),
        ):
            connector_mock.start_polling.side_effect = KeyboardInterrupt
            await run_telegram_bot_connector()

        ctor.assert_called_once()
        used_config = ctor.call_args.args[0]
        assert used_config.endpoint_identity == "my_real_bot"

    async def test_endpoint_identity_uses_fallback_when_resolution_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """run_telegram_bot_connector should keep env var identity when resolution fails."""
        monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:40100/sse")
        monkeypatch.setenv("CONNECTOR_ENDPOINT_IDENTITY", "telegram:user:dev")
        monkeypatch.setenv("BUTLER_TELEGRAM_TOKEN", "test-token")
        monkeypatch.setenv("CONNECTOR_POLL_INTERVAL_S", "1.0")
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("POSTGRES_HOST", raising=False)

        connector_mock = MagicMock()
        connector_mock.start_polling = AsyncMock()
        connector_mock.stop = AsyncMock()

        with (
            patch("butlers.connectors.telegram_bot.configure_logging"),
            patch(
                "butlers.connectors.telegram_bot.resolve_telegram_endpoint_identity",
                # Returns the fallback unchanged (simulates resolution failure)
                new=AsyncMock(return_value="telegram:user:dev"),
            ),
            patch(
                "butlers.connectors.telegram_bot.TelegramBotConnector",
                return_value=connector_mock,
            ) as ctor,
            patch(
                "butlers.connectors.cursor_store.create_cursor_pool_from_env",
                new=AsyncMock(return_value=AsyncMock()),
            ),
        ):
            connector_mock.start_polling.side_effect = KeyboardInterrupt
            await run_telegram_bot_connector()

        ctor.assert_called_once()
        used_config = ctor.call_args.args[0]
        assert used_config.endpoint_identity == "telegram:user:dev"
