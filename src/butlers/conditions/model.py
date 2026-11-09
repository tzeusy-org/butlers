"""Immutable values crossing the conditions evaluator seams (RFC 0038 S4)."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import NAMESPACE_URL, UUID, uuid5

from butlers.perception.model import canonical


class Tri(StrEnum):
    """Three-valued result. ``unknown`` is never collapsed into ``false``."""

    TRUE = "true"
    FALSE = "false"
    UNKNOWN = "unknown"


class State(StrEnum):
    OPEN = "open"
    FIRED = "fired"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class Grant:
    """What a goal may subscribe to. Server-derived by the caller, never model-supplied."""

    goal_id: str
    sources: frozenset[str]
    decisions: frozenset[str] = frozenset()
    effects: frozenset[str] = frozenset()
    bindings: frozenset[str] = frozenset()
    subjects: frozenset[str | None] = frozenset()

    @property
    def digest(self) -> str:
        body = {
            "goal_id": self.goal_id,
            "sources": sorted(self.sources),
            "decisions": sorted(self.decisions),
            "effects": sorted(self.effects),
            "bindings": sorted(self.bindings),
            "subjects": sorted(s or "" for s in self.subjects),
            "null_subject": None in self.subjects,
        }
        return hashlib.sha256(canonical(body).encode()).hexdigest()


@dataclass(frozen=True)
class ConditionEvent:
    """A trusted source event. Producers (perception, goal ledger) own authenticity."""

    id: str
    source: str
    position: int
    at: datetime
    tag: str  # decision | owner_input | effect_settled | transition
    data: Mapping[str, object]
    measurable: bool = True
    late: bool = False
    retracted: bool = False
    record: bool = False  # a retraction record itself, never a transition

    def __post_init__(self) -> None:
        if str(UUID(self.id)) != self.id:
            raise ValueError("condition event id must be a canonical UUID")


@dataclass(frozen=True)
class ClockReading:
    zone: str
    basis: str  # owner | home | utc_fallback
    source: str
    observed_at: datetime


@dataclass(frozen=True)
class Snapshot:
    """Current consistent state used at registration to avoid a lost wake."""

    now: datetime
    decisions: Mapping[str, str] = field(default_factory=dict)
    inputs: frozenset[str] = frozenset()
    effects: Mapping[str, str] = field(default_factory=dict)
    unmeasurable: frozenset[str] = frozenset()  # source names
    generation: int = 0  # source snapshot generation recorded in the receipt


@dataclass(frozen=True)
class Instance:
    id: str
    predicate: dict
    predicate_digest: str
    goal_id: str
    grant_digest: str
    source_boundary: tuple[str, ...]
    starts_at: datetime
    expires_at: datetime
    generation: int
    state: State = State.OPEN
    result: Tri = Tri.FALSE
    leaves: Mapping[str, Tri] = field(default_factory=dict)
    cursors: Mapping[str, int] = field(
        default_factory=dict
    )  # advancing high-water, receipts/resume
    floor: Mapping[str, int] = field(default_factory=dict)  # immutable exclusive replay floor
    version: int = 0

    @property
    def fire_key(self) -> UUID:
        """One fire intent per instance generation, whatever event wins the race."""
        return uuid5(NAMESPACE_URL, f"butlers:condition-fire:{self.id}:{self.generation}")

    def synthetic_event_id(self, kind: str) -> UUID:
        """Stable fire-event UUID for fires no source event caused (registration, clock)."""
        return uuid5(
            NAMESPACE_URL, f"butlers:condition-fire-event:{self.id}:{self.generation}:{kind}"
        )


@dataclass(frozen=True)
class FireIntent:
    fire_key: UUID
    instance_id: str
    generation: int
    event_id: UUID  # the causing source event, or a synthetic fire-event UUID
    reason: str
    fired_at: datetime
    snapshot_generation: int
    cursors: Mapping[str, int]
    claimed_by: str | None = None

    @property
    def occurrence(self) -> str:
        """RFC 0038 condition_fire occurrence: ``gen:<generation>:event:<fire-event UUID>``."""
        return f"gen:{self.generation}:event:{self.event_id}"


@dataclass(frozen=True)
class Receipt:
    instance_id: str
    generation: int
    kind: str  # register | evaluate | expire | cancel
    result: Tri
    state: State
    reason: str
    trigger: str | None
    cursors: Mapping[str, int]
    at: datetime
    snapshot_generation: int = 0
    clock: ClockReading | None = None
