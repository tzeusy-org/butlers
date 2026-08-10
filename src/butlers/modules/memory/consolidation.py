"""Consolidation runner and episode cleanup for the Memory Butler.

Provides two main entry points:

* ``run_consolidation`` — fetches unconsolidated episodes via a lease-based
  ``FOR UPDATE SKIP LOCKED`` claim ordered by ``(tenant_id, butler, created_at,
  id)``, groups by source butler, orchestrates the full consolidation pipeline
  (prompt building, CC spawning, parsing, and execution), and marks episodes as
  consolidated or failed.
* ``run_episode_cleanup`` — deletes expired episodes and enforces a capacity
  limit on the episodes table.

State machine
-------------
Episodes progress through the following states:

  pending  →  consolidated   (success)
           →  failed         (error, attempts < MAX_CONSOLIDATION_ATTEMPTS)
           →  dead_letter    (error, attempts >= MAX_CONSOLIDATION_ATTEMPTS)
  failed   →  consolidated   (successful retry)
           →  failed         (later retry backoff)
           →  dead_letter    (terminal retry failure)

Lease-based claiming prevents concurrent workers from processing the same
episode.  When a worker claims episodes it sets ``leased_until`` and
``leased_by``.  If a worker crashes mid-lease the lease expires and the episode
becomes claimable again on the next run.

Retry scheduling
----------------
Failed episodes are retried with exponential backoff:

    next_consolidation_retry_at = now() + 2^attempts * BASE_RETRY_SECONDS

Constants
---------
MAX_CONSOLIDATION_ATTEMPTS : int
    Maximum number of consolidation attempts before an episode is dead-lettered.
    Default: 5.
LEASE_DURATION_SECONDS : int
    How many seconds to hold a lease before it expires.  Default: 300 (5 min).
BASE_RETRY_SECONDS : int
    Base interval in seconds for exponential backoff.  Default: 60 (1 min).
DEFAULT_BATCH_SIZE : int
    Maximum episodes claimed per consolidation run.  Default: 100.
"""

from __future__ import annotations

import logging
import os
import socket
import uuid
from typing import TYPE_CHECKING, Any

from butlers.modules.memory.consolidation_executor import execute_consolidation
from butlers.modules.memory.consolidation_parser import parse_consolidation_output
from butlers.modules.memory.prompt_template import build_consolidation_prompt

if TYPE_CHECKING:
    from asyncpg import Pool

    from butlers.core.spawner import Spawner

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_CONSOLIDATION_ATTEMPTS: int = 5
LEASE_DURATION_SECONDS: int = 300  # 5 minutes
BASE_RETRY_SECONDS: int = 60  # 1 minute base for exponential backoff
DEFAULT_BATCH_SIZE: int = 100

# Episode-cleanup safety knobs.
#
# EPISODE_CLEANUP_BATCH_SIZE bounds every DELETE the sweep issues so re-enabling
# cleanup on a butler with a large accumulated backlog drains incrementally
# rather than in one table-wide-locking statement (mirrors the bounded-backlog
# contract the consolidation backfill follows).
#
# EPISODE_PENDING_GRACE_DAYS protects an expired episode that is still
# ``consolidation_status = 'pending'`` from deletion until it is this many days
# past ``expires_at`` — a lagging consolidator must never lose an un-extracted
# observation. A permanently-stuck pending episode is still reaped once past the
# grace window, so the table can never grow without bound.
EPISODE_CLEANUP_BATCH_SIZE: int = 1000
EPISODE_PENDING_GRACE_DAYS: int = 7

# Boolean SQL predicate (no bind parameters) selecting exactly the episodes the
# cleanup sweep is allowed to delete for expiry: expired AND (already out of
# consolidation, OR pending but past the grace window). The grace-days value is
# an int constant interpolated below — never external input, so this is not an
# injection surface.
#
# This predicate is shared verbatim by ``run_episode_cleanup`` (what the sweep
# deletes) and the dashboard's expired-retention deadman in
# ``butlers.api.routers.memory`` (what it reports as un-reaped). Keeping them
# byte-for-byte aligned is a hard contract: the "expired retained" metric must
# count only episodes the sweep *should* have deleted but has not (genuine
# cleanup lag), never an episode the sweep is deliberately still holding for
# consolidation — otherwise a healthy steady state reads as a degraded source.
REAPABLE_EXPIRED_EPISODE_SQL: str = (
    "expires_at < now() "
    "AND (consolidation_status <> 'pending' "
    f"OR expires_at < now() - make_interval(days => {EPISODE_PENDING_GRACE_DAYS}))"
)


def _worker_id() -> str:
    """Generate a stable-ish worker identifier from hostname + PID."""
    return f"{socket.gethostname()}:{os.getpid()}"


_SAFE_FAILURE_MESSAGES = {
    "runtime_no_output": "Consolidation runtime returned no output.",
    "runtime_unsuccessful": "Consolidation runtime reported a failure.",
    "execution_error": "Consolidation execution failed.",
}


def _safe_failure_message(failure_category: str) -> str:
    """Return the durable, non-sensitive lifecycle diagnosis for a failure.

    Runtime error strings can contain prompt-derived content, provider details,
    or credentials.  They remain available only to the local structured log;
    episode lifecycle rows and memory events retain a bounded diagnostic class.
    """
    return _SAFE_FAILURE_MESSAGES.get(
        failure_category,
        _SAFE_FAILURE_MESSAGES["execution_error"],
    )


# ---------------------------------------------------------------------------
# Consolidation runner
# ---------------------------------------------------------------------------


async def run_consolidation(
    pool: Pool,
    embedding_engine: Any,
    cc_spawner: Spawner | None = None,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    enable_shared_catalog: bool = False,
    source_schema: str | None = None,
    retry_failed: bool = True,
) -> dict[str, Any]:
    """Orchestrate the full consolidation pipeline for unconsolidated episodes.

    Uses ``FOR UPDATE SKIP LOCKED`` to claim eligible episodes, preventing
    concurrent workers from processing the same episode. Pending rows are
    immediately eligible when unleased; failed rows require an explicit, due
    retry timestamp and remain below the terminal-attempt ceiling. Episodes are
    ordered by ``(tenant_id, butler, created_at, id)`` for deterministic
    processing.

    For each ``(tenant_id, butler)`` group with eligible episodes:
    1. Fetch existing facts and rules for dedup context
    2. Build consolidation prompt via ``build_consolidation_prompt``
    3. Spawn a runtime instance with the consolidate skill
    4. Parse runtime output with ``parse_consolidation_output``
    5. Execute consolidation actions via ``execute_consolidation``

    Partial failures in one group do not block other groups from processing.

    Args:
        pool: asyncpg connection pool for the memory database.
        embedding_engine: EmbeddingEngine instance for storing new facts/rules.
        cc_spawner: Optional Spawner instance for invoking the LLM CLI. If None,
            only episode grouping is performed (no actual consolidation).
        batch_size: Maximum number of episodes to claim per run.
        enable_shared_catalog: Forwarded to ``execute_consolidation`` (and from
            there to every ``store_fact``/``store_rule`` call) so
            consolidation-derived facts/rules get a ``public.memory_catalog``
            row like directly-stored ones. Defaults to False.
        source_schema: The butler schema name written to catalog rows when
            ``enable_shared_catalog=True``. When omitted, ``execute_consolidation``
            resolves it from the pool's own ``current_schema()``.
        retry_failed: Whether this memory relation is eligible for automatic
            failed-episode recovery. Failed rows are claimed only when a live
            ``cc_spawner`` can execute the recovery; a no-spawner dry run never
            leases them. Dedicated private memory schemas preserve their
            existing pending-only behavior by passing ``False``.

    Returns:
        A stats dict with keys:
        - ``episodes_processed``: total episodes claimed.
        - ``butlers_processed``: number of distinct (tenant_id, butler) groups.
        - ``groups``: mapping of "tenant_id/butler_name" to episode count.
        - ``groups_consolidated``: number of groups successfully processed.
        - ``facts_created``: total new facts stored.
        - ``facts_updated``: total facts updated.
        - ``rules_created``: total new rules stored.
        - ``confirmations_made``: total fact confirmations made.
        - ``episodes_consolidated``: total episodes marked as consolidated.
        - ``errors``: list of error messages from failed groups.
    """
    # A hostname/PID identifies an operator process but not one invocation of
    # that process. The per-run suffix is the opaque lease-owner fence used by
    # terminal success/failure persistence.
    worker = f"{_worker_id()}:{uuid.uuid4()}"

    # Failed rows are automatic recovery work. A no-spawner invocation cannot
    # consume that recovery lease, so it must leave the row for the live
    # scheduled Spawner rather than suppressing a future retry.
    claim_retry_failed = retry_failed and cc_spawner is not None

    # -------------------------------------------------------------------------
    # Claim pending / retry-eligible failed episodes via FOR UPDATE SKIP LOCKED
    # -------------------------------------------------------------------------
    async with pool.acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                """
                SELECT id, butler, content, importance, metadata, created_at,
                       tenant_id, consolidation_attempts
                FROM episodes
                WHERE (
                    consolidation_status = 'pending'
                    OR (
                        $1::boolean
                        AND consolidation_status = 'failed'
                        AND consolidation_attempts < $2
                        AND next_consolidation_retry_at IS NOT NULL
                        AND next_consolidation_retry_at <= now()
                    )
                )
                  AND (leased_until IS NULL OR leased_until <= now())
                ORDER BY tenant_id, butler, created_at, id
                FOR UPDATE SKIP LOCKED
                LIMIT $3
                """,
                claim_retry_failed,
                MAX_CONSOLIDATION_ATTEMPTS,
                batch_size,
            )

            if rows:
                episode_ids_to_lease = [row["id"] for row in rows]
                # Set lease: prevent other workers from claiming the same episodes
                await conn.execute(
                    """
                    UPDATE episodes
                    SET leased_until = now() + ($1 * interval '1 second'),
                        leased_by    = $2
                    WHERE id = ANY($3)
                    """,
                    LEASE_DURATION_SECONDS,
                    worker,
                    episode_ids_to_lease,
                )

    # Group episodes by (tenant_id, butler_name) to prevent cross-tenant mixing
    groups: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        group_key = (row["tenant_id"], row["butler"])
        if group_key not in groups:
            groups[group_key] = []
        groups[group_key].append(dict(row))

    # Build initial stats — key is "tenant_id/butler_name" for readability
    group_counts: dict[str, int] = {
        f"{tid}/{bn}": len(episodes) for (tid, bn), episodes in groups.items()
    }

    # Initialize aggregate stats
    total_facts_created = 0
    total_facts_updated = 0
    total_rules_created = 0
    total_confirmations = 0
    total_episodes_consolidated = 0
    groups_consolidated = 0
    all_errors: list[str] = []

    # Process each (tenant_id, butler_name) group (only if spawner is provided)
    if cc_spawner is not None:
        for (tenant_id, butler_name), episodes in groups.items():
            try:
                # 1. Fetch existing facts and rules for dedup context.
                # Includes 'fading' facts (bu-5ud8p.1): they are still live,
                # and consolidation should recognize an episode that reconfirms
                # a fading fact as an update to it, not create a duplicate.
                facts_rows = await pool.fetch(
                    "SELECT id, subject, predicate, content, permanence, entity_id, valid_at "
                    "FROM facts "
                    "WHERE validity IN ('active', 'fading') AND source_butler = $1 "
                    "  AND tenant_id = $2 "
                    "ORDER BY created_at DESC "
                    "LIMIT 100",
                    butler_name,
                    tenant_id,
                )
                existing_facts = [dict(row) for row in facts_rows]

                rules_rows = await pool.fetch(
                    "SELECT id, content, maturity "
                    "FROM rules "
                    "WHERE maturity NOT IN ('anti_pattern') "
                    "  AND (metadata->>'forgotten')::boolean IS NOT TRUE "
                    "  AND source_butler = $1 "
                    "  AND tenant_id = $2 "
                    "ORDER BY created_at DESC "
                    "LIMIT 50",
                    butler_name,
                    tenant_id,
                )
                existing_rules = [dict(row) for row in rules_rows]

                # 2. Build consolidation prompt
                prompt = build_consolidation_prompt(
                    episodes=episodes,
                    existing_facts=existing_facts,
                    existing_rules=existing_rules,
                    butler_name=butler_name,
                )

                # 3. Spawn runtime instance with consolidate skill
                logger.info(
                    "Spawning consolidation session for %s/%s (%d episodes)",
                    tenant_id,
                    butler_name,
                    len(episodes),
                )
                result = await cc_spawner.trigger(
                    prompt=prompt,
                    trigger_source="schedule:consolidation",
                )

                output_missing = result.output is None or not result.output.strip()
                if not result.success or output_missing:
                    if output_missing and result.success:
                        failure_category = "runtime_no_output"
                    else:
                        failure_category = "runtime_unsuccessful"
                    failure_message = _safe_failure_message(failure_category)
                    error_msg = f"runtime session failed for {butler_name}: {failure_message}"
                    logger.error("%s", error_msg)
                    all_errors.append(error_msg)
                    # The worker may transition only its own active lease.
                    group_episode_ids = [uuid.UUID(str(ep["id"])) for ep in episodes]
                    await _mark_group_failed(
                        pool,
                        group_episode_ids,
                        failure_category,
                        tenant_id=tenant_id,
                        claim_token=worker,
                    )
                    continue

                # 4. Parse runtime output
                parsed = parse_consolidation_output(result.output)
                if parsed.parse_errors:
                    logger.warning("Parse errors for %s: %s", butler_name, parsed.parse_errors)
                    all_errors.extend(parsed.parse_errors)

                # 5. Execute consolidation actions — thread tenant_id and a
                #    per-group request_id through so derived knowledge is stored
                #    under the correct tenant.
                group_episode_ids = [uuid.UUID(str(ep["id"])) for ep in episodes]
                group_request_id = str(uuid.uuid4())
                exec_result = await execute_consolidation(
                    pool=pool,
                    embedding_engine=embedding_engine,
                    parsed=parsed,
                    source_episode_ids=group_episode_ids,
                    butler_name=butler_name,
                    tenant_id=tenant_id,
                    request_id=group_request_id,
                    enable_shared_catalog=enable_shared_catalog,
                    source_schema=source_schema,
                    claim_token=worker,
                    lease_duration_seconds=LEASE_DURATION_SECONDS,
                )

                # A lease can expire while the runtime is executing. Do not
                # count a stale worker as successful or write a run audit row;
                # its owner-fenced terminal write has already failed closed.
                if exec_result["episodes_consolidated"] != len(group_episode_ids):
                    if exec_result["errors"]:
                        all_errors.extend(exec_result["errors"])
                    else:
                        all_errors.append(
                            "Consolidation lease was lost before episodes could be finalized"
                        )
                    continue

                # Aggregate stats
                total_facts_created += exec_result["facts_created"]
                total_facts_updated += exec_result["facts_updated"]
                total_rules_created += exec_result["rules_created"]
                total_confirmations += exec_result["confirmations_made"]
                total_episodes_consolidated += exec_result["episodes_consolidated"]
                groups_consolidated += 1

                if exec_result["errors"]:
                    all_errors.extend(exec_result["errors"])

                # Persist one audit row per successful butler-group run so the
                # read-side stats endpoint can derive last_consolidation_at /
                # last_consolidation_facts_produced. Best-effort: a logging
                # failure must never mask a successful consolidation.
                await _record_consolidation_run(
                    pool,
                    butler=butler_name,
                    episodes_processed=exec_result["episodes_consolidated"],
                    facts_produced=exec_result["facts_created"],
                    facts_updated=exec_result["facts_updated"],
                    rules_created=exec_result["rules_created"],
                    confirmations_made=exec_result["confirmations_made"],
                    errors=len(exec_result["errors"]),
                )

                logger.info(
                    "Consolidated %s/%s: %d facts, %d rules, %d episodes",
                    tenant_id,
                    butler_name,
                    exec_result["facts_created"] + exec_result["facts_updated"],
                    exec_result["rules_created"],
                    exec_result["episodes_consolidated"],
                )

            except Exception as exc:
                error_msg = f"Failed to consolidate {butler_name}"
                logger.error("%s: %s", error_msg, exc, exc_info=True)
                all_errors.append(error_msg)
                # Clear leases so episodes can be retried / dead-lettered
                try:
                    group_episode_ids = [uuid.UUID(str(ep["id"])) for ep in episodes]
                    await _mark_group_failed(
                        pool,
                        group_episode_ids,
                        "execution_error",
                        tenant_id=tenant_id,
                        claim_token=worker,
                    )
                except Exception as clear_exc:
                    logger.error(
                        "Failed to update failure state for %s: %s", butler_name, clear_exc
                    )

    return {
        "episodes_processed": len(rows),
        "butlers_processed": len(groups),
        "groups": group_counts,
        "groups_consolidated": groups_consolidated,
        "facts_created": total_facts_created,
        "facts_updated": total_facts_updated,
        "rules_created": total_rules_created,
        "confirmations_made": total_confirmations,
        "episodes_consolidated": total_episodes_consolidated,
        "errors": all_errors,
    }


async def _mark_group_failed(
    pool: Pool,
    episode_ids: list[uuid.UUID],
    failure_category: str,
    *,
    tenant_id: str | None = None,
    claim_token: str,
) -> None:
    """Mark a batch of episodes as failed (or dead-lettered) after a group error.

    Called when a butler group's CC spawner call fails entirely. Each episode
    must still belong to this worker's active lease. It is then evaluated
    individually: if it reaches MAX_CONSOLIDATION_ATTEMPTS it is dead-lettered,
    otherwise it is moved to 'failed' with exponential backoff and the lease is
    cleared.

    Also emits a ``episode_consolidation_failed`` or
    ``episode_consolidation_dead_letter`` event to memory_events.
    """
    if not episode_ids:
        return

    safe_error_message = _safe_failure_message(failure_category)

    # The update and event insert share one CTE statement, so an absent event
    # table or failed event insert rolls back the lifecycle transition rather
    # than silently losing the terminal audit. The lease-owner/expiry predicate
    # makes a stale invocation a no-op after a replacement worker claims it.
    await pool.execute(
        """
        WITH transitioned AS (
            UPDATE episodes
            SET consolidation_attempts    = consolidation_attempts + 1,
                last_consolidation_error  = $1,
                consolidated              = false,
                leased_until              = NULL,
                leased_by                 = NULL,
                consolidation_status      = CASE
                    WHEN consolidation_attempts + 1 >= $2 THEN 'dead_letter'
                    ELSE 'failed'
                END,
                dead_letter_reason        = CASE
                    WHEN consolidation_attempts + 1 >= $2 THEN $1
                    ELSE NULL
                END,
                next_consolidation_retry_at = CASE
                    WHEN consolidation_attempts + 1 >= $2 THEN NULL
                    ELSE now() + (power(2, consolidation_attempts + 1) * $3
                                  * interval '1 second')
                END
            WHERE id = ANY($4::uuid[])
              AND leased_by = $5
              AND leased_until > now()
              AND consolidation_status IN ('pending', 'failed')
              AND consolidation_attempts < $2
            RETURNING id, butler, tenant_id, consolidation_attempts, consolidation_status
        )
        INSERT INTO memory_events (event_type, actor, tenant_id, actor_butler,
                                   memory_type, memory_id, payload)
        SELECT
            CASE consolidation_status
                WHEN 'dead_letter' THEN 'episode_consolidation_dead_letter'
                ELSE 'episode_consolidation_failed'
            END,
            'consolidation_worker',
            COALESCE($6, tenant_id),
            butler,
            'episode',
            id,
            jsonb_build_object(
                'attempts', consolidation_attempts,
                'outcome', CASE consolidation_status
                    WHEN 'dead_letter' THEN 'dead_letter'
                    ELSE 'retry_scheduled'
                END
            )
        FROM transitioned
        """,
        safe_error_message,
        MAX_CONSOLIDATION_ATTEMPTS,
        BASE_RETRY_SECONDS,
        episode_ids,
        claim_token,
        tenant_id,
    )


async def _record_consolidation_run(
    pool: Pool,
    *,
    butler: str,
    episodes_processed: int,
    facts_produced: int,
    facts_updated: int,
    rules_created: int,
    confirmations_made: int,
    errors: int,
) -> None:
    """Insert one row into ``public.consolidation_runs``; best-effort (no raise).

    Mirrors ``_log_compaction`` in scheduled_jobs: an audit-write failure (e.g.
    the table not yet migrated, or a privilege gap) must not fail an otherwise
    successful consolidation run. Called once per successfully consolidated
    ``(tenant_id, butler)`` group.
    """
    try:
        await pool.execute(
            """
            INSERT INTO public.consolidation_runs
                (butler, episodes_processed, facts_produced, facts_updated,
                 rules_created, confirmations_made, errors)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            butler,
            episodes_processed,
            facts_produced,
            facts_updated,
            rules_created,
            confirmations_made,
            errors,
        )
    except Exception as exc:
        logger.warning("Failed to record consolidation run for %s: %s", butler, exc)


# ---------------------------------------------------------------------------
# Episode cleanup
# ---------------------------------------------------------------------------


async def run_episode_cleanup(
    pool: Pool,
    *,
    max_entries: int = 10000,
    batch_size: int = EPISODE_CLEANUP_BATCH_SIZE,
) -> dict[str, Any]:
    """Delete expired episodes and enforce a capacity limit.

    The cleanup proceeds in three steps:

    1. **Expire** — delete expired episodes that are safe to reap, in bounded
       batches. Deletion is *consolidation-aware* (see
       :data:`REAPABLE_EXPIRED_EPISODE_SQL`): an expired episode still in
       ``consolidation_status = 'pending'`` is retained until it is
       :data:`EPISODE_PENDING_GRACE_DAYS` past its ``expires_at`` so a lagging
       consolidator never loses an un-extracted observation, while a
       permanently-stuck pending episode is still reaped once past the grace
       window. Non-pending expired episodes are deleted as soon as they expire.
    2. **Count** — check how many episodes remain.
    3. **Capacity** — if the remaining count exceeds *max_entries*, delete the
       oldest *consolidated* episodes until the count is within budget.
       Unconsolidated episodes are never deleted for capacity.

    Every ``DELETE`` runs in bounded *batch_size* chunks rather than one
    unbounded statement, so re-enabling the job on a butler with a large
    accumulated backlog drains incrementally without a single table-wide lock.

    Args:
        pool: asyncpg connection pool for the memory database.
        max_entries: Maximum number of episodes to retain (default 10 000).
        batch_size: Maximum rows deleted per statement (default
            :data:`EPISODE_CLEANUP_BATCH_SIZE`).

    Returns:
        A stats dict with keys:
        - ``expired_deleted``: episodes removed because they expired.
        - ``capacity_deleted``: consolidated episodes removed for capacity.
        - ``remaining``: total episodes still in the table.
    """
    # Step 1: delete reapable expired episodes in bounded, consolidation-aware
    # batches. asyncpg returns e.g. "DELETE 42".
    expired_deleted = 0
    while True:
        expired_result = await pool.execute(
            "DELETE FROM episodes WHERE id IN ("
            "  SELECT id FROM episodes "
            f"  WHERE {REAPABLE_EXPIRED_EPISODE_SQL} "
            "  LIMIT $1"
            ")",
            batch_size,
        )
        deleted = int(expired_result.split()[-1])
        expired_deleted += deleted
        if deleted < batch_size:
            break

    # Step 2: count remaining episodes
    remaining = await pool.fetchval("SELECT COUNT(*) FROM episodes")

    # Step 3: enforce capacity limit, deleting the oldest consolidated episodes
    # in bounded batches until within budget (or no more consolidated rows).
    capacity_deleted = 0
    while remaining > max_entries:
        excess = min(remaining - max_entries, batch_size)
        cap_result = await pool.execute(
            "DELETE FROM episodes WHERE id IN ("
            "  SELECT id FROM episodes "
            "  WHERE consolidated = true "
            "  ORDER BY created_at ASC "
            "  LIMIT $1"
            ")",
            excess,
        )
        deleted = int(cap_result.split()[-1])
        capacity_deleted += deleted
        remaining -= deleted
        # No further consolidated rows to reclaim — stop rather than spin.
        if deleted < excess:
            break

    return {
        "expired_deleted": expired_deleted,
        "capacity_deleted": capacity_deleted,
        "remaining": remaining,
    }
