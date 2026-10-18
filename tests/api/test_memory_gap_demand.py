"""GET /api/memory/gaps/demand merges pools, orders by asks and names a failing pool."""

from __future__ import annotations

import httpx
import pytest

from butlers.api.routers.memory import _get_db_manager

pytestmark = pytest.mark.unit


class _GapDB:
    butler_names = ["atlas", "memory", "broken"]

    def pool(self, name: str):
        return name


def _wire_demand(app, monkeypatch, per_pool: dict[str, list[dict] | Exception]):
    async def fake_demand(pool, *, memory_schema):
        outcome = per_pool[pool]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr("butlers.modules.memory.knowledge_gaps.demand_by_predicate", fake_demand)
    app.dependency_overrides[_get_db_manager] = lambda: _GapDB()


async def _get_demand(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get("/api/memory/gaps/demand")


async def test_demand_sums_a_predicate_across_pools_and_orders_by_asks(app, monkeypatch):
    _wire_demand(
        app,
        monkeypatch,
        {
            "atlas": [
                {"predicate": "lives_in", "open_gaps": 1, "asks": 1},
                {"predicate": "child_name", "open_gaps": 2, "asks": 3},
            ],
            "memory": [
                {"predicate": "lives_in", "open_gaps": 1, "asks": 4},
                {"predicate": "birthday", "open_gaps": 1, "asks": 1},
            ],
            "broken": [],
        },
    )

    resp = await _get_demand(app)

    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] == [
        {"predicate": "lives_in", "open_gaps": 2, "asks": 5},
        {"predicate": "child_name", "open_gaps": 2, "asks": 3},
        {"predicate": "birthday", "open_gaps": 1, "asks": 1},
    ]
    assert "sources_degraded" not in body["meta"]


async def test_demand_names_a_failing_pool_instead_of_reading_as_zero(app, monkeypatch):
    _wire_demand(
        app,
        monkeypatch,
        {
            "atlas": [{"predicate": "lives_in", "open_gaps": 1, "asks": 2}],
            "memory": [],
            "broken": RuntimeError("connection reset"),
        },
    )

    resp = await _get_demand(app)

    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] == [{"predicate": "lives_in", "open_gaps": 1, "asks": 2}]
    assert body["meta"]["sources_degraded"] == ["broken"]


async def test_demand_with_nothing_open_is_an_empty_list_not_an_error(app, monkeypatch):
    _wire_demand(app, monkeypatch, {"atlas": [], "memory": [], "broken": []})

    resp = await _get_demand(app)

    assert resp.status_code == 200
    assert resp.json()["data"] == []
