"""Prepared-action status resolver for Home (bu-jv1moa.114, REQ-home-deterministic-jobs-011).

One closed, content-blind answer to "what happened to this parked Home action?", derived from
Home's own approval row and the actuation receipt (``home.ha_command_log``, RFC 0028):

``awaiting_approval``  parked and still pending, not yet past its expiry;
``approved_not_run``   approved, no receipt: also what a replay refused because the away premise
                       ended reads as (the row stays ``approved``, no request was sent);
``succeeded``          the receipt's live read-back matched;
``unverified``         the receipt is ``unverified``, or an attempt is still ``attempting`` (a
                       request may have left; it is never success);
``failed``             the receipt records a definite failure;
``rejected``           the owner rejected it;
``cancelled``          a system actor withdrew it unexecuted (:data:`CANCEL_ACTOR`), or its goal
                       was cancelled;
``expired`` / ``abandoned``  as the approvals state machine says;
``unknown``            no row, an unreadable row, or a state nothing here classifies.

The receipt decides the executed case; the approval row decides everything else. ``unknown`` is
never rendered as done. The answer carries no arguments, target, message text or reason.

The Switchboard reaches the same mapping through ``public.resolve_home_action_status(uuid)``
(core_337), a SECURITY DEFINER function; ``tests/migrations/test_core_337_home_action_status.py``
holds the two to one table of cases.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Final
from uuid import UUID

import asyncpg

logger = logging.getLogger(__name__)

AWAITING_APPROVAL: Final = "awaiting_approval"
APPROVED_NOT_RUN: Final = "approved_not_run"
SUCCEEDED: Final = "succeeded"
UNVERIFIED: Final = "unverified"
FAILED: Final = "failed"
REJECTED: Final = "rejected"
CANCELLED: Final = "cancelled"
EXPIRED: Final = "expired"
ABANDONED: Final = "abandoned"
UNKNOWN: Final = "unknown"

#: The whole answer space; nothing else is ever returned or probed.
STATUSES: Final = (
    AWAITING_APPROVAL,
    APPROVED_NOT_RUN,
    SUCCEEDED,
    UNVERIFIED,
    FAILED,
    REJECTED,
    CANCELLED,
    EXPIRED,
    ABANDONED,
    UNKNOWN,
)

#: ``decided_by`` written when Home withdraws a still-pending fix (REQ-009, built by bu-jv1moa.115).
CANCEL_ACTOR: Final = "system:home-departure-fix"

#: Receipt status -> answer. ``attempting`` is an attempt in flight or left by a dead process.
_RECEIPT: Final = {
    "succeeded": SUCCEEDED,
    "failed": FAILED,
    "unverified": UNVERIFIED,
    "attempting": UNVERIFIED,
}

_ACTION_SQL = "SELECT status, decided_by, expires_at FROM pending_actions WHERE id = $1"
_RECEIPT_SQL = (
    "SELECT status FROM ha_command_log WHERE approval_id = $1 "
    "ORDER BY issued_at DESC, id DESC LIMIT 1"
)


def classify(
    action: Mapping[str, Any] | None,
    receipt: Mapping[str, Any] | None,
    *,
    now: datetime | None = None,
) -> str:
    """Map an approval row and its latest receipt to one of :data:`STATUSES`. Pure."""
    if receipt is not None:
        return _RECEIPT.get(str(receipt.get("status")), UNKNOWN)
    if action is None:
        return UNKNOWN
    status = action.get("status")
    if status == "pending":
        expires_at = action.get("expires_at")
        if expires_at is not None and expires_at <= (now or datetime.now(UTC)):
            return EXPIRED
        return AWAITING_APPROVAL
    if status == "approved":
        return APPROVED_NOT_RUN
    if status == "rejected":
        return CANCELLED if action.get("decided_by") == CANCEL_ACTOR else REJECTED
    if status == "cancelled":
        return CANCELLED
    if status == "expired":
        return EXPIRED
    if status == "abandoned":
        return ABANDONED
    # ``executed`` with no receipt, or anything new: nothing here may call it done.
    return UNKNOWN


async def prepared_action_status(pool: asyncpg.Pool, action_id: UUID | str) -> str:
    """The status of one parked Home action. Never raises; an unreadable store is ``unknown``."""
    try:
        action_uuid = UUID(str(action_id))
    except ValueError:
        return UNKNOWN
    try:
        action = await pool.fetchrow(_ACTION_SQL, action_uuid)
    except asyncpg.PostgresError:
        logger.warning("home prepared status: approval row unreadable")
        return UNKNOWN
    try:
        receipt = await pool.fetchrow(_RECEIPT_SQL, action_uuid)
    except asyncpg.UndefinedTableError:
        receipt = None  # no actuation ledger in this schema: no receipt can exist
    except asyncpg.PostgresError:
        logger.warning("home prepared status: receipt unreadable")
        return UNKNOWN
    return classify(
        dict(action) if action is not None else None,
        dict(receipt) if receipt is not None else None,
    )
