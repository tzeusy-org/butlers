"""Scheduled jobs for the Switchboard butler."""

from roster.switchboard.jobs.connector_stats import (
    run_connector_stats_daily_rollup,
    run_connector_stats_hourly_rollup,
    run_connector_stats_pruning,
)
from roster.switchboard.jobs.eligibility_sweep import run_eligibility_sweep_job

__all__ = [
    "run_connector_stats_hourly_rollup",
    "run_connector_stats_daily_rollup",
    "run_connector_stats_pruning",
    "run_eligibility_sweep_job",
]
