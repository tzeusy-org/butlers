"""The shared household projection: one effective-occurrence rule (bu-5ii4gj.1 S3)."""

from datetime import UTC, datetime, timedelta

from butlers.api.read_models.calendar_workspace import _household_from_workspace_rows

NOW = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)
START = NOW + timedelta(minutes=10)
END = NOW + timedelta(minutes=30)


def row(**changes):
    return {
        "source_time_role": "household_member",
        "source_subject_label": "Synthetic Partner",
        "source_id": "test-source",
        "event_metadata": {"attendees": []},
        "instance_metadata": {},
        "instance_starts_at": START,
        "instance_ends_at": END,
        "event_status": "confirmed",
        "instance_status": "confirmed",
        **changes,
    }


def test_every_non_cancelled_occurrence_is_projected_tentative_included():
    confirmed = row(source_id="confirmed")
    tentative = row(source_id="tentative", instance_status="tentative", event_status="tentative")
    cancelled = row(source_id="cancelled", instance_status="cancelled")
    cancelled_series = row(source_id="cancelled-series", event_status="cancelled")
    actual = _household_from_workspace_rows(
        [confirmed, tentative, cancelled, cancelled_series], start=NOW, end=END
    )
    assert {event.source_id for event in actual} == {"confirmed", "tentative"}


def test_an_occurrence_owner_attendance_overrides_the_series_metadata():
    series = row(source_id="series", recurrence_rule="RRULE:FREQ=WEEKLY")
    owner_here = row(
        source_id="owner_here",
        recurrence_rule="RRULE:FREQ=WEEKLY",
        instance_metadata={"attendees": [{"self": True, "responseStatus": "accepted"}]},
    )
    actual = _household_from_workspace_rows([series, owner_here], start=NOW, end=END)
    assert [event.source_id for event in actual] == ["series"]
