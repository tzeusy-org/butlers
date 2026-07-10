"""WhatsApp module — WhatsApp send/reply MCP tools over the connector-owned bridge.

Uses a two-layer gating model:
- ``send_tools`` (registration-time): controls whether send/reply tools are
  registered in the MCP schema at all (following the email module pattern).
  Only the Messenger butler sets ``send_tools = true``.
- ``send_enabled`` (runtime): controls whether registered send tools actually
  execute. Default ``false`` so tools are present but refuse to execute until
  ban risk is assessed.

Bridge ownership (bu-0c69e): the ``connector-whatsapp-user`` process is the
**sole owner** of the authenticated whatsapp-bridge sidecar and the whatsmeow
device session. This module is a *client* of that bridge — its send/reply tools
POST to the connector-owned Unix socket (shared via the ``wa_bridge_socket``
Docker volume, default ``/tmp/wa-bridge/bridge.sock``), exactly as the dashboard
API's pair/status endpoints do. It never spawns its own bridge subprocess, so
enabling outbound delivery can never authenticate a second client against the
same account (which would trigger WhatsApp's StreamReplaced and degrade ingress).
"""

from __future__ import annotations

import logging
import os
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from butlers.modules.base import Module

logger = logging.getLogger(__name__)

_SEND_DISABLED_ERROR = (
    "WhatsApp sending is disabled. Set modules.whatsapp.send_enabled=true in butler.toml "
    "to enable. WARNING: Sending via unofficial WhatsApp clients carries ban risk."
)

# Shared connector-owned bridge socket. The default matches the compose
# ``wa_bridge_socket`` volume mount; ``WHATSAPP_BRIDGE_SOCKET`` overrides it (the
# same env var the dashboard WhatsApp router reads — kept in lockstep so the two
# client surfaces always target the one bridge the connector owns).
_DEFAULT_BRIDGE_SOCKET = "/tmp/wa-bridge/bridge.sock"


def _default_bridge_socket() -> str:
    """Resolve the connector-owned bridge socket path from the environment."""
    return os.environ.get("WHATSAPP_BRIDGE_SOCKET", _DEFAULT_BRIDGE_SOCKET)


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
    - ``bridge_socket`` (str) — Unix socket path to the **connector-owned** Go
      bridge sidecar. Defaults to ``WHATSAPP_BRIDGE_SOCKET`` (compose sets this
      to the shared ``/tmp/wa-bridge/bridge.sock`` volume path). This module
      does not spawn a bridge; it POSTs send/reply requests to the socket the
      ``whatsapp_user_client`` connector already listens on.

    Setting ``send_enabled=true`` with ``send_tools=false`` is a configuration
    error raised at startup.
    """

    send_tools: bool = False
    send_enabled: bool = False
    bridge_socket: str = Field(default_factory=_default_bridge_socket)
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
    """WhatsApp module providing send/reply MCP tools over the connector's bridge.

    Uses a two-layer gating model: ``send_tools`` controls tool registration,
    ``send_enabled`` controls runtime execution.  Only the Messenger butler
    should set ``send_tools = true``.

    This module does **not** own a whatsapp-bridge subprocess. The
    ``whatsapp_user_client`` connector is the single owner of the authenticated
    bridge and the whatsmeow session; this module's tools issue send/reply
    requests to that connector-owned Unix socket. It owns no database tables.
    """

    def __init__(self) -> None:
        self._config: WhatsAppConfig = WhatsAppConfig()

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

    async def register_tools(self, mcp: Any, config: Any, db: Any, butler_name: str) -> None:
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

    async def on_startup(
        self, config: Any, db: Any, credential_store: Any = None, blob_store: Any = None
    ) -> None:
        """Parse configuration and record the connector-owned bridge socket.

        This module does not spawn or manage a whatsapp-bridge subprocess — the
        ``whatsapp_user_client`` connector owns the single authenticated bridge
        (bu-0c69e). Startup therefore performs no blocking bridge wait and never
        authenticates a WhatsApp session; send/reply tools talk to the
        connector-owned socket on demand.
        """
        self._config = (
            config if isinstance(config, WhatsAppConfig) else WhatsAppConfig(**(config or {}))
        )
        if self._config.send_tools:
            logger.info(
                "WhatsApp module: send tools registered; routing to connector-owned bridge at %s "
                "(send_enabled=%s)",
                self._config.bridge_socket,
                self._config.send_enabled,
            )

    async def on_shutdown(self) -> None:
        """No-op: this module owns no bridge subprocess to tear down."""
        return None

    # ------------------------------------------------------------------
    # Implementation helpers
    # ------------------------------------------------------------------

    async def _check_bridge_ready(self) -> str | None:
        """Probe the connector-owned bridge ``/status``.

        Returns ``None`` when the bridge is reachable and its WhatsApp link is
        live, or an actionable error string otherwise. The module holds no
        BridgeSubprocessManager, so liveness is read from the connector's bridge
        over the shared socket rather than from a locally-owned process.
        """
        from butlers.connectors.bridge_manager import _http_get_unix  # noqa: PLC0415

        try:
            status = await _http_get_unix(self._config.bridge_socket, "/status")
        except Exception:
            return (
                f"WhatsApp bridge is not reachable at {self._config.bridge_socket}. "
                "The whatsapp_user_client connector owns the bridge — ensure it is running "
                "and the shared socket volume is mounted."
            )

        # connected/logged_in are the bridge's authoritative liveness fields
        # (see docs/whatsapp-setup.md §2.1); state can lag a missed event.
        linked = bool(status.get("connected")) and bool(status.get("logged_in"))
        if not linked and status.get("state") != "connected":
            state = status.get("state") or "unknown"
            return (
                f"WhatsApp bridge is not connected (state={state}). Re-pairing may be "
                "required — open the dashboard WhatsApp settings."
            )
        return None

    async def _send_message(self, *, recipient: str, text: str) -> dict:
        """POST /send to the connector-owned bridge to deliver a WhatsApp message."""
        error = await self._check_bridge_ready()
        if error is not None:
            return {"error": error}

        from butlers.connectors.bridge_manager import _http_post_unix_with_body  # noqa: PLC0415

        payload = {"recipient": recipient, "text": text}
        try:
            result = await _http_post_unix_with_body(self._config.bridge_socket, "/send", payload)
        except Exception as exc:
            logger.error("WhatsApp send failed: %s", type(exc).__name__)
            return {"error": "WhatsApp send failed — check bridge health and configuration"}
        return result

    async def _reply_to_message(self, *, chat_jid: str, message_id: str, text: str) -> dict:
        """POST /send with reply_to field to the connector-owned bridge."""
        error = await self._check_bridge_ready()
        if error is not None:
            return {"error": error}

        from butlers.connectors.bridge_manager import _http_post_unix_with_body  # noqa: PLC0415

        payload = {"recipient": chat_jid, "text": text, "reply_to": message_id}
        try:
            result = await _http_post_unix_with_body(self._config.bridge_socket, "/send", payload)
        except Exception as exc:
            logger.error("WhatsApp reply failed: %s", type(exc).__name__)
            return {"error": "WhatsApp reply failed — check bridge health and configuration"}
        return result
