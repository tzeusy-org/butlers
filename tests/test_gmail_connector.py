"""Tests for Gmail connector runtime."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from butlers.connectors.gmail import (
    GmailConnectorConfig,
    GmailConnectorRuntime,
    GmailCursor,
)


@pytest.fixture
def temp_cursor_path(tmp_path: Path) -> Path:
    """Create temporary cursor file path."""
    return tmp_path / "cursor.json"


@pytest.fixture
def gmail_config(temp_cursor_path: Path) -> GmailConnectorConfig:
    """Create test Gmail connector config."""
    return GmailConnectorConfig(
        switchboard_api_base_url="http://localhost:8000",
        switchboard_api_token="test-token",
        connector_provider="gmail",
        connector_channel="email",
        connector_endpoint_identity="gmail:user:test@example.com",
        connector_cursor_path=temp_cursor_path,
        connector_max_inflight=4,
        gmail_client_id="test-client-id",
        gmail_client_secret="test-client-secret",
        gmail_refresh_token="test-refresh-token",
        gmail_watch_renew_interval_s=3600,
        gmail_poll_interval_s=5,
    )


@pytest.fixture
def gmail_runtime(gmail_config: GmailConnectorConfig) -> GmailConnectorRuntime:
    """Create Gmail connector runtime instance."""
    return GmailConnectorRuntime(gmail_config)


class TestGmailConnectorConfig:
    """Tests for GmailConnectorConfig."""

    def test_from_env_success(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test successful config loading from environment."""
        cursor_path = tmp_path / "cursor.json"
        monkeypatch.setenv("SWITCHBOARD_API_BASE_URL", "http://localhost:8000")
        monkeypatch.setenv("SWITCHBOARD_API_TOKEN", "test-token")
        monkeypatch.setenv("CONNECTOR_PROVIDER", "gmail")
        monkeypatch.setenv("CONNECTOR_CHANNEL", "email")
        monkeypatch.setenv("CONNECTOR_ENDPOINT_IDENTITY", "gmail:user:test@example.com")
        monkeypatch.setenv("CONNECTOR_CURSOR_PATH", str(cursor_path))
        monkeypatch.setenv("CONNECTOR_MAX_INFLIGHT", "8")
        monkeypatch.setenv("GMAIL_CLIENT_ID", "client-id")
        monkeypatch.setenv("GMAIL_CLIENT_SECRET", "client-secret")
        monkeypatch.setenv("GMAIL_REFRESH_TOKEN", "refresh-token")

        config = GmailConnectorConfig.from_env()

        assert config.switchboard_api_base_url == "http://localhost:8000"
        assert config.switchboard_api_token == "test-token"
        assert config.connector_provider == "gmail"
        assert config.connector_channel == "email"
        assert config.connector_endpoint_identity == "gmail:user:test@example.com"
        assert config.connector_cursor_path == cursor_path
        assert config.connector_max_inflight == 8
        assert config.gmail_client_id == "client-id"
        assert config.gmail_client_secret == "client-secret"
        assert config.gmail_refresh_token == "refresh-token"

    def test_from_env_missing_required(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test config loading fails with missing required env vars."""
        # Clear all required env vars
        monkeypatch.delenv("SWITCHBOARD_API_BASE_URL", raising=False)
        monkeypatch.delenv("CONNECTOR_ENDPOINT_IDENTITY", raising=False)
        monkeypatch.delenv("GMAIL_CLIENT_ID", raising=False)

        # Should raise ValueError for missing CONNECTOR_CURSOR_PATH first
        with pytest.raises((KeyError, ValueError)):
            GmailConnectorConfig.from_env()

    def test_from_env_invalid_integer(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test config loading fails with invalid integer values."""
        cursor_path = tmp_path / "cursor.json"
        monkeypatch.setenv("SWITCHBOARD_API_BASE_URL", "http://localhost:8000")
        monkeypatch.setenv("CONNECTOR_ENDPOINT_IDENTITY", "gmail:user:test@example.com")
        monkeypatch.setenv("CONNECTOR_CURSOR_PATH", str(cursor_path))
        monkeypatch.setenv("GMAIL_CLIENT_ID", "client-id")
        monkeypatch.setenv("GMAIL_CLIENT_SECRET", "client-secret")
        monkeypatch.setenv("GMAIL_REFRESH_TOKEN", "refresh-token")
        monkeypatch.setenv("CONNECTOR_MAX_INFLIGHT", "invalid")

        with pytest.raises(ValueError, match="CONNECTOR_MAX_INFLIGHT must be an integer"):
            GmailConnectorConfig.from_env()


class TestGmailCursor:
    """Tests for GmailCursor model."""

    def test_cursor_serialization(self) -> None:
        """Test cursor can be serialized to JSON."""
        cursor = GmailCursor(
            history_id="12345",
            last_updated_at="2026-02-15T10:00:00Z",
        )

        json_str = cursor.model_dump_json()
        parsed = GmailCursor.model_validate_json(json_str)

        assert parsed.history_id == "12345"
        assert parsed.last_updated_at == "2026-02-15T10:00:00Z"


class TestGmailConnectorRuntime:
    """Tests for GmailConnectorRuntime."""

    async def test_ensure_cursor_file_creates_initial(
        self, gmail_runtime: GmailConnectorRuntime, temp_cursor_path: Path
    ) -> None:
        """Test cursor file is created with initial historyId if missing."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"historyId": "999"}
        mock_response.raise_for_status = MagicMock()

        with (
            patch.object(gmail_runtime, "_http_client", new=AsyncMock()) as mock_client,
            patch.object(gmail_runtime, "_get_access_token", new=AsyncMock(return_value="token")),
        ):
            mock_client.get = AsyncMock(return_value=mock_response)

            await gmail_runtime._ensure_cursor_file()

            assert temp_cursor_path.exists()
            cursor_data = json.loads(temp_cursor_path.read_text())
            assert cursor_data["history_id"] == "999"

    async def test_load_cursor_success(
        self, gmail_runtime: GmailConnectorRuntime, temp_cursor_path: Path
    ) -> None:
        """Test loading cursor from existing file."""
        cursor = GmailCursor(
            history_id="12345",
            last_updated_at="2026-02-15T10:00:00Z",
        )
        temp_cursor_path.write_text(cursor.model_dump_json())

        loaded = await gmail_runtime._load_cursor()

        assert loaded.history_id == "12345"
        assert loaded.last_updated_at == "2026-02-15T10:00:00Z"

    async def test_load_cursor_missing_file(
        self, gmail_runtime: GmailConnectorRuntime, temp_cursor_path: Path
    ) -> None:
        """Test loading cursor fails when file doesn't exist."""
        with pytest.raises(RuntimeError, match="Cursor file not found"):
            await gmail_runtime._load_cursor()

    async def test_save_cursor(
        self, gmail_runtime: GmailConnectorRuntime, temp_cursor_path: Path
    ) -> None:
        """Test saving cursor to disk."""
        cursor = GmailCursor(
            history_id="67890",
            last_updated_at="2026-02-15T11:00:00Z",
        )

        await gmail_runtime._save_cursor(cursor)

        assert temp_cursor_path.exists()
        loaded = GmailCursor.model_validate_json(temp_cursor_path.read_text())
        assert loaded.history_id == "67890"

    async def test_get_access_token_refresh(self, gmail_runtime: GmailConnectorRuntime) -> None:
        """Test OAuth token refresh when expired."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "access_token": "new-token",
            "expires_in": 3600,
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(gmail_runtime, "_http_client", new=AsyncMock()) as mock_client:
            mock_client.post = AsyncMock(return_value=mock_response)

            token = await gmail_runtime._get_access_token()

            assert token == "new-token"
            assert gmail_runtime._access_token == "new-token"
            mock_client.post.assert_called_once()

    async def test_fetch_history_changes_success(
        self, gmail_runtime: GmailConnectorRuntime
    ) -> None:
        """Test fetching history changes from Gmail API."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "history": [
                {"id": "100", "messagesAdded": [{"message": {"id": "msg1"}}]},
                {"id": "101", "messagesAdded": [{"message": {"id": "msg2"}}]},
            ]
        }
        mock_response.raise_for_status = MagicMock()

        with (
            patch.object(gmail_runtime, "_http_client", new=AsyncMock()) as mock_client,
            patch.object(gmail_runtime, "_get_access_token", new=AsyncMock(return_value="token")),
        ):
            mock_client.get = AsyncMock(return_value=mock_response)

            history = await gmail_runtime._fetch_history_changes("99")

            assert len(history) == 2
            assert history[0]["id"] == "100"
            assert history[1]["id"] == "101"

    async def test_fetch_history_changes_404_resets_cursor(
        self, gmail_runtime: GmailConnectorRuntime, temp_cursor_path: Path
    ) -> None:
        """Test history fetch handles 404 (history too old) by resetting cursor."""
        mock_404_response = MagicMock()
        mock_404_response.status_code = 404

        mock_profile_response = MagicMock()
        mock_profile_response.json.return_value = {"historyId": "200"}
        mock_profile_response.raise_for_status = MagicMock()

        with (
            patch.object(gmail_runtime, "_http_client", new=AsyncMock()) as mock_client,
            patch.object(gmail_runtime, "_get_access_token", new=AsyncMock(return_value="token")),
        ):
            mock_client.get = AsyncMock(side_effect=[mock_404_response, mock_profile_response])

            history = await gmail_runtime._fetch_history_changes("1")

            assert history == []
            # Verify cursor was updated
            assert temp_cursor_path.exists()
            cursor_data = json.loads(temp_cursor_path.read_text())
            assert cursor_data["history_id"] == "200"

    def test_extract_message_ids_from_history(self, gmail_runtime: GmailConnectorRuntime) -> None:
        """Test extracting message IDs from history records."""
        history = [
            {
                "id": "100",
                "messagesAdded": [
                    {"message": {"id": "msg1", "threadId": "thread1"}},
                    {"message": {"id": "msg2", "threadId": "thread1"}},
                ],
            },
            {
                "id": "101",
                "messagesAdded": [
                    {"message": {"id": "msg3", "threadId": "thread2"}},
                ],
            },
        ]

        message_ids = gmail_runtime._extract_message_ids_from_history(history)

        assert set(message_ids) == {"msg1", "msg2", "msg3"}

    def test_build_ingest_envelope(self, gmail_runtime: GmailConnectorRuntime) -> None:
        """Test building ingest.v1 envelope from Gmail message data."""
        message_data = {
            "id": "msg123",
            "threadId": "thread456",
            "internalDate": "1708000000000",
            "payload": {
                "headers": [
                    {"name": "From", "value": "sender@example.com"},
                    {"name": "Subject", "value": "Test Email"},
                    {"name": "Message-ID", "value": "<unique-msg-id@example.com>"},
                ],
                "mimeType": "text/plain",
                "body": {
                    "data": "VGVzdCBib2R5IGNvbnRlbnQ=",  # base64: "Test body content"
                },
            },
        }

        envelope = gmail_runtime._build_ingest_envelope(message_data)

        assert envelope["schema_version"] == "ingest.v1"
        assert envelope["source"]["channel"] == "email"
        assert envelope["source"]["provider"] == "gmail"
        assert envelope["event"]["external_event_id"] == "<unique-msg-id@example.com>"
        assert envelope["event"]["external_thread_id"] == "thread456"
        assert envelope["sender"]["identity"] == "sender@example.com"
        assert "Test Email" in envelope["payload"]["normalized_text"]
        assert "Test body content" in envelope["payload"]["normalized_text"]

    async def test_submit_to_ingest_api_success(self, gmail_runtime: GmailConnectorRuntime) -> None:
        """Test submitting envelope to Switchboard ingest API."""
        envelope = {
            "schema_version": "ingest.v1",
            "source": {"channel": "email", "provider": "gmail", "endpoint_identity": "test"},
            "event": {
                "external_event_id": "msg1",
                "external_thread_id": "thread1",
                "observed_at": datetime.now(UTC).isoformat(),
            },
            "sender": {"identity": "sender@example.com"},
            "payload": {"raw": {}, "normalized_text": "test"},
            "control": {"policy_tier": "default"},
        }

        mock_response = MagicMock()
        mock_response.status_code = 202
        mock_response.json.return_value = {
            "request_id": str(uuid4()),
            "status": "accepted",
            "duplicate": False,
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(gmail_runtime, "_http_client", new=AsyncMock()) as mock_client:
            mock_client.post = AsyncMock(return_value=mock_response)

            await gmail_runtime._submit_to_ingest_api(envelope)

            mock_client.post.assert_called_once()
            call_args = mock_client.post.call_args
            assert call_args.kwargs["json"] == envelope
            assert "Authorization" in call_args.kwargs["headers"]

    async def test_submit_to_ingest_api_rate_limit_retry(
        self, gmail_runtime: GmailConnectorRuntime
    ) -> None:
        """Test ingest API handles 429 rate limit with retry."""
        envelope = {
            "schema_version": "ingest.v1",
            "source": {"channel": "email", "provider": "gmail", "endpoint_identity": "test"},
            "event": {
                "external_event_id": "msg1",
                "external_thread_id": None,
                "observed_at": datetime.now(UTC).isoformat(),
            },
            "sender": {"identity": "sender@example.com"},
            "payload": {"raw": {}, "normalized_text": "test"},
            "control": {"policy_tier": "default"},
        }

        # First call returns 429, second call succeeds
        mock_429_response = MagicMock()
        mock_429_response.status_code = 429
        mock_429_response.headers = {"Retry-After": "1"}

        mock_success_response = MagicMock()
        mock_success_response.status_code = 202
        mock_success_response.json.return_value = {
            "request_id": str(uuid4()),
            "status": "accepted",
            "duplicate": False,
        }
        mock_success_response.raise_for_status = MagicMock()

        with (
            patch.object(gmail_runtime, "_http_client", new=AsyncMock()) as mock_client,
            patch("asyncio.sleep", new=AsyncMock()),
        ):
            mock_client.post = AsyncMock(side_effect=[mock_429_response, mock_success_response])

            await gmail_runtime._submit_to_ingest_api(envelope)

            assert mock_client.post.call_count == 2

    def test_extract_body_from_payload_text_plain(
        self, gmail_runtime: GmailConnectorRuntime
    ) -> None:
        """Test extracting body from text/plain payload."""
        import base64

        payload = {
            "mimeType": "text/plain",
            "body": {
                "data": base64.urlsafe_b64encode(b"Hello, world!").decode(),
            },
        }

        body = gmail_runtime._extract_body_from_payload(payload)

        assert body == "Hello, world!"

    def test_extract_body_from_payload_multipart(
        self, gmail_runtime: GmailConnectorRuntime
    ) -> None:
        """Test extracting body from multipart payload."""
        import base64

        payload = {
            "mimeType": "multipart/alternative",
            "parts": [
                {
                    "mimeType": "text/html",
                    "body": {"data": base64.urlsafe_b64encode(b"<p>HTML</p>").decode()},
                },
                {
                    "mimeType": "text/plain",
                    "body": {"data": base64.urlsafe_b64encode(b"Plain text").decode()},
                },
            ],
        }

        body = gmail_runtime._extract_body_from_payload(payload)

        assert body == "Plain text"

    def test_extract_body_from_payload_no_body(self, gmail_runtime: GmailConnectorRuntime) -> None:
        """Test extracting body when no body is present."""
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [],
        }

        body = gmail_runtime._extract_body_from_payload(payload)

        assert body == "(no body)"
