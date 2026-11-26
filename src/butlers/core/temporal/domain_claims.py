"""Domain time claims: one calendar presence per specialist record (bu-5ii4gj.13).

A *claim* is the schema-v2 entry a producer (finance, travel, health, relationship, education)
writes into its overlay envelope for one of its own records: a bill, a flight leg, a visit. The
claim, not a model, decides whether the record shows on the calendar, so it is **pure data**:
parsed and validated here with no database, provider or LLM, and shared by the producing jobs
(which emit claims) and the calendar module (which reconciles them into calendar rows).

``claim_key`` is ``<domain>:<record_kind>:<record_id>``, stable for the record's life. The domain
must be the producing butler, which is what keeps a butler to its own claims.
``record_version`` changes whenever the time, amount or status the claim depends on changes: an
unchanged version means nothing to write.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

CLAIM_SCHEMA_VERSION = 2
MAX_LABEL_CHARS = 120
MAX_DOOR_BYTES = 2048
#: ``calendar_events.origin_ref`` prefix of every row a claim owns.
CLAIM_ORIGIN_PREFIX = "claim:"
#: A reminder claim with no stated end gets this long a row.
DEFAULT_REMINDER_MINUTES = 15

Presence = Literal["overlay", "block", "reminder"]
ClaimStatus = Literal["active", "settled", "cancelled"]
PRESENCES: tuple[str, ...] = ("overlay", "block", "reminder")
STATUSES: tuple[str, ...] = ("active", "settled", "cancelled")

_KEY = re.compile(r"^([a-z][a-z_]*):([a-z][a-z_]*):(\S.*)$")


class ClaimError(ValueError):
    """A claim is malformed, or is not the caller's to make."""


@dataclass(frozen=True)
class ClaimSpan:
    """When the claim's record is: timed (both instants aware) or one all-day date."""

    timezone: str
    start: datetime
    end: datetime
    all_day: bool


@dataclass(frozen=True)
class DomainClaim:
    """One validated claim."""

    claim_key: str
    domain: str
    record_version: str
    kind: str
    label: str
    priority: str | None
    span: ClaimSpan
    presence: Presence
    reminder_offsets: tuple[int, ...]
    owner_action: bool
    status: ClaimStatus
    door: Mapping[str, Any] | None

    @property
    def wants_row(self) -> bool:
        """An active ``block`` or ``reminder`` claim owns a calendar row; ``overlay`` owns none."""
        return self.status == "active" and self.presence in ("block", "reminder")


def make_claim_key(domain: str, record_kind: str, record_id: object) -> str:
    """The stable key for one record; raises :class:`ClaimError` when it is not well formed."""
    key = f"{domain}:{record_kind}:{record_id}"
    if _KEY.match(key) is None:
        raise ClaimError(f"malformed claim_key: {key!r}")
    return key


def _zone(name: object) -> ZoneInfo:
    if not isinstance(name, str) or not name:
        raise ClaimError("span.timezone is required")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ClaimError(f"unknown span.timezone: {name!r}") from exc


def _instant(raw: object, field: str) -> datetime:
    if not isinstance(raw, str):
        raise ClaimError(f"span.{field} must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ClaimError(f"span.{field} is not ISO-8601: {raw!r}") from exc
    if parsed.tzinfo is None:
        raise ClaimError(f"span.{field} must carry an offset")
    return parsed


def parse_span(raw: object, presence: str) -> ClaimSpan:
    """``{date, timezone}`` for all-day, or ``{start, end?, timezone}`` for a timed claim."""
    if not isinstance(raw, Mapping):
        raise ClaimError("span is required")
    zone = _zone(raw.get("timezone"))
    if raw.get("date") is not None:
        if raw.get("start") is not None or raw.get("end") is not None:
            raise ClaimError("span is either a date or a start/end, not both")
        try:
            day = date.fromisoformat(str(raw["date"]))
        except ValueError as exc:
            raise ClaimError(f"span.date is not an ISO date: {raw['date']!r}") from exc
        start = datetime.combine(day, time.min, tzinfo=zone)
        end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone)
        return ClaimSpan(zone.key, start, end, True)
    start = _instant(raw.get("start"), "start")
    if raw.get("end") is None:
        if presence != "reminder":
            raise ClaimError("span.end is required unless the claim is a reminder")
        return ClaimSpan(
            zone.key, start, start + timedelta(minutes=DEFAULT_REMINDER_MINUTES), False
        )
    end = _instant(raw.get("end"), "end")
    if end <= start:
        raise ClaimError("span.end must be after span.start")
    return ClaimSpan(zone.key, start, end, False)


def parse_claim(raw: object, *, source_butler: str) -> DomainClaim:
    """Validate one claim emitted by ``source_butler``; raises :class:`ClaimError`."""
    if not isinstance(raw, Mapping):
        raise ClaimError("a claim is an object")
    key = raw.get("claim_key")
    match = _KEY.match(key) if isinstance(key, str) else None
    if match is None or not isinstance(key, str):
        raise ClaimError(f"malformed claim_key: {key!r}")
    if match.group(1) != source_butler:
        raise ClaimError(f"claim {key!r} is not {source_butler}'s: a butler claims only its own")
    version = raw.get("record_version")
    if not isinstance(version, (str, int)) or isinstance(version, bool) or version == "":
        raise ClaimError("record_version is required")
    label = raw.get("label")
    if not isinstance(label, str) or not label.strip():
        raise ClaimError("label is required")
    if len(label.strip()) > MAX_LABEL_CHARS:
        raise ClaimError(f"label exceeds {MAX_LABEL_CHARS} characters")
    kind = raw.get("kind")
    if not isinstance(kind, str) or not kind:
        raise ClaimError("kind is required")
    presence = raw.get("presence", "overlay")
    if presence not in PRESENCES:
        raise ClaimError(f"presence must be one of {PRESENCES}: {presence!r}")
    status = raw.get("status", "active")
    if status not in STATUSES:
        raise ClaimError(f"status must be one of {STATUSES}: {status!r}")
    offsets_raw = raw.get("reminder_offsets") or []
    if not isinstance(offsets_raw, (list, tuple)) or not all(
        isinstance(o, int) and not isinstance(o, bool) and o >= 0 for o in offsets_raw
    ):
        raise ClaimError("reminder_offsets must be non-negative integer minutes")
    door = raw.get("door")
    if door is not None and not isinstance(door, Mapping):
        raise ClaimError("door must be an object")
    if door is not None and len(json.dumps(door, default=str)) > MAX_DOOR_BYTES:
        raise ClaimError(f"door exceeds {MAX_DOOR_BYTES} bytes")
    priority = raw.get("priority")
    return DomainClaim(
        claim_key=key,
        domain=match.group(1),
        record_version=str(version),
        kind=kind,
        label=label.strip(),
        priority=priority if isinstance(priority, str) else None,
        span=parse_span(raw.get("span"), presence),
        presence=presence,
        reminder_offsets=tuple(offsets_raw),
        owner_action=bool(raw.get("owner_action", False)),
        status=status,
        door=dict(door) if door is not None else None,
    )
