"""Typed situation reads and the timeline query, dark (bu-dhloi1.21 PR1).

RFC 0038 S1/S3: a read returns one of four states with source, age and labels; an
exposed or third-party caller sees owner availability only (``busy_until`` or
unknown), never place, routine, title or coordinates; ``timeline.query`` is
owner-only and carries derived ``retracted``/``late``.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from butlers.perception import flags
from butlers.perception.situation import (
    AVAILABILITY_FIELDS,
    Audience,
    situation_capabilities,
    situation_read,
    timeline_query,
)

pytestmark = pytest.mark.unit

OWNER = UUID("00000000-0000-4000-8000-000000000001")
AT = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
SECRET = "Secret Clinic, 1.3521,103.8198"
OWNER_ROLE = "perception_owner_reader"
AVAILABILITY_ROLE = "perception_availability_reader"


class _Conn:
    """Answers the role check and the two perception functions; records every call."""

    def __init__(self, role: str, *, read=None, timeline=None) -> None:
        self.role = role
        self.read_result = read
        self.timeline_rows = timeline or []
        self.calls: list[tuple[str, tuple]] = []

    async def fetchval(self, query: str, *args):
        self.calls.append((query, args))
        if "current_user" in query:
            return self.role
        if "perception_read" in query:
            return self.read_result
        if "perception_timeline" in query:
            return self.timeline_rows
        raise AssertionError(f"unexpected query: {query}")


def _owner_row(state="known", **overrides):
    row = {
        "id": "00000000-0000-4000-8000-0000000000aa",
        "kind": "owner.place",
        "subject": str(OWNER),
        "state": state,
        "value": {"presence": "inside", "place_ref": None, "locality_ref": None}
        if state == "known"
        else None,
        "stale_last_value": None,
        "source": "home.dots",
        "observed_at": AT.isoformat(),
        "fresh_until": (AT + timedelta(hours=1)).isoformat(),
        "age_seconds": 0,
        "labels": {
            "version": 1,
            "label_instr": ["world"],
            "label_data": ["world"],
            "label_sens": ["location"],
        },
        "evidence_refs": [],
        "generation": 1,
        "availability_reason": "confirmed" if state == "known" else "unknown_evidence",
    }
    row.update(overrides)
    return row


# -- dark --------------------------------------------------------------------------


def test_the_reads_are_dark_and_offer_no_capability():
    assert flags.SITUATION_READS_LIVE is False
    assert situation_capabilities() == ()


def test_turning_the_flag_on_offers_both_reads(monkeypatch):
    monkeypatch.setattr(flags, "SITUATION_READS_LIVE", True)
    assert situation_capabilities() == ("situation.read", "timeline.query")


# -- exposed and third-party callers see availability only -------------------------

#: Every detail an exposed caller must never see, as the SQL might (wrongly) return it.
_LEAKS = {
    "value": {"presence": "inside", "place_ref": SECRET, "locality_ref": SECRET},
    "stale_last_value": {"presence": "outside", "place_ref": SECRET, "locality_ref": None},
    "source": SECRET,
    "labels": {"label_sens": [SECRET]},
    "evidence_refs": [SECRET],
    "subject": SECRET,
    "id": SECRET,
    "title": SECRET,
    "routine": SECRET,
    "place": SECRET,
    "latitude": SECRET,
    "longitude": SECRET,
    "coordinates": [SECRET],
    "observed_at": SECRET,
    "fresh_until": SECRET,
    "age_seconds": SECRET,
    "availability_reason": SECRET,
    "state": SECRET,
    "busy_until": SECRET,
}


@pytest.mark.parametrize("kind", ["owner.place", "expect.window", "owner.activity", "owner.mode"])
@pytest.mark.parametrize("field", sorted(_LEAKS))
async def test_an_exposed_caller_never_sees_detail(kind, field):
    raw = {"state": "unknown", "availability_reason": "detail_restricted", "busy_until": None}
    raw[field] = _LEAKS[field]
    conn = _Conn(AVAILABILITY_ROLE, read=raw)
    result = await situation_read(
        conn, audience=Audience.AVAILABILITY, kind=kind, subject=OWNER, as_of=AT
    )
    shown = asdict(result)
    assert set(shown) == set(AVAILABILITY_FIELDS) == {"state", "busy_until", "availability_reason"}
    assert SECRET not in json.dumps(shown, default=str)
    assert shown["state"] == "unknown"
    assert shown["availability_reason"] == "detail_restricted"


async def test_an_exposed_caller_sees_a_busy_until():
    busy = AT + timedelta(minutes=45)
    conn = _Conn(
        AVAILABILITY_ROLE,
        read={
            "state": "unknown",
            "availability_reason": "detail_restricted",
            "busy_until": busy.isoformat(),
        },
    )
    result = await situation_read(
        conn, audience=Audience.AVAILABILITY, kind="owner.place", subject=OWNER, as_of=AT
    )
    assert result.busy_until == busy


@pytest.mark.parametrize(
    ("audience", "role"),
    [(Audience.AVAILABILITY, OWNER_ROLE), (Audience.OWNER, AVAILABILITY_ROLE)],
)
async def test_a_connection_in_the_wrong_role_is_refused_before_any_read(audience, role):
    conn = _Conn(role, read=_owner_row())
    with pytest.raises(PermissionError):
        await situation_read(conn, audience=audience, kind="owner.place", subject=OWNER, as_of=AT)
    assert not any("perception_read" in q for q, _ in conn.calls)


async def test_an_exposed_caller_has_no_timeline():
    conn = _Conn(AVAILABILITY_ROLE)
    with pytest.raises(PermissionError):
        await timeline_query(
            conn,
            audience=Audience.AVAILABILITY,
            since=AT - timedelta(hours=1),
            until=AT,
        )
    assert conn.calls == []  # refused before touching the database


# -- the owner's four-state read ----------------------------------------------------


@pytest.mark.parametrize("state", ["known", "unknown", "unmeasurable", "unconfigured"])
async def test_the_owner_reads_all_four_states(state):
    conn = _Conn(OWNER_ROLE, read=_owner_row(state))
    result = await situation_read(
        conn, audience=Audience.OWNER, kind="owner.place", subject=OWNER, as_of=AT
    )
    assert result.state == state
    assert (result.value is not None) == (state == "known")
    assert result.source == "home.dots" and result.labels["version"] == 1


async def test_a_stale_value_reads_unknown_with_its_last_value_labelled():
    last = {"presence": "inside", "place_ref": None, "locality_ref": None}
    conn = _Conn(
        OWNER_ROLE,
        read=_owner_row("unknown", stale_last_value=last, availability_reason="stale"),
    )
    result = await situation_read(
        conn, audience=Audience.OWNER, kind="owner.place", subject=OWNER, as_of=AT
    )
    assert result.state == "unknown" and result.value is None
    assert result.stale_last_value == last and result.availability_reason == "stale"


async def test_an_unconfigured_slot_has_no_source():
    conn = _Conn(
        OWNER_ROLE,
        read={
            "state": "unconfigured",
            "value": None,
            "source": None,
            "age_seconds": None,
            "labels": _owner_row()["labels"],
            "availability_reason": "unconfigured",
        },
    )
    result = await situation_read(
        conn, audience=Audience.OWNER, kind="owner.clock", subject=OWNER, as_of=AT
    )
    assert result.state == "unconfigured" and result.source is None and result.value is None


@pytest.mark.parametrize(
    "bad",
    [
        {"state": "all_clear"},
        {"state": "known", "value": None},
        {"state": "unknown", "value": {"presence": "inside"}},
        {"labels": None},
    ],
)
async def test_a_malformed_read_fails_closed(bad):
    conn = _Conn(OWNER_ROLE, read=_owner_row(**bad))
    with pytest.raises(ValueError):
        await situation_read(
            conn, audience=Audience.OWNER, kind="owner.place", subject=OWNER, as_of=AT
        )


# -- the owner's timeline ------------------------------------------------------------


async def test_the_owner_timeline_carries_retracted_and_late_and_skips_them_by_default():
    row = {
        "id": "00000000-0000-4000-8000-0000000000b1",
        "kind": "owner.place",
        "subject_entity": str(OWNER),
        "class": "transition",
        "operation": "assert",
        "at": AT.isoformat(),
        "confirmed_at": (AT + timedelta(minutes=20)).isoformat(),
        "late": True,
        "retracted": True,
        "retraction_event_id": "00000000-0000-4000-8000-0000000000b2",
        "retracts_event_id": None,
        "from_state": "known",
        "to_state": "known",
        "from_value": {"presence": "inside", "place_ref": None, "locality_ref": None},
        "to_value": {"presence": "outside", "place_ref": None, "locality_ref": None},
        "typed_value": None,
        "labels": _owner_row()["labels"],
        "source": "home.dots",
        "writer": "perception_dots_writer",
        "digest": "abc",
        "event_key": "k",
    }
    conn = _Conn(OWNER_ROLE, timeline=[row])
    entries = await timeline_query(
        conn, audience=Audience.OWNER, since=AT - timedelta(hours=1), until=AT + timedelta(hours=1)
    )
    (entry,) = entries
    assert entry.retracted is True and entry.late is True
    assert entry.retraction_event_id == UUID(row["retraction_event_id"])
    assert entry.to_value == row["to_value"]
    assert not {"writer", "digest", "event_key"} & set(asdict(entry))
    _, args = next(c for c in conn.calls if "perception_timeline" in c[0])
    assert args[-1] is True  # live: late and retracted assertions are skipped by default
