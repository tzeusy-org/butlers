"""Tests for connector health check endpoints."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from butlers.connectors.gmail import GmailConnectorConfig, GmailConnectorRuntime, HealthStatus
from butlers.connectors.telegram_bot import (
    TelegramBotConnector,
    TelegramBotConnectorConfig,
)

pytestmark = pytest.mark.unit


class TestTelegramBotConnectorHealth:
    """Tests for Telegram bot connector health endpoint."""

    @pytest.fixture
    def telegram_config(self, tmp_path: Path) -> TelegramBotConnectorConfig:
        """Create test Telegram connector config."""
        cursor_path = tmp_path / "telegram_cursor.json"
        return TelegramBotConnectorConfig(
            switchboard_mcp_url="http://localhost:40100/sse",
            provider="telegram",
            channel="telegram",
            endpoint_identity="telegram:bot:test_bot",
            telegram_token="test-telegram-token",
            cursor_path=cursor_path,
            poll_interval_s=1.0,
            max_inflight=4,
            health_port=40081,
        )

    @pytest.fixture
    def telegram_connector(
        self, telegram_config: TelegramBotConnectorConfig
    ) -> TelegramBotConnector:
        """Create Telegram connector instance."""
        return TelegramBotConnector(telegram_config)

    async def test_initial_health_status(self, telegram_connector: TelegramBotConnector) -> None:
        """Test health status immediately after initialization."""
        health = await telegram_connector.get_health_status()

        assert health.status == "healthy"
        assert health.uptime_seconds >= 0
        assert health.uptime_seconds < 1  # Should be very small initially
        assert health.last_checkpoint_save_at is None
        assert health.last_ingest_submit_at is None
        assert health.source_api_connectivity == "unknown"
        assert health.timestamp

    async def test_health_after_checkpoint_save(
        self, telegram_connector: TelegramBotConnector
    ) -> None:
        """Test health status reflects checkpoint save timestamp."""
        # Simulate checkpoint save
        telegram_connector._last_checkpoint_save = time.time()

        health = await telegram_connector.get_health_status()

        assert health.status == "healthy"
        assert health.last_checkpoint_save_at is not None
        assert health.last_ingest_submit_at is None

    async def test_health_after_ingest_submit(
        self, telegram_connector: TelegramBotConnector
    ) -> None:
        """Test health status reflects ingest submission timestamp."""
        # Simulate successful ingest submission
        telegram_connector._last_ingest_submit = time.time()

        health = await telegram_connector.get_health_status()

        assert health.status == "healthy"
        assert health.last_ingest_submit_at is not None

    async def test_health_api_connectivity_connected(
        self, telegram_connector: TelegramBotConnector
    ) -> None:
        """Test health status when source API is connected."""
        telegram_connector._source_api_ok = True

        health = await telegram_connector.get_health_status()

        assert health.status == "healthy"
        assert health.source_api_connectivity == "connected"

    async def test_health_api_connectivity_disconnected(
        self, telegram_connector: TelegramBotConnector
    ) -> None:
        """Test health status when source API is disconnected."""
        telegram_connector._source_api_ok = False

        health = await telegram_connector.get_health_status()

        assert health.status == "unhealthy"
        assert health.source_api_connectivity == "disconnected"

    async def test_health_uptime_increases(self, telegram_connector: TelegramBotConnector) -> None:
        """Test that uptime increases over time."""
        health1 = await telegram_connector.get_health_status()
        uptime1 = health1.uptime_seconds

        time.sleep(0.1)  # Wait 100ms

        health2 = await telegram_connector.get_health_status()
        uptime2 = health2.uptime_seconds

        assert uptime2 > uptime1


class TestGmailConnectorHealth:
    """Tests for Gmail connector health endpoint."""

    @pytest.fixture
    def gmail_config(self, tmp_path: Path) -> GmailConnectorConfig:
        """Create test Gmail connector config."""
        cursor_path = tmp_path / "gmail_cursor.json"
        return GmailConnectorConfig(
            switchboard_mcp_url="http://localhost:40100/sse",
            connector_provider="gmail",
            connector_channel="email",
            connector_endpoint_identity="gmail:user:test@example.com",
            connector_cursor_path=cursor_path,
            connector_max_inflight=4,
            connector_health_port=40082,
            gmail_client_id="test-client-id",
            gmail_client_secret="test-client-secret",
            gmail_refresh_token="test-refresh-token",
            gmail_watch_renew_interval_s=3600,
            gmail_poll_interval_s=60,
        )

    @pytest.fixture
    def gmail_runtime(self, gmail_config: GmailConnectorConfig) -> GmailConnectorRuntime:
        """Create Gmail connector runtime instance."""
        return GmailConnectorRuntime(gmail_config)

    async def test_initial_health_status(self, gmail_runtime: GmailConnectorRuntime) -> None:
        """Test health status immediately after initialization."""
        health = await gmail_runtime.get_health_status()

        assert health.status == "healthy"
        assert health.uptime_seconds >= 0
        assert health.uptime_seconds < 1  # Should be very small initially
        assert health.last_checkpoint_save_at is None
        assert health.last_ingest_submit_at is None
        assert health.source_api_connectivity == "unknown"
        assert health.timestamp

    async def test_health_after_checkpoint_save(self, gmail_runtime: GmailConnectorRuntime) -> None:
        """Test health status reflects checkpoint save timestamp."""
        # Simulate checkpoint save
        gmail_runtime._last_checkpoint_save = time.time()

        health = await gmail_runtime.get_health_status()

        assert health.status == "healthy"
        assert health.last_checkpoint_save_at is not None
        assert health.last_ingest_submit_at is None

    async def test_health_after_ingest_submit(self, gmail_runtime: GmailConnectorRuntime) -> None:
        """Test health status reflects ingest submission timestamp."""
        # Simulate successful ingest submission
        gmail_runtime._last_ingest_submit = time.time()

        health = await gmail_runtime.get_health_status()

        assert health.status == "healthy"
        assert health.last_ingest_submit_at is not None

    async def test_health_api_connectivity_connected(
        self, gmail_runtime: GmailConnectorRuntime
    ) -> None:
        """Test health status when source API is connected."""
        gmail_runtime._source_api_ok = True

        health = await gmail_runtime.get_health_status()

        assert health.status == "healthy"
        assert health.source_api_connectivity == "connected"

    async def test_health_api_connectivity_disconnected(
        self, gmail_runtime: GmailConnectorRuntime
    ) -> None:
        """Test health status when source API is disconnected."""
        gmail_runtime._source_api_ok = False

        health = await gmail_runtime.get_health_status()

        assert health.status == "unhealthy"
        assert health.source_api_connectivity == "disconnected"

    async def test_health_uptime_increases(self, gmail_runtime: GmailConnectorRuntime) -> None:
        """Test that uptime increases over time."""
        health1 = await gmail_runtime.get_health_status()
        uptime1 = health1.uptime_seconds

        time.sleep(0.1)  # Wait 100ms

        health2 = await gmail_runtime.get_health_status()
        uptime2 = health2.uptime_seconds

        assert uptime2 > uptime1


class TestHealthStatusModel:
    """Tests for HealthStatus Pydantic model."""

    def test_health_status_serialization(self) -> None:
        """Test HealthStatus model can be serialized to JSON."""
        from datetime import UTC, datetime

        status = HealthStatus(
            status="healthy",
            uptime_seconds=123.45,
            last_checkpoint_save_at="2024-01-01T00:00:00Z",
            last_ingest_submit_at="2024-01-01T00:01:00Z",
            source_api_connectivity="connected",
            timestamp=datetime.now(UTC).isoformat(),
        )

        # Should serialize without errors
        json_data = status.model_dump_json()
        assert json_data
        assert "healthy" in json_data
        assert "123.45" in json_data

    def test_health_status_with_nulls(self) -> None:
        """Test HealthStatus model handles null timestamps."""
        from datetime import UTC, datetime

        status = HealthStatus(
            status="healthy",
            uptime_seconds=10.0,
            last_checkpoint_save_at=None,
            last_ingest_submit_at=None,
            source_api_connectivity="unknown",
            timestamp=datetime.now(UTC).isoformat(),
        )

        assert status.last_checkpoint_save_at is None
        assert status.last_ingest_submit_at is None
        assert status.source_api_connectivity == "unknown"
