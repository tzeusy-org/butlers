"""Shared email recipient guard for outbound delivery approval.

Consolidates the email recipient check used by both the ``notify()`` core
tool and the ``route.execute`` handler in messenger.  A single implementation
ensures both gates enforce identical policy:

1. Resolve contact by email address.
2. Owner contact → auto-approve (no rule needed).
3. Non-owner or unknown → check standing approval rules.
4. Rule matches → approve, bump ``use_count``.
5. No rule → park as ``pending_action`` for human review.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)


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
    session_id: str | None = None,
    expiry_hours: int = 72,
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

    Returns
    -------
    EmailGuardDecision
        ``.allowed=True`` if delivery may proceed, ``False`` if parked.
    """
    from butlers.identity import resolve_contact_by_channel

    contact = await resolve_contact_by_channel(pool, "email", email_target)

    # Owner → always allowed
    if contact is not None and "owner" in contact.roles:
        return EmailGuardDecision(allowed=True, reason="owner")

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
            session_id,
            ActionStatus.PENDING.value,
            now,
            expires_at,
        )
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
