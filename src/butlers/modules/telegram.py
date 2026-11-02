"""Telegram module — Telegram MCP tools.

Ingestion is handled by ``TelegramBotConnector`` which submits messages through
the canonical ingest API. This module is output-only: it provides send/reply MCP
tools and webhook setup. Polling and pipeline wiring have been removed.

Configured via [modules.telegram] with optional
[modules.telegram.user] and [modules.telegram.bot] credential scopes in butler.toml.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from butlers.core.audit import write_audit_entry
from butlers.core.channel_reactions import (
    REACTION_FAILURE,
    REACTION_IN_PROGRESS,
    REACTION_SUCCESS,
)
from butlers.core.notify_attachments import (
    CAPTION_LIMIT,
    AttachmentError,
    ResolvedAttachment,
    attachment_audit_summary,
    resolve_attachments,
    validate_attachment_specs,
)
from butlers.core.permissions import NOTIFY_PERMISSION, PermissionUnavailable, require_permission
from butlers.modules.base import Module
from butlers.telegram_api import (
    TelegramPartialDeliveryError,
    TelegramReplyMarkupValidationError,
    edit_telegram_message_text,
    remove_telegram_inline_keyboard,
    telegram_parse_rejection,
    telegram_retry_after,
    validate_telegram_reply_markup,
)
from butlers.telegram_text import render_markdown, split_telegram_text, text_units

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}"
_ENV_VAR_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Reaction lifecycle emoji keys (map to emoji via REACTION_TO_EMOJI).
# Constants are imported from butlers.core.channel_reactions; re-exported here
# for backward compatibility with callers that import them from this module.

REACTION_TO_EMOJI = {
    REACTION_IN_PROGRESS: "\U0001f440",
    REACTION_SUCCESS: "\U0001f44d",
    REACTION_FAILURE: "\U0001f47e",
}


@dataclass
class _TelegramRetryBudget:
    used: int = 0


class _TelegramParseRejected(Exception):
    """A proven parse rejection permits one bounded plain-source fallback phase."""


class TelegramProviderResponseError(RuntimeError):
    """Telegram returned no provider-confirmed message receipt."""

    def __init__(self, message: str, *, provider_rejected: bool = False) -> None:
        super().__init__(message)
        self.provider_rejected = provider_rejected


def _telegram_send_failure_category(exc: BaseException) -> str:
    """Return a fixed, credential-safe audit category for a send failure."""
    if isinstance(exc, httpx.TimeoutException):
        return "telegram_transport_timeout"
    if isinstance(exc, httpx.TransportError):
        return "telegram_transport_error"
    if isinstance(exc, httpx.HTTPStatusError):
        return "telegram_provider_http_error"
    if isinstance(exc, TelegramProviderResponseError):
        return "telegram_provider_response_invalid"
    if isinstance(exc, TelegramReplyMarkupValidationError):
        return "telegram_reply_markup_invalid"
    if isinstance(exc, PermissionUnavailable):
        return "telegram_permission_unavailable"
    if type(exc).__name__ == "PermissionDenied":
        return "telegram_permission_denied"
    return "telegram_internal_error"


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


def _markdown_to_telegram_html(text: str) -> str:
    """Compatibility entry point for the single shared safe renderer."""
    return render_markdown(text)


def _telegram_error_text(exc: httpx.HTTPStatusError) -> str:
    """Return Telegram's ``description`` for a failed call, or the status line."""
    try:
        body = exc.response.json()
    except Exception:
        return str(exc)
    description = body.get("description") if isinstance(body, dict) else None
    return description if isinstance(description, str) else str(exc)


_PHOTO_REJECTION_MARKERS = ("PHOTO_INVALID_DIMENSIONS", "IMAGE_PROCESS_FAILED", "PHOTO_INVALID")


def _telegram_photo_rejected(response: httpx.Response) -> bool:
    """Only an explicit 400 photo-processing rejection permits a document resend."""
    if response.status_code != 400:
        return False
    try:
        data = response.json()
    except (ValueError, TypeError):
        return False
    description = data.get("description") if isinstance(data, dict) else None
    return isinstance(description, str) and any(
        marker in description.upper() for marker in _PHOTO_REJECTION_MARKERS
    )


class TelegramModule(Module):
    """Telegram module providing identity-prefixed Telegram MCP tools.

    Output-only: provides send/reply tools and webhook setup.
    Ingestion is owned by TelegramBotConnector via the canonical ingest API.
    """

    supports_inert_link_delivery = True

    def __init__(self) -> None:
        self._config: TelegramConfig = TelegramConfig()
        self._client: httpx.AsyncClient | None = None
        self._db: Any = None
        self._audit_pool: Any = None
        self._butler_name: str = "telegram"
        # Credentials cached at startup: user-scope from owner entity_info,
        # bot-scope from CredentialStore.
        self._resolved_credentials: dict[str, str] = {}
        self._chat_locks: dict[str, tuple[asyncio.Lock, int]] = {}
        self._blob_store: Any = None

    name = "telegram"

    @property
    def config_schema(self) -> type[BaseModel]:
        return TelegramConfig

    @property
    def credentials_env(self) -> list[str]:
        """Environment variables required by this module."""
        envs: list[str] = []
        if self._config.bot.enabled:
            envs.append(self._config.bot.token_env)
        # User-scope credentials come from owner entity_info, not butler_secrets.
        return envs

    def _get_bot_token(self) -> str:
        """Resolve Telegram bot token from startup-cached credentials.

        The bot token is an ecosystem-wide (Tier 1) credential resolved
        exclusively from CredentialStore at startup.
        """
        if not self._config.bot.enabled:
            raise RuntimeError("Telegram bot scope modules.telegram.bot is disabled")
        token_env = self._config.bot.token_env
        token = self._resolved_credentials.get(token_env)
        if not token:
            raise RuntimeError(
                f"Missing Telegram bot token for modules.telegram.bot: "
                f"configure {token_env} via butler_secrets"
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

    async def register_tools(self, mcp: Any, config: Any, db: Any, butler_name: str) -> None:
        """Register Telegram send/reply MCP tools."""
        self._config = (
            config if isinstance(config, TelegramConfig) else TelegramConfig(**(config or {}))
        )
        self._butler_name = butler_name or self._butler_name
        module = self  # capture for closures

        async def telegram_send_message(
            chat_id: str,
            text: str,
            reply_markup: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            """Send a Telegram message."""
            try:
                return await module._send_message(chat_id, text, reply_markup=reply_markup)
            except (TelegramReplyMarkupValidationError, TelegramPartialDeliveryError) as exc:
                return exc.to_tool_error()

        async def telegram_reply_to_message(
            chat_id: str,
            message_id: int,
            text: str,
            reply_markup: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            """Reply to a Telegram message."""
            try:
                return await module._reply_to_message(
                    chat_id, message_id, text, reply_markup=reply_markup
                )
            except (TelegramReplyMarkupValidationError, TelegramPartialDeliveryError) as exc:
                return exc.to_tool_error()

        async def telegram_react_to_message(
            chat_id: str, message_id: int, emoji: str
        ) -> dict[str, Any]:
            """React to a Telegram message with an emoji."""
            return await module._react_to_message(chat_id, message_id, emoji)

        async def telegram_edit_message_text(
            chat_id: str, message_id: int, text: str
        ) -> dict[str, Any]:
            """Edit the text of a message this bot already sent.

            Used to amend a proactive insight in place when the fact it asserted
            stopped being true. A Telegram 400 (message too old, unchanged, or
            not editable) is returned as ``{"edited": false, "error_code": 400}``
            rather than raised, so the caller can fall back to folding the
            correction into its next digest.
            """
            return await module._edit_message_text_or_reject(chat_id, message_id, text)

        mcp.tool()(telegram_send_message)
        mcp.tool()(telegram_reply_to_message)
        mcp.tool()(telegram_react_to_message)
        mcp.tool()(telegram_edit_message_text)

    async def on_startup(
        self, config: Any, db: Any, credential_store: Any = None, blob_store: Any = None
    ) -> None:
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
        self._blob_store = blob_store
        self._resolved_credentials = {}

        # --- Bot-scope: CredentialStore (ecosystem-wide Tier 1 credential) ----
        if credential_store is not None:
            bot_token_env = self._config.bot.token_env
            value = await credential_store.resolve(bot_token_env)
            if value is not None:
                self._resolved_credentials[bot_token_env] = value

        if self._config.webhook_url:
            await self._set_webhook(self._config.webhook_url)

    async def on_shutdown(self) -> None:
        """Clean up: close HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def wire_audit_pool(self, audit_pool: Any) -> None:
        """Store the switchboard audit pool for telegram_send egress audit entries."""
        self._audit_pool = audit_pool

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

    def _permission_pool(self) -> Any:
        """Return a pool able to read ``public.permissions`` for the gate.

        Prefers the switchboard audit pool (cross-butler, reads ``public``);
        falls back to the butler's own pool (which can also read ``public``).
        ``None`` when neither is wired; the send gate refuses before traffic.
        """
        if self._audit_pool is not None:
            return self._audit_pool
        return getattr(self._db, "pool", None) if self._db is not None else None

    async def _send_message(
        self,
        chat_id: str,
        text: str,
        reply_to_message_id: int | None = None,
        reply_markup: dict[str, Any] | None = None,
        *,
        render_links_inert: bool = False,
        server_built_reply_markup: bool = False,
    ) -> dict[str, Any]:
        """Call Telegram sendMessage API.

        Permissions-matrix enforcement (public.permissions: notify): Telegram is
        an owner-facing notify channel (the notify() core tool itself delivers
        via Telegram), so a butler with ``notify`` revoked must not be able to
        message the owner through the raw ``telegram_send_message`` /
        ``telegram_reply_to_message`` tools either. ``_send_message`` is the
        chokepoint both send and reply funnel through; gating it here covers both.
        Reuses the same ``notify`` capability the notify() gate enforces rather
        than introducing a parallel ``telegram.send`` key (no migration needed).
        Mirrors the spawn gate: consult the matrix at the decision point before
        any Telegram traffic. Unavailable permission state raises a transient error
        before delivery.
        """
        audit_summary: dict[str, Any] = {"chat_id": chat_id, "text_length": len(text)}
        response_status: int | None = None
        try:
            validated_reply_markup = validate_telegram_reply_markup(
                reply_markup,
                render_links_inert=render_links_inert,
                server_built=server_built_reply_markup,
            )
            await require_permission(
                self._permission_pool(), self._butler_name, NOTIFY_PERMISSION, fail_closed=True
            )
            chunks = split_telegram_text(text, inert=render_links_inert)
            lock, users = self._chat_locks.get(chat_id, (asyncio.Lock(), 0))
            self._chat_locks[chat_id] = (lock, users + 1)
            message_ids: list[int] = []
            try:
                async with lock:
                    deadline = time.monotonic() + 30
                    pending = deque((chunk, False, _TelegramRetryBudget()) for chunk in chunks)
                    while pending:
                        chunk, plain, retry_budget = pending.popleft()
                        payload: dict[str, Any] = {
                            "chat_id": chat_id,
                            "text": chunk.html,
                            "parse_mode": "HTML",
                        }
                        if render_links_inert:
                            payload.pop("parse_mode")
                            payload["text"] = chunk.source
                            payload["entities"] = [
                                {"type": "pre", "offset": 0, "length": text_units(chunk.source)}
                            ]
                            payload["link_preview_options"] = {"is_disabled": True}
                        if plain:
                            payload.pop("parse_mode")
                            payload["text"] = chunk.source
                        if not message_ids and reply_to_message_id is not None:
                            payload["reply_to_message_id"] = reply_to_message_id
                        if not pending and validated_reply_markup is not None:
                            payload["reply_markup"] = validated_reply_markup
                        try:
                            data = await self._send_chunk(payload, deadline, retry_budget)
                        except _TelegramParseRejected:
                            # A fitting visible code line can include >4000 raw
                            # units of fence syntax. Preserve that source in
                            # separately bounded, once-only plain requests.
                            fallback = split_telegram_text(chunk.source, inert=True)
                            pending.extendleft(
                                (part, True, retry_budget) for part in reversed(fallback)
                            )
                            continue
                        message_ids.append(data["result"]["message_id"])
            except (Exception, asyncio.CancelledError) as exc:
                if message_ids:
                    raise TelegramPartialDeliveryError(message_ids) from exc
                raise
            finally:
                _, users = self._chat_locks[chat_id]
                if users == 1:
                    del self._chat_locks[chat_id]
                else:
                    self._chat_locks[chat_id] = (lock, users - 1)
            message_id = message_ids[-1]
            if len(message_ids) > 1:
                data = {**data, "result": {**data["result"], "message_ids": message_ids}}

        except Exception as exc:
            failure_category = (
                "telegram_partial_delivery"
                if isinstance(exc, TelegramPartialDeliveryError)
                else _telegram_send_failure_category(exc)
            )
            if isinstance(exc, TelegramPartialDeliveryError):
                audit_summary["provider_message_ids"] = list(exc.message_ids)
            if isinstance(exc, PermissionUnavailable):
                logger.error("Telegram sendMessage blocked: category=%s", failure_category)
                # The guard already logged this refusal. An audit retry through the
                # unavailable pool could expose the raw DB traceback in its fallback log.
                raise
            logger.error(
                "Telegram sendMessage failed: category=%s status=%s chat_id=%r",
                failure_category,
                response_status,
                chat_id,
            )
            await write_audit_entry(
                self._audit_pool,
                self._butler_name,
                "telegram_send",
                audit_summary,
                result="error",
                error=failure_category,
            )
            raise

        audit_summary["provider_message_id"] = message_id
        audit_summary["provider_message_ids"] = message_ids
        await write_audit_entry(
            self._audit_pool,
            self._butler_name,
            "telegram_send",
            audit_summary,
        )
        return data

    async def _send_files(
        self,
        chat_id: str,
        text: str,
        attachments: list[dict[str, Any]],
        reply_to_message_id: int | None = None,
        *,
        origin_butler: str,
        render_links_inert: bool = False,
    ) -> dict[str, Any]:
        """Deliver files with sendPhoto/sendDocument, text as caption when it fits.

        Every attachment is fetched and verified (size, pinned sha256) before
        the first Telegram call, so a missing blob or a changed one sends
        nothing.  Text of at most ``CAPTION_LIMIT`` characters becomes the first
        file's caption; longer text (or inert-rendered text) goes first through
        :meth:`_send_message` and the files follow as replies to its last chunk.
        A failure after any accepted message is a terminal partial delivery.
        Audit rows carry media type, size, digest and message ids only.
        """
        audit_summary: dict[str, Any] = {"chat_id": chat_id, "text_length": len(text)}
        message_ids: list[int] = []
        try:
            specs = validate_attachment_specs(attachments)
            await require_permission(
                self._permission_pool(), self._butler_name, NOTIFY_PERMISSION, fail_closed=True
            )
            # origin_butler is the Messenger-verified notify.v1 sender; refs
            # outside its RFC 0016 key prefix (or the connector prefix) refuse.
            resolved = await resolve_attachments(
                specs, self._blob_store, origin_butler=origin_butler
            )
            audit_summary["attachments"] = attachment_audit_summary(resolved)
            text_chunks = split_telegram_text(text) if text.strip() else []
            caption_first = (
                not render_links_inert
                and resolved[0].caption is None
                and len(text_chunks) == 1
                and text_units(text) <= CAPTION_LIMIT
            )
            anchor = reply_to_message_id
            if text_chunks and not caption_first:
                lead_text = text
                if render_links_inert:
                    # Captions cannot be rendered inert; fold them into the
                    # inert text instead of dropping or sending them live.
                    lead_text += "".join(
                        f"\n\n{item.filename}: {item.caption}" for item in resolved if item.caption
                    )
                sent = await self._send_message(
                    chat_id,
                    lead_text,
                    reply_to_message_id=reply_to_message_id,
                    render_links_inert=render_links_inert,
                )
                result = sent.get("result", {})
                message_ids.extend(result.get("message_ids") or [result["message_id"]])
                anchor = message_ids[-1]
            lock, users = self._chat_locks.get(chat_id, (asyncio.Lock(), 0))
            self._chat_locks[chat_id] = (lock, users + 1)
            try:
                async with lock:
                    deadline = time.monotonic() + 120
                    for index, item in enumerate(resolved):
                        caption = text if index == 0 and caption_first else item.caption
                        if render_links_inert:
                            caption = None
                        data = await self._upload_file(chat_id, item, caption, anchor, deadline)
                        message_ids.append(data["result"]["message_id"])
            finally:
                _, users = self._chat_locks[chat_id]
                if users == 1:
                    del self._chat_locks[chat_id]
                else:
                    self._chat_locks[chat_id] = (lock, users - 1)
        except (Exception, asyncio.CancelledError) as exc:
            if isinstance(exc, TelegramPartialDeliveryError):
                message_ids = list(exc.message_ids)
            if message_ids:
                partial = TelegramPartialDeliveryError(message_ids)
                failure_category = "telegram_partial_delivery"
                audit_summary["provider_message_ids"] = message_ids
            else:
                partial = None
                failure_category = (
                    exc.code
                    if isinstance(exc, AttachmentError)
                    else _telegram_send_failure_category(exc)
                )
            if isinstance(exc, PermissionUnavailable):
                logger.error("Telegram file send blocked: category=%s", failure_category)
                raise
            logger.error(
                "Telegram file send failed: category=%s chat_id=%r", failure_category, chat_id
            )
            await write_audit_entry(
                self._audit_pool,
                self._butler_name,
                "telegram_send_files",
                audit_summary,
                result="error",
                error=failure_category,
            )
            if partial is not None and partial is not exc:
                raise partial from exc
            raise

        audit_summary["provider_message_id"] = message_ids[-1]
        audit_summary["provider_message_ids"] = message_ids
        await write_audit_entry(
            self._audit_pool, self._butler_name, "telegram_send_files", audit_summary
        )
        return {
            "ok": True,
            "result": {"message_id": message_ids[-1], "message_ids": message_ids},
        }

    async def _upload_file(
        self,
        chat_id: str,
        item: ResolvedAttachment,
        caption: str | None,
        reply_to_message_id: int | None,
        deadline: float,
    ) -> dict[str, Any]:
        """Upload one file; a caption parse rejection falls back to plain once.

        A photo Telegram refuses to process (dimensions or image decoding) is
        re-sent once as a document: an explicit 400 means nothing was posted.
        """
        if item.is_photo:
            try:
                return await self._upload_as(
                    "sendPhoto", "photo", chat_id, item, caption, reply_to_message_id, deadline
                )
            except httpx.HTTPStatusError as exc:
                if not _telegram_photo_rejected(exc.response):
                    raise
        return await self._upload_as(
            "sendDocument", "document", chat_id, item, caption, reply_to_message_id, deadline
        )

    async def _upload_as(
        self,
        method: str,
        field: str,
        chat_id: str,
        item: ResolvedAttachment,
        caption: str | None,
        reply_to_message_id: int | None,
        deadline: float,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"chat_id": chat_id}
        if reply_to_message_id is not None:
            payload["reply_to_message_id"] = reply_to_message_id
        rendered = split_telegram_text(caption) if caption else []
        if len(rendered) == 1:
            payload["caption"] = rendered[0].html
            payload["parse_mode"] = "HTML"
        elif caption:
            payload["caption"] = caption
        files = {field: (item.filename, item.data, item.media_type)}
        retry_budget = _TelegramRetryBudget()
        try:
            return await self._send_chunk(
                payload, deadline, retry_budget, method=method, files=files
            )
        except _TelegramParseRejected:
            payload.pop("parse_mode")
            payload["caption"] = caption
            return await self._send_chunk(
                payload, deadline, retry_budget, method=method, files=files
            )

    async def _send_chunk(
        self,
        payload: dict[str, Any],
        deadline: float,
        retry_budget: _TelegramRetryBudget,
        *,
        method: str = "sendMessage",
        files: dict[str, tuple[str, bytes, str]] | None = None,
    ) -> dict[str, Any]:
        """One request: only proven rejection permits a bounded resend.

        ``files`` switches the request to a multipart upload (sendPhoto /
        sendDocument); ``payload`` then holds the form fields.
        """
        client = self._get_client()
        url = f"{self._base_url()}/{method}"
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Telegram logical-send deadline expired.")
            async with asyncio.timeout(remaining):
                if files is None:
                    response = await client.post(url, json=payload)
                else:
                    response = await client.post(
                        url, data={k: str(v) for k, v in payload.items()}, files=files
                    )
            if "parse_mode" in payload and telegram_parse_rejection(response):
                raise _TelegramParseRejected()
            delay = telegram_retry_after(response)
            if delay is not None and retry_budget.used < 2 and delay < deadline - time.monotonic():
                retry_budget.used += 1
                await asyncio.sleep(delay)
                continue
            response.raise_for_status()
            try:
                data = response.json()
            except Exception as exc:
                raise TelegramProviderResponseError(
                    "telegram_provider_response: invalid JSON"
                ) from exc
            if not isinstance(data, dict) or data.get("ok") is not True:
                raise TelegramProviderResponseError(
                    "telegram_provider_response: provider did not confirm delivery",
                    provider_rejected=isinstance(data, dict) and data.get("ok") is False,
                )
            result = data.get("result")
            message_id = result.get("message_id") if isinstance(result, dict) else None
            if type(message_id) is not int or message_id <= 0:
                raise TelegramProviderResponseError(
                    "telegram_provider_response: invalid message_id"
                )
            return data

    async def _reply_to_message(
        self,
        chat_id: str,
        message_id: int,
        text: str,
        reply_markup: dict[str, Any] | None = None,
        *,
        render_links_inert: bool = False,
    ) -> dict[str, Any]:
        """Reply to a specific Telegram message."""
        return await self._send_message(
            chat_id,
            text,
            reply_to_message_id=message_id,
            reply_markup=reply_markup,
            **({"render_links_inert": True} if render_links_inert else {}),
        )

    async def _edit_message_text(self, chat_id: str, message_id: int, text: str) -> dict[str, Any]:
        """Edit a sent Telegram message while preserving its current keyboard."""
        await require_permission(
            self._permission_pool(), self._butler_name, NOTIFY_PERMISSION, fail_closed=True
        )
        return await edit_telegram_message_text(
            self._get_client(),
            self._base_url(),
            chat_id=chat_id,
            message_id=message_id,
            text=text,
        )

    async def _edit_message_text_or_reject(
        self, chat_id: str, message_id: int, text: str
    ) -> dict[str, Any]:
        """Edit a sent message; a Telegram 400 is a structured rejection, not an error."""
        try:
            data = await self._edit_message_text(chat_id, message_id, text)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 400:
                raise
            return {"edited": False, "error_code": 400, "description": _telegram_error_text(exc)}
        return {"edited": True, **data} if isinstance(data, dict) else {"edited": True}

    async def _remove_inline_keyboard(self, chat_id: str, message_id: int) -> dict[str, Any]:
        """Remove a sent Telegram message's inline keyboard."""
        await require_permission(
            self._permission_pool(), self._butler_name, NOTIFY_PERMISSION, fail_closed=True
        )
        return await remove_telegram_inline_keyboard(
            self._get_client(),
            self._base_url(),
            chat_id=chat_id,
            message_id=message_id,
        )

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

    async def _react_to_message(self, chat_id: str, message_id: int, emoji: str) -> dict[str, Any]:
        """Call Telegram setMessageReaction API with an arbitrary emoji."""
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

    async def _set_message_reaction(
        self, *, chat_id: str, message_id: int, reaction: str
    ) -> dict[str, Any]:
        """Call Telegram setMessageReaction API with a mapped lifecycle reaction emoji."""
        emoji = REACTION_TO_EMOJI[reaction]
        return await self._react_to_message(chat_id, message_id, emoji)


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
