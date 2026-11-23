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

from butlers.watches.model import Watch
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
        # One proposal per watch fire, across processes: the check and the insert run on one
        # connection under a session advisory lock keyed by the dedup key, so two runners that
        # both saw "no candidate" cannot both insert (the broker has no unique key for this lane).
        async with self._pool.acquire() as connection:
            await connection.execute(
                "SELECT pg_advisory_lock(hashtextextended($1, 0))", notice.dedup_key
            )
            try:
                await self._propose_once(connection, notice)
            finally:
                await connection.execute(
                    "SELECT pg_advisory_unlock(hashtextextended($1, 0))", notice.dedup_key
                )

    async def _propose_once(self, connection: Any, notice: WatchNotice) -> None:
        existing = await connection.fetchval(
            "SELECT id FROM insight_candidates WHERE dedup_key = $1 LIMIT 1", notice.dedup_key
        )
        if existing is not None:
            return  # a replayed delivery proposes nothing more
        result = await _broker().propose_insight_candidate(
            connection,
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

    async def withdraw(self, watch: Watch) -> None:
        """Take back this watch's notices the owner has not been shown (pause, delete, expiry).

        Only a pending, unclaimed candidate is withdrawn; one already claimed for sending or
        delivered is left alone. Candidates are marked withdrawn, never deleted.
        """
        await self._pool.execute(
            """
            UPDATE insight_candidates
            SET status = 'withdrawn',
                metadata = COALESCE(metadata, '{}'::jsonb)
                           || jsonb_build_object('withdrawn_at', $2::text,
                                                 'withdrawn_reason', 'owner_watch_closed')
            WHERE origin_butler = $3 AND category = $4 AND dedup_key LIKE $1
              AND status = 'pending' AND claim_id IS NULL
            """,
            f"owner_watch:{watch.id}:g%",
            self._now().isoformat(),
            ORIGIN,
            CATEGORY,
        )
