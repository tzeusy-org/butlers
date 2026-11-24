"""Resolve a vendor-door reference to a link, or to nothing (bu-jv1moa.40, REQ-vendor-doors-004).

The sender and the receiver each call this independently; neither trusts anything the other put
on the wire. A reference resolves only when a *current confirmed* door of the live owner (the
rows of ``public.vendor_doors_current``) has exactly the referenced version, every slot is
present and well typed, and the rebuilt URL passes the door rules again. Every other case
(missing, revoked, stale, foreign, proposal, read failure, timeout, exposure, flag off) gives
``None``, and the caller degrades to ``open_dashboard`` plus ``[link not verified]``.

Resolution is a read. It makes no broker call, has no effect and records no evidence, so a
click spends nothing. A cancelled read propagates ``CancelledError``: nothing is delivered, so
no partial link can leave.

The connection's role must be allowed to read the view (Finance, Switchboard, Messenger).
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlsplit

from butlers.core import vendor_doors as flag
from butlers.core.registrable_domain import registrable_domain

READ_TIMEOUT_S = 2.0
MAX_URL_CHARS = 300
PROVENANCE = "vendor_door"

# A slot's name is its type. Values never contain / ? # % @ : and never start with a dot, so a
# value cannot add a segment, a query, an authority or a dot segment.
SLOT_VALUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", re.ASCII)
SLOT_TYPES = frozenset({"order_ref", "booking_ref", "account_ref"})

_SLOT = re.compile(r"\{([a-z_]+)\}")
_HOST = re.compile(r"[a-z0-9.-]+", re.ASCII)
_PATH = re.compile(r"(/[A-Za-z0-9._~{}-]*)+", re.ASCII)
_DOT_SEGMENT = re.compile(r"(^|/)\.{1,2}(/|$)")

_READ = (
    "SELECT id, version, kind, host, template, slot_types "
    "FROM public.vendor_doors_current WHERE id = $1"
)


@dataclass(frozen=True)
class ResolvedDoor:
    """What a delivered link is built from; every field is derived here, none from the wire."""

    door_id: uuid.UUID
    version: int
    kind: str
    url: str
    host: str  # the registrable domain, shown to the owner
    label: str
    provenance: str = PROVENANCE
    link_preview: bool = False


class _Reader(Protocol):
    async def fetchrow(self, query: str, *args: Any) -> Any: ...


def build_url(template: str, slot_types: Any, slots: dict[str, str], host: str) -> str | None:
    """The URL for one template and slot set, or ``None`` when any door rule fails."""
    if not isinstance(template, str) or not template.startswith("https://"):
        return None
    if isinstance(slot_types, str):
        try:
            slot_types = json.loads(slot_types)
        except ValueError:
            return None
    if not isinstance(slot_types, dict) or not isinstance(slots, dict):
        return None
    rest = template[len("https://") :]
    template_host, _, tail = rest.partition("/")
    path = "/" + tail
    if not _HOST.fullmatch(template_host) or registrable_domain(template_host) != host:
        return None
    if not _PATH.fullmatch(path) or _DOT_SEGMENT.search(path):
        return None
    names = set(_SLOT.findall(path))
    stripped = _SLOT.sub("", path)
    if "{" in stripped or "}" in stripped:
        return None
    if set(slot_types) != names or set(slots) != names:
        return None
    for name in names:
        if slot_types[name] not in SLOT_TYPES:
            return None
        value = slots[name]
        if not isinstance(value, str) or not SLOT_VALUE.fullmatch(value):
            return None
    url = "https://" + template_host + _SLOT.sub(lambda m: slots[m.group(1)], path)
    if len(url) > MAX_URL_CHARS or not _same_shape(url, template_host, path):
        return None
    return url


def _same_shape(url: str, template_host: str, template_path: str) -> bool:
    """The rebuilt URL must still parse to the template's host and segment count.

    A last check independent of the slot regex: no query, fragment, userinfo, port or escape,
    no dot segment, and one path segment for each segment of the template.
    """
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    if (
        parts.scheme != "https"
        or parts.hostname != template_host
        or port is not None
        or parts.username is not None
        or parts.query
        or parts.fragment
        or "%" in url
        or "?" in url
        or "#" in url
    ):
        return False
    segments = parts.path.split("/")
    return len(segments) == len(template_path.split("/")) and ".." not in segments


async def resolve_door(
    conn: _Reader,
    *,
    door_id: uuid.UUID,
    version: int,
    slots: dict[str, str],
    exposed: bool = False,
    deadline_s: float = READ_TIMEOUT_S,
) -> ResolvedDoor | None:
    """The live link for a reference, or ``None`` to degrade to the inert form."""
    if not flag.VENDOR_DOORS_LIVE or exposed:
        return None
    try:
        row = await asyncio.wait_for(conn.fetchrow(_READ, door_id), deadline_s)
    except Exception:  # read failure or timeout: inert, never a partial link
        return None
    if row is None or row["version"] != version or row["id"] != door_id:
        return None
    host = row["host"]
    url = build_url(row["template"], row["slot_types"], slots, host)
    if url is None or not isinstance(host, str) or registrable_domain(host) != host:
        return None
    return ResolvedDoor(
        door_id=door_id,
        version=version,
        kind=row["kind"],
        url=url,
        host=host,
        label=f"Open {host} ({PROVENANCE})",
    )
