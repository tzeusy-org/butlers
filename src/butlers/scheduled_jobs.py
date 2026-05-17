"""Deterministic scheduled job implementations for the Butler daemon.

These handlers are invoked by the daemon's scheduler for named deterministic
schedule jobs (job_type="deterministic"). Each handler receives the DB pool
and optional job_args dict, and returns a result dict.

The registry maps butler_name → job_name → handler function.
"""

from __future__ import annotations

import functools
import logging
import sys
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

# Sentinel used when a retention policy row explicitly sets max_rows = NULL
# (meaning "no row cap").  Passing this to run_episode_cleanup ensures the
# capacity-enforcement branch never fires without requiring a signature change.
_NO_ROW_CAP: int = sys.maxsize

type _DeterministicScheduleJobHandler = Callable[
    [asyncpg.Pool, dict[str, Any] | None], Awaitable[Any]
]


_CHRONICLER_INTERNAL_SCHEMAS = frozenset(
    {
        "connector",
        "information_schema",
        "pg_catalog",
        "public",
        "shared",
    }
)


# ---------------------------------------------------------------------------
# Switchboard jobs
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def _load_switchboard_eligibility_sweep_job() -> Callable[
    [asyncpg.Pool], Awaitable[dict[str, Any]]
]:
    """Load the switchboard eligibility sweep job from roster/ by file path."""
    import importlib.util as _ilu

    module_path = (
        Path(__file__).resolve().parents[2]
        / "roster"
        / "switchboard"
        / "jobs"
        / "eligibility_sweep.py"
    )
    module_name = "roster_switchboard_eligibility_sweep_job"
    spec = _ilu.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load switchboard eligibility sweep job from {module_path}")
    module = _ilu.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.run_eligibility_sweep_job


async def _run_switchboard_eligibility_sweep_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run the switchboard eligibility sweep deterministic schedule job."""
    del job_args
    run_eligibility_sweep_job = _load_switchboard_eligibility_sweep_job()
    return await run_eligibility_sweep_job(pool)


async def _run_switchboard_insight_delivery_cycle_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run the proactive insight delivery cycle for the Switchboard butler.

    Orchestrates the full 10-step insight delivery pipeline:
    quiet-hours check, expiry, cooldown filter, dedup, budget computation,
    top-B selection, delivery, cooldown recording, engagement tracking,
    and cleanup.

    Passes ``notify_fn=None`` — delivery_cycle will skip the actual delivery
    step and return ``skipped=True`` until the Switchboard notify path is
    fully integrated. No candidates are consumed or marked delivered.
    """
    del job_args
    from butlers.tools.switchboard.insight.broker import delivery_cycle

    return await delivery_cycle(pool, notify_fn=None)


async def _run_switchboard_spend_rule_savings_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Compute and persist 7-day savings per spend rule (§5.4).

    Runs daily (scheduled at 04:15 UTC by default) and updates
    ``public.spend_rules.saved_7d`` with the difference between the
    workhorse-tier baseline cost and the actual cost incurred by each
    rule's chosen model over the trailing 7 days.
    """
    del job_args
    from butlers.jobs.spend import compute_spend_rule_savings

    return await compute_spend_rule_savings(pool)


# ---------------------------------------------------------------------------
# Memory maintenance jobs
# ---------------------------------------------------------------------------


async def _run_memory_consolidation_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run memory consolidation directly without spawning an LLM runtime session."""
    del job_args
    from butlers.modules.memory.consolidation import run_consolidation

    return await run_consolidation(pool=pool, embedding_engine=None, cc_spawner=None)


async def _fetch_retention_policy(pool: asyncpg.Pool, kind: str) -> dict[str, Any]:
    """Fetch a row from public.memory_retention_policies by kind.

    Falls back to an empty dict (no policy) when the table does not exist
    (migration core_096 not yet applied) so the cleanup jobs remain safe to
    run on un-migrated databases.
    """
    try:
        row = await pool.fetchrow(
            "SELECT ttl_days, max_rows FROM public.memory_retention_policies WHERE kind = $1",
            kind,
        )
        if row is not None:
            return {"ttl_days": row["ttl_days"], "max_rows": row["max_rows"]}
    except Exception:
        pass
    return {}


async def _table_size_bytes(pool: asyncpg.Pool, table_name: str) -> int | None:
    """Return pg_total_relation_size for *table_name* resolved via the current search_path.

    Uses ``to_regclass`` so an absent table returns NULL rather than raising.
    Any unexpected error is caught and returns None so callers remain best-effort.
    """
    try:
        return await pool.fetchval(
            "SELECT pg_total_relation_size(to_regclass($1))",
            table_name,
        )
    except Exception:
        logger.debug("Could not measure size for table %r", table_name, exc_info=True)
        return None


async def _log_compaction(
    pool: asyncpg.Pool, kind: str, rows_removed: int, *, bytes_freed: int | None = None
) -> None:
    """Insert one row into public.memory_compaction_log; best-effort (no raise)."""
    try:
        await pool.execute(
            "INSERT INTO public.memory_compaction_log (kind, rows_removed, bytes_freed)"
            " VALUES ($1, $2, $3)",
            kind,
            rows_removed,
            bytes_freed,
        )
    except Exception:
        logger.debug(
            "Failed to log compaction for kind=%r rows_removed=%d",
            kind,
            rows_removed,
            exc_info=True,
        )


async def _run_memory_episode_cleanup_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run memory episode cleanup directly without spawning an LLM runtime session.

    Consults public.memory_retention_policies for 'event' and 'transcript' kinds
    to determine the max_rows cap.  Falls back to the default (10 000) when the
    policy table is not yet available (migration core_096 not applied).

    Logs the number of removed rows to public.memory_compaction_log after each run.
    """
    from butlers.modules.memory.consolidation import run_episode_cleanup

    # Load policy from DB (kind='event' governs general episode capacity).
    policy = await _fetch_retention_policy(pool, "event")
    # "max_rows" absent  → table not yet migrated → fall back to 10 000.
    # "max_rows" = None  → explicit "no limit" in DB → use sys.maxsize so the
    #                       capacity step in run_episode_cleanup never triggers.
    if "max_rows" not in policy:
        max_entries = 10000
    elif policy["max_rows"] is None:
        max_entries = _NO_ROW_CAP
    else:
        max_entries = int(policy["max_rows"])

    # job_args override takes precedence for backward compatibility.
    if job_args is not None:
        unknown_args = sorted(set(job_args) - {"max_entries"})
        if unknown_args:
            raise RuntimeError(
                "memory_episode_cleanup job only supports job_args.max_entries; "
                f"received unsupported keys: {unknown_args}"
            )
        if "max_entries" in job_args:
            raw_max_entries = job_args["max_entries"]
            if (
                not isinstance(raw_max_entries, int)
                or isinstance(raw_max_entries, bool)
                or raw_max_entries <= 0
            ):
                raise RuntimeError(
                    "memory_episode_cleanup job_args.max_entries must be a positive integer"
                )
            max_entries = raw_max_entries

    size_before = await _table_size_bytes(pool, "episodes")
    result = await run_episode_cleanup(pool=pool, max_entries=max_entries)
    total_removed = result.get("expired_deleted", 0) + result.get("capacity_deleted", 0)
    if total_removed > 0:
        size_after = await _table_size_bytes(pool, "episodes")
        bytes_freed: int | None = None
        if size_before is not None and size_after is not None:
            bytes_freed = max(0, size_before - size_after)
        await _log_compaction(pool, "event", total_removed, bytes_freed=bytes_freed)
    return result


async def _run_memory_purge_superseded_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Purge superseded facts older than a threshold.

    Consults public.memory_retention_policies for 'fact' kind to determine
    the ttl_days threshold.  Falls back to 7 days when the policy table is
    not yet available.

    Logs the number of removed rows to public.memory_compaction_log.
    """
    from butlers.modules.memory.storage import purge_superseded_facts

    policy = await _fetch_retention_policy(pool, "fact")
    # "ttl_days" absent  → table not yet migrated → fall back to 7.
    # "ttl_days" = None  → explicit "no TTL" in DB → skip purge (return early).
    if "ttl_days" not in policy:
        older_than_days: int | None = 7
    elif policy["ttl_days"] is None:
        older_than_days = None  # no TTL cap; skip purge below
    else:
        older_than_days = int(policy["ttl_days"])

    if job_args is not None and "older_than_days" in job_args:
        raw = job_args["older_than_days"]
        if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0:
            older_than_days = raw

    if older_than_days is None:
        # Policy explicitly says no TTL → skip fact purge.
        return {"superseded_deleted": 0, "ha_state_deleted": 0, "skipped": "no_ttl_policy"}

    size_before = await _table_size_bytes(pool, "facts")
    result = await purge_superseded_facts(pool, older_than_days=older_than_days)
    total_removed = result.get("superseded_deleted", 0) + result.get("ha_state_deleted", 0)
    if total_removed > 0:
        size_after = await _table_size_bytes(pool, "facts")
        bytes_freed: int | None = None
        if size_before is not None and size_after is not None:
            bytes_freed = max(0, size_before - size_after)
        await _log_compaction(pool, "fact", total_removed, bytes_freed=bytes_freed)
    return result


_MEMORY_MAINTENANCE_JOB_HANDLERS: dict[str, _DeterministicScheduleJobHandler] = {
    "memory_consolidation": _run_memory_consolidation_job,
    "memory_episode_cleanup": _run_memory_episode_cleanup_job,
    "memory_purge_superseded": _run_memory_purge_superseded_job,
}


# ---------------------------------------------------------------------------
# Chronicler projection jobs
# ---------------------------------------------------------------------------


async def _discover_chronicler_projection_schemas(
    pool: asyncpg.Pool,
    *,
    table_name: str,
) -> tuple[str, ...]:
    """Discover schema-qualified Chronicler read surfaces for one evidence table."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT table_schema
            FROM information_schema.tables
            WHERE table_name = $1
              AND table_schema != ALL($2::text[])
              AND table_schema NOT LIKE 'pg_%'
            ORDER BY table_schema ASC
            """,
            table_name,
            list(_CHRONICLER_INTERNAL_SCHEMAS),
        )
    return tuple(row["table_schema"] for row in rows)


async def _run_chronicler_project_sessions_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run Chronicler's core.sessions projection adapter."""
    del job_args
    from butlers.chronicler.adapters import CoreSessionsAdapter

    butler_schemas = await _discover_chronicler_projection_schemas(pool, table_name="sessions")
    result = await CoreSessionsAdapter(butler_schemas=butler_schemas).run(
        pool=pool,
        chronicler_pool=pool,
    )
    return asdict(result)


async def _run_chronicler_project_calendar_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run Chronicler's google_calendar.completed projection adapter."""
    del job_args
    from butlers.chronicler.adapters import CalendarCompletedAdapter

    butler_schemas = await _discover_chronicler_projection_schemas(
        pool,
        table_name="calendar_event_instances",
    )
    result = await CalendarCompletedAdapter(butler_schemas=butler_schemas).run(
        pool=pool,
        chronicler_pool=pool,
    )
    return asdict(result)


# ---------------------------------------------------------------------------
# Domain-specific briefing contribution jobs
# ---------------------------------------------------------------------------


async def _run_education_compute_analytics_snapshots_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run education analytics snapshot computation as a deterministic job."""
    del job_args
    from butlers.tools.education.analytics import analytics_compute_all

    count = await analytics_compute_all(pool=pool)
    return {"snapshots_computed": count}


async def _run_health_briefing_contribution_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run health butler daily briefing contribution job."""
    from butlers.jobs.briefing import run_health_briefing_contribution

    return await run_health_briefing_contribution(pool=pool, job_args=job_args)


async def _run_finance_briefing_contribution_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run finance butler daily briefing contribution job."""
    from butlers.jobs.briefing import run_finance_briefing_contribution

    return await run_finance_briefing_contribution(pool=pool, job_args=job_args)


async def _run_finance_insight_scan_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run finance butler insight scan job."""
    del job_args
    from butlers.jobs._roster_loader import load_roster_jobs

    mod = load_roster_jobs("finance")
    return await mod.run_insight_scan(pool)


async def _run_relationship_briefing_contribution_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run relationship butler daily briefing contribution job."""
    from butlers.jobs.briefing import run_relationship_briefing_contribution

    return await run_relationship_briefing_contribution(pool=pool, job_args=job_args)


async def _run_travel_briefing_contribution_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run travel butler daily briefing contribution job."""
    from butlers.jobs.briefing import run_travel_briefing_contribution

    return await run_travel_briefing_contribution(pool=pool, job_args=job_args)


async def _run_travel_insight_scan_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run travel butler insight scan job."""
    del job_args
    from butlers.jobs._roster_loader import load_roster_jobs

    mod = load_roster_jobs("travel")
    return await mod.run_insight_scan(pool)


async def _run_health_insight_scan_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run health butler insight scan job."""
    del job_args
    from butlers.jobs._roster_loader import load_roster_jobs

    mod = load_roster_jobs("health")
    return await mod.run_insight_scan(pool)


async def _run_relationship_insight_scan_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run relationship butler insight scan job."""
    del job_args
    from butlers.jobs._roster_loader import load_roster_jobs

    mod = load_roster_jobs("relationship")
    return await mod.run_insight_scan(pool)


async def _run_relationship_interaction_sync_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run relationship butler interaction sync job."""
    del job_args
    from butlers.jobs._roster_loader import load_roster_jobs

    mod = load_roster_jobs("relationship")
    return await mod.run_interaction_sync(pool)


async def _run_education_briefing_contribution_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run education butler daily briefing contribution job."""
    from butlers.jobs.briefing import run_education_briefing_contribution

    return await run_education_briefing_contribution(pool=pool, job_args=job_args)


async def _run_home_briefing_contribution_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run home butler daily briefing contribution job."""
    from butlers.jobs.briefing import run_home_briefing_contribution

    return await run_home_briefing_contribution(pool=pool, job_args=job_args)


async def _run_lifestyle_briefing_contribution_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run lifestyle butler daily briefing contribution job."""
    from butlers.jobs.briefing import run_lifestyle_briefing_contribution

    return await run_lifestyle_briefing_contribution(pool=pool, job_args=job_args)


async def _run_collect_briefing_contributions_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run collect-briefing-contributions aggregation job for the general butler.

    Reads contributions from ``general.v_briefing_contributions`` for today's
    date, validates each envelope, and writes the combined payload to
    ``briefing/combined/<YYYY-MM-DD>``.
    """
    del job_args
    from butlers.jobs.briefing import run_collect_briefing_contributions

    return await run_collect_briefing_contributions(pool=pool)


# ---------------------------------------------------------------------------
# Home butler jobs
# ---------------------------------------------------------------------------


async def _run_home_device_health_check_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run device health check job for the home butler.

    Reads ha_entity_snapshot, classifies battery and offline issues by severity,
    stores volatile memory facts for each issue, and sends a Telegram notification.
    """
    from butlers.jobs.home import run_device_health_check

    return await run_device_health_check(pool, job_args)


async def _run_home_environment_report_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run the daily environment report job for the Home butler.

    Delegates to ``butlers.jobs.home.run_environment_report``, which reads
    environmental sensors from ``ha_entity_snapshot``, compares against comfort
    preferences, and sends a room-by-room Telegram notification.
    """
    from butlers.jobs.home import run_environment_report

    return await run_environment_report(pool, job_args)


async def _run_home_energy_digest_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run weekly energy digest job for the home butler.

    Delegates to ``butlers.jobs.home.run_energy_digest`` which discovers energy
    sensors, fetches weekly statistics via HA REST API, computes top consumers,
    detects anomalies, and sends a structured digest via Telegram.
    """
    from butlers.jobs.home import run_energy_digest

    return await run_energy_digest(pool, job_args)


async def _run_home_maintenance_schedule_check_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run the home maintenance schedule check deterministic job.

    Queries home.maintenance_items for due/overdue/upcoming items, classifies
    by severity, and returns a structured summary.  Notification delivery
    requires a notify_fn to be wired in; the daemon passes None until the
    switchboard notify path is integrated.
    """
    from butlers.jobs.home import run_maintenance_schedule_check

    return await run_maintenance_schedule_check(pool, job_args)


_HOME_DETERMINISTIC_JOB_HANDLERS: dict[str, _DeterministicScheduleJobHandler] = {
    "device_health_check": _run_home_device_health_check_job,
    "environment_report": _run_home_environment_report_job,
    "energy_digest": _run_home_energy_digest_job,
    "maintenance_schedule_check": _run_home_maintenance_schedule_check_job,
}


# ---------------------------------------------------------------------------
# Chronicler jobs
# ---------------------------------------------------------------------------


async def _run_chronicler_project_sessions_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run the Chronicler core-session projection job."""
    from butlers.jobs.chronicler import run_project_sessions

    return await run_project_sessions(pool, job_args)


async def _run_chronicler_project_calendar_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run the Chronicler completed-calendar projection job."""
    from butlers.jobs.chronicler import run_project_calendar

    return await run_project_calendar(pool, job_args)


# ---------------------------------------------------------------------------
# QA butler jobs
# ---------------------------------------------------------------------------


async def _run_qa_patrol_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run the QA patrol cycle via the active QaModule instance."""
    del pool, job_args
    from butlers.modules.qa import get_active_instance

    qa = get_active_instance()
    if qa is None:
        logger.warning("qa_patrol job: QaModule not active — skipping")
        return {"skipped": True, "reason": "qa_module_not_active"}
    await qa.run_patrol_tick()
    return {"status": "completed"}


async def _run_qa_pr_status_check_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run the QA PR status check via the active QaModule instance."""
    del job_args
    from butlers.modules.qa import get_active_instance

    qa = get_active_instance()
    if qa is None:
        logger.warning("qa_pr_status_check job: QaModule not active — skipping")
        return {"skipped": True, "reason": "qa_module_not_active"}

    gh_token = await qa._resolve_gh_token()

    await qa._check_pr_statuses(pool, gh_token)
    return {"status": "completed"}


async def _run_qa_evidence_cleanup_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run the QA raw evidence retention cleanup via the active QaModule instance."""
    del pool, job_args
    from butlers.modules.qa import get_active_instance

    qa = get_active_instance()
    if qa is None:
        logger.warning("qa_evidence_cleanup job: QaModule not active — skipping")
        return {"skipped": True, "reason": "qa_module_not_active"}
    return await qa.run_scheduled_evidence_cleanup()


# ---------------------------------------------------------------------------
# Chronicler jobs
# ---------------------------------------------------------------------------


async def _run_chronicler_project_sessions_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run Chronicler's cross-butler sessions projection job."""
    from butlers.chronicler.jobs import run_project_sessions

    return await run_project_sessions(pool, job_args)


async def _run_chronicler_project_calendar_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run Chronicler's completed-calendar projection job."""
    from butlers.chronicler.jobs import run_project_calendar

    return await run_project_calendar(pool, job_args)


async def _run_chronicler_project_owntracks_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run Chronicler's OwnTracks point projection job."""
    from butlers.chronicler.jobs import run_project_owntracks

    return await run_project_owntracks(pool, job_args)


async def _run_chronicler_project_steam_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run Chronicler's Steam play-history projection job."""
    from butlers.chronicler.jobs import run_project_steam

    return await run_project_steam(pool, job_args)


async def _run_chronicler_project_meals_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run Chronicler's health meals projection job."""
    from butlers.chronicler.jobs import run_project_meals

    return await run_project_meals(pool, job_args)


async def _run_chronicler_project_home_assistant_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run Chronicler's Home Assistant history projection job."""
    from butlers.chronicler.jobs import run_project_home_assistant

    return await run_project_home_assistant(pool, job_args)


async def _run_chronicler_project_google_health_sleep_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run Chronicler's Google Health sleep-episode projection job."""
    from butlers.chronicler.jobs import run_project_google_health_sleep

    return await run_project_google_health_sleep(pool, job_args)


async def _run_chronicler_project_google_health_workout_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run Chronicler's Google Health workout-episode projection job."""
    from butlers.chronicler.jobs import run_project_google_health_workout

    return await run_project_google_health_workout(pool, job_args)


async def _run_chronicler_project_google_health_steps_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run Chronicler's Google Health steps point-event projection job."""
    from butlers.chronicler.jobs import run_project_google_health_steps

    return await run_project_google_health_steps(pool, job_args)


async def _run_chronicler_project_google_health_heart_rate_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run Chronicler's Google Health heart-rate point-event projection job."""
    from butlers.chronicler.jobs import run_project_google_health_heart_rate

    return await run_project_google_health_heart_rate(pool, job_args)


async def _run_chronicler_project_focus_inferred_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run Chronicler's inferred focus-block projection job."""
    from butlers.chronicler.jobs import run_project_focus_inferred

    return await run_project_focus_inferred(pool, job_args)


async def _run_chronicler_project_reading_inferred_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run Chronicler's inferred reading-block projection job."""
    from butlers.chronicler.jobs import run_project_reading_inferred

    return await run_project_reading_inferred(pool, job_args)


async def _run_chronicler_project_spotify_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run Chronicler's Spotify listening-session projection job."""
    from butlers.chronicler.jobs import run_project_spotify

    return await run_project_spotify(pool, job_args)


# ---------------------------------------------------------------------------
# Consolidated registry
# ---------------------------------------------------------------------------


def _build_deterministic_schedule_job_registry() -> dict[
    str, dict[str, _DeterministicScheduleJobHandler]
]:
    """Return a fresh deterministic job registry.

    The exported module-level registry remains mutable for tests, but dispatch
    code can rebuild from this source-of-truth when a long-lived process has
    accidentally lost entries through mutation.
    """

    return {
        "general": {
            **_MEMORY_MAINTENANCE_JOB_HANDLERS,
            "collect_briefing_contributions": _run_collect_briefing_contributions_job,
        },
        "health": {
            **_MEMORY_MAINTENANCE_JOB_HANDLERS,
            "daily_briefing_contribution": _run_health_briefing_contribution_job,
            "insight_scan": _run_health_insight_scan_job,
        },
        "finance": {
            "daily_briefing_contribution": _run_finance_briefing_contribution_job,
            "insight_scan": _run_finance_insight_scan_job,
        },
        "relationship": {
            **_MEMORY_MAINTENANCE_JOB_HANDLERS,
            "daily_briefing_contribution": _run_relationship_briefing_contribution_job,
            "insight_scan": _run_relationship_insight_scan_job,
            "interaction_sync": _run_relationship_interaction_sync_job,
        },
        "travel": {
            "daily_briefing_contribution": _run_travel_briefing_contribution_job,
            "insight_scan": _run_travel_insight_scan_job,
        },
        "education": {
            "compute_analytics_snapshots": _run_education_compute_analytics_snapshots_job,
            "daily_briefing_contribution": _run_education_briefing_contribution_job,
        },
        "chronicler": {
            "chronicler_project_sessions": _run_chronicler_project_sessions_job,
            "chronicler_project_calendar": _run_chronicler_project_calendar_job,
            "chronicler_project_owntracks": _run_chronicler_project_owntracks_job,
            "chronicler_project_steam": _run_chronicler_project_steam_job,
            "chronicler_project_meals": _run_chronicler_project_meals_job,
            "chronicler_project_home_assistant": _run_chronicler_project_home_assistant_job,
            "chronicler_project_google_health_sleep": (
                _run_chronicler_project_google_health_sleep_job
            ),
            "chronicler_project_google_health_workout": (
                _run_chronicler_project_google_health_workout_job
            ),
            "chronicler_project_google_health_steps": (
                _run_chronicler_project_google_health_steps_job
            ),
            "chronicler_project_google_health_heart_rate": (
                _run_chronicler_project_google_health_heart_rate_job
            ),
            "chronicler_project_focus_inferred": _run_chronicler_project_focus_inferred_job,
            "chronicler_project_reading_inferred": _run_chronicler_project_reading_inferred_job,
            "chronicler_project_spotify": _run_chronicler_project_spotify_job,
        },
        "home": {
            **_MEMORY_MAINTENANCE_JOB_HANDLERS,
            **_HOME_DETERMINISTIC_JOB_HANDLERS,
            "daily_briefing_contribution": _run_home_briefing_contribution_job,
        },
        "lifestyle": {
            **_MEMORY_MAINTENANCE_JOB_HANDLERS,
            "daily_briefing_contribution": _run_lifestyle_briefing_contribution_job,
        },
        "switchboard": {
            "eligibility_sweep": _run_switchboard_eligibility_sweep_job,
            "insight_delivery_cycle": _run_switchboard_insight_delivery_cycle_job,
            "spend_rule_savings": _run_switchboard_spend_rule_savings_job,
            **_MEMORY_MAINTENANCE_JOB_HANDLERS,
        },
        "qa": {
            "qa_patrol": _run_qa_patrol_job,
            "qa_pr_status_check": _run_qa_pr_status_check_job,
            "qa_evidence_cleanup": _run_qa_evidence_cleanup_job,
        },
    }


def get_deterministic_schedule_job_registry() -> dict[
    str, dict[str, _DeterministicScheduleJobHandler]
]:
    """Return a fresh deterministic job registry snapshot."""

    return _build_deterministic_schedule_job_registry()


_DETERMINISTIC_SCHEDULE_JOB_REGISTRY: dict[str, dict[str, _DeterministicScheduleJobHandler]] = (
    _build_deterministic_schedule_job_registry()
)


def _resolve_deterministic_schedule_job_name(
    *,
    butler_name: str,
    trigger_source: str,
    job_name: str | None,
) -> str | None:
    """Resolve deterministic schedule job name from explicit job_name field."""
    if job_name is not None:
        normalized_job_name = job_name.strip()
        if not normalized_job_name:
            raise RuntimeError(
                "Deterministic scheduler job_name must be a non-empty string "
                f"(butler={butler_name!r})"
            )
        return normalized_job_name

    return None
