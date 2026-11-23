"""The watch loop: claim a fire, tell the owner once, arm the next generation.

REQ-owner-watches-004.

An evaluator instance fires once per generation, so a watch that should notify every time needs
this loop. A tick walks the active watches and, for each whose instance has fired, claims the
fire intent, delivers one notice, and registers the next generation with the fired one's cursors
as its floor. Every step is idempotent: delivery is keyed ``owner_watch:<watch>:g<generation>``,
registering the same generation again is a no-op, and the rearm is a version CAS. A crash after
delivery and before the rearm replays to the same single notice and the same next generation; a
delivery failure leaves the watch fired and retried, never rearmed past a lost notice. An active
watch with no instance (a crash inside create) is repaired by registering it again.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from butlers.conditions import ConditionsUnavailable, FireIntent, Snapshot, State
from butlers.watches.model import Watch, WatchConflict, WatchState
from butlers.watches.service import WatchService

logger = logging.getLogger(__name__)

CONSUMER = "owner-watches"


@dataclass(frozen=True)
class WatchNotice:
    """What the owner is told. It carries ids and the delivery mode, never free text."""

    watch_id: str
    generation: int
    delivery: str
    dedup_key: str
    fired_at: datetime


Deliver = Callable[[WatchNotice], Awaitable[None]]
SnapshotFor = Callable[[datetime], Snapshot]


@dataclass
class TickReport:
    delivered: int = 0
    rearmed: int = 0
    expired: int = 0
    repaired: int = 0
    failed: int = 0


def notice_for(watch: Watch, intent: FireIntent) -> WatchNotice:
    return WatchNotice(
        watch_id=watch.id,
        generation=watch.generation,
        delivery=watch.delivery,
        dedup_key=f"owner_watch:{watch.id}:g{watch.generation}",
        fired_at=intent.fired_at,
    )


class WatchRunner:
    def __init__(
        self,
        service: WatchService,
        deliver: Deliver,
        snapshot_for: SnapshotFor,
        *,
        consumer: str = CONSUMER,
    ) -> None:
        self.service = service
        self.deliver = deliver
        self.snapshot_for = snapshot_for
        self.consumer = consumer

    async def tick(self, now: datetime) -> TickReport:
        report = TickReport(expired=len(await self.service.expire_due(now)))
        for watch in await self.service.store.list_all():
            if watch.state is WatchState.ACTIVE:
                try:
                    await self._advance(watch, now, report)
                except (ConditionsUnavailable, ConnectionError):
                    report.failed += 1  # retained: the next tick starts from the same state
                    logger.warning("owner_watch_unavailable watch=%s", watch.id)
        return report

    async def _advance(self, watch: Watch, now: datetime, report: TickReport) -> None:
        instance = await self.service.instance(watch)
        snapshot = self.snapshot_for(now)
        if instance is None:
            await self.service._register(watch, snapshot)
            report.repaired += 1
            return
        if instance.state is not State.FIRED:
            return
        intent = await self.service.evaluator.consume_fire(watch.instance_id, self.consumer)
        if intent is None:
            return  # another consumer holds this fire
        try:
            await self.deliver(notice_for(watch, intent))
        except Exception:
            report.failed += 1
            logger.warning("owner_watch_delivery_failed watch=%s", watch.id, exc_info=True)
            return
        report.delivered += 1
        try:
            if await self.service.rearm(watch, instance.cursors, snapshot) is not None:
                report.rearmed += 1
        except WatchConflict:
            pass  # a racing runner already armed the next generation
