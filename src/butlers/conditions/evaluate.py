"""Pure deterministic evaluation of one condition instance (RFC 0038 S4).

No I/O, no clock reads, no model. Callers pass trusted events and an explicit ``now``.
Leaf values latch: once ``true`` a leaf stays ``true``; replay and reordering are idempotent.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from butlers.conditions.model import ConditionEvent, Instance, Snapshot, State, Tri
from butlers.conditions.predicate import SOURCE_CLOCK, Leaf, leaves
from butlers.perception.model import instant

REALTIME_HORIZON = timedelta(minutes=10)
LATE_AT_GRACE = timedelta(seconds=60)


def combine(node: dict, values: dict[str, Tri], prefix: str = "") -> Tri:
    """Kleene all/any: known false wins only when sufficient, unknown stays explicit."""
    tag = node["tag"]
    if tag not in ("all", "any"):
        return values[prefix.rstrip(".") or "0"]
    results = [combine(c, values, f"{prefix}{i}.") for i, c in enumerate(node["children"])]
    if tag == "all":
        if Tri.FALSE in results:
            return Tri.FALSE
        return Tri.TRUE if all(r is Tri.TRUE for r in results) else Tri.UNKNOWN
    if Tri.TRUE in results:
        return Tri.TRUE
    return Tri.FALSE if all(r is Tri.FALSE for r in results) else Tri.UNKNOWN


def initial_leaf(leaf: Leaf, snap: Snapshot) -> Tri:
    body = leaf.body
    if leaf.source in snap.unmeasurable:
        return Tri.UNKNOWN
    if leaf.tag == "decision":
        return Tri.TRUE if snap.decisions.get(body["subject_id"]) == body["outcome"] else Tri.FALSE
    if leaf.tag == "owner_input":
        return Tri.TRUE if body["binding_ref"] in snap.inputs else Tri.FALSE
    if leaf.tag == "effect_settled":
        return Tri.TRUE if snap.effects.get(body["effect_id"]) in body["status_set"] else Tri.FALSE
    if leaf.tag == "at":
        return Tri.TRUE if snap.now >= instant(body["instant"]) else Tri.FALSE
    return Tri.FALSE  # transition is an edge; a snapshot cannot witness one


def initial_leaves(predicate: dict, snap: Snapshot) -> dict[str, Tri]:
    return {leaf.path: initial_leaf(leaf, snap) for leaf in leaves(predicate)}


def _event_leaf(leaf: Leaf, cur: Tri, ev: ConditionEvent, now: datetime) -> Tri:
    body, data = leaf.body, ev.data
    if cur is Tri.TRUE or ev.tag != leaf.tag or ev.source != leaf.source:
        return cur
    if leaf.tag == "transition":
        if ev.late or ev.retracted or ev.record or now - ev.at > REALTIME_HORIZON:
            return cur  # historical evidence is recorded elsewhere, never a wake
        if data.get("kind") != body["kind"] or data.get("subject") != body["subject"]:
            return cur
        if not ev.measurable:
            return Tri.UNKNOWN
        if data.get("from") == body["from"] and data.get("to") == body["to"]:
            return Tri.TRUE
        return Tri.FALSE if cur is Tri.UNKNOWN else cur
    if leaf.tag == "decision":
        if data.get("subject_id") != body["subject_id"]:
            return cur
        if not ev.measurable:
            return Tri.UNKNOWN
        return Tri.TRUE if data.get("outcome") == body["outcome"] else Tri.FALSE
    if leaf.tag == "owner_input":
        if data.get("goal_id") != body["goal_id"] or data.get("binding_ref") != body["binding_ref"]:
            return cur
        return Tri.TRUE if ev.measurable else Tri.UNKNOWN
    if leaf.tag == "effect_settled":
        if data.get("effect_id") != body["effect_id"]:
            return cur
        if not ev.measurable:
            return Tri.UNKNOWN
        return Tri.TRUE if data.get("status") in body["status_set"] else Tri.FALSE
    return cur


def _clock_leaf(leaf: Leaf, cur: Tri, now: datetime, unmeasurable: frozenset[str]) -> Tri:
    if leaf.tag != "at" or cur is Tri.TRUE:
        return cur
    if SOURCE_CLOCK in unmeasurable:
        return Tri.UNKNOWN
    return Tri.TRUE if now >= instant(leaf.body["instant"]) else Tri.FALSE


@dataclass(frozen=True)
class Advance:
    instance: Instance
    changed: bool
    reason: str
    trigger: str | None = None


def advance(
    inst: Instance,
    events: Iterable[ConditionEvent],
    now: datetime,
    *,
    unmeasurable: frozenset[str] = frozenset(),
) -> Advance:
    """Apply events and time to an open instance; closed instances never change."""
    if inst.state is not State.OPEN:
        return Advance(inst, False, f"closed:{inst.state}")
    leaf_list = list(leaves(inst.predicate))
    values = dict(inst.leaves)
    cursors = dict(inst.cursors)
    trigger: str | None = None
    reason = "pending"
    for ev in sorted(events, key=lambda e: (e.position, e.id)):
        if ev.source not in inst.source_boundary or ev.at >= inst.expires_at:
            continue  # undeclared source or post-expiry evidence never contributes
        cursors[ev.source] = max(cursors.get(ev.source, -1), ev.position)
        for leaf in leaf_list:
            values[leaf.path] = _event_leaf(leaf, values[leaf.path], ev, now)
        if combine(inst.predicate, values) is Tri.TRUE:
            trigger, reason = ev.id, "fired:event"
            break
    if trigger is None:
        for leaf in leaf_list:
            values[leaf.path] = _clock_leaf(leaf, values[leaf.path], now, unmeasurable)
        if now < inst.expires_at and combine(inst.predicate, values) is Tri.TRUE:
            trigger, reason = f"clock:{inst.id}:{inst.generation}", "fired:clock"
            late = [
                leaf
                for leaf in leaf_list
                if leaf.tag == "at" and now - instant(leaf.body["instant"]) > LATE_AT_GRACE
            ]
            if late:
                reason = "fired:clock_late"
    result = combine(inst.predicate, values)
    state = inst.state
    if trigger is not None:
        state = State.FIRED
    elif now >= inst.expires_at:
        state, reason = State.EXPIRED, "expired"
    new = replace(inst, leaves=values, cursors=cursors, result=result, state=state)
    changed = (new.leaves, new.cursors, new.result, new.state) != (
        inst.leaves,
        inst.cursors,
        inst.result,
        inst.state,
    )
    if changed:
        new = replace(new, version=inst.version + 1)
    return Advance(new if changed else inst, changed, reason, trigger)
