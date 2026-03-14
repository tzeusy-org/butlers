"""Telegram User Client connector runtime for live ingestion.

This connector implements a Telegram user-client (MTProto) ingestion runtime
as defined in `docs/connectors/telegram_user_client.md`. It uses Telethon to
maintain a live user session and continuously ingest message activity visible
to the user's account.

IMPORTANT: This connector is privacy-sensitive and requires explicit user consent,
proper credential management, and scope controls before deployment.

Key behaviors:
- Live user-client session via Telethon (MTProto)
- Real-time message event subscription
- Durable checkpoint with restart-safe replay
- Idempotent submission to Switchboard MCP server via ingest tool
- Privacy/consent safeguards and scope controls
- Bounded in-flight requests with graceful degradation

Environment variables (see `docs/connectors/telegram_user_client.md` section 4):
- SWITCHBOARD_MCP_URL (required)
- CONNECTOR_PROVIDER=telegram (required)
- CONNECTOR_CHANNEL=telegram_user_client (required)
- CONNECTOR_MAX_INFLIGHT (optional, default 8)
- CONNECTOR_BACKFILL_WINDOW_H (optional, bounded startup replay in hours)
- CONNECTOR_BUTLER_DB_NAME (optional; local butler DB for per-butler overrides)
- BUTLER_SHARED_DB_NAME (optional; shared credential DB, defaults to 'butlers')
- TELEGRAM_API_ID (required; resolved from owner entity_info only; from my.telegram.org)
- TELEGRAM_API_HASH (required; resolved from owner entity_info only; from my.telegram.org)
- TELEGRAM_USER_SESSION (required; resolved from owner entity_info only; session string or
  encrypted file path)
- TELEGRAM_USER_FLUSH_INTERVAL_S (optional, default 600): seconds between per-chat flushes
- TELEGRAM_USER_HISTORY_MAX_MESSAGES (optional, default 50): history fetch limit per flush
- TELEGRAM_USER_HISTORY_TIME_WINDOW_M (optional, default 30): history lookback window (minutes)
- TELEGRAM_USER_BUFFER_MAX_MESSAGES (optional, default 200): per-chat buffer cap before force-flush

Security requirements:
- Never commit credentials or session artifacts to version control
- Store session material in secret manager or encrypted storage
- Rotate/revoke sessions after credential exposure
- Explicit user consent before enabling account-wide ingestion
- Clear disclosure of chat/sender scope (allow/deny lists)
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import time
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from butlers.connectors.discretion import (
    ContactWeightResolver,
    DiscretionConfig,
    DiscretionEvaluator,
)
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

# Telethon is marked as optional dependency - handle import gracefully
try:
    from telethon import TelegramClient, events
    from telethon.sessions import StringSession
    from telethon.tl.types import Message as TelegramMessage

    TELETHON_AVAILABLE = True
except ImportError:
    TELETHON_AVAILABLE = False
    TelegramClient = None
    events = None
    StringSession = None
    TelegramMessage = None

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Chat buffer data structure
# ---------------------------------------------------------------------------

_FLUSH_SCANNER_INTERVAL_S = 60  # How often the flush scanner wakes up


@dataclass
class ChatBuffer:
    """Per-chat accumulation buffer for incoming Telegram messages.

    Fields:
        messages:       Accumulated messages since last flush.
        last_flush_ts:  Monotonic timestamp of the last flush (or creation).
        lock:           asyncio.Lock preventing concurrent flush + append for
                        the same chat.
    """

    messages: list[Any] = field(default_factory=list)
    last_flush_ts: float = field(default_factory=time.monotonic)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@dataclass
class TelegramUserClientConnectorConfig:
    """Configuration for Telegram user-client connector runtime."""

    # Switchboard MCP config
    switchboard_mcp_url: str

    # Connector identity
    provider: str = "telegram"
    channel: str = "telegram_user_client"
    endpoint_identity: str = field(default="")

    # Telegram user-client credentials (MTProto)
    telegram_api_id: int = 0
    telegram_api_hash: str = field(default="")
    telegram_user_session: str = field(default="")

    # State/checkpoint config
    backfill_window_h: int | None = None

    # Concurrency control
    max_inflight: int = 8

    # Buffering / flush config
    flush_interval_s: int = 600
    history_max_messages: int = 50
    history_time_window_m: int = 30
    buffer_max_messages: int = 200

    @classmethod
    def from_env(cls) -> TelegramUserClientConnectorConfig:
        """Load non-credential configuration from environment variables.

        Telegram user credentials (TELEGRAM_API_ID, TELEGRAM_API_HASH,
        TELEGRAM_USER_SESSION) are resolved exclusively from owner entity_info
        via ``_resolve_telegram_user_credentials_from_db()``.  They are not
        read from environment variables.
        """
        if not TELETHON_AVAILABLE:
            raise RuntimeError("Telethon is not installed. Install with: uv pip install telethon")

        switchboard_mcp_url = os.environ.get("SWITCHBOARD_MCP_URL")
        if not switchboard_mcp_url:
            raise ValueError("SWITCHBOARD_MCP_URL environment variable is required")

        provider = os.environ.get("CONNECTOR_PROVIDER", "telegram")
        channel = os.environ.get("CONNECTOR_CHANNEL", "telegram_user_client")

        backfill_window_str = os.environ.get("CONNECTOR_BACKFILL_WINDOW_H")
        backfill_window_h = int(backfill_window_str) if backfill_window_str else None

        max_inflight = int(os.environ.get("CONNECTOR_MAX_INFLIGHT", "8"))

        flush_interval_s = int(os.environ.get("TELEGRAM_USER_FLUSH_INTERVAL_S", "600"))
        history_max_messages = int(os.environ.get("TELEGRAM_USER_HISTORY_MAX_MESSAGES", "50"))
        history_time_window_m = int(os.environ.get("TELEGRAM_USER_HISTORY_TIME_WINDOW_M", "30"))
        buffer_max_messages = int(os.environ.get("TELEGRAM_USER_BUFFER_MAX_MESSAGES", "200"))

        # Credential fields default to empty — must be populated from DB.
        return cls(
            switchboard_mcp_url=switchboard_mcp_url,
            provider=provider,
            channel=channel,
            telegram_api_id=0,
            telegram_api_hash="",
            telegram_user_session="",
            backfill_window_h=backfill_window_h,
            max_inflight=max_inflight,
            flush_interval_s=flush_interval_s,
            history_max_messages=history_max_messages,
            history_time_window_m=history_time_window_m,
            buffer_max_messages=buffer_max_messages,
        )


class TelegramUserClientConnector:
    """Telegram user-client connector runtime for live ingestion.

    Responsibilities:
    - Maintain live Telegram user-client session
    - Subscribe to account message updates
    - Normalize updates to ingest.v1 format
    - Submit to Switchboard ingest API
    - Persist checkpoint for safe resume

    Privacy/Consent Requirements:
    - Explicit user consent before enabling
    - Clear scope disclosure
    - Optional allow/deny lists for chats/senders
    - Audit trail of connector lifecycle

    Does NOT:
    - Classify messages
    - Route to specialist butlers
    - Mint canonical request_id values
    - Replace canonical Switchboard ingestion semantics
    """

    def __init__(
        self,
        config: TelegramUserClientConnectorConfig,
        db_pool: Any | None = None,
        cursor_pool: Any | None = None,
    ) -> None:
        if not TELETHON_AVAILABLE:
            raise RuntimeError("Telethon is not installed. Install with: uv pip install telethon")

        self._config = config
        self._mcp_client = CachedMCPClient(
            config.switchboard_mcp_url, client_name="telegram-user-client"
        )
        self._telegram_client: TelegramClient | None = None
        self._running = False
        self._semaphore = asyncio.Semaphore(config.max_inflight)
        self._last_message_id: int | None = None

        # DB pool for cursor read/write to switchboard.connector_registry.
        self._cursor_pool = cursor_pool

        # DB pool for filtered event persistence (may be None if DB unavailable).
        self._db_pool = db_pool

        # Metrics
        self._metrics = ConnectorMetrics(
            connector_type="telegram_user_client",
            endpoint_identity=config.endpoint_identity,
        )

        # Checkpoint tracking for heartbeat
        self._last_checkpoint_save: float | None = None

        # Heartbeat (started in start(), stopped in stop())
        self._switchboard_heartbeat: ConnectorHeartbeat | None = None

        # Ingestion policy evaluators (replaces SourceFilterEvaluator).
        # Two scopes evaluated in order:
        #   1. connector:telegram-user-client:<endpoint> — pre-ingest block/pass_through
        #   2. global — post-ingest skip/metadata_only/route_to/low_priority_queue
        # DB-backed with TTL refresh; fail-open on DB error.
        self._ingestion_policy = IngestionPolicyEvaluator(
            scope=f"connector:telegram-user-client:{config.endpoint_identity}",
            db_pool=db_pool,
        )
        self._global_ingestion_policy = IngestionPolicyEvaluator(
            scope="global",
            db_pool=db_pool,
        )

        # Filtered event buffer: accumulates events filtered during each message event.
        # Flushed to connectors.filtered_events after each event is processed.
        self._filtered_event_buffer = FilteredEventBuffer(
            connector_type=config.provider,
            endpoint_identity=config.endpoint_identity,
        )

        # Discretion layer: LLM-based FORWARD/IGNORE filter per chat.
        # Weight resolver maps sender → contact role → weight tier.
        self._discretion_config = DiscretionConfig(env_prefix="TELEGRAM_USER_")
        self._discretion_evaluators: dict[str, DiscretionEvaluator] = {}
        self._weight_resolver: ContactWeightResolver | None = (
            ContactWeightResolver(db_pool) if db_pool is not None else None
        )

        # Per-chat message buffers: chat_id (str) → ChatBuffer.
        # Messages are accumulated here instead of being submitted immediately.
        self._chat_buffers: dict[str, ChatBuffer] = {}

        # Background flush scanner task (started in start(), cancelled in stop()).
        self._flush_scanner_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start the Telegram user-client connector in live-stream mode.

        This:
        1. Loads checkpoint from disk
        2. Connects to Telegram with user-client session
        3. Optionally performs bounded backfill
        4. Subscribes to live message events
        5. Runs until stopped
        """
        if self._cursor_pool is None:
            raise ValueError("DB cursor pool is required")

        # Load checkpoint
        await self._load_checkpoint()

        # Initialize Telegram client with user session
        session = StringSession(self._config.telegram_user_session)
        self._telegram_client = TelegramClient(
            session,
            self._config.telegram_api_id,
            self._config.telegram_api_hash,
        )

        # Load ingestion policy rules before entering the ingestion loop.
        await self._ingestion_policy.ensure_loaded()
        await self._global_ingestion_policy.ensure_loaded()

        # Start switchboard heartbeat (runs in background)
        self._start_heartbeat()

        # Start flush scanner background task
        self._flush_scanner_task = asyncio.create_task(
            self._flush_scanner_loop(), name="tg-flush-scanner"
        )

        self._running = True
        logger.info(
            "Starting Telegram user-client connector",
            extra={
                "endpoint_identity": self._config.endpoint_identity,
                "last_message_id": self._last_message_id,
                "backfill_window_h": self._config.backfill_window_h,
            },
        )

        try:
            # Connect to Telegram
            await self._telegram_client.start()
            logger.info("Connected to Telegram as user-client")

            # Force Telethon to sync internal update state (pts/date/seq).
            # Without this, StringSession clients may not receive real-time
            # NewMessage events because the update gap is unknown.
            await self._telegram_client.get_dialogs(limit=1)

            # Optional: bounded backfill on startup
            if self._config.backfill_window_h:
                await self._perform_backfill()

            # Register live message handler
            @self._telegram_client.on(events.NewMessage)
            async def handle_new_message(event: events.NewMessage.Event) -> None:
                """Handle new message events from Telegram."""
                await self._buffer_message(event.message)

            # Keep running until stopped
            logger.info("Telegram user-client connector running, waiting for messages...")
            await self._telegram_client.run_until_disconnected()

        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "Error in Telegram user-client connector",
                extra={"endpoint_identity": self._config.endpoint_identity},
            )
            raise
        finally:
            await self.stop()

    async def stop(self) -> None:
        """Stop the connector gracefully."""
        self._running = False

        # Cancel the flush scanner task first
        if self._flush_scanner_task is not None and not self._flush_scanner_task.done():
            self._flush_scanner_task.cancel()
            try:
                await self._flush_scanner_task
            except asyncio.CancelledError:
                pass
            self._flush_scanner_task = None

        # Force-flush all non-empty chat buffers before disconnecting
        await self._flush_all_buffers(reason="shutdown")

        if self._telegram_client and self._telegram_client.is_connected():
            await self._telegram_client.disconnect()

        # Stop switchboard heartbeat
        if self._switchboard_heartbeat is not None:
            await self._switchboard_heartbeat.stop()

        await self._mcp_client.aclose()
        logger.info("Telegram user-client connector stopped")

    # -------------------------------------------------------------------------
    # Internal: Heartbeat
    # -------------------------------------------------------------------------

    def _start_heartbeat(self) -> None:
        """Initialize and start heartbeat background task."""
        heartbeat_config = HeartbeatConfig.from_env(
            connector_type="telegram_user_client",
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
        """Determine current health state for heartbeat.

        Returns:
            Tuple of (state, error_message) where state is one of:
            "healthy", "degraded", "error"
        """
        if self._telegram_client is None:
            return ("error", "Telegram client not initialized")

        if not self._telegram_client.is_connected():
            return ("error", "Telegram client disconnected")

        return ("healthy", None)

    def _get_checkpoint(self) -> tuple[str | None, datetime | None]:
        """Get current checkpoint state for heartbeat.

        Returns:
            Tuple of (cursor_json, updated_at) — cursor_json matches the
            format written by ``_save_checkpoint`` so the heartbeat UPSERT
            does not corrupt the cursor for ``_load_checkpoint``.
        """
        if self._last_message_id is None:
            return (None, None)

        cursor = json.dumps({"last_message_id": self._last_message_id})
        updated_at = (
            datetime.fromtimestamp(self._last_checkpoint_save, UTC)
            if self._last_checkpoint_save is not None
            else None
        )
        return (cursor, updated_at)

    # -------------------------------------------------------------------------
    # Internal: Per-chat buffering and flush scanner
    # -------------------------------------------------------------------------

    async def _buffer_message(self, message: Any) -> None:
        """Append a single Telegram message to its chat's buffer.

        Extracts the chat_id and creates a ChatBuffer for that chat if one
        does not already exist.  If the buffer reaches the configured cap
        (``buffer_max_messages``), a force-flush is triggered immediately to
        prevent unbounded memory growth.
        """
        chat_id = self._extract_chat_id(message)
        if chat_id is None:
            logger.warning(
                "Could not extract chat_id from message, falling back to direct processing",
                extra={"message_id": getattr(message, "id", None)},
            )
            await self._process_message(message)
            return

        if chat_id not in self._chat_buffers:
            self._chat_buffers[chat_id] = ChatBuffer()

        buf = self._chat_buffers[chat_id]
        async with buf.lock:
            buf.messages.append(message)
            msg_count = len(buf.messages)

        logger.debug(
            "Buffered message for chat %s (buffer size: %d)",
            chat_id,
            msg_count,
        )

        # Force-flush if the buffer has hit its cap
        if msg_count >= self._config.buffer_max_messages:
            logger.info(
                "Chat %s buffer reached cap (%d messages), force-flushing",
                chat_id,
                msg_count,
            )
            await self._flush_chat_buffer(chat_id)

    async def _flush_scanner_loop(self) -> None:
        """Background task: scan all chat buffers every 60 seconds.

        Flushes any chat whose buffer is non-empty and has exceeded the
        configured flush interval (``flush_interval_s``).
        """
        logger.debug("Flush scanner started (interval=%ds)", _FLUSH_SCANNER_INTERVAL_S)
        try:
            while True:
                await asyncio.sleep(_FLUSH_SCANNER_INTERVAL_S)
                await self._scan_and_flush()
        except asyncio.CancelledError:
            logger.debug("Flush scanner cancelled")
            raise

    async def _scan_and_flush(self) -> None:
        """Iterate all chat buffers and flush those whose interval has elapsed."""
        now = time.monotonic()
        # Snapshot keys to avoid mutation during iteration
        chat_ids = list(self._chat_buffers.keys())
        for chat_id in chat_ids:
            buf = self._chat_buffers.get(chat_id)
            if buf is None:
                continue
            # Check non-empty without acquiring the lock for the fast path
            if not buf.messages:
                continue
            elapsed = now - buf.last_flush_ts
            if elapsed >= self._config.flush_interval_s:
                logger.info(
                    "Flush interval elapsed for chat %s (elapsed=%.1fs), flushing",
                    chat_id,
                    elapsed,
                )
                await self._flush_chat_buffer(chat_id)

    async def _flush_all_buffers(self, reason: str = "force") -> None:
        """Force-flush all non-empty chat buffers (called on shutdown)."""
        chat_ids = list(self._chat_buffers.keys())
        if not chat_ids:
            return

        logger.info("Flushing all %d chat buffers (%s)", len(chat_ids), reason)
        await asyncio.gather(*(self._flush_chat_buffer(chat_id) for chat_id in chat_ids))

    async def _flush_chat_buffer(self, chat_id: str) -> None:
        """Flush a single chat's buffer through the full batch pipeline.

        Pipeline:
        a. Atomically swap buffer (take messages, reset list).
        b. Fetch surrounding conversation history.
        c. Resolve reply-to messages not already in history.
        d. Build batch ingest.v1 envelope.
        e. Evaluate ingestion policy (connector + global scope).
        f. Evaluate discretion on concatenated normalized_text.
        g. Submit batch envelope via _submit_to_ingest().
        h. Advance checkpoint to max message ID among buffered messages.
        i. Record filtered events for policy/discretion rejections.
        """
        buf = self._chat_buffers.get(chat_id)
        if buf is None:
            return

        async with buf.lock:
            if not buf.messages:
                return
            # a. Atomically swap: take the accumulated messages and reset the list.
            buffered_messages = buf.messages
            buf.messages = []
            buf.last_flush_ts = time.monotonic()

        logger.info(
            "Flushing %d messages for chat %s",
            len(buffered_messages),
            chat_id,
        )

        try:
            # b. Fetch surrounding conversation history.
            context_messages = await self._fetch_conversation_history(chat_id, buffered_messages)

            # c. Resolve reply-to messages not already in context.
            context_messages = await self._resolve_reply_tos(
                chat_id, buffered_messages, context_messages
            )

            # d. Build batch ingest.v1 envelope.
            envelope = self._build_batch_envelope(chat_id, buffered_messages, context_messages)

            # e. Evaluate ingestion policy (connector scope — block/pass_through).
            _ip_envelope = IngestionEnvelope(
                source_channel="telegram_user_client",
                raw_key=chat_id,
            )
            _ip_decision = self._ingestion_policy.evaluate(_ip_envelope)
            if not _ip_decision.allowed:
                logger.debug(
                    "Ingestion policy blocked batch for chat %s: action=%s reason=%s",
                    chat_id,
                    _ip_decision.action,
                    _ip_decision.reason,
                )
                min_id = min(m.id for m in buffered_messages)
                max_id = max(m.id for m in buffered_messages)
                batch_event_id = f"batch:{chat_id}:{min_id}-{max_id}"
                self._filtered_event_buffer.record(
                    external_message_id=batch_event_id,
                    source_channel=self._config.channel,
                    sender_identity="multiple",
                    subject_or_preview=None,
                    filter_reason=FilteredEventBuffer.reason_policy_rule(
                        "connector_rule",
                        "block",
                        _ip_decision.matched_rule_type or "unknown",
                    ),
                    full_payload=FilteredEventBuffer.full_payload(
                        channel=self._config.channel,
                        provider=self._config.provider,
                        endpoint_identity=self._config.endpoint_identity,
                        external_event_id=batch_event_id,
                        external_thread_id=chat_id,
                        observed_at=datetime.now(UTC).isoformat(),
                        sender_identity="multiple",
                        raw={},
                    ),
                )
                await self._flush_and_drain()
                return

            # e (continued). Evaluate global ingestion policy (skip/metadata_only/...).
            _gp_decision = self._global_ingestion_policy.evaluate(_ip_envelope)
            if _gp_decision.action == "skip":
                logger.debug(
                    "Global ingestion policy skipped batch for chat %s: reason=%s",
                    chat_id,
                    _gp_decision.reason,
                )
                min_id = min(m.id for m in buffered_messages)
                max_id = max(m.id for m in buffered_messages)
                batch_event_id = f"batch:{chat_id}:{min_id}-{max_id}"
                self._filtered_event_buffer.record(
                    external_message_id=batch_event_id,
                    source_channel=self._config.channel,
                    sender_identity="multiple",
                    subject_or_preview=None,
                    filter_reason=FilteredEventBuffer.reason_policy_rule(
                        "global_rule",
                        "skip",
                        _gp_decision.matched_rule_type or "unknown",
                    ),
                    full_payload=FilteredEventBuffer.full_payload(
                        channel=self._config.channel,
                        provider=self._config.provider,
                        endpoint_identity=self._config.endpoint_identity,
                        external_event_id=batch_event_id,
                        external_thread_id=chat_id,
                        observed_at=datetime.now(UTC).isoformat(),
                        sender_identity="multiple",
                        raw={},
                    ),
                )
                await self._flush_and_drain()
                return

            # f. Evaluate discretion on concatenated normalized_text of new messages only.
            normalized_text: str = envelope["payload"]["normalized_text"]
            if self._discretion_config.llm_url and normalized_text:
                if chat_id not in self._discretion_evaluators:
                    self._discretion_evaluators[chat_id] = DiscretionEvaluator(
                        source_name=f"tg:{chat_id}",
                        config=self._discretion_config,
                    )
                d_result = await self._discretion_evaluators[chat_id].evaluate(
                    normalized_text, weight=1.0
                )
                if d_result.verdict == "IGNORE":
                    logger.debug(
                        "Discretion IGNORE for batch in chat %s",
                        chat_id,
                    )
                    batch_event_id = envelope["event"]["external_event_id"]
                    self._filtered_event_buffer.record(
                        external_message_id=batch_event_id,
                        source_channel=self._config.channel,
                        sender_identity="multiple",
                        subject_or_preview=normalized_text[:200] if normalized_text else None,
                        filter_reason="discretion:IGNORE",
                        full_payload=FilteredEventBuffer.full_payload(
                            channel=self._config.channel,
                            provider=self._config.provider,
                            endpoint_identity=self._config.endpoint_identity,
                            external_event_id=batch_event_id,
                            external_thread_id=chat_id,
                            observed_at=datetime.now(UTC).isoformat(),
                            sender_identity="multiple",
                            raw={},
                        ),
                    )
                    await self._flush_and_drain()
                    return

            # g. Submit via _submit_to_ingest().
            await self._submit_to_ingest(envelope)

            # Flush filtered event buffer after successful submission.
            await self._flush_and_drain()

            # h. Advance checkpoint to max(msg.id for msg in buffered_messages).
            max_id = max(m.id for m in buffered_messages)
            if self._last_message_id is None or max_id > self._last_message_id:
                self._last_message_id = max_id
                await self._save_checkpoint()

        except Exception as exc:
            logger.exception(
                "Failed to flush chat buffer for chat %s",
                chat_id,
                extra={"endpoint_identity": self._config.endpoint_identity},
            )
            # i. Record error event for the batch failure.
            min_id_str = str(min((m.id for m in buffered_messages), default=0))
            max_id_str = str(max((m.id for m in buffered_messages), default=0))
            batch_event_id = f"batch:{chat_id}:{min_id_str}-{max_id_str}"
            self._filtered_event_buffer.record(
                external_message_id=batch_event_id,
                source_channel=self._config.channel,
                sender_identity="multiple",
                subject_or_preview=None,
                filter_reason=FilteredEventBuffer.reason_submission_error(),
                full_payload=FilteredEventBuffer.full_payload(
                    channel=self._config.channel,
                    provider=self._config.provider,
                    endpoint_identity=self._config.endpoint_identity,
                    external_event_id=batch_event_id,
                    external_thread_id=chat_id,
                    observed_at=datetime.now(UTC).isoformat(),
                    sender_identity="multiple",
                    raw={},
                ),
                status="error",
                error_detail=str(exc),
            )
            await self._flush_and_drain()

    def _build_batch_envelope(
        self,
        chat_id: str,
        buffered_messages: list[Any],
        context_messages: list[Any],
    ) -> dict[str, Any]:
        """Build an ingest.v1 batch envelope for a flushed chat buffer.

        The envelope contains:
        - event.external_event_id: "batch:<chat_id>:<min_id>-<max_id>"
        - sender.identity: "multiple" (batch contains multiple senders)
        - payload.normalized_text: concatenated NEW messages with sender prefixes
        - payload.conversation_history: ordered list of all context messages
        - control.idempotency_key: "tg_batch:<chat_id>:<min_id>:<max_id>"

        Args:
            chat_id: The chat identifier string.
            buffered_messages: The new (flush buffer) messages.
            context_messages: All context messages (history + buffered), sorted by ID.

        Returns:
            ingest.v1 envelope dict.
        """
        buffered_ids: set[int] = {getattr(m, "id", None) for m in buffered_messages} - {None}

        # Determine min/max message IDs from the buffered (new) messages.
        msg_ids = [getattr(m, "id", 0) for m in buffered_messages]
        min_id = min(msg_ids) if msg_ids else 0
        max_id = max(msg_ids) if msg_ids else 0

        # Build normalized_text: concatenate NEW messages only, with sender prefixes.
        new_messages_sorted = sorted(
            (m for m in buffered_messages if getattr(m, "id", None) is not None),
            key=lambda m: m.id,
        )
        text_parts: list[str] = []
        for msg in new_messages_sorted:
            sender_id = self._extract_sender_identity(msg)
            text = getattr(msg, "message", None) or getattr(msg, "text", None) or ""
            text_parts.append(f"[{sender_id}]: {text}")
        normalized_text = "\n".join(text_parts)

        # Build conversation_history: all context messages, sorted ascending by ID.
        conversation_history: list[dict[str, Any]] = []
        for msg in sorted(context_messages, key=lambda m: getattr(m, "id", 0)):
            msg_id = getattr(msg, "id", None)
            sender_id = getattr(msg, "sender_id", None)
            text = getattr(msg, "message", None) or getattr(msg, "text", None) or ""
            msg_date = getattr(msg, "date", None)
            if msg_date is None:
                logger.warning(
                    "Message %s in chat %s has no date; timestamp will be null in envelope",
                    msg_id,
                    chat_id,
                )
            timestamp = msg_date.isoformat() if msg_date is not None else None
            reply_to = getattr(msg, "reply_to_msg_id", None)
            conversation_history.append(
                {
                    "message_id": msg_id,
                    "sender_id": sender_id,
                    "text": text,
                    "timestamp": timestamp,
                    "is_new": msg_id in buffered_ids,
                    "reply_to": reply_to,
                }
            )

        flush_timestamp = datetime.now(UTC).isoformat()

        return {
            "schema_version": "ingest.v1",
            "source": {
                "channel": self._config.channel,
                "provider": self._config.provider,
                "endpoint_identity": self._config.endpoint_identity,
            },
            "event": {
                "external_event_id": f"batch:{chat_id}:{min_id}-{max_id}",
                "external_thread_id": chat_id,
                "observed_at": flush_timestamp,
            },
            "sender": {
                "identity": "multiple",
            },
            "payload": {
                "raw": {},
                "normalized_text": normalized_text,
                "conversation_history": conversation_history,
            },
            "control": {
                "idempotency_key": f"tg_batch:{chat_id}:{min_id}:{max_id}",
                "policy_tier": "default",
            },
        }

    # -------------------------------------------------------------------------
    # Internal: Message processing
    # -------------------------------------------------------------------------

    async def _process_message(self, message: Any) -> None:
        """Process a single Telegram message event.

        Normalizes to ingest.v1 and submits to Switchboard ingest API.

        Filtered and errored events are recorded into the FilteredEventBuffer
        for batch persistence after the event is processed.
        """
        async with self._semaphore:
            try:
                # Extract message ID for checkpoint tracking
                message_id = getattr(message, "id", None)
                if message_id is None:
                    logger.warning(
                        "Received message without ID, skipping",
                        extra={"endpoint_identity": self._config.endpoint_identity},
                    )
                    return

                message_id_str = str(message_id)

                # Ingestion policy gate: evaluate before Switchboard submission.
                # Blocked messages are intentionally dropped — not an error condition.
                _ip_envelope = self._build_ingestion_envelope(message)

                # 1. Connector-scope rules (block/pass_through)
                _ip_decision = self._ingestion_policy.evaluate(_ip_envelope)
                if not _ip_decision.allowed:
                    logger.debug(
                        "Ingestion policy blocked Telegram user-client message %s: "
                        "action=%s reason=%s",
                        message_id,
                        _ip_decision.action,
                        _ip_decision.reason,
                    )
                    self._filtered_event_buffer.record(
                        external_message_id=message_id_str,
                        source_channel=self._config.channel,
                        sender_identity=self._extract_sender_identity(message),
                        subject_or_preview=self._extract_preview(message),
                        filter_reason=FilteredEventBuffer.reason_policy_rule(
                            "connector_rule",
                            "block",
                            _ip_decision.matched_rule_type or "unknown",
                        ),
                        full_payload=FilteredEventBuffer.full_payload(
                            channel=self._config.channel,
                            provider=self._config.provider,
                            endpoint_identity=self._config.endpoint_identity,
                            external_event_id=message_id_str,
                            external_thread_id=self._extract_chat_id(message),
                            observed_at=datetime.now(UTC).isoformat(),
                            sender_identity=self._extract_sender_identity(message),
                            raw=message.to_dict() if hasattr(message, "to_dict") else {},
                        ),
                    )
                    await self._flush_and_drain()
                    return

                # 2. Global-scope rules (skip/metadata_only/route_to/low_priority_queue)
                _gp_decision = self._global_ingestion_policy.evaluate(_ip_envelope)
                if _gp_decision.action == "skip":
                    logger.debug(
                        "Global ingestion policy skipped Telegram user-client message %s: "
                        "reason=%s",
                        message_id,
                        _gp_decision.reason,
                    )
                    self._filtered_event_buffer.record(
                        external_message_id=message_id_str,
                        source_channel=self._config.channel,
                        sender_identity=self._extract_sender_identity(message),
                        subject_or_preview=self._extract_preview(message),
                        filter_reason=FilteredEventBuffer.reason_policy_rule(
                            "global_rule",
                            "skip",
                            _gp_decision.matched_rule_type or "unknown",
                        ),
                        full_payload=FilteredEventBuffer.full_payload(
                            channel=self._config.channel,
                            provider=self._config.provider,
                            endpoint_identity=self._config.endpoint_identity,
                            external_event_id=message_id_str,
                            external_thread_id=self._extract_chat_id(message),
                            observed_at=datetime.now(UTC).isoformat(),
                            sender_identity=self._extract_sender_identity(message),
                            raw=message.to_dict() if hasattr(message, "to_dict") else {},
                        ),
                    )
                    await self._flush_and_drain()
                    return

                # 3. Discretion layer: LLM-based FORWARD/IGNORE filter.
                #    Only evaluated when the LLM URL is configured.
                msg_text = getattr(message, "message", None) or getattr(message, "text", None)
                if self._discretion_config.llm_url and msg_text:
                    chat_id_str = self._extract_chat_id(message) or "unknown"
                    if chat_id_str not in self._discretion_evaluators:
                        self._discretion_evaluators[chat_id_str] = DiscretionEvaluator(
                            source_name=f"tg:{chat_id_str}",
                            config=self._discretion_config,
                        )
                    # Resolve sender weight from contact roles.
                    sender_id = self._extract_sender_identity(message)
                    sender_weight = 1.0
                    if self._weight_resolver and sender_id != "unknown":
                        sender_weight = await self._weight_resolver.resolve("telegram", sender_id)
                    d_result = await self._discretion_evaluators[chat_id_str].evaluate(
                        msg_text, weight=sender_weight
                    )
                    if d_result.verdict == "IGNORE":
                        logger.debug(
                            "Discretion IGNORE for Telegram message %s in chat %s",
                            message_id,
                            chat_id_str,
                        )
                        self._filtered_event_buffer.record(
                            external_message_id=message_id_str,
                            source_channel=self._config.channel,
                            sender_identity=self._extract_sender_identity(message),
                            subject_or_preview=self._extract_preview(message),
                            filter_reason="discretion:IGNORE",
                            full_payload=FilteredEventBuffer.full_payload(
                                channel=self._config.channel,
                                provider=self._config.provider,
                                endpoint_identity=self._config.endpoint_identity,
                                external_event_id=message_id_str,
                                external_thread_id=self._extract_chat_id(message),
                                observed_at=datetime.now(UTC).isoformat(),
                                sender_identity=self._extract_sender_identity(message),
                                raw=(message.to_dict() if hasattr(message, "to_dict") else {}),
                            ),
                        )
                        await self._flush_and_drain()
                        return

                # Normalize to ingest.v1
                envelope = await self._normalize_to_ingest_v1(message)

                # Submit to Switchboard ingest
                await self._submit_to_ingest(envelope)

                # Flush after successful processing (drain replay-pending rows too)
                await self._flush_and_drain()

                # Update checkpoint
                if self._last_message_id is None or message_id > self._last_message_id:
                    self._last_message_id = message_id
                    await self._save_checkpoint()

            except Exception as exc:
                logger.exception(
                    "Failed to process Telegram message",
                    extra={
                        "message_id": getattr(message, "id", None),
                        "endpoint_identity": self._config.endpoint_identity,
                    },
                )
                # Record error event in the filtered event buffer
                msg_id_str = str(getattr(message, "id", "unknown"))
                try:
                    raw_for_error = message.to_dict() if hasattr(message, "to_dict") else {}
                except Exception:
                    raw_for_error = {}
                self._filtered_event_buffer.record(
                    external_message_id=msg_id_str,
                    source_channel=self._config.channel,
                    sender_identity="unknown",
                    subject_or_preview=None,
                    filter_reason=FilteredEventBuffer.reason_submission_error(),
                    full_payload=FilteredEventBuffer.full_payload(
                        channel=self._config.channel,
                        provider=self._config.provider,
                        endpoint_identity=self._config.endpoint_identity,
                        external_event_id=msg_id_str,
                        external_thread_id=None,
                        observed_at=datetime.now(UTC).isoformat(),
                        sender_identity="unknown",
                        raw=raw_for_error,
                    ),
                    status="error",
                    error_detail=str(exc),
                )
                await self._flush_and_drain()

    @staticmethod
    def _build_ingestion_envelope(message: object) -> IngestionEnvelope:
        """Build an IngestionEnvelope from a Telethon message object.

        Extracts the chat_id from the message and sets it as raw_key
        for ingestion policy evaluation.
        """
        chat_id = ""
        cid = getattr(message, "chat_id", None)
        if cid is not None:
            chat_id = str(cid)
        else:
            # Fallback: try peer_id (various Telethon peer types)
            peer_id = getattr(message, "peer_id", None)
            if peer_id is not None:
                for attr in ("channel_id", "chat_id", "user_id"):
                    val = getattr(peer_id, attr, None)
                    if val is not None:
                        chat_id = str(val)
                        break
        return IngestionEnvelope(
            source_channel="telegram_user_client",
            raw_key=chat_id,
        )

    @staticmethod
    def _extract_chat_id(message: Any) -> str | None:
        """Extract chat ID string from a Telethon message object, or None if absent."""
        cid = getattr(message, "chat_id", None)
        if cid is not None:
            return str(cid)
        peer_id = getattr(message, "peer_id", None)
        if peer_id is not None:
            for attr in ("channel_id", "chat_id", "user_id"):
                val = getattr(peer_id, attr, None)
                if val is not None:
                    return str(val)
        return None

    @staticmethod
    def _extract_sender_identity(message: Any) -> str:
        """Extract sender identity from a Telethon message object."""
        sender_id = getattr(message, "sender_id", None)
        if sender_id is not None:
            return str(sender_id)
        from_id = getattr(message, "from_id", None)
        if from_id is not None:
            user_id = getattr(from_id, "user_id", None)
            if user_id is not None:
                return str(user_id)
        return "unknown"

    @staticmethod
    def _extract_preview(message: Any) -> str | None:
        """Extract a short text preview from a Telethon message object."""
        text = getattr(message, "message", None) or getattr(message, "text", None)
        if text:
            return str(text)[:200]
        return None

    async def _fetch_conversation_history(
        self,
        chat_id: Any,
        buffered_messages: list[Any],
    ) -> list[Any]:
        """Fetch surrounding conversation history for a batch of buffered messages.

        Fetches up to ``self._history_max_messages`` (default 50) recent messages
        from ``chat_id``, with the look-back window starting at least
        ``self._history_time_window_m`` (default 30) minutes before the oldest
        buffered message.

        The returned list is the union of fetched history and the buffered messages,
        deduplicated by message ID and sorted ascending by ID.  If the Telethon
        ``get_messages()`` call fails (including ``FloodWaitError``), the method
        logs a warning and returns only the buffered messages (fail-open).

        Args:
            chat_id: Telethon-compatible chat entity (string, int, or peer).
            buffered_messages: Messages already in the flush buffer.

        Returns:
            Merged, deduplicated, ID-ascending list of context messages.
        """
        if not self._telegram_client:
            return list(buffered_messages)

        history_max = self._config.history_max_messages
        history_window_m = self._config.history_time_window_m

        # Determine the offset_date: look back from the oldest buffered message.
        oldest_date: datetime | None = None
        for msg in buffered_messages:
            msg_date = getattr(msg, "date", None)
            if msg_date is not None:
                if oldest_date is None or msg_date < oldest_date:
                    oldest_date = msg_date

        if oldest_date is not None:
            offset_date = oldest_date - timedelta(minutes=history_window_m)
        else:
            offset_date = datetime.now(UTC) - timedelta(minutes=history_window_m)

        try:
            history: list[Any] = await self._telegram_client.get_messages(
                chat_id,
                limit=history_max,
                offset_date=offset_date,
            )
        except Exception as exc:
            # FloodWaitError and all other errors: fail-open, proceed without history.
            logger.warning(
                "Failed to fetch conversation history for chat %s, proceeding without context: %s",
                chat_id,
                exc,
            )
            return list(buffered_messages)

        # Merge and deduplicate by message ID.
        seen: set[int] = set()
        merged: list[Any] = []
        for msg in list(history) + list(buffered_messages):
            msg_id = getattr(msg, "id", None)
            if msg_id is None or msg_id in seen:
                continue
            seen.add(msg_id)
            merged.append(msg)

        # Sort ascending by message ID.
        merged.sort(key=lambda m: getattr(m, "id", 0))
        return merged

    async def _resolve_reply_tos(
        self,
        chat_id: Any,
        buffered_messages: list[Any],
        context_messages: list[Any],
    ) -> list[Any]:
        """Fetch replied-to messages not already present in the context window.

        For each buffered message that has ``reply_to_msg_id`` set, this method
        checks whether the referenced message is already in ``context_messages``.
        If not, it fetches it via ``client.get_messages(chat, ids=mid)`` and
        appends it to the returned list.

        Only single-level resolution is performed — no recursive chain following.

        Fetch errors for individual reply-to messages are logged at DEBUG level
        and skipped (fail-open).

        Args:
            chat_id: Telethon-compatible chat entity.
            buffered_messages: The flush buffer (new messages).
            context_messages: Already-resolved context (history + buffered).

        Returns:
            A new list containing all ``context_messages`` plus any successfully
            fetched reply-to messages, sorted ascending by message ID.
        """
        if not self._telegram_client:
            return list(context_messages)

        # Collect reply_to IDs referenced by buffered messages.
        reply_ids: set[int] = set()
        for msg in buffered_messages:
            rid = getattr(msg, "reply_to_msg_id", None)
            if rid is not None:
                reply_ids.add(rid)

        if not reply_ids:
            return list(context_messages)

        # Filter out IDs already present in context_messages.
        present_ids: set[int] = {getattr(m, "id", None) for m in context_messages} - {None}
        missing_ids = reply_ids - present_ids

        if not missing_ids:
            return list(context_messages)

        result: list[Any] = list(context_messages)
        for mid in missing_ids:
            try:
                reply_msg = await self._telegram_client.get_messages(chat_id, ids=mid)
                if reply_msg is not None:
                    # get_messages(ids=...) may return a list or a single message.
                    if isinstance(reply_msg, list):
                        result.extend(m for m in reply_msg if m is not None)
                    else:
                        result.append(reply_msg)
            except Exception as exc:
                logger.debug(
                    "Failed to fetch reply-to message %s in chat %s: %s",
                    mid,
                    chat_id,
                    exc,
                )

        # Sort ascending by message ID.
        result.sort(key=lambda m: getattr(m, "id", 0))
        return result

    async def _flush_and_drain(self) -> None:
        """Flush filtered event buffer then drain up to 10 replay-pending rows.

        Called after each message event is processed.  No-op when ``_db_pool``
        is None (no DB connectivity at connector startup).
        """
        if self._db_pool is None:
            return

        # 1. Flush accumulated filtered/error events from this event.
        await self._filtered_event_buffer.flush(self._db_pool)

        # 2. Drain replay-pending rows left by the dashboard "retry" action.
        await self._drain_replay_pending()

    async def _drain_replay_pending(self) -> None:
        """Process up to 10 replay_pending rows from connectors.filtered_events.

        Delegates to the shared ``drain_replay_pending`` helper in
        :mod:`butlers.connectors.filtered_event_buffer`.
        """
        if self._db_pool is None:
            return

        await drain_replay_pending(
            self._db_pool,
            self._config.provider,
            self._config.endpoint_identity,
            self._submit_to_ingest,
            logger,
        )

    async def _normalize_to_ingest_v1(self, message: Any) -> dict[str, Any]:
        """Normalize Telegram user-client message to canonical ingest.v1 format.

        Mapping (from docs/connectors/telegram_user_client.md):
        - source.channel: "telegram_user_client"
        - source.provider: "telegram"
        - source.endpoint_identity: user-client identity
        - event.external_event_id: message.id
        - event.external_thread_id: chat.id or thread_id
        - event.observed_at: current timestamp (RFC3339)
        - sender.identity: sender user ID
        - payload.raw: full Telegram message object (as dict)
        - payload.normalized_text: extracted text
        - control.idempotency_key: tg:<chat_id>:<message_id> (canonical across connectors)
        """
        message_id = str(getattr(message, "id", "unknown"))
        chat_id = None
        sender_id = "unknown"
        normalized_text = ""

        # Extract chat ID
        if hasattr(message, "chat_id"):
            chat_id = str(message.chat_id)
        elif hasattr(message, "peer_id"):
            # Handle different peer types
            peer_id = message.peer_id
            if hasattr(peer_id, "channel_id"):
                chat_id = str(peer_id.channel_id)
            elif hasattr(peer_id, "chat_id"):
                chat_id = str(peer_id.chat_id)
            elif hasattr(peer_id, "user_id"):
                chat_id = str(peer_id.user_id)

        # Extract sender ID
        if hasattr(message, "sender_id"):
            sender_id = str(message.sender_id)
        elif hasattr(message, "from_id"):
            from_id = message.from_id
            if hasattr(from_id, "user_id"):
                sender_id = str(from_id.user_id)

        # Extract message text and sanitize for XSS protection
        if hasattr(message, "message"):
            normalized_text = html.escape(message.message or "")

        # Convert message to dict for raw payload
        # Telethon objects have to_dict() method
        raw_payload = message.to_dict() if hasattr(message, "to_dict") else {}

        # Canonical idempotency key: tg:<chat_id>:<message_id>
        # Uses chat_id + message_id (unique per chat) so that bot and user-client
        # connectors produce the same key for the same Telegram message.
        idem_key = (
            f"tg:{chat_id}:{message_id}"
            if chat_id
            else f"telegram:{self._config.endpoint_identity}:{message_id}"
        )

        # Build ingest.v1 envelope
        envelope = {
            "schema_version": "ingest.v1",
            "source": {
                "channel": self._config.channel,
                "provider": self._config.provider,
                "endpoint_identity": self._config.endpoint_identity,
            },
            "event": {
                "external_event_id": message_id,
                "external_thread_id": chat_id,
                "observed_at": datetime.now(UTC).isoformat(),
            },
            "sender": {
                "identity": sender_id,
            },
            "payload": {
                "raw": raw_payload,
                "normalized_text": normalized_text,
            },
            "control": {
                "idempotency_key": idem_key,
                "policy_tier": "default",
            },
        }

        return envelope

    async def _submit_to_ingest(self, envelope: dict[str, Any]) -> None:
        """Submit ingest.v1 envelope to Switchboard via MCP ingest tool.

        Handles retries and treats accepted duplicates as success.
        """
        try:
            result = await self._mcp_client.call_tool("ingest", envelope)

            # Check for tool-level error response
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
    # Internal: Backfill
    # -------------------------------------------------------------------------

    async def _perform_backfill(self) -> None:
        """Perform bounded backfill of recent messages on startup.

        This fetches messages from the configured backfill window and submits
        them to ingest, respecting the current checkpoint.
        """
        if not self._config.backfill_window_h:
            return

        if not self._telegram_client:
            logger.warning("Telegram client not initialized, skipping backfill")
            return

        logger.info(
            "Starting bounded backfill",
            extra={
                "window_hours": self._config.backfill_window_h,
                "endpoint_identity": self._config.endpoint_identity,
            },
        )

        try:
            # Calculate backfill time window
            backfill_start = datetime.now(UTC) - timedelta(hours=self._config.backfill_window_h)

            # Fetch recent dialogs and messages
            backfill_count = 0
            async for dialog in self._telegram_client.iter_dialogs():
                try:
                    # Fetch messages from this dialog within backfill window
                    async for message in self._telegram_client.iter_messages(
                        dialog,
                        offset_date=backfill_start,
                        reverse=True,  # Start from oldest in window
                    ):
                        # Skip if we've already processed this message
                        if self._last_message_id and message.id <= self._last_message_id:
                            continue

                        await self._process_message(message)
                        backfill_count += 1

                except Exception as exc:
                    logger.error(
                        "Error backfilling dialog",
                        extra={
                            "dialog_id": dialog.id,
                            "error": str(exc),
                        },
                    )

            logger.info(
                "Backfill complete",
                extra={
                    "backfilled_count": backfill_count,
                    "endpoint_identity": self._config.endpoint_identity,
                },
            )

        except Exception:
            logger.exception(
                "Error during backfill",
                extra={"endpoint_identity": self._config.endpoint_identity},
            )

    # -------------------------------------------------------------------------
    # Internal: Checkpoint persistence
    # -------------------------------------------------------------------------

    async def _load_checkpoint(self) -> None:
        """Load checkpoint from DB."""
        from butlers.connectors.cursor_store import load_cursor

        try:
            raw = await load_cursor(
                self._cursor_pool,
                "telegram_user_client",
                self._config.endpoint_identity,
            )
            if raw is not None:
                data = json.loads(raw)
                self._last_message_id = data.get("last_message_id")
                logger.info(
                    "Loaded checkpoint from DB",
                    extra={"last_message_id": self._last_message_id},
                )
            else:
                logger.info("No checkpoint in DB, starting from scratch")
        except Exception:
            logger.exception("Failed to load checkpoint from DB, starting from scratch")

    async def _save_checkpoint(self) -> None:
        """Persist checkpoint to DB."""
        try:
            from butlers.connectors.cursor_store import save_cursor

            await save_cursor(
                self._cursor_pool,
                "telegram_user_client",
                self._config.endpoint_identity,
                json.dumps({"last_message_id": self._last_message_id}),
            )
            self._last_checkpoint_save = time.time()
            logger.debug(
                "Saved checkpoint to DB",
                extra={"last_message_id": self._last_message_id},
            )
        except Exception:
            logger.exception("Failed to save checkpoint to DB")


async def _resolve_telegram_user_credentials_from_db() -> dict[str, str] | None:
    """Resolve Telegram user-client credentials from owner entity_info.

    Credentials are resolved exclusively from ``shared.entity_info`` entries
    on the owner entity (types ``telegram_api_id``, ``telegram_api_hash``,
    ``telegram_user_session``).

    Returns a dict with keys ``TELEGRAM_API_ID``, ``TELEGRAM_API_HASH``,
    ``TELEGRAM_USER_SESSION`` if all three are found, or ``None`` if:
    - No DB connection parameters are configured.
    - The DB is reachable but one or more entries are missing from entity_info.
    """
    import asyncpg

    db_params = db_params_from_env()
    local_db_name = os.environ.get("CONNECTOR_BUTLER_DB_NAME", "").strip()
    shared_db_name = shared_db_name_from_env()
    candidate_db_names: list[str] = []
    for name in [local_db_name, shared_db_name]:
        if name and name not in candidate_db_names:
            candidate_db_names.append(name)

    connected_pools: list[tuple[str, asyncpg.Pool]] = []
    connection_errors: list[str] = []
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
            connected_pools.append((db_name, pool))
        except Exception as exc:
            connection_errors.append(f"db={db_name}: {exc}")
            logger.warning(
                "DB connection failed during Telegram user-client credential resolution "
                "(db=%s): %s",
                db_name,
                exc,
            )

    if not connected_pools:
        logger.warning(
            "Telegram user-client connector: no DB connections succeeded "
            "(host=%s, port=%s, candidates=%s). Errors: %s",
            db_params.get("host"),
            db_params.get("port"),
            candidate_db_names,
            "; ".join(connection_errors) if connection_errors else "no candidates",
        )
        return None

    primary_db_name, primary_pool = connected_pools[0]

    # contact_info type → result dict key
    _CI_MAP: list[tuple[str, str]] = [
        ("telegram_api_id", "TELEGRAM_API_ID"),
        ("telegram_api_hash", "TELEGRAM_API_HASH"),
        ("telegram_user_session", "TELEGRAM_USER_SESSION"),
    ]

    try:
        result: dict[str, str] = {}

        for ci_type, result_key in _CI_MAP:
            value = await resolve_owner_entity_info(primary_pool, ci_type)
            if value:
                result[result_key] = value

        expected_keys = {k for _, k in _CI_MAP}
        if expected_keys <= result.keys():
            logger.info(
                "Telegram user-client connector: resolved credentials from owner entity_info "
                "(primary_db=%s)",
                primary_db_name,
            )
            return result

        missing = sorted(expected_keys - result.keys())
        logger.warning(
            "Telegram user-client connector: missing credential types in owner entity_info "
            "(primary_db=%s): %s",
            primary_db_name,
            missing,
        )
        return None
    except Exception as exc:
        logger.warning("Telegram user-client connector: DB credential lookup failed: %s", exc)
        return None
    finally:
        for _, pool in connected_pools:
            await pool.close()


async def _resolve_endpoint_identity(
    api_id: int,
    api_hash: str,
    session_string: str,
) -> str:
    """Connect to Telegram and resolve the authenticated user's identity.

    Returns ``telegram:user:@<username>`` by calling ``get_me()`` on
    a temporary Telethon client (falls back to numeric user ID if no username).
    The client is disconnected after the call.
    """
    session = StringSession(session_string)
    client = TelegramClient(session, api_id, api_hash)
    try:
        await client.connect()
        me = await client.get_me()
        if me is None:
            raise RuntimeError(
                "Telegram user-client connector: get_me() returned None — "
                "session may be expired or revoked"
            )
        username = getattr(me, "username", None)
        user_id = me.id
        if username:
            identity = f"telegram:user:@{username}"
        else:
            identity = f"telegram:user:{user_id}"
        logger.info(
            "Resolved Telegram endpoint identity from get_me()",
            extra={"user_id": user_id, "username": username, "identity": identity},
        )
        return identity
    finally:
        await client.disconnect()


async def run_telegram_user_client_connector() -> None:
    """CLI entry point for running Telegram user-client connector.

    Telegram user credentials (TELEGRAM_API_ID, TELEGRAM_API_HASH,
    TELEGRAM_USER_SESSION) are resolved exclusively from owner entity_info
    in the database.  Non-credential configuration (SWITCHBOARD_MCP_URL,
    CONNECTOR_* env vars) is read from environment variables.

    The endpoint identity is auto-inferred from ``get_me()``.
    """
    configure_logging(level="INFO", butler_name="telegram-user-client")

    # Step 1: Load non-credential config from env vars.
    config = TelegramUserClientConnectorConfig.from_env()

    # Step 2: Resolve credentials from owner entity_info (DB-only).
    # Always attempt — db_params_from_env() has sensible defaults (localhost).
    db_creds = await _resolve_telegram_user_credentials_from_db()

    if db_creds is None:
        raise RuntimeError(
            "Telegram user-client connector: could not resolve credentials from "
            "owner entity_info. Configure telegram_api_id, telegram_api_hash, "
            "and telegram_user_session on the owner entity via the dashboard."
        )

    try:
        api_id = int(db_creds["TELEGRAM_API_ID"])
    except ValueError as exc:
        raise ValueError(
            f"Telegram user-client connector: invalid TELEGRAM_API_ID from contact_info: {exc}"
        ) from exc

    api_hash = db_creds["TELEGRAM_API_HASH"]
    session_string = db_creds["TELEGRAM_USER_SESSION"]

    # Step 3: Resolve endpoint identity from get_me().
    endpoint_identity = await _resolve_endpoint_identity(api_id, api_hash, session_string)

    config = replace(
        config,
        telegram_api_id=api_id,
        telegram_api_hash=api_hash,
        telegram_user_session=session_string,
        endpoint_identity=endpoint_identity,
    )

    # Create cursor pool for DB-backed checkpoint persistence.
    from butlers.connectors.cursor_store import create_cursor_pool_from_env

    cursor_pool = await create_cursor_pool_from_env()
    logger.info("Telegram user-client connector: cursor pool created for DB-backed checkpoints")

    connector = TelegramUserClientConnector(config, db_pool=cursor_pool, cursor_pool=cursor_pool)

    try:
        await connector.start()
    except KeyboardInterrupt:
        logger.info("Received interrupt, stopping connector")
    finally:
        await connector.stop()
        if cursor_pool is not None:
            await cursor_pool.close()


if __name__ == "__main__":
    asyncio.run(run_telegram_user_client_connector())
