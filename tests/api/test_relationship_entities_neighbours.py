"""Tests for GET /api/relationship/entities/{id}/neighbours.

Covers:
- Response shape: NeighboursResponse with neighbours dict
- canonical_name is included in each NeighbourEntry
- canonical_name sourced from public.entities JOIN
- Owner-required gate: no owner returns 403
- Entity not found: non-existent entity_id returns 404
- Empty neighbours when no relational triples exist

Uses the same mock-pool pattern as test_relationship_entities_search.py —
no real Postgres or Docker required.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager

pytestmark = pytest.mark.unit

BASE_URL = "http://test"

_ANCHOR_ID = uuid4()
_NEIGHBOUR_ID_1 = uuid4()
_NEIGHBOUR_ID_2 = uuid4()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_neighbour_row(
    *,
    subject: UUID | None = None,
    predicate: str = "knows",
    object_: str | None = None,
    object_kind: str = "entity",
    src: str = "relationship",
    conf: float = 1.0,
    last_seen: datetime | None = None,
    weight: int | None = None,
    verified: bool = False,
    primary: bool | None = None,
    direction: str = "forward",
    canonical_name: str = "Bob Example",
) -> MagicMock:
    """Build a MagicMock that behaves like an asyncpg Record for neighbour rows."""
    subject = subject or _ANCHOR_ID
    object_ = object_ or str(_NEIGHBOUR_ID_1)
    row_id = uuid4()
    data: dict = {
        "id": row_id,
        "subject": subject,
        "predicate": predicate,
        "object": object_,
        "object_kind": object_kind,
        "src": src,
        "conf": conf,
        "last_seen": last_seen,
        "weight": weight,
        "verified": verified,
        "primary": primary,
        "direction": direction,
        "canonical_name": canonical_name,
    }
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda key: data[key])
    return row


def _app_with_pool(
    *,
    fetch_rows: list | None = None,
    owner_row: dict | None = None,
    entity_exists: bool = True,
) -> tuple[FastAPI, AsyncMock]:
    """Wire a FastAPI app with a mocked relationship DB pool.

    ``owner_row`` controls _assert_owner_entity_exists() — defaults to passing.
    ``entity_exists`` controls _assert_entity_exists() fetchval result.
    ``fetch_rows`` is returned by pool.fetch (the neighbours query).
    """
    if owner_row is None:
        owner_row = {"id": uuid4()}

    mock_pool = AsyncMock()
    mock_pool.fetchrow = AsyncMock(return_value=owner_row)
    mock_pool.fetchval = AsyncMock(return_value=1 if entity_exists else None)
    mock_pool.fetch = AsyncMock(return_value=fetch_rows or [])

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool

    app = create_app()
    for butler_name, router_module in app.state.butler_routers:
        if butler_name == "relationship" and hasattr(router_module, "_get_db_manager"):
            app.dependency_overrides[router_module._get_db_manager] = lambda: mock_db
            break

    return app, mock_pool


def _no_owner_app() -> FastAPI:
    """Return an app whose mock DB simulates no owner entity (gate returns 403)."""
    mock_pool = AsyncMock()
    mock_pool.fetchrow = AsyncMock(return_value=None)
    mock_pool.fetchval = AsyncMock(return_value=None)
    mock_pool.fetch = AsyncMock(return_value=[])

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool

    app = create_app()
    for butler_name, router_module in app.state.butler_routers:
        if butler_name == "relationship" and hasattr(router_module, "_get_db_manager"):
            app.dependency_overrides[router_module._get_db_manager] = lambda: mock_db
            break
    return app


async def _get(app: FastAPI, entity_id: UUID = _ANCHOR_ID) -> httpx.Response:
    path = f"/api/relationship/entities/{entity_id}/neighbours"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=BASE_URL
    ) as client:
        return await client.get(path)


# ---------------------------------------------------------------------------
# Scenario: owner gate
# ---------------------------------------------------------------------------


async def test_owner_gate_returns_403_when_no_owner():
    """GET /neighbours returns 403 + owner_required when no owner entity is registered."""
    app = _no_owner_app()
    resp = await _get(app)
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "owner_required"


# ---------------------------------------------------------------------------
# Scenario: entity not found
# ---------------------------------------------------------------------------


async def test_returns_404_for_unknown_entity():
    """GET /neighbours returns 404 when the anchor entity_id is not in public.entities."""
    app, _ = _app_with_pool(entity_exists=False)
    resp = await _get(app)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Scenario: empty neighbours
# ---------------------------------------------------------------------------


async def test_empty_neighbours_returns_empty_dict():
    """GET /neighbours returns 200 with empty neighbours dict when no triples exist."""
    app, _ = _app_with_pool(fetch_rows=[])
    resp = await _get(app)
    assert resp.status_code == 200
    body = resp.json()
    assert body["neighbours"] == {}


# ---------------------------------------------------------------------------
# Scenario: response shape and canonical_name
# ---------------------------------------------------------------------------


async def test_response_shape_contains_required_fields():
    """Each NeighbourEntry must include entity_id, canonical_name, and direction."""
    rows = [
        _make_neighbour_row(
            subject=_ANCHOR_ID,
            object_=str(_NEIGHBOUR_ID_1),
            direction="forward",
            canonical_name="Bob Example",
        )
    ]
    app, _ = _app_with_pool(fetch_rows=rows)
    resp = await _get(app)

    assert resp.status_code == 200
    body = resp.json()
    entry = body["neighbours"]["knows"][0]
    assert "entity_id" in entry
    assert "canonical_name" in entry
    assert "direction" in entry
    assert "src" in entry
    assert "conf" in entry


async def test_canonical_name_included_in_neighbour_entry():
    """canonical_name from the JOIN must be present in each neighbour row."""
    rows = [
        _make_neighbour_row(
            subject=_ANCHOR_ID,
            object_=str(_NEIGHBOUR_ID_1),
            direction="forward",
            canonical_name="Alice Smith",
        ),
        _make_neighbour_row(
            subject=_NEIGHBOUR_ID_2,
            object_=str(_ANCHOR_ID),
            direction="reverse",
            predicate="family-of",
            canonical_name="Carol Danvers",
        ),
    ]
    app, _ = _app_with_pool(fetch_rows=rows)
    resp = await _get(app)

    assert resp.status_code == 200
    body = resp.json()
    knows_entry = body["neighbours"]["knows"][0]
    assert knows_entry["canonical_name"] == "Alice Smith"
    family_entry = body["neighbours"]["family-of"][0]
    assert family_entry["canonical_name"] == "Carol Danvers"


# ---------------------------------------------------------------------------
# Scenario: grouping by predicate
# ---------------------------------------------------------------------------


async def test_neighbours_grouped_by_predicate():
    """Neighbour entries are grouped by predicate in the response dict."""
    rows = [
        _make_neighbour_row(
            subject=_ANCHOR_ID,
            object_=str(_NEIGHBOUR_ID_1),
            predicate="knows",
            direction="forward",
            canonical_name="Bob Example",
        ),
        _make_neighbour_row(
            subject=_NEIGHBOUR_ID_2,
            object_=str(_ANCHOR_ID),
            predicate="family-of",
            direction="reverse",
            canonical_name="Carol Danvers",
        ),
    ]
    app, _ = _app_with_pool(fetch_rows=rows)
    resp = await _get(app)

    assert resp.status_code == 200
    body = resp.json()
    assert "knows" in body["neighbours"]
    assert "family-of" in body["neighbours"]
    assert len(body["neighbours"]["knows"]) == 1
    assert len(body["neighbours"]["family-of"]) == 1
