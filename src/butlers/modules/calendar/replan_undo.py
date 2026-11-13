"""The owner's undo for a calendar replan move (RFC 0038 D13, bu-4c7nvp.1.12).

A goal that moves a Butlers-made block without a park (``replan.py``) leaves the
owner one receipt: an approvals card whose call is the exact inverse move. The
card reaches the owner through the existing approvals surfaces (the pushed card
and the dashboard approvals page); approving it runs the inverse through the
ordinary approvals executor. No new channel exists.

One open *offer* per goal and event holds that receipt, as a
``calendar_action_log`` row of type ``replan_undo``. The order is fail-closed:
the owner sees every move that might have landed.

* Before the write, one transaction opens the offer (the pre-image and the
  intended new times) and parks its card. A card that cannot be parked means no
  write. A retry, or a later move of the same block by the same goal while the
  card is pending, reuses the offer: its pre-image is the earliest one, so a
  retry after a failed move that in fact committed still undoes to where the
  block started. Reuse records the new intended times.
* A write the provider refused on the etag (412: nothing written) drops an offer
  that attempt opened and cancels its card, which then says nothing happened.
  Any other failure keeps the card, because the move may have landed.
* Right after the provider accepts the write, before any audit or projection,
  the offer records the moved event's etag and clears the intended times: that
  attempt is resolved. If that bind fails, the intended times still bind the
  undo.
* The undo is bound to the move: the event must still be the version the last
  landed move left (its recorded etag), or sit exactly at an unresolved
  attempt's intended times (an ambiguous failure that landed). Otherwise it declines
  without writing: ``changed_since_move`` when a move landed and the event
  changed since, ``not_moved`` when no move is known to have landed (an
  ambiguous failure that did not, or approval before the write). Its own write
  is sent once, never retried.

* A declined undo carries a fixed ``error`` text, so the approvals executor
  records it as a failed execution, never as executed.

An offer closes when its card leaves ``pending`` or expires; the next move opens
a new one.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg

from butlers.calendar_action_result import reconstruct_action_result
from butlers.modules.approvals.delivery_lifecycle import transition_pending_action
from butlers.modules.approvals.models import ActionStatus
from butlers.modules.approvals.park import park_pending_action
from butlers.modules.calendar_approval import calendar_approval_args

from .models import CalendarEvent
from .replan import undo_receipt

#: The ``calendar_action_log.action_type`` of an undo offer.
UNDO_ACTION_TYPE = "replan_undo"
#: The ``status`` of an approved undo that wrote nothing.
UNDO_DECLINED = "undo_declined"
#: How long the owner's card stays actionable.
UNDO_CARD_TTL = timedelta(days=7)
#: Who ends a card whose move the provider refused.
_REFUSED_ACTOR = "system:replan_undo"

_OPEN_SQL = """
SELECT l.idempotency_key, l.action_result
  FROM calendar_action_log AS l
  JOIN pending_actions AS p ON p.id::text = l.action_result->>'action_id'
 WHERE l.action_type = 'replan_undo'
   AND l.origin_ref = $2
   AND l.action_payload->>'goal_id' = $1
   AND p.status = 'pending'
   AND (p.expires_at IS NULL OR p.expires_at > now())
 ORDER BY l.created_at DESC
 LIMIT 1
 FOR UPDATE OF l
"""

_INSERT_SQL = """
INSERT INTO calendar_action_log (
    idempotency_key, action_type, action_status, origin_ref,
    action_payload, action_result, applied_at
) VALUES ($1, 'replan_undo', 'noop', $2, $3, $4, now())
"""

_RESULT_SQL = (
    "UPDATE calendar_action_log SET action_result = $2, updated_at = now() "
    "WHERE idempotency_key = $1"
)


@dataclass(frozen=True, slots=True)
class UndoOffer:
    """One open offer: the earliest pre-image and its parked card."""

    key: str
    pre_state: dict[str, Any]
    action_id: uuid.UUID
    created: bool


def undo_card_args(pre_state: Mapping[str, Any], *, conflict_policy: str) -> dict[str, Any]:
    """The card's canonical call: the inverse move, bound as the executor replays it."""
    return calendar_approval_args(
        "calendar_update_event",
        **undo_receipt(pre_state)["arguments"],
        conflict_policy=conflict_policy,
    )


#: The card's ``why``: true whether or not the move it guards ever lands.
UNDO_CARD_WHY = (
    "A goal is moving this Butlers-made block without asking (calendar replan "
    "exception). This card is your undo."
)


def undo_card_summary(pre_state: Mapping[str, Any]) -> str:
    """What approving does: true before and after the write, and on every reuse.

    It names only the pre-image, never a target, since a reused card may guard a
    later move than the one it was parked for.
    """
    title = pre_state.get("title") or "a Butlers block"
    return f"Undo a goal's replan move: approve to put '{title}' back at {pre_state['start_at']}."


#: Fixed owner-facing text of an approved undo that wrote nothing, by reason. The
#: approvals executor records a result carrying ``error`` as a failed execution, so
#: the card never reads as executed when nothing moved.
UNDO_DECLINED_TEXT: dict[str, str] = {
    "changed_since_move": (
        "Undo not applied: the event changed after the replan move, so nothing was moved back."
    ),
    "not_moved": (
        "Undo not applied: the replan move never landed, so there was nothing to move back."
    ),
}


def undo_card_refused_summary() -> str:
    return (
        "Nothing happened: the calendar refused this replan move because the event "
        "had changed, so nothing was moved and there is nothing to undo."
    )


def undo_refusal(offer: Mapping[str, Any], event: CalendarEvent) -> str | None:
    """``None`` when the undo may write, else why it declines (nothing is written)."""
    post_move = offer.get("post_move") or {}
    bound = post_move.get("etag")
    if bound is not None and event.etag == bound:
        return None  # still the version the last landed move left
    intended = offer.get("intended") or {}
    try:
        at_intended = event.start_at == datetime.fromisoformat(
            intended["start_at"]
        ) and event.end_at == datetime.fromisoformat(intended["end_at"])
    except (KeyError, TypeError, ValueError):
        at_intended = False
    if at_intended:
        return None  # an unresolved attempt landed, though its etag was never recorded
    return "changed_since_move" if bound is not None else "not_moved"


def _lock_key(goal_id: uuid.UUID, event_id: str) -> str:
    return f"replan_undo:{goal_id}:{event_id}"


class ReplanUndoLedger:
    """The offers in one butler's schema; every method is one transaction."""

    def __init__(self, pool: Any, *, butler_name: str) -> None:
        self._pool = pool
        self._butler_name = butler_name

    async def available(self) -> bool:
        """The receipt needs both tables; without them no move may skip a park."""
        return await self._pool.fetchval(
            "SELECT to_regclass('pending_actions') IS NOT NULL "
            "AND to_regclass('calendar_action_log') IS NOT NULL"
        )

    async def open(
        self,
        *,
        goal_id: uuid.UUID,
        event_id: str,
        calendar_id: str,
        pre_state: Mapping[str, Any],
        intended: Mapping[str, Any],
        conflict_policy: str,
    ) -> UndoOffer:
        """Open or reuse the offer and park its card, before any write."""
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1))", _lock_key(goal_id, event_id)
            )
            row = await conn.fetchrow(_OPEN_SQL, str(goal_id), event_id)
            if row is not None:
                result = reconstruct_action_result(row["action_result"])
                await conn.execute(
                    _RESULT_SQL,
                    row["idempotency_key"],
                    {**result, "intended": dict(intended)},
                )
                return UndoOffer(
                    key=row["idempotency_key"],
                    pre_state=dict(result["pre_state"]),
                    action_id=uuid.UUID(str(result["action_id"])),
                    created=False,
                )
            key = f"{_lock_key(goal_id, event_id)}:{uuid.uuid4()}"
            action_id = uuid.uuid4()
            now = datetime.now(UTC)
            await park_pending_action(
                conn,
                action_id=action_id,
                tool_name="calendar_update_event",
                tool_args=undo_card_args(pre_state, conflict_policy=conflict_policy),
                agent_summary=undo_card_summary(pre_state),
                requested_at=now,
                expires_at=now + UNDO_CARD_TTL,
                why=UNDO_CARD_WHY,
                blast_radius="self",
                reversibility="reversible",
                origin_butler=self._butler_name,
            )
            await conn.execute(
                _INSERT_SQL,
                key,
                event_id,
                {"goal_id": str(goal_id), "event_id": event_id, "calendar_id": calendar_id},
                {
                    "pre_state": dict(pre_state),
                    "action_id": str(action_id),
                    "intended": dict(intended),
                    "post_move": None,
                },
            )
            return UndoOffer(key=key, pre_state=dict(pre_state), action_id=action_id, created=True)

    async def discard(self, offer: UndoOffer) -> None:
        """The provider refused the write: drop the offer and cancel the card it opened."""
        if not offer.created:
            # A reused offer keeps its card (an earlier move landed); only this
            # attempt, which provably wrote nothing, is resolved.
            await self._pool.execute(
                "UPDATE calendar_action_log "
                "SET action_result = action_result || '{\"intended\": null}'::jsonb, "
                "updated_at = now() WHERE idempotency_key = $1",
                offer.key,
            )
            return
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "UPDATE pending_actions SET agent_summary = $2 "
                "WHERE id = $1 AND status = 'pending'",
                offer.action_id,
                undo_card_refused_summary(),
            )
            ended = await transition_pending_action(
                conn,
                action_id=offer.action_id,
                target_status=ActionStatus.CANCELLED,
                decided_by=_REFUSED_ACTOR,
                event_actor=_REFUSED_ACTOR,
                event_reason="the calendar refused the move; nothing changed",
                _already_in_transaction=True,
            )
            if not ended.changed:
                # The owner already decided the card: keep the offer so an
                # approved undo still finds it and declines ``not_moved``.
                return
            await conn.execute(
                "DELETE FROM calendar_action_log WHERE idempotency_key = $1 "
                "AND action_type = 'replan_undo'",
                offer.key,
            )

    async def commit(self, offer: UndoOffer, *, moved: Mapping[str, Any]) -> uuid.UUID:
        """Bind the offer to the version the move left; returns the card id."""
        post_move = {
            "etag": moved.get("etag"),
            "start_at": moved["start_at"],
            "end_at": moved["end_at"],
        }
        async with self._pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                "SELECT action_result FROM calendar_action_log "
                "WHERE idempotency_key = $1 FOR UPDATE",
                offer.key,
            )
            result = reconstruct_action_result(row["action_result"])
            # The attempt is resolved: from now on only the recorded etag binds the undo.
            await conn.execute(
                _RESULT_SQL, offer.key, {**result, "post_move": post_move, "intended": None}
            )
        return offer.action_id

    async def for_card(self, action_id: uuid.UUID) -> dict[str, Any] | None:
        """The offer an approved card undoes, or ``None`` for any other approval."""
        try:
            row = await self._pool.fetchrow(
                "SELECT action_result FROM calendar_action_log "
                "WHERE action_type = 'replan_undo' AND action_result->>'action_id' = $1",
                str(action_id),
            )
        except asyncpg.UndefinedTableError:
            return None  # no projection tables: no move could have left an offer
        if row is None:
            return None
        offer = reconstruct_action_result(row.get("action_result"))
        if offer.get("action_id") != str(action_id) or "pre_state" not in offer:
            return None
        return offer


def replan_undo_ledger(module: Any) -> ReplanUndoLedger | None:
    """The module's ledger, or ``None`` when it has no database."""
    pool = getattr(getattr(module, "_db", None), "pool", None)
    if pool is None:
        return None
    return ReplanUndoLedger(pool, butler_name=module._butler_name)


__all__ = [
    "UNDO_ACTION_TYPE",
    "UNDO_CARD_TTL",
    "UNDO_DECLINED",
    "UNDO_CARD_WHY",
    "UNDO_DECLINED_TEXT",
    "ReplanUndoLedger",
    "UndoOffer",
    "replan_undo_ledger",
    "undo_card_args",
    "undo_card_refused_summary",
    "undo_card_summary",
    "undo_refusal",
]
