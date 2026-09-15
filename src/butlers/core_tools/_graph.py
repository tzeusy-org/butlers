"""entity_graph_walk / entity_graph_path — zero-LLM public entity graph traversal.

RFC 0031 (about/legends-and-lore/rfcs/0031-public-entity-graph-projection.md),
bu-8cdl1.8 Slice 3. Both tools are thin wrappers over the recursive-CTE walk
in ``butlers.core.entity_graph_edges``: no LLM call, no cross-butler MCP
fan-out, one deterministic SQL query per call.

Always registered on every butler, group ``graph`` — every butler role
already holds ``SELECT`` on ``public.entity_graph_edges`` (RFC 0031's grant
model mirrors ``core_210_expected_signals.py``'s ``_ALL_BUTLER_ROLES``), so
gating the tool surface further than the existing grant would just make the
capability harder to reach without narrowing who can already read the data.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any
from uuid import UUID

from pydantic import Field

from butlers.core.entity_graph_edges import (
    MAX_WALK_HOPS,
    find_entity_graph_path,
    walk_entity_graph,
)
from butlers.core_tools._base import ToolContext

_MAX_WALK_LIMIT = 500
_DIRECTIONS = ("out", "in", "both")


def _parse_uuid(value: str, *, field_name: str) -> UUID:
    try:
        return UUID(str(value))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError(f"{field_name} must be a UUID, got {value!r}") from exc


def _validate_direction(direction: str) -> None:
    if direction not in _DIRECTIONS:
        raise ValueError(f"direction must be one of {_DIRECTIONS}, got {direction!r}")


def _edge_dict(row: Any) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "subject_entity_id": str(row["subject_entity_id"]),
        "predicate": row["predicate"],
        "object_entity_id": str(row["object_entity_id"]),
    }


def register_graph_tools(ctx: ToolContext, mcp: Any, _core_tool: Callable) -> None:
    """Register ``entity_graph_walk`` and ``entity_graph_path``."""
    pool = ctx.pool

    @_core_tool("graph")
    async def entity_graph_walk(
        entity_id: Annotated[
            str, Field(description="Start entity UUID (public.entities.id) to walk outward from.")
        ],
        max_hops: Annotated[
            int, Field(ge=1, le=MAX_WALK_HOPS, description="Depth cap on the walk.")
        ] = 2,
        edge_types: Annotated[
            list[str] | None,
            Field(
                description="Restrict traversal to these predicates. Omit to follow any predicate."
            ),
        ] = None,
        direction: Annotated[
            str,
            Field(
                description=(
                    "'out' follows subject->object edges only, 'in' follows "
                    "object->subject only, 'both' (default) treats the graph as "
                    "undirected."
                )
            ),
        ] = "both",
        limit: Annotated[
            int, Field(ge=1, le=_MAX_WALK_LIMIT, description="Max reached entities to return.")
        ] = 100,
    ) -> list[dict[str, Any]]:
        """Walk the public entity graph up to ``max_hops`` from one entity, zero LLM cost.

        Recursive CTE over ``public.entity_graph_edges``, restricted to live
        (non-withheld) edges — a sensitivity-withheld fact was never a
        traversable edge to begin with. Returns one entry per distinct entity
        reached: ``entity_id``, ``hop`` (nearest distance), and ``via_edge``
        (the edge last traversed to reach it — a receipt, not a summary).
        Returns ``[]`` when nothing is reachable within ``max_hops``.
        """
        if pool is None:
            raise RuntimeError("Database pool is not available")
        parsed_entity_id = _parse_uuid(entity_id, field_name="entity_id")
        _validate_direction(direction)

        rows = await walk_entity_graph(
            pool,
            entity_id=parsed_entity_id,
            max_hops=max_hops,
            edge_types=edge_types,
            direction=direction,
            limit=limit,
        )
        return [
            {
                "entity_id": str(row["entity_id"]),
                "hop": row["hop"],
                "via_edge": _edge_dict(row),
            }
            for row in rows
        ]

    @_core_tool("graph")
    async def entity_graph_path(
        from_entity_id: Annotated[str, Field(description="Source entity UUID.")],
        to_entity_id: Annotated[str, Field(description="Target entity UUID.")],
        max_hops: Annotated[
            int, Field(ge=1, le=MAX_WALK_HOPS, description="Depth cap on the search.")
        ] = 4,
        edge_types: Annotated[
            list[str] | None,
            Field(
                description="Restrict traversal to these predicates. Omit to follow any predicate."
            ),
        ] = None,
        direction: Annotated[
            str,
            Field(
                description=(
                    "'out' follows subject->object edges only, 'in' follows "
                    "object->subject only, 'both' (default) treats the graph as "
                    "undirected."
                )
            ),
        ] = "both",
    ) -> dict[str, Any]:
        """Find the shortest live-edge path between two entities, zero LLM cost.

        Returns ``{"found": true, "hops": N, "path": [...]}`` (path ordered
        source-to-target, each entry an edge receipt) or
        ``{"found": false, "hops": null, "path": []}`` when no live-edge path
        connects the two entities within ``max_hops`` — never a guessed or
        partial path.
        """
        if pool is None:
            raise RuntimeError("Database pool is not available")
        parsed_from = _parse_uuid(from_entity_id, field_name="from_entity_id")
        parsed_to = _parse_uuid(to_entity_id, field_name="to_entity_id")
        _validate_direction(direction)

        path = await find_entity_graph_path(
            pool,
            from_entity_id=parsed_from,
            to_entity_id=parsed_to,
            max_hops=max_hops,
            edge_types=edge_types,
            direction=direction,
        )
        if path is None:
            return {"found": False, "hops": None, "path": []}
        return {
            "found": True,
            "hops": len(path),
            "path": [_edge_dict(edge) for edge in path],
        }
