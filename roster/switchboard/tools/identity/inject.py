"""Switchboard identity resolution and preamble injection.

This module implements the identity injection step that runs in the
Switchboard's message ingestion path **before** the LLM routing decision.

For each incoming message:

1. Call ``resolve_contact_by_channel(pool, channel_type, channel_value)`` to
   look up the sender in ``relationship.entity_facts`` (migration bead 7).
2. If unknown: create a transitory entity with
   ``metadata.unidentified=true`` and notify the owner at most once.
3. Build the identity preamble and inject it at the top of the routed prompt.

Preamble formats (``entity_id`` is the canonical identifier post bu-akads):

* Owner (with entity_id):    ``[Source: Owner (entity_id: {eid}), via {channel}]``
* Owner (no entity_id):      ``[Source: Owner, via {channel}]``
* Known (with entity_id):    ``[Source: {name} (entity_id: {eid}), via {channel}]``
* Known (no entity_id):      ``[Source: {name}, via {channel}]``
* Unknown (with entity_id):  ``[Source: Unknown sender (entity_id: {eid}), via {channel}``
  ``-- pending disambiguation]``
* Unknown (no entity_id):    ``[Source: Unknown sender, via {channel} -- pending disambiguation]``

The result includes ``entity_id`` and ``sender_roles`` for population in
``routing_log``. ``contact_id`` is retained only as a compatibility field;
the entity-first unknown-sender path always leaves it ``None``.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from html import escape
from typing import Any
from uuid import UUID

import asyncpg

from butlers.identity import (
    ResolvedContact,
    build_identity_preamble,
    canonical_identity_channel_type,
    create_temp_contact,
    resolve_contact_by_channel,
    resolve_contacts_by_channel_bulk,
)

logger = logging.getLogger(__name__)

# State-key prefix for the durable pre-delivery claim. The existing
# Switchboard-local ``state`` table serializes the claim so concurrent ingress
# cannot turn one unknown sender into multiple owner notifications.
_NOTIFIED_STATE_KEY_PREFIX = "identity:unknown_notified:"
# This reservation is intentionally independent from the one-time
# notification claim. It makes first-message entity minting idempotent before
# the relationship-owned post-resolution fact writer has established the
# channel-triple lookup key.
_TEMP_ENTITY_STATE_KEY_PREFIX = "identity:unknown_entity:"
_UNIDENTIFIED_ENTITIES_REVIEW_PATH = "/entities/index?state=unidentified"


@dataclass
class IdentityResolutionResult:
    """Result of identity resolution for a single inbound message.

    Attributes
    ----------
    preamble:
        The structured identity preamble line to prepend to the routed prompt.
        Empty string when resolution was skipped (no channel_value provided).
    contact_id:
        Optional compatibility UUID for an independently resolved legacy
        contact. The entity-first unknown-sender path always leaves it
        ``None`` and does not create a contact record.
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
        ``True`` iff this call atomically claimed the one owner-notification
        attempt for a newly surfaced unknown sender.
    channel_value:
        The raw sender identifier observed on the channel (e.g. a Telegram chat
        ID or email address). Carried through so the routing pipeline can
        deterministically assert the unresolved/temp sender's channel triple
        (entity-v3, bu-hvrt1) — switchboard ingress itself must not write
        ``relationship.entity_facts``. ``None`` when resolution was skipped
        (no channel_value supplied) or the sender was already known.
    display_name:
        Canonical display name for a known sender, or ``None`` when no
        authoritative human-readable name is available.
    """

    preamble: str = ""
    contact_id: UUID | None = None
    entity_id: UUID | None = None
    sender_roles: list[str] | None = None
    is_owner: bool = False
    is_known: bool = False
    is_unknown: bool = False
    new_unknown_sender: bool = False
    channel_value: str | None = None
    display_name: str | None = None


async def resolve_and_inject_identity(
    pool: asyncpg.Pool,
    channel_type: str,
    channel_value: str | None,
    *,
    display_name: str | None = None,
    notify_owner_fn: Callable[[str], Awaitable[None]] | None = None,
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
        on ``relationship.entity_facts`` and ``public.entities``, and INSERT on
        ``public.entities`` (for unknown sender creation).
    channel_type:
        Source channel type (e.g. ``"telegram"``, ``"email"``).
    channel_value:
        Sender identifier (e.g. a Telegram chat ID string or email address).
        When ``None`` or empty, resolution is skipped and an empty result is
        returned (no preamble, no column population).
    display_name:
        Optional human-readable name for the sender (e.g. from Telegram's
        ``from_user.full_name``). Used when creating the transitory entity and
        as the safe notification label.
    notify_owner_fn:
        Async callable ``(message: str) -> None`` that sends a notification to
        the owner through the standard delivery boundary. When ``None``, no
        notification claim or delivery attempt is made.
    state_pool:
        Optional separate pool for the Switchboard-local ``state`` KV store
        (for the atomic notification claim). When ``None``, the ``pool``
        argument is also used for the claim.

    Returns
    -------
    IdentityResolutionResult
        Populated result with preamble, contact_id, entity_id, sender_roles,
        and boolean flags.
    """
    if not channel_value:
        return IdentityResolutionResult()

    # Step 1: Attempt to resolve from relationship.entity_facts (migration bead 7).
    resolved: ResolvedContact | None = await resolve_contact_by_channel(
        pool, channel_type, channel_value
    )

    if resolved is not None:
        return _result_from_resolved_contact(resolved, channel_type)

    return await _inject_unknown_identity(
        pool,
        canonical_identity_channel_type(channel_type),
        channel_value,
        source_channel_type=channel_type,
        display_name=display_name,
        notify_owner_fn=notify_owner_fn,
        state_pool=state_pool,
    )


async def _inject_unknown_identity(
    pool: asyncpg.Pool,
    identity_channel_type: str,
    channel_value: str,
    *,
    source_channel_type: str,
    display_name: str | None,
    notify_owner_fn: Callable[[str], Awaitable[None]] | None,
    state_pool: asyncpg.Pool | None,
    strict_reservation: bool = False,
) -> IdentityResolutionResult:
    """Reserve and inject an already-confirmed unresolved sender.

    The caller owns the lookup decision. Keeping reservation separate prevents
    strict batch misses from falling through a second fail-open query.
    """
    temp_contact = await create_temp_contact(
        pool,
        source_channel_type,
        channel_value,
        display_name=display_name,
        identity_channel_type=identity_channel_type,
        pre_resolved_miss=True,
        reservation_state_key=(
            f"{_TEMP_ENTITY_STATE_KEY_PREFIX}{source_channel_type}:{channel_value}"
        ),
        raise_on_error=strict_reservation,
    )
    if temp_contact is not None and not temp_contact.is_unidentified:
        return _result_from_resolved_contact(temp_contact, source_channel_type)

    # Reserve the owner-notification attempt before delivery. The atomic claim
    # is deliberately made only when a real callback is wired: an unconfigured
    # caller must not permanently consume the notification without attempting
    # it, while the production Switchboard always supplies the callback.
    notification_claimed = False
    if temp_contact is not None and notify_owner_fn is not None:
        notification_claimed = await _claim_unknown_sender_notification(
            state_pool or pool,
            source_channel_type,
            channel_value,
        )

    preamble = build_identity_preamble(
        None,
        source_channel_type,
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
        new_unknown_sender=notification_claimed,
        # Carry the raw identifier so the routing pipeline can deterministically
        # assert the channel triple for this unresolved/temp sender (entity-v3,
        # bu-hvrt1). Switchboard ingress itself never writes entity_facts.
        channel_value=channel_value,
    )

    # Step 3: The successful claimer makes the one owner-facing attempt. A
    # delivery failure is observable but intentionally does not clear the
    # durable claim: later ingress must not retry into a notification storm.
    if notification_claimed and notify_owner_fn is not None:
        sender_label = _safe_sender_label(display_name)
        notification_msg = (
            f"Unknown sender: {sender_label} ({source_channel_type}). "
            f"Review in Unidentified Entities: {_UNIDENTIFIED_ENTITIES_REVIEW_PATH}"
        )
        try:
            await notify_owner_fn(notification_msg)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "identity.unknown_sender_notification_failed",
                extra={
                    "channel_type": source_channel_type,
                    "failure_class": type(exc).__name__,
                },
            )

    return result


def _result_from_resolved_contact(
    resolved: ResolvedContact,
    channel_type: str,
) -> IdentityResolutionResult:
    """Classify one explicit resolver result for presentation and routing."""
    if resolved.is_unidentified:
        return IdentityResolutionResult(
            preamble=build_identity_preamble(
                None,
                channel_type,
                temp_contact_id=resolved.contact_id,
                temp_entity_id=resolved.entity_id,
            ),
            contact_id=resolved.contact_id,
            entity_id=resolved.entity_id,
            sender_roles=None,
            is_owner=False,
            is_known=False,
            is_unknown=True,
            display_name=None,
        )

    is_owner = "owner" in resolved.roles
    return IdentityResolutionResult(
        preamble=build_identity_preamble(resolved, channel_type),
        contact_id=resolved.contact_id,
        entity_id=resolved.entity_id,
        sender_roles=resolved.roles or None,
        is_owner=is_owner,
        is_known=True,
        is_unknown=False,
        display_name=resolved.name or ("Owner" if is_owner else None),
    )


async def resolve_sender_identities(
    pool: asyncpg.Pool,
    channel_type: str,
    channel_values: Sequence[str],
    *,
    notify_owner_fn: Callable[[str], Awaitable[None]] | None = None,
    state_pool: asyncpg.Pool | None = None,
) -> dict[str, IdentityResolutionResult]:
    """Resolve each distinct batch speaker through one strict bulk lookup.

    Known speakers are constructed directly from the bulk result. Only values
    that the successful bulk lookup leaves unresolved enter the existing
    reservation and owner-notification path.
    """
    distinct_values: list[str] = []
    seen: set[str] = set()
    for raw_value in channel_values:
        if raw_value in (None, ""):
            continue
        channel_value = str(raw_value)
        if channel_value in seen:
            continue
        seen.add(channel_value)
        distinct_values.append(channel_value)

    if not distinct_values:
        return {}

    identity_channel = canonical_identity_channel_type(channel_type)
    pairs = [(identity_channel, value) for value in distinct_values]
    bulk_results = await resolve_contacts_by_channel_bulk(
        pool,
        pairs,
        raise_on_error=True,
    )

    results: dict[str, IdentityResolutionResult] = {}
    for channel_value in distinct_values:
        resolved = bulk_results[(identity_channel, channel_value)]
        if resolved is not None:
            results[channel_value] = _result_from_resolved_contact(
                resolved,
                channel_type,
            )
            continue

        try:
            results[channel_value] = await _inject_unknown_identity(
                pool,
                identity_channel,
                channel_value,
                source_channel_type=channel_type,
                display_name=None,
                notify_owner_fn=notify_owner_fn,
                state_pool=state_pool,
                strict_reservation=True,
            )
        except Exception as exc:  # noqa: BLE001
            failure_class = getattr(exc, "failure_class", type(exc).__name__)
            logger.warning(
                "identity.batch_unknown_reservation_failed",
                extra={
                    "channel_type": channel_type,
                    "failure_class": failure_class,
                },
            )
            results[channel_value] = IdentityResolutionResult(
                preamble=build_identity_preamble(None, channel_type),
                is_unknown=True,
                channel_value=channel_value,
            )

    return results


def _safe_sender_label(display_name: str | None) -> str:
    """Normalize an untrusted source label without exposing its raw identifier."""
    normalized = " ".join(display_name.split()) if isinstance(display_name, str) else ""
    return escape(normalized[:120], quote=False) if normalized else "Unknown sender"


async def _claim_unknown_sender_notification(
    pool: Any,
    channel_type: str,
    channel_value: str,
) -> bool:
    """Atomically reserve this sender's one owner-notification attempt.

    The ``state`` primary key makes the operation durable and race-safe. A
    state-store failure is deliberately fail-open for ingress routing but
    fail-closed for notification delivery: without a claim, no send occurs.
    """
    state_key = f"{_NOTIFIED_STATE_KEY_PREFIX}{channel_type}:{channel_value}"
    try:
        row = await pool.fetchrow(
            """
            INSERT INTO state (key, value, updated_at, version)
            VALUES (
                $1,
                jsonb_build_object('unknown_sender_notification_attempted', true),
                now(),
                1
            )
            ON CONFLICT (key) DO NOTHING
            RETURNING key
            """,
            state_key,
        )
        return row is not None
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "identity.unknown_sender_notification_claim_failed",
            extra={
                "channel_type": channel_type,
                "failure_class": type(exc).__name__,
            },
        )
        return False
