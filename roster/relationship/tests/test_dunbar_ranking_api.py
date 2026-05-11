"""Unit tests for GET /api/relationship/dunbar/ranking — alias exposure.

Verifies that the ranking endpoint includes the ``aliases`` field from
``public.entities`` in every DunbarEntry of the response.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row(**kwargs) -> MagicMock:
    """Build a MagicMock that behaves like an asyncpg Record."""
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda key: kwargs[key])
    row.get = MagicMock(side_effect=lambda key, default=None: kwargs.get(key, default))
    return row


def _build_app(
    ranked: list[dict],
    entity_rows: list[MagicMock],
    avatar_rows: list[MagicMock],
    owner_row: MagicMock | None,
) -> FastAPI:
    """Wire up a test app whose pool returns controlled data for the ranking endpoint."""
    mock_pool = AsyncMock()

    # compute_tier_ranking is called first and returns the ranked list.
    # The pool is then used in asyncio.gather for four queries:
    #   1. pool.fetch(entities query)            -> entity_rows
    #   2. pool.fetch(contacts/avatar query)     -> avatar_rows
    #   3. pool.fetchrow(owner query)            -> owner_row
    #   4. pool.fetch(30d interaction count)     -> [] (empty — warmth not tested here)
    # We'll patch compute_tier_ranking to avoid any real DB call and
    # configure pool.fetch to return the correct rows per call.
    fetch_call_count = [0]

    async def _fetch(*args, **kwargs):
        fetch_call_count[0] += 1
        if fetch_call_count[0] == 1:
            return entity_rows
        elif fetch_call_count[0] == 2:
            return avatar_rows
        else:
            # 30-day interaction count query — return empty list (warmth=None for tier-1500
            # or warmth computed from 0 interactions for others)
            return []

    mock_pool.fetch = _fetch
    mock_pool.fetchrow = AsyncMock(return_value=owner_row)

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool

    app = create_app()

    for butler_name, router_module in app.state.butler_routers:
        if butler_name == "relationship" and hasattr(router_module, "_get_db_manager"):
            app.dependency_overrides[router_module._get_db_manager] = lambda: mock_db
            break

    # Patch the engine to skip real dunbar scoring logic.
    from butlers.tools.relationship import dunbar as _dunbar_mod  # noqa: PLC0415

    _dunbar_mod.compute_tier_ranking = AsyncMock(return_value=ranked)

    return app


async def _get_ranking(app: FastAPI) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get("/api/relationship/dunbar/ranking")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDunbarRankingAliases:
    """GET /dunbar/ranking — aliases field is exposed per entry."""

    async def test_aliases_present_in_response_entry(self):
        """aliases from public.entities are included in each DunbarEntry."""
        contact_id = uuid4()
        entity_id = uuid4()

        ranked = [
            {
                "contact_id": contact_id,
                "entity_id": entity_id,
                "dunbar_tier": 15,
                "dunbar_score": 0.8,
                "dunbar_tier_override": False,
            }
        ]

        entity_row = _row(
            id=entity_id,
            canonical_name="Alice Nguyen",
            aliases=["Ally", "A. Nguyen"],
        )
        avatar_row = _row(id=contact_id, avatar_url=None)
        owner_row = None

        app = _build_app(ranked, [entity_row], [avatar_row], owner_row)
        resp = await _get_ranking(app)

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["entries"]) == 1
        entry = body["entries"][0]
        assert entry["canonical_name"] == "Alice Nguyen"
        assert entry["aliases"] == ["Ally", "A. Nguyen"]

    async def test_aliases_empty_list_when_entity_has_no_aliases(self):
        """When public.entities.aliases is empty, entry.aliases is []."""
        contact_id = uuid4()
        entity_id = uuid4()

        ranked = [
            {
                "contact_id": contact_id,
                "entity_id": entity_id,
                "dunbar_tier": 50,
                "dunbar_score": 0.5,
                "dunbar_tier_override": False,
            }
        ]

        entity_row = _row(id=entity_id, canonical_name="Bob Smith", aliases=[])
        avatar_row = _row(id=contact_id, avatar_url=None)

        app = _build_app(ranked, [entity_row], [avatar_row], None)
        resp = await _get_ranking(app)

        assert resp.status_code == 200
        body = resp.json()
        entry = body["entries"][0]
        assert entry["aliases"] == []

    async def test_aliases_empty_list_when_entity_aliases_is_null(self):
        """When public.entities.aliases is NULL, entry.aliases is []."""
        contact_id = uuid4()
        entity_id = uuid4()

        ranked = [
            {
                "contact_id": contact_id,
                "entity_id": entity_id,
                "dunbar_tier": 150,
                "dunbar_score": 0.3,
                "dunbar_tier_override": False,
            }
        ]

        entity_row = _row(id=entity_id, canonical_name="Carol Lee", aliases=None)
        avatar_row = _row(id=contact_id, avatar_url=None)

        app = _build_app(ranked, [entity_row], [avatar_row], None)
        resp = await _get_ranking(app)

        assert resp.status_code == 200
        body = resp.json()
        entry = body["entries"][0]
        assert entry["aliases"] == []

    async def test_owner_entity_id_returned_when_owner_exists(self):
        """owner_entity_id is populated from public.entities owner row."""
        owner_entity_id = uuid4()
        ranked: list[dict] = []

        owner_row = _row(id=owner_entity_id)

        app = _build_app(ranked, [], [], owner_row)
        resp = await _get_ranking(app)

        assert resp.status_code == 200
        body = resp.json()
        assert body["owner_entity_id"] == str(owner_entity_id)
        assert body["entries"] == []


class TestDunbarRankingLastInteractionAt:
    """GET /dunbar/ranking — last_interaction_at field is exposed per entry."""

    async def test_last_interaction_at_populated_when_present(self):
        """last_interaction_at from the scoring engine is included in each DunbarEntry."""
        from datetime import UTC, datetime

        contact_id = uuid4()
        entity_id = uuid4()
        last_seen = datetime(2026, 4, 20, 10, 0, 0, tzinfo=UTC)

        ranked = [
            {
                "contact_id": contact_id,
                "entity_id": entity_id,
                "dunbar_tier": 5,
                "dunbar_score": 2.5,
                "dunbar_tier_override": False,
                "last_interaction_at": last_seen,
            }
        ]

        entity_row = _row(id=entity_id, canonical_name="Alice Nguyen", aliases=[])
        avatar_row = _row(id=contact_id, avatar_url=None)

        app = _build_app(ranked, [entity_row], [avatar_row], None)
        resp = await _get_ranking(app)

        assert resp.status_code == 200
        entry = resp.json()["entries"][0]
        assert entry["last_interaction_at"] is not None
        assert "2026-04-20" in entry["last_interaction_at"]

    async def test_last_interaction_at_null_when_no_interactions(self):
        """last_interaction_at is null in DunbarEntry when the contact has no interactions."""
        contact_id = uuid4()
        entity_id = uuid4()

        ranked = [
            {
                "contact_id": contact_id,
                "entity_id": entity_id,
                "dunbar_tier": 1500,
                "dunbar_score": 0.0,
                "dunbar_tier_override": False,
                "last_interaction_at": None,
            }
        ]

        entity_row = _row(id=entity_id, canonical_name="Bob Smith", aliases=[])
        avatar_row = _row(id=contact_id, avatar_url=None)

        app = _build_app(ranked, [entity_row], [avatar_row], None)
        resp = await _get_ranking(app)

        assert resp.status_code == 200
        entry = resp.json()["entries"][0]
        assert entry["last_interaction_at"] is None
