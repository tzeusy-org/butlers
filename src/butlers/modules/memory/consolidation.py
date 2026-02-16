"""Consolidation runner and episode cleanup for the Memory Butler.

Provides two main entry points:

* ``run_consolidation`` — fetches unconsolidated episodes, groups by source
  butler, and prepares them for consolidation (actual CC spawning is handled
  separately).
* ``run_episode_cleanup`` — deletes expired episodes and enforces a capacity
  limit on the episodes table.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from asyncpg import Pool


# ---------------------------------------------------------------------------
# Consolidation runner
# ---------------------------------------------------------------------------


async def run_consolidation(
    pool: Pool,
    embedding_engine: Any,
) -> dict[str, Any]:
    """Fetch unconsolidated episodes, group by source butler, and return stats.

    Episodes with ``consolidation_status = 'pending'`` are fetched in chronological order
    and grouped by their ``butler`` column.  For each butler group the
    function collects the episodes ready for downstream prompt building
    (task 6.2) and CC spawning (task 6.4).

    Args:
        pool: asyncpg connection pool for the memory database.
        embedding_engine: EmbeddingEngine instance (reserved for downstream
            consolidation steps that will embed new facts/rules).

    Returns:
        A stats dict with keys:
        - ``episodes_processed``: total unconsolidated episodes found.
        - ``butlers_processed``: number of distinct butler groups.
        - ``groups``: mapping of butler name to episode count.
    """
    rows = await pool.fetch(
        "SELECT id, butler, content, importance, metadata, created_at "
        "FROM episodes "
        "WHERE consolidation_status = 'pending' "
        "ORDER BY created_at ASC"
    )

    # Group episodes by source butler
    groups: dict[str, list[dict]] = {}
    for row in rows:
        butler_name = row["butler"]
        if butler_name not in groups:
            groups[butler_name] = []
        groups[butler_name].append(dict(row))

    # Build stats
    group_counts: dict[str, int] = {
        butler_name: len(episodes) for butler_name, episodes in groups.items()
    }

    return {
        "episodes_processed": len(rows),
        "butlers_processed": len(groups),
        "groups": group_counts,
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
            "  WHERE consolidation_status = 'consolidated' "
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
