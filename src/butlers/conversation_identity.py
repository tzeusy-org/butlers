"""Channel-namespaced conversation identities carried by ``ingest.v1``.

``event.external_conversation_id`` is the continuity key: conversation anchors,
provider-session resume, and realtime history all select on it. It is never a
per-message reply target. Every producer of a Telegram or WhatsApp key builds it
here so live ingress, filtered-event replay, and outbound history agree.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def telegram_conversation_id(chat_id: str | int, topic_id: str | int | None = None) -> str:
    """Return ``telegram:<chat_id>``, suffixed ``:topic:<topic_id>`` for a forum topic."""
    key = f"telegram:{chat_id}"
    return key if topic_id is None else f"{key}:topic:{topic_id}"


def telegram_bot_message_identity(message: Mapping[str, Any]) -> tuple[str | None, str | None]:
    """Derive ``(external_conversation_id, reply_target_ref)`` from a Bot API message.

    The reply target is ``<chat_id>:<message_id>`` (bare ``<chat_id>`` when the
    message id is absent). Only a message Telegram marks ``is_topic_message``
    belongs to a forum topic; ``message_thread_id`` alone also tags reply
    threads, which stay in their chat's conversation.
    """
    chat = message.get("chat")
    if not isinstance(chat, Mapping) or chat.get("id") in (None, ""):
        return None, None
    chat_id = str(chat["id"])
    message_id = message.get("message_id")
    reply_target_ref = f"{chat_id}:{message_id}" if message_id is not None else chat_id
    topic_id = message.get("message_thread_id") if message.get("is_topic_message") else None
    return telegram_conversation_id(chat_id, topic_id), reply_target_ref


def telegram_bot_update_identity(update: Mapping[str, Any]) -> tuple[str | None, str | None]:
    """Apply :func:`telegram_bot_message_identity` to an update's message object."""
    for key in ("message", "edited_message", "channel_post"):
        message = update.get(key)
        if isinstance(message, Mapping):
            return telegram_bot_message_identity(message)
    return None, None


def whatsapp_conversation_id(chat_jid: str) -> str:
    """Return ``whatsapp:<chat_jid>``."""
    return f"whatsapp:{chat_jid}"


def event_conversation_identity(event: Mapping[str, Any]) -> tuple[str | None, str | None]:
    """Return ``(external_conversation_id, reply_target_ref)`` for an ``ingest.v1`` event.

    Producers that have not split their identity send only
    ``external_thread_id``, which is then both their conversation key and their
    reply target. A split field always wins over it.
    """
    unsplit = event.get("external_thread_id") or None
    return (
        event.get("external_conversation_id") or unsplit,
        event.get("reply_target_ref") or unsplit,
    )
