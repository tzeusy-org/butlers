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


@functools.lru_cache(maxsize=1)
def _load_switchboard_rule_promotion_trigger_job() -> Callable[
    [asyncpg.Pool, dict[str, Any] | None], Awaitable[dict[str, Any]]
]:
    """Load the switchboard rule-promotion trigger job from roster/ by file path."""
    import importlib.util as _ilu

    module_path = (
        Path(__file__).resolve().parents[2]
        / "roster"
        / "switchboard"
        / "jobs"
        / "rule_promotion_trigger.py"
    )
    module_name = "roster_switchboard_rule_promotion_trigger_job"
    spec = _ilu.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(
            f"Unable to load switchboard rule promotion trigger job from {module_path}"
        )
    module = _ilu.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.run_rule_promotion_trigger_job


async def _run_switchboard_rule_promotion_trigger_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run the switchboard rule-promotion trigger deterministic schedule job."""
    run_rule_promotion_trigger_job = _load_switchboard_rule_promotion_trigger_job()
    return await run_rule_promotion_trigger_job(pool, job_args)


async def _run_switchboard_decision_review_digest_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run the weekly decision-review digest job for the Switchboard butler.

    Delegates to ``butlers.jobs.decision_review.run_decision_review_digest``
    (bu-ckkpz.4, epic bu-ckkpz "Owner Decision Desk").
    """
    from butlers.jobs.decision_review import run_decision_review_digest

    return await run_decision_review_digest(pool, job_args)


async def _run_switchboard_decision_escalation_check_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run the P1/deploy decision-block age-escalation check for Switchboard.

    Delegates to ``butlers.jobs.decision_review.run_decision_escalation_check``
    (bu-ckkpz.4, epic bu-ckkpz "Owner Decision Desk").
    """
    from butlers.jobs.decision_review import run_decision_escalation_check

    return await run_decision_escalation_check(pool, job_args)


def _build_switchboard_insight_notify_fn(
    pool: asyncpg.Pool,
) -> Any:
    """Build the production notify_fn for the insight delivery cycle.

    Returns an async callable ``notify_fn(message, metadata) -> dict`` that:
    1. Reads ``metadata["channel"]`` to determine the delivery channel
       (falls back to ``"telegram"`` when not set, per spec default).
    2. Resolves the owner's recipient identifier for that channel from
       ``public.entity_info``.
    3. Dispatches via the Switchboard's ``deliver()`` path (direct channel
       routing — no MCP round-trip through the Switchboard itself).
    4. Translates ``deliver()``'s ``status="failed"`` to ``status="error"`` so
       the broker's failure-detection check (``status == "error"``) fires
       correctly on delivery failure.

    Parameters
    ----------
    pool:
        The shared asyncpg connection pool (captured in the closure).
    """

    async def _notify_fn(message: str, metadata: dict[str, Any]) -> dict[str, Any]:
        from butlers.credential_store import (
            resolve_owner_entity_info,
            resolve_owner_telegram_recipient,
        )
        from butlers.tools.switchboard.notification.deliver import deliver

        channel: str = metadata.get("channel") or "telegram"

        if channel == "telegram":
            # Resolve the numeric chat id (telegram_chat_id), not the @username
            # handle — the username is undeliverable and trips the approval
            # gate's owner-primacy check, parking owner notifications forever.
            recipient = await resolve_owner_telegram_recipient(pool)
            if not recipient:
                logger.error(
                    "insight-delivery-cycle: no telegram recipient configured for owner — "
                    "cannot deliver insight"
                )
                return {"status": "error", "error": "No telegram chat ID configured for owner"}
        elif channel == "email":
            recipient = await resolve_owner_entity_info(pool, "email")
            if not recipient:
                logger.error(
                    "insight-delivery-cycle: no email address configured for owner — "
                    "cannot deliver insight via email"
                )
                return {"status": "error", "error": "No email address configured for owner"}
        else:
            logger.warning(
                "insight-delivery-cycle: unsupported channel %r; falling back to telegram",
                channel,
            )
            channel = "telegram"
            recipient = await resolve_owner_telegram_recipient(pool)
            if not recipient:
                return {"status": "error", "error": "No telegram chat ID configured for owner"}

        deliver_result = await deliver(
            pool,
            channel=channel,
            message=message,
            recipient=recipient,
            source_butler="switchboard",
            metadata=metadata,
        )
        # Translate deliver()'s "failed" status to "error" so the broker's
        # failure-detection check (notify_result.get("status") == "error") fires.
        if isinstance(deliver_result, dict) and deliver_result.get("status") == "failed":
            return {
                "status": "error",
                "error": deliver_result.get("error", "delivery failed"),
            }
        return deliver_result

    return _notify_fn


async def _run_switchboard_insight_delivery_cycle_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run the proactive insight delivery cycle for the Switchboard butler.

    Orchestrates the full 10-step insight delivery pipeline:
    quiet-hours check, expiry, cooldown filter, dedup, budget computation,
    top-B selection, delivery, cooldown recording, engagement tracking,
    and cleanup.

    Builds the production notify_fn from the pool so that delivery_cycle
    actually dispatches candidates via the Switchboard's notification path.
    """
    del job_args
    from butlers.tools.switchboard.insight.broker import delivery_cycle

    notify_fn = _build_switchboard_insight_notify_fn(pool)
    return await delivery_cycle(pool, notify_fn=notify_fn)


async def _run_switchboard_insight_urgent_subcycle_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run the hourly urgent insight sub-cycle for the Switchboard butler.

    bu-o8233 (JARVIS pursuit move 8 slice 4): the daily ``insight_delivery_cycle``
    slot means a priority>=90 candidate proposed shortly after the daily 08:00
    run could otherwise sit ``pending`` for nearly 24h. This job calls the same
    ``delivery_cycle`` pipeline with ``urgent_only=True`` so priority>=90
    candidates only ever wait for the next hourly tick, never the next day.

    Shares the exact same production ``notify_fn`` as the daily cycle (same
    channel resolution, same Switchboard delivery path) — only the candidate
    selection and cadence differ.
    """
    del job_args
    from butlers.tools.switchboard.insight.broker import delivery_cycle

    notify_fn = _build_switchboard_insight_notify_fn(pool)
    return await delivery_cycle(pool, notify_fn=notify_fn, urgent_only=True)


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
    """Run memory consolidation through the daemon's live runtime spawner.

    ``job_args.batch_size`` optionally overrides ``run_consolidation``'s
    default batch size (``DEFAULT_BATCH_SIZE``). This lets a single handler
    serve both the steady-state ``memory_consolidation`` schedule (default
    batch size, slower cadence) and a ``memory_consolidation_backfill``
    schedule (larger batch size, tighter cadence) registered against the same
    ``job_name`` — see ``MemoryModule.on_startup``'s default schedules.

    Scheduler dispatch binds the dispatching daemon's fully configured
    ``Spawner`` with the maintenance runtime. Reusing that exact instance keeps
    model selection, spend routing, quotas, failover, and session timeouts under
    the authoritative model-catalog path. ``run_consolidation`` only invokes it
    when the bounded claim produced at least one ``(tenant_id, butler)`` group,
    so an empty backlog is a no-op.

    ``enable_shared_catalog=True`` is passed through regardless — it matches
    the memory module's own default (see the memory-discovery-catalog spec's
    "Catalog write-behind defaults to enabled" requirement; no butler.toml
    currently overrides it) and keeps the ``store_fact``/``store_rule``
    catalog pass-through correct for whenever a real ``Spawner`` lands here.
    Pool, embedding-model, and source-schema resolution remain owned by the
    started memory module through ``core.memory_hooks``.  This preserves custom
    embedding configuration and private memory schemas such as
    ``chronicler_mem`` rather than accidentally using the daemon's domain pool.
    """
    del pool

    from butlers.core.memory_hooks import consolidate_memory
    from butlers.modules.memory.consolidation import DEFAULT_BATCH_SIZE

    batch_size = DEFAULT_BATCH_SIZE
    if job_args is not None:
        unknown_args = sorted(set(job_args) - {"batch_size"})
        if unknown_args:
            raise RuntimeError(
                "memory_consolidation job only supports job_args.batch_size; "
                f"received unsupported keys: {unknown_args}"
            )
        if "batch_size" in job_args:
            raw_batch_size = job_args["batch_size"]
            if (
                not isinstance(raw_batch_size, int)
                or isinstance(raw_batch_size, bool)
                or raw_batch_size <= 0
            ):
                raise RuntimeError(
                    "memory_consolidation job_args.batch_size must be a positive integer"
                )
            batch_size = raw_batch_size

    return await consolidate_memory(
        batch_size=batch_size,
        enable_shared_catalog=True,
    )


async def _run_memory_decay_sweep_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run the confidence decay sweep directly (no job_args accepted).

    Wraps ``run_decay_sweep`` — previously defined but never wired to any
    scheduled-job handler or schedule (see docs/redesigns/2026-07-04-jarvis-
    pursuit.md #3). Fades/expires low-confidence facts and rules per
    ``memory_policies`` thresholds so displayed confidence values reflect
    actual decay rather than an un-decayed write-time number.
    """
    if job_args:
        raise RuntimeError(
            f"memory_decay_sweep job does not accept job_args; received: {sorted(job_args)}"
        )
    from butlers.core.memory_hooks import resolve_memory_runtime_pool
    from butlers.modules.memory.storage import run_decay_sweep

    pool = resolve_memory_runtime_pool()
    return await run_decay_sweep(pool)


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
    from butlers.core.memory_hooks import resolve_memory_runtime_pool
    from butlers.modules.memory.consolidation import run_episode_cleanup

    pool = resolve_memory_runtime_pool()

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
    from butlers.core.memory_hooks import resolve_memory_runtime_pool
    from butlers.modules.memory.storage import purge_superseded_facts

    pool = resolve_memory_runtime_pool()

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
        # Keys match purge_superseded_facts's return contract.
        return {"deleted": 0, "deleted_ha_state": 0, "skipped": "no_ttl_policy"}

    size_before = await _table_size_bytes(pool, "facts")
    result = await purge_superseded_facts(pool, older_than_days=older_than_days)
    # purge_superseded_facts returns {"deleted", "deleted_ha_state"}.
    total_removed = result.get("deleted", 0) + result.get("deleted_ha_state", 0)
    if total_removed > 0:
        size_after = await _table_size_bytes(pool, "facts")
        bytes_freed: int | None = None
        if size_before is not None and size_after is not None:
            bytes_freed = max(0, size_before - size_after)
        await _log_compaction(pool, "fact", total_removed, bytes_freed=bytes_freed)
    return result


async def _infer_current_schema(pool: asyncpg.Pool) -> str | None:
    """Best-effort resolve the owning butler's schema from the pool's search_path.

    The daemon connects each butler's pool with ``search_path = <schema>,
    public`` (see ``butlers.db.schema_search_path``), so ``current_schema()``
    resolves to the butler's own schema in the one-db/multi-schema topology.
    Treats a resolved value of ``'public'`` as unresolved — a real butler
    schema should never legitimately be ``public`` itself, and mis-tagging
    catalog rows with the wrong ``source_schema`` would be worse than a
    skipped run. Returns ``None`` (never raises) on any failure so the
    backfill job degrades to a no-op rather than crash the scheduler.
    """
    try:
        schema = await pool.fetchval("SELECT current_schema()")
    except Exception:
        logger.warning("memory_catalog_backfill: failed to resolve current_schema()", exc_info=True)
        return None
    if not schema or schema == "public":
        return None
    return schema


async def _run_memory_catalog_backfill_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Backfill + reverse-reconcile this butler's public.memory_catalog rows.

    Bounded, idempotent catch-up pass draining the pre-flip backlog (see
    docs/redesigns/2026-07-04-jarvis-pursuit.md #15: ``enable_shared_catalog``
    now defaults True, but ~3,600 facts/rules written before the flip predate
    write-behind and have no catalog row), plus a reverse-reconciliation pass
    that marks stale any catalog row whose source fact/rule has since gone,
    been forgotten, or reached a terminal state. Safe to re-run — see
    ``run_memory_catalog_backfill`` for the idempotency contract.

    ``job_args.batch_size`` overrides the default per-table batch size (200).
    ``job_args.source_schema`` overrides the schema inferred from the pool's
    ``current_schema()`` — mainly useful for test topologies where memory
    tables live directly in ``public`` (where auto-inference intentionally
    treats the result as unresolved).
    """
    from butlers.core.memory_hooks import resolve_memory_runtime_pool
    from butlers.modules.memory.storage import run_memory_catalog_backfill

    batch_size = 200
    source_schema: str | None = None
    if job_args is not None:
        unknown_args = sorted(set(job_args) - {"batch_size", "source_schema"})
        if unknown_args:
            raise RuntimeError(
                "memory_catalog_backfill job only supports job_args.batch_size and "
                f"job_args.source_schema; received unsupported keys: {unknown_args}"
            )
        if "batch_size" in job_args:
            raw_batch_size = job_args["batch_size"]
            if (
                not isinstance(raw_batch_size, int)
                or isinstance(raw_batch_size, bool)
                or raw_batch_size <= 0
            ):
                raise RuntimeError(
                    "memory_catalog_backfill job_args.batch_size must be a positive integer"
                )
            batch_size = raw_batch_size
        if "source_schema" in job_args:
            raw_schema = job_args["source_schema"]
            if not isinstance(raw_schema, str) or not raw_schema.strip():
                raise RuntimeError(
                    "memory_catalog_backfill job_args.source_schema must be a non-empty string"
                )
            source_schema = raw_schema.strip()

    pool = resolve_memory_runtime_pool()
    if source_schema is None:
        source_schema = await _infer_current_schema(pool)
        if source_schema is None:
            return {
                "facts_backfilled": 0,
                "rules_backfilled": 0,
                "facts_reconciled": 0,
                "rules_reconciled": 0,
                "skipped": "source_schema_not_resolved",
            }

    return await run_memory_catalog_backfill(
        pool, source_schema=source_schema, batch_size=batch_size
    )


async def _run_memory_ann_observability_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run the live-safe HNSW monitor through the memory runtime pool.

    The supplied scheduler pool may point at a butler's domain schema while
    the memory module uses a private schema (notably ``chronicler_mem``).  The
    registered runtime-pool resolver keeps this monitor pointed at the same
    local tables as memory retrieval.  The monitor itself is read-only and
    reports a degraded result rather than scanning a corpus above its
    exact-recall safety cap.
    """
    del pool
    if job_args:
        raise RuntimeError(
            f"memory_ann_observability job does not accept job_args; received: {sorted(job_args)}"
        )
    import importlib

    from butlers.core.memory_hooks import resolve_memory_runtime_pool

    # The deterministic Finder guard walks imports reachable from relationship
    # handlers.  Keep the pgvector implementation outside that graph; this
    # scheduler path loads it only when the maintenance job actually runs.
    ann_observability = importlib.import_module("butlers.modules.memory.ann_observability")
    return await ann_observability.run_ann_observability(resolve_memory_runtime_pool())


_MEMORY_MAINTENANCE_JOB_HANDLERS: dict[str, _DeterministicScheduleJobHandler] = {
    "memory_consolidation": _run_memory_consolidation_job,
    "memory_episode_cleanup": _run_memory_episode_cleanup_job,
    "memory_purge_superseded": _run_memory_purge_superseded_job,
    "memory_decay_sweep": _run_memory_decay_sweep_job,
    "memory_catalog_backfill": _run_memory_catalog_backfill_job,
    "memory_ann_observability": _run_memory_ann_observability_job,
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


# ---------------------------------------------------------------------------
# Domain-specific briefing contribution jobs
# ---------------------------------------------------------------------------


async def _run_education_compute_analytics_snapshots_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run education analytics snapshot computation as a deterministic job.

    Wires the curriculum-replan feedback loop (spec module-education-analytics,
    "Feedback Loop Trigger"): for every freshly computed snapshot,
    ``analytics_compute_all`` invokes the callback below when
    ``len(struggling_nodes) >= 3`` or ``retention_rate_7d < 0.60``. The callback
    re-sequences that map's curriculum via ``curriculum_replan`` so the learner's
    path adapts to current mastery instead of the loop never firing.
    """
    del job_args
    from butlers.tools.education.analytics import analytics_compute_all
    from butlers.tools.education.curriculum import curriculum_replan

    replans_triggered = 0

    async def _curriculum_replan_callback(mind_map_id: str, metrics: dict[str, Any]) -> None:
        """Replan one struggling map. Isolated so a single failure cannot abort the job."""
        nonlocal replans_triggered
        struggling = len(metrics.get("struggling_nodes", []))
        retention_7d = metrics.get("retention_rate_7d")
        reason = (
            "nightly analytics feedback loop: "
            f"struggling_nodes={struggling}, retention_rate_7d={retention_7d}"
        )
        try:
            await curriculum_replan(pool, mind_map_id, reason=reason)
            replans_triggered += 1
        except Exception:
            # A map that went abandoned/completed between snapshot and replan (or any
            # other replan error) should not abort the remaining maps' snapshots.
            logger.exception(
                "education feedback loop: curriculum_replan failed for mind_map_id=%s",
                mind_map_id,
            )

    count = await analytics_compute_all(
        pool=pool,
        curriculum_replan=_curriculum_replan_callback,
    )
    return {"snapshots_computed": count, "replans_triggered": replans_triggered}


async def _run_education_mind_map_staleness_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Abandon active mind maps inactive for 30+ days (deterministic weekly job).

    Per the module-education-mind-map spec, an ``active`` map transitions to
    ``abandoned`` once more than 30 days have elapsed since any node activity.
    The 30-day spec default may be overridden per-dispatch via
    ``job_args.inactivity_days`` (mirrors the sibling memory-cleanup jobs).
    """
    inactivity_days = 30
    if job_args is not None:
        unknown_args = sorted(set(job_args) - {"inactivity_days"})
        if unknown_args:
            raise RuntimeError(
                "mind_map_staleness_abandonment job only supports job_args.inactivity_days; "
                f"received unsupported keys: {unknown_args}"
            )
        if "inactivity_days" in job_args:
            raw_days = job_args["inactivity_days"]
            if not isinstance(raw_days, int) or isinstance(raw_days, bool) or raw_days <= 0:
                raise RuntimeError(
                    "mind_map_staleness_abandonment job_args.inactivity_days "
                    "must be a positive integer"
                )
            inactivity_days = raw_days

    from butlers.tools.education.mind_maps import mind_map_abandon_stale

    abandoned_ids = await mind_map_abandon_stale(pool=pool, inactivity_days=inactivity_days)
    return {"abandoned_count": len(abandoned_ids), "abandoned_ids": abandoned_ids}


async def _run_health_briefing_contribution_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run health butler daily briefing contribution job."""
    from butlers.jobs.briefing import run_health_briefing_contribution

    return await run_health_briefing_contribution(pool=pool, job_args=job_args)


async def _run_health_calendar_overlay_contribution_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run health butler calendar overlay contribution job (deterministic, zero LLM)."""
    from butlers.jobs.calendar_overlay import run_health_calendar_overlay_contribution

    return await run_health_calendar_overlay_contribution(pool=pool, job_args=job_args)


async def _run_finance_briefing_contribution_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run finance butler daily briefing contribution job."""
    from butlers.jobs.briefing import run_finance_briefing_contribution

    return await run_finance_briefing_contribution(pool=pool, job_args=job_args)


async def _run_finance_calendar_overlay_contribution_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run finance butler calendar overlay contribution job (deterministic, zero LLM)."""
    from butlers.jobs.calendar_overlay import run_finance_calendar_overlay_contribution

    return await run_finance_calendar_overlay_contribution(pool=pool, job_args=job_args)


async def _run_finance_insight_scan_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run finance butler insight scan job."""
    del job_args
    from butlers.jobs._roster_loader import load_roster_jobs

    mod = load_roster_jobs("finance")
    return await mod.run_insight_scan(pool)


async def _run_finance_bill_reconciliation_sweep_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run finance butler weekly bill-reconciliation sweep job (bu-rvz2o)."""
    del job_args
    from butlers.jobs._roster_loader import load_roster_jobs

    mod = load_roster_jobs("finance")
    return await mod.run_bill_reconciliation_sweep(pool)


async def _run_finance_anomaly_insight_scan_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run finance butler daily anomaly insight scan job (bu-rvz2o)."""
    del job_args
    from butlers.jobs._roster_loader import load_roster_jobs

    mod = load_roster_jobs("finance")
    return await mod.run_anomaly_insight_scan(pool)


async def _run_finance_monthly_finance_digest_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run finance butler monthly finance digest job (bu-rvz2o)."""
    del job_args
    from butlers.jobs._roster_loader import load_roster_jobs

    mod = load_roster_jobs("finance")
    return await mod.run_monthly_finance_digest(pool)


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


async def _run_travel_calendar_overlay_contribution_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run travel butler calendar overlay contribution job (deterministic, zero LLM)."""
    from butlers.jobs.calendar_overlay import run_travel_calendar_overlay_contribution

    return await run_travel_calendar_overlay_contribution(pool=pool, job_args=job_args)


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
    """Run health butler insight scan job.

    Builds a concrete ``HaEnvironmentReader`` from the health butler's own HA
    credentials (stored in ``public.entity_info``) and passes it into the scan
    so that ``_scan_environment_correlation`` can run in production.  When HA
    credentials are absent the reader is ``None`` and the correlation section is
    skipped cleanly — same behaviour as before this fix.
    """
    del job_args
    from butlers.jobs._roster_loader import load_roster_jobs
    from butlers.jobs.health_ha_reader import build_ha_environment_reader

    mod = load_roster_jobs("health")
    ha_reader = await build_ha_environment_reader(pool)
    return await mod.run_insight_scan(pool, ha_environment_reader=ha_reader)


async def _run_relationship_calendar_overlay_contribution_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run relationship butler calendar overlay contribution job (deterministic, zero LLM)."""
    from butlers.jobs.calendar_overlay import run_relationship_calendar_overlay_contribution

    return await run_relationship_calendar_overlay_contribution(pool=pool, job_args=job_args)


async def _run_relationship_calendar_prep_contribution_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run relationship butler calendar meeting-prep contribution job (deterministic, zero LLM)."""
    from butlers.jobs.calendar_prep import run_relationship_calendar_prep_contribution

    return await run_relationship_calendar_prep_contribution(pool=pool, job_args=job_args)


async def _run_messenger_calendar_prep_contribution_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run messenger butler calendar meeting-prep message-context job (deterministic, zero LLM)."""
    from butlers.jobs.calendar_prep import run_messenger_calendar_prep_contribution

    return await run_messenger_calendar_prep_contribution(pool=pool, job_args=job_args)


async def _run_travel_calendar_prep_contribution_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run travel butler calendar meeting-prep message-context job (deterministic, zero LLM)."""
    from butlers.jobs.calendar_prep import run_travel_calendar_prep_contribution

    return await run_travel_calendar_prep_contribution(pool=pool, job_args=job_args)


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


async def _run_relationship_memory_curation_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run relationship butler memory curation job (backfill structured edges).

    Behavior #1: backfills structured entity edges from existing prose facts
    (living_arrangement/family_relationship/etc. → partner-of/child-of/...).
    Every proposed mutation routes through relationship_assert_fact so
    owner-scoped edges land in pending_actions for owner approval.
    """
    del job_args
    from butlers.jobs._roster_loader import load_roster_jobs

    mod = load_roster_jobs("relationship")
    return await mod.run_memory_curation(pool)


async def _run_relationship_pending_actions_curation_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run relationship butler pending-actions curation job.

    Scans pending_actions for entries approaching expiry and surfaces them
    as insight candidates so the owner is prompted to act before the window
    closes.
    """
    del job_args
    from butlers.jobs._roster_loader import load_roster_jobs

    mod = load_roster_jobs("relationship")
    return await mod.run_pending_actions_curation(pool)


async def _run_relationship_fact_retraction_curation_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run relationship butler fact-retraction curation job (behavior #3).

    Scans relationship.facts for contradicted facts (two active rows on the
    same entity+predicate with different content) and low-confidence facts
    (confidence below threshold).  Flags each for owner review via
    pending_actions — nothing is auto-retracted.
    """
    del job_args
    from butlers.jobs._roster_loader import load_roster_jobs

    mod = load_roster_jobs("relationship")
    return await mod.run_fact_retraction_curation(pool)


async def _run_relationship_entity_dedup_curation_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run relationship butler entity-dedup curation job (behavior #2).

    Scans public.entities for entities with same or near-identical
    canonical_name values and surfaces each duplicate pair as a
    pending_actions merge candidate for owner review.  No autonomous merge
    is ever performed.
    """
    del job_args
    from butlers.jobs._roster_loader import load_roster_jobs

    mod = load_roster_jobs("relationship")
    return await mod.run_entity_dedup_curation(pool)


async def _run_relationship_email_identity_enrichment_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run relationship butler email identity enrichment job (bu-qeaou).

    Scans public.ingestion_events for recurring human email correspondents not
    yet linked to an entity via a has-email fact, and surfaces each candidate
    as a pending_actions proposal (entity creation/linking + has-email fact)
    for owner review. No fact is ever written without approval.
    """
    del job_args
    from butlers.jobs._roster_loader import load_roster_jobs

    mod = load_roster_jobs("relationship")
    return await mod.run_email_identity_enrichment(pool)


async def _run_relationship_episodic_predicate_curation_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run relationship butler episodic-predicate curation job (behavior #5).

    Scans durable relationship facts for episodic predicates that should be
    reclassified through owner-approved pending actions.
    """
    del job_args
    from butlers.jobs._roster_loader import load_roster_jobs

    mod = load_roster_jobs("relationship")
    return await mod.run_episodic_predicate_curation(pool)


# NOTE: _run_relationship_contact_info_reconciler_job was retired in migration
# bead 10 (bu-e2ja9 / core_115). public.contact_info is dropped, so the
# dual-write reconciler has nothing to sweep and is no longer dispatched.


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
# Situational context-bus producers (RFC 0009)
#
# Deterministic, zero-LLM producers that light public.user_context. Each runs
# on the butler RFC 0009 authorizes as the signal's single writer.
# ---------------------------------------------------------------------------


async def _run_context_producer_calendar_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Publish meeting/focused context from the general butler's live calendar."""
    from butlers.jobs.context_producers import run_calendar_context_producer

    return await run_calendar_context_producer(pool, job_args)


async def _run_context_producer_home_presence_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Publish at_home context from fresh Home Assistant presence entities."""
    from butlers.jobs.context_producers import run_home_presence_context_producer

    return await run_home_presence_context_producer(pool, job_args)


async def _run_context_producer_travel_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Publish traveling context from a currently-underway trip."""
    from butlers.jobs.context_producers import run_travel_context_producer

    return await run_travel_context_producer(pool, job_args)


async def _run_context_producer_sleep_window_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Publish sleeping context from the owner-declared quiet-hours window."""
    from butlers.jobs.context_producers import run_sleep_window_context_producer

    return await run_sleep_window_context_producer(pool, job_args)


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
    by severity, and delivers a notification to the owner through the notify
    boundary via the shared ``_send_notify`` helper when any items require
    attention (mirrors the device_health_check / environment_report /
    energy_digest siblings).
    """
    from butlers.jobs.home import _send_notify, run_maintenance_schedule_check

    async def _notify(message: str) -> None:
        await _send_notify(pool, message)

    return await run_maintenance_schedule_check(pool, job_args, notify_fn=_notify)


_HOME_DETERMINISTIC_JOB_HANDLERS: dict[str, _DeterministicScheduleJobHandler] = {
    "device_health_check": _run_home_device_health_check_job,
    "environment_report": _run_home_environment_report_job,
    "energy_digest": _run_home_energy_digest_job,
    "maintenance_schedule_check": _run_home_maintenance_schedule_check_job,
    "context_producer_home_presence": _run_context_producer_home_presence_job,
}


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


async def _run_chronicler_project_owntracks_place_cluster_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run Chronicler's OwnTracks GPS place-cluster projection job (bu-ac2pg)."""
    from butlers.chronicler.jobs import run_project_owntracks_place_cluster

    return await run_project_owntracks_place_cluster(pool, job_args)


async def _run_chronicler_project_owntracks_ssid_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run Chronicler's OwnTracks Wi-Fi SSID presence projection job."""
    from butlers.chronicler.jobs import run_project_owntracks_ssid

    return await run_project_owntracks_ssid(pool, job_args)


async def _run_chronicler_project_activitywatch_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run Chronicler's ActivityWatch desktop-activity projection job."""
    from butlers.chronicler.jobs import run_project_activitywatch

    return await run_project_activitywatch(pool, job_args)


async def _run_chronicler_project_owner_outbound_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run Chronicler's owner-outbound-message point-event projection job."""
    from butlers.chronicler.jobs import run_project_owner_outbound

    return await run_project_owner_outbound(pool, job_args)


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


async def _run_chronicler_project_home_assistant_sensor_activity_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run Chronicler's HA non-person sensor-activity projection job (bu-49fqa)."""
    from butlers.chronicler.jobs import run_project_home_assistant_sensor_activity

    return await run_project_home_assistant_sensor_activity(pool, job_args)


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


async def _run_chronicler_project_exercise_inferred_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run Chronicler's inferred exercise (HR+GPS) projection job."""
    from butlers.chronicler.jobs import run_project_exercise_inferred

    return await run_project_exercise_inferred(pool, job_args)


async def _run_chronicler_project_occupation_inferred_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run Chronicler's inferred occupation-block projection job."""
    from butlers.chronicler.jobs import run_project_occupation_inferred

    return await run_project_occupation_inferred(pool, job_args)


async def _run_chronicler_project_comms_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run Chronicler's comms->Social message-burst projection job."""
    from butlers.chronicler.jobs import run_project_comms

    return await run_project_comms(pool, job_args)


async def _run_chronicler_routines_mine_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run Chronicler's deterministic weekly routine miner (bu-whhll.9)."""
    from butlers.chronicler.jobs import run_routines_mine

    return await run_routines_mine(pool, job_args)


async def _run_chronicler_rollup_daily_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run Chronicler's deterministic daily rollup materializer (bu-u30as)."""
    from butlers.chronicler.jobs import run_rollup_daily

    return await run_rollup_daily(pool, job_args)


async def _run_chronicler_narrate_daily_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run Chronicler's bounded once-daily LLM rollup-labeling pass (bu-v9y18)."""
    from butlers.chronicler.jobs import run_narrate_daily

    return await run_narrate_daily(pool, job_args)


# ---------------------------------------------------------------------------
# Retention pruner jobs (opt-in, disabled by default)
# ---------------------------------------------------------------------------


async def _run_session_process_logs_prune_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Prune expired session_process_logs rows for a butler schema.

    Disabled by default.  Enable via ``job_args = {enabled = true, dry_run = false}``.
    ``schema`` must be supplied in job_args (defaults to the butler name).
    See docs/operations/data-retention.md §[A] and butlers.jobs.retention.
    """
    from butlers.jobs.retention import prune_session_process_logs

    args = job_args or {}
    schema: str = args.get("schema", "general")
    enabled: bool = bool(args.get("enabled", False))
    dry_run: bool = bool(args.get("dry_run", True))
    batch_limit: int = int(args.get("batch_limit", 500))
    return await prune_session_process_logs(
        pool,
        schema=schema,
        enabled=enabled,
        dry_run=dry_run,
        batch_limit=batch_limit,
    )


async def _run_filtered_events_partition_prune_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Drop old monthly partitions of connectors.filtered_events.

    Disabled by default.  Enable via ``job_args = {enabled = true, dry_run = false}``.
    See docs/operations/data-retention.md §[B] and butlers.jobs.retention.
    """
    from butlers.jobs.retention import prune_filtered_events_partitions

    args = job_args or {}
    enabled: bool = bool(args.get("enabled", False))
    dry_run: bool = bool(args.get("dry_run", True))
    keep_months: int = int(args.get("keep_months", 12))
    return await prune_filtered_events_partitions(
        pool,
        enabled=enabled,
        dry_run=dry_run,
        keep_months=keep_months,
    )


async def _run_insight_candidates_prune_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Prune old delivered/filtered rows from public.insight_candidates.

    Disabled by default.  Enable via ``job_args = {enabled = true, dry_run = false}``.
    See docs/operations/data-retention.md §[C] and butlers.jobs.retention.
    """
    from butlers.jobs.retention import prune_insight_candidates

    args = job_args or {}
    enabled: bool = bool(args.get("enabled", False))
    dry_run: bool = bool(args.get("dry_run", True))
    ttl_days: int = int(args.get("ttl_days", 90))
    batch_limit: int = int(args.get("batch_limit", 500))
    return await prune_insight_candidates(
        pool,
        enabled=enabled,
        dry_run=dry_run,
        ttl_days=ttl_days,
        batch_limit=batch_limit,
    )


async def _run_secret_probe_log_prune_job(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Prune old rows from public.secret_probe_log (≥90-day retention).

    Disabled by default.  Enable via ``job_args = {enabled = true, dry_run = false}``.
    See docs/operations/data-retention.md §[D] and butlers.jobs.retention.
    """
    from butlers.jobs.retention import prune_secret_probe_log

    args = job_args or {}
    enabled: bool = bool(args.get("enabled", False))
    dry_run: bool = bool(args.get("dry_run", True))
    ttl_days: int = int(args.get("ttl_days", 90))
    batch_limit: int = int(args.get("batch_limit", 500))
    return await prune_secret_probe_log(
        pool,
        enabled=enabled,
        dry_run=dry_run,
        ttl_days=ttl_days,
        batch_limit=batch_limit,
    )


_RETENTION_PRUNER_JOB_HANDLERS: dict[str, _DeterministicScheduleJobHandler] = {
    "session_process_logs_prune": _run_session_process_logs_prune_job,
    "filtered_events_partition_prune": _run_filtered_events_partition_prune_job,
    "insight_candidates_prune": _run_insight_candidates_prune_job,
    "secret_probe_log_prune": _run_secret_probe_log_prune_job,
}


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
            "context_producer_calendar": _run_context_producer_calendar_job,
            # Retention pruners (disabled by default — see docs/operations/data-retention.md)
            "session_process_logs_prune": _run_session_process_logs_prune_job,
            "filtered_events_partition_prune": _run_filtered_events_partition_prune_job,
            "insight_candidates_prune": _run_insight_candidates_prune_job,
            "secret_probe_log_prune": _run_secret_probe_log_prune_job,
        },
        "health": {
            **_MEMORY_MAINTENANCE_JOB_HANDLERS,
            "daily_briefing_contribution": _run_health_briefing_contribution_job,
            "calendar_overlay_contribution": _run_health_calendar_overlay_contribution_job,
            "insight_scan": _run_health_insight_scan_job,
            "context_producer_sleep_window": _run_context_producer_sleep_window_job,
            # Per-butler session log pruner
            "session_process_logs_prune": _run_session_process_logs_prune_job,
        },
        "finance": {
            **_MEMORY_MAINTENANCE_JOB_HANDLERS,
            "daily_briefing_contribution": _run_finance_briefing_contribution_job,
            "calendar_overlay_contribution": _run_finance_calendar_overlay_contribution_job,
            "insight_scan": _run_finance_insight_scan_job,
            "bill_reconciliation_sweep": _run_finance_bill_reconciliation_sweep_job,
            "anomaly_insight_scan": _run_finance_anomaly_insight_scan_job,
            "monthly_finance_digest": _run_finance_monthly_finance_digest_job,
            "session_process_logs_prune": _run_session_process_logs_prune_job,
        },
        "relationship": {
            **_MEMORY_MAINTENANCE_JOB_HANDLERS,
            "daily_briefing_contribution": _run_relationship_briefing_contribution_job,
            "calendar_overlay_contribution": _run_relationship_calendar_overlay_contribution_job,
            "calendar_prep_contribution": _run_relationship_calendar_prep_contribution_job,
            "insight_scan": _run_relationship_insight_scan_job,
            "interaction_sync": _run_relationship_interaction_sync_job,
            "memory_curation": _run_relationship_memory_curation_job,
            "pending_actions_curation": _run_relationship_pending_actions_curation_job,
            "fact_retraction_curation": _run_relationship_fact_retraction_curation_job,
            "entity_dedup_curation": _run_relationship_entity_dedup_curation_job,
            "episodic_predicate_curation": _run_relationship_episodic_predicate_curation_job,
            "email_identity_enrichment": _run_relationship_email_identity_enrichment_job,
            # contact_info_reconciler retired (bu-e2ja9 / core_115): table dropped.
            "session_process_logs_prune": _run_session_process_logs_prune_job,
        },
        "travel": {
            **_MEMORY_MAINTENANCE_JOB_HANDLERS,
            "daily_briefing_contribution": _run_travel_briefing_contribution_job,
            "calendar_overlay_contribution": _run_travel_calendar_overlay_contribution_job,
            "calendar_prep_contribution": _run_travel_calendar_prep_contribution_job,
            "insight_scan": _run_travel_insight_scan_job,
            "context_producer_travel": _run_context_producer_travel_job,
            "session_process_logs_prune": _run_session_process_logs_prune_job,
        },
        "messenger": {
            "calendar_prep_contribution": _run_messenger_calendar_prep_contribution_job,
            "session_process_logs_prune": _run_session_process_logs_prune_job,
        },
        "education": {
            **_MEMORY_MAINTENANCE_JOB_HANDLERS,
            "compute_analytics_snapshots": _run_education_compute_analytics_snapshots_job,
            "mind_map_staleness_abandonment": _run_education_mind_map_staleness_job,
            "daily_briefing_contribution": _run_education_briefing_contribution_job,
            "session_process_logs_prune": _run_session_process_logs_prune_job,
        },
        "chronicler": {
            # Memory maintenance handlers (bu-93y4rt): the chronicler now enables
            # [modules.memory] for the day-close write-back loop, so it must be
            # able to dispatch the memory module's self-registered maintenance
            # schedules (decay_sweep / consolidation / episode_cleanup /
            # purge_superseded / catalog_backfill) or they fail with "unknown
            # deterministic job".
            **_MEMORY_MAINTENANCE_JOB_HANDLERS,
            "chronicler_project_sessions": _run_chronicler_project_sessions_job,
            "chronicler_project_calendar": _run_chronicler_project_calendar_job,
            "chronicler_project_owntracks": _run_chronicler_project_owntracks_job,
            "chronicler_project_owntracks_place_cluster": (
                _run_chronicler_project_owntracks_place_cluster_job
            ),
            "chronicler_project_owntracks_ssid": _run_chronicler_project_owntracks_ssid_job,
            "chronicler_project_activitywatch": _run_chronicler_project_activitywatch_job,
            "chronicler_project_owner_outbound": _run_chronicler_project_owner_outbound_job,
            "chronicler_project_steam": _run_chronicler_project_steam_job,
            "chronicler_project_meals": _run_chronicler_project_meals_job,
            "chronicler_project_home_assistant": _run_chronicler_project_home_assistant_job,
            "chronicler_project_home_assistant_sensor_activity": (
                _run_chronicler_project_home_assistant_sensor_activity_job
            ),
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
            "chronicler_project_exercise_inferred": (_run_chronicler_project_exercise_inferred_job),
            "chronicler_project_occupation_inferred": (
                _run_chronicler_project_occupation_inferred_job
            ),
            "chronicler_project_comms": _run_chronicler_project_comms_job,
            "chronicler_routines_mine": _run_chronicler_routines_mine_job,
            "chronicler_rollup_daily": _run_chronicler_rollup_daily_job,
            "chronicler_narrate_daily": _run_chronicler_narrate_daily_job,
        },
        "home": {
            **_MEMORY_MAINTENANCE_JOB_HANDLERS,
            **_HOME_DETERMINISTIC_JOB_HANDLERS,
            "daily_briefing_contribution": _run_home_briefing_contribution_job,
            "session_process_logs_prune": _run_session_process_logs_prune_job,
        },
        "lifestyle": {
            **_MEMORY_MAINTENANCE_JOB_HANDLERS,
            "daily_briefing_contribution": _run_lifestyle_briefing_contribution_job,
            "session_process_logs_prune": _run_session_process_logs_prune_job,
        },
        "switchboard": {
            "eligibility_sweep": _run_switchboard_eligibility_sweep_job,
            "insight_delivery_cycle": _run_switchboard_insight_delivery_cycle_job,
            "insight_urgent_subcycle": _run_switchboard_insight_urgent_subcycle_job,
            "spend_rule_savings": _run_switchboard_spend_rule_savings_job,
            "rule_promotion_trigger": _run_switchboard_rule_promotion_trigger_job,
            "decision_review_digest": _run_switchboard_decision_review_digest_job,
            "decision_escalation_check": _run_switchboard_decision_escalation_check_job,
            **_MEMORY_MAINTENANCE_JOB_HANDLERS,
            "session_process_logs_prune": _run_session_process_logs_prune_job,
        },
        "qa": {
            "qa_patrol": _run_qa_patrol_job,
            "qa_pr_status_check": _run_qa_pr_status_check_job,
            "qa_evidence_cleanup": _run_qa_evidence_cleanup_job,
            "session_process_logs_prune": _run_session_process_logs_prune_job,
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
