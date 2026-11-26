"""``calendar_apply_plan``: a compiled plan becomes one approvals card per step (bu-5ii4gj.11 P2a).

The plan is re-compiled and applied only if its digest still matches. Every move step parks as its
own approvals card through ``park_resolution_move`` (bound to the etag the plan saw, written once,
declining ``changed_since_check`` if the event changed); a step that uses a slot another step
vacates carries that step's card as a dependency and declines ``dependency_not_applied`` unless it
was applied. The call returns ``pending`` at once: the owner decides each card in the approvals
surface. Each step leaves a ``plan_step`` row in ``calendar_action_log`` so re-applying the same
plan replays the recorded receipts and parks only the steps that have none. A step's outcome is
read off its card, never assumed.

Decline steps (an invited meeting in a cleared window) park ``calendar_respond`` through the
module's own response preparer (``response_park.park_response``): the owner decides the card, and a
changed occurrence is caught by the command's own fence. Their outcome is read off the same card.
A response that cannot be parked is recorded ``skipped`` with the preparer's reason, never dropped.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal
from zoneinfo import ZoneInfo

from butlers.core.temporal.change_plans import Plan, PlanStep

from .invitation_guard import pool_of
from .plan_tools import compile_for_window
from .resolution_move import card_outcome, park_resolution_move
from .response_park import park_response

if TYPE_CHECKING:
    from . import CalendarModule

logger = logging.getLogger(__name__)

PLAN_ROW = "plan"
PLAN_STEP_ROW = "plan_step"

_INSERT_SQL = """
INSERT INTO calendar_action_log (
    idempotency_key, action_type, action_status, origin_ref,
    action_payload, action_result, applied_at
) VALUES ($1, $2, 'noop', $3, $4, $5, now())
ON CONFLICT (idempotency_key) DO NOTHING
"""

_RECEIPTS_SQL = """
SELECT action_payload FROM calendar_action_log
WHERE action_type = 'plan_step' AND action_payload->>'plan_id' = $1
"""


def _obj(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


async def step_outcome(pool: Any, receipt: dict[str, Any]) -> tuple[str, str | None]:
    """``(outcome, reason)`` of one recorded step, read off its card.

    See ``card_outcome``: ``applied`` only when the approved call reports that it wrote; anything
    unreadable or unconfirmed is ``uncertain``, never ``applied``.
    """
    if receipt.get("outcome") == "skipped":
        return "skipped", receipt.get("reason")
    try:
        row = await pool.fetchrow(
            "SELECT status, execution_result FROM pending_actions WHERE id = $1",
            uuid.UUID(str(receipt["action_id"])),
        )
    except Exception:
        logger.warning("plan step: card unreadable", exc_info=True)
        return "uncertain", "card_unreadable"
    if row is None:
        return "uncertain", "card_missing"
    return card_outcome(row["status"], row["execution_result"])


def _call_args(step: PlanStep) -> dict[str, Any]:
    args = dict(step.exact_call["args"])
    args.pop("request_id", None)  # the card derives its own bound request id
    return args


async def apply_plan(
    module: CalendarModule,
    butler_name: str,
    *,
    plan_id: str,
    plan_digest: str,
    kind: Literal["clear_window", "shift_window"],
    window_start: datetime,
    window_end: datetime,
    shift_minutes: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Park the plan's steps (resuming any already parked); returns ``pending`` receipts."""
    compiled = await compile_for_window(
        module,
        kind=kind,
        window_start=window_start,
        window_end=window_end,
        shift_minutes=shift_minutes,
        now=now,
    )
    if isinstance(compiled, dict):
        return compiled
    plan: Plan = compiled.plan
    if plan.reason is not None:
        return {"status": "refused", "reason": plan.reason, "plan_id": plan.plan_id}
    if plan.plan_id != plan_id or plan.plan_digest != plan_digest:
        # Nothing is written: the owner must see the plan as it is now.
        return {
            "status": "refused",
            "reason": "plan_changed",
            "plan_id": plan.plan_id,
            "plan_digest": plan.plan_digest,
        }
    pool = pool_of(module)
    zone = ZoneInfo(compiled.zone) if compiled.zone else None
    try:
        recorded = {
            int(_obj(r["action_payload"])["index"]): _obj(r["action_payload"])
            for r in await pool.fetch(_RECEIPTS_SQL, plan.plan_id)
        }
    except Exception:
        logger.warning("calendar_apply_plan: receipts unreadable", exc_info=True)
        return {"status": "refused", "reason": "receipts_unreadable", "plan_id": plan.plan_id}

    await pool.execute(
        _INSERT_SQL,
        f"{PLAN_ROW}:{plan.plan_id}",
        PLAN_ROW,
        plan.plan_id,
        {
            "plan_id": plan.plan_id,
            "plan_digest": plan.plan_digest,
            "kind": kind,
            "window": [window_start.isoformat(), window_end.isoformat()],
            "steps": len(plan.steps),
        },
        {},
    )
    action_ids: dict[int, str] = {
        i: r["action_id"] for i, r in recorded.items() if r.get("action_id")
    }
    receipts: list[dict[str, Any]] = []
    for step in plan.steps:
        receipt = recorded.get(step.index)
        if receipt is None:
            receipt = await _record_step(
                module, pool, butler_name, plan, step, action_ids, zone=zone
            )
            if receipt.get("action_id"):
                action_ids[step.index] = receipt["action_id"]
        outcome, reason = await step_outcome(pool, receipt)
        receipts.append(
            {
                "index": step.index,
                "event_ref": step.entry_id,
                "action": step.action,
                "outcome": outcome,
                "reason": reason,
                "approval_id": receipt.get("action_id"),
                "depends_on": list(step.depends_on),
            }
        )
    pending = any(r["outcome"] == "pending" for r in receipts)
    return {
        "status": "pending" if pending else "settled",
        "plan_id": plan.plan_id,
        "plan_digest": plan.plan_digest,
        "steps": receipts,
        "skipped": [{"event_ref": s.entry_id, "why": s.why} for s in plan.skipped],
    }


async def _record_step(
    module: CalendarModule,
    pool: Any,
    butler_name: str,
    plan: Plan,
    step: PlanStep,
    action_ids: dict[int, str],
    *,
    zone: ZoneInfo | None,
) -> dict[str, Any]:
    """Park one step (or record why it was not) and write its ``plan_step`` receipt."""
    receipt: dict[str, Any] = {
        "plan_id": plan.plan_id,
        "index": step.index,
        "event_ref": step.entry_id,
        "action": step.action,
        "etag": step.etag,
        "authority": step.authority,
        "depends_on": list(step.depends_on),
    }
    if step.action == "decline":
        parked = await park_response(
            module,
            dict(step.exact_call["args"]),
            why="A calendar plan declines this invited meeting.",
        )
        card = parked.get("command_id") or parked.get("action_id")
        if card:
            receipt["action_id"] = str(card)
        else:
            reason = str(parked.get("error") or "response_unavailable")
            receipt.update(outcome="skipped", reason=reason)
    elif step.action != "move":
        receipt.update(outcome="skipped", reason="unsupported_action")
    else:
        deps = [action_ids[d] for d in step.depends_on if d in action_ids]
        if len(deps) != len(step.depends_on):
            # A dependency that was never parked can never have applied.
            receipt.update(outcome="skipped", reason="dependency_not_parked")
        else:
            action_id, _created = await park_resolution_move(
                pool,
                butler_name=butler_name,
                pair_id=plan.plan_id,
                option_id=f"step:{step.index}",
                issue_digest=plan.plan_digest,
                call_args=_call_args(step),
                etag=step.etag,
                authority=step.authority,
                zone=zone,
                series_event_id=step.series_event_id,
                depends_on=[uuid.UUID(d) for d in deps],
            )
            receipt["action_id"] = str(action_id)
    await pool.execute(
        _INSERT_SQL,
        f"{PLAN_STEP_ROW}:{plan.plan_id}:{step.index}",
        PLAN_STEP_ROW,
        step.entry_id,
        receipt,
        {},
    )
    return receipt
