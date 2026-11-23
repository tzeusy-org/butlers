"""Owner mode holds and owner law at the owner-delivery boundary (bu-dhloi1.22.1).

RFC 0038 S3.1, D7 and S1; owner decision bu-u33sb7 Q8=B. A hold can only **hold** a subject's
push; it never raises urgency, never opens a subject and never changes the urgency written when
the subject opened (``derive_urgency``, bu-dhloi1.22). Two inputs are read:

* the owner's active modes: the ``owner.mode`` projection (#5783) read as the owner reader, and
  each active mode's profile from ``switchboard.owner_mode_overlay`` (#5776), whose ``holds``
  are judged by ``modes.held_by``. A mode holds a subject when its profile holds the
  ``proactive`` class, unless the subject's urgency reason is in that mode's ``break_through``.
  Never mode-held: a ``home_safety`` interrupt (``modes.PINNED``), and any approval whose effect
  is not determinably proactive. The effect comes from the action's checked-in tool presentation
  descriptor (``effect_class``): ``read`` and ``record`` are proactive; ``transact`` (a bill or
  payment), ``communicate`` (a reply to a person), ``actuate``, ``administer`` and an unknown
  tool are delivered.
* the owner-law hold the broker recorded for the parked action
  (``switchboard.owner_policy_holds``, keyed by the effect, which is the action's id). It is
  attached to the subject for display only: an ``always_ask`` term is what parked the action,
  so the card that asks for approval is the thing it asks for and is not held.

An unreadable posture (an unreadable, stale, missing or off-shape ``owner.mode``, an unreadable
overlay, or a role that cannot be assumed) is always recorded as an unreadable ``owner_mode``
receipt, never as passed. Whether it holds or delivers is ``HOLD_WHEN_POSTURE_UNKNOWN``, which
awaits an owner decision; today it delivers. Overlay readers take no row lock: the overlay
enables row security with no runtime policy until activation, so a locking read could see zero
rows and silently fall back to the default profile.

Dark: ``flags.OWNER_DELIVERY_MODE_HOLDS_LIVE`` is off, which also keeps the owner-law receipt
unrecorded, and the whole path is behind the owner-delivery rollout flag.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final
from uuid import UUID

from butlers.core import owner_delivery as od
from butlers.core import owner_policy
from butlers.perception import flags
from butlers.perception.mode_overlay import read_profile
from butlers.perception.model import MODES
from butlers.perception.modes import ModeProfile, held_by
from butlers.perception.situation import READER_ROLE, Audience, SituationRead, situation_read

#: Effect classes (the tool presentation descriptor's ``effect_class``) that are determinably
#: proactive. Everything else, and an unknown tool, is delivered (Q8=B).
PROACTIVE_EFFECT_CLASSES: Final = frozenset({"read", "record"})
SUBJECT_CLASS: Final = "proactive"
#: Awaits an owner decision (unknown mode: hold or deliver). Deliver for now.
HOLD_WHEN_POSTURE_UNKNOWN: Final = False
MODE_STAGE: Final = "owner_mode"
_OWNER_MODE: Final = "owner.mode"
_READER: Final = READER_ROLE[Audience.OWNER]


@dataclass(frozen=True, slots=True)
class ModeHold:
    """What the owner's modes say of a subject: holding modes and/or an unreadable posture."""

    generation: int
    modes: tuple[str, ...]
    unreadable: bool = False

    @property
    def defers(self) -> bool:
        return bool(self.modes) or (self.unreadable and HOLD_WHEN_POSTURE_UNKNOWN)

    def receipt(self) -> dict[str, Any]:
        terms: list[dict[str, Any]] = [
            {"mode": mode, "obligation_class": SUBJECT_CLASS} for mode in self.modes
        ]
        if self.unreadable:
            terms.append({"unreadable": True})
        return {"stage": MODE_STAGE, "generation": self.generation, "terms": terms}


@dataclass(frozen=True, slots=True)
class Posture:
    """The owner's active modes with their profiles; ``readable`` is false when unknown."""

    readable: bool
    generation: int = 0
    profiles: Mapping[str, ModeProfile | None] = field(default_factory=dict)


UNREADABLE: Final = Posture(False)


def obligation_class(urgency: od.Urgency, effect_class: str | None) -> str | None:
    """The class a mode's holds are judged against, or ``None`` for a subject never mode-held."""
    if urgency.reason == "home_safety":
        return None
    return SUBJECT_CLASS if effect_class in PROACTIVE_EFFECT_CLASSES else None


def subject_urgency(subject: od.Subject) -> od.Urgency:
    try:
        return od.Urgency(subject.urgency_class, subject.urgency_reason)
    except od.OwnerDeliveryRefused:
        return od.ROUTINE


async def _read_modes(conn: Any, owner: UUID) -> tuple[frozenset[str], int] | None:
    """The active mode IDs and the projection generation, or ``None`` when unknown.

    Runs as the owner reader inside a savepoint, so a refused role or read rolls the role
    change back with it; on success the previous role is restored before the savepoint ends.
    """
    try:
        async with conn.transaction():
            previous = await conn.fetchval("SELECT current_user")
            session = await conn.fetchval("SELECT session_user")
            await conn.execute(f"SET LOCAL ROLE {_READER}")
            now = await conn.fetchval("SELECT clock_timestamp()")
            read = await situation_read(
                conn, audience=Audience.OWNER, kind=_OWNER_MODE, subject=owner, as_of=now
            )
            if previous == session:
                await conn.execute("RESET ROLE")
            else:
                await conn.execute(f'SET LOCAL ROLE "{previous}"')
    except Exception:
        return None
    return _active(read, now)


def _active(read: SituationRead | Any, now: Any) -> tuple[frozenset[str], int] | None:
    if not isinstance(read, SituationRead) or not isinstance(now, datetime):
        return None
    if read.state != "known" or read.fresh_until is None or read.fresh_until <= now:
        return None
    value = read.value
    if type(value) is not list or any(type(m) is not str or m not in MODES for m in value):
        return None
    generation = read.generation
    return frozenset(value), generation if type(generation) is int else 0


async def _profile(conn: Any, mode: str) -> ModeProfile | None:
    """A mode's overlay profile (or recorded default); ``None`` when unreadable. No lock."""
    try:
        async with conn.transaction():
            profile, _version = await read_profile(conn, mode)
        return profile
    except Exception:
        return None


async def read_posture(conn: Any, owner: UUID) -> Posture:
    """The owner's posture now. Reads only; takes no lock."""
    read = await _read_modes(conn, owner)
    if read is None:
        return UNREADABLE
    active, generation = read
    return Posture(True, generation, {m: await _profile(conn, m) for m in sorted(active)})


def hold_for(posture: Posture, urgency: od.Urgency, effect_class: str | None) -> ModeHold | None:
    """What the posture says about one subject; pure. ``None`` means nothing to record."""
    klass = obligation_class(urgency, effect_class)
    if klass is None:
        return None
    if not posture.readable:
        return ModeHold(0, (), True)
    holding: list[str] = []
    unreadable = False
    for mode, profile in posture.profiles.items():
        if profile is None:
            unreadable = True
            continue
        try:
            held = held_by(profile, klass)
        except ValueError:
            unreadable = True
            continue
        if held and urgency.reason not in profile.break_through:
            holding.append(mode)
    if holding or unreadable:
        return ModeHold(posture.generation, tuple(holding), unreadable)
    return None


async def mode_hold(conn: Any, subject: od.Subject, effect_class: str | None) -> ModeHold | None:
    """The mode verdict on this subject's push now, or ``None``. Reads only; takes no lock."""
    if not flags.OWNER_DELIVERY_MODE_HOLDS_LIVE:
        return None
    urgency = subject_urgency(subject)
    if obligation_class(urgency, effect_class) is None:
        return None
    posture = await read_posture(conn, subject.owner_entity)
    return hold_for(posture, urgency, effect_class)


async def digest_held(conn: Any, owner: UUID, effect_classes: Sequence[str | None]) -> bool:
    """Whether a digest card is deferred: every member is mode-held (and there is one).

    A digest carries a count, not the items, so any deliverable member sends it. Members are
    judged as routine subjects: the digest worker binds no per-action origin.
    """
    if not flags.OWNER_DELIVERY_MODE_HOLDS_LIVE or not effect_classes:
        return False
    if any(obligation_class(od.ROUTINE, e) is None for e in effect_classes):
        return False
    posture = await read_posture(conn, owner)
    verdicts = [hold_for(posture, od.ROUTINE, e) for e in effect_classes]
    return all(v is not None and v.defers for v in verdicts)


async def owner_law_receipt(conn: Any, action_id: UUID) -> dict[str, Any] | None:
    """The owner-law hold receipt the broker recorded for a parked action, if readable."""
    try:
        async with conn.transaction():
            holds = await owner_policy.holds_for(conn, "effect_id", action_id)
    except Exception:
        return None
    for hold in holds:
        receipt = hold.get("receipt")
        if isinstance(receipt, dict):
            return receipt
    return None


async def consume(
    conn: Any,
    subject: od.Subject,
    *,
    action_id: UUID,
    effect_class: str | None,
    defers_push: bool,
) -> ModeHold | None:
    """Record the hold on an open subject and return the mode hold that defers its push.

    ``defers_push`` is true only for the push surface; the dashboard is read on demand, so a
    mode only annotates it. The recorded receipt is the mode verdict when there is one
    (including an unreadable posture, which is never recorded as passed), else the owner-law
    receipt. Never writes urgency. Does nothing while dark.
    """
    if not flags.OWNER_DELIVERY_MODE_HOLDS_LIVE:
        return None
    verdict = await mode_hold(conn, subject, effect_class)
    receipt = verdict.receipt() if verdict is not None else await owner_law_receipt(conn, action_id)
    try:
        if receipt is not None:
            await od.record_holding_reason(conn, subject.id, receipt)
        else:
            # The posture reads clean (or this subject is never mode-held): a stale mode
            # receipt, such as an old unreadable one, no longer applies.
            await od.clear_holding_reason(conn, subject.id, MODE_STAGE)
    except od.OwnerDeliveryRefused:
        pass
    return verdict if verdict is not None and verdict.defers and defers_push else None


__all__ = [
    "HOLD_WHEN_POSTURE_UNKNOWN",
    "MODE_STAGE",
    "ModeHold",
    "PROACTIVE_EFFECT_CLASSES",
    "Posture",
    "SUBJECT_CLASS",
    "consume",
    "digest_held",
    "hold_for",
    "mode_hold",
    "obligation_class",
    "owner_law_receipt",
    "read_posture",
]
