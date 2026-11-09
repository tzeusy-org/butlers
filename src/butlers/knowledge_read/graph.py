"""``graph_neighbors`` and ``graph_path`` over the published entity graph (RFC 0039 D10).

Parity with ``entity_graph_walk``/``entity_graph_path`` (REQ-entity-graph-003/005/006/009):
traversal is undirected, follows live edges only, reports each reached entity once at its
nearest hop, never revisits the start, and a path is an ordered list of real edge receipts or
``null``, never a guess. The published edge view already holds only ``normal`` live edges
between published entities (REQ-public-mcp-projection-012); withheld stubs are counts in their
own view and never a hop. An edge or count whose fact source's domain is disabled is dropped.
"""

from __future__ import annotations

from typing import Any

import asyncpg

from butlers.knowledge_read.core import (
    Arguments,
    ReadContext,
    ReadError,
    fields,
    fit,
    in_snapshot,
    published,
    record_uri,
    source_domains,
)
from butlers.knowledge_read.entities import _entities, _entity, _survivors

NEIGHBOR_LIMIT = 200
NEIGHBOR_HOPS = 2
PATH_HOPS = 4
# Edges read per hop; a hub past this cap is cut (neighbors) or refused (path).
HOP_EDGE_CAP = 5000


class _Graph:
    """The per-call view names and disabled-source filters every hop query shares."""

    def __init__(self, context: ReadContext, tool: str):
        self.context = context
        self.entities, self.entity_view = published("public.entities", tool)
        self.edges, self.edge_view = published("public.entity_graph_edges", tool)
        _, self.count_view = published("public.entity_graph_withheld_counts", tool)
        domains = source_domains()
        self.disabled_sources = sorted(s for s, d in domains.items() if not context.enabled(d))
        self.disabled_domains = sorted({d for d in domains.values() if not context.enabled(d)})

    async def resolve(self, conn: asyncpg.Connection, entity_id: str) -> dict[str, Any]:
        """The published survivor of ``entity_id``, or ``not_found``."""
        found = await _entities(conn, self.entity_view, [entity_id])
        survivor = (await _survivors(conn, self.entity_view, found)).get(entity_id)
        if survivor is None:
            raise ReadError("not_found", domain="relationship")
        return survivor

    async def hop(
        self, conn: asyncpg.Connection, frontier: list[str], predicates: tuple[str, ...] | None
    ) -> list[dict[str, Any]]:
        rows = await conn.fetch(
            f"""SELECT source_id, fields FROM {self.edge_view}
            WHERE (fields->>'subject_entity_id' = ANY($1::text[])
                   OR fields->>'object_entity_id' = ANY($1::text[]))
              AND ($2::text[] IS NULL OR fields->>'predicate' = ANY($2::text[]))
              AND COALESCE(fields->>'source_domain', 'relationship') <> ALL($3::text[])
            ORDER BY source_id LIMIT $4""",
            frontier,
            None if predicates is None else list(predicates),
            self.disabled_domains,
            HOP_EDGE_CAP + 1,
        )
        return [fields(r["fields"]) for r in rows]

    async def withheld(self, conn: asyncpg.Connection, ids: list[str]) -> dict[str, int]:
        rows = await conn.fetch(
            f"""SELECT fields->>'entity_id' AS id, sum((fields->>'withheld')::bigint) AS n
            FROM {self.count_view}
            WHERE fields->>'entity_id' = ANY($1::text[])
              AND fields->>'source_key' <> ALL($2::text[])
            GROUP BY 1""",
            ids,
            self.disabled_sources,
        )
        return {r["id"]: int(r["n"]) for r in rows}

    def receipt(self, edge: dict[str, Any]) -> dict[str, Any]:
        kind, uri = record_uri(self.edges, edge["id"])
        return {
            "id": edge["id"],
            "subject_entity_id": edge["subject_entity_id"],
            "predicate": edge["predicate"],
            "object_entity_id": edge["object_entity_id"],
            "source": self.context.source(kind, uri),
        }


def _predicates(args: Arguments) -> tuple[str, ...] | None:
    return args.subset("predicates", max_items=20, max_length=64)


async def graph_neighbors(
    conn: asyncpg.Connection, context: ReadContext, arguments: object
) -> dict[str, Any]:
    """Entities within ``hops`` (1 or 2) of one entity, nearest first, with edge receipts.

    Returns ``{entity, nodes: [{entity_id, name, type, hop, via_edge_id, withheld, source}],
    edges, withheld}``: ``edges`` holds the receipt each node was reached by, and ``withheld``
    counts the start entity's sensitivity-withheld stubs, which are never shown or walked.
    """
    args = Arguments(arguments, {"entity_id", "predicates", "hops", "limit"})
    entity_id = args.uuid("entity_id")
    predicates = _predicates(args)
    hops = args.integer("hops", low=1, high=NEIGHBOR_HOPS, default=1)
    limit = args.integer("limit", low=1, high=NEIGHBOR_LIMIT, default=100)
    context.require("relationship")
    graph = _Graph(context, "graph_neighbors")

    async def read() -> dict[str, Any]:
        start = await graph.resolve(conn, entity_id)
        reached: dict[str, tuple[int, dict[str, Any]]] = {}
        frontier, seen, cut = [start["id"]], {start["id"]}, False
        for hop in range(1, hops + 1):
            if not frontier:
                break
            edges = await graph.hop(conn, frontier, predicates)
            cut |= len(edges) > HOP_EDGE_CAP
            current, following = set(frontier), []
            for edge in edges[:HOP_EDGE_CAP]:
                subject, obj = edge["subject_entity_id"], edge["object_entity_id"]
                for near, far in ((subject, obj), (obj, subject)):
                    if near in current and far not in seen:
                        seen.add(far)
                        reached[far] = (hop, edge)
                        following.append(far)
            frontier = sorted(following)
        ordered = sorted(reached.items(), key=lambda item: (item[1][0], item[0]))
        cut |= len(ordered) > limit
        ordered = ordered[:limit]
        ids = [node for node, _ in ordered]
        names = await _entities(conn, graph.entity_view, ids)
        withheld = await graph.withheld(conn, [start["id"], *ids])
        nodes = []
        for node, (hop, edge) in ordered:
            data = names.get(node) or {"id": node, "canonical_name": None, "entity_type": None}
            nodes.append(
                {
                    **{
                        k: v
                        for k, v in _entity(context, graph.entities, data).items()
                        if k in ("entity_id", "name", "type", "source")
                    },
                    "hop": hop,
                    "via_edge_id": edge["id"],
                    "withheld": withheld.get(node, 0),
                }
            )
        receipts = {edge["id"]: graph.receipt(edge) for _, (_, edge) in ordered}
        entity = _entity(context, graph.entities, start)
        total = withheld.get(start["id"], 0)

        def build(kept: list, truncated: bool) -> dict[str, Any]:
            kept_edges = sorted({n["via_edge_id"] for n in kept})
            return {
                "entity": entity,
                "nodes": kept,
                "edges": [receipts[e] for e in kept_edges],
                "withheld": total,
            }

        result = fit(nodes, build)
        if cut:
            result["truncated"] = True
        return result

    return await in_snapshot(conn, read)


async def graph_path(
    conn: asyncpg.Connection, context: ReadContext, arguments: object
) -> dict[str, Any]:
    """The shortest live-edge path of at most ``max_hops`` (1 to 4) between two entities.

    Returns ``{path: [edge receipts]}`` in source-to-target order, ``{path: []}`` when both
    ids name the same published entity, and ``{path: null}`` when no path is that short.
    """
    args = Arguments(arguments, {"from_id", "to_id", "max_hops"})
    from_id, to_id = args.uuid("from_id"), args.uuid("to_id")
    max_hops = args.integer("max_hops", low=1, high=PATH_HOPS, default=PATH_HOPS)
    context.require("relationship")
    graph = _Graph(context, "graph_path")

    async def read() -> dict[str, Any]:
        source = (await graph.resolve(conn, from_id))["id"]
        target = (await graph.resolve(conn, to_id))["id"]
        if source == target:
            return {"path": []}
        parent: dict[str, dict[str, Any]] = {}
        frontier, seen = [source], {source}
        for _ in range(max_hops):
            edges = await graph.hop(conn, frontier, None)
            if len(edges) > HOP_EDGE_CAP:
                raise ReadError("too_large", domain="relationship")
            current, following = set(frontier), []
            for edge in edges:
                subject, obj = edge["subject_entity_id"], edge["object_entity_id"]
                for near, far in ((subject, obj), (obj, subject)):
                    if near in current and far not in seen:
                        seen.add(far)
                        parent[far] = edge
                        following.append(far)
            if target in seen:
                path, node = [], target
                while node != source:
                    edge = parent[node]
                    path.append(graph.receipt(edge))
                    node = (
                        edge["object_entity_id"]
                        if edge["subject_entity_id"] == node
                        else edge["subject_entity_id"]
                    )
                return {"path": path[::-1]}
            if not following:
                break
            frontier = sorted(following)
        return {"path": None}

    return await in_snapshot(conn, read)
