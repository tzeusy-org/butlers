"""``describe``: the server's self-description (RFC 0039 D5, D10, D17; REQ-...-006).

It answers whenever the projection is reachable, including while every other tool refuses
for staleness or a kill switch: a domain any visible switch layer disables reports
``disabled``, otherwise a stale projection reports ``stale``.
"""

from __future__ import annotations

from functools import cache
from importlib.metadata import PackageNotFoundError, version
from typing import Any

import asyncpg

from butlers.knowledge_read.core import (
    DOMAINS,
    Arguments,
    ReadContext,
    in_snapshot,
    projection_disabled_domains,
    published,
)
from butlers.knowledge_read.manifest import load_manifest

# The pinned v1 catalogue (D10), in table order.
TOOLS = (
    "search",
    "fetch",
    "search_filtered",
    "entity_lookup",
    "entity_profile",
    "graph_neighbors",
    "graph_path",
    "timeline",
    "time_use",
    "trips",
    "describe",
    "finance_summary",
    "finance_ledger",
    "finance_obligations",
    "finance_records",
    "health_series",
    "health_profile",
    "health_records",
    "propose_fact",
)


def server_version() -> str:
    try:
        return version("butlers")
    except PackageNotFoundError:
        return "unknown"


# What each view returns beyond or instead of its manifest columns (D2, D4). The owner
# program builds the views; a unit test pins this map to its subscriber plan.
_FILTER_ONLY = {
    "public.entities": {"deleted", "unidentified", "placeholder_name"},
    "public.entity_graph_edges": {"publish_key"},
}
_DERIVED = {
    "public.entity_graph_edges": ("source_domain",),
    "relationship.entity_facts": ("object_entity_id",),
}


@cache
def published_columns() -> dict[str, list[str]]:
    """The ``fields`` keys each published source's view returns, filter-only columns excluded."""
    return {
        t.source: sorted(
            {c for c in t.columns if c not in _FILTER_ONLY.get(t.source, set())}
            | set(_DERIVED.get(t.source, ()))
        )
        for t in load_manifest().tables
    }


async def describe(
    conn: asyncpg.Connection, context: ReadContext, arguments: object
) -> dict[str, Any]:
    """``{server_version, manifest_digest, domains, as_of, tools, columns}``; never refuses."""
    Arguments(arguments, set())
    _, state_view = published("mcp_surface.manifest_state", "describe")

    async def read() -> tuple[str | None, frozenset[str]]:
        digest = await conn.fetchval(
            f"SELECT fields->>'digest' FROM {state_view} ORDER BY source_id LIMIT 1"
        )
        return digest, await projection_disabled_domains(conn)

    digest, replicated_off = await in_snapshot(conn, read)
    off = context.disabled_domains | replicated_off
    stale = context.freshness == "stale"
    return {
        "server_version": server_version(),
        "manifest_digest": digest,
        "domains": {
            d: "disabled" if d in off else "stale" if stale else "enabled" for d in DOMAINS
        },
        "as_of": None if context.as_of is None else context.as_of.isoformat(),
        "tools": list(TOOLS),
        "columns": published_columns(),
    }
