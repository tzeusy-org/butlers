"""Create connector statistics rollup tables.

Revision ID: sw_014
Revises: sw_013
Create Date: 2026-02-16 00:00:00.000000

Migration notes:
- Upgrade creates three rollup tables: connector_stats_hourly, connector_stats_daily,
  and connector_fanout_daily.
- connector_stats_hourly tracks volume and health metrics aggregated by hour.
- connector_stats_daily tracks daily aggregates with uptime percentage.
- connector_fanout_daily tracks message dispatch counts by target butler.
- Downgrade drops all three tables.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "sw_014"
down_revision = "sw_013b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create connector_stats_hourly table
    op.execute(
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

    # Create indexes for connector_stats_hourly
    op.execute(
        """
        CREATE INDEX ix_connector_stats_hourly_hour
        ON connector_stats_hourly (hour DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_connector_stats_hourly_connector_type_hour
        ON connector_stats_hourly (connector_type, hour DESC)
        """
    )

    # Create connector_stats_daily table
    op.execute(
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

    # Create indexes for connector_stats_daily
    op.execute(
        """
        CREATE INDEX ix_connector_stats_daily_day
        ON connector_stats_daily (day DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_connector_stats_daily_connector_type_day
        ON connector_stats_daily (connector_type, day DESC)
        """
    )

    # Create connector_fanout_daily table
    op.execute(
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

    # Create indexes for connector_fanout_daily
    op.execute(
        """
        CREATE INDEX ix_connector_fanout_daily_day
        ON connector_fanout_daily (day DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_connector_fanout_daily_connector_type_day
        ON connector_fanout_daily (connector_type, day DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_connector_fanout_daily_target_butler_day
        ON connector_fanout_daily (target_butler, day DESC)
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS connector_fanout_daily")
    op.execute("DROP TABLE IF EXISTS connector_stats_daily")
    op.execute("DROP TABLE IF EXISTS connector_stats_hourly")
