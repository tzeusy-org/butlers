"""What ``goal.wait_until`` may wait on: the goal's own ledger (RFC 0038 S2.2, S4).

bu-dhloi1.305 PR1 and PR2b. A lead may end its turn waiting on a closed S4 predicate over its own
goal:

* ``decision``: the owner's decision on one of its parked approvals;
* ``effect_settled``: the settlement of one of its effects;
* ``owner_input``: the owner's reply to one of its live prompts. Wake admission joins that reply
  to the waiting goal as an unread inbox row, and the feed turns the row into the leaf's event.

Those leaves need no approval, since they read only rows this goal already owns and can wake it
on nothing another principal controls. ``at`` and ``transition`` are refused here, before
anything is recorded: clock and perception subscriptions are third-party evidence that wait on
their own approval path.

The grant and the registration snapshot are derived from the goal's ledger rows, never from the
model:

* the decisions a leaf may name are the effects that went through approval;
* the effects it may name are all of the goal's effects;
* the prompts it may name are the goal's committed owner prompts still accepting input.

Each effect status maps to the S4 value it already shows. A prompt shows input while a reply to
it sits unread in the goal's inbox, so a reply a lead already read never wakes the goal again.
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
OWN_TAGS = frozenset({"decision", "effect_settled", "owner_input"})

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
    """One of the goal's effect rows: canonical id, status, whether it parked for approval, and
    whether it is a live owner prompt (committed, to the owner, still accepting input)."""

    id: str
    status: str
    approval: bool
    prompt: bool = False


@dataclass(frozen=True, slots=True)
class OwnReply:
    """An unread owner reply in the goal's inbox: the row id, the prompt it answers, and when."""

    id: str
    goal_id: str
    binding: str
    at: datetime


@dataclass(frozen=True, slots=True)
class Named:
    """The ledger rows a predicate names, each checked against the goal before recording."""

    decisions: frozenset[str]
    effects: frozenset[str]
    inputs: frozenset[str]
    goals: frozenset[str]


def own_predicate(value: object) -> dict:
    """The canonical predicate, or ``PredicateError`` when it is not S4 or not goal-own."""
    canonical = ast.validate(value)
    for leaf in ast.leaves(canonical):
        if leaf.tag not in OWN_TAGS:
            raise PredicateError(f"{leaf.tag} is not a goal-own leaf")
    return canonical


def named(predicate: dict) -> Named:
    """The decision subjects, effect ids, prompt bindings and goals the predicate names."""
    leaves = list(ast.leaves(predicate))
    inputs = [leaf.body for leaf in leaves if leaf.tag == "owner_input"]
    return Named(
        decisions=frozenset(leaf.body["subject_id"] for leaf in leaves if leaf.tag == "decision"),
        effects=frozenset(
            leaf.body["effect_id"] for leaf in leaves if leaf.tag == "effect_settled"
        ),
        inputs=frozenset(body["binding_ref"] for body in inputs),
        goals=frozenset(body["goal_id"] for body in inputs),
    )


def grant_for(goal_id: str, predicate: dict, effects: Iterable[OwnEffect]) -> Grant:
    """The server-derived subscription grant: the predicate's sources over this goal's rows."""
    effects = tuple(effects)
    return Grant(
        goal_id=goal_id,
        sources=ast.sources(predicate),
        decisions=frozenset(e.id for e in effects if e.approval),
        effects=frozenset(e.id for e in effects),
        bindings=frozenset(e.id for e in effects if e.prompt),
    )


def events_for(
    effects: Iterable[tuple[OwnEffect, datetime]], replies: Iterable[OwnReply] = ()
) -> list[ConditionEvent]:
    """The goal's own events, from each effect's current status and its unread replies.

    The same mappings as the registration snapshot. A decision event names the effect id as
    its ``subject_id``, which is what a ``decision`` leaf binds. Each effect event's id is
    derived from its source, effect and value, so a redelivered event is the same event; its
    position is the status change time in microseconds. The facts are monotonic (a decision is
    made once, a settled effect stays settled), so feeding current state never loses or invents
    a fire. A reply's event is its inbox row: one event per row, positioned at its arrival.
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
    for reply in replies:
        events.append(
            ConditionEvent(
                id=str(uuid5(NAMESPACE_URL, f"butlers:goal-event:{ast.SOURCE_INBOX}:{reply.id}")),
                source=ast.SOURCE_INBOX,
                position=int(reply.at.timestamp() * 1_000_000),
                at=reply.at,
                tag="owner_input",
                data={"goal_id": reply.goal_id, "binding_ref": reply.binding},
            )
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


def snapshot_for(
    now: datetime, effects: Iterable[OwnEffect], inputs: Iterable[str] = ()
) -> Snapshot:
    """The registration snapshot, read under the goal lock: what each effect already shows, and
    ``inputs``, the prompts with an unread reply in the goal's inbox."""
    decisions: dict[str, str] = {}
    settled: dict[str, str] = {}
    for effect in effects:
        if effect.approval and (outcome := _DECIDED.get(effect.status)) is not None:
            decisions[effect.id] = outcome
        if (status := _SETTLED.get(effect.status)) is not None:
            settled[effect.id] = status
    return Snapshot(now=now, decisions=decisions, inputs=frozenset(inputs), effects=settled)
