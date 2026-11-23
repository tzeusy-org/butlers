"""The scheduled owner-watch tick (REQ-owner-watches-004/008; dark).

One tick sweeps closed watches, expires the unrenewed, repairs a watch with no condition,
claims each fired condition, delivers one notice and arms the next generation
(``WatchRunner``). It runs only when ``OWNER_WATCHES_LIVE`` is on, and only with a production
service and snapshot provider. Without a snapshot provider the tick is blind: it reports
``blind:no_snapshot_provider``, degrades the watch dependency, fires nothing and changes
nothing, so a missing provider can never read as healthy or as a fire.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any, Protocol

from butlers.watches.approval import PROVIDERS, Providers, sweep_card_state
from butlers.watches.delivery import InsightDelivery
from butlers.watches.runner import Deliver, WatchRunner

logger = logging.getLogger(__name__)

#: The one rollout flag. Off: the tick does nothing (REQ-owner-watches-008).
OWNER_WATCHES_LIVE = False
DEPENDENCY = "system.dep.owner_watches"
BLIND = "blind:no_snapshot_provider"


class Reporter(Protocol):
    async def report(self, dependency: str, state: str, reason: str) -> None: ...


async def run_tick(
    pool: Any,
    *,
    now: datetime | None = None,
    live: bool | None = None,
    providers: Providers = PROVIDERS,
    reporter: Reporter | None = None,
    deliver_for: Callable[[Any], Deliver] = InsightDelivery,
) -> dict[str, Any]:
    """Run one tick; return its report. Never raises for a dark or blind tick."""
    if not (OWNER_WATCHES_LIVE if live is None else live):
        return {"status": "dark"}
    moment = now or datetime.now(UTC)
    await sweep_card_state(pool, now=moment)
    service = providers.service_for()
    snapshot = providers.snapshot_for(moment) if service is not None else None
    if service is None or snapshot is None:
        logger.warning("owner_watch_tick_blind reason=%s", BLIND)
        await _degrade(reporter, "no_snapshot_provider")
        return {"status": BLIND}
    delivery = deliver_for(pool)
    runner = WatchRunner(service, delivery, lambda _now: snapshot)
    report = await runner.tick(moment)
    await _healthy(reporter)
    return {"status": "ok", **asdict(report)}


async def _degrade(reporter: Reporter | None, reason: str) -> None:
    if reporter is not None:
        await reporter.report(DEPENDENCY, "degraded", reason)


async def _healthy(reporter: Reporter | None) -> None:
    if reporter is not None:
        await reporter.report(DEPENDENCY, "up", "tick_ok")
