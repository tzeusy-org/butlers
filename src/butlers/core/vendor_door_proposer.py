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

PROPOSER_ROLE = "butler_switchboard_rw"
MAX_TEXT_CHARS = 200_000
MAX_CANDIDATES = 200

_URL = re.compile(r"https://[^\s<>\"'`\\^{}|\[\]()]+", re.IGNORECASE)
_SLOT = re.compile(r"\{([a-z_]+)\}")
_SLOT_VALUE = r"[A-Za-z0-9._~-]{1,64}"
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
    if not template.startswith("https://"):
        return None
    rest = template[len("https://") :]
    host, slash, path = rest.partition("/")
    if not host or not slash:
        return None
    pieces: list[str] = []
    last = 0
    path = "/" + path
    for slot in _SLOT.finditer(path):
        pieces.append(re.escape(path[last : slot.start()]))
        pieces.append(_SLOT_VALUE)
        last = slot.end()
    pieces.append(re.escape(path[last:]))
    return re.compile(re.escape("https://" + host.lower()) + "".join(pieces), re.ASCII)


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
