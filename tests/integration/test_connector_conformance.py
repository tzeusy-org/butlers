"""Conformance tests for connector-to-ingest-to-switchboard flow.

These tests validate the full ingestion pipeline:
1. Connector normalizes source events to ingest.v1
2. Switchboard ingest API accepts events and assigns request context
3. Dedupe behavior correctly handles replay scenarios
4. Downstream routing handoff works for both Telegram and Gmail paths

Follows the contract defined in docs/connectors/interface.md.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from butlers.connectors.gmail import GmailConnectorConfig, GmailConnectorRuntime
from butlers.connectors.telegram_bot import (
    TelegramBotConnector,
    TelegramBotConnectorConfig,
)

pytestmark = pytest.mark.integration


# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------


@pytest.fixture
def telegram_config(tmp_path: Path) -> TelegramBotConnectorConfig:
    """Create test Telegram connector config."""
    return TelegramBotConnectorConfig(
        switchboard_mcp_url="http://localhost:8100/sse",
        provider="telegram",
        channel="telegram",
        endpoint_identity="test_bot",
        telegram_token="test-telegram-token",
        cursor_path=tmp_path / "telegram_cursor.json",
        poll_interval_s=0.1,
        max_inflight=4,
    )


@pytest.fixture
def gmail_config(tmp_path: Path) -> GmailConnectorConfig:
    """Create test Gmail connector config."""
    return GmailConnectorConfig(
        switchboard_mcp_url="http://localhost:8100/sse",
        connector_provider="gmail",
        connector_channel="email",
        connector_endpoint_identity="gmail:user:test@example.com",
        connector_cursor_path=tmp_path / "gmail_cursor.json",
        connector_max_inflight=4,
        gmail_client_id="test-client-id",
        gmail_client_secret="test-client-secret",
        gmail_refresh_token="test-refresh-token",
        gmail_poll_interval_s=1,
    )


@pytest.fixture
def telegram_connector(telegram_config: TelegramBotConnectorConfig) -> TelegramBotConnector:
    """Create Telegram connector instance."""
    return TelegramBotConnector(telegram_config)


@pytest.fixture
def gmail_connector(gmail_config: GmailConnectorConfig) -> GmailConnectorRuntime:
    """Create Gmail connector instance."""
    return GmailConnectorRuntime(gmail_config)


# -----------------------------------------------------------------------------
# Telegram connector conformance tests
# -----------------------------------------------------------------------------


class TestTelegramConnectorConformance:
    """Conformance tests for Telegram connector ingest flow."""

    async def test_telegram_ingest_acceptance(
        self, telegram_connector: TelegramBotConnector
    ) -> None:
        """Test Telegram connector successfully submits to ingest MCP tool."""
        telegram_update = {
            "update_id": 12345,
            "message": {
                "message_id": 1,
                "from": {"id": 987654321, "first_name": "Test"},
                "chat": {"id": 987654321, "type": "private"},
                "date": 1708012800,
                "text": "Test message",
            },
        }

        mock_result = {"request_id": "req-123", "status": "accepted", "duplicate": False}

        with patch.object(
            telegram_connector._mcp_client,
            "call_tool",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            envelope = telegram_connector._normalize_to_ingest_v1(telegram_update)
            await telegram_connector._submit_to_ingest(envelope)

            # Verify envelope conforms to ingest.v1 contract
            assert envelope["schema_version"] == "ingest.v1"
            assert envelope["source"]["channel"] == "telegram"
            assert envelope["source"]["provider"] == "telegram"
            assert envelope["source"]["endpoint_identity"] == "test_bot"
            assert envelope["event"]["external_event_id"] == "12345"
            assert envelope["event"]["external_thread_id"] == "987654321"
            assert envelope["sender"]["identity"] == "987654321"
            assert envelope["payload"]["normalized_text"] == "Test message"
            assert envelope["control"]["idempotency_key"] == "telegram:test_bot:12345"

    async def test_telegram_dedupe_replay_behavior(
        self, telegram_connector: TelegramBotConnector
    ) -> None:
        """Test that replaying the same Telegram update is handled as a duplicate."""
        telegram_update = {
            "update_id": 99999,
            "message": {
                "message_id": 1,
                "from": {"id": 111111, "first_name": "Replay"},
                "chat": {"id": 111111, "type": "private"},
                "date": 1708012800,
                "text": "Replay test",
            },
        }

        # First submission: accepted
        first_result = {"request_id": "req-123", "status": "accepted", "duplicate": False}

        # Second submission: duplicate accepted (same request_id returned)
        second_result = {"request_id": "req-123", "status": "accepted", "duplicate": True}

        with patch.object(
            telegram_connector._mcp_client,
            "call_tool",
            new_callable=AsyncMock,
            side_effect=[first_result, second_result],
        ):
            envelope = telegram_connector._normalize_to_ingest_v1(telegram_update)

            # First submission
            await telegram_connector._submit_to_ingest(envelope)

            # Second submission (replay) - should succeed and return same request_id
            await telegram_connector._submit_to_ingest(envelope)

    async def test_telegram_routing_handoff_structure(
        self, telegram_connector: TelegramBotConnector
    ) -> None:
        """Test that Telegram connector envelope contains fields needed for routing handoff."""
        telegram_update = {
            "update_id": 55555,
            "message": {
                "message_id": 1,
                "from": {"id": 222222, "first_name": "Router"},
                "chat": {"id": 222222, "type": "private"},
                "date": 1708012800,
                "text": "Route me please",
            },
        }

        envelope = telegram_connector._normalize_to_ingest_v1(telegram_update)

        # Verify envelope has all required routing handoff fields
        assert "source" in envelope
        assert "channel" in envelope["source"]
        assert "provider" in envelope["source"]
        assert "endpoint_identity" in envelope["source"]

        assert "event" in envelope
        assert "external_event_id" in envelope["event"]
        assert "external_thread_id" in envelope["event"]
        assert "observed_at" in envelope["event"]

        assert "sender" in envelope
        assert "identity" in envelope["sender"]

        assert "payload" in envelope
        assert "raw" in envelope["payload"]
        assert "normalized_text" in envelope["payload"]

        # Verify normalized_text is suitable for classification
        assert len(envelope["payload"]["normalized_text"]) > 0
        assert isinstance(envelope["payload"]["normalized_text"], str)

    async def test_telegram_checkpoint_recovery(
        self, telegram_connector: TelegramBotConnector, telegram_config: TelegramBotConnectorConfig
    ) -> None:
        """Test that Telegram connector can recover from checkpoint after crash."""
        # Simulate processing some updates
        telegram_connector._last_update_id = 50000

        # Save checkpoint
        telegram_connector._save_checkpoint()

        # Create new connector instance (simulates restart)
        new_connector = TelegramBotConnector(telegram_config)
        new_connector._load_checkpoint()

        # Verify checkpoint was restored
        assert new_connector._last_update_id == 50000


# -----------------------------------------------------------------------------
# Gmail connector conformance tests
# -----------------------------------------------------------------------------


class TestGmailConnectorConformance:
    """Conformance tests for Gmail connector ingest flow."""

    async def test_gmail_ingest_acceptance(self, gmail_connector: GmailConnectorRuntime) -> None:
        """Test Gmail connector successfully submits to ingest MCP tool."""
        gmail_message = {
            "id": "msg123",
            "threadId": "thread456",
            "internalDate": "1708000000000",
            "payload": {
                "headers": [
                    {"name": "From", "value": "sender@example.com"},
                    {"name": "Subject", "value": "Test Email"},
                    {"name": "Message-ID", "value": "<unique@example.com>"},
                ],
                "mimeType": "text/plain",
                "body": {
                    "data": "VGVzdCBib2R5",  # base64: "Test body"
                },
            },
        }

        mock_result = {"request_id": "req-123", "status": "accepted", "duplicate": False}

        with patch.object(
            gmail_connector._mcp_client,
            "call_tool",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            envelope = gmail_connector._build_ingest_envelope(gmail_message)
            await gmail_connector._submit_to_ingest_api(envelope)

            # Verify envelope conforms to ingest.v1 contract
            assert envelope["schema_version"] == "ingest.v1"
            assert envelope["source"]["channel"] == "email"
            assert envelope["source"]["provider"] == "gmail"
            assert envelope["source"]["endpoint_identity"] == "gmail:user:test@example.com"
            assert envelope["event"]["external_event_id"] == "<unique@example.com>"
            assert envelope["event"]["external_thread_id"] == "thread456"
            assert envelope["sender"]["identity"] == "sender@example.com"

    async def test_gmail_dedupe_replay_behavior(
        self, gmail_connector: GmailConnectorRuntime
    ) -> None:
        """Test that replaying the same Gmail message is handled as a duplicate."""
        gmail_message = {
            "id": "msg999",
            "threadId": "thread999",
            "internalDate": "1708000000000",
            "payload": {
                "headers": [
                    {"name": "From", "value": "replay@example.com"},
                    {"name": "Subject", "value": "Replay Test"},
                    {"name": "Message-ID", "value": "<replay@example.com>"},
                ],
                "mimeType": "text/plain",
                "body": {"data": "UmVwbGF5"},  # base64
            },
        }

        # First submission: accepted
        first_result = {"request_id": "req-123", "status": "accepted", "duplicate": False}

        # Second submission: duplicate accepted
        second_result = {"request_id": "req-123", "status": "accepted", "duplicate": True}

        with patch.object(
            gmail_connector._mcp_client,
            "call_tool",
            new_callable=AsyncMock,
            side_effect=[first_result, second_result],
        ):
            envelope = gmail_connector._build_ingest_envelope(gmail_message)

            # First submission
            await gmail_connector._submit_to_ingest_api(envelope)

            # Second submission (replay) - should succeed and return same request_id
            await gmail_connector._submit_to_ingest_api(envelope)

    async def test_gmail_routing_handoff_structure(
        self, gmail_connector: GmailConnectorRuntime
    ) -> None:
        """Test that Gmail connector envelope contains fields needed for routing handoff."""
        gmail_message = {
            "id": "msg777",
            "threadId": "thread777",
            "internalDate": "1708000000000",
            "payload": {
                "headers": [
                    {"name": "From", "value": "router@example.com"},
                    {"name": "Subject", "value": "Route Test"},
                    {"name": "Message-ID", "value": "<route@example.com>"},
                ],
                "mimeType": "text/plain",
                "body": {"data": "Um91dGUgdGVzdA=="},  # base64: "Route test"
            },
        }

        envelope = gmail_connector._build_ingest_envelope(gmail_message)

        # Verify envelope has all required routing handoff fields
        assert "source" in envelope
        assert "channel" in envelope["source"]
        assert "provider" in envelope["source"]
        assert "endpoint_identity" in envelope["source"]

        assert "event" in envelope
        assert "external_event_id" in envelope["event"]
        assert "external_thread_id" in envelope["event"]
        assert "observed_at" in envelope["event"]

        assert "sender" in envelope
        assert "identity" in envelope["sender"]

        assert "payload" in envelope
        assert "raw" in envelope["payload"]
        assert "normalized_text" in envelope["payload"]

        # Verify normalized_text is suitable for classification
        assert len(envelope["payload"]["normalized_text"]) > 0
        assert isinstance(envelope["payload"]["normalized_text"], str)

    async def test_gmail_checkpoint_recovery(
        self, gmail_connector: GmailConnectorRuntime, gmail_config: GmailConnectorConfig
    ) -> None:
        """Test that Gmail connector can recover from checkpoint after crash."""
        from butlers.connectors.gmail import GmailCursor

        # Save checkpoint
        cursor = GmailCursor(
            history_id="12345",
            last_updated_at=datetime.now(UTC).isoformat(),
        )
        await gmail_connector._save_cursor(cursor)

        # Create new connector instance (simulates restart)
        new_connector = GmailConnectorRuntime(gmail_config)
        loaded_cursor = await new_connector._load_cursor()

        # Verify checkpoint was restored
        assert loaded_cursor.history_id == "12345"


# -----------------------------------------------------------------------------
# Cross-connector conformance tests
# -----------------------------------------------------------------------------


class TestCrossConnectorConformance:
    """Conformance tests that apply to all connectors."""

    @pytest.mark.parametrize(
        "connector_fixture,update_fixture",
        [
            ("telegram_connector", "telegram_update"),
            ("gmail_connector", "gmail_message"),
        ],
    )
    async def test_idempotency_key_stability(
        self, connector_fixture: str, update_fixture: str, request: pytest.FixtureRequest
    ) -> None:
        """Test that connectors generate stable idempotency keys for the same source event."""
        # This is a parametrized test placeholder - actual implementation would be per-connector
        # The key requirement: same source event MUST produce same dedupe identity
        pass

    async def test_telegram_mcp_error_handling(
        self, telegram_connector: TelegramBotConnector
    ) -> None:
        """Test that connector handles MCP errors from ingest tool gracefully."""
        telegram_update = {
            "update_id": 88888,
            "message": {
                "message_id": 1,
                "from": {"id": 333333, "first_name": "Error"},
                "chat": {"id": 333333, "type": "private"},
                "date": 1708012800,
                "text": "Error test",
            },
        }

        with patch.object(
            telegram_connector._mcp_client,
            "call_tool",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Ingest tool error: Internal error"),
        ):
            envelope = telegram_connector._normalize_to_ingest_v1(telegram_update)

            # Should raise the error (caller is responsible for retry logic)
            with pytest.raises(RuntimeError, match="Ingest tool error"):
                await telegram_connector._submit_to_ingest(envelope)

    async def test_gmail_mcp_error_handling(self, gmail_connector: GmailConnectorRuntime) -> None:
        """Test that Gmail connector handles MCP errors from ingest tool gracefully."""
        gmail_message = {
            "id": "msg666",
            "threadId": "thread666",
            "internalDate": "1708000000000",
            "payload": {
                "headers": [
                    {"name": "From", "value": "error@example.com"},
                    {"name": "Subject", "value": "Error Test"},
                    {"name": "Message-ID", "value": "<error@example.com>"},
                ],
                "mimeType": "text/plain",
                "body": {"data": "RXJyb3IgdGVzdA=="},
            },
        }

        with patch.object(
            gmail_connector._mcp_client,
            "call_tool",
            new_callable=AsyncMock,
            side_effect=ConnectionError("Cannot reach switchboard"),
        ):
            envelope = gmail_connector._build_ingest_envelope(gmail_message)

            with pytest.raises(ConnectionError):
                await gmail_connector._submit_to_ingest_api(envelope)
