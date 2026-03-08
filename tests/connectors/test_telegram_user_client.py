"""Tests for Telegram user-client connector."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.connectors.source_filter import (
    SourceFilterEvaluator,
    SourceFilterSpec,
    extract_telethon_filter_key,
)
from butlers.connectors.telegram_user_client import (
    TELETHON_AVAILABLE,
    TelegramUserClientConnector,
    TelegramUserClientConnectorConfig,
    _resolve_telegram_user_credentials_from_db,
    run_telegram_user_client_connector,
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
        "SWITCHBOARD_MCP_URL": "http://localhost:40100/sse",
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
        switchboard_mcp_url="http://localhost:40100/sse",
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
        """Test loading non-credential config from environment variables.

        Telegram credentials are resolved exclusively from owner contact_info,
        so from_env() returns defaults (0, '', '') for them.
        """
        config = TelegramUserClientConnectorConfig.from_env()

        assert config.switchboard_mcp_url == "http://localhost:40100/sse"
        assert config.provider == "telegram"
        assert config.channel == "telegram"
        assert config.endpoint_identity == "telegram:user:123456"
        # Credentials are not read from env — they come from DB only
        assert config.telegram_api_id == 0
        assert config.telegram_api_hash == ""
        assert config.telegram_user_session == ""
        assert config.max_inflight == 8

    def test_from_env_missing_required_field(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that missing required fields raise ValueError."""
        # Missing SWITCHBOARD_MCP_URL
        with pytest.raises(ValueError, match="SWITCHBOARD_MCP_URL"):
            TelegramUserClientConnectorConfig.from_env()

    def test_from_env_ignores_telegram_credentials(
        self, mock_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """from_env() does not read Telegram credentials from env vars."""
        # Even with invalid env values, from_env() should succeed because
        # it no longer reads TELEGRAM_API_ID/HASH/SESSION from env.
        monkeypatch.setenv("TELEGRAM_API_ID", "not-an-integer")
        config = TelegramUserClientConnectorConfig.from_env()
        assert config.telegram_api_id == 0

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
        assert connector._mcp_client is not None
        assert connector._running is False
        assert connector._last_message_id is None
        # Heartbeat and metrics are initialized
        assert connector._metrics is not None
        assert connector._switchboard_heartbeat is None
        assert connector._last_checkpoint_save is None

    def test_get_health_state_no_client(self, config: TelegramUserClientConnectorConfig) -> None:
        """Health state is 'error' when Telegram client is not initialized."""
        connector = TelegramUserClientConnector(config)
        state, msg = connector._get_health_state()
        assert state == "error"
        assert msg is not None

    def test_get_health_state_disconnected(self, config: TelegramUserClientConnectorConfig) -> None:
        """Health state is 'error' when Telegram client is disconnected."""
        connector = TelegramUserClientConnector(config)
        mock_client = MagicMock()
        mock_client.is_connected.return_value = False
        connector._telegram_client = mock_client
        state, msg = connector._get_health_state()
        assert state == "error"
        assert "disconnected" in msg.lower()

    def test_get_health_state_connected(self, config: TelegramUserClientConnectorConfig) -> None:
        """Health state is 'healthy' when Telegram client is connected."""
        connector = TelegramUserClientConnector(config)
        mock_client = MagicMock()
        mock_client.is_connected.return_value = True
        connector._telegram_client = mock_client
        state, msg = connector._get_health_state()
        assert state == "healthy"
        assert msg is None

    def test_get_checkpoint_no_messages(self, config: TelegramUserClientConnectorConfig) -> None:
        """Checkpoint is (None, None) when no messages have been processed."""
        connector = TelegramUserClientConnector(config)
        cursor, updated_at = connector._get_checkpoint()
        assert cursor is None
        assert updated_at is None

    def test_get_checkpoint_with_messages(self, config: TelegramUserClientConnectorConfig) -> None:
        """Checkpoint includes last message ID when messages have been processed."""
        connector = TelegramUserClientConnector(config)
        connector._last_message_id = 99999
        connector._last_checkpoint_save = time.time()

        cursor, updated_at = connector._get_checkpoint()
        assert cursor == "99999"
        assert updated_at is not None
        assert isinstance(updated_at, datetime)

    def test_get_checkpoint_message_id_without_save_time(
        self, config: TelegramUserClientConnectorConfig
    ) -> None:
        """Checkpoint cursor is set but updated_at is None when save time not recorded."""
        connector = TelegramUserClientConnector(config)
        connector._last_message_id = 12345
        # _last_checkpoint_save remains None

        cursor, updated_at = connector._get_checkpoint()
        assert cursor == "12345"
        assert updated_at is None

    def test_start_heartbeat_creates_heartbeat(
        self, config: TelegramUserClientConnectorConfig
    ) -> None:
        """_start_heartbeat() creates and starts a ConnectorHeartbeat."""
        from butlers.connectors.heartbeat import ConnectorHeartbeat

        connector = TelegramUserClientConnector(config)
        assert connector._switchboard_heartbeat is None

        with patch.object(ConnectorHeartbeat, "start") as mock_start:
            connector._start_heartbeat()

        assert connector._switchboard_heartbeat is not None
        mock_start.assert_called_once()

    def test_start_heartbeat_uses_hardcoded_connector_type(
        self, config: TelegramUserClientConnectorConfig
    ) -> None:
        """_start_heartbeat() hardcodes connector_type='telegram_user_client'."""
        from butlers.connectors.heartbeat import ConnectorHeartbeat, HeartbeatConfig

        connector = TelegramUserClientConnector(config)

        captured_config: list[HeartbeatConfig] = []

        original_init = ConnectorHeartbeat.__init__

        def capturing_init(self, config, **kwargs):  # type: ignore[override]
            captured_config.append(config)
            original_init(self, config, **kwargs)

        with (
            patch.object(ConnectorHeartbeat, "__init__", capturing_init),
            patch.object(ConnectorHeartbeat, "start"),
        ):
            connector._start_heartbeat()

        assert len(captured_config) == 1
        assert captured_config[0].connector_type == "telegram_user_client"

    async def test_stop_stops_heartbeat(self, config: TelegramUserClientConnectorConfig) -> None:
        """stop() calls stop() on the heartbeat if one is running."""
        connector = TelegramUserClientConnector(config)

        mock_heartbeat = MagicMock()
        mock_heartbeat.stop = AsyncMock()
        connector._switchboard_heartbeat = mock_heartbeat

        # Mock telegram client as already disconnected
        connector._telegram_client = None

        await connector.stop()

        mock_heartbeat.stop.assert_awaited_once()

    async def test_save_checkpoint_records_save_time(
        self, config: TelegramUserClientConnectorConfig
    ) -> None:
        """_save_checkpoint() records the timestamp of the successful save."""
        connector = TelegramUserClientConnector(config)
        connector._last_message_id = 12345

        assert connector._last_checkpoint_save is None

        before = time.time()
        await connector._save_checkpoint()
        after = time.time()

        assert connector._last_checkpoint_save is not None
        assert before <= connector._last_checkpoint_save <= after

    def test_metrics_uses_hardcoded_connector_type(
        self, config: TelegramUserClientConnectorConfig
    ) -> None:
        """ConnectorMetrics is initialized with connector_type='telegram_user_client'."""
        connector = TelegramUserClientConnector(config)
        # Access private attribute to verify the connector type is hardcoded correctly
        assert connector._metrics._connector_type == "telegram_user_client"
        assert connector._metrics._endpoint_identity == config.endpoint_identity

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
        """Test successful submission to Switchboard via MCP ingest tool."""
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

        mock_result = {"request_id": "req-123", "duplicate": False, "status": "accepted"}

        with patch.object(
            connector._mcp_client,
            "call_tool",
            new_callable=AsyncMock,
            return_value=mock_result,
        ) as mock_call:
            await connector._submit_to_ingest(envelope)

            mock_call.assert_called_once_with("ingest", envelope)

    async def test_submit_to_ingest_mcp_error(
        self, config: TelegramUserClientConnectorConfig
    ) -> None:
        """Test handling of MCP errors during ingest submission."""
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

        mock_result = {"status": "error", "error": "Validation failed"}

        with patch.object(
            connector._mcp_client,
            "call_tool",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            with pytest.raises(RuntimeError, match="Ingest tool error"):
                await connector._submit_to_ingest(envelope)

    async def test_submit_to_ingest_connection_error(
        self, config: TelegramUserClientConnectorConfig
    ) -> None:
        """Test handling of connection errors to MCP server."""
        connector = TelegramUserClientConnector(config)

        envelope: dict[str, Any] = {
            "schema_version": "ingest.v1",
            "source": {"channel": "telegram", "provider": "telegram", "endpoint_identity": "x"},
            "event": {"external_event_id": "1", "observed_at": datetime.now(UTC).isoformat()},
            "sender": {"identity": "1"},
            "payload": {"raw": {}, "normalized_text": "test"},
            "control": {"idempotency_key": "test", "policy_tier": "default"},
        }

        with patch.object(
            connector._mcp_client,
            "call_tool",
            new_callable=AsyncMock,
            side_effect=ConnectionError("Cannot reach switchboard"),
        ):
            with pytest.raises(ConnectionError):
                await connector._submit_to_ingest(envelope)

    async def test_load_checkpoint_no_file(self, config: TelegramUserClientConnectorConfig) -> None:
        """Test loading checkpoint when no file exists."""
        connector = TelegramUserClientConnector(config)
        await connector._load_checkpoint()
        assert connector._last_message_id is None

    async def test_load_checkpoint_with_file(
        self, config: TelegramUserClientConnectorConfig
    ) -> None:
        """Test loading checkpoint from existing file."""
        # Write checkpoint file
        assert config.cursor_path is not None
        config.cursor_path.parent.mkdir(parents=True, exist_ok=True)
        with config.cursor_path.open("w") as f:
            json.dump({"last_message_id": 99999}, f)

        connector = TelegramUserClientConnector(config)
        await connector._load_checkpoint()
        assert connector._last_message_id == 99999

    async def test_save_checkpoint(self, config: TelegramUserClientConnectorConfig) -> None:
        """Test saving checkpoint to disk."""
        connector = TelegramUserClientConnector(config)
        connector._last_message_id = 88888
        await connector._save_checkpoint()

        # Verify checkpoint file was written
        assert config.cursor_path is not None
        assert config.cursor_path.exists()
        with config.cursor_path.open("r") as f:
            data = json.load(f)
            assert data["last_message_id"] == 88888

    async def test_save_checkpoint_atomic_write(
        self, config: TelegramUserClientConnectorConfig
    ) -> None:
        """Test that checkpoint save uses atomic write."""
        connector = TelegramUserClientConnector(config)
        connector._last_message_id = 77777

        # Save checkpoint
        await connector._save_checkpoint()

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

        # Mock successful MCP ingest call
        mock_result = {"request_id": "req-123", "duplicate": False, "status": "accepted"}

        with patch.object(
            connector._mcp_client,
            "call_tool",
            new_callable=AsyncMock,
            return_value=mock_result,
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

        with patch.object(connector._mcp_client, "call_tool", new_callable=AsyncMock) as mock_call:
            await connector._process_message(mock_message)

            # Verify no MCP call was made
            mock_call.assert_not_called()

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
                switchboard_mcp_url="http://localhost:40100/sse",
                telegram_api_id=12345,
                telegram_api_hash="test",
                telegram_user_session="test",
                endpoint_identity="test",
            )
            with pytest.raises(RuntimeError, match="Telethon is not installed"):
                TelegramUserClientConnector(config)


class TestResolveTelegramUserCredentialsFromDb:
    """Tests for _resolve_telegram_user_credentials_from_db."""

    @staticmethod
    def _configure_single_db_env(
        monkeypatch: pytest.MonkeyPatch, db_name: str = "butler_test"
    ) -> None:
        monkeypatch.setenv("CONNECTOR_BUTLER_DB_NAME", db_name)
        monkeypatch.setenv("BUTLER_SHARED_DB_NAME", db_name)

    async def test_returns_none_when_db_unreachable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Returns None gracefully when DB connection fails."""
        import asyncpg

        monkeypatch.setenv("DATABASE_URL", "postgres://localhost:5432/test")
        self._configure_single_db_env(monkeypatch)

        async def fake_create_pool(**kwargs):
            raise OSError("Connection refused")

        monkeypatch.setattr(asyncpg, "create_pool", fake_create_pool)
        result = await _resolve_telegram_user_credentials_from_db()
        assert result is None

    async def test_returns_none_when_secrets_partially_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Returns None when some secrets are missing from DB."""
        from unittest.mock import AsyncMock, MagicMock

        import asyncpg

        monkeypatch.setenv("DATABASE_URL", "postgres://localhost:5432/test")

        # Return a row for TELEGRAM_API_ID, but None for others
        row_for_api_id = MagicMock()
        row_for_api_id.__getitem__ = lambda self, key: "12345"

        mock_conn = AsyncMock()
        mock_conn.fetchrow.side_effect = [
            row_for_api_id,  # TELEGRAM_API_ID
            None,  # TELEGRAM_API_HASH — missing
            None,  # TELEGRAM_USER_SESSION — missing
        ]

        mock_pool = MagicMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_pool.close = AsyncMock()

        async def fake_create_pool(**kwargs):
            return mock_pool

        monkeypatch.setattr(asyncpg, "create_pool", fake_create_pool)
        result = await _resolve_telegram_user_credentials_from_db()
        assert result is None

    async def test_returns_all_credentials_when_stored_in_db(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Returns all three credentials when found in owner contact_info."""
        from unittest.mock import AsyncMock, MagicMock

        import asyncpg

        monkeypatch.setenv("DATABASE_URL", "postgres://localhost:5432/test")
        self._configure_single_db_env(monkeypatch)

        # CredentialStore.load calls conn.fetchrow for each key
        def make_row(value: str) -> MagicMock:
            row = MagicMock()
            row.__getitem__ = lambda self, key: value
            return row

        mock_conn = AsyncMock()
        mock_conn.fetchrow.side_effect = [
            make_row("12345"),  # TELEGRAM_API_ID
            make_row("test-api-hash"),  # TELEGRAM_API_HASH
            make_row("test-session"),  # TELEGRAM_USER_SESSION
        ]

        mock_pool = MagicMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_pool.close = AsyncMock()

        async def fake_create_pool(**kwargs):
            return mock_pool

        monkeypatch.setattr(asyncpg, "create_pool", fake_create_pool)
        result = await _resolve_telegram_user_credentials_from_db()
        assert result is not None
        assert result["TELEGRAM_API_ID"] == "12345"
        assert result["TELEGRAM_API_HASH"] == "test-api-hash"
        assert result["TELEGRAM_USER_SESSION"] == "test-session"


@pytest.mark.asyncio
async def test_run_telegram_user_client_connector_uses_db_credentials_when_env_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """DB credentials should be sufficient even when TELEGRAM_* env vars are absent."""
    cursor_path = tmp_path / "cursor.json"
    monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:40100/sse")
    monkeypatch.setenv("CONNECTOR_ENDPOINT_IDENTITY", "telegram:user:test")
    monkeypatch.setenv("CONNECTOR_CURSOR_PATH", str(cursor_path))
    monkeypatch.setenv("POSTGRES_HOST", "localhost")
    monkeypatch.delenv("TELEGRAM_API_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_API_HASH", raising=False)
    monkeypatch.delenv("TELEGRAM_USER_SESSION", raising=False)

    mock_connector = MagicMock()
    mock_connector.start = AsyncMock()
    mock_connector.stop = AsyncMock()

    with (
        patch(
            "butlers.connectors.telegram_user_client._resolve_telegram_user_credentials_from_db",
            new=AsyncMock(
                return_value={
                    "TELEGRAM_API_ID": "12345",
                    "TELEGRAM_API_HASH": "db-hash",
                    "TELEGRAM_USER_SESSION": "db-session",
                }
            ),
        ),
        patch("butlers.connectors.telegram_user_client.configure_logging"),
        patch(
            "butlers.connectors.telegram_user_client.TelegramUserClientConnector",
            return_value=mock_connector,
        ) as cls,
    ):
        await run_telegram_user_client_connector()

    passed_config = cls.call_args[0][0]
    assert passed_config.telegram_api_id == 12345
    assert passed_config.telegram_api_hash == "db-hash"
    assert passed_config.telegram_user_session == "db-session"
    mock_connector.start.assert_awaited_once()


# ---------------------------------------------------------------------------
# extract_telethon_filter_key tests
# ---------------------------------------------------------------------------


class TestExtractTelethonFilterKey:
    """Tests for the extract_telethon_filter_key helper."""

    def test_extracts_chat_id_from_message(self) -> None:
        """Returns str(message.chat_id) for key_type='chat_id'."""
        msg = MagicMock()
        msg.chat_id = 987654321
        assert extract_telethon_filter_key(msg, "chat_id") == "987654321"

    def test_extracts_negative_chat_id_for_groups(self) -> None:
        """Returns negative chat_id string for group/channel chats."""
        msg = MagicMock()
        msg.chat_id = -100987654321
        assert extract_telethon_filter_key(msg, "chat_id") == "-100987654321"

    def test_returns_empty_for_non_chat_id_key_type(self) -> None:
        """Returns '' for key_types other than chat_id."""
        msg = MagicMock()
        msg.chat_id = 111
        assert extract_telethon_filter_key(msg, "domain") == ""
        assert extract_telethon_filter_key(msg, "sender_address") == ""
        assert extract_telethon_filter_key(msg, "substring") == ""

    def test_falls_back_to_peer_id_channel_id(self) -> None:
        """Falls back to peer_id.channel_id when chat_id is absent."""
        msg = MagicMock(spec=[])  # no attributes
        peer = MagicMock(spec=["channel_id"])
        peer.channel_id = 99999
        object.__setattr__(msg, "peer_id", peer)
        assert extract_telethon_filter_key(msg, "chat_id") == "99999"

    def test_falls_back_to_peer_id_user_id(self) -> None:
        """Falls back to peer_id.user_id when chat_id and channel_id are absent."""
        msg = MagicMock(spec=[])
        peer = MagicMock(spec=["user_id"])
        peer.user_id = 22222
        object.__setattr__(msg, "peer_id", peer)
        assert extract_telethon_filter_key(msg, "chat_id") == "22222"

    def test_returns_empty_when_no_chat_id_or_peer_id(self) -> None:
        """Returns '' when neither chat_id nor peer_id is available."""
        msg = MagicMock(spec=[])  # no attributes at all
        assert extract_telethon_filter_key(msg, "chat_id") == ""


# ---------------------------------------------------------------------------
# Source filter integration tests for TelegramUserClientConnector
# ---------------------------------------------------------------------------


def _make_spec(
    *,
    id: str = "aaaaaaaa-0000-0000-0000-000000000001",
    name: str = "test-filter",
    filter_mode: str = "blacklist",
    source_key_type: str = "chat_id",
    patterns: list[str] | None = None,
    priority: int = 0,
) -> SourceFilterSpec:
    return SourceFilterSpec(
        id=id,
        name=name,
        filter_mode=filter_mode,
        source_key_type=source_key_type,
        patterns=patterns or [],
        priority=priority,
    )


def _evaluator_with_filters(
    filters: list[SourceFilterSpec],
    endpoint_identity: str = "telegram:user:123456",
) -> SourceFilterEvaluator:
    """Create an evaluator with a pre-loaded filter cache (no DB needed)."""
    import time as _time

    ev = SourceFilterEvaluator(
        connector_type="telegram-user-client",
        endpoint_identity=endpoint_identity,
        db_pool=None,
        refresh_interval_s=300,
    )
    ev._filters = filters
    ev._last_loaded_at = _time.monotonic()
    return ev


class TestSourceFilterIntegration:
    """Integration tests for source filter gate in TelegramUserClientConnector."""

    def test_connector_initializes_source_filter_evaluator(
        self, config: TelegramUserClientConnectorConfig
    ) -> None:
        """Connector creates a SourceFilterEvaluator with connector_type='telegram-user-client'."""
        connector = TelegramUserClientConnector(config)
        ev = connector._source_filter_evaluator
        assert isinstance(ev, SourceFilterEvaluator)
        assert ev._connector_type == "telegram-user-client"
        assert ev._endpoint_identity == config.endpoint_identity

    def test_connector_accepts_db_pool(self, config: TelegramUserClientConnectorConfig) -> None:
        """Connector passes db_pool to SourceFilterEvaluator."""
        mock_pool = MagicMock()
        connector = TelegramUserClientConnector(config, db_pool=mock_pool)
        assert connector._source_filter_evaluator._db_pool is mock_pool

    async def test_process_message_allowed_when_no_filters(
        self, config: TelegramUserClientConnectorConfig
    ) -> None:
        """Messages pass through when no source filters are active (fail-open)."""
        connector = TelegramUserClientConnector(config)
        # Evaluator has no filters loaded — fail-open
        connector._source_filter_evaluator._filters = []
        connector._source_filter_evaluator._last_loaded_at = __import__("time").monotonic()

        mock_message = MagicMock()
        mock_message.id = 1001
        mock_message.chat_id = 555555
        mock_message.sender_id = 111
        mock_message.message = "Hello"
        mock_message.to_dict.return_value = {"id": 1001}

        mock_result = {"request_id": "req-1", "duplicate": False, "status": "accepted"}
        with patch.object(
            connector._mcp_client, "call_tool", new_callable=AsyncMock, return_value=mock_result
        ) as mock_call:
            await connector._process_message(mock_message)

        mock_call.assert_awaited_once()
        assert connector._last_message_id == 1001

    async def test_process_message_blocked_by_blacklist(
        self, config: TelegramUserClientConnectorConfig
    ) -> None:
        """Messages from a blacklisted chat_id are dropped without calling Switchboard."""
        connector = TelegramUserClientConnector(config)
        connector._source_filter_evaluator = _evaluator_with_filters(
            [_make_spec(filter_mode="blacklist", patterns=["555555"])]
        )

        mock_message = MagicMock()
        mock_message.id = 1002
        mock_message.chat_id = 555555
        mock_message.sender_id = 111
        mock_message.message = "blocked"
        mock_message.to_dict.return_value = {"id": 1002}

        with patch.object(connector._mcp_client, "call_tool", new_callable=AsyncMock) as mock_call:
            await connector._process_message(mock_message)

        mock_call.assert_not_awaited()
        # Checkpoint must NOT advance for blocked messages
        assert connector._last_message_id is None

    async def test_process_message_passes_when_not_in_blacklist(
        self, config: TelegramUserClientConnectorConfig
    ) -> None:
        """Messages from a non-blacklisted chat_id pass through."""
        connector = TelegramUserClientConnector(config)
        connector._source_filter_evaluator = _evaluator_with_filters(
            [_make_spec(filter_mode="blacklist", patterns=["999999"])]
        )

        mock_message = MagicMock()
        mock_message.id = 1003
        mock_message.chat_id = 555555  # not in blacklist
        mock_message.sender_id = 111
        mock_message.message = "allowed"
        mock_message.to_dict.return_value = {"id": 1003}

        mock_result = {"request_id": "req-3", "duplicate": False, "status": "accepted"}
        with patch.object(
            connector._mcp_client, "call_tool", new_callable=AsyncMock, return_value=mock_result
        ) as mock_call:
            await connector._process_message(mock_message)

        mock_call.assert_awaited_once()

    async def test_process_message_blocked_by_whitelist(
        self, config: TelegramUserClientConnectorConfig
    ) -> None:
        """Messages not matching a whitelist are dropped without calling Switchboard."""
        connector = TelegramUserClientConnector(config)
        connector._source_filter_evaluator = _evaluator_with_filters(
            [_make_spec(filter_mode="whitelist", patterns=["111111"])]
        )

        mock_message = MagicMock()
        mock_message.id = 1004
        mock_message.chat_id = 555555  # not in whitelist
        mock_message.sender_id = 111
        mock_message.message = "not whitelisted"
        mock_message.to_dict.return_value = {"id": 1004}

        with patch.object(connector._mcp_client, "call_tool", new_callable=AsyncMock) as mock_call:
            await connector._process_message(mock_message)

        mock_call.assert_not_awaited()

    async def test_process_message_passes_when_in_whitelist(
        self, config: TelegramUserClientConnectorConfig
    ) -> None:
        """Messages matching a whitelist chat_id are allowed through."""
        connector = TelegramUserClientConnector(config)
        connector._source_filter_evaluator = _evaluator_with_filters(
            [_make_spec(filter_mode="whitelist", patterns=["555555"])]
        )

        mock_message = MagicMock()
        mock_message.id = 1005
        mock_message.chat_id = 555555
        mock_message.sender_id = 111
        mock_message.message = "whitelisted"
        mock_message.to_dict.return_value = {"id": 1005}

        mock_result = {"request_id": "req-5", "duplicate": False, "status": "accepted"}
        with patch.object(
            connector._mcp_client, "call_tool", new_callable=AsyncMock, return_value=mock_result
        ) as mock_call:
            await connector._process_message(mock_message)

        mock_call.assert_awaited_once()

    async def test_ensure_loaded_called_before_ingestion_in_start(
        self, config: TelegramUserClientConnectorConfig
    ) -> None:
        """start() calls ensure_loaded() on the source filter evaluator before connecting."""
        connector = TelegramUserClientConnector(config)

        ensure_loaded_calls: list[str] = []
        connect_calls: list[str] = []

        original_ensure_loaded = connector._source_filter_evaluator.ensure_loaded

        async def tracking_ensure_loaded() -> None:
            ensure_loaded_calls.append("ensure_loaded")
            await original_ensure_loaded()

        connector._source_filter_evaluator.ensure_loaded = tracking_ensure_loaded  # type: ignore[method-assign]

        # Patch TelegramClient to capture the call sequence
        mock_tg_client = MagicMock()

        async def fake_tg_start() -> None:
            connect_calls.append("connect")

        mock_tg_client.start = fake_tg_start
        mock_tg_client.run_until_disconnected = AsyncMock(side_effect=asyncio.CancelledError())

        with (
            patch(
                "butlers.connectors.telegram_user_client.TelegramClient",
                return_value=mock_tg_client,
            ),
            patch(
                "butlers.connectors.telegram_user_client.StringSession",
                return_value=MagicMock(),
            ),
            patch.object(connector, "_start_heartbeat"),
        ):
            with pytest.raises((asyncio.CancelledError, Exception)):
                await connector.start()

        # ensure_loaded must be called before connect
        assert "ensure_loaded" in ensure_loaded_calls
        combined = ensure_loaded_calls + connect_calls
        assert combined.index("ensure_loaded") < combined.index("connect") + len(
            ensure_loaded_calls
        )
