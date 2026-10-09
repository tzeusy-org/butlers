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
    is_transparent,
)

pytestmark = pytest.mark.unit


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
    assert is_owner_attending({"attendees": [{"self": True, "response_status": status}]}) is True


def test_only_the_self_attendee_decides_attendance_and_malformed_metadata_is_attending() -> None:
    other = {"attendees": [{"email": "g@x.io", "response_status": "declined"}]}
    assert is_owner_attending(other) is True
    assert is_owner_attending({"attendees": "nope"}) is True
    assert is_owner_attending(None) is True
    assert (
        is_owner_attending('{"attendees": [{"self": true, "responseStatus": "declined"}]}') is False
    )

    for key in ("response_status", "responseStatus"):
        for status in ("accepted", "tentative", "needsAction", "needs_action", None, 1):
            assert is_owner_attending({"attendees": [{"self": True, key: status}]}) is True
        assert is_owner_attending({"attendees": [{"self": False, key: "declined"}]}) is True


def test_transparency_transparent_is_free_time_everything_else_is_busy() -> None:
    assert is_transparent({"transparency": "transparent"}) is True
    assert is_transparent({"transparency": " Transparent "}) is True
    assert is_transparent({"transparency": "opaque"}) is False
    assert is_transparent({}) is False
    assert is_transparent(None) is False
