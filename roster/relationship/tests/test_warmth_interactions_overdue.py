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
                return [_row(id=entity_id, canonical_name="Alice Test", aliases=[])]
            elif fetch_call_count[0] == 2:
                # avatar rows
                return [_row(id=contact_id, avatar_url=None)]
            else:
                # interaction_30d rows — 2 interactions in last 30d
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
                return [_row(id=entity_id, canonical_name="Alice", aliases=[])]
            elif fetch_call_count[0] == 2:
                return [_row(id=contact_id, avatar_url=None)]
            else:
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
                return [_row(id=entity_id, canonical_name="Bob", aliases=[])]
            elif fetch_call_count[0] == 2:
                return [_row(id=contact_id, avatar_url=None)]
            else:
                return []  # no 30d interactions

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


# ---------------------------------------------------------------------------
# GET /contacts/{contact_id}/interactions — chronological thread
# ---------------------------------------------------------------------------


class TestContactInteractions:
    """GET /contacts/{id}/interactions — chronological list with direction."""

    def _build_app(
        self,
        *,
        contact_exists: bool = True,
        entity_id=None,
        interaction_rows: list | None = None,
    ) -> FastAPI:
        cid = uuid4()
        eid = entity_id or uuid4()
        mock_pool = AsyncMock()

        if not contact_exists:
            mock_pool.fetchrow = AsyncMock(return_value=None)
        else:
            contact_row = _row(id=cid, entity_id=eid)
            mock_pool.fetchrow = AsyncMock(return_value=contact_row)

        mock_pool.fetch = AsyncMock(return_value=interaction_rows or [])

        mock_db = _mock_db(mock_pool)
        return _wire_app(mock_db), cid

    async def test_returns_404_for_unknown_contact(self):
        app, _ = self._build_app(contact_exists=False)
        cid = uuid4()
        resp = await _get(app, f"/api/relationship/contacts/{cid}/interactions")
        assert resp.status_code == 404

    async def test_empty_history_returns_empty_list(self):
        """Contact with no interaction facts returns empty interactions list."""
        app, cid = self._build_app(interaction_rows=[])
        resp = await _get(app, f"/api/relationship/contacts/{cid}/interactions")
        assert resp.status_code == 200
        body = resp.json()
        assert body == {"interactions": []}

    async def test_mixed_in_out_directions_preserved(self):
        """in/out/drafted directions are correctly mapped from metadata."""
        ts1 = datetime(2026, 5, 1, 10, 0, 0, tzinfo=UTC)
        ts2 = datetime(2026, 5, 5, 14, 0, 0, tzinfo=UTC)
        ts3 = datetime(2026, 5, 8, 9, 0, 0, tzinfo=UTC)

        rows = [
            _row(
                id=uuid4(),
                content="Hi from contact",
                metadata={"direction": "in"},
                valid_at=ts1,
            ),
            _row(
                id=uuid4(),
                content="Reply from owner",
                metadata={"direction": "out"},
                valid_at=ts2,
            ),
            _row(
                id=uuid4(),
                content="Draft response",
                metadata={"direction": "drafted"},
                valid_at=ts3,
            ),
        ]

        app, cid = self._build_app(interaction_rows=rows)
        resp = await _get(app, f"/api/relationship/contacts/{cid}/interactions")

        assert resp.status_code == 200
        body = resp.json()
        items = body["interactions"]
        assert len(items) == 3
        assert items[0]["direction"] == "in"
        assert items[1]["direction"] == "out"
        assert items[2]["direction"] == "drafted"
        assert items[0]["text"] == "Hi from contact"

    async def test_legacy_incoming_outgoing_mapped(self):
        """Legacy interaction_log() directions 'incoming'/'outgoing' are mapped to 'in'/'out'."""
        ts1 = datetime(2026, 5, 1, 10, 0, 0, tzinfo=UTC)
        ts2 = datetime(2026, 5, 5, 14, 0, 0, tzinfo=UTC)
        ts3 = datetime(2026, 5, 7, 9, 0, 0, tzinfo=UTC)

        rows = [
            _row(
                id=uuid4(),
                content="Incoming message",
                metadata={"direction": "incoming"},
                valid_at=ts1,
            ),
            _row(
                id=uuid4(),
                content="Outgoing message",
                metadata={"direction": "outgoing"},
                valid_at=ts2,
            ),
            _row(
                id=uuid4(),
                content="Mutual exchange",
                metadata={"direction": "mutual"},
                valid_at=ts3,
            ),
        ]

        app, cid = self._build_app(interaction_rows=rows)
        resp = await _get(app, f"/api/relationship/contacts/{cid}/interactions")

        assert resp.status_code == 200
        items = resp.json()["interactions"]
        assert len(items) == 3
        assert items[0]["direction"] == "in"
        assert items[1]["direction"] == "out"
        # "mutual" has no clean in/out mapping — rendered as null
        assert items[2]["direction"] is None

    async def test_unknown_direction_rendered_as_null(self):
        """An unrecognised direction value maps to null, not an error."""
        rows = [
            _row(
                id=uuid4(),
                content="Some interaction",
                metadata={"direction": "unknown_value"},
                valid_at=datetime(2026, 5, 1, tzinfo=UTC),
            ),
        ]
        app, cid = self._build_app(interaction_rows=rows)
        resp = await _get(app, f"/api/relationship/contacts/{cid}/interactions")

        assert resp.status_code == 200
        item = resp.json()["interactions"][0]
        assert item["direction"] is None

    async def test_null_metadata_renders_null_direction(self):
        """Interactions with null metadata get null direction."""
        rows = [
            _row(
                id=uuid4(),
                content="Old-style interaction",
                metadata=None,
                valid_at=datetime(2026, 5, 2, tzinfo=UTC),
            ),
        ]
        app, cid = self._build_app(interaction_rows=rows)
        resp = await _get(app, f"/api/relationship/contacts/{cid}/interactions")

        assert resp.status_code == 200
        item = resp.json()["interactions"][0]
        assert item["direction"] is None

    async def test_limit_parameter_respected(self):
        """limit=5 sends 5 to pool query."""
        app, cid = self._build_app(interaction_rows=[])
        resp = await _get(app, f"/api/relationship/contacts/{cid}/interactions", limit=5)
        assert resp.status_code == 200

    async def test_contact_with_no_entity_returns_empty(self):
        """Contact with entity_id=None returns empty interactions list (not 404)."""
        cid = uuid4()
        mock_pool = AsyncMock()
        # Contact row with entity_id=None
        contact_row = _row(id=cid, entity_id=None)
        mock_pool.fetchrow = AsyncMock(return_value=contact_row)
        mock_pool.fetch = AsyncMock(return_value=[])

        mock_db = _mock_db(mock_pool)
        app = _wire_app(mock_db)

        resp = await _get(app, f"/api/relationship/contacts/{cid}/interactions")
        assert resp.status_code == 200
        assert resp.json() == {"interactions": []}


# ---------------------------------------------------------------------------
# GET /contacts/overdue — tier-cadence threshold logic
# ---------------------------------------------------------------------------


class TestContactsOverdue:
    """GET /contacts/overdue — overdue detection using tier-cadence logic."""

    def _build_app(
        self,
        *,
        contact_rows: list | None = None,
        ranked: list | None = None,
    ) -> FastAPI:
        """Wire app with controlled dunbar engine and DB pool.

        ``ranked`` is the pre-computed tier ranking output from
        ``compute_tier_ranking`` — each entry must include
        ``contact_id``, ``dunbar_tier``, and ``last_interaction_at``.
        """
        mock_pool = AsyncMock()
        mock_pool.fetch = AsyncMock(return_value=contact_rows or [])

        mock_db = _mock_db(mock_pool)
        app = _wire_app(mock_db)

        from butlers.tools.relationship import dunbar as _dunbar_mod

        _dunbar_mod.compute_tier_ranking = AsyncMock(return_value=ranked or [])

        return app

    async def test_empty_contacts_returns_empty_list(self):
        app = self._build_app(contact_rows=[])
        resp = await _get(app, "/api/relationship/contacts/overdue", days=14)
        assert resp.status_code == 200
        assert resp.json() == {"contacts": []}

    async def test_fresh_contact_under_cadence_not_returned(self):
        """A contact touched recently (within tier cadence) is NOT overdue."""
        cid = uuid4()
        eid = uuid4()
        last_at = datetime.now(UTC) - timedelta(days=5)

        contact_rows = [
            _row(
                id=cid,
                full_name="Alice Fresh",
                entity_id=eid,
                stay_in_touch_days=None,
            )
        ]
        # tier-5 cadence=14 days; contact touched 5 days ago → not overdue
        ranked = [
            {
                "contact_id": cid,
                "entity_id": eid,
                "dunbar_tier": 5,
                "dunbar_score": 5.0,
                "last_interaction_at": last_at,
            }
        ]

        app = self._build_app(contact_rows=contact_rows, ranked=ranked)
        resp = await _get(app, "/api/relationship/contacts/overdue", days=30)
        assert resp.status_code == 200
        body = resp.json()
        assert body["contacts"] == []

    async def test_contact_past_tier_cadence_is_returned(self):
        """A contact past their tier's cadence appears even if days param is longer."""
        cid = uuid4()
        eid = uuid4()
        # Touched 20 days ago; tier-5 cadence=14d → overdue by 6 days
        last_at = datetime.now(UTC) - timedelta(days=20)

        contact_rows = [
            _row(
                id=cid,
                full_name="Bob Overdue",
                entity_id=eid,
                stay_in_touch_days=None,
            )
        ]
        ranked = [
            {
                "contact_id": cid,
                "entity_id": eid,
                "dunbar_tier": 5,
                "dunbar_score": 3.0,
                "last_interaction_at": last_at,
            }
        ]

        app = self._build_app(contact_rows=contact_rows, ranked=ranked)
        # days=30 but tier-5 cadence=14 → effective threshold=14 → contact is overdue
        resp = await _get(app, "/api/relationship/contacts/overdue", days=30)
        assert resp.status_code == 200
        contacts = resp.json()["contacts"]
        assert len(contacts) == 1
        c = contacts[0]
        assert c["name"] == "Bob Overdue"
        assert c["tier"] == "tier-5"
        assert c["owed_days"] >= 1
        # effective_threshold = min(days=30, tier_cadence=14) = 14
        assert c["target_cadence_days"] == 14
        assert c["last_contact_date"] is not None

    async def test_contact_with_no_interactions_is_overdue(self):
        """Contact with no interaction history is always overdue.

        Uses stay_in_touch_days to ensure a cadence target exists independent
        of the Dunbar tier computation (dunbar_tier=1500, which has no
        default cadence, so stay_in_touch_days overrides it).
        Never-contacted contacts get a large sentinel owed_days value.
        """
        cid = uuid4()
        eid = uuid4()

        contact_rows = [
            _row(
                id=cid,
                full_name="Carol Unmet",
                entity_id=eid,
                stay_in_touch_days=14,  # explicit cadence override
            )
        ]
        # tier 1500 (no score), but stay_in_touch_days=14 forces a cadence
        ranked = [
            {
                "contact_id": cid,
                "entity_id": eid,
                "dunbar_tier": 1500,
                "dunbar_score": 0.0,
                "last_interaction_at": None,
            }
        ]

        app = self._build_app(contact_rows=contact_rows, ranked=ranked)
        resp = await _get(app, "/api/relationship/contacts/overdue", days=30)
        assert resp.status_code == 200
        contacts = resp.json()["contacts"]
        assert len(contacts) == 1
        assert contacts[0]["last_contact_date"] is None
        # Never-contacted → large sentinel value
        assert contacts[0]["owed_days"] > 1000
        # effective_threshold = min(days=30, stay_in_touch=14) = 14
        assert contacts[0]["target_cadence_days"] == 14

    async def test_response_fields_complete(self):
        """Response items have all required fields."""
        cid = uuid4()
        eid = uuid4()
        last_at = datetime.now(UTC) - timedelta(days=25)

        contact_rows = [
            _row(
                id=cid,
                full_name="Dave Fields",
                entity_id=eid,
                stay_in_touch_days=None,
            )
        ]
        ranked = [
            {
                "contact_id": cid,
                "entity_id": eid,
                "dunbar_tier": 5,
                "dunbar_score": 2.0,
                "last_interaction_at": last_at,
            }
        ]

        app = self._build_app(contact_rows=contact_rows, ranked=ranked)
        resp = await _get(app, "/api/relationship/contacts/overdue", days=14)
        assert resp.status_code == 200
        contacts = resp.json()["contacts"]
        assert len(contacts) == 1
        c = contacts[0]
        required_fields = {
            "contact_id",
            "name",
            "tier",
            "owed_days",
            "last_contact_date",
            "target_cadence_days",
        }
        assert required_fields.issubset(c.keys())

    async def test_tier_1500_contact_excluded(self):
        """Tier 1500 contacts with no stay_in_touch_days are excluded (no cadence)."""
        cid = uuid4()
        eid = uuid4()

        contact_rows = [
            _row(
                id=cid,
                full_name="Eve Distant",
                entity_id=eid,
                stay_in_touch_days=None,
            )
        ]
        # Empty ranked → contact defaults to dunbar_tier=1500 via fallback dict
        ranked: list = []

        app = self._build_app(contact_rows=contact_rows, ranked=ranked)
        resp = await _get(app, "/api/relationship/contacts/overdue", days=14)
        assert resp.status_code == 200
        assert resp.json()["contacts"] == []
