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
from collections.abc import Awaitable, Callable
from typing import Any, Protocol
from urllib.parse import urlsplit

from butlers.core import vendor_doors as flag
from butlers.core.tool_call_capture import get_current_runtime_session_routing_context

PROPOSER_ROLE = "butler_switchboard_rw"
MAX_TEXT_CHARS = 200_000
MAX_CANDIDATES = 200
MAX_TEMPLATE_CHARS = 300
MAX_SLOTS = 8

_URL = re.compile(r"https://[^\s<>\"'`\\^{}|\[\]()]+", re.IGNORECASE)
_SLOT = re.compile(r"\{([a-z_]+)\}")
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


def _parse_template(template: str) -> tuple[str, list[str | None]] | None:
    """Split a template into ``(host, segments)``; a segment is its literal text or ``None``.

    ``None`` is a slot, which must be the whole segment (``{name}``): a slot inside a segment,
    two slots in one segment, a slot in the host, an empty segment or a stray brace is not a
    template. Over-long or over-slotted input is refused before any work.
    """
    if len(template) > MAX_TEMPLATE_CHARS or not template.startswith("https://"):
        return None
    host, slash, path = template[len("https://") :].partition("/")
    if not host or not slash or "{" in host or "}" in host:
        return None
    segments: list[str | None] = []
    for segment in path.split("/"):
        if _SLOT.fullmatch(segment):
            segments.append(None)
        elif not segment or "{" in segment or "}" in segment:
            return None
        else:
            segments.append(segment)
    if segments.count(None) > MAX_SLOTS:
        return None
    return host.lower(), segments


def _matches(parsed: tuple[str, list[str | None]], url: str) -> bool:
    host, segments = parsed
    parts = urlsplit(url)
    if parts.netloc != host:
        return False
    candidate = parts.path.split("/")[1:]
    if len(candidate) != len(segments):
        return False
    return all(
        (want is None and bool(have)) or want == have
        for want, have in zip(segments, candidate, strict=True)
    )


def template_in_event(template: str, text: str | None) -> bool:
    """True when the template matches a URL the event itself carries.

    Compared segment by segment (linear, no regex over the path): scheme and host equal, the
    same number of path segments, literal segments equal, each slot one non-empty segment. The
    template never carries a query or fragment; a content URL's query and fragment are dropped
    before matching, because they are never part of a door.
    """
    parsed = _parse_template(template)
    if parsed is None or not text:
        return False
    return any(_matches(parsed, url) for url in candidate_urls(text))


class _Connection(Protocol):
    def transaction(self, **kwargs: Any) -> Any: ...
    async def execute(self, sql: str, *args: Any) -> Any: ...
    async def fetchval(self, sql: str, *args: Any) -> Any: ...


# Reads the stored text of one *ingested* event under the connection's current role. The
# Switchboard owns that schema, so its tool supplies the reader; this module holds no
# Switchboard SQL.
EventTextReader = Callable[[Any, uuid.UUID], Awaitable[str | None]]


def _refused(reason: str) -> dict[str, Any]:
    return {"outcome": "refused", "reason": reason}


async def propose_from_event(
    conn: _Connection,
    *,
    event_id: uuid.UUID,
    kind: str,
    template: str,
    slot_types: dict[str, Any],
    read_text: EventTextReader,
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
        text = await read_text(conn, event_id)
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
    pool: _Pool, *, event_id: str, kind: str, url_template: str, read_text: EventTextReader
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
            read_text=read_text,
        )
    out: dict[str, Any] = {"status": result.get("outcome", "refused")}
    if result.get("reason"):
        out["reason"] = result["reason"]
    return out
