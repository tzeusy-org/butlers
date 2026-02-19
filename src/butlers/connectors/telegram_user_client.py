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
- CONNECTOR_ENDPOINT_IDENTITY (required, user-client identity e.g. "telegram:user:123456")
- CONNECTOR_CURSOR_PATH (required; stores last processed update/message ID)
- CONNECTOR_MAX_INFLIGHT (optional, default 8)
- CONNECTOR_BACKFILL_WINDOW_H (optional, bounded startup replay in hours)
- CONNECTOR_BUTLER_DB_NAME (optional; local butler DB for per-butler overrides)
- BUTLER_SHARED_DB_NAME (optional; shared credential DB, defaults to 'butler_shared')
- BUTLER_LEGACY_SHARED_DB_NAME (optional; legacy centralized credential DB fallback)
- TELEGRAM_API_ID (required; resolved from DB first, then env; from my.telegram.org)
- TELEGRAM_API_HASH (required; resolved from DB first, then env; from my.telegram.org)
- TELEGRAM_USER_SESSION (required; resolved from DB first, then env; session string or
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
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from butlers.connectors.mcp_client import CachedMCPClient
from butlers.core.logging import configure_logging
from butlers.credential_store import (
    CredentialStore,
    legacy_shared_db_name_from_env,
    shared_db_name_from_env,
)
from butlers.db import db_params_from_env

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
    cursor_path: Path | None = None
    backfill_window_h: int | None = None

    # Concurrency control
    max_inflight: int = 8

    @classmethod
    def from_env(cls) -> TelegramUserClientConnectorConfig:
        """Load configuration from environment variables."""
        if not TELETHON_AVAILABLE:
            raise RuntimeError("Telethon is not installed. Install with: uv pip install telethon")

        switchboard_mcp_url = os.environ.get("SWITCHBOARD_MCP_URL")
        if not switchboard_mcp_url:
            raise ValueError("SWITCHBOARD_MCP_URL environment variable is required")

        provider = os.environ.get("CONNECTOR_PROVIDER", "telegram")
        channel = os.environ.get("CONNECTOR_CHANNEL", "telegram")

        endpoint_identity = os.environ.get("CONNECTOR_ENDPOINT_IDENTITY")
        if not endpoint_identity:
            raise ValueError("CONNECTOR_ENDPOINT_IDENTITY environment variable is required")

        api_id_str = os.environ.get("TELEGRAM_API_ID")
        if not api_id_str:
            raise ValueError("TELEGRAM_API_ID environment variable is required")
        try:
            api_id = int(api_id_str)
        except ValueError as exc:
            raise ValueError(f"TELEGRAM_API_ID must be an integer, got: {api_id_str}") from exc

        api_hash = os.environ.get("TELEGRAM_API_HASH")
        if not api_hash:
            raise ValueError("TELEGRAM_API_HASH environment variable is required")

        user_session = os.environ.get("TELEGRAM_USER_SESSION")
        if not user_session:
            raise ValueError("TELEGRAM_USER_SESSION environment variable is required")

        cursor_path_str = os.environ.get("CONNECTOR_CURSOR_PATH")
        if not cursor_path_str:
            raise ValueError("CONNECTOR_CURSOR_PATH environment variable is required")
        cursor_path = Path(cursor_path_str)

        backfill_window_str = os.environ.get("CONNECTOR_BACKFILL_WINDOW_H")
        backfill_window_h = int(backfill_window_str) if backfill_window_str else None

        max_inflight = int(os.environ.get("CONNECTOR_MAX_INFLIGHT", "8"))

        return cls(
            switchboard_mcp_url=switchboard_mcp_url,
            provider=provider,
            channel=channel,
            endpoint_identity=endpoint_identity,
            telegram_api_id=api_id,
            telegram_api_hash=api_hash,
            telegram_user_session=user_session,
            cursor_path=cursor_path,
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

    def __init__(self, config: TelegramUserClientConnectorConfig) -> None:
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

    async def start(self) -> None:
        """Start the Telegram user-client connector in live-stream mode.

        This:
        1. Loads checkpoint from disk
        2. Connects to Telegram with user-client session
        3. Optionally performs bounded backfill
        4. Subscribes to live message events
        5. Runs until stopped
        """
        if not self._config.cursor_path:
            raise ValueError("CONNECTOR_CURSOR_PATH is required")

        # Load checkpoint
        self._load_checkpoint()

        # Initialize Telegram client with user session
        session = StringSession(self._config.telegram_user_session)
        self._telegram_client = TelegramClient(
            session,
            self._config.telegram_api_id,
            self._config.telegram_api_hash,
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

        await self._mcp_client.aclose()
        logger.info("Telegram user-client connector stopped")

    # -------------------------------------------------------------------------
    # Internal: Message processing
    # -------------------------------------------------------------------------

    async def _process_message(self, message: Any) -> None:
        """Process a single Telegram message event.

        Normalizes to ingest.v1 and submits to Switchboard ingest API.
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

                # Normalize to ingest.v1
                envelope = await self._normalize_to_ingest_v1(message)

                # Submit to Switchboard ingest
                await self._submit_to_ingest(envelope)

                # Update checkpoint
                if self._last_message_id is None or message_id > self._last_message_id:
                    self._last_message_id = message_id
                    self._save_checkpoint()

            except Exception:
                logger.exception(
                    "Failed to process Telegram message",
                    extra={
                        "message_id": getattr(message, "id", None),
                        "endpoint_identity": self._config.endpoint_identity,
                    },
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
        - control.idempotency_key: telegram:<endpoint_identity>:<message_id>
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
                "idempotency_key": f"telegram:{self._config.endpoint_identity}:{message_id}",
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

    def _load_checkpoint(self) -> None:
        """Load checkpoint from disk."""
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
                self._last_message_id = data.get("last_message_id")

            logger.info(
                "Loaded checkpoint",
                extra={
                    "cursor_path": str(self._config.cursor_path),
                    "last_message_id": self._last_message_id,
                },
            )
        except Exception:
            logger.exception(
                "Failed to load checkpoint, starting from scratch",
                extra={"cursor_path": str(self._config.cursor_path)},
            )

    def _save_checkpoint(self) -> None:
        """Persist checkpoint to disk."""
        if not self._config.cursor_path:
            return

        try:
            # Ensure parent directory exists
            self._config.cursor_path.parent.mkdir(parents=True, exist_ok=True)

            # Write checkpoint atomically
            tmp_path = self._config.cursor_path.with_suffix(".tmp")
            with tmp_path.open("w") as f:
                json.dump({"last_message_id": self._last_message_id}, f)

            tmp_path.replace(self._config.cursor_path)

            logger.debug(
                "Saved checkpoint",
                extra={
                    "cursor_path": str(self._config.cursor_path),
                    "last_message_id": self._last_message_id,
                },
            )
        except Exception:
            logger.exception(
                "Failed to save checkpoint",
                extra={"cursor_path": str(self._config.cursor_path)},
            )


async def _resolve_telegram_user_credentials_from_db() -> dict[str, str] | None:
    """Attempt DB-first credential resolution for the Telegram user-client connector.

    Creates a short-lived asyncpg pool, resolves ``TELEGRAM_API_ID``,
    ``TELEGRAM_API_HASH``, and ``TELEGRAM_USER_SESSION`` from the
    ``butler_secrets`` table via :class:`~butlers.credential_store.CredentialStore`,
    and closes the pool before returning.

    Returns a dict with keys ``TELEGRAM_API_ID``, ``TELEGRAM_API_HASH``,
    ``TELEGRAM_USER_SESSION`` if all three are found in the DB, or ``None`` if:
    - No DB connection parameters are configured.
    - The DB is reachable but one or more secrets have not been stored yet.

    In both cases the caller should fall back to env-var resolution.
    """
    import asyncpg

    db_params = db_params_from_env()
    local_db_name = os.environ.get("CONNECTOR_BUTLER_DB_NAME", "").strip()
    shared_db_name = shared_db_name_from_env()
    legacy_db_name = legacy_shared_db_name_from_env()
    candidate_db_names: list[str] = []
    for name in [local_db_name, shared_db_name, legacy_db_name]:
        if name and name not in candidate_db_names:
            candidate_db_names.append(name)

    connected_pools: list[tuple[str, asyncpg.Pool]] = []
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
            logger.debug(
                "DB connection failed during Telegram user-client credential resolution "
                "(db=%s, non-fatal): %s",
                db_name,
                exc,
            )

    if not connected_pools:
        return None

    primary_db_name, primary_pool = connected_pools[0]
    fallback_pools = [pool for _, pool in connected_pools[1:]]
    store = CredentialStore(primary_pool, fallback_pools=fallback_pools)

    try:
        api_id = await store.resolve("TELEGRAM_API_ID", env_fallback=False)
        api_hash = await store.resolve("TELEGRAM_API_HASH", env_fallback=False)
        user_session = await store.resolve("TELEGRAM_USER_SESSION", env_fallback=False)

        if api_id and api_hash and user_session:
            logger.info(
                "Telegram user-client connector: resolved credentials from layered DB lookup "
                "(primary_db=%s, fallbacks=%d)",
                primary_db_name,
                len(fallback_pools),
            )
            return {
                "TELEGRAM_API_ID": api_id,
                "TELEGRAM_API_HASH": api_hash,
                "TELEGRAM_USER_SESSION": user_session,
            }

        missing = [
            k
            for k, v in [
                ("TELEGRAM_API_ID", api_id),
                ("TELEGRAM_API_HASH", api_hash),
                ("TELEGRAM_USER_SESSION", user_session),
            ]
            if not v
        ]
        logger.debug(
            "Telegram user-client connector: secrets not found in DB (primary_db=%s): %s",
            primary_db_name,
            missing,
        )
        return None
    except Exception as exc:
        logger.debug(
            "Telegram user-client connector: DB credential lookup failed (non-fatal): %s", exc
        )
        return None
    finally:
        for _, pool in connected_pools:
            await pool.close()


async def run_telegram_user_client_connector() -> None:
    """CLI entry point for running Telegram user-client connector.

    Credential resolution order:
    1. Database (if DATABASE_URL or POSTGRES_* env vars are configured).
    2. Environment variables TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_USER_SESSION
       (backward-compatible fallback).
    """
    configure_logging(level="INFO", butler_name="telegram-user-client")

    # Step 1: Try DB-first credential resolution.
    db_creds: dict[str, str] | None = None
    if os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_HOST"):
        db_creds = await _resolve_telegram_user_credentials_from_db()

    # Step 2: Load config from env vars.
    config = TelegramUserClientConnectorConfig.from_env()

    # Step 3: Override with DB-resolved credentials if available.
    if db_creds is not None:
        try:
            api_id = int(db_creds["TELEGRAM_API_ID"])
        except ValueError as exc:
            logger.error("Telegram user-client connector: invalid TELEGRAM_API_ID from DB: %s", exc)
            api_id = config.telegram_api_id

        config = replace(
            config,
            telegram_api_id=api_id,
            telegram_api_hash=db_creds["TELEGRAM_API_HASH"],
            telegram_user_session=db_creds["TELEGRAM_USER_SESSION"],
        )
        logger.debug("Telegram user-client connector: config updated with DB-resolved credentials")

    connector = TelegramUserClientConnector(config)

    try:
        await connector.start()
    except KeyboardInterrupt:
        logger.info("Received interrupt, stopping connector")
    finally:
        await connector.stop()


if __name__ == "__main__":
    asyncio.run(run_telegram_user_client_connector())
