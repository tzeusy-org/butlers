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

from butlers.core_tools._base import ToolContext

logger = logging.getLogger(__name__)

# Memory stores exposed to the LLM tier model.
_MEMORY_STORES: list[str] = ["episodes", "facts", "rules"]


async def _query_drops_7d(pool: Any) -> int:
    """Count memories dropped (expired/forgotten) in the last 7 days.

    Sums soft-deleted facts, expired episodes, and forgotten rules so the
    dashboard can surface retention pressure without a full stats query.

    Returns 0 when the memory schema is not present (table does not exist).
    """
    try:
        facts_dropped = await pool.fetchval(
            "SELECT COUNT(*) FROM facts WHERE validity IN ('superseded', 'expired')"
            " AND updated_at >= now() - interval '7 days'"
        )
        episodes_dropped = await pool.fetchval(
            "SELECT COUNT(*) FROM episodes WHERE expires_at <= now()"
            " AND expires_at >= now() - interval '7 days'"
        )
        rules_dropped = await pool.fetchval(
            "SELECT COUNT(*) FROM rules WHERE (metadata->>'forgotten')::boolean IS TRUE"
            " AND updated_at >= now() - interval '7 days'"
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
        # Detect whether the memory module is loaded and active.
        has_memory = any(
            getattr(mod, "name", None) == "memory" for mod in getattr(daemon, "_modules", [])
        )

        if not has_memory or pool is None:
            return {
                "read": [],
                "write": [],
                "namespace": None,
                "embedding_model": None,
                "drops_7d": 0,
            }

        drops_7d = await _query_drops_7d(pool)

        # Resolve the embedding model name from the memory module config if
        # available, falling back to the known default.
        embedding_model: str | None = None
        for mod in daemon._modules:
            if getattr(mod, "name", None) == "memory":
                cfg = getattr(mod, "_config", None)
                if cfg is not None:
                    embedding_model = getattr(cfg, "embedding_model", None)
                break
        if embedding_model is None:
            embedding_model = "all-MiniLM-L6-v2"

        return {
            "read": list(_MEMORY_STORES),
            "write": list(_MEMORY_STORES),
            "namespace": ctx.butler_name,
            "embedding_model": embedding_model,
            "drops_7d": drops_7d,
        }
