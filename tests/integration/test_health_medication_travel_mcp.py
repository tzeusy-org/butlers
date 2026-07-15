"""In-process proof of Travel -> Switchboard -> Health medication MCP routing."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastmcp import Client, FastMCP

from butlers.modules._roster_health import HealthModule
from butlers.modules._roster_travel import TravelModule

pytestmark = pytest.mark.integration


class _HealthPool:
    def __init__(self) -> None:
        self.queries: list[str] = []

    async def fetch(self, query: str, *_args: object) -> list[dict]:
        self.queries.append(query)
        return [
            {
                "id": uuid4(),
                "predicate": "medication",
                "content": "Metformin 500 mg twice daily",
                "valid_at": None,
                "created_at": None,
                "metadata": {
                    "name": "Metformin",
                    "dosage": "500 mg",
                    "frequency": "twice daily",
                    "schedule": ["08:00", "20:00"],
                    "active": True,
                    "notes": "private note that must stay in Health",
                },
            }
        ]


class _TravelPool:
    def __init__(self) -> None:
        self.queries: list[str] = []

    async def fetchrow(self, query: str, *_args: object) -> None:
        self.queries.append(query)
        if "health." in query.lower():
            raise AssertionError("Travel attempted a direct Health-schema query")
        return None


async def test_travel_obtains_minimum_medication_snapshot_through_health_mcp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from butlers.modules._roster_switchboard.tools import register_tools
    from butlers.tools.switchboard import routing as routing_package

    health_pool = _HealthPool()
    health_module = HealthModule()
    health_mcp = FastMCP("health")
    await health_module.register_tools(
        health_mcp,
        {},
        SimpleNamespace(pool=health_pool),
        butler_name="health",
    )

    travel_pool = _TravelPool()
    travel_module = TravelModule()
    travel_mcp = FastMCP("travel")
    await travel_module.register_tools(
        travel_mcp,
        {},
        SimpleNamespace(pool=travel_pool),
        butler_name="travel",
    )

    health_tool = await health_mcp.get_tool("medication_travel_snapshot")
    travel_tool = await travel_mcp.get_tool("health_medication_snapshot")
    assert health_tool is not None
    assert travel_tool is not None
    assert health_tool.parameters["additionalProperties"] is False
    assert set(health_tool.parameters["properties"]) == {"trace_context"}
    assert travel_tool.parameters == {
        "additionalProperties": False,
        "properties": {},
        "type": "object",
    }

    switchboard_calls: list[tuple[str, str, dict, str]] = []

    async def in_process_route(
        _pool: object,
        target_butler: str,
        tool_name: str,
        args: dict,
        *,
        source_butler: str,
        **_kwargs: object,
    ) -> dict:
        switchboard_calls.append((target_butler, tool_name, args, source_butler))
        async with Client(health_mcp) as client:
            health_result = await client.call_tool(tool_name, args, raise_on_error=True)
        return {"result": health_result}

    monkeypatch.setattr(routing_package, "route", in_process_route)
    switchboard_mcp = FastMCP("switchboard")
    register_tools(
        switchboard_mcp,
        SimpleNamespace(_get_pool=lambda: object()),
        SimpleNamespace(groups=["routing"]),
    )

    async with Client(switchboard_mcp) as switchboard_client:
        travel_module.wire_runtime(None, "/repo", switchboard_client=switchboard_client)
        async with Client(travel_mcp) as client:
            result = await client.call_tool("health_medication_snapshot", {}, raise_on_error=True)

    assert result.data == {
        "schema_version": "health.medication-travel.v1",
        "status": "ok",
        "medications": [
            {
                "name": "Metformin",
                "dosage": "500 mg",
                "frequency": "twice daily",
                "schedule": ["08:00", "20:00"],
            }
        ],
        "error": None,
    }
    assert switchboard_calls == [("health", "medication_travel_snapshot", {}, "travel")]
    assert health_pool.queries and "FROM facts" in health_pool.queries[0]
    assert travel_pool.queries
    assert all("health." not in query.lower() for query in travel_pool.queries)
    assert "private note" not in str(result.data)
