"""Google Calendar connector runtime for incremental calendar event ingestion.

This connector implements the Google Calendar ingestion target state defined in
`openspec/changes/connector-google-calendar/`. It uses Google Calendar API's
incremental sync (events.list with syncToken) to ingest calendar event changes
in near real-time.

Key behaviors:
- OAuth-based authentication (DB-only; no env-var credential fallback)
- Multi-account operation via shared.google_accounts (calendar scope)
- Incremental sync via syncToken with 410 Gone fallback to full sync
- Event change classification (created/updated/deleted)
- "Starting soon" notifications with in-memory dedup
- Durable syncToken cursor with checkpoint-after-acceptance
- Bounded in-flight requests with exponential backoff on 429/503
- Per-account error isolation (one account failure does not affect others)
- Dynamic account discovery (periodic re-scan at GCAL_ACCOUNT_RESCAN_INTERVAL_S)
- Aggregated health endpoint for Kubernetes readiness/liveness probes

Environment variables:
- SWITCHBOARD_MCP_URL (required)
- CONNECTOR_PROVIDER=google_calendar (required)
- CONNECTOR_CHANNEL=google_calendar (required)
- DATABASE_URL or POSTGRES_* (DB connectivity for account discovery/credentials)
- GCAL_POLL_INTERVAL_S (optional, default 60)
- GCAL_STARTING_SOON_LEAD_MINUTES (optional, default 15; 0 = disabled)
- GCAL_ACCOUNT_RESCAN_INTERVAL_S (optional, default 300)
- CONNECTOR_MAX_INFLIGHT (optional, default 8)
- CONNECTOR_HEALTH_PORT (optional, default 40084)
- CONNECTOR_HEARTBEAT_INTERVAL_S (optional, default 120)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import time
from datetime import UTC, datetime, timedelta
from threading import Thread
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    import asyncpg

import httpx
import uvicorn
from fastapi import FastAPI
from prometheus_client import REGISTRY, generate_latest
from pydantic import BaseModel, ConfigDict

from butlers.connectors.cursor_store import load_cursor, save_cursor
from butlers.connectors.heartbeat import ConnectorHeartbeat, HeartbeatConfig
from butlers.connectors.mcp_client import CachedMCPClient, wait_for_switchboard_ready
from butlers.connectors.metrics import ConnectorMetrics, get_error_type
from butlers.core.logging import configure_logging
from butlers.credential_store import CredentialStore, shared_db_name_from_env
from butlers.db import db_params_from_env, schema_search_path, should_retry_with_ssl_disable
from butlers.google_credentials import (
    InvalidGoogleCredentialsError,
    load_google_credentials,
)
from butlers.ingestion_policy import IngestionEnvelope, IngestionPolicyEvaluator

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_GCAL_CALENDAR_SCOPE = "calendar"
_GCAL_CALENDAR_SCOPE_FULL = "https://www.googleapis.com/auth/calendar"
_GCAL_CALENDAR_SCOPE_READONLY = "https://www.googleapis.com/auth/calendar.readonly"
_GCAL_CALENDAR_SCOPE_EVENTS = "https://www.googleapis.com/auth/calendar.events"
_GCAL_CALENDAR_SCOPE_EVENTS_READONLY = "https://www.googleapis.com/auth/calendar.events.readonly"

# All scope strings that qualify an account for calendar access
_CALENDAR_SCOPES: frozenset[str] = frozenset(
    [
        _GCAL_CALENDAR_SCOPE,
        _GCAL_CALENDAR_SCOPE_FULL,
        _GCAL_CALENDAR_SCOPE_READONLY,
        _GCAL_CALENDAR_SCOPE_EVENTS,
        _GCAL_CALENDAR_SCOPE_EVENTS_READONLY,
    ]
)

_CONNECTOR_TYPE = "google_calendar"
_GOOGLE_CALENDAR_API_BASE = "https://www.googleapis.com/calendar/v3"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

# Default re-scan interval for dynamic account discovery (seconds).
_DEFAULT_ACCOUNT_RESCAN_INTERVAL_S = 300

# Rate-limit retry constants (429/503 exponential backoff)
_RATE_LIMIT_STATUS_CODES = frozenset([429, 503])
_MAX_RETRY_ATTEMPTS = 5
_INITIAL_BACKOFF_S = 1.0
_MAX_BACKOFF_S = 64.0
_BACKOFF_MULTIPLIER = 2.0


# ---------------------------------------------------------------------------
# Health models
# ---------------------------------------------------------------------------


class AccountHealthStatus(BaseModel):
    """Per-account health status for multi-account connectors."""

    email: str | None
    endpoint_identity: str
    status: Literal["healthy", "degraded", "error"]
    last_checkpoint_save_at: str | None
    last_sync_at: str | None
    source_api_connectivity: Literal["connected", "disconnected", "unknown"]
    error: str | None = None


class MultiAccountHealthStatus(BaseModel):
    """Aggregated health status for the multi-account Google Calendar connector."""

    status: Literal["healthy", "degraded", "error"]
    uptime_seconds: float
    active_accounts: int
    account_health: list[AccountHealthStatus]
    timestamp: str


# ---------------------------------------------------------------------------
# Config models
# ---------------------------------------------------------------------------


class GoogleCalendarProcessConfig(BaseModel):
    """Process-level configuration for the multi-account Google Calendar connector manager.

    Holds environment-variable-based defaults. Per-account overrides come from
    ``google_accounts.metadata.calendar``. Credentials are resolved per-account from DB.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Switchboard MCP
    switchboard_mcp_url: str

    # Connector identity (process-level defaults; per-account values are derived)
    connector_provider: str = "google_calendar"
    connector_channel: str = "google_calendar"
    connector_max_inflight: int = 8

    # Health check
    connector_health_port: int = 40084

    # Heartbeat
    connector_heartbeat_interval_s: int = 120

    # Runtime controls (process-level defaults, overridable per-account)
    gcal_poll_interval_s: int = 60
    gcal_starting_soon_lead_minutes: int = 15
    gcal_account_rescan_interval_s: int = _DEFAULT_ACCOUNT_RESCAN_INTERVAL_S

    @classmethod
    def from_env(cls) -> GoogleCalendarProcessConfig:
        """Load process-level config from environment variables."""

        def _int_env(key: str, default: int) -> int:
            raw = os.environ.get(key, str(default))
            try:
                return int(raw)
            except ValueError as exc:
                raise ValueError(f"{key} must be an integer, got: {raw}") from exc

        return cls(
            switchboard_mcp_url=os.environ["SWITCHBOARD_MCP_URL"],
            connector_provider=os.environ.get("CONNECTOR_PROVIDER", "google_calendar"),
            connector_channel=os.environ.get("CONNECTOR_CHANNEL", "google_calendar"),
            connector_max_inflight=_int_env("CONNECTOR_MAX_INFLIGHT", 8),
            connector_health_port=_int_env("CONNECTOR_HEALTH_PORT", 40084),
            connector_heartbeat_interval_s=_int_env("CONNECTOR_HEARTBEAT_INTERVAL_S", 120),
            gcal_poll_interval_s=_int_env("GCAL_POLL_INTERVAL_S", 60),
            gcal_starting_soon_lead_minutes=_int_env("GCAL_STARTING_SOON_LEAD_MINUTES", 15),
            gcal_account_rescan_interval_s=_int_env(
                "GCAL_ACCOUNT_RESCAN_INTERVAL_S", _DEFAULT_ACCOUNT_RESCAN_INTERVAL_S
            ),
        )

    def make_account_config(
        self,
        email: str,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        metadata_calendar: dict[str, Any] | None = None,
    ) -> GoogleCalendarAccountConfig:
        """Build a per-account config by merging process defaults with per-account overrides.

        Per-account overrides come from ``google_accounts.metadata.calendar``.
        Supported override fields: poll_interval_s, starting_soon_lead_minutes, calendar_ids.
        """
        md = metadata_calendar or {}
        endpoint_identity = f"google_calendar:user:{email}"

        poll_interval_s = int(md.get("poll_interval_s", self.gcal_poll_interval_s))
        starting_soon_lead_minutes = int(
            md.get("starting_soon_lead_minutes", self.gcal_starting_soon_lead_minutes)
        )
        calendar_ids_raw = md.get("calendar_ids")
        calendar_ids: list[str] | None = (
            list(calendar_ids_raw) if isinstance(calendar_ids_raw, list) else None
        )

        return GoogleCalendarAccountConfig(
            switchboard_mcp_url=self.switchboard_mcp_url,
            connector_provider=self.connector_provider,
            connector_channel=self.connector_channel,
            connector_endpoint_identity=endpoint_identity,
            connector_max_inflight=self.connector_max_inflight,
            connector_heartbeat_interval_s=self.connector_heartbeat_interval_s,
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=refresh_token,
            user_email=email,
            gcal_poll_interval_s=poll_interval_s,
            gcal_starting_soon_lead_minutes=starting_soon_lead_minutes,
            calendar_ids=calendar_ids,
        )


class GoogleCalendarAccountConfig(BaseModel):
    """Per-account configuration for a single Google Calendar poll loop."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Switchboard MCP
    switchboard_mcp_url: str

    # Connector identity
    connector_provider: str = "google_calendar"
    connector_channel: str = "google_calendar"
    connector_endpoint_identity: str
    connector_max_inflight: int = 8

    # Heartbeat
    connector_heartbeat_interval_s: int = 120

    # Google OAuth credentials (DB-resolved)
    client_id: str
    client_secret: str
    refresh_token: str

    # Account email
    user_email: str

    # Runtime controls
    gcal_poll_interval_s: int = 60
    gcal_starting_soon_lead_minutes: int = 15

    # Calendar IDs to watch (None = primary calendar only)
    calendar_ids: list[str] | None = None


# ---------------------------------------------------------------------------
# Cursor model
# ---------------------------------------------------------------------------


class GoogleCalendarCursor(BaseModel):
    """Durable checkpoint state for Google Calendar sync token tracking."""

    model_config = ConfigDict(extra="forbid")

    sync_token: str
    last_updated_at: str  # ISO 8601 timestamp


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_google_error(response: httpx.Response) -> str | None:
    """Extract a compact Google API/OAuth error summary from response JSON."""
    try:
        payload = response.json()
    except Exception:
        return None

    if not isinstance(payload, dict):
        return None

    # Google API error shape: {"error": {"code": 404, "message": "...", ...}}
    nested_error = payload.get("error")
    if isinstance(nested_error, dict):
        parts: list[str] = []

        code = nested_error.get("code")
        if code is not None:
            parts.append(f"code={code}")

        status = nested_error.get("status")
        if isinstance(status, str) and status:
            parts.append(f"status={status}")

        reason = None
        nested_errors = nested_error.get("errors")
        if isinstance(nested_errors, list):
            for item in nested_errors:
                if isinstance(item, dict) and item.get("reason"):
                    reason = item["reason"]
                    break
        if isinstance(reason, str) and reason:
            parts.append(f"reason={reason}")

        message = nested_error.get("message")
        if isinstance(message, str) and message:
            parts.append(f"message={message}")

        return ", ".join(parts) if parts else None

    # OAuth token endpoint error shape: {"error": "invalid_grant", ...}
    if isinstance(nested_error, str) and nested_error:
        error_description = payload.get("error_description")
        if isinstance(error_description, str) and error_description:
            return f"error={nested_error}, description={error_description}"
        return f"error={nested_error}"

    return None


def _has_calendar_scope(scopes: list[str]) -> bool:
    """Return True if any of the given scopes qualify for Google Calendar access."""
    for s in scopes:
        # Accept both short-form ("calendar") and full URL scopes
        if s in _CALENDAR_SCOPES or "calendar" in s.lower():
            return True
    return False


def _build_normalized_text(
    event_type: str,
    event: dict[str, Any],
) -> str:
    """Build the normalized_text payload field for a calendar event.

    Format:
        [Calendar: <event_type>] <title> | <start> - <end> | <location> | <attendee_count>
        attendees | Organizer: <organizer>
    """
    title = event.get("summary", "(No title)")
    start_raw = event.get("start", {})
    end_raw = event.get("end", {})
    start = start_raw.get("dateTime") or start_raw.get("date") or "?"
    end = end_raw.get("dateTime") or end_raw.get("date") or "?"
    location = event.get("location", "")
    attendees = event.get("attendees", [])
    attendee_count = len(attendees) if isinstance(attendees, list) else 0
    organizer_info = event.get("organizer", {})
    organizer = organizer_info.get("email", "") if isinstance(organizer_info, dict) else ""

    parts = [f"[Calendar: {event_type}] {title}", f"{start} - {end}"]
    if location:
        parts.append(location)
    parts.append(f"{attendee_count} attendees")
    parts.append(f"Organizer: {organizer}")
    return " | ".join(parts)


def _get_organizer_email(event: dict[str, Any], fallback_email: str) -> str:
    """Extract the event organizer email, falling back to the account email."""
    organizer = event.get("organizer", {})
    if isinstance(organizer, dict):
        email = organizer.get("email", "")
        if email:
            return email.lower()
    return fallback_email.lower()


# ---------------------------------------------------------------------------
# Google Calendar API client
# ---------------------------------------------------------------------------


class GoogleCalendarClient:
    """Google Calendar API client with token refresh and rate-limit retry.

    Handles:
    - OAuth token refresh when access token expires
    - Exponential backoff retry on 429 (rate limit) and 503 (service unavailable)
    - Incremental sync via events.list with syncToken
    - Full sync fallback on 410 Gone
    """

    def __init__(
        self,
        config: GoogleCalendarAccountConfig,
        metrics: ConnectorMetrics,
    ) -> None:
        self._config = config
        self._metrics = metrics
        self._http_client: httpx.AsyncClient | None = None
        self._access_token: str | None = None
        self._token_expires_at: datetime | None = None
        self._source_api_ok: bool | None = None

    async def start(self) -> None:
        """Initialize the HTTP client."""
        self._http_client = httpx.AsyncClient(timeout=30.0)

    async def stop(self) -> None:
        """Close the HTTP client."""
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

    async def get_access_token(self) -> str:
        """Get a valid OAuth access token, refreshing if expired."""
        if self._access_token and self._token_expires_at:
            if datetime.now(UTC) < self._token_expires_at:
                return self._access_token

        if not self._http_client:
            raise RuntimeError("HTTP client not initialized — call start() first")

        try:
            response = await self._http_client.post(
                _GOOGLE_TOKEN_URL,
                data={
                    "client_id": self._config.client_id,
                    "client_secret": self._config.client_secret,
                    "refresh_token": self._config.refresh_token,
                    "grant_type": "refresh_token",
                },
            )
            if response.is_error:
                google_error = _format_google_error(response)
                if google_error:
                    logger.error(
                        "OAuth token refresh failed status=%s details=%s",
                        response.status_code,
                        google_error,
                    )
                else:
                    logger.error("OAuth token refresh failed status=%s", response.status_code)
            response.raise_for_status()
            token_data = response.json()

            self._access_token = token_data["access_token"]
            expires_in = token_data.get("expires_in", 3600)
            self._token_expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)
            self._source_api_ok = True
            self._metrics.record_source_api_call(api_method="token_refresh", status="success")

            logger.debug("Refreshed OAuth access token (expires in %ds)", expires_in)
            return self._access_token
        except Exception as exc:
            self._source_api_ok = False
            self._metrics.record_source_api_call(api_method="token_refresh", status="error")
            self._metrics.record_error(error_type=get_error_type(exc), operation="token_refresh")
            raise

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """Make an authenticated API request with exponential backoff on 429/503."""
        if not self._http_client:
            raise RuntimeError("HTTP client not initialized — call start() first")

        backoff = _INITIAL_BACKOFF_S
        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRY_ATTEMPTS):
            try:
                token = await self.get_access_token()
                response = await self._http_client.request(
                    method,
                    url,
                    params=params,
                    headers={"Authorization": f"Bearer {token}"},
                )

                if response.status_code in _RATE_LIMIT_STATUS_CODES:
                    retry_after = response.headers.get("Retry-After")
                    wait_s = float(retry_after) if retry_after else backoff
                    wait_s = min(wait_s, _MAX_BACKOFF_S)
                    logger.warning(
                        "Google Calendar API rate limit/unavailable (status=%d, attempt=%d/%d),"
                        " retrying in %.1fs",
                        response.status_code,
                        attempt + 1,
                        _MAX_RETRY_ATTEMPTS,
                        wait_s,
                    )
                    await asyncio.sleep(wait_s)
                    backoff = min(backoff * _BACKOFF_MULTIPLIER, _MAX_BACKOFF_S)
                    continue

                return response
            except httpx.TransportError as exc:
                last_exc = exc
                if attempt < _MAX_RETRY_ATTEMPTS - 1:
                    wait_s = min(backoff, _MAX_BACKOFF_S)
                    logger.warning(
                        "Google Calendar API transport error (attempt=%d/%d), retrying in %.1fs:"
                        " %s",
                        attempt + 1,
                        _MAX_RETRY_ATTEMPTS,
                        wait_s,
                        exc,
                    )
                    await asyncio.sleep(wait_s)
                    backoff = min(backoff * _BACKOFF_MULTIPLIER, _MAX_BACKOFF_S)

        if last_exc is not None:
            raise last_exc
        # Should not reach here; all retries failed via status code path
        raise RuntimeError(
            f"Google Calendar API request failed after {_MAX_RETRY_ATTEMPTS} attempts"
        )

    async def list_events(
        self,
        calendar_id: str = "primary",
        *,
        sync_token: str | None = None,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        """Call events.list, supporting both full sync and incremental sync.

        Pass ``sync_token`` for incremental sync. Omit for full sync.
        May raise httpx.HTTPStatusError with status_code=410 for expired syncToken.
        """
        url = f"{_GOOGLE_CALENDAR_API_BASE}/calendars/{calendar_id}/events"
        params: dict[str, Any] = {}
        if sync_token:
            params["syncToken"] = sync_token
        else:
            # Full sync: only future events needed for baseline
            params["singleEvents"] = "true"
            params["orderBy"] = "startTime"
        if page_token:
            params["pageToken"] = page_token

        api_method = "events.list"
        try:
            response = await self._request_with_retry("GET", url, params=params)
            if response.is_error:
                google_error = _format_google_error(response)
                if google_error:
                    logger.error(
                        "events.list failed status=%d details=%s",
                        response.status_code,
                        google_error,
                    )
                response.raise_for_status()

            self._source_api_ok = True
            self._metrics.record_source_api_call(api_method=api_method, status="success")
            return response.json()
        except Exception as exc:
            self._source_api_ok = False
            self._metrics.record_source_api_call(api_method=api_method, status="error")
            self._metrics.record_error(error_type=get_error_type(exc), operation=api_method)
            raise


# ---------------------------------------------------------------------------
# Per-account poll loop runtime
# ---------------------------------------------------------------------------


class GoogleCalendarAccountRuntime:
    """Per-account Google Calendar ingestion loop runtime.

    Manages the full poll cycle for a single Google account:
    - syncToken cursor management (load / save)
    - Initial full sync → incremental sync with 410 Gone fallback
    - Event change classification (created/updated/deleted)
    - ingest.v1 envelope normalization and submission to Switchboard
    - Starting-soon notification synthesis with in-memory dedup
    - Ingestion policy evaluation (IngestionPolicyEvaluator)
    """

    def __init__(
        self,
        config: GoogleCalendarAccountConfig,
        cursor_pool: asyncpg.Pool | None,
        shared_pool: asyncpg.Pool | None,
    ) -> None:
        self._config = config
        self._cursor_pool = cursor_pool
        self._shared_pool = shared_pool

        self._metrics = ConnectorMetrics(
            connector_type=_CONNECTOR_TYPE,
            endpoint_identity=config.connector_endpoint_identity,
        )

        self._client = GoogleCalendarClient(config, self._metrics)
        self._mcp_client: CachedMCPClient | None = None
        self._heartbeat: ConnectorHeartbeat | None = None
        self._policy_evaluator: IngestionPolicyEvaluator | None = None

        # State
        self._running = False
        self._last_checkpoint_save: float | None = None
        self._last_sync_at: float | None = None
        self._source_api_ok: bool | None = None

        # Starting-soon dedup: keyed by (event_id, lead_minutes)
        self._starting_soon_seen: set[tuple[str, int]] = set()

    @property
    def endpoint_identity(self) -> str:
        return self._config.connector_endpoint_identity

    @property
    def source_api_ok(self) -> bool | None:
        return self._client._source_api_ok

    async def start(self) -> None:
        """Start the poll loop for this account."""
        self._running = True
        await self._client.start()

        # Initialize MCP client for Switchboard submission
        self._mcp_client = CachedMCPClient(
            mcp_url=self._config.switchboard_mcp_url,
            max_inflight=self._config.connector_max_inflight,
        )

        # Initialize heartbeat
        self._heartbeat = ConnectorHeartbeat(
            mcp_client=self._mcp_client,
            config=HeartbeatConfig(
                endpoint_identity=self._config.connector_endpoint_identity,
                connector_type=_CONNECTOR_TYPE,
                interval_s=self._config.connector_heartbeat_interval_s,
            ),
        )

        # Initialize ingestion policy evaluator if shared pool available
        if self._shared_pool is not None:
            scope = f"connector:{_CONNECTOR_TYPE}:{self._config.connector_endpoint_identity}"
            self._policy_evaluator = IngestionPolicyEvaluator(
                pool=self._shared_pool,
                scope=scope,
            )

        try:
            await wait_for_switchboard_ready(self._config.switchboard_mcp_url)
        except Exception as exc:
            logger.warning(
                "Google Calendar [%s]: Switchboard not ready at startup: %s",
                self._config.user_email,
                exc,
            )

        # Start heartbeat
        if self._heartbeat:
            await self._heartbeat.start()

        try:
            await self._run_poll_loop()
        finally:
            if self._heartbeat:
                await self._heartbeat.stop()
            await self._client.stop()
            self._running = False

    async def stop(self) -> None:
        """Signal the poll loop to stop gracefully."""
        self._running = False
        if self._heartbeat:
            try:
                await self._heartbeat.stop()
            except Exception:
                pass
        await self._client.stop()

    async def _run_poll_loop(self) -> None:
        """Main poll loop: repeatedly sync, submit events, sleep, repeat."""
        email = self._config.user_email
        calendar_ids = self._config.calendar_ids or ["primary"]
        poll_interval = self._config.gcal_poll_interval_s

        logger.info(
            "Google Calendar poll loop started: email=%s calendars=%s interval=%ds",
            email,
            calendar_ids,
            poll_interval,
        )

        while self._running:
            cycle_start = time.time()
            try:
                for calendar_id in calendar_ids:
                    await self._poll_calendar(calendar_id)
                self._last_sync_at = time.time()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._metrics.record_error(error_type=get_error_type(exc), operation="poll_cycle")
                logger.error(
                    "Google Calendar [%s] poll cycle error (will retry): %s",
                    email,
                    exc,
                    exc_info=True,
                )

            # Sleep until next poll, accounting for cycle duration
            elapsed = time.time() - cycle_start
            sleep_s = max(0.0, poll_interval - elapsed)
            if sleep_s > 0 and self._running:
                await asyncio.sleep(sleep_s)

    async def _poll_calendar(self, calendar_id: str) -> None:
        """Execute one poll cycle for a single calendar.

        Performs incremental sync if cursor exists, falls back to full sync on 410 Gone.
        """
        endpoint_identity = self._config.connector_endpoint_identity
        # Key per (account, calendar) — each calendar has an independent syncToken namespace
        cursor_key = f"{endpoint_identity}:{calendar_id}"

        # Load persisted cursor
        sync_token: str | None = None
        if self._cursor_pool is not None:
            try:
                cursor_json = await load_cursor(self._cursor_pool, _CONNECTOR_TYPE, cursor_key)
                if cursor_json:
                    cursor = GoogleCalendarCursor.model_validate_json(cursor_json)
                    sync_token = cursor.sync_token
                    logger.debug(
                        "Google Calendar [%s]: loaded syncToken cursor", self._config.user_email
                    )
            except Exception as exc:
                logger.warning(
                    "Google Calendar [%s]: failed to load cursor (starting fresh): %s",
                    self._config.user_email,
                    exc,
                )

        if sync_token is None:
            # Initial full sync — establish baseline, persist token, no ingestion
            await self._full_sync(calendar_id, cursor_key=cursor_key, ingest_events=False)
        else:
            # Incremental sync
            try:
                await self._incremental_sync(calendar_id, sync_token, cursor_key=cursor_key)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 410:
                    # syncToken expired — recover via full sync with ingestion
                    logger.warning(
                        "Google Calendar [%s]: syncToken expired (410 Gone),"
                        " recovering via full sync",
                        self._config.user_email,
                    )
                    await self._full_sync(calendar_id, cursor_key=cursor_key, ingest_events=True)
                else:
                    raise

    async def _full_sync(self, calendar_id: str, *, cursor_key: str, ingest_events: bool) -> None:
        """Execute a full events.list sync to establish (or re-establish) the cursor.

        When ``ingest_events=False`` (initial baseline), events are NOT submitted.
        When ``ingest_events=True`` (recovery from 410), events ARE submitted.
        """
        email = self._config.user_email
        logger.info(
            "Google Calendar [%s]: starting full sync (calendar=%s, ingest=%s)",
            email,
            calendar_id,
            ingest_events,
        )

        page_token: str | None = None
        next_sync_token: str | None = None
        total_events = 0

        while True:
            try:
                response_data = await self._client.list_events(
                    calendar_id, sync_token=None, page_token=page_token
                )
            except Exception as exc:
                logger.error(
                    "Google Calendar [%s]: full sync API error (calendar=%s): %s",
                    email,
                    calendar_id,
                    exc,
                )
                raise

            items = response_data.get("items", [])
            next_sync_token = response_data.get("nextSyncToken")
            page_token = response_data.get("nextPageToken")

            if ingest_events:
                for event in items:
                    try:
                        await self._process_event(event, calendar_id)
                        total_events += 1
                    except Exception as exc:
                        logger.error(
                            "Google Calendar [%s]: failed to process event %s: %s",
                            email,
                            event.get("id", "?"),
                            exc,
                        )

            if not page_token:
                break

        if next_sync_token:
            await self._save_cursor(next_sync_token, cursor_key=cursor_key)
            logger.info(
                "Google Calendar [%s]: full sync complete"
                " (calendar=%s, events_processed=%d, ingest=%s)",
                email,
                calendar_id,
                total_events,
                ingest_events,
            )
        else:
            logger.warning(
                "Google Calendar [%s]: full sync returned no nextSyncToken (calendar=%s)",
                email,
                calendar_id,
            )

    async def _incremental_sync(
        self, calendar_id: str, sync_token: str, *, cursor_key: str
    ) -> None:
        """Execute an incremental events.list sync using the persisted syncToken.

        Paginates through all changed events before advancing the cursor.
        The cursor is only advanced after all events are successfully processed.
        """
        email = self._config.user_email
        page_token: str | None = None
        next_sync_token: str | None = None
        total_events: int = 0

        while True:
            response_data = await self._client.list_events(
                calendar_id, sync_token=sync_token, page_token=page_token
            )

            items = response_data.get("items", [])
            next_sync_token = response_data.get("nextSyncToken")
            page_token = response_data.get("nextPageToken")

            for event in items:
                try:
                    await self._process_event(event, calendar_id)
                    total_events += 1
                except Exception as exc:
                    logger.error(
                        "Google Calendar [%s]: failed to process event %s (skipping): %s",
                        email,
                        event.get("id", "?"),
                        exc,
                    )

            if not page_token:
                break

        if next_sync_token:
            # Checkpoint only after all events on all pages are processed
            await self._save_cursor(next_sync_token, cursor_key=cursor_key)
            if total_events:
                logger.debug(
                    "Google Calendar [%s]: incremental sync: %d events, cursor advanced",
                    email,
                    total_events,
                )
        elif total_events == 0:
            # No changes
            logger.debug("Google Calendar [%s]: incremental sync: no changes", email)

        # After sync: check for starting-soon events
        if self._config.gcal_starting_soon_lead_minutes > 0:
            await self._check_starting_soon(calendar_id)

    async def _process_event(self, event: dict[str, Any], calendar_id: str) -> None:
        """Normalize and submit a single calendar event change to Switchboard."""
        event_id = event.get("id")
        if not event_id:
            logger.debug("Google Calendar: skipping event without ID")
            return

        # Classify event type
        status = event.get("status", "")
        if status == "cancelled":
            event_type = "event_deleted"
        else:
            # Without a local event cache, default to event_updated for non-cancelled
            # (Switchboard dedup layer handles any resulting duplicates)
            event_type = "event_updated"

        await self._submit_event_envelope(event, event_type)

    async def _submit_event_envelope(
        self,
        event: dict[str, Any],
        event_type: str,
    ) -> None:
        """Build an ingest.v1 envelope and submit it to Switchboard."""
        email = self._config.user_email
        endpoint_identity = self._config.connector_endpoint_identity
        event_id = event.get("id", "")
        updated = event.get("updated", "")

        # Ingestion policy gate
        organizer_email = _get_organizer_email(event, email)
        if self._policy_evaluator is not None:
            envelope = IngestionEnvelope(
                sender_address=organizer_email,
                source_channel=self._config.connector_channel,
                raw_key=organizer_email,
            )
            try:
                decision = await self._policy_evaluator.evaluate(envelope)
                if not decision.allowed:
                    logger.debug(
                        "Google Calendar [%s]: event %s blocked by policy (%s)",
                        email,
                        event_id,
                        decision.reason,
                    )
                    return
            except Exception as exc:
                logger.warning(
                    "Google Calendar [%s]: policy evaluation failed for event %s (allowing): %s",
                    email,
                    event_id,
                    exc,
                )

        observed_at = datetime.now(UTC).isoformat()
        normalized_text = _build_normalized_text(event_type, event)
        idempotency_key = f"gcal:{endpoint_identity}:{event_id}:{updated}"

        ingest_payload = {
            "source": {
                "channel": self._config.connector_channel,
                "provider": self._config.connector_provider,
                "endpoint_identity": endpoint_identity,
            },
            "event": {
                "external_event_id": event_id,
                "external_thread_id": event_id,
                "observed_at": observed_at,
                "event_type": event_type,
            },
            "sender": {
                "identity": organizer_email,
            },
            "payload": {
                "raw": json.dumps(event),
                "normalized_text": normalized_text,
            },
            "control": {
                "idempotency_key": idempotency_key,
                "ingestion_tier": "full",
                "policy_tier": "default",
            },
        }

        if self._mcp_client is None:
            raise RuntimeError("MCP client not initialized")

        try:
            await self._mcp_client.call_tool("ingest", ingest_payload)
            self._metrics.record_ingest_submission(status="accepted", latency=0.0)
            logger.debug(
                "Google Calendar [%s]: submitted event %s (%s)",
                email,
                event_id,
                event_type,
            )
        except Exception as exc:
            self._metrics.record_ingest_submission(status="error", latency=0.0)
            self._metrics.record_error(error_type=get_error_type(exc), operation="ingest_submit")
            raise

    async def _check_starting_soon(self, calendar_id: str) -> None:
        """Scan upcoming events and emit starting-soon notifications.

        Uses in-memory seen-set keyed by (event_id, lead_minutes) to deduplicate.
        Prunes seen-set of past events to prevent unbounded growth.
        """
        email = self._config.user_email
        lead_minutes = self._config.gcal_starting_soon_lead_minutes
        if lead_minutes <= 0:
            return

        now = datetime.now(UTC)
        window_end = now + timedelta(minutes=lead_minutes)

        # Fetch upcoming events within the lead-time window
        try:
            response_data = await self._client.list_events(
                calendar_id,
                sync_token=None,
            )
        except Exception as exc:
            logger.warning(
                "Google Calendar [%s]: failed to fetch events for starting-soon check: %s",
                email,
                exc,
            )
            return

        items = response_data.get("items", [])
        seen_key_to_start: dict[tuple[str, int], datetime] = {}

        for event in items:
            event_id = event.get("id")
            if not event_id:
                continue

            # Parse event start time
            start_raw = event.get("start", {})
            start_str = start_raw.get("dateTime") or start_raw.get("date")
            if not start_str:
                continue

            try:
                if "T" in start_str:
                    start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                    if start_dt.tzinfo is None:
                        start_dt = start_dt.replace(tzinfo=UTC)
                else:
                    # All-day event — skip starting-soon
                    continue
            except ValueError:
                continue

            # Check if event is within the lead-time window and has not yet started
            if now <= start_dt <= window_end:
                key = (event_id, lead_minutes)
                if key not in self._starting_soon_seen:
                    self._starting_soon_seen.add(key)
                    seen_key_to_start[key] = start_dt
                    try:
                        await self._submit_starting_soon_envelope(event, lead_minutes)
                    except Exception as exc:
                        logger.error(
                            "Google Calendar [%s]: failed to submit starting-soon for event %s: %s",
                            email,
                            event_id,
                            exc,
                        )

        # Prune seen-set of past events: any key not seen in the current window scan
        # has either already started or moved out of the window — safe to remove.
        # Pruning unconditionally keeps memory bounded by the current window size.
        past_keys = self._starting_soon_seen - set(seen_key_to_start)
        self._starting_soon_seen -= past_keys

    async def _submit_starting_soon_envelope(
        self,
        event: dict[str, Any],
        lead_minutes: int,
    ) -> None:
        """Submit an event_starting_soon ingest.v1 envelope to Switchboard."""
        email = self._config.user_email
        endpoint_identity = self._config.connector_endpoint_identity
        event_id = event.get("id", "")
        organizer_email = _get_organizer_email(event, email)

        observed_at = datetime.now(UTC).isoformat()
        normalized_text = _build_normalized_text("starting_soon", event)
        idempotency_key = f"gcal:{endpoint_identity}:starting_soon:{event_id}:{lead_minutes}"

        ingest_payload = {
            "source": {
                "channel": self._config.connector_channel,
                "provider": self._config.connector_provider,
                "endpoint_identity": endpoint_identity,
            },
            "event": {
                "external_event_id": f"starting_soon:{event_id}",
                "external_thread_id": event_id,
                "observed_at": observed_at,
                "event_type": "event_starting_soon",
            },
            "sender": {
                "identity": organizer_email,
            },
            "payload": {
                "raw": json.dumps(event),
                "normalized_text": normalized_text,
            },
            "control": {
                "idempotency_key": idempotency_key,
                "ingestion_tier": "full",
                "policy_tier": "interactive",
            },
        }

        if self._mcp_client is None:
            raise RuntimeError("MCP client not initialized")

        await self._mcp_client.call_tool("ingest", ingest_payload)
        self._metrics.record_ingest_submission(status="accepted", latency=0.0)
        logger.info(
            "Google Calendar [%s]: emitted starting-soon notification for event %s (lead=%dmin)",
            email,
            event_id,
            lead_minutes,
        )

    async def _save_cursor(self, sync_token: str, *, cursor_key: str) -> None:
        """Persist the syncToken cursor to the DB.

        ``cursor_key`` must be ``f"{endpoint_identity}:{calendar_id}"`` so that
        each calendar for an account has an independent cursor record.
        """
        if self._cursor_pool is None:
            return

        cursor = GoogleCalendarCursor(
            sync_token=sync_token,
            last_updated_at=datetime.now(UTC).isoformat(),
        )
        try:
            await save_cursor(
                self._cursor_pool,
                _CONNECTOR_TYPE,
                cursor_key,
                cursor.model_dump_json(),
            )
            self._last_checkpoint_save = time.time()
            self._metrics.record_checkpoint_save(status="success")
        except Exception as exc:
            self._metrics.record_checkpoint_save(status="error")
            self._metrics.record_error(error_type=get_error_type(exc), operation="checkpoint_save")
            raise


# ---------------------------------------------------------------------------
# Per-account loop wrapper
# ---------------------------------------------------------------------------


class GoogleCalendarAccountLoop:
    """Per-account Google Calendar ingestion loop.

    Wraps a GoogleCalendarAccountRuntime instance for a single Google account.
    Runs as an independent asyncio task; errors are isolated from other accounts.
    """

    def __init__(
        self,
        email: str,
        config: GoogleCalendarAccountConfig,
        cursor_pool: asyncpg.Pool | None,
        shared_pool: asyncpg.Pool | None,
    ) -> None:
        self.email = email
        self.endpoint_identity = config.connector_endpoint_identity
        self._config = config
        self._runtime = GoogleCalendarAccountRuntime(config, cursor_pool, shared_pool)
        self._task: asyncio.Task[None] | None = None
        self._error: str | None = None

    def start(self) -> None:
        """Launch the per-account ingestion loop as an asyncio task."""
        self._task = asyncio.create_task(self._run(), name=f"gcal-account-{self.email}")
        self._task.add_done_callback(self._on_done)

    async def _run(self) -> None:
        try:
            logger.info(
                "Google Calendar account loop starting: email=%s endpoint_identity=%s",
                self.email,
                self.endpoint_identity,
            )
            await self._runtime.start()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._error = str(exc)
            logger.error(
                "Google Calendar account loop failed: email=%s error=%s",
                self.email,
                exc,
                exc_info=True,
            )
            raise

    def _on_done(self, task: asyncio.Task[None]) -> None:
        if not task.cancelled():
            exc = task.exception()
            if exc is not None:
                self._error = str(exc)

    async def stop(self) -> None:
        """Gracefully stop the account loop."""
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        await self._runtime.stop()
        logger.info("Google Calendar account loop stopped: email=%s", self.email)

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def get_health(self) -> AccountHealthStatus:
        """Return per-account health snapshot."""
        runtime = self._runtime

        last_checkpoint_save_at = None
        if runtime._last_checkpoint_save is not None:
            last_checkpoint_save_at = datetime.fromtimestamp(
                runtime._last_checkpoint_save, UTC
            ).isoformat()

        last_sync_at = None
        if runtime._last_sync_at is not None:
            last_sync_at = datetime.fromtimestamp(runtime._last_sync_at, UTC).isoformat()

        source_api_ok = runtime.source_api_ok
        if source_api_ok is None:
            connectivity: Literal["connected", "disconnected", "unknown"] = "unknown"
        elif source_api_ok:
            connectivity = "connected"
        else:
            connectivity = "disconnected"

        error_msg = self._error
        if not self.is_running and error_msg:
            account_status: Literal["healthy", "degraded", "error"] = "error"
        elif source_api_ok is False:
            account_status = "error"
        else:
            account_status = "healthy"

        return AccountHealthStatus(
            email=self.email,
            endpoint_identity=self.endpoint_identity,
            status=account_status,
            last_checkpoint_save_at=last_checkpoint_save_at,
            last_sync_at=last_sync_at,
            source_api_connectivity=connectivity,
            error=error_msg,
        )


# ---------------------------------------------------------------------------
# Multi-account manager
# ---------------------------------------------------------------------------


class GoogleCalendarConnectorManager:
    """Top-level orchestrator for multi-account Google Calendar connector.

    Discovers all active Google accounts with calendar scopes from shared.google_accounts,
    spawns independent GoogleCalendarAccountLoop instances per account, and manages their
    lifecycle.

    Supports:
    - Periodic account re-scan at GCAL_ACCOUNT_RESCAN_INTERVAL_S (default 300)
    - Aggregated health endpoint across all accounts
    - Degraded startup when no qualifying accounts found
    """

    def __init__(
        self,
        process_config: GoogleCalendarProcessConfig,
        db_pool: asyncpg.Pool,
        cursor_pool: asyncpg.Pool | None,
    ) -> None:
        self._process_config = process_config
        self._db_pool = db_pool
        self._cursor_pool = cursor_pool

        # Active account loops keyed by email
        self._loops: dict[str, GoogleCalendarAccountLoop] = {}

        # State
        self._start_time = time.time()
        self._running = False

        # Health server
        self._health_server: uvicorn.Server | None = None
        self._health_thread: Thread | None = None

        # Rescan task
        self._rescan_task: asyncio.Task[None] | None = None

        # Credential store (shared across accounts for app credentials)
        self._credential_store: CredentialStore | None = None

    def _get_credential_store(self) -> CredentialStore:
        """Return or initialize the CredentialStore for app credentials."""
        if self._credential_store is None:
            self._credential_store = CredentialStore(self._db_pool)
        return self._credential_store

    async def _discover_qualifying_accounts(
        self,
    ) -> list[tuple[str | None, dict[str, Any] | None]]:
        """Query shared.google_accounts for active accounts with calendar scopes.

        Returns list of (email, metadata_calendar) tuples. Only accounts with
        status='active' and calendar scope in granted_scopes are returned.
        """
        try:
            async with self._db_pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT email, granted_scopes, metadata
                    FROM shared.google_accounts
                    WHERE status = 'active'
                    ORDER BY is_primary DESC, connected_at ASC
                    """
                )
        except Exception as exc:
            logger.warning(
                "Google Calendar manager: failed to query google_accounts (non-fatal): %s", exc
            )
            return []

        qualifying = []
        for row in rows:
            email = row["email"]
            scopes = list(row["granted_scopes"] or [])
            metadata = row["metadata"] or {}

            if not _has_calendar_scope(scopes):
                logger.warning(
                    "Google Calendar manager: skipping account %r"
                    " — no calendar scopes in granted_scopes=%s",
                    email,
                    scopes,
                )
                continue

            metadata_calendar: dict[str, Any] | None = None
            if isinstance(metadata, dict):
                cal_section = metadata.get("calendar")
                if isinstance(cal_section, dict):
                    metadata_calendar = cal_section

            qualifying.append((email, metadata_calendar))

        return qualifying

    async def _resolve_credentials_for_account(
        self,
        email: str,
    ) -> dict[str, str] | None:
        """Resolve OAuth credentials for a single Google account.

        Returns dict with client_id, client_secret, refresh_token on success.
        Returns None if credentials cannot be resolved (non-fatal — account is skipped).
        """
        try:
            store = self._get_credential_store()
            creds = await load_google_credentials(store, pool=self._db_pool, account=email)
            if creds is None:
                logger.warning(
                    "Google Calendar manager: no credentials found for account %r — skipping",
                    email,
                )
                return None
            return {
                "client_id": creds.client_id,
                "client_secret": creds.client_secret,
                "refresh_token": creds.refresh_token,
            }
        except InvalidGoogleCredentialsError as exc:
            logger.warning(
                "Google Calendar manager: invalid credentials for account %r (skipping): %s",
                email,
                exc,
            )
            return None
        except Exception as exc:
            logger.warning(
                "Google Calendar manager: credential resolution failed for account %r"
                " (skipping): %s",
                email,
                exc,
            )
            return None

    async def _sync_account_loops(
        self,
        qualifying: list[tuple[str | None, dict[str, Any] | None]],
    ) -> None:
        """Sync active account loops against the qualifying list.

        - Start loops for newly qualifying accounts
        - Stop loops for accounts that are no longer qualifying
        """
        qualifying_emails: set[str] = {email for (email, _) in qualifying if email is not None}

        # Stop removed accounts
        removed_emails = set(self._loops.keys()) - qualifying_emails
        for email in removed_emails:
            logger.info("Google Calendar manager: stopping loop for removed account %r", email)
            loop = self._loops.pop(email)
            try:
                await loop.stop()
            except Exception as exc:
                logger.warning(
                    "Google Calendar manager: error stopping loop for %r: %s", email, exc
                )

        # Start new accounts
        for email, metadata_calendar in qualifying:
            if email is None or email in self._loops:
                continue

            creds = await self._resolve_credentials_for_account(email)
            if creds is None:
                logger.warning(
                    "Google Calendar manager: skipping account %r — credential resolution failed",
                    email,
                )
                continue

            try:
                account_config = self._process_config.make_account_config(
                    email=email,
                    client_id=creds["client_id"],
                    client_secret=creds["client_secret"],
                    refresh_token=creds["refresh_token"],
                    metadata_calendar=metadata_calendar,
                )
                loop = GoogleCalendarAccountLoop(
                    email=email,
                    config=account_config,
                    cursor_pool=self._cursor_pool,
                    shared_pool=self._db_pool,
                )
                loop.start()
                self._loops[email] = loop
                logger.info(
                    "Google Calendar manager: started loop for account %r (endpoint_identity=%s)",
                    email,
                    account_config.connector_endpoint_identity,
                )
            except Exception as exc:
                logger.error(
                    "Google Calendar manager: failed to start loop for account %r: %s",
                    email,
                    exc,
                )

    async def _rescan_loop(self) -> None:
        """Periodically re-scan google_accounts and sync active loops."""
        interval = self._process_config.gcal_account_rescan_interval_s
        while self._running:
            await asyncio.sleep(interval)
            if not self._running:
                break
            logger.debug("Google Calendar manager: periodic account re-scan")
            try:
                qualifying = await self._discover_qualifying_accounts()
                await self._sync_account_loops(qualifying)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "Google Calendar manager: account re-scan error (non-fatal): %s", exc
                )

    def _get_multi_account_health(self) -> MultiAccountHealthStatus:
        """Build aggregated health status across all account loops."""
        uptime = time.time() - self._start_time
        account_statuses = [loop.get_health() for loop in self._loops.values()]

        # Aggregate worst-case status
        if not account_statuses:
            agg_status: Literal["healthy", "degraded", "error"] = "degraded"
        elif any(s.status == "error" for s in account_statuses):
            agg_status = "error"
        elif any(s.status == "degraded" for s in account_statuses):
            agg_status = "degraded"
        else:
            agg_status = "healthy"

        return MultiAccountHealthStatus(
            status=agg_status,
            uptime_seconds=uptime,
            active_accounts=len(self._loops),
            account_health=account_statuses,
            timestamp=datetime.now(UTC).isoformat(),
        )

    def _start_health_server(self, port: int) -> None:
        """Start the FastAPI health/metrics HTTP server in a background thread."""
        app = FastAPI(title="Google Calendar Connector Health")

        @app.get("/health")
        async def health() -> MultiAccountHealthStatus:
            return self._get_multi_account_health()

        @app.get("/metrics")
        async def metrics() -> bytes:
            return generate_latest(REGISTRY)

        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        self._health_server = uvicorn.Server(config)

        def _run() -> None:
            asyncio.run(self._health_server.serve())  # type: ignore[union-attr]

        self._health_thread = Thread(target=_run, daemon=True)
        self._health_thread.start()
        logger.info("Google Calendar connector: health server started on port %d", port)

    async def start(self) -> None:
        """Start the manager: discover accounts, start loops, run until cancelled."""
        self._running = True

        # Start health server
        port = self._process_config.connector_health_port
        if port > 0:
            self._start_health_server(port)

        # Initial account discovery
        qualifying = await self._discover_qualifying_accounts()
        await self._sync_account_loops(qualifying)

        if not self._loops:
            logger.warning(
                "Google Calendar manager: no qualifying accounts found at startup."
                " Running in idle/degraded mode."
                " Will retry at rescan interval=%ds.",
                self._process_config.gcal_account_rescan_interval_s,
            )

        # Start periodic re-scan
        self._rescan_task = asyncio.create_task(self._rescan_loop(), name="gcal-rescan")

        # Install SIGTERM/SIGINT handlers
        loop = asyncio.get_running_loop()
        shutdown_event = asyncio.Event()

        def _handle_signal() -> None:
            logger.info("Google Calendar manager: shutdown signal received")
            shutdown_event.set()

        try:
            loop.add_signal_handler(signal.SIGTERM, _handle_signal)
            loop.add_signal_handler(signal.SIGINT, _handle_signal)
        except (NotImplementedError, OSError):
            # Signal handlers not available in all environments (e.g. tests)
            pass

        # Wait for shutdown
        try:
            await shutdown_event.wait()
        except asyncio.CancelledError:
            pass

        # Graceful shutdown
        logger.info("Google Calendar manager: shutting down")
        self._running = False

        if self._rescan_task and not self._rescan_task.done():
            self._rescan_task.cancel()
            try:
                await self._rescan_task
            except (asyncio.CancelledError, Exception):
                pass

        # Stop all account loops
        stop_tasks = [loop_obj.stop() for loop_obj in self._loops.values()]
        if stop_tasks:
            await asyncio.gather(*stop_tasks, return_exceptions=True)

        logger.info("Google Calendar manager: shutdown complete")


# ---------------------------------------------------------------------------
# DB pool helpers
# ---------------------------------------------------------------------------


async def _create_shared_db_pool() -> asyncpg.Pool:
    """Create an asyncpg pool connected to the shared database for account discovery."""
    import asyncpg as _asyncpg

    db_params = db_params_from_env()
    shared_db_name = shared_db_name_from_env()
    shared_schema = os.environ.get("BUTLER_SHARED_DB_SCHEMA", "shared").strip() or "shared"

    pool_kwargs: dict[str, Any] = {
        "host": db_params["host"],
        "port": db_params["port"],
        "user": db_params["user"],
        "password": db_params["password"],
        "database": shared_db_name,
        "min_size": 1,
        "max_size": 4,
        "command_timeout": 10,
    }

    if shared_schema:
        try:
            search_path = schema_search_path(shared_schema)
            pool_kwargs["server_settings"] = {"search_path": search_path}
        except ValueError as exc:
            logger.debug(
                "Google Calendar manager: invalid shared schema %r (non-fatal): %s",
                shared_schema,
                exc,
            )

    configured_ssl = db_params.get("ssl")
    if configured_ssl is not None:
        pool_kwargs["ssl"] = configured_ssl

    try:
        return await _asyncpg.create_pool(**pool_kwargs)
    except Exception as exc:
        ssl_str = configured_ssl if isinstance(configured_ssl, str) else None
        if should_retry_with_ssl_disable(exc, ssl_str):
            pool_kwargs["ssl"] = "disable"
            return await _asyncpg.create_pool(**pool_kwargs)
        raise


# ---------------------------------------------------------------------------
# Async entrypoint
# ---------------------------------------------------------------------------


async def run_google_calendar_connector() -> None:
    """Run the multi-account Google Calendar connector manager (async entrypoint).

    Discovers all active Google accounts with calendar scopes from shared.google_accounts
    and manages independent ingestion loops per account. Identity is derived per-account
    from the email address (``google_calendar:user:<email>``).
    Runs in idle/degraded mode if no qualifying accounts are found at startup.
    """
    configure_logging(level="INFO", butler_name="google_calendar")

    # Step 1: Parse process-level config from environment variables.
    try:
        process_config = GoogleCalendarProcessConfig.from_env()
    except Exception as exc:
        logger.error("Google Calendar connector: failed to load process config: %s", exc)
        raise

    # Step 2: Create DB pools.
    from butlers.connectors.cursor_store import create_cursor_pool_from_env

    try:
        shared_pool = await _create_shared_db_pool()
        logger.info("Google Calendar connector: shared DB pool created for account discovery")
    except Exception as exc:
        logger.error("Google Calendar connector: failed to create shared DB pool: %s", exc)
        raise

    try:
        cursor_pool = await create_cursor_pool_from_env()
        logger.info("Google Calendar connector: cursor pool created for DB-backed checkpoints")
    except Exception as exc:
        logger.error("Google Calendar connector: failed to create cursor pool: %s", exc)
        await shared_pool.close()
        raise

    # Step 3: Start the multi-account manager.
    manager = GoogleCalendarConnectorManager(
        process_config=process_config,
        db_pool=shared_pool,
        cursor_pool=cursor_pool,
    )
    try:
        await manager.start()
    finally:
        await shared_pool.close()
        if cursor_pool is not None:
            await cursor_pool.close()


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entrypoint for Google Calendar connector.

    Discovers and manages all Calendar-scoped Google accounts from shared.google_accounts.
    Identity is derived per-account from the email address.
    """
    asyncio.run(run_google_calendar_connector())


if __name__ == "__main__":
    main()
