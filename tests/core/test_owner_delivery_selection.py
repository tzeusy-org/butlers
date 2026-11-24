"""M7 deterministic surface admissibility (REQ-owner-delivery-006)."""

from datetime import UTC, datetime, timedelta

import pytest

from butlers.core import owner_delivery as od
from butlers.core.owner_delivery_selection import SurfaceEvidence, select

NOW = datetime(2026, 11, 24, tzinfo=UTC)
DESK = SurfaceEvidence("dashboard", "dashboard_focus", NOW, NOW + timedelta(seconds=90))
TELEGRAM = SurfaceEvidence("telegram", "owner_inbound", NOW, NOW + timedelta(minutes=10))


@pytest.mark.parametrize("kind", ["approval", "pending_ask", "decision_set"])
def test_desk_first_even_for_interrupts(kind):
    for urgency in (od.ROUTINE, od.Urgency("interrupt", "home_safety")):
        selected = select(
            kind=kind, urgency=urgency, sensitivity="ordinary", evidence=(DESK, TELEGRAM), now=NOW
        )
        assert (selected.primary, selected.fallback, selected.reason) == (
            "dashboard",
            "telegram",
            "desk_first",
        )


@pytest.mark.parametrize("sensitivity", ["sensitive", "unknown"])
def test_private_or_unclassified_content_never_leaves_for_telegram(sensitivity):
    selected = select(
        kind="approval",
        urgency=od.Urgency("interrupt", "owner_clause"),
        sensitivity=sensitivity,
        evidence=(TELEGRAM,),
        now=NOW,
    )
    assert (selected.primary, selected.fallback) == ("dashboard", None)
    control = select(
        kind="approval", urgency=od.ROUTINE, sensitivity="ordinary", evidence=(TELEGRAM,), now=NOW
    )
    assert control.primary == "telegram"


def test_stale_or_future_focus_never_infers_at_desk_or_ack():
    for moment in (NOW - timedelta(seconds=1), NOW + timedelta(seconds=90)):
        selected = select(
            kind="approval",
            urgency=od.ROUTINE,
            sensitivity="ordinary",
            evidence=(DESK,),
            now=moment,
        )
        assert (selected.primary, selected.fallback, selected.reason) == (
            "dashboard",
            None,
            "surface_liveness_unknown",
        )
    assert (
        select(
            kind="approval", urgency=od.ROUTINE, sensitivity="ordinary", evidence=(DESK,), now=NOW
        ).fallback
        == "telegram"
    )


@pytest.mark.parametrize("origin", ["telegram", "dashboard"])
def test_answers_keep_origin_even_when_another_surface_is_live(origin):
    selected = select(
        kind="goal_answer",
        urgency=od.ROUTINE,
        sensitivity="ordinary",
        evidence=(DESK, TELEGRAM),
        now=NOW,
        origin=origin,
    )
    assert (selected.primary, selected.fallback) == (origin, None)
    blocked = select(
        kind="goal_answer",
        urgency=od.ROUTINE,
        sensitivity="sensitive",
        evidence=(DESK,),
        now=NOW,
        origin="telegram",
    )
    assert blocked.primary is None


def test_unknown_liveness_has_no_provider_send_but_verified_interrupt_keeps_urgency():
    assert (
        select(
            kind="pending_ask", urgency=od.ROUTINE, sensitivity="ordinary", evidence=(), now=NOW
        ).primary
        == "dashboard"
    )
    urgency = od.Urgency("interrupt", "home_safety")
    selected = select(
        kind="pending_ask", urgency=urgency, sensitivity="ordinary", evidence=(), now=NOW
    )
    assert selected.primary == "telegram" and urgency.reason == "home_safety"


def test_held_posture_only_holds_and_never_rewrites_urgency():
    routine = select(
        kind="approval",
        urgency=od.ROUTINE,
        sensitivity="ordinary",
        evidence=(TELEGRAM,),
        now=NOW,
        posture="held",
    )
    assert routine.primary == "dashboard" and routine.fallback is None
    assert od.ROUTINE.cls == "routine"
    safety = od.Urgency("interrupt", "home_safety")
    assert (
        select(
            kind="approval",
            urgency=safety,
            sensitivity="ordinary",
            evidence=(TELEGRAM,),
            now=NOW,
            posture="held",
        ).primary
        == "telegram"
    )
