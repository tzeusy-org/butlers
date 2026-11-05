"""Server-derived content authority for memory artifacts (bu-q7vx1q.1).

Every episode, fact and rule records who authored the content that produced it,
derived only from the Switchboard-resolved routing context of the runtime
session (never from tool arguments or text inside content).  Steering-class
reads (Active Rules, Profile Facts, the discovery catalog) admit only
owner-class authority or an explicit owner endorsement, so a stranger's message
can be remembered as an attributed report but never wears the owner's authority.

Authority values:

* ``owner``        -- the routed sender is the owner entity.
* ``owner_device`` -- an owner surface (dashboard) with no sender entity.
* ``third_party``  -- any other routed sender, including an unresolved one.
* ``system``       -- no routing context (scheduled / internal sessions).
* ``mixed``        -- derived from evidence with differing or unknown authority.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from butlers.core.goal_lineage import current_lineage
from butlers.core.owner import fetch_owner_entity_id
from butlers.core.tool_call_capture import (
    current_runtime_session_locator_is_unbound,
    get_current_runtime_session_routing_context,
)

logger = logging.getLogger(__name__)

OWNER = "owner"
OWNER_DEVICE = "owner_device"
THIRD_PARTY = "third_party"
SYSTEM = "system"
MIXED = "mixed"

AUTHORITIES: frozenset[str] = frozenset({OWNER, OWNER_DEVICE, THIRD_PARTY, SYSTEM, MIXED})
OWNER_CLASS: frozenset[str] = frozenset({OWNER, OWNER_DEVICE})

#: SQL predicate shared by every steering-class rule reader (unqualified columns).
#: NULL authority with no endorsement evaluates to NULL, which excludes the row.
RULE_ADMITTED_SQL = "(content_authority IN ('owner', 'owner_device') OR endorsed_at IS NOT NULL)"

#: Consolidation partitions (bu-dhloi1.1, bu-dhloi1.26).  Each partition is
#: consolidated in its own runtime session, so no session can restate one
#: authority's content under another's evidence:
#:
#: * ``owner``     -- owner-class evidence (``owner``, ``owner_device``).
#: * ``system``    -- ``system`` evidence.  Kept apart from third-party content
#:   because a ``system`` fact renders unlabelled: a shared session could
#:   restate a stranger's words citing only a system episode.
#: * ``non_owner`` -- everything a third party may have authored
#:   (``third_party``, ``mixed`` and an unknown NULL); all of it renders labelled.
OWNER_PARTITION = "owner"
SYSTEM_PARTITION = "system"
NON_OWNER_PARTITION = "non_owner"
PARTITIONS: tuple[str, ...] = (OWNER_PARTITION, SYSTEM_PARTITION, NON_OWNER_PARTITION)

#: A fact is held while it is an attributed report from a non-owner source: third-party
#: or mixed authority, no owner vouch, still live.  NULL (legacy) and ``system`` are not
#: held; they are noise rather than a stranger's claim waiting on the owner.
HELD_FACT_SQL = (
    "(content_authority IN ('third_party', 'mixed') AND endorsed_at IS NULL"
    " AND validity IN ('active', 'fading'))"
)

_DASHBOARD_CHANNEL = "dashboard"


@dataclass(frozen=True)
class ContentAuthority:
    """A stamped authority plus the sender entity it was derived from."""

    authority: str
    entity_id: uuid.UUID | None = None


class AuthorityStampError(RuntimeError):
    """Raised when a steering artifact's authority cannot be stamped."""


def is_owner_class(authority: str | None) -> bool:
    return authority in OWNER_CLASS


def partition_of(authority: str | None) -> str:
    """The consolidation partition of one persisted authority (NULL is non-owner)."""
    if is_owner_class(authority):
        return OWNER_PARTITION
    if authority == SYSTEM:
        return SYSTEM_PARTITION
    return NON_OWNER_PARTITION


def partition_sql(partition: str, column: str = "content_authority") -> str:
    """SQL predicate selecting rows of one consolidation partition.

    ``column`` is a reviewed code constant, never caller input.  The non-owner
    predicate admits NULL explicitly, so an unstamped legacy row is never
    treated as owner-class or system.
    """
    if partition == OWNER_PARTITION:
        return f"{column} IN ('owner', 'owner_device')"
    if partition == SYSTEM_PARTITION:
        return f"{column} = 'system'"
    if partition == NON_OWNER_PARTITION:
        return f"COALESCE({column}, '') NOT IN ('owner', 'owner_device', 'system')"
    raise ValueError(f"unknown consolidation partition {partition!r}")


def is_rule_admitted(authority: str | None, endorsed_at: Any) -> bool:
    """True when a rule may steer sessions: owner-class authority or endorsed."""
    return is_owner_class(authority) or endorsed_at is not None


def catalog_attribution(authority: str | None) -> str | None:
    """Label a cross-butler catalog item the owner did not author.

    The catalog stores only the authority class (no sender entity), so the
    label names the class: ``from a third party`` / ``partly from a third
    party``, or ``source unverified`` for a legacy row with no stamp.  Owner
    and ``system`` content carries no label.
    """
    if is_owner_class(authority) or authority == SYSTEM:
        return None
    if authority is None:
        return "source unverified"
    if authority == MIXED:
        return "partly from a third party"
    return "from a third party"


def is_fact_held(authority: str | None, endorsed_at: Any, validity: str | None) -> bool:
    """True when a live fact is a non-owner report the owner has not vouched for."""
    return (
        authority in (THIRD_PARTY, MIXED)
        and endorsed_at is None
        and validity in ("active", "fading")
    )


def _parse_uuid(value: object) -> uuid.UUID | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return uuid.UUID(value.strip())
    except ValueError:
        return None


def classify_routing_context(
    routing_context: Mapping[str, Any] | None,
    owner_entity_id: uuid.UUID | None,
) -> ContentAuthority:
    """Pure authority derivation from a routing context and the owner entity."""
    if not isinstance(routing_context, Mapping) or not routing_context:
        return ContentAuthority(SYSTEM)

    sender = _parse_uuid(routing_context.get("source_entity_id"))
    if sender is not None:
        if owner_entity_id is not None and sender == owner_entity_id:
            return ContentAuthority(OWNER, sender)
        return ContentAuthority(THIRD_PARTY, sender)

    request_context = routing_context.get("request_context")
    channel = (
        request_context.get("source_channel") if isinstance(request_context, Mapping) else None
    )
    if channel == _DASHBOARD_CHANNEL:
        return ContentAuthority(OWNER_DEVICE, owner_entity_id)
    # Routed, but the sender never resolved to an entity: not provably the owner.
    return ContentAuthority(THIRD_PARTY)


async def resolve_content_authority(
    pool: Any,
    routing_context: Mapping[str, Any] | None = None,
) -> ContentAuthority:
    """Derive authority for a write from the (explicit or current) routing context.

    ``routing_context=None`` reads the current runtime session's context; callers
    outside that task (the Spawner's episode hook) pass the context explicitly.

    A broker-forwarded call (RFC 0038 D7) derives authority from its verified
    goal lineage only, never from a spawner-derived session: exposed or
    third-party lineage is ``third_party``, unexposed owner lineage is
    ``owner``, obligation and staffer lineage is ``system``.
    """
    lineage = current_lineage()
    if lineage is not None:
        if lineage.exposed or lineage.origin == "third_party":
            return ContentAuthority(THIRD_PARTY)
        if lineage.origin == "owner":
            return ContentAuthority(OWNER, await fetch_owner_entity_id(pool))
        return ContentAuthority(SYSTEM)
    if routing_context is None:
        routing_context = get_current_runtime_session_routing_context()
        if not routing_context and current_runtime_session_locator_is_unbound():
            # The request named a session by locator without a valid invocation binding:
            # not provably the owner, and not a daemon-internal caller either (bu-m2zcah).
            return ContentAuthority(THIRD_PARTY)
    if not routing_context:
        return ContentAuthority(SYSTEM)
    owner_entity_id = await fetch_owner_entity_id(pool)
    return classify_routing_context(routing_context, owner_entity_id)


def combine_authorities(authorities: Iterable[str | None]) -> str:
    """Weakest authority across evidence: identical stays, anything else is mixed.

    Unknown (NULL) evidence and an empty evidence set both yield ``mixed``.
    """
    values = list(authorities)
    if not values or any(a is None for a in values):
        return MIXED
    distinct = set(values)
    if distinct == {OWNER_DEVICE}:
        return OWNER_DEVICE
    if distinct <= OWNER_CLASS:
        return OWNER
    if len(distinct) == 1:
        return next(iter(distinct))
    return MIXED


def validate_authority(authority: str) -> str:
    if authority not in AUTHORITIES:
        raise AuthorityStampError(f"invalid content_authority {authority!r}")
    return authority
