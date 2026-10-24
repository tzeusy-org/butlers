"""Closed S3 values and stable identities. Database repeats trust validation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

NAMESPACE = UUID("e90a74c8-7ccd-5de7-b5f8-c82bc75e0c32")
STATES = frozenset({"known", "unknown", "unmeasurable", "unconfigured"})
ACTIVITIES = frozenset(
    {
        "traveling",
        "sleeping",
        "meeting",
        "focused",
        "exercising",
        "sick",
        "socializing",
        "commuting",
    }
)
MODES = frozenset(
    {"asleep", "travel", "house_unattended", "sick", "focus", "hosting", "holiday", "emergency"}
)
CATEGORIES = frozenset(
    {"health", "finance", "location", "routine", "relationship_notes", "credentials"}
)
KINDS = frozenset(
    {
        "owner.place",
        "owner.activity",
        "owner.clock",
        "system.dep",
        "expect.window",
        "home.sensing",
        "owner.mode",
        "owner.suppression",
    }
)
ALIASES = {
    **dict.fromkeys(ACTIVITIES, "owner.activity"),
    **dict.fromkeys(("at_home", "in_space", "away", "owner.presence"), "owner.place"),
    "owner_clock": "owner.clock",
    "dnd": "owner.suppression",
}


def canonical(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    )


def event_id(source: str, event_key: str) -> UUID:
    if not isinstance(source, str) or not source or len(source.encode()) > 128:
        raise ValueError("invalid registered source")
    if not isinstance(event_key, str) or not 0 < len(event_key.encode()) <= 256:
        raise ValueError("invalid event key")
    return uuid5(NAMESPACE, canonical(["perception-event-v1", source, event_key]))


def instant(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("UTC instant required")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("UTC instant required") from None
    if result.tzinfo is None or result.utcoffset() != UTC.utcoffset(result):
        raise ValueError("UTC instant required")
    return result


def reference(value: object, *, nullable: bool = False) -> None:
    if nullable and value is None:
        return
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError("canonical UUID reference required")


def _fields(value: object, names: set[str]) -> dict:
    if type(value) is not dict or set(value) != names:
        raise ValueError("closed product fields required")
    return value


def validate_value(kind: str, value: object) -> None:
    if kind == "owner.place":
        v = _fields(value, {"presence", "place_ref", "locality_ref"})
        if v["presence"] not in {"inside", "outside"}:
            raise ValueError("invalid presence")
        reference(v["place_ref"], nullable=True)
        reference(v["locality_ref"], nullable=True)
    elif kind in {"owner.activity", "owner.mode"}:
        allowed = ACTIVITIES if kind == "owner.activity" else MODES
        if (
            type(value) is not list
            or len(value) > 8
            or any(type(v) is not str or v not in allowed for v in value)
            or len(set(value)) != len(value)
        ):
            raise ValueError("invalid bounded set")
    elif kind == "owner.clock":
        v = _fields(value, {"zone", "basis", "evidence_ref"})
        if v["basis"] not in {"phone", "owner", "home", "utc_fallback", "owner_confirmed_booking"}:
            raise ValueError("invalid clock basis")
        try:
            ZoneInfo(v["zone"])
        except (ZoneInfoNotFoundError, TypeError, ValueError):
            raise ValueError("invalid IANA zone") from None
        reference(v["evidence_ref"])
    elif kind == "expect.window":
        v = _fields(value, {"start", "end", "deviation", "expectation_kind"})
        if instant(v["start"]) >= instant(v["end"]) or v["deviation"] not in {
            "pending",
            "within",
            "missed",
        }:
            raise ValueError("invalid expectation window")
        # Registered enums are resolved against the privileged source profile in SQL.
        if (
            type(v["expectation_kind"]) is not str
            or not 0 < len(v["expectation_kind"].encode()) <= 128
        ):
            raise ValueError("registered expectation enum required")
    elif kind == "owner.suppression":
        v = _fields(value, {"active", "record_ref"})
        if type(v["active"]) is not bool or (v["active"] and v["record_ref"] is None):
            raise ValueError("invalid suppression reference")
        reference(v["record_ref"], nullable=True)
    elif kind == "system.dep":
        if value not in ("available", "degraded", "blind", "down", "held", "unconfigured"):
            raise ValueError("invalid dependency state")
    elif kind == "home.sensing":
        if value not in ("available", "degraded", "unavailable"):
            raise ValueError("invalid sensing state")
    else:
        raise ValueError("unknown perception kind")


@dataclass(frozen=True)
class EventRequest:
    """Immutable request snapshot. No writer identity, late flag or from-state input.

    Evidence entries retain their original opaque source/ref and expiry. Source
    profiles, principal resolution and actual role authority are server-owned.
    """

    body: dict

    def __post_init__(self) -> None:
        b = json.loads(canonical(self.body))
        _fields(
            b,
            {
                "version",
                "kind",
                "subject",
                "source",
                "event_key",
                "expected_generation",
                "state",
                "observed_at",
                "effective_span",
                "typed_value_or_ref",
                "evidence_refs",
                "labels",
                "at",
                "confirmed_at",
                "retracts_event_id",
            },
        )
        if b["version"] != 1 or type(b["version"]) is not int or b["kind"] not in KINDS:
            raise ValueError("unsupported perception version/kind")
        reference(b["subject"], nullable=True)
        event_id(b["source"], b["event_key"])
        if (
            type(b["expected_generation"]) is not int
            or not 0 <= b["expected_generation"] < 2**63 - 1
        ):
            raise ValueError("invalid generation")
        if b["state"] not in STATES:
            raise ValueError("invalid perception state")
        if b["state"] == "known":
            validate_value(b["kind"], b["typed_value_or_ref"])
        elif b["typed_value_or_ref"] is not None:
            raise ValueError("nonknown state cannot carry current value")
        observed, at, confirmed = (instant(b[n]) for n in ("observed_at", "at", "confirmed_at"))
        span = _fields(b["effective_span"], {"start", "end"})
        if instant(span["start"]) > at or instant(span["end"]) <= at or confirmed < at:
            raise ValueError("invalid effective/confirmation interval")
        if observed > confirmed:
            raise ValueError("observation follows confirmation")
        evidence = b["evidence_refs"]
        if type(evidence) is not list or not 0 < len(evidence) <= 32:
            raise ValueError("bounded evidence required")
        refs = set()
        for e in evidence:
            _fields(e, {"ref", "source", "expires_at"})
            reference(e["ref"])
            event_id(e["source"], "evidence")
            instant(e["expires_at"])
            if e["ref"] in refs:
                raise ValueError("duplicate evidence")
            refs.add(e["ref"])
        labels = _fields(b["labels"], {"version", "label_instr", "label_data", "label_sens"})
        if type(labels["version"]) is not int or labels["version"] != 1:
            raise ValueError("invalid labels")
        for n in ("label_instr", "label_data", "label_sens"):
            v = labels[n]
            if type(v) is not list or len(v) > 32 or any(type(x) is not str for x in v):
                raise ValueError("invalid label set")
            if len(set(v)) != len(v):
                raise ValueError("duplicate label")
            if n == "label_sens":
                if not set(v) <= CATEGORIES:
                    raise ValueError("unknown category")
            else:
                for x in v:
                    if x != "world":
                        reference(x)
        reference(b["retracts_event_id"], nullable=True)
        # Freeze the canonical bytes, not a mutable caller-owned dict/cache.
        object.__setattr__(self, "_canonical", canonical(b))

    def payload(self) -> dict:
        return json.loads(self._canonical)
