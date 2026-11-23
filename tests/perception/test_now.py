"""The deterministic /now card, dark (bu-dhloi1.21 PR2).

/now renders the owner's situation from typed owner reads without a model. Every
one of the four states renders, an unknown or unreadable signal never reads as
all-clear, a stale value is shown as stale, and no title, place reference or raw
coordinate ever reaches the card.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from butlers.perception import flags
from butlers.perception.now import NOW_KINDS, read_now, render_now, render_text
from butlers.perception.situation import SituationRead

pytestmark = pytest.mark.unit

OWNER = UUID("00000000-0000-4000-8000-000000000001")
AS_OF = datetime(2026, 7, 1, 9, 30, tzinfo=UTC)
PLACE_REF = "11111111-1111-4111-8111-111111111111"
LOCALITY_REF = "22222222-2222-4222-8222-222222222222"
EVIDENCE_REF = "33333333-3333-4333-8333-333333333333"
RECORD_REF = "44444444-4444-4444-8444-444444444444"
SECRET = "Secret Clinic, 1.3521,103.8198"

KNOWN_VALUES = {
    "owner.place": {"presence": "inside", "place_ref": PLACE_REF, "locality_ref": LOCALITY_REF},
    "owner.activity": ["meeting", "commuting"],
    "owner.mode": ["focus"],
    "owner.clock": {"zone": "Europe/London", "basis": "phone", "evidence_ref": EVIDENCE_REF},
    "owner.suppression": {"active": True, "record_ref": RECORD_REF},
}


def _read(kind, state="known", *, value=None, stale=None, age=120.0, fresh_until=None, **kw):
    if state == "known" and value is None:
        value = KNOWN_VALUES[kind]
    return SituationRead(
        kind=kind,
        state=state,
        value=value if state == "known" else None,
        stale_last_value=stale,
        source=kw.get("source", "home.dots"),
        observed_at=AS_OF - timedelta(seconds=age) if age is not None else None,
        fresh_until=fresh_until or AS_OF + timedelta(hours=1),
        age_seconds=age,
        labels={"version": 1, "label_instr": [], "label_data": [], "label_sens": []},
        availability_reason=kw.get("reason", "confirmed"),
        generation=1,
    )


def _all_known():
    return {kind: _read(kind) for kind in NOW_KINDS}


def _line(card, kind):
    return next(line for line in card.lines if line.kind == kind)


# -- dark ---------------------------------------------------------------------------


def test_now_is_dark():
    assert flags.NOW_LIVE is False


# -- every state renders, deterministically ------------------------------------------


def test_the_card_has_one_line_per_signal_in_a_fixed_order():
    card = render_now(_all_known(), AS_OF)
    assert [line.kind for line in card.lines] == list(NOW_KINDS)
    assert [line.label for line in card.lines] == [
        "Place",
        "Activity",
        "Mode",
        "Clock",
        "Quiet",
    ]


def test_all_known_and_fresh_is_the_only_all_current_card():
    card = render_now(_all_known(), AS_OF)
    assert card.all_current is True
    assert card.headline == "All 5 signals current"
    assert {line.state for line in card.lines} == {"known"}
    assert not any(line.stale for line in card.lines)


def test_known_values_render_as_plain_text():
    card = render_now(_all_known(), AS_OF)
    assert _line(card, "owner.place").text == "At a known place"
    assert _line(card, "owner.activity").text == "commuting, meeting"
    assert _line(card, "owner.mode").text == "focus"
    assert _line(card, "owner.clock").text == "10:30 Europe/London (from phone)"
    assert _line(card, "owner.suppression").text == "On"


@pytest.mark.parametrize(
    ("kind", "value", "text"),
    [
        (
            "owner.place",
            {"presence": "outside", "place_ref": None, "locality_ref": None},
            "Elsewhere",
        ),
        (
            "owner.place",
            {"presence": "outside", "place_ref": PLACE_REF, "locality_ref": None},
            "Elsewhere",
        ),
        ("owner.activity", [], "Nothing reported"),
        ("owner.mode", [], "No mode set"),
        ("owner.mode", ["house_unattended", "travel"], "house unattended, travel"),
        ("owner.suppression", {"active": False, "record_ref": None}, "Off"),
        (
            "owner.clock",
            {
                "zone": "America/New_York",
                "basis": "owner_confirmed_booking",
                "evidence_ref": EVIDENCE_REF,
            },
            "05:30 America/New_York (from a confirmed booking)",
        ),
        (
            "owner.clock",
            {"zone": "UTC", "basis": "utc_fallback", "evidence_ref": EVIDENCE_REF},
            "09:30 UTC (UTC fallback)",
        ),
    ],
)
def test_each_known_value_shape_renders(kind, value, text):
    reads = _all_known()
    reads[kind] = _read(kind, value=value)
    assert _line(render_now(reads, AS_OF), kind).text == text


@pytest.mark.parametrize(
    ("state", "text"),
    [
        ("unknown", "Unknown"),
        ("unmeasurable", "Can't be measured"),
        ("unconfigured", "Not set up"),
    ],
)
@pytest.mark.parametrize("kind", NOW_KINDS)
def test_every_non_known_state_renders_and_is_never_all_clear(kind, state, text):
    reads = _all_known()
    reads[kind] = _read(kind, state, reason="unknown_evidence")
    card = render_now(reads, AS_OF)
    line = _line(card, kind)
    assert (line.state, line.text, line.stale) == (state, text, False)
    assert card.all_current is False
    assert card.headline == "1 of 5 signals not current"


@pytest.mark.parametrize("kind", NOW_KINDS)
def test_a_missing_or_unreadable_signal_reads_unknown_not_all_clear(kind):
    reads = _all_known()
    reads[kind] = None
    card = render_now(reads, AS_OF)
    assert (_line(card, kind).state, _line(card, kind).text) == ("unknown", "Unknown (unreadable)")
    assert card.all_current is False
    del reads[kind]
    assert _line(render_now(reads, AS_OF), kind).text == "Unknown (unreadable)"


def test_nothing_read_is_all_unknown():
    card = render_now({}, AS_OF)
    assert card.all_current is False
    assert card.headline == "5 of 5 signals not current"
    assert {line.state for line in card.lines} == {"unknown"}


# -- stale is shown as stale ---------------------------------------------------------


def test_a_stale_last_value_is_shown_as_stale_never_as_current():
    reads = _all_known()
    reads["owner.place"] = _read(
        "owner.place",
        "unknown",
        stale={"presence": "outside", "place_ref": None, "locality_ref": None},
        age=7200.0,
        reason="stale",
    )
    card = render_now(reads, AS_OF)
    line = _line(card, "owner.place")
    assert line.state == "unknown"
    assert line.stale is True
    assert line.text == "Unknown; last known: Elsewhere (stale)"
    assert card.all_current is False


def test_a_known_read_past_its_freshness_is_stale_even_if_the_row_says_known():
    reads = _all_known()
    reads["owner.mode"] = _read("owner.mode", fresh_until=AS_OF)
    card = render_now(reads, AS_OF)
    line = _line(card, "owner.mode")
    assert (line.state, line.stale) == ("unknown", True)
    assert line.text == "Unknown; last known: focus (stale)"
    assert card.all_current is False


def test_an_unrenderable_value_fails_closed_to_unknown():
    reads = _all_known()
    reads["owner.place"] = _read("owner.place", value={"presence": SECRET})
    reads["owner.clock"] = _read(
        "owner.clock", value={"zone": "Not/AZone", "basis": "phone", "evidence_ref": EVIDENCE_REF}
    )
    card = render_now(reads, AS_OF)
    for kind in ("owner.place", "owner.clock"):
        assert (_line(card, kind).state, _line(card, kind).text) == (
            "unknown",
            "Unknown (unreadable)",
        )
    assert SECRET not in json.dumps(asdict(card), default=str)


# -- no title, place reference or coordinate ----------------------------------------


def test_no_reference_title_or_coordinate_reaches_the_card():
    reads = _all_known()
    reads["owner.place"] = _read(
        "owner.place",
        value={"presence": "inside", "place_ref": PLACE_REF, "locality_ref": LOCALITY_REF},
    )
    reads["owner.activity"] = _read(
        "owner.activity",
        "unknown",
        stale={"presence": "inside", "place_ref": SECRET, "locality_ref": SECRET, "title": SECRET},
        reason="stale",
    )
    card = render_now(reads, AS_OF)
    rendered = json.dumps(asdict(card), default=str) + render_text(card)
    for leak in (PLACE_REF, LOCALITY_REF, EVIDENCE_REF, RECORD_REF, SECRET):
        assert leak not in rendered


# -- text form (for a later Telegram reply) ----------------------------------------


def test_text_form_is_deterministic_and_carries_age():
    reads = _all_known()
    reads["owner.place"] = _read("owner.place", age=30.0)
    reads["owner.activity"] = _read("owner.activity", age=600.0)
    reads["owner.mode"] = _read("owner.mode", age=3 * 3600.0)
    reads["owner.suppression"] = _read("owner.suppression", "unconfigured", age=None)
    reads["owner.clock"] = _read("owner.clock", age=2 * 86400.0)
    card = render_now(reads, AS_OF)
    assert render_text(card) == render_text(render_now(reads, AS_OF))
    assert render_text(card) == "\n".join(
        [
            "Now (2026-07-01 09:30 UTC): 1 of 5 signals not current",
            "Place: At a known place (just now)",
            "Activity: commuting, meeting (10 min ago)",
            "Mode: focus (3 h ago)",
            "Clock: 10:30 Europe/London (from phone) (2 d ago)",
            "Quiet: Not set up",
        ]
    )


# -- the read path -------------------------------------------------------------------


class _Conn:
    """Owner reader connection answering the role check and per-kind reads."""

    def __init__(self, rows, role="perception_owner_reader"):
        self.rows = rows
        self.role = role
        self.kinds: list[str] = []

    async def fetchval(self, query, *args):
        if "current_user" in query:
            return self.role
        if "perception_read" in query:
            kind, subject, as_of = args
            assert (subject, as_of) == (OWNER, AS_OF)
            self.kinds.append(kind)
            return self.rows.get(kind)
        raise AssertionError(query)


def _row(kind, state="known"):
    return {
        "kind": kind,
        "state": state,
        "value": KNOWN_VALUES[kind] if state == "known" else None,
        "stale_last_value": None,
        "source": "home.dots",
        "observed_at": AS_OF.isoformat(),
        "fresh_until": (AS_OF + timedelta(hours=1)).isoformat(),
        "age_seconds": 0,
        "labels": {"version": 1, "label_instr": [], "label_data": [], "label_sens": []},
        "generation": 1,
        "availability_reason": "confirmed",
    }


async def test_read_now_reads_every_kind_as_the_owner():
    conn = _Conn({kind: _row(kind) for kind in NOW_KINDS})
    card = await read_now(conn, subject=OWNER, as_of=AS_OF)
    assert conn.kinds == list(NOW_KINDS)
    assert card.all_current is True


async def test_read_now_renders_a_malformed_read_as_unreadable():
    rows = {kind: _row(kind) for kind in NOW_KINDS}
    rows["owner.place"] = {"state": "known", "value": None}
    card = await read_now(_Conn(rows), subject=OWNER, as_of=AS_OF)
    assert _line(card, "owner.place").text == "Unknown (unreadable)"
    assert card.all_current is False


async def test_read_now_refuses_a_connection_not_in_the_owner_role():
    conn = _Conn({}, role="perception_availability_reader")
    with pytest.raises(PermissionError):
        await read_now(conn, subject=OWNER, as_of=AS_OF)
