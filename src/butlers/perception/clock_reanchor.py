"""Obligations follow the owner's clock: re-anchor owner-local schedules on a zone change.

Dark behind ``flags.OWNER_CLOCK_LIVE`` (bu-5ii4gj.5 P5). It reads the owner clock from
``perception.clock`` and never edits it.

Two parts:

* ``evidence_from_legs`` turns Travel legs into owner-clock evidence. A leg is an
  ``OwnerConfirm`` only when ``leg_is_confirmed`` says so (the one named stand-in for the owner's
  confirmation), and it acts at the leg's ``arrival_at``, not at booking time. Any other leg with a
  mapped place is a ``Booking``, which only asks once.
* ``plan_reanchor`` / ``run_clock_transition`` recompute ``next_run_at`` for owner-local cron rows
  when the clock's zone changes. "Owner-local" is the ``'UTC'`` follow-the-owner sentinel in
  ``scheduled_tasks.timezone`` until the anchor-kind migration (P6) replaces ``is_owner_local``.

The rules, each a test: an unknown clock moves nothing (obligations stay on the home zone); a
transition younger than ``RETRACTION_WINDOW`` waits, so a flip back is a no-op; a pinned zone or a
one-shot row is never touched; a firing already due fires once on the old anchor; the write is a
compare-and-set on the old ``next_run_at``, so a claim that wins the race is not duplicated.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from butlers.core.scheduler.timing import _next_run
from butlers.core.temporal.city_zones import normalise_place, zone_for_place
from butlers.core.temporal.projected_zone import TripLeg
from butlers.perception import flags
from butlers.perception.clock import (
    Booking,
    ClockEvidence,
    ClockState,
    ClockValue,
    OwnerConfirm,
    reduce_clock,
)

logger = logging.getLogger(__name__)

#: A transition this young may still be retracted, so nothing is re-anchored yet.
RETRACTION_WINDOW = timedelta(minutes=10)


# -- Travel legs to clock evidence -----------------------------------------------------


@dataclass(frozen=True, slots=True)
class BookedLeg:
    """A Travel leg with the stable reference an ask is keyed on (once per booking)."""

    leg: TripLeg
    ref: str


def evidence_from_legs(
    booked: Iterable[BookedLeg],
    *,
    now: datetime,
    home_zone: str | None = None,
    home_city: str | None = None,
) -> list[ClockEvidence]:
    """Clock evidence from legs, in arrival order.

    A confirmed leg yields evidence only once it has arrived (the clock moves at ``arrival_at``).
    An unconfirmed leg yields a ``Booking`` (an ask, once per ref), arrived or not. A leg into a
    place the table does not name, and a leg already refused by the confirmation rule for
    cancelled trips, yields nothing: a zone is never guessed.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    evidence: list[ClockEvidence] = []
    for item in sorted(booked, key=lambda b: b.leg.arrival_at):
        leg = item.leg
        if (
            home_zone
            and home_city
            and normalise_place(leg.arrival_city) == normalise_place(home_city)
        ):
            zone = home_zone
        else:
            zone = zone_for_place(leg.arrival_airport, leg.arrival_city)
        if zone is None:
            continue
        if leg.confirmed:
            if leg.arrival_at <= now:
                evidence.append(OwnerConfirm(zone, leg.arrival_at, item.ref))
        else:
            evidence.append(Booking(zone, leg.arrival_at, item.ref))
    return evidence


def clock_after_legs(
    state: ClockState,
    booked: Iterable[BookedLeg],
    *,
    now: datetime,
    home_city: str | None = None,
) -> ClockState:
    """Feed the legs' evidence through the owner-clock reducer, in order, and return its state."""
    for item in evidence_from_legs(booked, now=now, home_zone=state.home.zone, home_city=home_city):
        state = reduce_clock(state, item, now=now).state
    return state


# -- Re-anchoring ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Move:
    task_id: Any
    name: str
    old_next_run_at: datetime
    new_next_run_at: datetime


@dataclass(frozen=True, slots=True)
class ReanchorPlan:
    moves: tuple[Move, ...] = ()
    #: Rows left alone because they are not owner-local (pinned, one-shot, no cron).
    kept_pinned: int = 0
    #: Rows left to fire on their old anchor (already due) or already correct.
    skipped: int = 0
    #: Why nothing was planned at all: ``clock_unknown``, ``settling``, ``same_zone``.
    reason: str | None = None
    #: For ``settling``: when the transition leaves the retraction window. The caller re-runs then.
    retry_after: datetime | None = None


def is_owner_local(row: Mapping[str, Any]) -> bool:
    """Whether a schedule row follows the owner's clock (the ``'UTC'`` sentinel, until P6)."""
    if not row.get("cron"):
        return False
    if (row.get("task_type") or "cron") != "cron":
        return False
    return (row.get("timezone") or "").strip().upper() in {"", "UTC"}


def plan_reanchor(
    rows: Iterable[Mapping[str, Any]],
    *,
    old_zone: str,
    new_zone: str | None,
    transition_at: datetime,
    now: datetime,
    stagger_key: str | None = None,
) -> ReanchorPlan:
    """Which owner-local rows move to the new zone, and to when. Pure."""
    if now.tzinfo is None or transition_at.tzinfo is None:
        raise ValueError("instants must be timezone-aware")
    if new_zone is None:
        return ReanchorPlan(reason="clock_unknown")
    if new_zone == old_zone:
        return ReanchorPlan(reason="same_zone")
    if now - transition_at < RETRACTION_WINDOW:
        return ReanchorPlan(reason="settling", retry_after=transition_at + RETRACTION_WINDOW)
    moves: list[Move] = []
    pinned = skipped = 0
    for row in rows:
        if not is_owner_local(row):
            pinned += 1
            continue
        current = row["next_run_at"]
        if current is None or current <= now:
            skipped += 1  # due: it fires once on the old anchor and is not duplicated
            continue
        recomputed = _next_run(row["cron"], timezone=new_zone, stagger_key=stagger_key, now=now)
        if recomputed == current:
            skipped += 1
            continue
        moves.append(Move(row["id"], row["name"], current, recomputed))
    return ReanchorPlan(tuple(moves), pinned, skipped)


async def apply_reanchor(pool: Any, plan: ReanchorPlan) -> int:
    """Write the plan with a compare-and-set on the old ``next_run_at``; the count that landed.

    A dispatcher claim advances ``next_run_at`` first, so a row it won no longer matches and is
    left on the anchor it fired with.
    """
    landed = 0
    for move in plan.moves:
        status = await pool.execute(
            "UPDATE scheduled_tasks SET next_run_at = $3, updated_at = now() "
            "WHERE id = $1 AND next_run_at = $2 AND enabled = true "
            "AND delete_requested_at IS NULL",
            move.task_id,
            move.old_next_run_at,
            move.new_next_run_at,
        )
        if status.endswith(" 1"):
            landed += 1
    return landed


_ROWS_SQL = """
    SELECT id, name, cron, timezone, next_run_at, COALESCE(task_type, 'cron') AS task_type
    FROM scheduled_tasks
    WHERE enabled = true AND delete_requested_at IS NULL
"""


# -- one owner-zone source for the scheduler and the re-anchor -------------------------

#: Clock bases that are the owner's own word or a confirmed place, not the home fallback.
_CONFIRMED_BASES = frozenset({"owner", "owner_confirmed_booking", "phone"})


def target_zone(clock: ClockValue | None, settings_zone: str) -> str | None:
    """The zone owner-local schedules should read in: the one rule both halves share.

    ``None`` when the clock is unknown or stale. A confirmed basis gives the clock's zone; any other
    (home, ``utc_fallback``) gives the settings zone, so a return home re-anchors back through the
    same rule and a ``utc_fallback`` clock never moves a row that is already on the settings zone.
    """
    if clock is None:
        return None
    return clock.zone if clock.basis in _CONFIRMED_BASES else settings_zone


async def run_clock_transition(
    pools: Mapping[str, Any],
    *,
    old_clock: ClockValue,
    new_clock: ClockValue | None,
    settings_zone: str,
    transition_at: datetime,
    now: datetime,
    expected: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Re-anchor every butler schema's owner-local schedules after the clock moved.

    ``scheduled_tasks`` is per butler schema, so this walks every one in ``expected`` (default:
    the pools given). A schema with no pool or that cannot be read is listed in ``failed`` and
    makes the run ``complete: False``; the readable schemas are still re-anchored (the write is
    idempotent, so a retry finishes the rest) but the run is never reported as done. Counts are
    per schema. Dark: does nothing until ``OWNER_CLOCK_LIVE``. ``new_clock`` is ``None`` when the
    clock is unknown or stale, which moves nothing.
    """
    if not flags.OWNER_CLOCK_LIVE:
        return {"live": False}
    names = sorted(set(expected) if expected is not None else set(pools))
    per_schema: dict[str, dict[str, int]] = {}
    failed: list[str] = []
    reason: str | None = None
    retry_after: datetime | None = None
    new_zone = target_zone(new_clock, settings_zone)
    for butler in names:
        pool = pools.get(butler)
        if pool is None:
            failed.append(butler)
            continue
        try:
            rows = [dict(r) for r in await pool.fetch(_ROWS_SQL)]
            plan = plan_reanchor(
                rows,
                old_zone=old_clock.zone,
                new_zone=new_zone,
                transition_at=transition_at,
                now=now,
                stagger_key=butler,
            )
            reason = plan.reason
            retry_after = plan.retry_after
            moved = await apply_reanchor(pool, plan) if plan.reason is None else 0
            per_schema[butler] = {
                "moved": moved,
                "kept_pinned": plan.kept_pinned,
                "skipped": plan.skipped,
            }
        except Exception:
            logger.warning("clock re-anchor failed for %s", butler, exc_info=True)
            failed.append(butler)
    return {
        "live": True,
        "complete": not failed,
        "failed": failed,
        "reason": reason,
        #: Set when the transition is still settling: re-run this at or after it.
        "retry_after": retry_after,
        "per_schema": per_schema,
    }


def owner_zone_resolver(read_clock: Callable[[], Awaitable[ClockValue | None]]):
    """The scheduler's owner zone: ``target_zone`` of the live clock, else the settings zone.

    ``read_clock`` returns the current clock, or ``None`` when it is unknown or stale. While
    ``OWNER_CLOCK_LIVE`` is off the settings zone wins, so a sentinel row recomputed after a firing
    lands in the same zone the re-anchor moved it to.
    """

    async def resolve(settings_zone: str) -> str:
        if not flags.OWNER_CLOCK_LIVE:
            return settings_zone
        return target_zone(await read_clock(), settings_zone) or settings_zone

    return resolve
