"""Shared identity resolution utilities.

Provides ``resolve_contact_by_channel`` — the canonical reverse-lookup that
maps a channel identifier (type + value) to a known contact and their
associated roles and entity_id.

Used by:
- Switchboard ingestion path (before routing) to inject sender identity preambles.
- notify() to resolve outbound recipients from contact_id.
- Approval gate to replace name-heuristic with role-based target resolution.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import asyncpg

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResolvedContact:
    """Resolved contact identity from a channel reverse-lookup.

    Attributes
    ----------
    contact_id:
        UUID of the resolved contact in shared.contacts.
    name:
        Display name of the contact (may be ``None`` if not set).
    roles:
        List of roles assigned to the contact (e.g., ``['owner']``).
    entity_id:
        UUID of the linked memory entity, or ``None`` if not linked.
    """

    contact_id: UUID
    name: str | None
    roles: list[str]
    entity_id: UUID | None


async def resolve_contact_by_channel(
    pool: asyncpg.Pool,
    channel_type: str,
    channel_value: str,
) -> ResolvedContact | None:
    """Resolve a contact from a channel identifier.

    Queries ``shared.contact_info JOIN shared.contacts`` to map a channel
    identifier to a known contact record.  Returns ``None`` when no contact
    is found for the given (type, value) pair.

    Parameters
    ----------
    pool:
        asyncpg connection pool.  The executing role must have at minimum
        ``SELECT`` on ``shared.contact_info`` and ``shared.contacts``.
    channel_type:
        The contact_info type field (e.g., ``"telegram"``, ``"email"``).
    channel_value:
        The contact_info value field (e.g., a Telegram chat ID string or
        an email address).

    Returns
    -------
    ResolvedContact | None
        A populated ``ResolvedContact`` on success, or ``None`` if no match
        is found or either shared table does not yet exist.

    Notes
    -----
    - The UNIQUE constraint on ``(type, value)`` in ``shared.contact_info``
      (added by core_007 migration) guarantees at most one result.
    - ``entity_id`` is cross-schema (entities lives in the memory butler
      schema); the value is read from the ``contacts.entity_id`` column which
      stores the UUID reference.
    - This function is safe to call if the migration has not yet run —
      it returns ``None`` gracefully.
    """
    try:
        row: asyncpg.Record | None = await pool.fetchrow(
            """
            SELECT c.id          AS contact_id,
                   c.name        AS name,
                   c.roles       AS roles,
                   c.entity_id   AS entity_id
            FROM   shared.contact_info ci
            JOIN   shared.contacts c ON c.id = ci.contact_id
            WHERE  ci.type = $1
              AND  ci.value = $2
            LIMIT  1
            """,
            channel_type,
            channel_value,
        )
    except Exception:  # noqa: BLE001
        logger.debug(
            "resolve_contact_by_channel: DB query failed (table may not exist yet); returning None",
            exc_info=True,
        )
        return None

    if row is None:
        return None

    contact_id = row["contact_id"]
    if not isinstance(contact_id, UUID):
        try:
            contact_id = UUID(str(contact_id))
        except (ValueError, AttributeError):
            return None

    entity_id = row["entity_id"]
    if entity_id is not None and not isinstance(entity_id, UUID):
        try:
            entity_id = UUID(str(entity_id))
        except (ValueError, AttributeError):
            entity_id = None

    raw_roles = row["roles"]
    if isinstance(raw_roles, (list, tuple)):
        roles = [str(r) for r in raw_roles]
    else:
        roles = []

    return ResolvedContact(
        contact_id=contact_id,
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
    """Create a temporary contact for an unknown sender.

    Creates a contact with ``metadata.needs_disambiguation = true`` plus a
    linked ``contact_info`` entry.  If a contact_info entry for (type, value)
    already exists (due to concurrent creation or a race), the existing
    contact is returned instead.

    Parameters
    ----------
    pool:
        asyncpg connection pool.  Role must have INSERT on shared.contacts
        and shared.contact_info.
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
        async with pool.acquire() as conn:
            async with conn.transaction():
                # Re-check under transaction to avoid double-creation.
                existing: asyncpg.Record | None = await conn.fetchrow(
                    """
                    SELECT c.id        AS contact_id,
                           c.name      AS name,
                           c.roles     AS roles,
                           c.entity_id AS entity_id
                    FROM   shared.contact_info ci
                    JOIN   shared.contacts c ON c.id = ci.contact_id
                    WHERE  ci.type = $1
                      AND  ci.value = $2
                    LIMIT  1
                    """,
                    channel_type,
                    channel_value,
                )
                if existing is not None:
                    raw_roles = existing["roles"]
                    roles = (
                        [str(r) for r in raw_roles] if isinstance(raw_roles, (list, tuple)) else []
                    )
                    eid = existing["entity_id"]
                    if eid is not None and not isinstance(eid, UUID):
                        try:
                            eid = UUID(str(eid))
                        except (ValueError, AttributeError):
                            eid = None
                    cid = existing["contact_id"]
                    if not isinstance(cid, UUID):
                        cid = UUID(str(cid))
                    return ResolvedContact(
                        contact_id=cid,
                        name=existing["name"] or None,
                        roles=roles,
                        entity_id=eid,
                    )

                # Create the contact.
                metadata: dict[str, Any] = {
                    "needs_disambiguation": True,
                    "source_channel": channel_type,
                    "source_value": channel_value,
                }
                contact_row: asyncpg.Record = await conn.fetchrow(
                    """
                    INSERT INTO shared.contacts (name, roles, metadata)
                    VALUES ($1, $2, $3::jsonb)
                    RETURNING id, name, roles, entity_id
                    """,
                    name,
                    [],
                    json.dumps(metadata),
                )
                contact_id: UUID = contact_row["id"]

                # Link contact_info — use ON CONFLICT to survive races.
                await conn.execute(
                    """
                    INSERT INTO shared.contact_info (contact_id, type, value, is_primary)
                    VALUES ($1, $2, $3, true)
                    ON CONFLICT (type, value) DO NOTHING
                    """,
                    contact_id,
                    channel_type,
                    channel_value,
                )

        return ResolvedContact(
            contact_id=contact_id,
            name=name,
            roles=[],
            entity_id=None,
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

    Parameters
    ----------
    resolved:
        A ``ResolvedContact`` for a known sender, or ``None`` for unknown.
    channel:
        The source channel (e.g., ``"telegram"``).
    temp_contact_id:
        contact_id of the temporary contact created for an unknown sender.
    temp_entity_id:
        entity_id of the temporary contact (may be ``None``).

    Returns
    -------
    str
        A formatted preamble line, e.g.:
        - ``"[Source: Owner, via telegram]"``
        - ``"[Source: Chloe (contact_id: <uuid>, entity_id: <uuid>), via telegram]"``
        - ``"[Source: Unknown sender (contact_id: <uuid>), via telegram --
          pending disambiguation]"``
    """
    if resolved is not None:
        if "owner" in resolved.roles:
            return f"[Source: Owner, via {channel}]"
        # Known non-owner
        name = resolved.name or "Unknown Contact"
        cid = resolved.contact_id
        eid = resolved.entity_id
        if eid is not None:
            return f"[Source: {name} (contact_id: {cid}, entity_id: {eid}), via {channel}]"
        return f"[Source: {name} (contact_id: {cid}), via {channel}]"

    # Unknown sender
    if temp_contact_id is not None:
        if temp_entity_id is not None:
            return (
                f"[Source: Unknown sender (contact_id: {temp_contact_id}, "
                f"entity_id: {temp_entity_id}), via {channel} -- pending disambiguation]"
            )
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
