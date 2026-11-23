"""Pure deterministic evaluation of one condition instance (RFC 0038 S4).

No I/O, no clock reads, no model. Callers pass trusted events and an explicit ``now``.
Leaf values latch: once ``true`` a leaf stays ``true``; replay and reordering are idempotent.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import cast
from uuid import UUID

from butlers.conditions.model import ConditionEvent, Instance, Snapshot, State, Tri
from butlers.conditions.predicate import M7_TAGS, SOURCE_CLOCK, Leaf, leaves
from butlers.core.goal_labels import Label
from butlers.perception.model import instant

REALTIME_HORIZON = timedelta(minutes=10)
LATE_AT_GRACE = timedelta(seconds=60)
# A transition event stamped further ahead of ``now`` than this is not evidence yet.
FUTURE_EVENT_SKEW = timedelta(seconds=60)


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
    return Tri.FALSE  # transition and the M7 leaves are occurrences; a snapshot cannot witness one


def initial_leaves(predicate: dict, snap: Snapshot) -> dict[str, Tri]:
    return {leaf.path: initial_leaf(leaf, snap) for leaf in leaves(predicate)}


EDGE_TAGS = frozenset({"transition"}) | M7_TAGS


def _compare(op: str, read: object, want: object) -> bool | None:
    """One typed comparison; None when the read value is not of the declared type."""
    if type(want) is bool or type(read) is bool:
        if type(want) is not type(read) or op not in ("eq", "ne"):
            return None
        return (read == want) is (op == "eq")
    if type(want) is str:
        if type(read) is not str or op not in ("eq", "ne"):
            return None
        return (read == want) is (op == "eq")
    if type(read) not in (int, float) or (type(read) is float and not math.isfinite(read)):
        return None
    left, right = cast(float, read), cast(float, want)  # numeric: validated at registration
    return {
        "eq": left == right,
        "ne": left != right,
        "lt": left < right,
        "le": left <= right,
        "gt": left > right,
        "ge": left >= right,
    }[op]


def _m7_leaf(leaf: Leaf, cur: Tri, ev: ConditionEvent) -> Tri:
    """An M7 occurrence: a typed row naming the leaf's subject reached the leaf's state.

    Off-schema or unmeasurable evidence is unknown and never true; a different row is ignored.
    """
    body, data = leaf.body, ev.data
    tag = leaf.tag
    if tag == "expectation":
        keys = ("expectation_kind", "subject")
        hit, state = "deviation", body["deviation"]
    elif tag == "routine_deviation":
        keys, hit, state = ("routine_id",), "deviation", "missed"
    elif tag == "condition":
        keys, hit, state = ("source",), "state", body["state"]
    elif tag == "mode":
        keys, hit, state = ("mode",), "change", body["change"]
    elif tag == "read_check":
        keys, hit, state = ("capability", "field"), None, None
    else:  # bundle
        keys, hit, state = ("bundle_id",), None, None
    if any(data.get(key) != body[key] for key in keys):
        return cur
    if not ev.measurable:
        return Tri.UNKNOWN
    if tag == "read_check":
        verdict = _compare(body["op"], data.get("value"), body["value"])
        if verdict is None:
            return Tri.UNKNOWN
        return Tri.TRUE if verdict else (Tri.FALSE if cur is Tri.UNKNOWN else cur)
    if hit is None or data.get(hit) == state:
        return Tri.TRUE
    return Tri.FALSE if cur is Tri.UNKNOWN else cur


def _event_leaf(leaf: Leaf, cur: Tri, ev: ConditionEvent, now: datetime) -> Tri:
    body, data = leaf.body, ev.data
    if cur is Tri.TRUE or ev.tag != leaf.tag or ev.source != leaf.source:
        return cur
    if leaf.tag in EDGE_TAGS and (
        ev.late or ev.retracted or ev.record or now - ev.at > REALTIME_HORIZON
    ):
        return cur  # historical evidence is recorded elsewhere, never a wake
    if leaf.tag in M7_TAGS:
        return _m7_leaf(leaf, cur, ev)
    if leaf.tag == "transition":
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
    trigger: UUID | None = None  # the fire-event UUID when this advance fired
    # A fire's evidence: the labels of this batch's events holding its true data leaves, or
    # None when any true data leaf is held by no labelled event of the batch (missing
    # provenance). A clock fire with no true data leaf has the empty evidence ``()``.
    evidence: tuple[Label, ...] | None = None


def _evidence(
    leaf_list: list[Leaf], values: dict[str, Tri], eligible: list[ConditionEvent], now: datetime
) -> tuple[Label, ...] | None:
    """The labels of the events of this batch that hold each true data leaf.

    Every true leaf other than a clock leaf must be held by at least one event of the batch,
    and every event holding one must be labelled; otherwise the provenance is missing (None).
    An event holds a leaf when it alone would make that leaf true.
    """
    labels: list[Label] = []
    for leaf in leaf_list:
        if values[leaf.path] is not Tri.TRUE or leaf.tag == "at":
            continue
        holding = [ev for ev in eligible if _event_leaf(leaf, Tri.FALSE, ev, now) is Tri.TRUE]
        if not holding or any(ev.label is None for ev in holding):
            return None
        labels.extend(ev.label for ev in holding if ev.label is not None)
    return tuple(labels)


def advance(
    inst: Instance,
    events: Iterable[ConditionEvent],
    now: datetime,
    *,
    unmeasurable: frozenset[str] = frozenset(),
) -> Advance:
    """Apply time then events to an open instance; closed instances never change.

    Expiry wins over every fire. Events at or below the instance's durable per-source cursor
    are an exclusive replay bound and never touch a leaf.
    """
    if inst.state is not State.OPEN:
        return Advance(inst, False, f"closed:{inst.state}")
    if now >= inst.expires_at:
        expired = replace(inst, state=State.EXPIRED, version=inst.version + 1)
        return Advance(expired, True, "expired")
    leaf_list = list(leaves(inst.predicate))
    events = list(events)
    values = dict(inst.leaves)
    cursors = dict(inst.cursors)
    trigger: UUID | None = None
    evidence: tuple[Label, ...] | None = None
    reason = "pending"
    # Apply the clock first so an event completing the predicate is the recorded cause.
    for leaf in leaf_list:
        values[leaf.path] = _clock_leaf(leaf, values[leaf.path], now, unmeasurable)
    if combine(inst.predicate, values) is Tri.TRUE:
        trigger, reason = inst.synthetic_event_id("clock"), "fired:clock"
        if any(
            leaf.tag == "at" and now - instant(leaf.body["instant"]) > LATE_AT_GRACE
            for leaf in leaf_list
        ):
            reason = "fired:clock_late"
        # The clock completed it, but data leaves latched earlier hold it too (bu-dhloi1.305
        # N1): they need the same evidence an event fire does, so an earlier batch's taint is
        # never laundered by the clock. A clock fire on clock leaves alone joins nothing.
        if any(values[leaf.path] is Tri.TRUE and leaf.tag != "at" for leaf in leaf_list):
            eligible = [ev for ev in events if _eligible(inst, ev, now)]
            evidence = _evidence(leaf_list, values, eligible, now)
        else:
            evidence = ()
    else:
        eligible = [ev for ev in events if _eligible(inst, ev, now)]
        for ev in sorted(eligible, key=lambda e: (e.position, e.id)):
            cursors[ev.source] = max(cursors.get(ev.source, -1), ev.position)
            for leaf in leaf_list:
                values[leaf.path] = _event_leaf(leaf, values[leaf.path], ev, now)
            if combine(inst.predicate, values) is Tri.TRUE:
                trigger, reason = UUID(ev.id), "fired:event"
                evidence = _evidence(leaf_list, values, eligible, now)
                break
    new = replace(
        inst,
        leaves=values,
        cursors=cursors,
        result=combine(inst.predicate, values),
        state=State.FIRED if trigger is not None else inst.state,
    )
    changed = (new.leaves, new.cursors, new.result, new.state) != (
        inst.leaves,
        inst.cursors,
        inst.result,
        inst.state,
    )
    if changed:
        new = replace(new, version=inst.version + 1)
    return Advance(new if changed else inst, changed, reason, trigger, evidence)


def _eligible(inst: Instance, ev: ConditionEvent, now: datetime) -> bool:
    """Whether an event may touch a leaf: declared, before expiry, past the floor, not future."""
    floor = inst.floor.get(ev.source)
    if ev.source not in inst.source_boundary or ev.at >= inst.expires_at:
        return False  # undeclared source or post-expiry evidence never contributes
    if floor is not None and ev.position <= floor:
        return False  # replay of an interval an earlier generation/run already consumed
    # A transition not yet true is neither evidence nor consumed, so a redelivery counts.
    return not (ev.tag in EDGE_TAGS and ev.at - now > FUTURE_EVENT_SKEW)
