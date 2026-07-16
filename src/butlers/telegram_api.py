"""Small reusable Telegram Bot API helpers for output adapters and connectors."""

from __future__ import annotations

from typing import Any

import httpx

MAX_TELEGRAM_CALLBACK_DATA_BYTES = 64


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


def validate_telegram_reply_markup(reply_markup: dict[str, Any] | None) -> dict[str, Any] | None:
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
    response = await client.post(
        f"{api_base_url}/editMessageText",
        json={
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML",
        },
    )
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
