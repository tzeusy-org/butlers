"""Switchboard identity resolution and preamble injection.

This module implements the identity injection step that runs in the
Switchboard's message ingestion path **before** the LLM routing decision.

For each incoming message:

1. Call ``resolve_contact_by_channel(pool, channel_type, channel_value)`` to
   look up the sender in ``shared.contact_info JOIN shared.contacts``.
2. If unknown: create a temporary contact with ``needs_disambiguation=true``
   and notify the owner (exactly once per new unknown sender).
3. Build the identity preamble and inject it at the top of the routed prompt.

The preamble formats are:

* Owner:        ``[Source: Owner, via {channel}]``
* Known:        ``[Source: {name} (contact_id: {cid}, entity_id: {eid}), via {channel}]``
* Unknown:      ``[Source: Unknown sender (contact_id: {cid}), via {channel}
  -- pending disambiguation]``

The result includes ``contact_id``, ``entity_id``, and ``sender_roles`` for
population in ``routing_log`` so every routed message carries identity context.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import asyncpg

from butlers.identity import (
    ResolvedContact,
    build_identity_preamble,
    create_temp_contact,
    resolve_contact_by_channel,
)

logger = logging.getLogger(__name__)

# State-key prefix for tracking "owner has been notified about this sender."
# Stored in the switchboard's key-value state so notification fires only once
# per unknown sender, not on every message.
_NOTIFIED_STATE_KEY_PREFIX = "identity:unknown_notified:"


@dataclass
class IdentityResolutionResult:
    """Result of identity resolution for a single inbound message.

    Attributes
    ----------
    preamble:
        The structured identity preamble line to prepend to the routed prompt.
        Empty string when resolution was skipped (no channel_value provided).
    contact_id:
        UUID of the resolved or created contact, or ``None``.
    entity_id:
        UUID of the linked memory entity, or ``None``.
    sender_roles:
        List of roles for the sender, or ``None`` (unknown / not resolved).
    is_owner:
        ``True`` iff the sender has the ``owner`` role.
    is_known:
        ``True`` iff the sender resolved to a pre-existing contact.
    is_unknown:
        ``True`` iff the sender was not found and a temp contact was created
        (or creation was attempted).
    new_unknown_sender:
        ``True`` iff a new temporary contact was created in this call
        (i.e., the owner should be notified).
    """

    preamble: str = ""
    contact_id: UUID | None = None
    entity_id: UUID | None = None
    sender_roles: list[str] | None = None
    is_owner: bool = False
    is_known: bool = False
    is_unknown: bool = False
    new_unknown_sender: bool = False


async def resolve_and_inject_identity(
    pool: asyncpg.Pool,
    channel_type: str,
    channel_value: str | None,
    *,
    display_name: str | None = None,
    notify_owner_fn: Any | None = None,
    state_pool: asyncpg.Pool | None = None,
) -> IdentityResolutionResult:
    """Resolve sender identity and build the preamble for the routed prompt.

    This is the single entry point for identity injection in the Switchboard
    ingestion path.  Call it before building the routing prompt; prepend the
    returned ``preamble`` to the message text.

    Parameters
    ----------
    pool:
        asyncpg pool for the Switchboard schema.  Must have at minimum SELECT
        on ``shared.contact_info`` and ``shared.contacts``, and INSERT on both
        (for unknown sender creation).
    channel_type:
        Source channel type (e.g. ``"telegram"``, ``"email"``).
    channel_value:
        Sender identifier (e.g. a Telegram chat ID string or email address).
        When ``None`` or empty, resolution is skipped and an empty result is
        returned (no preamble, no column population).
    display_name:
        Optional human-readable name for the sender (e.g. from Telegram's
        ``from_user.full_name``).  Used when creating a temporary contact.
    notify_owner_fn:
        Async callable ``(message: str) -> None`` that sends a notification to
        the owner.  When ``None``, no notification is sent.
    state_pool:
        Optional separate pool for checking/setting the ``butler_state`` KV
        store (for idempotent "already notified" tracking).  When ``None``,
        the ``pool`` argument is also used for state queries.

    Returns
    -------
    IdentityResolutionResult
        Populated result with preamble, contact_id, entity_id, sender_roles,
        and boolean flags.
    """
    if not channel_value:
        return IdentityResolutionResult()

    # Step 1: Attempt to resolve from shared.contact_info JOIN shared.contacts.
    resolved: ResolvedContact | None = await resolve_contact_by_channel(
        pool, channel_type, channel_value
    )

    if resolved is not None:
        is_owner = "owner" in resolved.roles
        preamble = build_identity_preamble(resolved, channel_type)
        return IdentityResolutionResult(
            preamble=preamble,
            contact_id=resolved.contact_id,
            entity_id=resolved.entity_id,
            sender_roles=resolved.roles or None,
            is_owner=is_owner,
            is_known=True,
            is_unknown=False,
        )

    # Step 2: Unknown sender — create a temporary contact.
    temp_contact = await create_temp_contact(
        pool,
        channel_type,
        channel_value,
        display_name=display_name,
    )

    # Determine if this temp contact was freshly created (not pre-existing).
    new_sender = False
    if temp_contact is not None:
        new_sender = await _is_new_unknown_sender(
            state_pool or pool,
            channel_type,
            channel_value,
        )

    preamble = build_identity_preamble(
        None,
        channel_type,
        temp_contact_id=temp_contact.contact_id if temp_contact else None,
        temp_entity_id=temp_contact.entity_id if temp_contact else None,
    )

    result = IdentityResolutionResult(
        preamble=preamble,
        contact_id=temp_contact.contact_id if temp_contact else None,
        entity_id=temp_contact.entity_id if temp_contact else None,
        sender_roles=None,
        is_owner=False,
        is_known=False,
        is_unknown=True,
        new_unknown_sender=new_sender,
    )

    # Step 3: Notify owner once per new unknown sender.
    if new_sender and notify_owner_fn is not None:
        contact_name = (
            temp_contact.name if temp_contact else None
        ) or f"{channel_type} {channel_value}"
        contact_link = (
            f"/butlers/contacts/{temp_contact.contact_id}" if temp_contact else "/butlers/contacts"
        )
        notification_msg = (
            f"Received a message from {contact_name} ({channel_type}). "
            f"Who is this? Resolve at {contact_link}"
        )
        try:
            await notify_owner_fn(notification_msg)
        except Exception:  # noqa: BLE001
            logger.warning(
                "Failed to notify owner about unknown sender %s/%s",
                channel_type,
                channel_value,
                exc_info=True,
            )
        finally:
            # Mark as notified so future messages from this sender don't re-notify.
            await _mark_notified(
                state_pool or pool,
                channel_type,
                channel_value,
            )

    return result


async def _is_new_unknown_sender(
    pool: asyncpg.Pool,
    channel_type: str,
    channel_value: str,
) -> bool:
    """Return True if we have NOT yet notified the owner about this sender.

    Uses a lightweight check in butler_state KV store to track per-sender
    notification state.  Returns True (i.e., "new") if no notification record
    exists or if the state table is not available.
    """
    state_key = f"{_NOTIFIED_STATE_KEY_PREFIX}{channel_type}:{channel_value}"
    try:
        row = await pool.fetchrow(
            "SELECT value FROM butler_state WHERE key = $1 LIMIT 1",
            state_key,
        )
        return row is None
    except Exception:  # noqa: BLE001
        # butler_state may not exist in this schema (switchboard).  Treat as new.
        logger.debug(
            "_is_new_unknown_sender: could not read butler_state; treating as new sender",
            exc_info=True,
        )
        return True


async def _mark_notified(
    pool: asyncpg.Pool,
    channel_type: str,
    channel_value: str,
) -> None:
    """Record that the owner has been notified about this unknown sender."""
    state_key = f"{_NOTIFIED_STATE_KEY_PREFIX}{channel_type}:{channel_value}"
    try:
        await pool.execute(
            """
            INSERT INTO butler_state (key, value)
            VALUES ($1, $2::jsonb)
            ON CONFLICT (key) DO NOTHING
            """,
            state_key,
            '{"notified": true}',
        )
    except Exception:  # noqa: BLE001
        logger.debug(
            "_mark_notified: could not write to butler_state (non-fatal)",
            exc_info=True,
        )
