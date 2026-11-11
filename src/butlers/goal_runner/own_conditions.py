"""What ``goal.wait_until`` may wait on: the goal's own ledger (RFC 0038 S2.2, S4).

bu-dhloi1.305 PR1. A lead may end its turn waiting on a closed S4 predicate over its own goal: the
owner's decision on one of its parked approvals (``decision``) and the settlement of one of its
effects (``effect_settled``). Those leaves need no approval, since they read only rows this goal
already owns and can wake it on nothing another principal controls.

Every other leaf is refused here, before anything is recorded:

* ``owner_input``: admission joins the owner's reply only to an ``owner_answer`` wait, so a
  conditions instance on it could never be fed (``goal.wait_owner`` is the reply wait).
* ``at`` and ``transition``: clock and perception subscriptions are third-party evidence; they
  wait on their own approval path.

The grant and the registration snapshot are derived from the goal's effect rows, never from the
model: the decisions a leaf may name are the effects that went through approval, the effects it
may name are all of the goal's effects, and each status maps to the S4 value it already shows.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

from butlers.conditions import ConditionEvent, Grant, PredicateError, Snapshot
from butlers.conditions import predicate as ast

#: The most wakes one wait may grant its goal, and how far past admission it may run.
MAX_WAKES = 6
HORIZON = timedelta(days=45)
OWN_TAGS = frozenset({"decision", "effect_settled"})

# An approval effect's status as the owner's decision: past ``parked`` the call was approved,
# whatever its dispatch did since; the domain's ends are the decision itself. ``refused`` is
# policy, not a decision.
_DECIDED = {
    "approved": "approved",
    "dispatched": "approved",
    "committed": "approved",
    "failed": "approved",
    "unknown": "approved",
    "rejected": "rejected",
    "expired": "expired",
    "cancelled": "cancelled",
}
# An effect's status once it can no longer change (D11): an effect that never ran is cancelled.
_SETTLED = {
    "committed": "succeeded",
    "failed": "failed",
    "unknown": "unknown",
    "cancelled": "cancelled",
    "rejected": "cancelled",
    "expired": "cancelled",
    "refused": "cancelled",
}


@dataclass(frozen=True, slots=True)
class OwnEffect:
    """One of the goal's effect rows: canonical id, status, and whether it parked for approval."""

    id: str
    status: str
    approval: bool


def own_predicate(value: object) -> dict:
    """The canonical predicate, or ``PredicateError`` when it is not S4 or not goal-own."""
    canonical = ast.validate(value)
    for leaf in ast.leaves(canonical):
        if leaf.tag not in OWN_TAGS:
            raise PredicateError(f"{leaf.tag} is not a goal-own leaf")
    return canonical


def named(predicate: dict) -> tuple[frozenset[str], frozenset[str]]:
    """``(decision subjects, effect ids)`` the predicate names."""
    leaves = list(ast.leaves(predicate))
    return (
        frozenset(leaf.body["subject_id"] for leaf in leaves if leaf.tag == "decision"),
        frozenset(leaf.body["effect_id"] for leaf in leaves if leaf.tag == "effect_settled"),
    )


def grant_for(goal_id: str, predicate: dict, effects: Iterable[OwnEffect]) -> Grant:
    """The server-derived subscription grant: the predicate's sources over this goal's rows."""
    effects = tuple(effects)
    return Grant(
        goal_id=goal_id,
        sources=ast.sources(predicate),
        decisions=frozenset(e.id for e in effects if e.approval),
        effects=frozenset(e.id for e in effects),
    )


def events_for(effects: Iterable[tuple[OwnEffect, datetime]]) -> list[ConditionEvent]:
    """The goal's own events, from each effect's current status and when it last changed.

    The same mappings as the registration snapshot. A decision event names the effect id as
    its ``subject_id``, which is what a ``decision`` leaf binds. Each event's id is derived from
    its source, effect and value, so a redelivered event is the same event; its position is
    the status change time in microseconds. The facts are monotonic (a decision is made once,
    a settled effect stays settled), so feeding current state never loses or invents a fire.
    """
    events: list[ConditionEvent] = []
    for effect, changed_at in effects:
        position = int(changed_at.timestamp() * 1_000_000)
        if effect.approval and (outcome := _DECIDED.get(effect.status)) is not None:
            events.append(
                _event(ast.SOURCE_DECISION, "decision", effect.id, outcome, position, changed_at)
            )
        if (status := _SETTLED.get(effect.status)) is not None:
            events.append(
                _event(ast.SOURCE_EFFECT, "effect_settled", effect.id, status, position, changed_at)
            )
    return events


def _event(
    source: str, tag: str, effect_id: str, value: str, position: int, at: datetime
) -> ConditionEvent:
    data = (
        {"subject_id": effect_id, "outcome": value}
        if tag == "decision"
        else {"effect_id": effect_id, "status": value}
    )
    return ConditionEvent(
        id=str(uuid5(NAMESPACE_URL, f"butlers:goal-event:{source}:{effect_id}:{value}")),
        source=source,
        position=position,
        at=at,
        tag=tag,
        data=data,
    )


def snapshot_for(now: datetime, effects: Iterable[OwnEffect]) -> Snapshot:
    """The registration snapshot: what each effect already shows, read under the goal lock."""
    decisions: dict[str, str] = {}
    settled: dict[str, str] = {}
    for effect in effects:
        if effect.approval and (outcome := _DECIDED.get(effect.status)) is not None:
            decisions[effect.id] = outcome
        if (status := _SETTLED.get(effect.status)) is not None:
            settled[effect.id] = status
    return Snapshot(now=now, decisions=decisions, effects=settled)
