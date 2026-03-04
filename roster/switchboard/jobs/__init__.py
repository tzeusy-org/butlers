"""Scheduled jobs for the Switchboard butler."""

from roster.switchboard.jobs.eligibility_sweep import run_eligibility_sweep_job

__all__ = [
    "run_eligibility_sweep_job",
]
