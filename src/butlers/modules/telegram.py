"""Telegram module — identity-prefixed Telegram MCP tools.

Supports polling mode (dev, no public URL needed) and webhook mode (production).
Configured via [modules.telegram] with optional
[modules.telegram.user] and [modules.telegram.bot] credential scopes in butler.toml.

When a ``MessagePipeline`` is attached, incoming messages from polling are
automatically classified and routed to the appropriate butler via the
switchboard.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from butlers.modules.base import Module, ToolIODescriptor
from butlers.modules.pipeline import MessagePipeline, RoutingResult

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}"
_ENV_VAR_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


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


REACTION_IN_PROGRESS = ":eye"
REACTION_SUCCESS = ":done"
REACTION_FAILURE = ":space invader"
REACTION_TO_EMOJI = {
    REACTION_IN_PROGRESS: "\U0001f440",
    REACTION_SUCCESS: "\u2705",
    REACTION_FAILURE: "\U0001f47e",
}


@dataclass
class ProcessingLifecycle:
    """Tracks per-message routing progress and terminal reaction state."""

    routed_targets: set[str] = field(default_factory=set)
    acked_targets: set[str] = field(default_factory=set)
    failed_targets: set[str] = field(default_factory=set)
    terminal_reaction: str | None = None


class TelegramConfig(BaseModel):
    """Configuration for the Telegram module."""

    mode: str = "polling"  # "polling" or "webhook"
    webhook_url: str | None = None
    poll_interval: float = 1.0
    user: TelegramUserCredentialsConfig = Field(default_factory=TelegramUserCredentialsConfig)
    bot: TelegramBotCredentialsConfig = Field(default_factory=TelegramBotCredentialsConfig)
    model_config = ConfigDict(extra="forbid")


class TelegramModule(Module):
    """Telegram module providing identity-prefixed Telegram MCP tools.

    When a ``MessagePipeline`` is set via ``set_pipeline()``, incoming
    Telegram messages are forwarded through ``classify_message()`` then
    ``route()`` to the appropriate butler.
    """

    def __init__(self) -> None:
        self._config: TelegramConfig = TelegramConfig()
        self._client: httpx.AsyncClient | None = None
        self._poll_task: asyncio.Task[None] | None = None
        self._last_update_id: int = 0
        self._updates_buffer: list[dict[str, Any]] = []
        self._pipeline: MessagePipeline | None = None
        self._routed_messages: list[RoutingResult] = []
        self._db: Any = None
        self._processing_lifecycle: dict[str, ProcessingLifecycle] = {}
        self._reaction_locks: dict[str, asyncio.Lock] = {}

    @property
    def name(self) -> str:
        return "telegram"

    @property
    def config_schema(self) -> type[BaseModel]:
        return TelegramConfig

    @property
    def dependencies(self) -> list[str]:
        return []

    def user_inputs(self) -> tuple[ToolIODescriptor, ...]:
        """User-identity Telegram input tools."""
        return (
            ToolIODescriptor(
                name="user_telegram_get_updates",
                description="Read updates from the user Telegram identity.",
            ),
        )

    def user_outputs(self) -> tuple[ToolIODescriptor, ...]:
        """User-identity Telegram output tools.

        User send/reply tools are marked as approval-required defaults.
        """
        return (
            ToolIODescriptor(
                name="user_telegram_send_message",
                description="Send as user. approval_default=always (approval required).",
            ),
            ToolIODescriptor(
                name="user_telegram_reply_to_message",
                description="Reply as user. approval_default=always (approval required).",
            ),
        )

    def bot_inputs(self) -> tuple[ToolIODescriptor, ...]:
        """Bot-identity Telegram input tools."""
        return (
            ToolIODescriptor(
                name="bot_telegram_get_updates",
                description="Read updates from the bot Telegram identity.",
            ),
        )

    def bot_outputs(self) -> tuple[ToolIODescriptor, ...]:
        """Bot-identity Telegram output tools."""
        return (
            ToolIODescriptor(
                name="bot_telegram_send_message",
                description="Send as bot. approval_default=conditional.",
            ),
            ToolIODescriptor(
                name="bot_telegram_reply_to_message",
                description="Reply as bot. approval_default=conditional.",
            ),
        )

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

    def set_pipeline(self, pipeline: MessagePipeline) -> None:
        """Attach a classification/routing pipeline for incoming messages.

        When set, incoming messages from polling or webhook processing will
        be classified and routed to the appropriate butler.
        """
        self._pipeline = pipeline

    def _get_bot_token(self) -> str:
        """Resolve Telegram bot token from configured bot credential scope."""
        if not self._config.bot.enabled:
            raise RuntimeError("Telegram bot scope modules.telegram.bot is disabled")
        token_env = self._config.bot.token_env
        token = os.environ.get(token_env)
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

    def _get_db_pool(self) -> Any | None:
        """Return an asyncpg-compatible pool-like object if available."""
        if self._db is None:
            return None
        pool = getattr(self._db, "pool", None)
        if pool is not None:
            return pool
        if hasattr(self._db, "acquire"):
            return self._db
        return None

    @staticmethod
    def _result_has_failure(result: RoutingResult) -> bool:
        if result.classification_error or result.routing_error:
            return True
        if result.failed_targets:
            return True
        route_error = result.route_result.get("error")
        return route_error not in (None, "")

    def _message_lock(self, message_key: str) -> asyncio.Lock:
        lock = self._reaction_locks.get(message_key)
        if lock is None:
            lock = asyncio.Lock()
            self._reaction_locks[message_key] = lock
        return lock

    def _lifecycle(self, message_key: str) -> ProcessingLifecycle:
        lifecycle = self._processing_lifecycle.get(message_key)
        if lifecycle is None:
            lifecycle = ProcessingLifecycle()
            self._processing_lifecycle[message_key] = lifecycle
        return lifecycle

    def _track_routing_progress(
        self, lifecycle: ProcessingLifecycle, result: RoutingResult
    ) -> None:
        if result.routed_targets:
            lifecycle.routed_targets.update(result.routed_targets)
        elif result.target_butler and result.target_butler != "multi":
            lifecycle.routed_targets.add(result.target_butler)

        if result.acked_targets:
            lifecycle.acked_targets.update(result.acked_targets)
        if result.failed_targets:
            lifecycle.failed_targets.update(result.failed_targets)

        if (
            not result.failed_targets
            and not result.routing_error
            and not result.classification_error
            and result.target_butler
            and result.target_butler != "multi"
            and not result.acked_targets
        ):
            lifecycle.acked_targets.add(result.target_butler)

    async def _update_reaction(
        self,
        *,
        chat_id: str | None,
        message_id: int | None,
        message_key: str | None,
        reaction: str,
    ) -> None:
        if chat_id in (None, "") or message_id is None or message_key is None:
            return
        if os.environ.get("BUTLER_TELEGRAM_TOKEN", "") == "":
            return
        if reaction not in REACTION_TO_EMOJI:
            return

        lifecycle = self._lifecycle(message_key)
        if lifecycle.terminal_reaction is not None:
            if lifecycle.terminal_reaction == reaction:
                return
            if reaction == REACTION_IN_PROGRESS:
                return
            return

        if reaction in (REACTION_SUCCESS, REACTION_FAILURE):
            lifecycle.terminal_reaction = reaction

        try:
            await self._set_message_reaction(
                chat_id=chat_id,
                message_id=message_id,
                reaction=reaction,
            )
        except Exception:
            logger.exception(
                "Failed to set Telegram message reaction",
                extra={
                    "source": "telegram",
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "reaction": reaction,
                },
            )

    async def register_tools(self, mcp: Any, config: Any, db: Any) -> None:
        """Register identity-prefixed Telegram MCP tools."""
        self._config = (
            config if isinstance(config, TelegramConfig) else TelegramConfig(**(config or {}))
        )
        module = self  # capture for closures

        def _register_send_tool(identity: str) -> None:
            async def send_message_tool(chat_id: str, text: str) -> dict[str, Any]:
                return await module._send_message(chat_id, text)

            send_message_tool.__name__ = f"{identity}_telegram_send_message"
            send_message_tool.__doc__ = f"Send a message as the {identity} Telegram identity."
            mcp.tool()(send_message_tool)

        def _register_reply_tool(identity: str) -> None:
            async def reply_to_message_tool(
                chat_id: str, message_id: int, text: str
            ) -> dict[str, Any]:
                return await module._reply_to_message(chat_id, message_id, text)

            reply_to_message_tool.__name__ = f"{identity}_telegram_reply_to_message"
            reply_to_message_tool.__doc__ = f"Reply as the {identity} Telegram identity."
            mcp.tool()(reply_to_message_tool)

        def _register_get_updates_tool(identity: str) -> None:
            async def get_updates_tool() -> list[dict[str, Any]]:
                return await module._get_updates()

            get_updates_tool.__name__ = f"{identity}_telegram_get_updates"
            get_updates_tool.__doc__ = f"Get recent updates for the {identity} Telegram identity."
            mcp.tool()(get_updates_tool)

        for identity in ("user", "bot"):
            _register_send_tool(identity)
            _register_reply_tool(identity)
            _register_get_updates_tool(identity)

    async def on_startup(self, config: Any, db: Any) -> None:
        """Start polling or set webhook based on config."""
        self._config = (
            config if isinstance(config, TelegramConfig) else TelegramConfig(**(config or {}))
        )
        self._client = httpx.AsyncClient()
        self._last_update_id = 0
        self._updates_buffer = []
        self._db = db

        if self._config.mode == "webhook" and self._config.webhook_url:
            await self._set_webhook(self._config.webhook_url)
        elif self._config.mode == "polling":
            self._poll_task = asyncio.create_task(self._poll_loop())

    async def on_shutdown(self) -> None:
        """Clean up: cancel polling, close HTTP client."""
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # Classification pipeline integration
    # ------------------------------------------------------------------

    async def process_update(self, update: dict[str, Any]) -> RoutingResult | None:
        """Process a single Telegram update through the classification pipeline.

        Extracts the message text from the update, classifies it via
        ``classify_message()``, and routes it to the target butler via
        ``route()``.

        Returns ``None`` if no pipeline is configured or the update has
        no extractable text.
        """
        chat_id = _extract_chat_id(update)
        message_id = _extract_message_id(update)
        message_key = _message_tracking_key(update, chat_id=chat_id, message_id=message_id)
        if self._pipeline is None:
            logger.warning(
                "Skipping Telegram update because no classification pipeline is configured",
                extra={
                    "source": "telegram",
                    "chat_id": chat_id,
                    "target_butler": None,
                    "latency_ms": None,
                    "update_id": update.get("update_id"),
                },
            )
            return None

        text = _extract_text(update)
        if not text:
            return None

        lock = self._message_lock(message_key) if message_key is not None else None
        if lock is not None:
            await lock.acquire()
        try:
            await self._update_reaction(
                chat_id=chat_id,
                message_id=message_id,
                message_key=message_key,
                reaction=REACTION_IN_PROGRESS,
            )

            # Phase 1: Log receipt
            message_inbox_id = None
            db_pool = self._get_db_pool()
            if db_pool is not None:
                async with db_pool.acquire() as conn:
                    message_inbox_id = await conn.fetchval(
                        """
                        INSERT INTO message_inbox
                            (source_channel, sender_id, raw_content, raw_metadata, received_at)
                        VALUES
                            ($1, $2, $3, $4, $5)
                        RETURNING id
                        """,
                        "telegram",
                        chat_id,
                        text,
                        json.dumps(update),
                        datetime.now(UTC),
                    )

            result = await self._pipeline.process(
                message_text=text,
                tool_name="handle_message",
                tool_args={
                    "source": "telegram",
                    "chat_id": chat_id,
                    "source_id": message_key,
                },
                message_inbox_id=message_inbox_id,
            )

            if message_key is not None:
                lifecycle = self._lifecycle(message_key)
                self._track_routing_progress(lifecycle, result)
                pending_targets = (
                    lifecycle.routed_targets - lifecycle.acked_targets - lifecycle.failed_targets
                )
                if self._result_has_failure(result) or lifecycle.failed_targets:
                    await self._update_reaction(
                        chat_id=chat_id,
                        message_id=message_id,
                        message_key=message_key,
                        reaction=REACTION_FAILURE,
                    )
                elif lifecycle.routed_targets and not pending_targets:
                    await self._update_reaction(
                        chat_id=chat_id,
                        message_id=message_id,
                        message_key=message_key,
                        reaction=REACTION_SUCCESS,
                    )
        except Exception:
            await self._update_reaction(
                chat_id=chat_id,
                message_id=message_id,
                message_key=message_key,
                reaction=REACTION_FAILURE,
            )
            raise
        finally:
            if lock is not None and lock.locked():
                lock.release()

        self._routed_messages.append(result)
        logger.info(
            "Telegram message routed",
            extra={
                "source": "telegram",
                "chat_id": chat_id,
                "target_butler": result.target_butler,
                "latency_ms": None,
            },
        )
        return result

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

    async def _get_updates(self) -> list[dict[str, Any]]:
        """Call Telegram getUpdates API and return new messages."""
        url = f"{self._base_url()}/getUpdates"
        params: dict[str, Any] = {"timeout": 0}
        if self._last_update_id:
            params["offset"] = self._last_update_id + 1

        client = self._get_client()
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()
        updates: list[dict[str, Any]] = data.get("result", [])

        if updates:
            self._last_update_id = updates[-1]["update_id"]

        return updates

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
        """Call Telegram setMessageReaction API with a mapped lifecycle reaction."""
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

    async def _poll_loop(self) -> None:
        """Long-polling loop for dev mode.

        When a pipeline is configured, each incoming update is automatically
        forwarded through the classification and routing pipeline.
        """
        while True:
            try:
                updates = await self._get_updates()
                if updates:
                    self._updates_buffer.extend(updates)
                    logger.info(
                        "Polled Telegram updates",
                        extra={
                            "source": "telegram",
                            "update_count": len(updates),
                        },
                    )

                    # Route through classification pipeline if available
                    for update in updates:
                        await self.process_update(update)

            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Error polling Telegram updates")

            await asyncio.sleep(self._config.poll_interval)


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


def _message_tracking_key(
    update: dict[str, Any], *, chat_id: str | None, message_id: int | None
) -> str | None:
    """Build a stable per-message key for lifecycle serialization and tracking."""
    if chat_id not in (None, "") and message_id is not None:
        return f"{chat_id}:{message_id}"
    update_id = update.get("update_id")
    if update_id is None:
        return None
    return f"update:{update_id}"
