"""Gmail connector runtime for live ingestion via watch/history delta flow.

This connector implements the Gmail ingestion target state defined in
`docs/connectors/gmail.md`. It uses Gmail push notifications (users.watch)
combined with history-based delta fetch (users.history.list) to ingest newly
arrived mail in near real-time.

Key behaviors:
- OAuth-based authentication for Gmail API access
- Watch/history delta flow with bounded polling fallback
- Durable historyId cursor with restart-safe replay
- Idempotent submission to Switchboard MCP server via ingest tool
- Bounded in-flight requests with exponential backoff
- Explicit overload handling (no silent drops)
- Health endpoint for Kubernetes readiness/liveness probes

Environment variables (see `docs/connectors/gmail.md` section 4):
- SWITCHBOARD_MCP_URL (required)
- CONNECTOR_PROVIDER=gmail (required)
- CONNECTOR_CHANNEL=email (required)
- CONNECTOR_ENDPOINT_IDENTITY (required, e.g. "gmail:user:alice@gmail.com")
- CONNECTOR_CURSOR_PATH (required; stores last historyId)
- CONNECTOR_MAX_INFLIGHT (optional, default 8)
- CONNECTOR_HEALTH_PORT (optional, default 40082)
- DATABASE_URL or POSTGRES_* (DB connectivity for credential lookup; defaults apply if unset)
- CONNECTOR_BUTLER_DB_NAME (optional; butler DB name, defaults to 'butlers')
- CONNECTOR_BUTLER_DB_SCHEMA (optional; local butler schema for one-db mode)
- BUTLER_SHARED_DB_NAME (optional; shared credentials DB, defaults to 'butlers')
- BUTLER_SHARED_DB_SCHEMA (optional; shared credentials schema, defaults to 'shared')
- GMAIL_WATCH_RENEW_INTERVAL_S (optional, default 86400 = 1 day)
- GMAIL_POLL_INTERVAL_S (optional, default 60)
- GMAIL_PUBSUB_ENABLED (optional, default false; enables Pub/Sub push mode)
- GMAIL_PUBSUB_TOPIC (required if GMAIL_PUBSUB_ENABLED=true; GCP Pub/Sub topic)
- GMAIL_PUBSUB_WEBHOOK_PORT (optional, default 40083; port for Pub/Sub webhook)
- GMAIL_PUBSUB_WEBHOOK_PATH (optional, default /gmail/webhook; path for Pub/Sub webhook)
- GMAIL_PUBSUB_WEBHOOK_TOKEN (optional but recommended; auth token for webhook security)
"""

from __future__ import annotations

import asyncio
import base64
import html
import json
import logging
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Thread
from typing import Any, Literal

import httpx
import uvicorn
from fastapi import FastAPI, Request
from prometheus_client import REGISTRY, generate_latest
from pydantic import BaseModel, ConfigDict

from butlers.connectors.heartbeat import ConnectorHeartbeat, HeartbeatConfig
from butlers.connectors.mcp_client import CachedMCPClient
from butlers.connectors.metrics import ConnectorMetrics, get_error_type
from butlers.core.logging import configure_logging
from butlers.credential_store import CredentialStore, shared_db_name_from_env
from butlers.db import db_params_from_env, schema_search_path, should_retry_with_ssl_disable
from butlers.google_credentials import (
    InvalidGoogleCredentialsError,
    load_google_credentials,
)
from butlers.storage.blobs import BlobStore

logger = logging.getLogger(__name__)


class HealthStatus(BaseModel):
    """Health check response model for Kubernetes probes."""

    status: Literal["healthy", "unhealthy"]
    uptime_seconds: float
    last_checkpoint_save_at: str | None
    last_ingest_submit_at: str | None
    source_api_connectivity: Literal["connected", "disconnected", "unknown"]
    timestamp: str


# Supported attachment MIME types
SUPPORTED_ATTACHMENT_TYPES = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/gif",
        "image/webp",
        "application/pdf",
    }
)

# Max attachment size: 5MB
MAX_ATTACHMENT_SIZE_BYTES = 5 * 1024 * 1024


def _format_google_error(response: httpx.Response) -> str | None:
    """Extract a compact Google API/OAuth error summary from response JSON."""
    try:
        payload = response.json()
    except Exception:
        return None

    if not isinstance(payload, dict):
        return None

    # Gmail/Google API error shape:
    # {"error": {"code": 404, "message": "...", "status": "...", "errors": [{"reason": "..."}]}}
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

    # OAuth token endpoint error shape:
    # {"error": "invalid_grant", "error_description": "..."}
    if isinstance(nested_error, str) and nested_error:
        error_description = payload.get("error_description")
        if isinstance(error_description, str) and error_description:
            return f"error={nested_error}, description={error_description}"
        return f"error={nested_error}"

    return None


class GmailConnectorConfig(BaseModel):
    """Configuration for Gmail connector runtime."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Switchboard MCP
    switchboard_mcp_url: str

    # Connector identity
    connector_provider: str = "gmail"
    connector_channel: str = "email"
    connector_endpoint_identity: str
    connector_cursor_path: Path
    connector_max_inflight: int = 8

    # Health check config
    connector_health_port: int = 40082

    # Gmail API OAuth
    gmail_client_id: str
    gmail_client_secret: str
    gmail_refresh_token: str

    # Runtime controls
    gmail_watch_renew_interval_s: int = 86400  # 1 day
    gmail_poll_interval_s: int = 60

    # Pub/Sub push notification config (optional)
    gmail_pubsub_enabled: bool = False
    gmail_pubsub_topic: str | None = None
    gmail_pubsub_webhook_port: int = 40083
    gmail_pubsub_webhook_path: str = "/gmail/webhook"
    gmail_pubsub_webhook_token: str | None = None  # Optional auth token for webhook

    @classmethod
    def _load_non_secret_env_config(cls) -> dict[str, Any]:
        """Load connector config from environment variables excluding OAuth secrets."""
        cursor_path_str = os.environ.get("CONNECTOR_CURSOR_PATH")
        if not cursor_path_str:
            raise ValueError("CONNECTOR_CURSOR_PATH is required")

        max_inflight_str = os.environ.get("CONNECTOR_MAX_INFLIGHT", "8")
        try:
            max_inflight = int(max_inflight_str)
        except ValueError as exc:
            raise ValueError(
                f"CONNECTOR_MAX_INFLIGHT must be an integer, got: {max_inflight_str}"
            ) from exc

        health_port_str = os.environ.get("CONNECTOR_HEALTH_PORT", "40082")
        try:
            health_port = int(health_port_str)
        except ValueError as exc:
            raise ValueError(
                f"CONNECTOR_HEALTH_PORT must be an integer, got: {health_port_str}"
            ) from exc

        watch_renew_str = os.environ.get("GMAIL_WATCH_RENEW_INTERVAL_S", "86400")
        try:
            watch_renew_interval = int(watch_renew_str)
        except ValueError as exc:
            raise ValueError(
                f"GMAIL_WATCH_RENEW_INTERVAL_S must be an integer, got: {watch_renew_str}"
            ) from exc

        poll_interval_str = os.environ.get("GMAIL_POLL_INTERVAL_S", "60")
        try:
            poll_interval = int(poll_interval_str)
        except ValueError as exc:
            raise ValueError(
                f"GMAIL_POLL_INTERVAL_S must be an integer, got: {poll_interval_str}"
            ) from exc

        # Parse Pub/Sub config
        pubsub_enabled = os.environ.get("GMAIL_PUBSUB_ENABLED", "false").lower() in (
            "true",
            "1",
            "yes",
        )
        pubsub_topic = os.environ.get("GMAIL_PUBSUB_TOPIC")

        if pubsub_enabled and not pubsub_topic:
            raise ValueError("GMAIL_PUBSUB_TOPIC is required when GMAIL_PUBSUB_ENABLED=true")

        pubsub_webhook_port_str = os.environ.get("GMAIL_PUBSUB_WEBHOOK_PORT", "40083")
        try:
            pubsub_webhook_port = int(pubsub_webhook_port_str)
        except ValueError as exc:
            raise ValueError(
                f"GMAIL_PUBSUB_WEBHOOK_PORT must be an integer, got: {pubsub_webhook_port_str}"
            ) from exc

        pubsub_webhook_path = os.environ.get("GMAIL_PUBSUB_WEBHOOK_PATH", "/gmail/webhook")
        pubsub_webhook_token = os.environ.get("GMAIL_PUBSUB_WEBHOOK_TOKEN")

        return {
            "switchboard_mcp_url": os.environ["SWITCHBOARD_MCP_URL"],
            "connector_provider": os.environ.get("CONNECTOR_PROVIDER", "gmail"),
            "connector_channel": os.environ.get("CONNECTOR_CHANNEL", "email"),
            "connector_endpoint_identity": os.environ["CONNECTOR_ENDPOINT_IDENTITY"],
            "connector_cursor_path": Path(cursor_path_str),
            "connector_max_inflight": max_inflight,
            "connector_health_port": health_port,
            "gmail_watch_renew_interval_s": watch_renew_interval,
            "gmail_poll_interval_s": poll_interval,
            "gmail_pubsub_enabled": pubsub_enabled,
            "gmail_pubsub_topic": pubsub_topic,
            "gmail_pubsub_webhook_port": pubsub_webhook_port,
            "gmail_pubsub_webhook_path": pubsub_webhook_path,
            "gmail_pubsub_webhook_token": pubsub_webhook_token,
        }

    @classmethod
    def from_env(
        cls,
        *,
        gmail_client_id: str,
        gmail_client_secret: str,
        gmail_refresh_token: str,
        gmail_pubsub_webhook_token: str | None = None,
    ) -> GmailConnectorConfig:
        """Load non-secret env config and inject DB-resolved Google OAuth credentials."""
        config_kwargs = cls._load_non_secret_env_config()
        sanitized_credentials = {
            "gmail_client_id": gmail_client_id.strip(),
            "gmail_client_secret": gmail_client_secret.strip(),
            "gmail_refresh_token": gmail_refresh_token.strip(),
        }
        missing = [key for key, value in sanitized_credentials.items() if not value]
        if missing:
            raise ValueError(
                "DB-resolved Gmail credentials missing required value(s): " + ", ".join(missing)
            )
        config_kwargs.update(sanitized_credentials)
        if gmail_pubsub_webhook_token is not None:
            config_kwargs["gmail_pubsub_webhook_token"] = gmail_pubsub_webhook_token
        return cls(**config_kwargs)


class GmailCursor(BaseModel):
    """Durable checkpoint state for Gmail history tracking."""

    model_config = ConfigDict(extra="forbid")

    history_id: str
    last_updated_at: str  # ISO 8601 timestamp


class GmailConnectorRuntime:
    """Gmail connector runtime using watch/history delta flow."""

    def __init__(self, config: GmailConnectorConfig, blob_store: BlobStore | None = None) -> None:
        self._config = config
        self._blob_store = blob_store
        self._http_client: httpx.AsyncClient | None = None
        self._mcp_client = CachedMCPClient(
            config.switchboard_mcp_url, client_name="gmail-connector"
        )
        self._access_token: str | None = None
        self._token_expires_at: datetime | None = None
        self._running = False
        self._semaphore = asyncio.Semaphore(config.connector_max_inflight)

        # Metrics
        self._metrics = ConnectorMetrics(
            connector_type="gmail",
            endpoint_identity=config.connector_endpoint_identity,
        )

        # Health tracking
        self._start_time = time.time()
        self._last_checkpoint_save: float | None = None
        self._last_ingest_submit: float | None = None
        self._source_api_ok: bool | None = None
        self._health_server: uvicorn.Server | None = None
        self._health_thread: Thread | None = None

        # Pub/Sub webhook tracking
        self._webhook_server: uvicorn.Server | None = None
        self._webhook_thread: Thread | None = None
        self._watch_expiration: datetime | None = None
        self._notification_queue: asyncio.Queue[dict[str, Any]] | None = None

        # Heartbeat
        self._heartbeat: ConnectorHeartbeat | None = None
        self._last_history_id: str | None = None

    async def get_health_status(self) -> HealthStatus:
        """Get current health status for Kubernetes probes."""
        uptime = time.time() - self._start_time

        last_checkpoint_save_at = None
        if self._last_checkpoint_save is not None:
            last_checkpoint_save_at = datetime.fromtimestamp(
                self._last_checkpoint_save, UTC
            ).isoformat()

        last_ingest_submit_at = None
        if self._last_ingest_submit is not None:
            last_ingest_submit_at = datetime.fromtimestamp(
                self._last_ingest_submit, UTC
            ).isoformat()

        if self._source_api_ok is None:
            connectivity = "unknown"
        elif self._source_api_ok:
            connectivity = "connected"
        else:
            connectivity = "disconnected"

        # Determine overall status
        status = "healthy"
        if self._source_api_ok is False:
            status = "unhealthy"

        return HealthStatus(
            status=status,
            uptime_seconds=uptime,
            last_checkpoint_save_at=last_checkpoint_save_at,
            last_ingest_submit_at=last_ingest_submit_at,
            source_api_connectivity=connectivity,
            timestamp=datetime.now(UTC).isoformat(),
        )

    def _start_health_server(self) -> None:
        """Start FastAPI health check server in background thread."""
        app = FastAPI(title="Gmail Connector Health")

        @app.get("/health")
        async def health() -> HealthStatus:
            return await self.get_health_status()

        @app.get("/metrics")
        async def metrics() -> bytes:
            """Prometheus metrics endpoint."""
            return generate_latest(REGISTRY)

        config = uvicorn.Config(
            app,
            host="0.0.0.0",
            port=self._config.connector_health_port,
            log_level="warning",
        )
        self._health_server = uvicorn.Server(config)

        def run_server() -> None:
            asyncio.run(self._health_server.serve())

        self._health_thread = Thread(target=run_server, daemon=True)
        self._health_thread.start()
        logger.info(
            "Health server started",
            extra={"port": self._config.connector_health_port},
        )

    def _start_webhook_server(self) -> None:
        """Start FastAPI webhook server for Pub/Sub notifications in background thread."""
        app = FastAPI(title="Gmail Pub/Sub Webhook")

        @app.post(self._config.gmail_pubsub_webhook_path)
        async def webhook(request: Request) -> dict[str, str]:
            """Handle incoming Pub/Sub push notifications."""
            try:
                # Verify webhook token if configured
                if self._config.gmail_pubsub_webhook_token:
                    auth_header = request.headers.get("Authorization", "")
                    expected_token = f"Bearer {self._config.gmail_pubsub_webhook_token}"
                    if auth_header != expected_token:
                        logger.warning("Webhook request with invalid or missing auth token")
                        return {"status": "unauthorized"}

                body = await request.json()
                logger.debug("Received Pub/Sub notification: %s", body)

                # Queue notification for processing
                if self._notification_queue:
                    await self._notification_queue.put(body)
                else:
                    logger.warning("Notification queue not initialized, dropping notification")

                return {"status": "accepted"}
            except Exception as exc:
                logger.error("Error handling webhook notification: %s", exc, exc_info=True)
                return {"status": "error", "message": str(exc)}

        config = uvicorn.Config(
            app,
            host="0.0.0.0",
            port=self._config.gmail_pubsub_webhook_port,
            log_level="warning",
        )
        self._webhook_server = uvicorn.Server(config)

        def run_server() -> None:
            asyncio.run(self._webhook_server.serve())

        self._webhook_thread = Thread(target=run_server, daemon=True)
        self._webhook_thread.start()
        logger.info(
            "Pub/Sub webhook server started",
            extra={
                "port": self._config.gmail_pubsub_webhook_port,
                "path": self._config.gmail_pubsub_webhook_path,
            },
        )

    def _start_heartbeat(self) -> None:
        """Initialize and start heartbeat background task."""
        heartbeat_config = HeartbeatConfig.from_env(
            connector_type=self._config.connector_provider,
            endpoint_identity=self._config.connector_endpoint_identity,
            version=None,  # Could be set from env or git sha
        )

        self._heartbeat = ConnectorHeartbeat(
            config=heartbeat_config,
            mcp_client=self._mcp_client,
            metrics=self._metrics,
            get_health_state=self._get_health_state,
            get_checkpoint=self._get_checkpoint,
        )

        self._heartbeat.start()

    def _get_health_state(self) -> tuple[str, str | None]:
        """Determine current health state for heartbeat.

        Returns:
            Tuple of (state, error_message) where state is one of:
            "healthy", "degraded", "error"
        """
        if self._source_api_ok is False:
            return ("error", "Gmail API unreachable or authentication failed")

        # Could add degraded state for high error rates
        return ("healthy", None)

    def _get_checkpoint(self) -> tuple[str | None, datetime | None]:
        """Get current checkpoint state for heartbeat.

        Returns:
            Tuple of (cursor, updated_at)
        """
        cursor = self._last_history_id
        updated_at = (
            datetime.fromtimestamp(self._last_checkpoint_save, UTC)
            if self._last_checkpoint_save is not None
            else None
        )
        return (cursor, updated_at)

    async def start(self) -> None:
        """Start the Gmail connector runtime."""
        self._running = True
        self._http_client = httpx.AsyncClient(timeout=30.0)

        # Start health server
        self._start_health_server()

        # Start heartbeat
        self._start_heartbeat()

        # Start Pub/Sub webhook server if enabled
        if self._config.gmail_pubsub_enabled:
            self._notification_queue = asyncio.Queue()
            self._start_webhook_server()
            # Start Gmail watch
            try:
                await self._gmail_watch_start()
            except Exception as exc:
                logger.error("Failed to start Gmail watch, falling back to polling: %s", exc)
                self._config = self._config.model_copy(update={"gmail_pubsub_enabled": False})

        logger.info(
            "Gmail connector starting: cursor=%s, pubsub=%s",
            self._config.connector_cursor_path,
            self._config.gmail_pubsub_enabled,
        )
        logger.debug(
            "Gmail connector endpoint: %s",
            self._config.connector_endpoint_identity,
        )

        # Ensure cursor file exists
        await self._ensure_cursor_file()

        # Main ingestion loop
        try:
            await self._run_ingestion_loop()
        finally:
            await self.stop()

    async def stop(self) -> None:
        """Stop the Gmail connector runtime."""
        self._running = False
        # Stop heartbeat
        if self._heartbeat is not None:
            await self._heartbeat.stop()

        await self._mcp_client.aclose()
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None
        logger.info("Gmail connector stopped")

    async def _run_ingestion_loop(self) -> None:
        """Main ingestion loop: process notifications or poll for history changes."""
        if self._config.gmail_pubsub_enabled:
            await self._run_pubsub_ingestion_loop()
        else:
            await self._run_polling_ingestion_loop()

    async def _run_polling_ingestion_loop(self) -> None:
        """Polling-based ingestion loop: poll for history changes and ingest new messages."""
        while self._running:
            try:
                # Load current cursor
                cursor = await self._load_cursor()

                # Fetch history changes since last cursor
                history_changes = await self._fetch_history_changes(cursor.history_id)

                if history_changes:
                    logger.info("Found %d history changes to process", len(history_changes))

                    # Process each history change (extract message IDs)
                    message_ids = self._extract_message_ids_from_history(history_changes)

                    # Fetch and ingest each new message
                    await self._ingest_messages(message_ids)

                    # Update cursor to latest history ID
                    latest_history_id = history_changes[-1].get("id", cursor.history_id)
                    await self._save_cursor(
                        GmailCursor(
                            history_id=latest_history_id,
                            last_updated_at=datetime.now(UTC).isoformat(),
                        )
                    )
                else:
                    logger.debug("No new history changes")

            except Exception as exc:
                logger.error("Error in polling ingestion loop: %s", exc, exc_info=True)
                # Back off on error
                await asyncio.sleep(min(60, self._config.gmail_poll_interval_s * 2))

            # Wait before next poll
            await asyncio.sleep(self._config.gmail_poll_interval_s)

    async def _run_pubsub_ingestion_loop(self) -> None:
        """Pub/Sub-based ingestion loop: process push notifications with polling fallback."""
        last_poll_time = time.time()
        poll_fallback_interval = max(
            300, self._config.gmail_poll_interval_s * 5
        )  # Poll every 5 minutes minimum

        while self._running:
            try:
                # Renew watch if needed
                await self._gmail_watch_renew_if_needed()

                # Wait for notification with timeout
                notification_received = False
                try:
                    if self._notification_queue:
                        # Wait for notification with timeout
                        timeout = self._config.gmail_poll_interval_s
                        await asyncio.wait_for(self._notification_queue.get(), timeout=timeout)
                        notification_received = True
                        logger.debug("Received push notification, triggering history fetch")
                except TimeoutError:
                    # No notification received, check if we should do fallback poll
                    current_time = time.time()
                    if current_time - last_poll_time >= poll_fallback_interval:
                        logger.debug(
                            "No notifications for %ds, running fallback poll",
                            poll_fallback_interval,
                        )
                        notification_received = True  # Trigger history fetch
                        last_poll_time = current_time

                if notification_received:
                    # Load current cursor
                    cursor = await self._load_cursor()

                    # Fetch history changes since last cursor
                    history_changes = await self._fetch_history_changes(cursor.history_id)

                    if history_changes:
                        logger.info("Found %d history changes to process", len(history_changes))

                        # Process each history change (extract message IDs)
                        message_ids = self._extract_message_ids_from_history(history_changes)

                        # Fetch and ingest each new message
                        await self._ingest_messages(message_ids)

                        # Update cursor to latest history ID
                        latest_history_id = history_changes[-1].get("id", cursor.history_id)
                        await self._save_cursor(
                            GmailCursor(
                                history_id=latest_history_id,
                                last_updated_at=datetime.now(UTC).isoformat(),
                            )
                        )
                    else:
                        logger.debug("No new history changes")

                    # Update last poll time
                    last_poll_time = time.time()

            except Exception as exc:
                logger.error("Error in Pub/Sub ingestion loop: %s", exc, exc_info=True)
                # Back off on error
                await asyncio.sleep(min(60, self._config.gmail_poll_interval_s * 2))

    async def _ensure_cursor_file(self) -> None:
        """Ensure cursor file exists with initial state if missing."""
        if not self._config.connector_cursor_path.exists():
            # Initialize with current history ID from Gmail
            try:
                profile = await self._gmail_get_profile()
                initial_history_id = profile.get("historyId", "1")
                initial_cursor = GmailCursor(
                    history_id=initial_history_id,
                    last_updated_at=datetime.now(UTC).isoformat(),
                )
                await self._save_cursor(initial_cursor)
                logger.info(
                    "Initialized cursor file with historyId=%s at %s",
                    initial_history_id,
                    self._config.connector_cursor_path,
                )
            except Exception as exc:
                logger.warning(
                    "Failed to fetch initial historyId from Gmail: %s. Using default.", exc
                )
                initial_cursor = GmailCursor(
                    history_id="1",
                    last_updated_at=datetime.now(UTC).isoformat(),
                )
                await self._save_cursor(initial_cursor)

    async def _load_cursor(self) -> GmailCursor:
        """Load cursor state from disk."""
        if not self._config.connector_cursor_path.exists():
            raise RuntimeError(f"Cursor file not found: {self._config.connector_cursor_path}")

        cursor_data = json.loads(self._config.connector_cursor_path.read_text())
        cursor = GmailCursor.model_validate(cursor_data)

        # Track last history_id for heartbeat checkpoint
        self._last_history_id = cursor.history_id

        return cursor

    async def _save_cursor(self, cursor: GmailCursor) -> None:
        """Save cursor state to disk."""
        try:
            self._config.connector_cursor_path.parent.mkdir(parents=True, exist_ok=True)
            self._config.connector_cursor_path.write_text(cursor.model_dump_json(indent=2))

            # Track last history_id for heartbeat checkpoint
            self._last_history_id = cursor.history_id

            # Record successful checkpoint save
            self._last_checkpoint_save = time.time()
            self._metrics.record_checkpoint_save(status="success")

            logger.debug("Saved cursor: historyId=%s", cursor.history_id)
        except Exception as exc:
            self._metrics.record_checkpoint_save(status="error")
            self._metrics.record_error(error_type=get_error_type(exc), operation="checkpoint_save")
            raise

    async def _get_access_token(self) -> str:
        """Get valid OAuth access token (refresh if expired)."""
        if self._access_token and self._token_expires_at:
            if datetime.now(UTC) < self._token_expires_at:
                return self._access_token

        # Refresh token
        if not self._http_client:
            raise RuntimeError("HTTP client not initialized")

        try:
            response = await self._http_client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": self._config.gmail_client_id,
                    "client_secret": self._config.gmail_client_secret,
                    "refresh_token": self._config.gmail_refresh_token,
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

            # Mark API as connected on successful token refresh
            self._source_api_ok = True
            self._metrics.record_source_api_call(api_method="token_refresh", status="success")

            logger.debug("Refreshed OAuth access token (expires in %ds)", expires_in)
            return self._access_token
        except Exception as exc:
            # Mark API as disconnected on failure
            self._source_api_ok = False
            self._metrics.record_source_api_call(api_method="token_refresh", status="error")
            self._metrics.record_error(error_type=get_error_type(exc), operation="token_refresh")
            raise

    async def _gmail_get_profile(self) -> dict[str, Any]:
        """Fetch Gmail profile to get current historyId."""
        if not self._http_client:
            raise RuntimeError("HTTP client not initialized")

        try:
            token = await self._get_access_token()
            response = await self._http_client.get(
                "https://gmail.googleapis.com/gmail/v1/users/me/profile",
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()

            # Mark API as connected on success
            self._source_api_ok = True
            self._metrics.record_source_api_call(api_method="profile.get", status="success")

            return response.json()
        except Exception as exc:
            # Mark API as disconnected on failure
            self._source_api_ok = False
            self._metrics.record_source_api_call(api_method="profile.get", status="error")
            self._metrics.record_error(error_type=get_error_type(exc), operation="fetch_profile")
            raise

    async def _gmail_watch_start(self) -> dict[str, Any]:
        """Start Gmail watch for push notifications via Pub/Sub."""
        if not self._http_client:
            raise RuntimeError("HTTP client not initialized")

        if not self._config.gmail_pubsub_topic:
            raise RuntimeError("Pub/Sub topic not configured")

        try:
            token = await self._get_access_token()
            response = await self._http_client.post(
                "https://gmail.googleapis.com/gmail/v1/users/me/watch",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "topicName": self._config.gmail_pubsub_topic,
                    "labelIds": ["INBOX"],  # Watch inbox by default
                },
            )
            response.raise_for_status()

            watch_response = response.json()
            # Extract expiration timestamp
            expiration_ms = int(watch_response.get("expiration", 0))
            if expiration_ms:
                self._watch_expiration = datetime.fromtimestamp(expiration_ms / 1000, UTC)
                logger.info(
                    "Gmail watch started, expires at %s",
                    self._watch_expiration.isoformat(),
                )
            else:
                logger.warning("Watch response did not include expiration timestamp")

            return watch_response
        except Exception:
            logger.error("Failed to start Gmail watch", exc_info=True)
            raise

    async def _gmail_watch_renew_if_needed(self) -> None:
        """Renew Gmail watch if approaching expiration."""
        if not self._config.gmail_pubsub_enabled:
            return

        # Renew if no expiration set or within renewal interval of expiration
        now = datetime.now(UTC)
        should_renew = (
            self._watch_expiration is None
            or (self._watch_expiration - now).total_seconds()
            < self._config.gmail_watch_renew_interval_s
        )

        if should_renew:
            try:
                await self._gmail_watch_start()
            except Exception as exc:
                logger.error("Failed to renew Gmail watch: %s", exc, exc_info=True)

    async def _fetch_history_changes(self, start_history_id: str) -> list[dict[str, Any]]:
        """Fetch history changes since the given historyId."""
        if not self._http_client:
            raise RuntimeError("HTTP client not initialized")

        try:
            token = await self._get_access_token()

            # Gmail history.list API
            response = await self._http_client.get(
                "https://gmail.googleapis.com/gmail/v1/users/me/history",
                params={"startHistoryId": start_history_id},
                headers={"Authorization": f"Bearer {token}"},
            )

            if response.status_code == 404:
                # History ID too old, reset to current
                logger.warning("History ID %s is too old, resetting to current", start_history_id)
                google_error = _format_google_error(response)
                if google_error:
                    logger.warning("Gmail history.list 404 details: %s", google_error)
                self._metrics.record_source_api_call(api_method="history.list", status="reset")
                profile = await self._gmail_get_profile()
                new_history_id = profile.get("historyId", start_history_id)
                await self._save_cursor(
                    GmailCursor(
                        history_id=new_history_id,
                        last_updated_at=datetime.now(UTC).isoformat(),
                    )
                )
                return []

            if response.is_error:
                google_error = _format_google_error(response)
                if google_error:
                    logger.error(
                        "Gmail history.list failed status=%s startHistoryId=%s details=%s",
                        response.status_code,
                        start_history_id,
                        google_error,
                    )
                else:
                    logger.error(
                        "Gmail history.list failed status=%s startHistoryId=%s",
                        response.status_code,
                        start_history_id,
                    )

            response.raise_for_status()
            data = response.json()

            # Mark API as connected on success
            self._source_api_ok = True
            self._metrics.record_source_api_call(api_method="history.list", status="success")

            return data.get("history", [])
        except Exception as exc:
            # Mark API as disconnected on failure
            self._source_api_ok = False
            self._metrics.record_source_api_call(api_method="history.list", status="error")
            self._metrics.record_error(error_type=get_error_type(exc), operation="fetch_history")
            raise

    def _extract_message_ids_from_history(self, history: list[dict[str, Any]]) -> list[str]:
        """Extract new message IDs from history changes."""
        message_ids: set[str] = set()

        for record in history:
            # History records contain messagesAdded, messagesDeleted, labelsAdded, labelsRemoved
            # We only care about messagesAdded for ingestion
            for added in record.get("messagesAdded", []):
                message = added.get("message", {})
                message_id = message.get("id")
                if message_id:
                    message_ids.add(message_id)

        return list(message_ids)

    async def _ingest_messages(self, message_ids: list[str]) -> None:
        """Fetch and ingest messages concurrently with bounded parallelism."""
        tasks = [self._ingest_single_message(msg_id) for msg_id in message_ids]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _ingest_single_message(self, message_id: str) -> None:
        """Fetch and ingest a single Gmail message."""
        async with self._semaphore:
            try:
                # Fetch message metadata and payload
                message_data = await self._fetch_message(message_id)

                # Build ingest.v1 envelope
                envelope = await self._build_ingest_envelope(message_data)

                # Submit to Switchboard ingest API
                await self._submit_to_ingest_api(envelope)

                logger.info("Ingested message: %s", message_id)

            except Exception as exc:
                logger.error("Failed to ingest message %s: %s", message_id, exc, exc_info=True)

    async def _fetch_message(self, message_id: str) -> dict[str, Any]:
        """Fetch full message metadata and payload from Gmail API."""
        if not self._http_client:
            raise RuntimeError("HTTP client not initialized")

        try:
            token = await self._get_access_token()
            response = await self._http_client.get(
                f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}",
                params={"format": "full"},
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()

            # Mark API as connected on success
            self._source_api_ok = True
            self._metrics.record_source_api_call(api_method="messages.get", status="success")

            return response.json()
        except Exception as exc:
            # Mark API as disconnected on failure
            self._source_api_ok = False
            self._metrics.record_source_api_call(api_method="messages.get", status="error")
            self._metrics.record_error(error_type=get_error_type(exc), operation="fetch_message")
            raise

    async def _build_ingest_envelope(self, message_data: dict[str, Any]) -> dict[str, Any]:
        """Build ingest.v1 envelope from Gmail message data."""
        message_id = message_data.get("id", "unknown")
        thread_id = message_data.get("threadId")
        internal_date = message_data.get("internalDate", "0")

        # Parse headers
        headers = {
            h["name"]: h["value"] for h in message_data.get("payload", {}).get("headers", [])
        }
        subject = headers.get("Subject", "(no subject)")
        from_address = headers.get("From", "unknown")
        rfc_message_id = headers.get("Message-ID", message_id)

        # Extract body
        body = self._extract_body_from_payload(message_data.get("payload", {}))

        # Build normalized text
        normalized_text = f"Subject: {html.escape(subject)}\n\n{html.escape(body)}"

        # Process attachments
        attachments = await self._process_attachments(message_id, message_data.get("payload", {}))

        # Observed timestamp
        try:
            observed_timestamp_ms = int(internal_date)
            observed_at = datetime.fromtimestamp(observed_timestamp_ms / 1000, tz=UTC)
        except (ValueError, OSError):
            observed_at = datetime.now(UTC)

        return {
            "schema_version": "ingest.v1",
            "source": {
                "channel": self._config.connector_channel,
                "provider": self._config.connector_provider,
                "endpoint_identity": self._config.connector_endpoint_identity,
            },
            "event": {
                "external_event_id": rfc_message_id,
                "external_thread_id": thread_id,
                "observed_at": observed_at.isoformat(),
            },
            "sender": {
                "identity": from_address,
            },
            "payload": {
                "raw": message_data,
                "normalized_text": normalized_text,
                "attachments": attachments,
            },
            "control": {
                "policy_tier": "default",
            },
        }

    def _extract_body_from_payload(self, payload: dict[str, Any], depth: int = 0) -> str:
        """Recursively extract body text from Gmail message payload."""
        # Prevent stack overflow from malicious deeply nested messages
        if depth > 20:
            logger.warning("Maximum recursion depth reached in email parsing")
            return "(body too deeply nested)"
        # Try to extract text/plain part
        mime_type = payload.get("mimeType", "")
        if mime_type == "text/plain":
            body_data = payload.get("body", {}).get("data", "")
            if body_data:
                return base64.urlsafe_b64decode(body_data).decode("utf-8", errors="replace")

        # If multipart, recurse into parts
        parts = payload.get("parts", [])
        if parts:
            for part in parts:
                body = self._extract_body_from_payload(part, depth + 1)
                if body and body != "(no body)":
                    return body

        return "(no body)"

    def _extract_attachments(self, payload: dict[str, Any], depth: int = 0) -> list[dict[str, Any]]:
        """Walk MIME parts tree, identify supported attachments.

        Returns list of dicts with:
            - filename: str | None
            - mime_type: str
            - attachment_id: str
            - size_bytes: int
        """
        if depth > 20:
            logger.warning("Maximum recursion depth reached in attachment extraction")
            return []

        attachments = []
        mime_type = payload.get("mimeType", "")

        # Check if this part is an attachment
        body = payload.get("body", {})
        attachment_id = body.get("attachmentId")
        size = body.get("size", 0)

        # Get filename from part
        filename = payload.get("filename")

        # Criteria for attachment:
        # 1. Has attachmentId (Gmail API marks it as attachment)
        # 2. Has supported MIME type
        # 3. Not a text body part

        has_attachment_id = bool(attachment_id)
        is_supported_type = mime_type in SUPPORTED_ATTACHMENT_TYPES

        if has_attachment_id and is_supported_type:
            attachments.append(
                {
                    "filename": filename or None,
                    "mime_type": mime_type,
                    "attachment_id": attachment_id,
                    "size_bytes": size,
                }
            )

        # Recurse into multipart
        parts = payload.get("parts", [])
        for part in parts:
            attachments.extend(self._extract_attachments(part, depth + 1))

        return attachments

    async def _download_gmail_attachment(self, message_id: str, attachment_id: str) -> bytes:
        """Download attachment data from Gmail API.

        Args:
            message_id: Gmail message ID
            attachment_id: Attachment ID from message part

        Returns:
            Binary attachment data

        Raises:
            Exception: If download fails
        """
        token = await self._get_access_token()

        if not self._http_client:
            raise RuntimeError("HTTP client not initialized")

        url = f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}/attachments/{attachment_id}"
        headers = {"Authorization": f"Bearer {token}"}

        try:
            response = await self._http_client.get(url, headers=headers)
            response.raise_for_status()

            data = response.json()
            # Gmail returns base64url-encoded data
            attachment_data_b64 = data.get("data", "")
            if not attachment_data_b64:
                raise ValueError(f"No data in attachment response for {attachment_id}")

            # Decode base64url
            attachment_bytes = base64.urlsafe_b64decode(attachment_data_b64)
            return attachment_bytes

        except httpx.HTTPStatusError as exc:
            logger.error(
                "Failed to download attachment: %s (status=%d)",
                attachment_id,
                exc.response.status_code,
            )
            raise
        except Exception as exc:
            logger.error("Failed to download attachment %s: %s", attachment_id, exc)
            raise

    async def _process_attachments(
        self, message_id: str, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], ...] | None:
        """Extract and store attachments from message payload.

        Args:
            message_id: Gmail message ID for downloading attachments
            payload: Gmail message payload dict

        Returns:
            Tuple of IngestAttachment dicts, or None if no attachments
        """
        if not self._blob_store:
            # Blob storage not configured, skip attachments
            return None

        # Extract attachment metadata
        attachment_metas = self._extract_attachments(payload)
        if not attachment_metas:
            return None

        processed_attachments = []

        for meta in attachment_metas:
            attachment_id = meta["attachment_id"]
            size_bytes = meta["size_bytes"]
            mime_type = meta["mime_type"]
            filename = meta["filename"]

            # Skip oversized attachments
            if size_bytes > MAX_ATTACHMENT_SIZE_BYTES:
                logger.warning(
                    "Skipping oversized attachment: %s (%d bytes > %d bytes limit)",
                    filename or attachment_id,
                    size_bytes,
                    MAX_ATTACHMENT_SIZE_BYTES,
                )
                continue

            try:
                # Download attachment bytes
                attachment_bytes = await self._download_gmail_attachment(message_id, attachment_id)

                # Store via blob storage
                storage_ref = await self._blob_store.put(
                    attachment_bytes,
                    content_type=mime_type,
                    filename=filename,
                )

                # Build IngestAttachment dict
                attachment_dict = {
                    "media_type": mime_type,
                    "storage_ref": storage_ref,
                    "size_bytes": size_bytes,
                }
                if filename:
                    attachment_dict["filename"] = filename

                processed_attachments.append(attachment_dict)

                logger.info(
                    "Stored attachment: %s (%s, %d bytes) -> %s",
                    filename or attachment_id,
                    mime_type,
                    size_bytes,
                    storage_ref,
                )

            except Exception as exc:
                # Log error but don't block text ingestion
                logger.error(
                    "Failed to process attachment %s: %s",
                    filename or attachment_id,
                    exc,
                    exc_info=True,
                )
                # Continue with other attachments
                continue

        if not processed_attachments:
            return None

        return tuple(processed_attachments)

    async def _submit_to_ingest_api(self, envelope: dict[str, Any]) -> None:
        """Submit ingest.v1 envelope to Switchboard via MCP ingest tool."""
        start_time = time.perf_counter()
        status = "error"

        try:
            result = await self._mcp_client.call_tool("ingest", envelope)

            # Check for tool-level error response
            if isinstance(result, dict) and result.get("status") == "error":
                error_msg = result.get("error", "Unknown ingest error")
                raise RuntimeError(f"Ingest tool error: {error_msg}")

            # Record successful ingest submission
            self._last_ingest_submit = time.time()

            # Determine status for metrics
            is_duplicate = isinstance(result, dict) and result.get("duplicate", False)
            status = "duplicate" if is_duplicate else "success"

            if is_duplicate:
                logger.debug("Duplicate ingestion for %s", envelope["event"]["external_event_id"])
            else:
                logger.info(
                    "Ingestion accepted: request_id=%s, event_id=%s",
                    result.get("request_id") if isinstance(result, dict) else None,
                    envelope["event"]["external_event_id"],
                )
        except Exception as exc:
            self._metrics.record_error(error_type=get_error_type(exc), operation="ingest_submit")
            raise
        finally:
            # Record metrics
            latency = time.perf_counter() - start_time
            self._metrics.record_ingest_submission(status=status, latency=latency)


async def _resolve_gmail_credentials_from_db() -> dict[str, str] | None:
    """Attempt DB-first credential resolution for the Gmail connector.

    Connects to one or more candidate PostgreSQL DB/schema contexts, looks up
    Google OAuth credentials from
    ``butler_secrets`` via :class:`~butlers.credential_store.CredentialStore`,
    and optionally resolves the Pub/Sub webhook token from the same store.

    Lookup order:
    1. ``CONNECTOR_BUTLER_DB_NAME`` + ``CONNECTOR_BUTLER_DB_SCHEMA`` (local)
    2. ``BUTLER_SHARED_DB_NAME`` + ``BUTLER_SHARED_DB_SCHEMA`` (shared)

    Each lookup pool applies schema-scoped ``search_path`` when schema is set.

    Returns a dict with keys ``client_id``, ``client_secret``,
    ``refresh_token``, and optionally ``pubsub_webhook_token`` on success.

    Returns ``None`` if:
    - The DB is unreachable from current runtime configuration.
    - The DB is reachable but no Google OAuth credentials have been stored yet.
    """
    import asyncpg

    db_params = db_params_from_env()
    local_db_name = os.environ.get("CONNECTOR_BUTLER_DB_NAME", "butlers").strip() or "butlers"
    local_schema = os.environ.get("CONNECTOR_BUTLER_DB_SCHEMA")
    shared_db_name = shared_db_name_from_env()
    shared_schema = os.environ.get("BUTLER_SHARED_DB_SCHEMA", "shared")

    candidates: list[tuple[str, str, str | None]] = []
    for source_name, db_name, schema in [
        ("local", local_db_name, local_schema),
        ("shared", shared_db_name, shared_schema),
    ]:
        normalized_db_name = db_name.strip()
        normalized_schema = schema.strip() if schema is not None else None
        if not normalized_db_name:
            continue
        candidate = (source_name, normalized_db_name, normalized_schema or None)
        if candidate not in candidates:
            candidates.append(candidate)

    connected_pools: list[tuple[str, str, str | None, asyncpg.Pool]] = []
    for source_name, db_name, schema in candidates:
        pool_kwargs: dict[str, Any] = {
            "host": db_params["host"],
            "port": db_params["port"],
            "user": db_params["user"],
            "password": db_params["password"],
            "database": db_name,
            "min_size": 1,
            "max_size": 2,
            "command_timeout": 5,
        }
        search_path: str | None = None
        if schema is not None:
            try:
                search_path = schema_search_path(schema)
            except ValueError as exc:
                logger.debug(
                    "Gmail connector: invalid %s schema %r (db=%s, non-fatal): %s",
                    source_name,
                    schema,
                    db_name,
                    exc,
                )
                continue
        if search_path is not None:
            pool_kwargs["server_settings"] = {"search_path": search_path}

        configured_ssl = db_params.get("ssl")
        if configured_ssl is not None:
            pool_kwargs["ssl"] = configured_ssl

        try:
            pool = await asyncpg.create_pool(**pool_kwargs)
        except Exception as exc:
            should_retry_ssl_disable = should_retry_with_ssl_disable(
                exc,
                configured_ssl if isinstance(configured_ssl, str) else None,
            )
            if should_retry_ssl_disable:
                retry_kwargs = dict(pool_kwargs)
                retry_kwargs["ssl"] = "disable"
                try:
                    pool = await asyncpg.create_pool(**retry_kwargs)
                except Exception as retry_exc:
                    logger.debug(
                        "DB connection failed during Gmail credential resolution "
                        "(source=%s, db=%s, schema=%s, non-fatal): %s",
                        source_name,
                        db_name,
                        schema,
                        retry_exc,
                    )
                    continue
            else:
                logger.debug(
                    "DB connection failed during Gmail credential resolution "
                    "(source=%s, db=%s, schema=%s, non-fatal): %s",
                    source_name,
                    db_name,
                    schema,
                    exc,
                )
                continue

        connected_pools.append((source_name, db_name, schema, pool))

    if not connected_pools:
        return None

    primary_source, primary_db_name, primary_schema, primary_pool = connected_pools[0]
    fallback_pools = [pool for _, _, _, pool in connected_pools[1:]]

    try:
        store = CredentialStore(primary_pool, fallback_pools=fallback_pools)
        creds = await load_google_credentials(store)
        if creds is None:
            logger.debug(
                "Gmail connector: no credentials in DB (primary=%s db=%s schema=%s, fallbacks=%d), "
                "connector startup will fail until credentials are stored",
                primary_source,
                primary_db_name,
                primary_schema,
                len(fallback_pools),
            )
            return None
        logger.info(
            "Gmail connector: resolved Google credentials from layered DB lookup "
            "(primary=%s db=%s schema=%s, fallbacks=%d)",
            primary_source,
            primary_db_name,
            primary_schema,
            len(fallback_pools),
        )

        result: dict[str, str] = {
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
            "refresh_token": creds.refresh_token,
        }

        # Pub/Sub token is optional: failure here must not invalidate OAuth credentials.
        try:
            pubsub_token = await store.resolve("GMAIL_PUBSUB_WEBHOOK_TOKEN", env_fallback=False)
            if pubsub_token:
                result["pubsub_webhook_token"] = pubsub_token
                logger.info(
                    "Gmail connector: resolved GMAIL_PUBSUB_WEBHOOK_TOKEN from layered DB lookup "
                    "(primary=%s db=%s schema=%s, fallbacks=%d)",
                    primary_source,
                    primary_db_name,
                    primary_schema,
                    len(fallback_pools),
                )
        except Exception as exc:
            logger.debug(
                "Gmail connector: optional Pub/Sub token lookup failed (non-fatal): %s",
                exc,
            )
        return result
    except InvalidGoogleCredentialsError as exc:
        logger.warning(
            "Gmail connector: stored Google credentials are invalid in layered DB lookup "
            "(primary=%s db=%s schema=%s): %s",
            primary_source,
            primary_db_name,
            primary_schema,
            exc,
        )
        return None
    except Exception as exc:
        logger.debug("Gmail connector: DB credential lookup failed (non-fatal): %s", exc)
        return None
    finally:
        for _, _, _, pool in connected_pools:
            await pool.close()


async def run_gmail_connector() -> None:
    """Run the Gmail connector runtime (async entrypoint).

    Credentials are resolved from the database only (``butler_secrets``).
    """
    configure_logging(level="INFO", butler_name="gmail")

    # Step 1: Resolve credentials from DB.
    db_creds: dict[str, str] | None = await _resolve_gmail_credentials_from_db()
    if db_creds is None:
        raise RuntimeError(
            "Gmail connector requires DB-stored Google OAuth credentials in butler_secrets. "
            "Run OAuth bootstrap via the dashboard."
        )

    # Step 2: Parse non-secret env config and inject DB credentials.
    try:
        config = GmailConnectorConfig.from_env(
            gmail_client_id=db_creds["client_id"],
            gmail_client_secret=db_creds["client_secret"],
            gmail_refresh_token=db_creds["refresh_token"],
            gmail_pubsub_webhook_token=db_creds.get("pubsub_webhook_token"),
        )
    except Exception as exc:
        logger.error("Failed to build connector config from DB credentials: %s", exc)
        raise

    connector = GmailConnectorRuntime(config)
    await connector.start()


def main() -> None:
    """CLI entrypoint for Gmail connector.

    Credentials are loaded from DB-backed credential storage only.
    """
    asyncio.run(run_gmail_connector())


if __name__ == "__main__":
    main()
