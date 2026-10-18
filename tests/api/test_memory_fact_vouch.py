"""API tests for the owner Vouch action on held facts (bu-jv1moa.14 S1).

Covers ``POST /api/memory/facts/{id}/vouch`` (owner only, receipt, 404/409/400),
``GET /api/memory/facts?held=``, the ``held`` flag on Fact rows, and the actor the
dashboard Confirm records.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from butlers.api.routers.memory import _get_db_manager
from butlers.modules.memory.content_authority import HELD_FACT_SQL
from butlers.modules.memory.storage import FactNotEndorsableError

pytestmark = pytest.mark.unit

_NOW = datetime.now(tz=UTC)


class _Record(dict):
    """asyncpg-Record stand-in supporting ``r[key]`` and ``r.get(key)``."""


def _fact_row(fact_id: uuid.UUID, **overrides) -> _Record:
    row = {
        "id": fact_id,
        "subject": "owner",
        "predicate": "allergic_to",
        "content": "Peanuts",
        "importance": 5.0,
        "confidence": 0.9,
        "decay_rate": 0.008,
        "permanence": "standard",
        "source_butler": "atlas",
        "source_episode_id": None,
        "session_id": None,
        "supersedes_id": None,
        "entity_id": None,
        "object_entity_id": None,
        "validity": "active",
        "scope": "global",
        "reference_count": 0,
        "created_at": _NOW,
        "last_referenced_at": None,
        "last_confirmed_at": None,
        "tags": None,
        "metadata": None,
        "content_authority": "third_party",
        "authority_entity_id": None,
        "endorsed_at": None,
        "endorsed_by": None,
    }
    row.update(overrides)
    return _Record(row)


class _FactPool:
    """Pool holding one fact row; records the list/confirm statements it sees."""

    def __init__(self, row: _Record | None) -> None:
        self._row = row
        self.fetchval_calls: list[tuple] = []
        self.fetch_calls: list[tuple] = []
        self.execute_calls: list[tuple] = []

    async def fetchval(self, query: str, *args: object):
        self.fetchval_calls.append((query, args))
        return 1 if self._row is not None else 0

    async def fetch(self, query: str, *args: object):
        self.fetch_calls.append((query, args))
        if "FROM facts" in query and self._row is not None:
            return [self._row]
        return []

    async def fetchrow(self, query: str, *args: object):
        if (
            self._row is not None
            and "FROM facts WHERE id" in query
            and str(args[0]) == str(self._row["id"])
        ):
            return self._row
        return None

    async def execute(self, query: str, *args: object) -> str:
        self.execute_calls.append((query, args))
        return "UPDATE 1" if self._row is not None else "UPDATE 0"


class _DB:
    def __init__(self, pools: dict[str, _FactPool]) -> None:
        self._pools = pools
        self.butler_names = list(pools)

    def pool(self, name: str) -> _FactPool:
        if name not in self._pools:
            raise KeyError(name)
        return self._pools[name]


def _wire(app, row: _Record | None) -> _FactPool:
    pool = _FactPool(row)
    db = _DB({"atlas": pool, "memory": _FactPool(None)})
    app.dependency_overrides[_get_db_manager] = lambda: db
    return pool


async def _post_vouch(app, fact_id, *, headers=None, **endorse_kwargs):
    mock = AsyncMock(**endorse_kwargs)
    with (
        patch("butlers.api.routers.memory.fetch_owner_entity_id", AsyncMock(return_value=None)),
        patch("butlers.api.routers.memory.endorse_fact", mock),
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(f"/api/memory/facts/{fact_id}/vouch", headers=headers)
    return resp, mock


async def test_vouch_returns_receipt(app):
    fact_id = uuid.uuid4()
    owner = uuid.uuid4()
    _wire(app, None)
    resp, mock = await _post_vouch(
        app,
        fact_id,
        return_value={
            "fact_id": fact_id,
            "content_authority": "third_party",
            "endorsed_at": _NOW,
            "endorsed_by": owner,
            "changed": True,
        },
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert (data["fact_id"], data["content_authority"], data["endorsed_by"], data["changed"]) == (
        str(fact_id),
        "third_party",
        str(owner),
        True,
    )
    assert mock.await_args.args[1] == fact_id


async def test_vouch_missing_fact_is_404_and_retracted_fact_is_409(app):
    fact_id = uuid.uuid4()
    _wire(app, None)
    missing, _ = await _post_vouch(app, fact_id, return_value=None)
    assert missing.status_code == 404

    retracted, _ = await _post_vouch(
        app, fact_id, side_effect=FactNotEndorsableError("retracted facts cannot be vouched for")
    )
    assert retracted.status_code == 409
    assert "retracted" in retracted.json()["detail"]


async def test_vouch_rejects_a_malformed_id(app):
    _wire(app, None)
    resp, mock = await _post_vouch(app, "not-a-uuid", return_value=None)
    assert resp.status_code == 400
    mock.assert_not_awaited()


async def test_vouch_refuses_a_caller_without_owner_authority(app):
    """A request that fails owner authentication never reaches the storage call."""
    _wire(app, None)
    resp, mock = await _post_vouch(
        app, uuid.uuid4(), headers={"X-API-Key": "not-the-owner-key"}, return_value=None
    )
    assert resp.status_code == 401
    mock.assert_not_awaited()


async def test_vouch_route_declares_the_owner_dependency():
    """The route itself depends on the owner gate, not only the app middleware."""
    from butlers.api.owner_control import require_dashboard_owner_control
    from butlers.api.routers.memory import router

    route = next(r for r in router.routes if getattr(r, "path", "").endswith("/vouch"))
    deps = [d.call for d in route.dependant.dependencies]
    assert require_dashboard_owner_control in deps
    assert route.methods == {"POST"}


async def test_list_facts_held_true_filters_on_the_held_predicate(app):
    fact_id = uuid.uuid4()
    pool = _wire(app, _fact_row(fact_id))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/memory/facts?held=true")
    assert resp.status_code == 200
    (fact,) = resp.json()["data"]
    assert fact["held"] is True
    assert fact["content_authority"] == "third_party"
    assert fact["endorsed_at"] is None
    assert HELD_FACT_SQL in pool.fetchval_calls[0][0]
    assert HELD_FACT_SQL in pool.fetch_calls[0][0]


async def test_list_facts_held_false_negates_the_predicate(app):
    pool = _wire(app, _fact_row(uuid.uuid4(), content_authority="owner"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/memory/facts?held=false")
    assert resp.status_code == 200
    assert resp.json()["data"][0]["held"] is False
    assert f"NOT COALESCE({HELD_FACT_SQL}, false)" in pool.fetch_calls[0][0]


async def test_list_facts_without_held_adds_no_authority_condition(app):
    pool = _wire(app, _fact_row(uuid.uuid4()))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/memory/facts")
    assert resp.status_code == 200
    assert "content_authority IN" not in pool.fetchval_calls[0][0]


@pytest.mark.parametrize(
    ("overrides", "held"),
    [
        ({}, True),
        ({"content_authority": "mixed"}, True),
        ({"validity": "fading"}, True),
        ({"endorsed_at": _NOW, "endorsed_by": uuid.uuid4()}, False),
        ({"content_authority": "owner"}, False),
        ({"content_authority": "owner_device"}, False),
        ({"content_authority": "system"}, False),
        ({"content_authority": None}, False),
        ({"validity": "retracted"}, False),
        ({"validity": "superseded"}, False),
    ],
)
async def test_fact_held_flag_follows_authority_vouch_and_validity(app, overrides, held):
    fact_id = uuid.uuid4()
    _wire(app, _fact_row(fact_id, **overrides))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/memory/facts/{fact_id}")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["held"] is held
    # Vouching never rewrites authority: the stored value is reported unchanged.
    assert data["content_authority"] == overrides.get("content_authority", "third_party")


async def test_dashboard_confirm_records_the_owner_as_actor(app):
    fact_id = uuid.uuid4()
    pool = _wire(app, _fact_row(fact_id))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(f"/api/memory/facts/{fact_id}/confirm")
    assert resp.status_code == 200
    query, args = pool.execute_calls[0]
    assert "memory_events" in query
    assert "owner" in args
