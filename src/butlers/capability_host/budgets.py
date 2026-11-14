"""In-process budgets keyed per client (RFC 0039 D15). One replica, so memory is the store."""

from __future__ import annotations

import math
from dataclasses import dataclass

from butlers.capability_host.config import BudgetLimits

DAY = 86_400


@dataclass
class _Bucket:
    """A token bucket holding ``capacity`` tokens that refills ``capacity`` per minute."""

    capacity: int
    tokens: float
    updated: float

    def wait(self, now: float) -> float:
        self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.capacity / 60)
        self.updated = now
        return 0.0 if self.tokens >= 1 else (1 - self.tokens) * 60 / self.capacity


class Budgets:
    def __init__(self, limits: BudgetLimits):
        self.limits = limits
        self._buckets: dict[tuple, _Bucket] = {}
        self._daily: dict[tuple, int] = {}

    def _bucket(self, key: tuple, capacity: int, now: float) -> _Bucket:
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = self._buckets[key] = _Bucket(capacity, float(capacity), now)
        return bucket

    def _day(self, now: float) -> int:
        day = int(now // DAY)
        if any(key[0] != day for key in self._daily):
            self._daily = {k: v for k, v in self._daily.items() if k[0] == day}
        return day

    def admit(self, sub: str, cid: str, tool: str, now: float) -> int | None:
        """Spend one call, or return ``retry_after_s`` (seconds, at least 1) without spending."""
        limits = self.limits
        buckets = [
            self._bucket(("server",), limits.server_per_minute, now),
            self._bucket(("client", sub, cid), limits.per_client_per_minute, now),
        ]
        day = self._day(now)
        until_tomorrow = (day + 1) * DAY - now
        waits = []
        if tool in limits.heavy_tools:
            buckets.append(self._bucket(("tool", sub, cid, tool), limits.heavy_per_minute, now))
            if self._daily.get((day, "rows", sub, cid, tool), 0) >= limits.heavy_rows_per_day:
                waits.append(until_tomorrow)
        if tool == "propose_fact":
            if self._daily.get((day, "proposals", sub, cid), 0) >= limits.proposals_per_day:
                waits.append(until_tomorrow)
        waits += [bucket.wait(now) for bucket in buckets]
        worst = max(waits)
        if worst > 0:
            return max(1, math.ceil(worst))
        for bucket in buckets:
            bucket.tokens -= 1
        if tool == "propose_fact":
            key = (day, "proposals", sub, cid)
            self._daily[key] = self._daily.get(key, 0) + 1
        return None

    def record_rows(self, sub: str, cid: str, tool: str, rows: int, now: float) -> None:
        if tool in self.limits.heavy_tools and rows:
            key = (self._day(now), "rows", sub, cid, tool)
            self._daily[key] = self._daily.get(key, 0) + rows
