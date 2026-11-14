"""Email-derived vendor-door proposals, bound to the event they came from (bu-jv1moa.40).

``public.vendor_door_propose`` already derives origin from the calling role and the sender
verdict and address from ``public.ingestion_events``; a caller can claim neither. What the
database cannot do is compare a template with message content. This module does that: a template
is proposed only if it matches an https URL that appears in the stored text of the very event
whose id is passed, so a caller cannot attach a template to someone else's event or invent one
the vendor never sent. Nothing about verdict, sender, origin or tier is accepted from the caller.

Dark behind ``VENDOR_DOORS_LIVE``.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, Protocol
from urllib.parse import urlsplit

from butlers.core import vendor_doors as flag
from butlers.core.tool_call_capture import get_current_runtime_session_routing_context

PROPOSER_ROLE = "butler_switchboard_rw"
MAX_TEXT_CHARS = 200_000
MAX_CANDIDATES = 200

_URL = re.compile(r"https://[^\s<>\"'`\\^{}|\[\]()]+", re.IGNORECASE)
_SLOT = re.compile(r"\{([a-z_]+)\}")
_SLOT_VALUE = r"[^/?#]+"
_TRAILING = ".,;:!?"


def candidate_urls(text: str) -> list[str]:
    """The https URLs in the event text, without query, fragment or trailing punctuation."""
    found: list[str] = []
    for match in _URL.finditer(text[:MAX_TEXT_CHARS]):
        url = match.group(0).rstrip(_TRAILING)
        parts = urlsplit(url)
        if not parts.netloc:
            continue
        found.append(f"https://{parts.netloc.lower()}{parts.path}")
        if len(found) >= MAX_CANDIDATES:
            break
    return found


def _template_pattern(template: str) -> re.Pattern[str] | None:
    """Compile a template to a full-match pattern, or None when it is not well formed.

    Slots stand only for whole path segments (``{name}`` and nothing else in the segment), never
    in the scheme or host; each becomes one segment ``[^/?#]+``. Every other segment is copied
    literally, so the template can only match a URL that carries those exact segments.
    """
    if not template.startswith("https://"):
        return None
    host, slash, path = template[len("https://") :].partition("/")
    if not host or not slash or re.search(r"[{}]", host):
        return None
    pieces: list[str] = []
    for segment in path.split("/"):
        if _SLOT.fullmatch(segment):
            pieces.append(_SLOT_VALUE)
        elif re.search(r"[{}]", segment) or segment == "":
            return None  # a slot inside a segment, a stray brace, or an empty/trailing segment
        else:
            pieces.append(re.escape(segment))
    return re.compile(re.escape("https://" + host.lower()) + "/" + "/".join(pieces), re.ASCII)


def template_in_event(template: str, text: str | None) -> bool:
    """True when the template matches a URL the event itself carries (host exact, path by slot)."""
    pattern = _template_pattern(template)
    if pattern is None or not text:
        return False
    return any(pattern.fullmatch(url) for url in candidate_urls(text))


class _Connection(Protocol):
    def transaction(self, **kwargs: Any) -> Any: ...
    async def execute(self, sql: str, *args: Any) -> Any: ...
    async def fetchval(self, sql: str, *args: Any) -> Any: ...


def _refused(reason: str) -> dict[str, Any]:
    return {"outcome": "refused", "reason": reason}


async def propose_from_event(
    conn: _Connection,
    *,
    event_id: uuid.UUID,
    kind: str,
    template: str,
    slot_types: dict[str, Any],
    vendor_entity_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Propose a door found in one stored ingestion event, or refuse without writing.

    The caller supplies only a candidate; the event's own text decides whether it is accepted.
    Runs under the Switchboard runtime role so the database derives origin ``email``.
    """
    if not flag.VENDOR_DOORS_LIVE:
        return _refused("dark")
    async with conn.transaction():
        await conn.execute(f"SET LOCAL ROLE {PROPOSER_ROLE}")
        text = await conn.fetchval(
            "SELECT normalized_text FROM switchboard.message_inbox WHERE id = $1", event_id
        )
        if text is None:
            return _refused("event")
        if not template_in_event(template, text):
            return _refused("not_in_event")
        raw = await conn.fetchval(
            "SELECT public.vendor_door_propose($1, $2, $3, $4, $5, $6)",
            kind,
            template,
            slot_types,
            event_id,
            vendor_entity_id,
            {},
        )
    return json.loads(raw) if isinstance(raw, str) else dict(raw)


# A slot's name is its type: the database accepts only these three, so the tool does too.
SLOT_NAMES = ("order_ref", "booking_ref", "account_ref")


class _Pool(Protocol):
    def acquire(self) -> Any: ...


async def propose_for_session(
    pool: _Pool, *, event_id: str, kind: str, url_template: str
) -> dict[str, Any]:
    """The MCP tool body: the model names a candidate, the session's own request decides.

    ``event_id`` must be the request this runtime session was routed (never another event), and
    the template must then match a URL in that request's stored text. While the flag is off the
    answer is ``{"status": "disabled"}`` and nothing is read or written.
    """
    if not flag.VENDOR_DOORS_LIVE:
        return {"status": "disabled"}
    try:
        event = uuid.UUID(event_id)
    except (ValueError, AttributeError, TypeError):
        return {"status": "refused", "reason": "event"}
    routed = get_current_runtime_session_routing_context() or {}
    if str(routed.get("request_id") or "").lower() != str(event):
        return {"status": "refused", "reason": "event_not_in_session"}
    names = _SLOT.findall(url_template)
    if any(name not in SLOT_NAMES for name in names):
        return {"status": "refused", "reason": "slot_type"}
    async with pool.acquire() as conn:
        result = await propose_from_event(
            conn,
            event_id=event,
            kind=kind,
            template=url_template,
            slot_types={name: name for name in names},
        )
    out: dict[str, Any] = {"status": result.get("outcome", "refused")}
    if result.get("reason"):
        out["reason"] = result["reason"]
    return out
