"""A move the radar proposes is an approvals card, never a dashboard write (bu-5ii4gj.11 R2b).

The owner's decision (bu-kf3n4i) retired dashboard event updates, so resolving an overlap by
moving an event parks one approvals card whose call is the exact ``calendar_update_event`` and
the owner approves it in the existing approvals surface. Two things bind it:

* the card's arguments digest, which the approvals executor checks on replay, and
* the provider etag the owner saw, kept OUTSIDE the call's arguments in a
  ``calendar_action_log`` row. When the approved call runs, the event's current etag must still
  equal it, otherwise the call declines ``changed_since_check`` and writes nothing.

An approved move is written once (never resent), so a 412 can only mean the event changed. The
card text names no event title: the event may be a third party's.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg

from butlers.modules.approvals.command_contracts import RESOLUTION_MOVE_COMMAND
from butlers.modules.approvals.park import park_pending_action
from butlers.modules.calendar_approval import calendar_approval_args

from .models import CalendarEvent

#: The ``calendar_action_log.action_type`` that binds a card to the etag the owner saw.
RESOLUTION_ACTION_TYPE = "resolution_move"
#: The ``status`` of an approved move that wrote nothing.
RESOLUTION_DECLINED = "resolution_declined"
#: How long the owner's card stays actionable.
RESOLUTION_CARD_TTL = timedelta(days=3)

RESOLUTION_DECLINED_TEXT = (
    "Move not applied: the event changed after the overlap was checked, so nothing was moved."
)

RESOLUTION_CARD_WHY = "You asked to resolve a calendar overlap by moving one event."


def resolution_request_id(pair_id: str, option_id: str, issue_digest: str) -> str:
    """Deterministic, bound to the digest the owner saw: a changed overlap is a new request."""
    key = f"{pair_id}\x00{option_id}\x00{issue_digest}"
    return "resolve:" + hashlib.sha256(key.encode()).hexdigest()[:24]


def resolution_card_args(call_args: Mapping[str, Any], *, request_id: str) -> dict[str, Any]:
    """The card's canonical call, bound exactly as the executor replays it."""
    # ``fail``: the slot was free when it was compiled; if the calendar has since filled it the
    # move fails rather than creating a new overlap. The calendar must be named: the executor
    # replays the call with the calendar the owner was shown bound in.
    if not call_args.get("calendar_id"):
        raise ValueError("a resolution move must name its calendar")
    return calendar_approval_args(
        "calendar_update_event",
        **{**call_args, "request_id": request_id, "conflict_policy": "fail"},
    )


def resolution_card_summary(call_args: Mapping[str, Any]) -> str:
    """What approving does. It never repeats an event title (it may be a third party's text)."""
    return f"Resolve an overlap: move one event to {call_args['start_at']} - {call_args['end_at']}."


_INSERT_SQL = """
INSERT INTO calendar_action_log (
    idempotency_key, action_type, action_status, origin_ref,
    action_payload, action_result, applied_at
) VALUES ($1, 'resolution_move', 'noop', $2, $3, $4, now())
ON CONFLICT (idempotency_key) DO NOTHING
"""


async def park_resolution_move(
    pool: Any,
    *,
    butler_name: str,
    pair_id: str,
    option_id: str,
    issue_digest: str,
    call_args: Mapping[str, Any],
    etag: str,
) -> tuple[uuid.UUID, bool]:
    """Park the move's card and bind it to ``etag``; returns ``(action_id, created)``.

    One transaction: the card and its binding commit together or not at all. A repeat of the
    same request (same pair, option and digest) returns the card already parked.
    """
    request_id = resolution_request_id(pair_id, option_id, issue_digest)
    args = RESOLUTION_MOVE_COMMAND.materialize(
        resolution_card_args(call_args, request_id=request_id)
    )
    now = datetime.now(UTC)
    async with pool.acquire() as conn, conn.transaction():
        admission = await park_pending_action(
            conn,
            action_id=uuid.uuid4(),
            tool_name="calendar_update_event",
            tool_args=args,
            agent_summary=resolution_card_summary(call_args),
            requested_at=now,
            expires_at=now + RESOLUTION_CARD_TTL,
            why=RESOLUTION_CARD_WHY,
            blast_radius="self",
            reversibility="reversible",
            origin_butler=butler_name,
            deduplication_key=request_id,
        )
        await conn.execute(
            _INSERT_SQL,
            f"{RESOLUTION_ACTION_TYPE}:{request_id}",
            str(call_args["event_id"]),
            {"pair_id": pair_id, "option_id": option_id, "issue_digest": issue_digest},
            {"action_id": str(admission.action_id), "etag": etag},
        )
        return admission.action_id, not admission.duplicate


async def offer_for_card(pool: Any, action_id: uuid.UUID) -> dict[str, Any] | None:
    """The binding an approved card runs under, or ``None`` for any other approval."""
    try:
        row = await pool.fetchrow(
            "SELECT action_result FROM calendar_action_log "
            "WHERE action_type = 'resolution_move' AND action_result->>'action_id' = $1",
            str(action_id),
        )
    except asyncpg.UndefinedTableError:
        return None
    if row is None:
        return None
    result = row["action_result"]
    if isinstance(result, str):
        import json

        result = json.loads(result)
    if not isinstance(result, dict) or result.get("action_id") != str(action_id):
        return None
    return result


def resolution_refusal(offer: Mapping[str, Any], event: CalendarEvent) -> str | None:
    """``None`` when the move may write, else why it declines (nothing is written).

    An event with no etag, or one that differs from the version the owner saw, never writes.
    """
    bound = offer.get("etag")
    if isinstance(bound, str) and bound and event.etag == bound:
        return None
    return "changed_since_check"


__all__ = [
    "RESOLUTION_ACTION_TYPE",
    "RESOLUTION_CARD_TTL",
    "RESOLUTION_DECLINED",
    "RESOLUTION_DECLINED_TEXT",
    "offer_for_card",
    "park_resolution_move",
    "resolution_card_args",
    "resolution_card_summary",
    "resolution_refusal",
    "resolution_request_id",
]
