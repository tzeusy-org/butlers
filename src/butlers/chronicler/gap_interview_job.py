"""Day-close gap interview ASK service and its scheduled job (bu-whhll.12, bu-idc5mx.4).

``ask_gap_interview`` is the whole deterministic ask: evaluate the closed day, enforce the
max-one-per-day dedupe (KV state), apply the quiet-hours / delivery-preferences gate and
deliver the Telegram inline-button prompt. Both the ``chronicler_gap_interview`` MCP tool and
the ``chronicler_gap_interview`` job-mode schedule call it, so there is one ask call site.

``run_gap_interview_job`` is the 09:00 owner-local schedule. It computes yesterday in the
owner's general timezone (the zone the scheduler evaluates the cron in) and asks for it. It
spawns no session, calls no model and sends nothing beyond what the ask itself delivers.

The dedupe is check-then-deliver-then-mark: sequential, not an atomic claim. An owner-invoked
tool call racing the 09:00 job for the same day can still ask twice, as two sessions could
before this job existed.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import asyncpg

_JOB_NAME = "chronicler_gap_interview"


async def ask_gap_interview(
    pool: asyncpg.Pool,
    *,
    date_label: str,
    timezone: str = "UTC",
    credential_store: Any | None = None,
) -> dict[str, Any]:
    """Run the once-daily day-close gap interview ask for *date_label*.

    Returns a status dict: ``asked`` (delivered), ``already_asked``, ``no_gap``,
    ``deferred_quiet_hours``, ``delivery_failed``, or ``not_configured`` (no owner telegram
    chat / bot token).
    """
    import httpx

    from butlers.chronicler.editorial import WAKING_HOUR_END, WAKING_HOUR_START
    from butlers.chronicler.gap_interview import run_gap_interview
    from butlers.chronicler.gap_interview_telegram import build_telegram_transport
    from butlers.chronicler.storage import list_episodes
    from butlers.core.state import state_get, state_set
    from butlers.core.temporal.delivery import should_defer_notification
    from butlers.core.temporal.delivery_db import get_delivery_preferences

    asked_key = f"gap_interview:asked:{date_label}"

    async def already_asked() -> bool:
        return await state_get(pool, asked_key) is not None

    # Cheap short-circuit before resolving a token / building an HTTP client.
    if await already_asked():
        return {"status": "already_asked", "date": date_label}

    day = datetime.fromisoformat(date_label).date()
    tzinfo = ZoneInfo(timezone)
    start_at = datetime(day.year, day.month, day.day, tzinfo=tzinfo).astimezone(UTC)
    end_at = (datetime(day.year, day.month, day.day, tzinfo=tzinfo) + timedelta(days=1)).astimezone(
        UTC
    )

    async def mark_asked(decision, interview_id) -> None:
        # Persist the answer-side mapping (occupation episode + routine) the
        # callback resolver reads, then record the per-day dedupe marker.
        await state_set(
            pool,
            f"gap_interview:pending:{interview_id}",
            {
                "interview_id": interview_id,
                "local_date": date_label,
                "occupation_episode_id": (
                    str(decision.occupation_episode_id) if decision.occupation_episode_id else None
                ),
                "routine_id": str(decision.routine_id) if decision.routine_id else None,
                "answered": False,
            },
        )
        await state_set(
            pool, asked_key, {"interview_id": interview_id, "reasons": list(decision.reasons)}
        )

    async def delivery_allowed() -> bool:
        # Same quiet-hours / delivery-preferences gate notify() applies; the
        # gap-interview prompt is low priority (deferrable). Fail-open.
        try:
            prefs = await get_delivery_preferences(pool, "chronicler")
        except Exception:
            return True
        if prefs is None:
            return True
        tz_name = prefs.get("timezone", "UTC")
        try:
            gate_tz = ZoneInfo(tz_name)
        except Exception:
            gate_tz = ZoneInfo("UTC")
        now_local = datetime.now(UTC).astimezone(gate_tz).time()
        return not should_defer_notification(
            priority="low", current_time=now_local, prefs=prefs, channel="telegram"
        )

    episodes = await list_episodes(pool, start_from=start_at, start_to=end_at, limit=1000)
    episode_dicts = [asdict(ep) for ep in episodes]

    async with httpx.AsyncClient(timeout=30.0) as client:
        transport = await build_telegram_transport(
            pool, http_client=client, credential_store=credential_store
        )
        if transport is None:
            return {
                "status": "not_configured",
                "detail": "no owner telegram chat id or bot token configured",
            }
        # interview_id == date_label: unique per day (one prompt/day) and
        # compact inside the 64-byte telegram callback_data budget.
        return await run_gap_interview(
            episode_dicts,
            local_date=date_label,
            day_start_utc=start_at,
            day_end_utc=end_at,
            tz=tzinfo,
            interview_id=date_label,
            transport=transport,
            already_asked=already_asked,
            mark_asked=mark_asked,
            delivery_allowed=delivery_allowed,
            waking_hour_start=WAKING_HOUR_START,
            waking_hour_end=WAKING_HOUR_END,
        )


async def run_gap_interview_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None = None,
    *,
    now: datetime | None = None,
    credential_store: Any | None = None,
) -> dict[str, Any]:
    """Ask the gap interview for yesterday in the owner's general timezone."""
    from butlers.chronicler.day_close_writer import _compute_day_window
    from butlers.core.general_settings import resolve_general_timezone

    if job_args:
        raise RuntimeError(
            f"{_JOB_NAME} job takes no job_args; received unsupported keys: {sorted(job_args)}"
        )
    timezone = await resolve_general_timezone(pool)
    yesterday, _, _ = _compute_day_window(now or datetime.now(UTC), timezone)
    store_kwargs = {"credential_store": credential_store} if credential_store is not None else {}
    return await ask_gap_interview(
        pool, date_label=yesterday.isoformat(), timezone=timezone, **store_kwargs
    )
