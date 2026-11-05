"""Memory-access core tool: memory_access.

Exposes per-store read/write access metadata for the butler, along with
a 7-day drop count.  Always registered on every butler regardless of whether
the memory module is loaded.  When the memory module is absent the tool
returns empty read/write lists so the dashboard can detect "no memory access".
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from butlers.core.chronicler_visibility import local_fact_visibility_condition
from butlers.core_tools._base import ToolContext

logger = logging.getLogger(__name__)

# Memory stores exposed to the LLM tier model.
_MEMORY_STORES: list[str] = ["episodes", "facts", "rules"]


async def _query_drops_7d(pool: Any, *, memory_schema: str | None = None) -> int:
    """Count memories dropped (expired/forgotten) in the last 7 days.

    Counts superseded/expired facts by dropped_at, expired episodes by
    expires_at, and forgotten rules by forgotten_at. One database as_of bounds
    every branch inclusively to [as_of - 7 days, as_of]. NULL legacy transition
    times are excluded, so pre-migration terminal history may be undercounted.
    invalid_at retains its separate bitemporal meaning.

    Returns 0 when the memory schema is not present (table does not exist).
    """
    visibility = await local_fact_visibility_condition(
        pool, memory_schema=memory_schema, alias="facts"
    )
    visibility_filter = f" AND {visibility}" if visibility is not None else ""
    guarded_chronicler_facts = (
        memory_schema == "chronicler_mem"
        and visibility is not None
        and "chronicler_mem.facts_readable" in visibility
    )
    try:
        as_of = await pool.fetchval("SELECT now()")
        facts_dropped = await pool.fetchval(
            "SELECT COUNT(*) FROM facts AS facts "
            "WHERE facts.validity IN ('superseded', 'expired') "
            "AND facts.dropped_at >= $1::timestamptz - interval '7 days' "
            "AND facts.dropped_at <= $1::timestamptz" + visibility_filter,
            as_of,
        )
    except Exception:
        if guarded_chronicler_facts:
            raise
        logger.debug("drops_7d facts query failed — memory schema may be absent", exc_info=True)
        return 0

    try:
        episodes_dropped = await pool.fetchval(
            "SELECT COUNT(*) FROM episodes WHERE expires_at <= $1::timestamptz"
            " AND expires_at >= $1::timestamptz - interval '7 days'",
            as_of,
        )
        rules_dropped = await pool.fetchval(
            "SELECT COUNT(*) FROM rules WHERE (metadata->>'forgotten')::boolean IS TRUE"
            " AND forgotten_at >= $1::timestamptz - interval '7 days'"
            " AND forgotten_at <= $1::timestamptz",
            as_of,
        )
        return int((facts_dropped or 0) + (episodes_dropped or 0) + (rules_dropped or 0))
    except Exception:
        logger.debug("drops_7d query failed — memory schema may be absent", exc_info=True)
        return 0


def register_memory_access_tool(ctx: ToolContext, mcp: Any, _core_tool: Callable) -> None:
    """Register the ``memory_access`` MCP tool.

    Always registered on every butler.  Returns empty stores when the memory
    module is not loaded so the dashboard route degrades gracefully.
    """
    daemon = ctx.daemon
    pool = ctx.pool

    @_core_tool("infra")
    async def memory_access() -> dict[str, Any]:
        """Return memory store access metadata for this butler.

        Reports which memory stores (episodes, facts, rules) this butler can
        read from and write to, plus the count of items dropped in the last
        7 days.  When the memory module is not loaded, read and write are
        empty lists.

        Response shape::

            {
                "read": ["episodes", "facts", "rules"],
                "write": ["episodes", "facts", "rules"],
                "namespace": "<butler-name>",
                "embedding_model": "all-MiniLM-L6-v2",
                "drops_7d": 3
            }
        """
        # Detect the memory module and resolve its config in a single pass.
        memory_mod = next(
            (m for m in getattr(daemon, "_modules", []) if getattr(m, "name", None) == "memory"),
            None,
        )

        if memory_mod is None or pool is None:
            return {
                "read": [],
                "write": [],
                "namespace": ctx.butler_name,
                "embedding_model": None,
                "drops_7d": 0,
            }

        # The module owns the configured memory pool. In particular, a private
        # Chronicler memory schema must not be counted through the domain pool
        # supplied in the core ToolContext.
        cfg = getattr(memory_mod, "_config", None)
        memory_schema = getattr(cfg, "memory_schema", None)
        get_memory_pool = getattr(memory_mod, "_get_pool", None)
        if callable(get_memory_pool):
            try:
                memory_pool = get_memory_pool()
            except Exception:
                logger.debug("memory pool unavailable for drops_7d", exc_info=True)
                memory_pool = None
        else:
            # Compatibility for lightweight modules without their own pool is
            # safe only when no separate memory schema is configured.
            memory_pool = pool if memory_schema is None else None
        drops_7d = (
            await _query_drops_7d(memory_pool, memory_schema=memory_schema)
            if memory_pool is not None
            else 0
        )

        # Resolve the embedding model name from the memory module config.
        # ``MemoryModuleConfig.embedding_model`` always has a string default
        # ("all-MiniLM-L6-v2"), so the config-sourced path is authoritative.
        # The ``or`` fallback is kept only for defensiveness against test
        # doubles or malformed modules that pass ``embedding_model=None``.
        embedding_model: str = getattr(cfg, "embedding_model", None) or "all-MiniLM-L6-v2"

        return {
            "read": list(_MEMORY_STORES),
            "write": list(_MEMORY_STORES),
            "namespace": ctx.butler_name,
            "embedding_model": embedding_model,
            "drops_7d": drops_7d,
        }
