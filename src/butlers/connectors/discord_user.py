"""Discord User Connector runtime for live ingestion.

DRAFT — v2-only WIP, not production-ready.

This connector implements a Discord user-account ingestion runtime as defined
in ``docs/connectors/draft_discord.md``. It uses the Discord Gateway (WebSocket)
to receive real-time message events visible to the linked user account and
normalizes them to ingest.v1 envelopes for Switchboard routing.

IMPORTANT: This connector is privacy-sensitive and requires explicit user consent,
proper credential management, and platform ToS review before deployment.

Key behaviors:
- Discord Gateway WebSocket connection for real-time message events
- Normalize Discord messages to ingest.v1 format
- Checkpoint-based resume (track last processed message ID per channel)
- Health endpoint for liveness reporting
- Prometheus metrics
- Heartbeat registration with Switchboard connector registry
- Optional guild/channel allowlisting for scope control

Environment variables:
- SWITCHBOARD_MCP_URL (required): SSE endpoint for Switchboard MCP server
- CONNECTOR_PROVIDER=discord (required)
- CONNECTOR_CHANNEL=discord (required)
- CONNECTOR_ENDPOINT_IDENTITY (required; e.g. "discord:user:<user_id>")
- CONNECTOR_CURSOR_PATH (required; checkpoint file path)
- CONNECTOR_MAX_INFLIGHT (optional, default 8)
- CONNECTOR_HEALTH_PORT (optional, default 40084)
- DISCORD_BOT_TOKEN (required; Discord bot token resolved from env or DB)
- DISCORD_GUILD_ALLOWLIST (optional; comma-separated guild IDs to ingest)
- DISCORD_CHANNEL_ALLOWLIST (optional; comma-separated channel IDs to ingest)

Discord-specific notes:
- DISCORD_BOT_TOKEN is a Discord bot token (from developer portal).
- For user-account context ingestion (v2), production deployment requires
  additional OAuth flow via DISCORD_CLIENT_ID, DISCORD_CLIENT_SECRET,
  DISCORD_REDIRECT_URI, and DISCORD_REFRESH_TOKEN — these are reserved for
  future implementation after ToS/compliance review.

Privacy/Safety:
- Explicit user consent and scope disclosure mandatory before enabling.
- Use DISCORD_GUILD_ALLOWLIST and DISCORD_CHANNEL_ALLOWLIST to limit ingestion scope.
- Never commit credentials to version control.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from threading import Thread
from typing import Any, Literal

import aiohttp
import uvicorn
from fastapi import FastAPI
from prometheus_client import REGISTRY, generate_latest
from pydantic import BaseModel

from butlers.connectors.heartbeat import ConnectorHeartbeat, HeartbeatConfig
from butlers.connectors.mcp_client import CachedMCPClient
from butlers.connectors.metrics import ConnectorMetrics, get_error_type
from butlers.core.logging import configure_logging

logger = logging.getLogger(__name__)

# Discord Gateway constants
DISCORD_GATEWAY_URL = "wss://gateway.discord.gg/?v=10&encoding=json"
DISCORD_API_BASE = "https://discord.com/api/v10"

# Discord Gateway opcodes
GATEWAY_OPCODE_DISPATCH = 0
GATEWAY_OPCODE_HEARTBEAT = 1
GATEWAY_OPCODE_IDENTIFY = 2
GATEWAY_OPCODE_RESUME = 6
GATEWAY_OPCODE_RECONNECT = 7
GATEWAY_OPCODE_INVALID_SESSION = 9
GATEWAY_OPCODE_HELLO = 10
GATEWAY_OPCODE_HEARTBEAT_ACK = 11

# Discord gateway event types we ingest
_INGESTED_EVENT_TYPES = frozenset(
    {
        "MESSAGE_CREATE",
        "MESSAGE_UPDATE",
        "MESSAGE_DELETE",
    }
)


def _extract_normalized_text(msg: dict[str, Any]) -> str | None:
    """Extract meaningful text from a Discord message dict.

    Returns the best available text representation using a tiered strategy:
    1. content — standard text messages
    2. attachment/embed descriptors — synthesized tags like [Attachment: filename.pdf]
    3. None — messages with no extractable user content
    """
    # Tier 1: explicit content field
    content = msg.get("content", "")
    if content:
        return content

    # Tier 2: attachment descriptors
    attachments = msg.get("attachments", [])
    if attachments and isinstance(attachments, list):
        descriptions = []
        for attachment in attachments:
            if not isinstance(attachment, dict):
                continue
            filename = attachment.get("filename") or attachment.get("id", "file")
            descriptions.append(f"[Attachment: {filename}]")
        if descriptions:
            return " ".join(descriptions)

    # Tier 3: embed descriptors
    embeds = msg.get("embeds", [])
    if embeds and isinstance(embeds, list):
        descriptions = []
        for embed in embeds:
            if not isinstance(embed, dict):
                continue
            title = embed.get("title") or embed.get("description") or "Embed"
            descriptions.append(f"[Embed: {title}]")
        if descriptions:
            return " ".join(descriptions)

    # Tier 4: sticker/sticker_items
    sticker_items = msg.get("sticker_items", [])
    if sticker_items and isinstance(sticker_items, list):
        sticker_names = [s.get("name", "sticker") for s in sticker_items if isinstance(s, dict)]
        if sticker_names:
            return " ".join(f"[Sticker: {name}]" for name in sticker_names)

    # No extractable content
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
class DiscordUserConnectorConfig:
    """Configuration for Discord user connector runtime."""

    # Switchboard MCP config
    switchboard_mcp_url: str

    # Connector identity
    provider: str = "discord"
    channel: str = "discord"
    endpoint_identity: str = field(default="")

    # Discord credentials
    discord_bot_token: str = field(default="")

    # Scope controls
    guild_allowlist: frozenset[str] = field(default_factory=frozenset)
    channel_allowlist: frozenset[str] = field(default_factory=frozenset)

    # Checkpoint config
    cursor_path: Path | None = None

    # Concurrency control
    max_inflight: int = 8

    # Health check config
    health_port: int = 40084

    @classmethod
    def from_env(cls) -> DiscordUserConnectorConfig:
        """Load configuration from environment variables."""
        switchboard_mcp_url = os.environ.get("SWITCHBOARD_MCP_URL")
        if not switchboard_mcp_url:
            raise ValueError("SWITCHBOARD_MCP_URL environment variable is required")

        provider = os.environ.get("CONNECTOR_PROVIDER", "discord")
        channel = os.environ.get("CONNECTOR_CHANNEL", "discord")

        endpoint_identity = os.environ.get("CONNECTOR_ENDPOINT_IDENTITY")
        if not endpoint_identity:
            raise ValueError("CONNECTOR_ENDPOINT_IDENTITY environment variable is required")

        discord_bot_token = os.environ.get("DISCORD_BOT_TOKEN")
        if not discord_bot_token:
            raise ValueError("DISCORD_BOT_TOKEN environment variable is required")

        cursor_path_str = os.environ.get("CONNECTOR_CURSOR_PATH")
        cursor_path = Path(cursor_path_str) if cursor_path_str else None

        # Parse optional allowlists (comma-separated IDs)
        guild_allowlist_str = os.environ.get("DISCORD_GUILD_ALLOWLIST", "")
        guild_allowlist: frozenset[str] = frozenset(
            g.strip() for g in guild_allowlist_str.split(",") if g.strip()
        )

        channel_allowlist_str = os.environ.get("DISCORD_CHANNEL_ALLOWLIST", "")
        channel_allowlist: frozenset[str] = frozenset(
            c.strip() for c in channel_allowlist_str.split(",") if c.strip()
        )

        max_inflight = int(os.environ.get("CONNECTOR_MAX_INFLIGHT", "8"))
        health_port = int(os.environ.get("CONNECTOR_HEALTH_PORT", "40084"))

        return cls(
            switchboard_mcp_url=switchboard_mcp_url,
            provider=provider,
            channel=channel,
            endpoint_identity=endpoint_identity,
            discord_bot_token=discord_bot_token,
            guild_allowlist=guild_allowlist,
            channel_allowlist=channel_allowlist,
            cursor_path=cursor_path,
            max_inflight=max_inflight,
            health_port=health_port,
        )


class DiscordUserConnector:
    """Discord user connector runtime for transport-only ingestion.

    DRAFT — v2-only WIP, not production-ready.

    Responsibilities:
    - Connect to Discord Gateway via WebSocket
    - Receive and normalize message events to ingest.v1
    - Submit to Switchboard ingest API
    - Persist per-channel checkpoints for safe resume
    - Expose health endpoint for Kubernetes probes
    - Send periodic heartbeats to Switchboard

    Does NOT:
    - Classify messages
    - Route to specialist butlers
    - Mint canonical request_id values
    - Bypass Switchboard canonical ingest semantics
    """

    def __init__(self, config: DiscordUserConnectorConfig) -> None:
        self._config = config
        self._mcp_client = CachedMCPClient(config.switchboard_mcp_url, client_name="discord-user")
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._http_session: aiohttp.ClientSession | None = None
        self._running = False
        self._semaphore = asyncio.Semaphore(config.max_inflight)

        # Gateway state
        self._sequence: int | None = None
        self._session_id: str | None = None
        self._heartbeat_interval_ms: int | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._last_heartbeat_ack: float = 0.0
        self._consecutive_failures: int = 0

        # Checkpoint state: {channel_id: last_message_id}
        self._channel_checkpoints: dict[str, str] = {}

        # Metrics
        self._metrics = ConnectorMetrics(
            connector_type="discord_user",
            endpoint_identity=config.endpoint_identity,
        )

        # Health tracking
        self._start_time = time.time()
        self._last_checkpoint_save: float | None = None
        self._last_ingest_submit: float | None = None
        self._gateway_connected: bool | None = None
        self._health_server: uvicorn.Server | None = None
        self._health_thread: Thread | None = None

        # Heartbeat
        self._switchboard_heartbeat: ConnectorHeartbeat | None = None

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

        if self._gateway_connected is None:
            connectivity = "unknown"
        elif self._gateway_connected:
            connectivity = "connected"
        else:
            connectivity = "disconnected"

        status = "healthy"
        if self._gateway_connected is False:
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
        app = FastAPI(title="Discord User Connector Health")

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

    def _start_switchboard_heartbeat(self) -> None:
        """Initialize and start heartbeat background task."""
        heartbeat_config = HeartbeatConfig.from_env(
            connector_type=self._config.provider,
            endpoint_identity=self._config.endpoint_identity,
            version=None,
        )

        self._switchboard_heartbeat = ConnectorHeartbeat(
            config=heartbeat_config,
            mcp_client=self._mcp_client,
            metrics=self._metrics,
            get_health_state=self._get_health_state,
            get_checkpoint=self._get_checkpoint,
        )

        self._switchboard_heartbeat.start()

    def _get_health_state(self) -> tuple[str, str | None]:
        """Determine current health state for heartbeat."""
        if self._gateway_connected is False:
            error_msg = "Discord Gateway disconnected or authentication failed"
            if self._consecutive_failures > 0:
                error_msg += f" (consecutive_failures={self._consecutive_failures})"
            return ("error", error_msg)

        if self._consecutive_failures > 0:
            return (
                "degraded",
                f"Gateway recovering after {self._consecutive_failures} consecutive failure(s)",
            )

        return ("healthy", None)

    def _get_checkpoint(self) -> tuple[str | None, datetime | None]:
        """Get current checkpoint state for heartbeat."""
        if not self._channel_checkpoints:
            return (None, None)

        # Report the most recent checkpoint across all channels
        cursor = json.dumps(self._channel_checkpoints)
        updated_at = (
            datetime.fromtimestamp(self._last_checkpoint_save, UTC)
            if self._last_checkpoint_save is not None
            else None
        )
        return (cursor, updated_at)

    # -------------------------------------------------------------------------
    # Main entry point
    # -------------------------------------------------------------------------

    async def start(self) -> None:
        """Connect to Discord Gateway and start ingesting messages.

        This:
        1. Loads checkpoint from disk
        2. Starts health server and heartbeat
        3. Connects to Discord Gateway via WebSocket
        4. Runs identify/resume handshake
        5. Processes dispatch events until stopped
        """
        if not self._config.cursor_path:
            raise ValueError("CONNECTOR_CURSOR_PATH is required")

        # Load checkpoint
        self._load_checkpoint()

        # Start health server
        self._start_health_server()

        # Create HTTP session
        self._http_session = aiohttp.ClientSession(
            headers={"Authorization": f"Bot {self._config.discord_bot_token}"},
            timeout=aiohttp.ClientTimeout(total=30),
        )

        # Start switchboard heartbeat (runs in background)
        self._start_switchboard_heartbeat()

        self._running = True
        logger.info(
            "Starting Discord user connector",
            extra={
                "endpoint_identity": self._config.endpoint_identity,
                "guild_allowlist": list(self._config.guild_allowlist) or "all",
                "channel_allowlist": list(self._config.channel_allowlist) or "all",
                "checkpoint_channels": len(self._channel_checkpoints),
            },
        )

        # Gateway reconnect loop with exponential backoff
        while self._running:
            try:
                await self._run_gateway_session()
                # Clean disconnect — reset backoff
                self._consecutive_failures = 0
            except asyncio.CancelledError:
                raise
            except Exception:
                self._consecutive_failures += 1
                self._gateway_connected = False
                logger.exception(
                    "Discord Gateway session failed",
                    extra={
                        "endpoint_identity": self._config.endpoint_identity,
                        "consecutive_failures": self._consecutive_failures,
                    },
                )

                if not self._running:
                    break

                # Exponential backoff with jitter, capped at 60s
                base_backoff = 1.0 * (2 ** min(self._consecutive_failures, 6))
                capped_backoff = min(base_backoff, 60.0)
                jitter = capped_backoff * 0.1 * (2 * random.random() - 1)
                sleep_s = capped_backoff + jitter
                logger.info(
                    "Reconnecting to Discord Gateway in %.1fs",
                    sleep_s,
                    extra={"endpoint_identity": self._config.endpoint_identity},
                )
                await asyncio.sleep(sleep_s)

    async def stop(self) -> None:
        """Stop the connector gracefully."""
        self._running = False

        # Cancel Discord heartbeat task
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None

        # Close WebSocket
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()
            self._ws = None

        # Stop switchboard heartbeat
        if self._switchboard_heartbeat is not None:
            await self._switchboard_heartbeat.stop()

        # Close MCP and HTTP clients
        await self._mcp_client.aclose()
        if self._http_session is not None:
            await self._http_session.close()
            self._http_session = None

        logger.info(
            "Discord user connector stopped",
            extra={"endpoint_identity": self._config.endpoint_identity},
        )

    # -------------------------------------------------------------------------
    # Internal: Gateway session
    # -------------------------------------------------------------------------

    async def _run_gateway_session(self) -> None:
        """Run a single Discord Gateway WebSocket session.

        Returns normally only on a clean intentional disconnect (self._running is False).
        Raises RuntimeError on unclean WebSocket close or error so the outer
        reconnect loop can apply exponential backoff before retrying.
        """
        assert self._http_session is not None

        _unclean_disconnect: bool = False

        async with self._http_session.ws_connect(DISCORD_GATEWAY_URL) as ws:
            self._ws = ws
            self._gateway_connected = True
            self._consecutive_failures = 0

            logger.info(
                "Connected to Discord Gateway",
                extra={"endpoint_identity": self._config.endpoint_identity},
            )

            async for msg in ws:
                if not self._running:
                    break

                if msg.type == aiohttp.WSMsgType.TEXT:
                    await self._handle_gateway_message(json.loads(msg.data))
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    error = ws.exception()
                    self._gateway_connected = False
                    self._metrics.record_error(
                        error_type=get_error_type(error) if error else "ws_error",
                        operation="gateway_receive",
                    )
                    logger.error(
                        "Discord Gateway WebSocket error",
                        extra={
                            "endpoint_identity": self._config.endpoint_identity,
                            "error": str(error),
                        },
                    )
                    _unclean_disconnect = True
                    break
                elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED):
                    self._gateway_connected = False
                    logger.warning(
                        "Discord Gateway WebSocket closed",
                        extra={
                            "endpoint_identity": self._config.endpoint_identity,
                            "close_code": ws.close_code,
                        },
                    )
                    _unclean_disconnect = True
                    break

        self._ws = None

        # Raise so the outer loop applies backoff on unclean disconnects.
        # Do NOT raise when self._running is False (graceful shutdown).
        if _unclean_disconnect and self._running:
            raise RuntimeError(
                f"Discord Gateway disconnected unexpectedly "
                f"(close_code={ws.close_code if hasattr(ws, 'close_code') else 'unknown'})"
            )

    async def _handle_gateway_message(self, payload: dict[str, Any]) -> None:
        """Dispatch a Gateway message to the appropriate handler."""
        opcode = payload.get("op")

        if opcode == GATEWAY_OPCODE_HELLO:
            # Server sent HELLO — start heartbeat and identify
            heartbeat_interval_ms = payload.get("d", {}).get("heartbeat_interval", 41250)
            self._heartbeat_interval_ms = heartbeat_interval_ms
            await self._start_discord_heartbeat(heartbeat_interval_ms)
            await self._identify_or_resume()

        elif opcode == GATEWAY_OPCODE_HEARTBEAT_ACK:
            self._last_heartbeat_ack = time.time()
            logger.debug("Discord Gateway heartbeat ack received")

        elif opcode == GATEWAY_OPCODE_HEARTBEAT:
            # Server requests immediate heartbeat
            await self._send_heartbeat_to_gateway()

        elif opcode == GATEWAY_OPCODE_RECONNECT:
            logger.info(
                "Discord Gateway requested reconnect",
                extra={"endpoint_identity": self._config.endpoint_identity},
            )
            if self._ws and not self._ws.closed:
                await self._ws.close()

        elif opcode == GATEWAY_OPCODE_INVALID_SESSION:
            resumable = payload.get("d", False)
            logger.warning(
                "Discord Gateway invalid session",
                extra={
                    "endpoint_identity": self._config.endpoint_identity,
                    "resumable": resumable,
                },
            )
            if not resumable:
                # Clear session state so next connect will identify fresh
                self._session_id = None
                self._sequence = None
            if self._ws and not self._ws.closed:
                await self._ws.close()

        elif opcode == GATEWAY_OPCODE_DISPATCH:
            # Update sequence number
            seq = payload.get("s")
            if seq is not None:
                self._sequence = seq

            event_type = payload.get("t")
            event_data = payload.get("d") or {}

            if event_type == "READY":
                self._session_id = event_data.get("session_id")
                user = event_data.get("user", {})
                user_id = user.get("id", "unknown")
                logger.info(
                    "Discord Gateway READY",
                    extra={
                        "endpoint_identity": self._config.endpoint_identity,
                        "user_id": user_id,
                        "session_id": self._session_id,
                    },
                )
                self._metrics.record_source_api_call(
                    api_method="gateway_identify", status="success"
                )

            elif event_type in _INGESTED_EVENT_TYPES:
                await self._process_dispatch_event(event_type, event_data)

    async def _identify_or_resume(self) -> None:
        """Send IDENTIFY or RESUME payload to the Gateway."""
        if self._ws is None:
            return

        if self._session_id and self._sequence is not None:
            # Attempt RESUME
            payload = {
                "op": GATEWAY_OPCODE_RESUME,
                "d": {
                    "token": self._config.discord_bot_token,
                    "session_id": self._session_id,
                    "seq": self._sequence,
                },
            }
            await self._ws.send_json(payload)
            logger.info(
                "Sent Discord Gateway RESUME",
                extra={
                    "endpoint_identity": self._config.endpoint_identity,
                    "session_id": self._session_id,
                    "seq": self._sequence,
                },
            )
        else:
            # Fresh IDENTIFY
            intents = (
                (1 << 9)  # GUILD_MESSAGES
                | (1 << 12)  # DIRECT_MESSAGES
                | (1 << 15)  # MESSAGE_CONTENT (privileged — must be enabled in dev portal)
            )
            payload = {
                "op": GATEWAY_OPCODE_IDENTIFY,
                "d": {
                    "token": self._config.discord_bot_token,
                    "intents": intents,
                    "properties": {
                        "os": "linux",
                        "browser": "butlers-discord-connector",
                        "device": "butlers-discord-connector",
                    },
                },
            }
            await self._ws.send_json(payload)
            logger.info(
                "Sent Discord Gateway IDENTIFY",
                extra={"endpoint_identity": self._config.endpoint_identity},
            )

    async def _start_discord_heartbeat(self, interval_ms: int) -> None:
        """Start the Discord-level Gateway heartbeat loop."""
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass

        self._heartbeat_task = asyncio.create_task(self._discord_heartbeat_loop(interval_ms))

    async def _discord_heartbeat_loop(self, interval_ms: int) -> None:
        """Send periodic heartbeats to keep the Gateway connection alive."""
        interval_s = interval_ms / 1000.0
        # Initial jitter: send between 0 and interval_s to avoid thundering herd
        await asyncio.sleep(random.random() * interval_s)

        try:
            while self._running:
                await self._send_heartbeat_to_gateway()
                await asyncio.sleep(interval_s)
        except asyncio.CancelledError:
            raise

    async def _send_heartbeat_to_gateway(self) -> None:
        """Send a single heartbeat payload to the Discord Gateway."""
        if self._ws is None or self._ws.closed:
            return
        try:
            payload = {"op": GATEWAY_OPCODE_HEARTBEAT, "d": self._sequence}
            await self._ws.send_json(payload)
            logger.debug(
                "Sent Discord Gateway heartbeat",
                extra={
                    "endpoint_identity": self._config.endpoint_identity,
                    "seq": self._sequence,
                },
            )
        except Exception:
            logger.exception(
                "Failed to send Discord Gateway heartbeat",
                extra={"endpoint_identity": self._config.endpoint_identity},
            )

    # -------------------------------------------------------------------------
    # Internal: Event processing
    # -------------------------------------------------------------------------

    async def _process_dispatch_event(
        self,
        event_type: str,
        event_data: dict[str, Any],
    ) -> None:
        """Normalize a Discord dispatch event to ingest.v1 and submit to Switchboard."""
        async with self._semaphore:
            try:
                envelope = self._normalize_to_ingest_v1(event_type, event_data)
                if envelope is None:
                    return  # Nothing to ingest

                # Apply scope filters
                if not self._is_allowed(event_data):
                    logger.debug(
                        "Discord event filtered by allowlist",
                        extra={
                            "guild_id": event_data.get("guild_id"),
                            "channel_id": event_data.get("channel_id"),
                        },
                    )
                    return

                await self._submit_to_ingest(envelope)

                # Advance per-channel checkpoint after successful ingest
                channel_id = event_data.get("channel_id")
                message_id = event_data.get("id")
                if channel_id and message_id:
                    self._update_checkpoint(channel_id, message_id)
                    await self._save_checkpoint()

            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Failed to process Discord event",
                    extra={
                        "event_type": event_type,
                        "endpoint_identity": self._config.endpoint_identity,
                    },
                )

    def _is_allowed(self, event_data: dict[str, Any]) -> bool:
        """Check whether this event passes the guild/channel allowlists.

        If an allowlist is empty, all values are allowed for that dimension.

        Guild allowlist behavior for DMs (events without guild_id):
        - If a guild allowlist is configured but no channel allowlist is set,
          DM events are blocked. This prevents a guild-scoped allowlist from
          accidentally leaking DM conversations.
        - If both guild and channel allowlists are configured, DMs are allowed
          only when the DM channel_id is in the channel allowlist.
        """
        guild_id = event_data.get("guild_id")
        channel_id = event_data.get("channel_id")

        if self._config.guild_allowlist:
            if guild_id:
                # Guild event: check guild against allowlist
                if str(guild_id) not in self._config.guild_allowlist:
                    return False
            else:
                # DM event (no guild_id): block unless channel allowlist permits it
                if not self._config.channel_allowlist:
                    return False
                # Fall through to channel allowlist check below

        if self._config.channel_allowlist and channel_id:
            if str(channel_id) not in self._config.channel_allowlist:
                return False

        return True

    def _normalize_to_ingest_v1(
        self,
        event_type: str,
        event_data: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Normalize a Discord dispatch event to canonical ingest.v1 format.

        Returns None when the event has no usable content.

        Mapping (from docs/connectors/draft_discord.md):
        - source.channel: "discord"
        - source.provider: "discord"
        - source.endpoint_identity: connector endpoint identity
        - event.external_event_id: Discord message/event ID (Snowflake)
        - event.external_thread_id: channel/thread ID
        - event.observed_at: connector-observed timestamp (RFC3339)
        - sender.identity: Discord author ID
        - payload.raw: full Discord payload
        - payload.normalized_text: extracted message text
        - control.idempotency_key: discord:<endpoint_identity>:<message_id>
        """
        message_id = event_data.get("id")
        if not message_id:
            return None

        channel_id = event_data.get("channel_id", "unknown")
        guild_id = event_data.get("guild_id")  # None for DMs

        # Determine thread identity: prefer thread_id if present, else channel_id
        thread_id = (
            event_data.get("thread", {}).get("id")
            if isinstance(event_data.get("thread"), dict)
            else None
        )
        external_thread_id = thread_id or channel_id

        # Extract sender identity
        author = event_data.get("author")
        if isinstance(author, dict):
            sender_identity = str(author.get("id", "unknown"))
        else:
            sender_identity = "unknown"

        # MESSAGE_DELETE events have no content — include as tombstones
        if event_type == "MESSAGE_DELETE":
            normalized_text = "[Message deleted]"
        else:
            normalized_text = _extract_normalized_text(event_data)
            if normalized_text is None:
                return None

        # Build raw payload including event_type for downstream awareness
        raw_payload = {
            "event_type": event_type,
            "guild_id": guild_id,
            **event_data,
        }

        return {
            "schema_version": "ingest.v1",
            "source": {
                "channel": self._config.channel,
                "provider": self._config.provider,
                "endpoint_identity": self._config.endpoint_identity,
            },
            "event": {
                "external_event_id": str(message_id),
                "external_thread_id": str(external_thread_id),
                "observed_at": datetime.now(UTC).isoformat(),
            },
            "sender": {
                "identity": sender_identity,
            },
            "payload": {
                "raw": raw_payload,
                "normalized_text": normalized_text,
            },
            "control": {
                "idempotency_key": (
                    f"{self._config.provider}:{self._config.endpoint_identity}:{message_id}"
                ),
                "policy_tier": "default",
            },
        }

    async def _submit_to_ingest(self, envelope: dict[str, Any]) -> None:
        """Submit ingest.v1 envelope to Switchboard via MCP ingest tool."""
        start_time = time.perf_counter()
        status = "error"

        try:
            result = await self._mcp_client.call_tool("ingest", envelope)

            if isinstance(result, dict) and result.get("status") == "error":
                error_msg = result.get("error", "Unknown ingest error")
                raise RuntimeError(f"Ingest tool error: {error_msg}")

            self._last_ingest_submit = time.time()

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
            latency = time.perf_counter() - start_time
            self._metrics.record_ingest_submission(status=status, latency=latency)

    # -------------------------------------------------------------------------
    # Internal: Checkpoint persistence
    # -------------------------------------------------------------------------

    def _update_checkpoint(self, channel_id: str, message_id: str) -> None:
        """Update in-memory checkpoint for a channel."""
        self._channel_checkpoints[channel_id] = message_id

    def _load_checkpoint(self) -> None:
        """Load per-channel checkpoints from checkpoint file."""
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
                checkpoints = data.get("channel_checkpoints", {})
                if isinstance(checkpoints, dict):
                    self._channel_checkpoints = {str(k): str(v) for k, v in checkpoints.items()}

            logger.info(
                "Loaded checkpoint",
                extra={
                    "cursor_path": str(self._config.cursor_path),
                    "channel_count": len(self._channel_checkpoints),
                },
            )
        except Exception:
            logger.exception(
                "Failed to load checkpoint, starting from scratch",
                extra={"cursor_path": str(self._config.cursor_path)},
            )

    async def _save_checkpoint(self) -> None:
        """Persist per-channel checkpoints to checkpoint file atomically.

        File I/O runs in a thread-pool executor to avoid blocking the asyncio
        event loop while writing the checkpoint.
        """
        if not self._config.cursor_path:
            return

        # Snapshot checkpoint state before entering executor to avoid data races
        checkpoints_snapshot = dict(self._channel_checkpoints)
        cursor_path = self._config.cursor_path

        def _do_io_save() -> None:
            """Synchronous file I/O portion executed off the event loop."""
            cursor_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = cursor_path.with_suffix(".tmp")
            with tmp_path.open("w") as f:
                json.dump({"channel_checkpoints": checkpoints_snapshot}, f)
            tmp_path.replace(cursor_path)

        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _do_io_save)

            self._last_checkpoint_save = time.time()
            self._metrics.record_checkpoint_save(status="success")

            logger.debug(
                "Saved checkpoint",
                extra={
                    "cursor_path": str(self._config.cursor_path),
                    "channel_count": len(self._channel_checkpoints),
                },
            )
        except Exception as exc:
            self._metrics.record_checkpoint_save(status="error")
            self._metrics.record_error(error_type=get_error_type(exc), operation="checkpoint_save")
            logger.exception(
                "Failed to save checkpoint",
                extra={"cursor_path": str(self._config.cursor_path)},
            )


async def run_discord_user_connector() -> None:
    """CLI entry point for running Discord user connector.

    Reads configuration from environment variables and runs the connector
    in gateway mode until interrupted.
    """
    configure_logging(level="INFO", butler_name="discord-user")

    config = DiscordUserConnectorConfig.from_env()
    connector = DiscordUserConnector(config)

    try:
        await connector.start()
    except KeyboardInterrupt:
        logger.info("Received interrupt, stopping connector")
    finally:
        await connector.stop()


if __name__ == "__main__":
    asyncio.run(run_discord_user_connector())
