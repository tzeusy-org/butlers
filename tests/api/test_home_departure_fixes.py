"""Owner-only dashboard editing of home.departure_fixes (bu-jv1moa.115, REQ-008)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api import dashboard_audit_middleware
from butlers.api.db import DatabaseManager
from butlers.api.deps import get_db_optional
from butlers.api.owner_control import require_dashboard_owner_control

pytestmark = pytest.mark.unit

_ROW = {
    "entity_id": "light.garage",
    "domain": "light",
    "service": "turn_off",
    "target_entity_id": "light.garage",
    "data": {},
    "updated_at": datetime(2026, 11, 14, 8, 0, tzinfo=UTC),
}
_BODY = {
    "entity_id": "light.garage",
    "domain": "light",
    "service": "turn_off",
    "target_entity_id": "light.garage",
    "data": {},
}


def _router(app):
    return next(m for name, m in app.state.butler_routers if name == "home")


def _client(app, pool: AsyncMock, monkeypatch, audits: list | None = None) -> httpx.AsyncClient:
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = pool
    app.dependency_overrides[get_db_optional] = lambda: mock_db

    async def audit(db, **kwargs):  # noqa: ANN001
        if audits is not None:
            audits.append(kwargs)

    monkeypatch.setattr(_router(app), "emit_dashboard_audit", audit)
    monkeypatch.setattr(dashboard_audit_middleware, "emit_dashboard_audit", AsyncMock())
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _pool(*, watched: bool = True) -> AsyncMock:
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[_ROW])
    pool.fetchrow = AsyncMock(return_value=_ROW)
    pool.fetchval = AsyncMock(return_value=1 if watched else None)
    pool.execute = AsyncMock(return_value="DELETE 1")
    return pool


async def test_a_fix_is_declared_and_listed_with_its_risk_class(app, monkeypatch) -> None:
    pool = _pool()
    async with _client(app, pool, monkeypatch) as client:
        saved = await client.put("/api/home/departure-fixes", json=_BODY)
        listed = await client.get("/api/home/departure-fixes")

    assert saved.status_code == 200
    assert saved.json()["risk"] == "reversible"
    assert listed.json()[0]["target_entity_id"] == "light.garage"
    assert pool.fetchrow.await_args.args[1:] == (
        "light.garage",
        "light",
        "turn_off",
        "light.garage",
        {},
    )


@pytest.mark.parametrize(
    ("body", "watched", "code"),
    [
        (_BODY, False, "NOT_WATCHED"),
        ({**_BODY, "target_entity_id": "light.a,light.b"}, True, "INVALID_REQUEST"),
        ({**_BODY, "service": "toggle"}, True, "UNCLASSIFIED_CALL"),
        ({**_BODY, "data": {"entity_id": "all"}}, True, "DATA_NOT_ALLOWED"),
        ({**_BODY, "data": {"brightness": 5}}, True, "DATA_NOT_ALLOWED"),
        ({**_BODY, "target_entity_id": "switch.garage"}, True, "NOT_EXPLICIT_ENTITY"),
        ({**_BODY, "area_id": "kitchen"}, True, "INVALID_REQUEST"),
    ],
    ids=["unwatched", "list-target", "unclassified", "selector", "foreign-key", "domain", "area"],
)
async def test_refused_declarations_store_nothing_and_are_content_blind(
    app, monkeypatch, body, watched, code
):
    pool = _pool(watched=watched)
    audits: list = []
    async with _client(app, pool, monkeypatch, audits) as client:
        resp = await client.put("/api/home/departure-fixes", json=body)

    assert resp.status_code == 422
    assert code in resp.text
    pool.fetchrow.assert_not_awaited()
    for value in ("light.a", "kitchen", "brightness"):
        assert value not in resp.text
    assert audits[-1]["body"] == {"outcome": code.lower()}


async def test_a_fix_is_deleted_and_a_missing_one_is_a_404(app, monkeypatch) -> None:
    pool = _pool()
    pool.execute = AsyncMock(side_effect=["DELETE 1", "DELETE 0"])
    async with _client(app, pool, monkeypatch) as client:
        gone = await client.delete("/api/home/departure-fixes/light.garage")
        missing = await client.delete("/api/home/departure-fixes/light.garage")

    assert (gone.status_code, missing.status_code) == (204, 404)


async def test_every_departure_fix_route_requires_the_owner(app) -> None:
    seen = 0
    for butler_name, router_module in app.state.butler_routers:
        if butler_name != "home":
            continue
        routes = [r for r in router_module.router.routes if "departure-fixes" in r.path]
        seen = len(routes)
        for route in routes:
            assert require_dashboard_owner_control in [d.call for d in route.dependant.dependencies]
    assert seen == 3
