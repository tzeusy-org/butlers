"""Rule promotion trigger job — periodic routing_verdict_log scan.

bu-wuwy9 (rule-promotion bead 3 of 7). Scheduled via ``butler.toml``
(``dispatch_mode = "job"``, ``job_name = "rule_promotion_trigger"``); loaded
dynamically by ``src/butlers/scheduled_jobs.py`` the same way as
``eligibility_sweep.py``. Thin wrapper: validates ``job_args`` and delegates
to ``butlers.tools.switchboard.routing.rule_promotion.run_rule_promotion_trigger``
for the actual scan/gate/classify/insert logic.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

import asyncpg

from butlers.tools.switchboard.routing.rule_promotion import (
    DEFAULT_LOOKBACK_WINDOW,
    DEFAULT_MIN_DISTINCT_DAYS,
    DEFAULT_MIN_ELAPSED_FLOOR,
    DEFAULT_PROMOTION_THRESHOLD,
    run_rule_promotion_trigger,
)

logger = logging.getLogger(__name__)

_VALID_JOB_ARGS = frozenset(
    {"threshold", "min_distinct_days", "min_elapsed_hours", "lookback_days"}
)


async def run_rule_promotion_trigger_job(
    db_pool: asyncpg.Pool,
    job_args: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Scheduled job to scan routing_verdict_log and propose rule promotions.

    ``job_args`` (all optional, override the module defaults):
        threshold: int          — N-consecutive-same-verdict count (default 3).
        min_distinct_days: int  — min distinct UTC calendar days (default 2).
        min_elapsed_hours: float — min elapsed hours between oldest/newest
            evidence (default 20).
        lookback_days: float    — candidate-scan lookback window (default 30).

    Returns the ``PromotionTriggerResult`` summary dict (candidates_scanned,
    suggestions_created, suggestions_bumped, and per-reason skip counters).
    """
    threshold = DEFAULT_PROMOTION_THRESHOLD
    min_distinct_days = DEFAULT_MIN_DISTINCT_DAYS
    min_elapsed = DEFAULT_MIN_ELAPSED_FLOOR
    lookback = DEFAULT_LOOKBACK_WINDOW

    if job_args:
        unknown_args = sorted(set(job_args) - _VALID_JOB_ARGS)
        if unknown_args:
            raise ValueError(
                "rule_promotion_trigger job only supports job_args "
                f"{sorted(_VALID_JOB_ARGS)}; got unknown key(s): {unknown_args}"
            )

        if "threshold" in job_args:
            threshold = int(job_args["threshold"])
            if threshold < 2:
                raise ValueError("rule_promotion_trigger job_args.threshold must be >= 2")

        if "min_distinct_days" in job_args:
            min_distinct_days = int(job_args["min_distinct_days"])
            if min_distinct_days < 1:
                raise ValueError("rule_promotion_trigger job_args.min_distinct_days must be >= 1")

        if "min_elapsed_hours" in job_args:
            hours = float(job_args["min_elapsed_hours"])
            if hours < 0:
                raise ValueError("rule_promotion_trigger job_args.min_elapsed_hours must be >= 0")
            min_elapsed = timedelta(hours=hours)

        if "lookback_days" in job_args:
            days = float(job_args["lookback_days"])
            if days <= 0:
                raise ValueError("rule_promotion_trigger job_args.lookback_days must be > 0")
            lookback = timedelta(days=days)

    result = await run_rule_promotion_trigger(
        db_pool,
        threshold=threshold,
        min_distinct_days=min_distinct_days,
        min_elapsed=min_elapsed,
        lookback=lookback,
    )
    logger.info("Rule promotion trigger job completed: %s", result)
    return result
