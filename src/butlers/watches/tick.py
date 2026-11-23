"""The scheduled owner-watch tick (REQ-owner-watches-004/008; dark).

One tick sweeps closed watches, expires the unrenewed, repairs a watch with no condition,
claims each fired condition, delivers one notice and arms the next generation
(``WatchRunner``). It runs only when ``OWNER_WATCHES_LIVE`` is on, and only with a production
service and snapshot provider. Without a snapshot provider the tick is blind: it reports
``blind:no_snapshot_provider`` (result and warning log), fires nothing and changes nothing,
so a missing provider can never read as healthy or as a fire. The scheduled job passes a
``StateReporter`` (``butlers.watches.health``): the outcome lands in one Switchboard ``state`` row
that the capability-health collector reads to publish the ``owner_watches`` dependency.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any, Protocol

from butlers.watches.approval import PROVIDERS, Providers, sweep_card_state
from butlers.watches.delivery import InsightDelivery
from butlers.watches.health import (
    DEGRADED,
    NO_SNAPSHOT_PROVIDER,
    SOURCES_UNREADABLE,
    TICK_OK,
    UP,
)
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
    service = providers.service_for()
    reason = NO_SNAPSHOT_PROVIDER
    if service is not None and not await providers.prepare(moment):
        service, reason = None, SOURCES_UNREADABLE  # unreadable: blind, never a stale judgement
    snapshot = providers.snapshot_for(moment) if service is not None else None
    if service is None or snapshot is None:
        logger.warning("owner_watch_tick_blind reason=%s", BLIND)
        await _degrade(reporter, reason)
        return {"status": BLIND}
    await _sweep_cards(pool, moment)
    delivery = deliver_for(pool)
    runner = WatchRunner(service, delivery, lambda _now: snapshot)
    report = await runner.tick(moment)
    await _healthy(reporter)
    return {"status": "ok", **asdict(report)}


async def _sweep_cards(pool: Any, now: datetime) -> None:
    """Clear spent card state; a sweep failure never stops the tick (the next tick retries)."""
    try:
        await sweep_card_state(pool, now=now)
    except Exception:
        logger.warning("owner_watch_card_sweep_failed", exc_info=True)


async def _degrade(reporter: Reporter | None, reason: str) -> None:
    await _report(reporter, DEGRADED, reason)


async def _healthy(reporter: Reporter | None) -> None:
    await _report(reporter, UP, TICK_OK)


async def _report(reporter: Reporter | None, state: str, reason: str) -> None:
    if reporter is None:
        return
    try:
        await reporter.report(DEPENDENCY, state, reason)
    except Exception:
        logger.warning("owner_watch_tick_report_failed", exc_info=True)
