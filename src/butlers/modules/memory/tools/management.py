"""Memory management tools — forget and stats."""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from asyncpg import Pool

from butlers.modules.memory.tools._helpers import _storage

logger = logging.getLogger(__name__)


async def memory_forget(
    pool: Pool,
    memory_type: str,
    memory_id: str,
) -> dict[str, Any]:
    """Soft-delete a memory by type and ID.

    Args:
        pool: asyncpg connection pool.
        memory_type: One of 'episode', 'fact', 'rule'.
        memory_id: UUID string of the memory to forget.

    Returns:
        Dict with key ``forgotten`` (bool) indicating success.
    """
    result = await _storage.forget_memory(pool, memory_type, uuid.UUID(memory_id))
    return {"forgotten": result}


async def memory_stats(
    pool: Pool,
    *,
    scope: str | None = None,
) -> dict[str, Any]:
    """Return system health indicators across all memory types.

    Args:
        pool: asyncpg connection pool.
        scope: Optional scope filter for facts and rules.

    Returns:
        Dict with keys ``episodes``, ``facts``, ``rules``, each containing
        count breakdowns by status/maturity.
    """
    # --- Episodes ---
    ep_total = await pool.fetchval("SELECT COUNT(*) FROM episodes")
    ep_unconsolidated = await pool.fetchval(
        "SELECT COUNT(*) FROM episodes WHERE consolidation_status = 'pending'"
    )
    ep_backlog_age = await pool.fetchval(
        "SELECT EXTRACT(EPOCH FROM (now() - MIN(created_at))) / 3600 "
        "FROM episodes WHERE consolidation_status = 'pending'"
    )

    # --- Facts ---
    scope_filter_facts = ""
    scope_params: list[Any] = []
    if scope is not None:
        scope_filter_facts = " AND scope IN ('global', $1)"
        scope_params = [scope]

    facts_active = await pool.fetchval(
        "SELECT COUNT(*) FROM facts WHERE validity = 'active'"
        " AND (metadata->>'status' IS NULL OR metadata->>'status' != 'fading')"
        + scope_filter_facts,
        *scope_params,
    )
    facts_fading = await pool.fetchval(
        "SELECT COUNT(*) FROM facts WHERE validity = 'active'"
        " AND metadata->>'status' = 'fading'" + scope_filter_facts,
        *scope_params,
    )
    facts_superseded = await pool.fetchval(
        "SELECT COUNT(*) FROM facts WHERE validity = 'superseded'" + scope_filter_facts,
        *scope_params,
    )
    facts_expired = await pool.fetchval(
        "SELECT COUNT(*) FROM facts WHERE validity = 'expired'" + scope_filter_facts,
        *scope_params,
    )

    # --- Rules ---
    scope_filter_rules = ""
    if scope is not None:
        scope_filter_rules = " AND scope IN ('global', $1)"

    rules_candidate = await pool.fetchval(
        "SELECT COUNT(*) FROM rules WHERE maturity = 'candidate'"
        " AND (metadata->>'forgotten')::boolean IS NOT TRUE" + scope_filter_rules,
        *scope_params,
    )
    rules_established = await pool.fetchval(
        "SELECT COUNT(*) FROM rules WHERE maturity = 'established'"
        " AND (metadata->>'forgotten')::boolean IS NOT TRUE" + scope_filter_rules,
        *scope_params,
    )
    rules_proven = await pool.fetchval(
        "SELECT COUNT(*) FROM rules WHERE maturity = 'proven'"
        " AND (metadata->>'forgotten')::boolean IS NOT TRUE" + scope_filter_rules,
        *scope_params,
    )
    rules_anti_pattern = await pool.fetchval(
        "SELECT COUNT(*) FROM rules WHERE maturity = 'anti_pattern'" + scope_filter_rules,
        *scope_params,
    )
    rules_forgotten = await pool.fetchval(
        "SELECT COUNT(*) FROM rules WHERE (metadata->>'forgotten')::boolean IS TRUE"
        + scope_filter_rules,
        *scope_params,
    )

    return {
        "episodes": {
            "total": ep_total,
            "unconsolidated": ep_unconsolidated,
            "backlog_age_hours": float(ep_backlog_age) if ep_backlog_age is not None else None,
        },
        "facts": {
            "active": facts_active,
            "fading": facts_fading,
            "superseded": facts_superseded,
            "expired": facts_expired,
        },
        "rules": {
            "candidate": rules_candidate,
            "established": rules_established,
            "proven": rules_proven,
            "anti_pattern": rules_anti_pattern,
            "forgotten": rules_forgotten,
        },
    }
