"""Unit tests for connector statistics rollup SQL logic.

These tests verify:
- Hourly rollup SQL correctly computes counter deltas and health states
- Daily rollup SQL correctly sums hourly data and computes uptime percentage
- Fanout rollup SQL correctly parses dispatch_outcomes JSONB
- Rollups are idempotent (safe to re-run)
- Pruning SQL correctly removes old data
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

# Skip all tests if Docker not available
docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


async def _create_test_schema(pool):
    """Create test schema for connector statistics tables."""
    # Create connector_heartbeat_log table (partitioned)
    await pool.execute(
        """
        CREATE TABLE connector_heartbeat_log (
            id BIGINT GENERATED ALWAYS AS IDENTITY,
            connector_type TEXT NOT NULL,
            endpoint_identity TEXT NOT NULL,
            instance_id UUID,
            state TEXT NOT NULL,
            error_message TEXT,
            uptime_s INTEGER,
            counter_messages_ingested BIGINT,
            counter_messages_failed BIGINT,
            counter_source_api_calls BIGINT,
            counter_checkpoint_saves BIGINT,
            counter_dedupe_accepted BIGINT,
            received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            sent_at TIMESTAMPTZ,
            PRIMARY KEY (received_at, id)
        ) PARTITION BY RANGE (received_at)
        """
    )

    # Create partition for current month
    await pool.execute(
        """
        CREATE TABLE connector_heartbeat_log_p202602
        PARTITION OF connector_heartbeat_log
        FOR VALUES FROM ('2026-02-01') TO ('2026-03-01')
        """
    )

    # Create partition management function
    await pool.execute(
        """
        CREATE OR REPLACE FUNCTION drop_expired_partitions(
            retention INTERVAL DEFAULT INTERVAL '7 days',
            reference_ts TIMESTAMPTZ DEFAULT now()
        ) RETURNS INTEGER
        LANGUAGE plpgsql
        AS $$
        DECLARE
            partition_name TEXT;
            partition_month DATE;
            cutoff_month DATE;
            dropped_count INTEGER := 0;
        BEGIN
            cutoff_month := date_trunc('month', reference_ts - retention)::date;

            FOR partition_name IN
                SELECT child.relname
                FROM pg_inherits
                JOIN pg_class parent ON parent.oid = pg_inherits.inhparent
                JOIN pg_class child ON child.oid = pg_inherits.inhrelid
                JOIN pg_namespace ns ON ns.oid = child.relnamespace
                WHERE parent.relname = 'connector_heartbeat_log'
                AND ns.nspname = current_schema()
                AND child.relname ~ '^connector_heartbeat_log_p[0-9]{6}$'
            LOOP
                partition_month := to_date(
                    substring(partition_name from '[0-9]{6}$'), 'YYYYMM'
                );
                IF partition_month < cutoff_month THEN
                    EXECUTE format('DROP TABLE IF EXISTS %I', partition_name);
                    dropped_count := dropped_count + 1;
                END IF;
            END LOOP;

            RETURN dropped_count;
        END;
        $$
        """
    )

    # Create connector_stats_hourly table
    await pool.execute(
        """
        CREATE TABLE connector_stats_hourly (
            connector_type TEXT NOT NULL,
            endpoint_identity TEXT NOT NULL,
            hour TIMESTAMPTZ NOT NULL,
            messages_ingested BIGINT DEFAULT 0,
            messages_failed BIGINT DEFAULT 0,
            source_api_calls BIGINT DEFAULT 0,
            dedupe_accepted BIGINT DEFAULT 0,
            heartbeat_count INTEGER DEFAULT 0,
            healthy_count INTEGER DEFAULT 0,
            degraded_count INTEGER DEFAULT 0,
            error_count INTEGER DEFAULT 0,
            PRIMARY KEY (connector_type, endpoint_identity, hour)
        )
        """
    )

    # Create connector_stats_daily table
    await pool.execute(
        """
        CREATE TABLE connector_stats_daily (
            connector_type TEXT NOT NULL,
            endpoint_identity TEXT NOT NULL,
            day DATE NOT NULL,
            messages_ingested BIGINT DEFAULT 0,
            messages_failed BIGINT DEFAULT 0,
            source_api_calls BIGINT DEFAULT 0,
            dedupe_accepted BIGINT DEFAULT 0,
            heartbeat_count INTEGER DEFAULT 0,
            healthy_count INTEGER DEFAULT 0,
            degraded_count INTEGER DEFAULT 0,
            error_count INTEGER DEFAULT 0,
            uptime_pct NUMERIC(5, 2),
            PRIMARY KEY (connector_type, endpoint_identity, day)
        )
        """
    )

    # Create connector_fanout_daily table
    await pool.execute(
        """
        CREATE TABLE connector_fanout_daily (
            connector_type TEXT NOT NULL,
            endpoint_identity TEXT NOT NULL,
            target_butler TEXT NOT NULL,
            day DATE NOT NULL,
            message_count BIGINT DEFAULT 0,
            PRIMARY KEY (connector_type, endpoint_identity, target_butler, day)
        )
        """
    )

    # Create message_inbox table (for fanout rollup tests)
    await pool.execute(
        """
        CREATE TABLE message_inbox (
            id UUID NOT NULL DEFAULT gen_random_uuid(),
            received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            source_channel TEXT NOT NULL,
            source_endpoint_identity TEXT NOT NULL,
            dispatch_outcomes JSONB,
            PRIMARY KEY (received_at, id)
        ) PARTITION BY RANGE (received_at)
        """
    )

    # Create partition for message_inbox
    await pool.execute(
        """
        CREATE TABLE message_inbox_p202602
        PARTITION OF message_inbox
        FOR VALUES FROM ('2026-02-01') TO ('2026-03-01')
        """
    )


# Hourly rollup tests


async def test_hourly_rollup_computes_deltas_correctly(provisioned_postgres_pool):
    """Test that hourly rollup correctly computes counter deltas between heartbeats."""
    async with provisioned_postgres_pool() as pool:
        await _create_test_schema(pool)

        # Insert heartbeat data with incrementing counters
        base_time = datetime(2026, 2, 16, 10, 0, 0, tzinfo=UTC)
        hour_start = datetime(2026, 2, 16, 10, 0, 0, tzinfo=UTC)
        hour_end = datetime(2026, 2, 16, 11, 0, 0, tzinfo=UTC)

        await pool.execute(
            """
            INSERT INTO connector_heartbeat_log (
                connector_type,
                endpoint_identity,
                state,
                counter_messages_ingested,
                counter_messages_failed,
                counter_source_api_calls,
                counter_dedupe_accepted,
                received_at
            ) VALUES
            ('telegram_bot', 'bot@123', 'healthy', 100, 5, 50, 95, $1),
            ('telegram_bot', 'bot@123', 'healthy', 150, 6, 75, 144, $2),
            ('telegram_bot', 'bot@123', 'degraded', 200, 8, 100, 192, $3)
            """,
            base_time + timedelta(minutes=10),
            base_time + timedelta(minutes=20),
            base_time + timedelta(minutes=30),
        )

        # Run hourly rollup SQL
        await pool.execute(
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
            """,
            hour_start,
            hour_end,
        )

        # Verify the rollup data
        row = await pool.fetchrow(
            """
            SELECT * FROM connector_stats_hourly
            WHERE connector_type = 'telegram_bot'
            AND endpoint_identity = 'bot@123'
            AND hour = $1
            """,
            hour_start,
        )

        assert row is not None
        # Deltas: (150-100) + (200-150) = 50 + 50 = 100
        assert row["messages_ingested"] == 100
        # Deltas: (6-5) + (8-6) = 1 + 2 = 3
        assert row["messages_failed"] == 3
        # Deltas: (75-50) + (100-75) = 25 + 25 = 50
        assert row["source_api_calls"] == 50
        # Deltas: (144-95) + (192-144) = 49 + 48 = 97
        assert row["dedupe_accepted"] == 97
        # Total heartbeats: 3
        assert row["heartbeat_count"] == 3
        # Healthy: 2, Degraded: 1
        assert row["healthy_count"] == 2
        assert row["degraded_count"] == 1
        assert row["error_count"] == 0


async def test_hourly_rollup_is_idempotent(provisioned_postgres_pool):
    """Test that running hourly rollup multiple times produces the same result."""
    async with provisioned_postgres_pool() as pool:
        await _create_test_schema(pool)

        base_time = datetime(2026, 2, 16, 11, 0, 0, tzinfo=UTC)
        hour_start = datetime(2026, 2, 16, 11, 0, 0, tzinfo=UTC)
        hour_end = datetime(2026, 2, 16, 12, 0, 0, tzinfo=UTC)

        await pool.execute(
            """
            INSERT INTO connector_heartbeat_log (
                connector_type,
                endpoint_identity,
                state,
                counter_messages_ingested,
                counter_messages_failed,
                counter_source_api_calls,
                counter_dedupe_accepted,
                received_at
            ) VALUES
            ('email', 'user@example.com', 'healthy', 10, 0, 5, 10, $1),
            ('email', 'user@example.com', 'healthy', 20, 0, 10, 20, $2)
            """,
            base_time + timedelta(minutes=15),
            base_time + timedelta(minutes=45),
        )

        # SQL for rollup (extracted as reusable query)
        rollup_sql = """
            WITH heartbeats AS (
                SELECT
                    connector_type,
                    endpoint_identity,
                    state,
                    counter_messages_ingested,
                    counter_messages_failed,
                    counter_source_api_calls,
                    counter_dedupe_accepted,
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
                        counter_messages_ingested - prev_messages_ingested, 0
                    ) AS delta_messages_ingested,
                    COALESCE(
                        counter_messages_failed - prev_messages_failed, 0
                    ) AS delta_messages_failed,
                    COALESCE(
                        counter_source_api_calls - prev_source_api_calls, 0
                    ) AS delta_source_api_calls,
                    COALESCE(
                        counter_dedupe_accepted - prev_dedupe_accepted, 0
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
                connector_type, endpoint_identity, hour,
                messages_ingested, messages_failed, source_api_calls, dedupe_accepted,
                heartbeat_count, healthy_count, degraded_count, error_count
            )
            SELECT
                connector_type, endpoint_identity, $1,
                messages_ingested, messages_failed, source_api_calls, dedupe_accepted,
                heartbeat_count::integer, healthy_count::integer,
                degraded_count::integer, error_count::integer
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
        """

        # Run rollup first time
        await pool.execute(rollup_sql, hour_start, hour_end)

        row1 = await pool.fetchrow(
            """
            SELECT * FROM connector_stats_hourly
            WHERE connector_type = 'email'
            AND endpoint_identity = 'user@example.com'
            AND hour = $1
            """,
            hour_start,
        )

        # Run rollup second time
        await pool.execute(rollup_sql, hour_start, hour_end)

        row2 = await pool.fetchrow(
            """
            SELECT * FROM connector_stats_hourly
            WHERE connector_type = 'email'
            AND endpoint_identity = 'user@example.com'
            AND hour = $1
            """,
            hour_start,
        )

        # Results should be identical
        assert row1 == row2


# Daily rollup tests


async def test_daily_rollup_sums_hourly_data_and_computes_uptime(provisioned_postgres_pool):
    """Test that daily rollup correctly sums hourly data and computes uptime."""
    async with provisioned_postgres_pool() as pool:
        await _create_test_schema(pool)

        day = datetime(2026, 2, 15, tzinfo=UTC).date()

        # Insert hourly data for 3 hours
        await pool.execute(
            """
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
            ) VALUES
            ('telegram_bot', 'bot@456', $1, 100, 5, 50, 95, 10, 9, 1, 0),
            ('telegram_bot', 'bot@456', $2, 150, 3, 75, 147, 12, 10, 2, 0),
            ('telegram_bot', 'bot@456', $3, 200, 7, 100, 193, 15, 12, 2, 1)
            """,
            datetime(2026, 2, 15, 10, 0, 0, tzinfo=UTC),
            datetime(2026, 2, 15, 11, 0, 0, tzinfo=UTC),
            datetime(2026, 2, 15, 12, 0, 0, tzinfo=UTC),
        )

        # Run daily rollup SQL
        await pool.execute(
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
                                (SUM(healthy_count)::numeric /
                                 SUM(heartbeat_count)::numeric) * 100,
                                2
                            )
                        ELSE NULL
                    END AS uptime_pct
                FROM connector_stats_hourly
                WHERE DATE(hour) = $1
                GROUP BY connector_type, endpoint_identity
            )
            INSERT INTO connector_stats_daily (
                connector_type, endpoint_identity, day,
                messages_ingested, messages_failed, source_api_calls, dedupe_accepted,
                heartbeat_count, healthy_count, degraded_count, error_count, uptime_pct
            )
            SELECT
                connector_type, endpoint_identity, $1,
                messages_ingested, messages_failed, source_api_calls, dedupe_accepted,
                heartbeat_count::integer, healthy_count::integer,
                degraded_count::integer, error_count::integer, uptime_pct
            FROM daily_aggregates
            """,
            day,
        )

        # Verify the daily data
        row = await pool.fetchrow(
            """
            SELECT * FROM connector_stats_daily
            WHERE connector_type = 'telegram_bot'
            AND endpoint_identity = 'bot@456'
            AND day = $1
            """,
            day,
        )

        assert row is not None
        assert row["messages_ingested"] == 450  # 100 + 150 + 200
        assert row["messages_failed"] == 15  # 5 + 3 + 7
        assert row["source_api_calls"] == 225  # 50 + 75 + 100
        assert row["dedupe_accepted"] == 435  # 95 + 147 + 193
        assert row["heartbeat_count"] == 37  # 10 + 12 + 15
        assert row["healthy_count"] == 31  # 9 + 10 + 12
        # uptime_pct is a Decimal, so compare as Decimal
        assert row["uptime_pct"] == Decimal("83.78")  # 31/37 * 100


async def test_fanout_rollup_parses_dispatch_outcomes(provisioned_postgres_pool):
    """Test that fanout rollup correctly parses dispatch_outcomes JSONB."""
    async with provisioned_postgres_pool() as pool:
        await _create_test_schema(pool)

        day = datetime(2026, 2, 15, tzinfo=UTC).date()

        # Insert message_inbox data with dispatch_outcomes
        await pool.execute(
            """
            INSERT INTO message_inbox (
                source_channel,
                source_endpoint_identity,
                dispatch_outcomes,
                received_at
            ) VALUES
            ('telegram_bot', 'telegram_bot.bot@789', '{"health": {"status": "success"}}', $1),
            ('telegram_bot', 'telegram_bot.bot@789', '{"health": {"status": "success"}}', $2),
            ('telegram_bot', 'telegram_bot.bot@789', '{"relationship": {"status": "success"}}', $3),
            ('email', 'email.user@example.com', '{"general": {"status": "success"}}', $4)
            """,
            datetime(2026, 2, 15, 10, 30, 0, tzinfo=UTC),
            datetime(2026, 2, 15, 11, 30, 0, tzinfo=UTC),
            datetime(2026, 2, 15, 12, 30, 0, tzinfo=UTC),
            datetime(2026, 2, 15, 13, 30, 0, tzinfo=UTC),
        )

        # Run fanout rollup SQL
        await pool.execute(
            """
            WITH fanout_aggregates AS (
                SELECT
                    source_channel,
                    source_endpoint_identity,
                    jsonb_object_keys(dispatch_outcomes) AS target_butler,
                    COUNT(*) AS message_count
                FROM message_inbox
                WHERE DATE(received_at) = $1
                AND dispatch_outcomes IS NOT NULL
                GROUP BY source_channel, source_endpoint_identity, target_butler
            )
            INSERT INTO connector_fanout_daily (
                connector_type, endpoint_identity, target_butler, day, message_count
            )
            SELECT
                source_channel AS connector_type,
                source_endpoint_identity AS endpoint_identity,
                target_butler,
                $1,
                message_count
            FROM fanout_aggregates
            """,
            day,
        )

        # Verify fanout data
        rows = await pool.fetch(
            """
            SELECT * FROM connector_fanout_daily
            WHERE day = $1
            ORDER BY connector_type, endpoint_identity, target_butler
            """,
            day,
        )

        assert len(rows) == 3

        # Check all rows without assuming specific order beyond the ORDER BY clause
        # Email comes before telegram_bot alphabetically
        assert rows[0]["connector_type"] == "email"
        assert rows[0]["endpoint_identity"] == "email.user@example.com"
        assert rows[0]["target_butler"] == "general"
        assert rows[0]["message_count"] == 1

        # telegram_bot.bot@789 -> health (2 messages)
        assert rows[1]["connector_type"] == "telegram_bot"
        assert rows[1]["endpoint_identity"] == "telegram_bot.bot@789"
        assert rows[1]["target_butler"] == "health"
        assert rows[1]["message_count"] == 2

        # telegram_bot.bot@789 -> relationship (1 message)
        assert rows[2]["connector_type"] == "telegram_bot"
        assert rows[2]["endpoint_identity"] == "telegram_bot.bot@789"
        assert rows[2]["target_butler"] == "relationship"
        assert rows[2]["message_count"] == 1


# Pruning tests


async def test_pruning_removes_old_hourly_and_daily_data(provisioned_postgres_pool):
    """Test that pruning correctly removes old data."""
    async with provisioned_postgres_pool() as pool:
        await _create_test_schema(pool)

        now = datetime.now(UTC)

        # Insert old hourly data (35 days ago)
        old_hour = now - timedelta(days=35)
        await pool.execute(
            """
            INSERT INTO connector_stats_hourly (
                connector_type, endpoint_identity, hour, messages_ingested
            ) VALUES ('telegram_bot', 'bot@old', $1, 100)
            """,
            old_hour.replace(minute=0, second=0, microsecond=0),
        )

        # Insert recent hourly data (25 days ago)
        recent_hour = now - timedelta(days=25)
        await pool.execute(
            """
            INSERT INTO connector_stats_hourly (
                connector_type, endpoint_identity, hour, messages_ingested
            ) VALUES ('telegram_bot', 'bot@recent', $1, 200)
            """,
            recent_hour.replace(minute=0, second=0, microsecond=0),
        )

        # Insert old daily data (400 days ago)
        old_day = (now - timedelta(days=400)).date()
        await pool.execute(
            """
            INSERT INTO connector_stats_daily (
                connector_type, endpoint_identity, day, messages_ingested
            ) VALUES ('telegram_bot', 'bot@old', $1, 1000)
            """,
            old_day,
        )

        # Prune hourly stats (older than 30 days)
        hourly_cutoff = now - timedelta(days=30)
        await pool.execute(
            """
            DELETE FROM connector_stats_hourly WHERE hour < $1
            """,
            hourly_cutoff,
        )

        # Prune daily stats (older than 1 year)
        daily_cutoff = (now - timedelta(days=365)).date()
        await pool.execute(
            """
            DELETE FROM connector_stats_daily WHERE day < $1
            """,
            daily_cutoff,
        )

        # Verify old hourly data is gone
        old_count = await pool.fetchval(
            "SELECT COUNT(*) FROM connector_stats_hourly WHERE endpoint_identity = 'bot@old'"
        )
        assert old_count == 0

        # Verify recent hourly data remains
        recent_count = await pool.fetchval(
            """
            SELECT COUNT(*) FROM connector_stats_hourly
            WHERE endpoint_identity = 'bot@recent'
            """
        )
        assert recent_count == 1

        # Verify old daily data is gone
        daily_count = await pool.fetchval(
            "SELECT COUNT(*) FROM connector_stats_daily WHERE endpoint_identity = 'bot@old'"
        )
        assert daily_count == 0
