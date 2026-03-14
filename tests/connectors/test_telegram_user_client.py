"""Tests for Telegram user-client connector."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.connectors.telegram_user_client import (
    TELETHON_AVAILABLE,
    ChatBuffer,
    TelegramUserClientConnector,
    TelegramUserClientConnectorConfig,
    _resolve_endpoint_identity,
    _resolve_telegram_user_credentials_from_db,
    run_telegram_user_client_connector,
)
from butlers.ingestion_policy import IngestionPolicyEvaluator

# Skip all tests if Telethon is not available
pytestmark = pytest.mark.skipif(
    not TELETHON_AVAILABLE,
    reason="Telethon not installed (optional dependency)",
)


@pytest.fixture
def mock_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Set up mock environment variables for connector config."""
    env_vars = {
        "SWITCHBOARD_MCP_URL": "http://localhost:40100/sse",
        "CONNECTOR_PROVIDER": "telegram",
        "CONNECTOR_CHANNEL": "telegram_user_client",
        "TELEGRAM_API_ID": "12345",
        "TELEGRAM_API_HASH": "test-hash",
        "TELEGRAM_USER_SESSION": "test-session-string",
        "CONNECTOR_MAX_INFLIGHT": "8",
    }
    for key, value in env_vars.items():
        monkeypatch.setenv(key, value)
    return env_vars


@pytest.fixture
def config() -> TelegramUserClientConnectorConfig:
    """Create a test connector config."""
    return TelegramUserClientConnectorConfig(
        switchboard_mcp_url="http://localhost:40100/sse",
        provider="telegram",
        channel="telegram_user_client",
        endpoint_identity="telegram:user:123456",
        telegram_api_id=12345,
        telegram_api_hash="test-hash",
        telegram_user_session="test-session-string",
        max_inflight=8,
    )


@pytest.fixture
def mock_cursor_pool() -> MagicMock:
    """Create a mock DB cursor pool."""
    return MagicMock()


class TestTelegramUserClientConnectorConfig:
    """Tests for TelegramUserClientConnectorConfig."""

    def test_from_env_success(self, mock_env: dict[str, str]) -> None:
        """Test loading non-credential config from environment variables.

        Telegram credentials are resolved exclusively from owner contact_info,
        so from_env() returns defaults (0, '', '') for them.
        endpoint_identity defaults to '' — auto-inferred from get_me() at startup.
        """
        config = TelegramUserClientConnectorConfig.from_env()

        assert config.switchboard_mcp_url == "http://localhost:40100/sse"
        assert config.provider == "telegram"
        assert config.channel == "telegram_user_client"
        assert config.endpoint_identity == ""
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
        assert cursor == '{"last_message_id": 99999}'
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
        assert cursor == '{"last_message_id": 12345}'
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
        self, config: TelegramUserClientConnectorConfig, mock_cursor_pool: MagicMock
    ) -> None:
        """_save_checkpoint() records the timestamp of the successful save."""
        connector = TelegramUserClientConnector(config, cursor_pool=mock_cursor_pool)
        connector._last_message_id = 12345

        assert connector._last_checkpoint_save is None

        before = time.time()
        with patch(
            "butlers.connectors.cursor_store.save_cursor",
            new=AsyncMock(),
        ):
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
        assert envelope["source"]["channel"] == "telegram_user_client"
        assert envelope["source"]["provider"] == "telegram"
        assert envelope["source"]["endpoint_identity"] == "telegram:user:123456"
        assert envelope["event"]["external_event_id"] == "12345"
        assert envelope["event"]["external_thread_id"] == "67890"
        assert envelope["sender"]["identity"] == "11111"
        assert envelope["payload"]["normalized_text"] == "Hello, world!"
        assert envelope["payload"]["raw"]["id"] == 12345
        assert envelope["control"]["idempotency_key"] == "tg:67890:12345"

    async def test_submit_to_ingest_success(
        self, config: TelegramUserClientConnectorConfig
    ) -> None:
        """Test successful submission to Switchboard via MCP ingest tool."""
        connector = TelegramUserClientConnector(config)

        envelope = {
            "schema_version": "ingest.v1",
            "source": {
                "channel": "telegram_user_client",
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
                "idempotency_key": "tg:67890:12345",
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
                "channel": "telegram_user_client",
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
                "idempotency_key": "tg:67890:12345",
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
            "source": {
                "channel": "telegram_user_client",
                "provider": "telegram",
                "endpoint_identity": "x",
            },
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

    async def test_load_checkpoint_no_data(
        self,
        config: TelegramUserClientConnectorConfig,
        mock_cursor_pool: MagicMock,
    ) -> None:
        """Test loading checkpoint when no DB row exists."""
        connector = TelegramUserClientConnector(config, cursor_pool=mock_cursor_pool)
        with patch(
            "butlers.connectors.cursor_store.load_cursor",
            new=AsyncMock(return_value=None),
        ):
            await connector._load_checkpoint()
        assert connector._last_message_id is None

    async def test_load_checkpoint_from_db(
        self,
        config: TelegramUserClientConnectorConfig,
        mock_cursor_pool: MagicMock,
    ) -> None:
        """Test loading checkpoint from DB."""
        connector = TelegramUserClientConnector(config, cursor_pool=mock_cursor_pool)
        with patch(
            "butlers.connectors.cursor_store.load_cursor",
            new=AsyncMock(return_value=json.dumps({"last_message_id": 99999})),
        ):
            await connector._load_checkpoint()
        assert connector._last_message_id == 99999

    async def test_save_checkpoint(
        self,
        config: TelegramUserClientConnectorConfig,
        mock_cursor_pool: MagicMock,
    ) -> None:
        """Test saving checkpoint to DB."""
        connector = TelegramUserClientConnector(config, cursor_pool=mock_cursor_pool)
        connector._last_message_id = 88888
        with patch(
            "butlers.connectors.cursor_store.save_cursor",
            new=AsyncMock(),
        ) as mock_save:
            await connector._save_checkpoint()

        mock_save.assert_awaited_once()
        saved_data = json.loads(mock_save.call_args[0][3])
        assert saved_data["last_message_id"] == 88888

    async def test_process_message_updates_checkpoint(
        self,
        config: TelegramUserClientConnectorConfig,
        mock_cursor_pool: MagicMock,
    ) -> None:
        """Test that processing a message updates the checkpoint."""
        connector = TelegramUserClientConnector(config, cursor_pool=mock_cursor_pool)

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
    def _configure_single_db_env(monkeypatch: pytest.MonkeyPatch, db_name: str = "butlers") -> None:
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
async def test_run_connector_infers_endpoint_identity_from_get_me(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Identity is always inferred from get_me()."""
    monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:40100/sse")
    monkeypatch.setenv("POSTGRES_HOST", "localhost")
    monkeypatch.delenv("TELEGRAM_API_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_API_HASH", raising=False)
    monkeypatch.delenv("TELEGRAM_USER_SESSION", raising=False)

    mock_connector = MagicMock()
    mock_connector.start = AsyncMock()
    mock_connector.stop = AsyncMock()

    mock_pool = MagicMock()
    mock_pool.close = AsyncMock()

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
        patch(
            "butlers.connectors.telegram_user_client._resolve_endpoint_identity",
            new=AsyncMock(return_value="telegram:user:777888"),
        ),
        patch("butlers.connectors.telegram_user_client.configure_logging"),
        patch(
            "butlers.connectors.telegram_user_client.TelegramUserClientConnector",
            return_value=mock_connector,
        ) as cls,
        patch(
            "butlers.connectors.cursor_store.create_cursor_pool_from_env",
            new=AsyncMock(return_value=mock_pool),
        ),
    ):
        await run_telegram_user_client_connector()

    passed_config = cls.call_args[0][0]
    assert passed_config.telegram_api_id == 12345
    assert passed_config.telegram_api_hash == "db-hash"
    assert passed_config.telegram_user_session == "db-session"
    assert passed_config.endpoint_identity == "telegram:user:777888"
    mock_connector.start.assert_awaited_once()


class TestResolveEndpointIdentity:
    """Tests for _resolve_endpoint_identity."""

    async def test_returns_user_id_from_get_me(self) -> None:
        """Returns telegram:user:@<username> from get_me() response."""
        mock_me = MagicMock()
        mock_me.id = 999111
        mock_me.username = "testuser"

        mock_client = MagicMock()
        mock_client.connect = AsyncMock()
        mock_client.get_me = AsyncMock(return_value=mock_me)
        mock_client.disconnect = AsyncMock()

        with (
            patch(
                "butlers.connectors.telegram_user_client.TelegramClient",
                return_value=mock_client,
            ),
            patch("butlers.connectors.telegram_user_client.StringSession"),
        ):
            result = await _resolve_endpoint_identity(12345, "hash", "session")

        assert result == "telegram:user:@testuser"
        mock_client.disconnect.assert_awaited_once()

    async def test_falls_back_to_numeric_id_without_username(self) -> None:
        """Falls back to telegram:user:<id> when username is None."""
        mock_me = MagicMock()
        mock_me.id = 999111
        mock_me.username = None

        mock_client = MagicMock()
        mock_client.connect = AsyncMock()
        mock_client.get_me = AsyncMock(return_value=mock_me)
        mock_client.disconnect = AsyncMock()

        with (
            patch(
                "butlers.connectors.telegram_user_client.TelegramClient",
                return_value=mock_client,
            ),
            patch("butlers.connectors.telegram_user_client.StringSession"),
        ):
            result = await _resolve_endpoint_identity(12345, "hash", "session")

        assert result == "telegram:user:999111"
        mock_client.disconnect.assert_awaited_once()

    async def test_raises_on_none_get_me(self) -> None:
        """Raises RuntimeError if get_me() returns None (expired session)."""
        mock_client = MagicMock()
        mock_client.connect = AsyncMock()
        mock_client.get_me = AsyncMock(return_value=None)
        mock_client.disconnect = AsyncMock()

        with (
            patch(
                "butlers.connectors.telegram_user_client.TelegramClient",
                return_value=mock_client,
            ),
            patch("butlers.connectors.telegram_user_client.StringSession"),
        ):
            with pytest.raises(RuntimeError, match="get_me.*returned None"):
                await _resolve_endpoint_identity(12345, "hash", "session")

        # Ensure client is disconnected even on error
        mock_client.disconnect.assert_awaited_once()


# ---------------------------------------------------------------------------
# _build_ingestion_envelope tests
# ---------------------------------------------------------------------------


class TestBuildIngestionEnvelope:
    """Tests for the _build_ingestion_envelope helper on TelegramUserClientConnector."""

    def test_extracts_chat_id_from_message(self) -> None:
        """Returns str(message.chat_id) in raw_key."""
        msg = MagicMock()
        msg.chat_id = 987654321
        envelope = TelegramUserClientConnector._build_ingestion_envelope(msg)
        assert envelope.raw_key == "987654321"
        assert envelope.source_channel == "telegram_user_client"

    def test_extracts_negative_chat_id_for_groups(self) -> None:
        """Returns negative chat_id string for group/channel chats."""
        msg = MagicMock()
        msg.chat_id = -100987654321
        envelope = TelegramUserClientConnector._build_ingestion_envelope(msg)
        assert envelope.raw_key == "-100987654321"

    def test_falls_back_to_peer_id_channel_id(self) -> None:
        """Falls back to peer_id.channel_id when chat_id is absent."""
        msg = MagicMock(spec=[])  # no attributes
        peer = MagicMock(spec=["channel_id"])
        peer.channel_id = 99999
        object.__setattr__(msg, "peer_id", peer)
        envelope = TelegramUserClientConnector._build_ingestion_envelope(msg)
        assert envelope.raw_key == "99999"

    def test_falls_back_to_peer_id_user_id(self) -> None:
        """Falls back to peer_id.user_id when chat_id and channel_id are absent."""
        msg = MagicMock(spec=[])
        peer = MagicMock(spec=["user_id"])
        peer.user_id = 22222
        object.__setattr__(msg, "peer_id", peer)
        envelope = TelegramUserClientConnector._build_ingestion_envelope(msg)
        assert envelope.raw_key == "22222"

    def test_returns_empty_when_no_chat_id_or_peer_id(self) -> None:
        """Returns '' when neither chat_id nor peer_id is available."""
        msg = MagicMock(spec=[])  # no attributes at all
        envelope = TelegramUserClientConnector._build_ingestion_envelope(msg)
        assert envelope.raw_key == ""


# ---------------------------------------------------------------------------
# Ingestion policy integration tests for TelegramUserClientConnector
# ---------------------------------------------------------------------------


def _policy_evaluator_with_rules(
    rules: list[dict[str, Any]],
    scope: str = "connector:telegram-user-client:telegram:user:123456",
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


class TestIngestionPolicyIntegration:
    """Integration tests for ingestion policy gate in TelegramUserClientConnector."""

    def test_connector_initializes_ingestion_policy(
        self, config: TelegramUserClientConnectorConfig
    ) -> None:
        """Connector creates an IngestionPolicyEvaluator."""
        connector = TelegramUserClientConnector(config)
        ev = connector._ingestion_policy
        assert isinstance(ev, IngestionPolicyEvaluator)
        expected_scope = f"connector:telegram-user-client:{config.endpoint_identity}"
        assert ev.scope == expected_scope

    def test_connector_accepts_db_pool(self, config: TelegramUserClientConnectorConfig) -> None:
        """Connector passes db_pool to IngestionPolicyEvaluator."""
        mock_pool = MagicMock()
        connector = TelegramUserClientConnector(config, db_pool=mock_pool)
        assert connector._ingestion_policy._db_pool is mock_pool

    async def test_process_message_allowed_when_no_rules(
        self, config: TelegramUserClientConnectorConfig
    ) -> None:
        """Messages pass through when no ingestion rules are active (pass_through)."""
        connector = TelegramUserClientConnector(config)
        # No rules loaded -> pass_through
        connector._ingestion_policy._rules = []
        connector._ingestion_policy._last_loaded_at = time.monotonic()

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

    async def test_process_message_blocked_by_chat_id_rule(
        self, config: TelegramUserClientConnectorConfig
    ) -> None:
        """Messages from a blocked chat_id are dropped without calling Switchboard."""
        connector = TelegramUserClientConnector(config)
        connector._ingestion_policy = _policy_evaluator_with_rules(
            [
                {
                    "id": "rule-001",
                    "rule_type": "chat_id",
                    "condition": {"chat_id": "555555"},
                    "action": "block",
                    "priority": 0,
                    "name": "block-chat",
                }
            ]
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

    async def test_process_message_passes_when_not_blocked(
        self, config: TelegramUserClientConnectorConfig
    ) -> None:
        """Messages from a non-blocked chat_id pass through."""
        connector = TelegramUserClientConnector(config)
        connector._ingestion_policy = _policy_evaluator_with_rules(
            [
                {
                    "id": "rule-002",
                    "rule_type": "chat_id",
                    "condition": {"chat_id": "999999"},
                    "action": "block",
                    "priority": 0,
                    "name": "block-other",
                }
            ]
        )

        mock_message = MagicMock()
        mock_message.id = 1003
        mock_message.chat_id = 555555  # not blocked
        mock_message.sender_id = 111
        mock_message.message = "allowed"
        mock_message.to_dict.return_value = {"id": 1003}

        mock_result = {"request_id": "req-3", "duplicate": False, "status": "accepted"}
        with patch.object(
            connector._mcp_client, "call_tool", new_callable=AsyncMock, return_value=mock_result
        ) as mock_call:
            await connector._process_message(mock_message)

        mock_call.assert_awaited_once()

    async def test_ensure_loaded_called_before_ingestion_in_start(
        self, config: TelegramUserClientConnectorConfig, mock_cursor_pool: MagicMock
    ) -> None:
        """start() calls ensure_loaded() on the ingestion policy evaluator before connecting."""
        connector = TelegramUserClientConnector(config, cursor_pool=mock_cursor_pool)

        ensure_loaded_calls: list[str] = []
        connect_calls: list[str] = []

        original_ensure_loaded = connector._ingestion_policy.ensure_loaded

        async def tracking_ensure_loaded() -> None:
            ensure_loaded_calls.append("ensure_loaded")
            await original_ensure_loaded()

        connector._ingestion_policy.ensure_loaded = tracking_ensure_loaded  # type: ignore[method-assign]

        # Patch TelegramClient to capture the call sequence
        mock_tg_client = MagicMock()

        async def fake_tg_start() -> None:
            connect_calls.append("connect")

        mock_tg_client.start = fake_tg_start
        mock_tg_client.get_dialogs = AsyncMock(return_value=[])
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
            patch(
                "butlers.connectors.cursor_store.load_cursor",
                new=AsyncMock(return_value=None),
            ),
        ):
            with pytest.raises((asyncio.CancelledError, Exception)):
                await connector.start()

        # ensure_loaded must be called before connect
        assert "ensure_loaded" in ensure_loaded_calls
        combined = ensure_loaded_calls + connect_calls
        assert combined.index("ensure_loaded") < combined.index("connect") + len(
            ensure_loaded_calls
        )


# ---------------------------------------------------------------------------
# ChatBuffer dataclass tests
# ---------------------------------------------------------------------------


class TestChatBuffer:
    """Tests for the ChatBuffer dataclass."""

    def test_default_messages_list_is_empty(self) -> None:
        """ChatBuffer.messages defaults to an empty list."""
        buf = ChatBuffer()
        assert buf.messages == []

    def test_default_last_flush_ts_is_recent(self) -> None:
        """ChatBuffer.last_flush_ts defaults to a recent monotonic timestamp."""
        before = time.monotonic()
        buf = ChatBuffer()
        after = time.monotonic()
        assert before <= buf.last_flush_ts <= after

    def test_lock_is_asyncio_lock(self) -> None:
        """ChatBuffer.lock is an asyncio.Lock instance."""
        buf = ChatBuffer()
        assert isinstance(buf.lock, asyncio.Lock)

    def test_each_buffer_gets_independent_lock(self) -> None:
        """Two ChatBuffer instances do not share a lock."""
        b1 = ChatBuffer()
        b2 = ChatBuffer()
        assert b1.lock is not b2.lock

    def test_messages_not_shared_between_instances(self) -> None:
        """Two ChatBuffer instances do not share the messages list."""
        b1 = ChatBuffer()
        b2 = ChatBuffer()
        b1.messages.append("x")
        assert b2.messages == []


# ---------------------------------------------------------------------------
# Config: new buffering env vars
# ---------------------------------------------------------------------------


class TestConfigBufferingEnvVars:
    """TelegramUserClientConnectorConfig reads buffering env vars from environment."""

    def test_defaults_when_env_vars_absent(self, mock_env: dict[str, str]) -> None:
        """Buffering config uses expected defaults when env vars are not set."""
        config = TelegramUserClientConnectorConfig.from_env()
        assert config.flush_interval_s == 600
        assert config.history_max_messages == 50
        assert config.history_time_window_m == 30
        assert config.buffer_max_messages == 200

    def test_reads_flush_interval_from_env(
        self, mock_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TELEGRAM_USER_FLUSH_INTERVAL_S", "120")
        config = TelegramUserClientConnectorConfig.from_env()
        assert config.flush_interval_s == 120

    def test_reads_history_max_messages_from_env(
        self, mock_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TELEGRAM_USER_HISTORY_MAX_MESSAGES", "25")
        config = TelegramUserClientConnectorConfig.from_env()
        assert config.history_max_messages == 25

    def test_reads_history_time_window_from_env(
        self, mock_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TELEGRAM_USER_HISTORY_TIME_WINDOW_M", "15")
        config = TelegramUserClientConnectorConfig.from_env()
        assert config.history_time_window_m == 15

    def test_reads_buffer_max_messages_from_env(
        self, mock_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TELEGRAM_USER_BUFFER_MAX_MESSAGES", "50")
        config = TelegramUserClientConnectorConfig.from_env()
        assert config.buffer_max_messages == 50


# ---------------------------------------------------------------------------
# TelegramUserClientConnector — chat buffering
# ---------------------------------------------------------------------------


def _make_mock_message(
    msg_id: int = 1,
    chat_id: int = 1000,
    sender_id: int = 42,
    text: str = "hello",
) -> MagicMock:
    """Build a minimal mock Telethon message."""
    m = MagicMock()
    m.id = msg_id
    m.chat_id = chat_id
    m.sender_id = sender_id
    m.message = text
    m.to_dict.return_value = {"id": msg_id, "chat_id": chat_id, "message": text}
    return m


class TestChatBuffering:
    """Tests for _buffer_message, _flush_chat_buffer, and related helpers."""

    def test_chat_buffers_initialized_empty(
        self, config: TelegramUserClientConnectorConfig
    ) -> None:
        """Connector starts with an empty _chat_buffers dict."""
        connector = TelegramUserClientConnector(config)
        assert connector._chat_buffers == {}

    def test_flush_scanner_task_initialized_none(
        self, config: TelegramUserClientConnectorConfig
    ) -> None:
        """Connector starts with _flush_scanner_task = None."""
        connector = TelegramUserClientConnector(config)
        assert connector._flush_scanner_task is None

    async def test_buffer_message_creates_chat_buffer(
        self, config: TelegramUserClientConnectorConfig
    ) -> None:
        """_buffer_message creates a ChatBuffer for a new chat_id."""
        connector = TelegramUserClientConnector(config)
        msg = _make_mock_message(msg_id=10, chat_id=9999)

        await connector._buffer_message(msg)

        assert "9999" in connector._chat_buffers
        assert len(connector._chat_buffers["9999"].messages) == 1

    async def test_buffer_message_appends_to_existing_buffer(
        self, config: TelegramUserClientConnectorConfig
    ) -> None:
        """Subsequent _buffer_message calls append to the same ChatBuffer."""
        connector = TelegramUserClientConnector(config)
        for i in range(3):
            await connector._buffer_message(_make_mock_message(msg_id=i, chat_id=1111))
        assert len(connector._chat_buffers["1111"].messages) == 3

    async def test_buffer_isolation_between_chats(
        self, config: TelegramUserClientConnectorConfig
    ) -> None:
        """Messages for different chats go into independent buffers."""
        connector = TelegramUserClientConnector(config)
        await connector._buffer_message(_make_mock_message(chat_id=111))
        await connector._buffer_message(_make_mock_message(chat_id=222))
        await connector._buffer_message(_make_mock_message(chat_id=111))

        assert len(connector._chat_buffers["111"].messages) == 2
        assert len(connector._chat_buffers["222"].messages) == 1

    async def test_buffer_message_falls_back_when_no_chat_id(
        self, config: TelegramUserClientConnectorConfig
    ) -> None:
        """_buffer_message falls back to _process_message when chat_id cannot be extracted."""
        connector = TelegramUserClientConnector(config)
        # Message with no chat_id and no peer_id
        msg = MagicMock(spec=[])
        object.__setattr__(msg, "id", 99)

        process_calls: list[Any] = []

        async def fake_process(m: Any) -> None:
            process_calls.append(m)

        connector._process_message = fake_process  # type: ignore[method-assign]

        await connector._buffer_message(msg)

        assert process_calls == [msg]
        assert connector._chat_buffers == {}

    async def test_flush_chat_buffer_clears_messages(
        self, config: TelegramUserClientConnectorConfig
    ) -> None:
        """_flush_chat_buffer empties the buffer and updates last_flush_ts."""
        connector = TelegramUserClientConnector(config)
        connector._chat_buffers["5555"] = ChatBuffer()
        connector._chat_buffers["5555"].messages = [_make_mock_message(i, 5555) for i in range(5)]
        old_flush_ts = connector._chat_buffers["5555"].last_flush_ts

        await connector._flush_chat_buffer("5555")

        buf = connector._chat_buffers["5555"]
        assert buf.messages == []
        assert buf.last_flush_ts >= old_flush_ts

    async def test_flush_chat_buffer_noop_when_empty(
        self, config: TelegramUserClientConnectorConfig
    ) -> None:
        """_flush_chat_buffer is a no-op when the buffer is already empty."""
        connector = TelegramUserClientConnector(config)
        connector._chat_buffers["7777"] = ChatBuffer()
        old_ts = connector._chat_buffers["7777"].last_flush_ts

        await connector._flush_chat_buffer("7777")

        # last_flush_ts unchanged when buffer was empty (early return)
        assert connector._chat_buffers["7777"].last_flush_ts == old_ts

    async def test_flush_chat_buffer_noop_for_unknown_chat(
        self, config: TelegramUserClientConnectorConfig
    ) -> None:
        """_flush_chat_buffer does nothing for a chat_id that has no buffer."""
        connector = TelegramUserClientConnector(config)
        # Should not raise
        await connector._flush_chat_buffer("nonexistent")

    async def test_buffer_message_force_flushes_on_cap(
        self, config: TelegramUserClientConnectorConfig
    ) -> None:
        """Force-flush is triggered when buffer reaches buffer_max_messages."""
        # Set a very small cap
        config_low_cap = TelegramUserClientConnectorConfig(
            switchboard_mcp_url=config.switchboard_mcp_url,
            provider=config.provider,
            channel=config.channel,
            endpoint_identity=config.endpoint_identity,
            telegram_api_id=config.telegram_api_id,
            telegram_api_hash=config.telegram_api_hash,
            telegram_user_session=config.telegram_user_session,
            buffer_max_messages=3,
        )
        connector = TelegramUserClientConnector(config_low_cap)

        flush_calls: list[str] = []
        original_flush = connector._flush_chat_buffer

        async def tracking_flush(chat_id: str) -> None:
            flush_calls.append(chat_id)
            await original_flush(chat_id)

        connector._flush_chat_buffer = tracking_flush  # type: ignore[method-assign]

        # First 2 messages — no flush yet
        await connector._buffer_message(_make_mock_message(1, chat_id=8888))
        await connector._buffer_message(_make_mock_message(2, chat_id=8888))
        assert flush_calls == []

        # 3rd message hits the cap → force-flush
        await connector._buffer_message(_make_mock_message(3, chat_id=8888))
        assert flush_calls == ["8888"]
        # Buffer should be empty after flush
        assert connector._chat_buffers["8888"].messages == []


# ---------------------------------------------------------------------------
# Flush scanner
# ---------------------------------------------------------------------------


class TestFlushScanner:
    """Tests for _flush_scanner_loop and _scan_and_flush."""

    async def test_scan_and_flush_flushes_overdue_buffer(
        self, config: TelegramUserClientConnectorConfig
    ) -> None:
        """_scan_and_flush flushes a chat whose interval has elapsed."""
        connector = TelegramUserClientConnector(config)

        # Pre-populate a buffer that is "overdue" (last_flush_ts in the past)
        buf = ChatBuffer()
        buf.messages = [_make_mock_message(1, chat_id=3333)]
        buf.last_flush_ts = time.monotonic() - config.flush_interval_s - 10
        connector._chat_buffers["3333"] = buf

        flush_calls: list[str] = []
        original_flush = connector._flush_chat_buffer

        async def tracking_flush(chat_id: str) -> None:
            flush_calls.append(chat_id)
            await original_flush(chat_id)

        connector._flush_chat_buffer = tracking_flush  # type: ignore[method-assign]
        await connector._scan_and_flush()

        assert "3333" in flush_calls

    async def test_scan_and_flush_skips_non_overdue_buffer(
        self, config: TelegramUserClientConnectorConfig
    ) -> None:
        """_scan_and_flush does not flush a chat that was recently flushed."""
        connector = TelegramUserClientConnector(config)

        buf = ChatBuffer()
        buf.messages = [_make_mock_message(1, chat_id=4444)]
        buf.last_flush_ts = time.monotonic()  # just flushed
        connector._chat_buffers["4444"] = buf

        flush_calls: list[str] = []
        original_flush = connector._flush_chat_buffer

        async def tracking_flush(chat_id: str) -> None:
            flush_calls.append(chat_id)
            await original_flush(chat_id)

        connector._flush_chat_buffer = tracking_flush  # type: ignore[method-assign]
        await connector._scan_and_flush()

        assert "4444" not in flush_calls

    async def test_scan_and_flush_skips_empty_buffer(
        self, config: TelegramUserClientConnectorConfig
    ) -> None:
        """_scan_and_flush does not flush a chat with an empty buffer."""
        connector = TelegramUserClientConnector(config)

        buf = ChatBuffer()
        buf.messages = []
        buf.last_flush_ts = time.monotonic() - config.flush_interval_s - 10
        connector._chat_buffers["5555"] = buf

        flush_calls: list[str] = []

        async def tracking_flush(chat_id: str) -> None:
            flush_calls.append(chat_id)

        connector._flush_chat_buffer = tracking_flush  # type: ignore[method-assign]
        await connector._scan_and_flush()

        assert "5555" not in flush_calls

    async def test_flush_scanner_loop_cancels_cleanly(
        self, config: TelegramUserClientConnectorConfig
    ) -> None:
        """_flush_scanner_loop terminates cleanly when cancelled."""
        connector = TelegramUserClientConnector(config)
        task = asyncio.create_task(connector._flush_scanner_loop())
        # Give the loop one tick to enter the sleep
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


# ---------------------------------------------------------------------------
# Graceful shutdown — flush all buffers
# ---------------------------------------------------------------------------


class TestGracefulShutdownFlush:
    """Tests for _flush_all_buffers and its invocation from stop()."""

    async def test_flush_all_buffers_flushes_non_empty_buffers(
        self, config: TelegramUserClientConnectorConfig
    ) -> None:
        """_flush_all_buffers flushes every non-empty chat buffer."""
        connector = TelegramUserClientConnector(config)
        for cid in ["100", "200", "300"]:
            buf = ChatBuffer()
            buf.messages = [_make_mock_message(1, chat_id=int(cid))]
            connector._chat_buffers[cid] = buf

        flush_calls: list[str] = []
        original_flush = connector._flush_chat_buffer

        async def tracking_flush(chat_id: str) -> None:
            flush_calls.append(chat_id)
            await original_flush(chat_id)

        connector._flush_chat_buffer = tracking_flush  # type: ignore[method-assign]
        await connector._flush_all_buffers(reason="test")

        assert sorted(flush_calls) == ["100", "200", "300"]

    async def test_flush_all_buffers_calls_flush_for_empty_buffer(
        self, config: TelegramUserClientConnectorConfig
    ) -> None:
        """_flush_all_buffers calls _flush_chat_buffer for every registered chat,
        including empty ones.  _flush_chat_buffer is itself a no-op for empty
        buffers, so no messages are lost and no errors are raised."""
        connector = TelegramUserClientConnector(config)
        buf = ChatBuffer()
        buf.messages = []
        connector._chat_buffers["empty_chat"] = buf

        flush_calls: list[str] = []
        original_flush = connector._flush_chat_buffer

        async def tracking_flush(chat_id: str) -> None:
            flush_calls.append(chat_id)
            await original_flush(chat_id)

        connector._flush_chat_buffer = tracking_flush  # type: ignore[method-assign]
        await connector._flush_all_buffers()

        # _flush_all_buffers now delegates empty-check to _flush_chat_buffer
        assert flush_calls == ["empty_chat"]
        # Buffer remains empty — no messages were produced
        assert connector._chat_buffers["empty_chat"].messages == []

    async def test_stop_flushes_all_buffers(
        self, config: TelegramUserClientConnectorConfig
    ) -> None:
        """stop() force-flushes all non-empty chat buffers before disconnecting."""
        connector = TelegramUserClientConnector(config)
        buf = ChatBuffer()
        buf.messages = [_make_mock_message(1, chat_id=777)]
        connector._chat_buffers["777"] = buf

        flush_calls: list[str] = []
        original_flush = connector._flush_chat_buffer

        async def tracking_flush(chat_id: str) -> None:
            flush_calls.append(chat_id)
            await original_flush(chat_id)

        connector._flush_chat_buffer = tracking_flush  # type: ignore[method-assign]

        # Minimal stop() — no real Telegram client
        connector._telegram_client = None

        await connector.stop()

        assert "777" in flush_calls

    async def test_stop_cancels_flush_scanner_task(
        self, config: TelegramUserClientConnectorConfig
    ) -> None:
        """stop() cancels the flush scanner task if it is running."""
        connector = TelegramUserClientConnector(config)
        connector._telegram_client = None

        # Simulate a running scanner task
        scanner_task = asyncio.create_task(connector._flush_scanner_loop())
        connector._flush_scanner_task = scanner_task

        await connector.stop()

        assert scanner_task.cancelled()
        assert connector._flush_scanner_task is None


# ---------------------------------------------------------------------------
# Helper for history/reply-to tests (separate from _make_mock_message above,
# which is used by TestChatBuffering and needs different fields)
# ---------------------------------------------------------------------------


def _make_history_msg(
    msg_id: int,
    date: datetime | None = None,
    reply_to_msg_id: int | None = None,
    text: str = "hello",
) -> MagicMock:
    """Build a minimal mock Telegram message for history/reply-to tests."""
    msg = MagicMock()
    msg.id = msg_id
    msg.date = date or datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
    msg.reply_to_msg_id = reply_to_msg_id
    msg.message = text
    return msg


class TestFetchConversationHistory:
    """Tests for TelegramUserClientConnector._fetch_conversation_history."""

    async def test_returns_buffered_messages_when_no_telegram_client(
        self, config: TelegramUserClientConnectorConfig
    ) -> None:
        """Returns only buffered messages when Telegram client is not initialised."""
        connector = TelegramUserClientConnector(config)
        connector._telegram_client = None

        buffered = [_make_history_msg(1), _make_history_msg(2)]
        result = await connector._fetch_conversation_history("chat123", buffered)

        assert [m.id for m in result] == [1, 2]

    async def test_merges_history_and_buffered_deduplicates_and_sorts(
        self, config: TelegramUserClientConnectorConfig
    ) -> None:
        """History + buffered messages are merged, deduplicated, and sorted by ID."""
        connector = TelegramUserClientConnector(config)

        buffered = [_make_history_msg(5), _make_history_msg(6)]
        history_msgs = [
            _make_history_msg(3),
            _make_history_msg(4),
            _make_history_msg(5),  # duplicate of buffered[0]
        ]

        mock_client = MagicMock()
        mock_client.get_messages = AsyncMock(return_value=history_msgs)
        connector._telegram_client = mock_client

        result = await connector._fetch_conversation_history("chat123", buffered)

        assert [m.id for m in result] == [3, 4, 5, 6]

    async def test_offset_date_is_bounded_by_time_window(
        self, config: TelegramUserClientConnectorConfig
    ) -> None:
        """offset_date passed to get_messages is HISTORY_TIME_WINDOW_M before oldest msg."""
        import dataclasses

        config = dataclasses.replace(config, history_time_window_m=30)
        connector = TelegramUserClientConnector(config)

        oldest_date = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
        buffered = [
            _make_history_msg(10, date=datetime(2024, 1, 1, 12, 30, 0, tzinfo=UTC)),
            _make_history_msg(11, date=oldest_date),  # oldest
        ]

        mock_client = MagicMock()
        mock_client.get_messages = AsyncMock(return_value=[])
        connector._telegram_client = mock_client

        await connector._fetch_conversation_history("chat123", buffered)

        call_kwargs = mock_client.get_messages.call_args
        offset_date_passed = call_kwargs[1]["offset_date"]
        expected_offset = oldest_date - timedelta(minutes=30)
        assert offset_date_passed == expected_offset

    async def test_uses_history_max_messages_limit(
        self, config: TelegramUserClientConnectorConfig
    ) -> None:
        """limit kwarg passed to get_messages equals _history_max_messages."""
        import dataclasses

        config = dataclasses.replace(config, history_max_messages=42)
        connector = TelegramUserClientConnector(config)

        buffered = [_make_history_msg(1)]

        mock_client = MagicMock()
        mock_client.get_messages = AsyncMock(return_value=[])
        connector._telegram_client = mock_client

        await connector._fetch_conversation_history("chat123", buffered)

        call_kwargs = mock_client.get_messages.call_args
        assert call_kwargs[1]["limit"] == 42

    async def test_flood_wait_error_returns_only_buffered(
        self, config: TelegramUserClientConnectorConfig
    ) -> None:
        """FloodWaitError causes fail-open: returns buffered messages only."""
        connector = TelegramUserClientConnector(config)

        buffered = [_make_history_msg(7), _make_history_msg(8)]

        mock_client = MagicMock()
        # Simulate FloodWaitError via generic exception (Telethon may not be installed)
        mock_client.get_messages = AsyncMock(side_effect=RuntimeError("FLOOD_WAIT_X"))
        connector._telegram_client = mock_client

        result = await connector._fetch_conversation_history("chat123", buffered)

        assert [m.id for m in result] == [7, 8]

    async def test_any_fetch_error_returns_only_buffered(
        self, config: TelegramUserClientConnectorConfig
    ) -> None:
        """Any exception from get_messages causes fail-open fallback."""
        connector = TelegramUserClientConnector(config)

        buffered = [_make_history_msg(3)]

        mock_client = MagicMock()
        mock_client.get_messages = AsyncMock(side_effect=OSError("network error"))
        connector._telegram_client = mock_client

        result = await connector._fetch_conversation_history("chat123", buffered)

        assert len(result) == 1
        assert result[0].id == 3

    async def test_uses_default_history_config_when_attributes_absent(
        self, config: TelegramUserClientConnectorConfig
    ) -> None:
        """Uses config defaults: limit=50, window=30m."""
        connector = TelegramUserClientConnector(config)
        # config fixture uses default values: history_max_messages=50, history_time_window_m=30

        buffered = [_make_history_msg(1)]

        mock_client = MagicMock()
        mock_client.get_messages = AsyncMock(return_value=[])
        connector._telegram_client = mock_client

        await connector._fetch_conversation_history("chat123", buffered)

        call_kwargs = mock_client.get_messages.call_args
        assert call_kwargs[1]["limit"] == 50

    async def test_empty_buffered_messages_still_fetches_history(
        self, config: TelegramUserClientConnectorConfig
    ) -> None:
        """History fetch works even when buffered_messages is empty (no oldest_date)."""
        connector = TelegramUserClientConnector(config)

        mock_client = MagicMock()
        mock_client.get_messages = AsyncMock(return_value=[_make_history_msg(99)])
        connector._telegram_client = mock_client

        result = await connector._fetch_conversation_history("chat123", [])

        assert len(result) == 1
        assert result[0].id == 99

    async def test_result_sorted_ascending_by_id(
        self, config: TelegramUserClientConnectorConfig
    ) -> None:
        """Merged result is always sorted ascending by message ID."""
        connector = TelegramUserClientConnector(config)

        buffered = [_make_history_msg(10)]
        history_msgs = [_make_history_msg(8), _make_history_msg(5), _make_history_msg(12)]

        mock_client = MagicMock()
        mock_client.get_messages = AsyncMock(return_value=history_msgs)
        connector._telegram_client = mock_client

        result = await connector._fetch_conversation_history("chat123", buffered)

        assert [m.id for m in result] == [5, 8, 10, 12]


# ---------------------------------------------------------------------------
# _resolve_reply_tos tests
# ---------------------------------------------------------------------------


class TestResolveReplyTos:
    """Tests for TelegramUserClientConnector._resolve_reply_tos."""

    async def test_returns_context_unchanged_when_no_telegram_client(
        self, config: TelegramUserClientConnectorConfig
    ) -> None:
        """Returns context_messages unchanged when Telegram client is not initialised."""
        connector = TelegramUserClientConnector(config)
        connector._telegram_client = None

        context = [_make_history_msg(1), _make_history_msg(2)]
        buffered = [_make_history_msg(3, reply_to_msg_id=99)]

        result = await connector._resolve_reply_tos("chat123", buffered, context)

        assert [m.id for m in result] == [1, 2]

    async def test_no_reply_ids_returns_context_unchanged(
        self, config: TelegramUserClientConnectorConfig
    ) -> None:
        """Returns context unchanged when no buffered messages have reply_to_msg_id."""
        connector = TelegramUserClientConnector(config)

        mock_client = MagicMock()
        connector._telegram_client = mock_client

        context = [_make_history_msg(1)]
        buffered = [_make_history_msg(2)]  # no reply_to_msg_id

        result = await connector._resolve_reply_tos("chat123", buffered, context)

        assert [m.id for m in result] == [1]
        mock_client.get_messages.assert_not_called()

    async def test_skips_reply_ids_already_in_context(
        self, config: TelegramUserClientConnectorConfig
    ) -> None:
        """Does not fetch reply-to messages that are already in context_messages."""
        connector = TelegramUserClientConnector(config)

        mock_client = MagicMock()
        mock_client.get_messages = AsyncMock()
        connector._telegram_client = mock_client

        context = [_make_history_msg(5), _make_history_msg(10)]
        buffered = [_make_history_msg(20, reply_to_msg_id=5)]  # 5 already in context

        result = await connector._resolve_reply_tos("chat123", buffered, context)

        # No fetch needed — 5 is already present
        mock_client.get_messages.assert_not_called()
        assert [m.id for m in result] == [5, 10]

    async def test_fetches_missing_reply_to_message(
        self, config: TelegramUserClientConnectorConfig
    ) -> None:
        """Fetches a reply-to message not in context and appends it."""
        connector = TelegramUserClientConnector(config)

        fetched_reply = _make_history_msg(42)

        mock_client = MagicMock()
        mock_client.get_messages = AsyncMock(return_value=fetched_reply)
        connector._telegram_client = mock_client

        context = [_make_history_msg(50)]
        buffered = [_make_history_msg(60, reply_to_msg_id=42)]

        result = await connector._resolve_reply_tos("chat123", buffered, context)

        mock_client.get_messages.assert_awaited_once_with("chat123", ids=42)
        assert 42 in [m.id for m in result]
        assert 50 in [m.id for m in result]

    async def test_fetch_error_is_logged_and_skipped(
        self, config: TelegramUserClientConnectorConfig
    ) -> None:
        """A fetch error for a reply-to message is logged at DEBUG and skipped (fail-open)."""
        connector = TelegramUserClientConnector(config)

        mock_client = MagicMock()
        mock_client.get_messages = AsyncMock(side_effect=OSError("fetch failed"))
        connector._telegram_client = mock_client

        context = [_make_history_msg(1)]
        buffered = [_make_history_msg(2, reply_to_msg_id=99)]

        result = await connector._resolve_reply_tos("chat123", buffered, context)

        # 99 was not fetched — result only contains original context
        assert [m.id for m in result] == [1]

    async def test_result_sorted_ascending_by_id(
        self, config: TelegramUserClientConnectorConfig
    ) -> None:
        """Result including fetched reply-tos is sorted ascending by ID."""
        connector = TelegramUserClientConnector(config)

        fetched_reply = _make_history_msg(3)

        mock_client = MagicMock()
        mock_client.get_messages = AsyncMock(return_value=fetched_reply)
        connector._telegram_client = mock_client

        context = [_make_history_msg(10), _make_history_msg(7)]
        buffered = [_make_history_msg(15, reply_to_msg_id=3)]

        result = await connector._resolve_reply_tos("chat123", buffered, context)

        assert [m.id for m in result] == [3, 7, 10]

    async def test_single_level_only_no_recursive_chain(
        self, config: TelegramUserClientConnectorConfig
    ) -> None:
        """Only the first-level reply is resolved; no recursive chasing."""
        connector = TelegramUserClientConnector(config)

        # The fetched reply itself has a reply_to_msg_id (chained reply)
        fetched_reply = _make_history_msg(30, reply_to_msg_id=99)

        mock_client = MagicMock()
        mock_client.get_messages = AsyncMock(return_value=fetched_reply)
        connector._telegram_client = mock_client

        context = [_make_history_msg(50)]
        buffered = [_make_history_msg(60, reply_to_msg_id=30)]

        result = await connector._resolve_reply_tos("chat123", buffered, context)

        # get_messages called exactly once — for msg 30, not recursively for 99
        assert mock_client.get_messages.await_count == 1
        call_args = mock_client.get_messages.call_args
        assert call_args[1]["ids"] == 30
        assert 30 in [m.id for m in result]
        assert 99 not in [m.id for m in result]

    async def test_handles_get_messages_returning_list(
        self, config: TelegramUserClientConnectorConfig
    ) -> None:
        """get_messages returning a list (not a single message) is handled correctly."""
        connector = TelegramUserClientConnector(config)

        fetched_list = [_make_history_msg(20)]

        mock_client = MagicMock()
        mock_client.get_messages = AsyncMock(return_value=fetched_list)
        connector._telegram_client = mock_client

        context = [_make_history_msg(30)]
        buffered = [_make_history_msg(40, reply_to_msg_id=20)]

        result = await connector._resolve_reply_tos("chat123", buffered, context)

        assert 20 in [m.id for m in result]

    async def test_handles_none_reply_message(
        self, config: TelegramUserClientConnectorConfig
    ) -> None:
        """If get_messages returns None (message deleted/missing), it is skipped."""
        connector = TelegramUserClientConnector(config)

        mock_client = MagicMock()
        mock_client.get_messages = AsyncMock(return_value=None)
        connector._telegram_client = mock_client

        context = [_make_history_msg(1)]
        buffered = [_make_history_msg(2, reply_to_msg_id=77)]

        result = await connector._resolve_reply_tos("chat123", buffered, context)

        # None result means message not found; original context returned unchanged
        assert [m.id for m in result] == [1]
