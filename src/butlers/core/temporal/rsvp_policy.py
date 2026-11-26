"""Owner-written RSVP policies and their deterministic evaluation (bu-5ii4gj.9, S1 and S2).

A policy is an ordered list of closed typed predicates, a verdict and a ``send_updates`` choice,
written only by the owner through the dashboard (``public.calendar_rsvp_policies``). This module
holds the one closed vocabulary (the dashboard route and the database definer both check it) and
the pure evaluator.

The evaluator is a pure function of verified facts about one invitation: no model, no provider
call, no clock, no title or description. A policy only ever *prepares* an answer; nothing here
sends one, and no standing rule applies ``calendar_respond`` (REQ-calendar-invitation-docket-003).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Literal

VERDICTS: Final = ("accepted", "declined", "tentative")
SEND_UPDATES: Final = ("all", "externalOnly", "none")

#: Organizer verdicts a predicate may name. A quarantined invitation (unanswered, untrusted
#: organizer) is never evaluated, so only the trusted ones are meaningful.
ORGANIZER_VERDICTS: Final = ("owner", "known_entity", "known_domain", "vouched")

#: Predicate kinds, by the shape of their value.
BOOLEAN_KINDS: Final = (
    "overlaps_focus_or_out_of_office",
    "conflicts_with_accepted",
    "outside_declared_hours",
    "recurring",
)
ENUM_KINDS: Final = {"organizer_verdict": ORGANIZER_VERDICTS}
ROLE_KIND: Final = "organizer_role"
NUMERIC_KINDS: Final = {"attendee_count": 10_000, "lead_time_hours": 8_760}
PREDICATE_KINDS: Final = (
    *ENUM_KINDS,
    ROLE_KIND,
    *BOOLEAN_KINDS,
    *NUMERIC_KINDS,
)

MAX_PREDICATES: Final = 8
MAX_ACTIVE_POLICIES: Final = 20
_ROLE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_OPS: Final = ("lte", "gte")


class PolicyError(ValueError):
    """A policy or predicate is outside the closed vocabulary."""


@dataclass(frozen=True, slots=True)
class Predicate:
    kind: str
    value: Any
    op: str | None = None

    def as_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"kind": self.kind, "value": self.value}
        if self.op is not None:
            out["op"] = self.op
        return out


def parse_predicate(raw: Any) -> Predicate:
    """One predicate from its stored or submitted JSON, refusing anything outside the vocabulary.

    Only ``kind``, ``value`` and (for the numeric kinds) ``op`` are accepted; an event title or
    description is not a kind, so a predicate over free text is refused here and again by the
    database definer.
    """
    if not isinstance(raw, Mapping):
        raise PolicyError("a predicate is an object")
    kind = raw.get("kind")
    if kind not in PREDICATE_KINDS:
        raise PolicyError(f"unknown predicate kind {kind!r}")
    value = raw.get("value")
    op = raw.get("op")
    allowed = {"kind", "value"} | ({"op"} if kind in NUMERIC_KINDS else set())
    if set(raw) - allowed:
        raise PolicyError(f"predicate {kind} carries unexpected keys")
    if kind in BOOLEAN_KINDS:
        if not isinstance(value, bool):
            raise PolicyError(f"{kind} takes true or false")
    elif kind in ENUM_KINDS:
        if value not in ENUM_KINDS[kind]:
            raise PolicyError(f"{kind} takes one of {', '.join(ENUM_KINDS[kind])}")
    elif kind == ROLE_KIND:
        if not isinstance(value, str) or not _ROLE_RE.match(value):
            raise PolicyError("organizer_role takes a lowercase role name")
    else:
        ceiling = NUMERIC_KINDS[kind]
        if op not in _OPS:
            raise PolicyError(f"{kind} needs op lte or gte")
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= ceiling:
            raise PolicyError(f"{kind} takes an integer from 0 to {ceiling}")
    return Predicate(kind=kind, value=value, op=op if kind in NUMERIC_KINDS else None)


def parse_predicates(raw: Any) -> tuple[Predicate, ...]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise PolicyError("predicates is a list")
    if not 1 <= len(raw) <= MAX_PREDICATES:
        raise PolicyError(f"a policy has 1 to {MAX_PREDICATES} predicates")
    parsed = tuple(parse_predicate(item) for item in raw)
    kinds = [p.kind for p in parsed]
    if len(set(kinds)) != len(kinds):
        raise PolicyError("a predicate kind appears once per policy")
    return parsed


@dataclass(frozen=True, slots=True)
class Policy:
    id: str
    position: int
    predicates: tuple[Predicate, ...]
    verdict: Literal["accepted", "declined", "tentative"]
    send_updates: Literal["all", "externalOnly", "none"]
    version: int
    revoked_at: Any = None

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> Policy:
        verdict = row["verdict"]
        send_updates = row["send_updates"]
        if verdict not in VERDICTS or send_updates not in SEND_UPDATES:
            raise PolicyError("verdict or send_updates is outside the closed set")
        return cls(
            id=str(row["id"]),
            position=int(row["position"]),
            predicates=parse_predicates(row["predicates"]),
            verdict=verdict,
            send_updates=send_updates,
            version=int(row["version"]),
            revoked_at=row.get("revoked_at"),
        )


@dataclass(frozen=True, slots=True)
class InvitationFacts:
    """Verified facts about one pending invitation. Never a title, description or attendee text.

    A ``None`` fact is unknown, and a predicate over an unknown fact does not hold: a policy never
    matches on something that could not be checked.
    """

    organizer_verdict: str | None = None
    organizer_roles: frozenset[str] = frozenset()
    overlaps_focus_or_out_of_office: bool | None = None
    conflicts_with_accepted: bool | None = None
    outside_declared_hours: bool | None = None
    recurring: bool | None = None
    attendee_count: int | None = None
    lead_time_hours: int | None = None


def _holds(predicate: Predicate, facts: InvitationFacts) -> tuple[bool, Any]:
    kind = predicate.kind
    if kind == ROLE_KIND:
        return predicate.value in facts.organizer_roles, predicate.value
    fact = getattr(facts, kind)
    if fact is None:
        return False, None
    if kind in NUMERIC_KINDS:
        ok = fact <= predicate.value if predicate.op == "lte" else fact >= predicate.value
        return ok, fact
    return fact == predicate.value, fact


@dataclass(frozen=True, slots=True)
class MatchedFact:
    kind: str
    expected: Any
    observed: Any
    op: str | None = None


@dataclass(frozen=True, slots=True)
class PolicyMatch:
    policy_id: str
    version: int
    position: int
    verdict: str
    send_updates: str
    matched: tuple[MatchedFact, ...]


@dataclass(frozen=True, slots=True)
class Evaluation:
    """``prepared`` is the chosen match; ``disagreeing`` names every match when they disagree."""

    status: Literal["prepared", "no_match", "disagree"]
    prepared: PolicyMatch | None = None
    matches: tuple[PolicyMatch, ...] = field(default_factory=tuple)

    @property
    def disagreeing(self) -> tuple[str, ...]:
        return tuple(m.policy_id for m in self.matches) if self.status == "disagree" else ()


def match_policy(policy: Policy, facts: InvitationFacts) -> PolicyMatch | None:
    """The policy's match against the facts, or ``None`` when any predicate does not hold."""
    if policy.revoked_at is not None:
        return None
    matched: list[MatchedFact] = []
    for predicate in policy.predicates:
        ok, observed = _holds(predicate, facts)
        if not ok:
            return None
        matched.append(MatchedFact(predicate.kind, predicate.value, observed, predicate.op))
    return PolicyMatch(
        policy_id=policy.id,
        version=policy.version,
        position=policy.position,
        verdict=policy.verdict,
        send_updates=policy.send_updates,
        matched=tuple(matched),
    )


def evaluate_policies(policies: Iterable[Policy], facts: InvitationFacts) -> Evaluation:
    """Every non-revoked policy in position order against one invitation.

    Matches that agree on verdict and ``send_updates`` prepare the first by position; matches that
    disagree prepare nothing and are all named (REQ-calendar-invitation-docket-002).
    """
    ordered = sorted(policies, key=lambda p: (p.position, p.id))
    matches = tuple(m for p in ordered if (m := match_policy(p, facts)) is not None)
    if not matches:
        return Evaluation("no_match")
    if len({(m.verdict, m.send_updates) for m in matches}) > 1:
        return Evaluation("disagree", None, matches)
    return Evaluation("prepared", matches[0], matches)
