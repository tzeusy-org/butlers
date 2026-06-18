"""Deferred retention pruning sweeps for high-growth tables.

Each pruner targets one specific table identified in the bu-dl98i.7.6 retention
audit (docs/operations/data-retention.md) as having a documented TTL/policy but
no automated sweep.

SAFETY CONTRACT — owner-data protection:
- All pruners are **disabled by default**.  With no configuration they do nothing.
- Each pruner supports a ``dry_run`` mode that logs/counts candidates WITHOUT
  deleting.  Dry-run is the safe default even when a pruner is enabled.
- Actual deletion requires two explicit flags: ``enabled=True`` **and**
  ``dry_run=False``.
- Deletes are bounded by a ``batch_limit`` to avoid long-running transactions.
- Counts of candidates and deletions are always logged.

Enabling pruners
----------------
Pruners are invoked as deterministic scheduled jobs.  To enable one, add a
scheduled task to the butler's ``butler.toml`` with ``job_type="deterministic"``
and pass ``enabled=true`` + ``dry_run=false`` via ``job_args``.  Example::

    [[schedule]]
    name         = "session_process_logs_prune"
    cron         = "0 3 * * *"    # 03:00 UTC daily
    job_type     = "deterministic"
    job_name     = "session_process_logs_prune"
    job_args     = {enabled = true, dry_run = false, batch_limit = 500}

Supported tables
----------------
[A] {butler_schema}.session_process_logs — 14-day TTL via ``expires_at``
[B] connectors.filtered_events            — monthly partitions older than N months
[C] public.insight_candidates             — delivered/filtered rows after 90 days
[D] public.secret_probe_log               — rows older than 90 days

See docs/operations/data-retention.md for policy rationale.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# [A] session_process_logs — 14-day TTL sweep
# ---------------------------------------------------------------------------

_SESSION_PROCESS_LOGS_TTL_DAYS = 14


async def prune_session_process_logs(
    pool: asyncpg.Pool,
    *,
    schema: str,
    enabled: bool = False,
    dry_run: bool = True,
    batch_limit: int = 500,
) -> dict[str, Any]:
    """Delete expired rows from ``{schema}.session_process_logs``.

    Rows are eligible when ``expires_at < now()``.  The TTL column and default
    are set by the migration (``core_001``); this sweep just reaps what has
    already expired.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    schema:
        Butler schema name (e.g. ``"general"``).  The table is per-butler
        and lives in the butler's own schema.
    enabled:
        Must be ``True`` to run.  When ``False`` the function returns
        immediately without touching the DB.
    dry_run:
        When ``True`` (the default), log what *would* be deleted without
        actually deleting.  Set to ``False`` together with ``enabled=True``
        to perform actual deletions.
    batch_limit:
        Maximum rows to delete per invocation (bounded delete).

    Returns
    -------
    dict
        ``{"candidates": int, "deleted": int, "dry_run": bool, "enabled": bool}``
    """
    if not enabled:
        logger.debug(
            "session_process_logs pruner is disabled (schema=%r); skipping",
            schema,
        )
        return {"candidates": 0, "deleted": 0, "dry_run": dry_run, "enabled": False}

    table = f"{schema}.session_process_logs"

    # Count candidates
    count_row = await pool.fetchrow(
        f"SELECT COUNT(*) AS n FROM {table} WHERE expires_at < now()",  # noqa: S608
    )
    candidates: int = count_row["n"] if count_row else 0

    logger.info(
        "session_process_logs prune: schema=%r candidates=%d dry_run=%s batch_limit=%d",
        schema,
        candidates,
        dry_run,
        batch_limit,
    )

    if candidates == 0:
        return {"candidates": 0, "deleted": 0, "dry_run": dry_run, "enabled": True}

    if dry_run:
        logger.info(
            "session_process_logs prune DRY RUN: would delete up to %d of %d expired rows",
            min(candidates, batch_limit),
            candidates,
        )
        return {"candidates": candidates, "deleted": 0, "dry_run": True, "enabled": True}

    result = await pool.execute(
        f"""
        DELETE FROM {table}
        WHERE session_id IN (
            SELECT session_id FROM {table}
            WHERE expires_at < now()
            LIMIT $1
        )
        """,
        batch_limit,
    )
    deleted = int(result.split()[-1])
    logger.info(
        "session_process_logs prune: schema=%r deleted=%d (of %d candidates)",
        schema,
        deleted,
        candidates,
    )
    return {"candidates": candidates, "deleted": deleted, "dry_run": False, "enabled": True}


# ---------------------------------------------------------------------------
# [B] connectors.filtered_events — monthly partition DROP sweep
# ---------------------------------------------------------------------------

_FILTERED_EVENTS_DEFAULT_KEEP_MONTHS = 12


async def prune_filtered_events_partitions(
    pool: asyncpg.Pool,
    *,
    enabled: bool = False,
    dry_run: bool = True,
    keep_months: int = _FILTERED_EVENTS_DEFAULT_KEEP_MONTHS,
) -> dict[str, Any]:
    """Drop old monthly partitions of ``connectors.filtered_events``.

    Partitions are named ``filtered_events_YYYYMM``.  Any partition whose
    month is strictly older than ``keep_months`` before the *current* month
    is eligible for a ``DROP TABLE``.

    Safety gates:
    - Only partitions whose names match ``filtered_events_YYYYMM`` are touched.
    - The current month and the previous ``keep_months - 1`` months are always
      retained.
    - Partitions are confirmed to exist via ``information_schema`` before drop.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    enabled:
        Must be ``True`` to run.
    dry_run:
        When ``True`` (default), log what would be dropped without dropping.
    keep_months:
        Number of calendar months to retain (including the current month).
        Must be >= 1.  Default: 12.

    Returns
    -------
    dict
        ``{"partitions_eligible": list[str], "partitions_dropped": list[str],
           "dry_run": bool, "enabled": bool}``
    """
    if not enabled:
        logger.debug("filtered_events partition pruner is disabled; skipping")
        return {
            "partitions_eligible": [],
            "partitions_dropped": [],
            "dry_run": dry_run,
            "enabled": False,
        }

    if keep_months < 1:
        raise ValueError("keep_months must be >= 1")

    now = datetime.now(UTC)
    # Cutoff: the first month that should be DROPPED (i.e. older than keep_months ago)
    # If keep_months=12 and today is 2026-06-18, retain 2025-07 through 2026-06.
    # Cutoff month = 2025-06 (anything <= 2025-06 is dropped).
    cutoff_year = now.year
    cutoff_month = now.month - keep_months  # may be <= 0
    while cutoff_month <= 0:
        cutoff_month += 12
        cutoff_year -= 1
    # cutoff is the last month to DROP (inclusive); partitions for months <= this are eligible
    cutoff_label = f"{cutoff_year:04d}{cutoff_month:02d}"

    # Discover existing partition tables via information_schema
    rows = await pool.fetch(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'connectors'
          AND table_name LIKE 'filtered_events_%'
          AND table_type = 'BASE TABLE'
        ORDER BY table_name ASC
        """,
    )

    eligible: list[str] = []
    for row in rows:
        name: str = row["table_name"]
        # Extract YYYYMM suffix
        suffix = name.removeprefix("filtered_events_")
        if len(suffix) != 6 or not suffix.isdigit():
            continue  # skip non-YYYYMM names
        if suffix <= cutoff_label:
            eligible.append(name)

    logger.info(
        "filtered_events partition prune: keep_months=%d cutoff=%s eligible=%d dry_run=%s",
        keep_months,
        cutoff_label,
        len(eligible),
        dry_run,
    )

    if not eligible:
        return {
            "partitions_eligible": [],
            "partitions_dropped": [],
            "dry_run": dry_run,
            "enabled": True,
        }

    if dry_run:
        logger.info(
            "filtered_events partition prune DRY RUN: would drop partitions: %s",
            eligible,
        )
        return {
            "partitions_eligible": eligible,
            "partitions_dropped": [],
            "dry_run": True,
            "enabled": True,
        }

    dropped: list[str] = []
    for name in eligible:
        try:
            await pool.execute(f'DROP TABLE IF EXISTS connectors."{name}"')
            dropped.append(name)
            logger.info("Dropped partition connectors.%r", name)
        except Exception:
            logger.exception("Failed to drop partition connectors.%r — skipping", name)

    logger.info(
        "filtered_events partition prune: dropped %d of %d eligible partitions",
        len(dropped),
        len(eligible),
    )
    return {
        "partitions_eligible": eligible,
        "partitions_dropped": dropped,
        "dry_run": False,
        "enabled": True,
    }


# ---------------------------------------------------------------------------
# [C] public.insight_candidates — delivered/filtered cleanup
# ---------------------------------------------------------------------------

_INSIGHT_CANDIDATES_DEFAULT_TTL_DAYS = 90


async def prune_insight_candidates(
    pool: asyncpg.Pool,
    *,
    enabled: bool = False,
    dry_run: bool = True,
    ttl_days: int = _INSIGHT_CANDIDATES_DEFAULT_TTL_DAYS,
    batch_limit: int = 500,
) -> dict[str, Any]:
    """Delete old non-pending rows from ``public.insight_candidates``.

    Only rows in terminal states (``delivered``, ``filtered``, ``expired``)
    older than ``ttl_days`` since ``created_at`` are eligible.  ``pending``
    rows are never touched.

    The partial index ``idx_insight_candidates_cleanup`` on
    ``(created_at) WHERE status <> 'pending'`` (created by ``core_010``) makes
    this scan efficient.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    enabled:
        Must be ``True`` to run.
    dry_run:
        When ``True`` (default), count without deleting.
    ttl_days:
        Age threshold in days (measured from ``created_at``).  Default: 90.
    batch_limit:
        Maximum rows to delete per invocation.

    Returns
    -------
    dict
        ``{"candidates": int, "deleted": int, "dry_run": bool, "enabled": bool}``
    """
    if not enabled:
        logger.debug("insight_candidates pruner is disabled; skipping")
        return {"candidates": 0, "deleted": 0, "dry_run": dry_run, "enabled": False}

    cutoff = datetime.now(UTC) - timedelta(days=ttl_days)

    count_row = await pool.fetchrow(
        """
        SELECT COUNT(*) AS n
        FROM public.insight_candidates
        WHERE status <> 'pending'
          AND created_at < $1
        """,
        cutoff,
    )
    candidates: int = count_row["n"] if count_row else 0

    logger.info(
        "insight_candidates prune: ttl_days=%d candidates=%d dry_run=%s batch_limit=%d",
        ttl_days,
        candidates,
        dry_run,
        batch_limit,
    )

    if candidates == 0:
        return {"candidates": 0, "deleted": 0, "dry_run": dry_run, "enabled": True}

    if dry_run:
        logger.info(
            "insight_candidates prune DRY RUN: would delete up to %d of %d rows",
            min(candidates, batch_limit),
            candidates,
        )
        return {"candidates": candidates, "deleted": 0, "dry_run": True, "enabled": True}

    result = await pool.execute(
        """
        DELETE FROM public.insight_candidates
        WHERE id IN (
            SELECT id FROM public.insight_candidates
            WHERE status <> 'pending'
              AND created_at < $1
            LIMIT $2
        )
        """,
        cutoff,
        batch_limit,
    )
    deleted = int(result.split()[-1])
    logger.info("insight_candidates prune: deleted=%d (of %d candidates)", deleted, candidates)
    return {"candidates": candidates, "deleted": deleted, "dry_run": False, "enabled": True}


# ---------------------------------------------------------------------------
# [D] public.secret_probe_log — 90-day pruning
# ---------------------------------------------------------------------------

_SECRET_PROBE_LOG_DEFAULT_TTL_DAYS = 90


async def prune_secret_probe_log(
    pool: asyncpg.Pool,
    *,
    enabled: bool = False,
    dry_run: bool = True,
    ttl_days: int = _SECRET_PROBE_LOG_DEFAULT_TTL_DAYS,
    batch_limit: int = 500,
) -> dict[str, Any]:
    """Delete probe log rows older than ``ttl_days`` from ``public.secret_probe_log``.

    The ``core_105`` migration spec declares "Retention: ≥ 90 days".  This
    pruner enforces that floor: only rows where ``recorded_at`` is older than
    ``ttl_days`` are eligible.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    enabled:
        Must be ``True`` to run.
    dry_run:
        When ``True`` (default), count without deleting.
    ttl_days:
        Age threshold in days (measured from ``recorded_at``).  Must be >= 90
        to honour the spec's minimum.  Default: 90.
    batch_limit:
        Maximum rows to delete per invocation.

    Returns
    -------
    dict
        ``{"candidates": int, "deleted": int, "dry_run": bool, "enabled": bool}``

    Raises
    ------
    ValueError
        If ``ttl_days < 90``, which would violate the core_105 spec minimum.
    """
    if not enabled:
        logger.debug("secret_probe_log pruner is disabled; skipping")
        return {"candidates": 0, "deleted": 0, "dry_run": dry_run, "enabled": False}

    if ttl_days < 90:
        raise ValueError(
            f"secret_probe_log ttl_days must be >= 90 (got {ttl_days}); "
            "the core_105 spec mandates ≥ 90-day retention"
        )

    cutoff = datetime.now(UTC) - timedelta(days=ttl_days)

    count_row = await pool.fetchrow(
        "SELECT COUNT(*) AS n FROM public.secret_probe_log WHERE recorded_at < $1",
        cutoff,
    )
    candidates: int = count_row["n"] if count_row else 0

    logger.info(
        "secret_probe_log prune: ttl_days=%d candidates=%d dry_run=%s batch_limit=%d",
        ttl_days,
        candidates,
        dry_run,
        batch_limit,
    )

    if candidates == 0:
        return {"candidates": 0, "deleted": 0, "dry_run": dry_run, "enabled": True}

    if dry_run:
        logger.info(
            "secret_probe_log prune DRY RUN: would delete up to %d of %d rows",
            min(candidates, batch_limit),
            candidates,
        )
        return {"candidates": candidates, "deleted": 0, "dry_run": True, "enabled": True}

    result = await pool.execute(
        """
        DELETE FROM public.secret_probe_log
        WHERE id IN (
            SELECT id FROM public.secret_probe_log
            WHERE recorded_at < $1
            LIMIT $2
        )
        """,
        cutoff,
        batch_limit,
    )
    deleted = int(result.split()[-1])
    logger.info("secret_probe_log prune: deleted=%d (of %d candidates)", deleted, candidates)
    return {"candidates": candidates, "deleted": deleted, "dry_run": False, "enabled": True}
