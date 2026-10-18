"""Health briefing adherence percentage and next-appointment highlights (bu-vaha16)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.jobs.briefing import run_health_briefing_contribution

SGT = timezone(timedelta(hours=8))
_DAY = date(2026, 3, 25)  # Wednesday
_NOW = datetime(2026, 3, 25, 6, 55, tzinfo=SGT)


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return _NOW.astimezone(tz) if tz else _NOW


def _med(name, frequency, schedule, taken=0):
    return {"name": name, "frequency": frequency, "schedule": schedule, "doses_taken": taken}


def _appointment(starts_at, title="Dentist", metadata=None):
    return {"content": title, "valid_at": starts_at, "metadata": metadata or {}}


async def _run(meds=(), appointment=None, timezone_setting=None):
    pool = MagicMock()
    pool.fetchval = AsyncMock(return_value=timezone_setting)
    pool.fetch = AsyncMock(side_effect=[list(meds), [{"cnt": 0}]])

    async def fetchrow(sql, *args):
        return appointment if "'appointment'" in sql else None

    pool.fetchrow = AsyncMock(side_effect=fetchrow)
    write = AsyncMock()
    with (
        patch("butlers.jobs.briefing.today_sgt", return_value=_DAY),
        patch("butlers.jobs.briefing.datetime", _FrozenDatetime),
        patch("butlers.jobs.briefing._write_contribution", write),
    ):
        result = await run_health_briefing_contribution(pool, None)
    return pool, result, write.call_args[0][1]


def _texts(envelope, category):
    return {h["text"]: h["priority"] for h in envelope["highlights"] if h["category"] == category}


async def test_adherence_counts_taken_against_doses_already_due():
    _, _, envelope = await _run(
        [
            _med("Missed pill", "daily", ["06:00"]),
            _med("Taken pill", "daily", ["06:00"], taken=1),
            _med("Evening pill", "daily", ["20:00"]),  # still ahead: not in the denominator
        ]
    )

    adherence = {t: p for t, p in _texts(envelope, "medication").items() if "adherence" in t}
    assert adherence == {"Medication adherence today: 50% (1 of 2 due doses taken)": "medium"}
    assert "Adherence 50%." in envelope["summary"]


async def test_a_surplus_of_doses_on_one_medication_does_not_offset_another():
    _, _, envelope = await _run(
        [_med("Over-taken", "daily", ["06:00"], taken=3), _med("Missed", "daily", ["06:00"])]
    )

    assert "Medication adherence today: 50% (1 of 2 due doses taken)" in _texts(
        envelope, "medication"
    )


async def test_fully_adherent_day_has_no_adherence_highlight_or_update():
    _, result, envelope = await _run([_med("Taken pill", "daily", ["06:00"], taken=1)])

    assert not [h for h in envelope["highlights"] if "adherence" in h["text"]]
    assert "Adherence" not in envelope["summary"]
    assert result["has_updates"] is False


async def test_nothing_scheduled_has_no_adherence_highlight():
    _, _, envelope = await _run(
        [
            _med("Painkiller", "as needed", []),
            _med("Phase unknown", "every other day", None),
            _med("Weekly shot", "weekly", ["thursday"]),  # off-weekday
            _med("Evening pill", "daily", ["20:00"]),  # nothing due yet
        ]
    )

    assert not [h for h in envelope["highlights"] if "adherence" in h["text"]]
    assert "Adherence" not in envelope["summary"]


@pytest.mark.parametrize(
    ("starts_at", "priority"),
    [
        (datetime(2026, 3, 26, 14, 30, tzinfo=SGT), "high"),  # tomorrow
        (datetime(2026, 3, 28, 9, 0, tzinfo=SGT), "medium"),  # in 3 days
        (datetime(2026, 4, 4, 9, 0, tzinfo=SGT), "low"),  # later
    ],
    ids=["tomorrow", "three-days", "later"],
)
async def test_next_appointment_highlight_priority_follows_how_soon(starts_at, priority):
    _, result, envelope = await _run(appointment=_appointment(starts_at.astimezone(UTC)))

    appointments = _texts(envelope, "appointment")
    assert list(appointments.values()) == [priority]
    assert result["has_updates"] is True


async def test_next_appointment_text_names_the_title_and_owner_local_time():
    _, _, envelope = await _run(
        appointment=_appointment(datetime(2026, 3, 26, 21, 30, tzinfo=UTC)),
        timezone_setting={"timezone": "America/Los_Angeles"},
    )

    # Owner-local "now" is Tue 24 Mar 15:55 PDT, so the appointment is two days out.
    assert _texts(envelope, "appointment") == {
        "Next appointment: Dentist on Thu 26 Mar 14:30": "medium"
    }


async def test_no_upcoming_appointment_emits_no_appointment_highlight():
    pool, result, envelope = await _run(appointment=None)

    assert _texts(envelope, "appointment") == {}
    assert result["has_updates"] is False
    assert envelope["summary"] == "No health updates today."


async def test_appointment_query_reads_active_health_facts_from_now_onwards():
    pool, _, _ = await _run(appointment=None)

    (sql, *args) = next(c[0] for c in pool.fetchrow.call_args_list if "'appointment'" in c[0][0])
    assert "FROM facts" in sql and "scope = 'health'" in sql and "validity = 'active'" in sql
    assert "ORDER BY valid_at ASC" in sql and "LIMIT 1" in sql
    assert "valid_at >= $1" in sql
    assert args == [_NOW.astimezone(UTC)]


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        ({"title": "Annual check-up"}, "Annual check-up"),
        ({"name": "Eye exam"}, "Eye exam"),
        ({"title": "Annual check-up", "name": "Eye exam"}, "Annual check-up"),
        ({}, "Dentist"),
    ],
    ids=["title", "name", "title-wins", "content-fallback"],
)
async def test_next_appointment_title_prefers_metadata_title_then_name_then_content(
    metadata, expected
):
    starts_at = datetime(2026, 3, 26, 14, 30, tzinfo=SGT).astimezone(UTC)
    _, _, envelope = await _run(appointment=_appointment(starts_at, metadata=metadata))

    assert list(_texts(envelope, "appointment")) == [
        f"Next appointment: {expected} on Thu 26 Mar 14:30"
    ]
