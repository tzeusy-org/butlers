"""Visit-brief adherence window: the first day owes a full day, so it counts a full day of doses."""

from __future__ import annotations

from datetime import UTC, datetime

from butlers.jobs import health_visit_brief as brief


class _Pool:
    """Answers the two adherence queries, honouring the dose query's time bounds."""

    def __init__(self, dose_times: list[datetime]) -> None:
        self._dose_times = dose_times

    async def fetch(self, sql: str, *args):
        if "took_dose" not in sql:
            return [
                {
                    "id": "med-1",
                    "subject": "owner",
                    "name": "Metformin",
                    "frequency": "daily",
                    "schedule": ["08:00"],
                    "tracked_since": datetime(2026, 9, 1, tzinfo=UTC),
                }
            ]
        lower, upper = args
        return [
            {"id": f"dose-{i}", "taken_at": at, "subject": "owner"}
            for i, at in enumerate(self._dose_times)
            if lower <= at <= upper
        ]


async def test_first_window_day_counts_doses_taken_before_the_window_start_time():
    start = datetime(2026, 10, 5, 14, tzinfo=UTC)  # mid-afternoon: the morning dose precedes it
    end = datetime(2026, 11, 3, 12, tzinfo=UTC)
    pool = _Pool([datetime(2026, 10, 5, 8, tzinfo=UTC)])
    figures = brief._Figures()

    lines = await brief._adherence_lines(pool, start, end, UTC, figures)  # pyright: ignore[reportArgumentType]

    # 30 days each owe one dose (Oct 5 .. Nov 3); the Oct 5 morning dose must be credited.
    assert any("1 dose logged against 30 scheduled, 29 missed" in line for line in lines), lines
