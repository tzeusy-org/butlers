"""Tests for Gmail connector runtime."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from butlers.connectors.gmail import (
    AccountHealthStatus,
    GmailAccountLoop,
    GmailConnectorConfig,
    GmailConnectorManager,
    GmailConnectorRuntime,
    GmailCursor,
    GmailProcessConfig,
    _redact_email,
    _resolve_gmail_credentials_from_db,
    resolve_gmail_endpoint_identity,
    run_gmail_connector,
)


@pytest.fixture
def gmail_config() -> GmailConnectorConfig:
    """Create test Gmail connector config."""
    return GmailConnectorConfig(
        switchboard_mcp_url="http://localhost:40100/sse",
        connector_provider="gmail",
        connector_channel="email",
        connector_endpoint_identity="gmail:user:test@example.com",
        connector_max_inflight=4,
        gmail_client_id="test-client-id",
        gmail_client_secret="test-client-secret",
        gmail_refresh_token="test-refresh-token",
        gmail_watch_renew_interval_s=3600,
        gmail_poll_interval_s=5,
    )


@pytest.fixture
def mock_cursor_pool() -> MagicMock:
    """Create a mock DB cursor pool."""
    return MagicMock()


@pytest.fixture
def gmail_runtime(
    gmail_config: GmailConnectorConfig,
    mock_cursor_pool: MagicMock,
) -> GmailConnectorRuntime:
    """Create Gmail connector runtime instance."""
    return GmailConnectorRuntime(gmail_config, cursor_pool=mock_cursor_pool)


class TestGmailConnectorConfig:
    """Tests for GmailConnectorConfig."""

    def test_from_env_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test successful config loading from environment."""
        monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:40100/sse")
        monkeypatch.setenv("CONNECTOR_PROVIDER", "gmail")
        monkeypatch.setenv("CONNECTOR_CHANNEL", "email")
        monkeypatch.setenv("CONNECTOR_ENDPOINT_IDENTITY", "gmail:user:test@example.com")

        monkeypatch.setenv("CONNECTOR_MAX_INFLIGHT", "8")

        config = GmailConnectorConfig.from_env(
            gmail_client_id="client-id",
            gmail_client_secret="client-secret",
            gmail_refresh_token="refresh-token",
        )

        assert config.switchboard_mcp_url == "http://localhost:40100/sse"
        assert config.connector_provider == "gmail"
        assert config.connector_channel == "email"
        assert config.connector_endpoint_identity == "gmail:user:test@example.com"
        assert config.connector_max_inflight == 8
        assert config.gmail_client_id == "client-id"
        assert config.gmail_client_secret == "client-secret"
        assert config.gmail_refresh_token == "refresh-token"

    def test_from_env_missing_required(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test config loading fails with missing required env vars."""
        # Clear all required env vars
        monkeypatch.delenv("SWITCHBOARD_MCP_URL", raising=False)
        monkeypatch.delenv("CONNECTOR_ENDPOINT_IDENTITY", raising=False)
        # Should raise for missing SWITCHBOARD_MCP_URL or CONNECTOR_ENDPOINT_IDENTITY
        with pytest.raises((KeyError, ValueError)):
            GmailConnectorConfig.from_env(
                gmail_client_id="client-id",
                gmail_client_secret="client-secret",
                gmail_refresh_token="refresh-token",
            )

    def test_from_env_invalid_integer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test config loading fails with invalid integer values."""
        monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:40100/sse")
        monkeypatch.setenv("CONNECTOR_ENDPOINT_IDENTITY", "gmail:user:test@example.com")

        monkeypatch.setenv("CONNECTOR_MAX_INFLIGHT", "invalid")

        with pytest.raises(ValueError, match="CONNECTOR_MAX_INFLIGHT must be an integer"):
            GmailConnectorConfig.from_env(
                gmail_client_id="client-id",
                gmail_client_secret="client-secret",
                gmail_refresh_token="refresh-token",
            )

    def test_from_env_requires_explicit_credentials(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Config loading fails when DB-injected credentials are empty."""
        monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:40100/sse")
        monkeypatch.setenv("CONNECTOR_ENDPOINT_IDENTITY", "gmail:user:test@example.com")

        with pytest.raises(ValueError, match="DB-resolved Gmail credentials missing"):
            GmailConnectorConfig.from_env(
                gmail_client_id="",
                gmail_client_secret="client-secret",
                gmail_refresh_token="refresh-token",
            )


class TestRunGmailConnectorStartup:
    """Tests for run_gmail_connector() multi-account startup flow."""

    async def test_startup_creates_manager_and_starts(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """run_gmail_connector should create GmailConnectorManager and call start()."""
        monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:40100/sse")

        manager = MagicMock()
        manager.start = AsyncMock()

        mock_shared_pool = AsyncMock()
        mock_shared_pool.close = AsyncMock()
        mock_cursor_pool = AsyncMock()
        mock_cursor_pool.close = AsyncMock()

        with (
            patch("butlers.connectors.gmail.configure_logging"),
            patch(
                "butlers.connectors.gmail._create_shared_db_pool",
                new=AsyncMock(return_value=mock_shared_pool),
            ),
            patch(
                "butlers.connectors.cursor_store.create_cursor_pool_from_env",
                new=AsyncMock(return_value=mock_cursor_pool),
            ),
            patch("butlers.connectors.gmail.GmailConnectorManager", return_value=manager),
        ):
            await run_gmail_connector()

        manager.start.assert_awaited_once()

    async def test_startup_raises_on_missing_switchboard_url(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """run_gmail_connector should raise on missing SWITCHBOARD_MCP_URL."""
        monkeypatch.delenv("SWITCHBOARD_MCP_URL", raising=False)

        with (
            patch("butlers.connectors.gmail.configure_logging"),
        ):
            with pytest.raises((KeyError, ValueError)):
                await run_gmail_connector()


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

    async def test_ensure_cursor_creates_initial(
        self, gmail_runtime: GmailConnectorRuntime
    ) -> None:
        """Test cursor is initialized in DB with initial historyId if missing."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"historyId": "999"}
        mock_response.raise_for_status = MagicMock()

        saved_values: list[str] = []

        async def fake_save(_pool: object, _prov: str, _eid: str, val: str) -> None:
            saved_values.append(val)

        with (
            patch.object(gmail_runtime, "_http_client", new=AsyncMock()) as mock_client,
            patch.object(gmail_runtime, "_get_access_token", new=AsyncMock(return_value="token")),
            patch(
                "butlers.connectors.cursor_store.load_cursor",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "butlers.connectors.cursor_store.save_cursor",
                new=AsyncMock(side_effect=fake_save),
            ),
        ):
            mock_client.get = AsyncMock(return_value=mock_response)

            await gmail_runtime._ensure_cursor()

            assert len(saved_values) == 1
            cursor_data = json.loads(saved_values[0])
            assert cursor_data["history_id"] == "999"

    async def test_load_cursor_success(self, gmail_runtime: GmailConnectorRuntime) -> None:
        """Test loading cursor from DB."""
        cursor = GmailCursor(
            history_id="12345",
            last_updated_at="2026-02-15T10:00:00Z",
        )

        with patch(
            "butlers.connectors.cursor_store.load_cursor",
            new=AsyncMock(return_value=cursor.model_dump_json()),
        ):
            loaded = await gmail_runtime._load_cursor()

        assert loaded.history_id == "12345"
        assert loaded.last_updated_at == "2026-02-15T10:00:00Z"

    async def test_load_cursor_missing(self, gmail_runtime: GmailConnectorRuntime) -> None:
        """Test loading cursor fails when not in DB."""
        with patch(
            "butlers.connectors.cursor_store.load_cursor",
            new=AsyncMock(return_value=None),
        ):
            with pytest.raises(RuntimeError, match="Cursor not found in DB"):
                await gmail_runtime._load_cursor()

    async def test_save_cursor(self, gmail_runtime: GmailConnectorRuntime) -> None:
        """Test saving cursor to DB."""
        cursor = GmailCursor(
            history_id="67890",
            last_updated_at="2026-02-15T11:00:00Z",
        )

        with patch(
            "butlers.connectors.cursor_store.save_cursor",
            new=AsyncMock(),
        ) as mock_save:
            await gmail_runtime._save_cursor(cursor)

        mock_save.assert_awaited_once()
        saved = GmailCursor.model_validate_json(mock_save.call_args[0][3])
        assert saved.history_id == "67890"

    async def test_get_access_token_refresh(self, gmail_runtime: GmailConnectorRuntime) -> None:
        """Test OAuth token refresh when expired."""
        mock_response = MagicMock()
        mock_response.is_error = False
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
        mock_response.is_error = False
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
        self,
        gmail_runtime: GmailConnectorRuntime,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Test history fetch handles 404 (history too old) by resetting cursor."""
        mock_404_response = MagicMock()
        mock_404_response.status_code = 404
        mock_404_response.json.return_value = {
            "error": {
                "code": 404,
                "message": "Requested entity was not found.",
                "status": "NOT_FOUND",
                "errors": [{"reason": "notFound"}],
            }
        }

        mock_profile_response = MagicMock()
        mock_profile_response.json.return_value = {"historyId": "200"}
        mock_profile_response.raise_for_status = MagicMock()

        saved_values: list[str] = []

        async def fake_save(_pool: object, _prov: str, _eid: str, val: str) -> None:
            saved_values.append(val)

        with (
            patch.object(gmail_runtime, "_http_client", new=AsyncMock()) as mock_client,
            patch.object(gmail_runtime, "_get_access_token", new=AsyncMock(return_value="token")),
            patch(
                "butlers.connectors.cursor_store.save_cursor",
                new=AsyncMock(side_effect=fake_save),
            ),
        ):
            mock_client.get = AsyncMock(side_effect=[mock_404_response, mock_profile_response])
            with caplog.at_level(logging.WARNING, logger="butlers.connectors.gmail"):
                history = await gmail_runtime._fetch_history_changes("1")

            assert history == []
            # Verify cursor was saved to DB
            assert len(saved_values) == 1
            cursor_data = json.loads(saved_values[0])
            assert cursor_data["history_id"] == "200"
            assert "Gmail history.list 404 details" in caplog.text
            assert "reason=notFound" in caplog.text

    async def test_fetch_history_changes_error_logs_google_details(
        self, gmail_runtime: GmailConnectorRuntime, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Non-404 history errors should log structured Google details."""
        mock_error_response = MagicMock()
        mock_error_response.status_code = 401
        mock_error_response.is_error = True
        mock_error_response.json.return_value = {
            "error": {
                "code": 401,
                "message": "Request had invalid authentication credentials.",
                "status": "UNAUTHENTICATED",
                "errors": [{"reason": "authError"}],
            }
        }
        mock_error_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "401 Unauthorized",
            request=httpx.Request("GET", "https://gmail.googleapis.com/gmail/v1/users/me/history"),
            response=mock_error_response,
        )

        with (
            patch.object(gmail_runtime, "_http_client", new=AsyncMock()) as mock_client,
            patch.object(gmail_runtime, "_get_access_token", new=AsyncMock(return_value="token")),
        ):
            mock_client.get = AsyncMock(return_value=mock_error_response)

            with caplog.at_level(logging.ERROR, logger="butlers.connectors.gmail"):
                with pytest.raises(httpx.HTTPStatusError):
                    await gmail_runtime._fetch_history_changes("123")

            assert "Gmail history.list failed status=401" in caplog.text
            assert "status=UNAUTHENTICATED" in caplog.text
            assert "reason=authError" in caplog.text

    async def test_get_access_token_error_logs_google_details(
        self, gmail_runtime: GmailConnectorRuntime, caplog: pytest.LogCaptureFixture
    ) -> None:
        """OAuth refresh failures should log Google OAuth error details."""
        mock_error_response = MagicMock()
        mock_error_response.status_code = 400
        mock_error_response.is_error = True
        mock_error_response.json.return_value = {
            "error": "invalid_grant",
            "error_description": "Token has been expired or revoked.",
        }
        mock_error_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "400 Bad Request",
            request=httpx.Request("POST", "https://oauth2.googleapis.com/token"),
            response=mock_error_response,
        )

        with patch.object(gmail_runtime, "_http_client", new=AsyncMock()) as mock_client:
            mock_client.post = AsyncMock(return_value=mock_error_response)

            with caplog.at_level(logging.ERROR, logger="butlers.connectors.gmail"):
                with pytest.raises(httpx.HTTPStatusError):
                    await gmail_runtime._get_access_token()

            assert "OAuth token refresh failed status=400" in caplog.text
            assert "error=invalid_grant" in caplog.text

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

    async def test_build_ingest_envelope(self, gmail_runtime: GmailConnectorRuntime) -> None:
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

        envelope = await gmail_runtime._build_ingest_envelope(message_data)

        assert envelope["schema_version"] == "ingest.v1"
        assert envelope["source"]["channel"] == "email"
        assert envelope["source"]["provider"] == "gmail"
        assert envelope["event"]["external_event_id"] == "<unique-msg-id@example.com>"
        assert envelope["event"]["external_thread_id"] == "thread456"
        assert envelope["sender"]["identity"] == "sender@example.com"
        assert "Test Email" in envelope["payload"]["normalized_text"]
        assert "Test body content" in envelope["payload"]["normalized_text"]

    async def test_submit_to_ingest_api_success(self, gmail_runtime: GmailConnectorRuntime) -> None:
        """Test submitting envelope to Switchboard via MCP ingest tool."""
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

        mock_result = {"request_id": "req-123", "duplicate": False, "status": "accepted"}

        with patch.object(
            gmail_runtime._mcp_client,
            "call_tool",
            new_callable=AsyncMock,
            return_value=mock_result,
        ) as mock_call:
            await gmail_runtime._submit_to_ingest_api(envelope)

            mock_call.assert_called_once_with("ingest", envelope)

    async def test_submit_to_ingest_api_mcp_error(
        self, gmail_runtime: GmailConnectorRuntime
    ) -> None:
        """Test handling of MCP errors during ingest submission."""
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

        with patch.object(
            gmail_runtime._mcp_client,
            "call_tool",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Ingest tool error: Validation failed"),
        ):
            with pytest.raises(RuntimeError, match="Ingest tool error"):
                await gmail_runtime._submit_to_ingest_api(envelope)

    async def test_submit_to_ingest_api_connection_error(
        self, gmail_runtime: GmailConnectorRuntime
    ) -> None:
        """Test handling of connection errors to MCP server."""
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

        with patch.object(
            gmail_runtime._mcp_client,
            "call_tool",
            new_callable=AsyncMock,
            side_effect=ConnectionError("Cannot reach switchboard"),
        ):
            with pytest.raises(ConnectionError):
                await gmail_runtime._submit_to_ingest_api(envelope)

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

    def test_extract_body_from_payload_html_only(
        self, gmail_runtime: GmailConnectorRuntime
    ) -> None:
        """HTML-only emails (no text/plain) are extracted and tags stripped. [bu-nwn]"""
        import base64

        html_content = b"<p>Hello from HTML!</p>"
        payload = {
            "mimeType": "multipart/alternative",
            "parts": [
                {
                    "mimeType": "text/html",
                    "body": {"data": base64.urlsafe_b64encode(html_content).decode()},
                },
            ],
        }

        body = gmail_runtime._extract_body_from_payload(payload)

        assert body == "Hello from HTML!"

    def test_extract_body_from_payload_html_fallback_strips_tags(
        self, gmail_runtime: GmailConnectorRuntime
    ) -> None:
        """HTML tags, style/script blocks are stripped from HTML-only emails. [bu-nwn]"""
        import base64

        html_content = (
            b"<html><head>"
            b"<style>body { color: red; }</style>"
            b"</head><body>"
            b"<script>alert('xss')</script>"
            b"<h1>Account Statement</h1>"
            b"<p>Your balance is <strong>1,234</strong> miles.</p>"
            b"</body></html>"
        )
        payload = {
            "mimeType": "text/html",
            "body": {"data": base64.urlsafe_b64encode(html_content).decode()},
        }

        body = gmail_runtime._extract_body_from_payload(payload)

        assert "Account Statement" in body
        assert "1,234" in body
        assert "miles" in body
        # Style/script content must not appear
        assert "color: red" not in body
        assert "alert" not in body
        # No raw HTML tags
        assert "<" not in body
        assert ">" not in body

    def test_extract_body_from_payload_prefers_plain_over_html(
        self, gmail_runtime: GmailConnectorRuntime
    ) -> None:
        """text/plain is still preferred even when html comes first in parts. [bu-nwn]"""
        import base64

        payload = {
            "mimeType": "multipart/alternative",
            "parts": [
                {
                    "mimeType": "text/html",
                    "body": {"data": base64.urlsafe_b64encode(b"<p>HTML version</p>").decode()},
                },
                {
                    "mimeType": "text/plain",
                    "body": {"data": base64.urlsafe_b64encode(b"Plain version").decode()},
                },
            ],
        }

        body = gmail_runtime._extract_body_from_payload(payload)

        assert body == "Plain version"

    def test_extract_body_charset_iso8859(self, gmail_runtime: GmailConnectorRuntime) -> None:
        """ISO-8859-1 encoded text/plain body is decoded correctly. [bu-791]"""
        import base64

        # "café" encoded in ISO-8859-1: 'é' is 0xe9
        iso_bytes = "café résumé".encode("iso-8859-1")
        payload = {
            "mimeType": "text/plain",
            "headers": [
                {"name": "Content-Type", "value": "text/plain; charset=iso-8859-1"},
            ],
            "body": {"data": base64.urlsafe_b64encode(iso_bytes).decode()},
        }

        body = gmail_runtime._extract_body_from_payload(payload)

        assert body == "café résumé"
        # Ensure no replacement characters leaked in
        assert "\ufffd" not in body

    def test_extract_body_charset_fallback(self, gmail_runtime: GmailConnectorRuntime) -> None:
        """Unknown charset gracefully falls back to UTF-8 with replacement chars. [bu-791]"""
        import base64

        utf8_bytes = b"hello"
        payload = {
            "mimeType": "text/plain",
            "headers": [
                {"name": "Content-Type", "value": "text/plain; charset=x-not-a-real-charset"},
            ],
            "body": {"data": base64.urlsafe_b64encode(utf8_bytes).decode()},
        }

        body = gmail_runtime._extract_body_from_payload(payload)

        # UTF-8 bytes of "hello" are valid UTF-8 so no replacement needed here
        assert body == "hello"

    # ------------------------------------------------------------------
    # S/MIME body extraction tests [bu-3np]
    # ------------------------------------------------------------------

    def test_extract_body_multipart_signed_detached_smime(
        self, gmail_runtime: GmailConnectorRuntime
    ) -> None:
        """multipart/signed with detached S/MIME: body extracted, signature skipped. [bu-3np]"""
        import base64

        payload = {
            "mimeType": "multipart/signed",
            "parts": [
                {
                    "mimeType": "multipart/alternative",
                    "parts": [
                        {
                            "mimeType": "text/plain",
                            "body": {
                                "data": base64.urlsafe_b64encode(b"Signed plain text body").decode()
                            },
                        },
                        {
                            "mimeType": "text/html",
                            "body": {
                                "data": base64.urlsafe_b64encode(
                                    b"<p>Signed HTML body</p>"
                                ).decode()
                            },
                        },
                    ],
                },
                {
                    # Detached S/MIME signature — must be skipped
                    "mimeType": "application/pkcs7-signature",
                    "body": {"data": base64.urlsafe_b64encode(b"\xde\xad\xbe\xef").decode()},
                },
            ],
        }

        body = gmail_runtime._extract_body_from_payload(payload)

        assert body == "Signed plain text body"

    def test_extract_body_multipart_signed_signature_comes_first(
        self, gmail_runtime: GmailConnectorRuntime
    ) -> None:
        """multipart/signed with signature first: body still extracted. [bu-3np]"""
        import base64

        payload = {
            "mimeType": "multipart/signed",
            "parts": [
                {
                    # Signature listed first — must be skipped
                    "mimeType": "application/pkcs7-signature",
                    "body": {"data": base64.urlsafe_b64encode(b"\xde\xad\xbe\xef").decode()},
                },
                {
                    "mimeType": "text/plain",
                    "body": {
                        "data": base64.urlsafe_b64encode(b"Body after signature part").decode()
                    },
                },
            ],
        }

        body = gmail_runtime._extract_body_from_payload(payload)

        assert body == "Body after signature part"

    def test_extract_body_multipart_signed_pgp_signature_skipped(
        self, gmail_runtime: GmailConnectorRuntime
    ) -> None:
        """multipart/signed with PGP detached signature: signature part is skipped. [bu-3np]"""
        import base64

        payload = {
            "mimeType": "multipart/signed",
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {"data": base64.urlsafe_b64encode(b"PGP signed message body").decode()},
                },
                {
                    # PGP detached signature — must be skipped
                    "mimeType": "application/pgp-signature",
                    "body": {
                        "data": base64.urlsafe_b64encode(b"-----BEGIN PGP SIGNATURE-----").decode()
                    },
                },
            ],
        }

        body = gmail_runtime._extract_body_from_payload(payload)

        assert body == "PGP signed message body"

    def test_extract_body_pkcs7_mime_opaque_smime(
        self, gmail_runtime: GmailConnectorRuntime
    ) -> None:
        """application/pkcs7-mime (opaque S/MIME): descriptive fallback returned. [bu-3np]"""
        import base64

        payload = {
            "mimeType": "application/pkcs7-mime",
            "body": {"data": base64.urlsafe_b64encode(b"\x30\x82\xde\xad").decode()},
        }

        body = gmail_runtime._extract_body_from_payload(payload)

        assert body == "(S/MIME encrypted body — cannot extract)"

    def test_extract_body_pkcs7_mime_opaque_smime_warning_logged(
        self, gmail_runtime: GmailConnectorRuntime, caplog: pytest.LogCaptureFixture
    ) -> None:
        """application/pkcs7-mime: warning is emitted to the logger. [bu-3np]"""
        import base64
        import logging

        payload = {
            "mimeType": "application/pkcs7-mime",
            "body": {"data": base64.urlsafe_b64encode(b"\x30\x82\xde\xad").decode()},
        }

        with caplog.at_level(logging.WARNING, logger="butlers.connectors.gmail"):
            body = gmail_runtime._extract_body_from_payload(payload)

        assert body == "(S/MIME encrypted body — cannot extract)"
        assert any(
            "pkcs7" in r.message.lower() or "s/mime" in r.message.lower() for r in caplog.records
        )

    def test_extract_body_multipart_signed_only_signatures_no_body(
        self, gmail_runtime: GmailConnectorRuntime
    ) -> None:
        """multipart/signed with only signature parts (malformed): returns '(no body)'. [bu-3np]"""
        import base64

        payload = {
            "mimeType": "multipart/signed",
            "parts": [
                {
                    "mimeType": "application/pkcs7-signature",
                    "body": {"data": base64.urlsafe_b64encode(b"\xde\xad").decode()},
                },
                {
                    "mimeType": "application/pgp-signature",
                    "body": {"data": base64.urlsafe_b64encode(b"\xbe\xef").decode()},
                },
            ],
        }

        body = gmail_runtime._extract_body_from_payload(payload)

        assert body == "(no body)"

    # ------------------------------------------------------------------
    # Mid-session Switchboard ConnectionError tests (butlers-tt60)
    # ------------------------------------------------------------------

    async def test_ingest_single_message_reraises_connection_error(
        self, gmail_runtime: GmailConnectorRuntime
    ) -> None:
        """ConnectionError from Switchboard is re-raised (not swallowed) so cursor stays put."""
        message_data = {
            "id": "msg_conn_err",
            "threadId": "thread1",
            "internalDate": "1708000000000",
            "labelIds": ["INBOX"],
            "payload": {
                "headers": [
                    {"name": "From", "value": "sender@example.com"},
                    {"name": "Subject", "value": "Test"},
                    {"name": "Message-ID", "value": "<msg1@example.com>"},
                ],
                "mimeType": "text/plain",
                "body": {"data": "dGVzdA=="},
            },
        }
        with (
            patch.object(
                gmail_runtime,
                "_fetch_message",
                new=AsyncMock(return_value=message_data),
            ),
            patch.object(
                gmail_runtime,
                "_submit_to_ingest_api",
                new=AsyncMock(side_effect=ConnectionError("Switchboard is down")),
            ),
        ):
            with pytest.raises(ConnectionError, match="Switchboard is down"):
                await gmail_runtime._ingest_single_message("msg_conn_err")

    async def test_ingest_single_message_swallows_non_transient_error(
        self, gmail_runtime: GmailConnectorRuntime
    ) -> None:
        """Non-transient errors (e.g. malformed message) are logged and swallowed."""
        with patch.object(
            gmail_runtime,
            "_fetch_message",
            new=AsyncMock(side_effect=RuntimeError("Malformed message")),
        ):
            # Should not raise — non-transient errors are swallowed
            await gmail_runtime._ingest_single_message("msg_bad")

    async def test_ingest_messages_propagates_connection_error(
        self, gmail_runtime: GmailConnectorRuntime
    ) -> None:
        """_ingest_messages re-raises ConnectionError when any delivery fails transiently."""
        call_count = 0

        async def fake_ingest(msg_id: str) -> None:
            nonlocal call_count
            call_count += 1
            if msg_id == "msg2":
                raise ConnectionError("Switchboard down mid-batch")

        with patch.object(gmail_runtime, "_ingest_single_message", side_effect=fake_ingest):
            with pytest.raises(ConnectionError, match="Switchboard down mid-batch"):
                await gmail_runtime._ingest_messages(["msg1", "msg2", "msg3"])

        # All three tasks must have been attempted (gather runs all concurrently)
        assert call_count == 3

    async def test_ingest_messages_no_error_when_all_succeed(
        self, gmail_runtime: GmailConnectorRuntime
    ) -> None:
        """_ingest_messages completes without raising when all messages ingest cleanly."""
        with patch.object(
            gmail_runtime, "_ingest_single_message", new=AsyncMock(return_value=None)
        ):
            await gmail_runtime._ingest_messages(["msg1", "msg2"])  # Should not raise

    async def test_polling_loop_does_not_advance_cursor_on_connection_error(
        self, gmail_runtime: GmailConnectorRuntime
    ) -> None:
        """Polling loop cursor is NOT advanced when Switchboard is down mid-session."""
        initial_cursor = GmailCursor(history_id="100", last_updated_at="2026-01-01T00:00:00Z")
        gmail_runtime._running = True

        call_count = 0
        saved_history_ids: list[str] = []

        async def fake_ingest_messages(message_ids: list[str]) -> None:
            nonlocal call_count
            call_count += 1
            gmail_runtime._running = False  # Stop the loop after first iteration
            raise ConnectionError("Switchboard down mid-session")

        async def fake_save(_pool: object, _prov: str, _eid: str, val: str) -> None:
            saved_history_ids.append(json.loads(val).get("history_id", ""))

        mock_history = [{"id": "200", "messagesAdded": [{"message": {"id": "msgX"}}]}]

        with (
            patch.object(
                gmail_runtime,
                "_fetch_history_changes",
                new=AsyncMock(return_value=mock_history),
            ),
            patch.object(
                gmail_runtime,
                "_ingest_messages",
                side_effect=fake_ingest_messages,
            ),
            patch("butlers.connectors.gmail.asyncio.sleep", new=AsyncMock()),
            patch(
                "butlers.connectors.cursor_store.load_cursor",
                new=AsyncMock(return_value=initial_cursor.model_dump_json()),
            ),
            patch(
                "butlers.connectors.cursor_store.save_cursor",
                new=AsyncMock(side_effect=fake_save),
            ),
        ):
            await gmail_runtime._run_polling_ingestion_loop()

        # Cursor must NOT have been advanced to "200"
        for hid in saved_history_ids:
            assert hid != "200", (
                f"Cursor was incorrectly advanced to {hid!r}; "
                "messages may have been permanently lost"
            )
        assert call_count == 1  # Exactly one ingestion attempt was made

    async def test_pubsub_loop_does_not_advance_cursor_on_connection_error(
        self, gmail_runtime: GmailConnectorRuntime
    ) -> None:
        """Pub/Sub loop cursor is NOT advanced when Switchboard is down mid-session."""
        initial_cursor = GmailCursor(history_id="200", last_updated_at="2026-01-01T00:00:00Z")
        gmail_runtime._running = True
        saved_history_ids: list[str] = []

        async def fake_ingest_messages(message_ids: list[str]) -> None:
            gmail_runtime._running = False  # Stop the loop after first batch
            raise ConnectionError("Switchboard down in pubsub path")

        async def fake_save(_pool: object, _prov: str, _eid: str, val: str) -> None:
            saved_history_ids.append(json.loads(val).get("history_id", ""))

        mock_history = [{"id": "300", "messagesAdded": [{"message": {"id": "msgY"}}]}]

        # Use a real asyncio.Queue with one item so the loop gets a notification
        notification_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        await notification_queue.put({"historyId": "300"})
        gmail_runtime._notification_queue = notification_queue

        with (
            patch.object(gmail_runtime, "_gmail_watch_renew_if_needed", new=AsyncMock()),
            patch.object(
                gmail_runtime,
                "_fetch_history_changes",
                new=AsyncMock(return_value=mock_history),
            ),
            patch.object(
                gmail_runtime,
                "_ingest_messages",
                side_effect=fake_ingest_messages,
            ),
            patch("butlers.connectors.gmail.asyncio.sleep", new=AsyncMock()),
            patch(
                "butlers.connectors.cursor_store.load_cursor",
                new=AsyncMock(return_value=initial_cursor.model_dump_json()),
            ),
            patch(
                "butlers.connectors.cursor_store.save_cursor",
                new=AsyncMock(side_effect=fake_save),
            ),
        ):
            await gmail_runtime._run_pubsub_ingestion_loop()

        # Cursor must NOT have been advanced to "300"
        for hid in saved_history_ids:
            assert hid != "300", (
                f"Cursor was incorrectly advanced to {hid!r}; "
                "messages may have been permanently lost"
            )


class TestGmailPubSubConfig:
    """Tests for Gmail Pub/Sub configuration."""

    def test_pubsub_config_enabled_with_topic(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test Pub/Sub config when enabled with topic."""
        monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:40100/sse")
        monkeypatch.setenv("CONNECTOR_ENDPOINT_IDENTITY", "gmail:user:test@example.com")

        monkeypatch.setenv("GMAIL_PUBSUB_ENABLED", "true")
        monkeypatch.setenv("GMAIL_PUBSUB_TOPIC", "projects/my-project/topics/gmail-push")

        config = GmailConnectorConfig.from_env(
            gmail_client_id="client-id",
            gmail_client_secret="client-secret",
            gmail_refresh_token="refresh-token",
        )

        assert config.gmail_pubsub_enabled is True
        assert config.gmail_pubsub_topic == "projects/my-project/topics/gmail-push"
        assert config.gmail_pubsub_webhook_port == 40083
        assert config.gmail_pubsub_webhook_path == "/gmail/webhook"

    def test_pubsub_config_enabled_without_topic_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test Pub/Sub config fails when enabled without topic."""
        monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:40100/sse")
        monkeypatch.setenv("CONNECTOR_ENDPOINT_IDENTITY", "gmail:user:test@example.com")

        monkeypatch.setenv("GMAIL_PUBSUB_ENABLED", "true")

        with pytest.raises(ValueError, match="GMAIL_PUBSUB_TOPIC is required"):
            GmailConnectorConfig.from_env(
                gmail_client_id="client-id",
                gmail_client_secret="client-secret",
                gmail_refresh_token="refresh-token",
            )

    def test_pubsub_config_custom_webhook_settings(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test Pub/Sub config with custom webhook settings."""
        monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:40100/sse")
        monkeypatch.setenv("CONNECTOR_ENDPOINT_IDENTITY", "gmail:user:test@example.com")

        monkeypatch.setenv("GMAIL_PUBSUB_ENABLED", "true")
        monkeypatch.setenv("GMAIL_PUBSUB_TOPIC", "projects/my-project/topics/gmail-push")
        monkeypatch.setenv("GMAIL_PUBSUB_WEBHOOK_PORT", "9000")
        monkeypatch.setenv("GMAIL_PUBSUB_WEBHOOK_PATH", "/custom/path")

        config = GmailConnectorConfig.from_env(
            gmail_client_id="client-id",
            gmail_client_secret="client-secret",
            gmail_refresh_token="refresh-token",
        )

        assert config.gmail_pubsub_webhook_port == 9000
        assert config.gmail_pubsub_webhook_path == "/custom/path"

    def test_pubsub_disabled_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test Pub/Sub is disabled by default."""
        monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:40100/sse")
        monkeypatch.setenv("CONNECTOR_ENDPOINT_IDENTITY", "gmail:user:test@example.com")

        config = GmailConnectorConfig.from_env(
            gmail_client_id="client-id",
            gmail_client_secret="client-secret",
            gmail_refresh_token="refresh-token",
        )

        assert config.gmail_pubsub_enabled is False
        assert config.gmail_pubsub_topic is None

    def test_pubsub_webhook_token_configuration(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test webhook token is loaded from environment."""
        monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:40100/sse")
        monkeypatch.setenv("CONNECTOR_ENDPOINT_IDENTITY", "gmail:user:test@example.com")

        monkeypatch.setenv("GMAIL_PUBSUB_ENABLED", "true")
        monkeypatch.setenv("GMAIL_PUBSUB_TOPIC", "projects/test/topics/gmail")
        monkeypatch.setenv("GMAIL_PUBSUB_WEBHOOK_TOKEN", "secret-token-123")

        config = GmailConnectorConfig.from_env(
            gmail_client_id="client-id",
            gmail_client_secret="client-secret",
            gmail_refresh_token="refresh-token",
        )

        assert config.gmail_pubsub_webhook_token == "secret-token-123"


class TestGmailWatchAPI:
    """Tests for Gmail watch API integration."""

    async def test_gmail_watch_start_success(self, gmail_config: GmailConnectorConfig) -> None:
        """Test starting Gmail watch subscription."""
        # Enable Pub/Sub for this test
        pubsub_config = gmail_config.model_copy(
            update={
                "gmail_pubsub_enabled": True,
                "gmail_pubsub_topic": "projects/test/topics/gmail",
            }
        )
        runtime = GmailConnectorRuntime(pubsub_config)

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "historyId": "12345",
            "expiration": "1708617600000",  # 2024-02-22 12:00:00 UTC
        }
        mock_response.raise_for_status = MagicMock()

        with (
            patch.object(runtime, "_http_client", new=AsyncMock()) as mock_client,
            patch.object(runtime, "_get_access_token", new=AsyncMock(return_value="token")),
        ):
            mock_client.post = AsyncMock(return_value=mock_response)

            result = await runtime._gmail_watch_start()

            assert result["historyId"] == "12345"
            assert runtime._watch_expiration is not None
            mock_client.post.assert_called_once()
            call_args = mock_client.post.call_args
            assert "gmail/v1/users/me/watch" in call_args.args[0]
            assert call_args.kwargs["json"]["topicName"] == "projects/test/topics/gmail"

    async def test_gmail_watch_start_without_topic_fails(
        self, gmail_runtime: GmailConnectorRuntime
    ) -> None:
        """Test watch start fails when Pub/Sub topic not configured."""
        with (
            patch.object(gmail_runtime, "_http_client", new=AsyncMock()),
            patch.object(gmail_runtime, "_get_access_token", new=AsyncMock(return_value="token")),
        ):
            with pytest.raises(RuntimeError, match="Pub/Sub topic not configured"):
                await gmail_runtime._gmail_watch_start()

    async def test_gmail_watch_renew_when_expiring(
        self, gmail_config: GmailConnectorConfig
    ) -> None:
        """Test watch renewal when approaching expiration."""
        pubsub_config = gmail_config.model_copy(
            update={
                "gmail_pubsub_enabled": True,
                "gmail_pubsub_topic": "projects/test/topics/gmail",
            }
        )
        runtime = GmailConnectorRuntime(pubsub_config)

        # Set expiration to 30 minutes from now (should trigger renewal)
        runtime._watch_expiration = datetime.now(UTC) + timedelta(minutes=30)

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "historyId": "12345",
            "expiration": str(int((datetime.now(UTC) + timedelta(days=1)).timestamp() * 1000)),
        }
        mock_response.raise_for_status = MagicMock()

        with (
            patch.object(runtime, "_http_client", new=AsyncMock()) as mock_client,
            patch.object(runtime, "_get_access_token", new=AsyncMock(return_value="token")),
        ):
            mock_client.post = AsyncMock(return_value=mock_response)

            await runtime._gmail_watch_renew_if_needed()

            # Should have renewed
            mock_client.post.assert_called_once()

    async def test_gmail_watch_no_renew_when_fresh(
        self, gmail_config: GmailConnectorConfig
    ) -> None:
        """Test watch not renewed when still fresh."""
        pubsub_config = gmail_config.model_copy(
            update={
                "gmail_pubsub_enabled": True,
                "gmail_pubsub_topic": "projects/test/topics/gmail",
            }
        )
        runtime = GmailConnectorRuntime(pubsub_config)

        # Set expiration to 2 hours from now (should not trigger renewal)
        runtime._watch_expiration = datetime.now(UTC) + timedelta(hours=2)

        with (
            patch.object(runtime, "_http_client", new=AsyncMock()) as mock_client,
            patch.object(runtime, "_get_access_token", new=AsyncMock(return_value="token")),
        ):
            mock_client.post = AsyncMock()

            await runtime._gmail_watch_renew_if_needed()

            # Should not have renewed
            mock_client.post.assert_not_called()


class TestGmailPubSubIngestion:
    """Tests for Pub/Sub-based ingestion flow."""

    async def test_pubsub_notification_triggers_history_fetch(
        self,
        gmail_config: GmailConnectorConfig,
        mock_cursor_pool: MagicMock,
    ) -> None:
        """Test that Pub/Sub notification triggers immediate history fetch."""
        pubsub_config = gmail_config.model_copy(
            update={
                "gmail_pubsub_enabled": True,
                "gmail_pubsub_topic": "projects/test/topics/gmail",
                "gmail_poll_interval_s": 1,
            }
        )
        runtime = GmailConnectorRuntime(pubsub_config, cursor_pool=mock_cursor_pool)

        # Initialize notification queue
        runtime._notification_queue = asyncio.Queue()
        runtime._running = True
        runtime._watch_expiration = datetime.now(UTC) + timedelta(hours=2)

        # Set up initial cursor
        initial_cursor = GmailCursor(
            history_id="100",
            last_updated_at=datetime.now(UTC).isoformat(),
        )

        # Mock history response
        mock_history_response = MagicMock()
        mock_history_response.status_code = 200
        mock_history_response.json.return_value = {
            "history": [
                {"id": "101", "messagesAdded": [{"message": {"id": "msg1"}}]},
            ]
        }
        mock_history_response.raise_for_status = MagicMock()

        # Stop the loop after the first _ingest_messages call to avoid blocking
        # on the next queue.get() wait, eliminating real wall-clock delay.
        async def ingest_and_stop(message_ids: list[str]) -> None:
            runtime._running = False

        with (
            patch.object(runtime, "_http_client", new=AsyncMock()) as mock_client,
            patch.object(runtime, "_get_access_token", new=AsyncMock(return_value="token")),
            patch.object(
                runtime, "_ingest_messages", new=AsyncMock(side_effect=ingest_and_stop)
            ) as mock_ingest,
            patch(
                "butlers.connectors.cursor_store.load_cursor",
                new=AsyncMock(return_value=initial_cursor.model_dump_json()),
            ),
            patch(
                "butlers.connectors.cursor_store.save_cursor",
                new=AsyncMock(),
            ),
        ):
            mock_client.get = AsyncMock(return_value=mock_history_response)

            # Queue a notification
            await runtime._notification_queue.put({"message": {"data": "test"}})

            # Run the loop — it stops itself after processing the first notification.
            await runtime._run_pubsub_ingestion_loop()

            # Should have fetched history and ingested messages
            mock_client.get.assert_called()
            mock_ingest.assert_called_once_with(["msg1"])

    async def test_pubsub_fallback_poll_when_no_notifications(
        self,
        gmail_config: GmailConnectorConfig,
        mock_cursor_pool: MagicMock,
    ) -> None:
        """Test fallback polling when no Pub/Sub notifications received."""
        pubsub_config = gmail_config.model_copy(
            update={
                "gmail_pubsub_enabled": True,
                "gmail_pubsub_topic": "projects/test/topics/gmail",
                "gmail_poll_interval_s": 1,
            }
        )
        runtime = GmailConnectorRuntime(pubsub_config, cursor_pool=mock_cursor_pool)

        # Initialize notification queue
        runtime._notification_queue = asyncio.Queue()
        runtime._running = True
        runtime._watch_expiration = datetime.now(UTC) + timedelta(hours=2)

        # Mock history response
        mock_history_response = MagicMock()
        mock_history_response.status_code = 200
        mock_history_response.json.return_value = {"history": []}
        mock_history_response.raise_for_status = MagicMock()

        # Patch asyncio.wait_for in the gmail module so the queue.get() timeout
        # fires immediately (no real wall-clock wait) and triggers fallback logic.
        async def instant_timeout(coro: object, **kwargs: object) -> object:
            coro.close()  # type: ignore[union-attr]
            raise TimeoutError

        # Stop the loop after the first HTTP history fetch to avoid looping forever.
        async def get_and_stop(*args: object, **kwargs: object) -> MagicMock:
            runtime._running = False
            return mock_history_response

        cursor_json = GmailCursor(
            history_id="100",
            last_updated_at=datetime.now(UTC).isoformat(),
        ).model_dump_json()

        with (
            patch.object(runtime, "_http_client", new=AsyncMock()) as mock_client,
            patch.object(runtime, "_get_access_token", new=AsyncMock(return_value="token")),
            patch(
                "time.time",
                side_effect=[0, 301] + [302 + i for i in range(100)],
            ),  # last_poll_time=0, current_time=301 (triggers fallback), then continuous time
            patch("butlers.connectors.gmail.asyncio.wait_for", side_effect=instant_timeout),
            patch(
                "butlers.connectors.cursor_store.load_cursor",
                new=AsyncMock(return_value=cursor_json),
            ),
            patch(
                "butlers.connectors.cursor_store.save_cursor",
                new=AsyncMock(),
            ),
        ):
            mock_client.get = AsyncMock(side_effect=get_and_stop)

            # Run the loop -- instant_timeout short-circuits queue.get(), triggering fallback.
            # get_and_stop sets _running=False after the first history fetch.
            await runtime._run_pubsub_ingestion_loop()

            # Should have done at least one history fetch (fallback poll)
            assert mock_client.get.called


class TestWebhookAuthentication:
    """Tests for webhook authentication."""

    async def test_webhook_accepts_no_auth_when_token_not_configured(
        self, gmail_config: GmailConnectorConfig
    ) -> None:
        """Test webhook accepts all requests when auth token is not configured."""
        pubsub_config = gmail_config.model_copy(
            update={
                "gmail_pubsub_enabled": True,
                "gmail_pubsub_topic": "projects/test/topics/gmail",
                "gmail_pubsub_webhook_token": None,
            }
        )
        runtime = GmailConnectorRuntime(pubsub_config)
        runtime._notification_queue = asyncio.Queue()

        # When no token configured, auth should be disabled
        assert runtime._config.gmail_pubsub_webhook_token is None


class TestGmailAttachmentExtraction:
    """Tests for Gmail attachment extraction and storage."""

    @pytest.fixture
    def mock_blob_store(self) -> AsyncMock:
        """Create mock blob store."""
        store = AsyncMock()
        store.put = AsyncMock(return_value="local://2026/02/16/test.jpg")
        return store

    @pytest.fixture
    def gmail_runtime_with_blob_store(
        self, gmail_config: GmailConnectorConfig, mock_blob_store: AsyncMock
    ) -> GmailConnectorRuntime:
        """Create Gmail runtime with blob store."""
        return GmailConnectorRuntime(gmail_config, blob_store=mock_blob_store)

    def test_extract_attachments_with_image(self, gmail_runtime: GmailConnectorRuntime) -> None:
        """Test extracting image attachment from payload."""
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {"data": "dGVzdA=="},
                },
                {
                    "mimeType": "image/jpeg",
                    "filename": "photo.jpg",
                    "body": {
                        "attachmentId": "att123",
                        "size": 1024,
                    },
                },
            ],
        }

        attachments = gmail_runtime._extract_attachments(payload)

        assert len(attachments) == 1
        assert attachments[0]["filename"] == "photo.jpg"
        assert attachments[0]["mime_type"] == "image/jpeg"
        assert attachments[0]["attachment_id"] == "att123"
        assert attachments[0]["size_bytes"] == 1024

    def test_extract_attachments_with_pdf(self, gmail_runtime: GmailConnectorRuntime) -> None:
        """Test extracting PDF attachment from payload."""
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "application/pdf",
                    "filename": "document.pdf",
                    "body": {
                        "attachmentId": "att456",
                        "size": 2048,
                    },
                },
            ],
        }

        attachments = gmail_runtime._extract_attachments(payload)

        assert len(attachments) == 1
        assert attachments[0]["mime_type"] == "application/pdf"
        assert attachments[0]["filename"] == "document.pdf"

    def test_extract_attachments_skips_unsupported_types(
        self, gmail_runtime: GmailConnectorRuntime
    ) -> None:
        """Test that unsupported MIME types are skipped."""
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "application/zip",
                    "filename": "archive.zip",
                    "body": {
                        "attachmentId": "att789",
                        "size": 1024,
                    },
                },
                {
                    "mimeType": "image/jpeg",
                    "filename": "photo.jpg",
                    "body": {
                        "attachmentId": "att123",
                        "size": 1024,
                    },
                },
            ],
        }

        attachments = gmail_runtime._extract_attachments(payload)

        # Only JPEG should be extracted
        assert len(attachments) == 1
        assert attachments[0]["mime_type"] == "image/jpeg"

    def test_extract_attachments_empty_payload(self, gmail_runtime: GmailConnectorRuntime) -> None:
        """Test extracting attachments from payload without attachments."""
        payload = {
            "mimeType": "text/plain",
            "body": {"data": "dGVzdA=="},
        }

        attachments = gmail_runtime._extract_attachments(payload)

        assert len(attachments) == 0

    def test_extract_attachments_nested_parts(self, gmail_runtime: GmailConnectorRuntime) -> None:
        """Test extracting attachments from deeply nested multipart structure."""
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "multipart/alternative",
                    "parts": [
                        {
                            "mimeType": "text/plain",
                            "body": {"data": "dGVzdA=="},
                        },
                        {
                            "mimeType": "text/html",
                            "body": {"data": "PGI+dGVzdDwvYj4="},
                        },
                    ],
                },
                {
                    "mimeType": "image/png",
                    "filename": "screenshot.png",
                    "body": {
                        "attachmentId": "att999",
                        "size": 3072,
                    },
                },
            ],
        }

        attachments = gmail_runtime._extract_attachments(payload)

        assert len(attachments) == 1
        assert attachments[0]["mime_type"] == "image/png"

    def test_extract_attachments_inline_image(self, gmail_runtime: GmailConnectorRuntime) -> None:
        """Test that inline images (Content-Disposition: inline) are included."""
        payload = {
            "mimeType": "multipart/related",
            "parts": [
                {
                    "mimeType": "text/html",
                    "body": {"data": "PGltZyBzcmM9ImNpZDppbWcxIj4="},
                },
                {
                    "mimeType": "image/png",
                    "filename": "inline-image.png",
                    "headers": [
                        {
                            "name": "Content-Disposition",
                            "value": "inline; filename=inline-image.png",
                        },
                        {"name": "Content-ID", "value": "<img1>"},
                    ],
                    "body": {
                        "attachmentId": "att_inline",
                        "size": 2048,
                    },
                },
            ],
        }

        attachments = gmail_runtime._extract_attachments(payload)

        # Inline images should still be extracted
        assert len(attachments) == 1
        assert attachments[0]["mime_type"] == "image/png"

    @pytest.mark.asyncio
    async def test_download_gmail_attachment_success(
        self, gmail_runtime_with_blob_store: GmailConnectorRuntime
    ) -> None:
        """Test successful attachment download from Gmail API."""
        runtime = gmail_runtime_with_blob_store
        runtime._http_client = AsyncMock()
        runtime._get_access_token = AsyncMock(return_value="test-token")

        # Mock successful API response
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": base64.urlsafe_b64encode(b"test attachment data").decode()
        }
        mock_response.raise_for_status = MagicMock()
        runtime._http_client.get = AsyncMock(return_value=mock_response)

        result = await runtime._download_gmail_attachment("msg123", "att456")

        assert result == b"test attachment data"
        runtime._http_client.get.assert_awaited_once()
        call_args = runtime._http_client.get.call_args
        assert "msg123" in call_args[0][0]
        assert "att456" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_download_gmail_attachment_no_data(
        self, gmail_runtime_with_blob_store: GmailConnectorRuntime
    ) -> None:
        """Test download fails when API returns no data."""
        runtime = gmail_runtime_with_blob_store
        runtime._http_client = AsyncMock()
        runtime._get_access_token = AsyncMock(return_value="test-token")

        # Mock API response with no data
        mock_response = MagicMock()
        mock_response.json.return_value = {}
        mock_response.raise_for_status = MagicMock()
        runtime._http_client.get = AsyncMock(return_value=mock_response)

        with pytest.raises(ValueError, match="No data in attachment response"):
            await runtime._download_gmail_attachment("msg123", "att456")

    @pytest.mark.asyncio
    async def test_process_attachments_success(
        self, gmail_runtime_with_blob_store: GmailConnectorRuntime, mock_blob_store: AsyncMock
    ) -> None:
        """Test lazy-fetch attachment processing writes DB ref but excludes from envelope.

        Lazy-fetched attachments cannot appear in the ingest envelope because
        IngestAttachment requires a non-empty storage_ref. They are persisted
        as attachment_refs DB rows for on-demand fetch via fetch_attachment().
        """
        runtime = gmail_runtime_with_blob_store
        # No HTTP client needed: lazy-fetch attachments are not downloaded at ingest time.

        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "image/jpeg",
                    "filename": "photo.jpg",
                    "body": {
                        "attachmentId": "att123",
                        "size": 1024,
                    },
                },
            ],
        }

        result = await runtime._process_attachments("msg123", payload)

        # Lazy-fetched attachments are NOT returned (no storage_ref yet).
        # The attachment_refs DB row is written for on-demand materialization.
        assert result is None

        # Blob store must NOT be called during lazy ingest.
        mock_blob_store.put.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_process_attachments_no_blob_store(
        self, gmail_runtime: GmailConnectorRuntime
    ) -> None:
        """Test that lazy-fetch attachments write DB ref without blob store.

        Lazy-fetch model: metadata refs are written regardless of blob store
        availability. Lazy attachments are excluded from the return value
        (and thus the ingest envelope) because IngestAttachment requires
        a non-empty storage_ref.
        """
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "image/jpeg",
                    "filename": "photo.jpg",
                    "body": {
                        "attachmentId": "att123",
                        "size": 1024,
                    },
                },
            ],
        }

        result = await gmail_runtime._process_attachments("msg123", payload)

        # Lazy-fetched: not returned (DB ref only).
        assert result is None

    @pytest.mark.asyncio
    async def test_process_attachments_oversized_skipped(
        self, gmail_runtime_with_blob_store: GmailConnectorRuntime, mock_blob_store: AsyncMock
    ) -> None:
        """Test that attachments >5MB are skipped with warning."""
        runtime = gmail_runtime_with_blob_store

        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "image/jpeg",
                    "filename": "huge.jpg",
                    "body": {
                        "attachmentId": "att_big",
                        "size": 6 * 1024 * 1024,  # 6MB
                    },
                },
            ],
        }

        result = await runtime._process_attachments("msg123", payload)

        # Should return None (no attachments processed)
        assert result is None
        # Blob store should not be called
        mock_blob_store.put.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_process_attachments_lazy_writes_refs_only(
        self, gmail_runtime_with_blob_store: GmailConnectorRuntime, mock_blob_store: AsyncMock
    ) -> None:
        """Test that lazy-fetch writes DB refs but excludes attachments from return.

        Both images (JPEG, PNG) are lazy-fetched. No HTTP download occurs at ingest.
        Lazy attachments are excluded from the return value because IngestAttachment
        requires a non-empty storage_ref.
        """
        runtime = gmail_runtime_with_blob_store

        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "image/jpeg",
                    "filename": "first.jpg",
                    "body": {
                        "attachmentId": "att_first",
                        "size": 1024,
                    },
                },
                {
                    "mimeType": "image/png",
                    "filename": "second.png",
                    "body": {
                        "attachmentId": "att_second",
                        "size": 2048,
                    },
                },
            ],
        }

        result = await runtime._process_attachments("msg123", payload)

        # Lazy-fetched: not returned (DB refs only).
        assert result is None

        # No blob store calls during lazy ingest.
        mock_blob_store.put.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_build_ingest_envelope_with_attachments(
        self, gmail_runtime_with_blob_store: GmailConnectorRuntime, mock_blob_store: AsyncMock
    ) -> None:
        """Test that _build_ingest_envelope includes attachments."""
        runtime = gmail_runtime_with_blob_store
        runtime._http_client = AsyncMock()
        runtime._get_access_token = AsyncMock(return_value="test-token")

        # Mock attachment download
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": base64.urlsafe_b64encode(b"attachment data").decode()
        }
        mock_response.raise_for_status = MagicMock()
        runtime._http_client.get = AsyncMock(return_value=mock_response)

        message_data = {
            "id": "msg123",
            "threadId": "thread456",
            "internalDate": "1708099200000",
            "payload": {
                "headers": [
                    {"name": "Subject", "value": "Test Email"},
                    {"name": "From", "value": "sender@example.com"},
                    {"name": "Message-ID", "value": "<msg123@example.com>"},
                ],
                "mimeType": "multipart/mixed",
                "parts": [
                    {
                        "mimeType": "text/plain",
                        "body": {"data": base64.urlsafe_b64encode(b"Email body").decode()},
                    },
                    {
                        "mimeType": "image/jpeg",
                        "filename": "photo.jpg",
                        "body": {
                            "attachmentId": "att123",
                            "size": 1024,
                        },
                    },
                ],
            },
        }

        envelope = await runtime._build_ingest_envelope(message_data)

        assert envelope["schema_version"] == "ingest.v1"
        # JPEG is lazy-fetched, so it is excluded from the envelope (no storage_ref).
        # The attachment ref is persisted via _write_attachment_ref for on-demand fetch.
        assert envelope["payload"]["attachments"] is None

    @pytest.mark.asyncio
    async def test_build_ingest_envelope_without_attachments(
        self, gmail_runtime: GmailConnectorRuntime
    ) -> None:
        """Test that emails without attachments work correctly."""
        message_data = {
            "id": "msg123",
            "threadId": "thread456",
            "internalDate": "1708099200000",
            "payload": {
                "headers": [
                    {"name": "Subject", "value": "Test Email"},
                    {"name": "From", "value": "sender@example.com"},
                    {"name": "Message-ID", "value": "<msg123@example.com>"},
                ],
                "mimeType": "text/plain",
                "body": {"data": base64.urlsafe_b64encode(b"Email body").decode()},
            },
        }

        envelope = await gmail_runtime._build_ingest_envelope(message_data)

        assert envelope["schema_version"] == "ingest.v1"
        # attachments should be None when no blob store
        assert envelope["payload"]["attachments"] is None


# ---------------------------------------------------------------------------
# DB-first Gmail credential resolution
# ---------------------------------------------------------------------------


class TestResolveGmailCredentialsFromDb:
    """Tests for _resolve_gmail_credentials_from_db."""

    @staticmethod
    def _configure_single_db_env(monkeypatch: pytest.MonkeyPatch, db_name: str = "butlers") -> None:
        monkeypatch.setenv("CONNECTOR_BUTLER_DB_NAME", db_name)
        monkeypatch.setenv("BUTLER_SHARED_DB_NAME", db_name)

    @staticmethod
    def _make_secret_row(value: str):
        from unittest.mock import MagicMock

        row = MagicMock()
        row.__getitem__ = lambda self, key: value if key == "secret_value" else None
        return row

    async def test_returns_none_when_no_db_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Returns None when DATABASE_URL and POSTGRES_HOST are absent (default localhost)."""
        import asyncpg

        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("POSTGRES_HOST", raising=False)

        # Patch asyncpg to simulate connection failure (no DB running)
        async def fake_create_pool(**kwargs):
            raise Exception("Connection refused")

        monkeypatch.setattr(asyncpg, "create_pool", fake_create_pool)
        result = await _resolve_gmail_credentials_from_db()
        assert result is None

    async def test_returns_none_when_db_unreachable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Returns None gracefully when DB connection fails."""
        import asyncpg

        monkeypatch.setenv("DATABASE_URL", "postgres://localhost:5432/test")

        async def fake_create_pool(**kwargs):
            raise OSError("Connection refused")

        monkeypatch.setattr(asyncpg, "create_pool", fake_create_pool)
        result = await _resolve_gmail_credentials_from_db()
        assert result is None

    async def test_returns_none_when_db_has_no_credentials(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Returns None when DB is connected but no credentials are stored."""
        from contextlib import asynccontextmanager
        from unittest.mock import AsyncMock, MagicMock

        import asyncpg

        monkeypatch.setenv("DATABASE_URL", "postgres://localhost:5432/test")
        self._configure_single_db_env(monkeypatch)

        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = None  # No credentials in DB

        @asynccontextmanager
        async def fake_acquire():
            yield mock_conn

        mock_pool = MagicMock()
        mock_pool.acquire = fake_acquire
        mock_pool.close = AsyncMock()

        async def fake_create_pool(**kwargs):
            return mock_pool

        monkeypatch.setattr(asyncpg, "create_pool", fake_create_pool)
        result = await _resolve_gmail_credentials_from_db()
        assert result is None

    async def test_returns_credentials_when_db_has_stored_credentials(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Returns (client_id, client_secret, refresh_token) when DB has credentials."""
        from contextlib import asynccontextmanager
        from unittest.mock import AsyncMock, MagicMock, patch

        import asyncpg

        monkeypatch.setenv("DATABASE_URL", "postgres://localhost:5432/test")
        self._configure_single_db_env(monkeypatch)

        mock_conn = AsyncMock()
        secrets = {
            "GOOGLE_OAUTH_CLIENT_ID": "db-client-id",
            "GOOGLE_OAUTH_CLIENT_SECRET": "db-client-secret",
        }

        async def _fetchrow(query, key):
            value = secrets.get(key)
            if value is None:
                return None
            return self._make_secret_row(value)

        mock_conn.fetchrow.side_effect = _fetchrow

        # Use asynccontextmanager to properly mock `async with pool.acquire() as conn:`
        @asynccontextmanager
        async def fake_acquire():
            yield mock_conn

        mock_pool = MagicMock()
        mock_pool.acquire = fake_acquire
        mock_pool.close = AsyncMock()

        async def fake_create_pool(**kwargs):
            return mock_pool

        monkeypatch.setattr(asyncpg, "create_pool", fake_create_pool)
        with (
            patch(
                "butlers.google_credentials._resolve_account_entity_id",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "butlers.google_credentials.resolve_owner_entity_info",
                new_callable=AsyncMock,
                return_value="db-refresh-token",
            ),
        ):
            result = await _resolve_gmail_credentials_from_db()
        assert result is not None
        assert result["client_id"] == "db-client-id"
        assert result["client_secret"] == "db-client-secret"
        assert result["refresh_token"] == "db-refresh-token"

    async def test_resolves_pubsub_webhook_token_from_db(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Returns pubsub_webhook_token in result dict when stored in butler_secrets."""
        from contextlib import asynccontextmanager
        from unittest.mock import AsyncMock, MagicMock, patch

        import asyncpg

        monkeypatch.setenv("DATABASE_URL", "postgres://localhost:5432/test")
        self._configure_single_db_env(monkeypatch)

        mock_conn = AsyncMock()
        secrets = {
            "GOOGLE_OAUTH_CLIENT_ID": "db-client-id",
            "GOOGLE_OAUTH_CLIENT_SECRET": "db-client-secret",
            "GMAIL_PUBSUB_WEBHOOK_TOKEN": "db-pubsub-token",
        }

        async def _fetchrow(query, key):
            value = secrets.get(key)
            if value is None:
                return None
            return self._make_secret_row(value)

        mock_conn.fetchrow.side_effect = _fetchrow

        @asynccontextmanager
        async def fake_acquire():
            yield mock_conn

        mock_pool = MagicMock()
        mock_pool.acquire = fake_acquire
        mock_pool.close = AsyncMock()

        async def fake_create_pool(**kwargs):
            return mock_pool

        monkeypatch.setattr(asyncpg, "create_pool", fake_create_pool)
        with (
            patch(
                "butlers.google_credentials._resolve_account_entity_id",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "butlers.google_credentials.resolve_owner_entity_info",
                new_callable=AsyncMock,
                return_value="db-refresh-token",
            ),
        ):
            result = await _resolve_gmail_credentials_from_db()
        assert result is not None
        assert result["client_id"] == "db-client-id"
        assert result["pubsub_webhook_token"] == "db-pubsub-token"

    async def test_result_has_no_pubsub_token_when_not_stored(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """pubsub_webhook_token key absent from result when not stored in DB."""
        from contextlib import asynccontextmanager
        from unittest.mock import AsyncMock, MagicMock, patch

        import asyncpg

        monkeypatch.setenv("DATABASE_URL", "postgres://localhost:5432/test")
        self._configure_single_db_env(monkeypatch)

        mock_conn = AsyncMock()
        secrets = {
            "GOOGLE_OAUTH_CLIENT_ID": "db-client-id",
            "GOOGLE_OAUTH_CLIENT_SECRET": "db-client-secret",
        }

        async def _fetchrow(query, key):
            value = secrets.get(key)
            if value is None:
                return None
            return self._make_secret_row(value)

        mock_conn.fetchrow.side_effect = _fetchrow

        @asynccontextmanager
        async def fake_acquire():
            yield mock_conn

        mock_pool = MagicMock()
        mock_pool.acquire = fake_acquire
        mock_pool.close = AsyncMock()

        async def fake_create_pool(**kwargs):
            return mock_pool

        monkeypatch.setattr(asyncpg, "create_pool", fake_create_pool)
        with (
            patch(
                "butlers.google_credentials._resolve_account_entity_id",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "butlers.google_credentials.resolve_owner_entity_info",
                new_callable=AsyncMock,
                return_value="db-refresh-token",
            ),
        ):
            result = await _resolve_gmail_credentials_from_db()
        assert result is not None
        assert "pubsub_webhook_token" not in result

    async def test_uses_shared_schema_fallback_with_schema_scoped_search_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Falls back to shared schema lookup and configures asyncpg search_path per pool."""
        from contextlib import asynccontextmanager
        from unittest.mock import patch

        import asyncpg

        monkeypatch.setenv("DATABASE_URL", "postgres://localhost:5432/test")
        monkeypatch.setenv("CONNECTOR_BUTLER_DB_NAME", "butlers")
        monkeypatch.setenv("CONNECTOR_BUTLER_DB_SCHEMA", "general")
        monkeypatch.setenv("BUTLER_SHARED_DB_NAME", "butlers")
        monkeypatch.setenv("BUTLER_SHARED_DB_SCHEMA", "shared")

        local_conn = AsyncMock()
        local_conn.fetchrow.return_value = None

        shared_conn = AsyncMock()
        secrets = {
            "GOOGLE_OAUTH_CLIENT_ID": "db-client-id",
            "GOOGLE_OAUTH_CLIENT_SECRET": "db-client-secret",
        }

        async def _shared_fetchrow(query, key):
            value = secrets.get(key)
            if value is None:
                return None
            return self._make_secret_row(value)

        shared_conn.fetchrow.side_effect = _shared_fetchrow

        @asynccontextmanager
        async def local_acquire():
            yield local_conn

        @asynccontextmanager
        async def shared_acquire():
            yield shared_conn

        local_pool = MagicMock()
        local_pool.acquire = local_acquire
        local_pool.close = AsyncMock()

        shared_pool = MagicMock()
        shared_pool.acquire = shared_acquire
        shared_pool.close = AsyncMock()

        search_paths: list[str | None] = []

        async def fake_create_pool(**kwargs):
            server_settings = kwargs.get("server_settings") or {}
            search_path = server_settings.get("search_path")
            search_paths.append(search_path)
            if search_path == "general,shared,public":
                return local_pool
            if search_path == "shared,public":
                return shared_pool
            raise AssertionError(f"Unexpected search_path: {search_path!r}")

        monkeypatch.setattr(asyncpg, "create_pool", fake_create_pool)
        with patch(
            "butlers.google_credentials.resolve_owner_entity_info",
            new_callable=AsyncMock,
            return_value="db-refresh-token",
        ):
            result = await _resolve_gmail_credentials_from_db()
        assert result is not None
        assert result["client_id"] == "db-client-id"
        assert search_paths == ["general,shared,public", "shared,public"]


class TestGmailConnectorConfigCredentialInjection:
    """Verify connector credentials are injected explicitly (DB-only)."""

    def test_env_credential_vars_are_ignored_when_explicit_credentials_are_provided(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Config uses injected values even when env credential vars are present."""
        monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:40100/sse")
        monkeypatch.setenv("CONNECTOR_ENDPOINT_IDENTITY", "gmail:user:test@example.com")

        monkeypatch.setenv("GMAIL_CLIENT_ID", "legacy-client-id")
        monkeypatch.setenv("GMAIL_CLIENT_SECRET", "legacy-secret")
        monkeypatch.setenv("GMAIL_REFRESH_TOKEN", "legacy-token")
        monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "env-client-id")
        monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "env-client-secret")

        config = GmailConnectorConfig.from_env(
            gmail_client_id="db-client-id",
            gmail_client_secret="db-client-secret",
            gmail_refresh_token="db-refresh-token",
        )
        assert config.gmail_client_id == "db-client-id"
        assert config.gmail_client_secret == "db-client-secret"
        assert config.gmail_refresh_token == "db-refresh-token"

    def test_explicit_credentials_must_be_non_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Injected credentials are required and validated as non-empty."""
        monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:40100/sse")
        monkeypatch.setenv("CONNECTOR_ENDPOINT_IDENTITY", "gmail:user:test@example.com")

        with pytest.raises(ValueError, match="DB-resolved Gmail credentials missing"):
            GmailConnectorConfig.from_env(
                gmail_client_id="",
                gmail_client_secret="db-client-secret",
                gmail_refresh_token="db-refresh-token",
            )


# ---------------------------------------------------------------------------
# New tests for ATTACHMENT_POLICY, lazy/eager fetch, metrics, and on-demand
# fetch — added for butlers-dsa4.2.4
# ---------------------------------------------------------------------------


class TestAttachmentPolicy:
    """Unit tests for ATTACHMENT_POLICY map and derived constants."""

    def test_attachment_policy_contains_all_categories(self) -> None:
        """ATTACHMENT_POLICY covers images, PDF, spreadsheets, documents, and calendar."""
        from butlers.connectors.gmail import ATTACHMENT_POLICY

        # Images
        for mime in ("image/jpeg", "image/png", "image/gif", "image/webp"):
            assert mime in ATTACHMENT_POLICY, f"Missing image type: {mime}"
        # PDF
        assert "application/pdf" in ATTACHMENT_POLICY
        # Spreadsheets
        for mime in (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/vnd.ms-excel",
            "text/csv",
        ):
            assert mime in ATTACHMENT_POLICY, f"Missing spreadsheet type: {mime}"
        # Documents
        for mime in (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "message/rfc822",
        ):
            assert mime in ATTACHMENT_POLICY, f"Missing document type: {mime}"
        # Calendar
        assert "text/calendar" in ATTACHMENT_POLICY

    def test_calendar_fetch_mode_is_eager(self) -> None:
        """text/calendar uses eager fetch mode for direct calendar routing."""
        from butlers.connectors.gmail import ATTACHMENT_POLICY

        assert ATTACHMENT_POLICY["text/calendar"]["fetch_mode"] == "eager"

    def test_non_calendar_fetch_modes_are_lazy(self) -> None:
        """All non-calendar attachment types use lazy fetch mode."""
        from butlers.connectors.gmail import ATTACHMENT_POLICY

        for mime, policy in ATTACHMENT_POLICY.items():
            if mime != "text/calendar":
                assert policy["fetch_mode"] == "lazy", (
                    f"Expected lazy fetch for {mime}, got {policy['fetch_mode']}"
                )

    def test_calendar_size_limit_is_1mb(self) -> None:
        """text/calendar per-type limit is 1 MB."""
        from butlers.connectors.gmail import ATTACHMENT_POLICY

        assert ATTACHMENT_POLICY["text/calendar"]["max_size_bytes"] == 1 * 1024 * 1024

    def test_pdf_size_limit_is_15mb(self) -> None:
        """application/pdf per-type limit is 15 MB."""
        from butlers.connectors.gmail import ATTACHMENT_POLICY

        assert ATTACHMENT_POLICY["application/pdf"]["max_size_bytes"] == 15 * 1024 * 1024

    def test_image_size_limit_is_5mb(self) -> None:
        """Image types have 5 MB per-type limit."""
        from butlers.connectors.gmail import ATTACHMENT_POLICY

        for mime in ("image/jpeg", "image/png", "image/gif", "image/webp"):
            assert ATTACHMENT_POLICY[mime]["max_size_bytes"] == 5 * 1024 * 1024

    def test_global_max_is_25mb(self) -> None:
        """GLOBAL_MAX_ATTACHMENT_SIZE_BYTES is 25 MB (Gmail maximum)."""
        from butlers.connectors.gmail import GLOBAL_MAX_ATTACHMENT_SIZE_BYTES

        assert GLOBAL_MAX_ATTACHMENT_SIZE_BYTES == 25 * 1024 * 1024

    def test_supported_attachment_types_derived_from_policy(self) -> None:
        """SUPPORTED_ATTACHMENT_TYPES is exactly the set of ATTACHMENT_POLICY keys."""
        from butlers.connectors.gmail import ATTACHMENT_POLICY, SUPPORTED_ATTACHMENT_TYPES

        assert SUPPORTED_ATTACHMENT_TYPES == frozenset(ATTACHMENT_POLICY.keys())

    def test_spreadsheet_size_limit_is_10mb(self) -> None:
        """Spreadsheet types have 10 MB per-type limit."""
        from butlers.connectors.gmail import ATTACHMENT_POLICY

        for mime in (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/vnd.ms-excel",
            "text/csv",
        ):
            assert ATTACHMENT_POLICY[mime]["max_size_bytes"] == 10 * 1024 * 1024

    def test_document_size_limit_is_10mb(self) -> None:
        """Document types have 10 MB per-type limit."""
        from butlers.connectors.gmail import ATTACHMENT_POLICY

        for mime in (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "message/rfc822",
        ):
            assert ATTACHMENT_POLICY[mime]["max_size_bytes"] == 10 * 1024 * 1024


class TestAttachmentPolicyEnforcement:
    """Tests for per-type and global size cap enforcement in _process_attachments."""

    @pytest.fixture
    def mock_blob_store(self) -> AsyncMock:
        store = AsyncMock()
        store.put = AsyncMock(return_value="local://2026/02/blob.bin")
        return store

    @pytest.fixture
    def runtime(
        self, gmail_config: GmailConnectorConfig, mock_blob_store: AsyncMock
    ) -> GmailConnectorRuntime:
        return GmailConnectorRuntime(gmail_config, blob_store=mock_blob_store)

    @pytest.mark.asyncio
    async def test_oversized_image_skipped_by_per_type_limit(
        self, runtime: GmailConnectorRuntime, mock_blob_store: AsyncMock
    ) -> None:
        """JPEG > 5 MB is skipped by per-type limit."""
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "image/jpeg",
                    "filename": "huge.jpg",
                    "body": {"attachmentId": "att1", "size": 6 * 1024 * 1024},
                }
            ],
        }
        result = await runtime._process_attachments("msg1", payload)
        assert result is None
        mock_blob_store.put.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_pdf_under_15mb_accepted(self, runtime: GmailConnectorRuntime) -> None:
        """PDF at 10 MB (< 15 MB limit) is lazy-accepted (DB ref only, not in envelope)."""
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "application/pdf",
                    "filename": "report.pdf",
                    "body": {"attachmentId": "att2", "size": 10 * 1024 * 1024},
                }
            ],
        }
        result = await runtime._process_attachments("msg2", payload)
        # Lazy-fetched: not returned (DB ref written for on-demand fetch).
        assert result is None

    @pytest.mark.asyncio
    async def test_pdf_over_15mb_skipped(self, runtime: GmailConnectorRuntime) -> None:
        """PDF > 15 MB is skipped by per-type limit."""
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "application/pdf",
                    "filename": "huge.pdf",
                    "body": {"attachmentId": "att3", "size": 16 * 1024 * 1024},
                }
            ],
        }
        result = await runtime._process_attachments("msg3", payload)
        assert result is None

    @pytest.mark.asyncio
    async def test_oversized_attachment_skipped_by_global_cap(
        self, runtime: GmailConnectorRuntime
    ) -> None:
        """Attachment > 25 MB is skipped by global cap regardless of per-type limit."""
        # PDF limit is 15 MB but global cap is 25 MB — test with 26 MB
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "application/pdf",
                    "filename": "massive.pdf",
                    "body": {"attachmentId": "att4", "size": 26 * 1024 * 1024},
                }
            ],
        }
        result = await runtime._process_attachments("msg4", payload)
        assert result is None

    @pytest.mark.asyncio
    async def test_calendar_within_1mb_eager_fetched(
        self,
        runtime: GmailConnectorRuntime,
        mock_blob_store: AsyncMock,
    ) -> None:
        """text/calendar within 1 MB is eagerly fetched and stored."""
        import base64

        runtime._http_client = AsyncMock()
        runtime._get_access_token = AsyncMock(return_value="test-token")

        ics_bytes = b"BEGIN:VCALENDAR\nEND:VCALENDAR"
        mock_response = MagicMock()
        mock_response.json.return_value = {"data": base64.urlsafe_b64encode(ics_bytes).decode()}
        mock_response.raise_for_status = MagicMock()
        runtime._http_client.get = AsyncMock(return_value=mock_response)

        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "text/calendar",
                    "filename": "invite.ics",
                    "body": {"attachmentId": "att_ics", "size": 500},
                }
            ],
        }

        result = await runtime._process_attachments("msg5", payload)

        assert result is not None
        assert len(result) == 1
        assert result[0]["media_type"] == "text/calendar"
        assert result[0]["storage_ref"] == "local://2026/02/blob.bin"
        assert "fetched" not in result[0]  # Internal field excluded from envelope dict
        # Blob store must be called for eager fetch.
        mock_blob_store.put.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_calendar_over_1mb_skipped(self, runtime: GmailConnectorRuntime) -> None:
        """text/calendar > 1 MB is skipped by per-type limit."""
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "text/calendar",
                    "filename": "big.ics",
                    "body": {"attachmentId": "att_big_ics", "size": 2 * 1024 * 1024},
                }
            ],
        }
        result = await runtime._process_attachments("msg6", payload)
        assert result is None

    @pytest.mark.asyncio
    async def test_calendar_no_blob_store_skipped(self, gmail_config: GmailConnectorConfig) -> None:
        """text/calendar with no blob store is skipped (eager path requires blob store)."""
        runtime = GmailConnectorRuntime(gmail_config)  # No blob store

        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "text/calendar",
                    "filename": "invite.ics",
                    "body": {"attachmentId": "att_ics2", "size": 500},
                }
            ],
        }
        result = await runtime._process_attachments("msg7", payload)
        assert result is None

    @pytest.mark.asyncio
    async def test_unsupported_type_not_extracted(self, runtime: GmailConnectorRuntime) -> None:
        """Unsupported MIME types (e.g., application/zip) are excluded from MIME walk."""
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "application/zip",
                    "filename": "archive.zip",
                    "body": {"attachmentId": "att_zip", "size": 1024},
                }
            ],
        }
        result = await runtime._process_attachments("msg8", payload)
        assert result is None

    @pytest.mark.asyncio
    async def test_mixed_types_lazy_and_eager(
        self,
        runtime: GmailConnectorRuntime,
        mock_blob_store: AsyncMock,
    ) -> None:
        """JPEG (lazy) and text/calendar (eager) are processed correctly together."""
        import base64

        runtime._http_client = AsyncMock()
        runtime._get_access_token = AsyncMock(return_value="test-token")

        ics_bytes = b"BEGIN:VCALENDAR\nEND:VCALENDAR"
        mock_response = MagicMock()
        mock_response.json.return_value = {"data": base64.urlsafe_b64encode(ics_bytes).decode()}
        mock_response.raise_for_status = MagicMock()
        runtime._http_client.get = AsyncMock(return_value=mock_response)

        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "image/jpeg",
                    "filename": "photo.jpg",
                    "body": {"attachmentId": "att_jpg", "size": 1024},
                },
                {
                    "mimeType": "text/calendar",
                    "filename": "invite.ics",
                    "body": {"attachmentId": "att_ics3", "size": 400},
                },
            ],
        }

        result = await runtime._process_attachments("msg9", payload)

        # Only eager-fetched attachments (calendar) appear in the return value.
        # Lazy-fetched (JPEG) is excluded (DB ref only).
        assert result is not None
        assert len(result) == 1
        assert result[0]["media_type"] == "text/calendar"
        assert result[0]["storage_ref"] is not None
        assert "fetched" not in result[0]

        # Blob store called exactly once (for .ics only).
        mock_blob_store.put.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_calendar_eager_fetch_error_is_visible(
        self,
        runtime: GmailConnectorRuntime,
        mock_blob_store: AsyncMock,
    ) -> None:
        """Eager fetch failure for text/calendar is logged and the attachment is dropped."""
        runtime._http_client = AsyncMock()
        runtime._get_access_token = AsyncMock(return_value="test-token")
        runtime._http_client.get = AsyncMock(side_effect=Exception("Network error"))

        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "text/calendar",
                    "filename": "invite.ics",
                    "body": {"attachmentId": "att_ics_err", "size": 400},
                }
            ],
        }

        result = await runtime._process_attachments("msg10", payload)
        # Failure is not silently dropped — result is None (no attachment processed).
        assert result is None


class TestAttachmentRefsWrite:
    """Tests for _write_attachment_ref and DB pool interaction."""

    @pytest.fixture
    def gmail_config(self) -> GmailConnectorConfig:
        return GmailConnectorConfig(
            switchboard_mcp_url="http://localhost:40100/sse",
            connector_endpoint_identity="gmail:user:test@example.com",
            gmail_client_id="cid",
            gmail_client_secret="csec",
            gmail_refresh_token="rtoken",
        )

    @pytest.mark.asyncio
    async def test_write_attachment_ref_no_pool(self, gmail_config: GmailConnectorConfig) -> None:
        """_write_attachment_ref is a no-op when db_pool is None."""
        runtime = GmailConnectorRuntime(gmail_config)
        # Should not raise
        await runtime._write_attachment_ref(
            message_id="msg1",
            attachment_id="att1",
            filename="file.pdf",
            media_type="application/pdf",
            size_bytes=1024,
        )

    @pytest.mark.asyncio
    async def test_write_attachment_ref_with_pool(self, gmail_config: GmailConnectorConfig) -> None:
        """_write_attachment_ref executes upsert SQL when pool is available."""
        mock_conn = AsyncMock()
        mock_pool = AsyncMock()
        mock_pool.acquire = MagicMock(return_value=mock_pool)
        mock_pool.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.__aexit__ = AsyncMock(return_value=None)

        runtime = GmailConnectorRuntime(gmail_config, db_pool=mock_pool)

        await runtime._write_attachment_ref(
            message_id="msg2",
            attachment_id="att2",
            filename="doc.pdf",
            media_type="application/pdf",
            size_bytes=2048,
            fetched=False,
            blob_ref=None,
        )

        mock_conn.execute.assert_awaited_once()
        call_args = mock_conn.execute.call_args
        sql = call_args[0][0]
        assert "attachment_refs" in sql
        assert "ON CONFLICT" in sql

    @pytest.mark.asyncio
    async def test_write_attachment_ref_db_error_does_not_raise(
        self, gmail_config: GmailConnectorConfig
    ) -> None:
        """DB errors in _write_attachment_ref are swallowed (best-effort)."""
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(side_effect=Exception("DB error"))
        mock_pool = AsyncMock()
        mock_pool.acquire = MagicMock(return_value=mock_pool)
        mock_pool.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.__aexit__ = AsyncMock(return_value=None)

        runtime = GmailConnectorRuntime(gmail_config, db_pool=mock_pool)

        # Must not raise
        await runtime._write_attachment_ref(
            message_id="msg3",
            attachment_id="att3",
            filename=None,
            media_type="image/jpeg",
            size_bytes=500,
        )


class TestOnDemandFetch:
    """Tests for the fetch_attachment on-demand materialization path."""

    @pytest.fixture
    def mock_blob_store(self) -> AsyncMock:
        store = AsyncMock()
        store.put = AsyncMock(return_value="local://2026/02/lazy.bin")
        return store

    @pytest.fixture
    def gmail_config(self) -> GmailConnectorConfig:
        return GmailConnectorConfig(
            switchboard_mcp_url="http://localhost:40100/sse",
            connector_endpoint_identity="gmail:user:test@example.com",
            gmail_client_id="cid",
            gmail_client_secret="csec",
            gmail_refresh_token="rtoken",
        )

    @pytest.mark.asyncio
    async def test_fetch_attachment_no_blob_store(self, gmail_config: GmailConnectorConfig) -> None:
        """fetch_attachment returns None when blob store is not configured."""
        runtime = GmailConnectorRuntime(gmail_config)
        result = await runtime.fetch_attachment("msg1", "att1")
        assert result is None

    @pytest.mark.asyncio
    async def test_fetch_attachment_idempotent_already_fetched(
        self, gmail_config: GmailConnectorConfig, mock_blob_store: AsyncMock
    ) -> None:
        """fetch_attachment returns existing blob_ref if already materialized."""
        # DB pool returns a row with fetched=True
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(
            return_value={
                "fetched": True,
                "blob_ref": "local://existing/blob.pdf",
                "filename": "doc.pdf",
                "media_type": "application/pdf",
                "size_bytes": 1024,
            }
        )
        mock_pool = AsyncMock()
        mock_pool.acquire = MagicMock(return_value=mock_pool)
        mock_pool.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.__aexit__ = AsyncMock(return_value=None)

        runtime = GmailConnectorRuntime(gmail_config, blob_store=mock_blob_store, db_pool=mock_pool)

        result = await runtime.fetch_attachment("msg1", "att_existing")

        assert result == "local://existing/blob.pdf"
        # Blob store should NOT be called (idempotent short-circuit).
        mock_blob_store.put.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_fetch_attachment_materializes_unfetched(
        self, gmail_config: GmailConnectorConfig, mock_blob_store: AsyncMock
    ) -> None:
        """fetch_attachment downloads, stores, and returns blob_ref for unfetched attachment."""
        import base64

        # DB pool returns unfetched row on first call, then write_attachment_ref runs
        mock_conn = AsyncMock()
        # First fetchrow: unfetched row
        # Second fetchrow (inside download path): metadata row
        mock_conn.fetchrow = AsyncMock(
            side_effect=[
                {
                    "fetched": False,
                    "blob_ref": None,
                    "filename": "doc.pdf",
                    "media_type": "application/pdf",
                    "size_bytes": 5000,
                },
                {
                    "fetched": False,
                    "blob_ref": None,
                    "filename": "doc.pdf",
                    "media_type": "application/pdf",
                    "size_bytes": 5000,
                },
            ]
        )
        mock_conn.execute = AsyncMock()
        mock_pool = AsyncMock()
        mock_pool.acquire = MagicMock(return_value=mock_pool)
        mock_pool.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.__aexit__ = AsyncMock(return_value=None)

        runtime = GmailConnectorRuntime(gmail_config, blob_store=mock_blob_store, db_pool=mock_pool)
        runtime._http_client = AsyncMock()
        runtime._get_access_token = AsyncMock(return_value="test-token")

        # Mock download
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": base64.urlsafe_b64encode(b"pdf content").decode()
        }
        mock_response.raise_for_status = MagicMock()
        runtime._http_client.get = AsyncMock(return_value=mock_response)

        result = await runtime.fetch_attachment("msg2", "att_lazy")

        assert result == "local://2026/02/lazy.bin"
        mock_blob_store.put.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_fetch_attachment_download_failure_returns_none(
        self, gmail_config: GmailConnectorConfig, mock_blob_store: AsyncMock
    ) -> None:
        """fetch_attachment returns None on download failure."""
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value={"fetched": False, "blob_ref": None})
        mock_pool = AsyncMock()
        mock_pool.acquire = MagicMock(return_value=mock_pool)
        mock_pool.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.__aexit__ = AsyncMock(return_value=None)

        runtime = GmailConnectorRuntime(gmail_config, blob_store=mock_blob_store, db_pool=mock_pool)
        runtime._http_client = AsyncMock()
        runtime._get_access_token = AsyncMock(return_value="test-token")
        runtime._http_client.get = AsyncMock(side_effect=Exception("Network fail"))

        result = await runtime.fetch_attachment("msg3", "att_fail")
        assert result is None


class TestAttachmentMetrics:
    """Tests for attachment-specific metrics on ConnectorMetrics."""

    def test_record_attachment_fetched_eager(self) -> None:
        """record_attachment_fetched with fetch_mode='eager' increments eager counter."""
        from prometheus_client import REGISTRY

        from butlers.connectors.metrics import ConnectorMetrics

        metrics = ConnectorMetrics(
            connector_type="gmail_test_eager",
            endpoint_identity="test@test.com",
        )
        metrics.record_attachment_fetched(
            media_type="text/calendar", fetch_mode="eager", result="success"
        )
        # Verify counter exists and was incremented (check via registry)
        eager_counter = REGISTRY.get_sample_value(
            "connector_attachment_fetched_eager_total",
            labels={
                "connector_type": "gmail_test_eager",
                "endpoint_identity": "test@test.com",
                "media_type": "text/calendar",
                "result": "success",
            },
        )
        assert eager_counter == 1.0

    def test_record_attachment_fetched_lazy(self) -> None:
        """record_attachment_fetched with fetch_mode='lazy' increments lazy counter."""
        from prometheus_client import REGISTRY

        from butlers.connectors.metrics import ConnectorMetrics

        metrics = ConnectorMetrics(
            connector_type="gmail_test_lazy",
            endpoint_identity="test@test.com",
        )
        metrics.record_attachment_fetched(
            media_type="image/jpeg", fetch_mode="lazy", result="success"
        )
        lazy_counter = REGISTRY.get_sample_value(
            "connector_attachment_fetched_lazy_total",
            labels={
                "connector_type": "gmail_test_lazy",
                "endpoint_identity": "test@test.com",
                "media_type": "image/jpeg",
                "result": "success",
            },
        )
        assert lazy_counter == 1.0

    def test_record_attachment_skipped_oversized(self) -> None:
        """record_attachment_skipped_oversized increments the oversized counter."""
        from prometheus_client import REGISTRY

        from butlers.connectors.metrics import ConnectorMetrics

        metrics = ConnectorMetrics(
            connector_type="gmail_test_oversized",
            endpoint_identity="test@test.com",
        )
        metrics.record_attachment_skipped_oversized(media_type="application/pdf")
        counter = REGISTRY.get_sample_value(
            "connector_attachment_skipped_oversized_total",
            labels={
                "connector_type": "gmail_test_oversized",
                "endpoint_identity": "test@test.com",
                "media_type": "application/pdf",
            },
        )
        assert counter == 1.0

    def test_record_attachment_type_distribution(self) -> None:
        """record_attachment_type_distribution increments the distribution counter."""
        from prometheus_client import REGISTRY

        from butlers.connectors.metrics import ConnectorMetrics

        metrics = ConnectorMetrics(
            connector_type="gmail_test_dist",
            endpoint_identity="test@test.com",
        )
        metrics.record_attachment_type_distribution(media_type="text/csv")
        counter = REGISTRY.get_sample_value(
            "connector_attachment_type_distribution_total",
            labels={
                "connector_type": "gmail_test_dist",
                "endpoint_identity": "test@test.com",
                "media_type": "text/csv",
            },
        )
        assert counter == 1.0


class TestExtractAttachmentsExpanded:
    """Tests for _extract_attachments with expanded MIME types."""

    @pytest.fixture
    def runtime(self) -> GmailConnectorRuntime:
        config = GmailConnectorConfig(
            switchboard_mcp_url="http://localhost:40100/sse",
            connector_endpoint_identity="gmail:user:test@example.com",
            gmail_client_id="cid",
            gmail_client_secret="csec",
            gmail_refresh_token="rtoken",
        )
        return GmailConnectorRuntime(config)

    def test_extract_spreadsheet_xlsx(self, runtime: GmailConnectorRuntime) -> None:
        """Excel XLSX files are extracted."""
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    "filename": "data.xlsx",
                    "body": {"attachmentId": "att_xlsx", "size": 5000},
                }
            ],
        }
        result = runtime._extract_attachments(payload)
        assert len(result) == 1
        assert result[0]["mime_type"] == (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

    def test_extract_csv(self, runtime: GmailConnectorRuntime) -> None:
        """CSV files are extracted."""
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "text/csv",
                    "filename": "export.csv",
                    "body": {"attachmentId": "att_csv", "size": 3000},
                }
            ],
        }
        result = runtime._extract_attachments(payload)
        assert len(result) == 1
        assert result[0]["mime_type"] == "text/csv"

    def test_extract_docx(self, runtime: GmailConnectorRuntime) -> None:
        """Word DOCX files are extracted."""
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": (
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                    ),
                    "filename": "letter.docx",
                    "body": {"attachmentId": "att_docx", "size": 4000},
                }
            ],
        }
        result = runtime._extract_attachments(payload)
        assert len(result) == 1
        assert result[0]["mime_type"] == (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )

    def test_extract_eml(self, runtime: GmailConnectorRuntime) -> None:
        """Forwarded email (message/rfc822) attachments are extracted."""
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "message/rfc822",
                    "filename": "fwd.eml",
                    "body": {"attachmentId": "att_eml", "size": 8000},
                }
            ],
        }
        result = runtime._extract_attachments(payload)
        assert len(result) == 1
        assert result[0]["mime_type"] == "message/rfc822"

    def test_extract_ics_calendar(self, runtime: GmailConnectorRuntime) -> None:
        """Calendar .ics (text/calendar) files are extracted."""
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "text/calendar",
                    "filename": "invite.ics",
                    "body": {"attachmentId": "att_ics", "size": 800},
                }
            ],
        }
        result = runtime._extract_attachments(payload)
        assert len(result) == 1
        assert result[0]["mime_type"] == "text/calendar"

    def test_extract_xls(self, runtime: GmailConnectorRuntime) -> None:
        """Legacy Excel XLS (application/vnd.ms-excel) files are extracted."""
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "application/vnd.ms-excel",
                    "filename": "old.xls",
                    "body": {"attachmentId": "att_xls", "size": 2000},
                }
            ],
        }
        result = runtime._extract_attachments(payload)
        assert len(result) == 1
        assert result[0]["mime_type"] == "application/vnd.ms-excel"


# ---------------------------------------------------------------------------
# Backfill polling protocol tests
# (docs/connectors/interface.md section 14, docs/connectors/gmail.md section 9)
# ---------------------------------------------------------------------------


@pytest.fixture
def backfill_runtime() -> GmailConnectorRuntime:
    """Create a Gmail connector runtime with backfill enabled."""
    from butlers.connectors.gmail import BackfillJob  # noqa: F401 (imported for use in tests)

    config = GmailConnectorConfig(
        switchboard_mcp_url="http://localhost:40100/sse",
        connector_provider="gmail",
        connector_channel="email",
        connector_endpoint_identity="gmail:user:backfill@example.com",
        connector_max_inflight=4,
        gmail_client_id="test-client-id",
        gmail_client_secret="test-client-secret",
        gmail_refresh_token="test-refresh-token",
        gmail_poll_interval_s=5,
        connector_backfill_enabled=True,
        connector_backfill_poll_interval_s=60,
        connector_backfill_progress_interval=5,
    )
    return GmailConnectorRuntime(config)


class TestBackfillConfig:
    """Tests for backfill-related GmailConnectorConfig fields."""

    def test_backfill_defaults(self, gmail_config: GmailConnectorConfig) -> None:
        """Default backfill config values match spec defaults."""
        assert gmail_config.connector_backfill_enabled is True
        assert gmail_config.connector_backfill_poll_interval_s == 60
        assert gmail_config.connector_backfill_progress_interval == 50

    def test_backfill_disabled_via_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """CONNECTOR_BACKFILL_ENABLED=false disables backfill."""
        monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:40100/sse")
        monkeypatch.setenv("CONNECTOR_ENDPOINT_IDENTITY", "gmail:user:test@example.com")

        monkeypatch.setenv("CONNECTOR_BACKFILL_ENABLED", "false")

        config = GmailConnectorConfig.from_env(
            gmail_client_id="client-id",
            gmail_client_secret="client-secret",
            gmail_refresh_token="refresh-token",
        )
        assert config.connector_backfill_enabled is False

    def test_backfill_poll_interval_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """CONNECTOR_BACKFILL_POLL_INTERVAL_S is parsed from env."""
        monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:40100/sse")
        monkeypatch.setenv("CONNECTOR_ENDPOINT_IDENTITY", "gmail:user:test@example.com")

        monkeypatch.setenv("CONNECTOR_BACKFILL_POLL_INTERVAL_S", "120")

        config = GmailConnectorConfig.from_env(
            gmail_client_id="client-id",
            gmail_client_secret="client-secret",
            gmail_refresh_token="refresh-token",
        )
        assert config.connector_backfill_poll_interval_s == 120

    def test_backfill_progress_interval_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """CONNECTOR_BACKFILL_PROGRESS_INTERVAL is parsed from env."""
        monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:40100/sse")
        monkeypatch.setenv("CONNECTOR_ENDPOINT_IDENTITY", "gmail:user:test@example.com")

        monkeypatch.setenv("CONNECTOR_BACKFILL_PROGRESS_INTERVAL", "25")

        config = GmailConnectorConfig.from_env(
            gmail_client_id="client-id",
            gmail_client_secret="client-secret",
            gmail_refresh_token="refresh-token",
        )
        assert config.connector_backfill_progress_interval == 25

    def test_backfill_poll_interval_invalid_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Non-integer CONNECTOR_BACKFILL_POLL_INTERVAL_S raises ValueError."""
        monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:40100/sse")
        monkeypatch.setenv("CONNECTOR_ENDPOINT_IDENTITY", "gmail:user:test@example.com")

        monkeypatch.setenv("CONNECTOR_BACKFILL_POLL_INTERVAL_S", "notanint")

        with pytest.raises(  # noqa: E501
            ValueError, match="CONNECTOR_BACKFILL_POLL_INTERVAL_S must be an integer"
        ):
            GmailConnectorConfig.from_env(
                gmail_client_id="client-id",
                gmail_client_secret="client-secret",
                gmail_refresh_token="refresh-token",
            )

    def test_backfill_progress_interval_invalid_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Non-integer CONNECTOR_BACKFILL_PROGRESS_INTERVAL raises ValueError."""
        monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:40100/sse")
        monkeypatch.setenv("CONNECTOR_ENDPOINT_IDENTITY", "gmail:user:test@example.com")

        monkeypatch.setenv("CONNECTOR_BACKFILL_PROGRESS_INTERVAL", "bad")

        with pytest.raises(  # noqa: E501
            ValueError, match="CONNECTOR_BACKFILL_PROGRESS_INTERVAL must be an integer"
        ):
            GmailConnectorConfig.from_env(
                gmail_client_id="client-id",
                gmail_client_secret="client-secret",
                gmail_refresh_token="refresh-token",
            )


class TestBackfillJob:
    """Tests for BackfillJob model."""

    def test_backfill_job_basic(self) -> None:
        """BackfillJob parses required fields."""
        from butlers.connectors.gmail import BackfillJob

        job = BackfillJob(
            job_id="job-123",
            date_from="2025-01-01",
            date_to="2025-12-31",
        )
        assert job.job_id == "job-123"
        assert job.date_from == "2025-01-01"
        assert job.date_to == "2025-12-31"
        assert job.rate_limit_per_hour == 100
        assert job.daily_cost_cap_cents == 500
        assert job.cursor is None
        assert job.target_categories == []

    def test_backfill_job_with_cursor(self) -> None:
        """BackfillJob accepts cursor for resume."""
        from butlers.connectors.gmail import BackfillJob

        job = BackfillJob(
            job_id="job-456",
            date_from="2024-01-01",
            date_to="2024-06-30",
            cursor={"page_token": "abc123"},
            rate_limit_per_hour=50,
            daily_cost_cap_cents=200,
        )
        assert job.cursor == {"page_token": "abc123"}
        assert job.rate_limit_per_hour == 50

    def test_backfill_job_with_target_categories(self) -> None:
        """BackfillJob stores target_categories."""
        from butlers.connectors.gmail import BackfillJob

        job = BackfillJob(
            job_id="job-789",
            date_from="2023-01-01",
            date_to="2023-12-31",
            target_categories=["finance", "health"],
        )
        assert "finance" in job.target_categories
        assert "health" in job.target_categories


class TestBackfillPollAndExecute:
    """Tests for _poll_and_execute_backfill_job and _run_backfill_loop."""

    async def test_no_pending_job_returns_silently(
        self, backfill_runtime: GmailConnectorRuntime
    ) -> None:
        """When backfill.poll returns None, no job is executed."""
        with patch.object(
            backfill_runtime._mcp_client, "call_tool", new=AsyncMock(return_value=None)
        ):
            # Should complete without error
            await backfill_runtime._poll_and_execute_backfill_job()

    async def test_poll_mcp_failure_is_non_fatal(
        self, backfill_runtime: GmailConnectorRuntime
    ) -> None:
        """backfill.poll MCP failure is logged and does not crash loop."""
        with patch.object(
            backfill_runtime._mcp_client,
            "call_tool",
            new=AsyncMock(side_effect=ConnectionError("mcp down")),
        ):
            # Should not raise
            await backfill_runtime._poll_and_execute_backfill_job()

    async def test_poll_first_attempt_failure_uses_debug_log(
        self, backfill_runtime: GmailConnectorRuntime
    ) -> None:
        """First backfill.poll failure is logged at DEBUG, not WARNING.

        Switchboard may still be starting up during the first poll attempt,
        so the warning is suppressed to avoid noise in normal startup.
        """
        with patch.object(
            backfill_runtime._mcp_client,
            "call_tool",
            new=AsyncMock(side_effect=ConnectionError("switchboard not ready yet")),
        ):
            with patch("butlers.connectors.gmail.logger") as mock_logger:
                await backfill_runtime._poll_and_execute_backfill_job()

        mock_logger.debug.assert_called_once()
        mock_logger.warning.assert_not_called()
        assert backfill_runtime._backfill_poll_attempts == 1

    async def test_poll_subsequent_failure_uses_warning_log(
        self, backfill_runtime: GmailConnectorRuntime
    ) -> None:
        """Subsequent backfill.poll failures (attempt > 1) are logged at WARNING.

        After the first attempt, connection failures indicate a genuine problem
        and must remain visible to operators.
        """
        # Simulate one prior successful attempt
        backfill_runtime._backfill_poll_attempts = 1

        with patch.object(
            backfill_runtime._mcp_client,
            "call_tool",
            new=AsyncMock(side_effect=ConnectionError("persistent failure")),
        ):
            with patch("butlers.connectors.gmail.logger") as mock_logger:
                await backfill_runtime._poll_and_execute_backfill_job()

        mock_logger.warning.assert_called_once()
        mock_logger.debug.assert_not_called()
        assert backfill_runtime._backfill_poll_attempts == 2

    async def test_poll_returns_invalid_type_is_non_fatal(
        self, backfill_runtime: GmailConnectorRuntime
    ) -> None:
        """Non-dict backfill.poll response is handled gracefully."""
        with patch.object(
            backfill_runtime._mcp_client, "call_tool", new=AsyncMock(return_value="invalid")
        ):
            await backfill_runtime._poll_and_execute_backfill_job()

    async def test_poll_result_missing_job_id_is_non_fatal(
        self, backfill_runtime: GmailConnectorRuntime
    ) -> None:
        """Poll result without job_id is logged and skipped."""
        with patch.object(
            backfill_runtime._mcp_client,
            "call_tool",
            new=AsyncMock(return_value={"date_from": "2025-01-01", "date_to": "2025-12-31"}),
        ):
            await backfill_runtime._poll_and_execute_backfill_job()

    async def test_poll_triggers_job_execution(
        self, backfill_runtime: GmailConnectorRuntime
    ) -> None:
        """A valid poll result dispatches to _execute_backfill_job."""
        job_response = {
            "job_id": "job-001",
            "date_from": "2025-01-01",
            "date_to": "2025-03-31",
            "rate_limit_per_hour": 100,
            "daily_cost_cap_cents": 500,
        }

        executed_jobs: list = []

        async def mock_execute(job: object) -> None:
            executed_jobs.append(job)

        with (
            patch.object(
                backfill_runtime._mcp_client, "call_tool", new=AsyncMock(return_value=job_response)
            ),
            patch.object(backfill_runtime, "_execute_backfill_job", new=mock_execute),
        ):
            await backfill_runtime._poll_and_execute_backfill_job()

        assert len(executed_jobs) == 1
        assert executed_jobs[0].job_id == "job-001"

    async def test_poll_with_nested_params_structure(
        self, backfill_runtime: GmailConnectorRuntime
    ) -> None:
        """Poll result with nested params dict is correctly parsed."""
        job_response = {
            "job_id": "job-nested",
            "params": {
                "date_from": "2025-06-01",
                "date_to": "2025-06-30",
                "rate_limit_per_hour": 50,
                "target_categories": ["finance"],
            },
            "cursor": {"page_token": "tok123"},
        }

        executed_jobs: list = []

        async def mock_execute(job: object) -> None:
            executed_jobs.append(job)

        with (
            patch.object(
                backfill_runtime._mcp_client, "call_tool", new=AsyncMock(return_value=job_response)
            ),
            patch.object(backfill_runtime, "_execute_backfill_job", new=mock_execute),
        ):
            await backfill_runtime._poll_and_execute_backfill_job()

        assert len(executed_jobs) == 1
        job = executed_jobs[0]
        assert job.date_from == "2025-06-01"
        assert job.cursor == {"page_token": "tok123"}
        assert "finance" in job.target_categories

    async def test_backfill_loop_runs_until_stopped(
        self, backfill_runtime: GmailConnectorRuntime
    ) -> None:
        """_run_backfill_loop calls poll repeatedly and stops when _running=False."""
        call_count = 0

        async def mock_poll_and_execute() -> None:
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                backfill_runtime._running = False

        backfill_runtime._running = True

        with (
            patch.object(
                backfill_runtime, "_poll_and_execute_backfill_job", new=mock_poll_and_execute
            ),
            patch("asyncio.sleep", new=AsyncMock()),
        ):
            await backfill_runtime._run_backfill_loop()

        assert call_count >= 2

    async def test_backfill_loop_errors_are_non_fatal(
        self, backfill_runtime: GmailConnectorRuntime
    ) -> None:
        """Errors in poll_and_execute are caught and loop continues."""
        call_count = 0

        async def mock_poll_and_execute() -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("transient error")
            backfill_runtime._running = False

        backfill_runtime._running = True

        with (
            patch.object(
                backfill_runtime, "_poll_and_execute_backfill_job", new=mock_poll_and_execute
            ),
            patch("asyncio.sleep", new=AsyncMock()),
        ):
            await backfill_runtime._run_backfill_loop()

        assert call_count >= 2

    async def test_backfill_loop_has_initial_delay_before_first_poll(
        self, backfill_runtime: GmailConnectorRuntime
    ) -> None:
        """_run_backfill_loop waits for initial delay before first poll."""
        sleep_calls: list[float] = []
        poll_call_count = 0

        async def mock_sleep(delay: float) -> None:
            """Track sleep calls."""
            sleep_calls.append(delay)

        async def mock_poll_and_execute() -> None:
            """Track that poll was called."""
            nonlocal poll_call_count
            poll_call_count += 1
            # Stop after first poll to keep test fast
            backfill_runtime._running = False

        backfill_runtime._running = True

        with (
            patch.object(
                backfill_runtime, "_poll_and_execute_backfill_job", new=mock_poll_and_execute
            ),
            patch("asyncio.sleep", new=mock_sleep),
        ):
            await backfill_runtime._run_backfill_loop()

        # Should have at least 2 sleep calls: initial delay (10s) + interval sleep
        assert len(sleep_calls) >= 2
        # First sleep should be the initial 10s delay
        assert sleep_calls[0] == 10
        # Poll should only be called once since we stopped after first
        assert poll_call_count == 1


class TestFetchBackfillMessagePage:
    """Tests for _fetch_backfill_message_page."""

    async def test_fetches_messages_for_date_range(
        self, backfill_runtime: GmailConnectorRuntime
    ) -> None:
        """Date range is converted to Gmail query and messages returned."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "messages": [{"id": "msg1"}, {"id": "msg2"}],
        }

        with (
            patch.object(backfill_runtime, "_http_client", new=AsyncMock()) as mock_client,
            patch.object(
                backfill_runtime, "_get_access_token", new=AsyncMock(return_value="token")
            ),
        ):
            mock_client.get = AsyncMock(return_value=mock_response)
            messages, next_token = await backfill_runtime._fetch_backfill_message_page(
                date_from="2025-01-01", date_to="2025-06-30"
            )

        assert len(messages) == 2
        assert next_token is None

        # Verify query was built correctly
        call_params = mock_client.get.call_args[1]["params"]
        assert "after:2025/01/01" in call_params["q"]
        assert "before:2025/06/30" in call_params["q"]

    async def test_passes_page_token_for_pagination(
        self, backfill_runtime: GmailConnectorRuntime
    ) -> None:
        """page_token is passed to messages.list for pagination resume."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "messages": [{"id": "msg3"}],
            "nextPageToken": "next-tok-456",
        }

        with (
            patch.object(backfill_runtime, "_http_client", new=AsyncMock()) as mock_client,
            patch.object(
                backfill_runtime, "_get_access_token", new=AsyncMock(return_value="token")
            ),
        ):
            mock_client.get = AsyncMock(return_value=mock_response)
            messages, next_token = await backfill_runtime._fetch_backfill_message_page(
                date_from="2025-01-01",
                date_to="2025-03-31",
                page_token="prev-tok-123",
            )

        assert len(messages) == 1
        assert next_token == "next-tok-456"
        call_params = mock_client.get.call_args[1]["params"]
        assert call_params["pageToken"] == "prev-tok-123"

    async def test_empty_result_returns_empty_list(
        self, backfill_runtime: GmailConnectorRuntime
    ) -> None:
        """Empty messages list from API returns empty list and no next token."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {}  # No 'messages' key

        with (
            patch.object(backfill_runtime, "_http_client", new=AsyncMock()) as mock_client,
            patch.object(
                backfill_runtime, "_get_access_token", new=AsyncMock(return_value="token")
            ),
        ):
            mock_client.get = AsyncMock(return_value=mock_response)
            messages, next_token = await backfill_runtime._fetch_backfill_message_page(
                date_from="2025-01-01", date_to="2025-01-31"
            )

        assert messages == []
        assert next_token is None


class TestReportBackfillProgress:
    """Tests for _report_backfill_progress."""

    async def test_sends_progress_to_switchboard(
        self, backfill_runtime: GmailConnectorRuntime
    ) -> None:
        """Progress is submitted via backfill.progress MCP tool."""
        with patch.object(
            backfill_runtime._mcp_client,
            "call_tool",
            new=AsyncMock(return_value={"status": "ack"}),
        ) as mock_call:
            status = await backfill_runtime._report_backfill_progress(
                job_id="job-001",
                rows_processed=50,
                rows_skipped=5,
                cost_spent_cents=10,
                cursor={"page_token": "tok123"},
            )

        assert status == "ack"
        mock_call.assert_awaited_once()
        args = mock_call.call_args[0]
        assert args[0] == "backfill.progress"
        payload = args[1]
        assert payload["job_id"] == "job-001"
        assert payload["rows_processed"] == 50
        assert payload["rows_skipped"] == 5
        assert payload["cost_spent_cents_delta"] == 10
        assert payload["cursor"] == {"page_token": "tok123"}

    async def test_returns_paused_status(self, backfill_runtime: GmailConnectorRuntime) -> None:
        """When Switchboard returns 'paused', connector should stop."""
        with patch.object(
            backfill_runtime._mcp_client,
            "call_tool",
            new=AsyncMock(return_value={"status": "paused"}),
        ):
            status = await backfill_runtime._report_backfill_progress(
                job_id="job-002",
                rows_processed=100,
                rows_skipped=0,
                cost_spent_cents=20,
            )

        assert status == "paused"

    async def test_returns_cancelled_status(self, backfill_runtime: GmailConnectorRuntime) -> None:
        """When Switchboard returns 'cancelled', connector should stop."""
        with patch.object(
            backfill_runtime._mcp_client,
            "call_tool",
            new=AsyncMock(return_value={"status": "cancelled"}),
        ):
            status = await backfill_runtime._report_backfill_progress(
                job_id="job-003",
                rows_processed=200,
                rows_skipped=10,
                cost_spent_cents=40,
            )

        assert status == "cancelled"

    async def test_returns_cost_capped_status(
        self, backfill_runtime: GmailConnectorRuntime
    ) -> None:
        """When Switchboard returns 'cost_capped', connector should stop."""
        with patch.object(
            backfill_runtime._mcp_client,
            "call_tool",
            new=AsyncMock(return_value={"status": "cost_capped"}),
        ):
            status = await backfill_runtime._report_backfill_progress(
                job_id="job-004",
                rows_processed=300,
                rows_skipped=50,
                cost_spent_cents=500,
            )

        assert status == "cost_capped"

    async def test_progress_mcp_failure_returns_ack(
        self, backfill_runtime: GmailConnectorRuntime
    ) -> None:
        """MCP call failure is non-fatal; connector assumes 'ack' and continues."""
        with patch.object(
            backfill_runtime._mcp_client,
            "call_tool",
            new=AsyncMock(side_effect=ConnectionError("progress endpoint unreachable")),
        ):
            status = await backfill_runtime._report_backfill_progress(
                job_id="job-005",
                rows_processed=10,
                rows_skipped=2,
                cost_spent_cents=5,
            )

        # Connector should assume 'ack' and continue when progress call fails
        assert status == "ack"

    async def test_sends_status_and_error_fields(
        self, backfill_runtime: GmailConnectorRuntime
    ) -> None:
        """Optional status and error fields are included when provided."""
        with patch.object(
            backfill_runtime._mcp_client,
            "call_tool",
            new=AsyncMock(return_value={"status": "ack"}),
        ) as mock_call:
            await backfill_runtime._report_backfill_progress(
                job_id="job-006",
                rows_processed=0,
                rows_skipped=1,
                cost_spent_cents=0,
                status="error",
                error="API quota exceeded",
            )

        payload = mock_call.call_args[0][1]
        assert payload["status"] == "error"
        assert payload["error"] == "API quota exceeded"


class TestExecuteBackfillJob:
    """Tests for _execute_backfill_job loop control and behavior."""

    def _make_job(self, **kwargs: object) -> object:
        from butlers.connectors.gmail import BackfillJob

        defaults = {
            "job_id": "test-job",
            "date_from": "2025-01-01",
            "date_to": "2025-01-31",
            "rate_limit_per_hour": 3600,  # 1 token/second -> no wait in tests
            "daily_cost_cap_cents": 500,
        }
        defaults.update(kwargs)
        return BackfillJob(**defaults)  # type: ignore[arg-type]

    def _make_message_data(self, msg_id: str = "msg1") -> dict:
        """Build a minimal Gmail message payload for testing."""
        return {
            "id": msg_id,
            "threadId": f"thread-{msg_id}",
            "internalDate": "1700000000000",
            "payload": {
                "mimeType": "text/plain",
                "headers": [
                    {"name": "Subject", "value": "Test Email"},
                    {"name": "From", "value": "sender@example.com"},
                    {"name": "Message-ID", "value": f"<{msg_id}@example.com>"},
                ],
                "body": {"data": "dGVzdCBib2R5"},  # base64 "test body"
            },
        }

    async def test_completes_when_no_messages(
        self, backfill_runtime: GmailConnectorRuntime
    ) -> None:
        """Job completes immediately when there are no messages in date range."""
        job = self._make_job()
        progress_calls: list[dict] = []

        async def mock_progress(**kwargs: object) -> str:
            progress_calls.append(kwargs)  # type: ignore[arg-type]
            return "ack"

        with (
            patch.object(
                backfill_runtime,
                "_fetch_backfill_message_page",
                new=AsyncMock(return_value=([], None)),
            ),
            patch.object(
                backfill_runtime,
                "_report_backfill_progress",
                new=AsyncMock(side_effect=lambda **kw: (progress_calls.append(kw), "ack")[1]),
            ),
        ):
            await backfill_runtime._execute_backfill_job(job)  # type: ignore[arg-type]

        # Completion progress should be sent
        assert any(call.get("status") == "completed" for call in progress_calls), (
            f"Expected 'completed' status in calls: {progress_calls}"
        )

    async def test_stops_on_paused_signal(self, backfill_runtime: GmailConnectorRuntime) -> None:
        """Job stops when backfill.progress returns 'paused'."""
        job = self._make_job(connector_backfill_progress_interval=1)

        # Override progress interval on config
        backfill_runtime._config = backfill_runtime._config.model_copy(
            update={"connector_backfill_progress_interval": 1}
        )

        pages_fetched = 0
        page_data = [[{"id": "msgA"}, {"id": "msgB"}]]

        async def mock_fetch_page(**kwargs: object) -> tuple[list, None]:
            nonlocal pages_fetched
            pages_fetched += 1
            return (page_data[0], None)

        async def mock_fetch_message(msg_id: str) -> dict:
            return self._make_message_data(msg_id)

        async def mock_submit(envelope: dict) -> None:
            pass

        call_count = 0

        async def mock_progress(**kwargs: object) -> str:
            nonlocal call_count
            call_count += 1
            return "paused"  # Signal pause on first progress call

        with (
            patch.object(
                backfill_runtime,
                "_fetch_backfill_message_page",
                new=AsyncMock(side_effect=mock_fetch_page),
            ),
            patch.object(
                backfill_runtime, "_fetch_message", new=AsyncMock(side_effect=mock_fetch_message)
            ),
            patch.object(
                backfill_runtime, "_submit_to_ingest_api", new=AsyncMock(side_effect=mock_submit)
            ),
            patch.object(
                backfill_runtime,
                "_report_backfill_progress",
                new=AsyncMock(side_effect=lambda **kw: (None, "paused")[1]),
            ),
        ):
            await backfill_runtime._execute_backfill_job(job)  # type: ignore[arg-type]

        # Should have stopped after receiving 'paused'
        assert pages_fetched <= 2  # Should not keep fetching after pause

    async def test_stops_on_cancelled_signal(self, backfill_runtime: GmailConnectorRuntime) -> None:
        """Job stops when backfill.progress returns 'cancelled'."""
        backfill_runtime._config = backfill_runtime._config.model_copy(
            update={"connector_backfill_progress_interval": 1}
        )
        job = self._make_job()

        async def mock_fetch_page(**kwargs: object) -> tuple[list, None]:
            return ([{"id": "msg1"}], None)

        async def mock_fetch_message(msg_id: str) -> dict:
            return self._make_message_data(msg_id)

        with (
            patch.object(
                backfill_runtime,
                "_fetch_backfill_message_page",
                new=AsyncMock(side_effect=mock_fetch_page),
            ),
            patch.object(
                backfill_runtime, "_fetch_message", new=AsyncMock(side_effect=mock_fetch_message)
            ),
            patch.object(backfill_runtime, "_submit_to_ingest_api", new=AsyncMock()),
            patch.object(
                backfill_runtime,
                "_report_backfill_progress",
                new=AsyncMock(return_value="cancelled"),
            ),
        ):
            await backfill_runtime._execute_backfill_job(job)  # type: ignore[arg-type]
        # Passes if no infinite loop

    async def test_resumes_from_server_side_cursor(
        self, backfill_runtime: GmailConnectorRuntime
    ) -> None:
        """Job resumes from server-side cursor in job.cursor.page_token."""
        from butlers.connectors.gmail import BackfillJob

        job = BackfillJob(
            job_id="resume-job",
            date_from="2025-01-01",
            date_to="2025-01-31",
            rate_limit_per_hour=3600,
            cursor={"page_token": "resume-token-xyz"},
        )

        fetch_calls: list = []

        async def mock_fetch_page(
            date_from: str, date_to: str, page_token: str | None = None
        ) -> tuple[list, None]:
            fetch_calls.append(page_token)
            return ([], None)  # No messages, completes immediately

        with (
            patch.object(
                backfill_runtime,
                "_fetch_backfill_message_page",
                new=AsyncMock(side_effect=mock_fetch_page),
            ),
            patch.object(
                backfill_runtime,
                "_report_backfill_progress",
                new=AsyncMock(return_value="ack"),
            ),
        ):
            await backfill_runtime._execute_backfill_job(job)

        assert fetch_calls[0] == "resume-token-xyz", (
            f"Expected first fetch with resume-token-xyz, got {fetch_calls}"
        )

    async def test_does_not_advance_live_cursor(
        self, backfill_runtime: GmailConnectorRuntime
    ) -> None:
        """Backfill execution must not modify the live ingestion cursor in DB."""
        job = self._make_job()

        # Track save_cursor calls to verify live cursor is never overwritten
        save_calls: list[dict[str, Any]] = []

        async def track_save(_pool: object, _prov: str, _eid: str, val: str) -> None:
            save_calls.append(json.loads(val))

        with (
            patch.object(
                backfill_runtime,
                "_fetch_backfill_message_page",
                new=AsyncMock(return_value=([], None)),
            ),
            patch.object(
                backfill_runtime,
                "_report_backfill_progress",
                new=AsyncMock(return_value="ack"),
            ),
            patch(
                "butlers.connectors.cursor_store.save_cursor",
                new=AsyncMock(side_effect=track_save),
            ),
        ):
            await backfill_runtime._execute_backfill_job(job)  # type: ignore[arg-type]

        # Backfill should not write to the live cursor store
        assert len(save_calls) == 0

    async def test_ingest_failure_increments_skipped(
        self, backfill_runtime: GmailConnectorRuntime
    ) -> None:
        """Failed message ingest increments rows_skipped, not rows_processed."""
        backfill_runtime._config = backfill_runtime._config.model_copy(
            update={"connector_backfill_progress_interval": 10}
        )
        job = self._make_job()

        progress_args: list[dict] = []

        async def mock_fetch_page(
            date_from: str, date_to: str, page_token: str | None = None
        ) -> tuple[list, None | str]:
            return ([{"id": "msg-fail"}], None)

        async def mock_progress(**kwargs: object) -> str:
            progress_args.append(kwargs)  # type: ignore[arg-type]
            return "ack"

        with (
            patch.object(
                backfill_runtime,
                "_fetch_backfill_message_page",
                new=AsyncMock(side_effect=mock_fetch_page),
            ),
            patch.object(
                backfill_runtime,
                "_fetch_message",
                new=AsyncMock(side_effect=RuntimeError("API down")),
            ),
            patch.object(
                backfill_runtime,
                "_report_backfill_progress",
                new=AsyncMock(side_effect=mock_progress),
            ),
        ):
            await backfill_runtime._execute_backfill_job(job)  # type: ignore[arg-type]

        # Completion progress call should show 0 processed (failure counted as skipped)
        completion_call = next((c for c in progress_args if c.get("status") == "completed"), None)
        if completion_call:
            assert completion_call.get("rows_processed", 0) == 0


class TestCapabilityAdvertisement:
    """Tests for backfill capability advertisement in heartbeats."""

    def test_get_capabilities_returns_backfill_true_when_enabled(
        self, backfill_runtime: GmailConnectorRuntime
    ) -> None:
        """_get_capabilities returns {backfill: True} when backfill enabled."""
        caps = backfill_runtime._get_capabilities()
        assert caps.get("backfill") is True

    def test_get_capabilities_returns_backfill_false_when_disabled(self) -> None:
        """_get_capabilities returns {backfill: False} when backfill disabled."""
        config = GmailConnectorConfig(
            switchboard_mcp_url="http://localhost:40100/sse",
            connector_provider="gmail",
            connector_channel="email",
            connector_endpoint_identity="gmail:user:test@example.com",
            connector_max_inflight=4,
            gmail_client_id="test-client-id",
            gmail_client_secret="test-client-secret",
            gmail_refresh_token="test-refresh-token",
            connector_backfill_enabled=False,
        )
        runtime = GmailConnectorRuntime(config)
        caps = runtime._get_capabilities()
        assert caps.get("backfill") is False


class TestHeartbeatCapabilities:
    """Tests for ConnectorHeartbeat capabilities extension."""

    async def test_heartbeat_includes_capabilities_when_provided(self) -> None:
        """Heartbeat envelope includes capabilities when get_capabilities is set."""
        from butlers.connectors.heartbeat import ConnectorHeartbeat, HeartbeatConfig

        config = HeartbeatConfig(
            connector_type="gmail",
            endpoint_identity="gmail:user:test@example.com",
            interval_s=120,
            enabled=True,
        )
        mock_mcp = MagicMock()
        mock_mcp.call_tool = AsyncMock(return_value={"status": "accepted"})
        mock_metrics = MagicMock()

        sent_envelopes: list[dict] = []

        async def capture_call(tool_name: str, envelope: dict) -> dict:
            sent_envelopes.append(envelope)
            return {"status": "accepted"}

        mock_mcp.call_tool = AsyncMock(side_effect=capture_call)

        heartbeat = ConnectorHeartbeat(
            config=config,
            mcp_client=mock_mcp,
            metrics=mock_metrics,
            get_health_state=lambda: ("healthy", None),
            get_capabilities=lambda: {"backfill": True},
        )

        await heartbeat._send_heartbeat()

        assert len(sent_envelopes) == 1
        envelope = sent_envelopes[0]
        assert "capabilities" in envelope, f"Expected capabilities in envelope: {envelope}"
        assert envelope["capabilities"].get("backfill") is True

    async def test_heartbeat_omits_capabilities_when_not_provided(self) -> None:
        """Heartbeat envelope omits capabilities key when get_capabilities is None."""
        from butlers.connectors.heartbeat import ConnectorHeartbeat, HeartbeatConfig

        config = HeartbeatConfig(
            connector_type="gmail",
            endpoint_identity="gmail:user:test@example.com",
            interval_s=120,
            enabled=True,
        )
        mock_mcp = MagicMock()
        mock_metrics = MagicMock()

        sent_envelopes: list[dict] = []

        async def capture_call(tool_name: str, envelope: dict) -> dict:
            sent_envelopes.append(envelope)
            return {"status": "accepted"}

        mock_mcp.call_tool = AsyncMock(side_effect=capture_call)

        heartbeat = ConnectorHeartbeat(
            config=config,
            mcp_client=mock_mcp,
            metrics=mock_metrics,
            get_health_state=lambda: ("healthy", None),
            get_capabilities=None,
        )

        await heartbeat._send_heartbeat()

        assert len(sent_envelopes) == 1
        envelope = sent_envelopes[0]
        assert "capabilities" not in envelope

    async def test_heartbeat_empty_capabilities_omitted(self) -> None:
        """Heartbeat envelope omits capabilities key when get_capabilities returns empty dict."""
        from butlers.connectors.heartbeat import ConnectorHeartbeat, HeartbeatConfig

        config = HeartbeatConfig(
            connector_type="gmail",
            endpoint_identity="gmail:user:test@example.com",
            interval_s=120,
            enabled=True,
        )
        mock_mcp = MagicMock()
        mock_metrics = MagicMock()

        sent_envelopes: list[dict] = []

        async def capture_call(tool_name: str, envelope: dict) -> dict:
            sent_envelopes.append(envelope)
            return {"status": "accepted"}

        mock_mcp.call_tool = AsyncMock(side_effect=capture_call)

        heartbeat = ConnectorHeartbeat(
            config=config,
            mcp_client=mock_mcp,
            metrics=mock_metrics,
            get_health_state=lambda: ("healthy", None),
            get_capabilities=lambda: {},  # Empty dict
        )

        await heartbeat._send_heartbeat()

        envelope = sent_envelopes[0]
        assert "capabilities" not in envelope


# ---------------------------------------------------------------------------
# Tests for review-fix validations
# (empty dates in BackfillJob, rate_limit_per_hour <= 0 guard,
#  dedicated backfill semaphore slot reservation)
# ---------------------------------------------------------------------------


class TestBackfillJobValidation:
    """Tests for BackfillJob field validators added in review fixes."""

    def test_empty_date_from_raises(self) -> None:
        """BackfillJob rejects empty date_from to prevent malformed Gmail queries."""
        from butlers.connectors.gmail import BackfillJob

        with pytest.raises(Exception, match="must not be empty"):
            BackfillJob(job_id="j1", date_from="", date_to="2025-12-31")

    def test_empty_date_to_raises(self) -> None:
        """BackfillJob rejects empty date_to to prevent malformed Gmail queries."""
        from butlers.connectors.gmail import BackfillJob

        with pytest.raises(Exception, match="must not be empty"):
            BackfillJob(job_id="j2", date_from="2025-01-01", date_to="")

    def test_valid_dates_accepted(self) -> None:
        """BackfillJob accepts non-empty YYYY-MM-DD date strings."""
        from butlers.connectors.gmail import BackfillJob

        job = BackfillJob(job_id="j3", date_from="2025-01-01", date_to="2025-12-31")
        assert job.date_from == "2025-01-01"
        assert job.date_to == "2025-12-31"

    async def test_zero_rate_limit_skips_job(self, backfill_runtime: GmailConnectorRuntime) -> None:
        """_execute_backfill_job skips execution when rate_limit_per_hour == 0."""
        from butlers.connectors.gmail import BackfillJob

        job = BackfillJob(
            job_id="zero-rate",
            date_from="2025-01-01",
            date_to="2025-01-31",
            rate_limit_per_hour=0,
        )

        fetch_called = False

        async def mock_fetch_page(**kwargs: object) -> tuple[list, None]:
            nonlocal fetch_called
            fetch_called = True
            return ([], None)

        with patch.object(
            backfill_runtime,
            "_fetch_backfill_message_page",
            new=AsyncMock(side_effect=mock_fetch_page),
        ):
            await backfill_runtime._execute_backfill_job(job)  # type: ignore[arg-type]

        # fetch should not be called because rate guard exits early
        assert not fetch_called, (
            "fetch_backfill_message_page should not be called with rate_limit_per_hour=0"
        )

    def test_backfill_semaphore_is_one_less_than_max_inflight(
        self, backfill_runtime: GmailConnectorRuntime
    ) -> None:
        """_backfill_semaphore is initialized to (max_inflight - 1) slots."""
        max_inflight = backfill_runtime._config.connector_max_inflight
        # asyncio.Semaphore._value is the initial count
        expected = max(1, max_inflight - 1)
        assert backfill_runtime._backfill_semaphore._value == expected, (
            f"Expected backfill semaphore value={expected}, "
            f"got {backfill_runtime._backfill_semaphore._value}"
        )


class TestResolveGmailEndpointIdentity:
    """Tests for resolve_gmail_endpoint_identity()."""

    async def test_resolves_email_from_profile_api(self) -> None:
        """Should return gmail:user:<email> when profile API returns emailAddress."""
        token_response = MagicMock(spec=httpx.Response)
        token_response.raise_for_status = MagicMock()
        token_response.json.return_value = {"access_token": "test-access-token"}

        profile_response = MagicMock(spec=httpx.Response)
        profile_response.raise_for_status = MagicMock()
        profile_response.json.return_value = {
            "emailAddress": "alice@example.com",
            "historyId": "12345",
        }

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=token_response)
        mock_client.get = AsyncMock(return_value=profile_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("butlers.connectors.gmail.httpx.AsyncClient", return_value=mock_client):
            result = await resolve_gmail_endpoint_identity(
                client_id="client-id",
                client_secret="client-secret",
                refresh_token="refresh-token",
                env_fallback="gmail:user:dev",
            )

        assert result == "gmail:user:alice@example.com"

    async def test_falls_back_to_env_fallback_on_api_error(self) -> None:
        """Should return env_fallback when the API call raises an exception."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("butlers.connectors.gmail.httpx.AsyncClient", return_value=mock_client):
            result = await resolve_gmail_endpoint_identity(
                client_id="client-id",
                client_secret="client-secret",
                refresh_token="refresh-token",
                env_fallback="gmail:user:dev",
            )

        assert result == "gmail:user:dev"

    async def test_falls_back_when_profile_missing_email(self) -> None:
        """Should return env_fallback when profile response has no emailAddress."""
        token_response = MagicMock(spec=httpx.Response)
        token_response.raise_for_status = MagicMock()
        token_response.json.return_value = {"access_token": "test-access-token"}

        profile_response = MagicMock(spec=httpx.Response)
        profile_response.raise_for_status = MagicMock()
        # Profile response missing emailAddress field
        profile_response.json.return_value = {"historyId": "12345"}

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=token_response)
        mock_client.get = AsyncMock(return_value=profile_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("butlers.connectors.gmail.httpx.AsyncClient", return_value=mock_client):
            result = await resolve_gmail_endpoint_identity(
                client_id="client-id",
                client_secret="client-secret",
                refresh_token="refresh-token",
                env_fallback="gmail:user:dev",
            )

        assert result == "gmail:user:dev"

    async def test_falls_back_on_http_error_status(self) -> None:
        """Should return env_fallback when the profile API returns an HTTP error."""
        token_response = MagicMock(spec=httpx.Response)
        token_response.raise_for_status = MagicMock()
        token_response.json.return_value = {"access_token": "test-access-token"}

        profile_response = MagicMock(spec=httpx.Response)
        profile_response.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(
                "401 Unauthorized", request=MagicMock(), response=MagicMock()
            )
        )

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=token_response)
        mock_client.get = AsyncMock(return_value=profile_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("butlers.connectors.gmail.httpx.AsyncClient", return_value=mock_client):
            result = await resolve_gmail_endpoint_identity(
                client_id="client-id",
                client_secret="client-secret",
                refresh_token="refresh-token",
                env_fallback="gmail:user:dev",
            )

        assert result == "gmail:user:dev"


class TestGmailProcessConfig:
    """Tests for GmailProcessConfig environment variable loading."""

    def test_from_env_minimal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Minimal required env vars should produce a valid config."""
        monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:40100/sse")
        # Clear optional vars to test defaults
        for key in [
            "CONNECTOR_PROVIDER",
            "CONNECTOR_CHANNEL",
            "CONNECTOR_MAX_INFLIGHT",
            "GMAIL_POLL_INTERVAL_S",
            "GMAIL_ACCOUNT_RESCAN_INTERVAL_S",
        ]:
            monkeypatch.delenv(key, raising=False)

        config = GmailProcessConfig.from_env()
        assert config.switchboard_mcp_url == "http://localhost:40100/sse"
        assert config.connector_provider == "gmail"
        assert config.connector_channel == "email"
        assert config.connector_max_inflight == 8
        assert config.gmail_poll_interval_s == 60
        assert config.gmail_account_rescan_interval_s == 300

    def test_from_env_custom_rescan_interval(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """GMAIL_ACCOUNT_RESCAN_INTERVAL_S should override default."""
        monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:40100/sse")
        monkeypatch.setenv("GMAIL_ACCOUNT_RESCAN_INTERVAL_S", "120")
        config = GmailProcessConfig.from_env()
        assert config.gmail_account_rescan_interval_s == 120

    def test_from_env_missing_switchboard_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Missing SWITCHBOARD_MCP_URL should raise."""
        monkeypatch.delenv("SWITCHBOARD_MCP_URL", raising=False)
        with pytest.raises(KeyError):
            GmailProcessConfig.from_env()

    def test_make_account_config_uses_process_defaults(self) -> None:
        """make_account_config should use process defaults when no metadata overrides."""
        process_config = GmailProcessConfig(
            switchboard_mcp_url="http://localhost:40100/sse",
            gmail_poll_interval_s=90,
            gmail_label_exclude=("SPAM", "TRASH"),
        )
        account_config = process_config.make_account_config(
            email="alice@example.com",
            client_id="cid",
            client_secret="csec",
            refresh_token="rtoken",
        )
        assert account_config.connector_endpoint_identity == "gmail:user:alice@example.com"
        assert account_config.gmail_poll_interval_s == 90
        assert account_config.gmail_label_exclude == ("SPAM", "TRASH")
        assert account_config.gmail_user_email == "alice@example.com"
        assert account_config.gmail_client_id == "cid"

    def test_make_account_config_applies_metadata_overrides(self) -> None:
        """Per-account metadata.gmail overrides should take precedence."""
        process_config = GmailProcessConfig(
            switchboard_mcp_url="http://localhost:40100/sse",
            gmail_poll_interval_s=60,
            gmail_label_exclude=("SPAM", "TRASH"),
        )
        account_config = process_config.make_account_config(
            email="work@example.com",
            client_id="cid",
            client_secret="csec",
            refresh_token="rtoken",
            metadata_gmail={"poll_interval_s": 120, "label_exclude": "SPAM"},
        )
        assert account_config.gmail_poll_interval_s == 120
        assert account_config.gmail_label_exclude == ("SPAM",)

    def test_make_account_config_endpoint_identity_format(self) -> None:
        """Endpoint identity should be gmail:user:<email>."""
        process_config = GmailProcessConfig(
            switchboard_mcp_url="http://localhost:40100/sse",
        )
        account_config = process_config.make_account_config(
            email="user@domain.com",
            client_id="cid",
            client_secret="csec",
            refresh_token="rtoken",
        )
        assert account_config.connector_endpoint_identity == "gmail:user:user@domain.com"


class TestGmailConnectorManager:
    """Tests for GmailConnectorManager multi-account orchestration."""

    def _make_process_config(self) -> GmailProcessConfig:
        return GmailProcessConfig(
            switchboard_mcp_url="http://localhost:40100/sse",
            gmail_poll_interval_s=5,
            gmail_account_rescan_interval_s=999,
        )

    def _make_mock_pool(self) -> MagicMock:
        pool = MagicMock()
        pool.acquire = MagicMock()
        return pool

    async def test_discover_qualifying_accounts_filters_by_scope(self) -> None:
        """Only active accounts with Gmail scopes should be returned."""
        process_config = self._make_process_config()
        db_pool = self._make_mock_pool()
        manager = GmailConnectorManager(
            process_config=process_config,
            db_pool=db_pool,
            cursor_pool=None,
        )

        rows = [
            {
                "email": "alice@example.com",
                "granted_scopes": ["https://www.googleapis.com/auth/gmail.modify"],
                "metadata": {},
            },
            {
                "email": "bob@example.com",
                "granted_scopes": ["https://www.googleapis.com/auth/calendar"],
                "metadata": {},
            },
            {
                "email": "carol@example.com",
                "granted_scopes": ["https://www.googleapis.com/auth/gmail.readonly"],
                "metadata": {"gmail": {"poll_interval_s": 120}},
            },
        ]

        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=rows)

        # Mock pool.acquire() as async context manager
        cm = AsyncMock()
        cm.__aenter__ = AsyncMock(return_value=mock_conn)
        cm.__aexit__ = AsyncMock(return_value=None)
        db_pool.acquire = MagicMock(return_value=cm)

        result = await manager._discover_qualifying_accounts()

        emails = [r[0] for r in result]
        assert "alice@example.com" in emails
        assert "carol@example.com" in emails
        assert "bob@example.com" not in emails

    async def test_discover_accounts_returns_empty_on_db_error(self) -> None:
        """DB errors during discovery should return empty list (non-fatal)."""
        process_config = self._make_process_config()
        db_pool = self._make_mock_pool()
        manager = GmailConnectorManager(
            process_config=process_config,
            db_pool=db_pool,
            cursor_pool=None,
        )

        db_pool.acquire = MagicMock(side_effect=Exception("DB connection failed"))

        result = await manager._discover_qualifying_accounts()
        assert result == []

    async def test_sync_accounts_adds_new_loop(self) -> None:
        """Newly discovered accounts should spawn a GmailAccountLoop."""
        process_config = self._make_process_config()
        db_pool = self._make_mock_pool()
        manager = GmailConnectorManager(
            process_config=process_config,
            db_pool=db_pool,
            cursor_pool=None,
        )

        mock_creds = {
            "client_id": "cid",
            "client_secret": "csec",
            "refresh_token": "rtoken",
        }

        mock_loop = MagicMock()
        mock_loop.start = MagicMock()
        mock_loop.is_running = True

        with (
            patch.object(
                manager,
                "_discover_qualifying_accounts",
                new=AsyncMock(return_value=[("alice@example.com", None)]),
            ),
            patch.object(
                manager,
                "_resolve_credentials_for_account",
                new=AsyncMock(return_value=mock_creds),
            ),
            patch("butlers.connectors.gmail.GmailAccountLoop", return_value=mock_loop) as loop_ctor,
        ):
            added, removed, unchanged = await manager._sync_accounts()

        assert "alice@example.com" in added
        assert removed == []
        loop_ctor.assert_called_once()
        mock_loop.start.assert_called_once()

    async def test_sync_accounts_removes_old_loop(self) -> None:
        """Accounts no longer in DB should have their loops stopped."""
        process_config = self._make_process_config()
        db_pool = self._make_mock_pool()
        manager = GmailConnectorManager(
            process_config=process_config,
            db_pool=db_pool,
            cursor_pool=None,
        )

        # Pre-populate with an existing loop
        old_loop = MagicMock()
        old_loop.stop = AsyncMock()
        manager._loops["old@example.com"] = old_loop

        with patch.object(
            manager,
            "_discover_qualifying_accounts",
            new=AsyncMock(return_value=[]),
        ):
            added, removed, unchanged = await manager._sync_accounts()

        assert "old@example.com" in removed
        assert "old@example.com" not in manager._loops
        old_loop.stop.assert_awaited_once()

    async def test_sync_accounts_skips_failed_credentials(self) -> None:
        """Accounts where credential resolution fails should be skipped (not crash)."""
        process_config = self._make_process_config()
        db_pool = self._make_mock_pool()
        manager = GmailConnectorManager(
            process_config=process_config,
            db_pool=db_pool,
            cursor_pool=None,
        )

        with (
            patch.object(
                manager,
                "_discover_qualifying_accounts",
                new=AsyncMock(return_value=[("badcreds@example.com", None)]),
            ),
            patch.object(
                manager,
                "_resolve_credentials_for_account",
                new=AsyncMock(return_value=None),
            ),
        ):
            added, removed, unchanged = await manager._sync_accounts()

        assert added == []
        assert "badcreds@example.com" not in manager._loops

    def test_get_multi_account_health_degraded_when_no_accounts(self) -> None:
        """Health status should be degraded when no accounts are active."""
        process_config = self._make_process_config()
        db_pool = self._make_mock_pool()
        manager = GmailConnectorManager(
            process_config=process_config,
            db_pool=db_pool,
            cursor_pool=None,
        )

        health = manager._get_multi_account_health()
        assert health.status == "degraded"
        assert health.active_accounts == 0
        assert health.account_health == []

    def test_get_multi_account_health_worst_case_aggregation(self) -> None:
        """Overall status should be worst-case of all account statuses."""
        process_config = self._make_process_config()
        db_pool = self._make_mock_pool()
        manager = GmailConnectorManager(
            process_config=process_config,
            db_pool=db_pool,
            cursor_pool=None,
        )

        healthy_loop = MagicMock()
        healthy_loop.get_health = MagicMock(
            return_value=AccountHealthStatus(
                email="a@example.com",
                endpoint_identity="gmail:user:a@example.com",
                status="healthy",
                last_checkpoint_save_at=None,
                last_ingest_submit_at=None,
                source_api_connectivity="connected",
            )
        )
        error_loop = MagicMock()
        error_loop.get_health = MagicMock(
            return_value=AccountHealthStatus(
                email="b@example.com",
                endpoint_identity="gmail:user:b@example.com",
                status="error",
                last_checkpoint_save_at=None,
                last_ingest_submit_at=None,
                source_api_connectivity="disconnected",
                error="token expired",
            )
        )
        manager._loops = {
            "a@example.com": healthy_loop,
            "b@example.com": error_loop,
        }

        health = manager._get_multi_account_health()
        assert health.status == "error"
        assert health.active_accounts == 2

    async def test_reload_accounts_triggers_rescan(self) -> None:
        """reload_accounts should call _sync_accounts and return summary."""
        process_config = self._make_process_config()
        db_pool = self._make_mock_pool()
        manager = GmailConnectorManager(
            process_config=process_config,
            db_pool=db_pool,
            cursor_pool=None,
        )

        with patch.object(
            manager,
            "_sync_accounts",
            new=AsyncMock(return_value=(["new@example.com"], [], ["existing@example.com"])),
        ):
            result = await manager.reload_accounts()

        assert result["added"] == ["new@example.com"]
        assert result["removed"] == []
        assert result["unchanged"] == ["existing@example.com"]


class TestGmailAccountLoop:
    """Tests for GmailAccountLoop per-account wrapper."""

    def _make_config(self) -> GmailConnectorConfig:
        return GmailConnectorConfig(
            switchboard_mcp_url="http://localhost:40100/sse",
            connector_endpoint_identity="gmail:user:test@example.com",
            gmail_client_id="cid",
            gmail_client_secret="csec",
            gmail_refresh_token="rtoken",
            gmail_poll_interval_s=5,
        )

    def test_loop_tracks_endpoint_identity(self) -> None:
        """GmailAccountLoop should set endpoint_identity from config."""
        config = self._make_config()
        loop = GmailAccountLoop(
            email="test@example.com",
            config=config,
            cursor_pool=None,
        )
        assert loop.endpoint_identity == "gmail:user:test@example.com"
        assert loop.email == "test@example.com"

    def test_loop_health_returns_account_status(self) -> None:
        """get_health() should return AccountHealthStatus with correct fields."""
        config = self._make_config()
        loop = GmailAccountLoop(
            email="test@example.com",
            config=config,
            cursor_pool=None,
        )
        health = loop.get_health()
        assert isinstance(health, AccountHealthStatus)
        # Email is redacted in health responses to avoid PII leakage
        assert health.email == "te***@example.com"
        assert health.endpoint_identity == "gmail:user:test@example.com"
        assert health.source_api_connectivity == "unknown"

    async def test_loop_stop_cancels_task(self) -> None:
        """stop() should cancel the running task."""
        config = self._make_config()
        mock_runtime = MagicMock()
        mock_runtime.start = AsyncMock(side_effect=asyncio.CancelledError)
        mock_runtime.stop = AsyncMock()
        mock_runtime._start_time = 0.0
        mock_runtime._last_checkpoint_save = None
        mock_runtime._last_ingest_submit = None
        mock_runtime._source_api_ok = None

        loop = GmailAccountLoop(
            email="test@example.com",
            config=config,
            cursor_pool=None,
        )
        loop._runtime = mock_runtime

        loop.start()
        # Give event loop a tick
        await asyncio.sleep(0)
        await loop.stop()

        mock_runtime.stop.assert_awaited_once()


class TestRunGmailConnectorIdentityResolution:
    """Tests for per-account endpoint_identity derivation in GmailProcessConfig."""

    def test_endpoint_identity_derived_from_email(self) -> None:
        """Per-account endpoint_identity should be gmail:user:<email>."""
        process_config = GmailProcessConfig(
            switchboard_mcp_url="http://localhost:40100/sse",
        )
        config = process_config.make_account_config(
            email="alice@example.com",
            client_id="cid",
            client_secret="csec",
            refresh_token="rtoken",
        )
        assert config.connector_endpoint_identity == "gmail:user:alice@example.com"

    def test_multiple_accounts_have_independent_identities(self) -> None:
        """Each account should have a distinct endpoint_identity."""
        process_config = GmailProcessConfig(
            switchboard_mcp_url="http://localhost:40100/sse",
        )
        config1 = process_config.make_account_config(
            email="personal@example.com",
            client_id="cid",
            client_secret="csec",
            refresh_token="rtoken1",
        )
        config2 = process_config.make_account_config(
            email="work@example.com",
            client_id="cid",
            client_secret="csec",
            refresh_token="rtoken2",
        )
        assert config1.connector_endpoint_identity != config2.connector_endpoint_identity
        assert config1.connector_endpoint_identity == "gmail:user:personal@example.com"
        assert config2.connector_endpoint_identity == "gmail:user:work@example.com"


class TestGmailPortConflictFix:
    """Regression tests: per-account runtimes must not bind the manager health port."""

    def test_make_account_config_uses_port_zero(self) -> None:
        """make_account_config must set connector_health_port=0 for per-account runtimes.

        Each GmailAccountLoop wraps a GmailConnectorRuntime that starts its own
        health server. If all accounts inherit the manager's port, every runtime
        after the first silently fails to bind — a port conflict.
        Port 0 lets the OS assign an ephemeral port per runtime.
        """
        process_config = GmailProcessConfig(
            switchboard_mcp_url="http://localhost:40100/sse",
            connector_health_port=40082,
        )
        account_config = process_config.make_account_config(
            email="alice@example.com",
            client_id="cid",
            client_secret="csec",
            refresh_token="rtoken",
        )
        # Per-account runtime MUST NOT inherit the manager's port.
        assert account_config.connector_health_port == 0, (
            "Per-account GmailConnectorRuntime must use port=0, not the manager health port, "
            "to avoid bind conflicts when multiple accounts run simultaneously."
        )

    def test_multiple_account_configs_do_not_share_port(self) -> None:
        """Two configs produced by make_account_config should both use port 0."""
        process_config = GmailProcessConfig(
            switchboard_mcp_url="http://localhost:40100/sse",
            connector_health_port=40082,
        )
        cfg1 = process_config.make_account_config(
            email="alice@example.com", client_id="cid", client_secret="csec", refresh_token="rt1"
        )
        cfg2 = process_config.make_account_config(
            email="bob@example.com", client_id="cid", client_secret="csec", refresh_token="rt2"
        )
        assert cfg1.connector_health_port == 0
        assert cfg2.connector_health_port == 0


class TestGmailReloadEventThreadSafety:
    """Regression tests: /reload endpoint must use call_soon_threadsafe."""

    def test_manager_stores_main_loop_reference(self) -> None:
        """GmailConnectorManager must initialise _main_loop to None before start()."""
        process_config = GmailProcessConfig(
            switchboard_mcp_url="http://localhost:40100/sse",
        )
        db_pool = MagicMock()
        manager = GmailConnectorManager(
            process_config=process_config, db_pool=db_pool, cursor_pool=None
        )
        # Before start(), _main_loop should be None (no loop captured yet)
        assert manager._main_loop is None

    async def test_start_captures_running_loop(self) -> None:
        """start() must capture the running event loop into _main_loop."""
        process_config = GmailProcessConfig(
            switchboard_mcp_url="http://localhost:40100/sse",
            gmail_account_rescan_interval_s=9999,
        )
        db_pool = MagicMock()
        manager = GmailConnectorManager(
            process_config=process_config, db_pool=db_pool, cursor_pool=None
        )

        with (
            patch.object(manager, "_start_health_server", return_value=None),
            patch.object(manager, "_setup_sighup", return_value=None),
            patch.object(
                manager,
                "_sync_accounts",
                new=AsyncMock(return_value=([], [], [])),
            ),
            patch(
                "butlers.connectors.gmail.wait_for_switchboard_ready",
                new=AsyncMock(return_value=None),
            ),
            patch.object(
                manager,
                "_run_rescan_loop",
                new=AsyncMock(return_value=None),
            ),
            patch.object(manager, "stop", new=AsyncMock(return_value=None)),
        ):
            await manager.start()

        # _main_loop should now be set to the running loop
        assert manager._main_loop is asyncio.get_running_loop()


class TestGmailPubsubWebhookTokenResolution:
    """Regression tests: _resolve_credentials_for_account must include pubsub_webhook_token."""

    async def test_pubsub_token_included_when_available(self) -> None:
        """When the credential store holds GMAIL_PUBSUB_WEBHOOK_TOKEN, it must be returned."""
        from butlers.google_credentials import GoogleCredentials

        process_config = GmailProcessConfig(
            switchboard_mcp_url="http://localhost:40100/sse",
        )
        db_pool = MagicMock()
        manager = GmailConnectorManager(
            process_config=process_config, db_pool=db_pool, cursor_pool=None
        )

        mock_creds = GoogleCredentials(
            client_id="cid", client_secret="csec", refresh_token="rtoken"
        )
        mock_store = AsyncMock()
        mock_store.resolve = AsyncMock(return_value="webhook-secret")

        with (
            patch.object(manager, "_get_credential_store", return_value=mock_store),
            patch(
                "butlers.connectors.gmail.load_google_credentials",
                new=AsyncMock(return_value=mock_creds),
            ),
        ):
            result = await manager._resolve_credentials_for_account("alice@example.com")

        assert result is not None
        assert result["client_id"] == "cid"
        assert result["pubsub_webhook_token"] == "webhook-secret"

    async def test_pubsub_token_absent_when_not_stored(self) -> None:
        """When GMAIL_PUBSUB_WEBHOOK_TOKEN is not in DB, key must be absent (not None)."""
        from butlers.google_credentials import GoogleCredentials

        process_config = GmailProcessConfig(
            switchboard_mcp_url="http://localhost:40100/sse",
        )
        db_pool = MagicMock()
        manager = GmailConnectorManager(
            process_config=process_config, db_pool=db_pool, cursor_pool=None
        )

        mock_creds = GoogleCredentials(
            client_id="cid", client_secret="csec", refresh_token="rtoken"
        )
        mock_store = AsyncMock()
        mock_store.resolve = AsyncMock(return_value=None)  # not stored

        with (
            patch.object(manager, "_get_credential_store", return_value=mock_store),
            patch(
                "butlers.connectors.gmail.load_google_credentials",
                new=AsyncMock(return_value=mock_creds),
            ),
        ):
            result = await manager._resolve_credentials_for_account("alice@example.com")

        assert result is not None
        assert "pubsub_webhook_token" not in result

    async def test_pubsub_token_propagated_to_make_account_config(self) -> None:
        """pubsub_webhook_token from _resolve_credentials_for_account must reach account config."""
        process_config = GmailProcessConfig(
            switchboard_mcp_url="http://localhost:40100/sse",
        )
        db_pool = MagicMock()
        manager = GmailConnectorManager(
            process_config=process_config, db_pool=db_pool, cursor_pool=None
        )

        mock_creds = {
            "client_id": "cid",
            "client_secret": "csec",
            "refresh_token": "rtoken",
            "pubsub_webhook_token": "per-account-token",
        }
        mock_loop = MagicMock()
        mock_loop.start = MagicMock()
        mock_loop.is_running = True

        with (
            patch.object(
                manager,
                "_discover_qualifying_accounts",
                new=AsyncMock(return_value=[("alice@example.com", None)]),
            ),
            patch.object(
                manager,
                "_resolve_credentials_for_account",
                new=AsyncMock(return_value=mock_creds),
            ),
            patch("butlers.connectors.gmail.GmailAccountLoop", return_value=mock_loop) as loop_ctor,
        ):
            await manager._sync_accounts()

        # Verify GmailAccountLoop was constructed with a config carrying the token
        call_kwargs = loop_ctor.call_args[1]
        account_cfg = call_kwargs["config"]
        assert account_cfg.gmail_pubsub_webhook_token == "per-account-token"


class TestRedactEmail:
    """Tests for _redact_email() PII-protection helper."""

    def test_standard_email(self) -> None:
        """Normal email: first 2 chars of local part visible, rest replaced by ***."""
        assert _redact_email("test@gmail.com") == "te***@gmail.com"

    def test_long_local_part(self) -> None:
        """Long local parts show only first 2 chars."""
        assert _redact_email("alice@example.com") == "al***@example.com"

    def test_single_char_local_part(self) -> None:
        """Single-char local part: show that 1 char, then ***."""
        assert _redact_email("a@example.com") == "a***@example.com"

    def test_two_char_local_part(self) -> None:
        """Exactly-2-char local part: show both chars, then ***."""
        assert _redact_email("ab@example.com") == "ab***@example.com"

    def test_none_input(self) -> None:
        """None email passes through as None."""
        assert _redact_email(None) is None

    def test_no_at_sign(self) -> None:
        """Malformed email with no @ returns fully masked string."""
        assert _redact_email("notanemail") == "***"

    def test_at_at_start(self) -> None:
        """Email starting with @ (empty local part) returns fully masked string."""
        assert _redact_email("@example.com") == "***"

    def test_domain_preserved(self) -> None:
        """Full domain is always preserved unmodified."""
        result = _redact_email("user@sub.domain.org")
        assert result is not None
        assert result.endswith("@sub.domain.org")

    def test_subdomain_email(self) -> None:
        """Subdomain email addresses are handled correctly."""
        assert _redact_email("bo@work.example.co.uk") == "bo***@work.example.co.uk"

    def test_health_response_redacts_emails(self) -> None:
        """GmailAccountLoop.get_health() should return redacted email in AccountHealthStatus."""
        config = GmailConnectorConfig(
            switchboard_mcp_url="http://localhost:40100/sse",
            connector_endpoint_identity="gmail:user:alice@example.com",
            gmail_client_id="cid",
            gmail_client_secret="csec",
            gmail_refresh_token="rtoken",
            gmail_poll_interval_s=5,
        )
        loop = GmailAccountLoop(
            email="alice@example.com",
            config=config,
            cursor_pool=None,
        )

        status = loop.get_health()

        # Email must be redacted — raw address must NOT appear
        assert status.email != "alice@example.com"
        assert status.email == "al***@example.com"
        # Domain is still visible so operators can identify the account family
        assert "@example.com" in (status.email or "")
