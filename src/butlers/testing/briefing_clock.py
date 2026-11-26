"""Pin the briefing jobs' clock to a day (bu-5ii4gj.5.1).

The jobs read the owner's day through ``butlers.jobs.briefing._now``. Tests that used to patch the
fixed-offset ``today_sgt`` pin that seam instead: noon in Singapore is the same calendar date in
the SGT fallback zone and in any zone within a few hours of it.
"""

from __future__ import annotations

from datetime import UTC, date, datetime


def sgt_noon(day: date) -> datetime:
    """12:00 on ``day`` in Singapore (04:00 UTC), as an aware instant."""
    return datetime(day.year, day.month, day.day, 4, 0, tzinfo=UTC)
