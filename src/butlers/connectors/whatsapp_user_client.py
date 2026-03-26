"""WhatsApp User Client connector runtime for live ingestion.

This connector implements a WhatsApp user-client ingestion runtime via a Go
bridge sidecar wrapping whatsmeow. Its sole purpose is readonly contextualization:
ingesting DMs, group chats, and broadcast messages visible to the user's account
without ever sending, replying, or modifying anything.

IMPORTANT: This connector is privacy-sensitive and requires explicit user consent
(QR-pairing ceremony), proper credential management, and scope controls.

Key behaviors:
- Go bridge sidecar management via BridgeSubprocessManager
- SSE event consumer on bridge GET /events via async HTTP on Unix socket
- Real-time message event normalization to ingest.v1
- Per-chat ChatBuffer with time-based and size-based flush to Switchboard
- Durable checkpoint with restart-safe replay via cursor_store
- Shared discretion layer with ContactWeightResolver for identity-based gating
- Bounded backfill on startup via CONNECTOR_BACKFILL_WINDOW_H
- Health endpoint on port 40082 via health_socket.py
- ConnectorHeartbeat (120s interval) and ConnectorMetrics (Prometheus)

Environment variables:
- SWITCHBOARD_MCP_URL (required)
- CONNECTOR_PROVIDER=whatsapp (required)
- CONNECTOR_CHANNEL=whatsapp_user_client (required)
- CONNECTOR_MAX_INFLIGHT (optional, default 8)
- CONNECTOR_BACKFILL_WINDOW_H (optional, bounded startup replay in hours)
- CONNECTOR_BUTLER_DB_NAME (optional; local butler DB for per-butler overrides)
- BUTLER_SHARED_DB_NAME (optional; shared credential DB, defaults to 'butlers')
- CONNECTOR_HEARTBEAT_INTERVAL_S (optional, default 120)
- CONNECTOR_HEALTH_PORT (optional, default 40082)
- WA_BRIDGE_SOCKET (optional, default /tmp/wa-bridge.sock)
- WA_FLUSH_INTERVAL_S (optional, default 600)
- WA_BUFFER_MAX_MESSAGES (optional, default 50)

Security requirements:
- Never commit credentials or session artifacts to version control
- whatsapp_phone resolved from owner entity_info (DB) or bridge /status after pairing
- The Go bridge manages its own session keys from whatsapp_sessions table
- Explicit user consent required (QR pairing ceremony = physical consent)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

from butlers.connectors.bridge_manager import BridgeConfig, BridgeSubprocessManager
from butlers.connectors.discretion import (
    ContactWeightResolver,
    DiscretionEvaluator,
)
from butlers.connectors.discretion_dispatcher import DiscretionDispatcher
from butlers.connectors.filtered_event_buffer import FilteredEventBuffer, drain_replay_pending
from butlers.connectors.heartbeat import ConnectorHeartbeat, HeartbeatConfig
from butlers.connectors.mcp_client import CachedMCPClient
from butlers.connectors.metrics import ConnectorMetrics
from butlers.core.logging import configure_logging
from butlers.credential_store import (
    resolve_owner_entity_info,
    shared_db_name_from_env,
)
from butlers.db import db_params_from_env
from butlers.ingestion_policy import IngestionEnvelope, IngestionPolicyEvaluator

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_FLUSH_SCANNER_INTERVAL_S = 60  # How often the flush scanner wakes up
_BRIDGE_STARTUP_TIMEOUT_S = 60.0  # Bridge startup timeout (longer for QR re-pair)
_SSE_RECONNECT_DELAY_S = 5.0  # Delay before reconnecting SSE after failure
_SSE_KEEPALIVE_TIMEOUT_S = 90.0  # Max silence from SSE stream before treating as stale

# ---------------------------------------------------------------------------
# Chat buffer data structure
# ---------------------------------------------------------------------------


@dataclass
class ChatBuffer:
    """Per-chat accumulation buffer for incoming WhatsApp messages.

    Fields:
        messages:       Accumulated bridge event dicts since last flush.
        last_flush_ts:  Monotonic timestamp of the last flush (or creation).
        lock:           asyncio.Lock preventing concurrent flush + append.
        chat_jid:       The WhatsApp JID for this chat.
    """

    messages: list[dict[str, Any]] = field(default_factory=list)
    last_flush_ts: float = field(default_factory=time.monotonic)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    chat_jid: str = ""


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class WhatsAppUserClientConnectorConfig:
    """Configuration for the WhatsApp user-client connector runtime."""

    # Switchboard MCP config
    switchboard_mcp_url: str

    # Connector identity
    provider: str = "whatsapp"
    channel: str = "whatsapp_user_client"
    endpoint_identity: str = field(default="")

    # Bridge config
    bridge_socket: str = "/tmp/wa-bridge.sock"

    # Backfill
    backfill_window_h: int | None = None

    # Concurrency
    max_inflight: int = 8

    # Buffering / flush config
    flush_interval_s: int = 600
    buffer_max_messages: int = 50

    # Health port
    health_port: int = 40082

    @classmethod
    def from_env(cls) -> WhatsAppUserClientConnectorConfig:
        """Load non-credential configuration from environment variables.

        whatsapp_phone is resolved exclusively from owner entity_info via DB.
        """
        switchboard_mcp_url = os.environ.get("SWITCHBOARD_MCP_URL")
        if not switchboard_mcp_url:
            raise ValueError("SWITCHBOARD_MCP_URL environment variable is required")

        provider = os.environ.get("CONNECTOR_PROVIDER", "whatsapp")
        channel = os.environ.get("CONNECTOR_CHANNEL", "whatsapp_user_client")

        bridge_socket = os.environ.get("WA_BRIDGE_SOCKET", "/tmp/wa-bridge.sock")

        backfill_window_str = os.environ.get("CONNECTOR_BACKFILL_WINDOW_H")
        backfill_window_h = int(backfill_window_str) if backfill_window_str else None

        max_inflight = int(os.environ.get("CONNECTOR_MAX_INFLIGHT", "8"))
        flush_interval_s = int(os.environ.get("WA_FLUSH_INTERVAL_S", "600"))
        buffer_max_messages = int(os.environ.get("WA_BUFFER_MAX_MESSAGES", "50"))
        health_port = int(os.environ.get("CONNECTOR_HEALTH_PORT", "40082"))

        return cls(
            switchboard_mcp_url=switchboard_mcp_url,
            provider=provider,
            channel=channel,
            bridge_socket=bridge_socket,
            backfill_window_h=backfill_window_h,
            max_inflight=max_inflight,
            flush_interval_s=flush_interval_s,
            buffer_max_messages=buffer_max_messages,
            health_port=health_port,
        )


# ---------------------------------------------------------------------------
# Message type normalization
# ---------------------------------------------------------------------------


def normalize_message_text(event: dict[str, Any]) -> str:
    """Normalize a bridge event's message content to plain text.

    Applies the ingest.v1 field mapping spec for message type normalization:
    - Conversation / ExtendedTextMessage → text verbatim
    - ImageMessage → caption if present, else [image]
    - VideoMessage → caption if present, else [video]
    - AudioMessage / PTTMessage → [voice message] or [audio]
    - DocumentMessage → FileName and caption
    - StickerMessage → [sticker]
    - LocationMessage → [location: lat, lon, name]
    - ContactMessage → [contact: DisplayName]
    - ReactionMessage → [reaction: emoji to message_id]
    - PollCreationMessage → [poll: question — option1, option2, ...]
    - ProtocolMessage (revoke) → [message deleted]
    """
    msg_type = event.get("type", "")
    content = event.get("content", {}) or {}

    if msg_type in ("Conversation", "ExtendedTextMessage"):
        return content.get("text", "") or event.get("text", "")

    if msg_type == "ImageMessage":
        caption = content.get("caption", "")
        return caption if caption else "[image]"

    if msg_type == "VideoMessage":
        caption = content.get("caption", "")
        return caption if caption else "[video]"

    if msg_type == "AudioMessage":
        return "[audio]"

    if msg_type == "PTTMessage":
        return "[voice message]"

    if msg_type == "DocumentMessage":
        filename = content.get("fileName", "")
        caption = content.get("caption", "")
        parts = [p for p in [filename, caption] if p]
        return " — ".join(parts) if parts else "[document]"

    if msg_type == "StickerMessage":
        return "[sticker]"

    if msg_type == "LocationMessage":
        lat = content.get("degreesLatitude", "")
        lon = content.get("degreesLongitude", "")
        name = content.get("name", "")
        if name:
            return f"[location: {lat}, {lon}, {name}]"
        return f"[location: {lat}, {lon}]"

    if msg_type == "ContactMessage":
        display_name = content.get("displayName", "")
        return f"[contact: {display_name}]" if display_name else "[contact]"

    if msg_type == "ReactionMessage":
        emoji = content.get("text", "")
        key_obj = content.get("key")
        target_id = key_obj.get("id", "") if isinstance(key_obj, dict) else ""
        if emoji and target_id:
            return f"[reaction: {emoji} to {target_id}]"
        return f"[reaction: {emoji}]" if emoji else "[reaction]"

    if msg_type == "PollCreationMessage":
        question = content.get("name", "")
        options = content.get("options", []) or []
        option_texts = [o.get("optionName", "") if isinstance(o, dict) else str(o) for o in options]
        opts_str = ", ".join(o for o in option_texts if o)
        return f"[poll: {question} — {opts_str}]" if question else "[poll]"

    if msg_type == "ProtocolMessage":
        proto_type = content.get("type", "")
        if proto_type == "REVOKE" or proto_type == 0:
            return "[message deleted]"
        return f"[protocol: {proto_type}]"

    # Fallback: try to extract any text field
    text = event.get("text", "") or content.get("text", "") or content.get("caption", "")
    if text:
        return str(text)

    return f"[{msg_type.lower()}]" if msg_type else "[unknown]"


# ---------------------------------------------------------------------------
# SSE consumer
# ---------------------------------------------------------------------------


async def _sse_event_stream(socket_path: str) -> asyncio.AsyncGenerator[dict[str, Any], None]:
    """Consume SSE events from the bridge GET /events endpoint via Unix socket.

    Yields parsed JSON dicts for each SSE ``data:`` line.
    Raises on connection failure (caller handles reconnect).
    """
    reader, writer = await asyncio.open_unix_connection(socket_path)
    try:
        # HTTP GET request
        request = (
            "GET /events HTTP/1.0\r\n"
            "Host: localhost\r\n"
            "Accept: text/event-stream\r\n"
            "Connection: close\r\n"
            "\r\n"
        )
        writer.write(request.encode())
        await writer.drain()

        # Read HTTP status line and validate before consuming headers
        status_line = await asyncio.wait_for(reader.readline(), timeout=30.0)
        if status_line:
            parts = status_line.split(None, 2)
            if len(parts) >= 2:
                try:
                    status_code = int(parts[1])
                except ValueError:
                    status_code = 0
                if status_code != 200:
                    status_text = status_line.decode(errors="replace").strip()
                    raise ConnectionError(f"Bridge /events returned non-200 status: {status_text}")

        # Read and discard remaining HTTP headers
        while True:
            line = await asyncio.wait_for(reader.readline(), timeout=30.0)
            if not line or line == b"\r\n":
                break

        # Consume SSE stream
        while True:
            line = await asyncio.wait_for(
                reader.readline(),
                timeout=_SSE_KEEPALIVE_TIMEOUT_S,
            )
            if not line:
                # Connection closed by bridge
                return

            text = line.decode(errors="replace").rstrip()

            # SSE keepalive comment lines (": keepalive" or empty)
            if not text or text.startswith(":"):
                continue

            # SSE data line
            if text.startswith("data:"):
                data_str = text[len("data:") :].strip()
                if not data_str:
                    continue
                try:
                    yield json.loads(data_str)
                except json.JSONDecodeError:
                    logger.warning(
                        "WhatsApp bridge SSE: malformed JSON in event (redacted, length=%d bytes)",
                        len(data_str),
                    )
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Main connector class
# ---------------------------------------------------------------------------


class WhatsAppUserClientConnector:
    """WhatsApp user-client connector runtime for live ingestion.

    Responsibilities:
    - Manage the whatsapp-bridge Go sidecar via BridgeSubprocessManager
    - Consume SSE events from bridge /events endpoint
    - Normalize events to ingest.v1 format per message-type spec
    - Buffer messages per-chat JID with configurable flush interval/size cap
    - Submit batches to Switchboard ingest API via CachedMCPClient
    - Persist checkpoint for restart-safe resume
    - Filter messages via discretion layer with identity-based weights

    Does NOT:
    - Send, reply, react, edit, or delete anything on the user's WhatsApp
    - Classify messages or route to specialist butlers directly
    """

    def __init__(
        self,
        config: WhatsAppUserClientConnectorConfig,
        db_pool: Any | None = None,
        cursor_pool: Any | None = None,
    ) -> None:
        self._config = config
        self._mcp_client = CachedMCPClient(
            config.switchboard_mcp_url, client_name="whatsapp-user-client"
        )
        self._running = False
        self._semaphore = asyncio.Semaphore(config.max_inflight)
        self._last_event_id: str | None = None  # last processed bridge event ID / timestamp
        self._last_checkpoint_save: float | None = None

        # DB pools
        self._cursor_pool = cursor_pool
        self._db_pool = db_pool

        # Bridge subprocess manager
        self._bridge_manager: BridgeSubprocessManager | None = None

        # Metrics
        self._metrics = ConnectorMetrics(
            connector_type="whatsapp_user_client",
            endpoint_identity=config.endpoint_identity,
        )

        # Heartbeat
        self._switchboard_heartbeat: ConnectorHeartbeat | None = None

        # Ingestion policy evaluators
        self._ingestion_policy = IngestionPolicyEvaluator(
            scope=f"connector:whatsapp-user-client:{config.endpoint_identity}",
            db_pool=db_pool,
        )
        self._global_ingestion_policy = IngestionPolicyEvaluator(
            scope="global",
            db_pool=db_pool,
        )

        # Filtered event buffer
        self._filtered_event_buffer = FilteredEventBuffer(
            connector_type=config.provider,
            endpoint_identity=config.endpoint_identity,
        )

        # Discretion layer
        self._discretion_dispatcher: DiscretionDispatcher | None = (
            DiscretionDispatcher(pool=db_pool) if db_pool is not None else None
        )
        self._discretion_evaluators: dict[str, DiscretionEvaluator] = {}
        self._weight_resolver: ContactWeightResolver | None = (
            ContactWeightResolver(db_pool) if db_pool is not None else None
        )

        # Per-chat message buffers: chat_jid → ChatBuffer
        self._chat_buffers: dict[str, ChatBuffer] = {}

        # Background flush scanner task
        self._flush_scanner_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start the WhatsApp user-client connector.

        1. Start Go bridge sidecar and wait for 'connected'
        2. Load checkpoint from DB
        3. Optionally perform bounded backfill
        4. Subscribe to SSE event stream
        5. Run until stopped
        """
        if self._cursor_pool is None:
            raise ValueError("DB cursor pool is required")

        # Start Go bridge — pass DSN via env var to avoid leaking credentials
        # in ps / /proc/<pid>/cmdline output.
        bridge_cfg = BridgeConfig(
            binary="whatsapp-bridge",
            args=["--listen", f"unix://{self._config.bridge_socket}"],
            env={"WA_BRIDGE_DSN": _get_bridge_db_dsn()},
            bridge_socket=self._config.bridge_socket,
            startup_timeout_s=_BRIDGE_STARTUP_TIMEOUT_S,
        )
        self._bridge_manager = BridgeSubprocessManager(bridge_cfg)
        await self._bridge_manager.start()

        # Resolve phone from bridge if endpoint_identity is still pending
        if self._config.endpoint_identity == "whatsapp:pending":
            status = await self._bridge_manager.get_status()
            bridge_phone = status.get("phone")
            if bridge_phone:
                self._config = replace(self._config, endpoint_identity=f"whatsapp:{bridge_phone}")
                logger.info(
                    "Resolved endpoint_identity from bridge: %s",
                    self._config.endpoint_identity,
                )
            else:
                logger.warning(
                    "Bridge connected but did not report phone number — using endpoint_identity=%s",
                    self._config.endpoint_identity,
                )

        # Load checkpoint
        await self._load_checkpoint()

        # Load ingestion policy rules
        await self._ingestion_policy.ensure_loaded()
        await self._global_ingestion_policy.ensure_loaded()

        # Start heartbeat
        self._start_heartbeat()

        # Start flush scanner
        self._flush_scanner_task = asyncio.create_task(
            self._flush_scanner_loop(), name="wa-flush-scanner"
        )

        self._running = True
        logger.info(
            "Starting WhatsApp user-client connector",
            extra={
                "endpoint_identity": self._config.endpoint_identity,
                "last_event_id": self._last_event_id,
                "backfill_window_h": self._config.backfill_window_h,
            },
        )

        # Optional backfill (requests bridge to replay from configured window)
        if self._config.backfill_window_h:
            await self._request_backfill()

        # Main SSE event loop with reconnect
        try:
            await self._sse_event_loop()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Error in WhatsApp user-client connector SSE loop")
            raise
        finally:
            await self.stop()

    async def stop(self) -> None:
        """Stop the connector gracefully."""
        self._running = False

        # Cancel flush scanner
        if self._flush_scanner_task is not None and not self._flush_scanner_task.done():
            self._flush_scanner_task.cancel()
            try:
                await self._flush_scanner_task
            except asyncio.CancelledError:
                pass
            self._flush_scanner_task = None

        # Force-flush all non-empty buffers
        await self._flush_all_buffers(reason="shutdown")

        # Stop bridge
        if self._bridge_manager is not None:
            await self._bridge_manager.stop()
            self._bridge_manager = None

        # Stop heartbeat
        if self._switchboard_heartbeat is not None:
            await self._switchboard_heartbeat.stop()

        await self._mcp_client.aclose()
        logger.info("WhatsApp user-client connector stopped")

    # -------------------------------------------------------------------------
    # Internal: SSE event loop
    # -------------------------------------------------------------------------

    async def _sse_event_loop(self) -> None:
        """Consume bridge SSE events with reconnect-on-failure."""
        backoff_attempt = 0

        while self._running:
            # Check if bridge is degraded (pairing timeout or session invalidated)
            if self._bridge_manager is not None and self._bridge_manager.is_degraded:
                logger.error(
                    "Bridge entered degraded mode: %s — stopping SSE loop",
                    self._bridge_manager.degraded_reason,
                )
                break

            try:
                logger.info("Connecting to bridge SSE /events stream …")
                async for event in _sse_event_stream(self._config.bridge_socket):
                    if not self._running:
                        return
                    backoff_attempt = 0  # reset backoff on successful event
                    await self._handle_bridge_event(event)

                # Stream ended cleanly — reconnect
                logger.info("Bridge SSE stream closed cleanly, reconnecting …")

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "Bridge SSE stream error (attempt %d): %s — reconnecting with backoff",
                    backoff_attempt + 1,
                    exc,
                )

            if not self._running:
                break

            # Jittered backoff before reconnect
            import random

            base = min(_SSE_RECONNECT_DELAY_S * (2.0**backoff_attempt), 300.0)
            jitter = base * 0.25
            delay = base + random.uniform(-jitter, jitter)  # noqa: S311
            backoff_attempt += 1

            logger.info("Reconnecting SSE in %.1fs …", delay)
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                break

    # -------------------------------------------------------------------------
    # Internal: Bridge event handling
    # -------------------------------------------------------------------------

    async def _handle_bridge_event(self, event: dict[str, Any]) -> None:
        """Dispatch a single bridge SSE event.

        Routes to the appropriate chat buffer. Updates _last_event_id for
        checkpoint tracking.
        """
        event_type = event.get("event_type", "message")

        # We only care about message events for ingestion
        if event_type not in ("message", ""):
            logger.debug("Ignoring bridge event type: %r", event_type)
            return

        msg_id = event.get("message_id") or event.get("id")
        chat_jid = event.get("chat_jid") or event.get("chat_id")

        if not chat_jid:
            logger.warning("Bridge event missing chat_jid, skipping: %r", str(event)[:200])
            return

        if msg_id:
            self._last_event_id = str(msg_id)

        await self._buffer_event(event, chat_jid)

    async def _buffer_event(self, event: dict[str, Any], chat_jid: str) -> None:
        """Append a bridge event to the chat's buffer.

        Triggers a force-flush if the buffer reaches buffer_max_messages.
        """
        if chat_jid not in self._chat_buffers:
            buf = ChatBuffer(chat_jid=chat_jid)
            self._chat_buffers[chat_jid] = buf

        buf = self._chat_buffers[chat_jid]
        async with buf.lock:
            buf.messages.append(event)
            msg_count = len(buf.messages)

        logger.debug("Buffered message for chat %s (buffer size: %d)", chat_jid, msg_count)

        # Force-flush if buffer cap reached
        if msg_count >= self._config.buffer_max_messages:
            logger.info(
                "Chat %s buffer reached cap (%d messages), force-flushing",
                chat_jid,
                msg_count,
            )
            await self._flush_chat_buffer(chat_jid)

    # -------------------------------------------------------------------------
    # Internal: Flush scanner
    # -------------------------------------------------------------------------

    async def _flush_scanner_loop(self) -> None:
        """Background task: scan all chat buffers every 60 seconds."""
        logger.debug("WA flush scanner started (interval=%ds)", _FLUSH_SCANNER_INTERVAL_S)
        try:
            while True:
                await asyncio.sleep(_FLUSH_SCANNER_INTERVAL_S)
                await self._scan_and_flush()
        except asyncio.CancelledError:
            logger.debug("WA flush scanner cancelled")
            raise

    async def _scan_and_flush(self) -> None:
        """Iterate all chat buffers and flush those whose interval has elapsed."""
        now = time.monotonic()
        chat_jids = list(self._chat_buffers.keys())
        for jid in chat_jids:
            buf = self._chat_buffers.get(jid)
            if buf is None:
                continue
            if not buf.messages:
                continue
            elapsed = now - buf.last_flush_ts
            if elapsed >= self._config.flush_interval_s:
                logger.info(
                    "Flush interval elapsed for chat %s (elapsed=%.1fs), flushing",
                    jid,
                    elapsed,
                )
                await self._flush_chat_buffer(jid)

    async def _flush_all_buffers(self, reason: str = "force") -> None:
        """Force-flush all non-empty chat buffers."""
        chat_jids = list(self._chat_buffers.keys())
        if not chat_jids:
            return

        logger.info("Flushing all %d chat buffers (%s)", len(chat_jids), reason)
        results = await asyncio.gather(
            *(self._flush_chat_buffer(jid) for jid in chat_jids),
            return_exceptions=True,
        )
        for jid, result in zip(chat_jids, results):
            if isinstance(result, Exception):
                logger.exception(
                    "Error flushing chat buffer %s during %s: %s",
                    jid,
                    reason,
                    result,
                )

    async def _flush_chat_buffer(self, chat_jid: str) -> None:
        """Flush a single chat's buffer through the full batch pipeline.

        Pipeline:
        a. Atomically swap buffer (take messages, reset list).
        b. Build batch ingest.v1 envelope.
        c. Evaluate ingestion policy (connector + global scope).
        d. Evaluate discretion on concatenated normalized_text.
        e. Submit batch envelope via Switchboard MCP.
        f. Advance checkpoint to latest event ID.
        g. Record filtered events for policy/discretion rejections.
        """
        buf = self._chat_buffers.get(chat_jid)
        if buf is None:
            return

        async with buf.lock:
            if not buf.messages:
                return
            buffered_events = buf.messages
            buf.messages = []
            buf.last_flush_ts = time.monotonic()

        logger.info("Flushing %d messages for chat %s", len(buffered_events), chat_jid)

        # Build batch event ID
        event_ids = [e.get("message_id") or e.get("id") or "" for e in buffered_events]
        min_id = min((i for i in event_ids if i), default="0")
        max_id = max((i for i in event_ids if i), default="0")
        batch_event_id = f"batch:{chat_jid}:{min_id}-{max_id}"

        try:
            # b. Build batch envelope
            envelope = self._build_batch_envelope(chat_jid, buffered_events, batch_event_id)

            # c. Evaluate ingestion policy (connector scope)
            _ip_envelope = IngestionEnvelope(
                source_channel="whatsapp_user_client",
                raw_key=chat_jid,
            )
            _ip_decision = self._ingestion_policy.evaluate(_ip_envelope)
            if not _ip_decision.allowed:
                logger.debug(
                    "Ingestion policy blocked batch for chat %s: action=%s reason=%s",
                    chat_jid,
                    _ip_decision.action,
                    _ip_decision.reason,
                )
                self._record_batch_filtered_event(
                    chat_jid=chat_jid,
                    batch_event_id=batch_event_id,
                    filter_reason=FilteredEventBuffer.reason_policy_rule(
                        "connector_rule",
                        "block",
                        _ip_decision.matched_rule_type or "unknown",
                    ),
                )
                await self._flush_and_drain()
                return

            # c (continued). Global ingestion policy
            _gp_decision = self._global_ingestion_policy.evaluate(_ip_envelope)
            if _gp_decision.action == "skip":
                logger.debug(
                    "Global ingestion policy skipped batch for chat %s: reason=%s",
                    chat_jid,
                    _gp_decision.reason,
                )
                self._record_batch_filtered_event(
                    chat_jid=chat_jid,
                    batch_event_id=batch_event_id,
                    filter_reason=FilteredEventBuffer.reason_policy_rule(
                        "global_rule",
                        "skip",
                        _gp_decision.matched_rule_type or "unknown",
                    ),
                )
                await self._flush_and_drain()
                return

            # d. Evaluate discretion on normalized_text
            normalized_text: str = envelope["payload"]["normalized_text"]
            if self._discretion_dispatcher is not None and normalized_text:
                if chat_jid not in self._discretion_evaluators:
                    self._discretion_evaluators[chat_jid] = DiscretionEvaluator(
                        source_name=f"wa:{chat_jid}",
                        dispatcher=self._discretion_dispatcher,
                    )

                # Resolve sender weight from last event in batch
                sender_jid = buffered_events[-1].get("sender_jid", "") if buffered_events else ""
                sender_weight = 1.0
                if self._weight_resolver and sender_jid:
                    sender_weight = await self._weight_resolver.resolve("whatsapp_jid", sender_jid)

                d_result = await self._discretion_evaluators[chat_jid].evaluate(
                    normalized_text, weight=sender_weight
                )
                if d_result.verdict == "IGNORE":
                    logger.debug("Discretion IGNORE for batch in chat %s", chat_jid)
                    self._record_batch_filtered_event(
                        chat_jid=chat_jid,
                        batch_event_id=batch_event_id,
                        filter_reason="discretion:IGNORE",
                        subject_or_preview=normalized_text[:200] if normalized_text else None,
                    )
                    await self._flush_and_drain()
                    return

            # e. Submit to Switchboard
            await self._submit_to_ingest(envelope)

            # Flush filtered event buffer after successful submission
            await self._flush_and_drain()

            # f. Advance checkpoint to latest event ID
            latest_id = max(
                (e.get("message_id") or e.get("id") or "" for e in buffered_events),
                default="",
            )
            if latest_id:
                self._last_event_id = latest_id
                await self._save_checkpoint()

        except Exception as exc:
            logger.exception(
                "Failed to flush chat buffer for chat %s",
                chat_jid,
                extra={"endpoint_identity": self._config.endpoint_identity},
            )
            self._record_batch_filtered_event(
                chat_jid=chat_jid,
                batch_event_id=batch_event_id,
                filter_reason=FilteredEventBuffer.reason_submission_error(),
                status="error",
                error_detail=str(exc),
            )
            await self._flush_and_drain()

    def _record_batch_filtered_event(
        self,
        chat_jid: str,
        batch_event_id: str,
        filter_reason: str,
        sender_identity: str = "multiple",
        subject_or_preview: str | None = None,
        status: str = "filtered",
        error_detail: str | None = None,
    ) -> None:
        """Record a filtered or errored batch event."""
        self._filtered_event_buffer.record(
            external_message_id=batch_event_id,
            source_channel=self._config.channel,
            sender_identity=sender_identity,
            subject_or_preview=subject_or_preview,
            filter_reason=filter_reason,
            full_payload=FilteredEventBuffer.full_payload(
                channel=self._config.channel,
                provider=self._config.provider,
                endpoint_identity=self._config.endpoint_identity,
                external_event_id=batch_event_id,
                external_thread_id=chat_jid,
                observed_at=datetime.now(UTC).isoformat(),
                sender_identity=sender_identity,
                raw={},
            ),
            status=status,
            error_detail=error_detail,
        )

    # -------------------------------------------------------------------------
    # Internal: Envelope building
    # -------------------------------------------------------------------------

    def _build_batch_envelope(
        self,
        chat_jid: str,
        buffered_events: list[dict[str, Any]],
        batch_event_id: str,
    ) -> dict[str, Any]:
        """Build an ingest.v1 batch envelope for a flushed chat buffer.

        Normalizes each event in the batch and concatenates into a framed
        normalized_text with header identifying the chat and time window.
        """
        if not buffered_events:
            normalized_text = ""
            flush_ts = datetime.now(UTC).isoformat()
            return {
                "schema_version": "ingest.v1",
                "source": {
                    "channel": self._config.channel,
                    "provider": self._config.provider,
                    "endpoint_identity": self._config.endpoint_identity,
                },
                "event": {
                    "external_event_id": batch_event_id,
                    "external_thread_id": chat_jid,
                    "observed_at": flush_ts,
                },
                "sender": {"identity": "multiple"},
                "payload": {"raw": {}, "normalized_text": normalized_text},
                "control": {
                    "idempotency_key": f"wa_batch:{chat_jid}:{batch_event_id}",
                    "policy_tier": "default",
                },
            }

        # Collect timestamps for time window
        timestamps = []
        for e in buffered_events:
            ts = e.get("timestamp") or e.get("observed_at")
            if ts:
                timestamps.append(str(ts))

        oldest_ts = timestamps[0] if timestamps else None
        newest_ts = timestamps[-1] if timestamps else None

        # Build header
        header_lines: list[str] = [f"=== Chat JID: {chat_jid} ==="]
        if oldest_ts and newest_ts and oldest_ts != newest_ts:
            header_lines.append(f"Window: {oldest_ts} → {newest_ts}")
        elif oldest_ts:
            header_lines.append(f"Timestamp: {oldest_ts}")
        header_lines.append("---")

        # Build message lines
        text_parts: list[str] = []
        for event in buffered_events:
            sender_jid = event.get("sender_jid", "unknown")
            msg_text = normalize_message_text(event)
            text_parts.append(f"[{sender_jid}]: {msg_text}")

        footer_lines = ["---", f"Messages: {len(buffered_events)} new"]

        normalized_text = "\n".join(header_lines + text_parts + footer_lines)

        flush_ts = datetime.now(UTC).isoformat()

        # Build raw payload from all events
        raw_payload = {
            "events": buffered_events,
            "chat_jid": chat_jid,
            "batch_size": len(buffered_events),
        }

        return {
            "schema_version": "ingest.v1",
            "source": {
                "channel": self._config.channel,
                "provider": self._config.provider,
                "endpoint_identity": self._config.endpoint_identity,
            },
            "event": {
                "external_event_id": batch_event_id,
                "external_thread_id": chat_jid,
                "observed_at": flush_ts,
            },
            "sender": {"identity": "multiple"},
            "payload": {
                "raw": raw_payload,
                "normalized_text": normalized_text,
            },
            "control": {
                "idempotency_key": f"wa_batch:{chat_jid}:{batch_event_id}",
                "policy_tier": "default",
            },
        }

    def _normalize_single_event_to_ingest_v1(self, event: dict[str, Any]) -> dict[str, Any]:
        """Normalize a single WhatsApp bridge event to ingest.v1 format.

        Used for direct (non-buffered) processing or individual message submission.
        Per spec field mapping:
        - source.channel = "whatsapp_user_client"
        - source.provider = "whatsapp"
        - source.endpoint_identity = "whatsapp:<e164_phone>"
        - event.external_event_id = message ID
        - event.external_thread_id = chat JID
        - event.observed_at = message timestamp (RFC3339)
        - sender.identity = sender's WhatsApp JID
        - payload.raw = full bridge event JSON
        - payload.normalized_text = extracted/annotated text
        - control.idempotency_key = "whatsapp:<endpoint_identity>:<message_id>"
        - control.policy_tier = "default"
        """
        msg_id = str(event.get("message_id") or event.get("id") or "unknown")
        chat_jid = event.get("chat_jid") or event.get("chat_id") or ""
        sender_jid = event.get("sender_jid") or event.get("from_jid") or "unknown"

        # Timestamp
        ts = event.get("timestamp") or event.get("observed_at")
        if ts:
            if isinstance(ts, (int, float)):
                observed_at = datetime.fromtimestamp(ts, UTC).isoformat()
            else:
                observed_at = str(ts)
        else:
            observed_at = datetime.now(UTC).isoformat()

        normalized_text = normalize_message_text(event)
        idempotency_key = f"whatsapp:{self._config.endpoint_identity}:{msg_id}"

        return {
            "schema_version": "ingest.v1",
            "source": {
                "channel": self._config.channel,
                "provider": self._config.provider,
                "endpoint_identity": self._config.endpoint_identity,
            },
            "event": {
                "external_event_id": msg_id,
                "external_thread_id": chat_jid if chat_jid else None,
                "observed_at": observed_at,
            },
            "sender": {"identity": sender_jid},
            "payload": {
                "raw": event,
                "normalized_text": normalized_text,
            },
            "control": {
                "idempotency_key": idempotency_key,
                "policy_tier": "default",
            },
        }

    # -------------------------------------------------------------------------
    # Internal: Submission
    # -------------------------------------------------------------------------

    async def _submit_to_ingest(self, envelope: dict[str, Any]) -> None:
        """Submit ingest.v1 envelope to Switchboard via MCP ingest tool."""
        try:
            result = await self._mcp_client.call_tool("ingest", envelope)

            if isinstance(result, dict) and result.get("status") == "error":
                error_msg = result.get("error", "Unknown ingest error")
                raise RuntimeError(f"Ingest tool error: {error_msg}")

            logger.info(
                "Submitted to Switchboard ingest",
                extra={
                    "request_id": result.get("request_id") if isinstance(result, dict) else None,
                    "duplicate": (
                        result.get("duplicate", False) if isinstance(result, dict) else False
                    ),
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
            raise

    # -------------------------------------------------------------------------
    # Internal: Flush and drain
    # -------------------------------------------------------------------------

    async def _flush_and_drain(self) -> None:
        """Flush filtered event buffer then drain replay-pending rows."""
        if self._db_pool is None:
            return
        await self._filtered_event_buffer.flush(self._db_pool)
        await drain_replay_pending(
            self._db_pool,
            self._config.provider,
            self._config.endpoint_identity,
            self._submit_to_ingest,
            logger,
        )

    # -------------------------------------------------------------------------
    # Internal: Heartbeat
    # -------------------------------------------------------------------------

    def _start_heartbeat(self) -> None:
        """Initialize and start heartbeat background task."""
        heartbeat_config = HeartbeatConfig.from_env(
            connector_type="whatsapp_user_client",
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
        if not self._running:
            return ("error", "Connector not running")
        if self._bridge_manager is not None and self._bridge_manager.is_degraded:
            return ("degraded", self._bridge_manager.degraded_reason)
        return ("healthy", None)

    def _get_checkpoint(self) -> tuple[str | None, datetime | None]:
        """Get current checkpoint state for heartbeat."""
        if self._last_event_id is None:
            return (None, None)
        cursor = json.dumps({"last_event_id": self._last_event_id})
        updated_at = (
            datetime.fromtimestamp(self._last_checkpoint_save, UTC)
            if self._last_checkpoint_save is not None
            else None
        )
        return (cursor, updated_at)

    # -------------------------------------------------------------------------
    # Internal: Checkpoint persistence
    # -------------------------------------------------------------------------

    async def _load_checkpoint(self) -> None:
        """Load checkpoint from DB."""
        from butlers.connectors.cursor_store import load_cursor

        try:
            raw = await load_cursor(
                self._cursor_pool,
                "whatsapp_user_client",
                self._config.endpoint_identity,
            )
            if raw is not None:
                data = json.loads(raw)
                self._last_event_id = data.get("last_event_id")
                logger.info(
                    "Loaded checkpoint from DB",
                    extra={"last_event_id": self._last_event_id},
                )
            else:
                logger.info("No checkpoint in DB, starting from scratch")
        except Exception:
            logger.exception("Failed to load checkpoint from DB, starting from scratch")

    async def _save_checkpoint(self) -> None:
        """Persist checkpoint to DB."""
        try:
            from butlers.connectors.cursor_store import save_cursor

            payload: dict[str, Any] = {"last_event_id": self._last_event_id}
            await save_cursor(
                self._cursor_pool,
                "whatsapp_user_client",
                self._config.endpoint_identity,
                json.dumps(payload),
            )
            self._last_checkpoint_save = time.time()
            logger.debug(
                "Saved checkpoint to DB",
                extra={"last_event_id": self._last_event_id},
            )
        except Exception:
            logger.exception("Failed to save checkpoint to DB")

    # -------------------------------------------------------------------------
    # Internal: Backfill
    # -------------------------------------------------------------------------

    async def _request_backfill(self) -> None:
        """Request the bridge to replay messages from the backfill window.

        Sends a POST /backfill?hours=N to the bridge, which replays historical
        events through the SSE stream. Duplicates are caught by Switchboard dedup.
        """
        if not self._config.backfill_window_h:
            return

        logger.info(
            "Requesting backfill from bridge",
            extra={
                "window_hours": self._config.backfill_window_h,
                "endpoint_identity": self._config.endpoint_identity,
            },
        )

        try:
            reader, writer = await asyncio.open_unix_connection(self._config.bridge_socket)
            try:
                request = (
                    f"POST /backfill?hours={self._config.backfill_window_h} HTTP/1.0\r\n"
                    "Host: localhost\r\n"
                    "Content-Length: 0\r\n"
                    "Connection: close\r\n"
                    "\r\n"
                )
                writer.write(request.encode())
                await writer.drain()
                # Read and discard response
                await asyncio.wait_for(reader.read(4096), timeout=10.0)
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
            logger.info("Backfill request sent to bridge")
        except Exception as exc:
            # Non-fatal: bridge may not support backfill endpoint
            logger.warning("Failed to request backfill from bridge: %s", exc)


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------


async def _run_health_server(
    port: int,
    connector: WhatsAppUserClientConnector,
) -> None:
    """Run a minimal HTTP health server on the given port.

    Exposes:
      GET /health  → JSON health status
      GET /metrics → Prometheus text metrics
    """
    from prometheus_client import generate_latest

    async def handle_request(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            request_line = await asyncio.wait_for(reader.readline(), timeout=5.0)
            path = b"/health"
            if request_line:
                parts = request_line.split()
                if len(parts) >= 2:
                    path = parts[1]

            # Drain remaining headers
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=5.0)
                if not line or line == b"\r\n":
                    break

            if path == b"/health" or path.startswith(b"/health?"):
                state, error_msg = connector._get_health_state()
                body_dict = {
                    "status": state,
                    "connector_type": "whatsapp_user_client",
                    "endpoint_identity": connector._config.endpoint_identity,
                }
                if error_msg:
                    body_dict["error"] = error_msg
                body = json.dumps(body_dict).encode()
                content_type = "application/json"
                http_status = "200 OK"
            elif path == b"/metrics" or path.startswith(b"/metrics?"):
                body = generate_latest()
                content_type = "text/plain; version=0.0.4"
                http_status = "200 OK"
            else:
                body = json.dumps({"error": "Not Found"}).encode()
                content_type = "application/json"
                http_status = "404 Not Found"

            response = (
                f"HTTP/1.0 {http_status}\r\n"
                f"Content-Type: {content_type}\r\n"
                f"Content-Length: {len(body)}\r\n"
                f"Connection: close\r\n"
                f"\r\n"
            ).encode() + body

            writer.write(response)
            await writer.drain()
        except Exception:
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    from butlers.connectors.health_socket import make_health_socket

    sock = make_health_socket("127.0.0.1", port)
    server = await asyncio.start_server(handle_request, sock=sock)
    logger.info("Health server listening on 127.0.0.1:%d", port)

    async with server:
        await server.serve_forever()


# ---------------------------------------------------------------------------
# Bridge DSN helper
# ---------------------------------------------------------------------------


def _get_bridge_db_dsn() -> str:
    """Build the PostgreSQL DSN for the Go bridge from environment variables."""
    params = db_params_from_env()
    host = params.get("host") or "localhost"
    port = params.get("port") or 5432
    user = params.get("user") or "butlers"
    password = params.get("password") or "butlers"
    db_name = os.environ.get("CONNECTOR_BUTLER_DB_NAME", "butlers").strip() or "butlers"
    return f"postgresql://{user}:{password}@{host}:{port}/{db_name}"


# ---------------------------------------------------------------------------
# Credential resolution
# ---------------------------------------------------------------------------


async def _resolve_whatsapp_phone_from_db() -> str | None:
    """Resolve the owner's WhatsApp phone number from owner entity_info.

    Returns the phone number string (E.164 format) or None if not found.
    """
    import asyncpg

    db_params = db_params_from_env()
    local_db_name = os.environ.get("CONNECTOR_BUTLER_DB_NAME", "").strip()
    shared_db_name = shared_db_name_from_env()
    candidate_db_names: list[str] = []
    for name in [local_db_name, shared_db_name]:
        if name and name not in candidate_db_names:
            candidate_db_names.append(name)
    if not candidate_db_names:
        candidate_db_names = ["butlers"]

    for db_name in candidate_db_names:
        try:
            pool = await asyncpg.create_pool(
                host=db_params["host"],
                port=db_params["port"],
                user=db_params["user"],
                password=db_params["password"],
                database=db_name,
                ssl=db_params.get("ssl"),  # type: ignore[arg-type]
                min_size=1,
                max_size=2,
                command_timeout=5,
            )
            try:
                phone = await resolve_owner_entity_info(pool, "whatsapp_phone")
                if phone:
                    logger.info(
                        "WhatsApp user-client: resolved whatsapp_phone from owner entity_info "
                        "(db=%s)",
                        db_name,
                    )
                    return phone
            finally:
                await pool.close()
        except Exception as exc:
            logger.warning(
                "DB connection failed during WhatsApp credential resolution (db=%s): %s",
                db_name,
                exc,
            )

    logger.warning("WhatsApp user-client: could not resolve whatsapp_phone from owner entity_info")
    return None


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


async def run_whatsapp_user_client_connector() -> None:
    """CLI entry point for running the WhatsApp user-client connector.

    Phone resolution order:
    1. Owner entity_info in the DB (``whatsapp_phone`` key)
    2. Bridge /status ``phone`` field after QR pairing completes

    Non-credential configuration is read from environment variables.
    Health server and connector run concurrently.
    """
    configure_logging(level="INFO", butler_name="whatsapp-user-client")

    # Step 1: Load non-credential config from env
    config = WhatsAppUserClientConnectorConfig.from_env()

    # Step 2: Try to resolve whatsapp_phone from owner entity_info
    phone = await _resolve_whatsapp_phone_from_db()
    if phone:
        endpoint_identity = f"whatsapp:{phone}"
        config = replace(config, endpoint_identity=endpoint_identity)
        logger.info(
            "WhatsApp user-client connector: endpoint_identity=%s (from DB)",
            endpoint_identity,
        )
    else:
        # Use a placeholder — will be resolved from bridge after pairing.
        config = replace(config, endpoint_identity="whatsapp:pending")
        logger.info(
            "WhatsApp user-client connector: whatsapp_phone not in DB, "
            "will resolve from bridge after pairing"
        )

    # Step 3: Create DB pools for cursor and filtered events
    from butlers.connectors.cursor_store import create_cursor_pool_from_env

    cursor_pool = await create_cursor_pool_from_env()
    logger.info("WhatsApp user-client connector: cursor pool created")

    connector = WhatsAppUserClientConnector(config, db_pool=cursor_pool, cursor_pool=cursor_pool)

    # Step 4: Run health server and connector concurrently
    health_task = asyncio.create_task(
        _run_health_server(config.health_port, connector),
        name="wa-health-server",
    )

    try:
        await connector.start()
    except KeyboardInterrupt:
        logger.info("Received interrupt, stopping connector")
    finally:
        health_task.cancel()
        try:
            await health_task
        except (asyncio.CancelledError, Exception):
            pass
        await connector.stop()
        if cursor_pool is not None:
            await cursor_pool.close()


if __name__ == "__main__":
    asyncio.run(run_whatsapp_user_client_connector())
