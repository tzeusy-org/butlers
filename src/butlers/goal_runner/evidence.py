"""Evidence-based completion (RFC 0038 D11, D13; bu-4c7nvp.1.3 PR-C). Dark.

A goal's plan carries requirements: what must be true, by receipt, for the goal to count as
completed. Each names one thing of the goal and the evidence that proves it:

- ``effect:<id>``: ``committed`` (the effect committed) or ``owner_delivered`` (it committed as a
  delivery to the live owner);
- ``child:<id>``: ``returned`` (the child returned its result);
- ``inbox:<id>``: ``consumed`` (the lead read the owner's reply);
- ``reply``: ``owner_delivered`` (an owner-origin goal answered the owner, D11).

A requirement is anchored ``owner`` or ``lead``. Owner anchors come only from server facts, never
from model text (``owner_anchors`` in the runner ledger): the reply an owner-origin goal owes,
each effect the owner approved, each owner reply that reached the goal. A later plan version can
drop or change a lead requirement, never an owner one: ``append_plan`` refuses, and the sw_075
trigger refuses again. The lead names lead requirements only; it can never claim an owner anchor.

``judge`` is the pure predicate the settle resolution applies once nothing can still change: a
lead that claimed ``completed`` gets ``completed`` only when every requirement, and every effect
it said it relied on, has its evidence; otherwise ``partial`` with the typed missing list. It
never upgrades a ``partial`` or ``failed`` claim. Nothing here does I/O.

Dark behind ``EVIDENCE_COMPLETION_LIVE``: until it flips, no requirement is recorded and the
settle resolution is unchanged for every goal.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

EVIDENCE_COMPLETION_LIVE = False

MAX_REQUIREMENTS = 64  # the sw_075 column bound
ANCHORS = ("owner", "lead")
# Which evidence each reference kind can carry; the first is what a lead requirement asks for.
EVIDENCE: Mapping[str, tuple[str, ...]] = {
    "effect": ("committed", "owner_delivered"),
    "child": ("returned",),
    "inbox": ("consumed",),
    "reply": ("owner_delivered",),
}
REPLY = "reply"


class RequirementError(ValueError):
    """A requirement that is not one of the closed shapes above."""


@dataclass(frozen=True, order=True, slots=True)
class Requirement:
    ref: str
    evidence: str
    anchor: str

    def __post_init__(self) -> None:
        kind, _, ident = self.ref.partition(":")
        if kind not in EVIDENCE or self.evidence not in EVIDENCE[kind]:
            raise RequirementError("unknown requirement")
        if self.anchor not in ANCHORS:
            raise RequirementError("unknown anchor")
        if kind == REPLY:
            if self.ref != REPLY:
                raise RequirementError("the reply requirement names nothing")
            return
        try:
            parsed = UUID(ident)
        except ValueError:
            raise RequirementError("a requirement names one row by id") from None
        if str(parsed) != ident:
            raise RequirementError("a requirement names one row by id")

    @property
    def kind(self) -> str:
        return self.ref.partition(":")[0]

    @property
    def ident(self) -> str:
        return self.ref.partition(":")[2]

    def to_json(self) -> dict[str, str]:
        return {"ref": self.ref, "evidence": self.evidence, "anchor": self.anchor}

    @classmethod
    def from_json(cls, value: Any) -> Requirement:
        if type(value) is not dict or set(value) != {"ref", "evidence", "anchor"}:
            raise RequirementError("a requirement is {ref, evidence, anchor}")
        if not all(type(v) is str for v in value.values()):
            raise RequirementError("a requirement is {ref, evidence, anchor}")
        return cls(value["ref"], value["evidence"], value["anchor"])


def lead_requirement(ref: str) -> Requirement:
    """The lead's requirement on ``ref``: its kind's own evidence, anchored ``lead``."""
    kind = ref.partition(":")[0] if type(ref) is str else ""
    if kind not in EVIDENCE or kind == REPLY:
        raise RequirementError("a lead requires one of its effects, children or inbox rows")
    return Requirement(ref, EVIDENCE[kind][0], "lead")


def merge(*groups: Iterable[Requirement]) -> tuple[Requirement, ...]:
    """One canonical, sorted list. The same proof required by owner and lead is owner-anchored."""
    owned: dict[tuple[str, str], str] = {}
    for group in groups:
        for req in group:
            key = (req.ref, req.evidence)
            if owned.get(key) != "owner":
                owned[key] = req.anchor
    merged = tuple(sorted(Requirement(ref, ev, anchor) for (ref, ev), anchor in owned.items()))
    if len(merged) > MAX_REQUIREMENTS:
        raise RequirementError("too many requirements")
    return merged


def to_json(requirements: Sequence[Requirement]) -> list[dict[str, str]]:
    return [req.to_json() for req in requirements]


def from_json(value: Any) -> tuple[Requirement, ...]:
    if type(value) is not list:
        raise RequirementError("requirements are a list")
    return tuple(Requirement.from_json(item) for item in value)


@dataclass(frozen=True, slots=True)
class Receipts:
    """What the ledger records, read under the goal lock; ids are canonical strings."""

    effects: Mapping[str, str]  # effect id -> status
    owner_delivered: frozenset[str]  # effect ids recorded as deliveries to the live owner
    children: Mapping[str, str]  # child invocation id -> status
    consumed: frozenset[str]  # inbox ids the lead consumed
    reply_delivered: bool  # the outcome's reply is a committed owner delivery


@dataclass(frozen=True, slots=True)
class Missing:
    requirement: Requirement
    found: str | None  # the recorded status, or None when nothing was recorded

    def to_json(self) -> dict[str, Any]:
        return {**self.requirement.to_json(), "found": self.found}


@dataclass(frozen=True, slots=True)
class Verdict:
    final: str
    satisfied: tuple[Requirement, ...]
    missing: tuple[Missing, ...]


def _found(req: Requirement, receipts: Receipts) -> tuple[bool, str | None]:
    if req.kind == REPLY:
        return receipts.reply_delivered, "delivered" if receipts.reply_delivered else None
    if req.kind == "effect":
        status = receipts.effects.get(req.ident)
        proved = status == "committed" and (
            req.evidence == "committed" or req.ident in receipts.owner_delivered
        )
        return proved, status
    if req.kind == "child":
        status = receipts.children.get(req.ident)
        return status == "returned", status
    consumed = req.ident in receipts.consumed
    return consumed, "consumed" if consumed else None


def judge(
    outcome: str,
    requirements: Iterable[Requirement],
    relied: Iterable[str],
    receipts: Receipts,
) -> Verdict:
    """The final status a settled goal gets from its receipts (pure)."""
    required = merge(
        requirements, (Requirement(f"effect:{e}", "committed", "lead") for e in relied)
    )
    satisfied: list[Requirement] = []
    missing: list[Missing] = []
    for req in required:
        proved, found = _found(req, receipts)
        if proved:
            satisfied.append(req)
        else:
            missing.append(Missing(req, found))
    final = outcome
    if outcome == "completed" and missing:
        final = "partial"
    return Verdict(final, tuple(satisfied), tuple(missing))
