"""Connector statistics aggregation and pruning jobs.

This module implements scheduled jobs for rolling up connector heartbeat data
into hourly and daily statistics tables, as well as pruning old data.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import asyncpg

logger = logging.getLogger(__name__)


async def run_connector_stats_hourly_rollup(db_pool: asyncpg.Pool) -> dict[str, int]:
    """Run hourly rollup of connector heartbeat data.

    Processes connector_heartbeat_log for the previous hour, computes counter deltas
    between consecutive heartbeats, counts health states, and upserts into
    connector_stats_hourly.

    Args:
        db_pool: Database connection pool

    Returns:
        Dictionary with 'rows_processed' and 'connectors_updated' counts
    """
    now = datetime.now(UTC)
    hour_start = (now - timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    hour_end = hour_start + timedelta(hours=1)

    logger.info(
        "Starting hourly rollup for hour: %s - %s",
        hour_start.isoformat(),
        hour_end.isoformat(),
    )

    async with db_pool.acquire() as conn:
        # Compute deltas and health counts from heartbeat log
        result = await conn.fetch(
            """
            WITH heartbeats AS (
                SELECT
                    connector_type,
                    endpoint_identity,
                    state,
                    counter_messages_ingested,
                    counter_messages_failed,
                    counter_source_api_calls,
                    counter_dedupe_accepted,
                    received_at,
                    LAG(counter_messages_ingested) OVER w AS prev_messages_ingested,
                    LAG(counter_messages_failed) OVER w AS prev_messages_failed,
                    LAG(counter_source_api_calls) OVER w AS prev_source_api_calls,
                    LAG(counter_dedupe_accepted) OVER w AS prev_dedupe_accepted
                FROM connector_heartbeat_log
                WHERE received_at >= $1 AND received_at < $2
                WINDOW w AS (
                    PARTITION BY connector_type, endpoint_identity
                    ORDER BY received_at
                )
            ),
            deltas AS (
                SELECT
                    connector_type,
                    endpoint_identity,
                    COALESCE(
                        counter_messages_ingested - prev_messages_ingested,
                        0
                    ) AS delta_messages_ingested,
                    COALESCE(
                        counter_messages_failed - prev_messages_failed,
                        0
                    ) AS delta_messages_failed,
                    COALESCE(
                        counter_source_api_calls - prev_source_api_calls,
                        0
                    ) AS delta_source_api_calls,
                    COALESCE(
                        counter_dedupe_accepted - prev_dedupe_accepted,
                        0
                    ) AS delta_dedupe_accepted,
                    state
                FROM heartbeats
            ),
            aggregates AS (
                SELECT
                    connector_type,
                    endpoint_identity,
                    SUM(delta_messages_ingested) AS messages_ingested,
                    SUM(delta_messages_failed) AS messages_failed,
                    SUM(delta_source_api_calls) AS source_api_calls,
                    SUM(delta_dedupe_accepted) AS dedupe_accepted,
                    COUNT(*) AS heartbeat_count,
                    SUM(CASE WHEN state = 'healthy' THEN 1 ELSE 0 END) AS healthy_count,
                    SUM(CASE WHEN state = 'degraded' THEN 1 ELSE 0 END) AS degraded_count,
                    SUM(CASE WHEN state = 'error' THEN 1 ELSE 0 END) AS error_count
                FROM deltas
                GROUP BY connector_type, endpoint_identity
            )
            INSERT INTO connector_stats_hourly (
                connector_type,
                endpoint_identity,
                hour,
                messages_ingested,
                messages_failed,
                source_api_calls,
                dedupe_accepted,
                heartbeat_count,
                healthy_count,
                degraded_count,
                error_count
            )
            SELECT
                connector_type,
                endpoint_identity,
                $1,
                messages_ingested,
                messages_failed,
                source_api_calls,
                dedupe_accepted,
                heartbeat_count::integer,
                healthy_count::integer,
                degraded_count::integer,
                error_count::integer
            FROM aggregates
            ON CONFLICT (connector_type, endpoint_identity, hour)
            DO UPDATE SET
                messages_ingested = EXCLUDED.messages_ingested,
                messages_failed = EXCLUDED.messages_failed,
                source_api_calls = EXCLUDED.source_api_calls,
                dedupe_accepted = EXCLUDED.dedupe_accepted,
                heartbeat_count = EXCLUDED.heartbeat_count,
                healthy_count = EXCLUDED.healthy_count,
                degraded_count = EXCLUDED.degraded_count,
                error_count = EXCLUDED.error_count
            RETURNING connector_type, endpoint_identity
            """,
            hour_start,
            hour_end,
        )

        rows_processed = len(result)
        logger.info(
            "Hourly rollup completed: %d connectors updated for hour %s",
            rows_processed,
            hour_start.isoformat(),
        )

        return {
            "rows_processed": rows_processed,
            "connectors_updated": rows_processed,
            "hour": hour_start.isoformat(),
        }


async def run_connector_stats_daily_rollup(db_pool: asyncpg.Pool) -> dict[str, int]:
    """Run daily rollup of connector statistics.

    Sums connector_stats_hourly into connector_stats_daily, computes uptime_pct,
    and also runs the fanout rollup from message_inbox.

    Args:
        db_pool: Database connection pool

    Returns:
        Dictionary with 'stats_updated' and 'fanout_updated' counts
    """
    now = datetime.now(UTC)
    day = (now - timedelta(days=1)).date()

    logger.info("Starting daily rollup for day: %s", day.isoformat())

    async with db_pool.acquire() as conn:
        # Rollup hourly stats into daily stats
        stats_result = await conn.fetch(
            """
            WITH daily_aggregates AS (
                SELECT
                    connector_type,
                    endpoint_identity,
                    SUM(messages_ingested) AS messages_ingested,
                    SUM(messages_failed) AS messages_failed,
                    SUM(source_api_calls) AS source_api_calls,
                    SUM(dedupe_accepted) AS dedupe_accepted,
                    SUM(heartbeat_count) AS heartbeat_count,
                    SUM(healthy_count) AS healthy_count,
                    SUM(degraded_count) AS degraded_count,
                    SUM(error_count) AS error_count,
                    CASE
                        WHEN SUM(heartbeat_count) > 0 THEN
                            ROUND(
                                (SUM(healthy_count)::numeric / SUM(heartbeat_count)::numeric) * 100,
                                2
                            )
                        ELSE NULL
                    END AS uptime_pct
                FROM connector_stats_hourly
                WHERE DATE(hour) = $1
                GROUP BY connector_type, endpoint_identity
            )
            INSERT INTO connector_stats_daily (
                connector_type,
                endpoint_identity,
                day,
                messages_ingested,
                messages_failed,
                source_api_calls,
                dedupe_accepted,
                heartbeat_count,
                healthy_count,
                degraded_count,
                error_count,
                uptime_pct
            )
            SELECT
                connector_type,
                endpoint_identity,
                $1,
                messages_ingested,
                messages_failed,
                source_api_calls,
                dedupe_accepted,
                heartbeat_count::integer,
                healthy_count::integer,
                degraded_count::integer,
                error_count::integer,
                uptime_pct
            FROM daily_aggregates
            ON CONFLICT (connector_type, endpoint_identity, day)
            DO UPDATE SET
                messages_ingested = EXCLUDED.messages_ingested,
                messages_failed = EXCLUDED.messages_failed,
                source_api_calls = EXCLUDED.source_api_calls,
                dedupe_accepted = EXCLUDED.dedupe_accepted,
                heartbeat_count = EXCLUDED.heartbeat_count,
                healthy_count = EXCLUDED.healthy_count,
                degraded_count = EXCLUDED.degraded_count,
                error_count = EXCLUDED.error_count,
                uptime_pct = EXCLUDED.uptime_pct
            RETURNING connector_type, endpoint_identity
            """,
            day,
        )

        stats_updated = len(stats_result)

        # Rollup fanout statistics from message_inbox
        # NOTE: source_channel and source_endpoint_identity were moved into the
        # request_context JSONB column in migration sw_008.
        fanout_result = await conn.fetch(
            """
            WITH fanout_aggregates AS (
                SELECT
                    request_context ->> 'source_channel' AS source_channel,
                    request_context ->> 'source_endpoint_identity' AS source_endpoint_identity,
                    jsonb_object_keys(dispatch_outcomes) AS target_butler,
                    COUNT(*) AS message_count
                FROM message_inbox
                WHERE DATE(received_at) = $1
                AND dispatch_outcomes IS NOT NULL
                GROUP BY
                    request_context ->> 'source_channel',
                    request_context ->> 'source_endpoint_identity',
                    target_butler
            )
            INSERT INTO connector_fanout_daily (
                connector_type,
                endpoint_identity,
                target_butler,
                day,
                message_count
            )
            SELECT
                source_channel AS connector_type,
                source_endpoint_identity AS endpoint_identity,
                target_butler,
                $1,
                message_count
            FROM fanout_aggregates
            ON CONFLICT (connector_type, endpoint_identity, target_butler, day)
            DO UPDATE SET
                message_count = EXCLUDED.message_count
            RETURNING connector_type, endpoint_identity, target_butler
            """,
            day,
        )

        fanout_updated = len(fanout_result)

        logger.info(
            "Daily rollup completed: %d stats updated, %d fanout entries updated for day %s",
            stats_updated,
            fanout_updated,
            day.isoformat(),
        )

        return {
            "stats_updated": stats_updated,
            "fanout_updated": fanout_updated,
            "day": day.isoformat(),
        }


async def run_connector_stats_pruning(db_pool: asyncpg.Pool) -> dict[str, int]:
    """Run pruning jobs for connector statistics and heartbeat data.

    Prunes:
    - connector_heartbeat_log: drop partitions older than 7 days
    - connector_stats_hourly: delete rows older than 30 days
    - connector_stats_daily and connector_fanout_daily: delete rows older than 1 year

    Args:
        db_pool: Database connection pool

    Returns:
        Dictionary with counts of pruned items per table
    """
    now = datetime.now(UTC)

    logger.info("Starting connector stats pruning job")

    async with db_pool.acquire() as conn:
        # Prune heartbeat log partitions (older than 7 days)
        heartbeat_dropped = await conn.fetchval(
            "SELECT switchboard_connector_heartbeat_log_drop_expired_partitions("
            "INTERVAL '7 days', $1)",
            now,
        )

        # Prune hourly stats (older than 30 days)
        hourly_cutoff = now - timedelta(days=30)
        hourly_deleted = await conn.fetchval(
            """
            WITH deleted AS (
                DELETE FROM connector_stats_hourly
                WHERE hour < $1
                RETURNING 1
            )
            SELECT COUNT(*) FROM deleted
            """,
            hourly_cutoff,
        )

        # Prune daily stats (older than 1 year)
        daily_cutoff = (now - timedelta(days=365)).date()
        daily_deleted = await conn.fetchval(
            """
            WITH deleted AS (
                DELETE FROM connector_stats_daily
                WHERE day < $1
                RETURNING 1
            )
            SELECT COUNT(*) FROM deleted
            """,
            daily_cutoff,
        )

        # Prune fanout stats (older than 1 year)
        fanout_deleted = await conn.fetchval(
            """
            WITH deleted AS (
                DELETE FROM connector_fanout_daily
                WHERE day < $1
                RETURNING 1
            )
            SELECT COUNT(*) FROM deleted
            """,
            daily_cutoff,
        )

        logger.info(
            "Pruning completed: %d heartbeat partitions dropped, "
            "%d hourly rows deleted, %d daily rows deleted, %d fanout rows deleted",
            heartbeat_dropped or 0,
            hourly_deleted or 0,
            daily_deleted or 0,
            fanout_deleted or 0,
        )

        return {
            "heartbeat_partitions_dropped": heartbeat_dropped or 0,
            "hourly_rows_deleted": hourly_deleted or 0,
            "daily_rows_deleted": daily_deleted or 0,
            "fanout_rows_deleted": fanout_deleted or 0,
        }
