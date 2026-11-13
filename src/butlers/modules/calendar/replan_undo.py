"""The owner's undo for a calendar replan move (RFC 0038 D13, bu-4c7nvp.1.12).

A goal that moves a Butlers-made block without a park (``replan.py``) leaves the
owner one receipt: an approvals card whose call is the exact inverse move. The
card reaches the owner through the existing approvals surfaces (the pushed card
and the dashboard approvals page); approving it runs the inverse through the
ordinary approvals executor. No new channel exists.

One open *offer* per goal and event holds that receipt, as a
``calendar_action_log`` row of type ``replan_undo``:

* The offer opens before the write, holding the pre-image the move read. A
  retry after a failed move that in fact committed reads the already-moved
  event, so its own pre-image is the new time; it reuses the open offer, whose
  pre-image is the earliest one. A write the provider refused on the etag
  (nothing written) discards an offer it opened.
* On commit the offer records the moved event's etag and parks the card once.
  Later moves of the same block by the same goal, while the card is pending,
  only rebind the etag: the card still puts the block back where it started.
* The undo is bound to that etag. Approved, it declines without writing when
  the event changed since the move, and its own write is sent once, never
  retried, so a 412 can only mean the event changed in between.

An offer closes when its card leaves ``pending`` (executed, rejected, expired or
abandoned); the next committed move opens a new one.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg

from butlers.calendar_action_result import reconstruct_action_result
from butlers.modules.approvals.park import park_pending_action
from butlers.modules.calendar_approval import calendar_approval_args

from .replan import undo_receipt

#: The ``calendar_action_log.action_type`` of an undo offer.
UNDO_ACTION_TYPE = "replan_undo"
#: The ``status`` of an approved undo that wrote nothing.
UNDO_DECLINED = "undo_declined"
#: How long the owner's card stays actionable.
UNDO_CARD_TTL = timedelta(days=7)

_OPEN_SQL = """
SELECT l.idempotency_key, l.action_result
  FROM calendar_action_log AS l
  LEFT JOIN pending_actions AS p ON p.id::text = l.action_result->>'action_id'
 WHERE l.action_type = 'replan_undo'
   AND l.origin_ref = $2
   AND l.action_payload->>'goal_id' = $1
   AND (l.action_result->>'action_id' IS NULL OR p.status = 'pending')
 ORDER BY l.created_at DESC
 LIMIT 1
"""

_INSERT_SQL = """
INSERT INTO calendar_action_log (
    idempotency_key, action_type, action_status, origin_ref,
    action_payload, action_result, applied_at
) VALUES ($1, 'replan_undo', 'noop', $2, $3, $4, now())
"""


@dataclass(frozen=True, slots=True)
class UndoOffer:
    """One open offer: the earliest pre-image and the card, once parked."""

    key: str
    pre_state: dict[str, Any]
    action_id: uuid.UUID | None
    created: bool


def undo_card_args(pre_state: Mapping[str, Any], *, conflict_policy: str) -> dict[str, Any]:
    """The card's canonical call: the inverse move, bound as the executor replays it."""
    return calendar_approval_args(
        "calendar_update_event",
        **undo_receipt(pre_state)["arguments"],
        conflict_policy=conflict_policy,
    )


def undo_card_summary(pre_state: Mapping[str, Any], moved: Mapping[str, Any]) -> str:
    title = pre_state.get("title") or "a Butlers block"
    return (
        f"Butlers moved '{title}' from {pre_state['start_at']} to {moved['start_at']} "
        "while replanning a goal. Approve to put it back."
    )


def _lock_key(goal_id: uuid.UUID, event_id: str) -> str:
    return f"replan_undo:{goal_id}:{event_id}"


def _action_id(result: Mapping[str, Any]) -> uuid.UUID | None:
    raw = result.get("action_id")
    return uuid.UUID(str(raw)) if raw else None


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
    ) -> UndoOffer:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1))", _lock_key(goal_id, event_id)
            )
            row = await conn.fetchrow(_OPEN_SQL, str(goal_id), event_id)
            if row is not None:
                result = reconstruct_action_result(row["action_result"])
                return UndoOffer(
                    key=row["idempotency_key"],
                    pre_state=dict(result["pre_state"]),
                    action_id=_action_id(result),
                    created=False,
                )
            key = f"{_lock_key(goal_id, event_id)}:{uuid.uuid4()}"
            await conn.execute(
                _INSERT_SQL,
                key,
                event_id,
                {"goal_id": str(goal_id), "event_id": event_id, "calendar_id": calendar_id},
                {"pre_state": dict(pre_state), "action_id": None, "post_move": None},
            )
            return UndoOffer(key=key, pre_state=dict(pre_state), action_id=None, created=True)

    async def discard(self, offer: UndoOffer) -> None:
        """Drop an offer this attempt opened when its write provably wrote nothing."""
        if not offer.created:
            return
        await self._pool.execute(
            "DELETE FROM calendar_action_log WHERE idempotency_key = $1 "
            "AND action_type = 'replan_undo' AND action_result->>'action_id' IS NULL",
            offer.key,
        )

    async def commit(
        self,
        offer: UndoOffer,
        *,
        moved: Mapping[str, Any],
        conflict_policy: str,
    ) -> uuid.UUID:
        """Bind the offer to the moved event and park its card once; returns the card id."""
        post_move = {
            "etag": moved.get("etag"),
            "start_at": moved["start_at"],
            "end_at": moved["end_at"],
        }
        async with self._pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                "SELECT action_payload, action_result FROM calendar_action_log "
                "WHERE idempotency_key = $1 FOR UPDATE",
                offer.key,
            )
            result = reconstruct_action_result(row["action_result"])
            action_id = _action_id(result)
            if action_id is None:
                action_id = uuid.uuid4()
                now = datetime.now(UTC)
                await park_pending_action(
                    conn,
                    action_id=action_id,
                    tool_name="calendar_update_event",
                    tool_args=undo_card_args(result["pre_state"], conflict_policy=conflict_policy),
                    agent_summary=undo_card_summary(result["pre_state"], moved),
                    requested_at=now,
                    expires_at=now + UNDO_CARD_TTL,
                    why=(
                        "A goal moved this Butlers-made block without asking (calendar "
                        "replan exception). This card is your undo."
                    ),
                    blast_radius="self",
                    reversibility="reversible",
                    origin_butler=self._butler_name,
                )
            await conn.execute(
                "UPDATE calendar_action_log SET action_result = $2, updated_at = now() "
                "WHERE idempotency_key = $1",
                offer.key,
                {**result, "action_id": str(action_id), "post_move": post_move},
            )
            return action_id

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


def changed_since_move(offer: Mapping[str, Any], current_etag: str | None) -> bool:
    """True when the event is no longer the version the move left."""
    post_move = offer.get("post_move") or {}
    bound = post_move.get("etag")
    return bound is None or current_etag != bound


__all__ = [
    "UNDO_ACTION_TYPE",
    "UNDO_CARD_TTL",
    "UNDO_DECLINED",
    "ReplanUndoLedger",
    "UndoOffer",
    "changed_since_move",
    "replan_undo_ledger",
    "undo_card_args",
    "undo_card_summary",
]
