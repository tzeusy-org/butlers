"""Untrusted span marking for Telegram messages (bu-xmueeq, REQ-connector-base-spec-004).

Bot API messages (plain dicts):

- A forwarded message (``forward_origin``, or the legacy ``forward_*`` fields, or
  ``is_automatic_forward``) is someone else's text in full, so its whole text, caption included,
  is one ``forwarded`` span carrying the original author when Telegram names one. The forwarding
  owner stays ``sender.identity``: D5 starts the owner goal exposed, it does not make it
  third-party.
- ``blockquote`` / ``expandable_blockquote`` entities inside the text are ``quoted`` spans.
- A reply to another message in the same chat carries that message only as a server-resolvable
  ``in_reply_to_ref`` (``<chat_id>:<message_id>``); its text is not part of
  ``normalized_text``, so it is not marked. A reply to the bot's own message is therefore never
  exposed by the reply alone; admission resolves the reference.
- A reply to a message in another chat (``external_reply``) cannot be resolved on the server, so
  the whole text is one ``quoted`` span (fail closed).

Telethon messages (user client): ``fwd_from`` marks the message as forwarded.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from butlers.ingestion_spans import span_in_text, whole_text_span

_LEGACY_FORWARD_KEYS = ("forward_from", "forward_from_chat", "forward_sender_name", "forward_date")
_QUOTE_ENTITY_TYPES = frozenset({"blockquote", "expandable_blockquote"})


def _person_name(user: dict[str, Any]) -> str | None:
    name = " ".join(p for p in (user.get("first_name"), user.get("last_name")) if p).strip()
    return name or user.get("username") or None


def _chat_origin(chat: dict[str, Any]) -> dict[str, str | None]:
    chat_id = chat.get("id")
    return {
        "origin_identity": str(chat_id) if chat_id is not None else None,
        "origin_display_name": chat.get("title") or chat.get("username") or None,
    }


def forward_origin(msg: dict[str, Any]) -> dict[str, str | None] | None:
    """Return the forward's original author (possibly empty), or ``None`` if not forwarded."""
    origin = msg.get("forward_origin")
    if isinstance(origin, dict):
        kind = origin.get("type")
        user = origin.get("sender_user")
        if kind == "user" and isinstance(user, dict):
            user_id = user.get("id")
            return {
                "origin_identity": str(user_id) if user_id is not None else None,
                "origin_display_name": _person_name(user),
            }
        if kind == "hidden_user":
            return {"origin_identity": None, "origin_display_name": origin.get("sender_user_name")}
        chat = origin.get("sender_chat") or origin.get("chat")
        if kind in ("chat", "channel") and isinstance(chat, dict):
            return _chat_origin(chat)
        return {}
    legacy_user = msg.get("forward_from")
    if isinstance(legacy_user, dict):
        user_id = legacy_user.get("id")
        return {
            "origin_identity": str(user_id) if user_id is not None else None,
            "origin_display_name": _person_name(legacy_user),
        }
    legacy_chat = msg.get("forward_from_chat")
    if isinstance(legacy_chat, dict):
        return _chat_origin(legacy_chat)
    if msg.get("forward_sender_name"):
        return {"origin_identity": None, "origin_display_name": msg["forward_sender_name"]}
    if msg.get("is_automatic_forward") or any(key in msg for key in _LEGACY_FORWARD_KEYS):
        return {}
    return None


def _utf16_to_index(text: str, utf16_offset: int) -> int:
    """Telegram entity offsets count UTF-16 code units; map one onto a ``str`` index."""
    units = 0
    for index, char in enumerate(text):
        if units >= utf16_offset:
            return index
        units += 2 if ord(char) > 0xFFFF else 1
    return len(text)


def bot_message_spans(msg: dict[str, Any], normalized_text: str) -> list[dict[str, Any]]:
    """Untrusted spans of one Bot API message whose text is ``normalized_text``."""
    origin = forward_origin(msg)
    if origin is not None:
        return [whole_text_span("forwarded", normalized_text, **origin)]
    if isinstance(msg.get("external_reply"), dict):
        return [whole_text_span("quoted", normalized_text)]
    spans: list[dict[str, Any]] = []
    entities = msg.get("entities") if msg.get("text") else msg.get("caption_entities")
    for entity in entities if isinstance(entities, list) else []:
        if not isinstance(entity, dict) or entity.get("type") not in _QUOTE_ENTITY_TYPES:
            continue
        offset, length = entity.get("offset"), entity.get("length")
        if not isinstance(offset, int) or not isinstance(length, int) or length <= 0:
            spans.append(whole_text_span("quoted", normalized_text))
            continue
        start = _utf16_to_index(normalized_text, offset)
        end = _utf16_to_index(normalized_text, offset + length)
        spans.append(span_in_text("quoted", normalized_text, start, end))
    return spans


def bot_in_reply_to_ref(msg: dict[str, Any]) -> str | None:
    """``<chat_id>:<message_id>`` of the same-chat message this one replies to, if any."""
    replied = msg.get("reply_to_message")
    chat = msg.get("chat")
    if not isinstance(replied, dict) or not isinstance(chat, dict):
        return None
    chat_id, message_id = chat.get("id"), replied.get("message_id")
    if chat_id is None or message_id is None:
        return None
    return f"{chat_id}:{message_id}"


def telethon_forward_origin(message: Any) -> dict[str, str | None] | None:
    """The original author of a forwarded Telethon message (possibly empty), else ``None``."""
    header = getattr(message, "fwd_from", None)
    # Telethon's MessageFwdHeader always carries the original send date; anything else in the
    # slot (None, a stand-in object) is not a forward.
    if header is None or not isinstance(getattr(header, "date", None), datetime):
        return None
    peer = getattr(header, "from_id", None)
    peer_id = None
    for attr in ("user_id", "channel_id", "chat_id"):
        value = getattr(peer, attr, None)
        if value is not None:
            peer_id = str(value)
            break
    return {"origin_identity": peer_id, "origin_display_name": getattr(header, "from_name", None)}


def telethon_in_reply_to_ref(message: Any) -> str | None:
    """Preserve only the provider's actual replied-message locator, not authority."""
    from telethon.tl.types import MessageReplyHeader
    from telethon.utils import get_peer_id

    header = getattr(message, "reply_to", None)
    if not isinstance(header, MessageReplyHeader) or type(header.reply_to_msg_id) is not int:
        return None
    peer = header.reply_to_peer_id
    chat_id = get_peer_id(peer) if peer is not None else getattr(message, "chat_id", None)
    return f"{chat_id}:{header.reply_to_msg_id}" if type(chat_id) is int else None


def telethon_message_spans(
    message: Any, normalized_text: str, *, escape_html: bool = True
) -> list[dict[str, Any]]:
    """Telethon 1.44 entities index this message in UTF-16, before HTML escaping.

    ReplyHeader.quote_offset indexes the *earlier* message. A known header
    quote/external reply has no reliable range in this body: mark conservatively.
    A same-chat ordinary reply carries only its reference and does not expose text.
    """
    import html

    from telethon.tl.types import MessageEntityBlockquote, MessageReplyHeader

    origin = telethon_forward_origin(message)
    if origin is not None:
        return [whole_text_span("forwarded", normalized_text, **origin)]
    header = getattr(message, "reply_to", None)
    if isinstance(header, MessageReplyHeader) and (
        header.quote is True
        or header.quote_text
        or header.reply_media
        or header.reply_to_peer_id is not None
    ):
        return [whole_text_span("quoted", normalized_text)]
    raw = getattr(message, "message", None)
    raw = raw if isinstance(raw, str) else ""
    entities = getattr(message, "entities", None)
    spans = []
    for entity in entities if isinstance(entities, (list, tuple)) else ():
        if not isinstance(entity, MessageEntityBlockquote):
            continue
        offset, length = entity.offset, entity.length
        units = len(raw.encode("utf-16-le")) // 2
        if (
            type(offset) is not int
            or type(length) is not int
            or offset < 0
            or length <= 0
            or offset + length > units
        ):
            return [whole_text_span("quoted", normalized_text)]
        start = _utf16_to_index(raw, offset)
        end = _utf16_to_index(raw, offset + length)
        if (
            len(raw[:start].encode("utf-16-le")) // 2 != offset
            or len(raw[:end].encode("utf-16-le")) // 2 != offset + length
        ):
            return [whole_text_span("quoted", normalized_text)]
        if escape_html:
            start, end = len(html.escape(raw[:start])), len(html.escape(raw[:end]))
        spans.append(span_in_text("quoted", normalized_text, start, end))
    return spans
