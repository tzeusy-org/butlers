"""Owner-only dashboard editing of home.critical_entities.

bu-jv1moa.122, REQ-module-home-assistant-001 (RFC 0028 Amendment 1).  Unit cases drive the
production routes over a fake connection; the database round trip runs in CI with Docker.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api import dashboard_audit_middleware
from butlers.api.db import DatabaseManager
from butlers.api.deps import get_db_optional
from butlers.api.owner_control import require_dashboard_owner_control

pytestmark = pytest.mark.unit

_LABEL = "Freezer plug sentinel-4471"
_ROW = {
    "entity_id": "switch.freezer",
    "owner_label": _LABEL,
    "added_by": "owner",
    "added_at": datetime(2026, 11, 21, 8, 0, tzinfo=UTC),
    "inserted": True,
}
_BODY = {"entity_id": "switch.freezer", "owner_label": _LABEL}


def _router(app):
    return next(m for name, m in app.state.butler_routers if name == "home")


class _Seams:
    def __init__(self) -> None:
        self.audits: list[dict] = []
        self.refusals: list[dict] = []
        self.connection = AsyncMock()
        self.connection.fetchrow = AsyncMock(return_value=_ROW)
        self.pool = MagicMock()
        self.pool.fetch = AsyncMock(return_value=[_ROW])

        @asynccontextmanager
        async def acquire():
            yield self.connection

        self.pool.acquire = acquire


def _client(app, monkeypatch, seams: _Seams) -> httpx.AsyncClient:
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = seams.pool
    app.dependency_overrides[get_db_optional] = lambda: mock_db
    module = _router(app)

    @asynccontextmanager
    async def no_transaction(connection):  # noqa: ANN001
        yield

    async def append(connection, actor, action, **kwargs):  # noqa: ANN001
        seams.audits.append({"actor": actor, "action": action, **kwargs})
        return 1

    async def refusal(db, **kwargs):  # noqa: ANN001
        seams.refusals.append(kwargs)

    monkeypatch.setattr(module, "audit_transaction", no_transaction)
    monkeypatch.setattr(module, "append_audit", append)
    monkeypatch.setattr(module, "emit_dashboard_audit", refusal)
    monkeypatch.setattr(dashboard_audit_middleware, "emit_dashboard_audit", AsyncMock())
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_an_entity_is_marked_critical_listed_and_audited_without_its_label(
    app, monkeypatch
) -> None:
    seams = _Seams()
    async with _client(app, monkeypatch, seams) as client:
        saved = await client.put("/api/home/critical-entities", json=_BODY)
        listed = await client.get("/api/home/critical-entities")

    assert saved.status_code == 200
    assert saved.json()["entity_id"] == "switch.freezer"
    assert listed.json()[0]["owner_label"] == _LABEL
    assert seams.connection.fetchrow.await_args.args[1:] == ("switch.freezer", _LABEL, "owner")
    assert len(seams.audits) == 1
    audit = seams.audits[0]
    assert (audit["actor"], audit["action"], audit["target"]) == (
        "owner",
        "home_critical_entity_add",
        "switch.freezer",
    )
    assert audit["metadata"] == {
        "operation": "add",
        "entity_id": "switch.freezer",
        "outcome": "applied",
    }
    assert "sentinel-4471" not in repr(seams.audits)


async def test_relabelling_is_audited_as_its_own_operation(app, monkeypatch) -> None:
    seams = _Seams()
    seams.connection.fetchrow.return_value = {**_ROW, "inserted": False}
    async with _client(app, monkeypatch, seams) as client:
        await client.put("/api/home/critical-entities", json=_BODY)

    assert seams.audits[0]["action"] == "home_critical_entity_relabel"


async def test_a_removal_writes_one_audit_row_and_a_missing_entity_is_a_404(
    app, monkeypatch
) -> None:
    seams = _Seams()
    seams.connection.fetchrow = AsyncMock(side_effect=[{"entity_id": "switch.freezer"}, None])
    async with _client(app, monkeypatch, seams) as client:
        gone = await client.delete("/api/home/critical-entities/switch.freezer")
        missing = await client.delete("/api/home/critical-entities/switch.freezer")
        malformed = await client.delete("/api/home/critical-entities/Switch.Freezer")

    assert (gone.status_code, missing.status_code, malformed.status_code) == (204, 404, 404)
    assert [a["action"] for a in seams.audits] == ["home_critical_entity_remove"]
    assert seams.audits[0]["target"] == "switch.freezer"
    assert [r["body"] for r in seams.refusals] == [{"outcome": "not_found"}] * 2


@pytest.mark.parametrize(
    "body",
    [
        {**_BODY, "entity_id": "freezer"},
        {**_BODY, "entity_id": "switch.a,switch.b"},
        {**_BODY, "entity_id": "all"},
        {**_BODY, "entity_id": "Switch.Freezer"},
        {**_BODY, "owner_label": "   "},
        {**_BODY, "owner_label": "x" * 81},
        {**_BODY, "area_id": "kitchen"},
        {**_BODY, "added_by": "someone-else"},
        {"entity_id": "switch.freezer"},
    ],
    ids=[
        "no-domain",
        "list",
        "all",
        "uppercase",
        "blank-label",
        "long-label",
        "area",
        "caller-actor",
        "no-label",
    ],
)
async def test_refused_writes_store_nothing_and_answer_one_content_blind_code(
    app, monkeypatch, body
) -> None:
    seams = _Seams()
    async with _client(app, monkeypatch, seams) as client:
        resp = await client.put("/api/home/critical-entities", json=body)

    assert resp.status_code == 422
    assert "INVALID_REQUEST" in resp.text
    for value in ("freezer", "kitchen", "someone-else", "xxxx", "sentinel"):
        assert value not in resp.text
    seams.connection.fetchrow.assert_not_awaited()
    assert seams.audits == []
    assert seams.refusals[-1]["body"] == {"outcome": "invalid_request"}


async def test_an_oversized_or_query_carrying_request_is_refused_before_storage(
    app, monkeypatch
) -> None:
    seams = _Seams()
    async with _client(app, monkeypatch, seams) as client:
        big = await client.put(
            "/api/home/critical-entities", json={**_BODY, "owner_label": "x" * 2_000}
        )
        query = await client.put("/api/home/critical-entities?entity_id=all", json=_BODY)

    assert big.status_code == 413
    assert query.status_code == 422
    seams.connection.fetchrow.assert_not_awaited()


async def test_an_unavailable_store_is_one_fixed_503(app, monkeypatch) -> None:
    import asyncpg

    seams = _Seams()
    seams.pool.fetch = AsyncMock(side_effect=asyncpg.InterfaceError("down"))
    seams.connection.fetchrow = AsyncMock(side_effect=asyncpg.InterfaceError("down"))
    async with _client(app, monkeypatch, seams) as client:
        listed = await client.get("/api/home/critical-entities")
        saved = await client.put("/api/home/critical-entities", json=_BODY)

    assert (listed.status_code, saved.status_code) == (503, 503)
    assert "CRITICAL_ENTITIES_UNAVAILABLE" in saved.text
    assert seams.audits == []


async def test_every_critical_entity_route_requires_the_owner(app) -> None:
    seen = 0
    for butler_name, router_module in app.state.butler_routers:
        if butler_name != "home":
            continue
        routes = [r for r in router_module.router.routes if "critical-entities" in r.path]
        seen = len(routes)
        for route in routes:
            assert require_dashboard_owner_control in [d.call for d in route.dependant.dependencies]
    assert seen == 3


def test_the_generic_audit_middleware_never_reads_the_registry_subtree() -> None:
    assert dashboard_audit_middleware._in_private_subtree("/api/home/critical-entities")
    assert dashboard_audit_middleware._in_private_subtree("/api/home/critical-entities/switch.x")
