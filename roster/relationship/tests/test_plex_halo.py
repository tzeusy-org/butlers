"""Tests for GET /api/relationship/plex/halo (dimension halo on the owner Plex).

Covers:
- Owner-only authz gate (Clause 12b): 403 when no owner entity is registered.
- Empty graph: 200 with empty arcs/totals.
- Arc grouping by entity_type with per-type totals.
- Person-edge attachment in both triple directions, deduped, non-person
  counterparties dropped.

Each test hits the FastAPI router via httpx.AsyncClient with a mocked DB pool
so no real Postgres or Docker is required.  Marked ``unit`` to skip the
Docker-availability guard applied to roster/ integration tests.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager

pytestmark = pytest.mark.unit

_OWNER_ENTITY_ID = uuid4()
_ORG_ID = uuid4()
_PLACE_ID = uuid4()
_PERSON_ID = uuid4()
_PERSON_2_ID = uuid4()


def _row(data: dict) -> MagicMock:
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda key: data[key])
    return row


def _owner_row() -> MagicMock:
    return _row({"id": _OWNER_ENTITY_ID, "roles": ["owner"]})


def _total_row(entity_type: str, n: int) -> MagicMock:
    return _row({"entity_type": entity_type, "n": n})


def _sat_row(sat_id: UUID, name: str, entity_type: str, last_seen=None) -> MagicMock:
    return _row(
        {
            "id": sat_id,
            "canonical_name": name,
            "entity_type": entity_type,
            "last_seen": last_seen,
        }
    )


def _edge_row(subject: UUID, obj: str, predicate: str) -> MagicMock:
    return _row({"subject": subject, "object": obj, "predicate": predicate})


def _person_row(person_id: UUID) -> MagicMock:
    return _row({"id": person_id})


def _app_with_pool(
    *,
    owner_exists: bool = True,
    totals_rows: list | None = None,
    sat_rows: list | None = None,
    edge_rows: list | None = None,
    person_rows: list | None = None,
) -> tuple[FastAPI, AsyncMock]:
    """Wire a FastAPI app with a mock DB pool for the halo endpoint.

    pool.fetch call sequence inside the handler:
      1. totals per entity_type
      2. top-N satellites per entity_type
      3. relational edges touching the satellites (skipped when no satellites)
      4. person filter on edge counterparties (skipped when no candidates)
    """
    mock_pool = AsyncMock()
    mock_pool.fetchrow = AsyncMock(return_value=_owner_row() if owner_exists else None)
    mock_pool.fetch = AsyncMock(
        side_effect=[
            totals_rows or [],
            sat_rows or [],
            edge_rows or [],
            person_rows or [],
        ]
    )

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool

    app = create_app()
    for butler_name, router_module in app.state.butler_routers:
        if butler_name == "relationship" and hasattr(router_module, "_get_db_manager"):
            app.dependency_overrides[router_module._get_db_manager] = lambda: mock_db
            break

    return app, mock_pool


async def _get(app: FastAPI, path: str = "/api/relationship/plex/halo") -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get(path)


class TestHaloOwnerGate:
    async def test_returns_403_when_no_owner_entity(self):
        app, _ = _app_with_pool(owner_exists=False)
        resp = await _get(app)
        assert resp.status_code == 403
        body = resp.json()
        detail = body.get("detail", body)
        assert detail.get("code") == "owner_required"


class TestHaloEmptyGraph:
    async def test_returns_empty_arcs_and_totals(self):
        app, pool = _app_with_pool()
        resp = await _get(app)
        assert resp.status_code == 200
        assert resp.json() == {"arcs": {}, "totals": {}}
        # No satellites → the edge and person queries are never issued.
        assert pool.fetch.await_count == 2


class TestHaloArcs:
    async def test_arcs_grouped_by_type_with_totals(self):
        app, _ = _app_with_pool(
            totals_rows=[_total_row("organization", 171), _total_row("place", 17)],
            sat_rows=[
                _sat_row(_ORG_ID, "BCG", "organization"),
                _sat_row(_PLACE_ID, "Claudine", "place"),
            ],
        )
        resp = await _get(app)
        assert resp.status_code == 200
        body = resp.json()
        assert body["totals"] == {"organization": 171, "place": 17}
        assert [s["canonical_name"] for s in body["arcs"]["organization"]] == ["BCG"]
        assert [s["canonical_name"] for s in body["arcs"]["place"]] == ["Claudine"]

    async def test_per_type_param_validated(self):
        app, _ = _app_with_pool()
        resp = await _get(app, "/api/relationship/plex/halo?per_type=0")
        assert resp.status_code == 422

    async def test_per_type_above_upper_bound_rejected(self):
        # le=60 caps how many satellites a single arc can pull; 61 is over.
        app, _ = _app_with_pool()
        resp = await _get(app, "/api/relationship/plex/halo?per_type=61")
        assert resp.status_code == 422

    async def test_per_type_at_upper_bound_accepted(self):
        app, _ = _app_with_pool()
        resp = await _get(app, "/api/relationship/plex/halo?per_type=60")
        assert resp.status_code == 200


class TestHaloEdges:
    async def test_person_edges_attach_in_both_directions(self):
        app, _ = _app_with_pool(
            totals_rows=[_total_row("organization", 1)],
            sat_rows=[_sat_row(_ORG_ID, "BCG", "organization")],
            edge_rows=[
                # forward: person is the subject, satellite the object
                _edge_row(_PERSON_ID, str(_ORG_ID), "works-at"),
                # reverse: satellite is the subject, person the object
                _edge_row(_ORG_ID, str(_PERSON_2_ID), "employs"),
            ],
            person_rows=[_person_row(_PERSON_ID), _person_row(_PERSON_2_ID)],
        )
        resp = await _get(app)
        body = resp.json()
        edges = body["arcs"]["organization"][0]["edges"]
        assert {(e["person_id"], e["predicate"]) for e in edges} == {
            (str(_PERSON_ID), "works-at"),
            (str(_PERSON_2_ID), "employs"),
        }

    async def test_non_person_counterparties_are_dropped(self):
        other_org = uuid4()
        app, _ = _app_with_pool(
            totals_rows=[_total_row("organization", 2)],
            sat_rows=[_sat_row(_ORG_ID, "BCG", "organization")],
            edge_rows=[
                _edge_row(_ORG_ID, str(other_org), "partner-of"),
                _edge_row(_PERSON_ID, str(_ORG_ID), "works-at"),
            ],
            # Only the person survives the entity_type='person' filter.
            person_rows=[_person_row(_PERSON_ID)],
        )
        resp = await _get(app)
        edges = resp.json()["arcs"]["organization"][0]["edges"]
        assert edges == [{"person_id": str(_PERSON_ID), "predicate": "works-at"}]

    async def test_duplicate_triples_dedupe(self):
        app, _ = _app_with_pool(
            totals_rows=[_total_row("organization", 1)],
            sat_rows=[_sat_row(_ORG_ID, "BCG", "organization")],
            edge_rows=[
                _edge_row(_PERSON_ID, str(_ORG_ID), "works-at"),
                _edge_row(_PERSON_ID, str(_ORG_ID), "works-at"),
            ],
            person_rows=[_person_row(_PERSON_ID)],
        )
        resp = await _get(app)
        edges = resp.json()["arcs"]["organization"][0]["edges"]
        assert len(edges) == 1

    async def test_malformed_object_uuid_is_skipped(self):
        app, _ = _app_with_pool(
            totals_rows=[_total_row("organization", 1)],
            sat_rows=[_sat_row(_ORG_ID, "BCG", "organization")],
            edge_rows=[_edge_row(_ORG_ID, "not-a-uuid", "works-at")],
        )
        resp = await _get(app)
        assert resp.status_code == 200
        assert resp.json()["arcs"]["organization"][0]["edges"] == []
