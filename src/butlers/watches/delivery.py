"""Deliver a watch notice through the insight broker (RFC 0011), owner only (dark).

The notice is a fixed, content-free line: it names no sender, amount or text. Delivery is a
proposal to the broker, which still decides when and whether the owner is told. ``right_away``
raises the candidate's priority above the digest default; it does not interrupt quiet hours,
which only deterministic safety sentinels, break-through rules and owner-flagged obligations may
(bu-u33sb7). A broker refusal for the owner's verbosity is final and is not retried.
"""

from __future__ import annotations

import importlib
from datetime import UTC, datetime, timedelta
from typing import Any

from butlers.watches.runner import WatchNotice

CATEGORY = "owner_watch"
ORIGIN = "switchboard"
MESSAGE = "One of your watches was triggered. Open Watches to see which."
PRIORITY = {"digest": 40, "right_away": 70}
TTL = {"digest": timedelta(hours=36), "right_away": timedelta(hours=12)}


def _broker() -> Any:
    """The Switchboard insight broker, imported lazily (a roster tool package, not core)."""
    return importlib.import_module("butlers.tools.switchboard.insight.broker")


class InsightDelivery:
    def __init__(self, pool: Any, *, now: Any = lambda: datetime.now(UTC)) -> None:
        self._pool = pool
        self._now = now

    async def __call__(self, notice: WatchNotice) -> None:
        existing = await self._pool.fetchval(
            "SELECT id FROM insight_candidates WHERE dedup_key = $1 LIMIT 1", notice.dedup_key
        )
        if existing is not None:
            return  # a replayed delivery proposes nothing more
        result = await _broker().propose_insight_candidate(
            self._pool,
            origin_butler=ORIGIN,
            priority=PRIORITY[notice.delivery],
            category=CATEGORY,
            dedup_key=notice.dedup_key,
            message=MESSAGE,
            expires_at=self._now() + TTL[notice.delivery],
            metadata={"watch_id": notice.watch_id, "generation": notice.generation},
        )
        if result.get("status") not in ("accepted", "filtered", "duplicate"):
            raise RuntimeError(f"watch notice not queued: {result.get('status')}")
