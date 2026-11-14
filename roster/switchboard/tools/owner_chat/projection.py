"""Owner-chat span projection (REQ-butler-switchboard-011, bu-jv1moa.11).

One fixed, read-only, deterministic read of Switchboard's own ``message_inbox``. It
returns the owner's own messages from one-to-one WhatsApp and Telegram user-client
chats, for the Relationship daemon's commitment-candidate reducer. It is served only
through the ``owner_chat.spans`` named relay, never as an MCP tool, so no model session
and no other daemon can reach it (``butlers.core.service_plane.wiring``).

A span qualifies only on stored transport facts, never on anything a model said:

* the row's source channel is a user client, its stored ``chat_type`` is ``private``
  and its stored ``participant_count`` is at most 2;
* the history entry has ``is_from_me`` explicitly true, ``forwarded`` explicitly
  false (REQ-connector-base-spec-013) and ``is_new`` true;
* the entry's channel fact is explicitly false: WhatsApp ``from_history`` (live
  delivery, not history sync) and Telegram ``via_bot`` (not inline-bot output);
* when the envelope stored the owner's sender identity, the entry's sender identity
  equals it;
* the entry has a parseable event time no more than 14 days old;
* exactly one other identity appears in the row (its senders and history), which is
  the counterparty. Zero or several mean the span is not returned.

Only batched ``conversation_history`` envelopes carry entries. Single-message envelopes
(Telegram backfill, ingest retry and the chat-id fallback) have none, so their messages
are never returned in S1.

Paging is by the stable ``(received_at, id)`` cursor. ``received_at`` is assigned before
the inbox insert commits, so rows younger than 60 seconds by the database clock are not
read yet: a slow ingest transaction commits before the cursor can pass its position.
The cursor advances over every row read, qualifying or not. Any database failure
raises; an empty page always means "nothing more yet", never "could not read".
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Final
from uuid import UUID

OWNER_CHAT_CHANNELS: Final = ("whatsapp_user_client", "telegram_user_client")
#: The per-channel fact that must be explicitly false for the owner to have typed the
#: text on this account. WhatsApp: the entry came from history sync, which can carry
#: text another linked device sent, a past bridge device's sends included (live
#: delivery never echoes the bridge's own sends). Telegram: the entry was posted via an
#: inline bot, whose output appears under the owner's account.
_TYPED_BY_OWNER_FACT: Final = {
    "whatsapp_user_client": "from_history",
    "telegram_user_client": "via_bot",
}
#: Rows read per page. Fixed: the caller chooses nothing but its cursor.
PAGE_ROWS: Final = 200
IN_FLIGHT: Final = timedelta(seconds=60)
MAX_AGE: Final = timedelta(days=14)

_PAGE_SQL: Final = """
SELECT id, received_at, request_context, raw_payload, now() AS db_now
FROM switchboard.message_inbox
WHERE received_at < now() - make_interval(secs => $1)
  AND received_at >= now() - make_interval(secs => $2)
  AND ($3::timestamptz IS NULL OR (received_at, id) > ($3::timestamptz, $4::uuid))
  AND request_context ->> 'source_channel' = ANY($5::text[])
ORDER BY received_at, id
LIMIT $6
"""


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, (str, bytes, bytearray)) else value


def _event_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _row_is_private(context: dict[str, Any]) -> bool:
    count = context.get("participant_count")
    return (
        context.get("chat_type") == "private"
        and isinstance(count, int)
        and not isinstance(count, bool)
        and count <= 2
    )


def _counterparty(context: dict[str, Any], history: list[Any], author: str) -> str | None:
    """The single identity in the row other than the author (who is the stored owner, if any)."""
    identities: set[str] = set()
    senders = context.get("source_sender_identities")
    if isinstance(senders, list):
        identities.update(s for s in senders if isinstance(s, str) and s)
    for entry in history:
        if isinstance(entry, dict) and isinstance(entry.get("sender_identity"), str):
            identities.add(entry["sender_identity"])
    identities.discard(author)
    identities.discard("multiple")
    identities.discard("unknown")
    return next(iter(identities)) if len(identities) == 1 else None


def spans_of_row(row: Any, db_now: datetime) -> list[dict[str, Any]]:
    """The qualifying owner spans of one inbox row, in history order."""
    context = _json(row["request_context"]) or {}
    payload = _json(row["raw_payload"]) or {}
    if not isinstance(context, dict) or not isinstance(payload, dict):
        return []
    if context.get("source_channel") not in OWNER_CHAT_CHANNELS or not _row_is_private(context):
        return []
    raw = (payload.get("payload") or {}).get("raw")
    history = raw.get("conversation_history") if isinstance(raw, dict) else None
    if not isinstance(history, list):
        return []
    owner = context.get("owner_sender_identity")
    owner = owner if isinstance(owner, str) and owner else None
    spans: list[dict[str, Any]] = []
    for entry in history:
        if not isinstance(entry, dict):
            continue
        if entry.get("is_from_me") is not True or entry.get("forwarded") is not False:
            continue
        if entry.get("is_new") is not True:
            continue
        if entry.get(_TYPED_BY_OWNER_FACT[context["source_channel"]]) is not False:
            continue
        author = entry.get("sender_identity")
        message_id = entry.get("message_id")
        text = entry.get("text")
        if not isinstance(author, str) or not author or message_id in (None, ""):
            continue
        if owner is not None and author != owner:
            continue
        if not isinstance(text, str) or not text:
            continue
        event_time = _event_time(entry.get("timestamp"))
        if event_time is None or event_time > db_now or db_now - event_time > MAX_AGE:
            continue
        counterparty = _counterparty(context, history, author)
        if counterparty is None:
            continue
        spans.append(
            {
                "source_channel": context["source_channel"],
                "endpoint_identity": context.get("source_endpoint_identity"),
                "chat_identity": context.get("external_conversation_id"),
                "counterparty_identity": counterparty,
                "provider_message_id": str(message_id),
                "event_time": event_time.isoformat(),
                "ingestion_event_id": str(row["id"]),
                "text": text,
            }
        )
    return spans


async def owner_chat_spans(
    pool: Any, *, after_received_at: datetime | None, after_inbox_id: UUID | None
) -> dict[str, Any]:
    """One page of owner spans after the cursor, and the cursor to pass next.

    The cursor is both values or neither. The returned cursor is the last row read,
    or the given cursor when no row was read. ``more`` is true when the page was full.
    """
    if (after_received_at is None) != (after_inbox_id is None):
        raise ValueError("cursor_incomplete")
    rows = await pool.fetch(
        _PAGE_SQL,
        IN_FLIGHT.total_seconds(),
        MAX_AGE.total_seconds(),
        after_received_at,
        after_inbox_id,
        list(OWNER_CHAT_CHANNELS),
        PAGE_ROWS,
    )
    spans: list[dict[str, Any]] = []
    for row in rows:
        spans.extend(spans_of_row(row, row["db_now"]))
    if rows:
        last = rows[-1]
        cursor = {"received_at": last["received_at"].isoformat(), "inbox_id": str(last["id"])}
    elif after_received_at is not None:
        cursor = {"received_at": after_received_at.isoformat(), "inbox_id": str(after_inbox_id)}
    else:
        cursor = None
    return {"spans": spans, "cursor": cursor, "more": len(rows) == PAGE_ROWS}
