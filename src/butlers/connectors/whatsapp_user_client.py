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
- WA_FLUSH_INTERVAL_S (optional, default 1800)
- WA_BUFFER_MAX_MESSAGES (optional, default 50)
- WA_HISTORY_TIME_WINDOW_M (optional, default 35)
- WHATSAPP_STALE_RESTART_THRESHOLD_S (optional, default 3600; recoverable-outage
  restart watchdog; 0 disables)
- WHATSAPP_INVALIDATED_SESSION_THRESHOLD_S (optional, default 300; seconds a
  disconnected/link-dead bridge may persist before being classified as an
  invalidated session requiring re-pair — see bu-5ocmh)

Security requirements:
- Never commit credentials or session artifacts to version control
- whatsapp_phone resolved from owner entity_info (DB) or bridge /status after pairing
- Whatsmeow owns protocol session keys in its public tables; the Go bridge records
  pair-history bookkeeping in messenger.whatsapp_sessions
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

from butlers.connectors.bridge_manager import (
    DEFAULT_INVALIDATED_SESSION_THRESHOLD_S,
    DEFAULT_STANDALONE_BRIDGE_SOCKET,
    BridgeConfig,
    BridgeSubprocessManager,
)
from butlers.connectors.db_role import connector_setup_role
from butlers.connectors.discretion import (
    ContactWeightResolver,
    DiscretionEvaluator,
    classify_ignore_kind,
    record_discretion_ignore,
)
from butlers.connectors.discretion_dispatcher import DiscretionDispatcher
from butlers.connectors.filtered_event_buffer import FilteredEventBuffer, drain_replay_pending
from butlers.connectors.heartbeat import ConnectorHeartbeat, HeartbeatConfig
from butlers.connectors.mcp_client import CachedMCPClient
from butlers.connectors.metrics import ConnectorMetrics
from butlers.connectors.owner_outbound_events import record_owner_outbound_point
from butlers.core.logging import configure_logging
from butlers.credential_store import (
    resolve_owner_entity_info,
    shared_db_name_from_env,
)
from butlers.db import db_params_from_env
from butlers.ingestion_policy import IngestionEnvelope, IngestionPolicyEvaluator

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Addressed-mention detection for passive connectors
# ---------------------------------------------------------------------------

_DEFAULT_ADDRESS_KEYWORDS: frozenset[str] = frozenset(
    {"@butler", "@butlers", "hey butler", "hey butlers", "ok butler", "ok butlers"}
)


def _detect_addressed(text: str, keywords: frozenset[str]) -> bool:
    """Return True if *text* starts with an address keyword (case-insensitive)."""
    if not text:
        return False
    lowered = text.lower().strip()
    return any(lowered.startswith(kw) for kw in keywords)


def _detect_addressed_in_events(
    events: list[dict[str, Any]],
    keywords: frozenset[str],
) -> bool:
    """Return True if ANY event in a batch contains an address keyword."""
    for evt in events:
        text = evt.get("text") or evt.get("body") or ""
        if _detect_addressed(text, keywords):
            return True
    return False


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_FLUSH_SCANNER_INTERVAL_S = 60  # How often the flush scanner wakes up
_LINK_WATCHDOG_INTERVAL_S = 60  # How often the stale-link watchdog checks the bridge
_BRIDGE_STARTUP_TIMEOUT_S = 60.0  # Bridge startup timeout (longer for QR re-pair)
_SSE_RECONNECT_DELAY_S = 5.0  # Delay before reconnecting SSE after failure
_SSE_KEEPALIVE_TIMEOUT_S = 90.0  # Max silence from SSE stream before treating as stale
_SSE_PAIRING_WAIT_TIMEOUT_S = 30.0  # Maximum wait for the bridge to reconnect after pairing
_CONNECTOR_TYPE = "whatsapp_user_client"

# Discretion fail-open threshold for WhatsApp (bu-cicgb). WhatsApp is a primary
# personal 1:1 messaging channel; a discretion *infra* failure (most observed:
# same-tier model-failover exhaustion) must not silently drop the owner's
# messages. The shared default (0.5) fail-CLOSES every sender below it, and
# WhatsApp senders resolve to the ``unknown`` tier (0.3) unless a
# ``has-handle=<full JID>`` triple exists — so under the default, an LLM outage
# drops 100% of WhatsApp traffic (the bu-cicgb audit: 9/9 recent drops were
# ``failover_exhausted``, 0 genuine ``llm_verdict``). Setting the threshold to
# the ``unknown`` floor makes every WhatsApp sender fail-OPEN (FORWARD) when the
# LLM cannot render a verdict, while a genuine LLM IGNORE still drops (fail-open
# only affects the error path). Reversible: restore the shared 0.5 default.
_WHATSAPP_DISCRETION_WEIGHT_FAIL_OPEN = 0.3

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
    bridge_socket: str = DEFAULT_STANDALONE_BRIDGE_SOCKET

    # Backfill
    backfill_window_h: int | None = None

    # Concurrency
    max_inflight: int = 8

    # Buffering / flush config
    flush_interval_s: int = 1800
    history_time_window_m: int = 35
    buffer_max_messages: int = 50

    # Health port
    health_port: int = 40082

    # Stale-link watchdog: if the bridge link stays down (e.g. session taken over
    # by another device via StreamReplaced — whatsmeow does not auto-reconnect)
    # for longer than this, the connector exits so Docker restarts it, which
    # re-claims the WhatsApp session on a fresh Connect(). 0 disables the watchdog.
    # The threshold is deliberately long (~1h) to avoid a reconnect war with a
    # genuinely-competing session.
    stale_restart_threshold_s: int = 3600

    # Invalidated-session detection (bu-5ocmh): seconds a recoverable degraded
    # link (disconnected/connecting, or connected-but-link-dead) may persist
    # before being escalated to a terminal "invalidated session" — the class
    # of failure where WhatsApp remotely invalidated the linked device and
    # whatsmeow's own auto-reconnect retries forever without ever reporting
    # pair_required or exiting. See bridge_manager.BridgeConfig for the
    # escalation itself; this just threads the threshold through from env.
    invalidated_session_threshold_s: float = DEFAULT_INVALIDATED_SESSION_THRESHOLD_S

    # Address-mention keywords for passive→interactive promotion
    address_keywords: frozenset[str] = field(default_factory=lambda: _DEFAULT_ADDRESS_KEYWORDS)

    # Dunbar group-aware interaction gating (RFC 0013)
    max_interaction_group_size: int = 20
    """Chats with more participants than this threshold have interaction_eligible=False."""

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

        bridge_socket = os.environ.get("WA_BRIDGE_SOCKET", DEFAULT_STANDALONE_BRIDGE_SOCKET)

        backfill_window_str = os.environ.get("CONNECTOR_BACKFILL_WINDOW_H")
        backfill_window_h = int(backfill_window_str) if backfill_window_str else None

        max_inflight = int(os.environ.get("CONNECTOR_MAX_INFLIGHT", "8"))
        flush_interval_s = int(os.environ.get("WA_FLUSH_INTERVAL_S", "1800"))
        history_time_window_m = int(os.environ.get("WA_HISTORY_TIME_WINDOW_M", "35"))
        buffer_max_messages = int(os.environ.get("WA_BUFFER_MAX_MESSAGES", "50"))
        health_port = int(os.environ.get("CONNECTOR_HEALTH_PORT", "40082"))
        stale_restart_threshold_s = int(
            os.environ.get("WHATSAPP_STALE_RESTART_THRESHOLD_S", "3600")
        )
        invalidated_session_threshold_s = float(
            os.environ.get(
                "WHATSAPP_INVALIDATED_SESSION_THRESHOLD_S",
                str(DEFAULT_INVALIDATED_SESSION_THRESHOLD_S),
            )
        )

        # Address keywords (comma-separated, case-insensitive)
        _raw_keywords = os.environ.get("CONNECTOR_ADDRESS_KEYWORDS", "").strip()
        address_keywords = (
            frozenset(k.strip().lower() for k in _raw_keywords.split(",") if k.strip())
            if _raw_keywords
            else _DEFAULT_ADDRESS_KEYWORDS
        )

        _raw_max_group = os.environ.get("WA_MAX_INTERACTION_GROUP_SIZE", "").strip()
        max_interaction_group_size = int(_raw_max_group) if _raw_max_group else 20

        return cls(
            switchboard_mcp_url=switchboard_mcp_url,
            provider=provider,
            channel=channel,
            bridge_socket=bridge_socket,
            backfill_window_h=backfill_window_h,
            max_inflight=max_inflight,
            flush_interval_s=flush_interval_s,
            history_time_window_m=history_time_window_m,
            buffer_max_messages=buffer_max_messages,
            health_port=health_port,
            stale_restart_threshold_s=stale_restart_threshold_s,
            invalidated_session_threshold_s=invalidated_session_threshold_s,
            address_keywords=address_keywords,
            max_interaction_group_size=max_interaction_group_size,
        )


# ---------------------------------------------------------------------------
# Message type normalization
# ---------------------------------------------------------------------------


def normalize_message_text(event: dict[str, Any]) -> str:
    """Normalize a bridge event's message content to plain text.

    Applies the ingest.v1 field mapping spec for message type normalization:
    - Conversation / ExtendedTextMessage → text verbatim
    - ImageMessage → caption if present, else [image]
    - video → caption if present, else [video]
    - audio / voice_note → [voice message] or [audio]
    - document → filename and caption
    - sticker → [sticker]
    - location → [location: lat, lon, name]
    - contact → [contact: display_name]
    - reaction → [reaction: emoji to message_id]
    - poll → [poll: question — option1, option2, ...]
    - message_deleted → [message deleted]
    - group_invite → [group invite: group_name]

    Bridge type names are lowercase (e.g. "text", "image") as emitted by
    the Go whatsapp-bridge mapper (internal/events/mapper.go).
    """
    msg_type = event.get("type", "")
    content = event.get("content", {}) or {}

    if msg_type == "text":
        return content.get("text") or event.get("text") or "[empty message]"

    if msg_type == "image":
        caption = content.get("caption", "")
        return caption if caption else "[image]"

    if msg_type == "video":
        caption = content.get("caption", "")
        return caption if caption else "[video]"

    if msg_type == "audio":
        return "[audio]"

    if msg_type == "voice_note":
        return "[voice message]"

    if msg_type == "document":
        filename = content.get("filename", "")
        caption = content.get("caption", "")
        parts = [p for p in [filename, caption] if p]
        return " — ".join(parts) if parts else "[document]"

    if msg_type == "sticker":
        return "[sticker]"

    if msg_type == "location":
        lat = content.get("latitude", "")
        lon = content.get("longitude", "")
        name = content.get("name", "")
        if name:
            return f"[location: {lat}, {lon}, {name}]"
        return f"[location: {lat}, {lon}]"

    if msg_type == "contact":
        display_name = content.get("display_name", "")
        return f"[contact: {display_name}]" if display_name else "[contact]"

    if msg_type == "reaction":
        emoji = content.get("emoji", "")
        target_id = content.get("target_message_id", "")
        if emoji and target_id:
            return f"[reaction: {emoji} to {target_id}]"
        return f"[reaction: {emoji}]" if emoji else "[reaction]"

    if msg_type == "poll":
        question = content.get("question", "")
        options = content.get("options", []) or []
        option_texts = [str(o) for o in options if o]
        opts_str = ", ".join(option_texts)
        return f"[poll: {question} — {opts_str}]" if question else "[poll]"

    if msg_type == "message_deleted":
        deleted_id = content.get("deleted_message_id", "")
        return f"[message deleted: {deleted_id}]" if deleted_id else "[message deleted]"

    if msg_type == "group_invite":
        group_name = content.get("group_name", "")
        return f"[group invite: {group_name}]" if group_name else "[group invite]"

    # Fallback: try to extract any text field
    text = event.get("text", "") or content.get("text", "") or content.get("caption", "")
    if text:
        return str(text)

    return f"[{msg_type}]" if msg_type else "[unknown]"


def _derive_wa_chat_type(chat_jid: str) -> str:
    """Derive a canonical chat_type from a WhatsApp JID string.

    WhatsApp JID suffixes:
    - ``@s.whatsapp.net`` → private (DM)
    - ``@g.us`` → group
    - ``@broadcast`` → channel/broadcast
    - ``@newsletter`` → channel/newsletter

    Returns one of: 'private', 'group', 'channel'.
    Falls back to 'private' for unknown suffixes.
    """
    if not chat_jid:
        return "private"
    jid_lower = chat_jid.lower()
    if jid_lower.endswith("@g.us"):
        return "group"
    if jid_lower.endswith("@broadcast") or jid_lower.endswith("@newsletter"):
        return "channel"
    if jid_lower.endswith("@lid"):
        # LID-based JIDs (new privacy-preserving identifiers) — treat as private
        return "private"
    return "private"


def _extract_wa_participant_count(event: dict[str, Any]) -> int | None:
    """Extract participant count from a WhatsApp bridge event, if present.

    The Go bridge currently does not emit participant_count in standard events.
    This function reads it from optional metadata fields that may be added in
    future bridge versions, or from explicit group_metadata payloads.

    Returns None when not available (caller should fall back to JID-based heuristic).
    """
    # Check top-level participant_count field (future bridge support)
    raw_count = event.get("participant_count")
    if raw_count is not None:
        try:
            return int(raw_count)
        except (TypeError, ValueError):
            pass

    # Check content.participant_count for group events
    content = event.get("content") or {}
    if isinstance(content, dict):
        raw_count = content.get("participant_count")
        if raw_count is not None:
            try:
                return int(raw_count)
            except (TypeError, ValueError):
                pass

    return None


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

        # Invalidated-session alerting/recovery bookkeeping (bu-5ocmh)
        self._invalidated_session_alert_sent = False
        self._last_pair_reset_handled_at: str | None = None

        # Metrics
        self._metrics = ConnectorMetrics(
            connector_type=_CONNECTOR_TYPE,
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
            connector_type=_CONNECTOR_TYPE,
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

        # Background stale-link watchdog task
        self._link_watchdog_task: asyncio.Task[None] | None = None

    def _build_bridge_config(self, *, startup_allow_degraded: bool = False) -> BridgeConfig:
        """Build the Go bridge subprocess config.

        ``startup_allow_degraded`` is False for the connector's ordinary
        boot (unchanged behavior: wait for a real "connected" state) but is
        set True by the pair-reset recovery path (bu-5ocmh), where the
        restarted bridge is *expected* to come up in ``pair_required`` (the
        device store was just cleared) — without this, BridgeSubprocessManager.
        start() would treat that as a startup failure and raise TimeoutError.
        """
        return BridgeConfig(
            binary="whatsapp-bridge",
            args=["--listen", f"unix://{self._config.bridge_socket}"],
            env={"WA_BRIDGE_DSN": _get_bridge_db_dsn()},
            bridge_socket=self._config.bridge_socket,
            startup_timeout_s=_BRIDGE_STARTUP_TIMEOUT_S,
            startup_allow_degraded=startup_allow_degraded,
            invalidated_session_threshold_s=self._config.invalidated_session_threshold_s,
        )

    async def _maybe_resolve_pending_endpoint_identity(self) -> None:
        """Resolve ``endpoint_identity`` from the bridge once it is known.

        ``endpoint_identity`` is only ever the ``"whatsapp:pending"``
        placeholder for a brand-new deployment with no ``whatsapp_phone`` yet
        in owner entity_info (see ``run_whatsapp_user_client_connector``) — a
        real phone is learned exclusively from the bridge's own ``/status``
        after QR pairing completes.

        Since bu-7sh43, ``BridgeSubprocessManager.start()`` returns as soon as
        the bridge reports ``pair_required`` — i.e. *before* the user has
        scanned the QR — so a single resolution attempt right after
        ``start()`` returns can no longer be relied on to see a phone number:
        it fires while the bridge is still awaiting pairing. This is a no-op
        once resolved (or once a real phone is configured), so it is safe to
        call repeatedly; ``_sse_event_loop`` calls it on every pass so
        resolution self-heals as soon as the bridge actually connects,
        without blocking startup on the pairing scan.
        """
        if self._config.endpoint_identity != "whatsapp:pending" or self._bridge_manager is None:
            return
        status = await self._bridge_manager.get_status()
        bridge_phone = status.get("phone")
        if bridge_phone:
            # Normalize to E.164: bridge returns bare digits, entity_info stores with '+'
            if not bridge_phone.startswith("+"):
                bridge_phone = f"+{bridge_phone}"
            self._config = replace(self._config, endpoint_identity=f"whatsapp:{bridge_phone}")
            logger.info(
                "Resolved endpoint_identity from bridge: %s",
                self._config.endpoint_identity,
            )
        elif status.get("state") == "connected":
            logger.warning(
                "Bridge connected but did not report phone number — using endpoint_identity=%s",
                self._config.endpoint_identity,
            )

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
        self._bridge_manager = BridgeSubprocessManager(self._build_bridge_config())
        await self._bridge_manager.start()

        # Resolve phone from bridge if endpoint_identity is still pending. On a
        # brand-new, never-configured deployment this first attempt usually
        # cannot succeed yet: since bu-7sh43, start() returns as soon as the
        # bridge reports pair_required (before the user has scanned the QR),
        # so the bridge has no phone to report. _sse_event_loop retries this
        # once the bridge actually reaches "connected" (see
        # _maybe_resolve_pending_endpoint_identity).
        await self._maybe_resolve_pending_endpoint_identity()

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

        # Start stale-link watchdog. Always runs (even when the stale-restart
        # threshold is disabled via stale_restart_threshold_s=0) because it
        # also drives invalidated-session alerting/recovery (bu-5ocmh), which
        # is an independent concern from the restart-on-recoverable-outage
        # behavior gated by that threshold — see _link_is_stale().
        self._link_watchdog_task = asyncio.create_task(
            self._link_watchdog_loop(), name="wa-link-watchdog"
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

        # Cancel stale-link watchdog
        if self._link_watchdog_task is not None and not self._link_watchdog_task.done():
            self._link_watchdog_task.cancel()
            try:
                await self._link_watchdog_task
            except asyncio.CancelledError:
                pass
            self._link_watchdog_task = None

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
                if self._bridge_manager.is_awaiting_pairing:
                    # A bridge sitting in pair_required is legitimately
                    # waiting for a human to scan the QR — not a failure.
                    # Wait for the manager's connection signal instead of
                    # polling on a fixed delay. The timeout preserves the
                    # previous recheck cadence if the bridge does not emit a
                    # connection signal, while a completed pairing wakes the
                    # loop immediately.
                    logger.debug("Bridge awaiting QR pairing — waiting for connection signal")
                    try:
                        await self._bridge_manager.wait_until_connected(
                            timeout=_SSE_PAIRING_WAIT_TIMEOUT_S
                        )
                    except TimeoutError:
                        logger.debug("Bridge is still awaiting QR pairing")
                    continue
                logger.error(
                    "Bridge entered degraded mode: %s — stopping SSE loop",
                    self._bridge_manager.degraded_reason,
                )
                break

            # Not degraded here — the bridge is connected (or this is the
            # very first pass before startup readiness was even checked).
            # Retry pending endpoint_identity resolution so a first-time
            # setup self-heals as soon as pairing actually completes,
            # instead of staying on the "whatsapp:pending" placeholder for
            # the rest of the process lifetime (see
            # _maybe_resolve_pending_endpoint_identity).
            await self._maybe_resolve_pending_endpoint_identity()

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
        bridge_type = event.get("type", "")

        # Keepalive frames: silent drop.
        if bridge_type == "keepalive":
            return

        # Session invalidated: the WhatsApp session was logged out.
        if bridge_type == "session_invalidated":
            logger.warning(
                "WhatsApp session invalidated (logged out): %s",
                str(event.get("content", ""))[:200],
            )
            return

        # Legacy filter: some callers may set event_type to distinguish
        # presence/status updates from messages.
        event_type = event.get("event_type", "message")
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

        await self._record_owner_outbound_if_applicable(event, chat_jid)
        await self._buffer_event(event, chat_jid)

    async def _record_owner_outbound_if_applicable(
        self, event: dict[str, Any], chat_jid: str
    ) -> None:
        """Record a metadata-only owner-outbound point event (bu-whhll.8).

        The bridge tags every message with ``raw.is_from_me`` (whatsmeow
        ``MessageInfo.IsFromMe``), so no phone-number comparison is needed
        here. Fires independently of buffering/ingest-policy/discretion —
        this is a lightweight "phone in hand" corroborator signal (timestamp
        + channel only), not a routing decision. Fails soft: never raises.
        """
        raw = event.get("raw")
        if not isinstance(raw, dict) or not raw.get("is_from_me"):
            return

        ts = event.get("timestamp") or event.get("observed_at")
        if isinstance(ts, (int, float)):
            occurred_at = datetime.fromtimestamp(ts, UTC)
        else:
            return

        message_id = event.get("message_id") or event.get("id") or "unknown"

        await record_owner_outbound_point(
            self._db_pool,
            channel=self._config.channel,
            provider=self._config.provider,
            endpoint_identity=self._config.endpoint_identity,
            occurred_at=occurred_at,
            dedup_material=f"{chat_jid}:{message_id}",
        )

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

    async def _load_flush_interval_from_db(self) -> int | None:
        """Read ``flush_interval_s`` from ``connector_registry.settings`` JSONB.

        Returns the dashboard-configured value, or ``None`` when unset / unavailable.
        The caller is responsible for applying the fallback chain:
        dashboard setting > env var > hardcoded default (1800 s).
        """
        if self._cursor_pool is None:
            return None
        try:
            from butlers.connectors.cursor_store import load_connector_settings

            settings = await load_connector_settings(
                self._cursor_pool,
                _CONNECTOR_TYPE,
                self._config.endpoint_identity,
            )
            if settings is None:
                return None
            raw = settings.get("flush_interval_s")
            if raw is None:
                return None
            return int(raw)
        except Exception as exc:
            logger.debug("WA: Failed to read flush_interval_s from DB (non-fatal): %s", exc)
            return None

    async def _flush_scanner_loop(self) -> None:
        """Background task: scan all chat buffers every 60 seconds.

        On each wake cycle, reads ``flush_interval_s`` from the dashboard
        settings (``connector_registry.settings``) to support live-reload
        without a connector restart.  Precedence: dashboard > env var > default.
        """
        logger.debug("WA flush scanner started (interval=%ds)", _FLUSH_SCANNER_INTERVAL_S)
        try:
            while True:
                await asyncio.sleep(_FLUSH_SCANNER_INTERVAL_S)
                db_interval = await self._load_flush_interval_from_db()
                effective_interval = (
                    db_interval if db_interval is not None else self._config.flush_interval_s
                )
                await self._scan_and_flush(effective_interval)
        except asyncio.CancelledError:
            logger.debug("WA flush scanner cancelled")
            raise

    async def _link_watchdog_loop(self) -> None:
        """Restart the connector if a *recoverable* link outage persists.

        A passive connector receiving no messages is indistinguishable from a
        dead link by message flow alone, so this watchdog is the independent
        liveness signal. On a persistent recoverable outage — notably a
        StreamReplaced, where whatsmeow deliberately does not auto-reconnect — it
        exits the process so Docker restarts the container and a fresh bridge
        ``Connect()`` re-claims the WhatsApp session.

        It deliberately does NOT restart on terminal degraded states
        (pairing timeout, session invalidated, pair_required): those need a human
        QR re-pair, so a restart would only re-degrade in a pointless loop.
        Transient disconnects that self-heal reset the bridge's degraded clock
        and never trip this.
        """
        logger.debug(
            "WA stale-link watchdog started (threshold=%ds, interval=%ds)",
            self._config.stale_restart_threshold_s,
            _LINK_WATCHDOG_INTERVAL_S,
        )
        try:
            while self._running:
                await asyncio.sleep(_LINK_WATCHDOG_INTERVAL_S)
                # Invalidated-session alerting/recovery (bu-5ocmh) is an
                # independent concern from the restart-on-recoverable-outage
                # check below — it must still run when stale_restart_threshold_s
                # disables the restart path. Guarded with a blanket try/except:
                # this loop has no outer handler besides CancelledError, so an
                # unexpected exception here must never kill the stale-link
                # restart check that follows — that would silently reintroduce
                # exactly the "nothing ever tells the owner" failure mode this
                # bead exists to fix, just from a different code path.
                try:
                    await self._check_invalidated_session_state()
                except Exception:
                    logger.exception(
                        "WA: invalidated-session check failed (non-fatal, continuing watchdog)"
                    )
                if self._link_is_stale():
                    await self._restart_for_stale_link()
                    return
        except asyncio.CancelledError:
            logger.debug("WA stale-link watchdog cancelled")
            raise

    def _link_is_stale(self) -> bool:
        """True if a *recoverable* link outage has exceeded the restart threshold.

        Terminal degraded states (needs human re-pair) are excluded: restarting
        cannot recover them, so the watchdog must not loop on them.
        """
        if self._bridge_manager is None:
            return False
        threshold = self._config.stale_restart_threshold_s
        if threshold <= 0:
            return False
        if self._bridge_manager.is_degraded_terminal:
            return False
        down_s = self._bridge_manager.degraded_duration_s
        return down_s is not None and down_s >= threshold

    async def _restart_for_stale_link(self) -> None:
        """Flush what we can, then exit non-zero so Docker restarts the container."""
        reason = self._bridge_manager.degraded_reason if self._bridge_manager else None
        down_s = self._bridge_manager.degraded_duration_s if self._bridge_manager else None
        logger.error(
            "WhatsApp link degraded for %.0fs (>= %ds threshold; reason: %s) — "
            "exiting to restart connector and re-claim the session",
            down_s or 0.0,
            self._config.stale_restart_threshold_s,
            reason,
        )
        # Best-effort flush of any messages buffered before the link died. The
        # Switchboard path is independent of the WhatsApp link, so this can still
        # succeed even though WhatsApp itself is unreachable.
        try:
            await asyncio.wait_for(
                self._flush_all_buffers(reason="stale_link_restart"), timeout=10.0
            )
        except Exception:
            logger.exception("Flush before stale-link restart failed (continuing to exit)")
        self._exit_process()

    def _exit_process(self) -> None:
        """Terminate the process so the container restart policy respawns it.

        Isolated as a seam so tests can assert the watchdog decided to restart
        without actually killing the test runner.
        """
        os._exit(1)

    # -------------------------------------------------------------------------
    # Internal: Invalidated-session alerting + owner-triggered recovery (bu-5ocmh)
    # -------------------------------------------------------------------------

    _PAIR_RESET_SETTINGS_KEY = "pair_reset_requested_at"

    async def _check_invalidated_session_state(self) -> None:
        """Called once per watchdog tick: alert the owner on a newly-detected
        invalidated session, and act on any owner-requested pairing reset.

        Split into two independent halves with different triggers: alerting
        fires on bridge_manager's own duration-based escalation (no owner
        action needed); recovery only fires once the owner has explicitly
        clicked "pair device" on an invalidated session (see the dashboard's
        POST /pair/start handling in api/routers/whatsapp.py).
        """
        if self._bridge_manager is None:
            return

        if self._bridge_manager.is_invalidated_session:
            if not self._invalidated_session_alert_sent:
                await self._send_invalidated_session_alert()
                self._invalidated_session_alert_sent = True
        else:
            # Recovered (re-paired) or not currently invalidated — allow a
            # future invalidation episode to alert again.
            self._invalidated_session_alert_sent = False

        await self._maybe_perform_pair_reset()

    async def _send_invalidated_session_alert(self) -> None:
        """Best-effort owner alert for a persistently-invalidated WhatsApp session.

        This runs outside any butler daemon, so the full ``notify()`` MCP
        tool (which needs a daemon's switchboard_client/db/permission
        context — see ``butlers.core_tools._notifications.notify``) is not
        reachable here. Calls Switchboard's ``deliver`` tool directly
        instead — the same low-level primitive ``notify()`` itself calls
        internally, and the established pattern for non-daemon callers (see
        ``butlers.background``). Never raises — an alerting failure must
        never affect ingestion.
        """
        notify_request = {
            "schema_version": "notify.v1",
            "origin_butler": _CONNECTOR_TYPE,
            "delivery": {
                "intent": "send",
                "channel": "telegram",
                "message": (
                    "WhatsApp appears to have been unlinked (remote unlink or "
                    "long-offline expiry) and cannot reconnect on its own. "
                    "Ingestion is paused until you re-pair — open the dashboard "
                    "WhatsApp settings and use 'Pair device' to scan a new QR code."
                ),
            },
        }
        delivered = False
        try:
            result = await self._mcp_client.call_tool(
                "deliver",
                {"source_butler": _CONNECTOR_TYPE, "notify_request": notify_request},
            )
            if isinstance(result, dict) and result.get("status") == "failed":
                logger.warning(
                    "WhatsApp invalidated-session alert was not delivered: %s",
                    result.get("error"),
                )
            else:
                delivered = True
        except Exception:
            logger.exception("WhatsApp invalidated-session alert failed to send")

        if delivered and self._db_pool is not None:
            from butlers.core.attention_ledger import record_attention_event

            await record_attention_event(
                self._db_pool,
                origin_butler=_CONNECTOR_TYPE,
                source="notify",
                outcome="delivered",
                channel="telegram",
                intent="send",
                priority="high",
                reason="whatsapp_invalidated_session",
            )

    async def _maybe_perform_pair_reset(self) -> None:
        """Act on an owner-requested pairing reset flag, if one is pending.

        The dashboard's POST /pair/start writes ``pair_reset_requested_at``
        to ``switchboard.connector_registry.settings`` when it detects the
        bridge holding a dead device it cannot recover from on its own
        (bu-5ocmh) — the dashboard runs in a separate container from this
        connector and cannot reach into this process's
        BridgeSubprocessManager directly, so it hands off via the DB, the
        same way dashboard-configurable settings like flush_interval_s
        already do (see ``_load_flush_interval_from_db``).

        Only acts while the bridge is currently degraded-terminal — a stale
        or duplicate flag must never clear a healthy, connected session.
        """
        if self._db_pool is None or self._bridge_manager is None:
            return
        if not self._bridge_manager.is_degraded_terminal:
            return

        try:
            from butlers.connectors.cursor_store import load_connector_settings

            settings = await load_connector_settings(
                self._db_pool, _CONNECTOR_TYPE, self._config.endpoint_identity
            )
            # Defensive: every pool in this codebase registers register_jsonb_codec()
            # (src/butlers/db.py), so `settings` is always a dict or None in
            # practice — but a raw JSON string here must never raise past this
            # try block and kill the watchdog loop for the rest of the process
            # lifetime (see _link_watchdog_loop, which has no blanket handler).
            requested_at = (settings or {}).get(self._PAIR_RESET_SETTINGS_KEY)
        except Exception:
            logger.debug("WA: failed to read pair-reset flag from DB (non-fatal)", exc_info=True)
            return

        if not requested_at or requested_at == self._last_pair_reset_handled_at:
            return

        logger.warning(
            "WhatsApp: owner-requested pairing reset detected (requested_at=%s) — "
            "clearing whatsmeow device store and restarting bridge into QR pairing",
            requested_at,
        )
        self._last_pair_reset_handled_at = requested_at
        await self._perform_pair_reset()

    async def _clear_whatsmeow_device_store(self) -> None:
        """DELETE FROM public.whatsmeow_device — the same cascade manual
        recovery used to unblock the 2026-07-05 outage (whatsmeow's own FK
        cascade to its ~12 child tables)."""
        if self._cursor_pool is None:
            raise RuntimeError(
                "WhatsApp: cannot clear whatsmeow_device store — no DB pool available"
            )
        await self._cursor_pool.execute("DELETE FROM public.whatsmeow_device")
        logger.warning("WhatsApp: cleared public.whatsmeow_device for re-pair")

    async def _perform_pair_reset(self) -> None:
        """Clear the dead whatsmeow device store and restart the bridge into
        QR pairing mode — the explicit, owner-triggered recovery action for
        an invalidated session (bu-5ocmh).

        This is destructive (drops the stored device) and deliberately NOT
        run automatically on detection; it only runs once an owner has
        explicitly requested it (see ``_maybe_perform_pair_reset``).
        """
        if self._bridge_manager is None:
            return

        # Consume the persisted flag now, before doing anything destructive.
        # `_last_pair_reset_handled_at` only guards re-triggers within THIS
        # process's lifetime — the DB row is otherwise never cleared, so a
        # future connector restart (redeploy, crash, host reboot) would
        # re-read the same old timestamp as if it were a brand-new request
        # and wipe an already-healthy, already-repaired device. Clearing it
        # here (best-effort; a ledger-write hiccup must not abort recovery)
        # makes "already handled" durable across restarts, not just in-memory.
        if self._db_pool is not None:
            try:
                from butlers.connectors.cursor_store import save_connector_settings

                await save_connector_settings(
                    self._db_pool,
                    _CONNECTOR_TYPE,
                    self._config.endpoint_identity,
                    {self._PAIR_RESET_SETTINGS_KEY: None},
                )
            except Exception:
                logger.exception(
                    "WhatsApp: failed to clear pair-reset flag in DB (continuing anyway) — "
                    "a connector restart before this clears could re-trigger recovery"
                )

        try:
            await self._bridge_manager.stop()
        except Exception:
            logger.exception("WhatsApp: failed to stop bridge before pair-reset (continuing)")

        try:
            await self._clear_whatsmeow_device_store()
        except Exception:
            # Don't abort the restart on a clear failure (e.g. a transient DB
            # blip) — that would leave the connector with NO running bridge
            # at all until the owner notices and clicks "pair device" again.
            # Restarting with the old (still-invalidated) device is at worst
            # a no-op, never worse than the outage this is meant to fix.
            logger.exception("WhatsApp: failed to clear whatsmeow_device store — restarting anyway")

        try:
            # The freshly-restarted bridge boots with no device row, so it
            # takes the Go bridge's "no paired device" branch and reports
            # pair_required with a real QR code. Unlike the connector's
            # ordinary boot (which requires reaching a full "connected"
            # state within startup_timeout_s), this restart must accept a
            # terminal pair_required outcome as a normal, expected result —
            # see _build_bridge_config(startup_allow_degraded=True).
            self._bridge_manager = BridgeSubprocessManager(
                self._build_bridge_config(startup_allow_degraded=True)
            )
            await self._bridge_manager.start()
            logger.warning("WhatsApp: bridge restarted into QR pairing mode after pair-reset")
        except TimeoutError:
            logger.info(
                "WhatsApp: bridge restart after pair-reset did not reach startup "
                "readiness within the timeout (may still be spawning)"
            )
        except Exception:
            logger.exception("WhatsApp: failed to restart bridge after pair-reset")
        finally:
            # Whichever way this went, forget the alert episode so a fresh
            # invalidation (or a still-stuck bridge) can alert/react again.
            self._invalidated_session_alert_sent = False

    async def _scan_and_flush(self, flush_interval_s: int | None = None) -> None:
        """Iterate all chat buffers and flush those whose interval has elapsed.

        Args:
            flush_interval_s: Effective flush interval in seconds.  When
                ``None``, falls back to ``self._config.flush_interval_s``.
        """
        if flush_interval_s is None:
            flush_interval_s = self._config.flush_interval_s
        now = time.monotonic()
        chat_jids = list(self._chat_buffers.keys())
        for jid in chat_jids:
            buf = self._chat_buffers.get(jid)
            if buf is None:
                continue
            if not buf.messages:
                continue
            elapsed = now - buf.last_flush_ts
            if elapsed >= flush_interval_s:
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
                        # Fail OPEN on a discretion infra failure for this
                        # primary personal channel — see the constant's rationale.
                        weight_fail_open=_WHATSAPP_DISCRETION_WEIGHT_FAIL_OPEN,
                    )

                # Resolve sender weight from last event in batch
                _last = buffered_events[-1] if buffered_events else {}
                sender_jid = _last.get("sender_jid") or _last.get("from_jid") or ""
                sender_weight = 1.0
                if self._weight_resolver and sender_jid:
                    sender_weight = await self._weight_resolver.resolve("whatsapp_jid", sender_jid)

                d_result = await self._discretion_evaluators[chat_jid].evaluate(
                    normalized_text, weight=sender_weight
                )
                if d_result.verdict == "IGNORE":
                    ignore_kind = classify_ignore_kind(d_result)
                    logger.debug(
                        "Discretion IGNORE (%s) for batch in chat %s", ignore_kind, chat_jid
                    )
                    # Per-channel drop-rate visibility (bu-cicgb): low-cardinality
                    # channel × kind counter so over-filtering (and the genuine
                    # llm_verdict vs infra fail-closed split) is scrapeable.
                    record_discretion_ignore(channel=self._config.channel, kind=ignore_kind)
                    self._record_batch_filtered_event(
                        chat_jid=chat_jid,
                        batch_event_id=batch_event_id,
                        filter_reason=FilteredEventBuffer.reason_discretion_ignore(ignore_kind),
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

        Includes participant_count and chat_type for Dunbar group-aware scoring
        (RFC 0013) and sets control.interaction_eligible=False for large groups.
        """
        # Derive chat type and participant count for Dunbar gating (RFC 0013).
        chat_type = _derive_wa_chat_type(chat_jid)
        participant_count: int | None = None
        # Scan all events for the first non-None participant_count (bridge may
        # only include it in some events; the last event is not guaranteed to have it).
        for _ev in buffered_events:
            participant_count = _extract_wa_participant_count(_ev)
            if participant_count is not None:
                break
        if participant_count is None:
            # Fallback: DMs always have 2 participants; groups are unknown without bridge support.
            participant_count = 2 if chat_type == "private" else None

        # Gate interaction eligibility (RFC 0013).
        _max_size = self._config.max_interaction_group_size
        if participant_count is not None and participant_count > _max_size:
            interaction_eligible = False
            self._metrics.record_interaction_gated(chat_type, participant_count)
            logger.debug(
                "Interaction gated for batch in chat %s (participant_count=%d, chat_type=%s)",
                chat_jid,
                participant_count,
                chat_type,
            )
        else:
            interaction_eligible = True

        if not buffered_events:
            normalized_text = "[no messages]"
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
                "sender": {
                    "identity": "multiple",
                    "participant_count": participant_count,
                    "chat_type": chat_type,
                },
                "payload": {
                    "raw": {"conversation_history": []},
                    "normalized_text": normalized_text,
                },
                "control": {
                    "idempotency_key": f"wa_batch:{chat_jid}:{batch_event_id}",
                    "policy_tier": "passive",
                    "addressed": False,
                    "payload_type": "conversation_history",
                    "interaction_eligible": interaction_eligible,
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
            sender_jid = event.get("sender_jid") or event.get("from_jid") or "unknown"
            msg_text = normalize_message_text(event)
            text_parts.append(f"[{sender_jid}]: {msg_text}")

        footer_lines = ["---", f"Messages: {len(buffered_events)} new"]

        normalized_text = "\n".join(header_lines + text_parts + footer_lines)

        flush_ts = datetime.now(UTC).isoformat()

        # Build conversation_history: one entry per buffered event, sorted ascending by message ID.
        conversation_history: list[dict[str, Any]] = []
        for event in buffered_events:
            msg_id = event.get("message_id") or event.get("id")
            sender_id = event.get("sender_jid") or event.get("from_jid")
            text = normalize_message_text(event)
            ts = event.get("timestamp") or event.get("observed_at")
            if ts is not None:
                if isinstance(ts, (int, float)):
                    timestamp = datetime.fromtimestamp(ts, UTC).isoformat()
                else:
                    timestamp = str(ts)
            else:
                timestamp = None
            reply_to = (event.get("content") or {}).get("quoted_message_id")
            conversation_history.append(
                {
                    "message_id": msg_id,
                    "sender_id": sender_id,
                    "text": text,
                    "timestamp": timestamp,
                    "is_new": True,
                    "reply_to": reply_to,
                }
            )

        # Build raw payload from all events
        raw_payload = {
            "events": buffered_events,
            "chat_jid": chat_jid,
            "batch_size": len(buffered_events),
            "conversation_history": conversation_history,
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
            "sender": {
                "identity": "multiple",
                "participant_count": participant_count,
                "chat_type": chat_type,
            },
            "payload": {
                "raw": raw_payload,
                "normalized_text": normalized_text,
            },
            "control": {
                "idempotency_key": f"wa_batch:{chat_jid}:{batch_event_id}",
                "policy_tier": "passive",
                "addressed": _detect_addressed_in_events(
                    buffered_events, self._config.address_keywords
                ),
                "payload_type": "conversation_history",
                "interaction_eligible": interaction_eligible,
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
        - control.policy_tier = "passive"
        - control.addressed = True if message starts with an address keyword
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

        # Participant count + chat type for Dunbar group-aware scoring (RFC 0013).
        chat_type = _derive_wa_chat_type(chat_jid)
        participant_count = _extract_wa_participant_count(event)
        if participant_count is None:
            participant_count = 2 if chat_type == "private" else None

        # Gate interaction eligibility based on participant count.
        _max_size = self._config.max_interaction_group_size
        if participant_count is not None and participant_count > _max_size:
            interaction_eligible = False
            self._metrics.record_interaction_gated(chat_type, participant_count)
            logger.debug(
                "Interaction gated for chat %s (participant_count=%d, chat_type=%s)",
                chat_jid,
                participant_count,
                chat_type,
            )
        else:
            interaction_eligible = True

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
            "sender": {
                "identity": sender_jid,
                "participant_count": participant_count,
                "chat_type": chat_type,
            },
            "payload": {
                "raw": event,
                "normalized_text": normalized_text,
            },
            "control": {
                "idempotency_key": idempotency_key,
                "policy_tier": "passive",
                "addressed": _detect_addressed(normalized_text, self._config.address_keywords),
                "interaction_eligible": interaction_eligible,
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
            _CONNECTOR_TYPE,
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
            connector_type=_CONNECTOR_TYPE,
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
        if self._discretion_dispatcher is not None:
            auth_health = self._discretion_dispatcher.get_auth_health()
            if auth_health["status"] == "degraded":
                return (
                    "degraded",
                    "discretion auth degraded: "
                    f"runtime={auth_health['runtime_type']} "
                    f"auth_file_present={auth_health['auth_file_present']}",
                )
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
                _CONNECTOR_TYPE,
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
                _CONNECTOR_TYPE,
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

        The owner-only bridge accepts a versioned request and acknowledges the
        number of already-normalized messages scheduled for its existing SSE
        stream. Duplicates remain harmless through Switchboard idempotency.
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
                body = json.dumps(
                    {
                        "schema_version": "whatsapp.backfill.v1",
                        "window_hours": self._config.backfill_window_h,
                    }
                ).encode()
                request = (
                    "POST /backfill HTTP/1.0\r\n"
                    "Host: localhost\r\n"
                    "Content-Type: application/json\r\n"
                    f"Content-Length: {len(body)}\r\n"
                    "Connection: close\r\n"
                    "\r\n"
                )
                writer.write(request.encode() + body)
                await writer.drain()
                raw_response = await asyncio.wait_for(reader.read(4096), timeout=10.0)
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass

            headers, separator, response_body = raw_response.partition(b"\r\n\r\n")
            status_line = headers.split(b"\r\n", maxsplit=1)[0]
            status_parts = status_line.split(maxsplit=2)
            if not separator or len(status_parts) < 2 or status_parts[1] != b"200":
                safe_status = status_line.decode(errors="replace")
                raise RuntimeError(
                    f"Bridge /backfill returned unexpected response: {safe_status}"
                )
            acknowledgement = json.loads(response_body)
            if (
                acknowledgement.get("schema_version") != "whatsapp.backfill.v1"
                or acknowledgement.get("status") != "accepted"
                or acknowledgement.get("window_hours") != self._config.backfill_window_h
            ):
                raise RuntimeError("Bridge /backfill returned an invalid acknowledgement")
            replay_event_count = acknowledgement.get("replay_event_count")
            if (
                not isinstance(replay_event_count, int)
                or isinstance(replay_event_count, bool)
                or replay_event_count < 0
            ):
                raise RuntimeError(
                    "Bridge /backfill acknowledgement has an invalid replay_event_count"
                )

            logger.info(
                "Backfill request accepted by bridge",
                extra={
                    "window_hours": self._config.backfill_window_h,
                    "replay_event_count": replay_event_count,
                },
            )
        except Exception as exc:
            # Non-fatal: replay supplements the normal live stream.
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
                body_dict: dict[str, Any] = {
                    "status": state,
                    "connector_type": "whatsapp_user_client",
                    "endpoint_identity": connector._config.endpoint_identity,
                }
                if error_msg:
                    body_dict["error"] = error_msg
                if connector._discretion_dispatcher is not None:
                    body_dict["discretion_auth"] = (
                        connector._discretion_dispatcher.get_auth_health()
                    )
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
                setup=connector_setup_role,
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

    # Restore the shared codex CLI-auth token from the credential DB to disk so
    # this connector's discretion-tier codex calls find ~/.codex/auth.json instead
    # of 401-ing and silently failing closed (bu-wzbu9). Non-fatal, logs loudly on
    # failure; degraded state also surfaces via the discretion-auth health hook.
    from butlers.cli_auth.persistence import restore_connector_cli_auth

    await restore_connector_cli_auth(cursor_pool, context="whatsapp_user_client")

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
