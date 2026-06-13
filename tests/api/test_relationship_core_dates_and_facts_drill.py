"""Unit tests for two entity-v3 lookup-hygiene fixes (bu-rag77).

Item 3 — registry-driven core-date predicates
    ``GET /entities/{id}/core-dates`` no longer narrows on a hardcoded
    ``_DATE_KIND_PREDICATES`` tuple; it reads the eligible predicates from
    ``relationship.entity_predicate_registry`` (dashboard-relationship spec
    "Core dates block": "future date predicates from the registry"). A future
    date contact predicate seeded in the registry surfaces with no code change.

Item 7 — narrative layer is not re-appended on every cursor page
    ``GET /entities/{id}/facts?store=all`` appended the full narrative block on
    every page because the cursor only advances over the identity keyset. The
    narrative layer is now appended once, on the first page only (cursor absent),
    so paginating no longer duplicates narrative rows.

Both endpoints are exercised through the FastAPI app with a mocked relationship
pool (the established pattern in this directory), asserting the SQL the handler
issues rather than a string match.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager

pytestmark = pytest.mark.unit

BASE_URL = "http://test"
ENTITY_ID = uuid4()


def _owner_row() -> MagicMock:
    data = {"id": uuid4(), "roles": ["owner"]}
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda k: data[k])
    return row


def _wire_app(mock_pool: AsyncMock) -> FastAPI:
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool
    app = create_app()
    for butler_name, router_module in app.state.butler_routers:
        if butler_name == "relationship" and hasattr(router_module, "_get_db_manager"):
            app.dependency_overrides[router_module._get_db_manager] = lambda: mock_db
            return app
    raise RuntimeError("Relationship router not found by router discovery.")


async def _get(app: FastAPI, path: str) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=BASE_URL
    ) as client:
        return await client.get(path)


# ---------------------------------------------------------------------------
# Item 3 — registry-driven core-date predicates
# ---------------------------------------------------------------------------


def _date_fact_row(predicate: str, value: str) -> dict:
    return {
        "id": uuid4(),
        "predicate": predicate,
        "object": value,
        "src": "user",
        "conf": 1.0,
        "verified": True,
        "staleness_band": "fresh",
    }


class TestCoreDatesRegistryDriven:
    """Core-date predicates are sourced from the registry, not a hardcoded list."""

    async def test_uses_registry_predicates_in_facts_query(self):
        """The facts query is parameterised with the registry's contact predicates."""
        registry_rows = [{"predicate": "has-birthday"}, {"predicate": "has-anniversary"}]
        fact_rows = [_date_fact_row("has-anniversary", "2020-06-15")]

        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=_owner_row())  # owner-role gate
        mock_pool.fetchval = AsyncMock(return_value=1)  # entity exists
        # 1st fetch: registry predicates. 2nd fetch: facts.
        mock_pool.fetch = AsyncMock(side_effect=[registry_rows, fact_rows])

        app = _wire_app(mock_pool)
        resp = await _get(app, f"/api/relationship/entities/{ENTITY_ID}/core-dates")

        assert resp.status_code == 200, resp.text
        # The registry was read with a kind='contact'/object_kind='literal' filter.
        registry_sql = mock_pool.fetch.await_args_list[0].args[0]
        assert "entity_predicate_registry" in registry_sql
        assert "kind = 'contact'" in registry_sql
        # The facts query received the registry-derived predicate list (NOT a
        # hardcoded tuple) — proving a future-seeded predicate would flow through.
        facts_call = mock_pool.fetch.await_args_list[1]
        passed_predicates = facts_call.args[2]
        assert passed_predicates == ["has-birthday", "has-anniversary"]
        # A row stored under the registry-only predicate is rendered.
        body = resp.json()
        assert any(item["predicate"] == "has-anniversary" for item in body["items"])

    async def test_empty_registry_returns_no_core_dates(self):
        """No registered date predicates → empty block, no facts query issued."""
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=_owner_row())
        mock_pool.fetchval = AsyncMock(return_value=1)
        mock_pool.fetch = AsyncMock(side_effect=[[]])  # registry empty

        app = _wire_app(mock_pool)
        resp = await _get(app, f"/api/relationship/entities/{ENTITY_ID}/core-dates")

        assert resp.status_code == 200, resp.text
        assert resp.json()["items"] == []
        # Only the registry read happened — no facts query when there are no
        # eligible predicates.
        assert mock_pool.fetch.await_count == 1


# ---------------------------------------------------------------------------
# Item 7 — narrative layer appended only on the first page
# ---------------------------------------------------------------------------


def _identity_fact_row(created_at: datetime) -> dict:
    return {
        "id": uuid4(),
        "subject": ENTITY_ID,
        "predicate": "works_at",
        "object": "Acme",
        "object_kind": "entity",
        "src": "user",
        "conf": 1.0,
        "weight": None,
        "last_seen": created_at,
        "verified": False,
        "primary": None,
        "validity": "active",
        "created_at": created_at,
        "staleness_band": "fresh",
    }


def _narrative_fact_row() -> dict:
    return {
        "id": uuid4(),
        "subject": ENTITY_ID,
        "predicate": "mentioned",
        "object": "switching jobs",
        "object_kind": "literal",
        "src": "memory",
        "conf": 0.8,
        "weight": None,
        "last_seen": datetime(2026, 1, 1, tzinfo=UTC),
        "verified": False,
        "primary": None,
        "validity": "active",
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
        "staleness_band": "fresh",
    }


def _facts_app(identity_rows: list[dict], narrative_rows: list[dict]) -> tuple[FastAPI, AsyncMock]:
    mock_pool = AsyncMock()
    mock_pool.fetchrow = AsyncMock(return_value=_owner_row())  # owner-entity gate
    mock_pool.fetchval = AsyncMock(return_value=1)  # entity exists
    # 1st fetch: identity rows. 2nd fetch (page 1 only): narrative rows.
    mock_pool.fetch = AsyncMock(side_effect=[identity_rows, narrative_rows])
    return _wire_app(mock_pool), mock_pool


class TestFactsDrillNarrativePagination:
    """store=all appends the narrative block once (first page), never on later pages."""

    async def test_narrative_appended_on_first_page(self):
        """First page (no cursor) appends narrative rows after the identity page."""
        identity = [_identity_fact_row(datetime(2026, 2, day, tzinfo=UTC)) for day in (3, 2, 1)]
        narrative = [_narrative_fact_row()]
        app, mock_pool = _facts_app(identity, narrative)

        resp = await _get(app, f"/api/relationship/entities/{ENTITY_ID}/facts?store=all&limit=20")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        stores = [item["store"] for item in body["items"]]
        assert "narrative" in stores, "first page must include the narrative layer"
        # Both stores queried on the first page.
        assert mock_pool.fetch.await_count == 2

    async def test_narrative_not_appended_on_cursored_page(self):
        """A cursored page (page 2+) must NOT re-append the narrative block."""
        identity = [_identity_fact_row(datetime(2026, 2, 1, tzinfo=UTC))]
        narrative = [_narrative_fact_row()]
        app, mock_pool = _facts_app(identity, narrative)

        # An opaque-but-valid cursor encodes (created_at, id). Build one via the
        # router's own encoder to avoid coupling to the wire format.
        cursor = _encode_cursor(datetime(2026, 3, 1, tzinfo=UTC), uuid4())
        resp = await _get(
            app,
            f"/api/relationship/entities/{ENTITY_ID}/facts?store=all&limit=20&cursor={cursor}",
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        stores = [item["store"] for item in body["items"]]
        assert "narrative" not in stores, "cursored pages must not re-append narrative rows"
        # Only the identity store was queried — the narrative fetch was skipped.
        assert mock_pool.fetch.await_count == 1


def _encode_cursor(created_at: datetime, fact_id: UUID) -> str:
    """Encode a facts cursor using the router's own encoder."""
    app = create_app()
    for butler_name, router_module in app.state.butler_routers:
        if butler_name == "relationship" and hasattr(router_module, "_encode_facts_cursor"):
            return router_module._encode_facts_cursor(created_at, fact_id)
    raise RuntimeError("facts cursor encoder not found on the relationship router")
