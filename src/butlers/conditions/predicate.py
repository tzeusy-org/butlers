"""The first-switch predicate language (RFC 0038 S4): a closed, bounded tagged AST.

Validation mirrors ``perception_private.valid_predicate`` in ``scripts/init-db.sql`` so a
predicate accepted here is accepted by the dark ``perception_bind`` store and vice versa.
No text expression, code, SQL, or model evaluation exists; unknown tags, fields or values
refuse before any registration side effect.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from butlers.perception.model import KINDS, STATES, canonical, instant

MAX_CHILDREN = 16
MAX_LEAVES = 16
MAX_DEPTH = 2
MAX_BYTES = 8192

DECISION_OUTCOMES = frozenset({"approved", "rejected", "expired", "cancelled"})
EFFECT_STATUSES = frozenset({"succeeded", "failed", "cancelled", "unknown"})

SOURCE_DECISION = "goal.decision"
SOURCE_INBOX = "goal.inbox"
SOURCE_EFFECT = "goal.effect"
SOURCE_CLOCK = "clock"


class PredicateError(ValueError):
    """A predicate is outside the closed S4 language."""


@dataclass(frozen=True)
class Leaf:
    path: str
    tag: str
    body: dict

    @property
    def source(self) -> str:
        if self.tag == "decision":
            return SOURCE_DECISION
        if self.tag == "owner_input":
            return SOURCE_INBOX
        if self.tag == "effect_settled":
            return SOURCE_EFFECT
        if self.tag == "at":
            return SOURCE_CLOCK
        return f"perception:{self.body['kind']}"


def _uuid(value: object, what: str) -> str:
    if not isinstance(value, str):
        raise PredicateError(f"{what} must be a canonical UUID string")
    try:
        ok = str(UUID(value)) == value
    except ValueError:
        ok = False
    if not ok:
        raise PredicateError(f"{what} must be a canonical UUID string")
    return value


def _closed(node: dict, names: set[str]) -> None:
    if set(node) != names:
        raise PredicateError(f"{node.get('tag')} requires exactly fields {sorted(names)}")


def _validate(node: object, depth: int) -> int:
    """Validate one node; return its leaf count."""
    if depth > MAX_DEPTH:
        raise PredicateError("predicate nests deeper than two levels")
    if type(node) is not dict or type(node.get("tag")) is not str:
        raise PredicateError("predicate node must be an object with a string tag")
    tag = node["tag"]
    if tag in ("all", "any"):
        _closed(node, {"tag", "children"})
        children = node["children"]
        if type(children) is not list or not 1 <= len(children) <= MAX_CHILDREN:
            raise PredicateError("all/any need 1..16 children")
        return sum(_validate(child, depth + 1) for child in children)
    if tag == "decision":
        _closed(node, {"tag", "subject_id", "outcome"})
        _uuid(node["subject_id"], "decision.subject_id")
        if node["outcome"] not in DECISION_OUTCOMES:
            raise PredicateError("unknown decision outcome")
    elif tag == "owner_input":
        _closed(node, {"tag", "goal_id", "binding_ref"})
        _uuid(node["goal_id"], "owner_input.goal_id")
        _uuid(node["binding_ref"], "owner_input.binding_ref")
    elif tag == "effect_settled":
        _closed(node, {"tag", "effect_id", "status_set"})
        _uuid(node["effect_id"], "effect_settled.effect_id")
        statuses = node["status_set"]
        if (
            type(statuses) is not list
            or not 1 <= len(statuses) <= 4
            or len(set(map(str, statuses))) != len(statuses)
            or any(s not in EFFECT_STATUSES for s in statuses)
        ):
            raise PredicateError("effect_settled.status_set must be 1..4 known statuses")
    elif tag == "at":
        _closed(node, {"tag", "instant", "zone"})
        try:
            instant(node["instant"])
        except ValueError as exc:
            raise PredicateError(str(exc)) from None
        try:
            ZoneInfo(node["zone"])
        except (ZoneInfoNotFoundError, TypeError, ValueError):
            raise PredicateError("at.zone must be an IANA zone") from None
    elif tag == "transition":
        _closed(node, {"tag", "kind", "subject", "from", "to"})
        if node["kind"] not in KINDS:
            raise PredicateError("unknown transition kind")
        if node["from"] not in STATES or node["to"] not in STATES:
            raise PredicateError("unknown transition state")
        if node["subject"] is not None:
            _uuid(node["subject"], "transition.subject")
    else:
        raise PredicateError(f"unknown predicate tag {tag!r}")
    return 1


def validate(predicate: object) -> dict:
    """Return the canonical copy of a valid predicate or raise ``PredicateError``."""
    try:
        text = canonical(predicate)
    except (TypeError, ValueError) as exc:
        raise PredicateError(f"predicate is not canonical JSON: {exc}") from None
    if len(text.encode()) > MAX_BYTES:
        raise PredicateError("predicate exceeds 8KiB canonical JSON")
    if _validate(predicate, 0) > MAX_LEAVES:
        raise PredicateError("predicate has more than 16 leaves")
    return json.loads(text)


def digest(predicate: dict) -> str:
    return hashlib.sha256(canonical(predicate).encode()).hexdigest()


def leaves(predicate: dict, prefix: str = "") -> Iterator[Leaf]:
    if predicate["tag"] in ("all", "any"):
        for i, child in enumerate(predicate["children"]):
            yield from leaves(child, f"{prefix}{i}.")
        return
    yield Leaf(prefix.rstrip(".") or "0", predicate["tag"], predicate)


def sources(predicate: dict) -> frozenset[str]:
    return frozenset(leaf.source for leaf in leaves(predicate))
