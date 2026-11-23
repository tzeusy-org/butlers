"""Tomorrow's day sheet, pushed to the owner by a deterministic API-process loop (bu-5ii4gj.2 S3).

The loop runs in the dashboard API process, beside ``calendar_overcommitment``, because the sheet
is the same projection the dashboard serves: :func:`query_calendar_day_sheet` over the real
``DatabaseManager`` fan-out. Telegram and the dashboard therefore share one read and one digest.
No model composes the message: the text is
:func:`~butlers.core.temporal.day_sheet.render_day_sheet_text`. An unreadable or stale read is
sent as such, never as a clear day (REQ-module-calendar-036).

Dark by default: nothing is read or sent unless ``DAY_SHEET_PUSH_ENABLED`` is truthy.

Timing: a tick inside ``[22:30, 23:30)`` owner-local pushes the owner-local *tomorrow*. A day is
pushed at most once successfully: a ``delivered`` marker (or a delivered ledger row for the same
digest) ends it. A failed send retries with the shared backoff; a send suppressed by quiet hours
or context is retried on a later tick without a ledger row per attempt.

Delivery goes through :mod:`butlers.jobs.owner_notify` with the general butler's pool and a
Switchboard client bound for the call, the boundary ``background.dispatch_scheduled_task`` binds
for butler jobs; the API process has no such client of its own.

Markers (general's state store): ``day_sheet/pushed/<date>`` and ``day_sheet/pushed/latest``, each
``{date, zone, outcome, digest, freshness, recorded_at}``. The ``eod-tomorrow-prep`` prompt reads
``latest`` (it carries the date, so the prompt needs no timezone arithmetic of its own).
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, time, timedelta
from typing import Any

import asyncpg

from butlers.api.db import DatabaseManager
from butlers.api.owner_time_bounds import owner_zoneinfo
from butlers.api.read_models.calendar_day_sheet import query_calendar_day_sheet
from butlers.core.attention_ledger import failed_attempts
from butlers.core.job_health import record_tick
from butlers.core.state import state_set
from butlers.core.temporal.day_sheet import render_day_sheet_text
from butlers.core.tool_call_capture import (
    reset_current_switchboard_client,
    set_current_switchboard_client,
)
from butlers.jobs import owner_notify
from butlers.jobs.document_deadlines import retry_backoff

logger = logging.getLogger(__name__)

JOB_NAME = "day_sheet_push"
ORIGIN_BUTLER = "general"
ENABLED_ENV = "DAY_SHEET_PUSH_ENABLED"
MARKER_PREFIX = "day_sheet/pushed/"
LATEST_MARKER = f"{MARKER_PREFIX}latest"
PUSH_WINDOW = (time(22, 30), time(23, 30))
DEFAULT_CHECK_INTERVAL_S = 300
_DEDUP_WINDOW = timedelta(hours=36)
_TRUTHY = frozenset({"1", "true", "yes", "on"})

ClientProvider = Callable[[], Awaitable[Any]]


def push_enabled() -> bool:
    return os.environ.get(ENABLED_ENV, "").strip().lower() in _TRUTHY


def tomorrow_if_due(now: datetime, zone: Any) -> Any:
    """Owner-local tomorrow when ``now`` is inside the push window, else ``None``."""
    local = now.astimezone(zone)
    if not PUSH_WINDOW[0] <= local.time() < PUSH_WINDOW[1]:
        return None
    return local.date() + timedelta(days=1)


async def _delivered_row_exists(pool: asyncpg.Pool, dedup_key: str, since: datetime) -> bool:
    try:
        row = await pool.fetchval(
            "SELECT 1 FROM public.attention_ledger "
            "WHERE dedup_key = $1 AND outcome = 'delivered' AND occurred_at >= $2 LIMIT 1",
            dedup_key,
            since,
        )
    except Exception:
        logger.warning("day sheet push: ledger read failed", exc_info=True)
        return False
    return row is not None


async def _write_markers(pool: asyncpg.Pool, day: Any, marker: dict[str, Any]) -> None:
    try:
        await state_set(pool, f"{MARKER_PREFIX}{day.isoformat()}", marker)
        await state_set(pool, LATEST_MARKER, marker)
    except Exception:
        logger.warning("day sheet push: could not record the push outcome", exc_info=True)


async def run_day_sheet_push(
    db: DatabaseManager,
    *,
    client_provider: ClientProvider | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """One tick. Sends tomorrow's sheet when due and not yet delivered. Never raises."""
    if not push_enabled():
        return {"outcome": "disabled"}
    now = now or datetime.now(UTC)
    try:
        pool = db.pool(ORIGIN_BUTLER)
    except KeyError:
        return {"outcome": "skipped", "reason": "no_pool"}
    zone = await owner_zoneinfo(pool)
    tomorrow = tomorrow_if_due(now, zone)
    if tomorrow is None:
        return {"outcome": "skipped", "reason": "outside_window"}

    sheet = await query_calendar_day_sheet(db, local_date=tomorrow, zone=zone, now=now)
    dedup_key = f"{JOB_NAME}:{tomorrow.isoformat()}:{sheet.digest}"
    if await _delivered_row_exists(pool, dedup_key, now - _DEDUP_WINDOW):
        return {"outcome": "skipped", "reason": "already_sent", "digest": sheet.digest}

    failures, since_last = await failed_attempts(pool, dedup_key=dedup_key)
    if since_last is not None and since_last < retry_backoff(failures):
        return {"outcome": "skipped", "reason": "retry_backoff"}
    reason = await owner_notify.check_owner_notify_suppression(pool, log_context=JOB_NAME)
    if reason is not None:
        return {"outcome": "skipped", "reason": reason}

    client = None
    if client_provider is not None:
        try:
            client = await client_provider()
        except Exception:
            logger.warning("day sheet push: Switchboard client unavailable", exc_info=True)
    token = set_current_switchboard_client(client)
    try:
        outcome = await owner_notify.send_owner_telegram(
            pool,
            origin_butler=ORIGIN_BUTLER,
            message=f"Tomorrow Prep\n{render_day_sheet_text(sheet)}",
            log_context=JOB_NAME,
            dedup_key=dedup_key,
        )
    finally:
        reset_current_switchboard_client(token)

    await _write_markers(
        pool,
        tomorrow,
        {
            "date": tomorrow.isoformat(),
            "zone": zone.key,
            "outcome": outcome,
            "digest": sheet.digest,
            "freshness": sheet.freshness.state,
            "recorded_at": now.isoformat(),
        },
    )
    return {"outcome": outcome, "digest": sheet.digest, "freshness": sheet.freshness.state}


async def run_day_sheet_push_loop(
    db: DatabaseManager,
    *,
    client_provider: ClientProvider | None = None,
    interval_s: float = DEFAULT_CHECK_INTERVAL_S,
) -> None:
    """Run :func:`run_day_sheet_push` every ``interval_s`` until cancelled; a bad tick is logged."""
    if interval_s <= 0:
        raise ValueError(f"interval_s must be a positive number, got {interval_s!r}")
    while True:
        await asyncio.sleep(interval_s)
        try:
            summary = await run_day_sheet_push(db, client_provider=client_provider)
            await record_tick(failed=summary["outcome"] == owner_notify.FAILED)
            logger.info("day_sheet_push: %s", summary)
        except asyncio.CancelledError:
            raise
        except Exception:
            await record_tick(failed=True)
            logger.exception("day_sheet_push: tick failed")
