"""HaEnvironmentReader factory for the health butler's insight-scan job.

Health has no Home Assistant connector or entity-snapshot table of its own --
``ha_entity_snapshot`` is populated only by the home butler's schema (see
``roster/home/migrations/001_home_tables.py``). There is therefore no source
health can query to discover which HA sensor entities to correlate against
sleep/symptom data, so environment correlation is not available for health
today. This factory always returns ``None`` so the insight-scan job skips
that correlation cleanly, matching the ``HaEnvironmentReader`` type alias's
``None``-means-unavailable contract defined in
``roster/health/jobs/health_jobs.py``::

    HaEnvironmentReader = Callable[[], Awaitable[list[dict[str, Any]]]]

Usage in the scheduled job::

    from butlers.jobs.health_ha_reader import build_ha_environment_reader

    ha_reader = await build_ha_environment_reader(pool)
    result = await run_insight_scan(pool, ha_environment_reader=ha_reader)
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)


async def build_ha_environment_reader(
    pool: asyncpg.Pool,
) -> Callable[[], Awaitable[list[dict[str, Any]]]] | None:
    """Return None: health has no HA environment-entity source to read from.

    Args:
        pool: asyncpg connection pool bound to the health butler's schema.
            Unused; kept for call-site compatibility with the insight-scan
            job's reader-factory signature.

    Returns:
        None, always -- so the caller skips environment correlation cleanly.
    """
    del pool
    logger.info(
        "health insight scan: no HA environment-entity source for health; "
        "environment correlation will be skipped"
    )
    return None
