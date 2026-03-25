"""Tests for Google Calendar connector.

Covers tasks 2.1-2.4 from openspec/changes/connector-google-calendar/tasks.md:
- 2.1 Base connector scaffolding (config, env loading, main entry point)
- 2.2 Multi-account discovery from shared.google_accounts (calendar scope)
- 2.3 Per-account OAuth credential resolution
- 2.4 Google Calendar API client with token refresh and rate-limit retry

Also covers:
- Event change classification (created/updated/deleted)
- ingest.v1 envelope normalization
- syncToken cursor lifecycle
- Starting-soon notification dedup
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from butlers.connectors.google_calendar import (
    GoogleCalendarAccountConfig,
    GoogleCalendarAccountLoop,
    GoogleCalendarAccountRuntime,
    GoogleCalendarClient,
    GoogleCalendarConnectorManager,
    GoogleCalendarCursor,
    GoogleCalendarProcessConfig,
    _build_normalized_text,
    _format_google_error,
    _get_organizer_email,
    _has_calendar_scope,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def process_config() -> GoogleCalendarProcessConfig:
    return GoogleCalendarProcessConfig(
        switchboard_mcp_url="http://localhost:41100/sse",
        connector_provider="google_calendar",
        connector_channel="google_calendar",
        connector_max_inflight=4,
        connector_health_port=0,
        gcal_poll_interval_s=5,
        gcal_starting_soon_lead_minutes=15,
        gcal_account_rescan_interval_s=300,
    )


@pytest.fixture
def account_config() -> GoogleCalendarAccountConfig:
    return GoogleCalendarAccountConfig(
        switchboard_mcp_url="http://localhost:41100/sse",
        connector_provider="google_calendar",
        connector_channel="google_calendar",
        connector_endpoint_identity="google_calendar:user:test@example.com",
        connector_max_inflight=4,
        client_id="test-client-id",
        client_secret="test-client-secret",
        refresh_token="test-refresh-token",
        user_email="test@example.com",
        gcal_poll_interval_s=5,
        gcal_starting_soon_lead_minutes=15,
    )


@pytest.fixture
def mock_cursor_pool() -> MagicMock:
    return MagicMock()


@pytest.fixture
def mock_shared_pool() -> MagicMock:
    return MagicMock()


@pytest.fixture
def account_runtime(
    account_config: GoogleCalendarAccountConfig,
    mock_cursor_pool: MagicMock,
    mock_shared_pool: MagicMock,
) -> GoogleCalendarAccountRuntime:
    return GoogleCalendarAccountRuntime(account_config, mock_cursor_pool, mock_shared_pool)


# ---------------------------------------------------------------------------
# Task 2.1 — Base scaffolding and config
# ---------------------------------------------------------------------------


class TestGoogleCalendarProcessConfig:
    """Tests for GoogleCalendarProcessConfig (task 2.1)."""

    def test_from_env_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:41100/sse")
        monkeypatch.setenv("CONNECTOR_PROVIDER", "google_calendar")
        monkeypatch.setenv("CONNECTOR_CHANNEL", "google_calendar")
        monkeypatch.setenv("GCAL_POLL_INTERVAL_S", "30")
        monkeypatch.setenv("GCAL_STARTING_SOON_LEAD_MINUTES", "10")
        monkeypatch.setenv("GCAL_ACCOUNT_RESCAN_INTERVAL_S", "120")
        monkeypatch.setenv("CONNECTOR_MAX_INFLIGHT", "4")
        monkeypatch.setenv("CONNECTOR_HEALTH_PORT", "40084")

        config = GoogleCalendarProcessConfig.from_env()

        assert config.switchboard_mcp_url == "http://localhost:41100/sse"
        assert config.connector_provider == "google_calendar"
        assert config.connector_channel == "google_calendar"
        assert config.gcal_poll_interval_s == 30
        assert config.gcal_starting_soon_lead_minutes == 10
        assert config.gcal_account_rescan_interval_s == 120
        assert config.connector_max_inflight == 4
        assert config.connector_health_port == 40084

    def test_from_env_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:41100/sse")
        monkeypatch.delenv("GCAL_POLL_INTERVAL_S", raising=False)
        monkeypatch.delenv("GCAL_STARTING_SOON_LEAD_MINUTES", raising=False)
        monkeypatch.delenv("GCAL_ACCOUNT_RESCAN_INTERVAL_S", raising=False)

        config = GoogleCalendarProcessConfig.from_env()

        assert config.gcal_poll_interval_s == 60
        assert config.gcal_starting_soon_lead_minutes == 15
        assert config.gcal_account_rescan_interval_s == 300

    def test_from_env_missing_required(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SWITCHBOARD_MCP_URL", raising=False)
        with pytest.raises(KeyError):
            GoogleCalendarProcessConfig.from_env()

    def test_from_env_invalid_integer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:41100/sse")
        monkeypatch.setenv("GCAL_POLL_INTERVAL_S", "not-a-number")
        with pytest.raises(ValueError, match="GCAL_POLL_INTERVAL_S must be an integer"):
            GoogleCalendarProcessConfig.from_env()

    def test_make_account_config_defaults(
        self, process_config: GoogleCalendarProcessConfig
    ) -> None:
        cfg = process_config.make_account_config(
            email="work@example.com",
            client_id="cid",
            client_secret="csec",
            refresh_token="rtok",
        )
        assert cfg.connector_endpoint_identity == "google_calendar:user:work@example.com"
        assert cfg.user_email == "work@example.com"
        assert cfg.gcal_poll_interval_s == process_config.gcal_poll_interval_s
        assert cfg.gcal_starting_soon_lead_minutes == process_config.gcal_starting_soon_lead_minutes
        assert cfg.calendar_ids is None

    def test_make_account_config_overrides(
        self, process_config: GoogleCalendarProcessConfig
    ) -> None:
        md = {
            "poll_interval_s": 120,
            "starting_soon_lead_minutes": 30,
            "calendar_ids": ["primary", "family@group.calendar.google.com"],
        }
        cfg = process_config.make_account_config(
            email="work@example.com",
            client_id="cid",
            client_secret="csec",
            refresh_token="rtok",
            metadata_calendar=md,
        )
        assert cfg.gcal_poll_interval_s == 120
        assert cfg.gcal_starting_soon_lead_minutes == 30
        assert cfg.calendar_ids == ["primary", "family@group.calendar.google.com"]


class TestGoogleCalendarAccountConfig:
    """Tests for GoogleCalendarAccountConfig (task 2.1)."""

    def test_valid_config(self) -> None:
        cfg = GoogleCalendarAccountConfig(
            switchboard_mcp_url="http://localhost:41100/sse",
            connector_endpoint_identity="google_calendar:user:a@b.com",
            client_id="cid",
            client_secret="csec",
            refresh_token="rtok",
            user_email="a@b.com",
        )
        assert cfg.connector_provider == "google_calendar"
        assert cfg.connector_channel == "google_calendar"

    def test_endpoint_identity_format(self, process_config: GoogleCalendarProcessConfig) -> None:
        cfg = process_config.make_account_config(
            email="alice@gmail.com",
            client_id="cid",
            client_secret="csec",
            refresh_token="rtok",
        )
        assert cfg.connector_endpoint_identity == "google_calendar:user:alice@gmail.com"


# ---------------------------------------------------------------------------
# Task 2.2 — Multi-account discovery
# ---------------------------------------------------------------------------


class TestMultiAccountDiscovery:
    """Tests for account discovery from shared.google_accounts (task 2.2)."""

    def test_has_calendar_scope_full_url(self) -> None:
        assert _has_calendar_scope(["https://www.googleapis.com/auth/calendar"])

    def test_has_calendar_scope_readonly_url(self) -> None:
        assert _has_calendar_scope(["https://www.googleapis.com/auth/calendar.readonly"])

    def test_has_calendar_scope_events_url(self) -> None:
        assert _has_calendar_scope(["https://www.googleapis.com/auth/calendar.events"])

    def test_has_calendar_scope_short_form(self) -> None:
        assert _has_calendar_scope(["calendar"])

    def test_has_calendar_scope_mixed(self) -> None:
        """Account with some other scopes plus calendar is still qualifying."""
        assert _has_calendar_scope(
            [
                "https://www.googleapis.com/auth/gmail.modify",
                "https://www.googleapis.com/auth/calendar.readonly",
            ]
        )

    def test_has_calendar_scope_no_calendar(self) -> None:
        assert not _has_calendar_scope(["https://www.googleapis.com/auth/gmail.modify"])

    def test_has_calendar_scope_empty(self) -> None:
        assert not _has_calendar_scope([])

    async def test_discover_qualifying_accounts_filters_by_scope(
        self,
        process_config: GoogleCalendarProcessConfig,
        mock_cursor_pool: MagicMock,
    ) -> None:
        """Accounts without calendar scope are skipped."""
        rows = [
            {
                "email": "alice@example.com",
                "granted_scopes": ["https://www.googleapis.com/auth/calendar"],
                "metadata": {},
            },
            {
                "email": "bob@example.com",
                "granted_scopes": ["https://www.googleapis.com/auth/gmail.modify"],
                "metadata": {},
            },
        ]

        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=rows)
        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

        manager = GoogleCalendarConnectorManager(process_config, mock_pool, mock_cursor_pool)
        qualifying = await manager._discover_qualifying_accounts()

        assert len(qualifying) == 1
        assert qualifying[0][0] == "alice@example.com"

    async def test_discover_qualifying_accounts_db_error_returns_empty(
        self,
        process_config: GoogleCalendarProcessConfig,
        mock_cursor_pool: MagicMock,
    ) -> None:
        """DB errors during discovery return empty list (non-fatal)."""
        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(
            side_effect=Exception("DB connection refused")
        )
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

        manager = GoogleCalendarConnectorManager(process_config, mock_pool, mock_cursor_pool)
        qualifying = await manager._discover_qualifying_accounts()

        assert qualifying == []

    async def test_discover_qualifying_accounts_extracts_metadata_calendar(
        self,
        process_config: GoogleCalendarProcessConfig,
        mock_cursor_pool: MagicMock,
    ) -> None:
        """metadata.calendar section is extracted and returned."""
        rows = [
            {
                "email": "alice@example.com",
                "granted_scopes": ["https://www.googleapis.com/auth/calendar"],
                "metadata": {"calendar": {"poll_interval_s": 120}},
            },
        ]

        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=rows)
        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

        manager = GoogleCalendarConnectorManager(process_config, mock_pool, mock_cursor_pool)
        qualifying = await manager._discover_qualifying_accounts()

        assert len(qualifying) == 1
        email, md = qualifying[0]
        assert email == "alice@example.com"
        assert md == {"poll_interval_s": 120}

    async def test_no_qualifying_accounts_starts_in_degraded_mode(
        self,
        process_config: GoogleCalendarProcessConfig,
        mock_cursor_pool: MagicMock,
    ) -> None:
        """Manager starts with empty loops when no qualifying accounts exist."""
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])
        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

        manager = GoogleCalendarConnectorManager(process_config, mock_pool, mock_cursor_pool)
        qualifying = await manager._discover_qualifying_accounts()

        assert qualifying == []
        health = manager._get_multi_account_health()
        assert health.active_accounts == 0
        assert health.status == "degraded"


# ---------------------------------------------------------------------------
# Task 2.3 — Per-account OAuth credential resolution
# ---------------------------------------------------------------------------


class TestCredentialResolution:
    """Tests for per-account OAuth credential resolution (task 2.3)."""

    async def test_resolve_credentials_returns_none_when_not_found(
        self,
        process_config: GoogleCalendarProcessConfig,
        mock_cursor_pool: MagicMock,
    ) -> None:
        """Returns None when no credentials are stored for the account."""
        mock_pool = MagicMock()

        manager = GoogleCalendarConnectorManager(process_config, mock_pool, mock_cursor_pool)

        with patch(
            "butlers.connectors.google_calendar.load_google_credentials",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await manager._resolve_credentials_for_account("nobody@example.com")

        assert result is None

    async def test_resolve_credentials_returns_dict_on_success(
        self,
        process_config: GoogleCalendarProcessConfig,
        mock_cursor_pool: MagicMock,
    ) -> None:
        """Returns credentials dict when credentials are successfully resolved."""
        from butlers.google_credentials import GoogleCredentials

        mock_creds = GoogleCredentials(
            client_id="cid",
            client_secret="csec",
            refresh_token="rtok",
        )

        mock_pool = MagicMock()
        manager = GoogleCalendarConnectorManager(process_config, mock_pool, mock_cursor_pool)

        with patch(
            "butlers.connectors.google_calendar.load_google_credentials",
            new_callable=AsyncMock,
            return_value=mock_creds,
        ):
            result = await manager._resolve_credentials_for_account("alice@example.com")

        assert result is not None
        assert result["client_id"] == "cid"
        assert result["client_secret"] == "csec"
        assert result["refresh_token"] == "rtok"

    async def test_resolve_credentials_returns_none_on_invalid_credentials(
        self,
        process_config: GoogleCalendarProcessConfig,
        mock_cursor_pool: MagicMock,
    ) -> None:
        """Returns None when stored credentials are invalid (non-fatal)."""
        from butlers.google_credentials import InvalidGoogleCredentialsError

        mock_pool = MagicMock()
        manager = GoogleCalendarConnectorManager(process_config, mock_pool, mock_cursor_pool)

        with patch(
            "butlers.connectors.google_calendar.load_google_credentials",
            new_callable=AsyncMock,
            side_effect=InvalidGoogleCredentialsError("malformed credentials"),
        ):
            result = await manager._resolve_credentials_for_account("alice@example.com")

        assert result is None

    async def test_resolve_credentials_returns_none_on_generic_error(
        self,
        process_config: GoogleCalendarProcessConfig,
        mock_cursor_pool: MagicMock,
    ) -> None:
        """Returns None on unexpected errors (non-fatal — account is skipped)."""
        mock_pool = MagicMock()
        manager = GoogleCalendarConnectorManager(process_config, mock_pool, mock_cursor_pool)

        with patch(
            "butlers.connectors.google_calendar.load_google_credentials",
            new_callable=AsyncMock,
            side_effect=RuntimeError("network error"),
        ):
            result = await manager._resolve_credentials_for_account("alice@example.com")

        assert result is None


# ---------------------------------------------------------------------------
# Task 2.4 — Google Calendar API client (token refresh + rate-limit retry)
# ---------------------------------------------------------------------------


class TestGoogleCalendarClient:
    """Tests for GoogleCalendarClient (task 2.4)."""

    @pytest.fixture
    def mock_metrics(self) -> MagicMock:
        m = MagicMock()
        m.record_source_api_call = MagicMock()
        m.record_error = MagicMock()
        return m

    @pytest.fixture
    def client(
        self,
        account_config: GoogleCalendarAccountConfig,
        mock_metrics: MagicMock,
    ) -> GoogleCalendarClient:
        return GoogleCalendarClient(account_config, mock_metrics)

    async def test_get_access_token_fresh_token(
        self,
        client: GoogleCalendarClient,
        mock_metrics: MagicMock,
    ) -> None:
        """Returns cached token when still valid."""
        client._access_token = "valid-token"
        client._token_expires_at = datetime.now(UTC) + timedelta(hours=1)
        # No HTTP client needed — cached token should be returned
        token = await client.get_access_token()
        assert token == "valid-token"
        mock_metrics.record_source_api_call.assert_not_called()

    async def test_get_access_token_refreshes_when_expired(
        self,
        client: GoogleCalendarClient,
        mock_metrics: MagicMock,
    ) -> None:
        """Refreshes token when expired."""
        client._access_token = "old-token"
        client._token_expires_at = datetime.now(UTC) - timedelta(seconds=1)

        mock_response = MagicMock()
        mock_response.is_error = False
        mock_response.json.return_value = {
            "access_token": "new-token",
            "expires_in": 3600,
        }
        mock_response.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_response)
        client._http_client = mock_http

        token = await client.get_access_token()
        assert token == "new-token"
        assert client._access_token == "new-token"
        mock_metrics.record_source_api_call.assert_called_once_with(
            api_method="token_refresh", status="success"
        )

    async def test_get_access_token_error_marks_api_disconnected(
        self,
        client: GoogleCalendarClient,
        mock_metrics: MagicMock,
    ) -> None:
        """Marks API as disconnected when token refresh fails."""
        client._token_expires_at = None  # Force refresh

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
        client._http_client = mock_http

        with pytest.raises(httpx.ConnectError):
            await client.get_access_token()

        assert client._source_api_ok is False
        mock_metrics.record_source_api_call.assert_called_once_with(
            api_method="token_refresh", status="error"
        )

    async def test_request_with_retry_429_retries(
        self,
        client: GoogleCalendarClient,
        mock_metrics: MagicMock,
    ) -> None:
        """Retries on 429 rate limit with exponential backoff."""
        # Token is valid
        client._access_token = "valid-token"
        client._token_expires_at = datetime.now(UTC) + timedelta(hours=1)

        rate_limit_response = MagicMock()
        rate_limit_response.status_code = 429
        rate_limit_response.headers = {}

        success_response = MagicMock()
        success_response.status_code = 200
        success_response.is_error = False
        success_response.json.return_value = {"items": [], "nextSyncToken": "tok123"}

        mock_http = AsyncMock()
        mock_http.request = AsyncMock(side_effect=[rate_limit_response, success_response])
        client._http_client = mock_http

        with patch("asyncio.sleep", new_callable=AsyncMock):
            response = await client._request_with_retry("GET", "https://example.com")

        assert response.status_code == 200
        assert mock_http.request.call_count == 2

    async def test_request_with_retry_503_retries(
        self,
        client: GoogleCalendarClient,
        mock_metrics: MagicMock,
    ) -> None:
        """Retries on 503 service unavailable."""
        client._access_token = "valid-token"
        client._token_expires_at = datetime.now(UTC) + timedelta(hours=1)

        unavailable_response = MagicMock()
        unavailable_response.status_code = 503
        unavailable_response.headers = {}

        success_response = MagicMock()
        success_response.status_code = 200
        success_response.is_error = False

        mock_http = AsyncMock()
        mock_http.request = AsyncMock(side_effect=[unavailable_response, success_response])
        client._http_client = mock_http

        with patch("asyncio.sleep", new_callable=AsyncMock):
            response = await client._request_with_retry("GET", "https://example.com")

        assert response.status_code == 200

    async def test_request_with_retry_transport_error_retries(
        self,
        client: GoogleCalendarClient,
        mock_metrics: MagicMock,
    ) -> None:
        """Retries on transport errors with exponential backoff."""
        client._access_token = "valid-token"
        client._token_expires_at = datetime.now(UTC) + timedelta(hours=1)

        success_response = MagicMock()
        success_response.status_code = 200
        success_response.is_error = False

        mock_http = AsyncMock()
        mock_http.request = AsyncMock(
            side_effect=[
                httpx.TransportError("connection reset"),
                success_response,
            ]
        )
        client._http_client = mock_http

        with patch("asyncio.sleep", new_callable=AsyncMock):
            response = await client._request_with_retry("GET", "https://example.com")

        assert response.status_code == 200

    async def test_list_events_incremental_passes_sync_token(
        self,
        client: GoogleCalendarClient,
        mock_metrics: MagicMock,
    ) -> None:
        """events.list passes syncToken for incremental sync."""
        client._access_token = "valid-token"
        client._token_expires_at = datetime.now(UTC) + timedelta(hours=1)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.is_error = False
        mock_response.json.return_value = {"items": [], "nextSyncToken": "new-token"}
        mock_response.raise_for_status = MagicMock()

        with patch.object(client, "_request_with_retry", return_value=mock_response):
            result = await client.list_events("primary", sync_token="old-token")

        assert result["nextSyncToken"] == "new-token"

    async def test_list_events_full_sync_no_sync_token(
        self,
        client: GoogleCalendarClient,
        mock_metrics: MagicMock,
    ) -> None:
        """events.list for full sync does not pass syncToken."""
        client._access_token = "valid-token"
        client._token_expires_at = datetime.now(UTC) + timedelta(hours=1)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.is_error = False
        mock_response.json.return_value = {"items": [], "nextSyncToken": "first-token"}
        mock_response.raise_for_status = MagicMock()

        with patch.object(client, "_request_with_retry", return_value=mock_response) as mock_req:
            await client.list_events("primary")

        # Verify no syncToken in params
        call_kwargs = mock_req.call_args[1]
        params = call_kwargs.get("params", {})
        assert "syncToken" not in params


# ---------------------------------------------------------------------------
# Envelope normalization helpers
# ---------------------------------------------------------------------------


class TestEnvelopeNormalization:
    """Tests for ingest.v1 envelope construction helpers."""

    def test_build_normalized_text_event_created(self) -> None:
        event: dict[str, Any] = {
            "summary": "Team Meeting",
            "start": {"dateTime": "2026-03-25T10:00:00+00:00"},
            "end": {"dateTime": "2026-03-25T11:00:00+00:00"},
            "organizer": {"email": "alice@example.com"},
            "attendees": [{}, {}, {}],
        }
        text = _build_normalized_text("event_created", event)
        assert "[Calendar: event_created]" in text
        assert "Team Meeting" in text
        assert "3 attendees" in text
        assert "alice@example.com" in text

    def test_build_normalized_text_with_location(self) -> None:
        event: dict[str, Any] = {
            "summary": "All Hands",
            "start": {"dateTime": "2026-03-25T09:00:00+00:00"},
            "end": {"dateTime": "2026-03-25T10:00:00+00:00"},
            "location": "Conference Room A",
            "organizer": {"email": "boss@example.com"},
        }
        text = _build_normalized_text("event_updated", event)
        assert "Conference Room A" in text

    def test_build_normalized_text_no_title(self) -> None:
        event: dict[str, Any] = {
            "start": {"date": "2026-03-25"},
            "end": {"date": "2026-03-26"},
        }
        text = _build_normalized_text("event_deleted", event)
        assert "(No title)" in text

    def test_build_normalized_text_starting_soon(self) -> None:
        event: dict[str, Any] = {
            "summary": "Standup",
            "start": {"dateTime": "2026-03-25T09:00:00+00:00"},
            "end": {"dateTime": "2026-03-25T09:15:00+00:00"},
            "organizer": {"email": "dev@example.com"},
        }
        text = _build_normalized_text("starting_soon", event)
        assert "[Calendar: starting_soon]" in text
        assert "Standup" in text

    def test_get_organizer_email_from_event(self) -> None:
        event: dict[str, Any] = {"organizer": {"email": "ORG@EXAMPLE.COM"}}
        result = _get_organizer_email(event, "fallback@example.com")
        assert result == "org@example.com"

    def test_get_organizer_email_fallback(self) -> None:
        event: dict[str, Any] = {}
        result = _get_organizer_email(event, "account@example.com")
        assert result == "account@example.com"

    def test_format_google_error_nested(self) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "error": {
                "code": 403,
                "status": "FORBIDDEN",
                "message": "Access denied",
            }
        }
        result = _format_google_error(mock_response)
        assert result is not None
        assert "code=403" in result
        assert "FORBIDDEN" in result

    def test_format_google_error_oauth(self) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "error": "invalid_grant",
            "error_description": "Token has been expired or revoked.",
        }
        result = _format_google_error(mock_response)
        assert result is not None
        assert "invalid_grant" in result

    def test_format_google_error_invalid_json(self) -> None:
        mock_response = MagicMock()
        mock_response.json.side_effect = ValueError("not json")
        result = _format_google_error(mock_response)
        assert result is None


# ---------------------------------------------------------------------------
# syncToken cursor lifecycle
# ---------------------------------------------------------------------------


class TestSyncTokenCursorLifecycle:
    """Tests for syncToken cursor persistence and lifecycle."""

    async def test_save_cursor_calls_cursor_store(
        self,
        account_runtime: GoogleCalendarAccountRuntime,
    ) -> None:
        """_save_cursor serializes and stores the cursor via cursor_store."""
        with patch(
            "butlers.connectors.google_calendar.save_cursor",
            new_callable=AsyncMock,
        ) as mock_save:
            await account_runtime._save_cursor(
                "sync-token-abc",
                cursor_key="google_calendar:user:test@example.com:primary",
            )

        mock_save.assert_called_once()
        call_args = mock_save.call_args
        # Verify connector type and cursor key (per-account:per-calendar)
        assert call_args[0][1] == "google_calendar"
        assert call_args[0][2] == "google_calendar:user:test@example.com:primary"
        # Verify cursor JSON contains the sync token
        cursor_json = call_args[0][3]
        cursor = GoogleCalendarCursor.model_validate_json(cursor_json)
        assert cursor.sync_token == "sync-token-abc"

    async def test_save_cursor_skipped_when_no_pool(
        self,
        account_config: GoogleCalendarAccountConfig,
    ) -> None:
        """_save_cursor is a no-op when no cursor_pool is configured."""
        runtime = GoogleCalendarAccountRuntime(account_config, None, None)

        with patch(
            "butlers.connectors.google_calendar.save_cursor",
            new_callable=AsyncMock,
        ) as mock_save:
            await runtime._save_cursor(
                "sync-token-abc",
                cursor_key="google_calendar:user:test@example.com:primary",
            )

        mock_save.assert_not_called()

    async def test_full_sync_saves_cursor_no_ingestion(
        self,
        account_runtime: GoogleCalendarAccountRuntime,
    ) -> None:
        """Initial full sync saves cursor but does NOT submit events."""
        items = [
            {"id": "event1", "status": "confirmed", "updated": "2026-03-25T00:00:00Z"},
        ]
        mock_response = {
            "items": items,
            "nextSyncToken": "baseline-token",
        }

        account_runtime._client = MagicMock()
        account_runtime._client.list_events = AsyncMock(return_value=mock_response)

        with patch.object(account_runtime, "_save_cursor", new_callable=AsyncMock) as mock_save:
            with patch.object(
                account_runtime, "_process_event", new_callable=AsyncMock
            ) as mock_process:
                await account_runtime._full_sync(
                    "primary",
                    cursor_key="google_calendar:user:test@example.com:primary",
                    ingest_events=False,
                )

        mock_save.assert_called_once_with(
            "baseline-token", cursor_key="google_calendar:user:test@example.com:primary"
        )
        # Events should NOT be processed on initial baseline sync
        mock_process.assert_not_called()

    async def test_full_sync_ingests_events_on_recovery(
        self,
        account_runtime: GoogleCalendarAccountRuntime,
    ) -> None:
        """Recovery full sync (after 410) DOES ingest events."""
        items = [
            {"id": "event1", "status": "confirmed", "updated": "2026-03-25T00:00:00Z"},
        ]
        mock_response = {
            "items": items,
            "nextSyncToken": "recovery-token",
        }

        account_runtime._client = MagicMock()
        account_runtime._client.list_events = AsyncMock(return_value=mock_response)

        with patch.object(account_runtime, "_save_cursor", new_callable=AsyncMock):
            with patch.object(
                account_runtime, "_process_event", new_callable=AsyncMock
            ) as mock_process:
                await account_runtime._full_sync(
                    "primary",
                    cursor_key="google_calendar:user:test@example.com:primary",
                    ingest_events=True,
                )

        mock_process.assert_called_once_with(items[0], "primary")

    async def test_incremental_sync_advances_cursor_after_all_events(
        self,
        account_runtime: GoogleCalendarAccountRuntime,
    ) -> None:
        """Incremental sync advances cursor only after all events are processed."""
        mock_response = {
            "items": [{"id": "evt1", "status": "confirmed", "updated": "2026-03-25T00:00:00Z"}],
            "nextSyncToken": "new-token",
        }

        account_runtime._client = MagicMock()
        account_runtime._client.list_events = AsyncMock(return_value=mock_response)

        with patch.object(
            account_runtime, "_process_event", new_callable=AsyncMock
        ) as mock_process:
            with patch.object(account_runtime, "_save_cursor", new_callable=AsyncMock) as mock_save:
                with patch.object(account_runtime, "_check_starting_soon", new_callable=AsyncMock):
                    await account_runtime._incremental_sync(
                        "primary",
                        "old-token",
                        cursor_key="google_calendar:user:test@example.com:primary",
                    )

        mock_process.assert_called_once()
        mock_save.assert_called_once_with(
            "new-token", cursor_key="google_calendar:user:test@example.com:primary"
        )

    async def test_incremental_sync_handles_pagination(
        self,
        account_runtime: GoogleCalendarAccountRuntime,
    ) -> None:
        """Incremental sync paginates before advancing cursor."""
        page1 = {
            "items": [{"id": "evt1", "status": "confirmed"}],
            "nextPageToken": "page2token",
        }
        page2 = {
            "items": [{"id": "evt2", "status": "confirmed"}],
            "nextSyncToken": "final-token",
        }

        account_runtime._client = MagicMock()
        account_runtime._client.list_events = AsyncMock(side_effect=[page1, page2])

        with patch.object(
            account_runtime, "_process_event", new_callable=AsyncMock
        ) as mock_process:
            with patch.object(account_runtime, "_save_cursor", new_callable=AsyncMock) as mock_save:
                with patch.object(account_runtime, "_check_starting_soon", new_callable=AsyncMock):
                    await account_runtime._incremental_sync(
                        "primary",
                        "old-token",
                        cursor_key="google_calendar:user:test@example.com:primary",
                    )

        assert mock_process.call_count == 2
        mock_save.assert_called_once_with(
            "final-token", cursor_key="google_calendar:user:test@example.com:primary"
        )

    async def test_poll_calendar_falls_back_on_410(
        self,
        account_runtime: GoogleCalendarAccountRuntime,
    ) -> None:
        """On 410 Gone, _poll_calendar falls back to full sync with ingestion."""
        # Simulate a stored cursor
        account_runtime._cursor_pool = MagicMock()

        with patch(
            "butlers.connectors.google_calendar.load_cursor",
            new_callable=AsyncMock,
            return_value=GoogleCalendarCursor(
                sync_token="expired-token",
                last_updated_at="2026-01-01T00:00:00+00:00",
            ).model_dump_json(),
        ):
            with patch.object(
                account_runtime,
                "_incremental_sync",
                new_callable=AsyncMock,
                side_effect=httpx.HTTPStatusError(
                    "Gone",
                    request=MagicMock(),
                    response=MagicMock(status_code=410),
                ),
            ):
                with patch.object(
                    account_runtime,
                    "_full_sync",
                    new_callable=AsyncMock,
                ) as mock_full:
                    await account_runtime._poll_calendar("primary")

        mock_full.assert_called_once_with(
            "primary",
            cursor_key="google_calendar:user:test@example.com:primary",
            ingest_events=True,
        )


# ---------------------------------------------------------------------------
# Event change classification
# ---------------------------------------------------------------------------


class TestEventChangeClassification:
    """Tests for event type classification (created/updated/deleted)."""

    async def test_cancelled_event_classified_as_deleted(
        self,
        account_runtime: GoogleCalendarAccountRuntime,
    ) -> None:
        """Events with status=cancelled are classified as event_deleted."""
        event = {"id": "evt1", "status": "cancelled"}

        with patch.object(
            account_runtime, "_submit_event_envelope", new_callable=AsyncMock
        ) as mock_submit:
            await account_runtime._process_event(event, "primary")

        mock_submit.assert_called_once_with(event, "event_deleted")

    async def test_confirmed_event_classified_as_updated(
        self,
        account_runtime: GoogleCalendarAccountRuntime,
    ) -> None:
        """Non-cancelled events default to event_updated (no local state cache)."""
        event = {"id": "evt1", "status": "confirmed"}

        with patch.object(
            account_runtime, "_submit_event_envelope", new_callable=AsyncMock
        ) as mock_submit:
            await account_runtime._process_event(event, "primary")

        mock_submit.assert_called_once_with(event, "event_updated")

    async def test_event_without_id_is_skipped(
        self,
        account_runtime: GoogleCalendarAccountRuntime,
    ) -> None:
        """Events without an ID are silently skipped."""
        event: dict[str, Any] = {"status": "confirmed"}  # no 'id'

        with patch.object(
            account_runtime, "_submit_event_envelope", new_callable=AsyncMock
        ) as mock_submit:
            await account_runtime._process_event(event, "primary")

        mock_submit.assert_not_called()


# ---------------------------------------------------------------------------
# Starting-soon deduplication
# ---------------------------------------------------------------------------


class TestStartingSoonDedup:
    """Tests for starting-soon notification deduplication."""

    async def test_starting_soon_emits_for_event_in_window(
        self,
        account_runtime: GoogleCalendarAccountRuntime,
    ) -> None:
        """Emits starting-soon for events within the lead-time window."""
        now = datetime.now(UTC)
        start = now + timedelta(minutes=10)  # 10 minutes away, within 15-min window

        event = {
            "id": "evt1",
            "summary": "Upcoming Meeting",
            "start": {"dateTime": start.isoformat()},
            "end": {"dateTime": (start + timedelta(hours=1)).isoformat()},
        }

        account_runtime._client = MagicMock()
        account_runtime._client.list_events = AsyncMock(return_value={"items": [event]})

        with patch.object(
            account_runtime, "_submit_starting_soon_envelope", new_callable=AsyncMock
        ) as mock_submit:
            await account_runtime._check_starting_soon("primary")

        mock_submit.assert_called_once_with(event, 15)

    async def test_starting_soon_not_emitted_twice(
        self,
        account_runtime: GoogleCalendarAccountRuntime,
    ) -> None:
        """Starting-soon notifications are deduplicated via seen-set."""
        now = datetime.now(UTC)
        start = now + timedelta(minutes=10)

        event = {
            "id": "evt1",
            "summary": "Upcoming Meeting",
            "start": {"dateTime": start.isoformat()},
            "end": {"dateTime": (start + timedelta(hours=1)).isoformat()},
        }

        account_runtime._client = MagicMock()
        account_runtime._client.list_events = AsyncMock(return_value={"items": [event]})

        with patch.object(
            account_runtime, "_submit_starting_soon_envelope", new_callable=AsyncMock
        ) as mock_submit:
            # First scan — should emit
            await account_runtime._check_starting_soon("primary")
            # Second scan — should be deduped
            await account_runtime._check_starting_soon("primary")

        # Should only be called once despite two scans
        assert mock_submit.call_count == 1

    async def test_starting_soon_skipped_for_past_events(
        self,
        account_runtime: GoogleCalendarAccountRuntime,
    ) -> None:
        """Events that have already started are not emitted as starting-soon."""
        now = datetime.now(UTC)
        start = now - timedelta(minutes=5)  # 5 minutes ago — already started

        event = {
            "id": "evt1",
            "summary": "Past Meeting",
            "start": {"dateTime": start.isoformat()},
            "end": {"dateTime": (start + timedelta(hours=1)).isoformat()},
        }

        account_runtime._client = MagicMock()
        account_runtime._client.list_events = AsyncMock(return_value={"items": [event]})

        with patch.object(
            account_runtime, "_submit_starting_soon_envelope", new_callable=AsyncMock
        ) as mock_submit:
            await account_runtime._check_starting_soon("primary")

        mock_submit.assert_not_called()

    async def test_starting_soon_disabled_when_lead_zero(
        self,
        account_config: GoogleCalendarAccountConfig,
        mock_cursor_pool: MagicMock,
    ) -> None:
        """Starting-soon is disabled when gcal_starting_soon_lead_minutes=0."""
        config = account_config.model_copy(update={"gcal_starting_soon_lead_minutes": 0})
        runtime = GoogleCalendarAccountRuntime(config, mock_cursor_pool, None)

        # _check_starting_soon should return immediately without any API call
        runtime._client = MagicMock()
        runtime._client.list_events = AsyncMock()

        await runtime._check_starting_soon("primary")

        runtime._client.list_events.assert_not_called()

    async def test_starting_soon_all_day_events_skipped(
        self,
        account_runtime: GoogleCalendarAccountRuntime,
    ) -> None:
        """All-day events (date only, no dateTime) are not emitted as starting-soon."""
        event = {
            "id": "evt1",
            "summary": "All Day Event",
            "start": {"date": "2026-03-25"},  # date-only, no dateTime
            "end": {"date": "2026-03-26"},
        }

        account_runtime._client = MagicMock()
        account_runtime._client.list_events = AsyncMock(return_value={"items": [event]})

        with patch.object(
            account_runtime, "_submit_starting_soon_envelope", new_callable=AsyncMock
        ) as mock_submit:
            await account_runtime._check_starting_soon("primary")

        mock_submit.assert_not_called()


# ---------------------------------------------------------------------------
# Health status
# ---------------------------------------------------------------------------


class TestHealthStatus:
    """Tests for health aggregation."""

    def test_health_degraded_with_no_loops(
        self,
        process_config: GoogleCalendarProcessConfig,
        mock_cursor_pool: MagicMock,
    ) -> None:
        mock_pool = MagicMock()
        manager = GoogleCalendarConnectorManager(process_config, mock_pool, mock_cursor_pool)

        health = manager._get_multi_account_health()
        assert health.status == "degraded"
        assert health.active_accounts == 0
        assert isinstance(health.timestamp, str)

    def test_account_health_loop_not_running(
        self,
        account_config: GoogleCalendarAccountConfig,
        mock_cursor_pool: MagicMock,
    ) -> None:
        loop = GoogleCalendarAccountLoop(
            email="test@example.com",
            config=account_config,
            cursor_pool=mock_cursor_pool,
            shared_pool=None,
        )
        # Loop not started — should not be running
        assert not loop.is_running

        health = loop.get_health()
        assert health.email == "test@example.com"
        assert health.endpoint_identity == "google_calendar:user:test@example.com"
        assert health.source_api_connectivity == "unknown"
