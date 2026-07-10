"""Telegram inline-button transport for the day-close gap interview (bu-whhll.12).

The concrete :class:`~butlers.chronicler.gap_interview.GapInterviewTransport`
used today: it delivers the one-tap question as a Telegram message carrying an
inline keyboard whose buttons encode ``cgi:<interview_id>:<answer>``
``callback_data`` (see ``gap_interview.build_callback_data``). The shared
``telegram_bot`` connector recognises that ``cgi:`` prefix on the way back in
and applies the answer via ``gap_interview.resolve_gap_interview_callback``.

Kept in its own module (not in the pure ``gap_interview`` engine) so the engine
stays I/O-free and telegram-agnostic — when the decision loop (RFC 0021) takes
over the transport, this file is what gets swapped, nothing in the engine.

Self-contained on purpose: it resolves the owner's telegram chat id from
``public.entity_info`` and the bot token from ``butler_secrets`` (both readable
from the chronicler pool's ``public`` search path) and POSTs ``sendMessage``
directly, rather than threading inline-keyboard support through the shared
``notify()`` → ``deliver`` → messenger → telegram chain. Owner-directed sends
auto-approve on the owner's own verified channel, so no approval gate is
bypassed; quiet-hours gating is applied by the caller
(``chronicler_gap_interview``) via the same ``delivery_preferences`` path
``notify()`` uses.
"""

from __future__ import annotations

import logging
from typing import Any

from butlers.chronicler.gap_interview import (
    GapInterview,
    GapInterviewAnswer,
    GapInterviewTransport,
    TransportResult,
    build_callback_data,
)

logger = logging.getLogger(__name__)

_TELEGRAM_CHAT_INFO_TYPE = "telegram_chat_id"
_TELEGRAM_TOKEN_SECRET = "BUTLER_TELEGRAM_TOKEN"

# Button labels shown to the owner, paired with the answer they encode.
_BUTTONS: tuple[tuple[str, GapInterviewAnswer], ...] = (
    ("✅ Work day", GapInterviewAnswer.CONFIRM),
    ("✏️ Not work", GapInterviewAnswer.CORRECT),
    ("🚫 Dismiss", GapInterviewAnswer.DISMISS),
)


class TelegramInlineButtonTransport(GapInterviewTransport):
    """Deliver a gap interview as a Telegram inline-keyboard message."""

    def __init__(self, *, http_client: Any, api_base: str, chat_id: str) -> None:
        self._http = http_client
        self._api_base = api_base.rstrip("/")
        self._chat_id = chat_id

    def _reply_markup(self, interview_id: str) -> dict[str, Any]:
        return {
            "inline_keyboard": [
                [
                    {
                        "text": label,
                        "callback_data": build_callback_data(interview_id, answer),
                    }
                    for label, answer in _BUTTONS
                ]
            ]
        }

    async def deliver_interview(self, interview: GapInterview) -> TransportResult:
        payload = {
            "chat_id": self._chat_id,
            "text": interview.decision.question,
            "reply_markup": self._reply_markup(interview.interview_id),
        }
        try:
            resp = await self._http.post(f"{self._api_base}/sendMessage", json=payload)
        except Exception as exc:  # noqa: BLE001 — network hiccup ⇒ not delivered, retried
            logger.warning("gap interview telegram send failed: %s", exc)
            return TransportResult(delivered=False, detail=f"send_error: {exc}")
        if resp.status_code >= 400:
            detail = _describe_error(resp)
            logger.warning("gap interview telegram send HTTP %d: %s", resp.status_code, detail)
            return TransportResult(delivered=False, detail=f"http_{resp.status_code}: {detail}")
        message_id = None
        try:
            message_id = resp.json().get("result", {}).get("message_id")
        except Exception:  # noqa: BLE001 — a 2xx with an odd body still counts as delivered
            pass
        return TransportResult(
            delivered=True,
            detail="sent",
            reference=str(message_id) if message_id is not None else None,
        )


def _describe_error(resp: Any) -> str:
    try:
        return str(resp.json().get("description", resp.text))
    except Exception:  # noqa: BLE001
        return getattr(resp, "text", "")


async def resolve_owner_chat_id(pool: Any) -> str | None:
    """Owner's telegram chat id from ``public.entity_info`` (or ``None``)."""
    from butlers.credential_store import resolve_owner_entity_info

    chat_id = await resolve_owner_entity_info(pool, _TELEGRAM_CHAT_INFO_TYPE)
    return chat_id.strip() if isinstance(chat_id, str) and chat_id.strip() else None


async def resolve_bot_token(pool: Any) -> str | None:
    """Bot token from ``butler_secrets`` via the chronicler pool's public reach."""
    from butlers.credential_store import CredentialStore

    try:
        return await CredentialStore(pool).resolve(_TELEGRAM_TOKEN_SECRET, env_fallback=True)
    except Exception:  # noqa: BLE001 — missing secret ⇒ cannot send, treated as no token
        logger.warning("gap interview: could not resolve %s", _TELEGRAM_TOKEN_SECRET, exc_info=True)
        return None


async def build_telegram_transport(
    pool: Any, *, http_client: Any
) -> TelegramInlineButtonTransport | None:
    """Assemble a telegram transport, or ``None`` when it cannot be delivered.

    Returns ``None`` (rather than raising) when the owner's telegram chat id or
    the bot token is unavailable, so the ask side degrades to "not delivered"
    exactly like ``notify()`` does for an unconfigured chat.
    """
    chat_id = await resolve_owner_chat_id(pool)
    if not chat_id:
        logger.info("gap interview: no owner telegram chat configured; skipping send")
        return None
    token = await resolve_bot_token(pool)
    if not token:
        return None
    api_base = f"https://api.telegram.org/bot{token}"
    return TelegramInlineButtonTransport(
        http_client=http_client, api_base=api_base, chat_id=chat_id
    )


__all__ = [
    "TelegramInlineButtonTransport",
    "build_telegram_transport",
    "resolve_bot_token",
    "resolve_owner_chat_id",
]
