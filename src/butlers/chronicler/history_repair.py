"""Finite historical quantitative repair; no LLM or present-day health inference.

All writes use the caller's connection and transaction. The advisory key is
shared with migration030, source invalidation and the eventual publication fence.
"""

from __future__ import annotations

import time
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import asyncpg

from butlers.chronicler.rollups import _local_day_bounds_utc, compute_daily_lane_rollup
from butlers.chronicler.storage import upsert_checkpoint, upsert_daily_rollup

LOCK_NAMESPACE = 1128813128
LOCK_ID = 5471
MAX_BATCH_DAYS = 32
MAX_BATCH_SECONDS = 20

Connection = asyncpg.Connection | asyncpg.pool.PoolConnectionProxy


async def try_lock(conn: Connection) -> bool:
    return bool(
        await conn.fetchval(
            "SELECT pg_try_advisory_xact_lock($1::integer, $2::integer)", LOCK_NAMESPACE, LOCK_ID
        )
    )


async def replay_epoch(conn: Any) -> int | None:
    """An unacknowledged epoch forces a full replay, even after a crash."""
    row = await conn.fetchrow(
        "SELECT replay_epoch, replay_ack FROM chronicler.daily_repair_control WHERE singleton"
    )
    if row is None:
        raise RuntimeError("daily repair state missing")
    if row["replay_epoch"] > row["replay_ack"]:
        return int(row["replay_epoch"])
    return None


async def acknowledge_replay(conn: Connection, *, epoch: int, result: Any) -> None:
    """Checkpoint and acknowledgement are one commit, never two durable phases."""
    async with conn.transaction():
        if not await try_lock(conn):
            raise RuntimeError("daily repair busy; replay remains unacknowledged")
        await upsert_checkpoint(
            conn,
            result.source_name,
            watermark=result.watermark,
            watermark_id=result.watermark_id,
            success=True,
            rows_projected=result.rows_projected,
        )
        await conn.execute(
            "UPDATE chronicler.daily_repair_control SET replay_ack = $1 "
            "WHERE singleton AND replay_epoch = $1",
            epoch,
        )


async def _archive_day(
    conn: Connection, day: date, generation: int, start: datetime, end: datetime
) -> None:
    # Text has no ordinary read surface. Unknown provenance remains audit-only;
    # later source corrections can redact copied content without erasing identity.
    await conn.execute(
        """
        WITH deps AS (
            SELECT coalesce(array_agg(id), '{}'::uuid[]) AS ids
                   , coalesce(bool_or(privacy = 'restricted' OR tombstone_at IS NOT NULL
                       OR (retention_days IS NOT NULL AND
                           start_at + make_interval(days => retention_days) < now())),
                       FALSE) AS denied
            FROM chronicler.v_episodes_corrected
            WHERE (start_at < $4 AND (end_at IS NULL OR end_at > $3))
               OR (canonical_start_at < $4 AND
                   (canonical_end_at IS NULL OR canonical_end_at > $3))
        ), snapshots AS (
            SELECT 'rollup' AS kind, lane AS key, to_jsonb(r) AS content
            FROM chronicler.daily_rollups r WHERE local_date = $1
            UNION ALL
            SELECT 'outlier', flag_type, to_jsonb(f)
            FROM chronicler.daily_rollup_flags f
            WHERE local_date = $1 AND flag_type = 'lane_share_outlier'
        )
        INSERT INTO chronicler.daily_repair_archive
            (local_date, generation, row_kind, row_key, audit_hash, content,
             dependency_ids, text_access)
        SELECT $1, $2, kind, key,
               CASE WHEN deps.denied THEN NULL
                    ELSE encode(sha256(convert_to(content::text, 'UTF8')), 'hex') END,
               CASE WHEN deps.denied THEN NULL ELSE content END, deps.ids,
               CASE WHEN deps.denied THEN 'redacted' ELSE 'audit_only' END
        FROM snapshots CROSS JOIN deps
        ON CONFLICT DO NOTHING
        """,
        day,
        generation,
        start,
        end,
    )


async def rebuild_day(conn: Connection, state: Any) -> None:
    """One locked date: archive, stable totals, outlier disposition and cursor.

    Historical availability is unknown for pre-upgrade dates. Preserve every
    health-dependent/unmanaged flag and supersede only the old outlier claim.
    Memory reconciliation remains a separate pending work bit, never certified
    by quantitative completion.
    """
    day = state["local_date"]
    start, end = _local_day_bounds_utc(day, ZoneInfo(state["timezone"]))
    await _archive_day(conn, day, state["dirty_generation"], start, end)
    if state["totals_pending"]:
        rows = await conn.fetch(
            """SELECT source_name, episode_type, start_at, end_at, layer,
                      payload->>'trigger_source' AS trigger_source
               FROM chronicler.v_episodes_corrected
               WHERE start_at < $2 AND (end_at IS NULL OR end_at > $1)
                 AND tombstone_at IS NULL AND privacy IN ('normal', 'sensitive')""",
            start,
            end,
        )
        totals = compute_daily_lane_rollup(rows, day_start_utc=start, day_end_utc=end)
        for lane, values in totals.items():
            await upsert_daily_rollup(
                conn,
                local_date=day,
                lane=lane,
                seconds=round(values["seconds"]),
                episode_count=values["episode_count"],
                timezone=state["timezone"],
            )
        # No historical prompt fingerprint exists: affected old prose cannot be
        # proved current merely because its numeric totals happen to agree.
    if state["totals_pending"] or state["outlier_pending"]:
        await conn.execute(
            "UPDATE chronicler.daily_rollups SET narrative = NULL WHERE local_date = $1", day
        )
    if state["outlier_pending"]:
        await conn.execute(
            "DELETE FROM chronicler.daily_rollup_flags "
            "WHERE local_date = $1 AND flag_type = 'lane_share_outlier'",
            day,
        )
    await conn.execute(
        """UPDATE chronicler.daily_repair_days
           SET built_generation = dirty_generation, totals_pending = FALSE,
               outlier_pending = FALSE, historical_flags_status = 'unknown',
               prose_status = 'superseded', published_revision = published_revision + 1,
               updated_at = now()
           WHERE local_date = $1 AND dirty_generation = $2""",
        day,
        state["dirty_generation"],
    )


async def run_rebuild(
    pool: asyncpg.Pool,
    *,
    timezone: str = "Asia/Singapore",
    situation_timezone: str | None = None,
    now: datetime | None = None,
    max_days: int = MAX_BATCH_DAYS,
) -> dict[str, Any]:
    """A bounded driver; waiting/blocked/busy are explicit, never completion."""
    if (
        isinstance(max_days, bool)
        or not isinstance(max_days, int)
        or not 1 <= max_days <= MAX_BATCH_DAYS
    ):
        raise ValueError("max_days must be between1 and32")
    tz = ZoneInfo(timezone)  # No silent fallback.
    if situation_timezone is not None and ZoneInfo(situation_timezone).key != tz.key:
        return {"status": "blocked", "reason": "situation/rollup timezone mismatch"}
    closed_through = (now or datetime.now(UTC)).astimezone(tz).date() - timedelta(days=1)
    processed: list[str] = []
    started = time.monotonic()
    for _ in range(max_days):
        if time.monotonic() - started >= MAX_BATCH_SECONDS:
            break
        async with pool.acquire() as conn:
            async with conn.transaction():
                if not await try_lock(conn):
                    return {"status": "busy", "days_processed": processed}
                ctl = await conn.fetchrow(
                    "SELECT * FROM chronicler.daily_repair_control WHERE singleton FOR UPDATE"
                )
                if ctl is None:
                    raise RuntimeError("daily repair state missing")
                zones = await conn.fetch("SELECT DISTINCT timezone FROM chronicler.daily_rollups")
                if (
                    ctl["blocked_reason"]
                    or ctl["timezone"] != timezone
                    or any(r["timezone"] != timezone for r in zones)
                ):
                    return {"status": "blocked", "reason": "mixed or changed rollup timezone"}
                if ctl["replay_ack"] < ctl["replay_epoch"]:
                    return {"status": "waiting_replay", "days_processed": processed}
                await conn.execute(
                    "UPDATE chronicler.daily_repair_control SET closed_through = $1 "
                    "WHERE singleton",
                    closed_through,
                )
                state = await conn.fetchrow(
                    """SELECT * FROM chronicler.daily_repair_days
                       WHERE (totals_pending OR outlier_pending) AND local_date <= $1
                       ORDER BY totals_pending DESC, local_date LIMIT 1 FOR UPDATE""",
                    closed_through,
                )
                if state is None:
                    facts = await conn.fetchval(
                        "SELECT EXISTS (SELECT 1 FROM chronicler.daily_repair_days "
                        "WHERE facts_pending)"
                    )
                    return {
                        "status": "waiting_facts" if facts else "complete",
                        "days_processed": processed,
                    }
                await rebuild_day(conn, state)
                processed.append(state["local_date"].isoformat())
    return {"status": "pending", "days_processed": processed}
