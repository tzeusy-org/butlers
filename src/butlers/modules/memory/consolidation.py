"""Consolidation runner and episode cleanup for the Memory Butler.

Provides two main entry points:

* ``run_consolidation`` — fetches unconsolidated episodes, groups by source
  butler, orchestrates the full consolidation pipeline (prompt building, CC
  spawning, parsing, and execution), and marks episodes as consolidated.
* ``run_episode_cleanup`` — deletes expired episodes and enforces a capacity
  limit on the episodes table.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from butlers.modules.memory.consolidation_executor import execute_consolidation
from butlers.modules.memory.consolidation_parser import parse_consolidation_output
from butlers.modules.memory.prompt_template import build_consolidation_prompt

if TYPE_CHECKING:
    from asyncpg import Pool

    from butlers.core.spawner import CCSpawner

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Consolidation runner
# ---------------------------------------------------------------------------


async def run_consolidation(
    pool: Pool,
    embedding_engine: Any,
    cc_spawner: CCSpawner | None = None,
) -> dict[str, Any]:
    """Orchestrate the full consolidation pipeline for unconsolidated episodes.

    For each butler group with unconsolidated episodes:
    1. Fetch existing facts and rules for dedup context
    2. Build consolidation prompt via ``build_consolidation_prompt``
    3. Spawn a CC instance with the consolidate skill
    4. Parse CC output with ``parse_consolidation_output``
    5. Execute consolidation actions via ``execute_consolidation``

    Partial failures in one group do not block other groups from processing.

    Args:
        pool: asyncpg connection pool for the memory database.
        embedding_engine: EmbeddingEngine instance for storing new facts/rules.
        cc_spawner: Optional CCSpawner instance for invoking Claude Code. If None,
            only episode grouping is performed (no actual consolidation).

    Returns:
        A stats dict with keys:
        - ``episodes_processed``: total unconsolidated episodes found.
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
    rows = await pool.fetch(
        "SELECT id, butler, content, importance, metadata, created_at "
        "FROM episodes "
        "WHERE consolidated = false "
        "ORDER BY created_at ASC"
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
                    "SELECT id, content, status "
                    "FROM rules "
                    "WHERE status = 'active' AND source_butler = $1 "
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

                # 3. Spawn CC instance with consolidate skill
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
                    error_msg = f"CC session failed for {butler_name}"
                    logger.error("%s: %s", error_msg, result.error)
                    all_errors.append(error_msg)
                    continue

                # 4. Parse CC output
                parsed = parse_consolidation_output(result.output)
                if parsed.parse_errors:
                    logger.warning("Parse errors for %s: %s", butler_name, parsed.parse_errors)
                    all_errors.extend(parsed.parse_errors)

                # 5. Execute consolidation actions
                episode_ids = [row["id"] for row in episodes]
                exec_result = await execute_consolidation(
                    pool=pool,
                    embedding_engine=embedding_engine,
                    parsed=parsed,
                    source_episode_ids=episode_ids,
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
