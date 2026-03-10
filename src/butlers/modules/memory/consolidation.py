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


def _worker_id() -> str:
    """Generate a stable-ish worker identifier from hostname + PID."""
    return f"{socket.gethostname()}:{os.getpid()}"


# ---------------------------------------------------------------------------
# Consolidation runner
# ---------------------------------------------------------------------------


async def run_consolidation(
    pool: Pool,
    embedding_engine: Any,
    cc_spawner: Spawner | None = None,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> dict[str, Any]:
    """Orchestrate the full consolidation pipeline for unconsolidated episodes.

    Uses ``FOR UPDATE SKIP LOCKED`` to claim pending episodes, preventing
    concurrent workers from processing the same episode.  Episodes are ordered
    by ``(tenant_id, butler, created_at, id)`` for deterministic processing.

    For each butler group with pending episodes:
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

    Returns:
        A stats dict with keys:
        - ``episodes_processed``: total episodes claimed (pending episodes found).
        - ``butlers_processed``: number of distinct butler groups.
        - ``groups``: mapping of butler name to episode count.
        - ``groups_consolidated``: number of groups successfully processed.
        - ``facts_created``: total new facts stored.
        - ``facts_updated``: total facts updated.
        - ``rules_created``: total new rules stored.
        - ``confirmations_made``: total fact confirmations made.
        - ``episodes_consolidated``: total episodes marked as consolidated.
        - ``errors``: list of error messages from failed groups.
    """
    worker = _worker_id()

    # -------------------------------------------------------------------------
    # Claim pending episodes via FOR UPDATE SKIP LOCKED
    # -------------------------------------------------------------------------
    async with pool.acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                """
                SELECT id, butler, content, importance, metadata, created_at,
                       tenant_id, consolidation_attempts
                FROM episodes
                WHERE consolidation_status = 'pending'
                  AND (leased_until IS NULL OR leased_until < now())
                  AND (next_consolidation_retry_at IS NULL
                       OR next_consolidation_retry_at <= now())
                ORDER BY tenant_id, butler, created_at, id
                FOR UPDATE SKIP LOCKED
                LIMIT $1
                """,
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

    # Group episodes by source butler
    groups: dict[str, list[dict]] = {}
    for row in rows:
        butler_name = row["butler"]
        if butler_name not in groups:
            groups[butler_name] = []
        groups[butler_name].append(dict(row))

    # Build initial stats
    group_counts: dict[str, int] = {
        butler_name: len(episodes) for butler_name, episodes in groups.items()
    }

    # Initialize aggregate stats
    total_facts_created = 0
    total_facts_updated = 0
    total_rules_created = 0
    total_confirmations = 0
    total_episodes_consolidated = 0
    groups_consolidated = 0
    all_errors: list[str] = []

    # Process each butler group (only if spawner is provided)
    if cc_spawner is not None:
        for butler_name, episodes in groups.items():
            try:
                # 1. Fetch existing facts and rules for dedup context
                facts_rows = await pool.fetch(
                    "SELECT id, subject, predicate, content, permanence "
                    "FROM facts "
                    "WHERE validity = 'active' AND source_butler = $1 "
                    "ORDER BY created_at DESC "
                    "LIMIT 100",
                    butler_name,
                )
                existing_facts = [dict(row) for row in facts_rows]

                rules_rows = await pool.fetch(
                    "SELECT id, content, maturity "
                    "FROM rules "
                    "WHERE maturity NOT IN ('anti_pattern') "
                    "  AND (metadata->>'forgotten')::boolean IS NOT TRUE "
                    "  AND source_butler = $1 "
                    "ORDER BY created_at DESC "
                    "LIMIT 50",
                    butler_name,
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
                    "Spawning consolidation session for %s (%d episodes)",
                    butler_name,
                    len(episodes),
                )
                result = await cc_spawner.trigger(
                    prompt=prompt,
                    trigger_source="schedule:consolidation",
                )

                if not result.success or result.output is None:
                    error_msg = f"runtime session failed for {butler_name}"
                    logger.error("%s: %s", error_msg, result.error)
                    all_errors.append(error_msg)
                    # Clear leases so episodes can be retried / dead-lettered
                    group_episode_ids = [uuid.UUID(str(ep["id"])) for ep in episodes]
                    await _mark_group_failed(
                        pool,
                        group_episode_ids,
                        f"runtime session failed: {result.error}",
                    )
                    continue

                # 4. Parse runtime output
                parsed = parse_consolidation_output(result.output)
                if parsed.parse_errors:
                    logger.warning("Parse errors for %s: %s", butler_name, parsed.parse_errors)
                    all_errors.extend(parsed.parse_errors)

                # 5. Execute consolidation actions
                group_episode_ids = [uuid.UUID(str(ep["id"])) for ep in episodes]
                exec_result = await execute_consolidation(
                    pool=pool,
                    embedding_engine=embedding_engine,
                    parsed=parsed,
                    source_episode_ids=group_episode_ids,
                    butler_name=butler_name,
                )

                # Aggregate stats
                total_facts_created += exec_result["facts_created"]
                total_facts_updated += exec_result["facts_updated"]
                total_rules_created += exec_result["rules_created"]
                total_confirmations += exec_result["confirmations_made"]
                total_episodes_consolidated += exec_result["episodes_consolidated"]
                groups_consolidated += 1

                if exec_result["errors"]:
                    all_errors.extend(exec_result["errors"])

                logger.info(
                    "Consolidated %s: %d facts, %d rules, %d episodes",
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
                    await _mark_group_failed(pool, group_episode_ids, str(exc))
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
    error_message: str,
) -> None:
    """Mark a batch of episodes as failed (or dead-lettered) after a group error.

    Called when a butler group's CC spawner call fails entirely.  Each episode
    is evaluated individually: if it has already hit MAX_CONSOLIDATION_ATTEMPTS
    it is dead-lettered, otherwise it is moved to 'failed' with exponential
    backoff and the lease is cleared.

    Also emits a ``episode_consolidation_failed`` or
    ``episode_consolidation_dead_letter`` event to memory_events.
    """
    if not episode_ids:
        return

    await pool.execute(
        """
        UPDATE episodes
        SET consolidation_attempts    = consolidation_attempts + 1,
            last_consolidation_error  = $1,
            leased_until              = NULL,
            leased_by                 = NULL,
            consolidation_status      = CASE
                WHEN consolidation_attempts + 1 >= $2 THEN 'dead_letter'
                ELSE 'failed'
            END,
            dead_letter_reason        = CASE
                WHEN consolidation_attempts + 1 >= $2 THEN $1
                ELSE dead_letter_reason
            END,
            next_consolidation_retry_at = CASE
                WHEN consolidation_attempts + 1 >= $2 THEN NULL
                ELSE now() + (power(2, consolidation_attempts + 1) * $3
                              * interval '1 second')
            END
        WHERE id = ANY($4)
        """,
        error_message,
        MAX_CONSOLIDATION_ATTEMPTS,
        BASE_RETRY_SECONDS,
        episode_ids,
    )

    # Emit memory_events for the transitions (best-effort)
    try:
        await pool.execute(
            """
            INSERT INTO memory_events (event_type, actor, payload)
            SELECT
                CASE
                    WHEN consolidation_attempts >= $1
                         THEN 'episode_consolidation_dead_letter'
                    ELSE 'episode_consolidation_failed'
                END,
                'consolidation_worker',
                jsonb_build_object(
                    'episode_id', id::text,
                    'attempts',   consolidation_attempts,
                    'error',      $2
                )
            FROM episodes
            WHERE id = ANY($3)
            """,
            MAX_CONSOLIDATION_ATTEMPTS,
            error_message,
            episode_ids,
        )
    except Exception as exc:
        logger.warning("Failed to emit consolidation events: %s", exc)


# ---------------------------------------------------------------------------
# Episode cleanup
# ---------------------------------------------------------------------------


async def run_episode_cleanup(
    pool: Pool,
    *,
    max_entries: int = 10000,
) -> dict[str, Any]:
    """Delete expired episodes and enforce a capacity limit.

    The cleanup proceeds in three steps:

    1. **Expire** — delete episodes whose ``expires_at`` is in the past.
    2. **Count** — check how many episodes remain.
    3. **Capacity** — if the remaining count exceeds *max_entries*, delete the
       oldest *consolidated* episodes until the count is within budget.
       Unconsolidated episodes that have not expired are never deleted.

    Args:
        pool: asyncpg connection pool for the memory database.
        max_entries: Maximum number of episodes to retain (default 10 000).

    Returns:
        A stats dict with keys:
        - ``expired_deleted``: episodes removed because they expired.
        - ``capacity_deleted``: consolidated episodes removed for capacity.
        - ``remaining``: total episodes still in the table.
    """
    # Step 1: delete expired episodes
    expired_result = await pool.execute("DELETE FROM episodes WHERE expires_at < now()")
    # asyncpg returns e.g. "DELETE 42"
    expired_deleted = int(expired_result.split()[-1])

    # Step 2: count remaining episodes
    remaining = await pool.fetchval("SELECT COUNT(*) FROM episodes")

    # Step 3: enforce capacity limit
    capacity_deleted = 0
    if remaining > max_entries:
        excess = remaining - max_entries
        cap_result = await pool.execute(
            "DELETE FROM episodes WHERE id IN ("
            "  SELECT id FROM episodes "
            "  WHERE consolidated = true "
            "  ORDER BY created_at ASC "
            "  LIMIT $1"
            ")",
            excess,
        )
        capacity_deleted = int(cap_result.split()[-1])
        remaining -= capacity_deleted

    return {
        "expired_deleted": expired_deleted,
        "capacity_deleted": capacity_deleted,
        "remaining": remaining,
    }
