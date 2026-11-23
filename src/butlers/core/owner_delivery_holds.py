"""Owner mode holds and owner law at the owner-delivery boundary (bu-dhloi1.22.1).

RFC 0038 S3.1 and S1. A hold can only **hold** a subject's push; it never raises urgency, never
opens a subject and never changes the urgency written when the subject opened (``derive_urgency``,
bu-dhloi1.22). Two inputs are read:

* the owner's active modes: the ``owner.mode`` projection (#5783) read as the owner reader, and
  each active mode's profile from ``switchboard.owner_mode_overlay`` (#5776), whose ``holds``
  are judged by ``modes.held_by``. A mode holds a subject when its profile holds the
  ``proactive`` class. A subject whose verified urgency is an interrupt is never held: its
  cause (the owner's own flag or clause, or a home-safety producer) is already a break-through,
  and safety is pinned. An approval subject has no sender, bill or medication kind, so
  ``proactive`` is the only class it can carry.
* the owner-law hold the broker recorded for the parked action
  (``switchboard.owner_policy_holds``, keyed by the effect, which is the action's id). It is
  attached to the subject for display only: an ``always_ask`` term is what parked the action,
  so the card that asks for approval is the thing it asks for and is not held.

An unknown reading holds nothing: an unreadable, stale, missing or off-shape ``owner.mode``, an
unreadable overlay or a role that cannot be assumed all mean no mode hold, exactly as an unknown
posture never manufactures urgency. Overlay readers take no row lock: the overlay enables row
security with no runtime policy until activation, so a locking read could see zero rows and
silently fall back to the default profile.

Dark: ``flags.OWNER_DELIVERY_MODE_HOLDS_LIVE`` is off, and the whole path is behind the
owner-delivery rollout flag. With mode controls dark there is no active mode to read.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final
from uuid import UUID

from butlers.core import owner_delivery as od
from butlers.core import owner_policy
from butlers.perception import flags
from butlers.perception.mode_overlay import read_profile
from butlers.perception.model import MODES
from butlers.perception.modes import held_by
from butlers.perception.situation import READER_ROLE, Audience, SituationRead, situation_read

#: The one obligation class an owner-delivery subject can carry (see the module docstring).
SUBJECT_CLASS: Final = "proactive"
MODE_STAGE: Final = "owner_mode"
_OWNER_MODE: Final = "owner.mode"
_READER: Final = READER_ROLE[Audience.OWNER]


@dataclass(frozen=True, slots=True)
class ModeHold:
    """The active modes that hold a subject, as the receipt the subject records."""

    generation: int
    modes: tuple[str, ...]

    def receipt(self) -> dict[str, Any]:
        return {
            "stage": MODE_STAGE,
            "generation": self.generation,
            "terms": [{"mode": mode, "obligation_class": SUBJECT_CLASS} for mode in self.modes],
        }


def obligation_class(urgency: od.Urgency) -> str | None:
    """The class a mode's holds are judged against, or ``None`` for a subject never held."""
    return None if urgency.cls == "interrupt" else SUBJECT_CLASS


def _subject_urgency(subject: od.Subject) -> od.Urgency:
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


async def _holds_proactive(conn: Any, mode: str) -> bool:
    """Does the mode's overlay (or recorded default) hold the subject class? Unknown: no."""
    try:
        async with conn.transaction():
            profile, _version = await read_profile(conn, mode)
        return held_by(profile, SUBJECT_CLASS)
    except Exception:
        return False


async def mode_hold(conn: Any, subject: od.Subject) -> ModeHold | None:
    """The mode hold on this subject's push now, or ``None``. Reads only; takes no lock."""
    if not flags.OWNER_DELIVERY_MODE_HOLDS_LIVE:
        return None
    if obligation_class(_subject_urgency(subject)) is None:
        return None
    read = await _read_modes(conn, subject.owner_entity)
    if read is None:
        return None
    active, generation = read
    holding = tuple([m for m in sorted(active) if await _holds_proactive(conn, m)])
    return ModeHold(generation, holding) if holding else None


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
    conn: Any, subject: od.Subject, *, action_id: UUID, defers_push: bool
) -> ModeHold | None:
    """Record the hold on an open subject and return the mode hold that defers its push.

    ``defers_push`` is true only for the push surface; the dashboard is read on demand, so a
    mode only annotates it. A mode hold, which defers the push, is the receipt recorded when
    there is one; otherwise the owner-law receipt is. Never writes urgency.
    """
    hold = await mode_hold(conn, subject) if defers_push else None
    receipt = hold.receipt() if hold is not None else await owner_law_receipt(conn, action_id)
    if receipt is not None:
        try:
            await od.record_holding_reason(conn, subject.id, receipt)
        except od.OwnerDeliveryRefused:
            pass
    return hold


__all__ = [
    "MODE_STAGE",
    "ModeHold",
    "SUBJECT_CLASS",
    "consume",
    "mode_hold",
    "obligation_class",
    "owner_law_receipt",
]
