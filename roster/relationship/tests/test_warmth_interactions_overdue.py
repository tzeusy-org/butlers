"""Unit tests for warmth, contact interaction thread, and overdue endpoints.

Covers acceptance criteria from bu-iuol4.22:
1. warmth field on DunbarEntry (GET /dunbar/ranking)
2. GET /contacts/{contact_id}/interactions — chronological thread with direction
3. GET /contacts/overdue?days=N — overdue contacts with tier-cadence logic

All tests hit the FastAPI router via httpx.AsyncClient with a mocked DB pool.
No real Postgres or Docker required. Marked ``unit`` to run without Docker.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
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

_NOW = datetime(2026, 5, 11, 12, 0, 0, tzinfo=UTC)


def _row(**kwargs) -> MagicMock:
    """Build a MagicMock that behaves like an asyncpg Record."""
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda key: kwargs[key])
    row.get = MagicMock(side_effect=lambda key, default=None: kwargs.get(key, default))
    return row


def _wire_app(mock_db: MagicMock) -> FastAPI:
    """Create a test app with the relationship DB pool overridden."""
    app = create_app()
    for butler_name, router_module in app.state.butler_routers:
        if butler_name == "relationship" and hasattr(router_module, "_get_db_manager"):
            app.dependency_overrides[router_module._get_db_manager] = lambda: mock_db
            break
    return app


def _mock_db(pool: AsyncMock) -> MagicMock:
    db = MagicMock(spec=DatabaseManager)
    db.pool.return_value = pool
    return db


async def _get(app: FastAPI, path: str, **params) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get(path, params=params or None)


# ---------------------------------------------------------------------------
# Warmth formula unit tests (no HTTP required)
# ---------------------------------------------------------------------------


class TestComputeWarmth:
    """Direct unit tests for the _compute_warmth helper."""

    def _warmth(self, last_interaction_at, interactions_in_last_30d, tier_cadence_days):
        # Import from the loaded router module
        from butlers.api.router_discovery import discover_butler_routers

        for butler_name, module in discover_butler_routers():
            if butler_name == "relationship":
                return module._compute_warmth(
                    last_interaction_at=last_interaction_at,
                    interactions_in_last_30d=interactions_in_last_30d,
                    tier_cadence_days=tier_cadence_days,
                )
        raise RuntimeError("relationship router not found")

    def test_fully_warm_contact(self):
        """Contact touched today and above frequency target is warmth=1.0."""
        last_at = datetime.now(UTC)
        # tier-5 cadence is 14 days; target per 30d = 30/14 ≈ 2.14
        # recency=1.0 (just touched), frequency=min(1, 3/2.14)=1.0
        result = self._warmth(last_at, interactions_in_last_30d=3, tier_cadence_days=14)
        assert result == pytest.approx(1.0, abs=0.01)

    def test_no_interactions_gives_zero(self):
        """No last_interaction_at → recency=0; no 30d interactions → frequency=0 → warmth=0."""
        result = self._warmth(
            last_interaction_at=None,
            interactions_in_last_30d=0,
            tier_cadence_days=14,
        )
        assert result == 0.0

    def test_partial_warmth_mid_cadence(self):
        """Contact at half the cadence window → recency=0.5; 0 recent → frequency=0."""
        half_cadence_days = 7  # half of 14-day cadence
        last_at = datetime.now(UTC) - timedelta(days=half_cadence_days)
        result = self._warmth(
            last_interaction_at=last_at,
            interactions_in_last_30d=0,
            tier_cadence_days=14,
        )
        # recency = 1 - 7/14 = 0.5, frequency = 0.0
        # warmth = 0.6 * 0.5 + 0.4 * 0.0 = 0.3
        assert result == pytest.approx(0.3, abs=0.01)

    def test_warmth_clamped_to_zero_when_overdue(self):
        """Contact way past cadence → recency clamped to 0."""
        last_at = datetime.now(UTC) - timedelta(days=100)
        result = self._warmth(
            last_interaction_at=last_at,
            interactions_in_last_30d=0,
            tier_cadence_days=14,
        )
        assert result == 0.0

    def test_frequency_capped_at_one(self):
        """Many interactions in 30d + touched today → warmth=1.0."""
        # Touch time is now to get recency_score=1.0; 100 interactions cap frequency at 1.0
        last_at = datetime.now(UTC)
        result = self._warmth(
            last_interaction_at=last_at,
            interactions_in_last_30d=100,
            tier_cadence_days=14,
        )
        assert result == pytest.approx(1.0, abs=0.01)

    def test_warmth_result_in_valid_range(self):
        """Warmth always in [0.0, 1.0]."""
        last_at = datetime.now(UTC) - timedelta(days=5)
        result = self._warmth(
            last_interaction_at=last_at,
            interactions_in_last_30d=1,
            tier_cadence_days=21,
        )
        assert 0.0 <= result <= 1.0


# ---------------------------------------------------------------------------
# GET /dunbar/ranking — warmth field included
# ---------------------------------------------------------------------------


class TestDunbarRankingWarmth:
    """warmth field is present in DunbarRanking entries."""

    def _build_app(self, ranked: list[dict]) -> FastAPI:
        contact_id = uuid4()
        entity_id = uuid4()

        mock_pool = AsyncMock()

        fetch_call_count = [0]

        async def _fetch(*args, **kwargs):
            fetch_call_count[0] += 1
            if fetch_call_count[0] == 1:
                # entity name rows
                return [
                    _row(id=entity_id, canonical_name="Alice Test", aliases=[], avatar_url=None)
                ]
            else:
                # interaction_30d rows — 2 interactions in last 30d
                # (avatar_url query removed from public.contacts — bu-j77a5)
                return [_row(contact_id=contact_id, interaction_count_30d=2)]

        mock_pool.fetch = _fetch
        mock_pool.fetchrow = AsyncMock(return_value=None)  # no owner

        mock_db = _mock_db(mock_pool)
        app = _wire_app(mock_db)

        from butlers.tools.relationship import dunbar as _dunbar_mod

        _dunbar_mod.compute_tier_ranking = AsyncMock(return_value=ranked)

        return app

    async def test_warmth_present_in_ranking_entry(self):
        """DunbarEntry has warmth field when tier has a cadence."""
        contact_id = uuid4()
        entity_id = uuid4()
        last_at = datetime.now(UTC) - timedelta(days=3)

        ranked = [
            {
                "contact_id": contact_id,
                "entity_id": entity_id,
                "dunbar_tier": 15,  # 21-day cadence
                "dunbar_score": 0.8,
                "dunbar_tier_override": False,
                "last_interaction_at": last_at,
            }
        ]

        mock_pool = AsyncMock()

        fetch_call_count = [0]

        async def _fetch(*args, **kwargs):
            fetch_call_count[0] += 1
            if fetch_call_count[0] == 1:
                return [_row(id=entity_id, canonical_name="Alice", aliases=[], avatar_url=None)]
            else:
                # interaction_30d rows (avatar_url query removed — bu-j77a5)
                return [_row(contact_id=contact_id, interaction_count_30d=2)]

        mock_pool.fetch = _fetch
        mock_pool.fetchrow = AsyncMock(return_value=None)

        mock_db = _mock_db(mock_pool)
        app = _wire_app(mock_db)

        from butlers.tools.relationship import dunbar as _dunbar_mod

        _dunbar_mod.compute_tier_ranking = AsyncMock(return_value=ranked)

        resp = await _get(app, "/api/relationship/dunbar/ranking")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["entries"]) == 1
        entry = body["entries"][0]
        assert "warmth" in entry
        assert entry["warmth"] is not None
        assert 0.0 <= entry["warmth"] <= 1.0

    async def test_tier_1500_warmth_is_null(self):
        """Tier 1500 contacts have warmth=null (no cadence target)."""
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

        mock_pool = AsyncMock()

        fetch_call_count = [0]

        async def _fetch(*args, **kwargs):
            fetch_call_count[0] += 1
            if fetch_call_count[0] == 1:
                return [_row(id=entity_id, canonical_name="Bob", aliases=[], avatar_url=None)]
            else:
                return []  # no 30d interactions (avatar_url query removed — bu-j77a5)

        mock_pool.fetch = _fetch
        mock_pool.fetchrow = AsyncMock(return_value=None)

        mock_db = _mock_db(mock_pool)
        app = _wire_app(mock_db)

        from butlers.tools.relationship import dunbar as _dunbar_mod

        _dunbar_mod.compute_tier_ranking = AsyncMock(return_value=ranked)

        resp = await _get(app, "/api/relationship/dunbar/ranking")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["entries"]) == 1
        entry = body["entries"][0]
        assert entry["warmth"] is None
