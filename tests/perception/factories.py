"""Opaque, invented source evidence shared by closed-type and real-PG cases."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

OWNER = UUID("00000000-0000-4000-8000-000000000001")
EVIDENCE = UUID("00000000-0000-4000-8000-000000000002")
AT = datetime(2026, 1, 1, tzinfo=UTC)


def wire_time(value):
    return value.isoformat()


def request(*, key="first", kind="owner.place", generation=0, minute=0, value=None, state="known"):
    at = AT + timedelta(minutes=minute)
    sources = {
        "owner.place": "home.dots",
        "owner.activity": "context.activity",
        "owner.clock": "context.clock",
        "owner.mode": "context.mode",
        "system.dep": "health.dependencies",
        "expect.window": "chronicler.expectations",
        "home.sensing": "home.sensing",
        "owner.suppression": "attention.suppression",
    }
    values = {
        "owner.place": {"presence": "inside", "place_ref": None, "locality_ref": None},
        "owner.activity": ["focused"],
        "owner.mode": ["focus"],
        "owner.clock": {"zone": "UTC", "basis": "home", "evidence_ref": str(EVIDENCE)},
        "system.dep": "available",
        "home.sensing": "available",
        "expect.window": {
            "start": wire_time(at),
            "end": wire_time(at + timedelta(hours=1)),
            "deviation": "pending",
            "expectation_kind": "confirmed_routine",
        },
        "owner.suppression": {"active": False, "record_ref": None},
    }
    return {
        "version": 1,
        "kind": kind,
        "subject": str(OWNER),
        "source": sources[kind],
        "event_key": key,
        "expected_generation": generation,
        "state": state,
        "observed_at": wire_time(at),
        "effective_span": {"start": wire_time(at), "end": wire_time(at + timedelta(hours=1))},
        "typed_value_or_ref": (values[kind] if value is None else value)
        if state == "known"
        else None,
        "evidence_refs": [
            {
                "ref": str(EVIDENCE),
                "source": "fixture.evidence",
                "expires_at": wire_time(at + timedelta(hours=1)),
            }
        ],
        "labels": {
            "version": 1,
            "label_instr": ["world"],
            "label_data": ["world"],
            "label_sens": ["location"],
        },
        "at": wire_time(at),
        "confirmed_at": wire_time(at),
        "retracts_event_id": None,
    }
