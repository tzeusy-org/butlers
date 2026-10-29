"""Tests for GET /api/entities/{id}/dossier (bu-8cdl1.8 S5; REQ-entity-graph-010).

The Switchboard pool is faked by SQL shape: the router asks for the entity row, then the dossier
read asks the edges table for live receipts, per-source live counts and the withheld count.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from butlers.api.deps import get_db

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
_ENTITY = uuid.uuid4()
_FRIEND = uuid.uuid4()
_BOSS = uuid.uuid4()


class _Row(dict):
    """Stands in for an asyncpg Record (``row["x"]`` and ``.get``)."""


def _edge(*, subject, obj, predicate, schema="relationship", table="entity_facts") -> _Row:
    return _Row(
        id=uuid.uuid4(),
        source_schema=schema,
        source_table=table,
        source_id=uuid.uuid4(),
        subject_entity_id=subject,
        predicate=predicate,
        object_entity_id=obj,
        sensitivity="normal",
        updated_at=_NOW,
    )


class _Pool:
    def __init__(
        self,
        *,
        entity_exists: bool = True,
        edges: list[_Row] | None = None,
        withheld: int = 0,
        fail: Exception | None = None,
    ) -> None:
        self.entity_exists = entity_exists
        self.edges = edges or []
        self.withheld = withheld
        self.fail = fail
        self.queries: list[str] = []

    def _check(self, query: str) -> None:
        self.queries.append(query)
        if self.fail is not None:
            raise self.fail

    async def fetchrow(self, query: str, *args: Any):
        self._check(query)
        return _Row(id=_ENTITY) if self.entity_exists else None

    async def fetchval(self, query: str, *args: Any):
        self._check(query)
        return self.withheld

    async def fetch(self, query: str, *args: Any):
        self._check(query)
        if "GROUP BY source_schema" in query:
            groups: dict[tuple[str, str], int] = {}
            for edge in self.edges:
                key = (edge["source_schema"], edge["source_table"])
                groups[key] = groups.get(key, 0) + 1
            return [_Row(source_schema=k[0], source_table=k[1], live=v) for k, v in groups.items()]
        if "public.entities" in query:
            return [
                _Row(id=_FRIEND, canonical_name="Alex Friend"),
                _Row(id=_BOSS, canonical_name="Sam Boss"),
            ]
        return self.edges[: args[1]] if "LIMIT $2" in query else self.edges


class _DB:
    butler_names = ["switchboard"]

    def __init__(self, pool: _Pool, *, missing_switchboard: bool = False) -> None:
        self._pool = pool
        self._missing = missing_switchboard

    def pool(self, name: str):
        if self._missing or name != "switchboard":
            raise KeyError(name)
        return self._pool


async def _get(app, pool: _Pool, entity: uuid.UUID = _ENTITY, **kwargs: Any) -> httpx.Response:
    db = _DB(pool, **kwargs)
    app.dependency_overrides[get_db] = lambda: db
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get(f"/api/entities/{entity}/dossier")


# REQ-entity-graph-010: handler, receipts and counts over a faked pool; migrated-schema SQL is
# covered by tests/integration/test_entity_graph_dossier.py.
async def test_dossier_groups_live_receipts_by_source_with_provenance(app):
    pool = _Pool(
        edges=[
            _edge(subject=_ENTITY, obj=_FRIEND, predicate="friend_of"),
            _edge(subject=_BOSS, obj=_ENTITY, predicate="manages", schema="general", table="facts"),
        ],
        withheld=2,
    )

    resp = await _get(app, pool)

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["relationships_known"] == 2
    assert data["relationships_withheld"] == 2
    by_source = {(s["source_schema"], s["source_table"]): s for s in data["sources"]}
    assert set(by_source) == {("relationship", "entity_facts"), ("general", "facts")}
    friend = by_source[("relationship", "entity_facts")]["receipts"][0]
    assert friend["predicate"] == "friend_of"
    assert friend["direction"] == "subject"
    assert friend["counterpart_entity_id"] == str(_FRIEND)
    assert friend["counterpart_name"] == "Alex Friend"
    assert friend["source_id"]
    boss = by_source[("general", "facts")]["receipts"][0]
    assert boss["direction"] == "object"
    assert boss["counterpart_entity_id"] == str(_BOSS)


async def test_withheld_edges_contribute_a_count_and_nothing_else(app):
    pool = _Pool(edges=[_edge(subject=_ENTITY, obj=_FRIEND, predicate="friend_of")], withheld=3)

    resp = await _get(app, pool)

    body = resp.json()["data"]
    assert body["relationships_withheld"] == 3
    # The withheld count is a bare integer: no per-row list, no payload keys anywhere.
    assert "withheld" not in {key for source in body["sources"] for key in source}
    assert all(
        "withheld" not in receipt and receipt["predicate"] == "friend_of"
        for source in body["sources"]
        for receipt in source["receipts"]
    )
    # The dossier never asks the edges table for withheld payload columns.
    withheld_queries = [q for q in pool.queries if "withheld_reason IS NOT NULL" in q]
    assert withheld_queries and all("predicate" not in q for q in withheld_queries)


async def test_an_existing_entity_with_no_edges_is_a_successful_empty_dossier(app):
    resp = await _get(app, _Pool())

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["sources"] == []
    assert data["relationships_known"] == 0
    assert data["relationships_withheld"] == 0


async def test_an_unknown_entity_is_a_404(app):
    resp = await _get(app, _Pool(entity_exists=False))

    assert resp.status_code == 404


async def test_a_graph_read_failure_is_unavailable_never_an_empty_dossier(app):
    resp = await _get(app, _Pool(fail=RuntimeError("pool closed")))

    assert resp.status_code == 503
    assert "data" not in resp.json()


async def test_a_missing_switchboard_pool_is_unavailable(app):
    resp = await _get(app, _Pool(), missing_switchboard=True)

    assert resp.status_code == 503


async def test_the_receipt_limit_is_reported_and_counts_stay_true(app):
    edges = [_edge(subject=_ENTITY, obj=_FRIEND, predicate=f"p{i}") for i in range(3)]
    pool = _Pool(edges=edges)
    db = _DB(pool)
    app.dependency_overrides[get_db] = lambda: db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/entities/{_ENTITY}/dossier", params={"limit": 2})

    data = resp.json()["data"]
    assert data["relationships_known"] == 3  # the true count, not the page
    assert data["receipts_truncated"] is True
    assert data["receipts_limit"] == 2


async def test_the_router_reads_only_public_tables(app):
    pool = _Pool(edges=[_edge(subject=_ENTITY, obj=_FRIEND, predicate="friend_of")])

    await _get(app, pool)

    for query in pool.queries:
        assert "public." in query
        # No butler-schema fan-out: only the shared projection and entity tables are queried.
        for private in ("facts ", "entity_facts ", "commitments "):
            assert f"FROM {private}" not in query
