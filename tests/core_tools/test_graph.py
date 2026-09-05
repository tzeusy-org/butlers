"""Tests for the ``entity_graph_walk``/``entity_graph_path`` core MCP tools.

Covers:
- Missing pool raises rather than silently degrading — a traversal tool has
  no safe "no graph" default.
- Malformed entity UUIDs / invalid ``direction`` raise a clear ValueError the
  model can see and correct, without a DB round trip.
- Successful calls delegate to the recursive-CTE core functions and reshape
  UUID fields to JSON-safe strings.

The recursive-CTE SQL itself (multi-hop walk, direction filtering, edge-type
filtering, cycle safety, withheld-edge exclusion) is exercised against a real
Postgres in tests/integration/test_entity_graph_walk.py — this file is the
mocked-pool control-flow layer only.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from butlers.core_tools._base import ToolContext

pytestmark = pytest.mark.unit


def _register_and_grab(pool=None, butler_name="finance"):
    import butlers.core_tools._graph as mod

    registered: dict = {}

    def _core_tool(_group: str, **_kwargs):
        def decorator(fn):
            registered[fn.__name__] = fn
            return fn

        return decorator

    ctx = ToolContext(
        daemon=SimpleNamespace(),
        pool=pool,
        spawner=None,
        butler_name=butler_name,
        butler_type=None,
        is_switchboard=False,
        is_messenger=False,
        route_metrics=None,
    )
    mcp = SimpleNamespace()
    mod.register_graph_tools(ctx, mcp, _core_tool)
    return registered["entity_graph_walk"], registered["entity_graph_path"]


# ---------------------------------------------------------------------------
# entity_graph_walk
# ---------------------------------------------------------------------------


async def test_entity_graph_walk_raises_when_pool_unavailable():
    walk, _ = _register_and_grab(pool=None)

    with pytest.raises(RuntimeError, match="Database pool"):
        await walk(entity_id=str(uuid4()))


async def test_entity_graph_walk_rejects_invalid_entity_id():
    walk, _ = _register_and_grab(pool=AsyncMock())

    with pytest.raises(ValueError, match="entity_id"):
        await walk(entity_id="not-a-uuid")


async def test_entity_graph_walk_rejects_invalid_direction():
    walk, _ = _register_and_grab(pool=AsyncMock())

    with pytest.raises(ValueError, match="direction"):
        await walk(entity_id=str(uuid4()), direction="sideways")


async def test_entity_graph_walk_maps_hits_and_forwards_filters(monkeypatch):
    start_id = uuid4()
    reached_id = uuid4()
    edge_id = uuid4()
    object_id = uuid4()

    fake_walk = AsyncMock(
        return_value=[
            {
                "entity_id": reached_id,
                "hop": 2,
                "id": edge_id,
                "subject_entity_id": start_id,
                "predicate": "knows",
                "object_entity_id": object_id,
            }
        ]
    )
    monkeypatch.setattr("butlers.core_tools._graph.walk_entity_graph", fake_walk)

    walk, _ = _register_and_grab(pool=AsyncMock())

    result = await walk(
        entity_id=str(start_id),
        max_hops=3,
        edge_types=["knows"],
        direction="out",
        limit=10,
    )

    assert result == [
        {
            "entity_id": str(reached_id),
            "hop": 2,
            "via_edge": {
                "id": str(edge_id),
                "subject_entity_id": str(start_id),
                "predicate": "knows",
                "object_entity_id": str(object_id),
            },
        }
    ]
    fake_walk.assert_awaited_once()
    kwargs = fake_walk.await_args.kwargs
    assert kwargs["entity_id"] == start_id
    assert kwargs["max_hops"] == 3
    assert kwargs["edge_types"] == ["knows"]
    assert kwargs["direction"] == "out"
    assert kwargs["limit"] == 10


async def test_entity_graph_walk_no_reachable_entities_returns_empty_list(monkeypatch):
    monkeypatch.setattr("butlers.core_tools._graph.walk_entity_graph", AsyncMock(return_value=[]))

    walk, _ = _register_and_grab(pool=AsyncMock())

    assert await walk(entity_id=str(uuid4())) == []


# ---------------------------------------------------------------------------
# entity_graph_path
# ---------------------------------------------------------------------------


async def test_entity_graph_path_raises_when_pool_unavailable():
    _, path = _register_and_grab(pool=None)

    with pytest.raises(RuntimeError, match="Database pool"):
        await path(from_entity_id=str(uuid4()), to_entity_id=str(uuid4()))


async def test_entity_graph_path_rejects_invalid_from_entity_id():
    _, path = _register_and_grab(pool=AsyncMock())

    with pytest.raises(ValueError, match="from_entity_id"):
        await path(from_entity_id="not-a-uuid", to_entity_id=str(uuid4()))


async def test_entity_graph_path_rejects_invalid_to_entity_id():
    _, path = _register_and_grab(pool=AsyncMock())

    with pytest.raises(ValueError, match="to_entity_id"):
        await path(from_entity_id=str(uuid4()), to_entity_id="not-a-uuid")


async def test_entity_graph_path_rejects_invalid_direction():
    _, path = _register_and_grab(pool=AsyncMock())

    with pytest.raises(ValueError, match="direction"):
        await path(from_entity_id=str(uuid4()), to_entity_id=str(uuid4()), direction="sideways")


async def test_entity_graph_path_found_maps_edges(monkeypatch):
    from_id = uuid4()
    to_id = uuid4()
    edge_id = uuid4()

    fake_path = AsyncMock(
        return_value=[
            {
                "id": edge_id,
                "subject_entity_id": from_id,
                "predicate": "knows",
                "object_entity_id": to_id,
            }
        ]
    )
    monkeypatch.setattr("butlers.core_tools._graph.find_entity_graph_path", fake_path)

    _, path = _register_and_grab(pool=AsyncMock())

    result = await path(from_entity_id=str(from_id), to_entity_id=str(to_id), max_hops=2)

    assert result == {
        "found": True,
        "hops": 1,
        "path": [
            {
                "id": str(edge_id),
                "subject_entity_id": str(from_id),
                "predicate": "knows",
                "object_entity_id": str(to_id),
            }
        ],
    }
    fake_path.assert_awaited_once()
    kwargs = fake_path.await_args.kwargs
    assert kwargs["from_entity_id"] == from_id
    assert kwargs["to_entity_id"] == to_id
    assert kwargs["max_hops"] == 2


async def test_entity_graph_path_not_found_reports_false(monkeypatch):
    monkeypatch.setattr(
        "butlers.core_tools._graph.find_entity_graph_path", AsyncMock(return_value=None)
    )

    _, path = _register_and_grab(pool=AsyncMock())

    result = await path(from_entity_id=str(uuid4()), to_entity_id=str(uuid4()))

    assert result == {"found": False, "hops": None, "path": []}
