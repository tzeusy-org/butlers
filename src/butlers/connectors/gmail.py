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
- CONNECTOR_BACKFILL_ENABLED (optional, default true; enable/disable backfill polling)
- CONNECTOR_BACKFILL_POLL_INTERVAL_S (optional, default 60; backfill poll cadence in seconds)
- CONNECTOR_BACKFILL_PROGRESS_INTERVAL (optional, default 50; report progress every N messages)
"""

from __future__ import annotations

import asyncio
import base64
import codecs
import html
import json
import logging
import os
import re
import time
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from threading import Thread
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    import asyncpg

import httpx
import uvicorn
from fastapi import FastAPI, Request
from prometheus_client import REGISTRY, generate_latest
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from butlers.connectors.filtered_event_buffer import FilteredEventBuffer
from butlers.connectors.gmail_policy import (
    INGESTION_TIER_FULL,
    INGESTION_TIER_METADATA,
    LabelFilterPolicy,
    MessagePolicyResult,
    PolicyTierAssigner,
    evaluate_message_policy,
    load_known_contacts_from_file,
    parse_label_list,
)
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


# Attachment policy: per-MIME-type size limits and fetch mode.
# See docs/connectors/attachment_handling.md section 3 and 4.
ATTACHMENT_POLICY: dict[str, dict[str, object]] = {
    # Images — lazy fetch, 5 MB limit
    "image/jpeg": {"max_size_bytes": 5 * 1024 * 1024, "fetch_mode": "lazy"},
    "image/png": {"max_size_bytes": 5 * 1024 * 1024, "fetch_mode": "lazy"},
    "image/gif": {"max_size_bytes": 5 * 1024 * 1024, "fetch_mode": "lazy"},
    "image/webp": {"max_size_bytes": 5 * 1024 * 1024, "fetch_mode": "lazy"},
    # PDF — lazy fetch, 15 MB limit
    "application/pdf": {"max_size_bytes": 15 * 1024 * 1024, "fetch_mode": "lazy"},
    # Spreadsheets — lazy fetch, 10 MB limit
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {
        "max_size_bytes": 10 * 1024 * 1024,
        "fetch_mode": "lazy",
    },
    "application/vnd.ms-excel": {"max_size_bytes": 10 * 1024 * 1024, "fetch_mode": "lazy"},
    "text/csv": {"max_size_bytes": 10 * 1024 * 1024, "fetch_mode": "lazy"},
    # Documents — lazy fetch, 10 MB limit
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": {
        "max_size_bytes": 10 * 1024 * 1024,
        "fetch_mode": "lazy",
    },
    "message/rfc822": {"max_size_bytes": 10 * 1024 * 1024, "fetch_mode": "lazy"},
    # Calendar — eager fetch, 1 MB limit; routed directly to calendar module
    "text/calendar": {"max_size_bytes": 1 * 1024 * 1024, "fetch_mode": "eager"},
}

# Derived allowlist for MIME eligibility check (keeps existing API surface).
SUPPORTED_ATTACHMENT_TYPES: frozenset[str] = frozenset(ATTACHMENT_POLICY.keys())

# Global hard ceiling: Gmail attachment maximum (25 MB).
# Any attachment exceeding this cap is skipped regardless of per-type limit.
GLOBAL_MAX_ATTACHMENT_SIZE_BYTES = 25 * 1024 * 1024


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

    # Label include/exclude policy (GMAIL_LABEL_INCLUDE, GMAIL_LABEL_EXCLUDE)
    # Per docs/connectors/email_ingestion_policy.md §9
    gmail_label_include: tuple[str, ...] = ()
    gmail_label_exclude: tuple[str, ...] = ("SPAM", "TRASH")

    # Policy tier assignment: user email (the account owner)
    # Required for direct-correspondence tier rule evaluation
    gmail_user_email: str = ""

    # Optional path to a known-contacts JSON cache file.
    # Format: {"contacts": ["addr@example.com", ...], "generated_at": "..."}
    # Per docs/switchboard/email_priority_queuing.md §4
    gmail_known_contacts_path: str | None = None

    # Backfill polling protocol (docs/connectors/interface.md section 14)
    # CONNECTOR_BACKFILL_ENABLED controls whether backfill polling is active.
    connector_backfill_enabled: bool = True
    # CONNECTOR_BACKFILL_POLL_INTERVAL_S: how often to poll Switchboard for pending backfill jobs.
    connector_backfill_poll_interval_s: int = 60
    # CONNECTOR_BACKFILL_PROGRESS_INTERVAL: report progress every N messages.
    connector_backfill_progress_interval: int = 50

    @classmethod
    def _load_non_secret_env_config(cls) -> dict[str, Any]:
        """Load connector config from environment variables excluding OAuth secrets."""
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

        # Label include/exclude policy (per docs/connectors/email_ingestion_policy.md §9)
        label_include_raw = os.environ.get("GMAIL_LABEL_INCLUDE", "")
        label_exclude_raw = os.environ.get("GMAIL_LABEL_EXCLUDE", "SPAM,TRASH")
        gmail_label_include = tuple(parse_label_list(label_include_raw))
        gmail_label_exclude = tuple(parse_label_list(label_exclude_raw))

        # Policy tier assignment (per docs/switchboard/email_priority_queuing.md)
        gmail_user_email = os.environ.get("GMAIL_USER_EMAIL", "")
        gmail_known_contacts_path = os.environ.get("GMAIL_KNOWN_CONTACTS_PATH")

        # Backfill polling protocol (docs/connectors/interface.md section 14)
        backfill_enabled_str = os.environ.get("CONNECTOR_BACKFILL_ENABLED", "true").lower()
        connector_backfill_enabled = backfill_enabled_str not in ("false", "0", "no", "off")

        backfill_poll_interval_str = os.environ.get("CONNECTOR_BACKFILL_POLL_INTERVAL_S", "60")
        try:
            connector_backfill_poll_interval_s = int(backfill_poll_interval_str)
        except ValueError as exc:
            raise ValueError(
                "CONNECTOR_BACKFILL_POLL_INTERVAL_S must be an integer, "
                f"got: {backfill_poll_interval_str}"
            ) from exc

        backfill_progress_interval_str = os.environ.get(
            "CONNECTOR_BACKFILL_PROGRESS_INTERVAL", "50"
        )
        try:
            connector_backfill_progress_interval = int(backfill_progress_interval_str)
        except ValueError as exc:
            raise ValueError(
                "CONNECTOR_BACKFILL_PROGRESS_INTERVAL must be an integer, "
                f"got: {backfill_progress_interval_str}"
            ) from exc

        return {
            "switchboard_mcp_url": os.environ["SWITCHBOARD_MCP_URL"],
            "connector_provider": os.environ.get("CONNECTOR_PROVIDER", "gmail"),
            "connector_channel": os.environ.get("CONNECTOR_CHANNEL", "email"),
            "connector_endpoint_identity": os.environ["CONNECTOR_ENDPOINT_IDENTITY"],
            "connector_max_inflight": max_inflight,
            "connector_health_port": health_port,
            "gmail_watch_renew_interval_s": watch_renew_interval,
            "gmail_poll_interval_s": poll_interval,
            "gmail_pubsub_enabled": pubsub_enabled,
            "gmail_pubsub_topic": pubsub_topic,
            "gmail_pubsub_webhook_port": pubsub_webhook_port,
            "gmail_pubsub_webhook_path": pubsub_webhook_path,
            "gmail_pubsub_webhook_token": pubsub_webhook_token,
            "gmail_label_include": gmail_label_include,
            "gmail_label_exclude": gmail_label_exclude,
            "gmail_user_email": gmail_user_email,
            "gmail_known_contacts_path": gmail_known_contacts_path,
            "connector_backfill_enabled": connector_backfill_enabled,
            "connector_backfill_poll_interval_s": connector_backfill_poll_interval_s,
            "connector_backfill_progress_interval": connector_backfill_progress_interval,
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


class BackfillJob(BaseModel):
    """Backfill job returned by backfill.poll MCP tool.

    Represents a pending backfill job assigned to this connector by Switchboard.
    Date-bounded traversal parameters, rate control, and server-side cursor come
    from job params as described in docs/connectors/email_backfill.md section 4.
    """

    model_config = ConfigDict(extra="allow")

    job_id: str
    # Date range for historical traversal (YYYY-MM-DD strings)
    date_from: str
    date_to: str
    # Rate limit: max messages per hour (must be >= 1 to avoid division by zero in token bucket)
    rate_limit_per_hour: int = 100
    # Daily cost cap in cents
    daily_cost_cap_cents: int = 500
    # Optional server-side cursor for resume
    cursor: dict[str, Any] | None = None
    # Optional target categories filter (e.g. ["finance", "health"])
    target_categories: list[str] = []

    @field_validator("date_from", "date_to")
    @classmethod
    def _require_non_empty_date(cls, v: str, info: object) -> str:
        """Reject empty date strings that would produce malformed Gmail queries."""
        if not v:
            raise ValueError(
                f"BackfillJob.{getattr(info, 'field_name', 'date')} must not be empty; "
                "expected YYYY-MM-DD format from Switchboard"
            )
        return v


class GmailConnectorRuntime:
    """Gmail connector runtime using watch/history delta flow."""

    def __init__(
        self,
        config: GmailConnectorConfig,
        blob_store: BlobStore | None = None,
        db_pool: asyncpg.Pool | None = None,
        cursor_pool: asyncpg.Pool | None = None,
    ) -> None:
        self._config = config
        self._blob_store = blob_store
        # Optional DB pool for writing attachment_refs rows (lazy-fetch model).
        # When None, attachment metadata persistence is skipped but ingest continues.
        self._db_pool = db_pool
        # DB pool for cursor read/write to switchboard.connector_registry.
        self._cursor_pool = cursor_pool
        self._http_client: httpx.AsyncClient | None = None
        self._mcp_client = CachedMCPClient(
            config.switchboard_mcp_url, client_name="gmail-connector"
        )
        self._access_token: str | None = None
        self._token_expires_at: datetime | None = None
        self._running = False
        self._semaphore = asyncio.Semaphore(config.connector_max_inflight)
        # Dedicated semaphore for backfill: limits to (max_inflight - 1) concurrent slots,
        # reserving at least one slot for live ingestion as documented in the comment below.
        self._backfill_semaphore = asyncio.Semaphore(max(1, config.connector_max_inflight - 1))

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

        # Backfill polling (docs/connectors/interface.md section 14)
        self._backfill_task: asyncio.Task[None] | None = None
        # Track how many backfill.poll attempts have been made so we can
        # suppress the first-attempt warning when Switchboard is still starting.
        self._backfill_poll_attempts: int = 0

        # Label filter policy (per docs/connectors/email_ingestion_policy.md §9)
        self._label_filter = LabelFilterPolicy.from_lists(
            include=list(config.gmail_label_include),
            exclude=list(config.gmail_label_exclude),
        )

        # Policy tier assigner (per docs/switchboard/email_priority_queuing.md §2)
        known_contacts: frozenset[str] = frozenset()
        if config.gmail_known_contacts_path:
            known_contacts = load_known_contacts_from_file(config.gmail_known_contacts_path)
        self._policy_tier_assigner = PolicyTierAssigner(
            user_email=config.gmail_user_email or "",
            known_contacts=known_contacts,
        )

        # Ingestion policy evaluators (replaces SourceFilterEvaluator).
        # Two scopes evaluated in order:
        #   1. connector:gmail:<endpoint> — pre-ingest block/pass_through
        #   2. global — post-ingest skip/metadata_only/route_to/low_priority_queue
        # DB-backed with TTL refresh; fail-open on DB error.
        self._ingestion_policy = IngestionPolicyEvaluator(
            scope=f"connector:gmail:{config.connector_endpoint_identity}",
            db_pool=db_pool,
        )
        self._global_ingestion_policy = IngestionPolicyEvaluator(
            scope="global",
            db_pool=db_pool,
        )

        # Filtered event buffer: accumulates events filtered during each poll cycle.
        # Flushed to connectors.filtered_events after each cycle completes.
        self._filtered_event_buffer = FilteredEventBuffer(
            connector_type=config.connector_provider,
            endpoint_identity=config.connector_endpoint_identity,
        )

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
            get_capabilities=self._get_capabilities,
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
            Tuple of (cursor_json, updated_at) — cursor_json is the full
            GmailCursor JSON so the heartbeat UPSERT writes a value that
            ``_load_cursor`` can deserialize without error.
        """
        if self._last_history_id is None:
            return (None, None)
        cursor_json = GmailCursor(
            history_id=self._last_history_id,
            last_updated_at=datetime.now(UTC).isoformat(),
        ).model_dump_json()
        updated_at = (
            datetime.fromtimestamp(self._last_checkpoint_save, UTC)
            if self._last_checkpoint_save is not None
            else None
        )
        return (cursor_json, updated_at)

    def _get_capabilities(self) -> dict[str, object]:
        """Return connector capabilities for heartbeat advertisement.

        Includes capabilities.backfill=True per docs/connectors/gmail.md section 9.5
        when backfill polling is enabled. Dashboard uses this to show/hide backfill
        controls for this connector.
        """
        return {"backfill": self._config.connector_backfill_enabled}

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

        # Wait for Switchboard to be ready before beginning live ingestion.
        # Gmail's historyId cursor is only advanced after a successful
        # ingestion batch, so this probe mainly guards against the first-poll
        # window where the Switchboard may still be starting up.
        try:
            await wait_for_switchboard_ready(self._config.switchboard_mcp_url)
        except TimeoutError:
            logger.warning(
                "Switchboard readiness probe timed out; proceeding anyway. "
                "Messages may be dropped if Switchboard is still starting.",
                extra={"endpoint_identity": self._config.connector_endpoint_identity},
            )

        logger.info(
            "Gmail connector starting: cursor=DB, pubsub=%s",
            self._config.gmail_pubsub_enabled,
        )
        logger.debug(
            "Gmail connector endpoint: %s",
            self._config.connector_endpoint_identity,
        )

        # Ensure cursor exists (DB or file)
        await self._ensure_cursor()

        # Load ingestion policy rules before starting ingestion loop
        await self._ingestion_policy.ensure_loaded()
        await self._global_ingestion_policy.ensure_loaded()

        # Start backfill polling loop in background (does not block live ingestion)
        if self._config.connector_backfill_enabled:
            self._backfill_task = asyncio.create_task(self._run_backfill_loop())
            logger.info(
                "Backfill polling loop started (interval=%ds)",
                self._config.connector_backfill_poll_interval_s,
            )
        else:
            logger.info("Backfill polling disabled via CONNECTOR_BACKFILL_ENABLED=false")

        # Main ingestion loop
        try:
            await self._run_ingestion_loop()
        finally:
            await self.stop()

    async def stop(self) -> None:
        """Stop the Gmail connector runtime."""
        self._running = False

        # Cancel backfill task if running
        if self._backfill_task is not None and not self._backfill_task.done():
            self._backfill_task.cancel()
            try:
                await self._backfill_task
            except asyncio.CancelledError:
                pass
            self._backfill_task = None

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

            # Flush filtered event buffer and drain replay-pending rows after each poll cycle
            await self._flush_and_drain()

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

                    # Flush filtered event buffer and drain replay-pending rows
                    await self._flush_and_drain()

            except Exception as exc:
                logger.error("Error in Pub/Sub ingestion loop: %s", exc, exc_info=True)
                # Back off on error
                await asyncio.sleep(min(60, self._config.gmail_poll_interval_s * 2))

    async def _run_backfill_loop(self) -> None:
        """Background loop that polls Switchboard for pending backfill jobs.

        Runs alongside live ingestion and never blocks it. Polls every
        CONNECTOR_BACKFILL_POLL_INTERVAL_S seconds (default 60).

        Per docs/connectors/interface.md section 14 and docs/connectors/gmail.md
        section 9.1.
        """
        logger.debug(
            "Backfill loop starting: poll_interval=%ds",
            self._config.connector_backfill_poll_interval_s,
        )
        # Wait for Switchboard MCP to be ready before first poll attempt
        initial_delay = 10
        logger.debug(
            "Backfill loop: waiting %ds before first poll for Switchboard readiness",
            initial_delay,
        )
        try:
            await asyncio.sleep(initial_delay)
        except asyncio.CancelledError:
            raise

        while self._running:
            try:
                await self._poll_and_execute_backfill_job()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(
                    "Backfill poll loop error (will retry after interval): %s",
                    exc,
                    exc_info=True,
                )

            try:
                await asyncio.sleep(self._config.connector_backfill_poll_interval_s)
            except asyncio.CancelledError:
                raise

    async def _poll_and_execute_backfill_job(self) -> None:
        """Poll Switchboard for a pending backfill job and execute it if found.

        Calls backfill.poll(connector_type, endpoint_identity). If a job is
        returned, delegates execution to _execute_backfill_job().
        """
        self._backfill_poll_attempts += 1
        try:
            result = await self._mcp_client.call_tool(
                "backfill.poll",
                {
                    "connector_type": self._config.connector_provider,
                    "endpoint_identity": self._config.connector_endpoint_identity,
                },
            )
        except Exception as exc:
            # Downgrade first-attempt failure to debug: Switchboard may still be
            # starting up and the connector handles this correctly (60s retry loop,
            # live ingestion unaffected).  Genuine persistent failures stay at
            # warning level so operators are alerted.
            if self._backfill_poll_attempts == 1:
                logger.debug(
                    "backfill.poll failed on first attempt (non-fatal,"
                    " Switchboard may still be starting): %s",
                    exc,
                )
            else:
                logger.warning("backfill.poll failed (non-fatal): %s", exc)
            return

        if result is None:
            logger.debug("No pending backfill jobs")
            return

        # Parse the job response
        if not isinstance(result, dict):
            logger.warning("Unexpected backfill.poll response type: %s", type(result))
            return

        try:
            # Switchboard returns {job_id, params, cursor} or flat job structure
            job_id = result.get("job_id")
            params = result.get("params", result)  # flatten if no nested params
            cursor = result.get("cursor")

            if not job_id:
                logger.warning("backfill.poll returned result without job_id: %s", result)
                return

            job = BackfillJob(
                job_id=job_id,
                date_from=params.get("date_from", ""),
                date_to=params.get("date_to", ""),
                rate_limit_per_hour=int(params.get("rate_limit_per_hour", 100)),
                daily_cost_cap_cents=int(params.get("daily_cost_cap_cents", 500)),
                cursor=cursor,
                target_categories=params.get("target_categories", []),
            )
        except Exception as exc:
            logger.error("Failed to parse backfill job from poll result %s: %s", result, exc)
            return

        logger.info(
            "Backfill job assigned: job_id=%s date_from=%s date_to=%s rate_limit=%d/hr",
            job.job_id,
            job.date_from,
            job.date_to,
            job.rate_limit_per_hour,
        )
        await self._execute_backfill_job(job)

    async def _execute_backfill_job(self, job: BackfillJob) -> None:
        """Walk Gmail history for a backfill job date range, ingesting historical messages.

        Implements docs/connectors/gmail.md section 9.2:
        - Uses users.messages.list with date-bounded query
        - Walks pages in reverse chronological order (newest first)
        - Applies tiered ingestion policy to each message
        - Reports progress every CONNECTOR_BACKFILL_PROGRESS_INTERVAL messages
        - Respects pause/cancel signals from backfill.progress responses
        - Honors rate_limit_per_hour via token-bucket throttle

        Parameters
        ----------
        job:
            Backfill job from Switchboard including date range, rate limit, and
            optional resume cursor.
        """
        rows_processed = 0
        rows_skipped = 0
        cost_spent_cents = 0
        progress_counter = 0

        # Guard against division by zero in token bucket (rate must be >= 1)
        if job.rate_limit_per_hour <= 0:
            logger.warning(
                "Backfill job %s: rate_limit_per_hour=%d <= 0; skipping to avoid divide-by-zero.",
                job.job_id,
                job.rate_limit_per_hour,
            )
            return

        # Token-bucket state for rate limiting (tokens = messages allowed per hour)
        # Kept as local variables: state is per-job-execution and not shared across jobs.
        # Refill rate: rate_limit_per_hour / 3600 tokens/second
        backfill_tokens: float = float(job.rate_limit_per_hour)
        backfill_token_last_refill: float = time.time()
        token_refill_rate = job.rate_limit_per_hour / 3600.0  # tokens per second

        # Resume cursor from server-side job state
        page_token: str | None = None
        if job.cursor and isinstance(job.cursor, dict):
            page_token = job.cursor.get("page_token")

        logger.info(
            "Executing backfill job %s: date_from=%s date_to=%s resume_page_token=%s",
            job.job_id,
            job.date_from,
            job.date_to,
            page_token,
        )

        try:
            while True:
                # Fetch a page of messages in date range
                messages_page, next_page_token = await self._fetch_backfill_message_page(
                    date_from=job.date_from,
                    date_to=job.date_to,
                    page_token=page_token,
                )

                if not messages_page:
                    # No messages in this page or empty response
                    if next_page_token is None:
                        # End of results
                        break
                    page_token = next_page_token
                    continue

                for msg_stub in messages_page:
                    message_id = msg_stub.get("id")
                    if not message_id:
                        rows_skipped += 1
                        continue

                    # Token-bucket rate limiting
                    now = time.time()
                    elapsed = now - backfill_token_last_refill
                    backfill_tokens = min(
                        float(job.rate_limit_per_hour),
                        backfill_tokens + elapsed * token_refill_rate,
                    )
                    backfill_token_last_refill = now

                    if backfill_tokens < 1.0:
                        # Wait until a token is available
                        wait_s = (1.0 - backfill_tokens) / token_refill_rate
                        logger.debug(
                            "Backfill rate limit: waiting %.2fs for token (job %s)",
                            wait_s,
                            job.job_id,
                        )
                        await asyncio.sleep(wait_s)
                        backfill_tokens = 0.0
                        backfill_token_last_refill = time.time()
                    else:
                        backfill_tokens -= 1.0

                    # Ingest using at-most (connector_max_inflight - 1) slots
                    # to always leave one slot for live ingestion (enforced by _backfill_semaphore)
                    async with self._backfill_semaphore:
                        try:
                            message_data = await self._fetch_message(message_id)
                            policy_result = evaluate_message_policy(
                                message_data,
                                label_filter=self._label_filter,
                                tier_assigner=self._policy_tier_assigner,
                                endpoint_identity=self._config.connector_endpoint_identity,
                            )

                            if not policy_result.should_ingest:
                                rows_skipped += 1
                                logger.info(
                                    "[Gmail] '%s' filtered out: %s",
                                    self._get_subject(message_data),
                                    policy_result.filter_reason,
                                )
                            else:
                                # Ingestion policy gate (backfill path)
                                _bf_envelope = self._build_ingestion_envelope(message_data)

                                # 1. Connector-scope rules
                                _bf_decision = self._ingestion_policy.evaluate(_bf_envelope)
                                if not _bf_decision.allowed:
                                    rows_skipped += 1
                                    logger.info(
                                        "[Gmail] '%s' filtered out by connector rule: %s",
                                        self._get_subject(message_data),
                                        _bf_decision.reason,
                                    )
                                # 2. Global-scope rules
                                elif (
                                    _bf_global := self._global_ingestion_policy.evaluate(
                                        _bf_envelope
                                    )
                                ).action == "skip":
                                    rows_skipped += 1
                                    logger.info(
                                        "[Gmail] '%s' filtered out by global rule: %s",
                                        self._get_subject(message_data),
                                        _bf_global.reason,
                                    )
                                else:
                                    envelope = await self._build_ingest_envelope(
                                        message_data,
                                        policy_result=policy_result,
                                    )
                                    await self._submit_to_ingest_api(envelope)
                                    rows_processed += 1

                                    # Estimate cost: ~0.01 cents per message as conservative proxy
                                    # Actual cost is LLM-side; connector estimates only.
                                    # Per docs/connectors/email_backfill.md section 9.4.
                                    cost_spent_cents += 1

                        except Exception as exc:
                            logger.warning(
                                "Backfill: failed to ingest message %s (job %s): %s",
                                message_id,
                                job.job_id,
                                exc,
                            )
                            rows_skipped += 1

                    progress_counter += 1

                    # Report progress every N messages
                    if progress_counter >= self._config.connector_backfill_progress_interval:
                        cursor_payload: dict[str, Any] | None = {"page_token": page_token or ""}
                        status = await self._report_backfill_progress(
                            job_id=job.job_id,
                            rows_processed=rows_processed,
                            rows_skipped=rows_skipped,
                            cost_spent_cents=cost_spent_cents,
                            cursor=cursor_payload,
                        )
                        progress_counter = 0
                        cost_spent_cents = 0  # reset delta after reporting

                        if status in ("paused", "cancelled", "cost_capped"):
                            logger.info(
                                "Backfill job %s: stopping due to status=%s",
                                job.job_id,
                                status,
                            )
                            return

                # Advance to next page
                if next_page_token is None:
                    break
                page_token = next_page_token

            # Date window exhausted — report completion
            logger.info(
                "Backfill job %s complete: processed=%d skipped=%d",
                job.job_id,
                rows_processed,
                rows_skipped,
            )
            await self._report_backfill_progress(
                job_id=job.job_id,
                rows_processed=rows_processed,
                rows_skipped=rows_skipped,
                cost_spent_cents=cost_spent_cents,
                cursor=None,
                status="completed",
            )

        except asyncio.CancelledError:
            # Connector shutting down — report partial progress before exit
            logger.info(
                "Backfill job %s interrupted by shutdown: processed=%d skipped=%d",
                job.job_id,
                rows_processed,
                rows_skipped,
            )
            try:
                await self._report_backfill_progress(
                    job_id=job.job_id,
                    rows_processed=rows_processed,
                    rows_skipped=rows_skipped,
                    cost_spent_cents=cost_spent_cents,
                    cursor={"page_token": page_token or ""},
                )
            except Exception:
                pass
            raise
        except Exception as exc:
            logger.error(
                "Backfill job %s failed: %s",
                job.job_id,
                exc,
                exc_info=True,
            )
            try:
                await self._report_backfill_progress(
                    job_id=job.job_id,
                    rows_processed=rows_processed,
                    rows_skipped=rows_skipped,
                    cost_spent_cents=cost_spent_cents,
                    cursor={"page_token": page_token or ""},
                    status="error",
                    error=str(exc),
                )
            except Exception:
                pass

    async def _fetch_backfill_message_page(
        self,
        date_from: str,
        date_to: str,
        page_token: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Fetch one page of messages for a backfill date range.

        Uses Gmail messages.list with date query format YYYY/MM/DD.
        Returns (message_stubs, next_page_token). next_page_token is None when
        the result set is exhausted.

        Parameters
        ----------
        date_from:
            Start of date range in YYYY-MM-DD format (inclusive).
        date_to:
            End of date range in YYYY-MM-DD format (inclusive).
        page_token:
            Optional page token for pagination resume.
        """
        if not self._http_client:
            raise RuntimeError("HTTP client not initialized")

        try:
            token = await self._get_access_token()

            # Convert YYYY-MM-DD to YYYY/MM/DD for Gmail query syntax
            date_from_q = date_from.replace("-", "/")
            date_to_q = date_to.replace("-", "/")
            query = f"after:{date_from_q} before:{date_to_q}"

            params: dict[str, Any] = {
                "q": query,
                "maxResults": 100,
            }
            if page_token:
                params["pageToken"] = page_token

            response = await self._http_client.get(
                "https://gmail.googleapis.com/gmail/v1/users/me/messages",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()

            self._source_api_ok = True
            self._metrics.record_source_api_call(
                api_method="messages.list.backfill", status="success"
            )

            data = response.json()
            messages = data.get("messages", [])
            next_page_token = data.get("nextPageToken")

            logger.debug(
                "Backfill page: %d messages, next_page_token=%s",
                len(messages),
                next_page_token,
            )
            return messages, next_page_token

        except Exception as exc:
            self._source_api_ok = False
            self._metrics.record_source_api_call(
                api_method="messages.list.backfill", status="error"
            )
            self._metrics.record_error(
                error_type=get_error_type(exc), operation="backfill_message_list"
            )
            raise

    async def _report_backfill_progress(
        self,
        job_id: str,
        rows_processed: int,
        rows_skipped: int,
        cost_spent_cents: int,
        cursor: dict[str, Any] | None = None,
        status: str | None = None,
        error: str | None = None,
    ) -> str:
        """Report backfill progress to Switchboard via backfill.progress MCP tool.

        Returns the authoritative status from Switchboard ('ack', 'paused',
        'cancelled', 'cost_capped'). Connector must stop if status is not 'ack'.

        Per docs/connectors/interface.md section 14.2 and docs/connectors/email_backfill.md
        section 6.2.
        """
        args: dict[str, Any] = {
            "job_id": job_id,
            "rows_processed": rows_processed,
            "rows_skipped": rows_skipped,
            "cost_spent_cents_delta": cost_spent_cents,
        }
        if cursor is not None:
            args["cursor"] = cursor
        if status is not None:
            args["status"] = status
        if error is not None:
            args["error"] = error

        try:
            result = await self._mcp_client.call_tool("backfill.progress", args)
            if isinstance(result, dict):
                returned_status = result.get("status", "ack")
            else:
                returned_status = "ack"

            logger.debug(
                "backfill.progress response: job_id=%s returned_status=%s",
                job_id,
                returned_status,
            )
            return str(returned_status)
        except Exception as exc:
            logger.warning(
                "backfill.progress call failed for job %s (non-fatal): %s",
                job_id,
                exc,
            )
            return "ack"  # assume continue on progress reporting failures

    async def _ensure_cursor(self) -> None:
        """Ensure cursor exists in DB, initialize if missing."""
        from butlers.connectors.cursor_store import load_cursor

        existing = await load_cursor(
            self._cursor_pool,
            self._config.connector_provider,
            self._config.connector_endpoint_identity,
        )
        if existing is None:
            # Initialize with current history ID from Gmail
            try:
                profile = await self._gmail_get_profile()
                initial_history_id = profile.get("historyId", "1")
            except Exception as exc:
                logger.warning(
                    "Failed to fetch initial historyId from Gmail: %s. Using default.", exc
                )
                initial_history_id = "1"
            initial_cursor = GmailCursor(
                history_id=initial_history_id,
                last_updated_at=datetime.now(UTC).isoformat(),
            )
            await self._save_cursor(initial_cursor)
            logger.info(
                "Initialized cursor in DB with historyId=%s",
                initial_history_id,
            )

    async def _load_cursor(self) -> GmailCursor:
        """Load cursor state from DB."""
        from butlers.connectors.cursor_store import load_cursor

        raw = await load_cursor(
            self._cursor_pool,
            self._config.connector_provider,
            self._config.connector_endpoint_identity,
        )
        if raw is None:
            raise RuntimeError(
                "Cursor not found in DB for "
                f"{self._config.connector_provider}/{self._config.connector_endpoint_identity}"
            )
        try:
            cursor = GmailCursor.model_validate_json(raw)
        except (ValueError, ValidationError):
            # Legacy/corrupt cursor: bare history_id integer stored instead of JSON.
            # Upgrade in-place so future loads succeed.
            logger.warning(
                "Cursor value is not valid GmailCursor JSON (%r); treating as bare history_id",
                raw[:120] if len(raw) > 120 else raw,
            )
            cursor = GmailCursor(
                history_id=raw.strip(),
                last_updated_at=datetime.now(UTC).isoformat(),
            )
            await self._save_cursor(cursor)
        self._last_history_id = cursor.history_id
        return cursor

    async def _save_cursor(self, cursor: GmailCursor) -> None:
        """Save cursor state to DB."""
        try:
            from butlers.connectors.cursor_store import save_cursor

            await save_cursor(
                self._cursor_pool,
                self._config.connector_provider,
                self._config.connector_endpoint_identity,
                cursor.model_dump_json(),
            )

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

    # Exception types that indicate a transient connectivity failure.
    # When these are raised during message delivery, the batch cursor must NOT be
    # advanced so the message will be retried on the next poll cycle.
    _TRANSIENT_DELIVERY_ERRORS: tuple[type[Exception], ...] = (
        ConnectionError,
        httpx.ConnectError,
        httpx.ConnectTimeout,
        httpx.RemoteProtocolError,
    )

    async def _ingest_messages(self, message_ids: list[str]) -> None:
        """Fetch and ingest messages concurrently with bounded parallelism.

        If any message fails with a transient connectivity error
        (ConnectionError, httpx.ConnectError, etc.), that error is re-raised
        after all tasks complete so the caller knows NOT to advance the batch
        cursor.  Per-message errors that are not transient (e.g. a malformed
        message) are logged and swallowed so they do not block other messages.
        """
        tasks = [self._ingest_single_message(msg_id) for msg_id in message_ids]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Re-raise the first transient connectivity error so the polling loop
        # skips cursor advancement and retries the batch on the next cycle.
        for result in results:
            if isinstance(result, self._TRANSIENT_DELIVERY_ERRORS):
                raise result

    async def _ingest_single_message(self, message_id: str) -> None:
        """Fetch and ingest a single Gmail message.

        Pipeline order (per docs/connectors/email_ingestion_policy.md §8):
        1. Fetch message data (always needed to get labels and headers).
        2. Apply label include/exclude filter; skip if excluded.
        3. Evaluate triage rules -> ingestion tier.
        4. Assign policy_tier for queue ordering.
        5. Execute tier behavior:
           - Tier 3 (skip): emit skip log, no Switchboard submission.
           - Tier 2 (metadata): submit slim envelope with ingestion_tier=metadata.
           - Tier 1 (full): submit full envelope.

        Filtered and errored events are recorded into the FilteredEventBuffer
        for batch persistence after the poll cycle completes.

        Transient connectivity errors (ConnectionError, httpx.ConnectError,
        etc.) are re-raised so that _ingest_messages can propagate them to the
        batch loop, preventing cursor advancement.
        Per-message non-transient errors are logged and swallowed.
        """
        async with self._semaphore:
            try:
                # Fetch message data (required for label/header-based policy evaluation)
                message_data = await self._fetch_message(message_id)

                # Extract common fields for buffer recording
                _subject = self._get_subject(message_data)
                _from_header = self._get_from_header(message_data)
                _thread_id = message_data.get("threadId")
                _internal_date = message_data.get("internalDate", "0")
                try:
                    _observed_at = datetime.fromtimestamp(
                        int(_internal_date) / 1000, tz=UTC
                    ).isoformat()
                except (ValueError, OSError):
                    _observed_at = datetime.now(UTC).isoformat()

                # Evaluate label filter + tier policy
                policy_result = evaluate_message_policy(
                    message_data,
                    label_filter=self._label_filter,
                    tier_assigner=self._policy_tier_assigner,
                    endpoint_identity=self._config.connector_endpoint_identity,
                )

                # Tier 3: skip — do not submit to Switchboard
                if not policy_result.should_ingest:
                    logger.info(
                        "[Gmail] '%s' filtered out: %s",
                        _subject,
                        policy_result.filter_reason,
                    )
                    self._filtered_event_buffer.record(
                        external_message_id=message_id,
                        source_channel=self._config.connector_channel,
                        sender_identity=_from_header,
                        subject_or_preview=_subject or None,
                        filter_reason=policy_result.filter_reason,
                        full_payload=FilteredEventBuffer.full_payload(
                            channel=self._config.connector_channel,
                            provider=self._config.connector_provider,
                            endpoint_identity=self._config.connector_endpoint_identity,
                            external_event_id=message_id,
                            external_thread_id=_thread_id,
                            observed_at=_observed_at,
                            sender_identity=_from_header,
                            raw=message_data,
                            policy_tier=policy_result.policy_tier,
                        ),
                    )
                    return

                # Ingestion policy gate (runs after label filter, before envelope build)
                _ip_envelope = self._build_ingestion_envelope(message_data)

                # 1. Connector-scope rules (block/pass_through)
                _ip_decision = self._ingestion_policy.evaluate(_ip_envelope)
                if not _ip_decision.allowed:
                    logger.info(
                        "[Gmail] '%s' filtered out by connector rule: %s",
                        _subject,
                        _ip_decision.reason,
                    )
                    self._filtered_event_buffer.record(
                        external_message_id=message_id,
                        source_channel=self._config.connector_channel,
                        sender_identity=_from_header,
                        subject_or_preview=_subject or None,
                        filter_reason=FilteredEventBuffer.reason_policy_rule(
                            "connector_rule",
                            "block",
                            _ip_decision.matched_rule_type or "unknown",
                        ),
                        full_payload=FilteredEventBuffer.full_payload(
                            channel=self._config.connector_channel,
                            provider=self._config.connector_provider,
                            endpoint_identity=self._config.connector_endpoint_identity,
                            external_event_id=message_id,
                            external_thread_id=_thread_id,
                            observed_at=_observed_at,
                            sender_identity=_from_header,
                            raw=message_data,
                            policy_tier=policy_result.policy_tier,
                        ),
                    )
                    return

                # 2. Global-scope rules (skip/metadata_only/route_to/low_priority_queue)
                _gp_decision = self._global_ingestion_policy.evaluate(_ip_envelope)
                if _gp_decision.action == "skip":
                    logger.info(
                        "[Gmail] '%s' filtered out by global rule: %s",
                        _subject,
                        _gp_decision.reason,
                    )
                    self._filtered_event_buffer.record(
                        external_message_id=message_id,
                        source_channel=self._config.connector_channel,
                        sender_identity=_from_header,
                        subject_or_preview=_subject or None,
                        filter_reason=FilteredEventBuffer.reason_policy_rule(
                            "global_rule",
                            "skip",
                            _gp_decision.matched_rule_type or "unknown",
                        ),
                        full_payload=FilteredEventBuffer.full_payload(
                            channel=self._config.connector_channel,
                            provider=self._config.connector_provider,
                            endpoint_identity=self._config.connector_endpoint_identity,
                            external_event_id=message_id,
                            external_thread_id=_thread_id,
                            observed_at=_observed_at,
                            sender_identity=_from_header,
                            raw=message_data,
                            policy_tier=policy_result.policy_tier,
                        ),
                    )
                    return

                # Build ingest.v1 envelope (tier-aware)
                envelope = await self._build_ingest_envelope(
                    message_data,
                    policy_result=policy_result,
                )

                # Submit to Switchboard ingest API
                await self._submit_to_ingest_api(envelope)

                logger.info(
                    "Ingested message: %s tier=%d policy_tier=%s",
                    message_id,
                    policy_result.ingestion_tier,
                    policy_result.policy_tier,
                )

            except self._TRANSIENT_DELIVERY_ERRORS as exc:
                # Re-raise transient connectivity errors so the batch loop
                # can skip cursor advancement and retry.
                logger.error(
                    "Transient connectivity failure for message %s (%s); cursor will not advance",
                    message_id,
                    type(exc).__name__,
                    exc_info=True,
                )
                raise
            except Exception as exc:
                logger.error("Failed to ingest message %s: %s", message_id, exc, exc_info=True)
                # Record error event in the filtered event buffer
                self._filtered_event_buffer.record(
                    external_message_id=message_id,
                    source_channel=self._config.connector_channel,
                    sender_identity="unknown",
                    subject_or_preview=None,
                    filter_reason=FilteredEventBuffer.reason_submission_error(),
                    full_payload=FilteredEventBuffer.full_payload(
                        channel=self._config.connector_channel,
                        provider=self._config.connector_provider,
                        endpoint_identity=self._config.connector_endpoint_identity,
                        external_event_id=message_id,
                        external_thread_id=None,
                        observed_at=datetime.now(UTC).isoformat(),
                        sender_identity="unknown",
                        raw={"message_id": message_id},
                    ),
                    status="error",
                    error_detail=str(exc),
                )

    # SQL for the replay drain loop.
    # Locks up to 10 replay_pending rows with skip-locked concurrency safety,
    # then updates each to replay_complete or replay_failed after submission.
    _REPLAY_SELECT_SQL = """\
SELECT id, received_at, external_message_id, full_payload
FROM connectors.filtered_events
WHERE connector_type = $1
  AND endpoint_identity = $2
  AND status = 'replay_pending'
ORDER BY received_at ASC
LIMIT 10
FOR UPDATE SKIP LOCKED
"""

    _REPLAY_UPDATE_SQL = """\
UPDATE connectors.filtered_events
SET status = $1,
    error_detail = $2,
    replay_completed_at = now()
WHERE id = $3 AND received_at = $4
"""

    async def _flush_and_drain(self) -> None:
        """Flush filtered event buffer then drain up to 10 replay-pending rows.

        Called after each poll cycle.  No-op when ``_db_pool`` is None (no DB
        connectivity at connector startup).
        """
        if self._db_pool is None:
            return

        # 1. Flush accumulated filtered/error events from this cycle.
        await self._filtered_event_buffer.flush(self._db_pool)

        # 2. Drain replay-pending rows left by the dashboard "retry" action.
        await self._drain_replay_pending()

    async def _drain_replay_pending(self) -> None:
        """Process up to 10 replay_pending rows from connectors.filtered_events.

        For each row:
        - Deserialize full_payload from JSONB.
        - Wrap in an ingest.v1 envelope (adds schema_version).
        - Submit via _submit_to_ingest_api.
        - Mark status=replay_complete on success or replay_failed on error.

        Uses FOR UPDATE SKIP LOCKED so concurrent connector instances never
        process the same row twice.  The SELECT and all UPDATEs share a single
        connection and transaction so the row locks are held until every status
        update commits — defeating the race window that would otherwise exist
        between connection-per-update calls.
        """
        if self._db_pool is None:
            return

        try:
            async with self._db_pool.acquire() as conn:
                async with conn.transaction():
                    rows = await conn.fetch(
                        self._REPLAY_SELECT_SQL,
                        self._config.connector_provider,
                        self._config.connector_endpoint_identity,
                    )

                    if not rows:
                        return

                    logger.debug("replay drain: processing %d replay_pending rows", len(rows))

                    for row in rows:
                        row_id = row["id"]
                        received_at = row["received_at"]
                        external_message_id = row["external_message_id"]
                        raw_payload = row["full_payload"]

                        # Deserialize JSONB (asyncpg may return str or dict depending on codec)
                        if isinstance(raw_payload, str):
                            try:
                                payload_dict: dict[str, Any] = json.loads(raw_payload)
                            except Exception as exc:
                                logger.warning(
                                    "replay drain: failed to parse full_payload for id=%s: %s",
                                    row_id,
                                    exc,
                                )
                                await conn.execute(
                                    self._REPLAY_UPDATE_SQL,
                                    "replay_failed",
                                    str(exc),
                                    row_id,
                                    received_at,
                                )
                                continue
                        else:
                            payload_dict = dict(raw_payload)

                        # Build ingest.v1 envelope by adding schema_version
                        envelope: dict[str, Any] = {"schema_version": "ingest.v1", **payload_dict}

                        try:
                            await self._submit_to_ingest_api(envelope)
                            await conn.execute(
                                self._REPLAY_UPDATE_SQL,
                                "replay_complete",
                                None,
                                row_id,
                                received_at,
                            )
                            logger.info(
                                "replay drain: replayed message %s (id=%s)",
                                external_message_id,
                                row_id,
                            )
                        except Exception as exc:
                            error_msg = str(exc)
                            logger.warning(
                                "replay drain: failed to replay message %s (id=%s): %s",
                                external_message_id,
                                row_id,
                                exc,
                            )
                            await conn.execute(
                                self._REPLAY_UPDATE_SQL,
                                "replay_failed",
                                error_msg,
                                row_id,
                                received_at,
                            )
        except Exception:
            logger.warning("replay drain: failed to query replay_pending rows", exc_info=True)

    @staticmethod
    def _get_from_header(message_data: dict[str, Any]) -> str:
        """Extract the raw From header value from a Gmail message payload."""
        headers = message_data.get("payload", {}).get("headers", [])
        for header in headers:
            if isinstance(header, dict) and header.get("name", "").lower() == "from":
                return header.get("value", "")
        return ""

    @staticmethod
    def _get_subject(message_data: dict[str, Any]) -> str:
        """Extract the Subject header value from a Gmail message payload."""
        headers = message_data.get("payload", {}).get("headers", [])
        for header in headers:
            if isinstance(header, dict) and header.get("name", "").lower() == "subject":
                return header.get("value", "")
        return "(no subject)"

    @staticmethod
    def _build_ingestion_envelope(message_data: dict[str, Any]) -> IngestionEnvelope:
        """Build an IngestionEnvelope from a Gmail message payload.

        Populates sender_address (normalized From header), headers dict,
        and raw_key (raw From header value) for policy evaluation.
        """
        raw_headers = message_data.get("payload", {}).get("headers", [])
        headers_dict: dict[str, str] = {}
        from_value = ""
        for h in raw_headers:
            if isinstance(h, dict):
                name = h.get("name", "")
                value = h.get("value", "")
                headers_dict[name] = value
                if name.lower() == "from":
                    from_value = value

        # Normalize sender address: strip display name, lowercase
        sender = from_value.strip().lower()
        if "<" in sender and ">" in sender:
            sender = sender.split("<", 1)[1].split(">", 1)[0].strip()

        return IngestionEnvelope(
            sender_address=sender,
            source_channel="email",
            headers=headers_dict,
            raw_key=from_value,
        )

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

    async def _build_ingest_envelope(
        self,
        message_data: dict[str, Any],
        policy_result: MessagePolicyResult | None = None,
    ) -> dict[str, Any]:
        """Build ingest.v1 envelope from Gmail message data.

        Builds a tier-appropriate envelope per spec §5:
        - Tier 1 (full): full normalized payload + attachments.
        - Tier 2 (metadata): slim envelope, payload.raw=null, subject-only normalized_text.
        - Tier 3 (skip): caller must not reach this method.

        Parameters
        ----------
        message_data:
            Raw Gmail message dict from messages.get API.
        policy_result:
            Result of policy evaluation (tier + policy_tier). If None, defaults to
            Tier 1 with policy_tier='default' (backward compatible).
        """
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

        # Resolve effective tier and policy_tier
        effective_ingestion_tier = INGESTION_TIER_FULL
        effective_policy_tier = "default"
        if policy_result is not None:
            effective_ingestion_tier = policy_result.ingestion_tier
            effective_policy_tier = policy_result.policy_tier

        # Observed timestamp
        try:
            observed_timestamp_ms = int(internal_date)
            observed_at = datetime.fromtimestamp(observed_timestamp_ms / 1000, tz=UTC)
        except (ValueError, OSError):
            observed_at = datetime.now(UTC)

        # --- Tier 2: Metadata-only envelope ---
        # Per spec §5.2: payload.raw=null, normalized_text=subject-only, ingestion_tier=metadata
        if effective_ingestion_tier == INGESTION_TIER_METADATA:
            idempotency_key = (
                f"{self._config.connector_provider}:"
                f"{self._config.connector_endpoint_identity}:"
                f"{rfc_message_id}"
            )
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
                    "raw": None,
                    "normalized_text": f"Subject: {html.escape(subject)} ",
                },
                "control": {
                    "idempotency_key": idempotency_key,
                    "ingestion_tier": "metadata",
                    "policy_tier": effective_policy_tier,
                },
            }

        # --- Tier 1: Full envelope ---
        # Extract body
        body = self._extract_body_from_payload(message_data.get("payload", {}))

        # Build normalized text
        normalized_text = f"Subject: {html.escape(subject)}\n\n{html.escape(body)}"

        # Process attachments
        attachments = await self._process_attachments(message_id, message_data.get("payload", {}))

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
                "policy_tier": effective_policy_tier,
            },
        }

    # MIME types that carry cryptographic signatures rather than body content.
    # These parts must be skipped during body extraction so signatures are never
    # mistaken for readable text.
    _SIGNATURE_MIME_TYPES: frozenset[str] = frozenset(
        {
            "application/pkcs7-signature",  # S/MIME detached signature
            "application/pgp-signature",  # PGP/MIME detached signature
            "application/x-pkcs7-signature",  # Legacy S/MIME variant
        }
    )

    @staticmethod
    def _charset_from_headers(headers: list[dict[str, str]]) -> str:
        """Extract charset from the Content-Type header in a MIME part's headers list.

        Returns the charset string if present and recognised by :mod:`codecs`,
        otherwise returns ``"utf-8"`` as a safe fallback.
        """
        for header in headers:
            if header.get("name", "").lower() == "content-type":
                match = re.search(r"charset=([^\s;\"']+)", header.get("value", ""), re.IGNORECASE)
                if match:
                    charset_name = match.group(1).strip()
                    try:
                        codecs.lookup(charset_name)
                        return charset_name
                    except LookupError:
                        logger.debug(
                            "Unknown charset %r in Content-Type; falling back to utf-8",
                            charset_name,
                        )
        return "utf-8"

    def _decode_part_bytes(self, raw_bytes: bytes, payload: dict[str, Any]) -> str:
        """Decode *raw_bytes* using the charset declared in *payload*'s Content-Type header.

        Falls back to UTF-8 with replacement characters when the declared
        charset is absent or not recognised by :mod:`codecs`.
        """
        charset = self._charset_from_headers(payload.get("headers", []))
        return raw_bytes.decode(charset, errors="replace")

    def _extract_body_from_payload(self, payload: dict[str, Any], depth: int = 0) -> str:
        """Recursively extract body text from Gmail message payload.

        Preference order: text/plain > text/html (stripped) > "(no body)".
        HTML is stripped using stdlib html.parser — no third-party deps.

        S/MIME handling:
        - multipart/signed: recurse into content parts, skip signature parts.
        - application/pkcs7-mime (opaque S/MIME): body is inside the PKCS
          envelope and cannot be extracted without the recipient's private key.
          Returns a descriptive fallback string and logs a warning.
        """
        # Prevent stack overflow from malicious deeply nested messages
        if depth > 20:
            logger.warning("Maximum recursion depth reached in email parsing")
            return "(body too deeply nested)"

        mime_type = payload.get("mimeType", "")

        # Opaque S/MIME: body is encrypted inside the PKCS envelope — cannot
        # extract without the recipient's private key.
        if mime_type in ("application/pkcs7-mime", "application/x-pkcs7-mime"):
            logger.warning(
                "S/MIME encrypted email (application/pkcs7-mime): body cannot be extracted"
            )
            return "(S/MIME encrypted body — cannot extract)"

        # Leaf node: text/plain — return immediately (highest priority)
        if mime_type == "text/plain":
            body_data = payload.get("body", {}).get("data", "")
            if body_data:
                raw_bytes = base64.urlsafe_b64decode(body_data)
                return self._decode_part_bytes(raw_bytes, payload)

        # Leaf node: text/html — decode and strip tags (fallback candidate)
        if mime_type == "text/html":
            body_data = payload.get("body", {}).get("data", "")
            if body_data:
                raw_bytes = base64.urlsafe_b64decode(body_data)
                raw_html = self._decode_part_bytes(raw_bytes, payload)
                return self._strip_html(raw_html)

        # Multipart: collect plain and html candidates separately so we can
        # prefer plain regardless of part ordering in the MIME tree.
        # For multipart/signed emails, skip detached-signature parts so only the
        # content part(s) contribute to the extracted body.
        parts = payload.get("parts", [])
        if parts:
            plain_candidate: str | None = None
            html_candidate: str | None = None
            for part in parts:
                child_mime = part.get("mimeType", "")
                # Skip cryptographic signature parts — they carry no readable body.
                if child_mime in self._SIGNATURE_MIME_TYPES:
                    continue
                body = self._extract_body_from_payload(part, depth + 1)
                if body in ("(no body)", "(body too deeply nested)"):
                    continue
                # Classify by the child's own MIME type to preserve preference.
                if child_mime == "text/plain" and plain_candidate is None:
                    plain_candidate = body
                elif child_mime == "text/html" and html_candidate is None:
                    html_candidate = body
                elif plain_candidate is None and html_candidate is None:
                    # Nested multipart returned a resolved body — treat as plain.
                    plain_candidate = body
            if plain_candidate is not None:
                return plain_candidate
            if html_candidate is not None:
                return html_candidate

        return "(no body)"

    @staticmethod
    def _strip_html(raw_html: str) -> str:
        """Strip HTML tags from *raw_html*, returning readable plain text.

        Uses stdlib ``html.parser.HTMLParser`` to skip ``<style>`` and
        ``<script>`` blocks entirely and collect visible text nodes.
        Collapses runs of whitespace into single spaces / newlines.
        """

        class _TextExtractor(HTMLParser):
            """Collect visible text, skipping style/script content."""

            _SKIP_TAGS = frozenset({"style", "script", "head", "meta", "link"})

            def __init__(self) -> None:
                super().__init__(convert_charrefs=True)
                self._skip_depth = 0
                self._parts: list[str] = []

            def handle_starttag(self, tag: str, attrs: list) -> None:  # type: ignore[override]
                if tag in self._SKIP_TAGS:
                    self._skip_depth += 1

            def handle_endtag(self, tag: str) -> None:  # type: ignore[override]
                if tag in self._SKIP_TAGS and self._skip_depth > 0:
                    self._skip_depth -= 1

            def handle_data(self, data: str) -> None:
                if self._skip_depth == 0:
                    self._parts.append(data)

            def get_text(self) -> str:
                text = "".join(self._parts)
                # Normalise whitespace: collapse blanks, preserve paragraph breaks
                text = re.sub(r"[ \t]+", " ", text)
                text = re.sub(r"\n{3,}", "\n\n", text)
                return text.strip()

        extractor = _TextExtractor()
        extractor.feed(raw_html)
        return extractor.get_text() or "(no body)"

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

    async def _write_attachment_ref(
        self,
        message_id: str,
        attachment_id: str,
        filename: str | None,
        media_type: str,
        size_bytes: int,
        fetched: bool = False,
        blob_ref: str | None = None,
    ) -> None:
        """Persist an attachment_refs row in the switchboard schema.

        This is a best-effort write: if the DB pool is unavailable or the upsert
        fails, the error is logged and the caller continues without raising.

        Args:
            message_id: Gmail message ID (part of composite PK).
            attachment_id: Gmail attachment ID (part of composite PK).
            filename: Original filename, nullable.
            media_type: MIME type.
            size_bytes: Attachment size in bytes.
            fetched: True when blob_ref is populated.
            blob_ref: BlobStore reference; NULL until materialized.
        """
        if not self._db_pool:
            return

        try:
            async with self._db_pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO attachment_refs
                        (message_id, attachment_id, filename, media_type, size_bytes,
                         fetched, blob_ref)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    ON CONFLICT (message_id, attachment_id) DO UPDATE SET
                        fetched = EXCLUDED.fetched,
                        blob_ref = EXCLUDED.blob_ref
                    """,
                    message_id,
                    attachment_id,
                    filename,
                    media_type,
                    size_bytes,
                    fetched,
                    blob_ref,
                )
        except Exception as exc:
            logger.warning(
                "Failed to write attachment_ref (%s, %s): %s",
                message_id,
                attachment_id,
                exc,
            )

    async def _process_attachments(
        self, message_id: str, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], ...] | None:
        """Extract and handle attachments from message payload.

        Implements the lazy/eager fetch model from docs/connectors/attachment_handling.md:
        - text/calendar (.ics): eager fetch, direct routing to calendar module.
        - all other supported types: lazy fetch — write attachment_refs row, no download.
        - Oversized or unsupported attachments are skipped; metrics are emitted.

        Args:
            message_id: Gmail message ID for downloading attachments.
            payload: Gmail message payload dict.

        Returns:
            Tuple of attachment metadata dicts for the ingest envelope, or None.
        """
        # Extract attachment metadata from MIME tree
        attachment_metas = self._extract_attachments(payload)
        if not attachment_metas:
            return None

        processed_attachments: list[dict[str, Any]] = []

        for meta in attachment_metas:
            attachment_id = meta["attachment_id"]
            size_bytes = meta["size_bytes"]
            mime_type = meta["mime_type"]
            filename = meta["filename"]

            policy = ATTACHMENT_POLICY.get(mime_type, {})
            per_type_limit: int = policy.get("max_size_bytes", 0)  # type: ignore[assignment]
            fetch_mode: str = policy.get("fetch_mode", "lazy")  # type: ignore[assignment]

            # --- Size enforcement ---
            # Global hard ceiling always checked first.
            if size_bytes > GLOBAL_MAX_ATTACHMENT_SIZE_BYTES:
                logger.warning(
                    "Skipping attachment exceeding global cap: %s (%d bytes > %d bytes global cap)",
                    filename or attachment_id,
                    size_bytes,
                    GLOBAL_MAX_ATTACHMENT_SIZE_BYTES,
                )
                self._metrics.record_attachment_skipped_oversized(media_type=mime_type)
                continue

            # Per-type size limit.
            if per_type_limit > 0 and size_bytes > per_type_limit:
                logger.warning(
                    "Skipping oversized attachment: %s (%d bytes > %d bytes per-type limit for %s)",
                    filename or attachment_id,
                    size_bytes,
                    per_type_limit,
                    mime_type,
                )
                self._metrics.record_attachment_skipped_oversized(media_type=mime_type)
                continue

            # --- Fetch mode ---
            if fetch_mode == "eager":
                # Eager fetch: download and store immediately.
                # Required for text/calendar to enable direct calendar routing.
                if not self._blob_store:
                    logger.warning(
                        "Blob store not configured; skipping eager attachment %s",
                        filename or attachment_id,
                    )
                    continue

                try:
                    attachment_bytes = await self._download_gmail_attachment(
                        message_id, attachment_id
                    )
                    storage_ref = await self._blob_store.put(
                        attachment_bytes,
                        content_type=mime_type,
                        filename=filename,
                    )

                    # Write ref row with fetched=True so idempotent re-fetch sees it.
                    await self._write_attachment_ref(
                        message_id=message_id,
                        attachment_id=attachment_id,
                        filename=filename,
                        media_type=mime_type,
                        size_bytes=size_bytes,
                        fetched=True,
                        blob_ref=storage_ref,
                    )

                    attachment_dict: dict[str, Any] = {
                        "media_type": mime_type,
                        "storage_ref": storage_ref,
                        "size_bytes": size_bytes,
                    }
                    if filename:
                        attachment_dict["filename"] = filename

                    processed_attachments.append(attachment_dict)
                    self._metrics.record_attachment_fetched(
                        media_type=mime_type, fetch_mode="eager", result="success"
                    )

                    logger.info(
                        "Eager-fetched attachment: %s (%s, %d bytes) -> %s",
                        filename or attachment_id,
                        mime_type,
                        size_bytes,
                        storage_ref,
                    )

                except Exception as exc:
                    # For calendar files: failure must be visible, not silently dropped.
                    logger.error(
                        "Failed to eager-fetch attachment %s (%s): %s",
                        filename or attachment_id,
                        mime_type,
                        exc,
                        exc_info=True,
                    )
                    self._metrics.record_attachment_fetched(
                        media_type=mime_type, fetch_mode="eager", result="error"
                    )
                    # Continue with other attachments; text ingestion is not blocked.
                    continue

            else:
                # Lazy fetch: persist metadata reference row, no download at ingest time.
                await self._write_attachment_ref(
                    message_id=message_id,
                    attachment_id=attachment_id,
                    filename=filename,
                    media_type=mime_type,
                    size_bytes=size_bytes,
                    fetched=False,
                    blob_ref=None,
                )

                # Lazy-fetched attachments are NOT included in the ingest
                # envelope — IngestAttachment requires a non-empty storage_ref.
                # The attachment_refs DB row (written above) is sufficient for
                # on-demand fetch via fetch_attachment().
                self._metrics.record_attachment_fetched(
                    media_type=mime_type, fetch_mode="lazy", result="success"
                )

                logger.debug(
                    "Lazy-ref attachment: %s (%s, %d bytes)",
                    filename or attachment_id,
                    mime_type,
                    size_bytes,
                )

            self._metrics.record_attachment_type_distribution(media_type=mime_type)

        if not processed_attachments:
            return None

        return tuple(processed_attachments)

    async def fetch_attachment(self, message_id: str, attachment_id: str) -> str | None:
        """On-demand fetch of a lazy attachment.

        Resolves the attachment_refs row, downloads bytes, stores in BlobStore,
        updates the row with fetched=True and the blob_ref, and returns the ref.

        Idempotent: if the attachment has already been fetched (blob_ref set),
        the existing blob_ref is returned immediately without re-downloading.

        Args:
            message_id: Gmail message ID.
            attachment_id: Gmail attachment ID.

        Returns:
            BlobStore reference string, or None if fetch failed or not possible.
        """
        if not self._blob_store:
            logger.warning("fetch_attachment: blob store not configured, cannot materialize")
            return None

        # Try to short-circuit if already fetched.
        if self._db_pool:
            try:
                async with self._db_pool.acquire() as conn:
                    row = await conn.fetchrow(
                        """
                        SELECT fetched, blob_ref, filename, media_type, size_bytes
                        FROM attachment_refs
                        WHERE message_id = $1 AND attachment_id = $2
                        """,
                        message_id,
                        attachment_id,
                    )
                if row and row["fetched"] and row["blob_ref"]:
                    logger.debug(
                        "fetch_attachment: already materialized (%s, %s) -> %s",
                        message_id,
                        attachment_id,
                        row["blob_ref"],
                    )
                    self._metrics.record_attachment_fetched(
                        media_type=row["media_type"], fetch_mode="lazy", result="success"
                    )
                    return row["blob_ref"]
            except Exception as exc:
                logger.warning(
                    "fetch_attachment: DB lookup failed (%s, %s): %s",
                    message_id,
                    attachment_id,
                    exc,
                )

        # Download and store.
        try:
            attachment_bytes = await self._download_gmail_attachment(message_id, attachment_id)

            # Determine media_type from DB row if available.
            media_type = "application/octet-stream"
            filename = None
            size_bytes = len(attachment_bytes)
            if self._db_pool:
                try:
                    async with self._db_pool.acquire() as conn:
                        row = await conn.fetchrow(
                            "SELECT filename, media_type, size_bytes FROM attachment_refs "
                            "WHERE message_id = $1 AND attachment_id = $2",
                            message_id,
                            attachment_id,
                        )
                    if row:
                        media_type = row["media_type"]
                        filename = row["filename"]
                        size_bytes = row["size_bytes"]
                except Exception:
                    pass

            blob_ref = await self._blob_store.put(
                attachment_bytes,
                content_type=media_type,
                filename=filename,
            )

            # Persist updated ref.
            await self._write_attachment_ref(
                message_id=message_id,
                attachment_id=attachment_id,
                filename=filename,
                media_type=media_type,
                size_bytes=size_bytes,
                fetched=True,
                blob_ref=blob_ref,
            )

            self._metrics.record_attachment_fetched(
                media_type=media_type, fetch_mode="lazy", result="success"
            )
            logger.info(
                "fetch_attachment: materialized (%s, %s) -> %s",
                message_id,
                attachment_id,
                blob_ref,
            )
            return blob_ref

        except Exception as exc:
            logger.error(
                "fetch_attachment: failed to materialize (%s, %s): %s",
                message_id,
                attachment_id,
                exc,
                exc_info=True,
            )
            self._metrics.record_attachment_fetched(
                media_type="unknown", fetch_mode="lazy", result="error"
            )
            return None

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
        creds = await load_google_credentials(store, pool=primary_pool)
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


async def resolve_gmail_endpoint_identity(
    client_id: str,
    client_secret: str,
    refresh_token: str,
    env_fallback: str,
) -> str:
    """Resolve the authenticated Gmail email address for use as endpoint_identity.

    Makes a lightweight OAuth token refresh + profile API call to determine the
    real email address associated with the configured credentials. Falls back to
    ``env_fallback`` if the API call fails for any reason.

    Args:
        client_id: OAuth client ID.
        client_secret: OAuth client secret.
        refresh_token: OAuth refresh token.
        env_fallback: Value to return if auto-resolution fails.

    Returns:
        Resolved identity string in the form ``gmail:user:<email>`` if successful,
        otherwise the ``env_fallback`` value unchanged.
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Refresh access token
            token_response = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
            )
            token_response.raise_for_status()
            access_token = token_response.json()["access_token"]

            # Fetch Gmail profile
            profile_response = await client.get(
                "https://gmail.googleapis.com/gmail/v1/users/me/profile",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            profile_response.raise_for_status()
            email = profile_response.json().get("emailAddress", "")
            if email:
                resolved = f"gmail:user:{email}"
                logger.info(
                    "Gmail connector: auto-resolved endpoint_identity=%s "
                    "from authenticated account",
                    resolved,
                )
                return resolved
            logger.warning(
                "Gmail connector: profile response missing emailAddress; "
                "falling back to env var endpoint_identity=%s",
                env_fallback,
            )
    except Exception as exc:
        logger.warning(
            "Gmail connector: failed to auto-resolve endpoint_identity (%s); "
            "falling back to env var value=%s",
            exc,
            env_fallback,
        )
    return env_fallback


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

    # Step 3: Auto-resolve endpoint_identity from the authenticated Gmail account.
    # This replaces the static env var default (e.g. "gmail:user:dev") with the
    # actual email address of the authenticated account.
    resolved_identity = await resolve_gmail_endpoint_identity(
        client_id=db_creds["client_id"],
        client_secret=db_creds["client_secret"],
        refresh_token=db_creds["refresh_token"],
        env_fallback=config.connector_endpoint_identity,
    )
    if resolved_identity != config.connector_endpoint_identity:
        config = config.model_copy(update={"connector_endpoint_identity": resolved_identity})
        logger.info(
            "Gmail connector: updated endpoint_identity to resolved value: %s", resolved_identity
        )

    # Create cursor pool for DB-backed checkpoint persistence.
    from butlers.connectors.cursor_store import create_cursor_pool_from_env

    cursor_pool = await create_cursor_pool_from_env()
    logger.info("Gmail connector: cursor pool created for DB-backed checkpoints")

    connector = GmailConnectorRuntime(config, db_pool=cursor_pool, cursor_pool=cursor_pool)
    try:
        await connector.start()
    finally:
        if cursor_pool is not None:
            await cursor_pool.close()


def main() -> None:
    """CLI entrypoint for Gmail connector.

    Credentials are loaded from DB-backed credential storage only.
    """
    asyncio.run(run_gmail_connector())


if __name__ == "__main__":
    main()
