"""The server-held lineage facts of a parked goal effect (RFC 0038 D19).

One builder feeds the Telegram card and the dashboard so the owner sees identical facts on both
surfaces. Every value comes from the park record the capability broker wrote (``goal_*`` columns
on ``pending_actions``): never from the model's dossier claims or from the call's arguments. The
text is a closed vocabulary (no free text), so nothing here can carry an injected instruction.

``rule_possible`` is an explanation of whether a standing rule could ever match this call. It is
never authority: approving or auto-approving is decided elsewhere, per rule.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

_ORIGIN = {
    "owner": "You started it",
    "obligation": "A standing obligation started it",
    "third_party": "Something a third party sent started it",
    "staffer": "A staffer started it",
    "legacy": "Before goal tracking",
}

# Why the lineage was first exposed (the closed S1 ``exposure_cause``).
_EXPOSED_BY = {
    "ingress": "Outside content that started the goal",
    "read": "Outside content it read",
    "child": "Output of a sub-task that had read outside content",
    "inbox": "A reply that carried outside content",
    "recovery": "A replaced lead that had already read outside content",
    "legacy_unknown": "Unknown (recorded before exposure causes)",
}


@dataclass(frozen=True, slots=True)
class LineageFact:
    label: str
    value: str


@dataclass(frozen=True, slots=True)
class LineageFacts:
    goal_id: str
    invocation_id: str
    depth: int
    origin: str
    exposed: bool
    exposure_cause: str | None
    rule_possible: bool | None
    facts: tuple[LineageFact, ...]


def _value(source: Mapping[str, Any], flat: str, nested: str) -> Any:
    goal = source.get("goal")
    if flat in source:
        return source[flat]
    return goal.get(nested) if isinstance(goal, Mapping) else None


def lineage_facts(source: Mapping[str, Any] | Any) -> LineageFacts | None:
    """The facts of a goal-parked action, or ``None`` for any other action.

    ``source`` is a ``pending_actions`` row or ``PendingAction.to_dict()`` (flat ``goal_*`` keys
    or a nested ``goal`` mapping). A row without a goal and invocation id is not goal lineage.
    """
    if not isinstance(source, Mapping):
        source = {k: getattr(source, k, None) for k in _FIELDS}
    goal_id = _value(source, "goal_id", "goal_id")
    invocation_id = _value(source, "goal_invocation_id", "invocation_id")
    origin = _value(source, "goal_origin", "origin")
    exposed = _value(source, "goal_exposed", "exposed")
    if goal_id is None or invocation_id is None or origin not in _ORIGIN or exposed is None:
        return None
    depth = _value(source, "goal_depth", "depth")
    depth = depth if depth in (0, 1) else 0
    cause = _value(source, "goal_exposure_cause", "exposure_cause")
    cause = cause if cause in _EXPOSED_BY else None
    possible = _value(source, "goal_rule_possible", "rule_possible")
    possible = possible if isinstance(possible, bool) else None
    exposed = bool(exposed)

    if not exposed:
        exposed_by = "Not exposed to outside content"
    elif cause is None:
        exposed_by = "Unknown"
    else:
        exposed_by = _EXPOSED_BY[cause]
    if possible is None:
        rule = "Unknown"
    elif possible:
        rule = "A standing rule could match this call"
    else:
        rule = "No standing rule can match it; each one needs your approval"
    return LineageFacts(
        goal_id=str(goal_id),
        invocation_id=str(invocation_id),
        depth=depth,
        origin=origin,
        exposed=exposed,
        exposure_cause=cause,
        rule_possible=possible,
        facts=(
            LineageFact("Goal", f"{str(goal_id)[:8]} ({'sub-task' if depth else 'lead'})"),
            LineageFact("Origin", _ORIGIN[origin]),
            LineageFact("Exposed by", exposed_by),
            LineageFact("Standing rule", rule),
        ),
    )


_FIELDS = (
    "goal_id",
    "goal_invocation_id",
    "goal_depth",
    "goal_exposed",
    "goal_origin",
    "goal_exposure_cause",
    "goal_rule_possible",
)

__all__ = ["LineageFact", "LineageFacts", "lineage_facts"]
