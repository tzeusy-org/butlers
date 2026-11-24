"""Deterministic attendee resolution (REQ-module-calendar-044, bu-5ii4gj.3).

A pure function over exact normalized addresses and the active, live ``has-email`` claimants the
caller read for them. It uses no model, no name, no domain or prefix, and never creates an
entity: an address with exactly one live claimant resolves to it, two or more is ``ambiguous``
(and names no one), none is ``unresolved``. The owner's own address and a room or resource address
are their own classes. Third-party text never reaches this function: only the address does.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final, Literal

RESOLVER_VERSION: Final = "1"
MAX_ADDRESSES: Final = 200  # the definer's cap; callers batch at this size

Resolution = Literal["exact", "ambiguous", "unresolved", "owner", "resource", "manual"]

_RESOURCE_DOMAIN: Final = "resource.calendar.google.com"


@dataclass(frozen=True, slots=True)
class Claim:
    """One live entity holding an address as an active ``has-email`` fact."""

    email_norm: str
    entity_id: uuid.UUID
    is_owner: bool = False


@dataclass(frozen=True, slots=True)
class Resolved:
    resolution: Resolution
    entity_id: uuid.UUID | None = None


#: The one definition of "the same address", shared with the SQL twins in
#: ``butlers.modules.calendar.participants``: trim ASCII whitespace, lowercase ASCII letters only
#: (no locale or Unicode case folding), and refuse any ASCII whitespace inside.
ASCII_WHITESPACE = " \t\n\r\f\v"
_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def normalize_email(raw: object) -> str | None:
    """Trim and lowercase one address (ASCII only); ``None`` when it is not one plain address."""
    if not isinstance(raw, str):
        return None
    address = raw.strip(ASCII_WHITESPACE).translate(_ASCII_LOWER)
    local, at, domain = address.partition("@")
    if not at or not local or not domain or "@" in domain:
        return None
    if any(c in ASCII_WHITESPACE for c in address):
        return None
    return address


def is_resource_address(email_norm: str) -> bool:
    """A Google room or equipment calendar, never a person."""
    return email_norm.rpartition("@")[2] == _RESOURCE_DOMAIN


def resolve_attendee_emails(emails: Iterable[str], claims: Iterable[Claim]) -> dict[str, Resolved]:
    """One resolution per normalized address; the same inputs always give the same answer."""
    by_address: dict[str, dict[uuid.UUID, bool]] = {}
    for claim in claims:
        by_address.setdefault(claim.email_norm, {})[claim.entity_id] = (
            by_address.get(claim.email_norm, {}).get(claim.entity_id, False) or claim.is_owner
        )
    resolved: dict[str, Resolved] = {}
    for email in emails:
        if is_resource_address(email):
            resolved[email] = Resolved("resource")
            continue
        found = by_address.get(email, {})
        owners = sorted(entity for entity, is_owner in found.items() if is_owner)
        if owners:
            # An address the owner holds is the owner's, however many other entities claim it.
            resolved[email] = Resolved("owner", owners[0] if len(owners) == 1 else None)
        elif len(found) == 1:
            resolved[email] = Resolved("exact", next(iter(found)))
        elif found:
            resolved[email] = Resolved("ambiguous")
        else:
            resolved[email] = Resolved("unresolved")
    return resolved
