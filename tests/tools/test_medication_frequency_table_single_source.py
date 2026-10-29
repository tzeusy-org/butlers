"""One frequency table drives adherence and ``dose_status_today`` (bu-sbcebt)."""

from __future__ import annotations

from datetime import datetime

import pytest

from butlers.tools.health._medication_utils import (
    _FREQUENCY_DOSES_PER_DAY,
    _PHASE_UNKNOWN_FREQUENCIES,
    _WEEKLY_FREQUENCIES,
    DoseStatus,
    dose_status_today,
    frequency_to_doses_per_day,
)

MONDAY_NOON = datetime(2026, 10, 19, 12, 0)  # a Monday


@pytest.mark.parametrize("frequency", ["weekly", "once a week", "once weekly"])
def test_weekly_spellings_are_one_seventh_in_adherence(frequency: str) -> None:
    assert frequency_to_doses_per_day(frequency) == pytest.approx(1 / 7)
    assert frequency in _WEEKLY_FREQUENCIES


def test_once_weekly_is_weekly_in_dose_status_today() -> None:
    named = dose_status_today(
        frequency="Once Weekly", schedule=["monday"], now_local=MONDAY_NOON, doses_taken=0
    )
    other_day = dose_status_today(
        frequency="once weekly", schedule=["tuesday"], now_local=MONDAY_NOON, doses_taken=0
    )
    assert named == DoseStatus(missed=1, due_later=0)
    assert other_day == DoseStatus()


def test_every_other_day_is_phase_unknown_in_both() -> None:
    assert frequency_to_doses_per_day("every other day") == 0.5
    assert "every other day" in _PHASE_UNKNOWN_FREQUENCIES
    status = dose_status_today(
        frequency="every other day", schedule=["08:00"], now_local=MONDAY_NOON, doses_taken=0
    )
    assert status == DoseStatus()


def test_sets_are_derived_from_the_table_so_they_cannot_fork() -> None:
    sub_daily = {k for k, v in _FREQUENCY_DOSES_PER_DAY.items() if v < 1}
    assert sub_daily == _WEEKLY_FREQUENCIES | _PHASE_UNKNOWN_FREQUENCIES
    assert _WEEKLY_FREQUENCIES.isdisjoint(_PHASE_UNKNOWN_FREQUENCIES)
