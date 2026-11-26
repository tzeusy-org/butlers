"""``calendar_undo_plan``: one approvals card per reversal (bu-5ii4gj.11 P2a).

Only a step whose move really applied is reversed, and each reversal is bound to the etag that
step recorded when it landed (``etag_after``): approving it moves the event back to where it was
only if the event is still as the plan left it, otherwise it declines ``changed_since_move`` and
writes nothing. A step that was declined, uncertain, skipped or still pending is never reversed.
Each card is its own decision: a reversal can reach other people, so none is bundled. The call
returns ``pending`` at once; calling it again returns the cards already parked.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from butlers.core.general_settings import load_configured_general_timezone

from .invitation_guard import pool_of
from .plan_apply import PLAN_ROW, _obj, step_outcome
from .resolution_move import park_resolution_move

if TYPE_CHECKING:
    from . import CalendarModule

logger = logging.getLogger(__name__)

UNDO_SUMMARY = "Undo a calendar plan: put one moved event back at its earlier time."

_RECEIPTS_SQL = """
SELECT action_payload FROM calendar_action_log
WHERE action_type = 'plan_step' AND action_payload->>'plan_id' = $1
ORDER BY (action_payload->>'index')::int
"""
_PLAN_SQL = (
    "SELECT action_payload FROM calendar_action_log "
    f"WHERE action_type = '{PLAN_ROW}' AND idempotency_key = $1"
)
_BINDING_SQL = (
    "SELECT action_result FROM calendar_action_log "
    "WHERE action_type = 'resolution_move' AND action_result->>'action_id' = $1"
)


def _undo_args(card: dict[str, Any], binding: dict[str, Any]) -> dict[str, Any] | None:
    """The reversing call, or None when the pre-image or the calendar was not recorded."""
    start, end = binding.get("pre_start"), binding.get("pre_end")
    event_id, calendar_id = card.get("event_id"), card.get("calendar_id")
    if not (start and end and event_id and calendar_id):
        return None
    return {"event_id": event_id, "calendar_id": calendar_id, "start_at": start, "end_at": end}


async def undo_plan(
    module: CalendarModule, butler_name: str, *, plan_id: str, now: datetime | None = None
) -> dict[str, Any]:
    """Park one reversal card per applied step; returns ``pending`` receipts."""
    del now
    pool = pool_of(module)
    if pool is None:
        return {"status": "refused", "reason": "database_unavailable"}
    try:
        plan_row = await pool.fetchrow(_PLAN_SQL, f"{PLAN_ROW}:{plan_id}")
        receipts = [_obj(r["action_payload"]) for r in await pool.fetch(_RECEIPTS_SQL, plan_id)]
        zone_name = await load_configured_general_timezone(pool)
    except Exception:
        logger.warning("calendar_undo_plan: plan unreadable", exc_info=True)
        return {"status": "refused", "reason": "plan_unreadable", "plan_id": plan_id}
    if plan_row is None:
        return {"status": "refused", "reason": "unknown_plan", "plan_id": plan_id}
    digest = str(_obj(plan_row["action_payload"]).get("plan_digest") or "")
    zone = ZoneInfo(str(zone_name)) if zone_name else None

    reversals: list[dict[str, Any]] = []
    for receipt in receipts:
        entry: dict[str, Any] = {"index": receipt["index"], "event_ref": receipt.get("event_ref")}
        outcome, _reason = await step_outcome(pool, receipt)
        if receipt.get("action") == "decline" and outcome == "applied":
            # A response reversal is the dashboard's server-bound inverse flow, not a move.
            reversals.append(
                {**entry, "undone": False, "reason": "response_reversal_not_supported"}
            )
            continue
        if outcome != "applied":
            reversals.append({**entry, "undone": False, "reason": f"step_{outcome}"})
            continue
        try:
            action_id = uuid.UUID(str(receipt["action_id"]))
            binding = _obj(
                (await pool.fetchrow(_BINDING_SQL, str(action_id)) or {}).get("action_result")
            )
            card_row = await pool.fetchrow(
                "SELECT tool_args FROM pending_actions WHERE id = $1", action_id
            )
        except Exception:
            logger.warning("calendar_undo_plan: step unreadable", exc_info=True)
            reversals.append({**entry, "undone": False, "reason": "step_unreadable"})
            continue
        card = _obj(card_row["tool_args"]) if card_row is not None else {}
        call = _undo_args(card, binding)
        etag_after = binding.get("etag_after")
        if call is None or not etag_after:
            # Nothing to bind the reversal to: it is never offered unbound.
            reversals.append({**entry, "undone": False, "reason": "no_recorded_result"})
            continue
        undo_id, _created = await park_resolution_move(
            pool,
            butler_name=butler_name,
            pair_id=f"undo:{plan_id}",
            option_id=f"step:{receipt['index']}",
            issue_digest=digest,
            call_args=call,
            etag=str(etag_after),
            authority=str(receipt.get("authority") or "parked_per_event"),
            zone=zone,
            stale_reason="changed_since_move",
            summary=UNDO_SUMMARY,
        )
        reversals.append({**entry, "undone": None, "approval_id": str(undo_id), "reason": None})
    return {"status": "pending", "plan_id": plan_id, "reversals": reversals}


__all__ = ["undo_plan"]
