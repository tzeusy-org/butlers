"""Shared email recipient guard for outbound delivery approval.

Consolidates the email recipient check used by both the ``notify()`` core
tool and the ``route.execute`` handler in messenger.  A single implementation
ensures both gates enforce identical policy:

1. Resolve contact by email address.
2. Owner contact AND address is primary → auto-approve (no rule needed).
   Non-primary owner addresses fall through to the rules/parking flow.
3. Context mismatch: if *msg_context* is provided and the entity_facts triple
   carrying the resolved address is tagged with a conflicting context, park
   for approval regardless of owner status.  (Owner primary addresses skip
   this check — see step 2.)
4. Non-owner or unknown → check standing approval rules.
5. Rule matches → approve, bump ``use_count``.
6. No rule → park as ``pending_action`` for human review.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg

from butlers.modules.approvals._shared import is_primary_contact

logger = logging.getLogger(__name__)


def _normalize_session_id(session_id: str | uuid.UUID | None) -> uuid.UUID | None:
    """Return a UUID-shaped session id or ``None`` for unparsable inputs."""
    if session_id is None:
        return None
    if isinstance(session_id, uuid.UUID):
        return session_id
    try:
        return uuid.UUID(session_id)
    except (ValueError, TypeError, AttributeError):
        logger.warning("email guard: ignoring non-UUID session_id %r", session_id)
        return None


async def _get_email_context(pool: asyncpg.Pool, email_address: str) -> str | None:
    """Return the ``context`` tag for *email_address* from ``relationship.entity_facts``.

    Queries the active ``has-email`` triple whose object matches the address.
    Returns ``metadata->>'context'`` from the triple row.  Returns ``None`` when
    the triple does not exist, has no context tag, or on any DB error
    (missing column is treated as NULL).

    Reads from ``relationship.entity_facts``.
    """
    try:
        row = await pool.fetchrow(
            """
            SELECT metadata->>'context' AS context
            FROM relationship.entity_facts
            WHERE predicate   = 'has-email'
              AND object      = $1
              AND object_kind = 'literal'
              AND validity    = 'active'
            LIMIT 1
            """,
            email_address,
        )
        if row is None:
            return None
        return row["context"]  # may be None for unclassified triples
    except Exception:  # noqa: BLE001
        logger.debug(
            "email guard: could not fetch context for <%s>; treating as unclassified",
            email_address,
            exc_info=True,
        )
        return None


def _context_conflicts(msg_context: str, address_context: str | None) -> bool:
    """Return True if *address_context* conflicts with *msg_context*.

    NULL/unclassified address context never conflicts — it is compatible with
    any declared message context.  A conflict occurs only when both sides are
    explicit and differ (e.g. ``msg_context="personal"`` vs
    ``address_context="work"``).
    """
    if address_context is None:
        return False
    return msg_context != address_context


@dataclass(frozen=True, slots=True)
class EmailGuardDecision:
    """Result of an email recipient guard check."""

    allowed: bool
    reason: str  # "owner", "rule", "parked"
    action_id: uuid.UUID | None = None
    rule_id: uuid.UUID | None = None
    contact_desc: str | None = None  # "known non-owner contact" | "unknown contact"


async def check_email_recipient(
    pool: asyncpg.Pool,
    *,
    email_target: str,
    rule_tool_name: str,
    rule_match_args: dict[str, Any],
    park_tool_name: str,
    park_tool_args: dict[str, Any],
    park_summary: str,
    session_id: str | uuid.UUID | None = None,
    expiry_hours: int = 72,
    msg_context: str | None = None,
    butler_name: str | None = None,
) -> EmailGuardDecision:
    """Check whether an outbound email to *email_target* is permitted.

    Parameters
    ----------
    pool:
        Database connection pool (must have ``shared`` schema access).
    email_target:
        The recipient email address to validate.
    rule_tool_name:
        Tool name used for standing-rule matching (e.g. ``"notify"``,
        ``"email_send_message"``).
    rule_match_args:
        Argument dict passed to :func:`match_rules` for rule evaluation.
    park_tool_name:
        Tool name recorded on the ``pending_action`` row if parked.
    park_tool_args:
        Tool args dict recorded on the ``pending_action`` row if parked.
    park_summary:
        Human-readable summary for the ``pending_action`` row.
    session_id:
        Runtime session ID for traceability.
    expiry_hours:
        Hours until the parked action expires (default 72).
    msg_context:
        Optional message context sphere (``"personal"``, ``"work"``, or
        ``"other"``).  When provided, a mismatch against the address's
        ``entity_facts`` triple context tag causes the delivery to be parked for
        approval (even if a standing rule exists).  Unclassified (NULL)
        address context never conflicts.
    butler_name:
        The name of the butler that owns this guard (used for WS event
        attribution).  Pass ``None`` when the name is unavailable.

    Returns
    -------
    EmailGuardDecision
        ``.allowed=True`` if delivery may proceed, ``False`` if parked.
    """

    def _emit_created(action_id: uuid.UUID, status: str) -> None:
        """Publish a 'created' approval WS event; silently ignored if broker is unavailable."""
        try:
            from butlers.api.routers.approvals import emit_approvals_event

            emit_approvals_event(
                "created",
                str(action_id),
                butler=butler_name,
                tool_name=park_tool_name,
                status=status,
            )
        except Exception:  # noqa: BLE001
            logger.debug(
                "email guard: emit_approvals_event('created') failed; ignoring", exc_info=True
            )

    from butlers.identity import resolve_contact_by_channel

    contact = await resolve_contact_by_channel(pool, "email", email_target)

    # Owner primary address → always allowed (no further checks needed)
    if contact is not None and "owner" in contact.roles:
        if contact.entity_id is None:
            # Owner contact has no entity_id — cannot check primacy; treat as non-primary
            # so the address falls through to the rules/parking flow.
            is_primary = False
        else:
            is_primary = await is_primary_contact(
                pool,
                contact.entity_id,
                "email",
                email_target,
            )
        if is_primary:
            return EmailGuardDecision(allowed=True, reason="owner")

    # Context mismatch check: park if the declared message context conflicts
    # with the address's tagged context.  This applies to non-primary owner
    # addresses and all non-owner contacts.  Unclassified (NULL) address context
    # is always compatible — it never forces a park.
    if msg_context is not None:
        address_context = await _get_email_context(pool, email_target)
        if _context_conflicts(msg_context, address_context):
            contact_desc = "known non-owner contact" if contact is not None else "unknown contact"
            action_id = uuid.uuid4()
            now = datetime.now(UTC)
            expires_at = now + timedelta(hours=expiry_hours)
            normalized_session_id = _normalize_session_id(session_id)
            mismatch_summary = (
                f"{park_summary} [context mismatch: message context={msg_context!r}, "
                f"address context={address_context!r}]"
            )
            try:
                from butlers.modules.approvals.models import ActionStatus

                await pool.execute(
                    "INSERT INTO pending_actions "
                    "(id, tool_name, tool_args, agent_summary, session_id, status, "
                    "requested_at, expires_at) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
                    action_id,
                    park_tool_name,
                    json.dumps(park_tool_args),
                    mismatch_summary,
                    normalized_session_id,
                    ActionStatus.PENDING.value,
                    now,
                    expires_at,
                )
                _emit_created(action_id, ActionStatus.PENDING.value)
                logger.warning(
                    "email guard: context mismatch — blocked delivery to %s %r "
                    "(msg_context=%r, address_context=%r) — parked as pending_action %s",
                    contact_desc,
                    email_target,
                    msg_context,
                    address_context,
                    action_id,
                )
            except Exception:
                logger.warning(
                    "email guard: failed to park context-mismatch pending_action for %r",
                    email_target,
                    exc_info=True,
                )
            return EmailGuardDecision(
                allowed=False,
                reason="parked",
                action_id=action_id,
                contact_desc=contact_desc,
            )

    contact_desc = "known non-owner contact" if contact is not None else "unknown contact"

    # Check standing approval rules
    rule = None
    try:
        from butlers.modules.approvals.rules import match_rules

        rule = await match_rules(pool, rule_tool_name, rule_match_args)
    except Exception:  # noqa: BLE001
        # Table may not exist in this schema — that's fine
        pass

    if rule is not None:
        logger.info(
            "email guard: standing rule %s permits %s %r — allowing delivery",
            rule.id,
            contact_desc,
            email_target,
        )
        # Bump use_count
        try:
            await pool.execute(
                "UPDATE approval_rules SET use_count = use_count + 1 WHERE id = $1",
                rule.id,
            )
        except Exception:  # noqa: BLE001
            pass
        return EmailGuardDecision(
            allowed=True,
            reason="rule",
            rule_id=rule.id,
            contact_desc=contact_desc,
        )

    # No rule → park for human review
    from butlers.modules.approvals.models import ActionStatus

    action_id = uuid.uuid4()
    now = datetime.now(UTC)
    expires_at = now + timedelta(hours=expiry_hours)
    normalized_session_id = _normalize_session_id(session_id)

    try:
        await pool.execute(
            "INSERT INTO pending_actions "
            "(id, tool_name, tool_args, agent_summary, session_id, status, "
            "requested_at, expires_at) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
            action_id,
            park_tool_name,
            json.dumps(park_tool_args),
            park_summary,
            normalized_session_id,
            ActionStatus.PENDING.value,
            now,
            expires_at,
        )
        _emit_created(action_id, ActionStatus.PENDING.value)
        logger.warning(
            "email guard: blocked delivery to %s %r — parked as pending_action %s",
            contact_desc,
            email_target,
            action_id,
        )
    except Exception:
        logger.warning(
            "email guard: failed to park pending_action for %r",
            email_target,
            exc_info=True,
        )

    return EmailGuardDecision(
        allowed=False,
        reason="parked",
        action_id=action_id,
        contact_desc=contact_desc,
    )


async def check_recipient(
    pool: asyncpg.Pool,
    *,
    channel: str,
    target: str,
    rule_tool_name: str,
    rule_match_args: dict[str, Any],
    park_tool_name: str,
    park_tool_args: dict[str, Any],
    park_summary: str,
    session_id: str | uuid.UUID | None = None,
    expiry_hours: int = 72,
    butler_name: str | None = None,
) -> EmailGuardDecision:
    """Channel-general outbound recipient guard (telegram, etc.).

    Applies the same role-based policy the approval gate wrapper enforces
    (post bu-nd5me) so that the ``notify()`` MCP tool gates every supported
    channel, not just email:

    1. Resolve the contact by ``(channel, target)``.  An ``'owner'`` role match
       auto-approves on ANY active, verified owner channel — channel resolution
       only returns a row for an *active* ``relationship.entity_facts`` triple,
       so an owner-role match is by definition a verified owner channel.  No
       channel-primacy check is applied (owner self-notification is low-risk).
    2. Cross-schema owner fallback: a non-relationship butler runs under a
       schema-isolated role that cannot read ``relationship.entity_facts``
       directly, so :func:`resolve_contact_by_channel` returns ``None`` even for
       owner-directed sends.  Recognise the owner via the ``SECURITY DEFINER``
       :func:`resolve_owner_channel_via_definer` lookup (the reported primacy
       flag is intentionally discarded — bu-nd5me).
    3. Non-owner / unresolvable target: check standing approval rules.  A
       matching rule auto-approves (and bumps ``use_count``); otherwise the
       send is parked as a ``pending_action`` for human review (fail-closed).

    Unlike :func:`check_email_recipient`, this guard does NOT apply the
    email-specific channel-primacy / context-conflict incident behaviour
    (bu-jwby9 / bu-axdie); that nuance is intentionally email-only.
    """
    from butlers.identity import (
        resolve_contact_by_channel,
        resolve_owner_channel_via_definer,
    )

    contact = await resolve_contact_by_channel(pool, channel, target)

    # Cross-schema owner fallback when direct resolution failed entirely.  A
    # resolved (non-owner) contact means the butler COULD read the relationship
    # schema, so the channel demonstrably belongs to a non-owner — no fallback.
    if contact is None:
        try:
            fallback = await resolve_owner_channel_via_definer(pool, channel, target)
        except Exception:  # noqa: BLE001
            fallback = None
        if fallback is not None:
            contact, _owner_is_primary = fallback

    # Owner-directed outbound: auto-approve on any active, verified owner channel.
    if contact is not None and "owner" in contact.roles:
        return EmailGuardDecision(allowed=True, reason="owner")

    contact_desc = "known non-owner contact" if contact is not None else "unknown contact"

    def _emit_created(action_id: uuid.UUID, status: str) -> None:
        """Publish a 'created' approval WS event; silently ignored if broker is unavailable."""
        try:
            from butlers.api.routers.approvals import emit_approvals_event

            emit_approvals_event(
                "created",
                str(action_id),
                butler=butler_name,
                tool_name=park_tool_name,
                status=status,
            )
        except Exception:  # noqa: BLE001
            logger.debug(
                "recipient guard: emit_approvals_event('created') failed; ignoring",
                exc_info=True,
            )

    # Non-owner / unresolvable: check standing approval rules.
    rule = None
    try:
        from butlers.modules.approvals.rules import match_rules

        rule = await match_rules(pool, rule_tool_name, rule_match_args)
    except Exception:  # noqa: BLE001
        # Table may not exist in this schema — that's fine.
        pass

    if rule is not None:
        logger.info(
            "recipient guard: standing rule %s permits %s send to %r — allowing delivery",
            rule.id,
            contact_desc,
            target,
        )
        try:
            await pool.execute(
                "UPDATE approval_rules SET use_count = use_count + 1 WHERE id = $1",
                rule.id,
            )
        except Exception:  # noqa: BLE001
            pass
        return EmailGuardDecision(
            allowed=True,
            reason="rule",
            rule_id=rule.id,
            contact_desc=contact_desc,
        )

    # No rule → park for human review.
    from butlers.modules.approvals.models import ActionStatus

    action_id = uuid.uuid4()
    now = datetime.now(UTC)
    expires_at = now + timedelta(hours=expiry_hours)
    normalized_session_id = _normalize_session_id(session_id)

    try:
        await pool.execute(
            "INSERT INTO pending_actions "
            "(id, tool_name, tool_args, agent_summary, session_id, status, "
            "requested_at, expires_at) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
            action_id,
            park_tool_name,
            json.dumps(park_tool_args),
            park_summary,
            normalized_session_id,
            ActionStatus.PENDING.value,
            now,
            expires_at,
        )
        _emit_created(action_id, ActionStatus.PENDING.value)
        logger.warning(
            "recipient guard: blocked %s send to %s %r — parked as pending_action %s",
            channel,
            contact_desc,
            target,
            action_id,
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "recipient guard: failed to park pending_action for %s %r",
            channel,
            target,
            exc_info=True,
        )

    return EmailGuardDecision(
        allowed=False,
        reason="parked",
        action_id=action_id,
        contact_desc=contact_desc,
    )
