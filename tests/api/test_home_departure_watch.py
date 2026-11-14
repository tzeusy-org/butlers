"""Owner-only dashboard editing of home.departure_watch (bu-jv1moa.8 PR-B)."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import HTTPException

from butlers.api.db import DatabaseManager
from butlers.api.deps import get_db
from butlers.api.owner_control import require_dashboard_owner_control

pytestmark = pytest.mark.unit

_ROW = {
    "entity_id": "binary_sensor.front_door",
    "owner_label": "Front door",
    "secure_states": ["off"],
    "updated_at": datetime(2026, 11, 14, 8, 0, tzinfo=UTC),
}


def _client(app, pool: AsyncMock) -> httpx.AsyncClient:
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = pool
    app.dependency_overrides[get_db] = lambda: mock_db
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_watched_items_are_listed_and_upserted_with_the_owner_label(app) -> None:
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[_ROW])
    pool.fetchrow = AsyncMock(return_value=_ROW)
    pool.execute = AsyncMock(return_value=None)
    async with _client(app, pool) as client:
        listed = await client.get("/api/home/departure-watch")
        saved = await client.put(
            "/api/home/departure-watch",
            json={
                "entity_id": "binary_sensor.front_door",
                "owner_label": "  Front door  ",
                "secure_states": ["off", "off"],
            },
        )

    assert listed.status_code == 200
    assert listed.json()[0] == {**_ROW, "updated_at": "2026-11-14T08:00:00+00:00"}
    assert saved.status_code == 200
    assert pool.fetchrow.await_args.args[1:] == ("binary_sensor.front_door", "Front door", ["off"])


@pytest.mark.parametrize(
    "body",
    [
        {"entity_id": "Front Door", "owner_label": "Door", "secure_states": ["off"]},
        {"entity_id": "lock.back", "owner_label": "   ", "secure_states": ["locked"]},
        {"entity_id": "lock.back", "owner_label": "x" * 81, "secure_states": ["locked"]},
        {"entity_id": "lock.back", "owner_label": "Back door", "secure_states": []},
        {"entity_id": "lock.back", "owner_label": "Back door", "secure_states": ["Locked!"]},
    ],
    ids=["bad-entity-id", "blank-label", "label-too-long", "no-secure-state", "bad-state"],
)
async def test_invalid_declarations_are_refused(app, body) -> None:
    pool = AsyncMock()
    async with _client(app, pool) as client:
        resp = await client.put("/api/home/departure-watch", json=body)

    assert resp.status_code == 422
    pool.fetchrow.assert_not_awaited()


async def test_a_watch_is_deleted_and_a_missing_one_is_a_404(app) -> None:
    pool = AsyncMock()
    pool.execute = AsyncMock(side_effect=["DELETE 1", "DELETE 0"])
    async with _client(app, pool) as client:
        gone = await client.delete("/api/home/departure-watch/binary_sensor.front_door")
        missing = await client.delete("/api/home/departure-watch/binary_sensor.front_door")

    assert (gone.status_code, missing.status_code) == (204, 404)


async def test_every_departure_watch_route_requires_the_owner(app) -> None:
    for butler_name, router_module in app.state.butler_routers:
        if butler_name != "home":
            continue
        routes = [r for r in router_module.router.routes if "departure-watch" in r.path]
        assert len(routes) == 3
        for route in routes:
            assert require_dashboard_owner_control in [d.call for d in route.dependant.dependencies]

    with pytest.raises(HTTPException) as denied:
        require_dashboard_owner_control(SimpleNamespace(state=SimpleNamespace()))
    assert denied.value.status_code == 401
