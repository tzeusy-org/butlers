"""Drop connector statistics rollup tables (replaced by OTel/Prometheus pipeline).

Revision ID: sw_025
Revises: sw_024
Create Date: 2026-03-04 00:00:00.000000

Migration notes:
- Drops the three rollup tables created by sw_014:
  connector_stats_hourly, connector_stats_daily, connector_fanout_daily.
- The rollup pipeline (hourly/daily/fanout cron jobs) has been replaced by the
  OTel/Prometheus-native metrics stack (butlers-ufzc).  Dashboard API endpoints
  now query Prometheus via PromQL instead of these tables.
- Downgrade re-creates the tables (empty; historical data is not preserved).
- connector_heartbeat_log is retained; it is still used by the connector detail
  endpoint for today_ingested and by heartbeat processing.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "sw_025"
down_revision = "sw_024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop all three rollup tables and their indexes.
    op.execute("DROP TABLE IF EXISTS connector_fanout_daily CASCADE")
    op.execute("DROP TABLE IF EXISTS connector_stats_daily CASCADE")
    op.execute("DROP TABLE IF EXISTS connector_stats_hourly CASCADE")


def downgrade() -> None:
    # Re-create the tables (empty — historical rollup data is not preserved).
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS connector_stats_hourly (
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
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_connector_stats_hourly_hour
        ON connector_stats_hourly (hour DESC)
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS connector_stats_daily (
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
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_connector_stats_daily_day
        ON connector_stats_daily (day DESC)
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS connector_fanout_daily (
            connector_type TEXT NOT NULL,
            endpoint_identity TEXT NOT NULL,
            target_butler TEXT NOT NULL,
            day DATE NOT NULL,
            message_count BIGINT DEFAULT 0,
            PRIMARY KEY (connector_type, endpoint_identity, target_butler, day)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_connector_fanout_daily_day
        ON connector_fanout_daily (day DESC)
        """
    )
