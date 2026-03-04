"""Connector statistics aggregation jobs — DEPRECATED.

This module previously implemented scheduled jobs for rolling up connector
heartbeat data into hourly/daily statistics tables.  These rollup jobs have
been replaced by the OTel/Prometheus-native metrics pipeline (butlers-ufzc).

The three rollup tables (connector_stats_hourly, connector_stats_daily,
connector_fanout_daily) are dropped by migration sw_025.  The dashboard API
endpoints now query Prometheus via PromQL instead.

The functions in this module are retained as no-ops so that any lingering
schedule entries or code references do not cause import errors.  They should
be removed once butlers-x02f (clean up rollup references) is resolved.
"""

from __future__ import annotations

import logging

import asyncpg

logger = logging.getLogger(__name__)


async def run_connector_stats_hourly_rollup(db_pool: asyncpg.Pool) -> dict[str, int]:
    """No-op stub — replaced by OTel/Prometheus metrics pipeline.

    The rollup pipeline was removed in butlers-ufzc.  This function is
    retained only for backward-compat with lingering schedule entries.
    """
    logger.info(
        "connector_stats_hourly_rollup: no-op (rollup pipeline replaced by OTel/Prometheus)"
    )
    return {"rows_processed": 0, "connectors_updated": 0}


async def run_connector_stats_daily_rollup(db_pool: asyncpg.Pool) -> dict[str, int]:
    """No-op stub — replaced by OTel/Prometheus metrics pipeline.

    The rollup pipeline was removed in butlers-ufzc.  This function is
    retained only for backward-compat with lingering schedule entries.
    """
    logger.info("connector_stats_daily_rollup: no-op (rollup pipeline replaced by OTel/Prometheus)")
    return {"stats_updated": 0, "fanout_updated": 0}


async def run_connector_stats_pruning(db_pool: asyncpg.Pool) -> dict[str, int]:
    """No-op stub — replaced by OTel/Prometheus metrics pipeline.

    The rollup tables were dropped by migration sw_025.  Heartbeat log
    partition pruning is handled separately.
    """
    logger.info(
        "connector_stats_pruning: no-op (rollup tables dropped; "
        "heartbeat log pruning handled separately)"
    )
    return {
        "heartbeat_partitions_dropped": 0,
        "hourly_rows_deleted": 0,
        "daily_rows_deleted": 0,
        "fanout_rows_deleted": 0,
    }
