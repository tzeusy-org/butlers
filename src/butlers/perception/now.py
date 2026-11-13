"""The owner's deterministic ``/now`` card (RFC 0038 S1, bu-dhloi1.21).

``/now`` shows the owner's current situation from typed owner reads, with no
model in the path. Each signal renders one of the four states:

* ``known``: a plain-text value, with its age.
* ``unknown``: "Unknown", or the last value marked stale when one is carried.
* ``unmeasurable`` and ``unconfigured``: said as such.

The card is "all current" only when every signal is known and fresh. A missing,
malformed or unrenderable read renders as unknown, never as all-clear, and a
known read past its freshness renders as stale.

Place is coarse by design: "At a known place" or "Elsewhere". No place or
locality reference, evidence or record reference, title or coordinate is copied
into the card. Naming the place waits for the permitted-grant rendering.

Dark: nothing serves this until ``flags.NOW_LIVE`` is turned on at the switch.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from butlers.perception.model import ACTIVITIES, MODES
from butlers.perception.situation import Audience, SituationRead, situation_read

#: The signals on the card, in display order, with their labels.
NOW_LABELS: dict[str, str] = {
    "owner.place": "Place",
    "owner.activity": "Activity",
    "owner.mode": "Mode",
    "owner.clock": "Clock",
    "owner.suppression": "Quiet",
}
NOW_KINDS = tuple(NOW_LABELS)

_CLOCK_BASIS = {
    "phone": "from phone",
    "owner": "set by you",
    "home": "home zone",
    "utc_fallback": "UTC fallback",
    "owner_confirmed_booking": "from a confirmed booking",
}
_STATE_TEXT = {
    "unknown": "Unknown",
    "unmeasurable": "Can't be measured",
    "unconfigured": "Not set up",
}
_UNREADABLE = "Unknown (unreadable)"


@dataclass(frozen=True, slots=True)
class NowLine:
    kind: str
    label: str
    state: str
    text: str
    stale: bool
    source: str | None
    age_seconds: float | None


@dataclass(frozen=True, slots=True)
class NowCard:
    as_of: datetime
    headline: str
    all_current: bool
    lines: tuple[NowLine, ...]


def _names(value: Any, allowed: frozenset[str]) -> str:
    if type(value) is not list or any(type(v) is not str or v not in allowed for v in value):
        raise ValueError("invalid bounded set")
    return ", ".join(sorted(v.replace("_", " ") for v in value))


def _value_text(kind: str, value: Any, as_of: datetime) -> str:
    """Plain text for a typed value; anything off-shape raises."""
    if kind == "owner.place":
        presence = value.get("presence") if isinstance(value, dict) else None
        if presence == "inside":
            return "At a known place"
        if presence == "outside":
            return "Elsewhere"
        raise ValueError("invalid presence")
    if kind == "owner.activity":
        return _names(value, ACTIVITIES) or "Nothing reported"
    if kind == "owner.mode":
        return _names(value, MODES) or "No mode set"
    if kind == "owner.clock":
        if not isinstance(value, dict) or value.get("basis") not in _CLOCK_BASIS:
            raise ValueError("invalid clock")
        try:
            zone = ZoneInfo(value["zone"])
        except (ZoneInfoNotFoundError, KeyError, TypeError, ValueError):
            raise ValueError("invalid zone") from None
        local = as_of.astimezone(zone)
        return f"{local:%H:%M} {zone.key} ({_CLOCK_BASIS[value['basis']]})"
    if kind == "owner.suppression":
        active = value.get("active") if isinstance(value, dict) else None
        if type(active) is not bool:
            raise ValueError("invalid suppression")
        return "On" if active else "Off"
    raise ValueError("not a /now signal")


def _line(kind: str, read: SituationRead | None, as_of: datetime) -> NowLine:
    label = NOW_LABELS[kind]
    if read is None:
        return NowLine(kind, label, "unknown", _UNREADABLE, False, None, None)
    state, value, stale_value = read.state, read.value, read.stale_last_value
    if state == "known" and read.fresh_until is not None and read.fresh_until <= as_of:
        state, value, stale_value = "unknown", None, read.value
    if state == "known":
        try:
            text = _value_text(kind, value, as_of)
        except (ValueError, TypeError, AttributeError):
            return NowLine(kind, label, "unknown", _UNREADABLE, False, read.source, None)
        return NowLine(kind, label, "known", text, False, read.source, read.age_seconds)
    if state not in _STATE_TEXT:
        return NowLine(kind, label, "unknown", _UNREADABLE, False, read.source, None)
    if state == "unknown" and stale_value is not None:
        try:
            last = _value_text(kind, stale_value, as_of)
        except (ValueError, TypeError, AttributeError):
            last = None
        if last is not None:
            text = f"Unknown; last known: {last} (stale)"
            return NowLine(kind, label, "unknown", text, True, read.source, read.age_seconds)
    return NowLine(kind, label, state, _STATE_TEXT[state], False, read.source, read.age_seconds)


def render_now(reads: Mapping[str, SituationRead | None], as_of: datetime) -> NowCard:
    """The card for ``reads`` at ``as_of``. A kind absent from ``reads`` is unreadable."""
    lines = tuple(_line(kind, reads.get(kind), as_of) for kind in NOW_KINDS)
    behind = sum(1 for line in lines if line.state != "known")
    headline = (
        f"All {len(lines)} signals current"
        if behind == 0
        else f"{behind} of {len(lines)} signals not current"
    )
    return NowCard(as_of=as_of, headline=headline, all_current=behind == 0, lines=lines)


def _age(seconds: float) -> str:
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)} min ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)} h ago"
    return f"{int(seconds // 86400)} d ago"


def render_text(card: NowCard) -> str:
    """Plain-text card, one signal per line (the form a chat reply would use)."""
    stamp = card.as_of.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
    rows = [f"Now ({stamp}): {card.headline}"]
    for line in card.lines:
        age = f" ({_age(line.age_seconds)})" if line.age_seconds is not None else ""
        rows.append(f"{line.label}: {line.text}{age}")
    return "\n".join(rows)


async def read_now(connection: Any, *, subject: UUID, as_of: datetime) -> NowCard:
    """Read every /now signal as the owner and render the card.

    The connection must be in the owner reader role (``PermissionError``
    otherwise). A malformed read renders as unreadable rather than failing the card.
    """
    reads: dict[str, SituationRead | None] = {}
    for kind in NOW_KINDS:
        try:
            read = await situation_read(
                connection, audience=Audience.OWNER, kind=kind, subject=subject, as_of=as_of
            )
        except ValueError:
            read = None
        reads[kind] = read if isinstance(read, SituationRead) else None
    return render_now(reads, as_of)


__all__ = ["NOW_KINDS", "NOW_LABELS", "NowCard", "NowLine", "read_now", "render_now", "render_text"]
