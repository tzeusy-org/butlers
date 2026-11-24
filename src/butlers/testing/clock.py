"""Independent deterministic clocks injected at existing test seams."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta


class FakeClock:
    """An aware UTC instant and monotonic seconds advanced by the same duration.

    The default origin is fixed, never a live read. This helper does not patch
    event-loop clocks, timeouts or PostgreSQL time.
    """

    def __init__(self, now: datetime | None = None, *, monotonic: float = 0.0) -> None:
        now = now if now is not None else datetime(2000, 1, 1, tzinfo=UTC)
        if now.utcoffset() is None:
            raise ValueError("FakeClock requires an aware datetime")
        if not math.isfinite(monotonic):
            raise ValueError("FakeClock requires finite monotonic seconds")
        self._now = now.astimezone(UTC)
        self._monotonic = float(monotonic)

    def now(self) -> datetime:
        return self._now

    def monotonic(self) -> float:
        return self._monotonic

    def advance(self, seconds: float | timedelta) -> None:
        seconds = seconds.total_seconds() if isinstance(seconds, timedelta) else float(seconds)
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError("FakeClock cannot advance by negative or nonfinite seconds")
        next_now = self._now + timedelta(seconds=seconds)
        self._now = next_now
        self._monotonic += seconds
