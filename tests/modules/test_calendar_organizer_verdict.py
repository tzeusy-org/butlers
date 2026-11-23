"""organizer_verdict: precedence, failure-to-unknown, lure badges and free-text purity."""

from __future__ import annotations

import inspect

import pytest

from butlers.modules.calendar.organizer_verdict import (
    QUARANTINE_VERDICTS,
    organizer_verdict,
)

_BASE = {
    "organizer_email": "sam@example.org",
    "own_email": "me@example.com",
    "identity_class": "none",
    "policy_blocked": False,
    "attendee_count": 2,
    "guests_hidden": False,
    "event_type": "default",
    "conference_host": None,
    "external_link_count": 0,
}


def _verdict(**override):
    return organizer_verdict(**{**_BASE, **override})


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        ({"organizer_email": "ME@example.com "}, "owner"),
        ({"identity_class": "known_entity"}, "known_entity"),
        ({"identity_class": "known_domain"}, "known_domain"),
        ({}, "first_contact"),
        ({"policy_blocked": True, "identity_class": "known_entity"}, "blocked"),
        ({"organizer_email": None}, "unknown"),
        ({"organizer_email": ""}, "unknown"),
    ],
)
def test_precedence(override, expected) -> None:
    assert _verdict(**override).verdict == expected


@pytest.mark.parametrize(
    "override",
    [
        {"identity_class": None},
        {"policy_blocked": None},
        {"identity_class": None, "policy_blocked": None},
    ],
)
def test_an_unreadable_lookup_is_unknown_never_known(override) -> None:
    assert _verdict(**{"identity_class": "known_entity", **override}).verdict == "unknown"


def test_the_owner_needs_no_lookup() -> None:
    result = _verdict(organizer_email="me@example.com", identity_class=None, policy_blocked=None)
    assert result.verdict == "owner"


def test_only_untrusted_verdicts_quarantine() -> None:
    assert QUARANTINE_VERDICTS == {"first_contact", "blocked", "unknown"}


def test_lure_badges_are_closed_and_derived_from_verified_fields() -> None:
    result = _verdict(
        attendee_count=30,
        guests_hidden=True,
        event_type="fromGmail",
        conference_host="join.evil.example",
        external_link_count=2,
    )
    assert set(result.lure_badges) == {
        "hidden_guest_list_large",
        "organizer_domain_mismatch_conference",
        "external_link_present",
        "from_gmail",
        "mass_invite",
    }
    assert _verdict(conference_host="meet.google.com").lure_badges == ()
    assert _verdict(conference_host="calls.example.org").lure_badges == ()


def test_no_free_text_can_reach_the_verdict() -> None:
    parameters = set(inspect.signature(organizer_verdict).parameters)
    assert parameters == set(_BASE)
    assert not parameters & {"title", "summary", "description", "location", "text", "body"}
