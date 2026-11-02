"""Small reusable Telegram Bot API helpers for output adapters and connectors."""

from __future__ import annotations

import math
from typing import Any

import httpx

from butlers.telegram_text import clamp_edit_source, render_markdown

MAX_TELEGRAM_CALLBACK_DATA_BYTES = 64


class TelegramPartialDeliveryError(RuntimeError):
    """An accepted prefix makes replay of this logical send unsafe."""

    retryable = False
    partial_delivery = True

    def __init__(self, message_ids: list[int]) -> None:
        super().__init__("Telegram accepted a message prefix; automatic replay is forbidden.")
        self.message_ids = tuple(message_ids)

    def to_tool_error(self) -> dict[str, Any]:
        return {
            "error": "telegram_partial_delivery",
            "retryable": False,
            "partial_delivery": True,
            "message_ids": list(self.message_ids),
        }


def is_partial_delivery(value: Any, *, _depth: int = 0) -> bool:
    """Read a server-produced partial outcome through the fixed delivery wrappers.

    This is outcome propagation, never actor/approval authority. Caller text,
    arbitrary object fields and exception messages are not searched.
    """
    if _depth > 8:
        return False
    if isinstance(value, TelegramPartialDeliveryError):
        return True
    if isinstance(value, dict):
        if value.get("partial_delivery") is True:
            return True
        return any(
            is_partial_delivery(value.get(key), _depth=_depth + 1)
            for key in ("result", "data", "error", "notify_response")
        )
    return False


def terminal_telegram_metadata(value: Any, *, _depth: int = 0) -> dict[str, bool]:
    """Only fixed server outcome fields can fence a Telegram automatic replay.

    An unknown effect receipt is not proof of a confirmed prefix. The producer
    stamps that distinct field only after actual Telegram dispatch selection.
    """
    if is_partial_delivery(value):
        return {"partial_delivery": True, "retryable": False}
    if _depth > 8 or not isinstance(value, dict):
        return {}
    if value.get("telegram_effect_uncertain") is True:
        return {"telegram_effect_uncertain": True, "retryable": False}
    for key in ("result", "data", "error", "notify_response"):
        metadata = terminal_telegram_metadata(value.get(key), _depth=_depth + 1)
        if metadata:
            return metadata
    return {}


def telegram_parse_rejection(response: httpx.Response) -> bool:
    """Only an explicit parse-entities rejection permits a plain-text resend."""
    if response.status_code != 400:
        return False
    try:
        data = response.json()
    except (ValueError, TypeError):
        return False
    description = data.get("description") if isinstance(data, dict) else None
    return isinstance(description, str) and "parse entities" in description.lower()


def telegram_retry_after(response: httpx.Response) -> float | None:
    """Accept only a finite provider-declared delay on an actual rejected 429."""
    if response.status_code != 429:
        return None
    try:
        data = response.json()
        value = data["parameters"]["retry_after"]
    except (ValueError, TypeError, KeyError):
        return None
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        return None
    return float(value)


class TelegramReplyMarkupValidationError(ValueError):
    """A reply-markup payload cannot be delivered safely to Telegram."""

    def __init__(
        self,
        message: str,
        *,
        field: str,
        max_bytes: int | None = None,
        actual_bytes: int | None = None,
    ) -> None:
        super().__init__(message)
        self.field = field
        self.max_bytes = max_bytes
        self.actual_bytes = actual_bytes

    def to_tool_error(self) -> dict[str, Any]:
        """Return the stable structured error shape for Telegram MCP tools."""
        payload: dict[str, Any] = {
            "error": str(self),
            "error_code": "validation_error",
            "field": self.field,
        }
        if self.max_bytes is not None:
            payload["max_bytes"] = self.max_bytes
        if self.actual_bytes is not None:
            payload["actual_bytes"] = self.actual_bytes
        return payload


def validate_telegram_reply_markup(
    reply_markup: dict[str, Any] | None,
    *,
    render_links_inert: bool = False,
    server_built: bool = False,
) -> dict[str, Any] | None:
    """Validate inline-keyboard callback data without rewriting the payload."""
    if reply_markup is None:
        return None
    if not isinstance(reply_markup, dict):
        raise TelegramReplyMarkupValidationError(
            "reply_markup must be an object.", field="reply_markup"
        )

    keyboard = reply_markup.get("inline_keyboard")
    if not isinstance(keyboard, list):
        raise TelegramReplyMarkupValidationError(
            "reply_markup.inline_keyboard must be a list of button rows.",
            field="reply_markup.inline_keyboard",
        )
    for row_index, row in enumerate(keyboard):
        row_field = f"reply_markup.inline_keyboard[{row_index}]"
        if not isinstance(row, list):
            raise TelegramReplyMarkupValidationError(
                "Inline keyboard rows must be lists.", field=row_field
            )
        for button_index, button in enumerate(row):
            button_field = f"{row_field}[{button_index}]"
            if not isinstance(button, dict):
                raise TelegramReplyMarkupValidationError(
                    "Inline keyboard buttons must be objects.", field=button_field
                )
            for identity_handoff in ("login_url", "web_app"):
                if identity_handoff in button:
                    raise TelegramReplyMarkupValidationError(
                        "Telegram identity handoff buttons are forbidden.",
                        field=f"{button_field}.{identity_handoff}",
                    )
            if render_links_inert and "url" in button and not server_built:
                raise TelegramReplyMarkupValidationError(
                    "Caller URL buttons are forbidden for inert-link delivery.",
                    field=f"{button_field}.url",
                )
            callback_data = button.get("callback_data")
            if callback_data is None:
                continue
            callback_field = f"{button_field}.callback_data"
            if not isinstance(callback_data, str):
                raise TelegramReplyMarkupValidationError(
                    "callback_data must be a string.", field=callback_field
                )
            callback_bytes = len(callback_data.encode("utf-8"))
            if callback_bytes > MAX_TELEGRAM_CALLBACK_DATA_BYTES:
                raise TelegramReplyMarkupValidationError(
                    "callback_data exceeds Telegram's "
                    f"{MAX_TELEGRAM_CALLBACK_DATA_BYTES}-byte limit.",
                    field=callback_field,
                    max_bytes=MAX_TELEGRAM_CALLBACK_DATA_BYTES,
                    actual_bytes=callback_bytes,
                )
    return reply_markup


async def edit_telegram_message_text(
    client: httpx.AsyncClient,
    api_base_url: str,
    *,
    chat_id: str,
    message_id: int,
    text: str,
) -> dict[str, Any]:
    """Edit one Telegram message's text through ``editMessageText``."""
    source = clamp_edit_source(text)
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": render_markdown(source),
        "parse_mode": "HTML",
    }
    response = await client.post(f"{api_base_url}/editMessageText", json=payload)
    if telegram_parse_rejection(response):
        payload = {key: value for key, value in payload.items() if key != "parse_mode"}
        payload["text"] = source
        response = await client.post(f"{api_base_url}/editMessageText", json=payload)
    response.raise_for_status()
    return response.json()


async def remove_telegram_inline_keyboard(
    client: httpx.AsyncClient,
    api_base_url: str,
    *,
    chat_id: str,
    message_id: int,
) -> dict[str, Any]:
    """Remove one Telegram message's inline keyboard through ``editMessageReplyMarkup``."""
    response = await client.post(
        f"{api_base_url}/editMessageReplyMarkup",
        json={"chat_id": chat_id, "message_id": message_id, "reply_markup": None},
    )
    response.raise_for_status()
    return response.json()


__all__ = [
    "MAX_TELEGRAM_CALLBACK_DATA_BYTES",
    "TelegramReplyMarkupValidationError",
    "edit_telegram_message_text",
    "remove_telegram_inline_keyboard",
    "validate_telegram_reply_markup",
]
