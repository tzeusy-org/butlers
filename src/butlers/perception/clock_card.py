"""The one owner card per clock transition (bu-5ii4gj.5, owner-card slice).

Pure and dark: it composes the card text and its doors from a ``run_clock_transition`` result;
sending it through the owner delivery ledger is a separate, flag-gated step. Every line is a door to
its schedule row (``schema`` and ``name``). Schedule titles are the owner's own words and appear
only on the card, never in logs.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from butlers.perception.clock import ClockValue
from butlers.perception.clock_reanchor import ReanchorPlan

#: Lines per section before "and N more".
MAX_LINES = 5


@dataclass(frozen=True, slots=True)
class Door:
    schema: str
    name: str


@dataclass(frozen=True, slots=True)
class TransitionCard:
    text: str
    doors: tuple[Door, ...]


def _place(zone: str) -> str:
    """A short place name. ``Etc/GMT-5`` is POSIX-inverted (UTC+5), so it renders as the offset."""
    etc = re.fullmatch(r"Etc/GMT([+-])(\d{1,2})", zone)
    if etc:
        return f"UTC{'+' if etc[1] == '-' else '-'}{int(etc[2])}"
    if zone in {"Etc/GMT", "Etc/UTC", "UTC"}:
        return "UTC"
    return zone.rsplit("/", 1)[-1].replace("_", " ")


def _clock_time(instant: datetime, zone: str) -> str:
    """``19:00`` in ``zone`` (a place name, since modern tzdata has no abbreviation for most)."""
    return f"{instant.astimezone(ZoneInfo(zone)):%H:%M}"


def _kept_line(item: Any, here: str) -> str:
    """One kept item. A pinned zone that no longer resolves shows the title alone, never raises."""
    try:
        there = _clock_time(item.next_run_at, item.zone)
        local = _clock_time(item.next_run_at, here)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return item.title
    return f"{item.title} {there} {_place(item.zone)} ({local} here)"


def _capped(lines: list[str]) -> str:
    shown = lines[:MAX_LINES]
    if len(lines) > MAX_LINES:
        shown.append(f"and {len(lines) - MAX_LINES} more")
    return "; ".join(shown)


def compose_transition_card(
    result: Mapping[str, Any],
    *,
    old_clock: ClockValue,
    new_clock: ClockValue | None,
    needs_ok: Iterable[str] = (),
) -> TransitionCard | None:
    """The card for one transition, or ``None`` when there is nothing to tell the owner.

    An unknown clock says so and that schedules stay on the old zone. A run that was dark, or
    moved and kept nothing and needs no approval, composes no card. An incomplete run says which
    schemas could not be read, so a partial re-anchor is never presented as done.
    """
    if not result.get("live"):
        return None
    plans: Mapping[str, ReanchorPlan] = result.get("plans", {})
    failed = list(result.get("failed", []))
    if new_clock is None:
        return TransitionCard(
            f"Clock unknown: schedules stay on {_place(old_clock.zone)} time.", ()
        )
    moved = [(schema, move) for schema, plan in sorted(plans.items()) for move in plan.moves]
    kept = [(schema, item) for schema, plan in sorted(plans.items()) for item in plan.kept]
    approvals = [line for line in needs_ok]
    if not moved and not approvals and not failed:
        return None

    parts: list[str] = []
    if moved:
        count = len(moved)
        noun = "item" if count == 1 else "items"
        parts.append(f"Moved to {_place(new_clock.zone)} time: {count} {noun}.")
    if kept:
        lines = [_kept_line(item, new_clock.zone) for _, item in kept]
        parts.append(f"Kept: {_capped(lines)}.")
    if approvals:
        parts.append(f"Needs OK: {_capped(approvals)}.")
    if failed:
        parts.append(f"Not checked: {', '.join(sorted(failed))}; will retry.")
    doors = tuple(
        [Door(schema, move.name) for schema, move in moved]
        + [Door(schema, item.name) for schema, item in kept]
    )
    return TransitionCard(" ".join(parts), doors)
