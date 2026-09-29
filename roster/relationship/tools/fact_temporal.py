"""Effective time for ``relationship.entity_facts`` (relationship-fact-effective-time).

Effective time says when an asserted relationship held in the world. It is a
fifth axis, independent of assertion time (``created_at``), observation time
(``observed_at``/``last_seen``), confidence (``conf``) and assertion lifecycle
(``validity``): none of those ever defaults or derives an effective bound, and
``validity='active'`` keeps meaning "current assertion version", not "effective
now".

One stored *packet* is five nullable columns: an occurrence id plus a half-open
``[effective_from, effective_to)`` interval with a precision per bound. A NULL
bound with NULL precision is *unknown*; a NULL bound with ``unbounded`` precision
is an explicit open end. The NULL occurrence id is the backward-compatible
default occurrence every legacy row and every caller without temporal intent
uses.

This module owns the wire contract, independent of any database state:

* :func:`normalize_request` turns the six optional writer arguments into a
  :class:`TemporalRequest`. Omission and explicit ``None`` are the same input at
  every position; the request mode is derived from values alone, never from
  which keys were present.
* Validation and UTC normalization happen here, before approval parking,
  deduplication or any write.
* :func:`require_temporal_admission` is the transition fence. While the legacy
  ``uq_ef_spo_active`` index exists (the expand stage), every temporal intent
  fails ``temporal_cutover_pending``. The index's absence after a future cutover
  migration is the only capability signal; there is no flag.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, fields
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import asyncpg

CONCRETE_PRECISIONS = frozenset({"instant", "day", "month", "year"})
UNBOUNDED = "unbounded"
DEFAULT_OCCURRENCE_SENTINEL = uuid.UUID(int=0)
LEGACY_SPO_INDEX = "relationship.uq_ef_spo_active"

#: The six optional writer arguments, in their canonical ``tool_args`` order.
WIRE_KEYS = (
    "effective_period_id",
    "effective_from",
    "effective_from_precision",
    "effective_to",
    "effective_to_precision",
    "corrects_fact_id",
)

#: The five stored columns, for SELECT/INSERT lists.
PACKET_COLUMNS = (
    "effective_period_id, effective_from, effective_from_precision, "
    "effective_to, effective_to_precision"
)


def temporal_bearing_sql(alias: str) -> str:
    """SQL predicate: the aliased ``entity_facts`` row carries any temporal value."""
    return (
        f"({alias}.effective_period_id IS NOT NULL"
        f" OR {alias}.effective_from IS NOT NULL"
        f" OR {alias}.effective_from_precision IS NOT NULL"
        f" OR {alias}.effective_to IS NOT NULL"
        f" OR {alias}.effective_to_precision IS NOT NULL)"
    )


class TemporalError(ValueError):
    """A temporal refusal with a stable machine-readable ``code``.

    A ``ValueError`` so every existing writer caller that maps validation
    failures keeps doing so; the code leads the message because MCP callers see
    only the message.
    """

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        super().__init__(f"{code}: {detail}")


INVALID = "temporal_invalid"
CUTOVER_PENDING = "temporal_cutover_pending"
CORRECTION_REQUIRED = "temporal_correction_required"
CORRECTION_STALE = "temporal_correction_stale"
OCCURRENCE_AMBIGUOUS = "temporal_occurrence_ambiguous"
OCCURRENCE_COLLISION = "temporal_occurrence_collision"
MUTATOR_UNSUPPORTED = "temporal_mutator_unsupported"


class RequestMode(StrEnum):
    """How a request's packet was resolved; frozen into approval context."""

    ordinary = "ordinary"  # no temporal intent: default occurrence, preserve stored packet
    explicit = "explicit"  # the supplied values are the desired packet
    correction = "correction"  # compare-and-swap replacement of one exact active row


@dataclass(frozen=True, slots=True)
class TemporalPacket:
    """The five stored effective-time values of one fact version."""

    period_id: uuid.UUID | None = None
    effective_from: datetime | None = None
    from_precision: str | None = None
    effective_to: datetime | None = None
    to_precision: str | None = None

    @property
    def is_temporal_bearing(self) -> bool:
        return any(getattr(self, f.name) is not None for f in fields(self))

    @property
    def bounds_unknown(self) -> bool:
        """Both bounds unknown (the occurrence id is not considered)."""
        return (
            self.effective_from is None
            and self.from_precision is None
            and self.effective_to is None
            and self.to_precision is None
        )

    def with_period(self, period_id: uuid.UUID | None) -> TemporalPacket:
        return TemporalPacket(
            period_id,
            self.effective_from,
            self.from_precision,
            self.effective_to,
            self.to_precision,
        )

    def sql_args(self) -> tuple[Any, ...]:
        """Bind values in :data:`PACKET_COLUMNS` order."""
        return (
            self.period_id,
            self.effective_from,
            self.from_precision,
            self.effective_to,
            self.to_precision,
        )

    def wire(self) -> dict[str, str | None]:
        """Canonical JSON form: UTC ISO strings, canonical UUID text, nulls."""
        return {
            "effective_period_id": _uuid_text(self.period_id),
            "effective_from": _instant_text(self.effective_from),
            "effective_from_precision": self.from_precision,
            "effective_to": _instant_text(self.effective_to),
            "effective_to_precision": self.to_precision,
        }

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> TemporalPacket:
        return cls(
            row["effective_period_id"],
            row["effective_from"],
            row["effective_from_precision"],
            row["effective_to"],
            row["effective_to_precision"],
        )


UNKNOWN = TemporalPacket()


@dataclass(frozen=True, slots=True)
class TemporalRequest:
    """A normalized request: its mode, desired packet and correction target.

    For ``ordinary`` the packet is :data:`UNKNOWN` until resolved against the
    stored default occurrence. For ``correction`` a ``None`` period id means
    "inherit the target's occurrence".
    """

    mode: RequestMode
    packet: TemporalPacket = UNKNOWN
    corrects_fact_id: uuid.UUID | None = None

    @property
    def has_temporal_intent(self) -> bool:
        return self.mode is not RequestMode.ordinary

    def tool_args(self, resolved: TemporalPacket) -> dict[str, str | None]:
        """All six canonical keys for ``pending_actions.tool_args``."""
        return {**resolved.wire(), "corrects_fact_id": _uuid_text(self.corrects_fact_id)}


ORDINARY = TemporalRequest(RequestMode.ordinary)


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

_COARSE_FORMS = {
    "day": re.compile(r"(?P<y>[0-9]{4})-(?P<m>[0-9]{2})-(?P<d>[0-9]{2})"),
    "month": re.compile(r"(?P<y>[0-9]{4})-(?P<m>[0-9]{2})"),
    "year": re.compile(r"(?P<y>[0-9]{4})"),
}


def _invalid(detail: str) -> TemporalError:
    return TemporalError(INVALID, detail)


def _uuid_text(value: uuid.UUID | None) -> str | None:
    return None if value is None else str(value)


def _instant_text(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(UTC).isoformat()


def _coerce_uuid(name: str, value: Any) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    if isinstance(value, str):
        try:
            return uuid.UUID(value)
        except ValueError:
            raise _invalid(f"{name} must be a UUID; got {value!r}.") from None
    raise _invalid(f"{name} must be a UUID; got {type(value).__name__}.")


def _parse_instant(name: str, value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            raise _invalid(f"{name} is not an ISO 8601 instant: {value!r}.") from None
    else:
        raise _invalid(f"{name} must be an ISO 8601 string; got {type(value).__name__}.")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise _invalid(f"{name} with precision 'instant' needs 'Z' or a UTC offset.")
    return parsed.astimezone(UTC)


def _parse_coarse(name: str, value: Any, precision: str, *, upper: bool) -> datetime:
    if not isinstance(value, str):
        raise _invalid(f"{name} with precision {precision!r} must be a civil date string.")
    match = _COARSE_FORMS[precision].fullmatch(value)
    if match is None:
        forms = {"day": "YYYY-MM-DD", "month": "YYYY-MM", "year": "YYYY"}
        raise _invalid(f"{name} with precision {precision!r} must be {forms[precision]}.")
    year = int(match["y"])
    month = int(match.groupdict().get("m") or 1)
    day = int(match.groupdict().get("d") or 1)
    try:
        start = datetime(year, month, day, tzinfo=UTC)
        if not upper:
            return start
        # Half-open: an upper bound is the first instant AFTER its named unit.
        if precision == "day":
            return datetime.fromordinal(start.toordinal() + 1).replace(tzinfo=UTC)
        if precision == "month":
            return (
                datetime(year + 1, 1, 1, tzinfo=UTC)
                if month == 12
                else datetime(year, month + 1, 1, tzinfo=UTC)
            )
        return datetime(year + 1, 1, 1, tzinfo=UTC)
    except (ValueError, OverflowError):
        raise _invalid(f"{name} {value!r} is not a representable {precision}.") from None


def _normalize_bound(
    name: str, value: Any, precision: Any, *, upper: bool
) -> tuple[datetime | None, str | None]:
    if precision is not None and precision not in CONCRETE_PRECISIONS and precision != UNBOUNDED:
        raise _invalid(
            f"{name}_precision must be one of instant, day, month, year, unbounded "
            f"(or null for unknown); got {precision!r}."
        )
    if value is None:
        if precision is not None and precision != UNBOUNDED:
            raise _invalid(f"{name}_precision {precision!r} needs a {name} value.")
        return None, precision
    if precision is None or precision == UNBOUNDED:
        raise _invalid(f"{name} needs a concrete precision (instant, day, month or year).")
    if precision == "instant":
        return _parse_instant(name, value), precision
    return _parse_coarse(name, value, precision, upper=upper), precision


def normalize_request(
    *,
    effective_period_id: Any = None,
    effective_from: Any = None,
    effective_from_precision: Any = None,
    effective_to: Any = None,
    effective_to_precision: Any = None,
    corrects_fact_id: Any = None,
) -> TemporalRequest:
    """Validate and normalize the six optional writer arguments.

    Raises :class:`TemporalError` (``temporal_invalid``) for any malformed or
    inconsistent packet. Pure: no database access.
    """
    period_id = _coerce_uuid("effective_period_id", effective_period_id)
    if period_id == DEFAULT_OCCURRENCE_SENTINEL:
        raise _invalid("effective_period_id must not be the reserved all-zero UUID.")
    target = _coerce_uuid("corrects_fact_id", corrects_fact_id)
    lower, lower_precision = _normalize_bound(
        "effective_from", effective_from, effective_from_precision, upper=False
    )
    upper, upper_precision = _normalize_bound(
        "effective_to", effective_to, effective_to_precision, upper=True
    )
    if lower is not None and upper is not None and not lower < upper:
        raise _invalid("effective_from must be earlier than effective_to (half-open interval).")
    packet = TemporalPacket(period_id, lower, lower_precision, upper, upper_precision)
    if target is not None:
        return TemporalRequest(RequestMode.correction, packet, target)
    if packet.is_temporal_bearing:
        return TemporalRequest(RequestMode.explicit, packet)
    return ORDINARY


def wire_values(**values: Any) -> dict[str, str | None]:
    """Canonical text of raw wire arguments, for comparing a replay to its parked form."""
    canonical: dict[str, str | None] = {}
    for key in WIRE_KEYS:
        value = values.get(key)
        if isinstance(value, uuid.UUID):
            value = str(value)
        elif isinstance(value, datetime):
            value = _instant_text(value)
        canonical[key] = value
    return canonical


def parked_request(
    tool_args: Mapping[str, Any], mode: str | None
) -> tuple[TemporalRequest, dict[str, str | None]]:
    """Decode the canonical temporal keys of an approved action.

    A pre-temporal action carries none of the keys and a NULL frozen mode; it
    decodes to the ordinary all-null request. Returns the request and the
    canonical wire values a replay must match exactly.
    """
    wire = {key: tool_args.get(key) for key in WIRE_KEYS}
    period_id = _coerce_uuid("effective_period_id", wire["effective_period_id"])
    packet = TemporalPacket(
        period_id,
        _decode_instant(wire["effective_from"]),
        wire["effective_from_precision"],
        _decode_instant(wire["effective_to"]),
        wire["effective_to_precision"],
    )
    request_mode = RequestMode(mode) if mode is not None else RequestMode.ordinary
    corrects = _coerce_uuid("corrects_fact_id", wire["corrects_fact_id"])
    return TemporalRequest(request_mode, packet, corrects), wire


def _decode_instant(value: Any) -> datetime | None:
    return None if value is None else _parse_instant("parked bound", value)


# ---------------------------------------------------------------------------
# Transition fence
# ---------------------------------------------------------------------------


async def legacy_spo_index_present(conn: asyncpg.Connection | asyncpg.Pool) -> bool:
    """True while the expand-stage legacy active-SPO index still exists."""
    return bool(await conn.fetchval(f"SELECT to_regclass('{LEGACY_SPO_INDEX}') IS NOT NULL"))


async def require_temporal_admission(conn: asyncpg.Connection | asyncpg.Pool) -> None:
    """Refuse temporal intent until the legacy index has been cut over."""
    if await legacy_spo_index_present(conn):
        raise TemporalError(
            CUTOVER_PENDING,
            "effective-time assertions, corrections and period ids are disabled until "
            "the relationship.entity_facts index cutover; omit every temporal argument.",
        )
