"""Household context: which events are other people's, and that nothing of theirs leaks."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta

import pytest

from butlers.core.temporal import household_context as hc
from butlers.core.temporal.calendar_source_roles import ROLES, asserts_owner_time

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 11, 23, 14, 0, tzinfo=UTC)
_SELF = {"self": True, "response_status": "accepted"}


def _row(role: str, metadata=None, **extra):
    return {
        "time_role": role,
        "metadata": metadata or {},
        "starts_at": _T0,
        "ends_at": _T0 + timedelta(hours=1),
        "all_day": False,
        "source_id": "src-1",
        "subject_entity_id": "11111111-1111-1111-1111-111111111111",
        "subject_label": "Alex",
        **extra,
    }


@pytest.mark.parametrize("role", sorted(hc.HOUSEHOLD_ROLES))
def test_other_peoples_events_are_context(role) -> None:
    [event] = hc.household_events([_row(role)])
    assert event.role == role and event.subject_label == "Alex"


@pytest.mark.parametrize("role", sorted(set(ROLES) - hc.HOUSEHOLD_ROLES), ids=lambda r: f"no-{r}")
def test_every_other_role_produces_nothing(role) -> None:
    """unclassified counts as nothing; owner, butler, holidays and the rest are not context."""
    assert hc.household_events([_row(role)]) == []


def test_an_absent_or_unknown_role_is_unclassified_and_counts_as_nothing() -> None:
    assert hc.household_events([_row(None), _row("made-up")]) == []


def test_an_event_the_owner_attends_is_owner_time_and_never_context() -> None:
    attending = _row("household_member", {"attendees": [_SELF]})
    declined = _row(
        "household_member", {"attendees": [{"self": True, "response_status": "declined"}]}
    )
    assert asserts_owner_time("household_member", attending["metadata"])
    assert hc.household_events([attending]) == []
    # A declined invitation is neither owner time nor the other person's context to show.
    assert not asserts_owner_time("household_member", declined["metadata"])


@pytest.mark.parametrize("role", sorted(hc.HOUSEHOLD_ROLES))
@pytest.mark.parametrize(
    "metadata",
    [
        {},
        {"attendees": [_SELF]},
        {"attendees": [{"self": True, "response_status": "declined"}]},
        {"transparency": "transparent"},
        {"butler_generated": True},
    ],
)
def test_an_event_is_never_both_owner_time_and_context(role, metadata) -> None:
    owner = asserts_owner_time(role, metadata)
    context = bool(hc.household_events([_row(role, metadata)]))
    assert not (owner and context)


def test_free_and_generated_events_are_not_context() -> None:
    assert hc.household_events([_row("team", {"transparency": "transparent"})]) == []
    assert hc.household_events([_row("team", {"butler_generated": True})]) == []


def test_the_window_keeps_only_overlapping_events() -> None:
    rows = [
        _row("team"),
        _row("team", starts_at=_T0 + timedelta(days=2), ends_at=_T0 + timedelta(days=2, hours=1)),
    ]
    inside = hc.household_events(rows, start=_T0, end=_T0 + timedelta(hours=2))
    assert len(inside) == 1


def test_events_group_by_subject() -> None:
    rows = [
        _row("household_member"),
        _row("household_member", subject_entity_id="22222222-2222-2222-2222-222222222222"),
        _row("other_person", subject_entity_id=None, subject_label=None),
    ]
    grouped = hc.household_time(rows)
    assert set(grouped) == {
        "11111111-1111-1111-1111-111111111111",
        "22222222-2222-2222-2222-222222222222",
        None,
    }


def test_no_title_location_or_attendee_field_can_leave_household_context() -> None:
    """Opacity is structural: the event type has no such field, and the line carries no text."""
    fields = {f.name for f in dataclasses.fields(hc.HouseholdEvent)}
    assert fields == {
        "source_id",
        "role",
        "subject_entity_id",
        "subject_label",
        "starts_at",
        "ends_at",
        "all_day",
    }
    secret = {
        "title": "Therapy with Dr Synthetic",
        "location": "12 Synthetic Road",
        "description": "synthetic private note",
        "attendees": [{"email": "synthetic@example.invalid"}],
    }
    row = _row("household_member", secret)
    [event] = hc.household_events([row])
    rendered = repr(event) + hc.context_line(event) + repr(hc.household_time([row]))
    for value in ("Therapy", "Synthetic Road", "private note", "synthetic@example.invalid"):
        assert value not in rendered


def test_context_lines_name_the_subject_or_the_role_and_carry_time_only() -> None:
    [named] = hc.household_events([_row("household_member")])
    [anon] = hc.household_events([_row("other_person", subject_label=None)])
    assert hc.context_line(named) == "Also on Alex's calendar: 14:00-15:00"
    assert hc.context_line(anon) == "Also on another person's calendar: 14:00-15:00"
    allday = dataclasses.replace(named, all_day=True)
    assert hc.context_line(allday).endswith("all day")
    assert hc.context_line(named, timezone="Asia/Singapore").endswith("22:00-23:00")


def test_context_lines_are_capped() -> None:
    events = hc.household_events([_row("team") for _ in range(9)])
    assert len(hc.context_lines(events, limit=3)) == 3
