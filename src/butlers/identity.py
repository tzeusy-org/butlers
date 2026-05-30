"""Shared identity resolution utilities.

Provides ``resolve_contact_by_channel`` — the canonical reverse-lookup that
maps a channel identifier (type + value) to a known entity and their
associated roles and entity_id.

Migration bead 7 (bu-akads): reads from ``relationship.entity_facts`` triples
(predicate ``has-handle``, ``has-email``, ``has-phone``) joined to
``public.entities``.  ``public.contact_info`` / ``public.contacts`` are no
longer consulted by the primary resolution path.

Used by:
- Switchboard ingestion path (before routing) to inject sender identity preambles.
- notify() to resolve outbound recipients from contact_id.
- Approval gate to replace name-heuristic with role-based target resolution.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import asyncpg

# WhatsApp individual-chat JID suffix for s.whatsapp.net domain.
_WHATSAPP_INDIVIDUAL_JID_SUFFIX = "@s.whatsapp.net"
# Regex to extract the E.164-prefix phone number from a WhatsApp individual JID.
# Matches numeric prefixes like "1234567890" from "1234567890@s.whatsapp.net".
_WHATSAPP_JID_PHONE_RE = re.compile(r"^(\d+)@s\.whatsapp\.net$")

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Channel-type → relationship.entity_facts predicate mapping (bead 7 cut-over)
# Must stay in sync with
# relationship_assert_fact._CI_TYPE_TO_PREDICATE
# ---------------------------------------------------------------------------
_CHANNEL_TYPE_TO_PREDICATE: dict[str, str] = {
    "email": "has-email",
    "phone": "has-phone",
    "telegram": "has-handle",
    "telegram_user_id": "has-handle",
    "telegram_user_client": "has-handle",
    "linkedin": "has-handle",
    "twitter": "has-handle",
    "website": "has-website",
    "other": "has-handle",
    "whatsapp_jid": "has-handle",
}


def _extract_whatsapp_jid_phone(jid: str) -> str | None:
    """Extract E.164-prefix phone number from a WhatsApp individual JID.

    Parameters
    ----------
    jid:
        WhatsApp JID string (e.g., ``"1234567890@s.whatsapp.net"``).

    Returns
    -------
    str | None
        The phone number string (e.g., ``"1234567890"``), or ``None`` if the
        JID is not an individual JID (e.g., group JIDs ending in ``@g.us``).
    """
    m = _WHATSAPP_JID_PHONE_RE.match(jid)
    return m.group(1) if m else None


@dataclass(frozen=True)
class ResolvedContact:
    """Resolved contact identity from a channel reverse-lookup.

    Attributes
    ----------
    contact_id:
        UUID of the resolved contact in public.contacts.  May be ``None``
        after the bead-7 cut-over when resolution goes through
        ``relationship.entity_facts`` (entity_id is the authoritative key).
    name:
        Display name of the contact (may be ``None`` if not set).
    roles:
        List of roles assigned to the linked entity (e.g., ``['owner']``).
        Sourced from ``public.entities.roles``.
    entity_id:
        UUID of the linked entity in public.entities, or ``None`` if not linked.
    """

    contact_id: UUID | None
    name: str | None
    roles: list[str]
    entity_id: UUID | None


async def _resolve_entity_by_triple(
    pool: asyncpg.Pool,
    predicate: str,
    object_value: str,
) -> asyncpg.Record | None:
    """Query ``relationship.entity_facts`` for an active triple and join entity info.

    Returns a row with ``entity_id``, ``name`` (canonical_name), and ``roles``,
    or ``None`` when not found or on DB error.
    """
    try:
        return await pool.fetchrow(
            """
            SELECT ef.subject                     AS entity_id,
                   e.canonical_name               AS name,
                   COALESCE(e.roles, '{}')        AS roles
            FROM   relationship.entity_facts ef
            JOIN   public.entities e ON e.id = ef.subject
            WHERE  ef.predicate    = $1
              AND  ef.object       = $2
              AND  ef.object_kind  = 'literal'
              AND  ef.validity     = 'active'
            LIMIT  1
            """,
            predicate,
            object_value,
        )
    except Exception:  # noqa: BLE001
        return None


async def resolve_contact_by_channel(
    pool: asyncpg.Pool,
    channel_type: str,
    channel_value: str,
) -> ResolvedContact | None:
    """Resolve an entity from a channel identifier via ``relationship.entity_facts``.

    Queries ``relationship.entity_facts`` to map a channel identifier to a known
    entity.  Roles and canonical name are read from ``public.entities``.
    Returns ``None`` when no entity is found for the given (type, value) pair.

    Migration bead 7 (bu-akads): this function now queries the triples store
    (``relationship.entity_facts``) directly, using predicates ``has-handle``,
    ``has-email``, and ``has-phone``.  ``public.contact_info`` / ``public.contacts``
    are no longer consulted.

    Parameters
    ----------
    pool:
        asyncpg connection pool.  The executing role must have at minimum
        ``SELECT`` on ``relationship.entity_facts`` and ``public.entities``.
    channel_type:
        The channel type (e.g., ``"telegram"``, ``"email"``).
    channel_value:
        The channel value (e.g., a Telegram chat ID string or an email address).

    Returns
    -------
    ResolvedContact | None
        A populated ``ResolvedContact`` on success, or ``None`` if no match
        is found or the tables do not yet exist.

    Notes
    -----
    - ``entity_id`` is the authoritative key post bead 7.  ``contact_id`` on
      the returned dataclass will be ``None`` since we no longer query
      ``public.contacts``.
    - This function is safe to call if the migration has not yet run —
      it returns ``None`` gracefully.
    """
    predicate = _CHANNEL_TYPE_TO_PREDICATE.get(channel_type)
    row: asyncpg.Record | None = None

    if predicate is not None:
        try:
            row = await _resolve_entity_by_triple(pool, predicate, channel_value)
        except Exception:  # noqa: BLE001
            logger.debug(
                "resolve_contact_by_channel: DB query failed "
                "(table may not exist yet); returning None",
                exc_info=True,
            )
            return None

    if row is None and channel_type == "telegram_user_client":
        # telegram_user_client fallback: try has-handle with telegram: prefix
        telegram_value = (
            channel_value if channel_value.startswith("telegram:") else f"telegram:{channel_value}"
        )
        try:
            row = await _resolve_entity_by_triple(pool, "has-handle", telegram_value)
        except Exception:  # noqa: BLE001
            logger.debug(
                "resolve_contact_by_channel: telegram_user_client fallback query failed",
                exc_info=True,
            )
            return None

    if row is None:
        # WhatsApp JID fallback: if no direct match, try phone-number cross-reference.
        # Extracts the E.164 phone prefix from "<number>@s.whatsapp.net" JIDs and
        # queries has-phone to link against entities from other providers
        # (e.g. Google Contacts) that share the same number.
        if channel_type == "whatsapp_jid":
            phone = _extract_whatsapp_jid_phone(channel_value)
            if phone is not None:
                try:
                    row = await _resolve_entity_by_triple(pool, "has-phone", phone)
                except Exception:  # noqa: BLE001
                    logger.debug(
                        "resolve_contact_by_channel: phone fallback query failed; returning None",
                        exc_info=True,
                    )
                    return None
        if row is None:
            return None

    entity_id = row["entity_id"]
    if not isinstance(entity_id, UUID):
        try:
            entity_id = UUID(str(entity_id))
        except (ValueError, AttributeError):
            return None

    raw_roles = row["roles"]
    if isinstance(raw_roles, (list, tuple)):
        roles = [str(r) for r in raw_roles]
    else:
        roles = []

    return ResolvedContact(
        contact_id=None,  # entity_id is now the authoritative key (bead 7)
        name=row["name"] or None,
        roles=roles,
        entity_id=entity_id,
    )


async def create_temp_contact(
    pool: asyncpg.Pool,
    channel_type: str,
    channel_value: str,
    display_name: str | None = None,
) -> ResolvedContact | None:
    """Create a temporary contact and entity for an unknown sender.

    Creates a ``public.entities`` entry with ``metadata.unidentified = true``
    and a ``public.contacts`` entry linked to it (with
    ``metadata.needs_disambiguation = true``), then asserts the sender's channel
    identifier as a triple in ``relationship.entity_facts`` via the central
    writer ``relationship_assert_fact()``.

    Write-path cut-over (bu-k9ylx): the channel identifier is NO LONGER written
    to ``public.contact_info`` (that table is read-only). Existing-sender
    detection now queries the triple store (the same path
    ``resolve_contact_by_channel()`` uses after the bead-7 read cut-over).

    Parameters
    ----------
    pool:
        asyncpg connection pool.  Role must have INSERT on public.entities and
        public.contacts; the channel triple is written via the central writer.
    channel_type:
        Channel type (e.g., ``"telegram"``).
    channel_value:
        Channel value (the raw sender identifier).
    display_name:
        Optional human-readable name for the contact.  Defaults to a
        synthesized ``"Unknown ({channel_type} {channel_value})"`` label.

    Returns
    -------
    ResolvedContact | None
        The newly created (or pre-existing) contact, or ``None`` on error.
    """
    name = display_name or f"Unknown ({channel_type} {channel_value})"

    try:
        # Re-check via the triple store to avoid double-creation: if the channel
        # identifier already resolves to an entity, return that instead of
        # minting a duplicate.  This mirrors resolve_contact_by_channel().
        existing_resolved = await resolve_contact_by_channel(pool, channel_type, channel_value)
        if existing_resolved is not None:
            return existing_resolved

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Re-check under the transaction (on the acquired connection) to
                # close the duplicate-creation race: two concurrent callers can
                # both pass the pre-transaction lookup above and each mint a
                # duplicate unidentified entity/contact for the same channel.
                # Re-resolving here on ``conn`` collapses that window — if the
                # channel now resolves, return it instead of creating a dup.
                existing_in_txn = await resolve_contact_by_channel(
                    conn, channel_type, channel_value
                )
                if existing_in_txn is not None:
                    return existing_in_txn

                # Create an unidentified entity so facts can be anchored.
                entity_metadata: dict[str, Any] = {
                    "unidentified": True,
                    "source_channel": channel_type,
                    "source_value": channel_value,
                }
                entity_id: UUID = await conn.fetchval(
                    """
                    INSERT INTO public.entities
                        (canonical_name, entity_type, aliases, metadata, roles)
                    VALUES ($1, 'person', '{}', $2, '{}')
                    RETURNING id
                    """,
                    name,
                    entity_metadata,
                )

                # Create the contact linked to the entity.
                contact_metadata: dict[str, Any] = {
                    "needs_disambiguation": True,
                    "source_channel": channel_type,
                    "source_value": channel_value,
                }
                contact_row: asyncpg.Record = await conn.fetchrow(
                    """
                    INSERT INTO public.contacts (name, entity_id, metadata)
                    VALUES ($1, $2, $3)
                    RETURNING id, name, entity_id
                    """,
                    name,
                    entity_id,
                    contact_metadata,
                )
                contact_id: UUID = contact_row["id"]

        # Write-path cut-over (bu-k9ylx): assert the channel identifier as a
        # triple via the central writer.  No public.contact_info write.
        predicate = _CHANNEL_TYPE_TO_PREDICATE.get(channel_type)
        if predicate is not None:
            try:
                from butlers.tools.relationship.relationship_assert_fact import (
                    relationship_assert_fact,
                )

                await relationship_assert_fact(
                    pool,
                    entity_id,
                    predicate,
                    channel_value,
                    src="identity",
                    object_kind="literal",
                    primary=True,
                )
            except Exception:  # noqa: BLE001 — never block temp-contact creation
                logger.warning(
                    "create_temp_contact: relationship_assert_fact failed for entity %s "
                    "(channel_type=%r, value=%r) — channel triple not written",
                    entity_id,
                    channel_type,
                    channel_value,
                    exc_info=True,
                )
        else:
            logger.debug(
                "create_temp_contact: no predicate mapping for channel_type=%r; "
                "channel triple not written",
                channel_type,
            )

        return ResolvedContact(
            contact_id=contact_id,
            name=name,
            roles=[],
            entity_id=entity_id,
        )

    except Exception:  # noqa: BLE001
        logger.warning(
            "create_temp_contact: failed to create temporary contact for %s/%s",
            channel_type,
            channel_value,
            exc_info=True,
        )
        return None


def build_identity_preamble(
    resolved: ResolvedContact | None,
    channel: str,
    temp_contact_id: UUID | None = None,
    temp_entity_id: UUID | None = None,
) -> str:
    """Build the structured identity preamble for a routed prompt.

    Migration bead 7 (bu-akads): ``contact_id`` is no longer included in the
    preamble output.  ``entity_id`` is the canonical identifier.

    Parameters
    ----------
    resolved:
        A ``ResolvedContact`` for a known sender, or ``None`` for unknown.
    channel:
        The source channel (e.g., ``"telegram"``).
    temp_contact_id:
        Kept for backward compatibility with ``create_temp_contact`` callers.
        No longer emitted in the preamble string.
    temp_entity_id:
        entity_id of the temporary contact (may be ``None``).

    Returns
    -------
    str
        A formatted preamble line, e.g.:
        - ``"[Source: Owner (entity_id: <uuid>), via telegram]"``
        - ``"[Source: Chloe (entity_id: <uuid>), via telegram]"``
        - ``"[Source: Unknown sender (entity_id: <uuid>), via telegram --
          pending disambiguation]"``
    """
    if resolved is not None:
        eid = resolved.entity_id
        if "owner" in resolved.roles:
            if eid is not None:
                return f"[Source: Owner (entity_id: {eid}), via {channel}]"
            return f"[Source: Owner, via {channel}]"
        # Known non-owner
        name = resolved.name or "Unknown Contact"
        if eid is not None:
            return f"[Source: {name} (entity_id: {eid}), via {channel}]"
        return f"[Source: {name}, via {channel}]"

    # Unknown sender
    if temp_entity_id is not None:
        return (
            f"[Source: Unknown sender (entity_id: {temp_entity_id}), "
            f"via {channel} -- pending disambiguation]"
        )
    if temp_contact_id is not None:
        # Fallback for create_temp_contact which always returns a contact_id
        return (
            f"[Source: Unknown sender (contact_id: {temp_contact_id}), "
            f"via {channel} -- pending disambiguation]"
        )

    return f"[Source: Unknown sender, via {channel} -- pending disambiguation]"


__all__ = [
    "ResolvedContact",
    "build_identity_preamble",
    "create_temp_contact",
    "resolve_contact_by_channel",
]
