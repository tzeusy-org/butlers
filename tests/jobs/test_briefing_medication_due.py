"""Health briefing reports only genuinely overdue doses as missed (bu-ikq7vr)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from butlers.jobs.briefing import run_health_briefing_contribution

SGT = timezone(timedelta(hours=8))
_DAY = date(2026, 3, 25)  # Wednesday


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 3, 25, 6, 55, tzinfo=tz or SGT)


async def _run(rows):
    pool = MagicMock()
    pool.fetch = AsyncMock(side_effect=[rows, [{"cnt": 0}]])
    pool.fetchrow = AsyncMock(return_value=None)
    write = AsyncMock()
    with (
        patch("butlers.jobs.briefing.today_sgt", return_value=_DAY),
        patch("butlers.jobs.briefing.datetime", _FrozenDatetime),
        patch("butlers.jobs.briefing._write_contribution", write),
    ):
        result = await run_health_briefing_contribution(pool, None)
    return result, write.call_args[0][1]


def _med(name, frequency, schedule, taken=0):
    return {"name": name, "frequency": frequency, "schedule": schedule, "doses_taken": taken}


async def test_only_overdue_doses_are_missed_and_later_ones_are_low_priority():
    result, envelope = await _run(
        [
            _med("Morning pill", "daily", ["06:00"]),
            _med("Evening pill", "daily", ["20:00"]),
            _med("Weekly shot", "weekly", ["thursday"]),
            _med("Painkiller", "as needed", []),
            _med("Taken pill", "daily", ["06:00"], taken=1),
        ]
    )

    assert result["missed_doses"] == 1
    texts = {h["text"]: h["priority"] for h in envelope["highlights"]}
    assert texts == {
        "Missed dose(s) today: Morning pill": "high",
        "Doses due later today: Evening pill": "low",
    }
    assert "Missed 1 dose(s) today." in envelope["summary"]


async def test_no_false_alarm_when_nothing_is_overdue():
    result, envelope = await _run(
        [_med("Evening pill", "daily", ["evening"]), _med("PRN", "prn", None)]
    )
    assert result["missed_doses"] == 0
    assert all("Missed" not in h["text"] for h in envelope["highlights"])
