"""WhatsApp module — WhatsApp MCP tools via Go bridge sidecar.

Uses a two-layer gating model:
- ``send_tools`` (registration-time): controls whether send/reply tools are
  registered in the MCP schema at all (following the email module pattern).
  Only the Messenger butler sets ``send_tools = true``.
- ``send_enabled`` (runtime): controls whether registered send tools actually
  execute. Default ``false`` so tools are present but refuse to execute until
  ban risk is assessed.

Credentials are resolved exclusively from owner entity_info (DB-only).
The Go bridge sidecar is managed via BridgeSubprocessManager.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from butlers.connectors.bridge_manager import BridgeConfig, BridgeSubprocessManager
from butlers.credential_store import resolve_owner_entity_info
from butlers.modules.base import Module

logger = logging.getLogger(__name__)

_SEND_DISABLED_ERROR = (
    "WhatsApp sending is disabled. Set modules.whatsapp.send_enabled=true in butler.toml "
    "to enable. WARNING: Sending via unofficial WhatsApp clients carries ban risk."
)


class WhatsAppUserCredentialScope(BaseModel):
    """Credential scope for user-based WhatsApp operations."""

    enabled: bool = True
    session_env: str = "WHATSAPP_USER_SESSION"
    model_config = ConfigDict(extra="forbid")


class WhatsAppConfig(BaseModel):
    """Configuration for the WhatsApp module.

    Two-layer send gating:

    - ``send_tools`` (bool, default ``false``) — controls whether send/reply
      tools are registered at all (registration-time). Set ``true`` only for
      the Messenger butler.
    - ``send_enabled`` (bool, default ``false``) — controls whether registered
      send tools actually execute (runtime gate). Default ``false`` so the
      Messenger butler ships with tools present but functionally disabled.
    - ``bridge_socket`` (str) — Unix socket path to the Go bridge sidecar.

    Setting ``send_enabled=true`` with ``send_tools=false`` is a configuration
    error raised at startup.
    """

    send_tools: bool = False
    send_enabled: bool = False
    bridge_socket: str = "/tmp/wa-bridge.sock"
    user: WhatsAppUserCredentialScope = Field(default_factory=WhatsAppUserCredentialScope)
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _validate_send_gating(self) -> WhatsAppConfig:
        if self.send_enabled and not self.send_tools:
            raise ValueError(
                "Cannot enable sending without send_tools=true. "
                "Set send_tools=true to register send tools."
            )
        return self


class WhatsAppModule(Module):
    """WhatsApp module providing send/reply MCP tools via the Go bridge sidecar.

    Uses a two-layer gating model: ``send_tools`` controls tool registration,
    ``send_enabled`` controls runtime execution.  Only the Messenger butler
    should set ``send_tools = true``.

    The Go whatsapp-bridge binary is managed via BridgeSubprocessManager.
    Session persistence is owned by the bridge (reads/writes whatsapp_sessions
    table directly); the module does not own database tables.
    """

    def __init__(self) -> None:
        self._config: WhatsAppConfig = WhatsAppConfig()
        self._bridge_manager: BridgeSubprocessManager | None = None
        self._whatsapp_phone: str | None = None

    @property
    def name(self) -> str:
        return "whatsapp"

    @property
    def config_schema(self) -> type[BaseModel]:
        return WhatsAppConfig

    @property
    def dependencies(self) -> list[str]:
        return []

    def migration_revisions(self) -> str | None:
        """No custom tables — bridge manages sessions, connector manages messages."""
        return None

    async def register_tools(self, mcp: Any, config: Any, db: Any) -> None:
        """Register WhatsApp MCP tools.

        Send/reply tools are only registered when ``send_tools = true`` in the
        module config.  When registered, each tool checks ``send_enabled`` at
        execution time and returns an error message if disabled.
        """
        self._config = (
            config if isinstance(config, WhatsAppConfig) else WhatsAppConfig(**(config or {}))
        )
        module = self  # capture for closures

        if self._config.send_tools:

            @mcp.tool()
            async def whatsapp_send_message(recipient: str, text: str) -> dict:
                """Send a WhatsApp message to a chat by JID or phone number."""
                if not module._config.send_enabled:
                    return {"error": _SEND_DISABLED_ERROR}
                return await module._send_message(recipient=recipient, text=text)

            @mcp.tool()
            async def whatsapp_reply_to_message(chat_jid: str, message_id: str, text: str) -> dict:
                """Reply to a specific WhatsApp message in a chat."""
                if not module._config.send_enabled:
                    return {"error": _SEND_DISABLED_ERROR}
                return await module._reply_to_message(
                    chat_jid=chat_jid, message_id=message_id, text=text
                )

    async def on_startup(self, config: Any, db: Any, credential_store: Any = None) -> None:
        """Initialize module config, resolve credentials, start Go bridge.

        Steps:
        1. Parse and validate configuration.
        2. Resolve ``whatsapp_phone`` from owner entity_info (log warning on miss).
        3. Start the Go bridge sidecar via BridgeSubprocessManager.
        4. Wait up to 30s for the bridge to report 'connected'.

        Raises:
            RuntimeError: If the whatsapp-bridge binary is not found in $PATH.
            TimeoutError: If the bridge does not reach 'connected' within 30s.
        """
        self._config = (
            config if isinstance(config, WhatsAppConfig) else WhatsAppConfig(**(config or {}))
        )

        # Resolve whatsapp_phone from owner entity_info (DB-only, no env fallback).
        pool = getattr(db, "pool", None) if db is not None else None
        if pool is not None:
            phone = await resolve_owner_entity_info(pool, "whatsapp_phone")
            if phone is not None:
                self._whatsapp_phone = phone
                logger.info("WhatsApp module: resolved owner phone %s", phone)
            else:
                logger.warning(
                    "WhatsApp module: whatsapp_phone not found in owner entity_info; "
                    "send/reply tools will return credential errors until configured"
                )
        else:
            logger.debug("WhatsApp module: no DB pool available; skipping credential resolution")

        # Build bridge args for --db-dsn and --listen.
        bridge_args: list[str] = []
        dsn = _get_db_dsn(db)
        if dsn:
            bridge_args.extend(["--db-dsn", dsn])
        bridge_args.extend(["--listen", f"unix://{self._config.bridge_socket}"])

        bridge_cfg = BridgeConfig(
            binary="whatsapp-bridge",
            args=bridge_args,
            bridge_socket=self._config.bridge_socket,
            startup_timeout_s=30.0,
            health_poll_interval_s=30.0,
        )
        self._bridge_manager = BridgeSubprocessManager(bridge_cfg)

        # BridgeSubprocessManager.start() raises RuntimeError for missing binary
        # and TimeoutError if the bridge does not connect within startup_timeout_s.
        await self._bridge_manager.start()
        logger.info("WhatsApp module: bridge started and connected")

    async def on_shutdown(self) -> None:
        """Gracefully shut down the Go bridge sidecar."""
        if self._bridge_manager is not None:
            await self._bridge_manager.stop()
            self._bridge_manager = None
            logger.info("WhatsApp module: bridge stopped")

    # ------------------------------------------------------------------
    # Implementation helpers
    # ------------------------------------------------------------------

    async def _send_message(self, *, recipient: str, text: str) -> dict:
        """POST /send to the Go bridge to deliver a WhatsApp message."""
        if self._bridge_manager is None or not self._bridge_manager.is_running:
            return {
                "error": "WhatsApp bridge is not running. "
                "Check bridge health and whatsapp_phone configuration."
            }
        if self._bridge_manager.is_degraded:
            reason = self._bridge_manager.degraded_reason or "unknown"
            return {
                "error": f"WhatsApp bridge is in degraded mode: {reason}. "
                "Re-pairing may be required."
            }

        from butlers.connectors.bridge_manager import _http_post_unix_with_body  # noqa: PLC0415

        payload = {"recipient": recipient, "text": text}
        try:
            result = await _http_post_unix_with_body(self._config.bridge_socket, "/send", payload)
        except Exception as exc:
            logger.error("WhatsApp send failed: %s", type(exc).__name__)
            return {"error": "WhatsApp send failed — check bridge health and configuration"}
        return result

    async def _reply_to_message(self, *, chat_jid: str, message_id: str, text: str) -> dict:
        """POST /send with reply_to field to the Go bridge."""
        if self._bridge_manager is None or not self._bridge_manager.is_running:
            return {
                "error": "WhatsApp bridge is not running. "
                "Check bridge health and whatsapp_phone configuration."
            }
        if self._bridge_manager.is_degraded:
            reason = self._bridge_manager.degraded_reason or "unknown"
            return {
                "error": f"WhatsApp bridge is in degraded mode: {reason}. "
                "Re-pairing may be required."
            }

        from butlers.connectors.bridge_manager import _http_post_unix_with_body  # noqa: PLC0415

        payload = {"recipient": chat_jid, "text": text, "reply_to": message_id}
        try:
            result = await _http_post_unix_with_body(self._config.bridge_socket, "/send", payload)
        except Exception as exc:
            logger.error("WhatsApp reply failed: %s", type(exc).__name__)
            return {"error": "WhatsApp reply failed — check bridge health and configuration"}
        return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_db_dsn(db: Any) -> str | None:
    """Extract the PostgreSQL DSN from the butler DB object, if available."""
    if db is None:
        return None
    # Try common attribute patterns used by butler DB objects.
    for attr in ("dsn", "db_dsn", "_dsn"):
        val = getattr(db, attr, None)
        if isinstance(val, str) and val:
            return val
    return None
