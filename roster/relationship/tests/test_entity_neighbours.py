"""Tests for GET /api/relationship/entities/{id}/neighbours endpoint.

Covers spec scenarios from
``openspec/changes/archive/2026-05-20-relationship-tabs-to-entities/specs/dashboard-relationship/spec.md``
§ "Requirement: Entity neighbours endpoint" and Amendment 12b (owner-only gate).

Each test hits the FastAPI router via httpx.AsyncClient with a mocked DB pool
so no real Postgres or Docker is required.  Tests are marked ``unit`` to avoid
the Docker-availability guard applied to roster/ integration tests.
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

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ENT_ID = uuid4()
_NEIGHBOUR_A = uuid4()
_NEIGHBOUR_B = uuid4()
_MISSING_ENT_ID = uuid4()
_OWNER_ENTITY_ID = uuid4()


# ---------------------------------------------------------------------------
# Row helpers
# ---------------------------------------------------------------------------


def _make_fact_row(
    *,
    subject: UUID | None = None,
    predicate: str = "knows",
    object_val: str | None = None,
    direction: str = "forward",
    src: str = "relationship",
    conf: float = 1.0,
    last_seen=None,
    weight: int | None = None,
    verified: bool = False,
    primary: bool | None = None,
    canonical_name: str = "",
) -> MagicMock:
    """Build a MagicMock that behaves like an asyncpg Record for a facts row.

    The neighbours SQL also selects ``canonical_name`` from the LEFT JOIN on
    ``public.entities`` (PR #1849); include it in the mock row so the handler's
    ``r["canonical_name"]`` lookup does not KeyError.
    """
    if subject is None:
        subject = _ENT_ID
    if object_val is None:
        object_val = str(_NEIGHBOUR_A)

    data = {
        "id": uuid4(),
        "subject": subject,
        "predicate": predicate,
        "object": object_val,
        "object_kind": "entity",
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


def _make_owner_row() -> MagicMock:
    """Simulate a row returned by the owner-entity check query.

    Must include ``roles`` because _get_owner_roles() (PR #1859 refactor of the
    Amendment 12a/12b gate) reads ``row["roles"]`` to verify ``'owner'`` is
    present.  See PR #1863 for the same fix applied to the older
    tests/api/test_relationship_entities_neighbours.py.
    """
    data = {"id": _OWNER_ENTITY_ID, "roles": ["owner"]}
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda key: data[key])
    return row


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def _app_with_pool(
    *,
    owner_exists: bool = True,
    entity_exists: bool = True,
    fact_rows: list | None = None,
) -> tuple[FastAPI, AsyncMock]:
    """Wire a FastAPI app with mock DB pool for the neighbours endpoint.

    Call sequence inside the endpoint:
      1. pool.fetchrow → owner entity check (None → 403)
      2. pool.fetchval → entity-exists check (None → 404)
      3. pool.fetch    → relational triples from relationship.entity_facts

    ``owner_exists`` controls whether fetchrow returns an owner row.
    ``entity_exists`` controls whether fetchval returns a non-None value.
    ``fact_rows`` is returned by pool.fetch for the triples query.
    """
    mock_pool = AsyncMock()
    mock_pool.fetchrow = AsyncMock(return_value=_make_owner_row() if owner_exists else None)
    mock_pool.fetchval = AsyncMock(return_value=1 if entity_exists else None)
    mock_pool.fetch = AsyncMock(return_value=fact_rows or [])

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool

    app = create_app()
    for butler_name, router_module in app.state.butler_routers:
        if butler_name == "relationship" and hasattr(router_module, "_get_db_manager"):
            app.dependency_overrides[router_module._get_db_manager] = lambda: mock_db
            break

    return app, mock_pool


async def _get(app: FastAPI, path: str) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get(path)


# ---------------------------------------------------------------------------
# Scenario 1: Happy path — entity with no relational triples
# ---------------------------------------------------------------------------


class TestNeighboursEmptyEntity:
    """Entity with zero relational triples returns empty neighbours dict."""

    async def test_returns_200_with_empty_neighbours(self):
        app, _ = _app_with_pool(fact_rows=[])
        resp = await _get(app, f"/api/relationship/entities/{_ENT_ID}/neighbours")

        assert resp.status_code == 200
        body = resp.json()
        assert "neighbours" in body
        assert body["neighbours"] == {}


# ---------------------------------------------------------------------------
# Scenario 2: Forward direction (queried entity as subject)
# ---------------------------------------------------------------------------


class TestNeighboursForwardDirection:
    """Triples where queried entity is the subject → direction='forward'."""

    async def test_forward_triple_appears_in_response(self):
        rows = [
            _make_fact_row(
                subject=_ENT_ID,
                predicate="knows",
                object_val=str(_NEIGHBOUR_A),
                direction="forward",
            )
        ]
        app, _ = _app_with_pool(fact_rows=rows)
        resp = await _get(app, f"/api/relationship/entities/{_ENT_ID}/neighbours")

        assert resp.status_code == 200
        body = resp.json()
        assert "knows" in body["neighbours"]
        entries = body["neighbours"]["knows"]
        assert len(entries) == 1
        assert entries[0]["direction"] == "forward"
        assert UUID(entries[0]["entity_id"]) == _NEIGHBOUR_A

    async def test_neighbour_entity_id_is_the_object(self):
        rows = [
            _make_fact_row(
                subject=_ENT_ID,
                predicate="family-of",
                object_val=str(_NEIGHBOUR_B),
                direction="forward",
            )
        ]
        app, _ = _app_with_pool(fact_rows=rows)
        resp = await _get(app, f"/api/relationship/entities/{_ENT_ID}/neighbours")

        body = resp.json()
        entry = body["neighbours"]["family-of"][0]
        assert UUID(entry["entity_id"]) == _NEIGHBOUR_B


# ---------------------------------------------------------------------------
# Scenario 3: Reverse direction (queried entity as object)
# ---------------------------------------------------------------------------


class TestNeighboursReverseDirection:
    """Triples where queried entity is the object → direction='reverse'."""

    async def test_reverse_triple_appears_in_response(self):
        # In this row, _NEIGHBOUR_A is the subject and _ENT_ID is the object.
        rows = [
            _make_fact_row(
                subject=_NEIGHBOUR_A,
                predicate="knows",
                object_val=str(_ENT_ID),
                direction="reverse",
            )
        ]
        app, _ = _app_with_pool(fact_rows=rows)
        resp = await _get(app, f"/api/relationship/entities/{_ENT_ID}/neighbours")

        assert resp.status_code == 200
        body = resp.json()
        assert "knows" in body["neighbours"]
        entries = body["neighbours"]["knows"]
        assert len(entries) == 1
        assert entries[0]["direction"] == "reverse"
        # The neighbour entity is the subject (not the queried entity).
        assert UUID(entries[0]["entity_id"]) == _NEIGHBOUR_A


# ---------------------------------------------------------------------------
# Scenario 4: Both directions returned for same predicate
# ---------------------------------------------------------------------------


class TestNeighboursBothDirections:
    """Bidirectional triples: queried entity as subject AND object."""

    async def test_both_directions_returned(self):
        rows = [
            _make_fact_row(
                subject=_ENT_ID,
                predicate="knows",
                object_val=str(_NEIGHBOUR_A),
                direction="forward",
            ),
            _make_fact_row(
                subject=_NEIGHBOUR_B,
                predicate="knows",
                object_val=str(_ENT_ID),
                direction="reverse",
            ),
        ]
        app, _ = _app_with_pool(fact_rows=rows)
        resp = await _get(app, f"/api/relationship/entities/{_ENT_ID}/neighbours")

        body = resp.json()
        entries = body["neighbours"]["knows"]
        assert len(entries) == 2
        directions = {e["direction"] for e in entries}
        assert directions == {"forward", "reverse"}


# ---------------------------------------------------------------------------
# Scenario 5: Grouped by predicate
# ---------------------------------------------------------------------------


class TestNeighboursGroupedByPredicate:
    """Multiple predicates produce separate groups in the response."""

    async def test_multiple_predicates_are_grouped(self):
        rows = [
            _make_fact_row(predicate="knows", object_val=str(_NEIGHBOUR_A), direction="forward"),
            _make_fact_row(
                predicate="family-of", object_val=str(_NEIGHBOUR_B), direction="forward"
            ),
            _make_fact_row(
                predicate="partner-of", object_val=str(_NEIGHBOUR_A), direction="forward"
            ),
        ]
        app, _ = _app_with_pool(fact_rows=rows)
        resp = await _get(app, f"/api/relationship/entities/{_ENT_ID}/neighbours")

        body = resp.json()
        assert set(body["neighbours"].keys()) == {"knows", "family-of", "partner-of"}
        assert len(body["neighbours"]["knows"]) == 1
        assert len(body["neighbours"]["family-of"]) == 1
        assert len(body["neighbours"]["partner-of"]) == 1


# ---------------------------------------------------------------------------
# Scenario 6: Owner-only authz gate (Clause 12b)
# ---------------------------------------------------------------------------


class TestNeighboursOwnerAuthzGate:
    """Clause 12b: endpoint returns 403 when no owner entity is registered."""

    async def test_returns_403_when_no_owner_entity(self):
        """Non-owner / misconfigured system → 403 with owner_required code."""
        app, _ = _app_with_pool(owner_exists=False)
        resp = await _get(app, f"/api/relationship/entities/{_ENT_ID}/neighbours")

        assert resp.status_code == 403
        body = resp.json()
        # The 'code' field must be 'owner_required' per Amendment 12b spec.
        detail = body.get("detail", body)
        if isinstance(detail, dict):
            assert detail.get("code") == "owner_required"
        else:
            assert "owner_required" in str(detail)

    async def test_returns_200_when_owner_entity_present(self):
        """Owner entity present → gate passes, endpoint proceeds normally."""
        app, _ = _app_with_pool(owner_exists=True, fact_rows=[])
        resp = await _get(app, f"/api/relationship/entities/{_ENT_ID}/neighbours")

        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Scenario 7: Entity not found → 404
# ---------------------------------------------------------------------------


class TestNeighboursEntityNotFound:
    """Non-existent entity UUID → 404."""

    async def test_returns_404_for_missing_entity(self):
        app, _ = _app_with_pool(entity_exists=False)
        resp = await _get(app, f"/api/relationship/entities/{_MISSING_ENT_ID}/neighbours")

        assert resp.status_code == 404
        body = resp.json()
        assert "not found" in body.get("detail", "").lower()


# ---------------------------------------------------------------------------
# Scenario 8: Provenance fields always present
# ---------------------------------------------------------------------------


class TestNeighboursProvenanceFields:
    """Every entry must include all six provenance fields per spec contract."""

    async def test_all_provenance_fields_present(self):
        from datetime import UTC, datetime

        last_seen_dt = datetime(2026, 4, 30, 10, 0, 0, tzinfo=UTC)
        rows = [
            _make_fact_row(
                predicate="knows",
                object_val=str(_NEIGHBOUR_A),
                direction="forward",
                src="relationship",
                conf=0.9,
                last_seen=last_seen_dt,
                weight=5,
                verified=True,
                primary=True,
            )
        ]
        app, _ = _app_with_pool(fact_rows=rows)
        resp = await _get(app, f"/api/relationship/entities/{_ENT_ID}/neighbours")

        assert resp.status_code == 200
        entry = resp.json()["neighbours"]["knows"][0]
        assert "src" in entry
        assert "conf" in entry
        assert "last_seen" in entry
        assert "weight" in entry
        assert "verified" in entry
        assert "primary" in entry

    async def test_provenance_null_fields_are_explicit_null(self):
        """Absent provenance fields must be explicit nulls, never omitted."""
        rows = [
            _make_fact_row(
                predicate="knows",
                object_val=str(_NEIGHBOUR_A),
                direction="forward",
                last_seen=None,
                weight=None,
                primary=None,
            )
        ]
        app, _ = _app_with_pool(fact_rows=rows)
        resp = await _get(app, f"/api/relationship/entities/{_ENT_ID}/neighbours")

        entry = resp.json()["neighbours"]["knows"][0]
        assert entry["last_seen"] is None
        assert entry["weight"] is None
        assert entry["primary"] is None
        assert entry["conf"] == 1.0
        assert entry["verified"] is False

    async def test_provenance_values_populated_when_present(self):
        rows = [
            _make_fact_row(
                predicate="knows",
                object_val=str(_NEIGHBOUR_A),
                direction="forward",
                src="telegram-butler",
                conf=0.75,
                weight=3,
                verified=True,
                primary=False,
            )
        ]
        app, _ = _app_with_pool(fact_rows=rows)
        resp = await _get(app, f"/api/relationship/entities/{_ENT_ID}/neighbours")

        entry = resp.json()["neighbours"]["knows"][0]
        assert entry["src"] == "telegram-butler"
        assert entry["conf"] == 0.75
        assert entry["weight"] == 3
        assert entry["verified"] is True
        assert entry["primary"] is False
