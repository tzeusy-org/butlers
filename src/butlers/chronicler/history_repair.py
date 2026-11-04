"""Finite historical quantitative repair; no LLM or present-day health inference.

All writes use the caller's connection and transaction. The advisory key is
shared with migration030, source invalidation and the eventual publication fence.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import asyncpg

from butlers.chronicler.rollups import _local_day_bounds_utc, compute_daily_lane_rollup
from butlers.chronicler.storage import upsert_checkpoint, upsert_daily_rollup

logger = logging.getLogger(__name__)

LOCK_NAMESPACE = 1128813128
LOCK_ID = 5471
MAX_BATCH_DAYS = 32
MAX_BATCH_SECONDS = 20
MAX_FACT_BASELINE_DAYS = 28
MAX_RETENTION_CURSOR_DAYS = 7
DEFAULT_RETENTION_CURSOR_TIMEOUT_MS = 5_000
MAX_RETENTION_CURSOR_TIMEOUT_MS = DEFAULT_RETENTION_CURSOR_TIMEOUT_MS

Connection = asyncpg.Connection | asyncpg.pool.PoolConnectionProxy


@dataclass(frozen=True)
class RepairDayVersion:
    """The exact repair state for one date, or absence of a repair row."""

    dirty_generation: int
    built_generation: int
    totals_pending: bool
    outlier_pending: bool
    facts_pending: bool


@dataclass(frozen=True)
class FactWriteToken:
    """Snapshot covering every day-close fact input before rollup reads begin."""

    day_date: date
    timezone: str
    baseline_lookback_days: int
    start_date: date
    end_date: date
    replay_epoch: int
    replay_ack: int
    control_timezone: str
    closed_through: date
    blocked_reason: str | None
    days: tuple[tuple[date, RepairDayVersion | None], ...]
    pending_retention: tuple[tuple[str, date, date], ...] = ()


class RepairAdmissionError(RuntimeError):
    """A day-close write cannot prove that its source generation is current."""


def _day_version(row: Any) -> RepairDayVersion:
    return RepairDayVersion(
        dirty_generation=int(row["dirty_generation"]),
        built_generation=int(row["built_generation"]),
        totals_pending=bool(row["totals_pending"]),
        outlier_pending=bool(row["outlier_pending"]),
        facts_pending=bool(row["facts_pending"]),
    )


async def _set_retention_cursor_statement_timeout(conn: Connection, timeout_ms: int) -> str:
    """Apply a numeric-ms cursor deadline without widening the caller's deadline."""
    if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int) or timeout_ms <= 0:
        raise ValueError("timeout_ms must be a positive integer")
    previous = await conn.fetchval("SELECT current_setting('statement_timeout')")
    applied = await conn.fetchval(
        """SELECT set_config(
               'statement_timeout',
               (CASE WHEN setting::bigint = 0 THEN $1::bigint
                     ELSE least(setting::bigint, $1::bigint) END)::text || 'ms',
               TRUE
           )
           FROM pg_catalog.pg_settings
           WHERE name = 'statement_timeout'""",
        timeout_ms,
    )
    if applied is None:
        raise RuntimeError("PostgreSQL statement_timeout setting is unavailable")
    return str(previous)


def _validate_fact_baseline(baseline_lookback_days: int) -> None:
    if (
        isinstance(baseline_lookback_days, bool)
        or not isinstance(baseline_lookback_days, int)
        or not 0 <= baseline_lookback_days <= MAX_FACT_BASELINE_DAYS
    ):
        raise ValueError(
            f"baseline_lookback_days must be between 0 and the {MAX_FACT_BASELINE_DAYS}-day "
            "invalidation horizon"
        )


async def try_shared_lock(conn: Connection) -> bool:
    """Admit fact/prose publication only while source invalidation is excluded."""
    return bool(
        await conn.fetchval(
            "SELECT pg_try_advisory_xact_lock_shared($1::integer, $2::integer)",
            LOCK_NAMESPACE,
            LOCK_ID,
        )
    )


async def _pending_retention_scopes(
    conn: Connection, *, start_date: date, end_date: date
) -> tuple[tuple[str, date, date], ...]:
    """Find exact expired-source scopes that overlap a fact/prose input window."""
    cursor_rows = await conn.fetch(
        """SELECT source_id, cursor_date, last_date
           FROM chronicler.daily_repair_retention_spans
           WHERE cursor_date <= $2 AND last_date >= $1
           ORDER BY source_id, span_number""",
        start_date,
        end_date,
    )
    expired_rows = await conn.fetch(
        """WITH control AS (
               SELECT timezone, closed_through FROM chronicler.daily_repair_control
               WHERE singleton
           ), eligible AS (
               SELECT source.id,
                      (source.start_at AT TIME ZONE control.timezone)::date AS raw_first,
                      CASE WHEN source.end_at IS NULL THEN control.closed_through
                           ELSE least(control.closed_through,
                               ((source.end_at - interval '1 microsecond')
                                   AT TIME ZONE control.timezone)::date) END AS raw_last,
                      (effective.start_at AT TIME ZONE control.timezone)::date AS effective_first,
                      CASE WHEN effective.end_at IS NULL THEN control.closed_through
                           ELSE least(control.closed_through,
                               ((effective.end_at - interval '1 microsecond')
                                   AT TIME ZONE control.timezone)::date) END AS effective_last
               FROM chronicler.episodes AS source
               JOIN chronicler.v_episodes_corrected AS effective ON effective.id = source.id
               CROSS JOIN control
               WHERE source.retention_days IS NOT NULL
                 AND source.start_at + make_interval(days => source.retention_days) < now()
                 AND source.tombstone_at IS NULL
                 AND source.privacy IS DISTINCT FROM 'restricted'
                 AND effective.tombstone_at IS NULL
                 AND effective.privacy IS DISTINCT FROM 'restricted'
                 AND NOT EXISTS (
                     SELECT 1 FROM chronicler.daily_repair_source_redactions AS redaction
                     WHERE redaction.source_id = source.id
                 )
           ), spans AS (
               SELECT id, raw_first AS first_date, raw_last AS last_date FROM eligible
               UNION ALL
               SELECT id, effective_first, effective_last FROM eligible
           )
           SELECT id, first_date, last_date FROM spans
           WHERE first_date IS NOT NULL AND last_date IS NOT NULL
             AND first_date <= last_date AND first_date <= $2
             AND last_date >= $1
           ORDER BY id, first_date, last_date""",
        start_date,
        end_date,
    )
    scopes = {(str(row["source_id"]), row["cursor_date"], row["last_date"]) for row in cursor_rows}
    scopes.update((str(row["id"]), row["first_date"], row["last_date"]) for row in expired_rows)
    return tuple(sorted(scopes))


async def pending_numeric_repair_dates(
    conn: Connection, *, start_date: date, end_date: date
) -> set[date]:
    """Return dates whose numeric rollup must not be presented as current.

    The caller holds the shared repair advisory lock in the transaction that
    reads the rollup rows, so a source invalidation cannot race this result.
    """
    if end_date < start_date:
        return set()
    rows = await conn.fetch(
        """SELECT local_date FROM chronicler.daily_repair_days
           WHERE local_date BETWEEN $1 AND $2
             AND (totals_pending OR outlier_pending)""",
        start_date,
        end_date,
    )
    pending = {row["local_date"] for row in rows}
    for _, first, last in await _pending_retention_scopes(
        conn, start_date=start_date, end_date=end_date
    ):
        current = max(first, start_date)
        stop = min(last, end_date)
        while current <= stop:
            pending.add(current)
            current += timedelta(days=1)
    return pending


async def _read_fact_write_token(
    conn: Connection,
    *,
    day_date: date,
    timezone: str,
    baseline_lookback_days: int,
) -> FactWriteToken:
    start_date = day_date - timedelta(days=baseline_lookback_days)
    control = await conn.fetchrow(
        """SELECT replay_epoch, replay_ack, timezone, closed_through, blocked_reason
           FROM chronicler.daily_repair_control WHERE singleton"""
    )
    if control is None:
        raise RepairAdmissionError("daily repair control is missing")
    rows = await conn.fetch(
        """SELECT local_date, dirty_generation, built_generation, totals_pending,
                  outlier_pending, facts_pending
           FROM chronicler.daily_repair_days
           WHERE local_date BETWEEN $1 AND $2 ORDER BY local_date""",
        start_date,
        day_date,
    )
    found = {row["local_date"]: _day_version(row) for row in rows}
    days = tuple(
        (start_date + timedelta(days=offset), found.get(start_date + timedelta(days=offset)))
        for offset in range(baseline_lookback_days + 1)
    )
    pending_retention = await _pending_retention_scopes(
        conn, start_date=start_date, end_date=day_date
    )
    return FactWriteToken(
        day_date=day_date,
        timezone=timezone,
        baseline_lookback_days=baseline_lookback_days,
        start_date=start_date,
        end_date=day_date,
        replay_epoch=int(control["replay_epoch"]),
        replay_ack=int(control["replay_ack"]),
        control_timezone=str(control["timezone"]),
        closed_through=control["closed_through"],
        blocked_reason=control["blocked_reason"],
        days=days,
        pending_retention=pending_retention,
    )


def _require_fact_write_ready(token: FactWriteToken) -> None:
    if token.blocked_reason is not None:
        raise RepairAdmissionError("daily repair is blocked")
    if token.pending_retention:
        raise RepairAdmissionError("expired source retention repair is pending")
    if token.timezone != token.control_timezone:
        raise RepairAdmissionError("daily repair timezone does not match writeback")
    if token.replay_ack != token.replay_epoch:
        raise RepairAdmissionError("daily replay is pending")
    if token.closed_through < token.day_date:
        raise RepairAdmissionError("day-close date is not closed")
    if any(
        version is not None
        and (
            version.totals_pending
            or version.outlier_pending
            or version.facts_pending
            or (
                (version.totals_pending or version.outlier_pending)
                and version.built_generation != version.dirty_generation
            )
        )
        for _, version in token.days
    ):
        raise RepairAdmissionError("daily repair generation is pending")


async def capture_fact_write_token(
    pool: asyncpg.Pool,
    *,
    day_date: date,
    timezone: str,
    baseline_lookback_days: int,
) -> FactWriteToken:
    """Capture a stable repair token before any day-close evidence is read."""
    _validate_fact_baseline(baseline_lookback_days)
    async with pool.acquire() as conn:
        async with conn.transaction():
            if not await try_shared_lock(conn):
                raise RepairAdmissionError("daily repair is busy")
            token = await _read_fact_write_token(
                conn,
                day_date=day_date,
                timezone=timezone,
                baseline_lookback_days=baseline_lookback_days,
            )
            _require_fact_write_ready(token)
            return token


def make_fact_write_guard(token: FactWriteToken):
    """Build the same-transaction fence used by the canonical memory writer."""

    async def guard(conn: Connection) -> None:
        if not await try_shared_lock(conn):
            raise RepairAdmissionError("daily repair is busy")
        current = await _read_fact_write_token(
            conn,
            day_date=token.day_date,
            timezone=token.timezone,
            baseline_lookback_days=token.baseline_lookback_days,
        )
        if current != token:
            raise RepairAdmissionError("daily repair state changed during writeback")
        _require_fact_write_ready(current)

    return guard


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
    conn: Connection,
    day: date,
    generation: int,
    start: datetime,
    end: datetime,
    as_of: datetime,
) -> None:
    # Text has no ordinary read surface. Unknown provenance remains audit-only;
    # later source corrections can redact copied content without erasing identity.
    await conn.execute(
        """
        WITH deps AS (
            SELECT coalesce(array_agg(episode.id), '{}'::uuid[]) AS ids,
                   coalesce(bool_or(
                       episode.privacy = 'restricted' OR episode.tombstone_at IS NOT NULL
                       OR (episode.retention_days IS NOT NULL AND
                           episode.canonical_start_at
                               + make_interval(days => episode.retention_days) < $5)
                       OR EXISTS (
                           SELECT 1 FROM chronicler.daily_repair_source_redactions AS redaction
                           WHERE redaction.source_id = episode.id
                       )), FALSE) AS denied
            FROM chronicler.v_episodes_corrected AS episode
            WHERE (episode.start_at < $4
                   AND (episode.end_at IS NULL OR episode.end_at > $3))
               OR (episode.canonical_start_at < $4
                   AND (episode.canonical_end_at IS NULL OR episode.canonical_end_at > $3))
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
        as_of,
    )


async def rebuild_day(conn: Connection, state: Any, *, as_of: datetime | None = None) -> None:
    """One locked date: archive, stable totals, outlier disposition and cursor.

    Historical availability is unknown for pre-upgrade dates. Preserve every
    health-dependent/unmanaged flag and supersede only the old outlier claim.
    Memory reconciliation remains a separate pending work bit, never certified
    by quantitative completion.
    """
    day = state["local_date"]
    as_of = as_of or datetime.now(UTC)
    start, end = _local_day_bounds_utc(day, ZoneInfo(state["timezone"]))
    await _archive_day(conn, day, state["dirty_generation"], start, end, as_of)
    if state["totals_pending"]:
        rows = await conn.fetch(
            """SELECT episode.source_name, episode.episode_type, episode.start_at,
                      episode.end_at, episode.layer,
                      episode.payload->>'trigger_source' AS trigger_source
               FROM chronicler.v_episodes_corrected AS episode
               WHERE episode.start_at < $2
                 AND (episode.end_at IS NULL OR episode.end_at > $1)
                 AND episode.tombstone_at IS NULL
                 AND episode.privacy IN ('normal', 'sensitive')
                 AND NOT EXISTS (
                     SELECT 1 FROM chronicler.daily_repair_source_redactions AS redaction
                     WHERE redaction.source_id = episode.id
                 )
                 AND (episode.retention_days IS NULL OR
                      episode.canonical_start_at
                          + make_interval(days => episode.retention_days) >= $3)""",
            start,
            end,
            as_of,
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


_RECOGNIZED_FACT_IDS_FOR_DAY_SQL = """
    WITH recognized AS (
        SELECT f.id,
               CASE
                   WHEN prior.fact_id IS NOT NULL THEN prior.local_date
                   WHEN f.predicate = 'sleep-debt'
                       THEN NULLIF(f.metadata->>'as_of', '')::date
                   ELSE NULLIF(f.metadata->>'local_date', '')::date
               END AS generated_date
        FROM chronicler_mem.facts AS f
        LEFT JOIN LATERAL (
            SELECT disposition.fact_id, disposition.local_date
            FROM chronicler.daily_repair_fact_dispositions AS disposition
            WHERE disposition.fact_id = f.id
              AND disposition.disposition = 'disposed'
              AND disposition.redacted
            ORDER BY disposition.generation DESC
            LIMIT 1
        ) AS prior ON TRUE
        WHERE coalesce(f.content_authority, '') NOT IN ('owner', 'owner_device')
          AND (
              (f.source_butler = 'chronicler'
               AND f.predicate IN ('lane-skew', 'sleep-debt', 'revisit-low-confidence-block'))
              OR prior.fact_id IS NOT NULL
          )
        FOR UPDATE OF f
    )
    SELECT id FROM recognized WHERE generated_date = $1
"""

_FACT_DISPOSITION_STATE_SQL = """
    SELECT f.predicate, f.validity,
           f.validity NOT IN ('active', 'fading') AS terminal,
           (f.content_authority IS DISTINCT FROM 'owner'
            AND f.content_authority IS DISTINCT FROM 'owner_device') AS not_owner_authored,
           f.subject = 'chronicler redacted fact'
               AND f.content = '[removed by source privacy correction]'
               AND f.embedding IS NULL AND f.search_vector IS NULL
               AND f.confidence = 0 AND f.validity = 'retracted'
               AND f.invalid_at IS NOT NULL AND f.tags = '[]'::jsonb
               AND f.entity_id IS NULL AND f.object_entity_id IS NULL
               AND f.source_episode_id IS NULL
               AND f.metadata->>'source' = 'chronicler'
               AND f.metadata->>'redacted' = 'true'
               AND CASE WHEN jsonb_typeof(f.metadata) = 'object' THEN
                   f.metadata ?& ARRAY['source', 'repair_generation', 'correction_id',
                                        'reason_code', 'redacted']
                   AND NOT EXISTS (
                       SELECT 1 FROM jsonb_object_keys(f.metadata) AS metadata_key(key)
                       WHERE key <> ALL (ARRAY['source', 'repair_generation', 'correction_id',
                                               'reason_code', 'redacted'])
                   )
                   AND (SELECT count(*) FROM jsonb_object_keys(f.metadata)) = 5
                   AND jsonb_typeof(f.metadata->'repair_generation') = 'number'
                   AND jsonb_typeof(f.metadata->'correction_id') = 'string'
                   AND f.metadata->>'reason_code' IN (
                       'source_changed', 'source_delete', 'source_privacy',
                       'source_tombstone', 'source_retention', 'upgrade'
                   )
               ELSE FALSE END AS canonical_redacted,
           (f.predicate <> 'revisit-low-confidence-block' OR EXISTS (
               SELECT 1 FROM chronicler.daily_rollup_flags AS flag
               WHERE flag.local_date = $2
                 AND flag.flag_type = f.metadata->>'flag_type'
                 AND coalesce(flag.detail, '{}'::jsonb)
                     IS NOT DISTINCT FROM f.metadata->'detail'
                 AND flag.severity IS NOT DISTINCT FROM f.metadata->>'severity'
           )) AS reminder_flag_current,
           NOT EXISTS (
               SELECT 1 FROM public.entity_graph_edges AS edge
               WHERE edge.source_schema = 'chronicler_mem'
                 AND edge.source_table = 'facts' AND edge.source_id = f.id
           ) AS graph_absent,
           NOT EXISTS (
               SELECT 1 FROM public.memory_catalog AS catalog
               WHERE catalog.source_schema = 'chronicler_mem'
                 AND catalog.source_table = 'facts' AND catalog.source_id = f.id
                 AND (catalog.confidence IS DISTINCT FROM 0 OR catalog.invalid_at IS NULL)
           ) AS catalog_invalid,
           NOT EXISTS (
               SELECT 1 FROM public.memory_catalog AS catalog
               WHERE catalog.source_schema = 'chronicler_mem'
                 AND catalog.source_table = 'facts' AND catalog.source_id = f.id
                 AND (catalog.summary IS DISTINCT FROM '' OR catalog.title IS NOT NULL
                      OR catalog.embedding IS NOT NULL OR catalog.search_vector IS NOT NULL)
           ) AS catalog_redacted
    FROM chronicler_mem.facts AS f
    WHERE f.id = $1
    FOR UPDATE OF f
"""

_MISSING_REDACTED_PROJECTION_SQL = """
    SELECT NOT EXISTS (
               SELECT 1 FROM public.entity_graph_edges AS edge
               WHERE edge.source_schema = 'chronicler_mem'
                 AND edge.source_table = 'facts' AND edge.source_id = $1
           ) AS graph_absent,
           NOT EXISTS (
               SELECT 1 FROM public.memory_catalog AS catalog
               WHERE catalog.source_schema = 'chronicler_mem'
                 AND catalog.source_table = 'facts' AND catalog.source_id = $1
                 AND (catalog.confidence IS DISTINCT FROM 0 OR catalog.invalid_at IS NULL)
           ) AS catalog_invalid,
           NOT EXISTS (
               SELECT 1 FROM public.memory_catalog AS catalog
               WHERE catalog.source_schema = 'chronicler_mem'
                 AND catalog.source_table = 'facts' AND catalog.source_id = $1
                 AND (catalog.summary IS DISTINCT FROM '' OR catalog.title IS NOT NULL
                      OR catalog.embedding IS NOT NULL OR catalog.search_vector IS NOT NULL)
           ) AS catalog_redacted
"""


async def _reconcile_facts_pending(
    conn: Connection,
    state: Any,
    control: Any,
    *,
    timezone: str,
    closed_through: date,
) -> bool:
    """Clear one exact facts generation only after its durable disposition is proved.

    This path never writes or regenerates facts. It verifies the same-transaction
    inventory, canonical terminal state, projections, and any retained reminders
    before a conditional reset under the caller's exclusive advisory transaction.
    """
    day = state["local_date"]
    generation = int(state["dirty_generation"])
    if (
        not state["facts_pending"]
        or state["totals_pending"]
        or state["outlier_pending"]
        or day > closed_through
        or state["timezone"] != timezone
        or control["timezone"] != timezone
        or control["blocked_reason"] is not None
        or control["replay_epoch"] != control["replay_ack"]
        or await _pending_retention_scopes(
            conn, start_date=day - timedelta(days=MAX_FACT_BASELINE_DAYS), end_date=day
        )
    ):
        return False

    current = await conn.fetchrow(
        """SELECT dirty_generation, totals_pending, outlier_pending, facts_pending, timezone
           FROM chronicler.daily_repair_days WHERE local_date = $1 FOR UPDATE""",
        day,
    )
    if (
        current is None
        or int(current["dirty_generation"]) != generation
        or not current["facts_pending"]
        or current["totals_pending"]
        or current["outlier_pending"]
        or current["timezone"] != timezone
    ):
        return False

    dispositions = await conn.fetch(
        """SELECT row_key, fact_id, disposition, redacted, reason_code, dependency_ids
           FROM chronicler.daily_repair_fact_dispositions
           WHERE local_date = $1 AND generation = $2
           ORDER BY row_key""",
        day,
        generation,
    )
    if not dispositions:
        return False

    no_match_count = 0
    inventory_ids: set[Any] = set()
    dependency_token: tuple[str, ...] | None = None
    reason_token: str | None = None
    for item in dispositions:
        dependencies = item["dependency_ids"]
        if not dependencies or any(dependency is None for dependency in dependencies):
            return False
        normalized_dependencies = tuple(sorted(str(dependency) for dependency in dependencies))
        if len(set(normalized_dependencies)) != len(normalized_dependencies):
            return False
        if dependency_token is None:
            dependency_token = normalized_dependencies
            reason_token = item["reason_code"]
        elif normalized_dependencies != dependency_token or item["reason_code"] != reason_token:
            return False

        disposition = item["disposition"]
        fact_id = item["fact_id"]
        if disposition == "no_match":
            if item["row_key"] != "no_match" or fact_id is not None or item["redacted"]:
                return False
            no_match_count += 1
            continue
        if disposition not in {"disposed", "kept"} or fact_id is None:
            return False
        if item["row_key"] != str(fact_id) or fact_id in inventory_ids:
            return False
        if item["redacted"] and disposition != "disposed":
            return False
        inventory_ids.add(fact_id)

    if no_match_count:
        if no_match_count != 1 or len(dispositions) != 1:
            return False
        if inventory_ids:
            return False

    missing_redacted_ids = await conn.fetch(
        """SELECT DISTINCT disposition.fact_id
           FROM chronicler.daily_repair_fact_dispositions AS disposition
           WHERE disposition.local_date = $1 AND disposition.generation = $2
             AND disposition.disposition = 'disposed' AND disposition.redacted
             AND NOT EXISTS (SELECT 1 FROM chronicler_mem.facts AS fact
                             WHERE fact.id = disposition.fact_id)""",
        day,
        generation,
    )
    missing_redacted_id_set = {row["fact_id"] for row in missing_redacted_ids}
    if not missing_redacted_id_set.issubset(inventory_ids):
        return False

    recognized_rows = await conn.fetch(_RECOGNIZED_FACT_IDS_FOR_DAY_SQL, day)
    recognized_ids = {row["id"] for row in recognized_rows}
    if recognized_ids != inventory_ids - missing_redacted_id_set:
        return False

    for item in dispositions:
        if item["disposition"] == "no_match":
            continue
        if item["fact_id"] in missing_redacted_id_set:
            if item["disposition"] != "disposed" or not item["redacted"]:
                return False
            continue
        fact = await conn.fetchrow(_FACT_DISPOSITION_STATE_SQL, item["fact_id"], day)
        if fact is None or not fact["not_owner_authored"]:
            return False
        if item["disposition"] == "disposed":
            if not fact["terminal"] or not fact["graph_absent"] or not fact["catalog_invalid"]:
                return False
            if item["redacted"] and (
                not fact["canonical_redacted"] or not fact["catalog_redacted"]
            ):
                return False
        else:
            if item["redacted"] or fact["predicate"] != "revisit-low-confidence-block":
                return False
            if not fact["reminder_flag_current"]:
                return False
            if fact["terminal"] and (not fact["graph_absent"] or not fact["catalog_invalid"]):
                return False

    # Current-generation missing IDs only affect canonical inventory equality
    # above. Older redacted IDs for this date remain projection obligations
    # even when a later, unrelated generation has a no-match disposition.
    prior_missing_redacted_ids = await conn.fetch(
        """SELECT DISTINCT disposition.fact_id
           FROM chronicler.daily_repair_fact_dispositions AS disposition
           WHERE disposition.local_date = $1
             AND disposition.disposition = 'disposed' AND disposition.redacted
             AND NOT EXISTS (SELECT 1 FROM chronicler_mem.facts AS fact
                             WHERE fact.id = disposition.fact_id)""",
        day,
    )
    # A prior redacted ID may have been physically purged after its projections
    # were invalidated. Its remaining public copies must still be clean before
    # this date is declared reconciled.
    for missing in prior_missing_redacted_ids:
        projection = await conn.fetchrow(_MISSING_REDACTED_PROJECTION_SQL, missing["fact_id"])
        if (
            projection is None
            or not projection["graph_absent"]
            or not projection["catalog_invalid"]
            or not projection["catalog_redacted"]
        ):
            return False

    redacted_dependencies = await conn.fetchval(
        """SELECT coalesce(array_agg(DISTINCT dependency_id), '{}'::uuid[])
           FROM chronicler.daily_repair_fact_dispositions AS disposition
           CROSS JOIN LATERAL unnest(disposition.dependency_ids) AS dependency(dependency_id)
           WHERE disposition.local_date = $1 AND disposition.disposition = 'disposed'
             AND disposition.redacted""",
        day,
    )
    archive_redacted_ids = set(redacted_dependencies or ())
    if dependency_token:
        source_redactions = await conn.fetch(
            """SELECT source_id FROM chronicler.daily_repair_source_redactions
               WHERE source_id = ANY($1::uuid[])""",
            [UUID(value) for value in dependency_token],
        )
        archive_redacted_ids.update(row["source_id"] for row in source_redactions)
    if archive_redacted_ids:
        stale_archive = await conn.fetchval(
            """SELECT EXISTS (
                   SELECT 1 FROM chronicler.daily_repair_archive AS archive
                   WHERE archive.dependency_ids && $1::uuid[]
                     AND (archive.content IS NOT NULL OR archive.audit_hash IS NOT NULL
                          OR archive.text_access <> 'redacted')
               )""",
            list(archive_redacted_ids),
        )
        if stale_archive:
            return False

    updated = await conn.execute(
        """UPDATE chronicler.daily_repair_days
           SET facts_pending = FALSE, updated_at = now()
           WHERE local_date = $1 AND dirty_generation = $2 AND facts_pending
             AND NOT totals_pending AND NOT outlier_pending""",
        day,
        generation,
    )
    return updated == "UPDATE 1"


async def _has_pending_retention(conn: Connection, *, as_of: datetime) -> bool:
    return bool(
        await conn.fetchval(
            """WITH control AS (
                   SELECT timezone, closed_through FROM chronicler.daily_repair_control
                   WHERE singleton
               )
               SELECT EXISTS (
                   SELECT 1 FROM chronicler.daily_repair_retention_spans
               ) OR EXISTS (
                   SELECT 1
                   FROM chronicler.episodes AS source
                   JOIN chronicler.v_episodes_corrected AS effective
                     ON effective.id = source.id
                   CROSS JOIN control
                   WHERE source.retention_days IS NOT NULL
                     AND source.start_at + make_interval(days => source.retention_days) < $1
                     AND source.tombstone_at IS NULL
                     AND source.privacy IS DISTINCT FROM 'restricted'
                     AND effective.tombstone_at IS NULL
                     AND effective.privacy IS DISTINCT FROM 'restricted'
                     AND (source.start_at AT TIME ZONE control.timezone)::date
                         <= control.closed_through
                     AND NOT EXISTS (
                         SELECT 1 FROM chronicler.daily_repair_source_redactions AS redaction
                         WHERE redaction.source_id = source.id
                     )
               )""",
            as_of,
        )
    )


async def _scan_expired_retention(
    conn: Connection, *, as_of: datetime, timezone: str
) -> dict[str, Any]:
    """Admit one expired source, redact its existing copies, then advance a bounded cursor.

    The source-wide canonical/projection redaction and exact local-date spans
    commit together before any per-day cursor work is deferred. Cursor batches
    only enqueue numeric/date dispositions, so a timeout cannot leave an old
    fact or projection valid while the source remains pending.
    """
    span = await conn.fetchrow(
        """SELECT source_id, span_number, first_date, last_date, cursor_date,
                  batch_days, timeout_count
           FROM chronicler.daily_repair_retention_spans
           ORDER BY updated_at, source_id, span_number
           LIMIT 1 FOR UPDATE SKIP LOCKED"""
    )
    if span is None:
        source = await conn.fetchrow(
            """WITH control AS (
                   SELECT timezone, closed_through
                   FROM chronicler.daily_repair_control WHERE singleton
               )
               SELECT source.id,
                      (source.start_at AT TIME ZONE control.timezone)::date AS raw_first,
                      CASE WHEN source.end_at IS NULL THEN control.closed_through
                           ELSE least(control.closed_through,
                               ((source.end_at - interval '1 microsecond')
                                   AT TIME ZONE control.timezone)::date) END AS raw_last,
                      (effective.start_at AT TIME ZONE control.timezone)::date AS effective_first,
                      CASE WHEN effective.end_at IS NULL THEN control.closed_through
                           ELSE least(control.closed_through,
                               ((effective.end_at - interval '1 microsecond')
                                   AT TIME ZONE control.timezone)::date) END AS effective_last
               FROM chronicler.episodes AS source
               JOIN chronicler.v_episodes_corrected AS effective ON effective.id = source.id
               CROSS JOIN control
               WHERE source.retention_days IS NOT NULL
                 AND source.start_at + make_interval(days => source.retention_days) < $1
                 AND source.tombstone_at IS NULL
                 AND source.privacy IS DISTINCT FROM 'restricted'
                 AND effective.tombstone_at IS NULL
                 AND effective.privacy IS DISTINCT FROM 'restricted'
                 AND (source.start_at AT TIME ZONE control.timezone)::date
                     <= control.closed_through
                 AND NOT EXISTS (
                     SELECT 1 FROM chronicler.daily_repair_source_redactions AS redaction
                     WHERE redaction.source_id = source.id
                 )
               ORDER BY source.start_at, source.id
               LIMIT 1 FOR UPDATE OF source SKIP LOCKED""",
            as_of,
        )
        if source is not None:
            source_id = source["id"]
            ranges = sorted(
                {
                    (source["raw_first"], source["raw_last"]),
                    (source["effective_first"], source["effective_last"]),
                }
            )
            ranges = [
                (first, last)
                for first, last in ranges
                if first is not None and last is not None and first <= last
            ]
            await conn.execute(
                """INSERT INTO chronicler.daily_repair_source_redactions
                   (source_id, reason_code) VALUES ($1, 'source_retention')
                   ON CONFLICT (source_id) DO UPDATE SET reason_code = 'source_retention'""",
                source_id,
            )
            for number, (first, last) in enumerate(ranges, start=1):
                await conn.execute(
                    """INSERT INTO chronicler.daily_repair_retention_spans
                       (source_id, span_number, first_date, last_date, cursor_date, batch_days)
                       VALUES ($1, $2, $3, $4, $3, $5)
                       ON CONFLICT (source_id, span_number) DO NOTHING""",
                    source_id,
                    number,
                    first,
                    last,
                    MAX_RETENTION_CURSOR_DAYS,
                )
            await conn.fetchval("SELECT chronicler.redact_retention_facts($1)", source_id)
            await conn.execute(
                """UPDATE chronicler.daily_repair_archive
                   SET content = NULL, audit_hash = NULL, text_access = 'redacted'
                   WHERE $1 = ANY(dependency_ids)""",
                source_id,
            )
            span = await conn.fetchrow(
                """SELECT source_id, span_number, first_date, last_date, cursor_date,
                          batch_days, timeout_count
                   FROM chronicler.daily_repair_retention_spans
                   WHERE source_id = $1
                   ORDER BY span_number LIMIT 1 FOR UPDATE""",
                source_id,
            )

    if span is not None:
        source_id = span["source_id"]
        chunk_last = min(
            span["last_date"],
            span["cursor_date"] + timedelta(days=int(span["batch_days"]) - 1),
        )
        zone = ZoneInfo(timezone)
        span_start, _ = _local_day_bounds_utc(span["cursor_date"], zone)
        _, span_end = _local_day_bounds_utc(chunk_last, zone)
        try:
            async with conn.transaction():
                previous_timeout = await _set_retention_cursor_statement_timeout(
                    conn, MAX_RETENTION_CURSOR_TIMEOUT_MS
                )
                await conn.execute(
                    """SELECT chronicler.enqueue_daily_repair(
                           $1, $2, TRUE, ARRAY[$3::uuid], TRUE, 'source_retention'
                       )""",
                    span_start,
                    span_end,
                    source_id,
                )
                await conn.execute(
                    "SELECT set_config('statement_timeout', $1, TRUE)", previous_timeout
                )
        except asyncpg.exceptions.QueryCanceledError:
            next_batch = max(1, int(span["batch_days"]) // 2)
            timeout_count = int(span["timeout_count"]) + 1
            await conn.execute(
                """UPDATE chronicler.daily_repair_retention_spans
                   SET batch_days = $3, timeout_count = $4, updated_at = now()
                   WHERE source_id = $1 AND span_number = $2""",
                source_id,
                span["span_number"],
                next_batch,
                timeout_count,
            )
            blocked = next_batch == 1 and timeout_count >= 3
            logger.warning(
                "Chronicler retention cursor timed out for source %s at %s (batch=%d, attempts=%d)",
                source_id,
                span["cursor_date"],
                next_batch,
                timeout_count,
            )
            return {
                "pending": True,
                "timeout": True,
                "blocked": blocked,
                "source_id": str(source_id),
                "cursor_date": span["cursor_date"].isoformat(),
                "batch_days": next_batch,
                "timeout_count": timeout_count,
            }
        if chunk_last >= span["last_date"]:
            await conn.execute(
                """DELETE FROM chronicler.daily_repair_retention_spans
                   WHERE source_id = $1 AND span_number = $2""",
                source_id,
                span["span_number"],
            )
        else:
            await conn.execute(
                """UPDATE chronicler.daily_repair_retention_spans
                   SET cursor_date = $3, batch_days = least(32, batch_days * 2),
                       timeout_count = 0, updated_at = now()
                   WHERE source_id = $1 AND span_number = $2""",
                source_id,
                span["span_number"],
                chunk_last + timedelta(days=1),
            )
    return {
        "pending": await _has_pending_retention(conn, as_of=as_of),
        "timeout": False,
        "blocked": False,
    }


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
    as_of = now or datetime.now(UTC)
    closed_through = as_of.astimezone(tz).date() - timedelta(days=1)
    processed: list[str] = []
    retention_scan_done = False
    retention_result: dict[str, Any] = {"pending": False, "timeout": False, "blocked": False}
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
                await conn.execute(
                    "UPDATE chronicler.daily_repair_control SET closed_through = $1 "
                    "WHERE singleton",
                    closed_through,
                )
                if not retention_scan_done:
                    retention_result = await _scan_expired_retention(
                        conn, as_of=as_of, timezone=timezone
                    )
                    retention_scan_done = True
                    if retention_result["timeout"]:
                        status = (
                            "blocked_retention"
                            if retention_result["blocked"]
                            else "pending_retention"
                        )
                        return {
                            "status": status,
                            "retention_timeout": True,
                            "retention_source_id": retention_result["source_id"],
                            "retention_cursor_date": retention_result["cursor_date"],
                            "retention_batch_days": retention_result["batch_days"],
                            "retention_timeout_count": retention_result["timeout_count"],
                            "days_processed": processed,
                        }
                if ctl["replay_ack"] < ctl["replay_epoch"]:
                    return {
                        "status": "waiting_replay",
                        "retention_pending": bool(retention_result["pending"])
                        if retention_scan_done
                        else await _has_pending_retention(conn, as_of=as_of),
                        "days_processed": processed,
                    }
                state = await conn.fetchrow(
                    """SELECT * FROM chronicler.daily_repair_days
                       WHERE (totals_pending OR outlier_pending) AND local_date <= $1
                       ORDER BY totals_pending DESC, local_date LIMIT 1 FOR UPDATE""",
                    closed_through,
                )
                if state is None:
                    facts_state = await conn.fetchrow(
                        """SELECT * FROM chronicler.daily_repair_days
                           WHERE facts_pending AND NOT totals_pending AND NOT outlier_pending
                             AND local_date <= $1
                           ORDER BY local_date LIMIT 1 FOR UPDATE""",
                        closed_through,
                    )
                    if facts_state is not None:
                        if await _reconcile_facts_pending(
                            conn, facts_state, ctl, timezone=timezone, closed_through=closed_through
                        ):
                            processed.append(facts_state["local_date"].isoformat())
                            continue
                        if await _has_pending_retention(conn, as_of=as_of):
                            return {"status": "pending_retention", "days_processed": processed}
                        return {"status": "waiting_facts", "days_processed": processed}
                    facts = await conn.fetchval(
                        "SELECT EXISTS (SELECT 1 FROM chronicler.daily_repair_days "
                        "WHERE facts_pending)"
                    )
                    retention_pending = await _has_pending_retention(conn, as_of=as_of)
                    if retention_pending:
                        return {"status": "pending_retention", "days_processed": processed}
                    return {
                        "status": "waiting_facts" if facts else "complete",
                        "days_processed": processed,
                    }
                await rebuild_day(conn, state, as_of=as_of)
                processed.append(state["local_date"].isoformat())
    status = (
        "pending_retention" if retention_scan_done and retention_result["pending"] else "pending"
    )
    return {"status": status, "days_processed": processed}
