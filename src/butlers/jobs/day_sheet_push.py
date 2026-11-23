"""Tomorrow's day sheet, pushed to the owner by a deterministic job (bu-5ii4gj.2 S3).

Runs on the general butler as a ``dispatch_mode="job"`` handler, so no model composes or rewrites
the message: the text is :func:`~butlers.core.temporal.day_sheet.render_day_sheet_text` over the
same read model the dashboard uses (REQ-module-calendar-036). An unreadable or stale read is sent
as such, never as a clear day.

Dark by default. The job does nothing unless ``DAY_SHEET_PUSH_ENABLED`` is truthy, so shipping it
changes no delivery.

Scope of the read: the general butler's own schema. Every calendar butler projects the owner's
provider calendars into its own schema, and this job (like the calendar context producer) reads the
general copy, so its freshness and unreadable states describe that schema alone. The dashboard
sheet fans out across every calendar butler.

Delivery dedup: the sheet digest keys the attention-ledger row, so a rerun of the same content
sends nothing. A changed day (new digest) sends again.

The ``day_sheet/pushed/<date>`` state key records what happened, so the ``eod-tomorrow-prep``
prompt can leave the calendar to this message instead of enumerating events itself.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from zoneinfo import ZoneInfo

import asyncpg

from butlers.api.db import DatabaseManager
from butlers.api.read_models.calendar_day_sheet import query_calendar_day_sheet
from butlers.core.attention_ledger import attention_event_recorded_since
from butlers.core.general_settings import load_general_settings
from butlers.core.state import state_set
from butlers.core.temporal.day_sheet import render_day_sheet_text
from butlers.jobs import owner_notify

logger = logging.getLogger(__name__)

JOB_NAME = "day_sheet_push"
ENABLED_ENV = "DAY_SHEET_PUSH_ENABLED"
PUSHED_STATE_PREFIX = "day_sheet/pushed/"
_DEDUP_WINDOW = timedelta(hours=36)
_TRUTHY = frozenset({"1", "true", "yes", "on"})


def push_enabled() -> bool:
    return os.environ.get(ENABLED_ENV, "").strip().lower() in _TRUTHY


@dataclass
class _SingleSchemaDb:
    """The slice of ``DatabaseManager`` the calendar read model uses, over one butler pool."""

    butler: str
    _pool: asyncpg.Pool

    @property
    def butler_names(self) -> list[str]:
        return [self.butler]

    def butlers_with_module(self, module_name: str) -> list[str] | None:
        return [self.butler]

    def pool(self, butler_name: str) -> asyncpg.Pool:
        if butler_name != self.butler:
            raise KeyError(butler_name)
        return self._pool

    async def fan_out_with_status(
        self,
        query: str,
        args: tuple[Any, ...] = (),
        butler_names: list[str] | None = None,
    ) -> tuple[dict[str, list[asyncpg.Record]], list[str]]:
        try:
            return {self.butler: list(await self._pool.fetch(query, *args))}, []
        except Exception:
            logger.warning("day sheet push: %s schema read failed", self.butler, exc_info=True)
            return {self.butler: []}, [self.butler]


async def run_day_sheet_push(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None = None,
    *,
    origin_butler: str = "general",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Send tomorrow's day sheet once. Returns a summary and never raises for a read failure."""
    del job_args
    if not push_enabled():
        return {"outcome": "disabled"}
    now = now or datetime.now(UTC)
    try:
        zone = ZoneInfo((await load_general_settings(pool))["timezone"])
    except Exception:
        logger.warning("day sheet push: owner timezone unreadable", exc_info=True)
        return {"outcome": "skipped", "reason": "timezone_unavailable"}

    tomorrow = now.astimezone(zone).date() + timedelta(days=1)
    db = cast(DatabaseManager, _SingleSchemaDb(origin_butler, pool))
    sheet = await query_calendar_day_sheet(db, local_date=tomorrow, zone=zone, now=now)
    message = f"Tomorrow Prep\n{render_day_sheet_text(sheet)}"
    dedup_key = f"{JOB_NAME}:{tomorrow.isoformat()}:{sheet.digest}"

    if await attention_event_recorded_since(pool, dedup_key=dedup_key, since=now - _DEDUP_WINDOW):
        return {"outcome": "skipped", "reason": "already_sent", "digest": sheet.digest}

    outcome = await owner_notify.send_owner_telegram(
        pool,
        origin_butler=origin_butler,
        message=message,
        log_context=JOB_NAME,
        dedup_key=dedup_key,
    )
    try:
        await state_set(
            pool,
            f"{PUSHED_STATE_PREFIX}{tomorrow.isoformat()}",
            {
                "outcome": outcome,
                "digest": sheet.digest,
                "freshness": sheet.freshness.state,
                "recorded_at": now.isoformat(),
            },
        )
    except Exception:
        logger.warning("day sheet push: could not record the push outcome", exc_info=True)
    return {"outcome": outcome, "digest": sheet.digest, "freshness": sheet.freshness.state}
