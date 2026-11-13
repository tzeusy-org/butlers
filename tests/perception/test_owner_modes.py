"""Owner modes: the closed set, bu-u33sb7 defaults, and step-up on loosening only (PR4a).

RFC 0038 S3.1 and owner decision bu-u33sb7: eight git mode IDs; in flight is a
travel posture, not a mode; only the owner edits duration, holds and
break-through senders; loosening needs the .5 dashboard step-up, tightening does
not; sick holds proactive items except medication, safety, bills due within 48 h
and replies to people; only owner-declared or owner-confirmed travel arms the
absence plan; the brief follows the wake signal, never before 06:00, else 09:30.
No automatic entry rule exists.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from uuid import UUID

import pytest

from butlers.core.dashboard_stepup import DashboardStepUpProof, dashboard_stepup_proof
from butlers.perception import flags
from butlers.perception.model import MODES, EventRequest
from butlers.perception.modes import (
    DEFAULT_PROFILES,
    MODE_LOOSEN_KIND,
    PINNED,
    EditBreakThrough,
    EditDuration,
    EditHolds,
    Enter,
    Exit,
    ModeControlsDark,
    ModeEntry,
    ModeStepUpRequired,
    arms_absence,
    authorize_mode_change,
    classify_change,
    classify_obligation,
    held_by,
    mode_binding,
    mode_event,
    mode_id,
    posture,
    wake_brief_time,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 5, 4, 8, 0, tzinfo=UTC)
OWNER = UUID("00000000-0000-4000-8000-000000000001")
ACTION = UUID("66666666-6666-4666-8666-666666666666")
REF = UUID("77777777-7777-4777-8777-777777777777")
NAMESPACE = "butlers:public"


# -- the closed set; in flight is posture -------------------------------------------


def test_the_mode_set_is_the_eight_git_ids():
    assert set(DEFAULT_PROFILES) == set(MODES)
    assert len(MODES) == 8


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("asleep", "asleep"),
        ("sleep", "asleep"),
        ("house unattended", "house_unattended"),
        ("focused", "focus"),
        ("Travel", "travel"),
    ],
)
def test_display_aliases_resolve_to_the_wire_id(name, expected):
    assert mode_id(name) == expected


@pytest.mark.parametrize("name", ["in flight", "in_flight", "flying", "vacation", "", "sleeping "])
def test_unknown_custom_and_in_flight_names_refuse(name):
    with pytest.raises(ValueError):
        mode_id(name)


def test_in_flight_is_a_travel_posture_never_a_mode():
    flying = posture({"travel"}, in_flight=True)
    assert flying.in_flight is True and flying.modes == frozenset({"travel"})
    assert posture({"focus"}, in_flight=True).in_flight is False
    assert posture(set(), in_flight=True).in_flight is False


# -- bu-u33sb7 defaults ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "due", "expected"),
    [
        ("medication", None, "medication"),
        ("safety", None, "safety"),
        ("reply", None, "reply_to_person"),
        ("bill", NOW + timedelta(hours=47), "bill_due_48h"),
        ("bill", NOW + timedelta(hours=48), "bill_due_48h"),
        ("bill", NOW + timedelta(hours=49), "proactive"),
        ("bill", None, "proactive"),
        ("other", None, "proactive"),
    ],
)
def test_obligations_classify_against_the_sick_exemptions(kind, due, expected):
    assert classify_obligation(kind, due_at=due, now=NOW) == expected


def test_sick_holds_proactive_items_but_not_its_four_exemptions():
    sick = DEFAULT_PROFILES["sick"]
    assert held_by(sick, "proactive") is True
    for exempt in ("medication", "safety", "bill_due_48h", "reply_to_person"):
        assert held_by(sick, exempt) is False


def test_no_other_mode_holds_anything_by_default():
    for mode, profile in DEFAULT_PROFILES.items():
        if mode != "sick":
            assert profile.holds == frozenset()
        assert profile.break_through == frozenset()


def test_safety_and_medication_are_pinned_and_can_never_be_held():
    assert PINNED == frozenset({"safety", "medication"})
    for pinned in PINNED:
        with pytest.raises(ValueError):
            classify_change(EditHolds("sick", frozenset({"proactive", pinned})), DEFAULT_PROFILES)


@pytest.mark.parametrize(
    ("mode", "cause", "arms"),
    [
        ("travel", "owner_declared", True),
        ("travel", "owner_confirmed", True),
        ("travel", "inferred", False),
        ("holiday", "owner_declared", False),
        ("house_unattended", "owner_declared", False),
    ],
)
def test_only_owner_declared_or_confirmed_travel_arms_the_absence_plan(mode, cause, arms):
    assert arms_absence(ModeEntry(mode, NOW, None, cause)) is arms


@pytest.mark.parametrize(
    ("wake", "expected"),
    [
        (None, time(9, 30)),
        (time(5, 10), time(6, 0)),
        (time(6, 0), time(6, 0)),
        (time(7, 45), time(7, 45)),
        (time(9, 30), time(9, 30)),
        (time(11, 0), time(9, 30)),
    ],
)
def test_the_wake_brief_is_never_before_six_and_falls_back_at_half_nine(wake, expected):
    assert wake_brief_time(wake) == expected


# -- tighten or loosen -----------------------------------------------------------------


def _profiles(**overrides):
    return {**DEFAULT_PROFILES, **overrides}


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (Enter("focus", until=NOW + timedelta(hours=2), cause="owner_declared"), "tighten"),
        (Exit("focus"), "loosen"),
        (EditHolds("sick", frozenset({"proactive"})), "none"),
        (EditHolds("focus", frozenset({"proactive"})), "tighten"),
        (EditHolds("sick", frozenset()), "loosen"),
        (EditBreakThrough("asleep", frozenset({"contact:partner"})), "loosen"),
        # The default duration is open-ended, so any finite duration is shorter.
        (EditDuration("focus", timedelta(hours=3)), "loosen"),
        (EditDuration("focus", None), "none"),
    ],
)
def test_each_change_is_classified_exactly(change, expected):
    assert classify_change(change, DEFAULT_PROFILES) == expected


def test_a_mixed_edit_counts_as_loosening():
    profiles = _profiles(
        focus=DEFAULT_PROFILES["focus"].__class__(
            "focus", timedelta(hours=2), frozenset({"proactive"}), frozenset({"contact:a"})
        )
    )
    # Remove one held class while adding nothing else: loosen.
    assert classify_change(EditHolds("focus", frozenset()), profiles) == "loosen"
    # Drop one sender and add another: one more sender can break through, so loosen.
    assert (
        classify_change(EditBreakThrough("focus", frozenset({"contact:b"})), profiles) == "loosen"
    )
    # Only removing senders tightens.
    assert classify_change(EditBreakThrough("focus", frozenset()), profiles) == "tighten"
    # Shorter is looser; open-ended is the longest.
    assert classify_change(EditDuration("focus", timedelta(hours=1)), profiles) == "loosen"
    assert classify_change(EditDuration("focus", None), profiles) == "tighten"


def test_an_inferred_entry_is_refused_no_automatic_entry_rule():
    with pytest.raises(ValueError):
        classify_change(Enter("asleep", until=None, cause="inferred"), DEFAULT_PROFILES)


# -- step-up on loosening only ---------------------------------------------------------


class _Conn:
    def __init__(self, state="admitted"):
        self.state = state
        self.calls: list[tuple] = []

    async def fetchval(self, query, *args):
        self.calls.append((query, args))
        return {"state": self.state}


def _binding(change):
    return mode_binding(
        change,
        DEFAULT_PROFILES,
        owner_action_id=ACTION,
        expected_version=4,
        namespace=NAMESPACE,
    )


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setattr(flags, "MODE_CONTROLS_LIVE", True)


@pytest.fixture
def proof():
    token = dashboard_stepup_proof.set(DashboardStepUpProof("a" * 64, "n" * 43))
    yield
    dashboard_stepup_proof.reset(token)


async def test_mode_controls_are_dark():
    assert flags.MODE_CONTROLS_LIVE is False
    with pytest.raises(ModeControlsDark):
        await authorize_mode_change(_Conn(), Exit("focus"), DEFAULT_PROFILES, binding=None)


async def test_a_tightening_passes_without_any_step_up(live):
    conn = _Conn()
    change = Enter("focus", until=NOW + timedelta(hours=2), cause="owner_declared")
    assert await authorize_mode_change(conn, change, DEFAULT_PROFILES, binding=None) == "tighten"
    assert conn.calls == []


async def test_a_loosening_without_a_proof_is_refused_before_the_store(live):
    conn = _Conn()
    change = Exit("focus")
    with pytest.raises(ModeStepUpRequired):
        await authorize_mode_change(conn, change, DEFAULT_PROFILES, binding=_binding(change))
    assert conn.calls == []


async def test_a_loosening_spends_one_admitted_step_up(live, proof):
    conn = _Conn()
    change = EditBreakThrough("asleep", frozenset({"contact:partner"}))
    binding = _binding(change)
    verdict = await authorize_mode_change(conn, change, DEFAULT_PROFILES, binding=binding)
    assert verdict == "loosen"
    ((query, args),) = conn.calls
    assert "dashboard_stepup.consume" in query
    assert args[0] == "begin" and args[3] == MODE_LOOSEN_KIND
    assert args[4] == binding.target


async def test_a_refused_step_up_refuses_the_loosening(live, proof):
    change = Exit("sick")
    with pytest.raises(ModeStepUpRequired):
        await authorize_mode_change(
            _Conn(state="refused"), change, DEFAULT_PROFILES, binding=_binding(change)
        )


async def test_a_loosening_needs_its_own_binding(live, proof):
    change = Exit("sick")
    with pytest.raises(ModeStepUpRequired):
        await authorize_mode_change(_Conn(), change, DEFAULT_PROFILES, binding=None)
    other = _binding(Exit("focus"))
    with pytest.raises(ModeStepUpRequired):
        await authorize_mode_change(_Conn(), change, DEFAULT_PROFILES, binding=other)


def test_the_binding_is_exact_to_the_change_and_version():
    a = _binding(Exit("focus"))
    assert a.kind == MODE_LOOSEN_KIND and a.namespace == NAMESPACE
    assert a.target.startswith("focus:5:") and len(a.target) <= 128
    assert a == _binding(Exit("focus"))
    assert a.request_digest != _binding(Exit("sick")).request_digest
    later = mode_binding(
        Exit("focus"),
        DEFAULT_PROFILES,
        owner_action_id=ACTION,
        expected_version=5,
        namespace=NAMESPACE,
    )
    assert later.action_digest != a.action_digest


def test_a_tightening_has_no_binding():
    with pytest.raises(ValueError):
        _binding(Enter("focus", until=None, cause="owner_declared"))


# -- the owner.mode record ------------------------------------------------------------


def test_mode_event_is_a_valid_owner_mode_request():
    body = mode_event(
        frozenset({"travel", "focus"}),
        subject=OWNER,
        evidence_ref=REF,
        generation=2,
        at=NOW,
        until=NOW + timedelta(days=3),
    )
    payload = EventRequest(body).payload()
    assert payload["kind"] == "owner.mode" and payload["source"] == "context.mode"
    assert payload["typed_value_or_ref"] == ["focus", "travel"]
    assert payload["effective_span"]["end"].startswith("2026-05-07")


def test_mode_event_refuses_in_flight_or_unknown_ids():
    for modes in ({"in_flight"}, {"vacation"}):
        with pytest.raises(ValueError):
            mode_event(
                frozenset(modes),
                subject=OWNER,
                evidence_ref=REF,
                generation=0,
                at=NOW,
                until=NOW + timedelta(hours=1),
            )
