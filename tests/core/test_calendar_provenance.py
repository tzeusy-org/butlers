"""Pure calendar provenance eligibility tests (bu-kqnum.9.5)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from butlers.core.temporal.calendar_provenance import (
    is_calendar_analysis_candidate,
    is_explicit_butler_generated,
    is_legacy_all_day_like,
    is_owner_attending,
    is_quarantined_invitation,
    is_status_event,
    is_transparent,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("event_type", "expected"),
    [
        ("outOfOffice", True),
        ("workingLocation", True),
        # Case-sensitive provider values; anything else is an ordinary event.
        ("outofoffice", False),
        ("focusTime", False),
        ("default", False),
        ("someFutureType", False),
        (None, False),
        (7, False),
    ],
)
def test_only_the_provider_status_types_are_status_events(event_type, expected) -> None:
    assert is_status_event(event_type) is expected


def test_explicit_butler_generated_marker_is_narrow_and_tolerates_malformed_metadata() -> None:
    assert is_explicit_butler_generated({"butler_generated": True}) is True
    assert is_explicit_butler_generated({"butler_generated": " true "}) is True
    assert is_explicit_butler_generated({"butler_generated": False}) is False
    assert is_explicit_butler_generated({"source_kind": "internal_scheduler"}) is False
    assert is_explicit_butler_generated("not-an-object") is False
    assert is_explicit_butler_generated(None) is False


def test_legacy_locally_midnight_aligned_event_is_non_meeting_only_with_valid_timezone() -> None:
    singapore = ZoneInfo("Asia/Singapore")
    starts_at = datetime(2026, 7, 1, 0, 0, tzinfo=singapore)
    ends_at = starts_at + timedelta(days=1)

    assert (
        is_legacy_all_day_like(
            all_day=False,
            starts_at=starts_at,
            ends_at=ends_at,
            timezone="Asia/Singapore",
        )
        is True
    )
    assert (
        is_legacy_all_day_like(
            all_day=False,
            starts_at=starts_at,
            ends_at=ends_at,
            timezone="not/a-timezone",
        )
        is False
    )


@pytest.mark.parametrize(
    ("metadata", "timezone", "expected"),
    [
        ({"butler_generated": True}, "UTC", False),
        ({"butler_generated": False}, "UTC", True),
        ("malformed-metadata", "UTC", True),
        ({}, "not/a-timezone", True),
    ],
)
def test_calendar_analysis_candidate_preserves_timed_human_events_on_malformed_input(
    metadata: object,
    timezone: str,
    expected: bool,
) -> None:
    starts_at = datetime(2026, 7, 1, 9, 0, tzinfo=UTC)
    ends_at = starts_at + timedelta(hours=1)

    assert (
        is_calendar_analysis_candidate(
            metadata=metadata,
            all_day=False,
            starts_at=starts_at,
            ends_at=ends_at,
            timezone=timezone,
        )
        is expected
    )


@pytest.mark.parametrize("key", ["response_status", "responseStatus"])
def test_owner_declined_is_not_attending_for_either_key_spelling(key: str) -> None:
    metadata = {"attendees": [{"self": True, key: "declined"}]}
    assert is_owner_attending(metadata) is False


@pytest.mark.parametrize("status", ["accepted", "tentative", "needs_action", "needsAction"])
def test_owner_non_declined_statuses_are_attending(status: str) -> None:
    metadata = {
        "organizer_verdict": "known_entity",  # an unanswered invitation needs a trusted organizer
        "attendees": [{"self": True, "response_status": status}],
    }
    assert is_owner_attending(metadata) is True


def test_only_the_self_attendee_decides_attendance_and_malformed_metadata_is_attending() -> None:
    other = {"attendees": [{"email": "g@x.io", "response_status": "declined"}]}
    assert is_owner_attending(other) is True
    assert is_owner_attending({"attendees": "nope"}) is True
    assert is_owner_attending(None) is True
    assert (
        is_owner_attending('{"attendees": [{"self": true, "responseStatus": "declined"}]}') is False
    )


def test_transparency_transparent_is_free_time_everything_else_is_busy() -> None:
    assert is_transparent({"transparency": "transparent"}) is True
    assert is_transparent({"transparency": " Transparent "}) is True
    assert is_transparent({"transparency": "opaque"}) is False
    assert is_transparent({}) is False
    assert is_transparent(None) is False


def _invite(status: str = "needsAction", **extra: object) -> dict[str, object]:
    return {
        "organizer": "sam@example.org",
        "attendees": [
            {"email": "me@example.com", "self": True, "response_status": status},
            {"email": "sam@example.org", "organizer": True, "response_status": "accepted"},
        ],
        **extra,
    }


@pytest.mark.parametrize("verdict", ["first_contact", "blocked", "unknown", "bogus", None])
def test_an_unanswered_invitation_from_an_untrusted_or_unstamped_organizer_is_quarantined(
    verdict: str | None,
) -> None:
    metadata = _invite(**({} if verdict is None else {"organizer_verdict": verdict}))
    assert is_quarantined_invitation(metadata) is True
    assert is_owner_attending(metadata) is False


@pytest.mark.parametrize("verdict", ["owner", "known_entity", "known_domain"])
def test_a_trusted_organizer_is_never_quarantined(verdict: str) -> None:
    assert is_quarantined_invitation(_invite(organizer_verdict=verdict)) is False


@pytest.mark.parametrize("status", ["accepted", "tentative", "declined"])
def test_the_owners_own_answer_wins_over_a_blocked_verdict(status: str) -> None:
    assert is_quarantined_invitation(_invite(status, organizer_verdict="blocked")) is False


def test_the_owners_own_events_and_butler_events_are_never_quarantined() -> None:
    mine = {
        "organizer": "me@example.com",
        "attendees": [{"email": "me@example.com", "self": True, "response_status": "needsAction"}],
    }
    assert is_quarantined_invitation(mine) is False
    assert is_quarantined_invitation(_invite(butler_generated=True)) is False
    organizer_flag = {"attendees": [{"self": True, "organizer": True}]}
    assert is_quarantined_invitation(organizer_flag) is False
    assert is_quarantined_invitation({"attendees": [{"email": "x@y.test"}]}) is False  # not invited
    assert is_quarantined_invitation(None) is False
    assert is_quarantined_invitation("not json") is False
    assert is_quarantined_invitation({"attendees": "nope"}) is False
