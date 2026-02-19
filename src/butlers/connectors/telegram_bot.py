"""Telegram Bot connector runtime for ingestion.

This connector is a transport-only adapter that:
- Polls Telegram for updates (dev mode) or registers webhooks (prod mode)
- Normalizes Telegram updates to canonical ingest.v1 format
- Submits normalized events to Switchboard MCP server via ingest tool
- Persists durable checkpoints for safe resume after crashes

The connector does NOT perform classification or routing - those remain
downstream responsibilities of the Switchboard after ingest acceptance.

Environment Variables (from docs/connectors/telegram_bot.md):
    SWITCHBOARD_MCP_URL: SSE endpoint URL for Switchboard MCP server (required)
    CONNECTOR_PROVIDER: "telegram" (required)
    CONNECTOR_CHANNEL: "telegram" (required)
    CONNECTOR_ENDPOINT_IDENTITY: Bot username or configured bot ID (required)
    CONNECTOR_CURSOR_PATH: Checkpoint file path for polling mode (required for polling)
    CONNECTOR_POLL_INTERVAL_S: Poll interval in seconds (required for polling)
    CONNECTOR_MAX_INFLIGHT: Max concurrent ingest submissions (optional, default 8)
    CONNECTOR_HEALTH_PORT: HTTP port for health endpoint (optional, default 40081)
    BUTLER_TELEGRAM_TOKEN: Telegram bot token (required)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from threading import Thread
from typing import Any, Literal

import httpx
import uvicorn
from fastapi import FastAPI
from prometheus_client import REGISTRY, generate_latest
from pydantic import BaseModel

from butlers.connectors.heartbeat import ConnectorHeartbeat, HeartbeatConfig
from butlers.connectors.mcp_client import CachedMCPClient
from butlers.connectors.metrics import ConnectorMetrics, get_error_type
from butlers.core.logging import configure_logging

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}"

_MEDIA_TYPE_LABELS: dict[str, str] = {
    "photo": "Photo",
    "sticker": "Sticker",
    "voice": "Voice message",
    "video_note": "Video message",
    "video": "Video",
    "animation": "GIF",
    "document": "Document",
    "audio": "Audio",
    "location": "Location",
    "contact": "Contact",
    "poll": "Poll",
    "dice": "Dice",
}


def _extract_normalized_text(msg: dict[str, Any]) -> str | None:
    """Extract meaningful text from a Telegram message dict.

    Returns the best available text representation using a tiered strategy:
    1. text — standard text messages
    2. caption — media messages with captions
    3. Media type descriptor — synthesized tag like [Photo], [Sticker: 😀]
    4. None — service messages with no user content
    """
    # Tier 1: explicit text field
    if msg.get("text"):
        return msg["text"]

    # Tier 2: caption on media messages
    if msg.get("caption"):
        return msg["caption"]

    # Tier 3: media type descriptor
    for media_key, label in _MEDIA_TYPE_LABELS.items():
        if media_key not in msg:
            continue

        # Enrich specific media types
        if media_key == "poll" and isinstance(msg["poll"], dict):
            question = msg["poll"].get("question", "")
            if question:
                return f"[Poll: {question}]"

        if media_key == "sticker" and isinstance(msg["sticker"], dict):
            emoji = msg["sticker"].get("emoji", "")
            if emoji:
                return f"[Sticker: {emoji}]"

        if media_key == "contact" and isinstance(msg["contact"], dict):
            contact = msg["contact"]
            first = contact.get("first_name", "")
            last = contact.get("last_name", "")
            name = f"{first} {last}".strip() if (first or last) else ""
            if name:
                return f"[Contact: {name}]"

        return f"[{label}]"

    # Tier 4: no extractable content (service messages like new_chat_members, etc.)
    return None


class HealthStatus(BaseModel):
    """Health check response model for Kubernetes probes."""

    status: Literal["healthy", "unhealthy"]
    uptime_seconds: float
    last_checkpoint_save_at: str | None
    last_ingest_submit_at: str | None
    source_api_connectivity: Literal["connected", "disconnected", "unknown"]
    timestamp: str


@dataclass
class TelegramBotConnectorConfig:
    """Configuration for Telegram bot connector runtime."""

    # Switchboard MCP config
    switchboard_mcp_url: str

    # Connector identity
    provider: str = "telegram"
    channel: str = "telegram"
    endpoint_identity: str = field(default="")

    # Telegram credentials
    telegram_token: str = field(default="")

    # Polling mode config
    cursor_path: Path | None = None
    poll_interval_s: float = 1.0

    # Webhook mode config
    webhook_url: str | None = None

    # Concurrency control
    max_inflight: int = 8

    # Health check config
    health_port: int = 40081

    @classmethod
    def from_env(cls) -> TelegramBotConnectorConfig:
        """Load configuration from environment variables."""
        switchboard_mcp_url = os.environ.get("SWITCHBOARD_MCP_URL")
        if not switchboard_mcp_url:
            raise ValueError("SWITCHBOARD_MCP_URL environment variable is required")

        provider = os.environ.get("CONNECTOR_PROVIDER", "telegram")
        channel = os.environ.get("CONNECTOR_CHANNEL", "telegram")

        endpoint_identity = os.environ.get("CONNECTOR_ENDPOINT_IDENTITY")
        if not endpoint_identity:
            raise ValueError("CONNECTOR_ENDPOINT_IDENTITY environment variable is required")

        telegram_token = os.environ.get("BUTLER_TELEGRAM_TOKEN")
        if not telegram_token:
            raise ValueError("BUTLER_TELEGRAM_TOKEN environment variable is required")

        cursor_path_str = os.environ.get("CONNECTOR_CURSOR_PATH")
        cursor_path = Path(cursor_path_str) if cursor_path_str else None

        poll_interval_s = float(os.environ.get("CONNECTOR_POLL_INTERVAL_S", "1.0"))

        webhook_url = os.environ.get("CONNECTOR_WEBHOOK_URL")

        max_inflight = int(os.environ.get("CONNECTOR_MAX_INFLIGHT", "8"))

        health_port = int(os.environ.get("CONNECTOR_HEALTH_PORT", "40081"))

        return cls(
            switchboard_mcp_url=switchboard_mcp_url,
            provider=provider,
            channel=channel,
            endpoint_identity=endpoint_identity,
            telegram_token=telegram_token,
            cursor_path=cursor_path,
            poll_interval_s=poll_interval_s,
            webhook_url=webhook_url,
            max_inflight=max_inflight,
            health_port=health_port,
        )


class TelegramBotConnector:
    """Telegram bot connector runtime for transport-only ingestion.

    Responsibilities:
    - Poll Telegram getUpdates or register webhook
    - Normalize updates to ingest.v1 format
    - Submit to Switchboard ingest API
    - Persist polling cursor for safe resume
    - Expose health endpoint for Kubernetes probes

    Does NOT:
    - Classify messages
    - Route to specialist butlers
    - Mint canonical request_id values
    """

    def __init__(self, config: TelegramBotConnectorConfig) -> None:
        self._config = config
        self._http_client = httpx.AsyncClient(timeout=30.0)
        self._mcp_client = CachedMCPClient(config.switchboard_mcp_url, client_name="telegram-bot")
        self._last_update_id: int | None = None
        self._running = False
        self._semaphore = asyncio.Semaphore(config.max_inflight)

        # Metrics
        self._metrics = ConnectorMetrics(
            connector_type="telegram_bot",
            endpoint_identity=config.endpoint_identity,
        )

        # Health tracking
        self._start_time = time.time()
        self._last_checkpoint_save: float | None = None
        self._last_ingest_submit: float | None = None
        self._source_api_ok: bool | None = None
        self._health_server: uvicorn.Server | None = None
        self._health_thread: Thread | None = None

        # Heartbeat
        self._heartbeat: ConnectorHeartbeat | None = None

    @property
    def _telegram_api_base(self) -> str:
        return TELEGRAM_API_BASE.format(token=self._config.telegram_token)

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
        app = FastAPI(title="Telegram Connector Health")

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
            port=self._config.health_port,
            log_level="warning",
        )
        self._health_server = uvicorn.Server(config)

        def run_server() -> None:
            asyncio.run(self._health_server.serve())

        self._health_thread = Thread(target=run_server, daemon=True)
        self._health_thread.start()
        logger.info(
            "Health server started",
            extra={"port": self._config.health_port},
        )

    def _start_heartbeat(self) -> None:
        """Initialize and start heartbeat background task."""
        heartbeat_config = HeartbeatConfig.from_env(
            connector_type=self._config.provider,
            endpoint_identity=self._config.endpoint_identity,
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
            return ("error", "Telegram API unreachable or authentication failed")

        # Could add degraded state for high error rates
        return ("healthy", None)

    def _get_checkpoint(self) -> tuple[str | None, datetime | None]:
        """Get current checkpoint state for heartbeat.

        Returns:
            Tuple of (cursor, updated_at)
        """
        cursor = str(self._last_update_id) if self._last_update_id is not None else None
        updated_at = (
            datetime.fromtimestamp(self._last_checkpoint_save, UTC)
            if self._last_checkpoint_save is not None
            else None
        )
        return (cursor, updated_at)

    async def start_polling(self) -> None:
        """Start long-polling loop for dev mode.

        Loads checkpoint, polls for updates, normalizes and submits to ingest,
        persists new checkpoint after successful submission.
        """
        if not self._config.cursor_path:
            raise ValueError("CONNECTOR_CURSOR_PATH is required for polling mode")

        # Start health server
        self._start_health_server()

        # Start heartbeat
        self._start_heartbeat()

        # Load checkpoint
        self._load_checkpoint()

        self._running = True
        logger.info(
            "Starting Telegram bot connector in polling mode",
            extra={
                "endpoint_identity": self._config.endpoint_identity,
                "poll_interval_s": self._config.poll_interval_s,
                "last_update_id": self._last_update_id,
            },
        )

        while self._running:
            try:
                updates = await self._get_updates()
                if updates:
                    logger.info(
                        "Polled Telegram updates",
                        extra={
                            "update_count": len(updates),
                            "endpoint_identity": self._config.endpoint_identity,
                        },
                    )

                    # Process each update
                    for update in updates:
                        await self._process_update(update)

                    # Save checkpoint after successful batch
                    self._save_checkpoint()

            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Error polling Telegram updates",
                    extra={"endpoint_identity": self._config.endpoint_identity},
                )

            await asyncio.sleep(self._config.poll_interval_s)

    async def start_webhook(self) -> None:
        """Register webhook for prod mode.

        Sets the Telegram webhook URL. Incoming updates should be POSTed to
        the webhook endpoint and processed via process_webhook_update().
        """
        if not self._config.webhook_url:
            raise ValueError("CONNECTOR_WEBHOOK_URL is required for webhook mode")

        # Start health server
        self._start_health_server()

        # Start heartbeat
        self._start_heartbeat()

        await self._set_webhook(self._config.webhook_url)
        logger.info(
            "Registered Telegram webhook",
            extra={
                "endpoint_identity": self._config.endpoint_identity,
                "webhook_url": self._config.webhook_url,
            },
        )

    async def process_webhook_update(self, update: dict[str, Any]) -> None:
        """Process a single webhook update.

        Called by the webhook endpoint handler when Telegram POSTs an update.
        """
        await self._process_update(update)

    async def stop(self) -> None:
        """Stop the connector gracefully."""
        self._running = False

        # Stop heartbeat
        if self._heartbeat is not None:
            await self._heartbeat.stop()

        await self._mcp_client.aclose()
        await self._http_client.aclose()

    # -------------------------------------------------------------------------
    # Internal: Telegram API calls
    # -------------------------------------------------------------------------

    async def _get_updates(self) -> list[dict[str, Any]]:
        """Call Telegram getUpdates API and return new updates."""
        url = f"{self._telegram_api_base}/getUpdates"
        params: dict[str, Any] = {"timeout": 0}
        if self._last_update_id is not None:
            params["offset"] = self._last_update_id + 1

        try:
            resp = await self._http_client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            updates: list[dict[str, Any]] = data.get("result", [])

            if updates:
                self._last_update_id = updates[-1]["update_id"]

            # Mark API as connected on success
            self._source_api_ok = True

            # Record successful API call
            self._metrics.record_source_api_call(api_method="getUpdates", status="success")

            return updates
        except Exception as exc:
            # Mark API as disconnected on failure
            self._source_api_ok = False

            # Record failed API call
            is_rate_limited = (
                isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429
            )
            status = "rate_limited" if is_rate_limited else "error"
            self._metrics.record_source_api_call(api_method="getUpdates", status=status)
            self._metrics.record_error(error_type=get_error_type(exc), operation="fetch_updates")

            raise

    async def _set_webhook(self, webhook_url: str) -> dict[str, Any]:
        """Call Telegram setWebhook API."""
        url = f"{self._telegram_api_base}/setWebhook"
        try:
            resp = await self._http_client.post(url, json={"url": webhook_url})
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()

            # Mark API as connected on success
            self._source_api_ok = True

            # Record successful API call
            self._metrics.record_source_api_call(api_method="setWebhook", status="success")

            return data
        except Exception as exc:
            # Mark API as disconnected on failure
            self._source_api_ok = False

            # Record failed API call
            self._metrics.record_source_api_call(api_method="setWebhook", status="error")
            self._metrics.record_error(error_type=get_error_type(exc), operation="set_webhook")

            raise

    # -------------------------------------------------------------------------
    # Internal: Update processing
    # -------------------------------------------------------------------------

    async def _process_update(self, update: dict[str, Any]) -> None:
        """Normalize Telegram update to ingest.v1 and submit to Switchboard.

        This is the core transport-only boundary. The connector:
        1. Extracts relevant fields from Telegram update
        2. Maps to canonical ingest.v1 contract
        3. Submits to Switchboard ingest API
        4. Handles retries and errors

        Updates that produce no usable content (service messages, non-message
        updates) are silently skipped.

        Does NOT classify or route - that happens downstream.
        """
        async with self._semaphore:
            try:
                envelope = self._normalize_to_ingest_v1(update)
                if envelope is None:
                    return  # Nothing to ingest
                await self._submit_to_ingest(envelope)
            except Exception:
                logger.exception(
                    "Failed to process Telegram update",
                    extra={
                        "update_id": update.get("update_id"),
                        "endpoint_identity": self._config.endpoint_identity,
                    },
                )

    def _normalize_to_ingest_v1(self, update: dict[str, Any]) -> dict[str, Any] | None:
        """Normalize Telegram update to canonical ingest.v1 format.

        Returns None when the update has no usable content (service messages,
        non-message updates like callback_query/inline_query).

        Mapping (from docs/connectors/telegram_bot.md):
        - source.channel: "telegram"
        - source.provider: "telegram"
        - source.endpoint_identity: receiving bot identity
        - event.external_event_id: update_id
        - event.external_thread_id: chat.id
        - event.observed_at: current timestamp (RFC3339)
        - sender.identity: message.from.id
        - payload.raw: full Telegram update JSON
        - payload.normalized_text: extracted text
        - control.idempotency_key: telegram:<endpoint_identity>:<update_id>
        """
        update_id = str(update.get("update_id", "unknown"))
        chat_id = None
        sender_id = "unknown"

        # Extract message data (handles message, edited_message, channel_post)
        msg = None
        for key in ("message", "edited_message", "channel_post"):
            if key in update and isinstance(update[key], dict):
                msg = update[key]
                break

        # No message object at all → non-message update (callback_query, inline_query, etc.)
        if msg is None:
            return None

        # Extract text using tiered strategy
        normalized_text = _extract_normalized_text(msg)

        # No extractable content → service message (new_chat_members, title changes, etc.)
        if normalized_text is None:
            return None

        if "chat" in msg and isinstance(msg["chat"], dict):
            chat_id = str(msg["chat"].get("id", ""))

        if "from" in msg and isinstance(msg["from"], dict):
            sender_id = str(msg["from"].get("id", "unknown"))

        message_id = msg.get("message_id")

        # Build thread identity as chat_id:message_id for reply targeting
        thread_identity = (
            f"{chat_id}:{message_id}" if chat_id and message_id is not None else chat_id
        )

        # Build ingest.v1 envelope
        return {
            "schema_version": "ingest.v1",
            "source": {
                "channel": self._config.channel,
                "provider": self._config.provider,
                "endpoint_identity": self._config.endpoint_identity,
            },
            "event": {
                "external_event_id": update_id,
                "external_thread_id": thread_identity,
                "observed_at": datetime.now(UTC).isoformat(),
            },
            "sender": {
                "identity": sender_id,
            },
            "payload": {
                "raw": update,
                "normalized_text": normalized_text,
            },
            "control": {
                "idempotency_key": f"telegram:{self._config.endpoint_identity}:{update_id}",
                "policy_tier": "default",
            },
        }

    async def _submit_to_ingest(self, envelope: dict[str, Any]) -> None:
        """Submit ingest.v1 envelope to Switchboard via MCP ingest tool.

        Handles retries and treats accepted duplicates as success.
        """
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

            logger.info(
                "Submitted to Switchboard ingest",
                extra={
                    "request_id": result.get("request_id") if isinstance(result, dict) else None,
                    "duplicate": is_duplicate,
                    "endpoint_identity": self._config.endpoint_identity,
                    "external_event_id": envelope["event"]["external_event_id"],
                },
            )
        except Exception as exc:
            logger.error(
                "Failed to submit to Switchboard ingest",
                extra={
                    "error": str(exc),
                    "endpoint_identity": self._config.endpoint_identity,
                },
            )
            self._metrics.record_error(error_type=get_error_type(exc), operation="ingest_submit")
            raise
        finally:
            # Record metrics
            latency = time.perf_counter() - start_time
            self._metrics.record_ingest_submission(status=status, latency=latency)

    # -------------------------------------------------------------------------
    # Internal: Checkpoint persistence
    # -------------------------------------------------------------------------

    def _load_checkpoint(self) -> None:
        """Load polling cursor from checkpoint file."""
        if not self._config.cursor_path:
            return

        if not self._config.cursor_path.exists():
            logger.info(
                "No checkpoint file found, starting from scratch",
                extra={"cursor_path": str(self._config.cursor_path)},
            )
            return

        try:
            with self._config.cursor_path.open("r") as f:
                data = json.load(f)
                self._last_update_id = data.get("last_update_id")

            logger.info(
                "Loaded checkpoint",
                extra={
                    "cursor_path": str(self._config.cursor_path),
                    "last_update_id": self._last_update_id,
                },
            )
        except Exception:
            logger.exception(
                "Failed to load checkpoint, starting from scratch",
                extra={"cursor_path": str(self._config.cursor_path)},
            )

    def _save_checkpoint(self) -> None:
        """Persist polling cursor to checkpoint file."""
        if not self._config.cursor_path:
            return

        try:
            # Ensure parent directory exists
            self._config.cursor_path.parent.mkdir(parents=True, exist_ok=True)

            # Write checkpoint atomically
            tmp_path = self._config.cursor_path.with_suffix(".tmp")
            with tmp_path.open("w") as f:
                json.dump({"last_update_id": self._last_update_id}, f)

            tmp_path.replace(self._config.cursor_path)

            # Record successful checkpoint save
            self._last_checkpoint_save = time.time()
            self._metrics.record_checkpoint_save(status="success")

            logger.debug(
                "Saved checkpoint",
                extra={
                    "cursor_path": str(self._config.cursor_path),
                    "last_update_id": self._last_update_id,
                },
            )
        except Exception as exc:
            self._metrics.record_checkpoint_save(status="error")
            self._metrics.record_error(error_type=get_error_type(exc), operation="checkpoint_save")
            logger.exception(
                "Failed to save checkpoint",
                extra={"cursor_path": str(self._config.cursor_path)},
            )


async def run_telegram_bot_connector() -> None:
    """CLI entry point for running Telegram bot connector."""
    configure_logging(level="INFO", butler_name="telegram-bot")

    config = TelegramBotConnectorConfig.from_env()
    connector = TelegramBotConnector(config)

    # Determine mode based on config
    if config.webhook_url:
        # Webhook mode
        logger.info("Running in webhook mode")
        await connector.start_webhook()
        # In webhook mode, the connector just registers the webhook and exits
        # Actual update handling happens via HTTP webhook endpoint
    else:
        # Polling mode
        logger.info("Running in polling mode")
        try:
            await connector.start_polling()
        except KeyboardInterrupt:
            logger.info("Received interrupt, stopping connector")
        finally:
            await connector.stop()


if __name__ == "__main__":
    asyncio.run(run_telegram_bot_connector())
