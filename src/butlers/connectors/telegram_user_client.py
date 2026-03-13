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
- CONNECTOR_CHANNEL=telegram (required)
- CONNECTOR_MAX_INFLIGHT (optional, default 8)
- CONNECTOR_BACKFILL_WINDOW_H (optional, bounded startup replay in hours)
- CONNECTOR_BUTLER_DB_NAME (optional; local butler DB for per-butler overrides)
- BUTLER_SHARED_DB_NAME (optional; shared credential DB, defaults to 'butlers')
- TELEGRAM_API_ID (required; resolved from owner entity_info only; from my.telegram.org)
- TELEGRAM_API_HASH (required; resolved from owner entity_info only; from my.telegram.org)
- TELEGRAM_USER_SESSION (required; resolved from owner entity_info only; session string or
  encrypted file path)

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


@dataclass
class TelegramUserClientConnectorConfig:
    """Configuration for Telegram user-client connector runtime."""

    # Switchboard MCP config
    switchboard_mcp_url: str

    # Connector identity
    provider: str = "telegram"
    channel: str = "telegram"
    endpoint_identity: str = field(default="")

    # Telegram user-client credentials (MTProto)
    telegram_api_id: int = 0
    telegram_api_hash: str = field(default="")
    telegram_user_session: str = field(default="")

    # State/checkpoint config
    backfill_window_h: int | None = None

    # Concurrency control
    max_inflight: int = 8

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
        channel = os.environ.get("CONNECTOR_CHANNEL", "telegram")

        backfill_window_str = os.environ.get("CONNECTOR_BACKFILL_WINDOW_H")
        backfill_window_h = int(backfill_window_str) if backfill_window_str else None

        max_inflight = int(os.environ.get("CONNECTOR_MAX_INFLIGHT", "8"))

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

            # Optional: bounded backfill on startup
            if self._config.backfill_window_h:
                await self._perform_backfill()

            # Register live message handler
            @self._telegram_client.on(events.NewMessage)
            async def handle_new_message(event: events.NewMessage.Event) -> None:
                """Handle new message events from Telegram."""
                await self._process_message(event.message)

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
            source_channel="telegram",
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
        - source.channel: "telegram"
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
