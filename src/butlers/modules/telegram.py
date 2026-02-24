"""Telegram module — Telegram MCP tools.

Ingestion is handled by ``TelegramBotConnector`` which submits messages through
the canonical ingest API. This module is output-only: it provides send/reply MCP
tools and webhook setup. Polling and pipeline wiring have been removed.

Configured via [modules.telegram] with optional
[modules.telegram.user] and [modules.telegram.bot] credential scopes in butler.toml.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from butlers.modules.base import Module

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}"
_ENV_VAR_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Reaction lifecycle emoji keys (map to emoji via REACTION_TO_EMOJI).
REACTION_IN_PROGRESS = ":eye"
REACTION_SUCCESS = ":done"
REACTION_FAILURE = ":space invader"

REACTION_TO_EMOJI = {
    REACTION_IN_PROGRESS: "\U0001f440",
    REACTION_SUCCESS: "\u2705",
    REACTION_FAILURE: "\U0001f47e",
}


def _validate_env_var_name(value: str, *, scope: str, field_name: str) -> str:
    """Validate a configured env var name for an identity credential field."""
    if not value or not value.strip():
        raise ValueError(
            f"modules.telegram.{scope}.{field_name} must be a non-empty environment variable name"
        )
    name = value.strip()
    if not _ENV_VAR_NAME_RE.fullmatch(name):
        raise ValueError(
            f"modules.telegram.{scope}.{field_name} must be a valid environment variable name "
            "(letters, numbers, underscores; cannot start with a number)"
        )
    return name


class TelegramUserCredentialsConfig(BaseModel):
    """Identity-scoped credentials for user Telegram operations."""

    enabled: bool = False
    token_env: str = "USER_TELEGRAM_TOKEN"
    model_config = ConfigDict(extra="forbid")

    @field_validator("token_env")
    @classmethod
    def _validate_token_env(cls, value: str) -> str:
        return _validate_env_var_name(value, scope="user", field_name="token_env")


class TelegramBotCredentialsConfig(BaseModel):
    """Identity-scoped credentials for bot Telegram operations."""

    enabled: bool = True
    token_env: str = "BUTLER_TELEGRAM_TOKEN"
    model_config = ConfigDict(extra="forbid")

    @field_validator("token_env")
    @classmethod
    def _validate_token_env(cls, value: str) -> str:
        return _validate_env_var_name(value, scope="bot", field_name="token_env")


class TelegramConfig(BaseModel):
    """Configuration for the Telegram module."""

    webhook_url: str | None = None
    user: TelegramUserCredentialsConfig = Field(default_factory=TelegramUserCredentialsConfig)
    bot: TelegramBotCredentialsConfig = Field(default_factory=TelegramBotCredentialsConfig)
    model_config = ConfigDict(extra="forbid")


class TelegramModule(Module):
    """Telegram module providing identity-prefixed Telegram MCP tools.

    Output-only: provides send/reply tools and webhook setup.
    Ingestion is owned by TelegramBotConnector via the canonical ingest API.
    """

    def __init__(self) -> None:
        self._config: TelegramConfig = TelegramConfig()
        self._client: httpx.AsyncClient | None = None
        self._db: Any = None
        # Credentials cached at startup via CredentialStore (DB-first, then env).
        # Keys are the env var names configured in TelegramConfig (e.g. "BUTLER_TELEGRAM_TOKEN").
        self._resolved_credentials: dict[str, str] = {}

    @property
    def name(self) -> str:
        return "telegram"

    @property
    def config_schema(self) -> type[BaseModel]:
        return TelegramConfig

    @property
    def dependencies(self) -> list[str]:
        return []

    @property
    def credentials_env(self) -> list[str]:
        """Environment variables required by this module."""
        envs: list[str] = []
        if self._config.bot.enabled:
            envs.append(self._config.bot.token_env)
        if self._config.user.enabled:
            envs.append(self._config.user.token_env)
        return envs

    def migration_revisions(self) -> str | None:
        return None  # No custom tables needed

    def _get_bot_token(self) -> str:
        """Resolve Telegram bot token — startup-cached store first, then environment variable.

        The token is pre-resolved at startup via CredentialStore (DB-first, then env)
        and cached in ``self._resolved_credentials``.  If not cached (e.g. tests
        without CredentialStore), falls back to ``os.environ`` directly.
        """
        if not self._config.bot.enabled:
            raise RuntimeError("Telegram bot scope modules.telegram.bot is disabled")
        token_env = self._config.bot.token_env
        # Use startup-resolved cache first; fall back to env vars for backwards compat.
        token = self._resolved_credentials.get(token_env) or os.environ.get(token_env)
        if not token:
            raise RuntimeError(
                f"Missing Telegram bot token for modules.telegram.bot: set {token_env}"
            )
        return token

    def _base_url(self) -> str:
        """Build the Telegram API base URL using the bot token."""
        token = self._get_bot_token()
        return TELEGRAM_API_BASE.format(token=token)

    def _get_client(self) -> httpx.AsyncClient:
        """Return the HTTP client, creating one if needed."""
        if self._client is None:
            self._client = httpx.AsyncClient()
        return self._client

    async def register_tools(self, mcp: Any, config: Any, db: Any) -> None:
        """Register Telegram send/reply MCP tools."""
        self._config = (
            config if isinstance(config, TelegramConfig) else TelegramConfig(**(config or {}))
        )
        module = self  # capture for closures

        async def telegram_send_message(chat_id: str, text: str) -> dict[str, Any]:
            """Send a Telegram message."""
            return await module._send_message(chat_id, text)

        async def telegram_reply_to_message(
            chat_id: str, message_id: int, text: str
        ) -> dict[str, Any]:
            """Reply to a Telegram message."""
            return await module._reply_to_message(chat_id, message_id, text)

        mcp.tool()(telegram_send_message)
        mcp.tool()(telegram_reply_to_message)

    async def on_startup(self, config: Any, db: Any, credential_store: Any = None) -> None:
        """Set webhook if configured. Ingestion is handled by TelegramBotConnector.

        Parameters
        ----------
        config:
            Module configuration (``TelegramConfig`` or raw dict).
        db:
            Butler database instance.
        credential_store:
            Optional :class:`~butlers.credential_store.CredentialStore`.
            When provided, tokens are resolved DB-first with env fallback.
            When ``None`` (e.g. tests), resolution falls back to env vars only.
        """
        self._config = (
            config if isinstance(config, TelegramConfig) else TelegramConfig(**(config or {}))
        )
        self._client = httpx.AsyncClient()
        self._db = db
        self._resolved_credentials = {}
        if credential_store is not None:
            # Resolve all configured token keys at startup and cache them.
            for scope_cfg in [self._config.bot, self._config.user]:
                token_env = scope_cfg.token_env
                value = await credential_store.resolve(token_env)
                if value is not None:
                    self._resolved_credentials[token_env] = value

        if self._config.webhook_url:
            await self._set_webhook(self._config.webhook_url)

    async def on_shutdown(self) -> None:
        """Clean up: close HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def react_for_ingest(
        self,
        *,
        external_thread_id: str | None,
        reaction: str,
    ) -> None:
        """Set a Telegram reaction for a message received via the ingest pipeline.

        Called by the daemon's ingest→pipeline flow to fire lifecycle reactions
        (👀 on receive, ✅ on success, 👾 on error) when messages arrive through
        the external TelegramBotConnector → MCP ingest path.

        Parses the ``external_thread_id`` from the ingest.v1 envelope
        (format: ``"<chat_id>:<message_id>"``).  No-ops silently when the
        thread identity cannot be resolved to a valid chat/message pair.

        Parameters
        ----------
        external_thread_id:
            The ``event.external_thread_id`` field from the ingest.v1 envelope.
            Expected format: ``"<chat_id>:<message_id>"`` where message_id is an
            integer.  If ``None`` or unparseable, the call is a no-op.
        reaction:
            One of the ``REACTION_*`` constants (e.g. ``REACTION_IN_PROGRESS``).
        """
        if not external_thread_id:
            return

        # Parse "chat_id:message_id" — the format written by TelegramBotConnector.
        try:
            chat_str, sep, message_str = external_thread_id.partition(":")
            if not sep or not chat_str or not message_str:
                return
            message_id = int(message_str)
        except (ValueError, AttributeError):
            return

        try:
            await self._set_message_reaction(
                chat_id=chat_str,
                message_id=message_id,
                reaction=reaction,
            )
        except Exception:
            logger.debug(
                "react_for_ingest: failed to set reaction %r for %s",
                reaction,
                external_thread_id,
            )

    # ------------------------------------------------------------------
    # Telegram API helpers
    # ------------------------------------------------------------------

    async def _send_message(
        self, chat_id: str, text: str, reply_to_message_id: int | None = None
    ) -> dict[str, Any]:
        """Call Telegram sendMessage API."""
        url = f"{self._base_url()}/sendMessage"
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if reply_to_message_id is not None:
            payload["reply_to_message_id"] = reply_to_message_id
        client = self._get_client()
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        return data

    async def _reply_to_message(self, chat_id: str, message_id: int, text: str) -> dict[str, Any]:
        """Reply to a specific Telegram message."""
        return await self._send_message(chat_id, text, reply_to_message_id=message_id)

    async def _set_webhook(self, url: str) -> dict[str, Any]:
        """Call Telegram setWebhook API."""
        api_url = f"{self._base_url()}/setWebhook"
        client = self._get_client()
        resp = await client.post(api_url, json={"url": url})
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        return data

    async def _delete_webhook(self) -> dict[str, Any]:
        """Call Telegram deleteWebhook API."""
        url = f"{self._base_url()}/deleteWebhook"
        client = self._get_client()
        resp = await client.post(url)
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        return data

    async def _set_message_reaction(
        self, *, chat_id: str, message_id: int, reaction: str
    ) -> dict[str, Any]:
        """Call Telegram setMessageReaction API with a mapped lifecycle reaction emoji."""
        emoji = REACTION_TO_EMOJI[reaction]
        url = f"{self._base_url()}/setMessageReaction"
        client = self._get_client()
        resp = await client.post(
            url,
            json={
                "chat_id": chat_id,
                "message_id": message_id,
                "reaction": [{"type": "emoji", "emoji": emoji}],
            },
        )
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        return data


def _extract_text(update: dict[str, Any]) -> str | None:
    """Extract message text from a Telegram update.

    Handles regular messages, edited messages, and channel posts.
    """
    for key in ("message", "edited_message", "channel_post"):
        msg = update.get(key)
        if msg and isinstance(msg, dict):
            text = msg.get("text")
            if text:
                return text
    return None


def _extract_chat_id(update: dict[str, Any]) -> str | None:
    """Extract chat ID from a Telegram update."""
    for key in ("message", "edited_message", "channel_post"):
        msg = update.get(key)
        if msg and isinstance(msg, dict):
            chat = msg.get("chat")
            if chat and isinstance(chat, dict):
                return str(chat.get("id", ""))
    return None


def _extract_message_id(update: dict[str, Any]) -> int | None:
    """Extract Telegram message ID from an update payload."""
    for key in ("message", "edited_message", "channel_post"):
        msg = update.get(key)
        if msg and isinstance(msg, dict):
            message_id = msg.get("message_id")
            if message_id is None:
                continue
            try:
                return int(message_id)
            except (TypeError, ValueError):
                return None
    return None
