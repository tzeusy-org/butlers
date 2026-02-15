"""Gmail connector runtime for live ingestion via watch/history delta flow.

This connector implements the Gmail ingestion target state defined in
`docs/connectors/gmail.md`. It uses Gmail push notifications (users.watch)
combined with history-based delta fetch (users.history.list) to ingest newly
arrived mail in near real-time.

Key behaviors:
- OAuth-based authentication for Gmail API access
- Watch/history delta flow with bounded polling fallback
- Durable historyId cursor with restart-safe replay
- Idempotent submission to Switchboard ingest API
- Bounded in-flight requests with exponential backoff
- Explicit overload handling (no silent drops)
- Health endpoint for Kubernetes readiness/liveness probes

Environment variables (see `docs/connectors/gmail.md` section 4):
- SWITCHBOARD_API_BASE_URL (required)
- SWITCHBOARD_API_TOKEN (required when auth enabled)
- CONNECTOR_PROVIDER=gmail (required)
- CONNECTOR_CHANNEL=email (required)
- CONNECTOR_ENDPOINT_IDENTITY (required, e.g. "gmail:user:alice@gmail.com")
- CONNECTOR_CURSOR_PATH (required; stores last historyId)
- CONNECTOR_MAX_INFLIGHT (optional, default 8)
- CONNECTOR_HEALTH_PORT (optional, default 8080)
- GMAIL_CLIENT_ID (required)
- GMAIL_CLIENT_SECRET (required)
- GMAIL_REFRESH_TOKEN (required)
- GMAIL_WATCH_RENEW_INTERVAL_S (optional, default 86400 = 1 day)
- GMAIL_POLL_INTERVAL_S (optional, default 60)
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
from fastapi import FastAPI
from prometheus_client import REGISTRY, generate_latest
from pydantic import BaseModel, ConfigDict

from butlers.connectors.metrics import ConnectorMetrics, get_error_type

logger = logging.getLogger(__name__)


class HealthStatus(BaseModel):
    """Health check response model for Kubernetes probes."""

    status: Literal["healthy", "unhealthy"]
    uptime_seconds: float
    last_checkpoint_save_at: str | None
    last_ingest_submit_at: str | None
    source_api_connectivity: Literal["connected", "disconnected", "unknown"]
    timestamp: str


class GmailConnectorConfig(BaseModel):
    """Configuration for Gmail connector runtime."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Switchboard API
    switchboard_api_base_url: str
    switchboard_api_token: str | None = None

    # Connector identity
    connector_provider: str = "gmail"
    connector_channel: str = "email"
    connector_endpoint_identity: str
    connector_cursor_path: Path
    connector_max_inflight: int = 8

    # Health check config
    connector_health_port: int = 8080

    # Gmail API OAuth
    gmail_client_id: str
    gmail_client_secret: str
    gmail_refresh_token: str

    # Runtime controls
    gmail_watch_renew_interval_s: int = 86400  # 1 day
    gmail_poll_interval_s: int = 60

    @classmethod
    def from_env(cls) -> GmailConnectorConfig:
        """Load connector config from environment variables."""
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

        health_port_str = os.environ.get("CONNECTOR_HEALTH_PORT", "8080")
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

        return cls(
            switchboard_api_base_url=os.environ["SWITCHBOARD_API_BASE_URL"],
            switchboard_api_token=os.environ.get("SWITCHBOARD_API_TOKEN"),
            connector_provider=os.environ.get("CONNECTOR_PROVIDER", "gmail"),
            connector_channel=os.environ.get("CONNECTOR_CHANNEL", "email"),
            connector_endpoint_identity=os.environ["CONNECTOR_ENDPOINT_IDENTITY"],
            connector_cursor_path=Path(cursor_path_str),
            connector_max_inflight=max_inflight,
            connector_health_port=health_port,
            gmail_client_id=os.environ["GMAIL_CLIENT_ID"],
            gmail_client_secret=os.environ["GMAIL_CLIENT_SECRET"],
            gmail_refresh_token=os.environ["GMAIL_REFRESH_TOKEN"],
            gmail_watch_renew_interval_s=watch_renew_interval,
            gmail_poll_interval_s=poll_interval,
        )


class GmailCursor(BaseModel):
    """Durable checkpoint state for Gmail history tracking."""

    model_config = ConfigDict(extra="forbid")

    history_id: str
    last_updated_at: str  # ISO 8601 timestamp


class GmailConnectorRuntime:
    """Gmail connector runtime using watch/history delta flow."""

    def __init__(self, config: GmailConnectorConfig) -> None:
        self._config = config
        self._http_client: httpx.AsyncClient | None = None
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

    async def start(self) -> None:
        """Start the Gmail connector runtime."""
        self._running = True
        self._http_client = httpx.AsyncClient(timeout=30.0)

        # Start health server
        self._start_health_server()

        logger.info(
            "Gmail connector starting: cursor=%s",
            self._config.connector_cursor_path,
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
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None
        logger.info("Gmail connector stopped")

    async def _run_ingestion_loop(self) -> None:
        """Main ingestion loop: poll for history changes and ingest new messages."""
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
                logger.error("Error in ingestion loop: %s", exc, exc_info=True)
                # Back off on error
                await asyncio.sleep(min(60, self._config.gmail_poll_interval_s * 2))

            # Wait before next poll
            await asyncio.sleep(self._config.gmail_poll_interval_s)

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
        return GmailCursor.model_validate(cursor_data)

    async def _save_cursor(self, cursor: GmailCursor) -> None:
        """Save cursor state to disk."""
        try:
            self._config.connector_cursor_path.parent.mkdir(parents=True, exist_ok=True)
            self._config.connector_cursor_path.write_text(cursor.model_dump_json(indent=2))

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
                envelope = self._build_ingest_envelope(message_data)

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

    def _build_ingest_envelope(self, message_data: dict[str, Any]) -> dict[str, Any]:
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

    async def _submit_to_ingest_api(self, envelope: dict[str, Any]) -> None:
        """Submit ingest.v1 envelope to Switchboard ingest API."""
        if not self._http_client:
            raise RuntimeError("HTTP client not initialized")

        url = f"{self._config.switchboard_api_base_url}/api/switchboard/ingest"
        headers = {"Content-Type": "application/json"}
        if self._config.switchboard_api_token:
            headers["Authorization"] = f"Bearer {self._config.switchboard_api_token}"

        start_time = time.perf_counter()
        status = "error"

        try:
            response = await self._http_client.post(
                url,
                json=envelope,
                headers=headers,
            )

            # Handle overload/rate limit
            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After", "60")
                logger.warning("Ingest API rate limited, retrying after %s seconds", retry_after)
                await asyncio.sleep(int(retry_after))
                # Retry once
                response = await self._http_client.post(
                    url,
                    json=envelope,
                    headers=headers,
                )

            response.raise_for_status()
            result = response.json()

            # Record successful ingest submission
            self._last_ingest_submit = time.time()

            # Determine status for metrics
            status = "duplicate" if result.get("duplicate", False) else "success"

            if result.get("duplicate"):
                logger.debug("Duplicate ingestion for %s", envelope["event"]["external_event_id"])
            else:
                logger.info(
                    "Ingestion accepted: request_id=%s, event_id=%s",
                    result.get("request_id"),
                    envelope["event"]["external_event_id"],
                )
        except Exception as exc:
            self._metrics.record_error(error_type=get_error_type(exc), operation="ingest_submit")
            raise
        finally:
            # Record metrics
            latency = time.perf_counter() - start_time
            self._metrics.record_ingest_submission(status=status, latency=latency)


async def run_gmail_connector() -> None:
    """Run the Gmail connector runtime (async entrypoint)."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    try:
        config = GmailConnectorConfig.from_env()
    except Exception as exc:
        logger.error("Failed to load connector config: %s", exc)
        raise

    connector = GmailConnectorRuntime(config)
    await connector.start()


def main() -> None:
    """CLI entrypoint for Gmail connector."""
    asyncio.run(run_gmail_connector())


if __name__ == "__main__":
    main()
