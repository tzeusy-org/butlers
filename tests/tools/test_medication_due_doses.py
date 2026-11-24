"""Pure due-dose calculation behind the daily briefing's missed / due-later split (bu-ikq7vr)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from butlers.tools.health._medication_utils import DoseStatus, dose_status_today, is_as_needed

pytestmark = pytest.mark.unit

SGT = timezone(timedelta(hours=8))
# 2026-03-25 is a Wednesday.
_BRIEFING = datetime(2026, 3, 25, 6, 55, tzinfo=SGT)
_NOON = datetime(2026, 3, 25, 12, 0, tzinfo=SGT)


def _status(frequency, schedule, taken=0, now=_BRIEFING):
    return dose_status_today(
        frequency=frequency, schedule=schedule, now_local=now, doses_taken=taken
    )


def test_daily_morning_dose_already_due_is_missed_until_taken():
    assert _status("daily", ["06:00"]) == DoseStatus(missed=1, due_later=0)
    assert _status("daily", ["06:00"], taken=1) == DoseStatus()


def test_evening_dose_not_yet_due_is_due_later_not_missed():
    assert _status("daily", ["20:00"]) == DoseStatus(missed=0, due_later=1)
    assert _status("daily", ["evening"]) == DoseStatus(missed=0, due_later=1)
    assert _status("every evening", []) == DoseStatus(missed=0, due_later=1)
    assert _status("at bedtime", None) == DoseStatus(missed=0, due_later=1)


def test_twice_daily_splits_missed_and_due_later():
    schedule = ["06:00", "18:00"]
    assert _status("twice daily", schedule) == DoseStatus(missed=1, due_later=1)
    assert _status("twice daily", schedule, taken=1) == DoseStatus(missed=0, due_later=1)
    assert _status("twice daily", schedule, taken=2) == DoseStatus()
    assert _status("bid", schedule, now=_NOON) == DoseStatus(missed=1, due_later=1)


def test_weekly_is_due_only_on_the_named_weekday():
    assert _status("weekly", ["thursday"]) == DoseStatus()
    assert _status("weekly", ["wednesday", "08:00"]) == DoseStatus(missed=0, due_later=1)
    assert _status("weekly", ["wednesday", "06:00"]) == DoseStatus(missed=1, due_later=0)


@pytest.mark.parametrize("frequency", ["weekly", "once a week", "every other day"])
def test_phase_unknown_frequencies_are_never_reported(frequency):
    assert _status(frequency, []) == DoseStatus()


@pytest.mark.parametrize("frequency", ["as needed", "PRN", "Take as needed for pain", "prn"])
def test_as_needed_is_never_missed_or_due(frequency):
    assert is_as_needed(frequency)
    assert _status(frequency, ["06:00"]) == DoseStatus()


def test_unrecognised_or_empty_frequency_is_not_reported():
    assert _status("every full moon", ["06:00"]) == DoseStatus()
    assert _status("", None) == DoseStatus()
    assert _status(None, None) == DoseStatus()


_EVENING = datetime(2026, 3, 25, 21, 30, tzinfo=SGT)


def test_unscheduled_dose_is_due_later_until_the_window_passes_then_missed():
    # Schedule-less daily and weekday-only weekly share the undated path (bu-6cdsvf).
    for frequency, schedule in [("daily", None), ("weekly", ["wednesday"])]:
        assert _status(frequency, schedule) == DoseStatus(missed=0, due_later=1)
        assert _status(frequency, schedule, now=_NOON) == DoseStatus(missed=0, due_later=1)
        assert _status(frequency, schedule, now=_EVENING) == DoseStatus(missed=1, due_later=0)
        assert _status(frequency, schedule, taken=1) == DoseStatus()
        assert _status(frequency, schedule, taken=1, now=_EVENING) == DoseStatus()


@pytest.mark.parametrize("entry", ["8am", "8:00 AM", "08:00", "0800"])
def test_recognised_clock_formats_are_due_by_noon(entry):
    assert _status("daily", [entry], now=_NOON) == DoseStatus(missed=1, due_later=0)


@pytest.mark.parametrize("entry", ["0800x", "8"])
def test_ambiguous_entries_are_undated(entry):
    assert _status("daily", [entry], now=_NOON) == DoseStatus(missed=0, due_later=1)
